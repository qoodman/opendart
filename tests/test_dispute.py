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

    def run_case(self, events, hp, ext, ally=0.0, tags=(), insiders=("오너",)):
        c = sqlite3.connect(":memory:")
        D.ensure(c)
        c.executemany("INSERT INTO dispute_event VALUES (?,?,?,?,?,?,?,?,?,?)", events)
        for t in tags:
            c.execute("INSERT INTO dispute_tag (code,name,role,updated,pct) VALUES (?,?,?,?,?)", t)
        orig = D.fetch_structure
        D.fetch_structure = lambda *a, **k: (hp, [dict(h) for h in ext], list(insiders), ally)
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

    def test_gabia_real_holders_stay_holders(self):                  # 가비아: 미리·얼라인은 실제 보유자, 공개매수자만 예정
        out = self.run_case([self.ev(1, "079940", "가비아", "주식등의공개매수신고서", "한국인터넷인프라홀딩스", "tender", 4.0),
                             self.ev(2, "079940", "가비아", "경영권분쟁소송", "가비아", "litig", 4.0)],
                            26.9, [holder("한국인터넷인프라홀딩스", 69.3, resn="계약체결"),
                                   holder("MiriCapitalManagementLLC", 24.2, resn="공동보유계약", activist=D._is_activist("MiriCapitalManagementLLC")),
                                   holder("얼라인파트너스자산운용", 14.3, resn="계약"), holder("GumshoeMasterFundLP", 6.8, "약식")])
        st = out["079940"][1]["struct"]
        self.assertEqual(sorted(h["name"] for h in st["ext"]), ["GumshoeMasterFundLP", "MiriCapitalManagementLLC", "얼라인파트너스자산운용"])
        self.assertEqual([x["name"] for x in st["pending"]], ["한국인터넷인프라홀딩스"])
        self.assertTrue(st["contest"])

    def test_activist_names_without_spaces(self):
        for n in ("MiriCapitalManagementLLC", "TheMiriStrategicFund", "KCGI", "AlignPartnersAssetManagement"):
            self.assertTrue(D._is_activist(n) or n.startswith("Align"), n)
        self.assertFalse(D.is_neutral_inst("MiriCapitalManagementLLC"))

    def test_friendly_affiliate_stake_counts_as_friendly(self):
        out = self.run_case([self.ev(1, "222222", "B", "대량보유상황보고서(일반)", "KCGI", "blk_gen", 3.0, 1)], 20.0, [holder("KCGI", 6.0)], ally=15.0)
        self.assertEqual(out, {})                                    # 우호 35% vs 도전자 6%

    def test_my_exclude_and_tags(self):
        evs = [self.ev(1, "333333", "C", "경영권분쟁소송", "형제", "litig", 4.0)]
        self.assertIn("333333", self.run_case(evs, 30.0, [holder("KCGI", 12.0)]))
        self.assertEqual(self.run_case(evs, 30.0, [holder("KCGI", 12.0)], tags=[("333333", "*", "exclude", "2026-11-26", None)]), {})
        # 맥쿼리를 직접 추가한 도전자로
        evs3 = evs + [self.ev(2, "333333", "C", "주식등의공개매수신고서", "한국인터넷인프라홀딩스", "tender", 4.0)]
        out = self.run_case(evs3, 26.0, [holder("한국인터넷인프라홀딩스", 69.3, resn="계약체결")], tags=[("333333", "맥쿼리", "challenger", "2026-11-26", 30.0)])
        self.assertEqual([h["name"] for h in out["333333"][1]["struct"]["ext"]], ["맥쿼리"])


