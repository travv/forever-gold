"""static data: the generated static/*.json, lookups, and tools/fetch_static.py offline."""

import contextlib
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

import test_support as ts
from fgold.static import PROFESSION_LINES, Static, profession_line

STATIC = ts.REPO / "static"
SPEC_VENDOR_REAGENTS = ["Empty Vial", "Leaded Vial", "Crystal Vial", "Imbued Vial", "Coarse Thread", "Fine Thread",
                        "Silken Thread", "Heavy Silken Thread", "Rune Thread", "Mild Spices", "Hot Spices",
                        "Soothing Spices", "Refreshing Spring Water"]
SPEC_CAPS = {"Black Lotus": 500000, "Arcane Crystal": 500000, "Pristine Leather": 50000, "Frilled Lichen": 50000,
             "Pyrite": 50000}


def load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_static", ts.REPO / "tools" / "fetch_static.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless((STATIC / "items.json").is_file(), "static/ not generated yet (python tools/fetch_static.py)")
class TestGeneratedStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = Static.load(STATIC)

    def test_files_and_meta(self):
        self.assertEqual(self.st.missing, [])
        meta = json.loads((STATIC / "meta.json").read_text(encoding="utf-8"))
        if meta.get("fallback"):
            self.skipTest("static/ holds the fallback set")
        self.assertEqual(meta["product"], "wowf")
        self.assertRegex(meta["build"], r"^\d+\.\d+\.\d+\.\d+$")

    def test_items(self):
        if self.st.fallback:
            self.skipTest("fallback set")
        self.assertGreater(len(self.st.items), 10000)
        pb = self.st.item(2447)
        self.assertEqual((pb["name"], pb["sell"], pb["stack"], pb["q"]), ("Peacebloom", 10, 20, 1))
        self.assertEqual(self.st.item(3371)["buy"], 4, "Empty Vial: BuyPrice 20 / VendorStackCount 5")
        raw = json.loads((STATIC / "items.json").read_text(encoding="utf-8"))
        for v in list(raw.values())[:200]:
            self.assertEqual(set(v), {"name", "sell", "buy", "stack", "q"}, "only the spec fields")

    def test_recipes_have_no_orphans(self):
        if self.st.fallback:
            self.skipTest("fallback set")
        raw = json.loads((STATIC / "recipes.json").read_text(encoding="utf-8"))
        self.assertGreater(len(raw), 500)
        lines = set(PROFESSION_LINES.values())
        for sid, r in raw.items():
            self.assertEqual(set(r), {"skill_line", "skill", "reagents", "out", "out_count"})
            self.assertTrue(r["reagents"], sid)
            self.assertIn(r["skill_line"], lines)
            self.assertIn(r["out"], self.st.items)
            self.assertGreaterEqual(r["out_count"], 1)
        self.assertNotIn("2330", raw, "old Alchemy Minor Healing Potion has no reagents in Forever")
        mhp = raw["1244431"]
        self.assertEqual((mhp["skill_line"], mhp["out"]), (129, 118), "healing potions moved to First Aid")
        self.assertEqual(mhp["reagents"], [[3371, 1], [2678, 1], [2447, 1]])

    def test_recipe_skill_gates_something(self):
        # MinSkillLineRank is 1 on 1874 of 1890 recipes in build 70634, so it let a First Aid 1
        # character plan Major Healing Potions; the yellow rank is what fetch_static stores now.
        if self.st.fallback:
            self.skipTest("fallback set")
        raw = json.loads((STATIC / "recipes.json").read_text(encoding="utf-8"))
        self.assertLess(sum(1 for r in raw.values() if r["skill"] <= 1), 50)
        self.assertEqual([raw[s]["skill"] for s in ("3275", "1244431", "1244436", "17635")], [30, 55, 290, 315],
                         "Linen Bandage, Minor and Major Healing Potion, Flask of the Titans")
        self.assertEqual(len(self.st.recipes_for(129, 1)), 0)

    def test_houses(self):
        self.assertEqual(self.st.house_rates("Alliance"), {"cut": 0.05, "deposit": 0.05})
        self.assertEqual(self.st.house_rates("Horde"), {"cut": 0.05, "deposit": 0.05})
        self.assertEqual(self.st.house_rates("Neutral"), {"cut": 0.15, "deposit": 0.25})

    def test_vendor_caps_and_reagents(self):
        raw = json.loads((STATIC / "vendor_caps.json").read_text(encoding="utf-8"))
        by_name = {v["name"]: v for v in raw.values()}
        if not self.st.fallback:
            self.assertEqual({n: v["cap"] for n, v in by_name.items()}, SPEC_CAPS)
        self.assertTrue(all(v["verified"] is False for v in raw.values()))
        names = {v["name"] for v in self.st.vendor_reagents.values()}
        if not self.st.fallback:
            for n in SPEC_VENDOR_REAGENTS:
                self.assertIn(n, names)
        self.assertEqual(self.st.vendor_buy(3371), 4)
        self.assertIsNone(self.st.vendor_buy(2447), "Peacebloom is not vendor-sold")
        self.assertEqual(self.st.cap(13468), 500000)


