"""market: per-item stats, history medians, scan days (CT), movers."""

import unittest

import test_support as ts  # noqa: F401
from fgold import market as mk

DAY = 86400


def scan(at, items, market=ts.MARKET):
    return {"at": at, "market": market, "by": "fg", "char": "A-Forever", "house": market.split("|")[2], "rows": 0,
            "items": {str(k): {"n": len(v), "ladder": v} for k, v in items.items()}, "own_items": {}}


class TestItemStats(unittest.TestCase):
    def test_peacebloom_with_anchor_listing(self):
        st = mk.item_stats([[22, 40], [23, 200], [25, 1600], [999999, 1]], 37)
        self.assertEqual(st, {"n": 37, "q": 1841, "min": 22, "mkt": 24, "p50": 25})

    def test_anchor_listing_does_not_move_mkt(self):
        base = [[22, 40], [23, 200], [25, 1600]]
        self.assertEqual(mk.item_stats(base)["mkt"], mk.item_stats(base + [[999999, 1]])["mkt"])

    def test_cheapest_quarter_unit_weighted_with_fractional_boundary(self):
        # q = 101 -> cheapest 25.25 units: 1 x 10 + 24.25 x 20 = 495 -> 19.6 -> 20
        self.assertEqual(mk.item_stats([[10, 1], [20, 100]])["mkt"], 20)
        # q = 200 -> 50 units: 40 x 34 + 10 x 36 = 1720 -> 34.4 -> 34
        self.assertEqual(mk.item_stats([[34, 40], [36, 60], [40, 100]])["mkt"], 34)

    def test_at_least_one_unit(self):
        st = mk.item_stats([[100, 1], [5000, 1]])
        self.assertEqual(st["mkt"], 100)
        self.assertEqual(mk.item_stats([[7, 1]])["mkt"], 7)

    def test_never_below_min_and_rounding_half_up(self):
        st = mk.item_stats([[3, 1], [4, 1], [4, 2]])  # q 4 -> 1 unit -> 3
        self.assertEqual(st["mkt"], 3)
        st = mk.item_stats([[1, 2], [2, 2], [3, 4]])  # q 8 -> 2 units at 1 -> 1
        self.assertGreaterEqual(st["mkt"], st["min"])
        self.assertEqual(mk.item_stats([[1, 2], [2, 2]])["mkt"], 1)
        # 4 units of q 16: 2 x 10 + 2 x 11 = 42 / 4 = 10.5 -> 11 (half up)
        self.assertEqual(mk.item_stats([[10, 2], [11, 2], [50, 12]])["mkt"], 11)

    def test_unsorted_input_and_zero_quantities(self):
        st = mk.item_stats([[40, 100], [34, 40], [36, 60], [1, 0]])
        self.assertEqual((st["min"], st["q"], st["mkt"]), (34, 200, 34))
        self.assertIsNone(mk.item_stats([]))
        self.assertIsNone(mk.item_stats([[5, 0]]))

    def test_p50_is_the_lower_weighted_median(self):
        self.assertEqual(mk.item_stats([[10, 1], [20, 1]])["p50"], 10)
        self.assertEqual(mk.item_stats([[10, 1], [20, 2]])["p50"], 20)
        self.assertEqual(mk.item_stats([[10, 5], [20, 1], [30, 1]])["p50"], 10)

    def test_n_defaults_to_points(self):
        self.assertEqual(mk.item_stats([[1, 1], [2, 1]])["n"], 2)

    def test_scan_stats(self):
        s = scan(100, {2447: [[22, 40]], 765: [[15, 60], [16, 100]]})
        st = mk.scan_stats(s)
        self.assertEqual(sorted(st), [765, 2447])
        self.assertEqual(st[765]["q"], 160)

    def test_ladder_helpers(self):
        ladder = [[10, 20], [11, 20], [20, 100]]
        self.assertEqual(mk.units_below(ladder, 13.5), 40)
        self.assertEqual(mk.units_below(ladder, 11), 20)
        self.assertEqual(mk.units_at_or_below(ladder, 11), 40)
        self.assertEqual(mk.fill_cost(ladder, 30), (10 * 20 + 11 * 10, 30))
        self.assertEqual(mk.fill_cost(ladder, 500), (200 + 220 + 2000, 140))


class TestHistory(unittest.TestCase):
    def test_three_and_seven_day_medians(self):
        t = ts.SCAN_AT
        scans = [scan(t - 8 * DAY, {1: [[1000, 10]]}), scan(t - 5 * DAY, {1: [[50, 10]]}),
                 scan(t - 2 * DAY, {1: [[30, 10]]}), scan(t - 1 * DAY, {1: [[20, 10]]}), scan(t, {1: [[10, 10]]})]
        h = mk.history(scans)[1]
        self.assertEqual((h["n3"], h["med3"]), (3, 20))
        self.assertEqual((h["n7"], h["med7"]), (4, 25))

    def test_single_scan_has_no_medians(self):
        h = mk.history([scan(ts.SCAN_AT, {1: [[10, 10]]})])[1]
        self.assertIsNone(h["med3"])
        self.assertIsNone(h["med7"])

    def test_two_scans_give_a_median(self):
        h = mk.history([scan(ts.SCAN_AT - 3600, {1: [[60, 10]]}), scan(ts.SCAN_AT, {1: [[34, 10]]})])[1]
        self.assertEqual(h["med3"], 47)

    def test_items_only_in_some_scans(self):
        h = mk.history([scan(ts.SCAN_AT - 3600, {1: [[60, 10]], 2: [[5, 5]]}), scan(ts.SCAN_AT, {1: [[34, 10]]})])
        self.assertEqual(h[2]["n3"], 1)
        self.assertIsNone(h[2]["med3"])

    def test_empty(self):
        self.assertEqual(mk.history([]), {})


class TestScanDays(unittest.TestCase):
    def test_days_are_ct_calendar_days(self):
        late = 1793856600   # Wed Nov 4 2026 11:30 PM CST (Nov 5 05:30 UTC)
        early = 1793860200  # Thu Nov 5 2026 12:30 AM CST (Nov 5 06:30 UTC)
        self.assertEqual(mk.scan_days([scan(late, {}), scan(early, {})]), 2)
        self.assertEqual(mk.scan_days([scan(early, {}), scan(early + 3600, {})]), 1)
        self.assertEqual(mk.scan_days([]), 0)


class TestMovers(unittest.TestCase):
    def test_movers(self):
        t = ts.SCAN_AT
        scans = [scan(t - DAY, {1: [[100, 10]], 2: [[100, 10]], 3: [[100, 10]]}),
                 scan(t, {1: [[150, 10]], 2: [[105, 10]], 3: [[40, 10]]})]
        m = mk.movers(scans)
        self.assertEqual([x["id"] for x in m], [3, 1])
        self.assertAlmostEqual(m[1]["change"], 0.5)
        self.assertEqual(m[0]["base"], 100)

    def test_needs_an_older_scan(self):
        t = ts.SCAN_AT
        self.assertEqual(mk.movers([scan(t - 3600, {1: [[100, 10]]}), scan(t, {1: [[300, 10]]})]), [])
        self.assertEqual(mk.movers([scan(t, {1: [[300, 10]]})]), [])

    def test_ignores_thin_items(self):
        t = ts.SCAN_AT
        self.assertEqual(mk.movers([scan(t - DAY, {1: [[100, 1]]}), scan(t, {1: [[300, 1]]})]), [])


if __name__ == "__main__":
    unittest.main()
