# Launch checklist: WoW: Forever, Wed Nov 4, 2026, 5:00 PM CT

**Next action:** Nov 4 at 4:00 PM CT, with WoW closed, start "Before the servers open" (about 15 min).

How to use it:
1. Tick a box when its check is done, and fill in its **Record** line here (or paste it to Claude).
2. **Probes** lines need the game's file on disk first: click **Done** (or log out), then in `C:\dev\forever-gold` run `py -3 -m fgold ingest` and `py -3 -m fgold probes`.
3. Something broke? See "Troubleshooting" in `README.md`.
4. When a section is done, paste its Record lines into Claude Code so it can update `docs/api-notes.md` and the settings that depend on them.

All times are Central Time (CT).

## Before the servers open (Nov 4, 4:00-4:45 PM CT, about 15 min, WoW closed)

- [ ] **Static refresh** (5 min)
  - Do: `py -3 -I tools/fetch_static.py --build latest`
  - Record: build ____ (its `build` line; the default was 1.60.1.70634); items ____ and recipes ____ (its `wrote` line); error, or "leaving it unchanged": ____
  - Probes: not shown. A new build means new item, recipe and vendor data for the next brief. If the download fails, the old data stays in place; rerun it later.
- [ ] **API contract refresh** (3 min)
  - Do: `py -3 tests/test_api_contract.py --refresh`
  - Record: pass or fail ____; missing API names ____
  - Probes: not shown. Any missing name: have Claude fix the addon before you log in.
- [ ] **Addon in place** (2 min)
  - Do: `py -3 -m fgold doctor`. If it shows ForeverGold missing in a root, run `py -3 -m fgold install`.
  - Record: roots found ____; addon installed in each: yes / no
  - Probes: not shown (doctor prints it).
- [ ] **Cold-start plan** (4 min)
  - Do: type `/forever-gold` in Claude Code. With no scans the plan is nearly empty; that is expected.
  - Record: plan id applied ____
  - Probes: not shown. `py -3 -m fgold status` prints the applied id.

## Launch night: the first 30-45 min (from 5:00 PM CT)

- [ ] **/fg status** (3 min)
  - Do: log in and type `/fg status`. Type it again with the AH open: house and copperOK fill in there.
  - Record: iface ____ (expect 16001); build ____; project ____ (expect 1); copperOK ____ (false = prices round up to whole silver); house ____; priorSessions ____; plan id ____
  - Probes: iface, build and copperOK per session. If the build is newer than the one the static refresh fetched, rerun that refresh after tonight.
- [ ] **Plan id round trip** (5 min)
  - Do: check that the login line `ForeverGold: plan <id> (<age> old): ...` shows the cold-start plan id. Then, still logged in, run `py -3 -m fgold plan --apply` (it prints a new id), type `/reload` in game, and check that the line shows the new id.
  - Record: applied ____, at login ____; re-applied ____, after /reload ____
  - Probes: not shown. After Done, `py -3 -m fgold status` shows plan id applied vs plan id the game reported. Wrong id at login: full client restart (README). If only `/reload` misses new plans, note it here and plan before logging in until it is fixed.
- [ ] **Read-back on second login** (3 min)
  - Do: log out to character select, log back in, and type `/fg status`.
  - Record: priorSessions at first login ____; after logout and login ____; after a full client restart (optional) ____
  - Probes: read-back (priorSessions per session). 0 every time means the game is not reading its file back. The addon copes (its DB is write-only), and fgold keeps every copy.
- [ ] **First scan: timing and rows** (5 min)
  - Do: at the AH, click **Scan** (or type `/fg scan`) and note the clock. Keep the AH open until the chat line `scan: N rows, M items in X s` appears.
  - Record: clicked ____ CT; line at ____ CT (the gap is the server wait); rows ____; items ____; X ____ s (time to read the rows); no line after 3 min: ____
  - Probes: scan durations and results from scanLog (`ok`, `dropped`, `closed`, `noreply`), with the row count.
