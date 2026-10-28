"""ingest: store layout, idempotency, .bak, several roots/accounts, dedupe, mtime wait."""

import copy
import gzip
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import test_support as ts
from fgold import ingest, util
from fgold.paths import Root, safe_part
from fgold.store import Store, market_key, region_code


def root_for(path: Path, name: str = "wow") -> Root:
    return Root(name=name, path=path, raw=str(path))


class IngestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-ingest-")
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"

    def tearDown(self):
        self._tmp.cleanup()

    def fixture_root(self, name="wow") -> Root:
        dest = self.tmp / name / "_forever_"
        shutil.copytree(ts.FIXTURE_ROOT / "WTF", dest / "WTF")
        return root_for(dest, name)

    def run_ingest(self, roots, **kw):
        kw.setdefault("stable_secs", 0)
        kw.setdefault("now", ts.NOW)
        return ingest.ingest(roots, self.data, **kw)


class TestFixtureIngest(IngestCase):
    def test_store_layout_matches_spec(self):
        root = self.fixture_root()
        rep = self.run_ingest([root])
        self.assertEqual(rep.errors, [])
        self.assertEqual((rep.files_seen, rep.files_new), (1, 1))
        self.assertEqual(rep.scans_new, 1)
        self.assertEqual(rep.invoices_new, 3)
        self.assertEqual(rep.sessions_new, 2)
        self.assertEqual(rep.inventory_updated, 2)
        self.assertEqual(rep.probes_updated, 1)
        self.assertEqual(rep.scanlog_new, 3)

        raw = list((self.data / "raw" / "wow" / "TEST#1").glob("*.lua.gz"))
        self.assertEqual(len(raw), 1)
        self.assertRegex(raw[0].name, r"^\d+-[0-9a-f]{8}\.lua\.gz$")
        sv_bytes = (root.path / "WTF" / "Account" / "TEST#1" / "SavedVariables" / "ForeverGold.lua").read_bytes()
        self.assertEqual(gzip.decompress(raw[0].read_bytes()), sv_bytes)
        mtime = int(os.stat(root.path / "WTF" / "Account" / "TEST#1" / "SavedVariables" / "ForeverGold.lua").st_mtime)
        self.assertTrue(raw[0].name.startswith(f"{mtime}-"))

        scan_path = self.data / "scans" / "us" / "Forever" / "Alliance" / f"{ts.SCAN_AT}.json.gz"
        self.assertTrue(scan_path.is_file())
        scan = util.read_json_gz(scan_path)
        for k in ("at", "market", "by", "char", "rows", "priced", "bidOnly", "own", "items", "own_items"):
            self.assertIn(k, scan)
        self.assertEqual(scan["market"], ts.MARKET)
        self.assertEqual(scan["by"], "fg")
        self.assertEqual((scan["rows"], scan["priced"], scan["bidOnly"], scan["own"]), (162, 157, 3, 2))
        self.assertEqual(len(scan["items"]), 28)
        self.assertEqual(scan["items"]["2447"], {"n": 37, "ladder": [[22, 40], [23, 200], [25, 1600], [999999, 1]]})
        self.assertEqual(scan["own_items"], {"785": {"n": 2, "ladder": [[38, 20]]}})

        store = Store(self.data)
        self.assertEqual(store.markets(), [ts.MARKET])
        self.assertEqual(store.scan_index(), {ts.MARKET: {str(ts.SCAN_AT): {"char": "Travv-Forever", "house": "Alliance",
                                                                           "by": "fg", "rows": 162}}},
                         "who scanned where, so reports never load every scan")
        ledger = store.ledger()
        self.assertEqual(sorted(r["type"] for r in ledger), ["buyer", "seller", "seller_temp_invoice"])
        self.assertTrue(all("key" in r for r in ledger))
        sessions = store.sessions()
        self.assertEqual([s["id"] for s in sessions], ["1793835000-Bankv", "1793926800-Travv"])
        self.assertIs(sessions[-1]["copperOK"], False)
        self.assertEqual(sessions[0]["priorSessions"], 0)
        inv = store.inventory()
        self.assertEqual(sorted(inv), ["Bankv-Forever", "Travv-Forever"])
        self.assertEqual(inv["Travv-Forever"]["bags"]["2447"], 37)
        self.assertEqual(inv["Travv-Forever"]["bank"], {"2449": 20, "2453": 8})
        self.assertEqual(inv["Travv-Forever"]["money"], 1234567)
        probes = store.probes()
        self.assertEqual(probes["deposit"]["785"]["3"], 240)
        self.assertEqual(probes["deposit"]["785"]["qty"], 20)
        self.assertEqual([e["result"] for e in probes["scanLog"]], ["closed", "dropped", "ok"])
        state = store.state()
        self.assertEqual(len(state["seen"]), 1)
        self.assertEqual(state["accounts"]["wow/TEST#1"]["sessions"], 2)
        self.assertEqual(state["last_ingest"], ts.NOW)

    def test_idempotent(self):
        root = self.fixture_root()
        self.run_ingest([root])
        before = ts.snapshot(self.data)
        rep = self.run_ingest([root])
        self.assertEqual(rep.files_new, 0)
        self.assertEqual((rep.scans_new, rep.invoices_new, rep.sessions_new, rep.inventory_updated), (0, 0, 0, 0))
        self.assertEqual(ts.snapshot(self.data), before)

    def test_idempotent_even_when_state_is_lost(self):
        root = self.fixture_root()
        self.run_ingest([root])
        before = ts.snapshot(self.data)
        (self.data / "state.json").unlink()
        rep = self.run_ingest([root])
        self.assertEqual(rep.files_new, 1)
        self.assertEqual((rep.scans_new, rep.invoices_new, rep.sessions_new), (0, 0, 0))
        after = ts.snapshot(self.data)
        for k in before:
            if k != "state.json":
                self.assertEqual(after[k], before[k], k)

    def test_never_writes_to_the_wow_folder(self):
        root = self.fixture_root()
        before = ts.snapshot(root.path)
        self.run_ingest([root])
        self.assertEqual(ts.snapshot(root.path), before)


