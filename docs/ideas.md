---
title: "Claude x WoW Forever: gold-making integration ideas"
type: reference
date_created: 2026-10-27
related:
  - "[[gold-making-reference-gillee-series]]"
repo: "~/Documents/forever-gold (push to github.com/travv/forever-gold, clone on the PC)"
tags:
  - wow-forever
  - gold-making
  - claude
---

# Claude x WoW Forever: gold-making integration ideas

Research from 2026-10-27. Five researchers each made claims, a separate fact-checker tried to disprove each claim, and a design agent checked the API details against Blizzard's own Forever UI source code. **Launch is Nov 4, 2026, 3:00 PM PST (5:00 PM CT).** The beta servers are down, so nothing in-game can be tested before then.

## TL;DR

**The rule: Claude thinks, you click.** Claude reads your scan data outside the game and writes the plan. A small addon shows it in the game. Every buy, post, and cancel is your own click.

**Top 5 ideas, ranked by gold per minute of your time:**
1. **Your own AH scanner + a daily "Gold Brief".** Claude turns your scans into tonight's 10-minute to-do list. *(MVP)*
2. **Price guards in the AH window.** Each item gets a max buy price and a minimum post price, re-checked against tonight's scan, so stale plans don't burn gold. *(MVP)*
3. **An Auctionator buy list made from Claude's plan.** It's called "Claude: tonight" and has max prices built in. *(MVP)*
4. **A sales ledger** that reads your AH sale mail, so you learn the real AH cut and what things actually sold for. *(MVP)*
5. **A trade-chat demand watcher.** It reads `/chatlog` and flags WTB/WTS lines priced above or below the AH. *(next)*

**Build path:** the `forever-gold` repo gets built on the Mac and tested against a fake game API. You push it to `travv/forever-gold`, clone it on the PC, and run it for the first time on launch night.

## What other people have done with LLMs

**The pattern:** people use LLMs to **build** gold tools and to **analyze** data. Nobody credible lets an LLM trade. On live servers, the projects that let an AI touch the game client are all bots.