class Reports(unittest.TestCase):
    """사업·반기보고서의 '5% 이상 주주' 표와 기간 선택 (가비아: The Miri Strategic Fund 24.2%는 반기보고서에만 나옴)"""

    def test_period_order(self):
        self.assertEqual(D.hy_periods(dt.date(2026, 11, 26))[0], (2026, "11014"))
        self.assertEqual(D.hy_periods(dt.date(2026, 9, 1))[0], (2026, "11012"))
        self.assertEqual(D.hy_periods(dt.date(2026, 6, 1))[0], (2026, "11013"))
        self.assertEqual(D.hy_periods(dt.date(2026, 3, 10)), [(2024, "11011")])

    def test_holder_table(self):
        txt = ("3. 주식의 분포\n가. 5% 이상 주주 현황\n구분\t주주명\t소유주식수\t지분율\t비고\n5% 이상 주주\tThe Miri Strategic Fund\t2,420,000\t24.2\t-\n"
               "\t국민연금공단\t600,000\t6.0\t-\n우리사주조합\t-\t-\t-\t-\n나. 소액주주현황\n소액주주\t10,000\t95,000,000\t80.0\n")
        got = {h["name"]: h["pct"] for h in D.parse_holder_table(txt)}
        self.assertEqual(got, {"The Miri Strategic Fund": 24.2, "국민연금공단": 6.0})

    def test_report_row_details_are_kept(self):             # 11월 3일 Miri 약식 보고: 24.17%, 3,243,617주, 주식매매계약 체결
        class R:
            def __init__(self, j):
                self._j, self.status_code = j, 200

            def json(self):
                return self._j

        class Sess:
            def get(self, url, params=None, timeout=0):
                if "hyslrSttus" in url:
                    return R({"status": "000", "list": [{"nm": "김홍국", "trmend_posesn_stock_qota_rt": "26.9"}, {"nm": "계", "trmend_posesn_stock_qota_rt": "26.9"}]} if params["reprt_code"] == "11012" else {"status": "013"})
                if "otrCpr" in url or "list.json" in url:
                    return R({"status": "000", "list": []})
                return R({"status": "000", "list": [{"repror": "Miri Capital Management LLC", "stkrt": "24.17", "stkrt_irds": "0.21", "stkqy": "3,243,617", "stkqy_irds": "0",
                                                      "ctr_stkrt": "0", "report_tp": "약식", "rcept_dt": "20261103", "report_resn": "주식매매계약 체결", "rcept_no": "1"}]})
        hp, ext, _, _ = D.fetch_structure("079940", "c", "k", TODAY, Sess(), corp_name="가비아")
        self.assertEqual(hp, 26.9)
        self.assertEqual(ext[0]["shares"], 3243617.0)
        self.assertEqual(ext[0]["resn"], "주식매매계약 체결")
        self.assertTrue(ext[0]["activist"])
        self.assertTrue(D.structure_score(hp, ext, None)["contest"])

    def _fetch_with(self, maj_date, maj_pct, rep_pct):
        import fin_clues

        class R:
            def __init__(self, j):
                self._j, self.status_code = j, 200

            def json(self):
                return self._j

        class Sess:
            def get(self, url, params=None, timeout=0):
                if "hyslrSttus" in url:
                    return R({"status": "000", "list": [{"nm": "김홍국", "trmend_posesn_stock_qota_rt": "26.0"}, {"nm": "계", "trmend_posesn_stock_qota_rt": "26.0"}]} if params["reprt_code"] == "11012" else {"status": "013"})
                if "otrCpr" in url:
                    return R({"status": "000", "list": []})
                if "list.json" in url:
                    return R({"status": "000", "list": [{"report_nm": "반기보고서 (2026.06)", "rcept_dt": "20260814", "rcept_no": "9"}]})
                return R({"status": "000", "list": [{"repror": "Miri Capital Management LLC", "stkrt": str(maj_pct), "stkrt_irds": "0", "stkqy": "1", "stkqy_irds": "0", "ctr_stkrt": "0",
                                                      "report_tp": "약식", "rcept_dt": maj_date, "report_resn": "", "rcept_no": "1"}]})
        orig = fin_clues.fetch_document_text
        fin_clues.fetch_document_text = lambda *a, **k: "가. 5% 이상 주주 현황\n구분\t주주명\t소유주식수\t지분율\nMiri Capital Management LLC\t2,420,000\t" + str(rep_pct) + "\n나. 소액\n"
        try:
            return D.fetch_structure("079940", "c", "k", TODAY, Sess(), corp_name="가비아")[1][0]
        finally:
            fin_clues.fetch_document_text = orig

    def test_newer_source_wins(self):                          # 정기보고서(6/30 기준)가 5% 보고(3/1)보다 최신이면 정기보고서 값을 표시
        h = self._fetch_with("20260301", 14.0, 24.2)
        self.assertEqual((h["pct"], h["asof_src"], h["alt_pct"]), (24.2, "정기보고서", 14.0))

    def test_newer_5pct_report_wins(self):                     # 5% 보고(11/3)가 정기보고서(6/30)보다 최신이면 5% 보고 값을 유지
        h = self._fetch_with("20261103", 24.17, 18.0)
        self.assertEqual((h["pct"], h["asof_src"]), (24.17, "5% 보고"))
        self.assertEqual(h["rep_pct"], 18.0)
        self.assertEqual(h["asof"], "20261103")

    def test_report_asof(self):
        self.assertEqual(D.report_asof("반기보고서 (2026.06)", "20260814"), "20260630")
        self.assertEqual(D.report_asof("사업보고서 (2025.12)", "20260331"), "20251231")

    def test_miri_is_activist(self):
        self.assertTrue(D._is_activist("The Miri Strategic Fund"))
        self.assertFalse(D.is_neutral_inst("The Miri Strategic Fund"))

    def test_report_only_holder_is_added(self):
        import fin_clues

        class R:
            def __init__(self, j):
                self._j, self.status_code, self.content = j, 200, b""

            def json(self):
                return self._j

        class Sess:
            def get(self, url, params=None, timeout=0):
                if "hyslrSttus" in url:
                    ok = params["reprt_code"] == "11012"
                    return R({"status": "000", "list": [{"nm": "김홍국", "trmend_posesn_stock_qota_rt": "26.0"}, {"nm": "계", "trmend_posesn_stock_qota_rt": "26.0"}]} if ok else {"status": "013"})
                if "list.json" in url:
                    return R({"status": "000", "list": [{"report_nm": "반기보고서 (2026.06)", "rcept_dt": "20260814", "rcept_no": "1"}]})
                return R({"status": "000", "list": []})
        orig = fin_clues.fetch_document_text
        fin_clues.fetch_document_text = lambda *a, **k: "가. 5% 이상 주주 현황\n구분\t주주명\t소유주식수\t지분율\n5% 이상 주주\tThe Miri Strategic Fund\t2,420,000\t24.2\n나. 소액주주\n"
        try:
            hp, ext, _, _ = D.fetch_structure("079940", "c", "k", TODAY, Sess(), corp_name="가비아")
        finally:
            fin_clues.fetch_document_text = orig
        self.assertEqual(hp, 26.0)
        self.assertEqual([h["name"] for h in ext], ["The Miri Strategic Fund"])
        self.assertEqual(D.STRUCT_META["079940"]["hy"], "2026 반기보고서")
        self.assertTrue(D.structure_score(hp, ext, None)["contest"])


