-- Deterministic game data for the harness: AH rows, mail, bags, plans.
local F = {}

F.T0 = 1793842200  -- 2026-11-04 19:30 CT (launch night)
F.T1 = 1793931300  -- 2026-11-05 20:15 CT (the next evening)
F.PLAYER = "Travv"
F.REALM = "Forever"
F.CHAR = "Travv-Forever"

F.NAMES = {
  [2447] = "Peacebloom", [765] = "Silverleaf", [2449] = "Earthroot", [785] = "Mageroyal",
  [2450] = "Briarthorn", [2453] = "Bruiseweed", [2318] = "Light Leather", [2319] = "Medium Leather",
  [2589] = "Linen Cloth", [2770] = "Copper Ore", [2840] = "Copper Bar", [118] = "Minor Healing Potion",
  [2454] = "Elixir of Lion's Strength", [6948] = "Hearthstone",
}

-- Vendor sell prices (copper), used by the stub's deposit model.
F.SELL = {
  [2447] = 5, [765] = 5, [2449] = 6, [785] = 8, [2450] = 10, [2453] = 10, [2318] = 5,
  [2319] = 15, [2589] = 3, [2770] = 5, [2840] = 10, [118] = 5, [2454] = 10,
}

local function row(itemID, count, buyout, extra)
  local r = { itemID = itemID, name = F.NAMES[itemID], count = count, buyout = buyout,
    minBid = math.floor((buyout or 0) * 0.8) }
  for k, v in pairs(extra or {}) do r[k] = v end
  return r
end
F.row = row

-- The launch-night replicate list. Row 0 (first) is the anchor: one
-- Peacebloom far below market, so a 1-based reader would miss it.
F.ROWS = {
  row(2447, 1, 1),                                      -- anchor, 1c
  row(2589, 20, 200),                                   -- Linen 10c
  row(785, 20, 680),                                    -- Mageroyal 34c
  row(2447, 20, 400),                                   -- Peacebloom 20c
  row(2840, 1, 99999999999999),                         -- joke price, 10 billion gold
  row(2450, 20, 900),                                   -- Briarthorn 45c
  row(785, 20, 760, { owner = "Travv" }),               -- own Mageroyal 38c
  row(765, 20, 300),                                    -- Silverleaf 15c
  row(2447, 20, 420),                                   -- Peacebloom 21c
  row(2318, 10, 200),                                   -- Light Leather 20c
  row(0, 1, 100, { name = nil }),                       -- itemID 0: skipped
  row(2449, 20, 560),                                   -- Earthroot 28c
  row(2447, 200, 4400),                                 -- Peacebloom 22c
  row(2589, 20, 0, { minBid = 150 }),                   -- Linen bid-only
  row(118, 5, 300),                                     -- Minor Healing Potion 60c
  row(785, 20, 700),                                    -- Mageroyal 35c
  row(2453, 20, 1200),                                  -- Bruiseweed 60c
  row(2447, 20, 440),                                   -- Peacebloom 22c (merges)
  row(2319, 10, 500),                                   -- Medium Leather 50c
  row(2770, 20, 500),                                   -- Copper Ore 25c
  row(765, 40, 640),                                    -- Silverleaf 16c
  row(2840, 20, 800),                                   -- Copper Bar 40c
  row(118, 5, 350, { owner = "Travv" }),                -- own potion 70c
  row(2454, 5, 450),                                    -- Lion's Strength 90c
  row(2447, 5, 115),                                    -- Peacebloom 23c
  row(2450, 20, 920),                                   -- Briarthorn 46c
  row(2318, 20, 440),                                   -- Light Leather 22c
  row(2447, 0, 0),                                      -- count 0: skipped
  row(2449, 15, 450),                                   -- Earthroot 30c
  row(118, 10, 650),                                    -- potion 65c
  row(2454, 1, 0, { minBid = 80 }),                     -- Lion's Strength bid-only
  row(785, 40, 1600),                                   -- Mageroyal 40c
  row(2318, 2, 45),                                     -- Light Leather 22.5c rounds to 23
  row(2447, 100, 2500),                                 -- Peacebloom 25c
  row(2450, 10, 500),                                   -- Briarthorn 50c
  row(2453, 5, 325),                                    -- Bruiseweed 65c
  row(2319, 10, 550),                                   -- Medium Leather 55c
  row(2770, 10, 300),                                   -- Copper Ore 30c
  row(2840, 5, 225),                                    -- Copper Bar 45c
  row(118, 5, 0, { owner = "Travv", minBid = 200 }),    -- own potion bid-only
  row(2454, 1, 100),                                    -- Lion's Strength 1s
  row(765, 10, 180),                                    -- Silverleaf 18c
  row(2447, 3, 70),                                     -- Peacebloom 23.33c rounds to 23
  row(2589, 20, 240),                                   -- Linen 12c
}

