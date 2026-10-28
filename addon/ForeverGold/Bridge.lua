local ADDON, ns = ...

local CALLER = "ForeverGold"
local LIST_NAME = "Claude: tonight"
local DURATION = { [1] = "2h", [2] = "8h", [3] = "24h" }

ns.market = {}

-- design.md 1.6: ForeverGoldPlan if it is a schema-1 table, else the default.
function ns.LoadPlan()
  local p = ForeverGoldPlan
  if type(p) == "table" and p.schema == 1 then
    ns.plan, ns.planSource = p, "data"
  else
    ns.plan, ns.planSource = ForeverGoldPlanDefault, (p == nil) and "missing" or "invalid"
  end
  if type(ns.plan) ~= "table" then
    ns.plan = { schema = 1, id = "none" }
  end
end

function ns.PlanList(name)
  local t = ns.plan and ns.plan[name]
  if type(t) == "table" then return t end
  return {}
end

local function validRows(name, idKey)
  local out = {}
  for _, row in ipairs(ns.PlanList(name)) do
    if type(row) == "table" and type(row[idKey or "id"]) == "number" then out[#out + 1] = row end
  end
  return out
end
ns.ValidRows = validRows

local function label(row)
  return ns.Esc(row.name or row.id)
end

-- Index the newest scan of this session, only for items the plan mentions. house = the AH it
-- came from: its prices say nothing about another AH.
function ns.BuildMarket(items, house)
  ns.marketHouse = house
  local want = {}
  for _, list in ipairs({ "buy", "post", "hold", "vendor" }) do
    for _, row in ipairs(validRows(list)) do want[row.id] = true end
  end
  for _, row in ipairs(validRows("craft", "out")) do want[row.out] = true end
  local market = {}
  for id in pairs(want) do
    local e = items[id]
    if e then
      local units = {}
      for u in pairs(e.units) do units[#units + 1] = u end
      table.sort(units)
      local ladder, q = {}, 0
      for k, u in ipairs(units) do
        ladder[k] = { u, e.units[u] }
        q = q + e.units[u]
      end
      market[id] = { min = units[1], q = q, ladder = ladder }
    end
  end
  ns.market = market
end

function ns.CopperOKNow()
  if ns.session then return ns.session.copperOK end
  return ns.copperOK
end

-- The house of the plan's market ("us|Forever|Alliance" -> "Alliance"), nil if it has none.
local function planHouse()
  local bos = type(ns.plan) == "table" and type(ns.plan.basedOnScan) == "table" and ns.plan.basedOnScan
  local market = bos and bos.market
  if type(market) ~= "string" then return nil end
  local h = market:match("|([^|]+)$")
  if h == "Alliance" or h == "Horde" or h == "Neutral" then return h end
  return nil
end

-- Why the rows are gray at this AH: the plan's floors and caps (5% or 15% cut) are for one house,
-- and this session's scan prices only the AH it was taken at. nil when nothing is off.
function ns.WrongHouse()
  local here = ns.house
  if not here then return nil end
  local p = planHouse()
  if p and p ~= here then return "plan is for the " .. p .. " AH, not this " .. here .. " one" end
  if ns.lastScan and ns.marketHouse and ns.marketHouse ~= here then
    return "scan this AH (the scan is from the " .. ns.marketHouse .. " AH)"
  end
  return nil
end

-- Buy guard: fill qty from the cheapest units; green if avg <= maxUnit.
function ns.BuyGuard(row)
  local qty = tonumber(row.qty) or 0
  local maxUnit = tonumber(row.maxUnit) or 0
  local head = label(row) .. " x" .. ns.Int(qty) .. (row.flip and " (flip)" or "") .. ": "
  local wrong = ns.WrongHouse()
  if wrong then
    return "gray", head .. "max " .. ns.Money(maxUnit) .. ", " .. wrong
  end
  if not ns.lastScan then
    return "gray", head .. "max " .. ns.Money(maxUnit) .. ", no scan yet"
  end
  local m = ns.market[row.id]
  if not m then
    return "red", head .. "none listed (max " .. ns.Money(maxUnit) .. ")"
  end
  local filled, cost = 0, 0
  for _, point in ipairs(m.ladder) do
    if filled >= qty then break end
    local take = math.min(point[2], qty - filled)
    filled = filled + take
    cost = cost + take * point[1]
  end
  local avg = filled > 0 and cost / filled or 0
  local color = (filled > 0 and avg <= maxUnit) and "green" or "red"
  local fill = filled < qty and (ns.Int(filled) .. "/" .. ns.Int(qty)) or ns.Int(filled)
  return color, head .. "fill " .. fill .. " @ " .. ns.MoneyAvg(avg) .. " (max " .. ns.Money(maxUnit) .. ")"
end

local function roundSilver(v)
  if ns.CopperOKNow() == false then return math.ceil(v / 100) * 100 end
  return v
end

-- Post guard: match (or undercut) the market min, never below floorUnit.
function ns.PostGuard(row)
  local qty = tonumber(row.qty) or 0
  local floorUnit = tonumber(row.floorUnit) or 0
  local head = label(row) .. " x" .. ns.Int(qty) .. ": "
  local tail = DURATION[row.duration] and (", " .. DURATION[row.duration]) or ""
  local wrong = ns.WrongHouse()
  if wrong then
    return "gray", head .. "floor " .. ns.Money(floorUnit) .. ", " .. wrong
  end
  if not ns.lastScan then
    return "gray", head .. "post at >= " .. ns.Money(floorUnit) .. " (floor, no scan yet)"
  end
  local m = ns.market[row.id]
  if not m then
    local start = roundSilver(math.max(tonumber(row.startUnit) or floorUnit, floorUnit))
    return "green", head .. "post at " .. ns.Money(start) .. tail .. " (none listed)"
  end
  if m.min < floorUnit then
    return "red", head .. "HOLD (market below floor): min " .. ns.Money(m.min) .. " < floor " .. ns.Money(floorUnit)
  end
  local settings = type(ns.plan.settings) == "table" and ns.plan.settings or {}
  local suggested = m.min
  if settings.postMode ~= "match" then
    suggested = m.min - (tonumber(settings.undercut) or 0)
  end
  suggested = roundSilver(math.max(suggested, floorUnit))
  return "green", head .. "post at " .. ns.Money(suggested) .. tail .. " (min " .. ns.Money(m.min) .. ", floor " .. ns.Money(floorUnit) .. ")"
end

function ns.BuyRows()
  local out = {}
  for _, row in ipairs(validRows("buy")) do
    local color, text = ns.BuyGuard(row)
    out[#out + 1] = { id = row.id, color = color, text = text }
  end
  return out
end

function ns.PostRows()
  local out = {}
  for _, row in ipairs(validRows("post")) do
    local color, text = ns.PostGuard(row)
    out[#out + 1] = { id = row.id, color = color, text = text }
  end
  return out
end

function ns.PlanHasRows()
  for _, name in ipairs({ "buy", "post", "craft", "hold", "vendor" }) do
    if #ns.PlanList(name) > 0 then return true end
  end
  return false
end

-- Deposit probes for plan post rows (commodities only; unrestricted calls).
function ns.RunDepositProbes()
  local calc = C_AuctionHouse and C_AuctionHouse.CalculateCommodityDeposit
  if type(calc) ~= "function" or not ns.db then return end
  local now = GetServerTime()
  for _, row in ipairs(validRows("post")) do
    local qty = math.max(1, math.floor(tonumber(row.qty) or 1))
    -- house: the neutral deposit rate is 5x the faction one, so fgold only uses a probe at its own AH
    local probe, got = { qty = qty, at = now, house = ns.house }, false
    for duration = 1, 3 do
      local ok, deposit = pcall(calc, row.id, duration, qty)
      if ok and type(deposit) == "number" then
        probe[duration] = deposit
        got = true
      end
    end
    if got then ns.db.probes.deposit[row.id] = probe end
  end
end

local function chatBuyList(reason)
  local rows = validRows("buy")
  ns.Print("buy list (" .. reason .. "), buy these by hand:")
  for _, row in ipairs(rows) do
    ns.Say("  " .. label(row) .. " x" .. ns.Int(tonumber(row.qty) or 0) .. " at <= " .. ns.Money(tonumber(row.maxUnit) or 0) .. " each")
  end
end

-- Auctionator buy list: callerID is the first argument of every API call.
function ns.MakeBuyList()
  local rows = validRows("buy")
  if #rows == 0 then
    ns.Print("buy list: the plan has no buy rows.")
    return false
  end
  if not (Auctionator and Auctionator.API and Auctionator.API.v1) then
    chatBuyList("Auctionator not loaded")
    return false
  end
  local api = Auctionator.API.v1
  local strings = {}
  for i, row in ipairs(rows) do
    local ok, s = pcall(api.ConvertToSearchString, CALLER, {
      searchString = type(row.name) == "string" and row.name or tostring(row.id),
      isExact = true, maxPrice = row.maxUnit, quantity = row.qty,
    })
    if not ok or type(s) ~= "string" then
      chatBuyList("Auctionator API error")
      return false
    end
    strings[i] = s
  end
  local ok = pcall(api.CreateShoppingList, CALLER, LIST_NAME, strings)
  if not ok then
    chatBuyList("Auctionator API error")
    return false
  end
  ns.Print("buy list: Auctionator list \"" .. LIST_NAME .. "\" has " .. ns.Int(#strings) .. " items.")
  return true
end
