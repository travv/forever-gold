"""plan --apply: validation (design.md 3.5), rendering, atomic writes to every root."""

import copy
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_support as ts
from fgold import apply, luasv, util
from fgold.apply import PlanError
from fgold.paths import Root

T = ts.SCAN_AT


def base_draft():
    return {
        "schema": 1,
        "id": "draft",
        "generatedAt": 1,
        "basedOnScan": {"at": T, "market": ts.MARKET, "rows": 162},
        "lastScanAt": T,
        "expectPriorSessions": 2,
        "settings": {"postMode": "match", "undercut": 100},
        "headline": "Tonight: buy 2, post 1",
        "notes": ["Cold start: 1 of 3 scan days."],
        "steps": ["Mailbox: Open All", "AH: click Scan"],
        "budget": {"spendCap": 250000, "principal": 5000000},
        "buy": [
            {"id": 2447, "name": "Peacebloom", "qty": 60, "maxUnit": 18, "why": "for potions", "flip": False},
            {"id": 2318, "name": "Light Leather", "qty": 40, "maxUnit": 13, "why": "below vendor", "flip": True},
        ],
        "craft": [{"recipe": 1244431, "name": "Minor Healing Potion", "out": 118, "count": 20, "profitEach": 35,
                   "why": "cheap mats"}],
        "post": [{"id": 785, "name": "Mageroyal", "qty": 40, "floorUnit": 30, "startUnit": 34, "duration": 3,
                  "why": "raid night"}],
        "hold": [{"id": 3820, "name": "Stranglekelp", "why": "below floor"}],
        "vendor": [{"id": 4865, "name": "Ruined Pelt", "why": "junk"}],
        "ref": {"2447": {"min": 16, "mkt": 17, "q": 2400}, "785": {"min": 34, "mkt": 34, "q": 200}},
    }


class ValidateCase(unittest.TestCase):
    def setUp(self):
        self.static = ts.load_static()

    def check(self, draft, **kw):
        kw.setdefault("static", self.static)
        return apply.validate(draft, **kw)

    def assertRejected(self, mutate, fragment, **kw):
        d = base_draft()
        mutate(d)
        with self.assertRaises(PlanError) as cm:
            self.check(d, **kw)
        joined = " | ".join(cm.exception.problems)
        self.assertIn(fragment, joined)
        return cm.exception


