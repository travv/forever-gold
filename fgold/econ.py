"""Economics: AH cut, net, vendor values, deposits, craft costs (design.md 3.2).

* cut_rate(house): the config cut (faction or neutral) until >= 3 seller
  invoices exist for that house, then the measured median of cut/bid.
* net(price) = price x (1 - cut).
* deposit: from data/probes.json when the addon probed the item, else the
  estimate max(100, round(sell x deposit_rate x {1:1, 2:4, 3:12}[duration]))
  (per post of `qty` units), flagged as an estimate.
* craft cost = sum of reagent unit costs, where a unit costs
  min(AH mkt, vendor unit price if an NPC sells it, Black Market cap).

Invoices do not say which auction house the sale happened at, so each seller
invoice is assigned a house: its own "house" field if present; else a cut
over 10% of a bid of 1s or more is the neutral house (15%; faction houses take
5%, and from 1s up rounding moves the rate by at most a point); else the only
house its character has scanned at (unless that is the neutral house and the
cut says faction); else (several houses) by the cut rate, else the character's
faction (from sessions), else the configured home house.

Deposit probes count only for the house they were measured at (the addon
records it); a probe without a house is never used.
"""

from __future__ import annotations

import math
import statistics

from . import config as config_mod
from . import util
from .store import HOUSES

DURATION_FACTOR = {1: 1, 2: 4, 3: 12}
DURATION_LABEL = {1: "2h", 2: "8h", 3: "24h"}
MIN_DEPOSIT = 100
MEASURE_MIN_INVOICES = 3
NEUTRAL_RATE_SPLIT = 0.10   # between the 5% faction cut and the 15% neutral cut
RATE_MIN_BID = 100          # from 1s up, cut rounding moves cut/bid by at most 1 point


class Econ:
    def __init__(self, cfg: dict, static, ledger=(), sessions=(), char_houses=None, probes=None):
        self.cfg = cfg
        self.static = static
        self.ledger = [r for r in ledger if isinstance(r, dict)]
        self.probes = probes or {}
        self.char_faction = {}
        for s in sessions:
            if isinstance(s, dict) and s.get("char") and s.get("faction") in ("Alliance", "Horde"):
                self.char_faction[s["char"]] = s["faction"]
        self.char_houses = {k: set(v) for k, v in (char_houses or {}).items()}

    # ---- cut ----------------------------------------------------------------
    def invoice_house(self, inv: dict):
        """(house, how) for an invoice."""
        h = inv.get("house")
        if h in HOUSES:
            return h, "invoice"
        char = inv.get("char")
        houses = self.char_houses.get(char) or set()
        bid = util.as_int(inv.get("bid"), 0) or 0
        rate = (util.as_int(inv.get("cut"), 0) or 0) / bid if bid > 0 else None
        clear = rate is not None and bid >= RATE_MIN_BID
        if clear and rate > NEUTRAL_RATE_SPLIT:
            return "Neutral", "rate"   # a 15% cut: neutral, whichever house the char scanned at
        if len(houses) == 1:
            only = next(iter(houses))
            if not (clear and only == "Neutral"):
                return only, "scans"
            # a faction-house cut from a char that only scanned the neutral AH: use its faction below
        faction = self.char_faction.get(char)
        if len(houses) > 1:
            if rate is not None and rate > NEUTRAL_RATE_SPLIT and "Neutral" in houses:
                return "Neutral", "rate"
            if faction in houses:
                return faction, "rate"
            return sorted(houses)[0], "rate"
        if faction:
            return faction, "faction"
        return self.cfg.get("home_house", "Alliance"), "default"

    def seller_rates(self, house: str) -> list:
        """[(rate, invoice)] for seller invoices assigned to house."""
        out = []
        for inv in self.ledger:
            if inv.get("type") != "seller":
                continue
            bid = util.as_int(inv.get("bid"), 0) or 0
            cut = util.as_int(inv.get("cut"), 0)
            if bid <= 0 or cut is None or cut < 0:
                continue
            if self.invoice_house(inv)[0] == house:
                out.append((cut / bid, inv))
        return out

    def cut_info(self, house: str) -> dict:
        rates = self.seller_rates(house)
        n = len(rates)
        if n >= MEASURE_MIN_INVOICES:
            return {"rate": float(statistics.median(r for r, _ in rates)), "source": "measured", "n": n}
        return {"rate": config_mod.cut_default(self.cfg, house), "source": "config", "n": n}

    def cut_rate(self, house: str) -> float:
        return self.cut_info(house)["rate"]

    def net(self, price, house: str) -> float:
        return float(price) * (1.0 - self.cut_rate(house))

    # ---- vendor / deposit -------------------------------------------------------
    def vendor_sell(self, iid) -> int:
        return self.static.sell(iid)

    def deposit(self, iid, duration: int, qty: int, house: str):
        """(copper, estimated) for one post of qty units."""
        qty = max(1, int(qty))
        pr = (self.probes.get("deposit") or {}).get(str(iid))
        # Only a probe measured at this house: the neutral deposit rate is 5x the faction one.
        if isinstance(pr, dict) and pr.get(str(duration)) is not None and pr.get("house") == house:
            pq = util.as_int(pr.get("qty"), 1) or 1
            val = util.as_int(pr.get(str(duration)), 0) or 0
            if qty == pq:
                return val, False
            return max(MIN_DEPOSIT, util.money_round(val / pq * qty)), False
        sell = self.static.sell(iid)
        rate = self.static.house_rates(house)["deposit"]
        est = max(MIN_DEPOSIT, util.money_round(sell * rate * DURATION_FACTOR[duration] * qty))
        return est, True

    # ---- crafting -----------------------------------------------------------------
    def unit_cost(self, iid, stats: dict):
        """(copper per unit, source) with source in ah/vendor/black market; (None, None) if unknown."""
        cands = []
        st = stats.get(iid)
        if st and st["mkt"] >= 1:  # a 0c mkt (sub-copper units) is not a price anyone can bid
            cands.append((st["mkt"], 0, "ah"))
        vb = self.static.vendor_buy(iid)
        if vb is not None and vb > 0:
            cands.append((vb, 1, "vendor"))
        cap = self.static.cap(iid)
        if cap is not None:
            cands.append((cap, 2, "black market"))
        if not cands:
            return None, None
        c = min(cands)
        return c[0], c[2]

    def craft(self, recipe: dict, stats: dict, house: str):
        """Economics of one craft at current prices, or None if it cannot be priced."""
        out = recipe["out"]
        st_out = stats.get(out)
        if not st_out:
            return None
        cost = 0.0
        parts = []
        for rid, cnt in recipe["reagents"]:
            uc, src = self.unit_cost(rid, stats)
            if uc is None:
                return None
            cost += uc * cnt
            parts.append({"id": rid, "qty": cnt, "unit": uc, "source": src})
        revenue = st_out["mkt"] * recipe["out_count"] * (1.0 - self.cut_rate(house))
        profit = revenue - cost
        margin = profit / cost if cost > 0 else math.inf
        return {"cost": cost, "revenue": revenue, "profit": profit, "margin": margin, "reagents": parts,
                "out_stats": st_out}