F.DATA = "118:2:60x5,65x10;765:3:15x20,16x40,18x10;785:3:34x20,35x20,40x40;"
  .. "2318:3:20x10,22x20,23x2;2319:2:50x10,55x10;2447:8:1x1,20x20,21x20,22x220,23x8,25x100;"
  .. "2449:2:28x20,30x15;2450:3:45x20,46x20,50x10;2453:2:60x20,65x5;2454:2:90x5,100x1;"
  .. "2589:2:10x20,12x20;2770:2:25x20,30x10;2840:3:40x20,45x5,99999999999999x1;"
F.OWNDATA = "118:1:70x5;785:1:38x20;"
F.COUNTS = { rows = 44, priced = 37, bidOnly = 3, own = 3, items = 13 }

-- The next evening: some herbs sold, prices moved. Captured from another addon's scan.
F.ROWS_B = {
  row(2447, 40, 760),                                   -- Peacebloom 19c
  row(785, 20, 720),                                    -- Mageroyal 36c
  row(2447, 60, 1200),                                  -- Peacebloom 20c
  row(2450, 20, 780),                                   -- Briarthorn 39c
  row(785, 10, 380, { owner = "Travv" }),               -- own Mageroyal 38c
  row(2319, 10, 650),                                   -- Medium Leather 65c
  row(2589, 20, 220),                                   -- Linen 11c
}
F.DATA_B = "785:1:36x20;2319:1:65x10;2447:2:19x40,20x60;2450:1:39x20;2589:1:11x20;"
F.OWNDATA_B = "785:1:38x10;"

-- Mail: three sale invoices, one pending sale, one purchase, one letter.
function F.mail(t0)
  -- Ages are off the hour (+1234 s) so floor(daysLeft * 24) is stable for a while.
  local function mail(hoursOld, sender, subject, invoice, money)
    return { sender = sender, subject = subject, money = money or 0, invoice = invoice,
      expiresAt = t0 - hoursOld * 3600 - 1234 + 30 * 86400 }
  end
  return {
    mail(2, "Stormwind Auction House", "Auction successful: Peacebloom (20)",
      { type = "seller", item = "Peacebloom", player = "Buyerone", bid = 440, buyout = 440,
        deposit = 15, cut = 22, delay = 0, etaH = 0, etaM = 0, count = 20, commodity = true }, 433),
    mail(3, "Stormwind Auction House", "Auction successful: Mageroyal (20)",
      { type = "seller", item = "Mageroyal", player = nil, bid = 680, buyout = 680,
        deposit = 30, cut = 34, delay = 0, etaH = 0, etaM = 0, count = 20, commodity = true }, 676),
    mail(1, "Stormwind Auction House", "Sale pending: Minor Healing Potion (5)",
      { type = "seller_temp_invoice", item = "Minor Healing Potion", player = "Healbot", bid = 300,
        buyout = 300, deposit = 20, cut = 15, delay = 3600, etaH = 20, etaM = 30, count = 5, commodity = true }),
    mail(5, "Stormwind Auction House", "Auction won: Silverleaf (40)",
      { type = "buyer", item = "Silverleaf", player = "Sellerguy", bid = 640, buyout = 640,
        deposit = 0, cut = 0, delay = 0, etaH = 0, etaM = 0, count = 40, commodity = true }),
    mail(30, "Mom", "Happy launch night!", nil, 0),
    mail(26, "Stormwind Auction House", "Auction successful: Linen Cloth (20)",
      { type = "seller", item = "Linen Cloth", player = "Tailorjoe", bid = 220, buyout = 220,
        deposit = 6, cut = 11, delay = 0, etaH = 0, etaM = 0, count = 20, commodity = true }, 215),
  }
