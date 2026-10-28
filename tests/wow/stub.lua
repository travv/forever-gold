-- Stubbed WoW: Forever API for the ForeverGold harness (luajit, Lua 5.1).
-- Each Stub.new() is one game client: its own globals table (env), frames,
-- events, a controllable clock, a C_Timer queue the harness pumps, and traps
-- that fail the run if the addon touches a protected function.

local Stub = {}
Stub.__index = Stub

local rawformat = string.format
Stub.rawformat = rawformat

---------------------------------------------------------------------------
-- string.format with the client's 32-bit %d
---------------------------------------------------------------------------
local INT_MIN, INT_MAX = -2147483648, 2147483647
Stub.formatTrips = 0

local function checkedFormat(fmt, ...)
  if type(fmt) == "string" and fmt:find("%", 1, true) then
    local argi, i = 0, 1
    while true do
      local p = fmt:find("%", i, true)
      if not p then break end
      if fmt:sub(p + 1, p + 1) == "%" then
        i = p + 2
      else
        local s, e, conv = fmt:find("^[-+ #0]*%d*%.?%d*(%a)", p + 1)
        if not s then break end
        argi = argi + 1
        if conv == "d" or conv == "i" then
          local v = select(argi, ...)
          v = tonumber(v)
          if v and (v > INT_MAX or v < INT_MIN) then
            Stub.formatTrips = Stub.formatTrips + 1
            error(rawformat("bad argument #%s to 'format' (%%%s overflows the client's 32-bit integer: %s)",
              tostring(argi + 1), conv, rawformat("%.0f", v)), 2)
          end
        end
        i = e + 1
      end
    end
  end
  return rawformat(fmt, ...)
end
Stub.checkedFormat = checkedFormat

-- Patches the real string table so ("%d"):format(x) is checked too.
function Stub.installFormat() string.format = checkedFormat end
function Stub.uninstallFormat() string.format = rawformat end

---------------------------------------------------------------------------
-- Static API facts (Forever 1.60.1)
---------------------------------------------------------------------------
-- Enum.BagIndex as datamined for Forever 70532 (Ketho LuaEnum.lua): -1 is the keyring.
local BAG_INDEX = {
  Accountbanktab = -3, Characterbanktab = -2, Keyring = -1, Backpack = 0,
  Bag_1 = 1, Bag_2 = 2, Bag_3 = 3, Bag_4 = 4, ReagentBag = 5,
  CharacterBankTab_1 = 6, CharacterBankTab_2 = 7, CharacterBankTab_3 = 8,
  CharacterBankTab_4 = 9, CharacterBankTab_5 = 10, CharacterBankTab_6 = 11,
  CharacterBankTab_7 = 12, CharacterBankTab_8 = 13, CharacterBankTab_9 = 14,
  AccountBankTab_1 = 15, AccountBankTab_2 = 16, AccountBankTab_3 = 17,
  AccountBankTab_4 = 18, AccountBankTab_5 = 19, AccountBankTab_6 = 20,
  AccountBankTab_7 = 21, AccountBankTab_8 = 22, AccountBankTab_9 = 23,
}
Stub.BAG_INDEX = BAG_INDEX

local KNOWN_EVENTS = {}
for _, e in ipairs({
  "ADDON_LOADED", "PLAYER_LOGIN", "PLAYER_LOGOUT", "PLAYER_ENTERING_WORLD",
  "AUCTION_HOUSE_SHOW", "AUCTION_HOUSE_CLOSED", "AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED",
  "AUCTION_HOUSE_THROTTLED_MESSAGE_QUEUED", "AUCTION_HOUSE_THROTTLED_MESSAGE_SENT",
  "AUCTION_HOUSE_THROTTLED_SYSTEM_READY", "REPLICATE_ITEM_LIST_UPDATE",
  "COMMODITY_SEARCH_RESULTS_UPDATED", "OWNED_AUCTIONS_UPDATED",
  "MAIL_SHOW", "MAIL_CLOSED", "MAIL_INBOX_UPDATE",
  "BAG_UPDATE", "BAG_UPDATE_DELAYED", "BANKFRAME_OPENED", "BANKFRAME_CLOSED",
  "PLAYERBANKSLOTS_CHANGED", "PLAYER_MONEY",
}) do KNOWN_EVENTS[e] = true end
Stub.KNOWN_EVENTS = KNOWN_EVENTS

local KNOWN_TEMPLATES = {
  BackdropTemplate = true, UIPanelButtonTemplate = true,
  UIPanelCloseButton = true, UIPanelScrollFrameTemplate = true,
}

-- Globals the addon may read while they are legitimately nil.
local OPTIONAL_GLOBALS = {
  ForeverGoldDB = true, ForeverGoldPlan = true, ForeverGoldPlanDefault = true, Auctionator = true,
}

