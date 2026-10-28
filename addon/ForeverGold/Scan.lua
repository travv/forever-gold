local ADDON, ns = ...

local CHUNK = 2000      -- rows read per frame
local ENCODE_CHUNK = 500 -- items written per frame
local THROTTLE = 900    -- seconds between our own full scans
local REPLY_WAIT = 180  -- seconds to wait for the server's reply

local job               -- the scan in flight: state "waiting" or "packing"
local lastSig           -- signature of the last list packed this session

ns.ahOpen = false
ns.house = nil

local function houseNow()
  local f = UnitFactionGroup("npc")
  if f == "Alliance" or f == "Horde" then return f end
  return "Neutral"
end

local function addLog(entry)
  local log = ns.db.scanLog
  log[#log + 1] = entry
  ns.Trim(log, ns.CAPS.scanLog)
end

function ns.ScanState()
  return job and job.state or "idle"
end

function ns.ScanJob()
  return job
end

local function add(map, itemID, count, buyout)
  local e = map[itemID]
  if not e then
    e = { n = 0, units = {} }
    map[itemID] = e
  end
  e.n = e.n + 1
  local unit = math.floor(buyout / count + 0.5)
  e.units[unit] = (e.units[unit] or 0) + count
end

local function fmt(n)
  return string.format("%.0f", n)
end

-- Scan string (design.md 1.3): itemID:auctions:unit x qty,...; sorted, integers only.
local function encodeItem(id, e)
  local units = {}
  for u in pairs(e.units) do units[#units + 1] = u end
  table.sort(units)
  local points = {}
  for k, u in ipairs(units) do points[k] = fmt(u) .. "x" .. fmt(e.units[u]) end
  return fmt(id) .. ":" .. fmt(e.n) .. ":" .. table.concat(points, ",") .. ";"
end

local function sortedIDs(map)
  local ids = {}
  for id in pairs(map) do ids[#ids + 1] = id end
  table.sort(ids)
  return ids
end

local function encode(map)
  local parts = {}
  for k, id in ipairs(sortedIDs(map)) do parts[k] = encodeItem(id, map[id]) end
  return table.concat(parts)
end

local function finish(j)
  local data = table.concat(j.parts)
  local items = #j.ids
  local ownData = encode(j.ownItems)
  local packMs = math.floor(debugprofilestop() - j.t0 + 0.5)
  local scan = {
    v = 1, at = j.gotAt, reqAt = j.reqAt, by = j.by,
    char = ns.CharKey(), realm = GetRealmName(), region = GetCurrentRegion(),
    faction = (UnitFactionGroup("player")), house = ns.house or houseNow(),
    rows = j.n, priced = j.priced, bidOnly = j.bidOnly, own = j.own, items = items,
    packMs = packMs, data = data, ownData = ownData,
  }
  local db = ns.db
  db.scans[#db.scans + 1] = scan
  ns.Trim(db.scans, ns.CAPS.scans)
  if j.by == "fg" then db.lastScanAt = scan.at end
  lastSig = j.sig
  j.entry.result = "ok"
  j.entry.gotAt = j.gotAt
  j.entry.rows = j.n
  job = nil
  ns.lastScan = scan
  ns.BuildMarket(j.items, scan.house)
  ns.RefreshUI()
  ns.Print(("scan: %s rows, %s items in %.1f s"):format(fmt(j.n), fmt(items), packMs / 1000))
end

-- After the last row: build the string ENCODE_CHUNK items per frame. The rows
-- are already read, so closing the AH no longer aborts this phase.
local function encodeStep(j)
  if job ~= j then return end
  local last = math.min(j.k + ENCODE_CHUNK - 1, #j.ids)
  for k = j.k, last do
    local id = j.ids[k]
    j.parts[k] = encodeItem(id, j.items[id])
  end
  j.k = last + 1
  if j.k <= #j.ids then
    C_Timer.After(0, function() encodeStep(j) end)
  else
    finish(j)
  end
end

local function packStep(j)
  if job ~= j then return end  -- aborted (AH closed) or replaced
  local get = C_AuctionHouse.GetReplicateItemInfo
  local last = math.min(j.i + CHUNK, j.n) - 1
  for i = j.i, last do
    -- 0-based index; 18 returns. Do not wait for hasAllInfo.
    local name, texture, count, qualityID, usable, level, levelType, minBid, minIncrement, buyoutPrice, bidAmount, highBidder, bidderFullName, owner, ownerFullName, saleStatus, itemID, hasAllInfo = get(i)
    if itemID and itemID > 0 and count and count > 0 then
      if owner ~= nil then j.own = j.own + 1 end
      if not buyoutPrice or buyoutPrice <= 0 then
        j.bidOnly = j.bidOnly + 1
      elseif owner ~= nil then
        add(j.ownItems, itemID, count, buyoutPrice)
      else
        j.priced = j.priced + 1
        add(j.items, itemID, count, buyoutPrice)
      end
    end
  end
  j.i = last + 1
  if j.i < j.n then
    C_Timer.After(0, function() packStep(j) end)
  else
    j.phase, j.ids, j.parts, j.k = "encode", sortedIDs(j.items), {}, 1
    C_Timer.After(0, function() encodeStep(j) end)
  end
end

-- Cheap identity of the current replicate list: row count plus four sample rows.
local function signature(n)
  if n <= 0 then return "0" end
  local get = C_AuctionHouse.GetReplicateItemInfo
  local parts = { fmt(n) }
  for _, i in ipairs({ 0, math.floor(n / 3), math.floor(2 * n / 3), n - 1 }) do
    local _, _, count, _, _, _, _, _, _, buyoutPrice, _, _, _, _, _, _, itemID = get(i)
    parts[#parts + 1] = tostring(itemID) .. "x" .. tostring(count) .. "@" .. fmt(buyoutPrice or 0)
  end
  return table.concat(parts, "|")
end

local function startPacking(n, sig)
  local now = GetServerTime()
  local entry, by, reqAt
  if job and job.state == "waiting" and now - job.reqAt <= REPLY_WAIT then
    entry, by, reqAt = job.entry, "fg", job.reqAt
  else
    if job then job.entry.result = "noreply" end  -- our request expired unanswered
    entry = { reqAt = 0, gotAt = 0, result = "other", rows = 0, by = "other" }
    addLog(entry)
    by, reqAt = "other", 0
  end
  entry.gotAt = now
  entry.rows = n
  local j = {
    state = "packing", by = by, reqAt = reqAt, gotAt = now, entry = entry, sig = sig,
    n = n, i = 0, priced = 0, bidOnly = 0, own = 0, items = {}, ownItems = {},
    t0 = debugprofilestop(),
  }
  job = j
  ns.RefreshUI()
  C_Timer.After(0, function() packStep(j) end)
end

function ns.RequestScan(force)
  local db = ns.db
  if not ns.ahOpen then
    ns.Print("scan: open the auction house first.")
    return false
  end
  if job then
    ns.Print("scan: already running.")
    return false
  end
  local now = GetServerTime()
  local lastScan = math.max(db.lastScanAt or 0, tonumber(ns.plan and ns.plan.lastScanAt) or 0)
  if now - lastScan < THROTTLE and not force then
    local mins = math.ceil((THROTTLE - (now - lastScan)) / 60)
    ns.Print("next full scan in " .. fmt(mins) .. " min (/fg scan force skips this check).")
    return false
  end
  if db.settings.neverScan then
    ns.Print("scan: settings.neverScan is on, so only scans by other addons are captured.")
    return false
  end
  local entry = { reqAt = now, gotAt = 0, result = "other", rows = 0, by = "fg" }
  addLog(entry)
  local j = { state = "waiting", reqAt = now, entry = entry }
  job = j
  local ok = pcall(C_AuctionHouse.ReplicateItems)
  if not ok then
    job = nil
    ns.Print("scan: the request failed.")
    ns.RefreshUI()
    return false
  end
  C_Timer.After(REPLY_WAIT, function()
    if job == j and j.state == "waiting" then
      job = nil
      if j.dropped then
        entry.result = "dropped"
        ns.Print("scan: the server dropped the request (throttled). Try again in a few minutes.")
      else
        entry.result = "noreply"
        ns.Print("scan: no reply from the server in 3 min. Try again later.")
      end
      ns.RefreshUI()
    end
  end)
  ns.Print("scan: requested, waiting for the server (can take a minute).")
  ns.RefreshUI()
  return true
end

local handlers = {}

function handlers.AUCTION_HOUSE_SHOW()
  ns.ahOpen = true
  ns.house = houseNow()
  local copperOK = ns.CopperOK()
  ns.copperOK = copperOK
  if ns.session then ns.session.copperOK = copperOK end
  ns.RunDepositProbes()
  if ns.UI and ns.UI.OnAuctionHouseShow then ns.UI.OnAuctionHouseShow() end
end

function handlers.AUCTION_HOUSE_CLOSED()
  ns.ahOpen = false
  if job and job.phase ~= "encode" then
    job.entry.result = "closed"
    job = nil
    ns.Print("scan: auction house closed, scan aborted.")
  end
  ns.RefreshUI()
end

function handlers.AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED()
  -- The event has no payload and also fires for other addons' throttled AH messages (an
  -- Auctionator search started while we wait), so it does not end our wait: the entry says
  -- "dropped" unless our list still arrives within REPLY_WAIT, and the Scan button stays off.
  if job and job.state == "waiting" and not job.dropped then
    job.dropped = true
    job.entry.result = "dropped"
    ns.Print("scan: the server dropped a throttled AH message (maybe this scan); still waiting up to 3 min.")
    ns.RefreshUI()
  end
end

function handlers.REPLICATE_ITEM_LIST_UPDATE()
  -- Re-fires while item data caches: ignore it while packing. Re-fires also
  -- arrive after a fast pack, so an unrequested copy of the last list is skipped.
  if not ns.ahOpen or (job and job.state == "packing") then return end
  local n = C_AuctionHouse.GetNumReplicateItems() or 0
  local waiting = job and job.state == "waiting"
  -- An empty list is never a scan, not even while ours is pending (a new ReplicateItems call
  -- clears the old rows): packing it would store a 0-row "fg" scan and set the throttle clock.
  if n <= 0 then return end
  local sig = signature(n)
  if sig == lastSig and not waiting then return end
  startPacking(n, sig)
end

local events = CreateFrame("Frame")
events:RegisterEvent("AUCTION_HOUSE_SHOW")
events:RegisterEvent("AUCTION_HOUSE_CLOSED")
events:RegisterEvent("AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED")
events:RegisterEvent("REPLICATE_ITEM_LIST_UPDATE")
events:SetScript("OnEvent", function(self, event, ...)
  local h = handlers[event]
  if h then h(...) end
end)
