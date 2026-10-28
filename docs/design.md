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
- Dedupe within a session by `type|item|count|bid|deposit|cut|floor(daysLeft*24)`, counting copies: identical mails are separate sales (two buyers at one price), so a walk that sees k mails with one key records the copies beyond those already recorded this session (k minus that count). Re-fires see the same k and add nothing.

**inv** (per character):
`{ at = <unix>, money = GetMoney(), bags = { [itemID] = count, ... }, bank = { [itemID] = count, ... }, bankAt = <unix or 0> }`
- Bags: containers 0..4 (`BACKPACK_CONTAINER` .. `NUM_BAG_SLOTS`, falling back to literals), plus the reagent bag `Enum.BagIndex.ReagentBag` (5 on Forever) when `Enum.BagIndex` has it (without the enum, 5 is a bank bag). Use `C_Container.GetContainerNumSlots` / `C_Container.GetContainerItemInfo(bag, slot)` (`.itemID`, `.stackCount`); skip slots whose `.isBound` is true (soulbound copies cannot be posted), in the bank too.
- Bank: only while `BANKFRAME_OPENED`. Use the containers whose `Enum.BagIndex` name is `Bank`, `BankBag_n` or `CharacterBankTab_n` if `Enum.BagIndex` exists (Forever 70532 has `CharacterBankTab_1..9` = 6..14, and -1 is its Keyring), otherwise -1 and 5..11. Keep the previous `bank` if the bank was not opened this session.
- Account bank tabs (`AccountBankTab_1..9` = 15..23) are not recorded: they are account-wide, so storing them per character would count them once per character, and whether Forever enables them (`C_PlayerInfo.IsAccountBankEnabled`) is unchecked. Items there are invisible to the plan.
- Update on `BAG_UPDATE_DELAYED` (at most every 5 seconds via `C_Timer.After`) and at `PLAYER_LOGOUT`.

**depositProbe:** `{ [1] = <copper 2h>, [2] = <copper 8h>, [3] = <copper 24h>, qty = <int>, at = <unix>, house = "Alliance"|"Horde"|"Neutral" }` from `C_AuctionHouse.CalculateCommodityDeposit(itemID, duration, qty)` (commodities) or skipped for items. Run for plan `post` rows when the AH opens (pcall each; these calls are unrestricted). `house` is the AH it was measured at (the neutral deposit rate is 5x the faction one); fgold uses a probe only for its own house.