class TestBakAndAccounts(IngestCase):
    def test_bak_is_read_and_merged(self):
        root = self.fixture_root()
        db = ts.fixture_db()
        old = copy.deepcopy(db)
        old["sessions"] = [db["sessions"][0]]
        old["scans"] = [dict(db["scans"][0], at=ts.SCAN_AT - 86400, reqAt=0)]
        old["invoices"] = [db["invoices"][0]]
        old["inventory"]["Bankv-Forever"]["bank"] = {3820: 7}
        old["inventory"]["Bankv-Forever"]["bankAt"] = 1793836000
        old["inventory"]["Travv-Forever"]["at"] = 1793900000
        old["inventory"]["Travv-Forever"]["bags"] = {2447: 1}
        sv = root.path / "WTF" / "Account" / "TEST#1" / "SavedVariables"
        (sv / "ForeverGold.lua.bak").write_text(ts.sv_text(old), encoding="utf-8")
        rep = self.run_ingest([root])
        self.assertEqual(rep.errors, [])
        self.assertEqual((rep.files_seen, rep.files_new), (2, 2))
        self.assertEqual(rep.scans_new, 2)
        self.assertEqual(rep.invoices_new, 3)
        store = Store(self.data)
        self.assertEqual(len(store.sessions()), 2)
        self.assertEqual(len(store.ledger()), 3)
        self.assertEqual([at for at, _ in store.scan_paths(ts.MARKET)], [ts.SCAN_AT - 86400, ts.SCAN_AT])
        inv = store.inventory()
        self.assertEqual(inv["Travv-Forever"]["bags"]["2447"], 37, "newest bags win")
        self.assertEqual(inv["Bankv-Forever"]["bank"], {"3820": 7}, "bank kept from the version that saw it")
        self.assertEqual(len(list((self.data / "raw" / "wow" / "TEST#1").glob("*.lua.gz"))), 2)

    def test_identical_bak_is_one_version(self):
        root = self.fixture_root()
        sv = root.path / "WTF" / "Account" / "TEST#1" / "SavedVariables"
        shutil.copy(sv / "ForeverGold.lua", sv / "ForeverGold.lua.bak")
        rep = self.run_ingest([root])
        self.assertEqual((rep.files_seen, rep.files_new), (2, 1))

    def test_two_roots_and_two_accounts(self):
        db = ts.fixture_db()
        horde = copy.deepcopy(db)
        for s in horde["sessions"]:
            s["faction"] = "Horde"
            s["char"] = s["char"].replace("v-", "h-")
            s["id"] = s["id"] + "h"
        horde["scans"][0].update(house="Horde", faction="Horde", char="Travh-Forever", at=ts.SCAN_AT + 60)
        horde["invoices"] = []
        horde["inventory"] = {"Travh-Forever": db["inventory"]["Travv-Forever"]}
        neutral = copy.deepcopy(db)
        neutral["sessions"] = []
        neutral["scans"][0].update(house="Neutral", at=ts.SCAN_AT + 120)
        neutral["scans"][0].pop("house")  # nil house means Neutral
        neutral["invoices"] = []
        neutral["inventory"] = {}
        a = ts.make_root(self.tmp / "a" / "_forever_", {"ACC#1": ts.FIXTURE_SV.read_text(encoding="utf-8"),
                                                       "ACC#2": ts.sv_text(horde)})
        b = ts.make_root(self.tmp / "b" / "_forever_", {"VM#1": ts.sv_text(neutral)})
        rep = self.run_ingest([root_for(a, "pc"), root_for(b, "vm")])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.accounts, ["pc/ACC#1", "pc/ACC#2", "vm/VM#1"])
        store = Store(self.data)
        self.assertEqual(store.markets(), ["us|Forever|Alliance", "us|Forever|Horde", "us|Forever|Neutral"])
        self.assertEqual(len(store.sessions()), 4)
        self.assertEqual(sorted(store.inventory()), ["Bankv-Forever", "Travh-Forever", "Travv-Forever"])
        self.assertTrue((self.data / "raw" / "pc" / "ACC#2").is_dir())
        self.assertTrue((self.data / "raw" / "vm" / "VM#1").is_dir())
        accts = store.state()["accounts"]
        self.assertEqual(sorted(accts), ["pc/ACC#1", "pc/ACC#2", "vm/VM#1"])

    def test_missing_root_is_a_warning(self):
        rep = self.run_ingest([root_for(self.tmp / "nope", "gone")])
        self.assertEqual(rep.files_seen, 0)
        self.assertTrue(any("folder not found" in w and "Fix:" in w for w in rep.warnings))


