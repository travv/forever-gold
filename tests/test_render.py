"""render: next action line, 5-line summary, in-game minutes, brief sections."""

import unittest

import test_support as ts
from fgold import render
from fgold.planner import NO_SCAN_ACTION

EMPTY_PLAN = {"buy": [], "craft": [], "post": [], "hold": [], "vendor": [], "steps": [], "notes": [], "headline": "",
              "budget": {"spendCap": 0, "principal": 5000000}}
SCAN = {"at": ts.SCAN_AT, "rows": 162}


def ctx(plan=None, info=None, roots=None, **kw):
    info = info if info is not None else {"newest": SCAN, "market": ts.MARKET, "days": 1, "confidence": "low (one scan)",
                                          "gold": 0, "nest": 5000000, "profit": {}, "chars": []}
    roots = roots if roots is not None else [("pc", "C:/WoW/_forever_", True, True)]
    return render.BriefContext(plan=plan or dict(EMPTY_PLAN), info=info, static=ts.load_static(), now=ts.NOW,
                               roots=roots, **kw)


class TestNextAction(unittest.TestCase):
    def test_normal(self):
        plan = dict(EMPTY_PLAN, buy=[{"id": 1, "name": "x", "qty": 1, "maxUnit": 1}],
                    post=[{"id": 2, "name": "y", "qty": 1, "floorUnit": 1, "startUnit": 1, "duration": 1}])
        line = render.next_action(ctx(plan))
        self.assertEqual(line, "Next: `python -m fgold plan --apply`, then log in: Mailbox: Open All, AH: Scan, "
                               "Buy list, post (about 6 min in game)")

    def test_no_scans(self):
        self.assertEqual(render.next_action(ctx(info={})), "Next: " + NO_SCAN_ACTION)

    def test_setup_comes_first(self):
        line = render.next_action(ctx(info={}, roots=[]))
        self.assertTrue(line.startswith("Next: run `python -m fgold doctor --write`"))
        self.assertTrue(line.endswith(NO_SCAN_ACTION))
        line = render.next_action(ctx(roots=[("pc", "x", True, False)]))
        self.assertTrue(line.startswith("Next: run `python -m fgold install`, then fully restart WoW"))
        self.assertIn("plan --apply", line)


class TestMinutesAndSummary(unittest.TestCase):
    def test_ingame_minutes(self):
        self.assertEqual(render.ingame_minutes(EMPTY_PLAN), 5)
        busy = dict(EMPTY_PLAN, buy=[{}] * 4, craft=[{"count": 15}], post=[{}] * 8, vendor=[{}])
        # 300 base + 120 buys + 60 + 60 crafts + 120 posts + 60 vendor = 720 s
        self.assertEqual(render.ingame_minutes(busy), 12)

    def test_summary_has_five_lines(self):
        lines = render.summary_lines(ctx())
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].startswith("market us|Forever|Alliance: scan 20 min old, 162 rows"))
        self.assertIn("confidence low (one scan); cold start: 1 of 3 scan days", lines[0])
        empty = render.summary_lines(ctx(info={"gold": 0, "nest": 5000000}))
        self.assertEqual(len(empty), 5)
        self.assertEqual(empty[0], NO_SCAN_ACTION)

    def test_table_escapes_pipes(self):
        rows = render._table(["Item", "Qty#"], [["a|b", 3]])
        self.assertEqual(rows, ["| Item | Qty |", "|---|---:|", "| a/b | 3 |"])
        self.assertEqual(render._table(["Item"], []), ["(none)"])


