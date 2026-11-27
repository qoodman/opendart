"""흑자전환 임박 점수 시험"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import turnaround as T  # noqa: E402

HIST = [(2025, "11013", dict(op_q=-300, rev_q=1000)), (2025, "11012", dict(op_q=-250, rev_q=1300)), (2025, "11014", dict(op_q=-150, rev_q=1700)),
        (2025, "11011", dict(op_q=-600, rev_q=7000)), (2026, "11013", dict(op_q=-90, rev_q=2000)), (2026, "11012", dict(op_q=-30, rev_q=2500))]
LATEST = dict(op=-120, op_q=-30, rev_q=2500, op_q_py=-250, rev_yoy=1.0, rev_q_yoy=0.92, opm=-0.04, opm_py=-0.22, gpm=0.34, gpm_py=0.25, ocf=40, cash=800, months=6, debt_ratio=0.9)


class Turnaround(unittest.TestCase):
    def test_strong_candidate(self):
        r = T.score(LATEST, HIST, cons_next_op=500)
        self.assertGreaterEqual(r["score"], 85)
        self.assertEqual(r["up"], 4)
        self.assertEqual([lab for lab, _ in r["trend"]][-2:], ["2026 1Q", "2026 2Q"])
        self.assertFalse(any("현금" in x for x in r["risk"]))

    def test_profitable_company_is_not_a_target(self):
        self.assertIsNone(T.score(dict(op=100, op_q=30, rev_q=500), [], None))

    def test_quarterly_profit_flag(self):
        r = T.score(dict(op=-80, op_q=10, rev_q=500, rev_yoy=0.8), [], None)
        self.assertTrue(r["q_profit"])
        self.assertGreaterEqual(r["parts"]["분기 흑자"], 20)

    def test_cash_runway_penalty(self):
        r = T.score(dict(op=-200, op_q=-60, rev_q=500, op_q_py=-100, rev_yoy=0.5, cash=100, ocf=-300, months=9), [], None)
        self.assertTrue(any("개월치" in x for x in r["risk"]))
        self.assertLess(r["runway"], 9)

    def test_shrinking_revenue_penalty(self):
        a = T.score(dict(op=-200, op_q=-60, rev_q=500, op_q_py=-100, rev_yoy=0.5), [], None)["score"]
        b = T.score(dict(op=-200, op_q=-60, rev_q=500, op_q_py=-100, rev_yoy=-0.1), [], None)["score"]
        self.assertGreater(a, b)


if __name__ == "__main__":
    unittest.main()
