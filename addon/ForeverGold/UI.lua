local ADDON, ns = ...

local WIDTH = 470
local PAD = 12
local LINE_H = 14
local TOP = 34          -- below the title
local BOTTOM = 42       -- button row
local MAX_ROWS = 12     -- per section; /fg print shows everything

local UI = { buttons = {} }
ns.UI = UI

local frame, title, lines, used, y

-- One FontString per line; wrap = up to two lines (the headline).
local function addLine(text, wrap)
  used = used + 1
  local fs = lines[used]
  if not fs then
    fs = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    fs:SetJustifyH("LEFT")
    fs:SetTextColor(ns.RGB("text"))
    fs:SetWidth(WIDTH - 2 * PAD)
    lines[used] = fs
  end
  fs:SetWordWrap(wrap and true or false)
  fs:SetMaxLines(wrap and 2 or 1)
  fs:ClearAllPoints()
  fs:SetPoint("TOPLEFT", frame, "TOPLEFT", PAD, -(TOP + y))
  fs:SetText(text)
  fs:Show()
  local rows = (wrap and fs:GetUnboundedStringWidth() > WIDTH - 2 * PAD) and 2 or 1
  y = y + rows * LINE_H
end

local function section(name, rows, render)
  if #rows == 0 then return end
  addLine(ns.Paint("purple", name))
  for i, row in ipairs(rows) do
    if i > MAX_ROWS then
      addLine(ns.Paint("gray", "  +" .. ns.Int(#rows - MAX_ROWS) .. " more: /fg print"))
      break
    end
    addLine("  " .. render(row))
  end
end

local function names(rows)
  local out = {}
  for i, row in ipairs(rows) do out[i] = ns.Esc(row.name or row.id) end
  return table.concat(out, ", ")
end

local function scanStatus()
  local state = ns.ScanState()
  if state == "waiting" then return ns.Paint("orange", "scan: waiting for the server...") end
  if state == "packing" then
    local j = ns.ScanJob()
    return ns.Paint("orange", "scan: reading " .. ns.Int(j and j.n or 0) .. " rows...")
  end
  if ns.lastScan then
    return ns.Paint("gray", "scan: " .. ns.Int(ns.lastScan.rows) .. " rows, " .. ns.FmtAge(GetServerTime() - ns.lastScan.at) .. " ago")
  end
  if not ns.ahOpen then return ns.Paint("gray", "scan: open the AH, then click Scan") end
  return ns.Paint("gray", "scan: none this session, click Scan")
end

local function render()
  used, y = 0, 0
  local plan = ns.plan or {}
  title:SetText(ns.Paint("purple", "ForeverGold") .. ns.Paint("gray", "  plan " .. ns.Esc(plan.id or "none") .. ", " .. ns.PlanAge() .. " old"))
  if type(plan.headline) == "string" and plan.headline ~= "" then addLine(ns.Esc(plan.headline), true) end
  for i, step in ipairs(ns.PlanList("steps")) do
    if i > 5 then break end
    addLine(ns.Paint("cyan", ns.Int(i) .. ".") .. " " .. ns.Esc(step))
  end
  section("Buy", ns.BuyRows(), function(r) return ns.Paint(r.color, r.text) end)
  section("Post", ns.PostRows(), function(r) return ns.Paint(r.color, r.text) end)
  section("Craft", ns.ValidRows("craft", "out"), function(r)
    return ns.Esc(r.name or r.out) .. " x" .. ns.Int(tonumber(r.count) or 0) .. ": +" .. ns.Money(tonumber(r.profitEach) or 0) .. " each"
  end)
  local hold, vendor = ns.ValidRows("hold"), ns.ValidRows("vendor")
  if #hold > 0 or #vendor > 0 then
    addLine(ns.Paint("purple", "Hold/Vendor"))
    if #hold > 0 then addLine("  " .. ns.Paint("orange", "hold: ") .. names(hold)) end
    if #vendor > 0 then addLine("  " .. ns.Paint("gray", "vendor: ") .. names(vendor)) end
  end
  addLine(scanStatus())
  for i = used + 1, #lines do lines[i]:Hide() end
  frame:SetHeight(TOP + y + BOTTOM)
  UI.buttons.scan:SetEnabled(ns.ahOpen and ns.ScanState() == "idle")
end

function UI.Refresh()
  if frame and frame:IsShown() then render() end
end

function UI.Show()
  frame:Show()
  render()
end

function UI.Hide()
  frame:Hide()
end

function UI.Toggle()
  if frame:IsShown() then UI.Hide() else UI.Show() end
end

function UI.OnAuctionHouseShow()
  if ns.PlanHasRows() then UI.Show() else UI.Refresh() end
end

-- Done reloads the UI (saves the DB). Warn once if a scan would be lost.
local doneWarnedAt = 0
function UI.Done()
  local now = GetServerTime()
  if ns.ScanState() ~= "idle" and now - doneWarnedAt > 10 then
    doneWarnedAt = now
    ns.Print("a scan is still running and a reload loses it. Click Done again to reload anyway.")
    return
  end
  C_UI.Reload()
end

local function button(key, text, x, onClick)
  local b = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
  b:SetSize(104, 22)
  b:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", x, 12)
  b:SetText(text)
  b:SetScript("OnClick", onClick)
  UI.buttons[key] = b
  return b
end

local function build()
  frame = CreateFrame("Frame", "ForeverGoldFrame", UIParent, "BackdropTemplate")
  frame:SetSize(WIDTH, TOP + BOTTOM)
  frame:SetPoint("RIGHT", UIParent, "RIGHT", -40, 60)
  frame:SetFrameStrata("HIGH")
  frame:SetClampedToScreen(true)
  frame:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Buttons\\WHITE8X8", edgeSize = 1 })
  local r, g, b = ns.RGB("bg")
  frame:SetBackdropColor(r, g, b, 0.96)
  frame:SetBackdropBorderColor(ns.RGB("purple"))
  frame:EnableMouse(true)
  frame:SetMovable(true)
  frame:RegisterForDrag("LeftButton")
  frame:SetScript("OnDragStart", function(self) self:StartMoving() end)
  frame:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
  title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", frame, "TOPLEFT", PAD, -12)
  title:SetJustifyH("LEFT")
  lines, used, y = {}, 0, 0
  button("scan", "Scan", PAD, function() ns.RequestScan(false) end)
  button("buylist", "Buy list", PAD + 112, function() ns.MakeBuyList() end)
  button("done", "Done", PAD + 224, function() UI.Done() end)
  button("close", "Close", PAD + 336, function() UI.Hide() end)
  UI.buttons.scan:SetEnabled(false)
  UI.frame = frame
  frame:Hide()
  if UISpecialFrames then table.insert(UISpecialFrames, "ForeverGoldFrame") end
end

local function lastScanAge()
  local last = math.max(ns.db and ns.db.lastScanAt or 0, tonumber(ns.plan and ns.plan.lastScanAt) or 0)
  if ns.lastScan then last = math.max(last, ns.lastScan.at) end
  if last <= 0 then return "never" end
  return ns.FmtAge(GetServerTime() - last) .. " ago"
end

local function status()
  local s = ns.session or {}
  local version, build = GetBuildInfo()
  ns.Print("iface " .. tostring(ns.iface) .. (ns.isForever and " (Forever)" or " (not Forever)")
    .. ", build " .. tostring(version) .. "." .. tostring(build) .. ", project " .. tostring(WOW_PROJECT_ID)
    .. ", copperOK " .. tostring(ns.CopperOKNow()) .. ", house " .. tostring(ns.house or "not at AH"))
  local expect = tonumber(ns.plan and ns.plan.expectPriorSessions)
  ns.Say("  priorSessions " .. tostring(s.priorSessions) .. (expect and (" (plan expects " .. ns.Int(expect) .. ")") or "")
    .. ", plan " .. ns.Esc(ns.plan and ns.plan.id) .. " (" .. ns.PlanAge() .. " old, " .. tostring(ns.planSource) .. ")"
    .. ", last scan " .. lastScanAge())
end

local function printPlan()
  local plan = ns.plan or {}
  ns.Print(ns.LoginLine())
  if type(plan.headline) == "string" and plan.headline ~= "" then ns.Say(ns.Esc(plan.headline)) end
  for i, step in ipairs(ns.PlanList("steps")) do ns.Say(ns.Int(i) .. ". " .. ns.Esc(step)) end
  for _, r in ipairs(ns.BuyRows()) do ns.Say(ns.Paint(r.color, "buy " .. r.text)) end
  for _, r in ipairs(ns.PostRows()) do ns.Say(ns.Paint(r.color, "post " .. r.text)) end
  for _, r in ipairs(ns.ValidRows("craft", "out")) do
    ns.Say("craft " .. ns.Esc(r.name or r.out) .. " x" .. ns.Int(tonumber(r.count) or 0) .. ": +" .. ns.Money(tonumber(r.profitEach) or 0) .. " each")
  end
  for _, r in ipairs(ns.ValidRows("hold")) do ns.Say("hold " .. ns.Esc(r.name or r.id) .. (r.why and (": " .. ns.Esc(r.why)) or "")) end
  for _, r in ipairs(ns.ValidRows("vendor")) do ns.Say("vendor " .. ns.Esc(r.name or r.id) .. (r.why and (": " .. ns.Esc(r.why)) or "")) end
  for _, note in ipairs(ns.PlanList("notes")) do ns.Say(ns.Paint("gray", ns.Esc(note))) end
end

SLASH_FOREVERGOLD1 = "/fg"
SlashCmdList.FOREVERGOLD = function(msg)
  local cmd, arg = string.lower(msg or ""):match("^%s*(%S*)%s*(.-)%s*$")
  if cmd == "" then
    UI.Toggle()
  elseif cmd == "print" then
    printPlan()
  elseif cmd == "status" then
    status()
  elseif cmd == "scan" and (arg == "" or arg == "force") then
    ns.RequestScan(arg == "force")
  elseif cmd == "buylist" then
    ns.MakeBuyList()
  elseif cmd == "done" then
    UI.Done()
  else
    ns.Print("/fg [print | status | scan | scan force | buylist | done]")
  end
end

build()
