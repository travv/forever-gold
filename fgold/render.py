"""The brief markdown (design.md 3.2 render.py, 3.4).

Sections: Next action, Data health, Buy, Post, Craft, Hold/Vendor, Movers
(when there is history), Nest egg, and an empty "## Reads" for Claude.
The first line of the file is the next action.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ct, util
from .econ import DURATION_LABEL
from .planner import NO_SCAN_ACTION


@dataclass
class BriefContext:
    plan: dict
    info: dict
    static: object
    now: int
    roots: list = field(default_factory=list)          # [(name, raw path, exists, addon_installed)]
    sessions: list = field(default_factory=list)
    ingest: object = None                             # IngestReport or None
    markets: list = field(default_factory=list)       # [(market, count, first_at, last_at)]
    applied: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def ingame_minutes(plan: dict) -> int:
    """Rough in-game minutes for tonight's loop: log in 1, mailbox 1, scan 2,
    30 s per buy row, 1 min plus 4 s per craft batch, 15 s per post row,
    vendor 1, Done 1."""
    secs = 60 + 60 + 120 + 60
    secs += 30 * len(plan.get("buy") or [])
    secs += sum(60 + 4 * int(c.get("count", 0)) for c in plan.get("craft") or [])
    secs += 15 * len(plan.get("post") or [])
    if plan.get("vendor"):
        secs += 60
    return max(3, (secs + 59) // 60)


def next_action(ctx: BriefContext) -> str:
    pre = []
    if not ctx.roots:
        pre.append("run `python -m fgold doctor --write` to find your WoW install")
    elif not any(r[3] for r in ctx.roots):
        pre.append("run `python -m fgold install`, then fully restart WoW (not /reload)")
    if not ctx.info.get("newest"):
        main = NO_SCAN_ACTION
        if pre:
            return "Next: " + "; ".join(pre) + "; then " + main
        return "Next: " + main
    plan = ctx.plan
    parts = ["Mailbox: Open All", "AH: Scan"]
    if plan.get("buy"):
        parts.append("Buy list")
    if plan.get("craft"):
        parts.append("craft")
    if plan.get("post"):
        parts.append("post")
    main = (f"`python -m fgold plan --apply`, then log in: " + ", ".join(parts) +
            f" (about {ingame_minutes(plan)} min in game)")
    if pre:
        return "Next: " + "; ".join(pre) + "; then " + main
    return "Next: " + main


def _table(header: list, rows: list) -> list:
    if not rows:
        return ["(none)"]
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---:" if h.endswith("#") else "---" for h in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    out[0] = out[0].replace("#", "")
    return out


def readback_check(sessions: list, applied: dict) -> str:
    """Read-back verdict for the applied plan; "" until a session reports its id.

    Every /reload (the Done button) fires PLAYER_LOGIN and adds a session, so
    the newest session usually counts more prior sessions than the plan
    expected. Judge the first session that loaded the applied plan instead:
    the file was read back when its priorSessions >= the session count its
    own account's file had at the apply (applied["expect"]; each WoW account
    has its own SavedVariables file). Older applied records only have the
    plan-wide expectPriorSessions, which is the newest file's count."""
    applied = applied or {}
    pid = applied.get("id")
    if not pid:
        return ""
    expect = applied.get("expect") if isinstance(applied.get("expect"), dict) else None
    fallback = util.as_int(applied.get("expectPriorSessions"))
    firsts = {}
    for x in sessions:
        if isinstance(x, dict) and x.get("planId") == pid:
            key = f"{x['root']}/{x['account']}" if x.get("root") and x.get("account") else ""
            firsts.setdefault(key, x)
    found = []
    for key, first in firsts.items():
        prior = util.as_int(first.get("priorSessions"))
        if expect is not None and key:
            exp = util.as_int(expect.get(key), 0)   # no file for that account at the apply: nothing to read back
        else:
            exp = fallback
        if prior is None or exp is None:
            continue
        verdict = "OK" if prior >= exp else "NOT READ BACK (known beta issue, not an error: fgold keeps every copy)"
        found.append((key, prior, exp, verdict))
    if not found:
        return ""
    if len(found) == 1:
        _, prior, exp, verdict = found[0]
        return f"first login on plan {pid}: priorSessions {prior}, plan expected {exp} or more: {verdict}"
    return f"first login on plan {pid}: " + "; ".join(
        f"{key or '?'} priorSessions {prior}, expected {exp} or more: {verdict}" for key, prior, exp, verdict in found)


def _confidence(info: dict) -> str:
    if not info.get("newest"):
        return "none (no scans)"
    text = info.get("confidence") or "low"
    if info.get("days", 0) < 3:
        text += f"; cold start: {info.get('days', 0)} of 3 scan days"
    return text