### 1.4 Scan behavior (`Scan.lua`)
1. `AUCTION_HOUSE_SHOW`: remember `house`; refresh `copperOK`; run deposit probes; enable the Scan button.
2. **Scan button / `/fg scan`:** throttle with `lastScan = max(ForeverGoldDB.lastScanAt, ForeverGoldPlan.lastScanAt or 0)`. If `GetServerTime() - lastScan < 900` and not `force`, print "next full scan in N min" and stop. If `settings.neverScan`, print that and stop. Otherwise log `scanLog {reqAt}` and call `C_AuctionHouse.ReplicateItems()`. **Never call ReplicateItems automatically on AH open.**
3. `REPLICATE_ITEM_LIST_UPDATE`: ignore it while packing (the event re-fires as item data caches), while the AH is closed, and when the list is empty (0 rows is never a scan, not even while our request is pending: a new `ReplicateItems` call clears the old rows). Re-fires also arrive after a fast pack, so skip a list identical to the last one packed this session (row count plus 4 sample rows) unless our own request is pending. Otherwise start packing: `by = "fg"` if our request is still pending (sent at most 180 s ago and not closed; a DROPPED event does not end it, see 6), else `"other"` (a capture of another addon's scan).
4. **Packing:** read `GetReplicateItemInfo(i)` for `i = 0 .. n-1` (**0-based**), 2,000 rows per frame (`C_Timer.After(0, ...)`). Returns, in order: `name, texture, count, qualityID, usable, level, levelType, minBid, minIncrement, buyoutPrice, bidAmount, highBidder, bidderFullName, owner, ownerFullName, saleStatus, itemID, hasAllInfo`. Do not wait for `hasAllInfo`. Then build the scan string 500 items per frame.
5. On finish: store the scan, set `ForeverGoldDB.lastScanAt = at` (only when `by == "fg"`), complete the scanLog entry, rebuild `ns.market` (see 1.6) and refresh the UI. Print one line: "scan: N rows, M items in X s".
6. `AUCTION_HOUSE_CLOSED` while waiting or reading rows: abort, log `result = "closed"` (once every row is read, the string-building phase still finishes). `AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED` while waiting: log `"dropped"` but keep waiting, because the event has no payload and other addons' throttled AH messages (an Auctionator search started during the wait) fire it too; a list that still arrives within the 180 s is ours (`"ok"`, `by = "fg"`, `lastScanAt` set). No list within 180 s: the entry stays `"dropped"` if a DROPPED came, else `"noreply"` (timer).

### 1.5 Ledger (`Ledger.lua`)
`MAIL_INBOX_UPDATE`: walk the inbox and record invoices (1.3). Inventory snapshots as in 1.3.

### 1.6 Plan and guards (`Bridge.lua`)
- `ns.plan` = `ForeverGoldPlan` if it is a table with `schema == 1`, else `ForeverGoldPlanDefault`.
- `ns.market[itemID] = { min = <unit>, q = <units>, ladder = { {unit, qty}, ... } }`, built from the newest scan **of this session** (never from the DB read back, which may be empty or stale). Only items present in the plan need indexing. `ns.marketHouse` keeps that scan's `house`.
- **House check (both guards, first):** when the AH open now (`house`) is not the plan's house (the third part of `basedOnScan.market`), every row is gray "plan is for the <house> AH, not this <house> one" (its floors and caps assume that house's cut). Else, when this session's scan is from another AH than the open one, gray "scan this AH (the scan is from the <house> AH)".
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
- `expectPriorSessions`: the session count of the newest `ForeverGold.lua` when the plan was made. The first login on this plan should report `priorSessions >=` it (fewer means the file was not read back). Every `/reload` (the Done button) fires `PLAYER_LOGIN` and adds a session, so later sessions count more. Each WoW account has its own file, so fgold judges the first login of each account against that account's count at the apply (`state.applied.expect`), not against this plan-wide number.
- `plan/draft.json` is the same object as JSON (with `ref` keys as strings).

## 3. Python package `fgold/`
Run as `python -m fgold <command>` (`py -3 -m fgold` on Windows).

| Command | Does |
|---|---|
| `doctor [--write]` | Finds WoW installs (`_forever_` folders under the usual Windows and macOS roots, plus config roots), lists accounts that have `SavedVariables/ForeverGold.lua`, checks the addon is installed in each root, prints Python version and paths. `--write` saves `config.local.json`. |
| `install [--root NAME]` | Puts the addon into `<root>/Interface/AddOns/ForeverGold`. Local root: directory junction on Windows (the `mklink /J` kind, made with `_winapi.CreateJunction`, never through `cmd.exe`, which re-parses `&`, `^` and `%` in paths), symlink on macOS, copy as fallback. A new link must resolve to the repo addon folder; one that points anywhere else is removed (the link only) and the addon copied. Remote root (UNC path): copy. Always creates `PlanData.lua` from `PlanDefault.lua` content if missing. Reminds that a **full client restart** is needed the first time. |
| `ingest` | For every root and account: reads `ForeverGold.lua` and `ForeverGold.lua.bak`; waits until mtime is stable for 2 s; archives each new version (by sha) to `data/raw/<root>/<account>/<mtime>-<sha8>.lua.gz`; extracts scans, invoices, inventory, sessions, probes into the data store. Idempotent. Never writes to the WoW folder. |
| `brief [--no-ingest] [--market KEY]` | Ingest, then market + econ + planner. Writes `reports/<YYYYMMDD-HHMM>-brief.md` (with an empty `## Reads` section for Claude), `plan/draft.json`, and the same draft as `plan/draft.orig.json` (what `plan --apply` checks edits against). Prints the report path and a 5-line summary. |
| `plan --apply [--draft PATH]` | Validates the draft (section 3.5), renders `PlanData.lua`, re-parses it with `luasv`, writes it atomically into **every** root's addon folder (and the repo copy). A root whose addon folder is a link to somewhere other than the repo addon folder is skipped. Prints the plan id and "now /reload in game". The plan counts as applied (`state.applied`, `plan/applied.json`) only when at least one WoW root got it; `state.applied` records those roots, the skip reason of the others, and each account's session count. |
| `status` | Data health: newest scan per market and its age, sessions seen, newest invoice, measured cut, plan id applied vs plan id the game reported (a session that started before the apply still shows the old id: `/reload`; one that started after it: full client restart; one from a WoW root the apply did not reach: fix that root and apply again). |
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
`professions` values are skill ranks: a recipe is planned when the rank is at or above its `skill` in `static/recipes.json` (3.3). Config, draft and static JSON files may start with a UTF-8 byte order mark (Windows PowerShell 5 and older Notepad write one).

