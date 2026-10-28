"""Text reports: probes (launch-night measurements), status (data health), price."""

from __future__ import annotations

import statistics

from . import ct, market as mk, util
from .econ import Econ
from .store import Store


def char_houses(store: Store) -> dict:
    """{char: set(houses it has scanned at)} across all markets, from the scan index ingest
    writes. A scan the index lacks (an older store) is read on its own and not kept in memory."""
    out = {}
    index = store.scan_index()
    for m in store.markets():
        known = index.get(m) if isinstance(index.get(m), dict) else {}
        for at, path in store.scan_paths(m):
            e = known.get(str(at))
            if not isinstance(e, dict):
                try:
                    s = util.read_json_gz(path)
                except (OSError, ValueError, EOFError):
                    continue
                e = {"char": s.get("char"), "house": s.get("house")}
            if e.get("char"):
                out.setdefault(e["char"], set()).add(e.get("house") or m.split("|")[-1])
    return out


def recent_scans(store: Store, market: str, n: int = None, window: int = None) -> list:
    """The newest n scans, or the scans within window seconds of the newest, without loading older ones."""
    found = store.scan_paths(market)
    if not found:
        return []
    since = found[max(0, len(found) - n)][0] if n else found[-1][0] - window
    return store.load_scans(market, since=since)


