"""planner: flips, triage, crafts, no-invest rule, budget drop order, caps."""

import shutil
import tempfile
import unittest
from pathlib import Path

import test_support as ts
from fgold import apply, ingest, planner
from fgold.paths import Root
from fgold.planner import PlanInputs, build_plan, chars_on_market, expect_prior_sessions
from fgold.store import Store

DAY = 86400
NEST = 5000000
T = ts.SCAN_AT
SESS = [{"id": "1-Travv", "char": "Travv-Forever", "realm": "Forever", "region": 1, "faction": "Alliance",
         "loginAt": 1, "copperOK": True}]

LIGHT_LEATHER = {2318: [[10, 20], [11, 20], [20, 100]]}        # sells to vendor for 15c
HEAVY_LEATHER = {4234: [[100, 10], [200, 20]]}                 # sells to vendor for 1s 50c
MAGEROYAL = {785: [[34, 40], [36, 60], [40, 100]]}             # mkt 34, vendor 20c
POTION_MARKET = {118: [[45, 5], [48, 10], [50, 20]], 2447: [[22, 40], [23, 200], [25, 1600]],
                 3371: [[10, 20]], 2678: [[5, 20]]}


def scan(at, items, market=ts.MARKET):
    return {"at": at, "market": market, "by": "fg", "char": "Travv-Forever", "house": market.split("|")[2],
            "rows": sum(len(v) for v in items.values()),
            "items": {str(k): {"n": len(v), "ladder": v} for k, v in items.items()}, "own_items": {}}


def inv(money=0, bags=None, bank=None):
    return {"at": 1, "money": money, "bags": {str(k): v for k, v in (bags or {}).items()},
            "bank": {str(k): v for k, v in (bank or {}).items()}, "bankAt": 0}


