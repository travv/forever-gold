# Forever API notes (checked 2026-10-27)

Sources:
- Gethe/wow-ui-source `forever` branch (head 1.60.1 build 70532, 2026-10-22)
- Ketho/BlizzardInterfaceResources `forever` (70532)
- warcraft.wiki.gg
- wago.tools DB2 `wowf 1.60.1.70634`

**The live build (70634) is newer than any datamined code, so re-check names on launch day** (`tests/test_api_contract.py`).

| Topic | What the source says |
|---|---|
| Client | Project "Camelot" (`Blizzard_ProjectConstants/Camelot`), version 1.60.1, TOC `## Interface: 16001`. It's in the mainline addon family. `_Camelot.toc`/`_Forever.toc` suffixes exist, but a plain TOC is enough. `WOW_PROJECT_ID` reportedly reads 1 like retail, so detect Forever by interface number instead. |
| Full scan | `C_AuctionHouse.ReplicateItems()`: no args, no restriction flag. Retail rule: 15 min account-wide throttle between successful calls; unmeasured on Forever. The server sends the whole list at once; AHledger saw 45 s+ waits in beta. A refused request fires `AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED`. Forever Sniper reports "one list per session", and a new ReplicateItems call clears rows another addon already received. |
| Scan event | `REPLICATE_ITEM_LIST_UPDATE`: no payload. It can re-fire as item data caches, so ignore it while packing. It also fires when another addon scans, so its scan can be captured. |
| Reading rows | `GetNumReplicateItems()`. `GetReplicateItemInfo(index)` is **0-based** and returns 18 values: `name, texture, count, qualityID, usable, level, levelType, minBid, minIncrement, buyoutPrice, bidAmount, highBidder, bidderFullName, owner, ownerFullName, saleStatus, itemID, hasAllInfo`. `count`, `buyoutPrice` and `itemID` are available right away. `owner` is nil except for your own auctions. Also `GetReplicateItemTimeLeft(i)`, `GetReplicateItemLink(i)`. |
| Commodities | Included in the replicate list. Unit price = `buyoutPrice / count` (Auctionator 340's modern full scan does this). Launch probe: compare one herb's search vs scan. |
| Restricted (need a hardware event) | `PostItem`, `PostCommodity`, `ConfirmPostItem`, `ConfirmPostCommodity`, `PlaceBid`, `StartCommoditiesPurchase`, `CancelAuction`. `ConfirmCommoditiesPurchase` is unflagged, but ForeverGold never calls any of these. |
| Not restricted | `SendSearchQuery` (100/min, #noscript), `QueryOwnedAuctions`, `CalculateCommodityDeposit`, `CalculateItemDeposit`, `SupportsCopperValues`, `ReplicateItems`. |
| Price rounding | `C_AuctionHouse.SupportsCopperValues()`. When false, Blizzard's `AuctionHouseUtil.SanitizeAuctionHousePrice` rounds **up** to whole silver. |
| Encoding | `C_EncodingUtil`: `CompressString(source, method, level)`, `DecompressString(source, method)`, `EncodeBase64`/`DecodeBase64(source, variant)`, `EncodeHex`/`DecodeHex`, `SerializeJSON(value, options)`, `DeserializeJSON(source)`, `SerializeCBOR`, `DeserializeCBOR`. `Enum.CompressionMethod`: Deflate 0, Zlib 1, Gzip 2. Python `zlib.compress` output = **Zlib**. Limits: 100 MiB, depth 100. (Phase 2 paste-in replans.) |
| Sale invoices | `GetInboxInvoiceInfo(index)`, 1-based, returns `invoiceType` ("buyer", "seller", "seller_temp_invoice" or nil), `itemName`, `playerName` (nil for multiple buyers), `bid, buyout, deposit, consignment, moneyDelay, etaHour, etaMin, count, commerceAuction`. Seller net = `bid + deposit - consignment`; cut % = `consignment / bid`. Chat also shows "A buyer has been found for your auction of X" (system message). |
| AH cut (DB2 `AuctionHouse`) | Alliance/Horde: ConsignmentRate 5, DepositRate 5. **Blackwater (neutral): 15 and 25.** Tag scans by house with `UnitFactionGroup("npc")` at `AUCTION_HOUSE_SHOW` (nil = Neutral). |
| Auctionator API v1 | `ConvertToSearchString(callerID, term)`, **callerID first**. `term`: `searchString` (required), `isExact`, `categoryKey`, min/max level, item level, crafted level, `minPrice`/`maxPrice` (copper), `quality`, `tier`, `expansion`, `quantity`. `CreateShoppingList(callerID, name, searchStrings)` replaces a same-name list and starts searching if the AH is open. Also `GetShoppingListItems`, `MultiSearch(Exact)`, `GetAuctionPriceByItemID(callerID, itemID)`. It raises `error()` on misuse, so always pcall. String format: `query;cat;minIlvl;maxIlvl;minLvl;maxLvl;minCraft;maxCraft;minPrice(gold);maxPrice(gold);quality;tier;expansion;qty`. The GitHub repo 404'd on 2026-10-27; verified from an Auctionator 340 Forever install mirror. Re-check against the 341 zip. |
| Auctionator price DB | `AUCTIONATOR_PRICE_DATABASE[realmKey]` is CBOR at logout. It keeps only the last min, daily low/high and daily max quantity: no depth, no house. **In the beta it reset on every game load.** Don't build on it. |
| Professions | `C_TradeSkillUI.GetAllRecipeIDs()`, `GetRecipeInfo(id).learned`, `GetRecipeSchematic(id, false)` returns `reagentSlotSchematics[].reagents[].itemID`, `quantityRequired`, `outputItemID`, `quantityMin/Max` (phase 2 recipe export). Healing potions moved to First Aid (skill 129, spells 1244431-1244436); the old Alchemy rows (2330, 17556...) have no reagents or output. |
| Other names that exist | `C_Container.GetContainerItemInfo` (`itemID`, `stackCount`, `isBound`), `C_Item.GetItemInfo` (the bare `GetItemInfo` is not listed), `C_UI.Reload`, `GetServerTime`, `GetCurrentRegion`. Events: `MAIL_INBOX_UPDATE`, `BANKFRAME_OPENED`, `BAG_UPDATE_DELAYED`, `AUCTION_HOUSE_SHOW`, `AUCTION_HOUSE_CLOSED`, `COMMODITY_SEARCH_RESULTS_UPDATED`, `OWNED_AUCTIONS_UPDATED`. Templates: `UIPanelButtonTemplate`, `BackdropTemplate`, `UIPanelScrollFrameTemplate`. |

## File-loading rules (measured on the beta client)
- Files created **after the client launched** stay invisible until a full restart, so `fgold install` must run before the first launch.
- `/reload` re-reads existing addon Lua (not cached fonts or textures).
- SavedVariables are written at `/reload`, logout, quit, disconnect (not a crash). Never edit them while the game runs.
- Read-back was unreliable in beta (a fix was noted in build 70009, but a 2026-10-07 report says it still failed). Treat the DB as write-only.
- A not-yet-loaded load-on-demand addon's files are read when it's first loaded (phase 2 "slot addon" replans).

## Account notes
- Self-found characters cannot use the AH or receive mail ("You are self-found and cannot use the auction house.").
- Same-account mail is instant (community). Cross-account timing is unmeasured: test with a cheap item, never gold.