end

-- Bags 0..4 and the Forever bank tab (CharacterBankTab_1 = 6).
function F.bags()
  return {
    [0] = { size = 16, slots = {
      [1] = { itemID = 2447, count = 20 }, [2] = { itemID = 2447, count = 15 },
      [3] = { itemID = 2589, count = 12 }, [4] = { itemID = 6948, count = 1 } } },
    [1] = { size = 10, slots = { [1] = { itemID = 2318, count = 8 }, [5] = { itemID = 785, count = 40 } } },
    [2] = { size = 6, slots = {} },
    [6] = { size = 28, slots = { [1] = { itemID = 2770, count = 20 }, [7] = { itemID = 2450, count = 10 } } },
  }
end
F.BAGS_EXPECT = { [2447] = 35, [2589] = 12, [6948] = 1, [2318] = 8, [785] = 40 }
F.BANK_EXPECT = { [2770] = 20, [2450] = 10 }

F.COMMODITIES = { [2447] = true, [765] = true, [2449] = true, [785] = true, [2450] = true,
  [2453] = true, [2318] = true, [2319] = true, [2589] = true, [2770] = true, [2840] = true,
  [118] = true, [2454] = true }

-- A plan as fgold would render it (schema 1).
function F.plan(t0)
  return {
    schema = 1,
    id = "20261104-1830-ab12",
    generatedAt = t0 - 3600,
    basedOnScan = { at = t0 - 7200, market = "us|Forever|Alliance", rows = 41 },
    lastScanAt = t0 - 7200,
    expectPriorSessions = 0,
    settings = { postMode = "match", undercut = 100 },
    headline = "Launch night: sell herbs into the empty market, buy cheap Peacebloom.",
    notes = { "Cold start: no history yet, so no invest calls." },
    steps = { "Mailbox: Open All", "AH: click Scan", "Click Buy list, buy the green rows",
      "Post the green rows with Auctionator", "Click Done" },
    budget = { spendCap = 250000, principal = 5000000 },
    buy = {
      { id = 2447, name = "Peacebloom", qty = 60, maxUnit = 22, why = "Below the 3-day mkt", flip = false },
      { id = 2450, name = "Briarthorn", qty = 40, maxUnit = 40, why = "For Lesser Healing Potions", flip = false },
    },
    craft = {
      { recipe = 1244431, name = "Minor Healing Potion", out = 118, count = 10, profitEach = 25,
        why = "First Aid makes these now" },
    },
    post = {
      { id = 785, name = "Mageroyal", qty = 40, floorUnit = 30, startUnit = 36, duration = 3, why = "Held in bags" },
      { id = 2319, name = "Medium Leather", qty = 10, floorUnit = 60, startUnit = 65, duration = 2, why = "Skinning" },
    },
    hold = { { id = 2453, name = "Bruiseweed", why = "Raid nights start Dec 9" } },
    vendor = { { id = 4865, name = "Ruined Pelt", why = "Vendor beats the AH after the cut" } },
    ref = { [2447] = { min = 20, mkt = 21, q = 400 }, [2450] = { min = 38, mkt = 40, q = 50 } },
  }
end

-- Synthetic big list: n rows, deterministic (Park-Miller LCG, exact in doubles).
function F.bigList(n, itemCount)
  itemCount = itemCount or 8000
  local seed = 12345
  local function rnd(m)
    seed = (seed * 16807) % 2147483647
    return seed % m
  end
  local ids, counts, buyouts, owners = {}, {}, {}, {}
  for i = 1, n do
    local k = rnd(itemCount)
    local id = 1000 + k * 7
    local base = 10 + (k * 37) % 5000
    local count = 1 + rnd(200)
    local unit = base + rnd(math.floor(base / 5) + 1)
    ids[i], counts[i] = id, count
    if rnd(100) < 5 then buyouts[i] = 0 else buyouts[i] = unit * count end
    if rnd(1000) < 3 then owners[i] = "Travv" end
  end
  local function gen(index)
    local i = index + 1
    return "Item " .. ids[i], 134400, counts[i], 1, true, 1, "", 0, 0, buyouts[i], 0, nil, nil,
      owners[i], owners[i] and "Travv-Forever" or nil, 0, ids[i], false
  end
  return gen
end

return F
