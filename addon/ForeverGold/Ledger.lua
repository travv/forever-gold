local ADDON, ns = ...

local BAG_WAIT = 5        -- seconds between bag snapshots
local recorded = {}       -- dedupe key -> copies recorded this session
local bagTimer = false
local bankOpen = false
local bankSnap, bankSnapAt

local function fmt(n)
  return string.format("%.0f", n)
end

local function readInbox()
  local db = ns.db
  local now = GetServerTime()
  local char = ns.CharKey()
  local added = 0
  local present = {}  -- key -> copies in the inbox during this walk
  for i = 1, GetInboxNumItems() or 0 do
    local invoiceType, itemName, playerName, bid, buyout, deposit, consignment, moneyDelay, etaHour, etaMin, count, commerceAuction = GetInboxInvoiceInfo(i)
    if invoiceType then
      local daysLeft = tonumber((select(7, GetInboxHeaderInfo(i)))) or 0
      bid, buyout, deposit, consignment, count = bid or 0, buyout or 0, deposit or 0, consignment or 0, count or 0
      local key = table.concat({ invoiceType, tostring(itemName), fmt(count), fmt(bid), fmt(deposit),
        fmt(consignment), fmt(math.floor(daysLeft * 24)) }, "|")
      -- Identical mails are separate sales (two buyers at one price): k of them in the inbox at
      -- once are k records. Re-fires see the same k, so they add nothing.
      present[key] = (present[key] or 0) + 1
      if present[key] > (recorded[key] or 0) then
        recorded[key] = present[key]
        db.invoices[#db.invoices + 1] = {
          seenAt = now, char = char, daysLeft = daysLeft, type = invoiceType,
          item = itemName, player = playerName, bid = bid, buyout = buyout,
          deposit = deposit, cut = consignment, delay = moneyDelay or 0,
          etaH = etaHour or 0, etaM = etaMin or 0, count = count,
          commodity = commerceAuction and true or false,
        }
        added = added + 1
      end
    end
  end
  if added > 0 then
    ns.Trim(db.invoices, ns.CAPS.invoices)
    ns.Print("ledger: recorded " .. fmt(added) .. " AH invoice" .. (added == 1 and "" or "s") .. ".")
  end
end

local function bagIDs()
  local first = BACKPACK_CONTAINER or 0
  local last = NUM_BAG_SLOTS or 4
  local ids = {}
  for bag = first, last do ids[#ids + 1] = bag end
  -- Forever has a reagent bag slot (Enum.BagIndex.ReagentBag = 5): herbs, leather and cloth
  -- often live there. Without the enum, 5 is a bank bag, so it is only added by name.
  local E = Enum and Enum.BagIndex
  local reagent = type(E) == "table" and E.ReagentBag
  if type(reagent) == "number" and reagent > last then ids[#ids + 1] = reagent end
  return ids
end

-- Bank containers by Enum.BagIndex name when present (Forever 70532 has
-- CharacterBankTab_1..9, and -1 is the keyring there), else -1 and 5..11.
local function bankIDs()
  local ids = {}
  local E = Enum and Enum.BagIndex
  if type(E) == "table" then
    for name, id in pairs(E) do
      if type(id) == "number" and (name == "Bank" or name:find("^BankBag_%d+$") or name:find("^CharacterBankTab_%d+$")) then
        ids[#ids + 1] = id
      end
    end
    table.sort(ids)
  end
  if #ids == 0 then ids = { -1, 5, 6, 7, 8, 9, 10, 11 } end
  return ids
end

local function readContainers(ids)
  local out = {}
  for _, bag in ipairs(ids) do
    for slot = 1, C_Container.GetContainerNumSlots(bag) or 0 do
      local info = C_Container.GetContainerItemInfo(bag, slot)
      -- Soulbound copies cannot go on the AH, so the plan must not count them as postable.
      if info and info.itemID and not info.isBound then
        out[info.itemID] = (out[info.itemID] or 0) + (info.stackCount or 1)
      end
    end
  end
  return out
end

function ns.SnapshotInventory()
  local db = ns.db
  if not db then return end
  local key = ns.CharKey()
  local prev = db.inventory[key]
  local now = GetServerTime()
  local inv = { at = now, money = GetMoney(), bags = readContainers(bagIDs()) }
  if bankOpen then
    bankSnap, bankSnapAt = readContainers(bankIDs()), now
  end
  if bankSnap then
    inv.bank, inv.bankAt = bankSnap, bankSnapAt
  elseif type(prev) == "table" and type(prev.bank) == "table" then
    inv.bank, inv.bankAt = prev.bank, tonumber(prev.bankAt) or 0
  else
    inv.bank, inv.bankAt = {}, 0
  end
  db.inventory[key] = inv
end

local handlers = {}

function handlers.MAIL_INBOX_UPDATE()
  readInbox()
end

function handlers.BAG_UPDATE_DELAYED()
  if bagTimer then return end
  bagTimer = true
  C_Timer.After(BAG_WAIT, function()
    bagTimer = false
    ns.SnapshotInventory()
  end)
end

function handlers.BANKFRAME_OPENED()
  bankOpen = true
  ns.SnapshotInventory()
end

function handlers.BANKFRAME_CLOSED()
  bankOpen = false
end

function handlers.PLAYER_LOGOUT()
  ns.SnapshotInventory()
end

local events = CreateFrame("Frame")
events:RegisterEvent("MAIL_INBOX_UPDATE")
events:RegisterEvent("BAG_UPDATE_DELAYED")
events:RegisterEvent("BANKFRAME_OPENED")
events:RegisterEvent("BANKFRAME_CLOSED")
events:RegisterEvent("PLAYER_LOGOUT")
events:SetScript("OnEvent", function(self, event, ...)
  local h = handlers[event]
  if h then h(...) end
end)