class TestLookups(unittest.TestCase):
    def setUp(self):
        self.st = ts.load_static()

    def test_find(self):
        self.assertEqual(self.st.find("Peacebloom"), [2447])
        self.assertEqual(self.st.find("  peaceBLOOM "), [2447])
        self.assertEqual(self.st.find("Light Lea"), [2318])
        self.assertIn(118, self.st.find("healing pot"))
        self.assertEqual(self.st.find(""), [])
        self.assertEqual(self.st.find("no such thing"), [])
        self.assertEqual(self.st.id_for_name("Empty Vial"), 3371)
        self.assertIsNone(self.st.id_for_name("Empty"))

    def test_names_and_values(self):
        self.assertEqual(self.st.name(2447), "Peacebloom")
        self.assertEqual(self.st.name(424242), "item 424242")
        self.assertEqual(self.st.sell(424242), 0)
        self.assertEqual(self.st.quality(4865), 0)
        self.assertTrue(self.st.is_vendor_reagent(2678))
        self.assertFalse(self.st.is_vendor_reagent(2318))

    def test_recipes_for(self):
        ids = [sid for sid, _ in self.st.recipes_for(129, 100)]
        self.assertEqual(ids, [3275, 3276, 1244431, 1244432])
        # the yellow ranks: Linen Bandage 30, Heavy Linen 50, Minor Healing 55, Lesser Healing 85
        self.assertEqual([sid for sid, _ in self.st.recipes_for(129, 60)], [3275, 3276, 1244431])
        self.assertEqual(self.st.recipes_for(129, 1), [], "skill 1 used to unlock every recipe")
        self.assertEqual(self.st.recipes_for(129, 0), [])
        self.assertEqual(profession_line("First Aid"), 129)
        self.assertEqual(profession_line("alchemy"), 171)
        self.assertIsNone(profession_line("Herbalism"))

    def test_byte_order_marks_are_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            for p in ts.FIXTURE_STATIC.glob("*.json"):
                (Path(d) / p.name).write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
            st = Static.load(d)
        self.assertEqual(st.missing, [])
        self.assertEqual(st.name(2447), "Peacebloom")

    def test_missing_directory_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            st = Static.load(d)
        self.assertIn("items.json", st.missing)
        self.assertEqual(st.items, {})
        self.assertEqual(st.house_rates("Neutral")["cut"], 0.15)


def write_csv(path: Path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "0") for h in header])