def merged(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


class PlannerCase(unittest.TestCase):
    def setUp(self):
        self.static = ts.load_static()

    def plan(self, scans, inventory=None, sessions=None, cfg=None, ledger=(), probes=None, state=None):
        c = dict(ts.TEST_CONFIG)
        c.update(cfg or {})
        inp = PlanInputs(cfg=c, static=self.static, market=ts.MARKET if scans else None, scans=scans,
                         inventory=inventory or {}, sessions=SESS if sessions is None else sessions,
                         ledger=list(ledger), probes=probes or {"deposit": {}, "scanLog": []}, state=state or {},
                         now=ts.NOW, char_houses={}, last_scan_at=max((s["at"] for s in scans), default=0))
        plan, info = build_plan(inp)
        apply.validate(plan, static=self.static, max_rows=c["max_rows"],
                       copper_ok=planner.newest_copper_ok(inp.sessions))
        self.assertLessEqual(len(plan["notes"]), 5)
        self.assertLessEqual(len(plan["steps"]), 5)
        return plan, info

    def rich(self, bags=None, bank=None, spare=10 ** 6):
        return {"Travv-Forever": inv(money=NEST + spare, bags=bags, bank=bank)}

    def poor(self, bags=None, bank=None):
        return {"Travv-Forever": inv(money=100, bags=bags, bank=bank)}

    @staticmethod
    def ids(rows):
        return [r["id"] for r in rows]


class TestFlips(PlannerCase):
    def test_below_vendor_flip(self):
        plan, _ = self.plan([scan(T, LIGHT_LEATHER)], self.rich())
        self.assertEqual(len(plan["buy"]), 1)
        row = plan["buy"][0]
        self.assertEqual((row["id"], row["qty"], row["maxUnit"], row["flip"]), (2318, 40, 13, True))
        self.assertEqual(row["name"], "Light Leather")
        self.assertIn("vendor", row["why"])
        self.assertEqual(plan["ref"]["2318"], {"min": 10, "mkt": 10, "q": 140})

    def test_trigger_is_min_at_most_80_percent_of_vendor(self):
        plan, _ = self.plan([scan(T, {2318: [[12, 5], [13, 5], [14, 5]]})], self.rich())
        self.assertEqual([(r["qty"], r["maxUnit"]) for r in plan["buy"]], [(10, 13)])
        plan, _ = self.plan([scan(T, {2318: [[13, 5]]})], self.rich())
        self.assertEqual(plan["buy"], [])

    def test_buys_only_units_strictly_under_90_percent(self):
        plan, _ = self.plan([scan(T, {2447: [[8, 5], [9, 5]]})], self.rich())   # Peacebloom sells for 10c
        self.assertEqual([(r["qty"], r["maxUnit"]) for r in plan["buy"]], [(5, 9)])


class TestTriage(PlannerCase):
    def test_post_with_floor_and_match(self):
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}))
        self.assertEqual(len(plan["post"]), 1)
        row = plan["post"][0]
        # floor = max(ceil(20 / 0.95) = 22, floor(0.85 x 34) = 28)
        self.assertEqual((row["id"], row["qty"], row["floorUnit"], row["startUnit"]), (785, 18, 28, 34))
        self.assertIn(row["duration"], (1, 2, 3))

    def test_undercut_mode_never_goes_below_floor(self):
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}), cfg={"post_mode": "undercut", "undercut_copper": 1})
        self.assertEqual(plan["post"][0]["startUnit"], 33)
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}), cfg={"post_mode": "undercut", "undercut_copper": 10})
        self.assertEqual(plan["post"][0]["startUnit"], 28)

    def test_hold_when_market_is_below_floor(self):
        scans = [scan(T - 3600, {785: [[60, 200]]}), scan(T, MAGEROYAL)]
        plan, _ = self.plan(scans, self.poor({785: 18}))
        self.assertEqual(plan["post"], [])
        self.assertEqual(self.ids(plan["hold"]), [785])
        self.assertTrue(plan["hold"][0]["why"].startswith("HOLD"))

    def test_vendor_when_vendor_beats_ah_net(self):
        plan, _ = self.plan([scan(T, LIGHT_LEATHER)], self.poor({2318: 25}))
        self.assertEqual(self.ids(plan["vendor"]), [2318])
        self.assertEqual(plan["post"], [])

    def test_bank_items_count_and_are_named(self):
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 3}, {785: 15}))
        self.assertEqual(plan["post"][0]["qty"], 18)
        self.assertIn("15 in bank", plan["post"][0]["why"])

    def test_unlisted_items(self):
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({4865: 3, 6948: 1, 2449: 5}))
        self.assertEqual(self.ids(plan["vendor"]), [4865], "gray junk is vendored")
        self.assertEqual(self.ids(plan["hold"]), [2449], "unlisted trade goods wait for a price")
        self.assertNotIn(6948, self.ids(plan["post"] + plan["hold"] + plan["vendor"]), "no vendor value: ignored")

    def test_silver_rounding_when_copper_is_off(self):
        off = [dict(SESS[0], copperOK=False)]
        silver = {785: [[300, 40], [400, 60]]}   # whole-silver listings, as copperOK false implies
        plan, _ = self.plan([scan(T, silver)], self.poor({785: 18}), sessions=off)
        # floor = max(22, floor(0.85 x 300) = 255) rounds up to 3s; start matches the 3s min
        self.assertEqual((plan["post"][0]["floorUnit"], plan["post"][0]["startUnit"]), (300, 300))
        unknown = [{k: v for k, v in SESS[0].items() if k != "copperOK"}]
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}), sessions=unknown)
        self.assertEqual((plan["post"][0]["floorUnit"], plan["post"][0]["startUnit"]), (28, 34))
        older_off = [dict(SESS[0], copperOK=False, id="0-x", loginAt=0), SESS[0]]
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}), sessions=older_off)
        self.assertEqual(plan["post"][0]["floorUnit"], 28, "only the newest session counts")

    def test_rounded_floor_above_the_market_is_a_hold(self):
        # copperOK false with a 34c market: the 28c floor rounds up to 1s, so the in-game
        # guard would show HOLD (min 34c < floor 1s). The plan must say hold, not post.
        off = [dict(SESS[0], copperOK=False)]
        plan, info = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}), sessions=off)
        self.assertEqual(plan["post"], [])
        self.assertEqual(self.ids(plan["hold"]), [785])
        self.assertIn("below your floor 1s", plan["hold"][0]["why"])
        # crafts too: a 46c potion under a 1s rounded floor would only be held, so no craft
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}), sessions=off)
        self.assertEqual(plan["craft"], [])
        self.assertNotIn(118, self.ids(plan["post"]))


