# ForeverGold design spec (the contract)

The addon, the Python CLI, the luajit test harness and the Claude skill are built in parallel against this file. **If code and this spec disagree, the spec wins until it is deliberately changed.** Field names are exact.

## 0. Principles
1. **Claude thinks, Travis clicks.** The addon never calls a protected function (see section 6). Buying and posting happen in Auctionator's or Blizzard's UI with a human click per action.
2. **Files are the only bridge.** Data leaves the game through SavedVariables, written at `/reload`, logout, quit or disconnect. Data enters through `PlanData.lua`, a file inside the addon folder that Python overwrites while the game is closed or before a `/reload`.
3. **The addon's DB is write-only.** The Forever beta sometimes did not read SavedVariables back at login, so the addon must work with an empty `ForeverGoldDB` on every login. Python archives every version of the file it sees, so nothing is lost if the game forgets.
4. **Python does all the arithmetic.** Claude only edits a draft plan inside a fixed schema, and `fgold plan --apply` validates it before anything reaches the game.
5. **Stdlib only.** Python 3.11+ standard library, no pip installs (tests use `unittest`). The addon is Lua 5.1 (no `goto`, no `//`, no bit operators, no `os`/`io`).
6. **No em-dashes** (U+2014) anywhere in the repo: code, comments, docs, generated text.

## 1. Addon: `addon/ForeverGold/`

### 1.1 TOC (`ForeverGold.toc`)
```
## Interface: 16001
## Title: ForeverGold
## Notes: Shows a Claude-made gold plan and records AH scans and sales. Never buys, posts or cancels for you.
## Author: travv
## Version: 0.1.0
## SavedVariables: ForeverGoldDB
## OptionalDeps: Auctionator
PlanDefault.lua
PlanData.lua
Core.lua
Scan.lua
Ledger.lua
Bridge.lua
UI.lua
```
- `PlanDefault.lua` (committed): defines `ForeverGoldPlanDefault` (an empty schema-1 plan with `id = "none"`).
- `PlanData.lua` (generated, gitignored; `fgold install` creates it from the default): defines the global `ForeverGoldPlan`. If it is missing or invalid, the addon uses `ForeverGoldPlanDefault`.
- Shared namespace: every file starts with `local ADDON, ns = ...`.

### 1.2 Game detection
`local iface = select(4, GetBuildInfo())`. Forever is `iface >= 16000 and iface < 17000`. Record `WOW_PROJECT_ID` for information only (reported as 1 on Forever, like retail). If not Forever, print one warning line and keep running (useful for testing).

### 1.3 SavedVariables `ForeverGoldDB` (account-wide)
```lua
ForeverGoldDB = {
  schema = 1,
  settings = { neverScan = false },          -- neverScan: only capture other addons' scans
  lastScanAt = 0,                            -- unix seconds of our last successful full scan
  sessions = { <session>, ... },             -- newest last, keep <= 20
  scans = { <scan>, ... },                   -- newest last, keep <= 2
  scanLog = { <scanLogEntry>, ... },         -- newest last, keep <= 50
  invoices = { <invoice>, ... },             -- newest last, keep <= 500
  inventory = { ["Name-Realm"] = <inv>, ... },
  probes = { deposit = { [itemID] = <depositProbe>, ... } },
}
```
Initialize missing keys at `ADDON_LOADED` (the table may be nil or partial). Trim lists from the front when over the cap.

**session** (appended at `PLAYER_LOGIN`):
`{ id = "<loginAt>-<Name>", char = "Name-Realm", realm = GetRealmName(), region = GetCurrentRegion(), faction = UnitFactionGroup("player"), version = (GetBuildInfo()), build = select(2, GetBuildInfo()), iface = <int>, project = WOW_PROJECT_ID, copperOK = <bool or nil>, planId = <ForeverGoldPlan.id or "none">, loginAt = GetServerTime(), priorSessions = <#sessions before appending> }`
- `copperOK`: `C_AuctionHouse.SupportsCopperValues()` if the function exists (pcall), else nil. Refresh it at `AUCTION_HOUSE_SHOW` too.
- `priorSessions` is the read-back probe: 0 on every login means the game is not reading SavedVariables back.

