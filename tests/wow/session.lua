-- One game session: a fresh stubbed client with ForeverGold loaded in TOC
-- order, SavedVariables applied, then ADDON_LOADED / PLAYER_LOGIN fired.
local Stub = require("stub")
local SV = require("svwriter")

local Session = {}
Session.__index = Session

Session.root = "."  -- repo root; run.lua sets it

function Session.addonDir()
  return Session.root .. "/addon/ForeverGold/"
end

function Session.readToc(path)
  local f = assert(io.open(path, "rb"))
  local files = {}
  for line in f:lines() do
    line = line:gsub("\r$", ""):gsub("^%s+", ""):gsub("%s+$", "")
    if line ~= "" and not line:find("^#") then files[#files + 1] = line end
  end
  f:close()
  return files
end

-- opts: db, plan (nil/false = missing file, table, {path=}, {source=}),
-- auctionator ("ok" | "broken" | nil), now, profile, build, enumBagIndex,
-- copperOK (true/false/"missing"), money, bags, npcFaction.
function Session.start(opts)
  opts = opts or {}
  local s = setmetatable({ opts = opts, loadErrors = {}, ns = {} }, Session)
  local w = Stub.new(opts)
  s.w = w
  local env = w.env
  if opts.copperOK == "missing" then
    rawset(env.C_AuctionHouse, "SupportsCopperValues", nil)
    w.optional["C_AuctionHouse.SupportsCopperValues"] = true
  elseif opts.copperOK ~= nil then
    w.ah.copperOK = opts.copperOK
  end
  for id in pairs(opts.commodities or {}) do w.ah.commodities[id] = true end
  for id, v in pairs(opts.sellPrice or {}) do w.ah.sellPrice[id] = v end
  if opts.bags then w.bags = opts.bags end
  if opts.auctionator then w:installAuctionator(opts.auctionator) end

  s.files = Session.readToc(Session.addonDir() .. "ForeverGold.toc")
  for _, file in ipairs(s.files) do
    if file == "PlanData.lua" then
      s:loadPlan(opts.plan)
    else
      s:loadFile(Session.addonDir() .. file, file)
    end
  end
  -- The client applies SavedVariables after the addon's files, then fires ADDON_LOADED.
  rawset(env, "ForeverGoldDB", opts.db)
  w:fire("ADDON_LOADED", "ForeverGold")
  w:fire("ADDON_LOADED", "Blizzard_AuctionHouseUI")
  w:fire("PLAYER_LOGIN")
  w:fire("PLAYER_ENTERING_WORLD", true, false)
  return s
end

function Session:loadChunk(chunk, label)
  setfenv(chunk, self.w.env)
  return self.w:call("load " .. label, chunk, "ForeverGold", self.ns)
end

function Session:loadFile(path, label)
  local chunk, err = loadfile(path)
  if not chunk then
    self.w:note("errors", "load " .. label .. ": " .. tostring(err))
    return false
  end
  return self:loadChunk(chunk, label)
end

-- PlanData.lua is generated: it may be missing, broken or a real plan.
function Session:loadPlan(plan)
  local w = self.w
  if plan == nil or plan == false then
    self.loadErrors[#self.loadErrors + 1] = "PlanData.lua: file not found"
    return
  end
  local chunk, err
  if type(plan) == "table" and plan.path then
    chunk, err = loadfile(plan.path)
  elseif type(plan) == "table" and plan.source then
    chunk, err = loadstring(plan.source, "=PlanData.lua")
  elseif type(plan) == "table" then
    rawset(w.env, "ForeverGoldPlan", Stub.deepcopy(plan))
    return
  end
  if not chunk then
    self.loadErrors[#self.loadErrors + 1] = "PlanData.lua: " .. tostring(err)
    return
  end
  setfenv(chunk, w.env)
  local ok, e = pcall(chunk, "ForeverGold", self.ns)
  if not ok then self.loadErrors[#self.loadErrors + 1] = "PlanData.lua: " .. tostring(e) end
end

-- game actions -----------------------------------------------------------
function Session:openAH(npcFaction)
  self.w.npcFaction = npcFaction
  self.w:fire("AUCTION_HOUSE_SHOW")
end

function Session:closeAH()
  self.w:fire("AUCTION_HOUSE_CLOSED")
end

function Session:button(key)
  return self.ns.UI and self.ns.UI.buttons[key]
end

function Session:click(key)
  return self.w:userClick(self:button(key))
end

function Session:slash(text)
  return self.w:slash(text)
end

function Session:openMail(items)
  self.w.mail.items = items
  self.w:fire("MAIL_SHOW")
  self.w:fire("MAIL_INBOX_UPDATE")
  self.w:frame()
  self.w:fire("MAIL_INBOX_UPDATE")
end

function Session:openBank()
  self.w.bankOpen = true
  self.w:fire("BANKFRAME_OPENED")
end

function Session:closeBank()
  self.w:fire("BANKFRAME_CLOSED")
  self.w.bankOpen = false
end

function Session:bagUpdate()
  self.w:fire("BAG_UPDATE_DELAYED")
end

function Session:db()
  return rawget(self.w.env, "ForeverGoldDB")
end

-- Logout: PLAYER_LOGOUT, then the client writes SavedVariables.
function Session:logout(path)
  self.w:fire("PLAYER_LOGOUT")
  local text = SV.serialize("ForeverGoldDB", self:db())
  if path then SV.writeFile(path, text, true) end
  return text
end

function Session:chat()
  local out = {}
  for i, line in ipairs(self.w.chat) do out[i] = Stub.strip(line) end
  return out
end

function Session:chatSince(n)
  local out = {}
  for i = n + 1, #self.w.chat do out[#out + 1] = Stub.strip(self.w.chat[i]) end
  return out
end

function Session:findChat(pattern, plain)
  for _, line in ipairs(self:chat()) do
    if line:find(pattern, 1, plain) then return line end
  end
  return nil
end

-- Lines the addon drew in its window (color codes kept).
function Session:uiLines()
  local out = {}
  local frame = self.ns.UI and self.ns.UI.frame
  if not frame then return out end
  for fs, st in pairs(self.w.state) do
    if st.type == "FontString" and st.parent == frame and st.shown and st.text then
      out[#out + 1] = { y = (st.points[1] and st.points[1][5]) or 0, text = st.text }
    end
  end
  table.sort(out, function(a, b) return a.y > b.y end)
  local texts = {}
  for i, l in ipairs(out) do texts[i] = l.text end
  return texts
end

return Session