class TestNoInvestRule(PlannerCase):
    LINEN = {2589: [[15, 100], [18, 200]]}   # mkt 15 -> net 14.25, vendor 13 x 1.1 = 14.3: near vendor

    def test_near_vendor_is_vendored_during_cold_start(self):
        plan, info = self.plan([scan(T - DAY, self.LINEN), scan(T, self.LINEN)], self.poor({2589: 40}))
        self.assertEqual(info["days"], 2)
        self.assertFalse(info["invest_ok"])
        self.assertEqual(self.ids(plan["vendor"]), [2589])
        self.assertEqual(plan["hold"], [])
        self.assertTrue(any("no invest" in n for n in plan["notes"]))

    def test_invest_holds_after_three_scan_days(self):
        scans = [scan(T - 2 * DAY, self.LINEN), scan(T - DAY, self.LINEN), scan(T, self.LINEN)]
        plan, info = self.plan(scans, self.poor({2589: 40}))
        self.assertEqual(info["days"], 3)
        self.assertEqual(self.ids(plan["hold"]), [2589])
        self.assertTrue(plan["hold"][0]["why"].startswith("invest:"))
        self.assertFalse(any("no invest" in n for n in plan["notes"]))

    def test_no_invest_reasons_ever_appear_in_cold_start(self):
        plan, _ = self.plan([scan(T, merged(self.LINEN, MAGEROYAL, LIGHT_LEATHER))],
                            self.rich({2589: 40, 785: 18, 2318: 5}))
        for key in ("buy", "post", "hold", "vendor", "craft"):
            for r in plan[key]:
                self.assertNotIn("invest", r.get("why", ""))


class TestCrafts(PlannerCase):
    def test_craft_uses_held_reagents_and_vendor_mats(self):
        plan, info = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}))
        self.assertEqual(len(plan["craft"]), 1)
        c = plan["craft"][0]
        self.assertEqual((c["recipe"], c["out"], c["name"]), (1244431, 118, "Minor Healing Potion"))
        self.assertEqual(c["count"], 8, "at most a quarter of the 35 listed")
        self.assertEqual(c["profitEach"], 13, "floor(46 x 0.95 - (4 + 2 + 24))")
        self.assertEqual(plan["buy"], [], "Peacebloom is held, vials and spices come from a vendor")
        shop = {v["id"]: v["qty"] for v in info["vendor_shop"]}
        self.assertEqual(shop, {2678: 8, 3371: 8})
        posts = {r["id"]: r for r in plan["post"]}
        self.assertEqual(posts[2447]["qty"], 29, "8 Peacebloom are reserved for the craft")
        self.assertEqual(posts[118]["qty"], 8)
        self.assertEqual(posts[118]["floorUnit"], 39, "max(ceil(30 / 0.95), floor(0.85 x 46))")
        self.assertEqual(plan["ref"]["118"]["mkt"], 46)

    def test_missing_reagents_become_buy_rows(self):
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich())
        self.assertEqual(len(plan["buy"]), 1)
        row = plan["buy"][0]
        # maxUnit = min(ceil(1.1 x 24), floor(1.5 x 24)) = min(27, 36)
        self.assertEqual((row["id"], row["qty"], row["maxUnit"], row["flip"]), (2447, 8, 27, False))

    def test_margin_must_be_at_least_25_percent(self):
        low = merged(POTION_MARKET, {118: [[36, 35]]})    # 34.2 - 30 = 4.2 -> 14%
        plan, _ = self.plan([scan(T, low)], self.rich({2447: 37}))
        self.assertEqual(plan["craft"], [])
        ok = merged(POTION_MARKET, {118: [[40, 35]]})     # 38 - 30 = 8 -> 26.7%
        plan, _ = self.plan([scan(T, ok)], self.rich({2447: 37}))
        self.assertEqual(len(plan["craft"]), 1)

    def test_recipes_above_the_profession_rank_are_not_planned(self):
        # Minor Healing Potion turns yellow at First Aid 55 (build 70634); every recipe used to say skill 1
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}), cfg={"professions": {"First Aid": 54}})
        self.assertEqual(plan["craft"], [])
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}), cfg={"professions": {"First Aid": 55}})
        self.assertEqual([c["recipe"] for c in plan["craft"]], [1244431])

    def test_reagent_max_unit_uses_integer_math(self):
        market = merged(POTION_MARKET, {118: [[100, 40]], 2447: [[50, 100]]})
        plan, _ = self.plan([scan(T, market)], self.rich())
        row = next(r for r in plan["buy"] if r["id"] == 2447)
        self.assertEqual(row["maxUnit"], 55, "min(ceil(1.1 x 50), floor(1.5 x 50)); float math gave 56")

    def test_profession_skill_zero_means_no_crafts(self):
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}), cfg={"professions": {"First Aid": 0}})
        self.assertEqual(plan["craft"], [])
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}), cfg={"professions": {"Herbalism": 300}})
        self.assertEqual(plan["craft"], [], "gathering professions have no recipes")


