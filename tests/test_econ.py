"""econ: cut by house (5% / 15% / measured), net, deposits, craft costs."""

import unittest

import test_support as ts
from fgold import market as mk
from fgold import util
from fgold.econ import Econ


def seller(bid, cut, char="Travv-Forever", **kw):
    inv = {"type": "seller", "bid": bid, "cut": cut, "char": char, "item": "Peacebloom", "count": 20,
           "seenAt": 1793926920, "daysLeft": 29.9, "deposit": 100}
    inv.update(kw)
    return inv


CHAR_HOUSES = {"Travv-Forever": {"Alliance"}, "Goblin-Forever": {"Neutral"}, "Both-Forever": {"Alliance", "Neutral"}}
SESSIONS = [{"char": "Travv-Forever", "faction": "Alliance"}, {"char": "Horder-Forever", "faction": "Horde"},
            {"char": "Both-Forever", "faction": "Alliance"}]


class TestCut(unittest.TestCase):
    def setUp(self):
        self.static = ts.load_static()
        self.cfg = dict(ts.TEST_CONFIG)

    def econ(self, ledger=(), probes=None):
        return Econ(self.cfg, self.static, ledger, SESSIONS, CHAR_HOUSES, probes)

    def test_config_defaults(self):
        e = self.econ()
        self.assertEqual(e.cut_rate("Alliance"), 0.05)
        self.assertEqual(e.cut_rate("Horde"), 0.05)
        self.assertEqual(e.cut_rate("Neutral"), 0.15)
        self.assertEqual(e.cut_info("Alliance")["source"], "config")
        self.assertAlmostEqual(e.net(100, "Alliance"), 95.0)
        self.assertAlmostEqual(e.net(100, "Neutral"), 85.0)

    def test_measured_after_three_seller_invoices(self):
        two = [seller(1000, 60), seller(1000, 60)]
        self.assertEqual(self.econ(two).cut_info("Alliance")["source"], "config")
        three = two + [seller(1000, 50)]
        info = self.econ(three).cut_info("Alliance")
        self.assertEqual(info["source"], "measured")
        self.assertEqual(info["n"], 3)
        self.assertAlmostEqual(info["rate"], 0.06)
        self.assertAlmostEqual(self.econ(three).net(100, "Alliance"), 94.0)

    def test_only_seller_invoices_with_a_bid_count(self):
        ledger = [seller(1000, 60), seller(1000, 60), seller(0, 0), dict(seller(1000, 60), type="seller_temp_invoice"),
                  dict(seller(1000, 60), type="buyer")]
        self.assertEqual(self.econ(ledger).cut_info("Alliance")["source"], "config")

    def test_measured_is_per_house(self):
        neutral = [seller(1000, 150, char="Goblin-Forever") for _ in range(3)]
        e = self.econ(neutral)
        self.assertEqual(e.cut_info("Neutral")["source"], "measured")
        self.assertAlmostEqual(e.cut_rate("Neutral"), 0.15)
        self.assertEqual(e.cut_info("Alliance")["source"], "config")

    def test_invoice_house_inference(self):
        e = self.econ()
        self.assertEqual(e.invoice_house(seller(100, 15, house="Neutral")), ("Neutral", "invoice"))
        self.assertEqual(e.invoice_house(seller(100, 5)), ("Alliance", "scans"))
        self.assertEqual(e.invoice_house(seller(100, 15, char="Both-Forever")), ("Neutral", "rate"))
        self.assertEqual(e.invoice_house(seller(100, 5, char="Both-Forever")), ("Alliance", "rate"))
        self.assertEqual(e.invoice_house(seller(100, 5, char="Horder-Forever")), ("Horde", "faction"))
        self.assertEqual(e.invoice_house(seller(100, 5, char="Nobody-Forever")), ("Alliance", "default"))

    def test_a_15_percent_cut_is_neutral_even_for_a_one_house_char(self):
        # Travv has only scanned the Alliance AH, but sold at Booty Bay: 3 such invoices used to be
        # counted as an Alliance cut of 15% ("measured") and priced every Alliance post wrong.
        e = self.econ()
        self.assertEqual(e.invoice_house(seller(1000, 150)), ("Neutral", "rate"))
        three = [seller(1000, 150) for _ in range(3)]
        self.assertEqual(self.econ(three).cut_info("Alliance")["source"], "config")
        self.assertAlmostEqual(self.econ(three).cut_rate("Neutral"), 0.15)
        # the other way: a 5% cut by a char that only scanned the neutral AH is a faction sale
        goblin = Econ(self.cfg, self.static, [], SESSIONS + [{"char": "Goblin-Forever", "faction": "Horde"}],
                      CHAR_HOUSES)
        self.assertEqual(goblin.invoice_house(seller(1000, 50, char="Goblin-Forever")), ("Horde", "faction"))
        # tiny bids round too coarsely to tell: 1c of cut on 7c is 14%
        self.assertEqual(e.invoice_house(seller(7, 1)), ("Alliance", "scans"))