**scan** (one per successful full scan):
`{ v = 1, at = <unix>, reqAt = <unix or 0>, by = "fg" | "other", char = "Name-Realm", realm = <str>, region = <int>, faction = "Alliance"|"Horde", house = "Alliance"|"Horde"|"Neutral", rows = <int>, priced = <int>, bidOnly = <int>, own = <int>, items = <int>, packMs = <int>, data = "<scan string>", ownData = "<scan string>" }`
- `house`: at `AUCTION_HOUSE_SHOW`, `UnitFactionGroup("npc")`; nil means `"Neutral"`.
- `rows`: `C_AuctionHouse.GetNumReplicateItems()`. `priced`: rows with buyout > 0 that went into `data`. `bidOnly`: rows with buyout 0 or nil. `own`: rows where `owner ~= nil` (only your own auctions return an owner); these go to `ownData`, not `data`.
- **Scan string grammar:** `entry*` where `entry := itemID ":" auctions ":" point ("," point)* ";"` and `point := unit "x" qty`. All integers, no spaces. Entries sorted by itemID ascending; points by unit ascending; equal units merged (qty summed). Example: `2447:37:22x40,23x200,25x1600;765:3:150x20;`.
  - `unit = floor(buyoutPrice / count + 0.5)` copper; `qty = count`; `auctions` = number of rows for that item.
  - Skip rows with `itemID == nil or itemID <= 0` or `count == nil or count <= 0`.
  - **Write numbers with `string.format("%.0f", n)`**, never `%d` (32-bit overflow in the client).

**scanLogEntry:** `{ reqAt = <unix or 0>, gotAt = <unix or 0>, result = "ok"|"dropped"|"closed"|"noreply"|"other", rows = <int or 0>, by = "fg"|"other" }`

**invoice** (from the mailbox):
`{ seenAt = <unix>, char = "Name-Realm", daysLeft = <float>, type = "seller"|"seller_temp_invoice"|"buyer", item = <name>, player = <name or nil>, bid = <copper>, buyout = <copper>, deposit = <copper>, cut = <copper>, delay = <int>, etaH = <int>, etaM = <int>, count = <int>, commodity = <bool> }`
- Source: `GetInboxInvoiceInfo(i)` for `i = 1..GetInboxNumItems()` returns `invoiceType, itemName, playerName, bid, buyout, deposit, consignment, moneyDelay, etaHour, etaMin, count, commerceAuction`. `cut = consignment`. `daysLeft` is the 7th return of `GetInboxHeaderInfo(i)`.
- Seller net = `bid + deposit - cut`; per-unit price = `bid / count`; **cut rate = cut / bid**.
- Dedupe within a session by `type|item|count|bid|deposit|cut|floor(daysLeft*24)`.

**inv** (per character):
`{ at = <unix>, money = GetMoney(), bags = { [itemID] = count, ... }, bank = { [itemID] = count, ... }, bankAt = <unix or 0> }`
- Bags: containers 0..4 (`BACKPACK_CONTAINER` .. `NUM_BAG_SLOTS`, falling back to literals). Use `C_Container.GetContainerNumSlots` / `C_Container.GetContainerItemInfo(bag, slot)` (`.itemID`, `.stackCount`).
- Bank: only while `BANKFRAME_OPENED` (bank container -1 plus bank bags; use `Enum.BagIndex` names if present, otherwise -1 and 5..11). Keep the previous `bank` if the bank was not opened this session.
- Update on `BAG_UPDATE_DELAYED` (at most every 5 seconds via `C_Timer.After`) and at `PLAYER_LOGOUT`.

**depositProbe:** `{ [1] = <copper 2h>, [2] = <copper 8h>, [3] = <copper 24h>, qty = <int>, at = <unix> }` from `C_AuctionHouse.CalculateCommodityDeposit(itemID, duration, qty)` (commodities) or skipped for items. Run for plan `post` rows when the AH opens (pcall each; these calls are unrestricted).

