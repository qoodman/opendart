"""지분분쟁 판정 회귀 시험: 사용자가 짚어 준 실제 사례를 그대로 시험으로 남겨 두고, 규칙을 고칠 때 되돌아가지 않게 한다.
실행: python -m unittest tests.test_dispute -v   (네트워크·DART 키 필요 없음)"""
import datetime as dt
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dispute as D  # noqa: E402

TODAY = dt.date(2026, 11, 26)


def holder(name, pct, tp="일반", **kw):
    h = dict(name=name, pct=pct, tp=tp, date="20261001", resn="", chg=0.0, activist=D._is_activist(name), shift="", grow=0.0, first_pct=pct)
    h.update(kw)
    return h


class Classify(unittest.TestCase):
    def test_officer_report_is_not_proxy(self):          # 임원·주요주주 소유보고가 '표 대결'로 잡히던 오류
        self.assertIsNone(D.classify("임원ㆍ주요주주특정증권등소유상황보고서", "이창규", "아모레퍼시픽", "D003"))

    def test_patent_injunction_is_not_dispute(self):      # 대기업 특허 가처분
        self.assertIsNone(D.classify("소송등의제기ㆍ신청(특허 가처분)", "", "삼성전자"))

    def test_governance_injunction_is_dispute(self):
        self.assertEqual(D.classify("소송등의제기ㆍ신청(주주총회 의결권행사금지 가처분)", "", "한진칼")[0], "injunction")

    def test_own_tender_is_not_dispute(self):             # 회사가 자기 주식을 사는 공개매수
        self.assertEqual(D.classify("주식등의공개매수신고서", "삼성전자", "삼성전자")[0], "defense")
        self.assertEqual(D.classify("주식등의공개매수신고서", "호반건설", "한진칼")[0], "tender")

    def test_proxy_by_title(self):
        self.assertEqual(D.classify("의결권대리행사권유참고서류", "호반건설", "한진칼")[0], "proxy_ext")
        self.assertEqual(D.classify("의결권대리행사권유참고서류", "한진칼", "한진칼")[0], "proxy_co")


class Names(unittest.TestCase):
    def test_insider_spelling(self):
        self.assertTrue(D.is_insider("주식회사 한진칼", {"(주)한진칼", "조원태"}))
        self.assertFalse(D.is_insider("KCGI", {"(주)한진칼"}))

    def test_group_affiliate(self):
        self.assertTrue(D.is_group_affiliate("삼성생명보험", "삼성전자", ["삼성물산"]))
        self.assertTrue(D.is_group_affiliate("아모레퍼시픽홀딩스", "아모레퍼시픽"))
        self.assertFalse(D.is_group_affiliate("KCGI", "한진칼"))

    def test_neutral_and_activist(self):
        for n in ("국민연금공단", "한국산업은행", "블랙록", "Norges Bank", "노르웨이중앙은행"):
            self.assertTrue(D.is_neutral_inst(n), n)
        for n in ("KCGI", "얼라인파트너스", "미리캐피탈자산운용", "Elliott Associates", "트러스톤자산운용"):
            self.assertFalse(D.is_neutral_inst(n), n)
            self.assertTrue(D._is_activist(n), n)


class Structure(unittest.TestCase):
    def test_public_institution_alone_is_not_a_dispute(self):      # 아모레: 최대주주 44.8%, 국민연금 10.5%
        sc = D.structure_score(44.8, [holder("국민연금공단", 10.5, "약식")], 0.5)
        self.assertEqual(sc["score"], 0)
        self.assertFalse(sc["contest"])

    def test_small_activist_stake_is_not_a_dispute(self):          # KCGI 6% vs 최대주주 45%
        self.assertFalse(D.structure_score(45.0, [holder("KCGI", 6.0)], 0.5)["contest"])

    def test_comparable_activist_is_a_dispute(self):
        sc = D.structure_score(30.0, [holder("KCGI", 12.0)], 0.5)
        self.assertTrue(sc["contest"])
        self.assertGreaterEqual(sc["score"], 4)

    def test_swing_votes(self):
        ext = [holder("KCGI", 15.0), holder("한국산업은행", 10.0, "약식"), holder("블랙록", 6.0, "약식")]
        names = [x["name"] for x in D.swing_holders(27.0, ext)]
        self.assertIn("한국산업은행", names)
        self.assertNotIn("KCGI", names)

    def test_slow_accumulation(self):
        rows = [("20250301", "약식", 5.1, "신규"), ("20250601", "약식", 5.9, ""), ("20251001", "약식", 6.8, ""), ("20260301", "일반", 8.2, "목적변경"), ("20260901", "일반", 9.4, "")]
        acc = D.accumulation(rows, TODAY)
        self.assertTrue(acc["slow"])
        self.assertEqual(acc["acc_months"], 20)
        new = D.accumulation([("20260901", "약식", 5.2, "신규")], TODAY)
        self.assertFalse(new["slow"])

    def test_role_override(self):
        ext = [holder("델타항공", 14.9, "약식", role="neutral"), holder("호반건설", 20.7)]
        self.assertEqual([x["name"] for x in D.swing_holders(20.6, ext)], ["델타항공"])


