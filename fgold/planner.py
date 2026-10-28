"""Draft plan builder, cold start first (design.md 3.2 planner.py, rules 1-6).

1. Below-vendor flips: AH min <= 0.8 x vendor sell -> buy the units listed
   under 0.9 x sell, flip = true, maxUnit = floor(0.9 x sell).
2. Inventory triage (characters on this market): post if net(mkt) > 1.1 x
   vendor with floorUnit = max(ceil(vendor / (1 - cut)), floor(0.85 x ref)),
   ref = 3-day median mkt if available else mkt; startUnit = match min (or
   undercut) but >= floor; hold if min < floor; vendor if vendor >= net.
   With copperOK false the floor is rounded up to whole silver before the
   hold test, as the in-game guard compares it.
3. Crafts (professions with skill > 0): recipes with margin >= 25% after the
   cut; buy rows for missing reagents with maxUnit = min(ceil(1.1 x mkt),
   floor(1.5 x mkt)). Crafted output also gets a post row with a break-even
   floor; a craft whose output would be a hold (min < floor) is skipped.
4. No invest/hold-for-later calls until >= 3 days of scans for the market.
5. Budget: spendCap = max(0, gold on ONE WoW account - nest egg): the
   account of the newest session on this market, whose characters there
   pool their gold. Other accounts never fund the list (gold mailed between
   accounts is what Economy Holds flag). AH buys (qty x maxUnit) plus the
   vendor / Black Market mats of craft groups must fit in spendCap, dropping
   the lowest-profit groups (a craft, its reagent rows and its vendor mats
   count as one) first.
6. Lists sorted by expected profit, capped at max_rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import ct
from . import market as mk
from . import util
from .econ import DURATION_LABEL, Econ
from .static import profession_line
from .store import house_of_market, region_code, split_market

FLIP_TRIGGER = 0.8
FLIP_BUY_BELOW = 0.9
POST_OVER_VENDOR = 1.1
FLOOR_REF_SHARE = 0.85
CRAFT_MIN_MARGIN = 0.25
CRAFT_MAX_COUNT = 20
INVEST_MIN_DAYS = 3
DEPOSIT_MAX_SHARE = 0.10
MAX_NOTES = 5
MAX_STEPS = 5
NO_SCAN_ACTION = "no scans yet: log in, open the AH, click Scan, then /reload"


@dataclass
class PlanInputs:
    cfg: dict
    static: object
    market: object = None
    scans: list = field(default_factory=list)
    inventory: dict = field(default_factory=dict)
    sessions: list = field(default_factory=list)
    ledger: list = field(default_factory=list)
    probes: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    now: int = 0
    char_houses: dict = field(default_factory=dict)
    last_scan_at: int = 0
    # Distinct CT days with scans for the market, counted over every stored
    # scan; None = count the scans passed in (callers may pass only 7 days).
    scan_days: int = None


def vendor_cost(vendor_buys) -> int:
    """Copper for vendor / Black Market mats: [(id, qty, unit, source)] or shop dicts."""
    total = 0.0
    for v in vendor_buys:
        qty, unit = (v["qty"], v["unit"]) if isinstance(v, dict) else (v[1], v[2])
        total += qty * unit
    return int(math.ceil(total - 1e-9))


@dataclass
class Group:
    kind: str
    profit: float
    buys: list
    craft: object = None
    post: object = None
    vendor_buys: list = field(default_factory=list)
    label: str = ""
    reserve: dict = field(default_factory=dict)

    @property
    def cost(self) -> int:
        """Most this group can spend: AH buys at maxUnit plus its vendor / Black Market mats."""
        return sum(b["qty"] * b["maxUnit"] for b in self.buys) + vendor_cost(self.vendor_buys)


def chars_on_market(inventory: dict, sessions: list, market) -> list:
    """Characters whose realm/region/faction fit the market (all if market is None)."""
    chars = sorted(inventory)
    if not market:
        return chars
    region, realm, house = split_market(market)
    info = {}
    for s in sessions:
        if isinstance(s, dict) and s.get("char"):
            info[s["char"]] = s

    def norm(x):
        return str(x).replace(" ", "").lower()

    out = []
    for c in chars:
        s = info.get(c) or {}
        crealm = s.get("realm") or (c.split("-", 1)[1] if "-" in c else None)
        if crealm is not None and norm(crealm) != norm(realm):
            continue
        if s.get("region") is not None and region_code(s["region"]) != region:
            continue
        faction = s.get("faction")
        if house != "Neutral" and faction in ("Alliance", "Horde") and faction != house:
            continue
        out.append(c)
    return out


def spend_basis(inventory: dict, sessions: list, market) -> dict:
    """Whose gold may fund tonight's buys (design.md 3.2 rule 5).

    Characters on one WoW account pool their gold; other accounts never fund
    the list, because gold mailed between accounts is the one-sided move
    Economy Holds flag. The paying account is the one of the newest session
    on this market (else of the newest inventory snapshot). Characters whose
    account is unknown (no root/account in the record) count as one account.
    Returns {"chars", "account", "gold", "accounts": [{"account", "gold", "chars"}]}."""
    chars = chars_on_market(inventory, sessions, market)
    by_char = {}
    for s in sessions:
        if isinstance(s, dict) and s.get("char"):
            by_char.setdefault(s["char"], []).append(s)

    def account(c):
        a = (inventory.get(c) or {}).get("account")
        if not a:
            a = next((s.get("account") for s in by_char.get(c, []) if s.get("account")), None)
        return str(a) if a else ""

    def money(c):
        return util.as_int((inventory.get(c) or {}).get("money"), 0) or 0

    groups = {}
    for c in chars:
        groups.setdefault(account(c), []).append(c)
    newest = None
    for c in chars:
        for s in by_char.get(c, []):
            key = (util.as_int(s.get("loginAt"), 0) or 0, str(s.get("id", "")))
            if newest is None or key > newest[0]:
                newest = (key, c)
    if newest is not None:
        chosen = account(newest[1])
    elif chars:
        chosen = account(max(chars, key=lambda c: (util.as_int((inventory.get(c) or {}).get("at"), 0) or 0, c)))
    else:
        chosen = ""
    accounts = [{"account": a, "gold": sum(money(c) for c in cs), "chars": cs} for a, cs in sorted(groups.items())]
    return {"chars": chars, "account": chosen, "gold": sum(money(c) for c in groups.get(chosen, [])),
            "accounts": accounts}


def _held(inventory: dict, chars: list):
    total, bank = {}, {}
    for c in chars:
        inv = inventory.get(c) or {}
        for src, dest in ((inv.get("bags") or {}, None), (inv.get("bank") or {}, bank)):
            for k, n in src.items():
                iid = util.item_id(k)
                n = util.as_int(n, 0) or 0
                if iid and n > 0:
                    total[iid] = total.get(iid, 0) + n
                    if dest is not None:
                        dest[iid] = dest.get(iid, 0) + n
    return total, bank


def newest_session(sessions: list):
    recs = [s for s in sessions if isinstance(s, dict)]
    if not recs:
        return None
    return max(recs, key=lambda s: (s.get("loginAt") or 0, str(s.get("id", ""))))


def newest_copper_ok(sessions: list):
    """copperOK of the newest session (True, False or None when it did not say)."""
    s = newest_session(sessions)
    v = s.get("copperOK") if s else None
    return v if isinstance(v, bool) else None


def confidence(newest, scan_count: int, days: int, now: int) -> str:
    """Price confidence, graded like the skill's Reads: low = one scan or data
    older than 24 h, medium = 1-2 days of scans, high = 3+ days."""
    if not newest:
        return "none (no scans)"
    age = (now or 0) - (newest.get("at") or 0)
    if now and age > 86400:
        return f"low (newest scan is {ct.age_str(age)} old)"
    if scan_count <= 1:
        return "low (one scan)"
    if days < INVEST_MIN_DAYS:
        return f"medium ({days} day(s) of scans)"
    return f"high ({days} days of scans)"


def expect_prior_sessions(state: dict) -> int:
    best = None
    for acct in (state.get("accounts") or {}).values():
        if isinstance(acct, dict) and (best is None or (acct.get("mtime") or 0) > (best.get("mtime") or 0)):
            best = acct
    if not best:
        return 0
    return max(0, min(20, util.as_int(best.get("sessions"), 0) or 0))


class _Planner:
    def __init__(self, inp: PlanInputs):
        self.inp = inp
        self.cfg = inp.cfg
        self.static = inp.static
        self.market = inp.market
        self.house = house_of_market(inp.market) if inp.market else self.cfg.get("home_house", "Alliance")
        self.scans = sorted(inp.scans, key=lambda s: s.get("at", 0))
        self.newest = self.scans[-1] if self.scans else None
        self.stats = mk.scan_stats(self.newest) if self.newest else {}
        self.ladders = {}
        if self.newest:
            for k, e in (self.newest.get("items") or {}).items():
                iid = util.item_id(k)
                if iid and isinstance(e, dict):
                    self.ladders[iid] = e.get("ladder") or []
        self.hist = mk.history(self.scans) if self.scans else {}
        self.days = inp.scan_days if inp.scan_days is not None else mk.scan_days(self.scans)
        self.invest_ok = self.days >= INVEST_MIN_DAYS
        self.econ = Econ(self.cfg, self.static, inp.ledger, inp.sessions, inp.char_houses, inp.probes)
        self.cut = self.econ.cut_info(self.house)
        self.copper_ok = newest_copper_ok(inp.sessions)
        self.silver = self.copper_ok is False
        self.basis = spend_basis(inp.inventory, inp.sessions, inp.market)
        self.chars = self.basis["chars"]
        self.gold = self.basis["gold"]
        self.multi_account = len(self.basis["accounts"]) > 1
        self.nest = int(self.cfg.get("nest_egg_copper", 0))
        self.spend_cap = max(0, self.gold - self.nest)
        self.held, self.bank = _held(inp.inventory, self.chars)
        self.max_rows = int(self.cfg.get("max_rows", 25))
        self.deposit_estimated = False
        self.confidence = confidence(self.newest, len(self.scans), self.days, inp.now)
        self.info = {}

    # ---- helpers ---------------------------------------------------------
    def name(self, iid) -> str:
        return self.static.name(iid)

    def ref_price(self, iid) -> int:
        h = self.hist.get(iid) or {}
        if h.get("med3"):
            return h["med3"]
        return self.stats[iid]["mkt"]

    def silverize(self, c: int) -> int:
        return util.ceil_silver(c) if self.silver else int(c)

    def start_price(self, st: dict, floor_unit: int) -> int:
        if self.cfg.get("post_mode") == "undercut":
            start = st["min"] - int(self.cfg.get("undercut_copper", 0))
        else:
            start = st["min"]
        return max(start, floor_unit, 1)

    def duration(self, iid, qty: int, start: int):
        net_total = qty * start * (1.0 - self.cut["rate"])
        last = None
        for d in (3, 2, 1):
            dep, est = self.econ.deposit(iid, d, qty, self.house)
            last = (d, dep, est)
            if dep <= DEPOSIT_MAX_SHARE * net_total:
                return last
        return last

    def post_row(self, iid, qty, floor_unit, why, profit):
        st = self.stats[iid]
        floor_unit = self.silverize(floor_unit)
        start = self.silverize(self.start_price(st, floor_unit))
        d, dep, est = self.duration(iid, qty, start)
        if est:
            self.deposit_estimated = True
        dep_txt = f"{DURATION_LABEL[d]} deposit {util.fmt_money(dep)}" + (" (est.)" if est else "")
        return {"id": iid, "name": self.name(iid), "qty": int(qty), "floorUnit": int(floor_unit),
                "startUnit": int(start), "duration": d, "why": f"{why}; {dep_txt}", "_profit": profit}

    # ---- rule 1 -------------------------------------------------------------
    def flips(self) -> list:
        groups = []
        for iid, st in sorted(self.stats.items()):
            sell = self.static.sell(iid)
            if sell <= 0 or not self.static.item(iid):
                continue
            if st["min"] > FLIP_TRIGGER * sell:
                continue
            ladder = self.ladders.get(iid) or []
            limit = FLIP_BUY_BELOW * sell
            qty = mk.units_below(ladder, limit)
            if qty <= 0:
                continue
            max_unit = int(math.floor(limit))
            if max_unit < 1:
                continue
            cost, _ = mk.fill_cost(ladder, qty)
            profit = qty * sell - cost
            if profit <= 0:
                continue
            why = (f"listed from {util.fmt_money(st['min'])}, vendors for {util.fmt_money(sell)}: "
                   f"buy, then sell to any vendor (+{util.fmt_money(profit)})")
            buy = {"id": iid, "name": self.name(iid), "qty": int(qty), "maxUnit": max_unit, "why": why, "flip": True,
                   "_profit": profit}
            groups.append(Group("flip", profit, [buy], label=f"flip {self.name(iid)}"))
        return groups

    # ---- rule 2 -------------------------------------------------------------
    def triage(self, held: dict):
        post, hold, vendor = [], [], []
        cut = self.cut["rate"]
        for iid, qty in sorted(held.items()):
            if qty <= 0:
                continue
            sell = self.static.sell(iid)
            st = self.stats.get(iid)
            item = self.static.item(iid)
            in_bank = min(self.bank.get(iid, 0), qty)
            bank_txt = f" ({in_bank} in bank)" if in_bank else ""
            held_txt = f" ({qty} held, {in_bank} in bank)" if in_bank else f" ({qty} held)"
            if st is None:
                if not item or sell <= 0:
                    continue
                if item.get("q") == 0:
                    vendor.append({"id": iid, "name": self.name(iid), "why": f"junk: vendor ({qty} x {util.fmt_money(sell)})",
                                   "_value": qty * sell})
                elif self.static.stack(iid) > 1:
                    hold.append({"id": iid, "name": self.name(iid),
                                 "why": f"not on the AH in the newest scan: no price yet{held_txt}",
                                 "_value": qty * sell})
                continue
            mkt_ = st["mkt"]
            net = mkt_ * (1.0 - cut)
            ref = self.ref_price(iid)
            # Rounded the way the post row and the in-game guard round it, so a row the
            # guard would show as HOLD is planned as a hold, not a post.
            floor_unit = self.silverize(max(1, int(math.ceil(sell / (1.0 - cut))) if sell > 0 else 0,
                                            int(math.floor(FLOOR_REF_SHARE * ref))))
            if net > POST_OVER_VENDOR * sell:
                if st["min"] < floor_unit:
                    hold.append({"id": iid, "name": self.name(iid),
                                 "why": f"HOLD: market {util.fmt_money(st['min'])} is below your floor "
                                        f"{util.fmt_money(floor_unit)}{held_txt}",
                                 "_value": qty * mkt_})
                else:
                    start = max(self.start_price(st, floor_unit), floor_unit)
                    profit = qty * (start * (1.0 - cut) - sell)
                    why = f"net {util.fmt_money(net)} vs vendor {util.fmt_money(sell)}{bank_txt}"
                    post.append(self.post_row(iid, qty, floor_unit, why, profit))
            elif sell >= net:
                vendor.append({"id": iid, "name": self.name(iid),
                               "why": f"vendor {util.fmt_money(sell)} beats AH net {util.fmt_money(net)}{held_txt}",
                               "_value": qty * sell})
            elif self.invest_ok:
                hold.append({"id": iid, "name": self.name(iid),
                             "why": f"invest: AH net {util.fmt_money(net)} is near vendor {util.fmt_money(sell)}, "
                                    f"low-risk hold for later{held_txt}",
                             "_value": qty * mkt_, "_invest": True})
            else:
                vendor.append({"id": iid, "name": self.name(iid),
                               "why": f"AH net {util.fmt_money(net)} is within 10% of vendor {util.fmt_money(sell)}: "
                                      f"vendor, no deposit risk{held_txt}",
                               "_value": qty * sell})
        return post, hold, vendor

    # ---- rule 3 -------------------------------------------------------------
    def crafts(self) -> list:
        cands = []
        for prof, skill in sorted((self.cfg.get("professions") or {}).items()):
            if (util.as_int(skill, 0) or 0) <= 0:
                continue
            line = profession_line(prof)
            if line is None:
                continue
            for sid, rec in self.static.recipes_for(line, int(skill)):
                c = self.econ.craft(rec, self.stats, self.house)
                if not c or c["profit"] <= 0 or c["margin"] < CRAFT_MIN_MARGIN:
                    continue
                if not self.static.item(rec["out"]):
                    continue
                cands.append((sid, rec, c, prof))
        cands.sort(key=lambda t: (-t[2]["profit"], t[0]))
        held_left = dict(self.held)
        groups = []
        seen_out = set()
        for sid, rec, c, prof in cands:
            if rec["out"] in seen_out:
                continue
            out_st = c["out_stats"]
            profit_each = int(math.floor(c["profit"]))
            if profit_each <= 0:
                continue
            unit_cost = c["cost"] / rec["out_count"]
            floor_unit = self.silverize(max(1, int(math.ceil(unit_cost / (1.0 - self.cut["rate"]))),
                                            int(math.floor(FLOOR_REF_SHARE * self.ref_price(rec["out"])))))
            if out_st["min"] < floor_unit:
                continue  # the output would be a hold tonight (the guard shows HOLD below the floor)
            count = max(1, min(CRAFT_MAX_COUNT, out_st["q"] // 4))
            plan_parts = []
            for r in c["reagents"]:
                if r["source"] == "ah":
                    st = self.stats[r["id"]]
                    # min(ceil(1.1 x mkt), floor(1.5 x mkt)) in integers: 1.1 x 50 is 55.00000000000001
                    max_unit = min((11 * st["mkt"] + 9) // 10, (3 * st["mkt"]) // 2)
                    avail = mk.units_at_or_below(self.ladders.get(r["id"]) or [], max_unit)
                    if max_unit < 1 or not self.static.item(r["id"]):
                        avail = 0
                else:
                    max_unit = None
                    avail = None
                have = held_left.get(r["id"], 0)
                if avail is not None:
                    count = min(count, (have + avail) // r["qty"])
                plan_parts.append((r, max_unit))
            if count <= 0:
                continue
            buys, vendor_buys, reserve = [], [], {}
            for r, max_unit in plan_parts:
                need = count * r["qty"]
                have = held_left.get(r["id"], 0)
                take = min(have, need)
                held_left[r["id"]] = have - take
                if take:
                    reserve[r["id"]] = reserve.get(r["id"], 0) + take
                short = need - take
                if short <= 0:
                    continue
                if r["source"] == "ah":
                    buys.append({"id": r["id"], "name": self.name(r["id"]), "qty": int(short), "maxUnit": int(max_unit),
                                 "why": f"for {count} {self.name(rec['out'])}", "flip": False})
                else:
                    vendor_buys.append((r["id"], int(short), r["unit"], r["source"]))
            total = profit_each * count
            for b in buys:
                b["_profit"] = total
            cost_txt = util.fmt_money(c["cost"])
            craft_row = {
                "recipe": sid, "name": self.name(rec["out"]), "out": rec["out"], "count": int(count),
                "profitEach": profit_each,
                "why": (f"{prof}: cost {cost_txt}, sells {util.fmt_money(out_st['mkt'])}"
                        f"{' x' + str(rec['out_count']) if rec['out_count'] > 1 else ''}, "
                        f"+{util.fmt_money(profit_each)} each after cut ({c['margin'] * 100:.0f}%), if you know the recipe"),
                "_profit": total,
            }
            qty_out = count * rec["out_count"]
            start = max(self.start_price(out_st, floor_unit), floor_unit)
            post_profit = qty_out * (start * (1.0 - self.cut["rate"]) - self.static.sell(rec["out"]))
            post = self.post_row(rec["out"], qty_out, floor_unit, f"crafted tonight, floor = cost after cut", post_profit)
            seen_out.add(rec["out"])
            groups.append(Group("craft", total, buys, craft=craft_row, post=post, vendor_buys=vendor_buys,
                                label=f"craft {count} {self.name(rec['out'])}", reserve=reserve))
        return groups

    # ---- rules 5 and 6 ------------------------------------------------------
    def budget(self, groups: list):
        total = sum(g.cost for g in groups)
        order = sorted(groups, key=lambda g: (g.profit, g.label))
        dropped = []
        for g in order:
            if total <= self.spend_cap:
                break
            if g.cost <= 0:
                continue
            dropped.append(g)
            total -= g.cost
        gone = {id(g) for g in dropped}
        kept = [g for g in groups if id(g) not in gone]
        return kept, dropped

    def build(self):
        cfg = self.cfg
        groups = []
        if self.newest:
            groups = self.flips() + self.crafts()
        kept, dropped = self.budget(groups)
        kept.sort(key=lambda g: (-g.profit, g.label))
        buy_rows, craft_rows, craft_posts, vendor_shop = [], [], [], {}
        capped = []
        held_left = dict(self.held)
        for g in kept:
            if len(buy_rows) + len(g.buys) > self.max_rows or (g.craft and len(craft_rows) >= self.max_rows):
                capped.append(g)
                continue
            buy_rows.extend(g.buys)
            if g.craft:
                craft_rows.append(g.craft)
            if g.post:
                craft_posts.append(g.post)
            for iid, n in g.reserve.items():
                held_left[iid] = held_left.get(iid, 0) - n
            for iid, qty, unit, src in g.vendor_buys:
                cur = vendor_shop.get(iid) or {"id": iid, "name": self.name(iid), "qty": 0, "unit": unit, "source": src}
                cur["qty"] += qty
                vendor_shop[iid] = cur
        post, hold, vendor = self.triage(held_left) if self.newest else ([], [], [])
        buy_rows = _merge_buys(buy_rows)
        post = _merge_posts(post + craft_posts)
        buy_rows.sort(key=lambda r: (-r["_profit"], r["id"]))
        post.sort(key=lambda r: (-r["_profit"], r["id"]))
        hold.sort(key=lambda r: (-r["_value"], r["id"]))
        vendor = _merge_simple(vendor)
        vendor.sort(key=lambda r: (-r["_value"], r["id"]))
        craft_rows.sort(key=lambda r: (-r["_profit"], r["recipe"]))
        lists = {
            "buy": buy_rows[: self.max_rows],
            "craft": craft_rows[: self.max_rows],
            "post": post[: self.max_rows],
            "hold": hold[: self.max_rows],
            "vendor": vendor[: self.max_rows],
        }
        cut_off = {k: max(0, len(v) - self.max_rows) for k, v in
                   (("buy", buy_rows), ("craft", craft_rows), ("post", post), ("hold", hold), ("vendor", vendor))}
        ref = {}
        for key in ("buy", "post", "hold", "vendor"):
            for r in lists[key]:
                st = self.stats.get(r["id"])
                if st:
                    ref[str(r["id"])] = {"min": st["min"], "mkt": st["mkt"], "q": st["q"]}
        for r in lists["craft"]:
            st = self.stats.get(r["out"])
            if st:
                ref[str(r["out"])] = {"min": st["min"], "mkt": st["mkt"], "q": st["q"]}
        profit = {k: sum(r.get("_profit", 0) for r in lists[k]) for k in ("buy", "craft", "post")}
        vendor_total = sum(r.get("_value", 0) for r in lists["vendor"])
        for k in lists:
            lists[k] = [{kk: vv for kk, vv in r.items() if not kk.startswith("_")} for r in lists[k]]
        shop = sorted(vendor_shop.values(), key=lambda v: v["id"])

        notes = self.notes(dropped, shop)
        steps = self.steps(lists, shop)
        headline = self.headline(lists, profit)
        newest = self.newest or {}
        plan = {
            "schema": 1,
            "id": "draft",
            "generatedAt": int(self.inp.now),
            "basedOnScan": {"at": int(newest.get("at", 0) or 0), "market": self.market or "",
                            "rows": int(newest.get("rows", 0) or 0)},
            "lastScanAt": int(self.inp.last_scan_at or 0),
            "expectPriorSessions": expect_prior_sessions(self.inp.state),
            "settings": {"postMode": cfg.get("post_mode", "match"), "undercut": int(cfg.get("undercut_copper", 0))},
            "headline": headline,
            "notes": notes,
            "steps": steps,
            "budget": {"spendCap": int(self.spend_cap), "principal": int(self.nest)},
            "buy": lists["buy"],
            "craft": lists["craft"],
            "post": lists["post"],
            "hold": lists["hold"],
            "vendor": lists["vendor"],
            "ref": ref,
        }
        self.info = {
            "market": self.market,
            "house": self.house,
            "newest": self.newest,
            "scan_count": len(self.scans),
            "days": self.days,
            "invest_ok": self.invest_ok,
            "cut": self.cut,
            "copper_ok": self.copper_ok,
            "chars": self.chars,
            "gold": self.gold,
            "accounts": self.basis["accounts"],
            "spend_account": self.basis["account"],
            "nest": self.nest,
            "spend_cap": self.spend_cap,
            "vendor_cost": vendor_cost(shop),
            "buy_total": sum(r["qty"] * r["maxUnit"] for r in lists["buy"]) + vendor_cost(shop),
            "dropped": [{"label": g.label, "profit": g.profit, "cost": g.cost} for g in dropped],
            "capped": cut_off,
            "vendor_shop": shop,
            "profit": profit,
            "vendor_total": vendor_total,
            "deposit_estimated": self.deposit_estimated,
            "confidence": self.confidence,
            "movers": mk.movers(self.scans) if self.scans else [],
            "hist": self.hist,
            "stats": self.stats,
        }
        return plan, self.info

    # ---- text drafts ------------------------------------------------------------
    def notes(self, dropped, shop) -> list:
        notes = []
        if not self.newest:
            notes.append("No AH scan yet, so there are no prices: this plan is empty on purpose.")
        elif self.invest_ok:
            notes.append(f"{self.days} days of scans: invest and hold-for-later calls are allowed.")
        else:
            notes.append(f"Cold start: {self.days} of {INVEST_MIN_DAYS} scan days, so no invest or hold-for-later calls "
                         f"yet. Confidence: {self.confidence.split(' (')[0]}.")
        acct, tail = "", ""
        if self.multi_account:
            acct = f" on account {self.basis['account'] or '?'}"
            tail = " Buy with that account's gold only: never mail gold between accounts."
        if self.gold < self.nest:
            notes.append(f"No buys: gold {util.fmt_money(self.gold)}{acct} is under the "
                         f"{util.fmt_money(self.nest)} nest egg.{tail}")
        elif dropped:
            notes.append(f"Budget: dropped {len(dropped)} buy group(s), lowest profit first, to stay under "
                         f"{util.fmt_money(self.spend_cap)}{acct}.{tail}")
        else:
            notes.append(f"Spend cap {util.fmt_money(self.spend_cap)} (gold above the {util.fmt_money(self.nest)} "
                         f"nest egg{acct}).{tail}")
        src = (f"measured from {self.cut['n']} sales" if self.cut["source"] == "measured"
               else f"config default; measured after 3 seller invoices, have {self.cut['n']}")
        notes.append(f"AH cut {self.cut['rate'] * 100:.1f}% at the {self.house} house ({src}).")
        if shop:
            items = ", ".join(f"{v['qty']} {v['name']}" for v in shop[:4])
            where = "vendor" if all(v["source"] == "vendor" for v in shop) else "vendor / Black Market"
            notes.append(f"Buy from a {where} first: {items} (about {util.fmt_money(vendor_cost(shop))}).")
        if self.silver:
            notes.append("Copper prices are off here: post prices are rounded up to whole silver.")
        if self.deposit_estimated:
            notes.append("Deposits are estimates until the addon's deposit probe runs at the AH.")
        return notes[:MAX_NOTES]

    def steps(self, lists, shop) -> list:
        """Session steps, each ending with a minute estimate (costs from the skill)."""
        if not self.newest:
            return ["Log in: the ForeverGold chat line must show this plan id (1 min)",
                    "AH: click Scan, keep the AH open until 'scan: N rows' prints; buy and post nothing yet (3 min)",
                    "Click Done to save the scan (1 min)",
                    "Run /forever-gold again for the first plan built on a scan (3 min)"]
        steps = ["Mailbox: Open All (1 min)"]
        n_buy = len(lists["buy"])
        if n_buy:
            steps.append(f"AH: click Scan, then Buy list; buy the {n_buy} green row(s) ({_mins(120 + 30 * n_buy)} min)")
        else:
            steps.append("AH: click Scan (2 min)")
        if lists["craft"]:
            crafts = sum(c["count"] for c in lists["craft"])
            pre = "Buy the vendor mats, then craft" if shop else "Craft"
            secs = 60 * len(lists["craft"]) + 4 * crafts + (60 if shop else 0)
            steps.append(f"{pre} {len(lists['craft'])} recipe(s), {crafts} in all ({_mins(secs)} min)")
        if lists["post"]:
            steps.append(f"Post {len(lists['post'])} row(s) with Auctionator, one click each "
                         f"({_mins(15 * len(lists['post']))} min)")
        if lists["vendor"]:
            steps.append(f"Vendor {len(lists['vendor'])} item(s), then click Done (2 min)")
        else:
            steps.append("Click Done (1 min)")
        return steps[:MAX_STEPS]

    def headline(self, lists, profit) -> str:
        if not self.newest:
            return NO_SCAN_ACTION[0].upper() + NO_SCAN_ACTION[1:]
        parts = []
        for k, label in (("buy", "buy"), ("craft", "craft"), ("post", "post"), ("vendor", "vendor")):
            if lists[k]:
                parts.append(f"{label} {len(lists[k])}")
        if not parts:
            return "Nothing worth doing at these prices: scan again later"
        est = profit["buy"] + profit["craft"] + profit["post"]
        return "Tonight: " + ", ".join(parts) + (f" (about +{util.fmt_money(est)})" if est > 0 else "")


def _mins(secs: float) -> int:
    return max(1, int(math.ceil(secs / 60.0)))


def _merge_buys(rows: list) -> list:
    out = {}
    order = []
    for r in rows:
        key = (r["id"], bool(r.get("flip")))
        if key in out:
            cur = out[key]
            cur["qty"] += r["qty"]
            cur["maxUnit"] = max(cur["maxUnit"], r["maxUnit"])
            cur["_profit"] = cur.get("_profit", 0) + r.get("_profit", 0)
            if r["why"] not in cur["why"]:
                cur["why"] = cur["why"] + "; " + r["why"]
        else:
            out[key] = dict(r)
            order.append(key)
    return [out[k] for k in order]


def _merge_posts(rows: list) -> list:
    out = {}
    order = []
    for r in rows:
        if r["id"] in out:
            cur = out[r["id"]]
            cur["qty"] += r["qty"]
            cur["floorUnit"] = max(cur["floorUnit"], r["floorUnit"])
            cur["startUnit"] = max(cur["startUnit"], r["startUnit"], cur["floorUnit"])
            cur["_profit"] = cur.get("_profit", 0) + r.get("_profit", 0)
            cur["why"] = cur["why"] + "; plus " + r["why"]
        else:
            out[r["id"]] = dict(r)
            order.append(r["id"])
    return [out[k] for k in order]


def _merge_simple(rows: list) -> list:
    out = {}
    order = []
    for r in rows:
        if r["id"] in out:
            continue
        out[r["id"]] = r
        order.append(r["id"])
    return [out[k] for k in order]


def build_plan(inp: PlanInputs):
    """(plan dict in draft.json shape, info dict for the brief)."""
    return _Planner(inp).build()