### 1.4 Scan behavior (`Scan.lua`)
1. `AUCTION_HOUSE_SHOW`: remember `house`; refresh `copperOK`; run deposit probes; enable the Scan button.
2. **Scan button / `/fg scan`:** throttle with `lastScan = max(ForeverGoldDB.lastScanAt, ForeverGoldPlan.lastScanAt or 0)`. If `GetServerTime() - lastScan < 900` and not `force`, print "next full scan in N min" and stop. If `settings.neverScan`, print that and stop. Otherwise log `scanLog {reqAt}` and call `C_AuctionHouse.ReplicateItems()`. **Never call ReplicateItems automatically on AH open.**
3. `REPLICATE_ITEM_LIST_UPDATE`: if we are already packing, ignore (the event re-fires as item data caches). Otherwise start packing (`by = "fg"` if we requested within the last 180 s, else `"other"`, a capture of another addon's scan).
4. **Packing:** read `GetReplicateItemInfo(i)` for `i = 0 .. n-1` (**0-based**), 2,000 rows per frame (`C_Timer.After(0, ...)`). Returns, in order: `name, texture, count, qualityID, usable, level, levelType, minBid, minIncrement, buyoutPrice, bidAmount, highBidder, bidderFullName, owner, ownerFullName, saleStatus, itemID, hasAllInfo`. Do not wait for `hasAllInfo`.
5. On finish: store the scan, set `ForeverGoldDB.lastScanAt = at` (only when `by == "fg"`), complete the scanLog entry, rebuild `ns.market` (see 1.6) and refresh the UI. Print one line: "scan: N rows, M items in X s".
6. `AUCTION_HOUSE_CLOSED` while waiting or packing: abort, log `result = "closed"`. `AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED` while waiting: log `"dropped"`. No reply within 180 s: log `"noreply"` (timer).

### 1.5 Ledger (`Ledger.lua`)
`MAIL_INBOX_UPDATE`: walk the inbox and record invoices (1.3). Inventory snapshots as in 1.3.

### 1.6 Plan and guards (`Bridge.lua`)
- `ns.plan` = `ForeverGoldPlan` if it is a table with `schema == 1`, else `ForeverGoldPlanDefault`.
- `ns.market[itemID] = { min = <unit>, q = <units>, ladder = { {unit, qty}, ... } }`, built from the newest scan **of this session** (never from the DB read back, which may be empty or stale). Only items present in the plan need indexing.
- **Buy guard:** walk the ladder cheapest first until `qty` units are covered; `avg = cost / qty`. Green if `avg <= maxUnit`, red otherwise, gray if no scan yet. Show "fill N @ avg".
- **Post guard:** `min = ns.market[id].min`. If no scan: gray "post at >= floor". If `min < floorUnit`: red "HOLD (market below floor)". Else suggested = `postMode == "match"` and `min` or `min - undercut`, then `max(suggested, floorUnit)`, then round **up** to whole silver when `copperOK == false`.
- **Auctionator buy list (button "Buy list" / `/fg buylist`):** if `Auctionator and Auctionator.API and Auctionator.API.v1`: for each buy row, `pcall(Auctionator.API.v1.ConvertToSearchString, "ForeverGold", { searchString = row.name, isExact = true, maxPrice = row.maxUnit, quantity = row.qty })`; then `pcall(Auctionator.API.v1.CreateShoppingList, "ForeverGold", "Claude: tonight", strings)`. **callerID is the first argument.** On any failure, or if Auctionator is missing, print the buy rows to chat instead.

### 1.7 UI (`UI.lua`)
- One movable frame (`BackdropTemplate`), hidden by default; opens with `/fg` and when the AH opens if the plan has rows.
- Sections: headline + steps (<= 5), Buy, Post, Craft, Hold/Vendor (compact). Rows are FontStrings with color codes. Buttons: **Scan**, **Buy list**, **Done** (`C_UI.Reload()`), **Close**.
- Dracula palette: background `#282a36`, text `#f8f8f2`, green `#50fa7b`, red `#ff5555`, orange `#ffb86c`, gray `#6272a4`, purple `#bd93f9` (titles), cyan `#8be9fd`.
- Slash `/fg`: no arg = toggle window; `print` = plan to chat; `status` = iface, build, project, copperOK, house, priorSessions, plan id and age, last scan age; `scan` / `scan force`; `buylist`; `done`.
- Login line: `ForeverGold: plan <id> (<age> old): <b> buy, <p> post, <c> craft, <h> hold`.

## 2. Plan file `PlanData.lua` (written by Python)
```lua
-- Generated by fgold. Do not edit; change plan/draft.json and run: python -m fgold plan --apply
ForeverGoldPlan = {
  schema = 1,
  id = "20261105-0412-ab12",
  generatedAt = 1762310000,
  basedOnScan = { at = 1762300000, market = "us|Forever|Alliance", rows = 41234 },
  lastScanAt = 1762300000,
  expectPriorSessions = 3,
  settings = { postMode = "match", undercut = 100 },
  headline = "...",
  notes = { "...", },          -- <= 5
  steps = { "...", },          -- <= 5
  budget = { spendCap = 250000, principal = 5000000 },
  buy = { { id = 2447, name = "Peacebloom", qty = 60, maxUnit = 18, why = "...", flip = false }, },
  craft = { { recipe = 2330, name = "Minor Healing Potion", out = 118, count = 20, profitEach = 35, why = "..." }, },
  post = { { id = 785, name = "Mageroyal", qty = 40, floorUnit = 30, startUnit = 34, duration = 3, why = "..." }, },
  hold = { { id = 3820, name = "Stranglekelp", why = "..." }, },
  vendor = { { id = 4865, name = "Ruined Pelt", why = "..." }, },
  ref = { [2447] = { min = 16, mkt = 17, q = 2400 }, },
}
```
- All money is whole copper (integers). `duration`: 1 = 2h, 2 = 8h, 3 = 24h.
- `plan/draft.json` is the same object as JSON (with `ref` keys as strings).

## 3. Python package `fgold/`
Run as `python -m fgold <command>` (`py -3 -m fgold` on Windows).

| Command | Does |
|---|---|
| `doctor [--write]` | Finds WoW installs (`_forever_` folders under the usual Windows and macOS roots, plus config roots), lists accounts that have `SavedVariables/ForeverGold.lua`, checks the addon is installed in each root, prints Python version and paths. `--write` saves `config.local.json`. |
| `install [--root NAME]` | Puts the addon into `<root>/Interface/AddOns/ForeverGold`. Local root: directory junction on Windows (`mklink /J`), symlink on macOS, copy as fallback. Remote root (UNC path): copy. Always creates `PlanData.lua` from `PlanDefault.lua` content if missing. Reminds that a **full client restart** is needed the first time. |
| `ingest` | For every root and account: reads `ForeverGold.lua` and `ForeverGold.lua.bak`; waits until mtime is stable for 2 s; archives each new version (by sha) to `data/raw/<root>/<account>/<mtime>-<sha8>.lua.gz`; extracts scans, invoices, inventory, sessions, probes into the data store. Idempotent. Never writes to the WoW folder. |
| `brief [--no-ingest] [--market KEY]` | Ingest, then market + econ + planner. Writes `reports/<YYYYMMDD-HHMM>-brief.md` (with an empty `## Reads` section for Claude) and `plan/draft.json`. Prints the report path and a 5-line summary. |
| `plan --apply [--draft PATH]` | Validates the draft (section 3.5), renders `PlanData.lua`, re-parses it with `luasv`, writes it atomically into **every** root's addon folder (and the repo copy). Prints the plan id and "now /reload in game". |
| `status` | Data health: newest scan per market and its age, sessions seen, newest invoice, measured cut, plan id applied vs plan id the game reported. |
| `probes` | Launch-night measurements from the data: iface/build/copperOK per session, read-back (`priorSessions`), scan durations and results from `scanLog`, cut rate per invoice and per house, deposit probes. |
| `price <name or itemID>` | Quick lookup for Claude: min, mkt, q, scan age, 3/7-day medians when available. |

Global options: `--data-dir PATH` (default `<repo>/data`, env `FGOLD_DATA`), `--config PATH` (default `<repo>/config.local.json`), `--wow-root PATH` (repeatable; overrides config roots; used by tests).

### 3.1 Config (`config.local.json`, gitignored; `config.example.json` committed)
```json
{
  "roots": [
    {"name": "pc", "path": "C:/Program Files (x86)/World of Warcraft/_forever_"},
    {"name": "vm", "path": "//UNRAID-VM/wow/_forever_"}
  ],
  "region": "us",
  "home_house": "Alliance",
  "nest_egg_copper": 5000000,
  "cut": {"faction": 0.05, "neutral": 0.15},
  "post_mode": "match",
  "undercut_copper": 100,
  "professions": {"Herbalism": 1, "Skinning": 1, "Alchemy": 0, "First Aid": 0, "Cooking": 0},
  "watchlist": [],
  "max_rows": 25
}
```

### 3.2 Modules
- `luasv.py`: safe parser for WoW SavedVariables (never executes Lua). Supports `Name = <value>` statements; tables with `["k"] = v`, `[1] = v`, bare positional values; strings with escapes `\" \\ \n \t \r \ddd` and long strings `[[...]]`, `[==[...]==]`; numbers incl. negative, decimals, exponents (`1e+15`), hex; `true`, `false`, `nil`; comments `--` and `--[[ ]]`. Tables whose keys are exactly 1..n become lists, others dicts. Also `dump(name, value)` producing deterministic Lua for `PlanData.lua` (escape `\\ \" \n`, sorted keys, ints as ints).
- `ingest.py`: the store layout:
  - `data/raw/...` (gz archives)
  - `data/scans/<market>/<at>.json.gz` with `{at, market, by, char, rows, priced, bidOnly, own, items: {"<id>": {"n": auctions, "ladder": [[unit, qty], ...]}}, own_items: {...}}`
  - `data/ledger.jsonl` (invoices, deduped by `type|item|count|bid|deposit|cut|eta-hour bucket`)
  - `data/sessions.jsonl`, `data/inventory.json` (latest per character), `data/probes.json`, `data/state.json` (seen shas, last ingest)
  - **market key** = `"<region>|<realm>|<house>"` with region mapped `1 us, 2 kr, 3 eu, 4 tw, 5 cn`.
- `market.py`: per item in a scan: `q` (units), `n` (auctions), `min`, `mkt` = unit-weighted mean of the cheapest 25% of units (at least 1 unit, never below `min`), `p50` = unit-weighted median. History: medians of `mkt` over 3 and 7 days across scans of the same market.
- `econ.py`: `cut_rate(house)` = config cut (faction or neutral), replaced by the **measured** median of `cut/bid` once >= 3 seller invoices exist for that house; net = price x (1 - cut); vendor values from static; deposit from `probes.json` when present, else estimate `max(100, round(sell_price x deposit_rate x {1:1, 2:4, 3:12}[duration]))`, flagged as an estimate; craft cost = sum of reagent unit costs where unit cost = min(AH `mkt`, vendor unit buy price if the reagent is vendor-sold, Black Market cap if listed).
- `planner.py` (cold start first):
  1. **Below-vendor flips:** AH `min <= 0.8 x vendor sell`, buy the units under `0.9 x vendor sell`; `flip = true`, `maxUnit = floor(0.9 x sell)`.
  2. **Inventory triage** (characters on this market): for each held item, AH net vs vendor: post if `net(mkt) > vendor x 1.1` with `floorUnit = max(ceil(vendor / (1 - cut)), floor(0.85 x ref))` where `ref` = 3-day median mkt if available else `mkt`; `startUnit` = match `min` (or undercut per config) but >= floor; hold if `min < floor`; vendor if vendor >= net.
  3. **Crafts** (professions with skill > 0 in config): recipes with margin >= 25% after cut; buy rows for missing reagents with `maxUnit = min(ceil(1.1 x mkt), floor(1.5 x mkt))`.
  4. **No invest/hold-for-later calls** until >= 3 days of scans for the market (state that in notes).
  5. Budget: `spendCap = max(0, total gold across characters - nest_egg_copper)`, sum of `qty x maxUnit` over buy rows <= spendCap (drop lowest-profit rows first). `principal = nest_egg_copper`.
  6. Lists sorted by expected profit, capped at `max_rows`. Headline/steps are plain text drafts for Claude to rewrite.
- `render.py`: the brief markdown (sections: Next action, Data health, Buy, Post, Craft, Hold/Vendor, Movers (when history), Nest egg, `## Reads` empty).
- `static.py`: loads `static/items.json`, `recipes.json`, `houses.json`, `vendor_caps.json`, `vendor_reagents.json`; name <-> id lookup.

### 3.3 Static data (`tools/fetch_static.py`)
Downloads wago.tools DB2 CSVs for product `wowf` (default build `1.60.1.70634`; `--build latest` asks `https://wago.tools/api/builds`), e.g. `https://wago.tools/db2/ItemSparse/csv?build=1.60.1.70634`. Tables: ItemSparse (ID, Display_lang, SellPrice, BuyPrice, VendorStackCount, Stackable, OverallQualityID), SpellReagents, SpellEffect (Effect 24 = create item, EffectItemType, EffectBasePoints), SkillLineAbility (SkillLine, Spell, MinSkillLineRank), AuctionHouse (ConsignmentRate, DepositRate). Writes:
- `static/items.json`: `{"<id>": {"name", "sell", "buy", "stack", "q"}}` (`buy` per unit = BuyPrice / max(1, VendorStackCount))
- `static/recipes.json`: `{"<spellID>": {"skill_line", "skill", "reagents": [[itemID, qty]], "out": itemID, "out_count"}}` for skill lines 171 Alchemy, 129 First Aid, 185 Cooking, 164 Blacksmithing, 197 Tailoring, 165 Leatherworking, 202 Engineering; **drop orphans** (no reagents or no created item).
- `static/houses.json`: cut/deposit rates per house type.
- `static/vendor_caps.json`: Black Market caps by name, resolved to ids: Black Lotus 500000, Arcane Crystal 500000, Pristine Leather 50000, Frilled Lichen 50000, Pyrite 50000 (copper; community-reported, `"verified": false`).
- `static/vendor_reagents.json`: names of vendor-sold reagents (Empty Vial, Leaded Vial, Crystal Vial, Imbued Vial, Coarse Thread, Fine Thread, Silken Thread, Heavy Silken Thread, Rune Thread, Mild Spices, Hot Spices, Soothing Spices, Refreshing Spring Water, ...), resolved to ids.
Treat downloads as untrusted data (parse CSV only). Re-run on launch day (the live build may differ).

### 3.4 Brief rules (for the skill)
First line is the next action. Lists <= 5 items in the summary. Times in CT. Confidence stated on cold-start calls.

### 3.5 `plan --apply` validation
- Only the keys in section 2; unknown keys rejected.
- Money fields are non-negative integers; `duration` in {1,2,3}; ids positive ints.
- `post.floorUnit`/`startUnit` rounded **up** to whole silver when the newest session reports `copperOK == false`.
- `buy.maxUnit <= 1.5 x ref[id].mkt` unless `flip == true` (then `<= vendor sell`).
- `sum(qty x maxUnit) <= budget.spendCap`.
- At most `max_rows` rows per list, `notes`/`steps` <= 5.
- Rendered Lua re-parses with `luasv` to the same data. Atomic write (temp file + replace, retry on PermissionError for Windows).
- New `id` each apply: `YYYYMMDD-HHMM-<4 hex of content hash>`.

## 4. Session loop (what Travis does)
1. `/forever-gold` in Claude Code (2-4 min, out of game).
2. Log in: the chat line shows the plan id.
3. Mailbox: Open All (invoices recorded).
4. AH: click **Scan**; while it runs click **Buy list** and buy by hand.
5. Rows re-color; post with Auctionator one click per post.
6. **Done** (reloads, data saved).

## 5. Tests
- `python -m unittest discover -s tests -v` (stdlib).
- `luajit tests/wow/run.lua` runs the harness (the Python e2e test calls it when `luajit` is on PATH, else skips).
- `tests/wow/stub.lua`: a WoW API stub (frames, events, C_Timer, clock, C_AuctionHouse incl. 0-based replicate rows, mail invoices, C_Container, Auctionator.API.v1 that errors unless callerID is first, `string.format` that errors on `%d` beyond 32 bits, and traps that fail the test if any protected function in section 6 is called).
- `tests/wow/svwriter.lua`: writes `ForeverGoldDB` in WoW's SavedVariables style (`["key"] = value,` lines, `-- [n]` index comments).
- Fixtures: `tests/fixtures/handmade/WTF/Account/TEST#1/SavedVariables/ForeverGold.lua` (hand-written to this spec) and `tests/fixtures/harness/...` (produced by the harness, committed).

## 6. Red line enforced by `tests/test_redline.py`
The addon source (`addon/`) must not contain any of: `PostItem`, `PostCommodity`, `ConfirmPostItem`, `ConfirmPostCommodity`, `PlaceBid`, `StartCommoditiesPurchase`, `ConfirmCommoditiesPurchase`, `CancelAuction`, `CancelSell`, `SendMail`, `TakeInboxItem`, `TakeInboxMoney`, `AutoLootMailItem`, `DeleteInboxItem`, `CraftRecipe`, `DoTradeSkill`, `PickupContainerItem`, `UseContainerItem`, `RunMacro`, `RunMacroText`, `RunBinding`, `SecureActionButton`, `SetOverrideBinding`, `:Click(`, `SendChatMessage`, `AcceptTrade`. The repo must not contain input-automation code (AutoHotkey, pyautogui, pynput, AppleScript/osascript keystrokes, cliclick) or anti-AFK logic.