class TestValidation(ValidateCase):
    def test_base_draft_is_valid(self):
        plan, warns = self.check(base_draft())
        self.assertEqual(warns, [])
        self.assertEqual(set(plan["ref"]), {2447, 785}, "ref keys become ints for Lua")
        self.assertEqual(plan["post"][0]["floorUnit"], 30)

    def test_unknown_keys_are_rejected(self):
        self.assertRejected(lambda d: d.update(extra=1), "unknown key 'extra'")
        self.assertRejected(lambda d: d["buy"][0].update(price=5), "unknown key 'price'")
        self.assertRejected(lambda d: d["budget"].update(x=1), "budget: unknown key 'x'")
        self.assertRejected(lambda d: d["basedOnScan"].update(sha="x"), "basedOnScan: unknown key 'sha'")
        self.assertRejected(lambda d: d["ref"]["2447"].update(avg=1), "unknown key 'avg'")
        self.assertRejected(lambda d: d["hold"][0].update(qty=3), "unknown key 'qty'")

    def test_money_must_be_non_negative_ints(self):
        self.assertRejected(lambda d: d["buy"][0].update(maxUnit=18.0), "whole number of copper")
        self.assertRejected(lambda d: d["buy"][0].update(maxUnit="18"), "whole number of copper")
        self.assertRejected(lambda d: d["post"][0].update(floorUnit=-1), ">= 0")
        self.assertRejected(lambda d: d["craft"][0].update(profitEach=1.5), "whole number of copper")
        self.assertRejected(lambda d: d["budget"].update(spendCap=True), "whole number of copper")
        self.assertRejected(lambda d: d["settings"].update(undercut=-5), ">= 0")
        self.assertRejected(lambda d: d["ref"]["2447"].update(mkt=17.5), "whole number of copper")

    def test_counts_durations_and_ids(self):
        self.assertRejected(lambda d: d["buy"][0].update(qty=0), "qty must be a whole number >= 1")
        self.assertRejected(lambda d: d["buy"][0].update(qty=True), "qty must be a whole number >= 1")
        self.assertRejected(lambda d: d["craft"][0].update(count=2.0), "count must be a whole number >= 1")
        self.assertRejected(lambda d: d["post"][0].update(duration=4), "duration must be 1 (2h), 2 (8h) or 3 (24h)")
        self.assertRejected(lambda d: d["post"][0].update(duration="3"), "duration must be")
        self.assertRejected(lambda d: d["buy"][0].update(id=0), "id must be a positive integer id")
        self.assertRejected(lambda d: d["vendor"][0].update(id="4865"), "id must be a positive integer id")
        self.assertRejected(lambda d: d["craft"][0].update(recipe=-1), "recipe must be a positive integer id")
        self.assertRejected(lambda d: d["ref"].update({"abc": {"min": 1, "mkt": 1, "q": 1}}), "ref key 'abc'")
        self.assertRejected(lambda d: d["ref"].update({"0": {"min": 1, "mkt": 1, "q": 1}}), "ref key '0'")
        self.assertRejected(lambda d: d["ref"]["785"].pop("q"), "ref[785].q is required")

    def test_required_fields_and_types(self):
        self.assertRejected(lambda d: d.pop("budget"), "budget must be an object")
        self.assertRejected(lambda d: d["budget"].pop("principal"), "budget.principal is required")
        self.assertRejected(lambda d: d["buy"][0].pop("maxUnit"), "maxUnit is required")
        self.assertRejected(lambda d: d["buy"][0].update(name=""), "name must be a non-empty string")
        self.assertRejected(lambda d: d["buy"][0].update(flip="yes"), "flip must be true or false")
        self.assertRejected(lambda d: d.update(schema=2), "schema must be 1")
        self.assertRejected(lambda d: d.update(schema=True), "schema must be 1")
        self.assertRejected(lambda d: d.pop("schema"), "schema must be 1")
        self.assertRejected(lambda d: d["settings"].update(postMode="cheap"), "postMode")
        self.assertRejected(lambda d: d.update(headline=5), "headline must be")
        self.assertRejected(lambda d: d.update(buy={}), "buy must be a list")
        with self.assertRaises(PlanError):
            self.check([base_draft()])

    def test_notes_and_steps_at_most_five(self):
        self.assertRejected(lambda d: d.update(notes=["n"] * 6), "notes has 6 entries (max 5)")
        self.assertRejected(lambda d: d.update(steps=["s"] * 6), "steps has 6 entries (max 5)")
        self.assertRejected(lambda d: d.update(notes=["ok", 5]), "notes[2] must be")
        d = base_draft()
        d["notes"] = ["n"] * 5
        self.check(d)

    def test_row_caps(self):
        self.assertRejected(lambda d: None, "buy has 2 rows (max_rows is 1)", max_rows=1)

    def test_max_unit_vs_ref_and_vendor(self):
        d = base_draft()
        d["buy"][0]["maxUnit"] = 25          # 1.5 x 17 = 25.5
        self.check(d)
        self.assertRejected(lambda d: d["buy"][0].update(maxUnit=26), "above 1.5 x ref mkt 17")
        self.assertRejected(lambda d: d["ref"].pop("2447"), "no ref[2447].mkt")
        d = base_draft()
        d["buy"][1]["maxUnit"] = 15          # Light Leather vendor sell 15
        self.check(d)
        self.assertRejected(lambda d: d["buy"][1].update(maxUnit=16), "above the vendor sell price 15")
        self.assertRejected(lambda d: d["buy"][1].update(id=999999), "no known vendor sell price")
        d = base_draft()
        d["buy"][1]["maxUnit"] = 14
        d["ref"]["2318"] = {"min": 10, "mkt": 1, "q": 5}
        self.check(d)   # flips are checked against vendor sell, not ref

    def test_spend_cap(self):
        d = base_draft()
        d["budget"]["spendCap"] = 1600       # 60 x 18 + 40 x 13
        self.check(d)
        self.assertRejected(lambda d: d["budget"].update(spendCap=1599), "budget.spendCap")
        d = base_draft()
        d["buy"] = []
        d["budget"]["spendCap"] = 0
        self.check(d)

    def test_start_below_floor(self):
        self.assertRejected(lambda d: d["post"][0].update(startUnit=29), "startUnit 29 is below floorUnit 30")

    def test_em_dash_is_rejected(self):
        self.assertRejected(lambda d: d["post"][0].update(why="raid night \u2014 sells fast"), "em-dash")
        self.assertRejected(lambda d: d.update(headline="a\u2014b"), "em-dash")

    def test_all_problems_are_listed(self):
        d = base_draft()
        d["extra"] = 1
        d["post"][0]["duration"] = 9
        d["buy"][0]["maxUnit"] = 99
        with self.assertRaises(PlanError) as cm:
            self.check(d)
        self.assertGreaterEqual(len(cm.exception.problems), 3)

    def test_defaults_fill_optional_fields(self):
        d = {"schema": 1, "budget": {"spendCap": 0, "principal": 0}}
        plan, _ = self.check(d)
        self.assertEqual(plan["buy"], [])
        self.assertEqual(plan["settings"], {"postMode": "match", "undercut": 0})
        self.assertEqual(plan["basedOnScan"], {"at": 0, "market": "", "rows": 0})
        d = base_draft()
        d["buy"][0].pop("why")
        d["buy"][0].pop("flip")
        plan, _ = self.check(d)
        self.assertEqual((plan["buy"][0]["why"], plan["buy"][0]["flip"]), ("", False))

    def test_pipe_in_game_text_is_a_warning(self):
        d = base_draft()
        d["steps"][0] = "Mailbox | Open All"
        plan, warns = self.check(d)
        self.assertTrue(any("steps[1]" in w and "escape code" in w for w in warns))
        plan, warns = self.check(base_draft())
        self.assertEqual(warns, [], "the market key may contain '|'")

    def test_spend_cap_above_gold_is_an_error(self):
        # was only a warning: a draft could spend into the nest egg with exit 0
        self.assertRejected(lambda d: None, "above the gold over the nest egg", gold=5000100)
        self.check(base_draft(), gold=5250000)   # exactly gold minus principal


