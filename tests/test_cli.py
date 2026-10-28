"""CLI smoke tests: main(argv) in temp dirs with --wow-root at the handmade fixture."""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import test_support as ts
from fgold import apply, luasv

SECTIONS = ["## Next action", "## Data health", "## Buy", "## Post", "## Craft", "## Hold / Vendor", "## Nest egg",
            "## Reads"]
NO_SCANS = "no scans yet: log in, open the AH, click Scan, then /reload"


class TestBrief(ts.TempHomeCase):
    def test_brief_on_the_fixture(self):
        code, out, err = self.run_cli("brief")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("Next: "))
        self.assertIn("plan --apply", lines[0])
        self.assertRegex(lines[0], r"about \d+ min in game")
        self.assertTrue(lines[1].startswith("report: "))
        self.assertTrue(lines[2].startswith("draft: "))
        self.assertEqual([l[:2] for l in lines[3:]], ["1.", "2.", "3.", "4.", "5."])
        report = self.home / "reports" / "20261105-1930-brief.md"
        self.assertTrue(report.is_file())
        text = report.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], lines[0], "the report's first line is the next action")
        positions = [text.index(s + "\n") for s in SECTIONS]
        self.assertEqual(positions, sorted(positions), "sections in spec order")
        self.assertEqual(text[text.index("## Reads") + len("## Reads"):].strip(), "", "## Reads is left empty")
        self.assertNotIn("## Movers", text, "no history yet")
        self.assertIn("Confidence: low", text)
        self.assertIn("Thu Nov 5, 7:30 PM CT", text)
        self.assertNotIn("\u2014", text)
        draft = json.loads((self.home / "plan" / "draft.json").read_text(encoding="utf-8"))
        apply.validate(draft, static=ts.load_static(), copper_ok=False)
        self.assertEqual(draft["basedOnScan"]["market"], ts.MARKET)
        self.assertTrue(all(isinstance(k, str) for k in draft["ref"]))
        self.assertTrue((self.data_dir / "scans" / "us" / "Forever" / "Alliance" / f"{ts.SCAN_AT}.json.gz").is_file())

    def test_second_brief_ingests_nothing_new(self):
        self.run_cli("brief")
        code, out, _ = self.run_cli("brief")
        self.assertEqual(code, 0)
        report = (self.home / "reports" / "20261105-1930-brief.md").read_text(encoding="utf-8")
        self.assertIn("ingest: 0 new of 1 file", report)

    def test_no_ingest_uses_the_store(self):
        self.run_cli("ingest")
        code, out, _ = self.run_cli("brief", "--no-ingest", roots=[])
        self.assertEqual(code, 0)
        self.assertIn("market us|Forever|Alliance", out)

    def test_market_option(self):
        code, _, err = self.run_cli("brief", "--market", "us|Forever|Horde")
        self.assertEqual(code, 1)
        self.assertIn("no scans for market", err)
        code, _, err = self.run_cli("brief", "--market", "nonsense")
        self.assertEqual(code, 2)
        self.assertIn("Fix:", err)
        code, out, _ = self.run_cli("brief", "--market", ts.MARKET)
        self.assertEqual(code, 0)

    def test_movers_with_history(self):
        db = ts.fixture_db()
        old = copy.deepcopy(db)
        old["scans"][0]["at"] = ts.SCAN_AT - 86400
        old["scans"][0]["data"] = old["scans"][0]["data"].replace("2447:37:22x40,23x200,25x1600,999999x1;",
                                                                  "2447:1:12x1841;")
        sv = self.root / "WTF" / "Account" / "TEST#1" / "SavedVariables"
        (sv / "ForeverGold.lua.bak").write_text(ts.sv_text(old), encoding="utf-8")
        code, out, _ = self.run_cli("brief")
        self.assertEqual(code, 0)
        text = (self.home / "reports" / "20261105-1930-brief.md").read_text(encoding="utf-8")
        self.assertIn("## Movers", text)
        self.assertIn("| Peacebloom | 24c | 12c | +100% |", text)