CONTRACT_TXT = """나. 계약 내용
연번\t성명(명칭)\t관계\t번호\t종류\t주식등의 수\t계약 상대방\t계약의 종류\t계약체결(변경)일\t계약기간\t비율\t비고
1\t한국인터넷인프라홀딩스 유한회사\t본인\t280\t의결권있는 주식\t1,917,916\t삼성증권(주),
얼라인파트너스자산운용(주)
(집합투자업자)\t주식매매계약\t2026.10.27\t-\t14.29\t주1)
2\t한국인터넷인프라홀딩스 유한회사\t본인\t280\t의결권있는 주식\t3,243,617\tThe Miri Strategic Emerging Markets Fund LP\t주식매매계약\t2026.10.27\t-\t24.17\t주2)
3\t한국인터넷인프라홀딩스 유한회사\t본인\t280\t의결권있는 주식\t875,419\tGumshoe Master Fund LP\t주식매매계약\t2026.10.27\t-\t6.52\t주3)
4\t한국인터넷인프라홀딩스 유한회사\t본인\t280\t의결권있는 주식\t3,270,248\t김홍국, 전정완, 원종홍\t주식매매 및 현물출자계약\t2026.10.27\t-\t24.37\t주4)
합계(주식등의 수)\t9,307,200\t합계(비율)\t69.35\t-
주1) 주식매매계약의 주요내용
"""


class Contracts(unittest.TestCase):
    """공개매수자의 '보유주식등에 관한 계약' 표 (가비아: 한국인터넷인프라홀딩스가 미리·얼라인·검슈·최대주주 지분을 매수 계약)"""

    def test_parse(self):
        c = D.parse_contracts(CONTRACT_TXT)
        self.assertEqual([r["pct"] for r in c["rows"]], [14.29, 24.17, 6.52, 24.37])
        self.assertEqual(c["total_pct"], 69.35)
        self.assertIn("김홍국", c["names"])

    def test_same_party(self):
        names = D.parse_contracts(CONTRACT_TXT)["names"]
        for h in ("MiriCapitalManagementLLC", "얼라인파트너스자산운용", "GumshoeMasterFundLP", "김홍국"):
            self.assertTrue(any(D.same_party(h, n) for n in names), h)
        for h in ("KB자산운용", "국민연금공단", "KCGI"):
            self.assertFalse(any(D.same_party(h, n) for n in names), h)

    def test_committed_sellers_are_not_challengers(self):
        import fin_clues
        orig = fin_clues.fetch_document_text
        fin_clues.fetch_document_text = lambda *a, **k: CONTRACT_TXT
        try:
            a = Analyze()
            out = a.run_case([a.ev(1, "079940", "가비아", "주식등의공개매수신고서", "한국인터넷인프라홀딩스", "tender", 4.0),
                              a.ev(2, "079940", "가비아", "경영권분쟁소송", "가비아", "litig", 4.0)],
                             24.37, [holder("한국인터넷인프라홀딩스", 69.35, resn="주식매매계약", rcept_no="OFFER"), holder("MiriCapitalManagementLLC", 24.17, "약식"),
                                     holder("얼라인파트너스자산운용", 14.29), holder("GumshoeMasterFundLP", 6.52, "약식"), holder("KB자산운용", 5.2, "약식")],
                             insiders=("김홍국", "전정완", "원종홍"))
        finally:
            fin_clues.fetch_document_text = orig
        st = out["079940"][1]["struct"]
        self.assertEqual([h["name"] for h in st["ext"]], ["KB자산운용"])
        self.assertEqual(sorted(x["name"] for x in st["committed"]), ["GumshoeMasterFundLP", "MiriCapitalManagementLLC", "얼라인파트너스자산운용"])
        self.assertTrue(st["owner_in_deal"])
        self.assertEqual(st["contract_total"], 69.35)


