"""Market math on scans (design.md 3.2 market.py).

Per item in a scan:
    q    units listed
    n    auctions
    min  cheapest unit price
    mkt  unit-weighted mean of the cheapest 25% of units (at least 1 unit,
         never below min), so one silly listing cannot anchor it
    p50  unit-weighted median (the lower median: first price where the
         cumulative units reach half of q)
History: medians of mkt over the last 3 and 7 days across scans of the same
market, measured back from the newest scan.
"""

from __future__ import annotations

import statistics

from . import ct, util

DAY = 86400
MKT_SHARE = 0.25
HISTORY_WINDOW = 7 * DAY   # history and movers never look further back than this from the newest scan


def item_stats(ladder, n=None) -> dict:
    """ladder = [[unit, qty], ...] (any order). Returns {n, q, min, mkt, p50}."""
    pts = sorted((int(u), int(q)) for u, q in ladder if int(q) > 0)
    if not pts:
        return None
    q_total = sum(q for _, q in pts)
    lo = pts[0][0]
    want = max(1.0, MKT_SHARE * q_total)
    left = want
    cost = 0.0
    for u, q in pts:
        take = min(float(q), left)
        cost += take * u
        left -= take
        if left <= 0:
            break
    mkt = max(lo, util.money_round(cost / want))
    half = q_total / 2.0
    cum = 0
    p50 = pts[-1][0]
    for u, q in pts:
        cum += q
        if cum >= half:
            p50 = u
            break
    return {"n": int(n) if n is not None else len(pts), "q": q_total, "min": lo, "mkt": mkt, "p50": p50}


def scan_stats(scan: dict) -> dict:
    """{itemID(int): stats} for a stored scan."""
    out = {}
    for k, e in (scan.get("items") or {}).items():
        iid = util.item_id(k)
        if iid is None or not isinstance(e, dict):
            continue
        st = item_stats(e.get("ladder") or [], e.get("n"))
        if st:
            out[iid] = st
    return out


def units_at_or_below(ladder, max_unit) -> int:
    return sum(int(q) for u, q in ladder if int(u) <= max_unit)


def units_below(ladder, limit) -> int:
    return sum(int(q) for u, q in ladder if int(u) < limit)


def fill_cost(ladder, qty: int):
    """(cost, units) buying qty units cheapest first (units may be < qty)."""
    left = int(qty)
    cost = 0
    got = 0
    for u, q in sorted((int(u), int(q)) for u, q in ladder):
        take = min(q, left)
        cost += take * u
        got += take
        left -= take
        if left <= 0:
            break
    return cost, got


def _median_int(values):
    if not values:
        return None
    return util.money_round(statistics.median(values))


def history(scans: list, ref_at=None) -> dict:
    """{itemID: {"med3", "n3", "med7", "n7"}} from scans (any order) of one market.

    A median is only reported when at least 2 scans in the window list the item."""
    if not scans:
        return {}
    if ref_at is None:
        ref_at = max(s.get("at", 0) for s in scans)
    per3, per7 = {}, {}
    for s in scans:
        at = s.get("at", 0)
        age = ref_at - at
        if age < 0 or age > 7 * DAY:
            continue
        for iid, st in scan_stats(s).items():
            per7.setdefault(iid, []).append(st["mkt"])
            if age <= 3 * DAY:
                per3.setdefault(iid, []).append(st["mkt"])
    out = {}
    for iid, vals in per7.items():
        h = {"n7": len(vals), "med7": _median_int(vals) if len(vals) >= 2 else None}
        v3 = per3.get(iid, [])
        h["n3"] = len(v3)
        h["med3"] = _median_int(v3) if len(v3) >= 2 else None
        out[iid] = h
    return out


def scan_days(scans: list) -> int:
    """Distinct CT calendar days that have at least one scan."""
    return days_of(s.get("at") for s in scans)


def days_of(ats) -> int:
    """Distinct CT calendar days among scan times (file names hold them, so no scan is loaded)."""
    return len({ct.ct_date(at) for at in ats if at})


def movers(scans: list, min_change: float = 0.2, min_units: int = 5, limit: int = 5) -> list:
    """Items whose newest mkt moved >= min_change vs the median of earlier
    scans in the last 7 days (scans >= 12 h older than the newest only)."""
    if len(scans) < 2:
        return []
    scans = sorted(scans, key=lambda s: s.get("at", 0))
    newest = scans[-1]
    ref_at = newest.get("at", 0)
    earlier = [s for s in scans[:-1] if 12 * 3600 <= ref_at - s.get("at", 0) <= 7 * DAY]
    if not earlier:
        return []
    prev = {}
    for s in earlier:
        for iid, st in scan_stats(s).items():
            prev.setdefault(iid, []).append(st["mkt"])
    out = []
    for iid, st in scan_stats(newest).items():
        vals = prev.get(iid)
        if not vals or st["q"] < min_units:
            continue
        base = statistics.median(vals)
        if base <= 0:
            continue
        change = (st["mkt"] - base) / base
        if abs(change) >= min_change:
            out.append({"id": iid, "mkt": st["mkt"], "base": util.money_round(base), "change": change, "q": st["q"]})
    out.sort(key=lambda m: (-abs(m["change"]), m["id"]))
    return out[:limit]