class TestBudget(PlannerCase):
    FLIPS = merged(LIGHT_LEATHER, HEAVY_LEATHER)   # LL: +180 for 520; HL: +500 for 1350

    def test_spend_cap_is_gold_above_the_nest_egg(self):
        inventory = {"Travv-Forever": inv(money=1234567), "Bankv-Forever": inv(money=4000000)}
        sessions = SESS + [dict(SESS[0], id="2-Bankv", char="Bankv-Forever")]
        plan, info = self.plan([scan(T, MAGEROYAL)], inventory, sessions=sessions)
        self.assertEqual(plan["budget"], {"spendCap": 234567, "principal": NEST})
        self.assertEqual(info["gold"], 5234567)

    def test_spend_cap_uses_one_wow_account(self):
        # A 900g main and a 10g bank alt on another WoW account, same market: the alt's buys must
        # never be funded by mailing the main's gold, so only the paying account's gold counts.
        inventory = {"Main-Forever": dict(inv(money=9000000), root="pc", account="1234#1"),
                     "Bankalt-Forever": dict(inv(money=100000), root="vm", account="1234#2")}
        main = dict(SESS[0], id="10-Main", char="Main-Forever", loginAt=10, root="pc", account="1234#1")
        alt = dict(SESS[0], id="20-Bankalt", char="Bankalt-Forever", loginAt=20, root="vm", account="1234#2")
        plan, info = self.plan([scan(T, MAGEROYAL)], inventory, sessions=[main, alt])
        self.assertEqual(info["spend_account"], "1234#2", "the newest login on this market pays")
        self.assertEqual((info["gold"], plan["budget"]["spendCap"]), (100000, 0), "10g is under the 500g nest egg")
        self.assertTrue(any("1234#2" in n and "never mail gold between accounts" in n for n in plan["notes"]))
        plan, info = self.plan([scan(T, MAGEROYAL)], inventory, sessions=[dict(alt, loginAt=5), main])
        self.assertEqual(info["spend_account"], "1234#1")
        self.assertEqual(plan["budget"]["spendCap"], 4000000)
        self.assertEqual(sorted(a["account"] for a in info["accounts"]), ["1234#1", "1234#2"])
        # characters on one account still pool their gold
        same = {"Main-Forever": dict(inv(money=4000000), account="1234#1"),
                "Alt-Forever": dict(inv(money=2000000), account="1234#1")}
        plan, info = self.plan([scan(T, MAGEROYAL)], same, sessions=[main])
        self.assertEqual(plan["budget"]["spendCap"], 1000000)
        self.assertFalse(any("between accounts" in n for n in plan["notes"]))

    def test_vendor_mats_count_against_the_spend_cap(self):
        # Peacebloom is held, so the potion craft needs only vendor mats: 8 Mild Spices x 2c + 8 Empty Vial x 4c.
        plan, info = self.plan([scan(T, POTION_MARKET)], self.poor({2447: 37}))
        self.assertEqual(plan["budget"]["spendCap"], 0)
        self.assertEqual(plan["craft"], [], "no craft when its vendor mats would dip into the nest egg")
        self.assertEqual(info["vendor_shop"], [])
        self.assertFalse(any("Buy from a vendor" in n for n in plan["notes"]))
        self.assertEqual([d["label"] for d in info["dropped"]], ["craft 8 Minor Healing Potion"])
        plan, _ = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}, spare=47))
        self.assertEqual(plan["craft"], [], "47c above the nest egg is 1c short of the 48c of mats")
        plan, info = self.plan([scan(T, POTION_MARKET)], self.rich({2447: 37}, spare=48))
        self.assertEqual(len(plan["craft"]), 1)
        self.assertEqual(info["buy_total"], 48, "AH buys plus vendor mats")

    def test_no_buys_below_the_nest_egg(self):
        plan, info = self.plan([scan(T, self.FLIPS)], self.poor())
        self.assertEqual(plan["buy"], [])
        self.assertEqual(plan["budget"]["spendCap"], 0)
        self.assertTrue(any("nest egg" in n for n in plan["notes"]))

    def test_all_rows_fit_and_sort_by_profit(self):
        plan, info = self.plan([scan(T, self.FLIPS)], self.rich(spare=2000))
        self.assertEqual(self.ids(plan["buy"]), [4234, 2318])
        self.assertEqual(info["dropped"], [])

    def test_lowest_profit_rows_are_dropped_first(self):
        plan, info = self.plan([scan(T, self.FLIPS)], self.rich(spare=1400))
        self.assertEqual(self.ids(plan["buy"]), [4234])
        self.assertEqual([d["label"] for d in info["dropped"]], ["flip Light Leather"])
        self.assertLessEqual(sum(r["qty"] * r["maxUnit"] for r in plan["buy"]), plan["budget"]["spendCap"])
        plan, info = self.plan([scan(T, self.FLIPS)], self.rich(spare=600))
        self.assertEqual(plan["buy"], [])
        self.assertEqual([d["label"] for d in info["dropped"]], ["flip Light Leather", "flip Heavy Leather"])

    def test_a_craft_and_its_reagent_rows_are_dropped_together(self):
        market = merged(POTION_MARKET, HEAVY_LEATHER)
        plan, info = self.plan([scan(T, market)], self.rich(spare=2000))
        self.assertEqual(sorted(self.ids(plan["buy"])), [2447, 4234])
        self.assertEqual(len(plan["craft"]), 1)
        plan, info = self.plan([scan(T, market)], self.rich(spare=1400))
        self.assertEqual(self.ids(plan["buy"]), [4234])
        self.assertEqual(plan["craft"], [])
        self.assertNotIn(118, self.ids(plan["post"]), "no post row for a craft that was dropped")
        self.assertEqual([d["label"] for d in info["dropped"]], ["craft 8 Minor Healing Potion"])


