# forever-gold

**Next action:** not set up yet? Do "One-time PC setup" below (about 25 min). Finish it before launch: Wed Nov 4, 5:00 PM CT. Already set up? Run the daily loop.

## What this is
- **ForeverGold** (WoW: Forever addon): records your AH scans, sale mail and bags, and shows tonight's plan with price guards in the AH window.
- **fgold** (Python, nothing to pip install): reads the addon's saved data, does the math, and writes the plan back into the addon.
- **/forever-gold** (Claude Code skill): turns the math into a 10-minute to-do list.

**The rule: Claude thinks, you click.** Nothing here buys, posts, cancels, presses keys, clicks, or keeps a character online. Every AH action is your own click.

## One-time PC setup (about 25 min)
Run every `py -3 ...` command in a terminal opened in `C:\dev\forever-gold`.

### Part 1: Python and the repo (about 15 min)
1. Install Python 3.12 or newer (5 min): `winget install Python.Python.3.13`, or the python.org installer with "Add python.exe to PATH" ticked. Both install the `py` launcher used below.
2. Turn off the Microsoft Store python alias (1 min): Settings > Apps > Advanced app settings > App execution aliases, switch off `python.exe` and `python3.exe`. Then open a new terminal.
3. Clone the repo to `C:\dev\forever-gold`, not a OneDrive folder: OneDrive sync can lock or offload files the game and fgold need (3 min). No `git` yet? Run `winget install Git.Git` first (Claude Code needs it too).
   ```
   git clone https://github.com/travv/forever-gold C:\dev\forever-gold
   cd C:\dev\forever-gold
   ```
4. Find WoW and save the config (1 min): `py -3 -m fgold doctor --write`. It lists the `_forever_` install it found and writes `config.local.json`. Found nothing? Copy `config.example.json` to `config.local.json` and set `roots` to your `_forever_` folder (forward slashes).
5. Check three values in `config.local.json` (3 min): `home_house` (your faction), `nest_egg_copper` (5000000 = 500g; plans only spend gold above it), `professions` (your current skill rank in each, for example `"First Aid": 75`: craft rows only use recipes that are yellow or easier at that rank, so raise it as you level).

### Part 2: The addons (about 5 min, WoW fully closed)
1. Install Auctionator (3 min): CurseForge app > World of Warcraft > the Forever flavor > search "Auctionator" > Install.
2. Install ForeverGold (1 min): `py -3 -m fgold install`. Do this before the client's first start: WoW only sees new addon files at a full client start.

### Part 3: First start (launch night, about 5 min)
1. Start WoW. At character select, click AddOns and check that ForeverGold and Auctionator are ticked (2 min).
2. Log in and type `/fg status` (1 min). Expect interface 16001, and plan `none` until your first `/forever-gold`.
3. Work through `docs/launch-checklist.md` (30-45 min).

## Daily loop (about 15 min)
**Out of game (2-4 min):** in Claude Code, in the repo folder, type `/forever-gold`. Claude applies the plan and replies with its id and up to 5 steps.

**In game (8-12 min):**
1. Log in. The chat line `ForeverGold: plan <id> ...` must show Claude's plan id (1 min).
2. Mailbox: Open All. The sale invoices get recorded (1 min).
3. AH: click **Scan**. While it runs, click **Buy list** and buy by hand in Auctionator (3-5 min). The plan window opens with the AH when the plan has rows; `/fg` toggles it.
4. When the rows re-color (green = go, red = skip or HOLD), post with Auctionator, one click per post (2-4 min).
5. Click **Done**. It reloads the UI, which saves tonight's data for the next brief (1 min).

## Using Claude Code on the PC
1. Install Claude Code if it is missing (3 min). In PowerShell: `irm https://claude.ai/install.ps1 | iex`. It needs Git for Windows (Part 1, step 3).
2. Open the repo folder (1 min): `cd C:\dev\forever-gold`, then `claude`. Start it here, or it will not see the skill or the permission file.
3. Type `/forever-gold` (2-4 min). Claude runs `fgold brief`, trims the draft plan, writes its Reads into the newest `reports/*-brief.md`, and runs `fgold plan --apply`.
4. Ask follow-ups in plain words, for example "is 3g a good price for Peacebloom?" (Claude runs `fgold price`).

Only the `fgold` commands, and Claude's edits to `plan/draft.json` and the newest brief, run without a prompt (`.claude/settings.json` and the skill's `allowed-tools`). Anything else asks you first, and edits inside the WoW folders are refused outright.