class NextCandidates(unittest.TestCase):
    """공시 신호가 나오기 전 지분 구조만으로 다음 분쟁 후보 찾기"""

    def _db(self):
        import float_stock
        c = sqlite3.connect(":memory:")
        float_stock.ensure(c)
        D.ensure(c)

        def add(code, name, hp, ext, ally=0.0):
            c.execute("INSERT INTO float_stock (code,name,market,mcap,tv,holder_pct,treasury_pct,float_real,float_pct,fetched,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (code, name, "KOSDAQ", 2000, 1, hp, 0, 30, 40, "2026-11-26", json.dumps(dict(ext=ext, ally=ally), ensure_ascii=False)))

        H = lambda n, p, **k: dict(name=n, pct=p, tp="약식", activist=D._is_activist(n), **k)
        add("100001", "가상A", 26.9, [H("얼라인파트너스", 14.3, slow=True, acc_months=20, pct_1y=9.0, grow=5.3, acc_up=3, first_pct=5.0)])
        add("100002", "가상B", 45.0, [H("KCGI", 6.0)])                          # 도전자가 너무 작음
        add("100003", "가상C", 31.0, [H("국민연금공단", 9.0)])                  # 공적 기관만
        add("100005", "가상E", 55.0, [H("X펀드", 20.0)])                        # 과반
        add("100006", "가상F", 30.0, [H("Y펀드", 12.0)], ally=15.0)             # 우호 계열 지분으로 두꺼워짐
        return c

    def test_ranking(self):
        c = self._db()
        rows, scanned = D.next_candidates(c)
        self.assertEqual(scanned, 5)
        self.assertEqual([r["code"] for r in rows], ["100001"])
        self.assertTrue(rows[0]["slow"] and rows[0]["activist"])

    def test_scan_candidates_show_in_main_list(self):          # 별도 화면 없이 지분분쟁 목록의 '가능성'으로 올라옴
        c = self._db()
        D.analyze(c, key="k", today=TODAY, log=lambda m: None)
        rows = {r[0]: (r[1], json.loads(r[2])) for r in c.execute("SELECT code, level, payload FROM dispute_state")}
        self.assertEqual(list(rows), ["100001"])
        self.assertIn(rows["100001"][0], ("가능성", "가능성 높음"))
        st = rows["100001"][1]["struct"]
        self.assertTrue(st["contest"])
        self.assertEqual(rows["100001"][1]["ev"], None)
        self.assertEqual(st["meta"]["source"], "지분 스캔")

    def test_loss_making_stock_is_not_a_scan_candidate(self):      # 적자 회사는 아무도 사고 싶어 하지 않음
        c = self._db()
        c.execute("CREATE TABLE valuation (code TEXT PRIMARY KEY, per REAL, pbr REAL, div REAL, eps REAL, bps REAL, fetched TEXT)")
        c.execute("INSERT INTO valuation VALUES ('100001', NULL, 1.2, 0, -300, 5000, '2026-11-26')")
        self.assertEqual(D.next_candidates(c)[0], [])
        c.execute("UPDATE valuation SET eps=1200, per=9.0 WHERE code='100001'")
        self.assertEqual([r["code"] for r in D.next_candidates(c)[0]], ["100001"])

    def test_my_exclude_and_role(self):
        c = self._db()
        c.execute("INSERT INTO dispute_tag (code,name,role,updated,pct) VALUES ('100001','*','exclude','2026-11-26',NULL)")
        self.assertEqual(D.next_candidates(c)[0], [])
        c = self._db()
        c.execute("INSERT INTO dispute_tag (code,name,role,updated,pct) VALUES ('100001','얼라인파트너스','ally','2026-11-26',NULL)")      # 우호로 정하면 후보에서 빠짐
        self.assertEqual(D.next_candidates(c)[0], [])


if __name__ == "__main__":
    unittest.main()