class TestCapsAndShapes(PlannerCase):
    def test_max_rows(self):
        bags = {785: 18, 2318: 25, 2589: 40, 4865: 3, 2449: 5, 6948: 1}
        market = merged(MAGEROYAL, LIGHT_LEATHER, {2589: [[15, 100]], 765: [[15, 60]]})
        plan, info = self.plan([scan(T, market)], self.poor(bags), cfg={"max_rows": 1})
        for k in ("buy", "craft", "post", "hold", "vendor"):
            self.assertLessEqual(len(plan[k]), 1, k)
        self.assertGreater(info["capped"]["vendor"], 0)

    def test_empty_plan_without_scans(self):
        plan, info = self.plan([], self.poor({2447: 5}))
        self.assertEqual(plan["headline"], "No scans yet: log in, open the AH, click Scan, then /reload")
        for k in ("buy", "craft", "post", "hold", "vendor"):
            self.assertEqual(plan[k], [])
        self.assertEqual(plan["basedOnScan"], {"at": 0, "market": "", "rows": 0})
        self.assertEqual(plan["schema"], 1)
        self.assertEqual(len(plan["steps"]), 4)
        self.assertIn("plan id", plan["steps"][0])
        self.assertTrue(all(st.endswith("min)") for st in plan["steps"]))

    def test_steps_have_minute_estimates(self):
        market = merged(POTION_MARKET, HEAVY_LEATHER, MAGEROYAL)
        plan, _ = self.plan([scan(T, market)], self.rich({785: 18, 4865: 2}, spare=2000))
        self.assertEqual(len(plan["steps"]), 5)
        self.assertEqual(plan["steps"][0], "Mailbox: Open All (1 min)")
        self.assertEqual(plan["steps"][1], "AH: click Scan, then Buy list; buy the 2 green row(s) (3 min)")
        self.assertTrue(plan["steps"][2].startswith("Buy the vendor mats, then craft 1 recipe(s), 8 in all"))
        self.assertTrue(plan["steps"][4].startswith("Vendor 1 item(s), then click Done"))
        for st in plan["steps"] + [plan["headline"]] + plan["notes"]:
            self.assertRegex(st, r"^[ -~]+$", "plain ASCII")
            self.assertNotIn("|", st)
        for st in plan["steps"]:
            self.assertRegex(st, r"\(\d+ min\)$")

    def test_plan_keys_are_exactly_the_spec(self):
        plan, _ = self.plan([scan(T, MAGEROYAL)], self.poor({785: 18}))
        self.assertEqual(set(plan), set(apply.TOP_KEYS))
        for k, keys in apply.ROW_KEYS.items():
            for r in plan[k]:
                self.assertLessEqual(set(r), set(keys))
        self.assertEqual(plan["settings"], {"postMode": "match", "undercut": 100})
        self.assertEqual(plan["lastScanAt"], T)

    def test_chars_on_market(self):
        inventory = {c: inv() for c in ("Travv-Forever", "Horder-Forever", "Nosess-Forever", "Other-Elsewhere")}
        sessions = SESS + [dict(SESS[0], id="2-H", char="Horder-Forever", faction="Horde")]
        self.assertEqual(chars_on_market(inventory, sessions, "us|Forever|Alliance"), ["Nosess-Forever", "Travv-Forever"])
        self.assertEqual(chars_on_market(inventory, sessions, "us|Forever|Horde"), ["Horder-Forever", "Nosess-Forever"])
        self.assertEqual(chars_on_market(inventory, sessions, "us|Forever|Neutral"),
                         ["Horder-Forever", "Nosess-Forever", "Travv-Forever"])
        self.assertEqual(chars_on_market(inventory, sessions, "eu|Forever|Alliance"), ["Nosess-Forever"])
        self.assertEqual(len(chars_on_market(inventory, sessions, None)), 4)

    def test_confidence_grades(self):
        from fgold.planner import confidence
        s = {"at": ts.NOW - 600}
        self.assertEqual(confidence(None, 0, 0, ts.NOW), "none (no scans)")
        self.assertEqual(confidence(s, 1, 1, ts.NOW), "low (one scan)")
        self.assertEqual(confidence(s, 2, 1, ts.NOW), "medium (1 day(s) of scans)")
        self.assertEqual(confidence(s, 6, 3, ts.NOW), "high (3 days of scans)")
        self.assertEqual(confidence({"at": ts.NOW - 2 * DAY}, 6, 3, ts.NOW), "low (newest scan is 2 d old)")
        plan, info = self.plan([scan(T - DAY, MAGEROYAL), scan(T, MAGEROYAL)], self.poor({785: 18}))
        self.assertTrue(plan["notes"][0].endswith("Confidence: medium."))

    def test_expect_prior_sessions(self):
        self.assertEqual(expect_prior_sessions({}), 0)
        st = {"accounts": {"a": {"mtime": 9, "sessions": 3}, "b": {"mtime": 5, "sessions": 7}}}
        self.assertEqual(expect_prior_sessions(st), 3)
        self.assertEqual(expect_prior_sessions({"accounts": {"a": {"mtime": 1, "sessions": 25}}}), 20)