## Optional: second account in the Unraid VM
Not for launch week. Start with an AH alt on your main's own WoW account (free, and mail is instant). Set this up in week 2-3 if switching characters gets annoying.

**Setup (about 20 min, WoW closed in the VM):**
1. In the VM, install Auctionator and create the empty folder `_forever_\Interface\AddOns\ForeverGold` (3 min).
2. Share the VM's `_forever_` folder over SMB as `forever` (5 min): folder Properties > Sharing > Advanced Sharing, allow Change for the account the PC connects with. Then Properties > Security: Read on `_forever_` (covers `WTF`), Modify only on `Interface\AddOns\ForeverGold`.
3. On the PC, open `\\UNRAID-VM\forever` in Explorer once and save the login. Add the root to `roots` in `config.local.json` (2 min): `{"name": "vm", "path": "//UNRAID-VM/forever"}` (use the VM's real name).
4. Install the addon into the VM (1 min): `py -3 -m fgold install --root vm`. Over the network it copies instead of linking, so rerun it after every `git pull`.
5. Start WoW in the VM, log in once and log out (that writes the first file), then run `py -3 -m fgold doctor` on the PC: the vm root and its account should show (5 min).

**Rules for the second account:**
1. Drive the VM by hand through Parsec or Moonlight. No anti-AFK, no input sharing or broadcasting between clients.
2. Same ruleset and faction as the main.
3. Move goods, not gold. The alt buys its own materials with its own gold: the brief's spend cap only counts the gold on one WoW account.
4. Keep both WoW licenses on one Battle.net account with one authenticator.
5. Never make the main or the alt self-found: self-found characters cannot use the AH or receive mail.
6. Never buy gold, join GDKP runs, or accept gold from strangers: Temporary Economy Holds can freeze trade, mail and the AH on suspicion alone.

VM caveat (unverified): no Blizzard policy on VMs was found, but community threads tie VMs to bots dodging hardware bans, so a VM could be a weak risk signal. A second client on the PC avoids the question.

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| The login line shows another plan id than Claude's, or `none` | The client did not load the new plan file. Do a full restart: exit WoW completely (not /reload, not logout) and start it again. Still wrong? Run `py -3 -m fgold doctor`, then `py -3 -m fgold install` with WoW closed. |
| ForeverGold is missing or "out of date" in the AddOns list | The client's interface number moved past the addon's (16001). Tick "Load out of date AddOns" at character select, log in, read `iface` in `/fg status`, and ask Claude to update `## Interface:` in `addon/ForeverGold/ForeverGold.toc`. |
| The brief shows no new data after you played | WoW writes SavedVariables only at /reload, logout or quit, and not after a crash. Click Done or log out, then run `/forever-gold` again. |
| `python` is not found, or opens the Microsoft Store | Use `py -3 -m fgold ...`, and turn off the Store alias (Part 1, step 2). |
| `fgold install` says the junction failed | It copies the addon instead. That works; rerun `py -3 -m fgold install` after every `git pull` so the copy stays current. |
| `fgold status` says the plan did not reach a WoW root | That root never got `PlanData.lua` (the addon is not installed there, the share was offline, or its addon folder links somewhere else). Fix the reason it names, usually `py -3 -m fgold install --root <name>` with WoW closed there, then run `/forever-gold` again. |
| The plan has no buy rows | Your gold is below `nest_egg_copper`. Plans only spend profit, so they sell until you pass it. |
| Scan says "next full scan in N min" | The 15-min scan throttle. Use the plan as it is: with no scan this session the rows stay gray, so post at or above the floor shown. |

## Updating (2 min)
1. In `C:\dev\forever-gold`, run `git pull`.
2. If `install` copied instead of linking, or you use the VM: rerun `py -3 -m fgold install` (and `py -3 -m fgold install --root vm`).
3. New addon files need a full client restart. Changed files load with `/reload`.

## Where things live
- `reports/<YYYYMMDD-HHMM>-brief.md`: each brief, with Claude's Reads at the bottom.
- `plan/draft.json`: the draft Claude edits. `fgold plan --apply` turns it into `addon/ForeverGold/PlanData.lua`, the file the game reads.
- `data/`: every saved copy of the addon's file, plus your price history. Never delete it.
- `config.local.json`: your settings (WoW roots, nest egg, professions).

## Run the tests (1 min)
In `C:\dev\forever-gold`:
```
py -3 -m unittest discover -s tests -v
```
Everything must pass, including `tests/test_redline.py` (the no-automation check). The addon harness (`luajit tests/wow/run.lua`) needs LuaJIT on PATH; without it, the Python suite skips that part.
