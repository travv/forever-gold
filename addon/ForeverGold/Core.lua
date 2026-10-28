local ADDON, ns = ...

ns.VERSION = "0.1.0"
ns.CAPS = { sessions = 20, scans = 2, scanLog = 50, invoices = 500 }

-- Game detection (design.md 1.2): Forever is interface 16xxx.
local iface = select(4, GetBuildInfo())
ns.iface = iface
ns.isForever = type(iface) == "number" and iface >= 16000 and iface < 17000

-- Dracula palette (design.md 1.7)
ns.COLOR = {
  bg = "282a36", text = "f8f8f2", green = "50fa7b", red = "ff5555",
  orange = "ffb86c", gray = "6272a4", purple = "bd93f9", cyan = "8be9fd",
}

-- Plan text is shown as is: double the pipe so it cannot start a client escape code.
function ns.Esc(text)
  return (tostring(text):gsub("|", "||"))
end

function ns.Paint(color, text)
  return "|cff" .. (ns.COLOR[color] or ns.COLOR.text) .. text .. "|r"
end

function ns.RGB(color)
  local hex = ns.COLOR[color] or ns.COLOR.text
  return tonumber(hex:sub(1, 2), 16) / 255, tonumber(hex:sub(3, 4), 16) / 255, tonumber(hex:sub(5, 6), 16) / 255
end

-- Whole numbers as text. Always %.0f: the client's %d overflows past 32 bits.
function ns.Int(n)
  return string.format("%.0f", n or 0)
end

function ns.Money(c)
  c = math.floor((tonumber(c) or 0) + 0.5)
  local sign = ""
  if c < 0 then sign, c = "-", -c end
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  local parts = {}
  if g > 0 then parts[#parts + 1] = ns.Int(g) .. "g" end
  if s > 0 then parts[#parts + 1] = ns.Int(s) .. "s" end
  if k > 0 or #parts == 0 then parts[#parts + 1] = ns.Int(k) .. "c" end
  return sign .. table.concat(parts, " ")
end

-- Averages keep one decimal while they are below a silver.
function ns.MoneyAvg(v)
  if v < 100 and math.floor(v) ~= v then
    return string.format("%.1fc", v)
  end
  return ns.Money(v)
end

function ns.FmtAge(sec)
  sec = tonumber(sec) or 0
  if sec < 60 then return "<1m" end
  local m = math.floor(sec / 60)
  if m < 60 then return ns.Int(m) .. "m" end
  local h = math.floor(m / 60)
  if h < 48 then return ns.Int(h) .. "h " .. ns.Int(m % 60) .. "m" end
  return ns.Int(math.floor(h / 24)) .. "d " .. ns.Int(h % 24) .. "h"
end

function ns.Print(msg)
  local line = ns.Paint("purple", "ForeverGold") .. ": " .. msg
  if DEFAULT_CHAT_FRAME then
    DEFAULT_CHAT_FRAME:AddMessage(line)
  else
    print(line)
  end
end

-- Plain chat line without the prefix (multi-line output).
function ns.Say(msg)
  if DEFAULT_CHAT_FRAME then
    DEFAULT_CHAT_FRAME:AddMessage(msg)
  else
    print(msg)
  end
end

function ns.CharKey()
  return tostring(UnitName("player") or "?") .. "-" .. tostring(GetRealmName() or "?")
end

-- Drop the oldest entries (front of the list) beyond cap.
function ns.Trim(list, cap)
  local n = #list
  local extra = n - cap
  if extra <= 0 then return end
  for i = 1, cap do list[i] = list[i + extra] end
  for i = n, cap + 1, -1 do list[i] = nil end
end

function ns.CopperOK()
  local f = C_AuctionHouse and C_AuctionHouse.SupportsCopperValues
  if type(f) ~= "function" then return nil end
  local ok, v = pcall(f)
  if ok and type(v) == "boolean" then return v end
  return nil
end

function ns.RefreshUI()
  if ns.UI and ns.UI.Refresh then ns.UI.Refresh() end
end

-- The DB is write-only: it may arrive nil or partial on any login.
function ns.InitDB()
  local db = ForeverGoldDB
  if type(db) ~= "table" then
    db = {}
    ForeverGoldDB = db
  end
  db.schema = 1
  if type(db.settings) ~= "table" then db.settings = {} end
  if db.settings.neverScan == nil then db.settings.neverScan = false end
  if type(db.lastScanAt) ~= "number" then db.lastScanAt = 0 end
  for _, key in ipairs({ "sessions", "scans", "scanLog", "invoices" }) do
    if type(db[key]) ~= "table" then db[key] = {} end
    ns.Trim(db[key], ns.CAPS[key])
  end
  if type(db.inventory) ~= "table" then db.inventory = {} end
  if type(db.probes) ~= "table" then db.probes = {} end
  if type(db.probes.deposit) ~= "table" then db.probes.deposit = {} end
  ns.db = db
end

function ns.PlanAge()
  local at = tonumber(ns.plan and ns.plan.generatedAt) or 0
  if at <= 0 then return "n/a" end
  return ns.FmtAge(GetServerTime() - at)
end

function ns.LoginLine()
  local L = ns.PlanList
  return ("plan %s (%s old): %s buy, %s post, %s craft, %s hold"):format(
    ns.Esc(ns.plan.id or "none"), ns.PlanAge(),
    ns.Int(#L("buy")), ns.Int(#L("post")), ns.Int(#L("craft")), ns.Int(#L("hold")))
end

local function onLogin()
  if not ns.db then ns.InitDB() end
  if not ns.plan then ns.LoadPlan() end
  local db = ns.db
  local now = GetServerTime()
  local version, build = GetBuildInfo()
  local session = {
    id = ns.Int(now) .. "-" .. tostring(UnitName("player") or "?"),
    char = ns.CharKey(),
    realm = GetRealmName(),
    region = GetCurrentRegion(),
    faction = (UnitFactionGroup("player")),
    version = version,
    build = build,
    iface = iface,
    project = WOW_PROJECT_ID,
    copperOK = ns.CopperOK(),
    planId = ns.plan.id or "none",
    loginAt = now,
    priorSessions = #db.sessions,
  }
  db.sessions[#db.sessions + 1] = session
  ns.Trim(db.sessions, ns.CAPS.sessions)
  ns.session = session
  if not ns.isForever then
    ns.Print("interface " .. tostring(iface) .. " is not WoW: Forever (16xxx); running anyway.")
  end
  ns.Print(ns.LoginLine())
  ns.RefreshUI()
end

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:SetScript("OnEvent", function(self, event, arg1)
  if event == "ADDON_LOADED" then
    if arg1 == ADDON then
      self:UnregisterEvent("ADDON_LOADED")
      ns.InitDB()
      ns.LoadPlan()
    end
  elseif event == "PLAYER_LOGIN" then
    onLogin()
  end
end)