class TestPreLaunch(ts.TempHomeCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.root)
        (self.root / "WTF" / "Account").mkdir(parents=True)

    def test_brief_without_any_data(self):
        code, out, err = self.run_cli("brief")
        self.assertEqual(code, 0, err)
        first = out.splitlines()[0]
        self.assertIn(NO_SCANS, first)
        self.assertIn("python -m fgold install", first, "the addon is not installed in the root yet")
        self.assertEqual(len([l for l in out.splitlines() if re.match(r"^\d\. ", l)]), 5)
        text = (self.home / "reports" / "20261105-1930-brief.md").read_text(encoding="utf-8")
        self.assertIn(NO_SCANS, text)
        self.assertTrue(text.rstrip().endswith("## Reads"))
        draft = json.loads((self.home / "plan" / "draft.json").read_text(encoding="utf-8"))
        self.assertEqual([draft[k] for k in ("buy", "craft", "post", "hold", "vendor")], [[]] * 5)
        apply.validate(draft, static=ts.load_static())

    def test_brief_with_addon_installed_but_no_scans(self):
        self.assertEqual(self.run_cli("install")[0], 0)
        code, out, _ = self.run_cli("brief")
        self.assertEqual(out.splitlines()[0], "Next: " + NO_SCANS)

    def test_brief_without_any_root(self):
        code, out, _ = self.run_cli("brief", roots=[])
        self.assertEqual(code, 0)
        self.assertIn("doctor --write", out.splitlines()[0])
        self.assertIn(NO_SCANS, out.splitlines()[0])

    def test_other_commands_do_not_crash(self):
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "Next: " + NO_SCANS)
        code, out, _ = self.run_cli("probes")
        self.assertEqual(code, 0)
        self.assertIn("none yet", out)
        code, out, _ = self.run_cli("price", "Peacebloom")
        self.assertEqual(code, 0)
        self.assertIn("no scans yet", out)
        code, out, _ = self.run_cli("ingest")
        self.assertEqual(code, 0)
        self.assertIn("log in once", out.splitlines()[0])

    def test_plan_apply_without_a_draft(self):
        code, out, err = self.run_cli("plan", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("no draft at", err)
        self.assertIn("Fix: run `python -m fgold brief` first", err)


class TestApplyFlow(ts.TempHomeCase):
    def test_install_brief_apply_status(self):
        code, out, _ = self.run_cli("install")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Next: fully QUIT and restart WoW"))
        self.assertIn("full", out)
        self.assertEqual(self.run_cli("brief")[0], 0)
        code, out, err = self.run_cli("plan", "--apply")
        self.assertEqual(code, 0, out + err)
        m = re.match(r"^plan (\d{8}-\d{4}-[0-9a-f]{4}): now /reload in game", out)
        self.assertIsNotNone(m, out)
        pid = m.group(1)
        lua = self.root / "Interface" / "AddOns" / "ForeverGold" / "PlanData.lua"
        plan = luasv.load(lua)["ForeverGoldPlan"]
        self.assertEqual(plan["id"], pid)
        for r in plan["post"]:
            self.assertEqual(r["floorUnit"] % 100, 0, "copperOK false in the newest session")
        applied = json.loads((self.home / "plan" / "applied.json").read_text(encoding="utf-8"))
        self.assertEqual(applied["id"], pid)
        state = json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["applied"]["id"], pid)
        self.assertEqual(state["applied"]["expect"], {"wow-forever/TEST#1": 2}, "read-back is judged per account")
        self.assertEqual(state["applied"]["roots"], ["wow-forever"])
        code, out, _ = self.run_cli("status")
        self.assertIn(f"Plan: applied {pid}", out)
        self.assertIn("differs: /reload in game to load it", out)
        self.assertTrue(out.startswith("Next: /reload in game to load plan"))

    def test_rejected_draft_changes_nothing(self):
        self.run_cli("install")
        self.run_cli("brief")
        self.assertEqual(self.run_cli("plan", "--apply")[0], 0)
        lua = self.root / "Interface" / "AddOns" / "ForeverGold" / "PlanData.lua"
        before = lua.read_text(encoding="utf-8")
        draft_path = self.home / "plan" / "draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["surprise"] = True
        draft["post"][0]["duration"] = 5
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        code, out, err = self.run_cli("plan", "--apply")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("Next: fix 2 problem(s)"))
        self.assertIn("1. unknown key 'surprise'", err)
        self.assertIn("duration must be 1 (2h), 2 (8h) or 3 (24h)", err)
        self.assertEqual(lua.read_text(encoding="utf-8"), before)

    def test_dry_run_and_custom_draft_path(self):
        self.run_cli("brief")
        code, out, _ = self.run_cli("plan")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Next: run `python -m fgold plan --apply` (draft is valid"))
        custom = self.tmp / "my-draft.json"
        shutil.copy(self.home / "plan" / "draft.json", custom)
        code, out, _ = self.run_cli("plan", "--draft", str(custom))
        self.assertEqual(code, 0)
        custom.write_bytes(b"\xef\xbb\xbf" + (self.home / "plan" / "draft.json").read_bytes())
        code, out, err = self.run_cli("plan", "--draft", str(custom))
        self.assertEqual(code, 0, "a draft saved with a BOM (PowerShell 5, older Notepad) is fine: " + err)
        custom.write_text("{not json", encoding="utf-8")
        code, _, err = self.run_cli("plan", "--draft", str(custom))
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)

    def test_odd_draft_shapes_are_rejected_not_crashing(self):
        self.run_cli("brief")
        draft_path = self.home / "plan" / "draft.json"
        for bad in ({"schema": 1, "basedOnScan": [1, 2], "budget": {"spendCap": 0, "principal": 0}}, [1, 2], "x"):
            draft_path.write_text(json.dumps(bad), encoding="utf-8")
            code, out, err = self.run_cli("plan", "--apply")
            self.assertEqual(code, 1, bad)
            self.assertTrue(out.startswith("Next: fix"), out)
        state = json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("applied", state)

    def test_status_after_a_login_that_missed_the_plan(self):
        self.run_cli("install")
        self.run_cli("brief")
        code, out, _ = self.run_cli("plan", "--apply")
        self.assertEqual(code, 0, out)
        pid = re.match(r"^plan (\S+):", out).group(1)
        db = ts.fixture_db()
        late = dict(db["sessions"][-1], id=f"{ts.NOW + 600}-Travv", loginAt=ts.NOW + 600, priorSessions=2)
        db["sessions"].append(late)  # logged in after the apply, still on the old plan
        sv = self.root / "WTF" / "Account" / ts.FIXTURE_ACCOUNT / "SavedVariables" / "ForeverGold.lua"
        sv.write_text(ts.sv_text(db), encoding="utf-8", newline="\n")
        self.assertEqual(self.run_cli("ingest")[0], 0)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("Next: fully quit and restart WoW (Menu > Exit Game, not /reload)"), first)
        self.assertIn(f"reported plan 20261104-1745-ab12, not {pid}", first)
        self.assertIn("(MISMATCH: a session that started after the apply reported it; fully restart WoW", out)

    def test_apply_without_install_writes_only_the_repo_copy(self):
        self.run_cli("brief")
        code, out, _ = self.run_cli("plan", "--apply")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("Next: run `python -m fgold install`"))
        self.assertTrue((self.addon_src / "PlanData.lua").is_file())
        self.assertIn("skipped", out)
        # No WoW root got it, so it is not the applied plan: a later login must not be judged
        # against an id no client can load (that used to prescribe a full restart).
        state = json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("applied", state)
        self.assertFalse((self.home / "plan" / "applied.json").exists())
        db = ts.fixture_db()
        db["sessions"].append(dict(db["sessions"][-1], id=f"{ts.NOW + 600}-Travv", loginAt=ts.NOW + 600))
        sv = self.root / "WTF" / "Account" / ts.FIXTURE_ACCOUNT / "SavedVariables" / "ForeverGold.lua"
        sv.write_text(ts.sv_text(db), encoding="utf-8", newline="\n")
        self.run_cli("ingest")
        code, out, _ = self.run_cli("status")
        self.assertNotIn("restart", out.splitlines()[0])
        self.assertNotIn("MISMATCH", out)

    def test_status_names_the_root_a_plan_did_not_reach(self):
        vm = self.tmp / "vm" / "_forever_"
        alt = {"char": "Vmalt-Forever", "realm": "Forever", "region": 1, "faction": "Alliance", "planId": "none"}
        vm_db = {"schema": 1, "sessions": [dict(alt, id="1793900000-Vmalt", loginAt=1793900000, priorSessions=0)],
                 "scans": [], "scanLog": [], "invoices": [], "inventory": {}, "probes": {"deposit": {}}}
        ts.make_root(vm, {"VM#1": ts.sv_text(vm_db)})
        roots = [self.root, vm]
        self.run_cli("install", roots=[self.root])          # the VM root never got the addon
        self.assertEqual(self.run_cli("brief", roots=roots)[0], 0)
        code, out, _ = self.run_cli("plan", "--apply", roots=roots)
        self.assertEqual(code, 0, out)
        pid = re.match(r"^plan (\S+):", out).group(1)
        state = json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["applied"]["roots"], ["wow-forever"])
        self.assertIn("vm-forever", state["applied"]["skipped"])
        vm_db["sessions"].append(dict(alt, id=f"{ts.NOW + 600}-Vmalt", loginAt=ts.NOW + 600, priorSessions=1))
        (vm / "WTF" / "Account" / "VM#1" / "SavedVariables" / "ForeverGold.lua").write_text(
            ts.sv_text(vm_db), encoding="utf-8", newline="\n")
        self.run_cli("ingest", roots=roots)
        code, out, _ = self.run_cli("status", roots=roots)
        first = out.splitlines()[0]
        self.assertTrue(first.startswith(f"Next: plan {pid} did not reach WoW root vm-forever (addon not installed"),
                        first)
        self.assertNotIn("fully quit", first)
        self.assertIn("NOT IN ROOT vm-forever", out)

    def test_consistent_edits_cannot_defeat_the_caps(self):
        # The review's demo: principal 0, a huge spendCap, Briarthorn raised to 7000 at 6s 75c and its
        # ref mkt raised 10x so maxUnit stays under 1.5 x ref. It used to apply with exit 0.
        self.run_cli("install")
        self.run_cli("brief")
        lua = self.root / "Interface" / "AddOns" / "ForeverGold" / "PlanData.lua"
        before = lua.read_text(encoding="utf-8")
        d = json.loads((self.home / "plan" / "draft.json").read_text(encoding="utf-8"))
        d["budget"] = {"principal": 0, "spendCap": 5234567}
        row = next(r for r in d["buy"] if r["id"] == 2450)
        row.update(qty=7000, maxUnit=675)
        d["ref"]["2450"]["mkt"] = 450
        bad = self.tmp / "bad-draft.json"
        bad.write_text(json.dumps(d), encoding="utf-8")
        code, out, err = self.run_cli("plan", "--apply", "--draft", str(bad))
        self.assertEqual(code, 1, out + err)
        self.assertTrue(out.startswith("Next: fix"), out)
        for frag in ("budget.principal 0 is not the nest egg", "above the gold over the nest egg",
                     "does not match the stored scan", "qty raised from 3 to 7000", "maxUnit raised from 50 to 675"):
            self.assertIn(frag, err)
        self.assertEqual(lua.read_text(encoding="utf-8"), before, "nothing reached the game")

    def test_apply_needs_the_generated_draft(self):
        self.run_cli("brief")
        gen = self.home / "plan" / "draft.orig.json"
        self.assertEqual(json.loads(gen.read_text(encoding="utf-8")),
                         json.loads((self.home / "plan" / "draft.json").read_text(encoding="utf-8")))
        gen.unlink()
        code, out, err = self.run_cli("plan", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("draft.orig.json", err)
        self.assertIn("Fix: run `python -m fgold brief` first", err)


class TestOtherCommands(ts.TempHomeCase):
    def test_ingest_command(self):
        code, out, _ = self.run_cli("ingest")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "Next: run `python -m fgold brief`")
        self.assertIn("1 new of 1 file,", out)
        code, out, _ = self.run_cli("ingest")
        self.assertIn("0 new of 1 file", out)

    def test_status(self):
        self.run_cli("ingest")
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("us|Forever|Alliance: newest scan Thu Nov 5, 7:10 PM CT (20 min old), 1 scan(s) over 1 day(s)", out)
        self.assertIn("Sessions: 2 seen", out)
        self.assertIn("Newest invoice:", out)
        self.assertIn("Alliance 5% (config, 1 of 3 sales)", out)
        self.assertIn("Plan: none applied yet; game reported 20261104-1745-ab12", out)

    def test_probes(self):
        self.run_cli("ingest")
        code, out, _ = self.run_cli("probes")
        self.assertEqual(code, 0)
        self.assertIn("iface 16001", out)
        self.assertIn("copperOK false", out)
        self.assertIn("read-back: priorSessions was 0 at 1 of 2 logins", out)
        self.assertIn("results: closed 1, dropped 1, ok 1, median wait 45 s", out)
        self.assertIn("Mageroyal x20  bid 7s 60c  cut 38c  5.00%", out)
        self.assertIn("house Alliance (scans)", out)
        self.assertIn("Deposit probes (1):", out)
        self.assertIn("24h 2s 40c", out)

    def test_price(self):
        self.run_cli("ingest")
        for q in ("Peacebloom", "peacebloom", "2447"):
            code, out, _ = self.run_cli("price", q)
            self.assertEqual(code, 0)
            self.assertIn("Peacebloom (2447): vendor 10c", out)
            self.assertIn("min 22c, mkt 24c, q 1,841 (37 auctions), p50 25c; scan 20 min old", out)
        code, out, _ = self.run_cli("price", "Black", "Lotus")
        self.assertIn("Black Market cap 50g (unverified)", out)
        code, out, _ = self.run_cli("price", "zzzz not an item")
        self.assertEqual(code, 1)
        self.assertIn("Fix:", out)

    def test_doctor(self):
        code, out, _ = self.run_cli("doctor")
        self.assertIn("account TEST#1: ForeverGold.lua", out)
        self.assertTrue(out.startswith("Next: "))
        code, out, _ = self.run_cli("doctor", "--write")
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn(str(self.root), [r["path"] for r in saved["roots"]])

    def test_doctor_write_never_overwrites_a_broken_config(self):
        broken = '{"nest_egg_copper": 9000000, "professions": {"Alchemy": 50},}\n'
        self.config_path.write_text(broken, encoding="utf-8")
        code, out, err = self.run_cli("doctor", "--write")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        self.assertTrue(out.startswith("Next: fix config.local.json (error above)"), out)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), broken, "the hand edits survive")
        code, out, _ = self.run_cli("doctor")
        self.assertTrue(out.startswith("Next: "), "plain doctor still diagnoses")

    def test_config_errors_are_explained(self):
        self.config_path.write_text("{ bad json", encoding="utf-8")
        code, _, err = self.run_cli("brief")
        self.assertEqual(code, 2)
        self.assertIn("config:", err)
        self.assertIn("Fix:", err)
        self.config_path.write_text(json.dumps({"region": "mars"}), encoding="utf-8")
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("region must be one of", err)

    def test_global_options_work_after_the_command(self):
        other = self.tmp / "elsewhere"
        from fgold import cli
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["ingest", "--data-dir", str(other), "--wow-root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertTrue((other / "state.json").is_file())
        self.assertFalse(self.data_dir.exists())

    def test_config_roots_are_used_without_wow_root(self):
        cfg = dict(ts.TEST_CONFIG, roots=[{"name": "pc", "path": str(self.root)}])
        self.config_path.write_text(json.dumps(cfg), encoding="utf-8")
        code, out, _ = self.run_cli("ingest", roots=[])
        self.assertEqual(code, 0)
        self.assertTrue((self.data_dir / "raw" / "pc" / "TEST#1").is_dir())

    def test_no_command_prints_help(self):
        code, out, _ = self.run_cli(roots=[])
        self.assertEqual(code, 2)
        self.assertIn("doctor", out)


class TestScanMemory(ts.TempHomeCase):
    """brief, status and probes used to load (and cache) every stored scan of every market:
    about 31 MB of Python objects per 250k-row scan, so a month of scans took gigabytes."""

    DAYS_BACK = (30, 20, 10, 6, 1, 0)

    def fill_store(self, with_index=True):
        from fgold import util
        from fgold.store import Store
        store = Store(self.data_dir)
        index = {}
        for d in self.DAYS_BACK:
            at = ts.SCAN_AT - d * 86400
            util.write_json_gz(store.scan_path(ts.MARKET, at), {
                "at": at, "market": ts.MARKET, "by": "fg", "char": "Travv-Forever", "house": "Alliance", "rows": 1,
                "items": {"2447": {"n": 1, "ladder": [[22, 40]]}}, "own_items": {}})
            index[str(at)] = {"char": "Travv-Forever", "house": "Alliance", "by": "fg", "rows": 1}
        if with_index:
            util.write_json(store.scan_index_path, {ts.MARKET: index})
        return store

    def loads(self, fn):
        from unittest import mock
        from fgold import util
        real, seen = util.read_json_gz, []

        def counting(path):
            seen.append(Path(path).name)
            return real(path)

        with mock.patch("fgold.util.read_json_gz", side_effect=counting):
            result = fn()
        return result, seen

    def test_brief_loads_one_week_and_counts_every_scan_day(self):
        from fgold import cli
        self.fill_store()
        env = cli.Env(cli.build_parser().parse_args(["brief", "--data-dir", str(self.data_dir)]))
        inp, seen = self.loads(lambda: cli.plan_inputs(env, ts.MARKET))
        week = [ts.SCAN_AT - d * 86400 for d in (6, 1, 0)]
        self.assertEqual([s["at"] for s in inp.scans], week, "history and movers need 7 days")
        self.assertEqual(sorted(seen), sorted(f"{at}.json.gz" for at in week), "no older scan is read")
        self.assertEqual(inp.scan_days, 6, "the invest rule still counts every scan day")
        self.assertEqual(inp.char_houses, {"Travv-Forever": {"Alliance"}})

    def test_status_and_probes_do_not_load_every_scan(self):
        self.fill_store()
        (code, out, _), seen = self.loads(lambda: self.run_cli("status", roots=[]))
        self.assertEqual(code, 0)
        self.assertIn("6 scan(s) over 6 day(s)", out)
        self.assertEqual(seen, [], "status reads file names and the index only")
        (code, out, _), seen = self.loads(lambda: self.run_cli("probes", roots=[]))
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 5, "probes shows the newest 5 scans per market")

    def test_char_houses_without_an_index_still_works(self):
        from fgold import probes
        store = self.fill_store(with_index=False)
        houses, seen = self.loads(lambda: probes.char_houses(store))
        self.assertEqual(houses, {"Travv-Forever": {"Alliance"}})
        self.assertEqual(store._scan_cache, {}, "read one at a time, never all kept in memory")