def summary_lines(ctx: BriefContext) -> list:
    """The 5-line summary printed by `brief`."""
    info, plan = ctx.info, ctx.plan
    if not info.get("newest"):
        lines = [
            NO_SCAN_ACTION,
            f"WoW roots: {len(ctx.roots)} configured, {sum(1 for r in ctx.roots if r[2])} found, "
            f"{sum(1 for r in ctx.roots if r[3])} with the addon installed",
            f"sessions seen: {len(ctx.sessions)}",
            f"gold: {util.fmt_money(info.get('gold', 0))} vs nest egg {util.fmt_money(info.get('nest', 0))}",
            "draft plan is empty until the first scan",
        ]
        return lines[:5]
    newest = info["newest"]
    profit = info.get("profit") or {}
    lines = [
        f"market {info['market']}: scan {ct.age_old(ctx.now - newest['at'])}, {newest.get('rows', 0):,} rows; "
        f"confidence {_confidence(info)}",
        f"buy: {len(plan['buy'])} row(s), up to {util.fmt_money(info.get('buy_total', 0))} of "
        f"{util.fmt_money(plan['budget']['spendCap'])} spend cap",
        f"post: {len(plan['post'])} row(s), about +{util.fmt_money(profit.get('post', 0))} over vendoring",
        f"craft: {len(plan['craft'])} recipe(s), about +{util.fmt_money(profit.get('craft', 0))}",
        f"hold: {len(plan['hold'])}, vendor: {len(plan['vendor'])} (about {util.fmt_money(info.get('vendor_total', 0))})",
    ]
    return lines[:5]