class TestBackstop(ValidateCase):
    """The validator checks the draft against what it cannot edit: one self-consistent edit of
    ref, the budget and a row used to pass every cap (finding: 7000 Briarthorn at 15x the scan)."""

    SCAN_REF = {2447: {"min": 16, "mkt": 17, "q": 2400}, 785: {"min": 34, "mkt": 34, "q": 200}}
    FULL = dict(gold=5250000, nest=5000000, market_ref=SCAN_REF)

    def test_a_consistent_edit_of_ref_budget_and_rows_is_rejected(self):
        gen = base_draft()
        d = base_draft()
        d["budget"] = {"principal": 0, "spendCap": 5234567}
        d["buy"][0].update(qty=7000, maxUnit=675)
        d["ref"]["2447"]["mkt"] = 450
        self.check(d)   # consistent with itself: the spec's own checks pass
        with self.assertRaises(PlanError) as cm:
            self.check(d, generated=gen, **self.FULL)
        joined = " | ".join(cm.exception.problems)
        for frag in ("budget.principal 0 is not the nest egg", "above the gold over the nest egg",
                     "ref[2447] {'min': 16, 'mkt': 450, 'q': 2400} does not match the stored scan",
                     "qty raised from 60 to 7000", "maxUnit raised from 18 to 675",
                     "budget.spendCap: raised", "ref: changed"):
            self.assertIn(frag, joined)

    def test_the_skills_allowed_edits_pass(self):
        gen = base_draft()
        d = base_draft()
        d.update(headline="Sell into raid night", steps=["Mailbox: Open All (1 min)"], notes=[])
        d["buy"][0].update(qty=30, maxUnit=17, why="cheap tonight")
        del d["buy"][1]
        d["budget"]["spendCap"] = 100000
        d["craft"][0]["count"] = 5
        moved = d["post"].pop(0)
        d["hold"].append({"id": moved["id"], "name": moved["name"], "why": "keep it for potions"})
        d["vendor"] = []
        plan, _ = self.check(d, generated=gen, **self.FULL)
        self.assertEqual([r["id"] for r in plan["hold"]], [3820, 785])
        d2 = base_draft()
        d2["post"][0]["duration"] = 2
        self.check(d2, generated=gen, **self.FULL)

    def test_raises_additions_and_never_touch_fields_are_rejected(self):
        gen = base_draft()
        cases = [
            (lambda d: d["buy"][0].update(qty=61), "qty raised from 60 to 61"),
            (lambda d: d["buy"][0].update(maxUnit=19), "maxUnit raised from 18 to 19"),
            (lambda d: d["buy"][1].update(flip=False), "(flip changed?)"),
            (lambda d: d["buy"].append(dict(d["buy"][0])), "appears twice"),
            (lambda d: d["craft"][0].update(count=21), "count raised from 20 to 21"),
            (lambda d: d["craft"][0].update(profitEach=99), "profitEach changed from 35 to 99"),
            (lambda d: d["post"][0].update(floorUnit=20, startUnit=25), "floorUnit changed from 30 to 20"),
            (lambda d: d["post"][0].update(qty=41), "qty raised from 40 to 41"),
            (lambda d: d["hold"].append({"id": 2447, "name": "Peacebloom"}), "rows are only deleted, never added"),
            (lambda d: d["vendor"].append({"id": 2447, "name": "Peacebloom"}), "rows are only deleted, never added"),
            (lambda d: d["vendor"][0].update(name="Ruined Pelts"), "name changed"),
            (lambda d: d.update(lastScanAt=0), "lastScanAt: changed"),
            (lambda d: d.update(expectPriorSessions=0), "expectPriorSessions: changed"),
            (lambda d: d["basedOnScan"].update(at=T - 60), "basedOnScan: changed"),
            (lambda d: d["settings"].update(undercut=1), "settings: changed"),
            (lambda d: d["budget"].update(spendCap=250001), "budget.spendCap: raised from 250000 to 250001"),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment):
                self.assertRejected(mutate, fragment, generated=gen)

    def test_ref_must_match_the_stored_scan(self):
        self.check(base_draft(), market_ref=self.SCAN_REF)
        self.assertRejected(lambda d: d["ref"]["785"].update(q=9999), "does not match the stored scan",
                            market_ref=self.SCAN_REF)
        self.assertRejected(lambda d: None, "is not a scan in the data store", market_ref={})
        d = base_draft()
        d["ref"] = {}
        d["buy"] = [r for r in d["buy"] if r["flip"]]
        self.check(d, market_ref={})   # no prices, nothing to compare

    def test_principal_is_the_config_nest_egg(self):
        self.check(base_draft(), nest=5000000)
        self.assertRejected(lambda d: d["budget"].update(principal=0), "is not the nest egg", nest=5000000)
        # the gold check uses the config nest egg, not the draft's principal
        self.assertRejected(lambda d: d["budget"].update(principal=0), "above the gold over the nest egg",
                            nest=5000000, gold=5249999)

    def test_generated_draft_must_be_an_object(self):
        self.assertRejected(lambda d: None, "draft.orig.json is not a JSON object", generated=[1])


