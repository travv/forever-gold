-- ForeverGold addon harness. Usage (from the repo root):
--   luajit tests/wow/run.lua                  run every scenario, exit 1 on failure
--   luajit tests/wow/run.lua --write-fixture  also write tests/fixtures/harness/... (deterministic)
--   luajit tests/wow/run.lua --plan PATH      load a PlanData.lua, scan the fixture rows and print
--                                             LOGIN:/PLANID:/PLANSRC:/BUYROW:/POSTROW:/AUCSTR: lines;
--                                             exit 0 ok, 2 plan not loaded (default used), 1 harness error
--   luajit tests/wow/run.lua --plan PATH --sv SVPATH
--                                             also play the session loop against a SavedVariables file:
--                                             read SVPATH back at login, Scan, Buy list, Done (a /reload:
--                                             save, then PLAYER_LOGIN again), log out; SVPATH is written
--                                             (with .bak) at the reload and at logout like the client
-- Extra flags: --only <text> (scenario filter), --verbose, --copper-ok true|false|missing (with --plan).

local function normpath(p)
  p = p:gsub("\\", "/")
  local lead = p:match("^(%a:/)") or p:match("^(/)") or ""
  local parts = {}
  for part in p:sub(#lead + 1):gmatch("[^/]+") do
    if part == ".." and #parts > 0 and parts[#parts] ~= ".." then
      parts[#parts] = nil
    elseif part ~= "." then
      parts[#parts + 1] = part
    end
  end
  local out = lead .. table.concat(parts, "/")
  if out == "" then out = "." end
  return out
end

local script = normpath((arg and arg[0]) or "tests/wow/run.lua")
local here = script:match("^(.*)/[^/]*$") or "."
local ROOT = normpath(here .. "/../..")
package.path = here .. "/?.lua;" .. package.path

local Stub = require("stub")
local SV = require("svwriter")
local Session = require("session")
local F = require("fixtures")
Session.root = ROOT

local F_ = Stub.rawformat
local EMDASH = "\226\128\148"
local OUT_SV = ROOT .. "/tests/out/WTF/Account/TEST#1/SavedVariables/ForeverGold.lua"
local FIXTURE_SV = ROOT .. "/tests/fixtures/harness/WTF/Account/TEST#1/SavedVariables/ForeverGold.lua"
local BIG_SV = ROOT .. "/tests/out/WTF/Account/BIG#1/SavedVariables/ForeverGold.lua"

local ADDON_GLOBALS = { ForeverGoldDB = true, ForeverGoldPlanDefault = true, ForeverGoldPlan = true,
  SLASH_FOREVERGOLD1 = true }

local args = { flags = {} }
do
  local i = 1
  while arg and arg[i] do
    local a = arg[i]
    if a == "--plan" or a == "--only" or a == "--copper-ok" or a == "--sv" then
      if not arg[i + 1] then
        io.stderr:write(a .. " needs a value\n")
        os.exit(2)
      end
      args[a:sub(3)] = arg[i + 1]
      i = i + 2
    elseif a == "--write-fixture" or a == "--verbose" then
      args.flags[a] = true
      i = i + 1
    else
      io.stderr:write("unknown flag " .. a .. "\nusage: luajit tests/wow/run.lua [--write-fixture] [--only TEXT]"
        .. " [--plan PATH [--copper-ok true|false|missing] [--sv SVPATH]] [--verbose]\n")
      os.exit(2)
    end
  end
end

---------------------------------------------------------------------------
-- tiny test framework
---------------------------------------------------------------------------
local function show(v)
  if type(v) == "string" then return F_("%q", v) end
  return tostring(v)
end

local Ctx = {}
Ctx.__index = Ctx

local function fail(msg, level)
  error({ fail = msg }, (level or 1) + 1)
end

function Ctx:session(opts)
  local s = Session.start(opts)
  self.sessions[#self.sessions + 1] = s
  return s
end
function Ctx:check(cond, msg)
  self.checks = self.checks + 1
  if not cond then fail(msg or "check failed", 2) end
  return cond
end
function Ctx:eq(got, want, msg)
  self.checks = self.checks + 1
  if got ~= want then fail((msg or "value") .. ": expected " .. show(want) .. ", got " .. show(got), 2) end
end
function Ctx:has(text, sub, msg)
  self.checks = self.checks + 1
  if type(text) ~= "string" or not text:find(sub, 1, true) then
    fail((msg or "text") .. ": " .. show(sub) .. " not found in " .. show(text), 2)
  end
end
function Ctx:lacks(text, sub, msg)
  self.checks = self.checks + 1
  if type(text) == "string" and text:find(sub, 1, true) then
    fail((msg or "text") .. ": " .. show(sub) .. " must not appear in " .. show(text), 2)
  end
end
function Ctx:info(line) self.infos[#self.infos + 1] = line end
function Ctx:expectTraps(n) self.trapsExpected = n end
function Ctx:expectErrors(pattern) self.errorsExpected[#self.errorsExpected + 1] = pattern end

local function deepEqual(a, b, path)
  path = path or "value"
  if type(a) ~= type(b) then return false, path .. ": type " .. type(a) .. " vs " .. type(b) end
  if type(a) ~= "table" then
    if a == b then return true end
    return false, path .. ": " .. show(a) .. " vs " .. show(b)
  end
  for k, v in pairs(a) do
    local ok, why = deepEqual(v, b[k], path .. "." .. tostring(k))
    if not ok then return false, why end
  end
  for k in pairs(b) do
    if a[k] == nil then return false, path .. "." .. tostring(k) .. ": missing on the left" end
  end
  return true
end
function Ctx:same(got, want, msg)
  self.checks = self.checks + 1
  local ok, why = deepEqual(got, want)
  if not ok then fail((msg or "tables differ") .. ": " .. why, 2) end
end

-- Checks every session must pass: no trap hits, no Lua errors, no unknown
-- API reads, no out-of-range replicate reads, no leaked globals, no em-dash.
local function postChecks(ctx)
  local problems = {}
  local traps = 0
  for si, s in ipairs(ctx.sessions) do
    local w = s.w
    traps = traps + #w.traps
    for _, e in ipairs(w.errors) do
      local expected = false
      for _, p in ipairs(ctx.errorsExpected) do if e:find(p, 1, true) then expected = true end end
      if not expected then problems[#problems + 1] = "session " .. si .. " Lua error: " .. e end
    end
    for _, u in ipairs(w.unknown) do problems[#problems + 1] = "session " .. si .. " unknown API read: " .. u end
    if w.ah.badIndex > 0 then
      problems[#problems + 1] = "session " .. si .. ": " .. w.ah.badIndex .. " out-of-range GetReplicateItemInfo reads"
    end
    for k in pairs(w.globalsSet) do
      if not ADDON_GLOBALS[k] then problems[#problems + 1] = "session " .. si .. " leaked global: " .. tostring(k) end
    end
    for _, line in ipairs(w.chat) do
      if line:find(EMDASH, 1, true) then problems[#problems + 1] = "session " .. si .. " em-dash in chat: " .. line end
    end
    if w.auc and w.auc.misuse > 0 then
      problems[#problems + 1] = "session " .. si .. ": Auctionator API misused " .. w.auc.misuse .. " time(s)"
    end
  end
  if traps ~= (ctx.trapsExpected or 0) then
    for si, s in ipairs(ctx.sessions) do
      for _, t in ipairs(s.w.traps) do problems[#problems + 1] = "session " .. si .. " PROTECTED CALL: " .. t end
    end
    problems[#problems + 1] = "trap hits: " .. traps .. ", expected " .. (ctx.trapsExpected or 0)
  end
  return problems
end

local scenarios = {}
local function scenario(name, fn) scenarios[#scenarios + 1] = { name = name, fn = fn } end

---------------------------------------------------------------------------
-- shared helpers
---------------------------------------------------------------------------
local function baseOpts(extra)
  local o = { now = F.T0, plan = F.plan(F.T0), money = 1234567, bags = F.bags(),
    commodities = F.COMMODITIES, sellPrice = F.SELL }
  for k, v in pairs(extra or {}) do o[k] = v end
  return o
end

-- Click Scan (or /fg scan), let the server answer and the pack finish.
local function scanNow(s, how)
  local w = s.w
  if how == "force" then s:slash("/fg scan force") elseif how == "slash" then s:slash("/fg scan") else s:click("scan") end
  w:advance(w.ah.delay + 1)
  w:idle()
end

local function buyRow(s, id)
  for _, r in ipairs(s.ns.BuyRows()) do if r.id == id then return r end end
end
local function postRow(s, id)
  for _, r in ipairs(s.ns.PostRows()) do if r.id == id then return r end end
end

---------------------------------------------------------------------------
-- scenarios
---------------------------------------------------------------------------
local happyText

scenario("happy path: login, mail, bags, bank, AH scan, buy list, logout, next-day read-back", function(t)
  local T0, T1 = F.T0, F.T1
  local s = t:session(baseOpts({ auctionator = "ok" }))
  local w, ns = s.w, s.ns
  s.w:setRows(F.ROWS)

  t:eq(s:findChat("ForeverGold: plan", true),
    "ForeverGold: plan 20261104-1830-ab12 (1h 0m old): 2 buy, 2 post, 1 craft, 1 hold", "login line")
  local db = s:db()
  t:eq(#db.sessions, 1, "sessions")
  t:same(db.sessions[1], { id = "1793842200-Travv", char = F.CHAR, realm = "Forever", region = 1,
    faction = "Alliance", version = "1.60.1", build = "70634", iface = 16001, project = 1,
    copperOK = true, planId = "20261104-1830-ab12", loginAt = T0, priorSessions = 0 }, "session record")
  t:check(not ns.UI.frame:IsShown(), "window hidden after login")

  -- bags: BAG_UPDATE_DELAYED is batched to one snapshot 5 s later
  s:bagUpdate()
  w:advance(1)
  s:bagUpdate()
  w:advance(5)
  local inv = db.inventory[F.CHAR]
  t:check(inv ~= nil, "inventory snapshot")
  t:eq(inv.at, T0 + 5, "snapshot time")
  t:same(inv.bags, F.BAGS_EXPECT, "bags")
  t:same(inv.bank, {}, "bank before visiting")
  t:eq(inv.bankAt, 0, "bankAt before visiting")
  t:eq(inv.money, 1234567, "money")

  -- mailbox: Open All re-fires MAIL_INBOX_UPDATE; invoices are recorded once
  w:advance(54, 0.5)
  local tMail = w.now
  t:eq(tMail, T0 + 60, "clock")
  s:openMail(F.mail(T0))
  w:fire("MAIL_INBOX_UPDATE")
  t:eq(#db.invoices, 5, "invoices (letter skipped, re-fires deduped)")
  local inv1 = db.invoices[1]
  t:same(inv1, { seenAt = T0 + 60, char = F.CHAR, daysLeft = (T0 - 2 * 3600 - 1234 + 30 * 86400 - tMail) / 86400,
    type = "seller", item = "Peacebloom", player = "Buyerone", bid = 440, buyout = 440, deposit = 15,
    cut = 22, delay = 0, etaH = 0, etaM = 0, count = 20, commodity = true }, "first invoice")
  t:eq(db.invoices[2].player, nil, "multiple buyers: player nil")
  t:eq(db.invoices[3].type, "seller_temp_invoice", "pending sale")
  t:eq(db.invoices[3].delay, 3600, "money delay")
  t:eq(db.invoices[4].type, "buyer", "purchase invoice")
  t:has(s:findChat("ledger:", true), "recorded 5 AH invoices", "ledger chat line")

  -- bank: read only while open, kept after closing
  w:advance(60, 0.5)
  s:openBank()
  s:closeBank()
  inv = db.inventory[F.CHAR]
  t:same(inv.bank, F.BANK_EXPECT, "bank")
  t:eq(inv.bankAt, T0 + 120, "bankAt")

  -- AH: the window opens (plan has rows), guards are gray, nothing auto-scans
  w:advance(180, 0.5)
  s:openAH("Alliance")
  t:check(ns.UI.frame:IsShown(), "window shown at the AH")
  t:eq(w.ah.replicateCalls, 0, "no automatic ReplicateItems on AH open")
  t:check(s:button("scan"):IsEnabled(), "Scan enabled at the AH")
  t:eq(buyRow(s, 2447).color, "gray", "buy guard before a scan")
  t:has(postRow(s, 785).text, "post at >= 30c (floor, no scan yet)", "post guard before a scan")
  t:same(db.probes.deposit[785], { 100, 100, 192, qty = 40, at = T0 + 300, house = "Alliance" }, "deposit probe 785")
  t:same(db.probes.deposit[2319], { 100, 100, 100, qty = 10, at = T0 + 300, house = "Alliance" },
    "deposit probe 2319 (with the house it was measured at)")

  -- Scan click: request, server reply 4 s later, pack, re-fires ignored
  w:advance(10)
  local chatBefore = #w.chat
  t:check(s:click("scan"), "Scan click")
  t:eq(w.ah.replicateCalls, 1, "one ReplicateItems call")
  t:eq(ns.ScanState(), "waiting", "waiting for the server")
  t:check(not s:button("scan"):IsEnabled(), "Scan disabled while waiting")
  t:eq(db.scanLog[1].result, "other", "pending scanLog entry")
  w:advance(5)
  w:idle()
  t:eq(ns.ScanState(), "idle", "scan finished")
  t:eq(#db.scans, 1, "one scan stored (re-fires ignored)")
  local scan = db.scans[1]
  t:eq(scan.data, F.DATA, "scan data string")
  t:eq(scan.ownData, F.OWNDATA, "own data string")
  t:same({ rows = scan.rows, priced = scan.priced, bidOnly = scan.bidOnly, own = scan.own, items = scan.items },
    F.COUNTS, "scan counts")
  t:eq(scan.v, 1, "scan v")
  t:eq(scan.by, "fg", "scan by")
  t:eq(scan.reqAt, T0 + 310, "scan reqAt")
  t:eq(scan.at, T0 + 314, "scan at")
  t:eq(scan.house, "Alliance", "house")
  t:eq(scan.faction, "Alliance", "faction")
  t:eq(scan.char, F.CHAR, "char")
  t:eq(scan.realm, "Forever", "realm")
  t:eq(scan.region, 1, "region")
  t:eq(scan.packMs, 32, "packMs (read + encode: two 16 ms sim frames)")
  t:eq(db.lastScanAt, T0 + 314, "lastScanAt")
  t:same(db.scanLog[1], { reqAt = T0 + 310, gotAt = T0 + 314, result = "ok", rows = 44, by = "fg" }, "scanLog")
  t:eq(#db.scanLog, 1, "one scanLog entry")
  t:has(table.concat(s:chatSince(chatBefore), "\n"), "scan: 44 rows, 13 items in 0.0 s", "scan chat line")
  t:check(s:button("scan"):IsEnabled(), "Scan enabled again")

  -- guards after the scan
  local pb = buyRow(s, 2447)
  t:eq(pb.color, "green", "Peacebloom buy guard")
  t:has(pb.text, "Peacebloom x60: fill 60 @ 20.", "Peacebloom fill text")
  local bt = buyRow(s, 2450)
  t:eq(bt.color, "red", "Briarthorn buy guard")
  t:eq(bt.text, "Briarthorn x40: fill 40 @ 45.5c (max 40c)", "Briarthorn text")
  t:eq(postRow(s, 785).color, "green", "Mageroyal post guard")
  t:eq(postRow(s, 785).text, "Mageroyal x40: post at 34c, 24h (min 34c, floor 30c)", "Mageroyal post text")
  t:eq(postRow(s, 2319).color, "red", "Medium Leather post guard")
  t:has(postRow(s, 2319).text, "HOLD (market below floor)", "Medium Leather HOLD")

  -- Buy list into Auctionator
  t:check(s:click("buylist"), "Buy list click")
  t:same(w.auc.lists["Claude: tonight"], { "\"Peacebloom\";;;;;;;;;0.0022;;;;60", "\"Briarthorn\";;;;;;;;;0.004;;;;40" },
    "Auctionator list")

  -- logout: SavedVariables written like the client does
  w:advance(600, 0.5)
  s:closeAH()
  w:advance(0.5)
  w.money = 1300000
  local text = s:logout(OUT_SV)
  t:eq(db.inventory[F.CHAR].money, 1300000, "logout snapshot")
  t:has(text, "\nForeverGoldDB = {\n", "SavedVariables header")
  t:has(text, "\t[\"schema\"] = 1,\n", "SavedVariables keyed line")
  t:has(text, "}, -- [1]\n", "SavedVariables positional comment")

  -- next evening: the client reads the file back this time
  local s2 = t:session(baseOpts({ now = T1, db = SV.load(text, "ForeverGoldDB"), plan = (function()
    local p = F.plan(T1)
    p.id = "20261105-1900-cd34"
    p.expectPriorSessions = 1
    return p
  end)() }))
  local w2, db2 = s2.w, s2:db()
  w2:setRows(F.ROWS_B)
  t:eq(#db2.sessions, 2, "sessions after read-back")
  t:eq(db2.sessions[2].priorSessions, 1, "priorSessions read-back probe")
  t:eq(db2.sessions[2].planId, "20261105-1900-cd34", "new plan id")
  w2:advance(60, 0.5)
  s2:openAH("Alliance")
  w2:advance(30)
  w2:foreignScan()
  w2:idle()
  t:eq(#db2.scans, 2, "scans after the captured scan")
  t:eq(db2.scans[2].by, "other", "captured scan by")
  t:eq(db2.scans[2].reqAt, 0, "captured scan reqAt")
  t:eq(db2.scans[2].data, F.DATA_B, "captured data")
  t:eq(db2.scans[2].ownData, F.OWNDATA_B, "captured own data")
  t:eq(db2.lastScanAt, T0 + 314, "lastScanAt untouched by other addons' scans")
  t:same(db2.scanLog[2], { reqAt = 0, gotAt = T1 + 90, result = "ok", rows = 7, by = "other" }, "captured scanLog")
  t:eq(buyRow(s2, 2447).color, "green", "guards use this session's scan")
  s2:openMail({ { sender = "Stormwind Auction House", subject = "Auction successful: Copper Bar (5)",
    money = 214, expiresAt = T1 + 30 * 86400 - 600,
    invoice = { type = "seller", item = "Copper Bar", player = "Smithy", bid = 225, buyout = 225, deposit = 10,
      cut = 11, delay = 0, etaH = 0, etaM = 0, count = 5, commodity = true } } })
  t:eq(#db2.invoices, 6, "invoices after the second evening")
  w2:advance(500, 0.5)
  s2:closeAH()
  local text2 = s2:logout(OUT_SV)
  t:same(db2.inventory[F.CHAR].bank, F.BANK_EXPECT, "bank kept from the earlier visit")
  t:eq(db2.inventory[F.CHAR].bankAt, T0 + 120, "bankAt kept")
  t:same(SV.load(text2, "ForeverGoldDB"), db2, "SavedVariables round trip")
  happyText = text2
  t:info("happy-path SavedVariables: " .. #text2 .. " bytes -> " .. OUT_SV)
end)

scenario("read-back broken: ForeverGoldDB nil, partial or corrupt at load", function(t)
  local s = t:session(baseOpts({ db = nil }))
  local db = s:db()
  t:check(type(db) == "table", "DB created")
  t:eq(db.schema, 1, "schema")
  t:eq(db.settings.neverScan, false, "settings.neverScan default")
  t:eq(db.lastScanAt, 0, "lastScanAt default")
  for _, k in ipairs({ "sessions", "scans", "scanLog", "invoices" }) do t:check(type(db[k]) == "table", k) end
  t:same(db.inventory, {}, "inventory")
  t:same(db.probes, { deposit = {} }, "probes")
  t:eq(db.sessions[1].priorSessions, 0, "priorSessions = 0 when nothing was read back")

  local partial = { sessions = { { id = "1-Old", priorSessions = 0 } }, settings = { neverScan = true }, scans = "junk" }
  local s2 = t:session(baseOpts({ db = partial }))
  local db2 = s2:db()
  t:eq(db2.settings.neverScan, true, "neverScan kept")
  t:same(db2.scans, {}, "bad scans replaced")
  t:eq(#db2.sessions, 2, "old session kept")
  t:eq(db2.sessions[2].priorSessions, 1, "priorSessions counts the read-back")
  t:same(db2.probes, { deposit = {} }, "probes filled in")

  local s3 = t:session(baseOpts({ db = "corrupt" }))
  t:check(type(s3:db()) == "table" and #s3:db().sessions == 1, "non-table DB replaced")
end)

scenario("AH closed mid-scan (while packing and while waiting)", function(t)
  local s = t:session(baseOpts())
  local w, ns = s.w, s.ns
  w:setGenerator(10000, F.bigList(10000, 500))
  s:openAH("Alliance")
  s:click("scan")
  w:advance(4)
  w:frame()
  t:eq(ns.ScanState(), "packing", "packing started")
  w:frames(2)
  local reads = w.ah.infoCalls
  t:check(reads > 0 and reads < 10000, "partially packed")
  s:closeAH()
  t:eq(ns.ScanState(), "idle", "aborted")
  w:frames(20)
  t:eq(w.ah.infoCalls, reads, "no reads after the AH closed")
  local db = s:db()
  t:eq(#db.scans, 0, "no scan stored")
  t:eq(db.lastScanAt, 0, "lastScanAt unchanged")
  t:eq(db.scanLog[1].result, "closed", "scanLog closed")
  t:eq(db.scanLog[1].rows, 10000, "rows announced")
  t:check(s:findChat("auction house closed, scan aborted", true), "abort chat line")
  t:check(not s:button("scan"):IsEnabled(), "Scan disabled with the AH closed")

  -- closed after the last row was read: the string assembly still finishes
  local s3 = t:session(baseOpts())
  s3.w:setGenerator(4000, F.bigList(4000, 3000))
  s3:openAH("Alliance")
  s3:click("scan")
  s3.w:advance(4)
  s3.w:frames(3)
  t:eq(s3.ns.ScanJob() and s3.ns.ScanJob().phase, "encode", "encode phase")
  s3:closeAH()
  s3.w:idle()
  t:eq(#s3:db().scans, 1, "a fully read scan is kept")
  t:eq(s3:db().scanLog[1].result, "ok", "logged ok")

  -- closed while waiting; the late reply arrives with the AH closed and is ignored
  local s2 = t:session(baseOpts())
  s2.w:setRows(F.ROWS)
  s2:openAH("Alliance")
  s2:click("scan")
  s2.w:advance(1)
  s2:closeAH()
  s2.w:advance(10)
  s2.w:idle()
  local db2 = s2:db()
  t:eq(db2.scanLog[1].result, "closed", "closed while waiting")
  t:eq(#db2.scanLog, 1, "late reply not logged")
  t:eq(#db2.scans, 0, "late reply not packed")
  t:check(s2:findChat("open the auction house first", true) == nil, "no stray chat")
  s2:slash("/fg scan")
  t:check(s2:findChat("open the auction house first", true), "scan needs the AH")
end)

scenario("throttle, dropped, noreply, late reply and neverScan", function(t)
  -- plan.lastScanAt 5 min ago: throttled to 10 min
  local p = F.plan(F.T0)
  p.lastScanAt = F.T0 - 300
  local s = t:session(baseOpts({ plan = p }))
  local w, db = s.w, s:db()
  w:setRows(F.ROWS)
  s:openAH("Alliance")
  s:click("scan")
  t:check(s:findChat("next full scan in 10 min", true), "plan throttle message")
  t:eq(w.ah.replicateCalls, 0, "throttled: no request")
  t:eq(#db.scanLog, 0, "throttled: nothing logged")

  -- DB.lastScanAt (read back) newer than the plan's wins: max() of both
  local s2 = t:session(baseOpts({ db = { lastScanAt = F.T0 - 100 } }))
  s2.w:setRows(F.ROWS)
  s2:openAH("Alliance")
  s2:slash("/fg scan")
  t:check(s2:findChat("next full scan in 14 min", true), "DB throttle message")
  t:eq(s2.w.ah.replicateCalls, 0, "no request")
  scanNow(s2, "force")
  t:eq(s2.w.ah.replicateCalls, 1, "scan force requests")
  t:eq(#s2:db().scans, 1, "forced scan stored")

  -- dropped: the event has no payload, so it marks the entry and the wait goes on to 180 s
  local s3 = t:session(baseOpts())
  s3.w:setRows(F.ROWS)
  s3.w.ah.mode = "drop"
  s3:openAH("Alliance")
  s3:click("scan")
  s3.w:advance(2)
  t:eq(s3:db().scanLog[1].result, "dropped", "dropped")
  t:eq(s3.ns.ScanState(), "waiting", "a DROPPED event does not end the wait")
  t:check(not s3:button("scan"):IsEnabled(), "Scan stays off while waiting")
  t:check(s3:findChat("maybe this scan); still waiting up to 3 min", true), "provisional drop chat line")
  s3.w:advance(179, 0.5)
  t:eq(s3.ns.ScanState(), "idle", "idle after the wait")
  t:eq(s3:db().scanLog[1].result, "dropped", "still logged as dropped")
  t:check(s3:button("scan"):IsEnabled(), "Scan enabled after drop")
  t:check(s3:findChat("dropped the request (throttled)", true), "drop chat line")
  t:eq(#s3:db().scans, 0, "no scan after drop")
  t:eq(s3:db().lastScanAt, 0, "no throttle clock without a list")

  -- no reply in 180 s
  local s4 = t:session(baseOpts())
  s4.w:setRows(F.ROWS)
  s4.w.ah.mode = "none"
  s4:openAH("Alliance")
  s4:click("scan")
  s4.w:advance(179, 0.5)
  t:eq(s4.ns.ScanState(), "waiting", "still waiting at 179 s")
  s4.w:advance(2, 0.5)
  t:eq(s4:db().scanLog[1].result, "noreply", "noreply")
  t:eq(s4.ns.ScanState(), "idle", "idle after noreply")

  -- late reply after noreply: captured as another addon's scan
  local s5 = t:session(baseOpts())
  s5.w:setRows(F.ROWS)
  s5.w.ah.mode = "late"
  s5.w.ah.lateDelay = 200
  s5:openAH("Alliance")
  s5:click("scan")
  s5.w:advance(205, 0.5)
  s5.w:idle()
  local db5 = s5:db()
  t:eq(db5.scanLog[1].result, "noreply", "first entry noreply")
  t:eq(db5.scanLog[2].by, "other", "late list logged as other")
  t:eq(db5.scanLog[2].result, "ok", "late list packed")
  t:eq(db5.scans[1].by, "other", "late scan by other")
  t:eq(db5.lastScanAt, 0, "lastScanAt only for our scans")

  -- neverScan: our button refuses, other addons' scans are still captured
  local s6 = t:session(baseOpts({ db = { settings = { neverScan = true } } }))
  s6.w:setRows(F.ROWS)
  s6:openAH("Alliance")
  s6:click("scan")
  t:check(s6:findChat("neverScan is on", true), "neverScan message")
  t:eq(s6.w.ah.replicateCalls, 0, "neverScan: no request")
  s6.w:foreignScan()
  s6.w:idle()
  t:eq(#s6:db().scans, 1, "neverScan still captures")
end)

scenario("scan wait: another addon's DROPPED and an empty list neither end nor fake our scan", function(t)
  -- Scan with a 30 s server wait; 2 s later Buy list starts Auctionator searches and one of their
  -- throttled messages fires DROPPED (no payload). Our list still arrives: it is our scan.
  local s = t:session(baseOpts({ auctionator = "ok" }))
  local w, ns = s.w, s.ns
  w:setRows(F.ROWS)
  w.ah.delay = 30
  s:openAH("Alliance")
  s:click("scan")
  w:advance(2)
  t:check(s:click("buylist"), "Buy list while the scan waits")
  w:fire("AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED")
  t:eq(ns.ScanState(), "waiting", "still waiting for our list")
  w:advance(30)
  w:idle()
  local db = s:db()
  t:same(db.scanLog, { { reqAt = F.T0, gotAt = F.T0 + 30, result = "ok", rows = 44, by = "fg" } },
    "one request, one entry, ok")
  t:eq(#db.scans, 1, "one scan")
  t:eq(db.scans[1].by, "fg", "stored as our scan, not another addon's")
  t:eq(db.lastScanAt, F.T0 + 30, "throttle clock set")
  local calls = w.ah.replicateCalls
  s:click("scan")
  t:eq(w.ah.replicateCalls, calls, "the next Scan click is throttled: no second ReplicateItems")
  t:check(s:findChat("next full scan in", true), "throttle message")

  -- an empty list while ours is pending is not a scan; the real list that follows is ours
  local s2 = t:session(baseOpts())
  local w2 = s2.w
  w2.ah.mode = "none"
  s2:openAH("Alliance")
  s2:click("scan")
  w2.ah.ready = true
  w2:setRows({})
  w2:fire("REPLICATE_ITEM_LIST_UPDATE")
  w2:idle()
  local db2 = s2:db()
  t:eq(#db2.scans, 0, "no 0-row scan stored")
  t:eq(s2.ns.ScanState(), "waiting", "still waiting")
  t:eq(db2.lastScanAt, 0, "no throttle clock from an empty list")
  w2:advance(3)
  w2:setRows(F.ROWS)
  w2:fire("REPLICATE_ITEM_LIST_UPDATE")
  w2:idle()
  t:eq(#db2.scans, 1, "the real list is stored")
  t:eq(db2.scans[1].by, "fg", "and it is ours")
  t:eq(db2.scans[1].rows, 44, "rows")
  t:same(db2.scanLog, { { reqAt = F.T0, gotAt = F.T0 + 3, result = "ok", rows = 44, by = "fg" } }, "one ok entry")
end)

scenario("foreign scan captured (by = other), re-fires ignored, scans capped at 2", function(t)
  local s = t:session(baseOpts())
  local w, db = s.w, s:db()
  w:setRows(F.ROWS)
  s:openAH("Alliance")
  w:advance(20)
  w:foreignScan()
  w:frame()
  w:fire("REPLICATE_ITEM_LIST_UPDATE")
  w:advance(2)
  w:idle()
  t:eq(#db.scans, 1, "one capture despite re-fires")
  t:eq(db.scans[1].by, "other", "by other")
  t:eq(db.scans[1].reqAt, 0, "reqAt 0")
  t:eq(db.scans[1].data, F.DATA, "data")
  t:eq(db.lastScanAt, 0, "lastScanAt untouched")
  t:same(db.scanLog[1], { reqAt = 0, gotAt = F.T0 + 20, result = "ok", rows = 44, by = "other" }, "scanLog")
  t:eq(w.ah.replicateCalls, 0, "we never asked")
  for i = 1, 2 do
    local rows = Stub.deepcopy(F.ROWS_B)
    rows[1].count = 40 + i
    rows[1].buyout = 19 * (40 + i)
    w:setRows(rows)
    w:advance(60, 0.5)
    w:foreignScan()
    w:idle()
  end
  t:eq(#db.scans, 2, "scans capped at 2")
  t:eq(#db.scanLog, 3, "three captures logged")
  t:has(db.scans[2].data, "2447:2:19x42,", "newest scan last")

  -- Neutral house (Blackwater): UnitFactionGroup("npc") is nil
  local s2 = t:session(baseOpts())
  s2.w:setRows(F.ROWS)
  s2:openAH(nil)
  s2.w:foreignScan()
  s2.w:idle()
  t:eq(s2:db().scans[1].house, "Neutral", "neutral house")
  t:eq(s2:db().probes.deposit[785][3], 960, "neutral deposit rate in the probe")
end)

scenario("250k-row scan: chunking, timing, file size", function(t)
  local N = 250000
  local s = t:session(baseOpts({ profile = "real", plan = F.plan(F.T0) }))
  local w, ns = s.w, s.ns
  local gen = F.bigList(N, 8000)
  w:setGenerator(N, gen)
  s:openAH("Alliance")
  s:click("scan")
  w:advance(4)
  local frames, maxFrameMs, t0 = 0, 0, os.clock()
  local startFrame = w.frameNo
  while ns.ScanState() ~= "idle" and frames < 1000 do
    local c = os.clock()
    w:frame()
    local ms = (os.clock() - c) * 1000
    if ms > maxFrameMs then maxFrameMs = ms end
    frames = frames + 1
  end
  local cpuMs = (os.clock() - t0) * 1000
  local db = s:db()
  t:eq(#db.scans, 1, "scan stored")
  local scan = db.scans[1]
  t:eq(scan.rows, N, "rows")
  local maxReads, chunks, total = 0, 0, 0
  for f, n in pairs(w.ah.readsByFrame) do
    if f > startFrame then
      chunks = chunks + 1
      total = total + n
      if n > maxReads then maxReads = n end
    end
  end
  t:eq(maxReads, 2000, "rows read per frame (max)")
  t:eq(chunks, 125, "frames that read rows")
  t:check(total >= N, "every row read")
  t:check(frames > chunks + 1, "string assembly spread over frames too")
  t:check(maxFrameMs < 60, F_("worst frame %.1f ms (limit 60 ms under luajit)", maxFrameMs))
  t:check(scan.priced + scan.bidOnly <= N and scan.priced + scan.bidOnly + scan.own >= N, "row accounting")
  local text = SV.serialize("ForeverGoldDB", db)
  local size = SV.writeFile(BIG_SV, text)
  t:check(size < 20 * 1024 * 1024, "SavedVariables under 20 MB")
  t:eq(SV.load(text, "ForeverGoldDB").scans[1].data, scan.data, "big file reads back")
  t:info(F_("rows %s, items %s, chunks %s x <= %s rows, frames %s", scan.rows, scan.items, chunks, maxReads, frames))
  t:info(F_("pack CPU %.0f ms total, worst frame %.1f ms, packMs %s; at 60 fps ~%.1f s wall", cpuMs, maxFrameMs,
    scan.packMs, frames / 60 + cpuMs / 1000))
  t:info(F_("data %.0f KB, ownData %.1f KB, SavedVariables file %.0f KB -> %s", #scan.data / 1024,
    #scan.ownData / 1024, size / 1024, BIG_SV))
  t:info("chat: " .. (s:findChat("scan: 250000 rows", true) or "?"))
end)

scenario("Auctionator missing: buy rows go to chat", function(t)
  local s = t:session(baseOpts({ auctionator = nil }))
  local before = #s.w.chat
  s:slash("/fg buylist")
  local out = table.concat(s:chatSince(before), "\n")
  t:has(out, "buy list (Auctionator not loaded), buy these by hand:", "fallback header")
  t:has(out, "Peacebloom x60 at <= 22c each", "row 1")
  t:has(out, "Briarthorn x40 at <= 40c each", "row 2")
end)

scenario("Auctionator present: exact search strings, callerID first, failure falls back", function(t)
  local s = t:session(baseOpts({ auctionator = "ok" }))
  local w = s.w
  t:check(s.ns.MakeBuyList(), "list made")
  t:same(w.auc.calls, { "ConvertToSearchString", "ConvertToSearchString", "CreateShoppingList" }, "API calls")
  t:same(w.auc.lists["Claude: tonight"], {
    "\"Peacebloom\";;;;;;;;;0.0022;;;;60",
    "\"Briarthorn\";;;;;;;;;0.004;;;;40",
  }, "search strings")
  t:check(s:findChat("Auctionator list \"Claude: tonight\" has 2 items", true), "chat confirmation")

  -- the stub itself rejects a missing or misplaced callerID
  local v1 = rawget(w.env, "Auctionator").API.v1
  local ok1 = pcall(v1.ConvertToSearchString, { searchString = "Peacebloom" })
  local ok2 = pcall(function() return v1:ConvertToSearchString({ searchString = "Peacebloom" }) end)
  local ok3 = pcall(v1.CreateShoppingList, "Claude: tonight", { "x" })
  t:check(not ok1 and not ok2, "ConvertToSearchString without callerID errors")
  t:check(not ok3, "CreateShoppingList without callerID errors")
  w.auc.misuse = 0

  -- an Auctionator update that breaks the API: chat fallback, no partial list
  local s2 = t:session(baseOpts({ auctionator = "broken" }))
  t:check(not s2.ns.MakeBuyList(), "fallback")
  t:check(s2.w.auc.lists["Claude: tonight"] == nil, "no list")
  t:check(s2:findChat("buy list (Auctionator API error)", true), "fallback chat")
  t:check(s2:findChat("Peacebloom x60 at <= 22c each", true), "rows in chat")

  -- no buy rows
  local p = F.plan(F.T0)
  p.buy = {}
  local s3 = t:session(baseOpts({ auctionator = "ok", plan = p }))
  s3.ns.MakeBuyList()
  t:check(s3:findChat("the plan has no buy rows", true), "empty buy list")
  t:check(next(s3.w.auc.lists) == nil, "nothing created")
end)

scenario("%d overflow trap and %.0f scan numbers", function(t)
  local trips = Stub.formatTrips
  t:check(not pcall(string.format, "%d", 99999999999999), "%d beyond 32 bits errors")
  t:check(not pcall(string.format, "%d", 2147483648), "%d at 2^31 errors")
  t:check(not pcall(string.format, "%5i", -2147483649), "%i below -2^31 errors")
  t:check(not pcall(function() return ("%d items"):format(3e9) end), "method form errors")
  t:eq(string.format("%d|%d", 2147483647, -2147483648), "2147483647|-2147483648", "32-bit edges ok")
  t:eq(string.format("%.0f", 99999999999999), "99999999999999", "%.0f ok")
  t:eq(string.format("%%d %s %d", "x", 5), "%d x 5", "%% handled")
  t:eq(Stub.formatTrips, trips + 4, "trap counted")
  local s = t:session(baseOpts())
  s.w:setRows(F.ROWS)
  s:openAH("Alliance")
  local before = Stub.formatTrips
  scanNow(s)
  t:eq(Stub.formatTrips, before, "the addon never formats a big %d")
  t:has(s:db().scans[1].data, "99999999999999x1;", "joke price written exactly")
  s:slash("/fg status")
  s:slash("/fg print")
  t:eq(Stub.formatTrips, before, "status and print stay 32-bit safe")
end)

scenario("invoice dedupe within a session, cap 500", function(t)
  local s = t:session(baseOpts())
  local w, db = s.w, s:db()
  local mail = F.mail(F.T0)
  s:openMail(mail)
  for _ = 1, 3 do w:fire("MAIL_INBOX_UPDATE") end
  t:eq(#db.invoices, 5, "re-fires deduped")
  local before = #w.chat
  w:fire("MAIL_INBOX_UPDATE")
  t:eq(#w.chat, before, "no chat when nothing new")
  mail[#mail + 1] = { sender = "Stormwind Auction House", subject = "Auction successful: Mageroyal (20)",
    expiresAt = F.T0 + 30 * 86400,
    invoice = { type = "seller", item = "Mageroyal", bid = 700, buyout = 700, deposit = 30, cut = 35, count = 20,
      commodity = true } }
  w:fire("MAIL_INBOX_UPDATE")
  t:eq(#db.invoices, 6, "new invoice recorded")
  t:check(s:findChat("recorded 1 AH invoice.", true), "singular chat line")
  -- an identical twin in the same hour bucket is another sale (two buyers at one price): two
  -- mails with one key in the inbox at once are two records (design.md 1.3)
  local twin = Stub.deepcopy(mail[#mail])
  mail[#mail + 1] = twin
  w:fire("MAIL_INBOX_UPDATE")
  t:eq(#db.invoices, 7, "two identical mails are two sales")
  for _ = 1, 3 do w:fire("MAIL_INBOX_UPDATE") end
  t:eq(#db.invoices, 7, "re-fires add nothing")
  table.remove(mail)
  w:fire("MAIL_INBOX_UPDATE")
  t:eq(#db.invoices, 7, "a mail taken from the inbox records nothing")
  -- two hours later the hour bucket moved, so the spec key no longer matches
  w:advance(7200, 1)
  w:fire("MAIL_INBOX_UPDATE")
  t:info("after 2 h in one session the floor(daysLeft*24) key changes: " .. (#db.invoices - 7)
    .. " invoices recorded again (fgold's expiry-time dedupe drops those)")

  -- the review's case: 20 Peacebloom sold to two buyers at 19:10 and 19:35, both read at 20:00
  local sale = { type = "seller", item = "Peacebloom", bid = 440, buyout = 440, deposit = 15, cut = 22, count = 20,
    commodity = true }
  local s3 = t:session(baseOpts())
  s3:openMail({
    { sender = "Stormwind Auction House", subject = "Auction successful: Peacebloom (20)",
      expiresAt = F.T0 - 50 * 60 + 30 * 86400, invoice = sale },
    { sender = "Stormwind Auction House", subject = "Auction successful: Peacebloom (20)",
      expiresAt = F.T0 - 25 * 60 + 30 * 86400, invoice = sale },
  })
  t:eq(#s3:db().invoices, 2, "two sales, two invoices")

  -- the invoice list keeps the newest 500
  local old = {}
  for i = 1, 520 do old[i] = { seenAt = i, char = F.CHAR, type = "seller", item = "Old " .. i } end
  local s2 = t:session(baseOpts({ db = { invoices = old } }))
  t:eq(#s2:db().invoices, 500, "trimmed at load")
  t:eq(s2:db().invoices[1].item, "Old 21", "oldest dropped first")
  s2:openMail(F.mail(F.T0))
  t:eq(#s2:db().invoices, 500, "still 500")
  t:eq(s2:db().invoices[500].item, "Linen Cloth", "newest last")
end)

scenario("guards: buy green/red/partial, post match/undercut/hold, silver rounding", function(t)
  local function plan(settings)
    local p = F.plan(F.T0)
    p.settings = settings
    p.buy = {
      { id = 2447, name = "Peacebloom", qty = 15, maxUnit = 12 },
      { id = 2447, name = "Peacebloom", qty = 16, maxUnit = 12 },
      { id = 765, name = "Silverleaf", qty = 40, maxUnit = 20 },
      { id = 2449, name = "Earthroot", qty = 10, maxUnit = 30 },
    }
    p.post = {
      { id = 785, name = "Mageroyal", qty = 20, floorUnit = 30, startUnit = 45, duration = 3 },
      { id = 2319, name = "Medium Leather", qty = 10, floorUnit = 60, startUnit = 70, duration = 2 },
      { id = 2453, name = "Bruiseweed", qty = 5, floorUnit = 50, startUnit = 70, duration = 1 },
      { id = 2770, name = "Copper Ore", qty = 20, floorUnit = 30, startUnit = 40, duration = 3 },
      { id = 2840, name = "Copper Bar", qty = 10, floorUnit = 1000, startUnit = 1300, duration = 3 },
    }
    return p
  end
  local R = F.row
  local rows = { R(2447, 5, 50), R(2447, 10, 120), R(2447, 100, 3000), R(765, 20, 300),
    R(785, 10, 400), R(785, 10, 450, { owner = "Travv" }), R(2319, 10, 500), R(2770, 10, 310),
    R(2840, 5, 6170) }

  local s = t:session(baseOpts({ plan = plan({ postMode = "match", undercut = 3 }) }))
  local g = s.ns
  t:eq(g.BuyRows()[1].color, "gray", "no scan: gray")
  t:has(g.BuyRows()[1].text, "no scan yet", "no scan text")
  t:eq(g.PostRows()[1].color, "gray", "no scan: post gray")
  s.w:setRows(rows)
  s:openAH("Alliance")
  s.w:foreignScan()
  s.w:idle()
  local b = g.BuyRows()
  t:eq(b[1].color, "green", "avg 11.33 <= 12")
  t:eq(b[1].text, "Peacebloom x15: fill 15 @ 11.3c (max 12c)", "buy text")
  t:eq(b[2].color, "red", "avg 12.5 > 12")
  t:eq(b[2].text, "Peacebloom x16: fill 16 @ 12.5c (max 12c)", "buy text red")
  t:eq(b[3].color, "green", "short supply but cheap")
  t:eq(b[3].text, "Silverleaf x40: fill 20/40 @ 15c (max 20c)", "partial fill")
  t:eq(b[4].color, "red", "none listed")
  t:eq(b[4].text, "Earthroot x10: none listed (max 30c)", "none listed text")
  local p = g.PostRows()
  t:eq(p[1].text, "Mageroyal x20: post at 40c, 24h (min 40c, floor 30c)", "match (own 45c ignored)")
  t:eq(p[1].color, "green", "match green")
  t:eq(p[2].color, "red", "hold red")
  t:eq(p[2].text, "Medium Leather x10: HOLD (market below floor): min 50c < floor 60c", "hold text")
  t:eq(p[3].text, "Bruiseweed x5: post at 70c, 2h (none listed)", "nobody else lists it")
  t:eq(p[4].text, "Copper Ore x20: post at 31c, 24h (min 31c, floor 30c)", "match above floor")
  t:eq(p[5].text, "Copper Bar x10: post at 12s 34c, 24h (min 12s 34c, floor 10s)", "match in silver")

  local s2 = t:session(baseOpts({ plan = plan({ postMode = "undercut", undercut = 3 }) }))
  s2.w:setRows(rows)
  s2:openAH("Alliance")
  s2.w:foreignScan()
  s2.w:idle()
  local p2 = s2.ns.PostRows()
  t:eq(p2[1].text, "Mageroyal x20: post at 37c, 24h (min 40c, floor 30c)", "undercut")
  t:eq(p2[4].text, "Copper Ore x20: post at 30c, 24h (min 31c, floor 30c)", "undercut clamped to floor")
  t:eq(p2[5].text, "Copper Bar x10: post at 12s 31c, 24h (min 12s 34c, floor 10s)", "undercut in silver")

  -- copperOK == false: suggestions round UP to whole silver
  local s3 = t:session(baseOpts({ copperOK = false, plan = plan({ postMode = "undercut", undercut = 3 }) }))
  s3.w:setRows(rows)
  s3:openAH("Alliance")
  s3.w:foreignScan()
  s3.w:idle()
  t:eq(s3:db().sessions[1].copperOK, false, "copperOK recorded")
  local p3 = s3.ns.PostRows()
  t:eq(p3[1].text, "Mageroyal x20: post at 1s, 24h (min 40c, floor 30c)", "37c rounds up to 1s")
  t:eq(p3[3].text, "Bruiseweed x5: post at 1s, 2h (none listed)", "start rounds up")
  t:eq(p3[5].text, "Copper Bar x10: post at 13s, 24h (min 12s 34c, floor 10s)", "12s 31c rounds up to 13s")

  -- SupportsCopperValues missing: copperOK nil, no rounding
  local s4 = t:session(baseOpts({ copperOK = "missing", plan = plan({ postMode = "match" }) }))
  t:eq(s4:db().sessions[1].copperOK, nil, "copperOK nil without the API")
  s4.w:setRows(rows)
  s4:openAH("Alliance")
  s4.w:foreignScan()
  s4.w:idle()
  t:eq(s4.ns.PostRows()[1].text, "Mageroyal x20: post at 40c, 24h (min 40c, floor 30c)", "no rounding when nil")

  -- the house: plan and scan price one AH; at another AH the rows go gray
  local s5 = t:session(baseOpts({ plan = plan({ postMode = "match" }) }))
  s5.w:setRows(rows)
  s5:openAH("Alliance")
  s5.w:foreignScan()
  s5.w:idle()
  t:eq(s5.ns.PostRows()[1].color, "green", "plan and scan at the Alliance AH")
  s5:closeAH()
  s5:openAH(nil)   -- the neutral AH (15% cut)
  t:eq(s5.ns.BuyRows()[1].color, "gray", "an Alliance plan at the neutral AH")
  t:eq(s5.ns.BuyRows()[1].text, "Peacebloom x15: max 12c, plan is for the Alliance AH, not this Neutral one",
    "buy text at the wrong AH")
  t:eq(s5.ns.PostRows()[1].text, "Mageroyal x20: floor 30c, plan is for the Alliance AH, not this Neutral one",
    "post text at the wrong AH")
  -- a neutral plan, scanned at the Alliance AH, then taken to the neutral AH: scan there first
  local np = plan({ postMode = "match" })
  np.basedOnScan = { at = F.T0 - 7200, market = "us|Forever|Neutral", rows = 41 }
  local s6 = t:session(baseOpts({ plan = np }))
  s6.w:setRows(rows)
  s6:openAH("Alliance")
  s6.w:foreignScan()
  s6.w:idle()
  t:has(s6.ns.PostRows()[1].text, "plan is for the Neutral AH, not this Alliance one", "neutral plan here")
  s6:closeAH()
  s6:openAH(nil)
  t:eq(s6.ns.PostRows()[1].color, "gray", "the scan is from another AH")
  t:has(s6.ns.PostRows()[1].text, "scan this AH (the scan is from the Alliance AH)", "post text")
  local rows2 = Stub.deepcopy(rows)
  rows2[1].count = 6
  s6.w:setRows(rows2)
  s6.w:advance(60)
  s6.w:foreignScan()
  s6.w:idle()
  t:eq(s6.ns.PostRows()[1].color, "green", "after a scan at this AH the guards work again")
  t:eq(s6:db().probes.deposit[785].house, "Neutral", "the probe says where it was measured")
end)

scenario("protected-call traps: every section 6 name is trapped, and none is ever hit", function(t)
  local s = t:session(baseOpts({ auctionator = "ok" }))
  local env = s.w.env
  local missing = {}
  for _, name in ipairs({ "PostItem", "PostCommodity", "ConfirmPostItem", "ConfirmPostCommodity", "PlaceBid",
    "StartCommoditiesPurchase", "ConfirmCommoditiesPurchase", "CancelAuction", "CancelSell", "SendMail",
    "TakeInboxItem", "TakeInboxMoney", "AutoLootMailItem", "DeleteInboxItem", "CraftRecipe", "DoTradeSkill",
    "PickupContainerItem", "UseContainerItem", "RunMacro", "RunMacroText", "RunBinding", "SetOverrideBinding",
    "SendChatMessage", "AcceptTrade" }) do
    if type(rawget(env, name)) ~= "function" then missing[#missing + 1] = name end
  end
  t:eq(table.concat(missing, ","), "", "bare traps")
  t:check(type(rawget(env.C_AuctionHouse, "PostCommodity")) == "function", "C_AuctionHouse traps")
  t:check(type(rawget(env.C_Container, "UseContainerItem")) == "function", "C_Container traps")
  t:check(type(rawget(env.C_TradeSkillUI, "CraftRecipe")) == "function", "C_TradeSkillUI trap")
  t:check(type(rawget(env.C_BattleNet, "SendWhisper")) == "function", "C_BattleNet.SendWhisper trap")
  t:check(type(rawget(env.C_Club, "SendMessage")) == "function", "C_Club.SendMessage trap")

  -- self-test: a trap records the hit even when the caller swallows the error
  t:expectTraps(3)
  t:check(not pcall(env.C_AuctionHouse.PostCommodity), "trap raises")
  t:check(not pcall(s:button("scan").Click, s:button("scan")), "Button:Click is a trap")
  t:check(not pcall(env.CreateFrame, "Button", nil, env.UIParent, "SecureActionButtonTemplate"), "secure template trap")
  t:eq(#s.w.traps, 3, "hits recorded")

  -- a full evening through every addon path: no trap may fire
  local s2 = t:session(baseOpts({ auctionator = "ok" }))
  local w = s2.w
  w:setRows(F.ROWS)
  s2:bagUpdate()
  w:advance(6)
  s2:openMail(F.mail(F.T0))
  s2:openBank()
  s2:closeBank()
  s2:slash("/fg")
  s2:slash("/fg")
  s2:openAH("Alliance")
  scanNow(s2)
  s2:click("buylist")
  for _, cmd in ipairs({ "/fg print", "/fg status", "/fg scan", "/fg scan force", "/fg buylist", "/fg bogus" }) do
    s2:slash(cmd)
    w:advance(6)
    w:idle()
  end
  s2:click("close")
  s2:slash("/fg")
  s2:click("done")
  s2:closeAH()
  s2:logout()
  t:eq(#w.traps, 0, "no protected call in a full evening")
  t:eq(w.reloads, 1, "Done reloads")
end)

scenario("plan loading: missing, invalid, broken, default copy, garbage rows", function(t)
  local s = t:session(baseOpts({ plan = false }))
  t:eq(s.ns.planSource, "missing", "missing PlanData.lua")
  t:eq(s.ns.plan, rawget(s.w.env, "ForeverGoldPlanDefault"), "default plan used")
  t:eq(s:findChat("ForeverGold: plan", true), "ForeverGold: plan none (n/a old): 0 buy, 0 post, 0 craft, 0 hold",
    "login line for the default plan")
  t:eq(s:db().sessions[1].planId, "none", "planId none")
  s:openAH("Alliance")
  t:check(not s.ns.UI.frame:IsShown(), "no auto-open for an empty plan")
  s:slash("/fg")
  t:check(s.ns.UI.frame:IsShown(), "/fg opens it")
  t:has(table.concat(s:uiLines(), "\n"), "No plan yet", "default headline shown")

  local bad = F.plan(F.T0)
  bad.schema = 2
  local s2 = t:session(baseOpts({ plan = bad }))
  t:eq(s2.ns.planSource, "invalid", "schema 2 rejected")
  t:eq(s2.ns.plan.id, "none", "falls back")

  local s3 = t:session(baseOpts({ plan = { source = "ForeverGoldPlan = { schema = 1, id = " } }))
  t:eq(s3.ns.plan.id, "none", "syntax error falls back")
  t:has(s3.loadErrors[1] or "", "PlanData.lua", "load error recorded")

  local s4 = t:session(baseOpts({ plan = { source = "error('boom')" } }))
  t:eq(s4.ns.plan.id, "none", "runtime error falls back")

  local s5 = t:session(baseOpts({ plan = { path = Session.addonDir() .. "PlanDefault.lua" } }))
  t:eq(s5.ns.plan.id, "none", "PlanData.lua copied from PlanDefault.lua works")
  t:eq(#s5.w.errors, 0, "and loads cleanly")

  local src = table.concat({
    "-- Generated by fgold. Do not edit; change plan/draft.json and run: python -m fgold plan --apply",
    "ForeverGoldPlan = {",
    "  schema = 1, id = \"20261105-0412-ab12\", generatedAt = " .. (F.T0 - 120) .. ",",
    "  settings = { postMode = \"match\", undercut = 100 },",
    "  buy = { \"junk\", { id = \"2447\" }, { id = 2447, name = \"Peacebloom\", qty = 5, maxUnit = 30 } },",
    "  post = { 7, { id = 785, name = \"Mageroyal\", qty = 1, floorUnit = 30, startUnit = 30, duration = 9 } },",
    "  craft = { { name = \"no out\" } }, hold = \"nope\",",
    "}",
  }, "\n")
  local piped = F.plan(F.T0)
  piped.headline = "sell |cffff0000now"
  piped.buy[1].name = "Peace|bloom"
  local s7 = t:session(baseOpts({ plan = piped, auctionator = "ok" }))
  s7:slash("/fg")
  t:has(table.concat(s7:uiLines(), "\n"), "sell ||cffff0000now", "plan text pipes escaped in the window")
  t:has(s7.ns.BuyRows()[1].text, "Peace||bloom x60", "escaped in guard text")
  s7.ns.MakeBuyList()
  t:eq(s7.w.auc.lists["Claude: tonight"][1], "\"Peace|bloom\";;;;;;;;;0.0022;;;;60", "raw name for Auctionator")

  local s6 = t:session(baseOpts({ plan = { source = src } }))
  t:eq(s6.ns.planSource, "data", "valid plan used")
  t:eq(s6:findChat("ForeverGold: plan", true),
    "ForeverGold: plan 20261105-0412-ab12 (2m old): 3 buy, 2 post, 1 craft, 0 hold", "raw row counts")
  t:eq(#s6.ns.BuyRows(), 1, "garbage buy rows skipped")
  s6.w:setRows(F.ROWS)
  s6:openAH("Alliance")
  scanNow(s6)
  t:eq(s6.ns.BuyRows()[1].color, "green", "valid row guarded")
  t:eq(s6.ns.PostRows()[1].text, "Mageroyal x1: post at 34c (min 34c, floor 30c)", "unknown duration omitted")
  s6:slash("/fg print")
  t:check(s6:findChat("buy Peacebloom x5", true), "print works with garbage rows")
end)

scenario("inventory: 5 s batching, bank only while open, keep previous bank, Enum fallback", function(t)
  local s = t:session(baseOpts())
  local w, db = s.w, s:db()
  t:eq(db.inventory[F.CHAR], nil, "no snapshot before BAG_UPDATE_DELAYED")
  s:bagUpdate()
  w:advance(2)
  s:bagUpdate()
  w:advance(2)
  s:bagUpdate()
  t:eq(db.inventory[F.CHAR], nil, "batched")
  w:advance(1.5)
  t:eq(db.inventory[F.CHAR].at, F.T0 + 5, "one snapshot 5 s after the first event")
  w.bags[0].slots[5] = { itemID = 2447, count = 7 }
  w:advance(10)
  t:eq(db.inventory[F.CHAR].bags[2447], 35, "no new event, no new snapshot")
  s:bagUpdate()
  w:advance(6)
  t:eq(db.inventory[F.CHAR].bags[2447], 42, "next event, next snapshot")
  t:same(db.inventory[F.CHAR].bank, {}, "bank never opened")

  s:openBank()
  t:same(db.inventory[F.CHAR].bank, F.BANK_EXPECT, "bank read at open")
  w.bags[6].slots[2] = { itemID = 2319, count = 3 }
  s:bagUpdate()
  w:advance(6)
  t:eq(db.inventory[F.CHAR].bank[2319], 3, "bank updates while open")
  s:closeBank()
  w.bags[6].slots[3] = { itemID = 2318, count = 9 }
  s:bagUpdate()
  w:advance(6)
  t:eq(db.inventory[F.CHAR].bank[2318], nil, "closed bank is not read")
  t:eq(db.inventory[F.CHAR].bank[2319], 3, "closed bank kept")
  w.money = 999
  s:logout()
  t:eq(db.inventory[F.CHAR].money, 999, "logout snapshot")

  -- read back: previous bank kept when the bank is not opened this session
  local prev = { inventory = { [F.CHAR] = { at = 1, money = 5, bags = {}, bank = { [2770] = 99 }, bankAt = 77 } } }
  local s2 = t:session(baseOpts({ db = prev }))
  s2:bagUpdate()
  s2.w:advance(6)
  t:same(s2:db().inventory[F.CHAR].bank, { [2770] = 99 }, "previous bank kept")
  t:eq(s2:db().inventory[F.CHAR].bankAt, 77, "previous bankAt kept")

  -- no Enum.BagIndex: fall back to -1 and 5..11
  local bags = F.bags()
  bags[6] = nil
  bags[-1] = { size = 24, slots = { [3] = { itemID = 2589, count = 40 } } }
  bags[7] = { size = 8, slots = { [1] = { itemID = 2454, count = 2 } } }
  local s3 = t:session(baseOpts({ enumBagIndex = false, bags = bags }))
  s3:openBank()
  t:same(s3:db().inventory[F.CHAR].bank, { [2589] = 40, [2454] = 2 }, "literal bank containers")

  -- Forever's enum: -1 is the keyring, never read as bank
  local bags4 = F.bags()
  bags4[-1] = { size = 4, slots = { [1] = { itemID = 5396, count = 1 } } }
  local s4 = t:session(baseOpts({ bags = bags4 }))
  s4:openBank()
  t:eq(s4:db().inventory[F.CHAR].bank[5396], nil, "keyring not counted as bank")

  -- the reagent bag (Enum.BagIndex.ReagentBag = 5) counts as bags; soulbound copies never count
  local bags5 = F.bags()
  bags5[5] = { size = 16, slots = { [1] = { itemID = 2453, count = 20 }, [2] = { itemID = 2318, count = 5 } } }
  bags5[0].slots[6] = { itemID = 785, count = 1, bound = true }
  bags5[6].slots[9] = { itemID = 2450, count = 3, bound = true }
  local s5 = t:session(baseOpts({ bags = bags5 }))
  s5:bagUpdate()
  s5.w:advance(6)
  local b5 = s5:db().inventory[F.CHAR].bags
  t:eq(b5[2453], 20, "reagent bag herbs are in the bags")
  t:eq(b5[2318], 13, "reagent bag leather adds to the bag stacks")
  t:eq(b5[785], 40, "a soulbound copy is not counted")
  s5:openBank()
  t:eq(s5:db().inventory[F.CHAR].bank[2450], 10, "soulbound bank copies are not counted either")
  -- without Enum.BagIndex, container 5 is a bank bag and is not read as a bag
  local bags6 = F.bags()
  bags6[5] = { size = 16, slots = { [1] = { itemID = 2453, count = 20 } } }
  local s6 = t:session(baseOpts({ enumBagIndex = false, bags = bags6 }))
  s6:bagUpdate()
  s6.w:advance(6)
  t:eq(s6:db().inventory[F.CHAR].bags[2453], nil, "no enum: 5 stays a bank container")
end)

scenario("slash commands and the window (Dracula colors, buttons, Done)", function(t)
  local s = t:session(baseOpts({ auctionator = "ok" }))
  local w, ns = s.w, s.ns
  local frame = ns.UI.frame
  t:eq(frame:GetName(), "ForeverGoldFrame", "frame name")
  t:same(frame.__s.templates, { "BackdropTemplate" }, "BackdropTemplate")
  t:check(frame.__s.movable and frame.__s.mouse, "movable")
  t:same(frame.__s.drag, { "LeftButton" }, "drag button")
  local bg = frame.__s.backdropColor
  t:eq(F_("%02x%02x%02x", math.floor(bg[1] * 255 + 0.5), math.floor(bg[2] * 255 + 0.5), math.floor(bg[3] * 255 + 0.5)),
    "282a36", "background #282a36")
  local bc = frame.__s.borderColor
  t:eq(F_("%02x%02x%02x", math.floor(bc[1] * 255 + 0.5), math.floor(bc[2] * 255 + 0.5), math.floor(bc[3] * 255 + 0.5)),
    "bd93f9", "border purple")
  for key, label in pairs({ scan = "Scan", buylist = "Buy list", done = "Done", close = "Close" }) do
    local b = s:button(key)
    t:check(b ~= nil, key .. " button")
    t:eq(b:GetText(), label, key .. " label")
    t:same(b.__s.templates, { "UIPanelButtonTemplate" }, key .. " template")
  end
  t:check(not frame:IsShown(), "hidden by default")
  s:slash("/fg")
  t:check(frame:IsShown(), "/fg shows")
  local lines = s:uiLines()
  t:has(lines[1], "|cffbd93f9ForeverGold|r", "purple title")
  local all = table.concat(lines, "\n")
  t:has(all, "|cffbd93f9Buy|r", "Buy section")
  t:has(all, "|cffbd93f9Post|r", "Post section")
  t:has(all, "|cffbd93f9Craft|r", "Craft section")
  t:has(all, "|cffbd93f9Hold/Vendor|r", "Hold/Vendor section")
  t:has(all, "|cff8be9fd1.|r Mailbox: Open All", "cyan step numbers")
  t:has(all, "|cff6272a4Peacebloom x60: max 22c, no scan yet|r", "gray before scan")
  t:check(not s:button("scan"):IsEnabled(), "Scan disabled away from the AH")
  t:check(not s:click("scan"), "disabled button does not click")
  s:slash("/fg")
  t:check(not frame:IsShown(), "/fg toggles")

  w:setRows(F.ROWS)
  s:openAH("Alliance")
  scanNow(s)
  all = table.concat(s:uiLines(), "\n")
  t:has(all, "|cff50fa7bPeacebloom x60: fill 60 @ 20.", "green row")
  t:has(all, "|cffff5555Briarthorn x40: fill 40 @ 45.5c (max 40c)|r", "red row")
  t:has(all, "scan: 44 rows, <1m ago", "scan status line")
  t:eq(#lines > 5, true, "rows drawn")
  t:eq(frame:GetHeight(), 34 + #s:uiLines() * 14 - 14 + 42, "height follows the line count")
  local long = F.plan(F.T0)
  long.headline = string.rep("Sell herbs into raid night demand. ", 4)
  local sl = t:session(baseOpts({ plan = long }))
  sl:slash("/fg")
  t:eq(sl.ns.UI.frame:GetHeight(), 34 + (#sl:uiLines() - 1 + 1) * 14 + 42, "a long headline takes two lines")

  local n = #w.chat
  s:slash("/fg status")
  local status = table.concat(s:chatSince(n), "\n")
  for _, part in ipairs({ "iface 16001 (Forever)", "build 1.60.1.70634", "project 1", "copperOK true", "house Alliance",
    "priorSessions 0 (plan expects 0)", "plan 20261104-1830-ab12 (1h 0m old, data)", "last scan <1m ago" }) do
    t:has(status, part, "status")
  end
  n = #w.chat
  s:slash("/fg print")
  local printed = table.concat(s:chatSince(n), "\n")
  t:has(printed, "Launch night: sell herbs", "print headline")
  t:has(printed, "1. Mailbox: Open All", "print steps")
  t:has(printed, "buy Peacebloom x60: fill 60", "print buy")
  t:has(printed, "post Mageroyal x40: post at 34c", "print post")
  t:has(printed, "craft Minor Healing Potion x10: +25c each", "print craft")
  t:has(printed, "hold Bruiseweed: Raid nights start Dec 9", "print hold")
  t:has(printed, "vendor Ruined Pelt", "print vendor")
  t:has(printed, "Cold start: no history yet", "print notes")
  n = #w.chat
  s:slash("/fg wat")
  t:has(table.concat(s:chatSince(n), "\n"), "/fg [print | status | scan | scan force | buylist | done]", "usage")
  s:click("close")
  t:check(not frame:IsShown(), "Close hides")

  -- Done: reload, but warn once while a scan is running
  s:slash("/fg done")
  t:eq(w.reloads, 1, "/fg done reloads")
  local rows = Stub.deepcopy(F.ROWS)
  rows[1].count = 2
  w:setRows(rows)
  s:slash("/fg scan force")
  t:eq(ns.ScanState(), "waiting", "scan running")
  s:slash("/fg")
  s:click("done")
  t:eq(w.reloads, 1, "first Done during a scan only warns")
  t:check(s:findChat("Click Done again to reload anyway", true), "warning")
  s:click("done")
  t:eq(w.reloads, 2, "second Done reloads")
end)

scenario("SavedVariables writer: escapes, -- [n] comments, numbers, round trip", function(t)
  local value = {
    schema = 1,
    name = "He said \"hi\" \\ then\nleft\r",
    list = { "a", { x = 1 }, true, 2.5 },
    mixed = { 100, 200, 300, qty = 40, at = 1793842200 },
    big = 99999999999999, neg = -5, float = 29.958333333333332, small = 0.1,
    [2447] = { min = 1 }, flag = false,
  }
  local text = SV.serialize("ForeverGoldDB", value)
  t:has(text, "[\"name\"] = \"He said \\\"hi\\\" \\\\ then\\nleft\\r\",", "escapes")
  t:has(text, "\t\t\"a\", -- [1]\n", "positional string with comment")
  t:has(text, "\t\t}, -- [2]\n", "positional table with comment")
  t:has(text, "\t\t100, -- [1]\n", "array part of a mixed table")
  t:has(text, "\t\t[\"qty\"] = 40,\n", "hash part of a mixed table")
  t:has(text, "[\"big\"] = 99999999999999,", "big integer")
  t:has(text, "[\"float\"] = 29.958333333333332,", "float round trip digits")
  t:has(text, "[\"small\"] = 0.1,", "short float")
  t:has(text, "\t[2447] = {\n", "numeric key")
  t:lacks(text, "e+", "no exponent notation")
  t:same(SV.load(text, "ForeverGoldDB"), value, "round trip")
  t:eq(SV.serialize("ForeverGoldDB", nil), "\nForeverGoldDB = nil\n", "nil DB")
  t:eq(SV.serialize("X", value), SV.serialize("X", SV.load(text, "ForeverGoldDB")), "deterministic")
end)

scenario("list caps trim from the front: sessions 20, scans 2, scanLog 50", function(t)
  local db = { sessions = {}, scans = {}, scanLog = {} }
  for i = 1, 25 do db.sessions[i] = { id = "s" .. i } end
  for i = 1, 5 do db.scans[i] = { at = i } end
  for i = 1, 60 do db.scanLog[i] = { reqAt = i, result = "ok" } end
  local s = t:session(baseOpts({ db = db }))
  local d = s:db()
  t:eq(#d.sessions, 20, "sessions")
  t:eq(d.sessions[1].id, "s7", "oldest sessions dropped (20 kept after the new one)")
  t:eq(d.sessions[20].priorSessions, 20, "priorSessions counted after the load trim")
  t:eq(#d.scans, 2, "scans")
  t:eq(d.scans[1].at, 4, "scans keep the newest")
  t:eq(#d.scanLog, 50, "scanLog")
  t:eq(d.scanLog[1].reqAt, 11, "scanLog keeps the newest")
end)

scenario("not Forever: one warning line, keeps running", function(t)
  local s = t:session(baseOpts({ build = { "1.15.7", "61582", "Jul 2025", 11507 } }))
  local warn = 0
  for _, line in ipairs(s:chat()) do if line:find("is not WoW: Forever", 1, true) then warn = warn + 1 end end
  t:eq(warn, 1, "one warning")
  t:check(s:findChat("ForeverGold: plan 20261104-1830-ab12", true), "login line still printed")
  t:eq(s:db().sessions[1].iface, 11507, "iface recorded")
  s.w:setRows(F.ROWS)
  s:openAH("Alliance")
  scanNow(s)
  t:eq(#s:db().scans, 1, "scans still work")
end)

---------------------------------------------------------------------------
-- --plan mode: load a real PlanData.lua and print machine-readable lines
---------------------------------------------------------------------------
local function runPlan(path)
  local copper = args["copper-ok"]
  local copperOK = true
  if copper == "false" then copperOK = false elseif copper == "missing" then copperOK = "missing" end
  -- Plan age is shown relative to a fixed point 10 min after generatedAt.
  local probe = {}
  local chunk = loadfile(path)
  if chunk then
    setfenv(chunk, probe)
    pcall(chunk, "ForeverGold", {})
  end
  local gen = type(probe.ForeverGoldPlan) == "table" and tonumber(probe.ForeverGoldPlan.generatedAt)
  local now = (gen and gen > 0) and (gen + 600) or F.T0
  -- --sv: the client reads this SavedVariables file back at login (nil = missing file, empty DB).
  local svPath, db = args.sv, nil
  if svPath then
    local ok, v = pcall(SV.readFile, svPath, "ForeverGoldDB")
    if not ok then
      io.stderr:write("problem: --sv " .. svPath .. ": " .. tostring(v) .. "\n")
      print("RESULT: fail")
      return 1
    end
    db = v
    -- Log in after every session already in the file, so repeated runs stay in order.
    for _, old in ipairs(type(db) == "table" and type(db.sessions) == "table" and db.sessions or {}) do
      local at = type(old) == "table" and tonumber(old.loginAt)
      if at and at >= now then now = at + 600 end
    end
  end
  local function start(at, readBack)
    return Session.start({ now = at, db = readBack, plan = { path = path }, auctionator = "ok", copperOK = copperOK,
      money = 1234567, bags = F.bags(), commodities = F.COMMODITIES, sellPrice = F.SELL })
  end
  local s = start(now, db)
  local w = s.w
  w:setRows(F.ROWS)
  for _, e in ipairs(s.loadErrors) do
    if not e:find("file not found", 1, true) then print("PLANERR: " .. e) end
  end
  print("LOGIN: " .. (s:findChat("ForeverGold: plan", true) or ""))
  print("PLANID: " .. tostring(s.ns.plan and s.ns.plan.id))
  print("PLANSRC: " .. tostring(s.ns.planSource))
  s:openAH("Alliance")
  scanNow(s, "force")
  for _, r in ipairs(s.ns.BuyRows()) do print("BUYROW: " .. F_("%.0f", r.id) .. "|" .. r.color .. "|" .. r.text) end
  for _, r in ipairs(s.ns.PostRows()) do print("POSTROW: " .. F_("%.0f", r.id) .. "|" .. r.color .. "|" .. r.text) end
  s.ns.MakeBuyList()
  for _, str in ipairs((w.auc.lists or {})["Claude: tonight"] or {}) do print("AUCSTR: " .. str) end
  local sessions, extra = { s }, {}
  if svPath then
    -- Done = /reload: PLAYER_LOGOUT, the client saves (keeping a .bak), the UI loads again and
    -- PLAYER_LOGIN starts a new session. Then the player logs out and the client saves again.
    w:advance(60, 0.5)
    s:closeAH()
    s:slash("/fg done")
    if w.reloads ~= 1 then extra[#extra + 1] = "Done did not reload the UI (" .. w.reloads .. " reloads)" end
    local text = s:logout(svPath)
    local s2 = start(w.now + 30, SV.load(text, "ForeverGoldDB"))
    sessions[2] = s2
    print("RELOAD: " .. (s2:findChat("ForeverGold: plan", true) or ""))
    s2.w:advance(60, 0.5)
    s2:logout(svPath)
    print("SVOUT: " .. svPath)
  end
  if args.flags["--verbose"] then
    for si, sx in ipairs(sessions) do
      for _, line in ipairs(sx:chat()) do io.stderr:write("chat " .. si .. ": " .. line .. "\n") end
    end
  end
  local ctx = setmetatable({ sessions = sessions, errorsExpected = {}, checks = 0 }, Ctx)
  local problems = postChecks(ctx)
  for _, p in ipairs(extra) do problems[#problems + 1] = p end
  for _, p in ipairs(problems) do io.stderr:write("problem: " .. p .. "\n") end
  print(#problems == 0 and "RESULT: ok" or "RESULT: fail")
  if #problems > 0 then return 1 end
  if s.ns.planSource ~= "data" then return 2 end
  return 0
end

---------------------------------------------------------------------------
-- main
---------------------------------------------------------------------------
Stub.installFormat()

if args.plan then
  local code = runPlan(args.plan)
  Stub.uninstallFormat()
  os.exit(code)
end

local passed, failed = 0, 0
local totalChecks = 0
local t0 = os.clock()
for _, sc in ipairs(scenarios) do
  if not args.only or sc.name:find(args.only, 1, true) then
    local ctx = setmetatable({ sessions = {}, checks = 0, infos = {}, errorsExpected = {} }, Ctx)
    local ok, err = xpcall(function() sc.fn(ctx) end, function(e)
      if type(e) == "table" and e.fail then return e end
      return { crash = tostring(e) .. "\n" .. debug.traceback() }
    end)
    local problems = postChecks(ctx)
    if not ok then
      table.insert(problems, 1, err.fail or err.crash)
    end
    totalChecks = totalChecks + ctx.checks
    if #problems == 0 then
      passed = passed + 1
      print("PASS  " .. sc.name .. "  (" .. ctx.checks .. " checks)")
    else
      failed = failed + 1
      print("FAIL  " .. sc.name)
      for _, p in ipairs(problems) do print("      " .. p:gsub("\n", "\n      ")) end
    end
    for _, line in ipairs(ctx.infos) do print("      " .. line) end
  end
end

print(F_("\n%s scenarios: %s passed, %s failed, %s checks, %.1f s", passed + failed, passed, failed, totalChecks,
  os.clock() - t0))

if failed == 0 and args.flags["--write-fixture"] then
  if happyText then
    SV.writeFile(FIXTURE_SV, happyText)
    print("fixture written: " .. FIXTURE_SV .. " (" .. #happyText .. " bytes)")
  else
    print("fixture NOT written: the happy path did not run (use no --only filter)")
    failed = failed + 1
  end
end

Stub.uninstallFormat()
os.exit(failed == 0 and 0 or 1)