def render_brief(ctx: BriefContext) -> str:
    plan, info, st = ctx.plan, ctx.info, ctx.static
    out = [next_action(ctx), ""]
    out.append(f"# Gold brief: {ct.fmt_ct(ctx.now)}")
    out.append("")
    newest = info.get("newest")
    if newest:
        out.append(f"Market `{info['market']}`, newest scan {ct.fmt_ct(newest['at'])} "
                   f"({ct.age_old(ctx.now - newest['at'])}, {newest.get('rows', 0):,} rows). "
                   f"Confidence: {_confidence(info)}.")
    else:
        out.append(f"{NO_SCAN_ACTION[0].upper() + NO_SCAN_ACTION[1:]}.")
    out.append("")

    out.append("## Next action")
    out.append(f"Headline draft: {plan.get('headline', '')}")
    for i, s in enumerate(plan.get("steps") or [], 1):
        out.append(f"{i}. {s}")
    if plan.get("notes"):
        out.append("")
        out.append("Plan notes (draft):")
        for n in plan["notes"]:
            out.append(f"- {n}")
    out.append("")

    out.append("## Data health")
    if ctx.roots:
        for name, raw, exists, installed in ctx.roots:
            if not exists:
                out.append(f"- Root `{name}`: {raw}: NOT FOUND. Fix: correct the path in config.local.json")
            elif installed:
                out.append(f"- Root `{name}`: {raw}: found, addon installed")
            else:
                out.append(f"- Root `{name}`: {raw}: found, addon not installed. Fix: `python -m fgold install`")
    else:
        out.append("- No WoW roots configured. Fix: `python -m fgold doctor --write`.")
    if ctx.ingest is not None:
        out.append(f"- {ctx.ingest.summary()}")
        for e in ctx.ingest.errors[:3]:
            out.append(f"- Ingest error: {e}")
        for w in ctx.ingest.warnings[:3]:
            out.append(f"- Ingest warning: {w}")
    if ctx.markets:
        for m, n, first, last in ctx.markets:
            out.append(f"- Scans `{m}`: {n}, newest {ct.fmt_ct(last)} ({ct.age_old(ctx.now - last)})")
    else:
        out.append("- Scans: none yet")
    if newest:
        out.append(f"- Scan days for this market: {info.get('days', 0)} (invest calls need 3)")
    if ctx.sessions:
        s = ctx.sessions[-1]
        prior = s.get("priorSessions")
        rb = f"; read-back priorSessions {prior}" if prior is not None else ""
        check = readback_check(ctx.sessions, ctx.applied)
        if check:
            rb += f" ({check})"
        out.append(f"- Sessions: {len(ctx.sessions)} seen; newest {s.get('char', '?')} at {ct.fmt_ct(s.get('loginAt'))}, "
                   f"plan {s.get('planId', '?')}{rb}")
        cok = info.get("copper_ok")
        out.append(f"- Copper prices: {'on' if cok else 'OFF (post prices round up to whole silver)' if cok is False else 'unknown'}")
    else:
        out.append("- Sessions: none yet (log in once with the addon installed)")
    cut = info.get("cut") or {}
    if cut:
        src = f"measured from {cut['n']} sales" if cut.get("source") == "measured" else \
            f"config default until 3 seller invoices; have {cut.get('n', 0)}"
        out.append(f"- AH cut at the {info.get('house')} house: {cut['rate'] * 100:.1f}% ({src})")
    meta = st.meta or {}
    if st.missing:
        out.append(f"- Static data MISSING ({', '.join(st.missing)}). Fix: `python tools/fetch_static.py`")
    elif meta.get("fallback"):
        out.append("- Static data is the FALLBACK set. Fix: `python tools/fetch_static.py` when online")
    else:
        out.append(f"- Static data: build {meta.get('build', '?')} ({len(st.items):,} items, {len(st.recipes):,} recipes)")
    for w in ctx.warnings:
        out.append(f"- Warning: {w}")
    out.append("")

    out.append("## Buy")
    rows = []
    for r in plan.get("buy") or []:
        rows.append([r["name"], r["qty"], util.fmt_money(r["maxUnit"]), util.fmt_money(r["qty"] * r["maxUnit"]),
                     "flip" if r.get("flip") else "", r.get("why", "")])
    out += _table(["Item", "Qty#", "Max each#", "Max total#", "Type", "Why"], rows)
    if info.get("dropped"):
        out.append("")
        out.append("Dropped for budget (lowest profit first): " + "; ".join(
            f"{d['label']} (+{util.fmt_money(d['profit'])}, needs {util.fmt_money(d['cost'])})" for d in info["dropped"][:5]))
    out.append("")

    out.append("## Post")
    rows = []
    for r in plan.get("post") or []:
        rows.append([r["name"], r["qty"], util.fmt_money(r["floorUnit"]), util.fmt_money(r["startUnit"]),
                     DURATION_LABEL.get(r["duration"], "?"), r.get("why", "")])
    out += _table(["Item", "Qty#", "Floor#", "Start#", "Time", "Why"], rows)
    out.append("")

    out.append("## Craft")
    rows = []
    for r in plan.get("craft") or []:
        rows.append([r["name"], r["count"], util.fmt_money(r["profitEach"]), r.get("why", "")])
    out += _table(["Recipe", "Count#", "Profit each#", "Why"], rows)
    shop = info.get("vendor_shop") or []
    if shop:
        out.append("")
        out.append("Buy from vendors first: " + ", ".join(
            f"{v['qty']} {v['name']} ({util.fmt_money(v['unit'])} each, {v['source']})" for v in shop))
    out.append("")

    out.append("## Hold / Vendor")
    holds = plan.get("hold") or []
    vends = plan.get("vendor") or []
    if not holds and not vends:
        out.append("(none)")
    for r in holds:
        out.append(f"- Hold {r['name']}: {r.get('why', '')}")
    for r in vends:
        out.append(f"- Vendor {r['name']}: {r.get('why', '')}")
    out.append("")

    movers = info.get("movers") or []
    if movers:
        out.append("## Movers")
        rows = [[st.name(m["id"]), util.fmt_money(m["mkt"]), util.fmt_money(m["base"]), f"{m['change'] * 100:+.0f}%",
                 m["q"]] for m in movers]
        out += _table(["Item", "Now#", "Earlier median#", "Change#", "Units#"], rows)
        out.append("")

    out.append("## Nest egg")
    chars = info.get("chars") or []
    accounts = info.get("accounts") or []
    if len(accounts) > 1:
        parts = [f"{a['account'] or '?'} {util.fmt_money(a['gold'])} ({', '.join(a['chars'][:4])})" for a in accounts]
        out.append(f"- Gold on this market: {util.fmt_money(sum(a['gold'] for a in accounts))} on {len(accounts)} WoW "
                   f"accounts: " + "; ".join(parts))
        out.append(f"- The spend cap uses account {info.get('spend_account') or '?'} only (newest login on this "
                   f"market, {util.fmt_money(info.get('gold', 0))}): buys use only gold already on that account; "
                   f"never mail gold across accounts to fund this list")
    else:
        out.append(f"- Gold on this market: {util.fmt_money(info.get('gold', 0))} across {len(chars)} character(s)"
                   + (f" ({', '.join(chars[:4])})" if chars else ""))
    out.append(f"- Nest egg (principal): {util.fmt_money(info.get('nest', 0))}; "
               f"spend cap tonight: {util.fmt_money(plan['budget']['spendCap'])}")
    if info.get("gold", 0) < info.get("nest", 0):
        out.append(f"- {util.fmt_money(info.get('nest', 0) - info.get('gold', 0))} to go before any buys (only spend profit)")
    out.append("")

    out.append("## Reads")
    out.append("")
    return "\n".join(out)