class TestPlanState(unittest.TestCase):
    def test_states(self):
        from fgold import probes
        applied = {"id": "P", "at": 1000}
        self.assertEqual(probes.plan_state({}, {"planId": "x", "loginAt": 5}), "none")
        self.assertEqual(probes.plan_state(applied, None), "pending")
        self.assertEqual(probes.plan_state(applied, {"planId": "P", "loginAt": 5}), "match")
        self.assertEqual(probes.plan_state(applied, {"planId": "old", "loginAt": 900}), "pending",
                         "a session from before the apply: /reload loads the plan")
        self.assertEqual(probes.plan_state(applied, {"planId": "old", "loginAt": 1000 + probes.PLAN_SKEW}), "pending",
                         "PC vs server clock skew")
        self.assertEqual(probes.plan_state(applied, {"planId": "old", "loginAt": 1001 + probes.PLAN_SKEW}), "mismatch")


class TestEntryPoint(ts.TempHomeCase):
    def test_python_m_fgold(self):
        proc = subprocess.run([sys.executable, "-m", "fgold", "--wow-root", str(self.root), "brief"],
                              cwd=str(ts.REPO), env=dict(os.environ), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("Next: "))
        self.assertTrue((self.home / "reports" / "20261105-1930-brief.md").is_file())
        proc = subprocess.run([sys.executable, "-m", "fgold", "--version"], cwd=str(ts.REPO), capture_output=True,
                              text=True, timeout=60)
        self.assertIn("fgold 0.1.0", proc.stdout)


if __name__ == "__main__":
    unittest.main()