| Project | What it does | AI? | ToS read |
|---|---|---|---|
| [Everloot](https://github.com/Everloot/everloot) | Forever companion app. An in-game `/ask` saves your question, the companion answers through MCP plus an optional Claude, OpenAI or Ollama model (default `claude-opus-5-5`, your own key), and the answer shows after `/reload`. Public read-only MCP at `everloot.app/mcp/public`. | Yes | Green: file bridge, no input |
| [auxForever](https://github.com/TylerHampton/aux-forever) (about 6K downloads) | The aux AH addon rebuilt for Forever. The repo contains `CLAUDE.md` and `AGENTS.md`. | Built with Claude Code + Gemini | Green |
| AuctionatorSync, anvilbook, AuctionatorPlus, Profiteer, ForeverFlip | Small Forever AH addons; most commits carry Claude Code co-author tags. [anvilbook](https://github.com/traagel/anvilbook) pushes buy lists into Auctionator. | Built with Claude Code | Green, low usage (0-2 stars) |
| [wow-forever-companion](https://github.com/fedechiesa/wow-forever-companion) | FastAPI + Postgres market backend over Auctionator data | Partly | Green |
| [AHledger](https://github.com/DeepCapt/ahledger-sync) | Sync app reads scans and rewrites `AHledger_Data.lua` every 30 min. Free public API. | No | Green |
| [ForeverWowCraft](https://foreverwowcraft.com) (Gillee) | Cheapest 1-300 profession routes; exports Auctionator shopping-list strings. Helper app is Windows-only and not public yet. | No | Green |
| Retail LLM advisors ([arbitrage agent](https://github.com/igorhsouza/wow-market-arbitrage-agent), Gemini-to-Discord alerts) | AH analysis and alerts | Yes | Green, but retail only |
| [CSOAI blizzard-wow-mcp](https://github.com/CSOAI-ORG/blizzard-wow-mcp) | Sold as "AH intelligence" | n/a | **Avoid: it returns randomly generated fake prices** |
| Retail Battle.net MCP wrappers ([wow-mcp](https://github.com/brunofin/wow-mcp) and others) | Auctions and commodities for retail | n/a | Can't reach Forever |
| [agent-wow](https://github.com/agent-wow/agent-wow), GPT computer-use demos | LLMs actually playing the game | Yes | Private servers or slow demos only. **Red on live.** |
| [wow-fishing-forever](https://github.com/rsamf/wow-fishing-forever) | Vision fishing bot | No | **Red** |
| Pixel-strip bridges ([WoW-Chat-Translator](https://github.com/Mouchoir/WoW-Chat-Translator), witchcraft) | Addon draws data as screen pixels, a desktop app reads them | Some | Yellow: screen capture is grayer than files |
| claude-wow (Azeroth Companion, Mac) and [WITCHCRAFT](https://github.com/Geraldlol/witchcraft) (Windows) | Claude Code chatting *inside* the Forever client, using addon-drawn pixel strips, `Screenshot()` files or the chat log as live channels | Yes (Claude Code) | Yellow: no input automation, but these depend on client quirks; WITCHCRAFT's fast channels are pinned to one beta build and switch off on others |
| [GoldHound](https://github.com/AndyHeardApps/GoldHound), [Profiteer](https://github.com/DrKlampz/Profiteer), [AuctionatorSync](https://github.com/DrangedAddons/AuctionatorSync) | Opportunity finder from scans, craft ROI ranking, price sharing between players | Partly | Green; worth a look before building the same feature |

**Failure mode to remember:** LLMs fill Forever gaps with Classic or retail knowledge. Forever runs the **modern** addon API, so ground any Claude-written Lua in Blizzard's [Forever UI source](https://github.com/Gethe/wow-ui-source/tree/forever), not vanilla 1.12 docs.

## Forever facts that shape the design

Confidence: **H** = primary source (Blizzard code or posts, game data), **M** = several community sources, **L** = single report.

1. **No official data feed (H).** Blizzard has no Forever API namespace (403 on every guessed namespace as of Oct 26), and [TSM doesn't support Forever](https://www.curseforge.com/wow/addons/tradeskill-master). Every Forever price comes from players scanning in the game.
2. **Modern client, retail-style AH (H).** Project "Camelot", interface 16001.
   - Stackable goods are "commodities", bought cheapest first with no bids.
   - **A full scan (`ReplicateItems`) can be started from addon code.** It's throttled to about once per 15 min (retail's rule, unmeasured on Forever).
   - **Post, buy, and cancel each need a real click** (`PostItem`, `PostCommodity`, `ConfirmPost*`, `PlaceBid`, `StartCommoditiesPurchase`, `CancelAuction`).
3. **Data in and out only through files (H).**
   - Out: addon data is saved on `/reload`, logout, or quit.
   - In: a data `.lua` inside an addon folder is read on load. A brand-new addon folder needs a full client restart.
   - `/reload` closes the AH window.
4. **Auctionator's price memory was unreliable in beta (M).** It reset its own database on every game load. That's why we build our own scanner and keep Auctionator for the buying and posting UI.
5. **Saved data may not load back (M).** In beta, data was written but sometimes not read back on login (wiki says fixed in build 70009; AHledger still keeps a workaround). Our addon treats its own data as write-only, and Python archives every copy.
6. **AH cut (H, from game data):** 5% at faction houses, **15% at neutral (Blackwater) houses**, deposits 5% and 25%. The "15% cut" forum report was a neutral AH. The ledger will confirm both from real sale mail.
7. **Posting details (M):**
   - Listings last 2, 8, or 24 hours.
   - Beta charged at least 1s deposit per post.
   - Gear prices round to whole silver.
   - At equal price the **newest listing sells first** (Gillee, unverified). If that's true, *matching* the lowest price beats undercutting it.
8. **Price ceilings (M).** The "Agent of the Black Market" vendor at Powderfuse Port sells scarce materials at fixed prices: Black Lotus and Arcane Crystal 50g; Pristine Leather, Frilled Lichen, and Pyrite 5g. The AH can't sustainably go above those.
9. **Profession changes (H/M):**
   - **Healing potions moved to First Aid.** Anyone with First Aid can make them now, so expect more competition there.
   - 600+ new recipes, and food gives XP.
   - No enchanting vellums.
   - Crafted-item vendor prices were cut repeatedly in beta, so check vendor prices in the live client.
10. **One AH per ruleset (M).** Forever has no realms. Whether Horde and Alliance share listings is unconfirmed, so we key data by ruleset + faction + house.
11. **Every market starts empty at launch (H).** Third-party beta data is thin and partly fake: AHledger had made-up item IDs listed at 40,000g.
12. **Strict real-money-trade enforcement (H).**
    - About 2,100 gold-buyer and 1,600 seller/bot accounts were closed during beta.
    - GDKP (selling raid loot for gold) is banned.
    - **Temporary Economy Holds** can freeze trade, mail, and AH access on suspicion alone.
    - Blizzard's [gold-buying article](https://us.support.blizzard.com/en/article/379634) says receiving gold from an "alt character" can put you at risk.
13. **Self-found mode blocks the AH and mail (H, client strings).** Forever's client has "You are self-found and cannot use the auction house" and "Recipient is self-found and cannot receive mail". **Never make the main or the AH alt self-found.**
14. **The live build is newer than the datamined code (H).** Live is build 70634; the newest datamined Forever code is 70532 (beta). Every API detail here comes from beta code, so re-run the API check on launch day.

## Idea catalog

### Build now (the MVP, in `forever-gold`)
1. **ForeverGold scanner addon.** On your click it runs one full scan, or it captures a scan another addon already ran. It saves each item's price ladder: units and prices, cheapest first. It tags each scan with house, faction, and time. It also snapshots bags, bank, and gold per character.
2. **Daily Gold Brief (Claude Code skill `/forever-gold`).** Python does the math:
   - Market value: the average price of the cheapest 25% of units, so one silly listing can't anchor it.
   - Craft margins after the real cut.
   - Vendor-price floors and Black Market ceilings.
   - A cold-start mode for launch week: sell, hold, or vendor gathered herbs and leather, flip items listed below vendor price, and only crafts with a 25%+ margin. No "invest" calls until there are 3 days of scans.

   Claude reviews the draft, writes 3-5 "Reads" bullets, and applies the plan.
3. **Plan guards in the AH.** Per item: max buy price, minimum post price, quantity. Rows turn green or red against tonight's fresh scan.
4. **Auctionator buy list.** One button creates "Claude: tonight" with exact names and max prices. You click Buy.
5. **Sales ledger.** It reads every "Auction successful" mail with `GetInboxInvoiceInfo`: realized price, deposit, and the actual cut.

**Session loop (8-12 minutes in game):**
1. Run `/forever-gold` on the PC.
2. Log in.
3. At the mailbox, Open All.
4. At the AH, click Scan, then Buy list. Buy by hand.
5. Post with Auctionator, one click per post.
6. Click Done (it runs `/reload`).

### Next (phase 2)
1. **Trade-chat watcher.** Run `/chatlog` and tail `Logs\WoWChatLog.txt`. Find WTS listings below the AH and WTB requests above it, plus "LF alch" requests you could pre-sell crafts into (Gillee's pre-selling). You whisper and trade by hand.
2. **Replans with the AH still open (no `/reload`).** Two ways:
   - Paste-in: Claude prints a compressed string, you paste it into the addon, and it decodes with Forever's built-in `C_EncodingUtil`.
   - "Slot addons": pre-create a few load-on-demand data addons before launching the client. Python writes the next unused one, and `/fg load` loads it mid-session. This is the pattern Everloot uses; files only count if they existed when the client started.
3. **Black Market AH digest.** Log listings whenever you open the BMAH, and Claude tracks the daily history.
4. **Cross-checks with community data.** Everloot's public MCP and AHledger's free API cover markets you didn't scan. Sanity-filter them, given the fake beta rows.
5. **"Blizzard API appeared" watcher.** A daily check for a Forever API namespace. If one shows up, switch to hourly official snapshots with no logging in.

**Also next:** phone alerts after each scan, an MCP wrapper over `fgold` so you can ask Claude Desktop or your phone "is 3g a good price for X", and P&L with cost basis plus a Gillee-style investment calendar.

### Later (phase 3)
1. **Legacy perk planner.** Spend each character's 16 Legacy points for profit. Master Chef and Luremaster sell food to levelers; Bartering gives cheaper vials.
2. **Waylaid Crate + Merchant's Favor optimizer.** Which crate to fill, and which Favor recipes have outputs worth selling.
3. **Leveling-cost route.** A local version of Gillee's cheapest 1-300 route using your own scan prices, exported as an Auctionator list.
4. **Raid-calendar investing.** The first raids unlock Dec 9 (Barrow Deeps, Hyjal Summit, Onyxia). Buy consumable mats in the lull before announcements; sell into raid nights.

### Yellow: possible, but be careful
- **One key = the next AH action** (like Auctionator's MagicButton). Each press does exactly one action, which is how the protected-call rules work, but it's tolerated rather than blessed. Never add auto-repeat or a macro keyboard.
- **Screenshot advisor:** you screenshot an AH page and Claude reads the image. Harmless, but slow.
- **Low-level gathering alt** for Frilled Lichen and Pristine Leather (beta suggests only characters at level 25 or below find them). Played by hand only.
- **Selling crafting services for gold in Trade chat.** Allowed, but keep every agreement in in-game chat. That's the evidence Blizzard's gold article asks for.
- **Moving gold between your own accounts.** See the next section.

### Red: never
- AutoHotkey, macros, AppleScript, key repeaters, or any software that clicks or types for you.
- Auto buy, post, or cancel loops, and snipers that buy on their own.
- Anti-AFK tricks to keep the AH alt "always online". The client logs you out after about 30 min idle, and Blizzard has suspended players for anti-AFK tools.
- Claude computer use or any vision-to-click agent driving the game.
- Memory reading, packet sniffing, or client modification.
- Input broadcasting, or focus-follow-mouse "streamlining" across two clients (banned since the 2020/2021 policy).
- Real-money trading, GDKP, or accepting gold from strangers ("gold bombing" got beta players actioned).

**Why the line matters:** the EULA bans "automated control" by any software, Forever's RMT enforcement is Blizzard's harshest yet, and penalties can reach your whole Battle.net account, meaning both WoW accounts and the gold.

## Second account and the Unraid VM

**What's allowed:**
- A second account, if every window is driven by hand. Blizzard's [multiboxing article](https://us.support.blizzard.com/en/article/24258) says playing multiple accounts at once is not a EULA violation.
- It needs its own subscription or game time.
- The alt must be on the **same ruleset** as your main, and same faction if factions turn out to have separate auction houses.

**The real risk is gold flow, not the alt itself:**
- Main to alt is a one-sided transfer, which is the pattern Economy Holds look for.
- A retail player reported a suspension after mailing about 2m gold between their own Battle.net accounts.

**On the VM:**
- No Blizzard policy on VMs was found.
- In community threads VMs come up mostly as a bot trick for dodging hardware bans, so a VM *could* be a weak risk signal. That's unverified.
- Driving the VM by hand through Parsec or Moonlight is fine. Anything that keeps it alive or clicks for you is not.

| Option | Cost | Upside | Risk |
|---|---|---|---|
| A. AH alt on the **main's own WoW account** | Free | Same-account moves look like normal alt play; mail is instant | You log out of the main to post (about 1 min each way) |
| B. WoW2 license on the **same Battle.net account**, second client on the PC | 2nd sub | Both online at once; one authenticator protects both | Gold moves between licenses; beta had a license-picker bug |
| C. WoW2 in the **Unraid VM** | 2nd sub + VM setup | Main PC stays free; the alt is always parked | B's risks, plus the VM question; SMB share needed for the bridge |

**Recommendation:**
1. **Launch week: option A.** It's free and safe while the market is empty anyway.
2. **Week 2-3:** if switching characters becomes the friction, move to B or C. The MVP's config already supports several WoW installs, so the VM is just another path.
3. **If you run two accounts:**
   - Send **goods** to the alt, not gold.
   - Let the alt buy its own materials with its own gold.
   - Keep gold transfers rare and small.
   - Keep both licenses on one Battle.net account with one authenticator.
4. **Cheap trial:** Invite-A-Friend codes reportedly give a WoW account Forever free Nov 4-11 ([support article 385883](https://us.support.blizzard.com/en/article/385883)). That could trial the VM at no cost. Verify the terms first.

## Launch-week measurement list

`fgold probes` prints most of these automatically. The open questions behind each measurement are under "Forever facts" above.

**Launch night, first 30-45 minutes:**
1. `/fg status` shows interface 16001, the AH house, and whether copper prices are allowed.
2. The plan round trip: `/forever-gold`, then `/reload`, and the game reports the plan id.
3. Saved data loads back on the second login.
4. First full scan: how long it takes, how many rows, and whether commodities match a manual herb search.

**First week:**
1. Scan throttle: does a scan work after 15 min? What happens if you try at 5 min?
2. The first sale mail: real cut (5% faction vs 15% neutral), deposits for 2/8/24h, and whether the 1s minimum holds.
3. Equal-price order: does the newest listing really sell first?
4. Do Horde and Alliance share an AH?
5. Cross-account mail delay, tested with a **cheap item, never gold**.

## Key sources
- Blizzard: [EULA](https://www.blizzard.com/en-us/legal/fba4d00f-c7e4-4883-b8b9-1b4500a402ea/blizzard-end-user-license-agreement), [multiboxing](https://us.support.blizzard.com/en/article/24258), [gold buying](https://us.support.blizzard.com/en/article/379634), [Economy Holds](https://us.forums.blizzard.com/en/wow/t/temporary-economy-holds-for-rmt/2381433), [input broadcasting policy](https://us.forums.blizzard.com/en/wow/t/policy-update-for-input-broadcasting-software/707426), [beta dev notes](https://us.forums.blizzard.com/en/wow/t/wow-forever-beta-development-notes-%E2%80%93-updated-october-26/2360696)
- Forever API: [Gethe/wow-ui-source `forever` branch](https://github.com/Gethe/wow-ui-source/tree/forever), [Ketho/BlizzardInterfaceResources `forever`](https://github.com/Ketho/BlizzardInterfaceResources/tree/forever), [warcraft.wiki.gg ReplicateItems](https://warcraft.wiki.gg/wiki/API_C_AuctionHouse.ReplicateItems), [aux-forever AH notes](https://github.com/TylerHampton/aux-forever/blob/main/docs/forever-auction-house.md)
- Static game data: [wago.tools DB2, product `wowf`](https://wago.tools/db2/ItemSparse/csv?build=1.60.1.70634)
- Community data: [AHledger API](https://ahledger.com/llms.txt), [Everloot MCP](https://everloot.app/docs/mcp), [auctionhouse.fyi](https://auctionhouse.fyi/docs/api/agents) (don't run its pixel-capture contributor app)