class TestSilverRounding(ValidateCase):
    def test_rounds_up_only_when_copper_is_off(self):
        plan, _ = self.check(base_draft(), copper_ok=False)
        self.assertEqual((plan["post"][0]["floorUnit"], plan["post"][0]["startUnit"]), (100, 100))
        d = base_draft()
        d["post"][0].update(floorUnit=250, startUnit=301)
        plan, _ = self.check(d, copper_ok=False)
        self.assertEqual((plan["post"][0]["floorUnit"], plan["post"][0]["startUnit"]), (300, 400))
        for ok in (True, None):
            plan, _ = self.check(base_draft(), copper_ok=ok)
            self.assertEqual((plan["post"][0]["floorUnit"], plan["post"][0]["startUnit"]), (30, 34))

    def test_buy_prices_are_not_rounded(self):
        plan, _ = self.check(base_draft(), copper_ok=False)
        self.assertEqual(plan["buy"][0]["maxUnit"], 18)


class TestRenderAndIds(ValidateCase):
    def test_render_reparses_to_the_same_data(self):
        plan, _ = self.check(base_draft())
        text = apply.render(plan)
        self.assertTrue(text.startswith("-- Generated by fgold. Do not edit; change plan/draft.json and run: "
                                        "python -m fgold plan --apply\nForeverGoldPlan = {\n"))
        apply.check_roundtrip(plan, text)
        back = luasv.loads(text)["ForeverGoldPlan"]
        self.assertEqual(back["ref"][2447], {"min": 16, "mkt": 17, "q": 2400})
        self.assertIn("[2447] = {", text)
        self.assertNotIn("\u2014", text)

    def test_roundtrip_check_catches_differences(self):
        plan, _ = self.check(base_draft())
        bad = apply.render(plan).replace("qty = 40", "qty = 41", 1)
        with self.assertRaises(PlanError):
            apply.check_roundtrip(plan, bad)
        with self.assertRaises(PlanError):
            apply.check_roundtrip(plan, "ForeverGoldPlan = {")

    def test_ids(self):
        plan, _ = self.check(base_draft())
        a = apply.new_id(plan, ts.NOW)
        self.assertRegex(a, r"^\d{8}-\d{4}-[0-9a-f]{4}$")
        self.assertTrue(a.startswith("20261105-1930-"), "CT clock")
        self.assertEqual(a, apply.new_id(dict(plan, generatedAt=99, id="other"), ts.NOW), "id and time are not content")
        changed = copy.deepcopy(plan)
        changed["buy"][0]["qty"] = 61
        self.assertNotEqual(a[-4:], apply.new_id(changed, ts.NOW)[-4:])
        self.assertNotEqual(a, apply.new_id(plan, ts.NOW + 60))