class TestBriefSections(unittest.TestCase):
    def test_sections_and_empty_reads(self):
        text = render.render_brief(ctx())
        order = ["## Next action", "## Data health", "## Buy", "## Post", "## Craft", "## Hold / Vendor",
                 "## Nest egg", "## Reads"]
        pos = [text.index(h) for h in order]
        self.assertEqual(pos, sorted(pos))
        self.assertTrue(text.rstrip().endswith("## Reads"))
        self.assertNotIn("## Movers", text)
        self.assertEqual(text.splitlines()[0], render.next_action(ctx()))

    def test_nest_egg_lists_gold_per_account(self):
        info = {"newest": SCAN, "market": ts.MARKET, "days": 1, "confidence": "low (one scan)", "gold": 100000,
                "nest": 5000000, "profit": {}, "chars": ["Bankalt-Forever", "Main-Forever"], "spend_account": "1234#2",
                "accounts": [{"account": "1234#1", "gold": 9000000, "chars": ["Main-Forever"]},
                             {"account": "1234#2", "gold": 100000, "chars": ["Bankalt-Forever"]}]}
        text = render.render_brief(ctx(info=info))
        self.assertIn("- Gold on this market: 910g on 2 WoW accounts: 1234#1 900g (Main-Forever); "
                      "1234#2 10g (Bankalt-Forever)", text)
        self.assertIn("uses account 1234#2 only", text)
        self.assertIn("never mail gold across accounts to fund this list", text)
        one = dict(info, accounts=info["accounts"][:1], chars=["Main-Forever"])
        self.assertNotIn("across accounts", render.render_brief(ctx(info=one)))

    def test_movers_section_when_history(self):
        info = {"newest": SCAN, "market": ts.MARKET, "days": 2, "confidence": "medium", "gold": 0, "nest": 0,
                "profit": {}, "chars": [], "movers": [{"id": 2447, "mkt": 24, "base": 12, "change": 1.0, "q": 1841}]}
        text = render.render_brief(ctx(info=info))
        self.assertIn("## Movers", text)
        self.assertIn("| Peacebloom | 24c | 12c | +100% | 1841 |", text)
        self.assertLess(text.index("## Movers"), text.index("## Nest egg"))


class TestReadbackCheck(unittest.TestCase):
    """Every /reload (Done) starts a session, so read-back is judged on the first login with the plan."""

    APPLIED = {"id": "P", "at": 100, "expectPriorSessions": 2}

    @staticmethod
    def sessions(*pairs):
        return [{"planId": pid, "priorSessions": prior, "loginAt": 50 * i, "char": "Travv-Forever"}
                for i, (pid, prior) in enumerate(pairs, 1)]

    def test_done_reload_is_not_a_mismatch(self):
        s = self.sessions(("old", 1), ("P", 2), ("P", 3))
        self.assertEqual(render.readback_check(s, self.APPLIED),
                         "first login on plan P: priorSessions 2, plan expected 2 or more: OK")
        text = render.render_brief(ctx(sessions=s, applied=self.APPLIED))
        self.assertIn("read-back priorSessions 3 (first login on plan P: priorSessions 2, plan expected 2 or more: OK)",
                      text)

    def test_file_not_read_back(self):
        s = self.sessions(("old", 1), ("P", 0), ("P", 0))
        self.assertTrue(render.readback_check(s, self.APPLIED).endswith(
            "priorSessions 0, plan expected 2 or more: NOT READ BACK (known beta issue, not an error: "
            "fgold keeps every copy)"))

    def test_each_account_is_judged_against_its_own_file(self):
        # The PC account's file had 7 sessions at the apply, the VM account's 3. The first VM login on
        # the plan reports 3: read back fine, though it is below the plan-wide 7 (the newest file).
        applied = {"id": "P", "at": 100, "expectPriorSessions": 7, "expect": {"pc/A#1": 7, "vm/B#1": 3}}
        s = [{"planId": "P", "priorSessions": 7, "loginAt": 200, "root": "pc", "account": "A#1"},
             {"planId": "P", "priorSessions": 8, "loginAt": 250, "root": "pc", "account": "A#1"},
             {"planId": "P", "priorSessions": 3, "loginAt": 300, "root": "vm", "account": "B#1"}]
        text = render.readback_check(s, applied)
        self.assertNotIn("NOT READ BACK", text)
        self.assertEqual(text, "first login on plan P: pc/A#1 priorSessions 7, expected 7 or more: OK; "
                               "vm/B#1 priorSessions 3, expected 3 or more: OK")
        s[2]["priorSessions"] = 0
        self.assertIn("vm/B#1 priorSessions 0, expected 3 or more: NOT READ BACK", render.readback_check(s, applied))
        # an account that had no file at the apply expects nothing
        new = dict(applied, expect={"pc/A#1": 7})
        self.assertIn("vm/B#1 priorSessions 0, expected 0 or more: OK", render.readback_check(s, new))
        # older applied records have only the plan-wide number
        self.assertIn("NOT READ BACK", render.readback_check(s[2:], {"id": "P", "expectPriorSessions": 7}))

    def test_no_verdict_before_the_game_loads_the_plan(self):
        self.assertEqual(render.readback_check(self.sessions(("old", 1)), self.APPLIED), "")
        self.assertEqual(render.readback_check(self.sessions(("P", 2)), {}), "")
        self.assertEqual(render.readback_check(self.sessions(("P", 2)), {"id": "P"}), "")
        text = render.render_brief(ctx(sessions=self.sessions(("old", 1)), applied=self.APPLIED))
        self.assertIn("plan old; read-back priorSessions 1\n", text)


if __name__ == "__main__":
    unittest.main()