-- Protected or input-like functions (design.md section 6 plus close relatives).
local TRAP_GLOBALS = {
  "PostItem", "PostCommodity", "ConfirmPostItem", "ConfirmPostCommodity", "PlaceBid",
  "StartCommoditiesPurchase", "ConfirmCommoditiesPurchase", "CancelAuction", "CancelSell",
  "SendMail", "TakeInboxItem", "TakeInboxMoney", "AutoLootMailItem", "DeleteInboxItem",
  "ReturnInboxItem", "CraftRecipe", "DoTradeSkill", "PickupContainerItem", "UseContainerItem",
  "RunMacro", "RunMacroText", "RunBinding", "SetOverrideBinding", "SetOverrideBindingClick",
  "SetOverrideBindingItem", "SetOverrideBindingMacro", "SetOverrideBindingSpell",
  "SendChatMessage", "AcceptTrade", "PlaceAuctionBid", "StartAuction", "PostAuction",
  "ClickAuctionSellItemButton",
}
local TRAP_NAMESPACED = {
  C_AuctionHouse = { "PostItem", "PostCommodity", "ConfirmPostItem", "ConfirmPostCommodity",
    "PlaceBid", "StartCommoditiesPurchase", "ConfirmCommoditiesPurchase", "CancelAuction", "CancelSell" },
  C_Container = { "PickupContainerItem", "UseContainerItem" },
  C_TradeSkillUI = { "CraftRecipe" },
  C_ChatInfo = { "SendChatMessage" },
  -- Forever's other chat-send APIs (Battle.net whispers, community messages)
  C_BattleNet = { "SendWhisper" },
  C_Club = { "SendMessage" },
}
Stub.TRAP_GLOBALS = TRAP_GLOBALS
Stub.TRAP_NAMESPACED = TRAP_NAMESPACED

local AUCTIONATOR_TERM_KEYS = {
  searchString = true, isExact = true, categoryKey = true, minItemLevel = true, maxItemLevel = true,
  minLevel = true, maxLevel = true, minCraftedLevel = true, maxCraftedLevel = true,
  minPrice = true, maxPrice = true, quality = true, tier = true, expansion = true, quantity = true,
}

---------------------------------------------------------------------------
-- helpers
---------------------------------------------------------------------------
local function copy(t)
  local out = {}
  for k, v in pairs(t) do out[k] = v end
  return out
end

local function deepcopy(v, seen)
  if type(v) ~= "table" then return v end
  seen = seen or {}
  if seen[v] then return seen[v] end
  local out = {}
  seen[v] = out
  for k, x in pairs(v) do out[deepcopy(k, seen)] = deepcopy(x, seen) end
  return out
end
Stub.deepcopy = deepcopy

function Stub.strip(text)
  return (tostring(text):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""))
end

---------------------------------------------------------------------------
-- world
---------------------------------------------------------------------------
function Stub.new(opts)
  opts = opts or {}
  local w = setmetatable({}, Stub)
  w.opts = opts
  -- Integer milliseconds keep GetServerTime() exact; w.now mirrors it in seconds.
  w.ms = math.floor((opts.now or 1793842200) * 1000 + 0.5)
  w.ms0 = w.ms
  w.now = w.ms / 1000
  w.frameMs = 16
  w.profile = opts.profile or "fixed"  -- "fixed": debugprofilestop follows the sim clock
  w.frameNo = 0
  w.simMs = 0
  w.seq = 0
  w.timers = {}
  w.serverQueue = {}
  w.registry = {}
  w.frameList = {}
  w.state = setmetatable({}, { __mode = "k" })
  w.chat = {}
  w.traps = {}
  w.errors = {}
  w.unknown = {}
  w.globalsSet = {}
  w.optional = {}
  w.reloads = 0
  w.player = { name = opts.name or "Travv", faction = opts.faction or "Alliance" }
  w.realm = opts.realm or "Forever"
  w.region = opts.region or 1
  w.build = opts.build or { "1.60.1", "70634", "Oct 2026", 16001 }
  w.money = opts.money or 0
  w.npcFaction = nil
  w.ah = {
    rows = {}, n = nil, gen = nil, ready = false, mode = "reply", delay = 4,
    refires = { 0.05, 0.5 }, replicateCalls = 0, infoCalls = 0, badIndex = 0,
    readsByFrame = {}, copperOK = true, commodities = {}, sellPrice = {}, depositCalls = {},
  }
  w.mail = { items = {} }
  w.bags = {}
  w.bankOpen = false
  if opts.enumBagIndex == false then
    w.enumBagIndex = nil
  else
    w.enumBagIndex = opts.enumBagIndex or BAG_INDEX
  end
  w.bankBags = {}
  if w.enumBagIndex then
    for i = 1, 9 do w.bankBags[BAG_INDEX["CharacterBankTab_" .. i]] = true end
  else
    for _, b in ipairs({ -1, 5, 6, 7, 8, 9, 10, 11 }) do w.bankBags[b] = true end
  end
  w:makeEnv()
  return w
end

