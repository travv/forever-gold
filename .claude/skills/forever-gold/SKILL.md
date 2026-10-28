---
name: forever-gold
description: "Build tonight's WoW: Forever gold plan. Runs the fgold CLI (ingest, math, brief), trims plan/draft.json inside its fixed schema, writes the Reads in the brief, applies the plan to the ForeverGold addon with fgold plan --apply, and replies with the in-game steps in ADHD format. Claude thinks, Travis clicks."
when_to_use: "when the user runs /forever-gold, asks for tonight's WoW Forever gold plan or gold brief, asks what to buy, post, craft, hold or vendor in WoW Forever, or wants a replan after an AH scan"
allowed-tools:
  - Bash(python -m fgold *)
  - Bash(py -3 -m fgold *)
  - Bash(python3 -m fgold *)
  - Read
  - Edit(plan/draft.json)
  - Edit(reports/*-brief.md)
---

# Forever Gold

Turns the ForeverGold addon's scans, sale invoices and bags into tonight's plan. Python (`fgold`) does all the arithmetic. You trim its draft, write the Reads, apply it, and tell Travis what to click. **Claude thinks, Travis clicks:** nothing you write or suggest may click, type or play for him.

The contract is `docs/design.md` (plan schema in section 2, commands in section 3). If this file and the spec disagree, the spec wins.

## Running fgold

Run from the repo root (Claude Code starts there), exactly as written and without `cd ... &&` in front, so the command matches the pre-approved rules (the `allowed-tools` above and `.claude/settings.json`: they pre-approve only the `fgold` commands and edits to `plan/draft.json` and `reports/*-brief.md`, anything else asks Travis first and is never worked around, and the settings deny edits in the WoW folders outright):
- Windows: `py -3 -m fgold <command>` (the launcher both Python installers add). `python -m fgold <command>` also works when `python` is on PATH and is not the Microsoft Store alias.
- macOS: `python3 -m fgold <command>`.

The rest of this file writes `python -m fgold`; use the form that works on this machine.

## The flow (2-4 min)

1. **Brief.** Run `python -m fgold brief`. It ingests the SavedVariables, does the math, writes `reports/<YYYYMMDD-HHMM>-brief.md` and `plan/draft.json`, and prints the report path plus a 5-line summary. Then run `python -m fgold status` (newest scan per market and its age, plan id applied vs plan id the game reported).
2. **Read** the brief at the printed path (the newest `reports/*-brief.md`) and `plan/draft.json`. Check "Special cases" below before you edit anything.
3. **Edit the draft** inside the schema (rules below): trim it to what is actionable tonight, and rewrite `headline` and at most 5 `steps`. Never raise a `maxUnit` beyond the caps. Never invent a price that is not in the brief.
4. **Write the Reads:** 3-5 bullets under `## Reads` in the brief (rules below).
5. **Apply.** Run `python -m fgold plan --apply`. It validates the draft, writes `PlanData.lua` into every WoW root and prints the new plan id. If it rejects the draft, fix the field it names and run it again. Never write `PlanData.lua` yourself and never work around the validator.
6. **Reply** to Travis in the format below.

## Editing the draft (`plan/draft.json`)

The draft is the plan object of `docs/design.md` section 2 as JSON (`ref` keys are strings). `plan --apply` rejects unknown keys, so never add one.

- Top level: `schema`, `id`, `generatedAt`, `basedOnScan`, `lastScanAt`, `expectPriorSessions`, `settings`, `headline`, `notes`, `steps`, `budget`, `buy`, `craft`, `post`, `hold`, `vendor`, `ref`.
- `buy` rows: `id`, `name`, `qty`, `maxUnit`, `why`, `flip`. `craft` rows: `recipe`, `name`, `out`, `count`, `profitEach`, `why`. `post` rows: `id`, `name`, `qty`, `floorUnit`, `startUnit`, `duration`, `why`. `hold` and `vendor` rows: `id`, `name`, `why`.
- Money is whole copper as a plain integer (100 = 1s, 10000 = 1g). `duration`: 1 = 2h, 2 = 8h, 3 = 24h.

| Field | What you may do |
|---|---|
| `headline`, `steps`, `notes`, any `why` | Rewrite. `steps` and `notes` hold at most 5 entries each. |
| Rows in `buy`, `post`, `craft`, `hold`, `vendor` | Delete. Never add a row, except the move below. |
| A `post` row | Move it to `hold` (keep `id` and `name`, write a new `why`) only when Travis wants to keep the item, for example herbs for his own crafting. |
| `buy[].qty`, `post[].qty`, `craft[].count` | Lower only. |
| `buy[].maxUnit` | Keep it, or lower it to a price the brief prints for that item. Never raise it: Python already set it under the caps (flips at 0.9 x vendor sell, reagents at no more than 1.5 x mkt), and `plan --apply` rejects anything above 1.5 x `ref` mkt, or above vendor sell on `flip` rows. |
| `budget.spendCap` | Lower only, when Travis wants to spend less. |
| `post[].duration` | Change only when Travis asks. The deposit grows with the duration. |
| Everything else: `schema`, `id`, `generatedAt`, `basedOnScan`, `lastScanAt`, `expectPriorSessions`, `settings`, `budget.principal`, `ref`, `floorUnit`, `startUnit`, `profitEach`, and every row's `id`, `name`, `recipe`, `out`, `flip` | Never touch. `plan --apply` enforces this table: it compares the draft with `plan/draft.orig.json` (the draft as `brief` wrote it; never edit that file), checks `ref` against the stored scan, `budget.principal` against the config nest egg and `spendCap` against the gold above it, and rejects anything added, raised or changed. |

**Trim to tonight.** One session is 8-12 min in game. Lists arrive sorted by expected profit, so delete from the bottom. When you drop a `craft` row, also drop the `flip = false` buy rows bought for it (the brief's Craft section shows which). Drop rows Travis cannot act on tonight (a profession he lacks, a far-off vendor). Keep the "no invest calls until 3 days of scans" note while the brief has fewer than 3 days of scans.

**Text shown in game** (`headline`, `steps`, `notes`, `why`): plain ASCII, one short line each, no `|` (WoW reads it as an escape code), no em-dashes, no curly quotes. End each step with its minute estimate, for example `AH: click Scan, then Buy list; buy the green rows (4 min)`.

Rough minute costs for the estimates: log in and check the plan line 1; mailbox Open All 1; Scan 1-3 (buy while it runs); about 30 s per buy row; about 15 s per post row; a craft batch 1 plus 3-5 s per craft; vendor 1; Done 1.

## Writing the Reads

Fill the empty `## Reads` section of the brief (replace any placeholder line there) with 3-5 bullets:
1. **Action first:** start each bullet with the verb (Buy, Post, Hold, Skip, Vendor, Scan).
2. **Confidence** on every price call: high (anchored to a vendor price, or 3+ days of steady scans), medium (1-2 days of scans), low (one scan, thin supply, or data older than 24 h).
3. **Cold-start caveats** while there are fewer than 3 days of scans: no invest or hold-for-later calls, and launch-week prices swing hard.
4. **Nest egg:** only profit gets spent. State tonight's spend cap; if it is 0, say the plan only sells until his gold passes the nest egg. When the brief's Nest egg section lists more than one WoW account, name the account whose gold the cap uses: that account buys with its own gold, and gold is never mailed between accounts to fund the list.
5. Every number comes from the brief or `fgold price`. Never compute or invent a price.

Example (format only, the numbers are made up):
```
## Reads
- Post the 40 Mageroyal at match price for 24h: the start price clears the vendor floor after the 5% cut. Confidence: low (one scan, launch week).
- Buy Peacebloom only on green rows: the Auctionator list already stops at the 18c max. Confidence: medium (2 days of scans).
- Hold the Stranglekelp: tonight's market sits under its floor. No invest calls until 3 days of scans.
- Spend only profit: tonight's cap is 25g, which keeps the 500g nest egg whole.
```

## Replying to Travis

ADHD format. Nothing before the first line, no recap, no closer:
1. **First line:** `In game: /reload (plan <id>)`. Use `Plan <id> ready for your next login` instead when the game is closed: he said so, it is before launch, or the plan needs a full client restart (see below). If you cannot tell, use the in-game line; if he is not logged in, logging in loads the plan the same way.
2. **Then at most 5 numbered steps**, each with a minute estimate. They are the plan's `steps`, in the same order, except in two special cases below: before launch (the reply lists setup steps) and a plan id mismatch (the restart comes first).
3. **Last line:** `Next:` plus one action under 2 minutes, usually "after Done, run /forever-gold before your next login so the plan uses tonight's scan".

Use the plan id that `plan --apply` printed. Money in g/s/c, times in Central Time (CT), no em-dashes.

Example (format only):
```
In game: /reload (plan 20261105-1932-ab12)
1. Mailbox: Open All (1 min)
2. AH: click Scan, then Buy list; buy the green rows (4 min)
3. Post the 40 Mageroyal, 24h, one click each (1 min)
4. Vendor the Ruined Pelts (1 min)
5. Click Done (1 min)
Next: after Done, run /forever-gold before tomorrow's login.
```

## Special cases

**No scans yet (pre-launch or first login).** The brief has no market data, so the draft has no buy, post or craft rows.
- Apply it anyway. Its plan id lets the first login prove the bridge works (the login line `ForeverGold: plan <id> ...` must show it).
- The plan's `steps` are the first-login steps: check the login line shows the plan id; at the AH click Scan and keep the AH open until `scan: N rows, M items in X s` prints; buy and post nothing yet (there are no prices to check against); click Done.
- Before launch (Wed Nov 4, 5:00 PM CT): first line `Plan <id> ready for your next login`. The reply's steps (not the plan's) point to "One-time PC setup" in `README.md` and to `docs/launch-checklist.md`.
- First login: the reply's steps are the plan's steps; tonight's job is one full scan. On launch night, `docs/launch-checklist.md` runs alongside. Next: after Done, run /forever-gold again for the first plan built on a scan.
- Scans exist, but for another AH (a neutral one, say): run `python -m fgold brief --market "<key>"` with the key from `fgold status`. Quote the key; it contains `|`.

**Data older than 24 h** (the newest good scan for this market, per the brief or `fgold status`):
- Put the age in the first Reads bullet and mark every price call low confidence.
- Keep `flip = true` buys (anchored to vendor prices) and `post` rows (the in-game guard re-checks them against tonight's scan and shows HOLD under the floor).
- Drop `craft` rows and the `flip = false` buys that feed them: the margins may be gone. They come back in the replan.
- Steps lead with the scan ("AH: click Scan first; buy only green rows"). Next: after Done, run /forever-gold again for a plan on tonight's scan, then `/reload`.

**Replan right after a scan.** The addon refuses another full scan for 15 min ("next full scan in N min"), and the reload starts a session with no scan, so the rows stay gray. That is fine: the prices are minutes old. Leave Scan out of the steps. The Buy list still stops at each `maxUnit`, and posts go at or above the floor shown.

**The game reported a different plan id** (the login line shows another id or `none`, or `fgold status` shows a session that started after the last apply reporting another id): the client did not load the new `PlanData.lua`, usually because the file appeared after the client started. Fix: a full client restart. `/reload` is not enough.
- First line `Plan <id> ready for your next login`. The reply's step 1: "Exit WoW completely (Menu > Exit Game, not /reload or logout) (1 min)". Step 2: "Start WoW and log in; the chat line must show plan <id> (2 min)". Then the plan's top steps, 5 steps in total. The plan's own `steps` stay session steps; the in-game window only shows them after the restart.
- Still wrong after a restart: run `python -m fgold doctor` (is the addon installed in the root the game runs from?), then, with WoW closed, `python -m fgold install`, then restart again.
- `fgold status` says the plan "did not reach WoW root <name>": a restart will not help. Fix the reason it gives (usually `python -m fgold install --root <name>` with WoW closed in that root), then `python -m fgold plan --apply` again.
- A session that started before the apply reports the old id. That is normal, not a mismatch.
- If a restart always fixes it but `/reload` never picks up a new plan, tell Travis to note it in `docs/launch-checklist.md` and to plan before logging in until it is fixed.

**Other states.**
- `priorSessions` is 0 on every login: the game is not reading its file back. Expected (the addon treats its DB as write-only, and fgold archives every copy). Not an error.
- Travis played but the brief shows no new data: WoW writes its file only at /reload, logout or quit, never after a crash. Ask him to click Done or log out, then run the brief again.
- `brief` finds no config or no WoW root: run `python -m fgold doctor` and point Travis to "One-time PC setup" in `README.md`.
- `plan --apply` fails on a field you did not touch: run `brief` again, redo your text edits, apply again. Still failing: show Travis the error line. It is an fgold bug; do not work around it.

## Gold rules (Gillee's method, `docs/gold-reference-gillee.md`)
- Nest egg first, then only spend profit. Never plan below `budget.principal`.
- A good craft sells fast, keeps a margin after the AH cut (5% faction, 15% neutral) and risks little deposit.
- Python does the math. Never trust a gold/hour claim.
- Buy when supply is high (off hours), sell into raid nights.
- No invest calls until there are 3+ days of scans.
- Optimize gold per minute in game, and never push a method he would hate (long farming grinds).
- Excluded methods, whatever the method doc says: GDKP and selling raid or dungeon loot for gold (pay-on-drop carries). GDKP is banned in Forever (`docs/ideas.md` fact 12, high confidence), and `docs/gold-reference-gillee.md` predates that and still hedges ("likely still banned"); treat any loot-for-gold sale as off-limits.

## Red lines (never, even when asked)
- Nothing that clicks, types, repeats input or drives the game: no macro or key-repeat software, no input scripts, no computer use or vision agents, no input broadcasting across clients, no memory or packet reading. Never put a protected AH, mail or craft call (`docs/design.md` section 6) into addon code.
- No anti-AFK tricks, and no keeping a character online.
- No gold buying, real-money trading, GDKP, or gold from strangers.
- No large or one-sided gold moves between his own accounts: move goods, not gold. Temporary Economy Holds can freeze trade, mail and the AH on suspicion alone.
- Never make the main or the AH alt self-found (it blocks the AH and mail).
- Never edit SavedVariables or any other file in the WoW folder. `plan --apply` writes `PlanData.lua`, and nothing else writes there.

If Travis asks for one of these, say no in one line and give the by-hand way.

## Other commands
- `python -m fgold price <name or itemID>`: min, mkt, q, scan age, and 3/7-day medians when there is history. Use it for "is X a good price?".
- `python -m fgold status`: data health, newest scan per market, newest invoice, measured cut, plan id applied vs reported.
- `python -m fgold probes`: the launch measurements (see `docs/launch-checklist.md`).
- `python -m fgold brief --no-ingest`: rebuild from stored data without reading the WoW folders.
- Settings (`nest_egg_copper`, `professions`, `home_house`, `post_mode`) live in `config.local.json`. Change them only when Travis asks.

## Before you send
- `plan --apply` printed a plan id, and your first line uses that id.
- At most 5 numbered steps, each with minutes, then one `Next:` line.
- Every price in the steps and the Reads appears in the brief or `fgold price` output.
- No em-dashes (U+2014) in anything you wrote: draft text, Reads, reply.