class TestDedupeAndMerge(IngestCase):
    def _db(self, invoices=(), sessions=(), inventory=None):
        return {"schema": 1, "sessions": list(sessions), "scans": [], "scanLog": [], "invoices": list(invoices),
                "inventory": inventory or {}, "probes": {"deposit": {}}}

    def test_same_mail_seen_in_two_sessions_is_one_invoice(self):
        base = {"char": "A-Forever", "type": "seller", "item": "Peacebloom", "bid": 960, "buyout": 960,
                "deposit": 100, "cut": 48, "delay": 0, "etaH": 0, "etaM": 0, "count": 40, "commodity": True}
        first = dict(base, seenAt=1793926920, daysLeft=29.9)
        again = dict(base, seenAt=1793926920 + 7200, daysLeft=29.9 - 2 / 24)
        other = dict(base, seenAt=1793926920 + 7200, daysLeft=27.9)  # another identical sale, 2 days earlier
        self.assertEqual(ingest.invoice_key(first), ingest.invoice_key(again))
        self.assertAlmostEqual(ingest.invoice_expiry(first), ingest.invoice_expiry(again), places=3)
        self.assertGreater(abs(ingest.invoice_expiry(first) - ingest.invoice_expiry(other)), ingest.EXPIRY_TOLERANCE)
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(self._db([first]))})
        self.run_ingest([root_for(r1)])
        (r1 / "WTF" / "Account" / "A#1" / "SavedVariables" / "ForeverGold.lua").write_text(
            ts.sv_text(self._db([again, other])), encoding="utf-8")
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.invoices_new, 1)
        self.assertEqual(len(Store(self.data).ledger()), 2)

    def test_deposit_probe_shapes(self):
        db = self._db()
        db["probes"] = {"deposit": {785: {1: 100, 2: 100, 3: 240, "qty": 20, "at": 9},
                                    2447: [100, 120, 300],
                                    2449: {"1": 5, "qty": 2, "at": 3},
                                    2450: {"qty": 2}}}
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(db)})
        self.run_ingest([root_for(r1)])
        dep = Store(self.data).probes()["deposit"]
        self.assertEqual({k: (v.get("1"), v.get("3"), v["qty"]) for k, v in dep.items()},
                         {"785": (100, 240, 20), "2447": (100, 300, 1), "2449": (5, None, 2)})

    def test_same_mail_recorded_twice_in_one_session_is_one_invoice(self):
        # The addon's in-session key floor(daysLeft*24) changes as the session ages, so a long
        # session can record the same mail again; the expiry-hour key collapses them.
        base = {"char": "A-Forever", "type": "seller", "item": "Mageroyal", "bid": 680, "buyout": 680,
                "deposit": 30, "cut": 34, "delay": 0, "etaH": 0, "etaM": 0, "count": 20, "commodity": True}
        first = dict(base, seenAt=1793842260, daysLeft=29.86)
        later = dict(base, seenAt=1793842260 + 7200, daysLeft=29.86 - 7200 / 86400)
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(self._db([first, later]))})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.invoices_new, 1)

    def test_identical_sales_in_one_hour_are_separate_invoices(self):
        # Two buyers took 20 Peacebloom each at 22c, at 19:10 and 19:35; both mails were read at 20:00.
        # The old expiry-hour key made them one record, undercounting sales and the measured cut.
        base = {"char": "A-Forever", "type": "seller", "item": "Peacebloom", "bid": 440, "buyout": 440,
                "deposit": 15, "cut": 22, "delay": 0, "etaH": 0, "etaM": 0, "count": 20, "commodity": True}
        read = 1793930400
        one = dict(base, seenAt=read, daysLeft=30 - 50 / 1440)
        two = dict(base, seenAt=read, daysLeft=30 - 25 / 1440)
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(self._db([one, two]))})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.invoices_new, 2)
        self.assertEqual(sorted(r["copy"] for r in Store(self.data).ledger()), [1, 2])
        # the next session reads the same two mails again (the addon records them again): nothing new
        later = 1793930400 + 3 * 3600
        again = [dict(base, seenAt=later, daysLeft=one["daysLeft"] - 3 / 24),
                 dict(base, seenAt=later, daysLeft=two["daysLeft"] - 3 / 24)]
        sv = r1 / "WTF" / "Account" / "A#1" / "SavedVariables" / "ForeverGold.lua"
        sv.write_text(ts.sv_text(self._db([one, two] + again)), encoding="utf-8")
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.invoices_new, 0)
        # three identical mails in one walk are three sales, even with the same expiry minute
        trio = [dict(base, item="Mageroyal", seenAt=read, daysLeft=29.5) for _ in range(3)]
        sv.write_text(ts.sv_text(self._db([one, two] + trio)), encoding="utf-8")
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.invoices_new, 3)
        self.assertEqual(len(Store(self.data).ledger()), 5)
        # and the same mails on another character are another character's sales
        other_char = [dict(r, char="B-Forever") for r in (one, two)]
        sv.write_text(ts.sv_text(self._db(other_char)), encoding="utf-8")
        self.assertEqual(self.run_ingest([root_for(r1)]).invoices_new, 2)

    def test_unknown_invoice_types_are_ignored(self):
        bad = {"char": "A", "type": "spam", "item": "x", "bid": 1, "seenAt": 1, "daysLeft": 1}
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(self._db([bad]))})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.invoices_new, 0)

    def test_newer_session_values_win_regardless_of_order(self):
        s_new = {"id": "100-A", "char": "A-Forever", "loginAt": 100, "copperOK": False, "priorSessions": 3}
        s_old = {"id": "100-A", "char": "A-Forever", "loginAt": 100, "copperOK": True, "priorSessions": 3,
                 "planId": "p1"}
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(self._db(sessions=[s_new]))})
        sv = r1 / "WTF" / "Account" / "A#1" / "SavedVariables"
        os.utime(sv / "ForeverGold.lua", (2000000000, 2000000000))
        self.run_ingest([root_for(r1)])
        (sv / "ForeverGold.lua.bak").write_text(ts.sv_text(self._db(sessions=[s_old])), encoding="utf-8")
        os.utime(sv / "ForeverGold.lua.bak", (1000000000, 1000000000))
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.sessions_new, 0)
        s = Store(self.data).sessions()[0]
        self.assertIs(s["copperOK"], False, "an older file must not override a newer one")
        self.assertEqual(s["planId"], "p1", "but it may fill fields the newer one lacked")

    def test_session_id_is_synthesized(self):
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(self._db(sessions=[{"char": "Bob-Forever", "loginAt": 5}]))})
        self.run_ingest([root_for(r1)])
        self.assertEqual(Store(self.data).sessions()[0]["id"], "5-Bob")

    def test_scan_without_data_is_skipped_with_a_warning(self):
        db = self._db()
        db["scans"] = [{"at": 5, "house": "Alliance", "realm": "Forever", "region": 1}]
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(db)})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.scans_new, 0)
        self.assertTrue(rep.warnings)

    def test_malformed_scan_entries_are_dropped(self):
        db = self._db()
        db["scans"] = [{"at": 7, "house": "Horde", "realm": "Forever", "region": 3, "data": "1:1:5x2;junk;2:1:;3:1:4x4;"}]
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.sv_text(db)})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.scans_new, 1)
        scan = Store(self.data).newest_scan("eu|Forever|Horde")
        self.assertEqual(sorted(scan["items"]), ["1", "3"])
        self.assertTrue(any("malformed" in w for w in rep.warnings))