class TestSubCopperEdges(PlannerCase):
    def test_zero_copper_listings_never_make_invalid_rows(self):
        zero = merged(POTION_MARKET, {2447: [[0, 30]], 3371: [[0, 10]]})
        plan, _ = self.plan([scan(T, zero)], self.rich())
        self.assertTrue(all(r["maxUnit"] >= 1 for r in plan["buy"]))
        self.assertEqual([(r["id"], r["flip"]) for r in plan["buy"]], [(2447, True)],
                         "0c Peacebloom is a flip (vendor 10c); Empty Vial (vendor 1c) has no buyable flip price")
        self.assertEqual(plan["craft"], [], "a 0c reagent cannot get a valid reagent buy row, and none is held")
        free_vials = merged(POTION_MARKET, {3371: [[0, 10]]})
        plan, info = self.plan([scan(T, free_vials)], self.rich({2447: 37}))
        self.assertEqual([c["count"] for c in plan["craft"]], [8], "vials fall back to the 4c vendor price")
        self.assertEqual(plan["buy"], [])
        self.assertEqual({v["id"]: v["source"] for v in info["vendor_shop"]}, {2678: "vendor", 3371: "vendor"})

    def test_posts_never_start_at_zero(self):
        from fgold.static import Static
        st = Static(items={9001: {"name": "Strange Dust", "sell": 0, "buy": 0, "stack": 20, "q": 1}})
        inp = PlanInputs(cfg=dict(ts.TEST_CONFIG, post_mode="undercut", undercut_copper=100), static=st,
                         market=ts.MARKET, scans=[scan(T, {9001: [[1, 50]]})],
                         inventory=self.poor({9001: 10}), sessions=SESS, now=ts.NOW)
        plan, _ = build_plan(inp)
        self.assertEqual([(r["floorUnit"], r["startUnit"]) for r in plan["post"]], [(1, 1)])
        apply.validate(plan, static=st)