function Stub:note(list, item)
  self[list][#self[list] + 1] = item
end

-- Runs addon code like the client does: errors are reported, not fatal.
function Stub:call(label, fn, ...)
  local args, n = { ... }, select("#", ...)
  local ok, err = xpcall(function() return fn(unpack(args, 1, n)) end, debug.traceback)
  if not ok then self:note("errors", label .. ": " .. tostring(err)) end
  return ok, err
end

function Stub:trap(name)
  local w = self
  return function()
    w:note("traps", name .. "\n" .. debug.traceback("", 2))
    error("PROTECTED CALL TRAP: " .. name, 2)
  end
end

function Stub:chatLine(text)
  self.chat[#self.chat + 1] = text
end

function Stub:namespace(name, t)
  local w = self
  return setmetatable(t, {
    __index = function(_, k)
      local full = name .. "." .. tostring(k)
      if not w.optional[full] then w:note("unknown", full) end
      return nil
    end,
  })
end

---------------------------------------------------------------------------
-- frames
---------------------------------------------------------------------------
local REGION, FRAME, BUTTON, BACKDROP, FONTSTRING, TEXTURE = {}, {}, {}, {}, {}, {}

local SCRIPTS = {
  Frame = { OnEvent = true, OnUpdate = true, OnShow = true, OnHide = true, OnDragStart = true,
    OnDragStop = true, OnEnter = true, OnLeave = true, OnMouseDown = true, OnMouseUp = true,
    OnSizeChanged = true, OnLoad = true },
}
SCRIPTS.Button = copy(SCRIPTS.Frame)
SCRIPTS.Button.OnClick = true
SCRIPTS.Button.PreClick = true
SCRIPTS.Button.PostClick = true

-- region methods (all objects)
function REGION:GetName() return self.__s.name end
function REGION:GetParent() return self.__s.parent end
function REGION:SetParent(p) self.__s.parent = p end
function REGION:GetObjectType() return self.__s.type end
function REGION:IsObjectType(t) return self.__s.type == t end
function REGION:SetPoint(point, rel, relPoint, x, y)
  if type(point) ~= "string" then error("SetPoint: point must be a string", 2) end
  local p = self.__s.points
  p[#p + 1] = { point, rel, relPoint, x, y }
end
function REGION:ClearAllPoints() self.__s.points = {} end
function REGION:SetAllPoints(rel) self.__s.points = { { "ALL", rel } } end
function REGION:GetNumPoints() return #self.__s.points end
function REGION:GetPoint(i) local p = self.__s.points[i or 1]; if p then return unpack(p, 1, 5) end end
function REGION:SetSize(w, h)
  if type(w) ~= "number" or type(h) ~= "number" then error("SetSize: numbers expected", 2) end
  self.__s.width, self.__s.height = w, h
end
function REGION:SetWidth(w) self.__s.width = w end
function REGION:SetHeight(h)
  if type(h) ~= "number" then error("SetHeight: number expected", 2) end
  self.__s.height = h
end
function REGION:GetWidth() return self.__s.width end
function REGION:GetHeight() return self.__s.height end
function REGION:GetSize() return self.__s.width, self.__s.height end
function REGION:Show()
  local s = self.__s
  if not s.shown then
    s.shown = true
    if s.scripts.OnShow then s.w:call("OnShow", s.scripts.OnShow, self) end
  end
end
function REGION:Hide()
  local s = self.__s
  if s.shown then
    s.shown = false
    if s.scripts.OnHide then s.w:call("OnHide", s.scripts.OnHide, self) end
  end
end
function REGION:SetShown(v) if v then self:Show() else self:Hide() end end
function REGION:IsShown() return self.__s.shown end
function REGION:IsVisible()
  local s = self.__s
  if not s.shown then return false end
  if s.parent and s.parent.IsVisible then return s.parent:IsVisible() end
  return true
end
function REGION:SetAlpha(a) self.__s.alpha = a end
function REGION:GetAlpha() return self.__s.alpha end

-- frame methods
function FRAME:RegisterEvent(event)
  local s = self.__s
  if type(event) ~= "string" or not KNOWN_EVENTS[event] then
    error("Frame:RegisterEvent(): Attempt to register unknown event \"" .. tostring(event) .. "\"", 2)
  end
  if not s.events[event] then
    s.events[event] = true
    local list = s.w.registry[event] or {}
    s.w.registry[event] = list
    list[#list + 1] = self
  end
end
function FRAME:UnregisterEvent(event)
  local s = self.__s
  s.events[event] = nil
  local list = s.w.registry[event] or {}
  for i = #list, 1, -1 do if list[i] == self then table.remove(list, i) end end
end
function FRAME:UnregisterAllEvents()
  for event in pairs(self.__s.events) do self:UnregisterEvent(event) end
end
function FRAME:IsEventRegistered(event) return self.__s.events[event] == true end
function FRAME:SetScript(name, fn)
  local s = self.__s
  if not (SCRIPTS[s.type] or SCRIPTS.Frame)[name] then
    error(s.type .. ":SetScript(): Unknown script type \"" .. tostring(name) .. "\"", 2)
  end
  if fn ~= nil and type(fn) ~= "function" then error("SetScript: function expected", 2) end
  s.scripts[name] = fn
end
function FRAME:GetScript(name) return self.__s.scripts[name] end
function FRAME:HasScript(name) return (SCRIPTS[self.__s.type] or SCRIPTS.Frame)[name] == true end
function FRAME:HookScript(name, fn)
  local prev = self.__s.scripts[name]
  self.__s.scripts[name] = function(...)
    if prev then prev(...) end
    return fn(...)
  end
end
function FRAME:CreateFontString(name, layer, inherits)
  local w = self.__s.w
  local fs = w:wrap("FontString", name, self)
  fs.__s.font = inherits
  fs.__s.text = nil
  fs.__s.color = { 1, 1, 1, 1 }
  if name then rawset(w.env, name, fs) end
  return fs
end
function FRAME:CreateTexture(name, layer)
  local w = self.__s.w
  return w:wrap("Texture", name, self)
end
function FRAME:EnableMouse(v) self.__s.mouse = v and true or false end
function FRAME:IsMouseEnabled() return self.__s.mouse == true end
function FRAME:SetMovable(v) self.__s.movable = v and true or false end
function FRAME:IsMovable() return self.__s.movable == true end
function FRAME:RegisterForDrag(...) self.__s.drag = { ... } end
function FRAME:StartMoving()
  if not self.__s.movable then error("Frame:StartMoving(): frame is not movable", 2) end
  self.__s.moving = true
end
function FRAME:StopMovingOrSizing() self.__s.moving = false end
function FRAME:SetClampedToScreen(v) self.__s.clamped = v end
function FRAME:SetFrameStrata(v) self.__s.strata = v end
function FRAME:GetFrameStrata() return self.__s.strata or "MEDIUM" end
function FRAME:SetFrameLevel(v) self.__s.level = v end
function FRAME:GetFrameLevel() return self.__s.level or 1 end
function FRAME:SetToplevel(v) self.__s.toplevel = v end
function FRAME:Raise() end
function FRAME:SetID(id) self.__s.id = id end
function FRAME:GetID() return self.__s.id or 0 end

-- BackdropTemplateMixin (only with the BackdropTemplate)
function BACKDROP:SetBackdrop(info)
  if info ~= nil and type(info) ~= "table" then error("SetBackdrop: table expected", 2) end
  self.__s.backdrop = info
end
function BACKDROP:SetBackdropColor(r, g, b, a) self.__s.backdropColor = { r, g, b, a or 1 } end
function BACKDROP:SetBackdropBorderColor(r, g, b, a) self.__s.borderColor = { r, g, b, a or 1 } end

-- button methods
function BUTTON:SetText(t) self.__s.text = t end
function BUTTON:GetText() return self.__s.text end
function BUTTON:Enable() self.__s.enabled = true end
function BUTTON:Disable() self.__s.enabled = false end
function BUTTON:SetEnabled(v) self.__s.enabled = v and true or false end
function BUTTON:IsEnabled() return self.__s.enabled end
function BUTTON:RegisterForClicks(...) self.__s.clicks = { ... } end

-- font strings
function FONTSTRING:SetText(t) self.__s.text = t end
function FONTSTRING:GetText() return self.__s.text end
function FONTSTRING:SetTextColor(r, g, b, a) self.__s.color = { r, g, b, a or 1 } end
function FONTSTRING:GetTextColor() return unpack(self.__s.color) end
function FONTSTRING:SetFontObject(f) self.__s.font = f end
function FONTSTRING:SetFont(path, size, flags) self.__s.font = path; return true end
function FONTSTRING:SetJustifyH(v) self.__s.justifyH = v end
function FONTSTRING:SetJustifyV(v) self.__s.justifyV = v end
function FONTSTRING:SetWordWrap(v) self.__s.wrap = v end
function FONTSTRING:SetNonSpaceWrap(v) self.__s.nonSpaceWrap = v end
function FONTSTRING:SetMaxLines(n) self.__s.maxLines = n end
-- Rough metrics: 6 px per visible character.
function FONTSTRING:GetUnboundedStringWidth() return #(Stub.strip(self.__s.text or "")) * 6 end
function FONTSTRING:GetStringWidth()
  local w = self:GetUnboundedStringWidth()
  if self.__s.wrap and self.__s.width > 0 and w > self.__s.width then return self.__s.width end
  return w
end
function FONTSTRING:GetStringHeight() return 12 end
function FONTSTRING:SetDrawLayer(v) self.__s.layer = v end

-- textures
function TEXTURE:SetColorTexture(r, g, b, a) self.__s.color = { r, g, b, a or 1 } end
function TEXTURE:SetTexture(t) self.__s.texture = t end
function TEXTURE:SetVertexColor(r, g, b, a) self.__s.color = { r, g, b, a or 1 } end
function TEXTURE:SetTexCoord(...) self.__s.texCoord = { ... } end
function TEXTURE:SetDrawLayer(v) self.__s.layer = v end

local function compose(...)
  local out = {}
  for i = 1, select("#", ...) do
    for k, v in pairs((select(i, ...))) do out[k] = v end
  end
  return out
end

local METHODS = {
  Frame = compose(REGION, FRAME),
  Button = compose(REGION, FRAME, BUTTON),
  FontString = compose(REGION, FONTSTRING),
  Texture = compose(REGION, TEXTURE),
}

-- Objects keep their state in a hidden field; methods read it via self.__s.
function Stub:wrap(otype, name, parent, mixins)
  local methods = METHODS[otype]
  if mixins then methods = compose(methods, mixins) end
  local w = self
  local obj = {}
  local s = { w = w, type = otype, name = name, parent = parent, shown = true, points = {},
    scripts = {}, events = {}, width = 0, height = 0, enabled = true, alpha = 1 }
  w.state[obj] = s
  setmetatable(obj, {
    __index = function(t, k)
      if k == "__s" then return s end
      return methods[k]
    end,
  })
  return obj
end

function Stub:createFrame(ftype, name, parent, template)
  local w = self
  if ftype ~= "Frame" and ftype ~= "Button" then
    error("stub CreateFrame: frame type \"" .. tostring(ftype) .. "\" is not stubbed", 3)
  end
  local mixins
  local templates = {}
  if template ~= nil then
    if type(template) ~= "string" then error("CreateFrame: template must be a string", 3) end
    for t in template:gmatch("[^,%s]+") do
      if t:find("Secure") then
        w:note("traps", "CreateFrame with secure template " .. t .. "\n" .. debug.traceback("", 3))
        error("PROTECTED CALL TRAP: secure template " .. t, 3)
      end
      if not KNOWN_TEMPLATES[t] then error("CreateFrame: Couldn't find inherited node \"" .. t .. "\"", 3) end
      templates[#templates + 1] = t
      if t == "BackdropTemplate" then mixins = BACKDROP end
      if t == "UIPanelButtonTemplate" and ftype ~= "Button" then
        error("CreateFrame: UIPanelButtonTemplate needs a Button", 3)
      end
    end
  end
  local f = w:wrap(ftype, name, parent, mixins)
  f.__s.templates = templates
  rawset(f, "Click", w:trap(ftype .. ":Click"))
  if name then rawset(w.env, name, f) end
  w.frameList[#w.frameList + 1] = f
  return f
end

function Stub:fire(event, ...)
  local list = self.registry[event]
  if not list then return end
  local snapshot = copy(list)
  for _, f in ipairs(snapshot) do
    local fn = f.__s.scripts.OnEvent
    if fn and f.__s.events[event] then self:call("event " .. event, fn, f, event, ...) end
  end
end

-- A human click: only enabled, visible buttons run OnClick.
function Stub:userClick(button, mouse)
  if not button then return false, "no button" end
  local s = button.__s
  if not s.enabled then return false, "disabled" end
  if not button:IsVisible() then return false, "hidden" end
  local fn = s.scripts.OnClick
  if not fn then return false, "no OnClick" end
  self:call("OnClick", fn, button, mouse or "LeftButton", false)
  return true
end

---------------------------------------------------------------------------
-- clock and frames
---------------------------------------------------------------------------
local function toMs(seconds)
  return math.floor(seconds * 1000 + 0.5)
end

function Stub:schedule(delay, fn)
  self.seq = self.seq + 1
  self.serverQueue[#self.serverQueue + 1] = { due = self.ms + toMs(delay), seq = self.seq, fn = fn, frame = self.frameNo }
end

function Stub:runDue(queue, label)
  local due, keep = {}, {}
  for _, t in ipairs(queue) do
    if t.due <= self.ms and t.frame < self.frameNo then due[#due + 1] = t else keep[#keep + 1] = t end
  end
  if #due == 0 then return 0 end
  for i = #queue, 1, -1 do queue[i] = nil end
  for i, t in ipairs(keep) do queue[i] = t end
  table.sort(due, function(a, b)
    if a.due ~= b.due then return a.due < b.due end
    return a.seq < b.seq
  end)
  for _, t in ipairs(due) do self:call(label, t.fn) end
  return #due
end

function Stub:frameMsStep(dms)
  self.frameNo = self.frameNo + 1
  self.ms = self.ms + dms
  self.now = self.ms / 1000
  self.simMs = self.simMs + dms
  self:runDue(self.serverQueue, "server")
  self:runDue(self.timers, "C_Timer")
  for _, f in ipairs(self.frameList) do
    local fn = f.__s.scripts.OnUpdate
    if fn and f:IsVisible() then self:call("OnUpdate", fn, f, dms / 1000) end
  end
end

-- One rendered frame (default 16 ms): advance the clock, run due server events and timers.
function Stub:frame(dt)
  self:frameMsStep(dt and math.max(1, toMs(dt)) or self.frameMs)
end

function Stub:frames(n, dt)
  for _ = 1, n do self:frame(dt) end
end

function Stub:advance(seconds, dt)
  local target = self.ms + toMs(seconds)
  local step = dt and math.max(1, toMs(dt)) or self.frameMs
  while self.ms < target do self:frameMsStep(math.min(step, target - self.ms)) end
end

function Stub:busy()
  return #self.timers > 0 or #self.serverQueue > 0
end

-- Pump frames until nothing is queued that is due soon (max frames).
function Stub:idle(maxFrames, dt)
  local n = 0
  maxFrames = maxFrames or 100000
  while n < maxFrames do
    local soon = false
    for _, t in ipairs(self.timers) do if t.due <= self.ms + 1000 then soon = true break end end
    if not soon then
      for _, t in ipairs(self.serverQueue) do if t.due <= self.ms + 1000 then soon = true break end end
    end
    if not soon then break end
    self:frame(dt)
    n = n + 1
  end
  return n
end

---------------------------------------------------------------------------
-- auction house server model
---------------------------------------------------------------------------
function Stub:setRows(rows)
  self.ah.rows, self.ah.gen, self.ah.n = rows, nil, nil
end

function Stub:setGenerator(n, gen)
  self.ah.rows, self.ah.gen, self.ah.n = nil, gen, n
end

function Stub:numRows()
  if self.ah.n then return self.ah.n end
  return #self.ah.rows
end

function Stub:deliverList()
  self.ah.ready = true
  self:fire("REPLICATE_ITEM_LIST_UPDATE")
  for _, d in ipairs(self.ah.refires or {}) do
    self:schedule(d, function() self:fire("REPLICATE_ITEM_LIST_UPDATE") end)
  end
end

function Stub:onReplicateRequest()
  local ah = self.ah
  ah.replicateCalls = ah.replicateCalls + 1
  if ah.mode == "reply" then
    self:schedule(ah.delay, function() self:deliverList() end)
  elseif ah.mode == "drop" then
    self:schedule(1, function() self:fire("AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED") end)
  elseif ah.mode == "late" then
    self:schedule(ah.lateDelay or 200, function() self:deliverList() end)
  end
end

-- Another addon ran ReplicateItems; the list arrives for everyone.
function Stub:foreignScan()
  self:deliverList()
end

---------------------------------------------------------------------------
-- Auctionator.API.v1 (errors unless callerID is the first argument)
---------------------------------------------------------------------------
local function goldText(copper)
  if copper == nil then return "" end
  local s = rawformat("%.4f", copper / 10000)
  s = s:gsub("0+$", ""):gsub("%.$", "")
  return s
end

local function numText(v)
  if v == nil then return "" end
  return tostring(v)
end

-- query;cat;minIlvl;maxIlvl;minLvl;maxLvl;minCraft;maxCraft;minPrice(gold);maxPrice(gold);quality;tier;expansion;qty
function Stub.searchString(term)
  local q = term.isExact and ("\"" .. term.searchString .. "\"") or term.searchString
  return table.concat({
    q, term.categoryKey or "", numText(term.minItemLevel), numText(term.maxItemLevel),
    numText(term.minLevel), numText(term.maxLevel), numText(term.minCraftedLevel),
    numText(term.maxCraftedLevel), goldText(term.minPrice), goldText(term.maxPrice),
    numText(term.quality), numText(term.tier), numText(term.expansion), numText(term.quantity),
  }, ";")
end

function Stub:installAuctionator(mode)
  local w = self
  w.auc = { mode = mode or "ok", lists = {}, converted = {}, calls = {}, misuse = 0 }
  local function verify(callerID, fn)
    w.auc.calls[#w.auc.calls + 1] = fn
    if type(callerID) ~= "string" or callerID == "" then
      w.auc.misuse = w.auc.misuse + 1
      error("Auctionator.API.v1." .. fn .. ": the first argument must be your callerID string", 3)
    end
  end
  local v1 = {}
  function v1.ConvertToSearchString(callerID, term)
    verify(callerID, "ConvertToSearchString")
    if w.auc.mode == "broken" then error("Auctionator.API.v1.ConvertToSearchString: internal error", 2) end
    if type(term) ~= "table" or type(term.searchString) ~= "string" then
      w.auc.misuse = w.auc.misuse + 1
      error("Usage Auctionator.API.v1.ConvertToSearchString(string, table): the table needs a searchString", 2)
    end
    for k, v in pairs(term) do
      if not AUCTIONATOR_TERM_KEYS[k] then
        w.auc.misuse = w.auc.misuse + 1
        error("Auctionator.API.v1.ConvertToSearchString: unknown term field " .. tostring(k), 2)
      end
      if (k == "minPrice" or k == "maxPrice" or k == "quantity") and type(v) ~= "number" then
        w.auc.misuse = w.auc.misuse + 1
        error("Auctionator.API.v1.ConvertToSearchString: " .. k .. " must be a number", 2)
      end
    end
    local s = Stub.searchString(term)
    w.auc.converted[#w.auc.converted + 1] = s
    return s
  end
  function v1.CreateShoppingList(callerID, listName, searchStrings)
    verify(callerID, "CreateShoppingList")
    if type(listName) ~= "string" or type(searchStrings) ~= "table" then
      w.auc.misuse = w.auc.misuse + 1
      error("Usage Auctionator.API.v1.CreateShoppingList(string, string, string[])", 2)
    end
    for _, s in ipairs(searchStrings) do
      if type(s) ~= "string" then
        w.auc.misuse = w.auc.misuse + 1
        error("Auctionator.API.v1.CreateShoppingList: search strings must be strings", 2)
      end
    end
    w.auc.lists[listName] = copy(searchStrings)
    w.auc.lastList = listName
  end
  function v1.GetShoppingListItems(callerID, listName)
    verify(callerID, "GetShoppingListItems")
    return copy(w.auc.lists[listName] or {})
  end
  rawset(w.env, "Auctionator", { API = { v1 = v1 } })
end

---------------------------------------------------------------------------
-- globals
---------------------------------------------------------------------------
function Stub:makeEnv()
  local w = self
  local env = {}
  w.env = env

  -- Lua as the client exposes it: no os, io, require, loadfile or dofile.
  for _, k in ipairs({ "assert", "error", "ipairs", "next", "pairs", "pcall", "xpcall", "select",
    "tonumber", "tostring", "type", "unpack", "setmetatable", "getmetatable", "rawget", "rawset",
    "rawequal" }) do
    env[k] = _G[k]
  end
  env.math = copy(math)
  env.table = copy(table)
  env.string = string  -- shared so ("..."):format() hits the same checked format
  env.coroutine = copy(coroutine)
  env.format = function(...) return string.format(...) end
  env.tinsert, env.tremove = table.insert, table.remove
  env.floor, env.ceil, env.max, env.min, env.abs = math.floor, math.ceil, math.max, math.min, math.abs
  env.strlower, env.strupper, env.strsub, env.strlen = string.lower, string.upper, string.sub, string.len
  env.strtrim = function(s) return (tostring(s):gsub("^%s+", ""):gsub("%s+$", "")) end
  env.wipe = function(t) for k in pairs(t) do t[k] = nil end return t end

  env.print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
    w:chatLine(table.concat(parts, " "))
  end
  local chatFrame = {}
  chatFrame.AddMessage = function(self, msg)
    if self ~= chatFrame then error("DEFAULT_CHAT_FRAME:AddMessage needs ':' syntax", 2) end
    w:chatLine(tostring(msg))
  end
  env.DEFAULT_CHAT_FRAME = chatFrame

  env.GetBuildInfo = function() return unpack(w.build) end
  env.GetServerTime = function() return math.floor(w.ms / 1000) end
  env.GetTime = function() return 3600 + (w.ms - w.ms0) / 1000 end
  env.debugprofilestop = function()
    if w.profile == "real" then return os.clock() * 1000 end
    return w.simMs
  end
  env.GetRealmName = function() return w.realm end
  env.GetCurrentRegion = function() return w.region end
  env.UnitName = function(unit)
    if unit == "player" then return w.player.name, nil end
    if unit == "npc" and w.npcName then return w.npcName, nil end
    return nil
  end
  env.UnitFactionGroup = function(unit)
    if unit == "player" then return w.player.faction, w.player.faction end
    if unit == "npc" then
      if w.npcFaction then return w.npcFaction, w.npcFaction end
      return nil
    end
    return nil
  end
  env.GetMoney = function() return w.money end
  env.WOW_PROJECT_ID = 1
  env.BACKPACK_CONTAINER = 0
  env.NUM_BAG_SLOTS = 4
  env.SlashCmdList = {}
  env.UISpecialFrames = {}

  env.CreateFrame = function(ftype, name, parent, template)
    return w:createFrame(ftype, name, parent, template)
  end
  env.UIParent = w:createFrame("Frame", "UIParent")

  env.C_Timer = w:namespace("C_Timer", {
    After = function(seconds, callback)
      if type(seconds) ~= "number" or seconds < 0 then error("C_Timer.After: seconds must be a number >= 0", 2) end
      if type(callback) ~= "function" then error("C_Timer.After: callback must be a function", 2) end
      w.seq = w.seq + 1
      w.timers[#w.timers + 1] = { due = w.ms + toMs(seconds), seq = w.seq, fn = callback, frame = w.frameNo }
    end,
  })

  env.C_UI = w:namespace("C_UI", { Reload = function() w.reloads = w.reloads + 1 end })

  local ah = w.ah
  env.C_AuctionHouse = w:namespace("C_AuctionHouse", {
    ReplicateItems = function() w:onReplicateRequest() end,
    GetNumReplicateItems = function()
      if not ah.ready then return 0 end
      return w:numRows()
    end,
    GetReplicateItemInfo = function(index)
      if type(index) ~= "number" then error("GetReplicateItemInfo: index must be a number", 2) end
      ah.infoCalls = ah.infoCalls + 1
      ah.readsByFrame[w.frameNo] = (ah.readsByFrame[w.frameNo] or 0) + 1
      if not ah.ready then return end
      if index < 0 or index >= w:numRows() or index ~= math.floor(index) then
        ah.badIndex = ah.badIndex + 1
        return
      end
      if ah.gen then return ah.gen(index) end
      local r = ah.rows[index + 1]
      return r.name, r.texture or 134400, r.count, r.quality or 1, true, r.level or 1, "",
        r.minBid or 0, r.minIncrement or 0, r.buyout, r.bidAmount or 0, nil, nil,
        r.owner, r.owner and (r.owner .. "-" .. w.realm) or nil, r.saleStatus or 0, r.itemID,
        r.hasAllInfo ~= false
    end,
    SupportsCopperValues = function() return ah.copperOK end,
    CalculateCommodityDeposit = function(itemID, duration, quantity)
      if type(itemID) ~= "number" or type(duration) ~= "number" or type(quantity) ~= "number" then
        error("CalculateCommodityDeposit(itemID, duration, quantity): numbers expected", 2)
      end
      if duration ~= 1 and duration ~= 2 and duration ~= 3 then
        error("CalculateCommodityDeposit: duration must be 1, 2 or 3", 2)
      end
      ah.depositCalls[#ah.depositCalls + 1] = { itemID, duration, quantity }
      if not ah.commodities[itemID] then return nil end
      local rate = (w.npcFaction == nil) and 0.25 or 0.05
      local mult = ({ 1, 4, 12 })[duration]
      return math.max(100, math.floor((ah.sellPrice[itemID] or 5) * quantity * rate * mult + 0.5))
    end,
  })
  for ns, names in pairs(TRAP_NAMESPACED) do
    if not env[ns] then env[ns] = w:namespace(ns, {}) end
    for _, n in ipairs(names) do rawset(env[ns], n, w:trap(ns .. "." .. n)) end
  end
  for _, n in ipairs(TRAP_GLOBALS) do env[n] = w:trap(n) end

  -- mail
  local mail = w.mail
  local function mailItem(i)
    if type(i) ~= "number" then error("mail index must be a number", 3) end
    local m = mail.items[i]
    if not m then w:note("unknown", "mail index out of range " .. tostring(i)) end
    return m
  end
  env.GetInboxNumItems = function() return #mail.items, #mail.items end
  env.GetInboxHeaderInfo = function(i)
    local m = mailItem(i)
    if not m then return end
    local daysLeft = (m.expiresAt - w.now) / 86400
    return nil, nil, m.sender, m.subject, m.money or 0, 0, daysLeft, m.hasItem or false,
      m.wasRead or false, false, false, true, false
  end
  env.GetInboxInvoiceInfo = function(i)
    local m = mailItem(i)
    if not m or not m.invoice then return nil end
    local v = m.invoice
    return v.type, v.item, v.player, v.bid, v.buyout, v.deposit, v.cut, v.delay or 0,
      v.etaH or 0, v.etaM or 0, v.count, v.commodity
  end

  -- bags and bank
  env.C_Container = env.C_Container or w:namespace("C_Container", {})
  rawset(env.C_Container, "GetContainerNumSlots", function(bag)
    if type(bag) ~= "number" then error("GetContainerNumSlots: bag must be a number", 2) end
    if w.bankBags[bag] and not w.bankOpen then return 0 end
    local b = w.bags[bag]
    return b and b.size or 0
  end)
  rawset(env.C_Container, "GetContainerItemInfo", function(bag, slot)
    if type(bag) ~= "number" or type(slot) ~= "number" then error("GetContainerItemInfo: numbers expected", 2) end
    if w.bankBags[bag] and not w.bankOpen then return nil end
    local b = w.bags[bag]
    local item = b and b.slots[slot]
    if not item then return nil end
    return { iconFileID = 134400, stackCount = item.count, isLocked = false, quality = 1,
      isReadable = false, hasLoot = false, hyperlink = "|Hitem:" .. item.itemID .. "|h", isFiltered = false,
      hasNoValue = false, itemID = item.itemID, isBound = item.bound == true, itemName = item.name or "?" }
  end)
  if w.enumBagIndex then
    env.Enum = w:namespace("Enum", { BagIndex = w:namespace("Enum.BagIndex", copy(w.enumBagIndex)) })
  else
    w.optional.Enum = true
  end

  setmetatable(env, {
    __index = function(_, k)
      if not OPTIONAL_GLOBALS[k] and not w.optional[k] then w:note("unknown", tostring(k)) end
      return nil
    end,
    __newindex = function(t, k, v)
      w.globalsSet[k] = true
      rawset(t, k, v)
    end,
  })
end

---------------------------------------------------------------------------
-- slash commands, as the chat edit box dispatches them
---------------------------------------------------------------------------
function Stub:slash(text)
  local cmd, rest = text:match("^(/%S+)%s*(.-)$")
  if not cmd then return false end
  cmd = cmd:lower()
  for key, fn in pairs(self.env.SlashCmdList) do
    local i = 1
    while true do
      local alias = rawget(self.env, "SLASH_" .. key .. i)
      if not alias then break end
      if alias:lower() == cmd then
        self:call("slash " .. text, fn, rest, nil)
        return true
      end
      i = i + 1
    end
  end
  return false
end

return Stub