class TestBadFiles(IngestCase):
    def test_parse_error_is_archived_and_reported_once(self):
        r1 = ts.make_root(self.tmp / "r1", {"A#1": "ForeverGoldDB = {\n  [\"schema\"] = 1,\n"})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(rep.files_new, 1)
        self.assertEqual(len(rep.errors), 1)
        self.assertIn("unfinished table", rep.errors[0])
        self.assertEqual(len(list((self.data / "raw").rglob("*.lua.gz"))), 1)
        rep2 = self.run_ingest([root_for(r1)])
        self.assertEqual((rep2.files_new, rep2.errors), (0, []))

    def test_file_without_db(self):
        r1 = ts.make_root(self.tmp / "r1", {"A#1": "SomethingElse = 1\n"})
        rep = self.run_ingest([root_for(r1)])
        self.assertTrue(any("no ForeverGoldDB" in e for e in rep.errors))

    def test_code_in_the_file_is_not_executed(self):
        r1 = ts.make_root(self.tmp / "r1", {"A#1": "ForeverGoldDB = { x = os.execute('touch pwned') }\n"})
        rep = self.run_ingest([root_for(r1)])
        self.assertEqual(len(rep.errors), 1)
        self.assertFalse(Path("pwned").exists())


class TestWaitStable(IngestCase):
    def setUp(self):
        super().setUp()
        self.f = self.tmp / "sv.lua"
        self.f.write_text("x", encoding="utf-8")

    def test_zero_means_no_wait(self):
        calls = []
        st, ok = ingest.wait_stable(self.f, 0, _sleep=calls.append)
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_old_file_is_stable_immediately(self):
        old = time.time() - 100
        os.utime(self.f, (old, old))
        calls = []
        st, ok = ingest.wait_stable(self.f, 2, _sleep=calls.append)
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_fresh_file_waits_until_unchanged(self):
        future = time.time() + 1000  # clock skew on a network share
        os.utime(self.f, (future, future))
        t0 = time.monotonic()
        st, ok = ingest.wait_stable(self.f, 0.3)
        self.assertTrue(ok)
        self.assertGreaterEqual(time.monotonic() - t0, 0.29)

    def test_file_still_changing_gives_up(self):
        future = time.time() + 1000
        counter = [0]

        def touch(_secs):
            counter[0] += 1
            self.f.write_text("x" * (counter[0] + 1), encoding="utf-8")
            t = future + counter[0]
            os.utime(self.f, (t, t))
            time.sleep(0.05)

        os.utime(self.f, (future, future))
        st, ok = ingest.wait_stable(self.f, 0.2, max_wait=0.4, _sleep=touch)
        self.assertFalse(ok)
        self.assertGreater(counter[0], 1)

    def test_unstable_file_is_skipped_by_ingest(self):
        r1 = ts.make_root(self.tmp / "r1", {"A#1": ts.FIXTURE_SV.read_text(encoding="utf-8")})
        sv = r1 / "WTF" / "Account" / "A#1" / "SavedVariables" / "ForeverGold.lua"
        orig = ingest.wait_stable
        try:
            ingest.wait_stable = lambda path, secs, max_wait=None: (os.stat(path), False)
            rep = self.run_ingest([root_for(r1)], stable_secs=2)
        finally:
            ingest.wait_stable = orig
        self.assertEqual((rep.files_unstable, rep.files_new), (1, 0))
        self.assertTrue(any("still being written" in w for w in rep.warnings))
        self.assertTrue(sv.exists())


