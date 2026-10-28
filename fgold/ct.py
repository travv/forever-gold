"""Central Time (CT) formatting. Travis reads every time in CT.

zoneinfo needs a tz database, which Windows only has when the third-party
`tzdata` package is installed, so there is a built-in fallback that applies
the US DST rules in force since 2007 (second Sunday in March, 2:00 local, to
first Sunday in November, 2:00 local).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

_CST = timezone(timedelta(hours=-6), "CST")
_CDT = timezone(timedelta(hours=-5), "CDT")

try:  # pragma: no cover - depends on the platform's tz database
    from zoneinfo import ZoneInfo

    _ZONE = ZoneInfo("America/Chicago")
except Exception:  # ZoneInfoNotFoundError, ImportError
    _ZONE = None


def _nth_sunday(year: int, month: int, n: int) -> int:
    first = date(year, month, 1)
    first_sunday = 1 + (6 - first.weekday()) % 7
    return first_sunday + 7 * (n - 1)


def fallback_ct(ts: float) -> datetime:
    """CT without zoneinfo (US rules since 2007)."""
    utc = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    y = utc.year
    start = datetime(y, 3, _nth_sunday(y, 3, 2), 8, 0, tzinfo=timezone.utc)   # 2:00 CST
    end = datetime(y, 11, _nth_sunday(y, 11, 1), 7, 0, tzinfo=timezone.utc)   # 2:00 CDT
    tz = _CDT if start <= utc < end else _CST
    return utc.astimezone(tz)


def to_ct(ts: float) -> datetime:
    if _ZONE is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=_ZONE)
        except (OverflowError, OSError, ValueError):
            pass
    return fallback_ct(ts)


def ct_date(ts: float) -> date:
    return to_ct(ts).date()


def fmt_ct(ts, with_date: bool = True) -> str:
    """'Thu Nov 5, 7:00 PM CT' (or '7:00 PM CT')."""
    if not ts:
        return "never"
    d = to_ct(ts)
    hour = d.strftime("%I").lstrip("0") or "12"
    clock = f"{hour}:{d.strftime('%M')} {d.strftime('%p')} CT"
    if not with_date:
        return clock
    return f"{d.strftime('%a %b')} {d.day}, {clock}"


def stamp_ct(ts: float) -> str:
    """'YYYYMMDD-HHMM' in CT (report file names and plan ids)."""
    return to_ct(ts).strftime("%Y%m%d-%H%M")


def age_str(seconds) -> str:
    """Human age: '45 s', '12 min', '2 h 10 min', '3 d 4 h'."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        return "in the future (clock skew?)"
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


SKEW_TOLERANCE = 300  # seconds of clock difference (PC vs server) treated as "now"


def _age_phrase(seconds, suffix: str) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "age unknown"
    if s < -SKEW_TOLERANCE:
        return "dated in the future (check the PC clock)"
    if s < 60:
        return "just now"
    return f"{age_str(s)} {suffix}"


def age_old(seconds) -> str:
    """'20 min old', 'just now', or a clock warning for future timestamps."""
    return _age_phrase(seconds, "old")


def age_ago(seconds) -> str:
    """'20 min ago', 'just now', or a clock warning for future timestamps."""
    return _age_phrase(seconds, "ago")