class TestApplyWrites(ValidateCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-apply-")
        self.tmp = Path(self._tmp.name)
        self.repo_addon = ts.write_addon_src(self.tmp / "repo" / "addon" / "ForeverGold")

    def tearDown(self):
        self._tmp.cleanup()

    def root(self, name, installed=True):
        p = self.tmp / name / "_forever_"
        (p / "WTF").mkdir(parents=True)
        if installed:
            (p / "Interface" / "AddOns" / "ForeverGold").mkdir(parents=True)
        return Root(name, p, str(p))

    def test_writes_every_root_and_the_repo_copy(self):
        roots = [self.root("pc"), self.root("vm"), self.root("bare", installed=False),
                 Root("gone", self.tmp / "nope", str(self.tmp / "nope"))]
        res = apply.apply_draft(base_draft(), roots, self.repo_addon, static=self.static, now=ts.NOW)
        labels = [w[0] for w in res.written]
        self.assertEqual(labels, ["pc", "vm", "repo"])
        self.assertEqual(res.reached, ["pc", "vm"])
        texts = {Path(p).read_text(encoding="utf-8") for _, p in res.written}
        self.assertEqual(len(texts), 1)
        text = texts.pop()
        self.assertEqual(text, res.text)
        back = luasv.loads(text)["ForeverGoldPlan"]
        self.assertEqual(back["id"], res.plan["id"])
        self.assertEqual(back["generatedAt"], ts.NOW)
        self.assertRegex(back["id"], r"^\d{8}-\d{4}-[0-9a-f]{4}$")
        skipped = dict(res.skipped)
        self.assertIn("python -m fgold install", skipped["bare"])
        self.assertIn("not found", skipped["gone"])
        for _, p in res.written:
            leftovers = [x.name for x in Path(p).parent.iterdir() if x.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_linked_install_is_written_once(self):
        r = self.root("pc", installed=False)
        (r.path / "Interface" / "AddOns").mkdir(parents=True)
        os.symlink(self.repo_addon, r.path / "Interface" / "AddOns" / "ForeverGold", target_is_directory=True)
        res = apply.apply_draft(base_draft(), [r], self.repo_addon, static=self.static, now=ts.NOW)
        self.assertEqual([w[0] for w in res.written], ["pc"])
        self.assertIn("same folder as pc", dict(res.skipped)["repo"])
        self.assertTrue((self.repo_addon / "PlanData.lua").is_file())
        self.assertEqual(res.reached, ["pc"])

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_a_link_to_another_folder_is_never_written_through(self):
        # e.g. a junction the old cmd-based install pointed at C:\Users\Travis\R instead of the repo
        r = self.root("pc", installed=False)
        (r.path / "Interface" / "AddOns").mkdir(parents=True)
        other = self.tmp / "R"
        other.mkdir()
        os.symlink(other, r.addon_dir, target_is_directory=True)
        res = apply.apply_draft(base_draft(), [r], self.repo_addon, static=self.static, now=ts.NOW)
        self.assertEqual([w[0] for w in res.written], ["repo"])
        self.assertIn("is a link to somewhere other than", dict(res.skipped)["pc"])
        self.assertEqual(list(other.iterdir()), [], "nothing written into the folder the link points at")
        self.assertEqual(res.reached, [])

    def test_dry_run_writes_nothing(self):
        r = self.root("pc")
        res = apply.apply_draft(base_draft(), [r], self.repo_addon, static=self.static, now=ts.NOW, dry_run=True)
        self.assertEqual(res.written, [])
        self.assertFalse((r.addon_dir / "PlanData.lua").exists())

    def test_rejected_draft_writes_nothing(self):
        r = self.root("pc")
        d = base_draft()
        d["post"][0]["duration"] = 7
        with self.assertRaises(PlanError):
            apply.apply_draft(d, [r], self.repo_addon, static=self.static, now=ts.NOW)
        self.assertFalse((r.addon_dir / "PlanData.lua").exists())

    def test_atomic_write_retries_permission_errors(self):
        target = self.tmp / "PlanData.lua"
        target.write_text("old", encoding="utf-8")
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("locked by the game")
            return real(src, dst)

        with mock.patch("fgold.util.os.replace", side_effect=flaky):
            util.write_text_atomic(target, "new", delay=0.001)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(len(calls), 3)
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.endswith(".tmp")], [])

    def test_atomic_write_gives_up_and_cleans_up(self):
        target = self.tmp / "PlanData.lua"
        target.write_text("old", encoding="utf-8")
        with mock.patch("fgold.util.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                util.write_text_atomic(target, "new", retries=3, delay=0.001)
        self.assertEqual(target.read_text(encoding="utf-8"), "old", "the old file is untouched")
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.endswith(".tmp")], [])

    def test_plan_to_json_has_string_ref_keys(self):
        res = apply.apply_draft(base_draft(), [], None, static=self.static, now=ts.NOW, dry_run=True)
        j = apply.plan_to_json(res.plan)
        self.assertEqual(sorted(j["ref"]), ["2447", "785"])
        again, _ = self.check(j)
        self.assertEqual(again["ref"], res.plan["ref"])


if __name__ == "__main__":
    unittest.main()