class TestDepositAndCosts(unittest.TestCase):
    def setUp(self):
        self.static = ts.load_static()
        self.cfg = dict(ts.TEST_CONFIG)
        db = ts.fixture_db()
        from fgold.ingest import parse_scan_string
        items, _ = parse_scan_string(db["scans"][0]["data"])
        self.stats = {iid: mk.item_stats(e["ladder"], e["n"]) for iid, e in items.items()}

    def test_deposit_estimate(self):
        e = Econ(self.cfg, self.static)
        # Peacebloom sells for 10c: 10 x 0.05 x 12 x 37 = 222
        self.assertEqual(e.deposit(2447, 3, 37, "Alliance"), (222, True))
        self.assertEqual(e.deposit(2447, 1, 1, "Alliance"), (100, True), "1s minimum per post")
        self.assertEqual(e.deposit(2447, 2, 30, "Alliance"), (100, True))
        # Black Lotus sells for 10s; neutral deposit rate 25%, 8h factor 4: 1000 x 0.25 x 4 = 1000
        self.assertEqual(e.deposit(13468, 2, 1, "Neutral"), (1000, True))
        self.assertEqual(e.deposit(999999, 3, 5, "Alliance"), (100, True), "unknown item: minimum")

    def test_deposit_from_probe(self):
        probes = {"deposit": {"785": {"1": 100, "2": 100, "3": 240, "qty": 20, "at": 5, "house": "Alliance"}}}
        e = Econ(self.cfg, self.static, probes=probes)
        self.assertEqual(e.deposit(785, 3, 20, "Alliance"), (240, False))
        self.assertEqual(e.deposit(785, 3, 10, "Alliance"), (120, False))
        self.assertEqual(e.deposit(785, 1, 5, "Alliance"), (100, False))
        tagged = {"deposit": {"785": {"1": 1, "2": 1, "3": 1, "qty": 20, "at": 5, "house": "Neutral"}}}
        self.assertTrue(Econ(self.cfg, self.static, probes=tagged).deposit(785, 3, 20, "Alliance")[1])

    def test_a_probe_without_a_house_is_never_used(self):
        # A neutral-AH probe (25% deposit rate) without a house used to price an Alliance post:
        # 60s measured against a 2s 40c estimate.
        untagged = {"deposit": {"785": {"1": 500, "2": 2000, "3": 6000, "qty": 20, "at": 5}}}
        e = Econ(self.cfg, self.static, probes=untagged)
        self.assertEqual(e.deposit(785, 3, 20, "Alliance"), (240, True))
        self.assertEqual(e.deposit(785, 3, 20, "Neutral")[1], True)

    def test_unit_cost_takes_the_cheapest_source(self):
        e = Econ(self.cfg, self.static)
        self.assertEqual(e.unit_cost(2447, self.stats), (24, "ah"))
        self.assertEqual(e.unit_cost(3371, self.stats), (4, "vendor"), "Empty Vial: vendor 4c beats AH 10c")
        self.assertEqual(e.unit_cost(2678, self.stats), (2, "vendor"), "Mild Spices: vendor 2c beats AH 5c")
        self.assertEqual(e.unit_cost(13468, self.stats), (500000, "black market"), "Black Lotus capped at 50g")
        self.assertEqual(e.unit_cost(3372, {}), (40, "vendor"), "Leaded Vial vendor-only")
        self.assertEqual(e.unit_cost(2449, {}), (None, None))
        self.assertEqual(e.unit_cost(2318, self.stats)[1], "ah", "Light Leather has a BuyPrice but no vendor sells it")
        zero = {3371: mk.item_stats([[0, 10]]), 2447: mk.item_stats([[0, 10]])}
        self.assertEqual(e.unit_cost(3371, zero), (4, "vendor"), "a 0c AH mkt is not a usable price")
        self.assertEqual(e.unit_cost(2447, zero), (None, None))

    def test_craft_economics(self):
        e = Econ(self.cfg, self.static)
        c = e.craft(self.static.recipes[1244431], self.stats, "Alliance")
        self.assertAlmostEqual(c["cost"], 30.0)
        self.assertAlmostEqual(c["revenue"], 46 * 0.95)
        self.assertAlmostEqual(c["profit"], 46 * 0.95 - 30)
        self.assertAlmostEqual(c["margin"], (46 * 0.95 - 30) / 30)
        self.assertEqual([p["source"] for p in c["reagents"]], ["vendor", "vendor", "ah"])
        neutral = e.craft(self.static.recipes[1244431], self.stats, "Neutral")
        self.assertAlmostEqual(neutral["revenue"], 46 * 0.85)

    def test_craft_needs_an_output_price(self):
        e = Econ(self.cfg, self.static)
        stats = dict(self.stats)
        del stats[118]
        self.assertIsNone(e.craft(self.static.recipes[1244431], stats, "Alliance"))

    def test_silver_rounding_helper(self):
        self.assertEqual([util.ceil_silver(x) for x in (0, 1, 99, 100, 101, 250)], [0, 100, 100, 100, 200, 300])


if __name__ == "__main__":
    unittest.main()