class TestScanString(unittest.TestCase):
    def test_spec_example(self):
        items, bad = ingest.parse_scan_string("2447:37:22x40,23x200,25x1600;765:3:150x20;")
        self.assertEqual(bad, 0)
        self.assertEqual(items, {2447: {"n": 37, "ladder": [[22, 40], [23, 200], [25, 1600]]},
                                 765: {"n": 3, "ladder": [[150, 20]]}})

    def test_merges_equal_units_and_sorts(self):
        items, _ = ingest.parse_scan_string("5:3:9x1,2x5,9x4;")
        self.assertEqual(items[5]["ladder"], [[2, 5], [9, 5]])

    def test_duplicate_item_entries_merge(self):
        items, _ = ingest.parse_scan_string("5:1:2x1;5:2:2x3,4x1;")
        self.assertEqual(items[5], {"n": 3, "ladder": [[2, 4], [4, 1]]})

    def test_bad_entries(self):
        items, bad = ingest.parse_scan_string("abc;1:2:;1:2:3y4;0:1:5x1;\u0663:1:1x1;7:1:0x0;8:1:5x2")
        self.assertEqual(sorted(items), [8])
        self.assertEqual(bad, 6)

    def test_empty_and_non_string(self):
        self.assertEqual(ingest.parse_scan_string(""), ({}, 0))
        self.assertEqual(ingest.parse_scan_string(None), ({}, 0))
        self.assertEqual(ingest.parse_scan_string(";;"), ({}, 0))

    def test_format_round_trip(self):
        s = "765:3:150x20;2447:37:22x40,23x200,25x1600;"
        items, _ = ingest.parse_scan_string(s)
        self.assertEqual(ingest.format_scan_string(items), s)


class TestKeys(unittest.TestCase):
    def test_region_codes(self):
        self.assertEqual([region_code(i) for i in (1, 2, 3, 4, 5)], ["us", "kr", "eu", "tw", "cn"])
        self.assertEqual(region_code(99), "99")
        self.assertEqual(region_code(None, "eu"), "eu")

    def test_market_key(self):
        self.assertEqual(market_key("us", "Forever", "Alliance"), "us|Forever|Alliance")
        self.assertEqual(market_key("us", "Bad|Realm", "Horde"), "us|Bad/Realm|Horde")

    def test_safe_part_is_windows_safe(self):
        self.assertEqual(safe_part("Kel'Thuzad"), "Kel'Thuzad")
        self.assertEqual(safe_part("TEST#1"), "TEST#1")
        self.assertEqual(safe_part("a:b|c"), "a%3Ab%7Cc")
        self.assertEqual(safe_part("trailing."), "trailing%2E")
        self.assertEqual(safe_part("CON"), "%43ON")
        self.assertEqual(safe_part(""), "_")
        self.assertFalse(re.search(r'[<>:"/\\|?*]', safe_part('<>:"/\\|?*')))


if __name__ == "__main__":
    unittest.main()