class TestFixturePlan(PlannerCase):
    def test_plan_from_the_handmade_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            shutil.copytree(ts.FIXTURE_ROOT / "WTF", d / "wow" / "WTF")
            ingest.ingest([Root("wow", d / "wow", str(d / "wow"))], d / "data", stable_secs=0, now=ts.NOW)
            store = Store(d / "data")
            sessions = store.sessions()
            inp = PlanInputs(cfg=dict(ts.TEST_CONFIG), static=self.static, market=ts.MARKET,
                             scans=store.load_scans(ts.MARKET), inventory=store.inventory(), sessions=sessions,
                             ledger=store.ledger(), probes=store.probes(), state=store.state(), now=ts.NOW,
                             char_houses={}, last_scan_at=T)
            plan, info = build_plan(inp)
        apply.validate(plan, static=self.static, copper_ok=False)
        self.assertEqual(plan["budget"], {"spendCap": 234567, "principal": NEST})
        flips = {r["id"] for r in plan["buy"] if r["flip"]}
        self.assertEqual(flips, {2318, 4234})
        vend = {r["id"]: r for r in plan["vendor"]}
        self.assertIn(4865, vend, "Ruined Pelt is gray junk")
        self.assertIn("40 held", vend[2589]["why"],
                      "copperOK false: the sub-silver bandages would be holds, so no craft reserves Linen Cloth")
        self.assertIn(785, [r["id"] for r in plan["hold"]], "Mageroyal at 34c is under its 1s rounded floor")
        for r in plan["post"]:
            self.assertEqual(r["floorUnit"] % 100, 0, "newest session has copperOK false")
            self.assertEqual(r["startUnit"] % 100, 0)
            self.assertGreaterEqual(info["stats"][r["id"]]["min"], r["floorUnit"],
                                    "no post row that the in-game guard would show as HOLD")
        self.assertEqual(plan["expectPriorSessions"], 2)
        self.assertEqual(plan["basedOnScan"], {"at": T, "market": ts.MARKET, "rows": 162})
        self.assertNotIn(6948, [r["id"] for k in ("post", "hold", "vendor") for r in plan[k]])
        self.assertTrue(plan["headline"].startswith("Tonight:"))
        self.assertEqual(info["cut"]["source"], "config")


if __name__ == "__main__":
    unittest.main()