def probes_report(store: Store, cfg: dict, static, now: int) -> list:
    lines = []
    sessions = store.sessions()
    lines.append(f"Sessions ({len(sessions)}): iface / build / copperOK / read-back")
    for s in sessions[-20:]:
        lines.append(f"  {ct.fmt_ct(s.get('loginAt'))}  {s.get('char', '?')}  iface {s.get('iface', '?')}  "
                     f"build {s.get('build', '?')} ({s.get('version', '?')})  project {s.get('project', '?')}  "
                     f"copperOK {_tri(s.get('copperOK'))}  priorSessions {s.get('priorSessions', '?')}  "
                     f"plan {s.get('planId', '?')}")
    if sessions:
        zeros = sum(1 for s in sessions if s.get("priorSessions") == 0)
        lines.append(f"  read-back: priorSessions was 0 at {zeros} of {len(sessions)} logins "
                     f"(0 on every login after the first = the game is not reading SavedVariables back)")
        ifaces = sorted({s.get("iface") for s in sessions if s.get("iface")})
        if ifaces and any(not (16000 <= i < 17000) for i in ifaces):
            lines.append(f"  WARNING: interface {ifaces} is not Forever (16xxx)")
    probes = store.probes()
    log = probes.get("scanLog") or []
    lines.append(f"Scan requests (scanLog, {len(log)}):")
    waits = []
    for e in log[-15:]:
        wait = (e["gotAt"] - e["reqAt"]) if e.get("gotAt") and e.get("reqAt") else None
        if wait is not None and wait >= 0:
            waits.append(wait)
        lines.append(f"  {ct.fmt_ct(e.get('reqAt') or e.get('gotAt'))}  {e.get('result')}  "
                     f"{'wait ' + ct.age_str(wait) if wait is not None else 'no reply time'}  "
                     f"{e.get('rows', 0):,} rows  by {e.get('by')}")
    if log:
        counts = {}
        for e in log:
            counts[e.get("result")] = counts.get(e.get("result"), 0) + 1
        all_waits = [e["gotAt"] - e["reqAt"] for e in log if e.get("gotAt") and e.get("reqAt") and e["gotAt"] >= e["reqAt"]]
        med = f", median wait {ct.age_str(statistics.median(all_waits))}" if all_waits else ""
        lines.append("  results: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + med)
    lines.append("Scans stored:")
    any_scan = False
    for m in store.markets():
        for s in recent_scans(store, m, n=5)[-5:]:
            any_scan = True
            lines.append(f"  {m}  {ct.fmt_ct(s['at'])}  {s.get('rows', 0):,} rows ({s.get('priced', 0):,} priced, "
                         f"{s.get('bidOnly', 0)} bid-only, {s.get('own', 0)} own)  {len(s.get('items') or {}):,} items  "
                         f"pack {s.get('packMs', 0)} ms  by {s.get('by')}")
    if not any_scan:
        lines.append("  none yet")
    econ = Econ(cfg, static, store.ledger(), sessions, char_houses(store), probes)
    sellers = [inv for inv in econ.ledger if inv.get("type") == "seller"]
    lines.append(f"Cut rate per seller invoice ({len(sellers)}):")
    for inv in sellers[-15:]:
        bid = inv.get("bid") or 0
        rate = f"{inv.get('cut', 0) / bid * 100:.2f}%" if bid else "n/a"
        house, how = econ.invoice_house(inv)
        lines.append(f"  {ct.fmt_ct(inv.get('seenAt'))}  {inv.get('item')} x{inv.get('count')}  bid "
                     f"{util.fmt_money(bid)}  cut {util.fmt_money(inv.get('cut', 0))}  {rate}  deposit "
                     f"{util.fmt_money(inv.get('deposit', 0))}  house {house} ({how})")
    lines.append("Cut rate per house:")
    for house in ("Alliance", "Horde", "Neutral"):
        info = econ.cut_info(house)
        rates = econ.seller_rates(house)
        if rates:
            med = statistics.median(r for r, _ in rates)
            lines.append(f"  {house}: median {med * 100:.2f}% of {len(rates)} invoice(s); using "
                         f"{info['rate'] * 100:.1f}% ({info['source']})")
        else:
            lines.append(f"  {house}: no seller invoices; using {info['rate'] * 100:.1f}% (config)")
    dep = probes.get("deposit") or {}
    lines.append(f"Deposit probes ({len(dep)}):")
    for k in sorted(dep, key=lambda x: int(x) if str(x).isdigit() else 0):
        p = dep[k]
        iid = util.item_id(k)
        vals = ", ".join(f"{lab} {util.fmt_money(p[d])}" for d, lab in (("1", "2h"), ("2", "8h"), ("3", "24h")) if d in p)
        lines.append(f"  {static.name(iid)} x{p.get('qty', 1)}: {vals} ({ct.fmt_ct(p.get('at'))})")
    if not dep:
        lines.append("  none yet (the addon probes plan post rows when the AH opens)")
    return lines


def _tri(v) -> str:
    return "true" if v is True else "false" if v is False else "nil"


PLAN_SKEW = 120  # seconds: PC clock (apply time) vs server clock (loginAt)


def plan_state(applied: dict, newest) -> str:
    """'none' (no plan applied), 'match', 'unreached' (the newest session runs from a WoW root
    the apply did not write: install or fix that root, then apply again), 'pending' (the newest
    session started before the apply, so it still reports the old id: /reload loads the plan)
    or 'mismatch' (a session that started after the apply reported another id: the client did
    not load the new PlanData.lua, so fully restart WoW)."""
    pid = (applied or {}).get("id")
    if not pid:
        return "none"
    if not newest:
        return "pending"
    if newest.get("planId") == pid:
        return "match"
    roots = applied.get("roots")
    if isinstance(roots, list) and newest.get("root") and newest["root"] not in roots:
        return "unreached"
    if (newest.get("loginAt") or 0) > (applied.get("at") or 0) + PLAN_SKEW:
        return "mismatch"
    return "pending"


def status_report(store: Store, cfg: dict, static, now: int, roots=None) -> list:
    lines = []
    markets = store.markets()
    sessions = store.sessions()
    state = store.state()
    applied = state.get("applied") or {}
    newest_s = sessions[-1] if sessions else None
    pstate = plan_state(applied, newest_s)
    verdict = "Next: "
    if pstate == "unreached":
        root = newest_s.get("root")
        why = (applied.get("skipped") or {}).get(root) or "it was not written there"
        verdict += (f"plan {applied['id']} did not reach WoW root {root} ({why}): fix that, then run "
                    f"`python -m fgold plan --apply` again (a restart alone will not load it)")
    elif pstate == "mismatch":
        verdict += (f"fully quit and restart WoW (Menu > Exit Game, not /reload), then log in: a session that "
                    f"started after the apply reported plan {newest_s.get('planId')}, not {applied['id']}")
    elif not markets:
        verdict += "no scans yet: log in, open the AH, click Scan, then /reload"
    elif pstate == "pending" and newest_s:
        verdict += f"/reload in game to load plan {applied['id']} (the game last reported {newest_s.get('planId')})"
    else:
        newest_at = max((store.scan_counts(m)[2] or 0) for m in markets)
        age = now - newest_at
        if age > 12 * 3600:
            verdict += f"newest scan is {ct.age_str(age)} old: run /forever-gold, then scan in game"
        else:
            verdict += "data is fresh: run `python -m fgold brief` (or /forever-gold)"
    lines.append(verdict)
    lines.append("Markets:" if markets else "Markets: none yet")
    for m in markets:
        n, first, last = store.scan_counts(m)
        days = mk.days_of(at for at, _ in store.scan_paths(m))
        lines.append(f"  {m}: newest scan {ct.fmt_ct(last)} ({ct.age_old(now - last)}), {n} scan(s) over {days} day(s)")
    if sessions:
        lines.append(f"Sessions: {len(sessions)} seen; newest {newest_s.get('char', '?')} at {ct.fmt_ct(newest_s.get('loginAt'))} "
                     f"(priorSessions {newest_s.get('priorSessions', '?')}, copperOK {_tri(newest_s.get('copperOK'))})")
    else:
        lines.append("Sessions: none yet")
    ledger = store.ledger()
    if ledger:
        inv = max(ledger, key=lambda r: r.get("seenAt") or 0)
        lines.append(f"Newest invoice: {inv.get('type')} {inv.get('item')} x{inv.get('count')} for "
                     f"{util.fmt_money(inv.get('bid', 0))} ({ct.fmt_ct(inv.get('seenAt'))}); {len(ledger)} in the ledger")
    else:
        lines.append("Newest invoice: none yet (open the mailbox in game)")
    econ = Econ(cfg, static, ledger, sessions, char_houses(store), store.probes())
    cuts = []
    for house in ("Alliance", "Horde", "Neutral"):
        info = econ.cut_info(house)
        if info["source"] == "measured":
            cuts.append(f"{house} {info['rate'] * 100:.2f}% (measured, {info['n']} sales)")
        else:
            cuts.append(f"{house} {info['rate'] * 100:.0f}% (config, {info['n']} of 3 sales)")
    lines.append("Cut: " + "; ".join(cuts))
    if applied.get("id"):
        rep = newest_s.get("planId") if newest_s else None
        match = {"match": "matches",
                 "unreached": f"NOT IN ROOT {newest_s.get('root') if newest_s else '?'}: plan --apply did not write it "
                              f"there",
                 "mismatch": "MISMATCH: a session that started after the apply reported it; fully restart WoW, "
                             "a /reload is not enough"}.get(pstate, "differs: /reload in game to load it")
        lines.append(f"Plan: applied {applied['id']} at {ct.fmt_ct(applied.get('at'))}; game reported "
                     f"{rep or 'nothing yet'} ({match})")
    else:
        lines.append(f"Plan: none applied yet; game reported {newest_s.get('planId') if newest_s else 'nothing yet'}")
    if state.get("last_ingest"):
        lines.append(f"Last ingest: {ct.fmt_ct(state['last_ingest'])} ({len(state.get('seen') or {})} file versions archived)")
    if roots is not None:
        for r in roots:
            lines.append(f"Root {r.name}: {r.raw} ({'found' if r.exists else 'NOT FOUND'}"
                         f"{', addon installed' if r.exists and r.addon_dir.is_dir() else ''})")
    return lines


def price_report(store: Store, static, query: str, now: int) -> tuple:
    """(lines, exit_code) for `price <name or itemID>`."""
    q = str(query).strip()
    ids = []
    if q.isascii() and q.isdigit():
        ids = [int(q)]
    else:
        ids = static.find(q)
    if not ids:
        return [f"no item matches {q!r}. Fix: check the spelling, or use the item id"], 1
    lines = []
    shown = 0
    markets = store.markets()
    for iid in ids[:5]:
        sell = static.sell(iid)
        head = f"{static.name(iid)} ({iid}): vendor {util.fmt_money(sell)}"
        cap = static.cap(iid)
        if cap:
            head += f", Black Market cap {util.fmt_money(cap)} (unverified)"
        lines.append(head)
        found = False
        for m in markets:
            scans = recent_scans(store, m, window=mk.HISTORY_WINDOW)
            if not scans:
                continue
            newest = scans[-1]
            st = mk.scan_stats(newest).get(iid)
            if not st:
                continue
            found = True
            h = mk.history(scans).get(iid) or {}
            med3 = util.fmt_money(h["med3"]) if h.get("med3") else "n/a"
            med7 = util.fmt_money(h["med7"]) if h.get("med7") else "n/a"
            lines.append(f"  {m}: min {util.fmt_money(st['min'])}, mkt {util.fmt_money(st['mkt'])}, "
                         f"q {st['q']:,} ({st['n']} auctions), p50 {util.fmt_money(st['p50'])}; "
                         f"scan {ct.age_old(now - newest['at'])}; 3-day median {med3}, 7-day median {med7}")
        if not found:
            lines.append("  not in any stored scan" if markets else "  no scans yet")
        shown += 1
    if len(ids) > 5:
        lines.append(f"(+{len(ids) - 5} more matches; be more specific)")
    return lines, 0