### 3.2 Modules
- `luasv.py`: safe parser for WoW SavedVariables (never executes Lua). Supports `Name = <value>` statements; tables with `["k"] = v`, `[1] = v`, bare positional values; strings with escapes `\" \\ \n \t \r \ddd` and long strings `[[...]]`, `[==[...]==]`; numbers incl. negative, decimals, exponents (`1e+15`), hex; `true`, `false`, `nil`; comments `--` and `--[[ ]]`. Tables whose keys are exactly 1..n become lists, others dicts. Also `dump(name, value)` producing deterministic Lua for `PlanData.lua` (escape `\\ \" \n`, sorted keys, ints as ints).
- `ingest.py`: the store layout:
  - `data/raw/...` (gz archives)
  - `data/scans/<region>/<realm>/<house>/<at>.json.gz` (the market key's three parts as folders: `|` is not allowed in Windows file names; characters Windows rejects are `%XX`-encoded) with `{at, market, by, char, rows, priced, bidOnly, own, items: {"<id>": {"n": auctions, "ladder": [[unit, qty], ...]}}, own_items: {...}}`
  - `data/scans/index.json`: `{market: {"<at>": {char, house, by, rows}}}`, written at ingest, so reports read who scanned where without loading every scan
  - `data/ledger.jsonl` (invoices, deduped: two records are the same mail when `char|type|item|count|bid|deposit|cut|copy` matches and their expiry times `seenAt + daysLeft x 86400` are within 2 minutes; that time stays the same each time the same mail is seen, in any session. `copy` numbers the identical records of one inbox walk (same `char` and `seenAt`), so k identical mails seen at once stay k sales)
  - `data/sessions.jsonl`, `data/inventory.json` (latest per character), `data/probes.json`, `data/state.json` (seen shas, last ingest)
  - **market key** = `"<region>|<realm>|<house>"` with region mapped `1 us, 2 kr, 3 eu, 4 tw, 5 cn`.
- `market.py`: per item in a scan: `q` (units), `n` (auctions), `min`, `mkt` = unit-weighted mean of the cheapest 25% of units (at least 1 unit, never below `min`), `p50` = unit-weighted median. History: medians of `mkt` over 3 and 7 days across scans of the same market.
- `econ.py`: `cut_rate(house)` = config cut (faction or neutral), replaced by the **measured** median of `cut/bid` once >= 3 seller invoices exist for that house; net = price x (1 - cut); vendor values from static; deposit from `probes.json` when a probe exists **for that house**, else estimate `max(100, round(sell_price x deposit_rate x {1:1, 2:4, 3:12}[duration]))`, flagged as an estimate; craft cost = sum of reagent unit costs where unit cost = min(AH `mkt`, vendor unit buy price if the reagent is vendor-sold, Black Market cap if listed). Invoices carry no house, so a seller invoice with a bid of 1s or more and `cut/bid > 0.10` is the neutral house (15%) before any other rule; then the only house its character scanned at, then the cut rate among several houses, the character's faction, the home house.
- `planner.py` (cold start first):
  1. **Below-vendor flips:** AH `min <= 0.8 x vendor sell`, buy the units under `0.9 x vendor sell`; `flip = true`, `maxUnit = floor(0.9 x sell)`.
  2. **Inventory triage** (characters on this market): for each held item, AH net vs vendor: post if `net(mkt) > vendor x 1.1` with `floorUnit = max(ceil(vendor / (1 - cut)), floor(0.85 x ref))` where `ref` = 3-day median mkt if available else `mkt`; `startUnit` = match `min` (or undercut per config) but >= floor; hold if `min < floor`; vendor if vendor >= net. With `copperOK == false` the floor is rounded up to whole silver **before** the hold test, as the in-game guard compares it.
  3. **Crafts** (professions with skill > 0 in config, recipes whose `skill` <= that rank): recipes with margin >= 25% after cut; buy rows for missing reagents with `maxUnit = min(ceil(1.1 x mkt), floor(1.5 x mkt))` (integer math). The crafted output gets a post row with a break-even floor; a craft whose output `min` is below that (rounded) floor is skipped, as its output would only be held.
  4. **No invest/hold-for-later calls** until >= 3 days of scans for the market (state that in notes).
  5. Budget: `spendCap = max(0, gold on one WoW account - nest_egg_copper)`: the account (WTF account folder) of the newest session on this market, whose characters there pool their gold. Other accounts never fund the list: the alt buys with its own gold, because gold mailed between accounts is the one-sided move Temporary Economy Holds flag. AH buys (`qty x maxUnit`) plus the vendor and Black Market mats of craft groups <= spendCap (drop lowest-profit groups first; a craft, its reagent rows and its mats are one group). `principal = nest_egg_copper`. With more than one account on the market the brief lists gold per account, names the paying account, and the budget note says never to mail gold between accounts.
  6. Lists sorted by expected profit, capped at `max_rows`. Headline/steps are plain text drafts for Claude to rewrite.
- `render.py`: the brief markdown (sections: Next action, Data health, Buy, Post, Craft, Hold/Vendor, Movers (when history), Nest egg, `## Reads` empty).
- `static.py`: loads `static/items.json`, `recipes.json`, `houses.json`, `vendor_caps.json`, `vendor_reagents.json`; name <-> id lookup.

### 3.3 Static data (`tools/fetch_static.py`)
Downloads wago.tools DB2 CSVs for product `wowf` (default build `1.60.1.70634`; `--build latest` asks `https://wago.tools/api/builds`), e.g. `https://wago.tools/db2/ItemSparse/csv?build=1.60.1.70634`. Tables: ItemSparse (ID, Display_lang, SellPrice, BuyPrice, VendorStackCount, Stackable, OverallQualityID), SpellReagents, SpellEffect (Effect 24 = create item, EffectItemType, EffectBasePoints; build 70634 only has EffectBasePointsF, so use whichever exists), SkillLineAbility (SkillLine, Spell, MinSkillLineRank, TrivialSkillLineRankLow), AuctionHouse (ConsignmentRate, DepositRate). Writes:
- `static/items.json`: `{"<id>": {"name", "sell", "buy", "stack", "q"}}` (`buy` per unit = BuyPrice / max(1, VendorStackCount))
- `static/recipes.json`: `{"<spellID>": {"skill_line", "skill", "reagents": [[itemID, qty]], "out": itemID, "out_count"}}` for skill lines 171 Alchemy, 129 First Aid, 185 Cooking, 164 Blacksmithing, 197 Tailoring, 165 Leatherworking, 202 Engineering; **drop orphans** (no reagents or no created item). `skill = max(MinSkillLineRank, TrivialSkillLineRankLow)`: the rank where the recipe turns yellow, at or above the rank needed to learn it, so a profession at that rank can always make it (build 70634 has MinSkillLineRank 1 on 1874 of 1890 recipes; Linen Bandage 30, Major Healing Potion 290, Flask of the Titans 315). Planning may miss a recipe in its orange band; it never plans one Travis cannot make yet. Whether he *knows* the recipe is still unchecked (the craft row says "if you know the recipe").
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
- The caps above are only as good as `ref` and the budget, so the draft is also checked against what it cannot change:
  - `ref` equals the `{min, mkt, q}` of the stored scan `basedOnScan` names (`data/scans/...`); a `ref` with prices but no such scan is rejected.
  - `budget.principal == nest_egg_copper` (config), and `spendCap <= max(0, gold - nest_egg_copper)` with the paying account's gold (3.2 rule 5) is an error, not a warning.
  - Against `plan/draft.orig.json` (the draft `brief` generated): `schema`, `basedOnScan`, `lastScanAt`, `expectPriorSessions`, `settings`, `ref` and `budget.principal` unchanged; `spendCap` not raised; every row is a generated row (buy rows by `id` and `flip`, craft rows by `recipe`, others by `id`; a hold row may also be a generated post row, the move to hold) used once, with `name`, `out`, `profitEach`, `floorUnit` and `startUnit` unchanged and `qty`, `count` and `maxUnit` not raised. Text, `why`, `duration` and deletions are free. No `draft.orig.json`: run `brief` first.
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
- `tests/test_e2e.py`: the round trip on both fixtures. It copies each into a temp WoW root and runs `install`, `ingest`, `brief`, `plan --apply` (temp `FGOLD_HOME`, `--data-dir`, `--wow-root`; nothing is written into the repo). With luajit, `run.lua --plan <PlanData.lua>` must show the applied id, one guard row per buy/post row and one Auctionator search per buy row (name, max price, qty). Then `--sv <ForeverGold.lua>` plays one session loop (login, Scan, Buy list, Done, logout) and fgold must see the plan id come back.
- `tests/wow/stub.lua`: a WoW API stub (frames, events, C_Timer, clock, C_AuctionHouse incl. 0-based replicate rows, mail invoices, C_Container, Auctionator.API.v1 that errors unless callerID is first, `string.format` that errors on `%d` beyond 32 bits, and traps that fail the test if any protected function in section 6 is called).
- `tests/wow/svwriter.lua`: writes `ForeverGoldDB` in WoW's SavedVariables style (`["key"] = value,` lines, `-- [n]` index comments).
- Fixtures: `tests/fixtures/handmade/WTF/Account/TEST#1/SavedVariables/ForeverGold.lua` (hand-written to this spec) and `tests/fixtures/harness/...` (produced by the harness, committed).

## 6. Red line enforced by `tests/test_redline.py`
The addon source (`addon/`) must not contain any of: `PostItem`, `PostCommodity`, `ConfirmPostItem`, `ConfirmPostCommodity`, `PlaceBid`, `StartCommoditiesPurchase`, `ConfirmCommoditiesPurchase`, `CancelAuction`, `CancelSell`, `SendMail`, `TakeInboxItem`, `TakeInboxMoney`, `AutoLootMailItem`, `DeleteInboxItem`, `CraftRecipe`, `DoTradeSkill`, `PickupContainerItem`, `UseContainerItem`, `RunMacro`, `RunMacroText`, `RunBinding`, `SecureActionButton`, `SetOverrideBinding`, `:Click(`, `SendChatMessage`, `AcceptTrade`, `SendWhisper`, `SendMessage`. The repo must not contain input-automation code (AutoHotkey, pyautogui, pynput, pydirectinput, AppleScript/osascript keystrokes, Quartz CGEvent posting, win32 window messages, Linux uinput/evdev/ydotool, robotjs, cliclick) or anti-AFK logic; in Markdown only code is scanned (fenced blocks and inline code: prose may name a banned tool to forbid it), and this spec and `docs/api-notes.md` are not scanned.
- The addon also must not build a function name at runtime (a `_G` or `C_` namespace index, `..` inside an index, a call of a computed index, `loadstring`, `getglobal`, `setfenv`, the `securecall` family), and `PlanData.lua`, which the TOC loads as code, must parse with `luasv` as exactly one `ForeverGoldPlan = { ... }` data assignment.
- The `/forever-gold` skill's `allowed-tools` (no prompt for the whole turn, not gated by workspace trust) and `.claude/settings.json` pre-approve only the `fgold` commands, `Read`, and edits to `plan/draft.json` and `reports/*-brief.md`; the settings deny edits under `_forever_`, `Interface/AddOns` and to any `PlanData.lua`.

## Changelog
- **2026-10-27, integration pass** (the code already behaved this way unless noted):
  - 1.3 bank: Forever 70532's `Enum.BagIndex` has `CharacterBankTab_1..9` and no `Bank`/`BankBag_n`, and -1 is the Keyring there, so "bank container -1" would have read the keyring (found in the cached API data in `tests/fixtures/forever_api`).
  - 1.4.3, 1.4.4, 1.4.6 scans: the literal rule stored a re-fired copy of the same list as a second scan (pushing the older scan out of the 2-scan cap) and could label another addon's scan `fg` after our request was dropped or the AH closed. Building the whole string in one frame froze the client for 0.3 to 0.5 s at 250k rows.
  - 2 `expectPriorSessions` and 3 `status`: the meaning is now written down, and fgold was fixed to match. The brief compared it with the newest session using `==`, but the Done reload always adds a session, so every normal loop showed a false read-back MISMATCH (`render.py`). `status` said `/reload` even when a session that started after the apply missed the plan, which needs a full restart (`probes.py`).
  - 3.2 store: one folder named `<market>` is impossible on Windows (`|`), so the three parts are nested folders. The ledger's "eta-hour bucket" was ambiguous: it is the mail's expiry hour, which stays the same across sessions.
  - 3.3: build 70634's SpellEffect has no EffectBasePoints column, only EffectBasePointsF.
  - 5: added `tests/test_e2e.py` and the harness option `--plan PATH --sv SVPATH`.
- **2026-10-27, review fixes** (each one changed the code to match; found by the redline and correctness reviews):
  - 1.3 invoices: identical mails are separate sales. The `floor(daysLeft*24)` key (addon) and the expiry-hour key (fgold) kept one record for two buyers who paid one price in the same clock hour, undercounting sales and delaying the measured cut. The addon now records k copies when it sees k identical mails at once; the ledger matches a mail by its content, its copy number in the walk and its expiry time within 2 minutes (3.2).
  - 1.3 inventory: the reagent bag (`Enum.BagIndex.ReagentBag` = 5, `NUM_REAGENTBAG_SLOTS` in the 1.60.1 client) was never read, so its herbs could not be posted and crafts bought reagents already held. Soulbound slots (`isBound`) are skipped. Account bank tabs stay unrecorded (account-wide; reading them per character would double-count).
  - 1.3 depositProbe has `house`, and econ uses a probe only at its own house: a neutral-AH probe (25% deposit rate) priced Alliance posts as measured.
  - 1.4.3 and 1.4.6: `AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED` has no payload and other addons fire it (the Buy list starts Auctionator searches during the scan wait), so it no longer ends our wait. The old rule stored our own list as `other`, left `lastScanAt` at 0 and told Travis to try again. A 0-row list is never packed: it was stored as a successful `fg` scan, set the throttle clock and pushed a good scan out of the 2-scan cap.
  - 1.6: guards ignored the house. A scan at the Alliance AH kept rows green at Booty Bay (15% cut), and an Alliance plan's floors were shown at a neutral AH. Rows are gray there now.
  - 2 and 3 `status`: read-back is judged per WoW account (each has its own SavedVariables file); a second account's first login was flagged NOT READ BACK against the main's count. A plan that reached no WoW root no longer counts as applied (status prescribed a full restart for an id no client could load), and a session from a root the apply did not reach names that root.
  - 3 `install`: `cmd /c mklink /J` re-parsed the paths: with `&` in the repo path the junction pointed at a truncated path, the copy fallback then wrote the addon through it, and every later apply wrote `PlanData.lua` there. Junctions are made with `_winapi.CreateJunction`, a link must resolve to the repo addon folder, and apply skips a root whose addon folder links elsewhere.
  - 3.1: a UTF-8 BOM in config, draft or static JSON was a parse error, and the config error advised deleting the file.
  - 3.2 econ: a seller invoice with a 15% cut was assigned to the only house its character had scanned at; the cut rate now decides first (bids of 1s or more).
  - 3.2 planner rule 2 and 3: the post/hold test used the unrounded floor while the post row and the guard use the silver-rounded one, so with `copperOK == false` rows planned as posts showed HOLD in game. Crafts whose output would be a hold are skipped. Reagent `maxUnit` used float math (`ceil(1.1 x 50)` gave 56).
  - 3.2 planner rule 5: `spendCap` pooled gold across WoW accounts, so a buy list for a 10g alt was sized by the main's 900g and could only be funded by mailing gold across licenses, the Economy Holds pattern the README forbids. Vendor and Black Market mats were not counted, so a craft could spend into the nest egg ("No buys" and "Buy from a vendor first" in one plan).
  - 3.3: `MinSkillLineRank` is 1 for 1874 of 1890 recipes in build 70634, so any rank planned every recipe (Major Healing Potion at First Aid 1). `skill` is now the yellow rank, `max(MinSkillLineRank, TrivialSkillLineRankLow)`.
  - 3.2 store: brief, status and probes loaded every stored scan (about 31 MB of Python objects per 250k-row scan). The planner and `price` load 7 days, scan days come from file names, and `data/scans/index.json` says who scanned where.
  - 3.5: the validator only checked the draft against itself, so one consistent edit of `ref`, the budget and a row defeated every cap (7000 Briarthorn at 15x the scan price, exit 0). It now checks `ref` against the stored scan, `principal` against the config, the cap against gold, and the edits against `plan/draft.orig.json`.
  - 6: added Forever's other chat-send APIs (`C_BattleNet.SendWhisper`, `C_Club.SendMessage`), the runtime-name and `PlanData.lua` checks, more input drivers, code in all Markdown but the spec and API notes, and the skill permissions: its `allowed-tools` listed bare `Bash`, `Write` and `Edit`, so any command or file write ran without a prompt during `/forever-gold`.