class Analyze(unittest.TestCase):
    """DART 호출 없이(지분 구조는 가짜로) 판정 전체를 돌려 본다."""

    def run_case(self, events, hp, ext, ally=0.0, tags=()):
        c = sqlite3.connect(":memory:")
        D.ensure(c)
        c.executemany("INSERT INTO dispute_event VALUES (?,?,?,?,?,?,?,?,?,?)", events)
        for t in tags:
            c.execute("INSERT INTO dispute_tag (code,name,role,updated,pct) VALUES (?,?,?,?,?)", t)
        orig = D.fetch_structure
        D.fetch_structure = lambda *a, **k: (hp, [dict(h) for h in ext], ["오너"], ally)
        try:
            D.analyze(c, key="k", today=TODAY, log=lambda m: None)
        finally:
            D.fetch_structure = orig
        res = {r[0]: (r[1], json.loads(r[2])) for r in c.execute("SELECT code, level, payload FROM dispute_state")}
        c.close()
        return res

    def ev(self, i, code, corp, title, flr, kind, w=3.0, act=0, date="20261101"):
        return (f"r{i}", "c", corp, code, date, title, flr, kind, w, act)

    def test_amore_samsung_not_listed(self):
        out = self.run_case([self.ev(1, "090430", "아모레퍼시픽", "임원ㆍ주요주주특정증권등소유상황보고서", "이창규", "proxy_ext", 4.0),
                             self.ev(2, "090430", "아모레퍼시픽", "소송등의제기ㆍ신청(특허 가처분)", "아모레퍼시픽", "injunction"),
                             self.ev(3, "090430", "아모레퍼시픽", "주식등의공개매수신고서", "아모레퍼시픽", "tender", 4.0)],
                            44.8, [holder("국민연금공단", 10.5, "약식")])
        self.assertEqual(out, {})

    def test_majority_owner_excluded_even_with_lawsuit(self):
        out = self.run_case([self.ev(1, "111111", "A", "경영권분쟁소송", "형제", "litig", 4.0)], 55.0, [holder("KCGI", 6.0)])
        self.assertEqual(out, {})

    def test_hanjin_style_dispute_listed(self):
        out = self.run_case([self.ev(1, "180640", "한진칼", "소송등의제기ㆍ신청(주주총회 의결권행사금지 가처분)", "호반건설", "injunction"),
                             self.ev(2, "180640", "한진칼", "주식등의공개매수신고서", "호반건설", "tender", 4.0)],
                            20.58, [holder("호반건설", 20.7, resn="계약체결")])
        self.assertIn("180640", out)
        self.assertEqual(out["180640"][0], "치열")

    def test_pending_tender_stake_not_a_holder(self):               # 가비아
        out = self.run_case([self.ev(1, "079940", "가비아", "주식등의공개매수신고서", "한국인터넷인프라홀딩스", "tender", 4.0),
                             self.ev(2, "079940", "가비아", "경영권분쟁소송", "가비아", "litig", 4.0)],
                            26.0, [holder("한국인터넷인프라홀딩스", 69.3, resn="계약체결"), holder("얼라인파트너스자산운용", 7.0)])
        st = out["079940"][1]["struct"]
        self.assertEqual([h["name"] for h in st["ext"]], ["얼라인파트너스자산운용"])
        self.assertEqual([x["name"] for x in st["pending"]], ["한국인터넷인프라홀딩스"])

    def test_friendly_affiliate_stake_counts_as_friendly(self):
        out = self.run_case([self.ev(1, "222222", "B", "대량보유상황보고서(일반)", "KCGI", "blk_gen", 3.0, 1)], 20.0, [holder("KCGI", 6.0)], ally=15.0)
        self.assertEqual(out, {})                                    # 우호 35% vs 도전자 6%

    def test_my_exclude_and_tags(self):
        evs = [self.ev(1, "333333", "C", "경영권분쟁소송", "형제", "litig", 4.0)]
        self.assertIn("333333", self.run_case(evs, 30.0, [holder("KCGI", 12.0)]))
        self.assertEqual(self.run_case(evs, 30.0, [holder("KCGI", 12.0)], tags=[("333333", "*", "exclude", "2026-11-26", None)]), {})
        # 맥쿼리를 직접 추가한 도전자로
        out = self.run_case(evs, 26.0, [holder("한국인터넷인프라홀딩스", 69.3, resn="계약체결")], tags=[("333333", "맥쿼리", "challenger", "2026-11-26", 30.0)])
        self.assertEqual([h["name"] for h in out["333333"][1]["struct"]["ext"]], ["맥쿼리"])


if __name__ == "__main__":
    unittest.main()