- [ ] **Commodity check** (5 min)
  - Do: right after the scan, search one herb with listings (Peacebloom, Silverleaf or Earthroot) and note the cheapest unit price and the quantity available. Click **Done**, then run `py -3 -m fgold ingest` and `py -3 -m fgold price Peacebloom` (use your herb).
  - Record: herb ____; AH search: min ____, quantity ____; fgold price: min ____, q ____
  - Probes: not shown; `fgold price` shows it. The same min means commodity unit prices are right (unit = buyout / count). The quantity can drift a little as things sell.

## First week (Nov 5-11)

- [ ] **Scan throttle** (2 min of clicks, spread over 15 min)
  - Do: 5 min after a successful scan, type `/fg scan force` (plain Scan waits 15 min on its own). At 15+ min, in the same session, click **Scan**.
  - Record: at 5 min, result ____; at 15+ min in the same session, result ____
  - Probes: scanLog results per request (`ok`, `dropped`, `noreply`). If 5 min works, tell Claude: the addon's 15-min wait can shrink. If the 15-min scan fails too, Forever allows one list per session.
- [ ] **Real cut from the first sale invoice** (3 min)
  - Do: after your first sale (chat: "A buyer has been found for your auction of ..."), open the mailbox and click Open All. Click **Done**, then ingest and probes.
  - Record: house ____; sale price ____; AH cut ____; cut rate ____ (expect 5% at a faction AH, 15% at a neutral one)
  - Probes: cut rate per invoice and per house. After 3 seller invoices for a house, the brief uses the measured rate on its own.
- [ ] **Deposits and the 1s minimum** (5 min)
  - Do: in the AH sell tab, place a herb stack and read the deposit shown at 2h, 8h and 24h (no need to post it). Then post one cheap item (vendor price under 1s) for 2h, and note your gold before and after: the difference is the deposit charged.
  - Record: herb ____ x ____: 2h ____, 8h ____, 24h ____; cheap item: shown ____, charged ____ (1s minimum holds: yes / no)
  - Probes: deposit probes (2h, 8h, 24h per item), but only for items on the plan's Post list. Compare them with the sell tab.
- [ ] **Equal-price selling order** (5 min, then wait for the sale)
  - Do: pick a commodity where other sellers list units at the lowest price. Post 1 unit at exactly that price (2h), and note how many units sat at that price before yours. When your sale message arrives, search the item again.
  - Record: item ____; price ____; units at that price before yours ____; posted ____ CT; sold ____ CT; other sellers' units still at that price ____
  - Probes: not shown. Theirs still there after yours sold: the newest listing sells first, so matching (`post_mode: match`) stays. Yours sold only after theirs: oldest first, so ask Claude to switch to undercutting.
- [ ] **Recipe ranks** (1 min)
  - Do: open First Aid (or any crafting profession) and note your rank and the color of one recipe near it, for example Linen Bandage (fgold expects it yellow from 30) or Minor Healing Potion (yellow from 55).
  - Record: profession ____; rank ____; recipe ____ is orange / yellow / green / gray
  - Probes: not shown. fgold plans a recipe only from the rank where it turns yellow (`skill` in `static/recipes.json`). A recipe that is yellow below that rank means the gate is too strict; tell Claude either way.
- [ ] **Faction split** (10 min)
  - Do: on your faction's AH, post 1 cheap item at an odd price you will recognize (for example 1 Linen Cloth at 9s 87c, 2h). From a character of the other faction on the same ruleset (or a friend's), search for it.
  - Record: seen from the other faction: yes / no ____; date ____
  - Probes: not shown. `py -3 -m fgold status` lists scans per market (`<region>|<realm>|<house>`). If the AH is shared, tell Claude so the two houses can pool data.
- [ ] **Cross-account mail delay** (5 min, only with a second WoW account)
  - Do: mail 1 cheap item (Linen Cloth) from the main to a character on your other WoW account. **Never gold, no COD.** Watch the alt's mailbox.
  - Record: sent ____ CT; arrived ____ CT; delay ____ min; same-account mail instant: yes / no ____
  - Probes: not shown.