class TestFetchStaticOffline(unittest.TestCase):
    def setUp(self):
        self.fs = load_fetcher()
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-fetch-")
        self.tmp = Path(self._tmp.name)
        self.csv = self.tmp / "csv"
        self.csv.mkdir()
        self.out = self.tmp / "out"
        item_h = ["ID", "Description_lang", "Display_lang", "SellPrice", "BuyPrice", "VendorStackCount", "Stackable",
                  "OverallQualityID", "Bonding"]
        write_csv(self.csv / "ItemSparse.csv", item_h, [
            {"ID": "2447", "Display_lang": "Peacebloom", "SellPrice": "10", "BuyPrice": "40", "VendorStackCount": "1",
             "Stackable": "20", "OverallQualityID": "1"},
            {"ID": "3371", "Display_lang": "Empty Vial", "SellPrice": "1", "BuyPrice": "20", "VendorStackCount": "5",
             "Stackable": "20", "OverallQualityID": "1"},
            {"ID": "2678", "Display_lang": "Mild Spices", "SellPrice": "2", "BuyPrice": "7", "VendorStackCount": "5",
             "Stackable": "20", "OverallQualityID": "1"},
            {"ID": "118", "Display_lang": "Minor Healing Potion", "SellPrice": "5", "BuyPrice": "20",
             "VendorStackCount": "1", "Stackable": "5", "OverallQualityID": "1"},
            {"ID": "13468", "Display_lang": "Black Lotus", "SellPrice": "1000", "BuyPrice": "4000",
             "VendorStackCount": "1", "Stackable": "20", "OverallQualityID": "2"},
            {"ID": "5", "Display_lang": 'Odd, "quoted"\x01 name', "SellPrice": "x", "Stackable": "1"},
            {"ID": "6", "Display_lang": "", "SellPrice": "1"},
        ])
        write_csv(self.csv / "SkillLineAbility.csv", ["ID", "SkillLine", "Spell", "MinSkillLineRank", "AcquireMethod"], [
            {"ID": "10", "SkillLine": "129", "Spell": "100", "MinSkillLineRank": "1"},
            {"ID": "11", "SkillLine": "171", "Spell": "101", "MinSkillLineRank": "1"},
            {"ID": "12", "SkillLine": "129", "Spell": "102", "MinSkillLineRank": "1"},
            {"ID": "13", "SkillLine": "999", "Spell": "103", "MinSkillLineRank": "1"},
            {"ID": "14", "SkillLine": "129", "Spell": "104", "MinSkillLineRank": "1"},
            {"ID": "50", "SkillLine": "197", "Spell": "105", "MinSkillLineRank": "40"},
            {"ID": "40", "SkillLine": "165", "Spell": "105", "MinSkillLineRank": "30"},
        ])
        reg_h = ["ID", "SpellID"] + [f"Reagent_{i}" for i in range(8)] + [f"ReagentCount_{i}" for i in range(8)]
        write_csv(self.csv / "SpellReagents.csv", reg_h, [
            {"ID": "1", "SpellID": "100", "Reagent_0": "3371", "ReagentCount_0": "1", "Reagent_1": "2678",
             "ReagentCount_1": "1", "Reagent_2": "2447", "ReagentCount_2": "1", "Reagent_3": "2447", "ReagentCount_3": "1"},
            {"ID": "2", "SpellID": "102", "Reagent_0": "2447", "ReagentCount_0": "1"},
            {"ID": "3", "SpellID": "103", "Reagent_0": "2447", "ReagentCount_0": "1"},
            {"ID": "4", "SpellID": "104", "Reagent_0": "2447", "ReagentCount_0": "1"},
            {"ID": "5", "SpellID": "105", "Reagent_0": "2447", "ReagentCount_0": "2"},
        ])
        eff_h = ["ID", "EffectIndex", "Effect", "EffectItemType", "EffectBasePointsF", "SpellID"]
        write_csv(self.csv / "SpellEffect.csv", eff_h, [
            {"ID": "1", "EffectIndex": "0", "Effect": "24", "EffectItemType": "118", "EffectBasePointsF": "1", "SpellID": "100"},
            {"ID": "2", "EffectIndex": "1", "Effect": "6", "EffectItemType": "0", "EffectBasePointsF": "5", "SpellID": "100"},
            {"ID": "3", "EffectIndex": "0", "Effect": "24", "EffectItemType": "118", "EffectBasePointsF": "1", "SpellID": "101"},
            {"ID": "4", "EffectIndex": "0", "Effect": "24", "EffectItemType": "77777", "EffectBasePointsF": "1", "SpellID": "104"},
            {"ID": "5", "EffectIndex": "0", "Effect": "24", "EffectItemType": "118", "EffectBasePointsF": "0", "SpellID": "105"},
        ])
        write_csv(self.csv / "AuctionHouse.csv", ["ID", "Name_lang", "FactionID", "DepositRate", "ConsignmentRate"], [
            {"ID": "1", "Name_lang": "Stormwind Auction House", "FactionID": "1", "DepositRate": "5", "ConsignmentRate": "5"},
            {"ID": "6", "Name_lang": "Horde Auction House", "FactionID": "2", "DepositRate": "5", "ConsignmentRate": "5"},
            {"ID": "7", "Name_lang": "Blackwater Auction House", "FactionID": "369", "DepositRate": "25",
             "ConsignmentRate": "15"},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def paths(self):
        return {t: self.csv / f"{t}.csv" for t in self.fs.TABLES}

    def read(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_generate(self):
        meta = self.fs.generate(self.paths(), self.out, "1.2.3.4", "unit test")
        items = self.read("items.json")
        self.assertEqual(sorted(items, key=int), ["5", "118", "2447", "2678", "3371", "13468"])
        self.assertEqual(items["3371"], {"name": "Empty Vial", "sell": 1, "buy": 4, "stack": 20, "q": 1})
        self.assertEqual(items["2678"]["buy"], 1.4)
        self.assertEqual(items["5"]["name"], 'Odd, "quoted" name')
        self.assertEqual(items["5"]["sell"], 0)
        recipes = self.read("recipes.json")
        self.assertEqual(sorted(recipes), ["100", "105"])
        self.assertEqual(recipes["100"], {"skill_line": 129, "skill": 1, "reagents": [[3371, 1], [2678, 1], [2447, 2]],
                                          "out": 118, "out_count": 1})
        self.assertEqual((recipes["105"]["skill_line"], recipes["105"]["skill"], recipes["105"]["out_count"]),
                         (165, 30, 1), "lowest SkillLineAbility row wins; base points 0 makes 1")
        self.assertEqual(meta["counts"]["recipes_orphan_dropped"], 2)
        self.assertEqual(meta["counts"]["recipes_unresolved_item_dropped"], 1)
        self.assertEqual(meta["columns"]["create_count"], "EffectBasePointsF")
        self.assertEqual(self.read("houses.json"), {"Alliance": {"cut": 0.05, "deposit": 0.05},
                                                    "Horde": {"cut": 0.05, "deposit": 0.05},
                                                    "Neutral": {"cut": 0.15, "deposit": 0.25}})
        self.assertEqual(self.read("vendor_caps.json"), {"13468": {"name": "Black Lotus", "cap": 500000, "verified": False}})
        self.assertIn("Arcane Crystal", meta["unresolved"])
        self.assertEqual(sorted(self.read("vendor_reagents.json")), ["2678", "3371"])
        self.assertFalse(meta["fallback"])
        st = Static.load(self.out)
        self.assertEqual(st.vendor_buy(3371), 4)

    def test_skill_is_the_yellow_rank_when_the_column_exists(self):
        sla_h = ["ID", "SkillLine", "Spell", "MinSkillLineRank", "TrivialSkillLineRankLow", "TrivialSkillLineRankHigh"]
        write_csv(self.csv / "SkillLineAbility.csv", sla_h, [
            {"ID": "10", "SkillLine": "129", "Spell": "100", "MinSkillLineRank": "1", "TrivialSkillLineRankLow": "290",
             "TrivialSkillLineRankHigh": "330"},
            {"ID": "40", "SkillLine": "165", "Spell": "105", "MinSkillLineRank": "30", "TrivialSkillLineRankLow": "0"},
        ])
        meta = self.fs.generate(self.paths(), self.out, "1.2.3.4", "unit test")
        recipes = self.read("recipes.json")
        self.assertEqual((recipes["100"]["skill"], recipes["105"]["skill"]), (290, 30),
                         "max(MinSkillLineRank, TrivialSkillLineRankLow)")
        self.assertEqual(meta["columns"]["skill"], "max(MinSkillLineRank, TrivialSkillLineRankLow)")

    def test_prefers_integer_base_points_column(self):
        eff_h = ["ID", "EffectIndex", "Effect", "EffectItemType", "EffectBasePoints", "EffectBasePointsF", "SpellID"]
        write_csv(self.csv / "SpellEffect.csv", eff_h, [
            {"ID": "1", "EffectIndex": "0", "Effect": "24", "EffectItemType": "118", "EffectBasePoints": "3",
             "EffectBasePointsF": "9", "SpellID": "100"}])
        meta = self.fs.generate(self.paths(), self.out, "1.2.3.4", "unit test")
        self.assertEqual(self.read("recipes.json")["100"]["out_count"], 3)
        self.assertEqual(meta["columns"]["create_count"], "EffectBasePoints")

    def test_missing_column_is_explained(self):
        write_csv(self.csv / "ItemSparse.csv", ["ID", "Name"], [{"ID": "1", "Name": "x"}])
        with self.assertRaises(self.fs.FetchError) as cm:
            self.fs.generate(self.paths(), self.out, "1.2.3.4", "unit test")
        self.assertIn("missing columns", str(cm.exception))
        self.assertIn("columns are", str(cm.exception))

    def test_pick_latest_and_build_validation(self):
        data = {"wow": [{"version": "9.9.9.99999", "created_at": "2030-01-01"}],
                "wowf": [{"version": "1.60.1.70630", "created_at": "2026-10-27 01:44:11"},
                         {"version": "1.60.1.70634", "created_at": "2026-10-27 15:26:09"},
                         {"version": "1.60.1.70634; rm -rf /", "created_at": "2099-01-01"},
                         "junk"]}
        self.assertEqual(self.fs.pick_latest(data), "1.60.1.70634")
        with self.assertRaises(self.fs.FetchError):
            self.fs.pick_latest({"wow": []})
        with self.assertRaises(self.fs.FetchError):
            self.fs.pick_latest({"wowf": [{"version": "../../x"}]})
        self.assertEqual(self.fs.resolve_build("1.60.1.70634", self.tmp), "1.60.1.70634")
        with self.assertRaises(self.fs.FetchError):
            self.fs.resolve_build("1.60.1.70634&x=1", self.tmp)

    def test_fallback_files(self):
        meta = self.fs.write_fallback(self.out, "offline")
        self.assertTrue(meta["fallback"])
        st = Static.load(self.out)
        self.assertTrue(st.fallback)
        self.assertEqual(st.missing, [])
        self.assertEqual(st.name(2447), "Peacebloom")
        self.assertIn(1244431, st.recipes)

    def test_main_from_dir(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = self.fs.main(["--from-dir", str(self.csv), "--out", str(self.out), "--build", "1.2.3.4"])
            missing = self.fs.main(["--from-dir", str(self.tmp / "nope"), "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "meta.json").is_file())
        self.assertEqual(missing, 2)
        self.assertIn("names not found in ItemSparse", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
