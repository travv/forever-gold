# forever-gold: Claude-assisted gold making in WoW: Forever

Personal project for Travis (GitHub `travv`). It isn't work code, so keep job and client context out of it.

## What this is
- **`addon/ForeverGold/`:** a small WoW addon. It records AH scans, sale invoices, and bags, and shows the plan with price guards in the AH. Its own button creates an Auctionator buy list.
- **`fgold/`:** a Python CLI (stdlib only). It ingests the addon's SavedVariables, does the math, and writes a draft plan and a brief. `plan --apply` writes `PlanData.lua` back into the addon folder.
- **`.claude/skills/forever-gold/`:** the `/forever-gold` skill. It runs the CLI, Claude edits the draft and writes the "Reads", then it applies the plan.
- **Specs:** `docs/design.md` (the contract), `docs/api-notes.md` (checked Forever API facts), `docs/ideas.md` (roadmap and sourced research), `docs/gold-reference-gillee.md` (gold-making method).

## Red lines (non-negotiable)
Blizzard's EULA bans software that automates the game. A ban would hit every WoW account on the Battle.net account, along with all the gold.

**Never write code that:**
- calls a protected AH/mail/craft function (the list is in `docs/design.md` section 6);
- presses keys or clicks (AutoHotkey, pyautogui, pynput, AppleScript, macros, key repeaters);
- keeps a character online (anti-AFK);
- drives the client with computer use or vision;
- reads game memory or packets;
- broadcasts input across clients;
- sends chat messages automatically.

**Claude thinks, Travis clicks.** `tests/test_redline.py` enforces this; it must pass.

Never suggest buying gold, GDKP, accepting gold from strangers, or large one-sided gold moves between his own accounts. Forever's Temporary Economy Holds can freeze trade, mail, and the AH on suspicion.

## Commands
```
python -m fgold doctor --write      # find WoW installs, save config.local.json
python -m fgold install             # link/copy the addon (then FULL client restart the first time)
python -m fgold brief               # ingest + compute: reports/<ts>-brief.md + plan/draft.json
python -m fgold plan --apply        # validate draft, write PlanData.lua to every WoW root
python -m fgold status | probes | price <item>
python -m unittest discover -s tests -v
luajit tests/wow/run.lua            # addon harness (stubbed WoW API)
```
On Windows use `py -3 -m fgold ...` if `python` isn't on PATH.

## How to talk to Travis
He has ADHD and plays as a busy dad. Optimize gold per minute of in-game time.
1. **First line = the next action** (a command, a click, or "/reload").
2. Multi-step work goes in a numbered list. Cap lists at 5; split into "now" vs "later" if needed.
3. Give **specific time estimates** ("about 3 min in game").
4. Restate where we are each turn ("step 2 of 4 done: plan applied").
5. No preamble, no recap, no "let me know" closers. Matter-of-fact about errors: cause + fix.
6. **No em-dashes** (U+2014) in anything you write, code comments included. Use colons, commas, parentheses.
7. Times in **Central Time (CT)**.
8. UI colors use the Dracula palette (see `docs/design.md` 1.7).

## Gold-making rules (Gillee's method, see `docs/gold-reference-gillee.md`)
- Nest egg first. Then **only spend profit**, never dip below the nest egg (`budget.principal`).
- A good craft has: high sale rate, a margin **after the AH cut** (5% faction, 15% neutral), and a low deposit risk.
- Do the math. Never trust a gold/hour claim.
- Buy when supply is high (off hours) and sell into raid nights.
- Investing needs history: no invest calls until there are 3+ days of scans.
- The best gold farm is one you can do forever. Don't recommend methods he'd hate.

## Forever facts to trust over memory
Read `docs/api-notes.md` before writing Lua. Forever runs the **modern** API (interface 16001), not Classic 1.12. Retail knowledge mostly carries over, but check names against the notes.

Key points:
- Scans are 0-based.
- Every buy, post, and cancel needs a hardware event.
- `/reload` closes the AH.
- New files need a full restart.
- The SavedVariables read-back is unreliable, so treat the DB as write-only.
- Healing potions moved to First Aid.
- Black Market vendor caps limit some prices.

## Testing rules
- Run both test suites before calling anything done.
- New addon code needs a harness scenario; new math needs a unit test.
- Don't edit `tests/fixtures/harness/` by hand; regenerate it with the harness.
