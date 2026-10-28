"""ct: Central Time display, the no-tzdata fallback, ages."""

import unittest
from datetime import datetime, timedelta, timezone

import test_support as ts
from fgold import ct


class TestCentralTime(unittest.TestCase):
    def test_fallback_dst_boundaries_2026(self):
        start = int(datetime(2026, 3, 8, 8, 0, tzinfo=timezone.utc).timestamp())   # 2:00 CST -> 3:00 CDT
        end = int(datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc).timestamp())    # 2:00 CDT -> 1:00 CST
        self.assertEqual(ct.fallback_ct(start - 1).utcoffset(), timedelta(hours=-6))
        self.assertEqual(ct.fallback_ct(start).utcoffset(), timedelta(hours=-5))
        self.assertEqual(ct.fallback_ct(start).hour, 3)
        self.assertEqual(ct.fallback_ct(end - 1).utcoffset(), timedelta(hours=-5))
        self.assertEqual(ct.fallback_ct(end).utcoffset(), timedelta(hours=-6))
        self.assertEqual(ct.fallback_ct(end).hour, 1)

    def test_fallback_matches_zoneinfo(self):
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo("America/Chicago")
        except Exception:
            self.skipTest("no tz database here")
        t = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
        stop = int(datetime(2028, 1, 1, tzinfo=timezone.utc).timestamp())
        while t < stop:
            a = ct.fallback_ct(t)
            b = datetime.fromtimestamp(t, tz=zone)
            self.assertEqual((a.utcoffset(), a.replace(tzinfo=None)), (b.utcoffset(), b.replace(tzinfo=None)), t)
            t += 3 * 3600 + 17 * 60

    def test_formats(self):
        self.assertEqual(ct.stamp_ct(ts.NOW), "20261105-1930")
        self.assertEqual(ct.fmt_ct(ts.NOW), "Thu Nov 5, 7:30 PM CT")
        self.assertEqual(ct.fmt_ct(ts.NOW, with_date=False), "7:30 PM CT")
        self.assertEqual(ct.fmt_ct(1793835000), "Wed Nov 4, 5:30 PM CT")
        self.assertEqual(ct.fmt_ct(0), "never")
        self.assertEqual(ct.fmt_ct(None), "never")
        noon = int(datetime(2026, 7, 4, 17, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(ct.fmt_ct(noon), "Sat Jul 4, 12:00 PM CT")

    def test_ct_date(self):
        self.assertEqual(str(ct.ct_date(1793856600)), "2026-11-04")   # 11:30 PM CST = Nov 5 UTC

    def test_age_str(self):
        self.assertEqual(ct.age_str(45), "45 s")
        self.assertEqual(ct.age_str(120), "2 min")
        self.assertEqual(ct.age_str(3600), "1 h")
        self.assertEqual(ct.age_str(7800), "2 h 10 min")
        self.assertEqual(ct.age_str(3 * 86400 + 4 * 3600), "3 d 4 h")
        self.assertEqual(ct.age_str(3 * 86400), "3 d")
        self.assertEqual(ct.age_str(-5), "in the future (clock skew?)")
        self.assertEqual(ct.age_str("x"), "?")

    def test_age_phrases(self):
        self.assertEqual(ct.age_old(1200), "20 min old")
        self.assertEqual(ct.age_ago(7800), "2 h 10 min ago")
        self.assertEqual(ct.age_old(30), "just now")
        self.assertEqual(ct.age_old(-120), "just now", "small PC/server clock differences are fine")
        self.assertEqual(ct.age_old(-86400), "dated in the future (check the PC clock)")
        self.assertEqual(ct.age_ago(None), "age unknown")


if __name__ == "__main__":
    unittest.main()
