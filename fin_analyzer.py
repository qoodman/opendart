# -*- coding: utf-8 -*-
"""
재무제표 분석기: 종목마다 가장 최근 정기보고서(분기·반기·사업보고서)의 전체 재무제표를 DART에서 받아 투자 지표로 정리해요.

- 받는 곳: DART OpenAPI fnlttSinglAcntAll (연결 재무제표 우선, 없으면 별도). 종목당 보통 1~2번 호출.
- 보는 것 (모두 누적 기준, 전년 같은 기간과 비교):
  성장 — 매출·영업이익·순이익 증감, 최근 분기 영업이익 증감 / 수익성 — 영업이익률과 그 변화, 연환산 ROE /
  현금 — 영업현금흐름 대비 순이익, 잉여현금흐름 / 안정성 — 부채비율, 유동비율, 이자보상배율, 순차입금 /
  이익의 질 — 매출채권·재고가 매출보다 빨리 늘었나, 순이익이 영업 외 이익에서 왔나, 자본잠식
- 점수(0~100)와 등급(A~D)은 제가 정한 단순 기준이에요: 성장 30 · 수익성 20 · 현금 25 · 안정성 20 · 이익의 질 5(감점 포함).
  업종마다 정상 범위가 다르고(예: 건설·유통은 부채비율이 높음), 금융업(은행·보험·증권 등)은 영업이익·부채 개념이 달라 점수를 매기지 않아요.
  한 분기 숫자라서 계절성·일회성(자산 매각, 충당금)이 섞여요. 투자 추천이 아니라 보고서를 빠르게 훑어보는 도구예요.
"""
import datetime as dt
import json
import os
import re
import sqlite3
import time

import requests

DART_BASE = "https://opendart.fss.or.kr/api"
RCODE_MONTHS = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}
RCODE_NAME = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업(연간)"}
FIN_WORDS = ("은행", "보험", "증권", "캐피탈", "카드", "저축", "금융", "창업투자", "벤처캐피탈", "리츠", "스팩", "기업인수목적")
SCORE_MAX = {"성장": 30, "수익성": 20, "현금": 25, "안정성": 20, "이익의 질": 5}


class FinError(Exception):
    pass


# ---------------- 저장 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS fin_report (
        code TEXT, year INTEGER, rcode TEXT, fs TEXT, rcept_no TEXT, name TEXT, m TEXT, score REAL, grade TEXT, parts TEXT, signals TEXT, fetched TEXT,
        PRIMARY KEY (code, year, rcode));
    CREATE TABLE IF NOT EXISTS fin_report_try (code TEXT PRIMARY KEY, tried TEXT, note TEXT);
    CREATE INDEX IF NOT EXISTS fin_report_code ON fin_report(code);
    """)


# ---------------- 기간 후보 ----------------
def candidates(today=None, n=3):
    """오늘 기준 공시 기한이 지난 정기보고서를 최근 것부터 [(연도, 보고서코드)]. 기한: 1분기 5/15 · 반기 8/14 · 3분기 11/14 · 사업 다음 해 3/31 (+며칠 여유)"""
    today = today or dt.date.today()
    out = []
    for y in range(today.year, today.year - 3, -1):
        for rcode, due in (("11014", dt.date(y, 11, 17)), ("11012", dt.date(y, 8, 17)), ("11013", dt.date(y, 5, 18)), ("11011", dt.date(y + 1, 4, 2))):
            if due <= today:
                out.append((due, y, rcode))
    out.sort(reverse=True)
    return [(y, r) for _, y, r in out[:n]]


# ---------------- DART 호출 ----------------
def fetch_statements(corp, year, rcode, key, fs_div="CFS", session=None):
    """→ 계정 행 목록 ([] 이면 그 보고서가 없음). 한도 초과는 FinError."""
    s = session or requests
    r = s.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params=dict(crtfc_key=key, corp_code=corp, bsns_year=str(year), reprt_code=rcode, fs_div=fs_div), timeout=30)
    if r.status_code != 200:
        raise FinError(f"DART HTTP {r.status_code}")
    try:
        j = r.json()
    except ValueError:
        raise FinError("DART 응답을 읽지 못함")
    st = str(j.get("status", ""))
    if st == "020":
        raise FinError("DART 하루 호출 한도를 넘었어요")
    if st == "013":
        return []
    if st != "000":
        raise FinError(f"DART {st} {j.get('message', '')}".strip())
    return j.get("list") or []


# ---------------- 파싱 ----------------
def _n(v):
    if v is None:
        return None
    t = str(v).replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _norm(t):
    return re.sub(r"[\s\(\)\[\]·ㆍ,]", "", t or "")


# (키, 재무제표 구분, 계정ID 후보(소문자 포함 일치), 계정명 후보(정규화 후 완전 일치))
SPEC = [
    ("rev", ("IS", "CIS"), ("ifrs-full_revenue", "ifrs_revenue"), ("매출액", "수익매출액", "영업수익", "매출", "순매출액", "수익")),
    ("gross", ("IS", "CIS"), ("ifrs-full_grossprofit",), ("매출총이익", "매출총손익", "매출총이익손실")),
    ("op", ("IS", "CIS"), ("dart_operatingincomeloss",), ("영업이익", "영업이익손실", "영업손익", "영업손실")),
    ("ni", ("IS", "CIS"), ("ifrs-full_profitloss",), ("당기순이익", "당기순이익손실", "분기순이익", "분기순이익손실", "반기순이익", "반기순이익손실", "연결당기순이익", "당기순손익", "분기순손익", "반기순손익")),
    ("fincost", ("IS", "CIS"), ("ifrs-full_financecosts",), ("금융원가", "금융비용", "이자비용")),
    ("assets", ("BS",), ("ifrs-full_assets",), ("자산총계",)),
    ("liab", ("BS",), ("ifrs-full_liabilities",), ("부채총계",)),
    ("equity", ("BS",), ("ifrs-full_equity",), ("자본총계",)),
    ("cur_assets", ("BS",), ("ifrs-full_currentassets",), ("유동자산",)),
    ("cur_liab", ("BS",), ("ifrs-full_currentliabilities",), ("유동부채",)),
    ("cash", ("BS",), ("ifrs-full_cashandcashequivalents",), ("현금및현금성자산",)),
    ("inv", ("BS",), ("ifrs-full_inventories",), ("재고자산",)),
    ("rec", ("BS",), ("dart_shorttermtradereceivable", "ifrs-full_tradereceivables", "ifrs-full_currenttradereceivables"), ("매출채권", "매출채권및기타채권", "매출채권및기타유동채권")),
    ("ocf", ("CF",), ("ifrs-full_cashflowsfromusedinoperatingactivities",), ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름")),
    ("icf", ("CF",), ("ifrs-full_cashflowsfromusedininvestingactivities",), ("투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름")),
    ("capex", ("CF",), ("purchaseofpropertyplantandequipment",), ("유형자산의취득", "유형자산취득", "유형자산의증가")),
]
BORROW_WORDS = ("단기차입금", "장기차입금", "유동성장기부채", "유동성장기차입금", "사채", "유동성사채", "전환사채", "신주인수권부사채")


def _find(rows, sjs, ids, names):
    """계정 한 줄 찾기: 계정ID 일치 → 계정명 일치 순. 같은 계정이 여러 줄(지배·비지배 등)이면 첫 줄."""
    pool = [r for r in rows if r.get("sj_div") in sjs]
    for want in ids:
        for r in pool:
            aid = (r.get("account_id") or "").lower()
            if aid == want or (want.startswith("purchaseof") and want in aid):
                return r
    for r in pool:
        if _norm(r.get("account_nm")) in names:
            return r
    return None


def parse(rows, rcode):
    """계정 행 → dict(키 → dict(cur, prev, cur_ytd, prev_ytd)) 와 borrow(차입금 추정 합계, 현재·전기말)
    cur/prev: 분기 3개월(IS) 또는 시점·누적(BS·CF), *_ytd: 누적. 사업보고서는 모두 연간."""
    out = {}
    for key, sjs, ids, names in SPEC:
        r = _find(rows, sjs, ids, names)
        if not r:
            continue
        th, fr = _n(r.get("thstrm_amount")), _n(r.get("frmtrm_amount"))
        tha, fra = _n(r.get("thstrm_add_amount")), _n(r.get("frmtrm_add_amount"))
        if "IS" in sjs:
            out[key] = dict(cur=th, prev=fr, cur_ytd=tha if tha is not None else th, prev_ytd=fra if fra is not None else fr)
        elif sjs == ("CF",):
            out[key] = dict(cur=th, prev=fr, cur_ytd=th, prev_ytd=fr)
        else:
            out[key] = dict(cur=th, prev=fr, cur_ytd=th, prev_ytd=fr)
    cur = prev = 0.0
    found = False
    for r in rows:
        if r.get("sj_div") != "BS":
            continue
        nm = _norm(r.get("account_nm"))
        if any(nm == w or (w in nm and "채권" not in nm and "매입" not in nm and "충당" not in nm and "리스" not in nm) for w in BORROW_WORDS) and "합계" not in nm:
            a, b = _n(r.get("thstrm_amount")), _n(r.get("frmtrm_amount"))
            if a is not None:
                cur += a
                found = True
            if b is not None:
                prev += b
    if found:
        out["borrow"] = dict(cur=cur, prev=prev, cur_ytd=cur, prev_ytd=prev)
    return out


def _g(now, old):
    """증감률. 기준값이 0 이하이거나 없으면 None (흑자전환·적자지속은 따로 판단)"""
    if now is None or old is None or old <= 0:
        return None
    return now / old - 1


def metrics(p, rcode):
    """parse() 결과 → 지표 dict (비율은 소수, 금액은 원)"""
    g = lambda k, f="cur_ytd": (p.get(k) or {}).get(f)
    months = RCODE_MONTHS.get(rcode, 12)
    m = dict(months=months)
    for k in ("rev", "op", "ni", "ocf", "capex", "fincost", "gross"):
        m[k] = g(k)
        m[k + "_py"] = g(k, "prev_ytd")
    m["rev_q"], m["rev_q_py"] = g("rev", "cur"), g("rev", "prev")
    m["op_q"], m["op_q_py"] = g("op", "cur"), g("op", "prev")
    for k in ("assets", "liab", "equity", "cur_assets", "cur_liab", "cash", "inv", "rec", "borrow"):
        m[k] = g(k, "cur")
        m[k + "_ye"] = g(k, "prev")
    if m["rev"]:
        m["opm"] = (m["op"] / m["rev"]) if m["op"] is not None else None
        m["npm"] = (m["ni"] / m["rev"]) if m["ni"] is not None else None
    if m.get("rev_py"):
        m["opm_py"] = (m["op_py"] / m["rev_py"]) if m["op_py"] is not None else None
    m["rev_yoy"], m["op_yoy"], m["ni_yoy"] = _g(m["rev"], m["rev_py"]), _g(m["op"], m["op_py"]), _g(m["ni"], m["ni_py"])
    m["rev_q_yoy"], m["op_q_yoy"] = _g(m["rev_q"], m["rev_q_py"]), _g(m["op_q"], m["op_q_py"])
    if m["equity"] and m["equity"] > 0 and m["ni"] is not None:
        m["roe"] = m["ni"] * 12.0 / months / m["equity"]
    if m["equity"] and m["equity"] > 0 and m["liab"] is not None:
        m["debt_ratio"] = m["liab"] / m["equity"]
    if m["equity_ye"] and m["equity_ye"] > 0 and m["liab_ye"] is not None:
        m["debt_ratio_ye"] = m["liab_ye"] / m["equity_ye"]
    if m["cur_liab"]:
        m["current_ratio"] = m["cur_assets"] / m["cur_liab"] if m["cur_assets"] is not None else None
    if m["fincost"] and m["fincost"] > 0 and m["op"] is not None:
        m["interest_cover"] = m["op"] / abs(m["fincost"])
    if m["ocf"] is not None and m["ni"] is not None and m["ni"] > 0:
        m["ocf_to_ni"] = m["ocf"] / m["ni"]
    if m["ocf"] is not None:
        m["fcf"] = m["ocf"] - abs(m["capex"] or 0)
    if m["borrow"] is not None and m["cash"] is not None:
        m["net_debt"] = m["borrow"] - m["cash"]
    m["rec_chg"] = _g(m["rec"], m["rec_ye"])
    m["inv_chg"] = _g(m["inv"], m["inv_ye"])
    return m


# ---------------- 점수 ----------------
def _step(v, rules, default=0):
    """v ≥ 기준이면 점수: rules = [(기준, 점수), ...] 큰 기준부터"""
    if v is None:
        return None
    for th, pts in rules:
        if v >= th:
            return pts
    return default


def score(m, financial=False):
    """지표 → dict(total, grade, parts{항목: 점수}, signals[(종류, 문장)]) . 금융업이거나 핵심 숫자가 없으면 total=None"""
    sig = []
    if financial:
        return dict(total=None, grade="-", parts={}, signals=[("info", "금융업(은행·보험·증권 등)은 영업이익·부채 개념이 달라 점수를 매기지 않았어요.")])
    if m.get("rev") is None and m.get("op") is None:
        return dict(total=None, grade="-", parts={}, signals=[("info", "매출·영업이익을 읽지 못해 점수를 매기지 못했어요.")])
    parts = {}
    pct = lambda v: f"{v:+.0%}"
    # 성장 (30): 매출 10 · 영업이익 10 · 최근 분기 영업이익 10
    g_rev = _step(m.get("rev_yoy"), [(0.20, 10), (0.10, 8), (0.0, 6), (-0.10, 3)]) or 0

    def op_pts(now, old, yoy):
        if now is None:
            return 0
        if old is not None and old <= 0 and now > 0:
            return 10                                           # 흑자 전환
        if yoy is not None:
            return _step(yoy, [(0.30, 10), (0.10, 8), (0.0, 6), (-0.20, 3)])
        if old is not None and old < 0 and now < 0:
            return 4 if now > old else 0                         # 적자 지속: 줄었으면 약간
        return 0
    g_op = op_pts(m.get("op"), m.get("op_py"), m.get("op_yoy"))
    g_opq = op_pts(m.get("op_q"), m.get("op_q_py"), m.get("op_q_yoy")) if m.get("op_q") is not None else g_op
    parts["성장"] = g_rev + g_op + g_opq
    if m.get("rev_yoy") is not None:
        sig.append(("good" if m["rev_yoy"] >= 0.10 else "warn" if m["rev_yoy"] < 0 else "info", f"누적 매출 전년 대비 {pct(m['rev_yoy'])}"))
    if m.get("op_py") is not None and m.get("op") is not None and m["op_py"] <= 0 < m["op"]:
        sig.append(("good", "영업이익 흑자 전환"))
    elif m.get("op_yoy") is not None:
        sig.append(("good" if m["op_yoy"] >= 0.10 else "warn" if m["op_yoy"] < -0.10 else "info", f"누적 영업이익 전년 대비 {pct(m['op_yoy'])}"))
    if m.get("op") is not None and m["op"] < 0:
        sig.append(("warn", "영업적자"))
    if m.get("op_q_yoy") is not None and m.get("op_yoy") is not None and abs(m["op_q_yoy"] - m["op_yoy"]) >= 0.3:
        sig.append(("info", f"최근 분기 영업이익 전년 대비 {pct(m['op_q_yoy'])} (누적과 다름)"))
    # 수익성 (20): 영업이익률 수준 10 · 변화 10
    p1 = _step(m.get("opm"), [(0.15, 10), (0.08, 8), (0.03, 5), (0.0, 2)]) or 0
    chg = None if m.get("opm") is None or m.get("opm_py") is None else m["opm"] - m["opm_py"]
    p2 = 0 if chg is None else _step(chg, [(0.02, 10), (0.0, 7), (-0.02, 4)]) or 0
    parts["수익성"] = p1 + p2
    if m.get("opm") is not None:
        sig.append(("good" if m["opm"] >= 0.10 else "warn" if m["opm"] < 0.03 else "info", f"영업이익률 {m['opm']:.1%}" + ("" if chg is None else f" (전년 같은 기간 대비 {chg * 100:+.1f}%p)")))
    # 현금 (25): 영업현금흐름 흑자 8 · 현금 전환(영업CF/순이익) 9 · 잉여현금흐름 8
    c1 = 8 if (m.get("ocf") or 0) > 0 else 0
    c2 = _step(m.get("ocf_to_ni"), [(1.0, 9), (0.7, 6), (0.3, 3)]) if m.get("ocf_to_ni") is not None else (4 if c1 else 0)
    c3 = 8 if (m.get("fcf") or -1) > 0 else 3 if c1 else 0
    parts["현금"] = c1 + (c2 or 0) + c3
    if m.get("ocf") is not None and m["ocf"] < 0:
        sig.append(("warn", "영업현금흐름이 적자 (이익이 현금으로 안 들어오고 있어요)"))
    if m.get("ocf_to_ni") is not None:
        sig.append(("good" if m["ocf_to_ni"] >= 1.0 else "warn" if m["ocf_to_ni"] < 0.5 else "info", f"영업현금흐름 / 순이익 {m['ocf_to_ni']:.1f}배"))
    if m.get("fcf") is not None:
        sig.append(("good" if m["fcf"] > 0 else "info", "잉여현금흐름(영업CF - 유형자산 취득) " + ("흑자" if m["fcf"] > 0 else "적자 (설비투자 중일 수 있어요)")))
    # 안정성 (20): 부채비율 8 · 유동비율 6 · 이자보상 6
    d = m.get("debt_ratio")
    s1 = 0 if d is None else 8 if d <= 1.0 else 5 if d <= 2.0 else 2 if d <= 3.0 else 0
    s2 = _step(m.get("current_ratio"), [(1.5, 6), (1.0, 4), (0.7, 1)]) or 0
    ic = m.get("interest_cover")
    if ic is not None:
        s3 = _step(ic, [(5, 6), (2, 4), (1, 2)]) or 0
    elif m.get("fincost") is not None and m["fincost"] <= 0:
        s3 = 6                                                    # 이자 부담 없음
    else:
        s3 = 4                                                    # 알 수 없음은 중간
    parts["안정성"] = s1 + s2 + s3
    if d is not None:
        sig.append(("good" if d <= 1.0 else "warn" if d > 3.0 else "info", f"부채비율 {d:.0%}" + ("" if m.get("debt_ratio_ye") is None else f" (직전 연말 {m['debt_ratio_ye']:.0%})")))
    if m.get("current_ratio") is not None and m["current_ratio"] < 1.0:
        sig.append(("warn", f"유동비율 {m['current_ratio']:.0%} — 1년 안에 갚을 돈이 1년 안에 들어올 돈보다 많아요"))
    if ic is not None and ic < 1.5:
        sig.append(("warn", f"이자보상배율 {ic:.1f}배 — 영업이익으로 이자를 겨우 감당해요"))
    if m.get("net_debt") is not None:
        sig.append(("good" if m["net_debt"] < 0 else "info", "순현금(현금성자산이 차입금보다 많음)" if m["net_debt"] < 0 else "순차입(차입금이 현금성자산보다 많음)"))
    # 이익의 질 (5, 감점)
    q = 5
    if m.get("rec_chg") is not None and m.get("rev_yoy") is not None and m["rec_chg"] > 0.3 and m["rec_chg"] - m["rev_yoy"] > 0.2:
        q -= 3
        sig.append(("warn", f"매출채권이 연초보다 {pct(m['rec_chg'])} 늘어 매출 증가보다 훨씬 빨라요 (대금 회수 지연·매출 부풀리기 신호일 수 있어요)"))
    if m.get("inv_chg") is not None and m.get("rev_yoy") is not None and m["inv_chg"] > 0.3 and m["inv_chg"] - m["rev_yoy"] > 0.2:
        q -= 2
        sig.append(("warn", f"재고자산이 연초보다 {pct(m['inv_chg'])} 늘어 매출 증가보다 훨씬 빨라요 (재고 쌓임 신호일 수 있어요)"))
    if m.get("ni") is not None and m.get("op") is not None and m["ni"] > 0 and (m["op"] <= 0 or m["ni"] > m["op"] * 1.5) and m["ni"] > 0.02 * (m.get("rev") or 0):
        q -= 3
        sig.append(("warn", "순이익이 영업이익보다 훨씬 커요 — 영업 외 이익(자산 매각·평가이익 등)이 섞였을 수 있어요"))
    pen = 0
    if m.get("equity") is not None and m["equity"] <= 0:
        pen = 10
        sig.append(("warn", "자본총계가 0 이하 (완전 자본잠식)"))
    parts["이익의 질"] = max(0, q)
    total = max(0, min(100, sum(parts.values()) - pen))
    grade = "A" if total >= 75 else "B" if total >= 60 else "C" if total >= 45 else "D"
    return dict(total=float(total), grade=grade, parts=parts, signals=sig)


# ---------------- 한 종목 ----------------
def analyze_code(code, key, corp, today=None, session=None, financial=False, sleep=0.2):
    """종목 하나의 가장 최근 정기보고서를 찾아 분석 → dict(year, rcode, fs, rcept_no, m, score) 또는 None(보고서 없음)"""
    for year, rcode in candidates(today):
        for fs in ("CFS", "OFS"):
            rows = fetch_statements(corp, year, rcode, key, fs, session)
            time.sleep(sleep)
            if rows:
                m = metrics(parse(rows, rcode), rcode)
                return dict(year=year, rcode=rcode, fs=fs, rcept_no=(rows[0].get("rcept_no") or ""), m=m, score=score(m, financial))
    return None


# ---------------- 대상 종목 ----------------
def targets(conn, limit=400):
    """분석할 종목: 시가총액 상위(KRX 시세가 있을 때) + 공시에 나온 종목 + 점수표·법안 관심 종목. 최대 limit개. → [(code, 이름)]"""
    out, seen = [], set()

    def add(code, name=""):
        if code and re.fullmatch(r"\d{6}", str(code)) and code not in seen:
            seen.add(code)
            out.append((code, name or ""))
    qs = [("SELECT code, name FROM legis_watch", ()), ("SELECT code, name FROM scorecard_stock", ()),
          ("SELECT DISTINCT 종목코드, 종목명 FROM summary WHERE 종목코드 IS NOT NULL AND 종목코드 != ''", ())]
    for q, a in qs:
        try:
            for c, n in conn.execute(q, a):
                add(c, n)
        except sqlite3.Error:
            pass
    try:
        for c, n in conn.execute("SELECT code, name FROM krx_daily WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_daily) AND mktcap IS NOT NULL ORDER BY mktcap DESC LIMIT ?", (limit,)):
            add(c, n)
    except sqlite3.Error:
        pass
    names = {}
    try:
        names = {c: n for c, n in conn.execute("SELECT code, name FROM theme_stocks WHERE name IS NOT NULL")}
    except sqlite3.Error:
        pass
    out = [(c, n or names.get(c, "")) for c, n in out]
    return out[:limit]


def is_financial(conn, code):
    try:
        r = conn.execute("SELECT group_name FROM industry_map WHERE code=?", (code,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(r and r[0] and any(w in r[0] for w in FIN_WORDS))


# ---------------- 한 번에 돌리기 ----------------
def run(db_path, key, cache_dir, limit=400, log=print, progress=None, today=None, session=None, max_age_days=5, sleep=0.2):
    """대상 종목을 차례로 분석해 fin_report에 저장. 최근 max_age_days일 안에 받은 종목은 건너뜀(이어서 하기). → dict(대상, 분석, 보고서 없음, 실패)"""
    import score_auto
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        today = today or dt.date.today()
        todo = targets(conn, limit)
        fresh = {r[0] for r in conn.execute("SELECT code FROM fin_report_try WHERE tried >= ?", ((today - dt.timedelta(days=max_age_days)).isoformat(),))}
        res = dict(대상=len(todo), 분석=0, 없음=0, 실패=0, 건너뜀=0)
        streak = 0
        for i, (code, nm) in enumerate(todo):
            if progress and i % 5 == 0:
                progress(f"{i}/{len(todo)} 종목")
            if code in fresh:
                res["건너뜀"] += 1
                continue
            if streak >= 8:
                log("연속 실패가 많아 중단합니다. 다음에 이어서 받아요.")
                break
            try:
                corp = score_auto.corp_code_for(code, cache_dir, key, session=session)
                got = analyze_code(code, key, corp, today, session, is_financial(conn, code), sleep)
                streak = 0
            except (score_auto.AutoError, FinError, requests.RequestException) as e:
                if "회사 목록에 이 종목이 없어요" in str(e):
                    conn.execute("INSERT OR REPLACE INTO fin_report_try VALUES (?,?,?)", (code, today.isoformat(), "회사 목록에 없음"))
                    conn.commit()
                    res["없음"] += 1
                    continue
                res["실패"] += 1
                streak += 1
                log(f"  ✖ {code} {nm}: {e}")
                if "한도" in str(e):
                    break
                continue
            if not got:
                res["없음"] += 1
                conn.execute("INSERT OR REPLACE INTO fin_report_try VALUES (?,?,?)", (code, today.isoformat(), "정기보고서 없음"))
                conn.commit()
                continue
            sc = got["score"]
            conn.execute("INSERT OR REPLACE INTO fin_report VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (code, got["year"], got["rcode"], got["fs"], got["rcept_no"], nm, json.dumps(got["m"], ensure_ascii=False), sc["total"], sc["grade"],
                          json.dumps(sc["parts"], ensure_ascii=False), json.dumps(sc["signals"], ensure_ascii=False), today.isoformat()))
            conn.execute("INSERT OR REPLACE INTO fin_report_try VALUES (?,?,?)", (code, today.isoformat(), "ok"))
            conn.commit()
            res["분석"] += 1
        log(f"[재무제표 분석] {res}")
        return res
    finally:
        conn.close()


# ---------------- 읽기 ----------------
def latest(conn):
    """종목별 가장 최근 보고서 → [dict(code, name, year, rcode, fs, rcept_no, m, score, grade, parts, signals, fetched)] (점수 높은 순, 점수 없음은 뒤)"""
    try:
        rows = conn.execute("SELECT code, year, rcode, fs, rcept_no, name, m, score, grade, parts, signals, fetched FROM fin_report").fetchall()
    except sqlite3.Error:
        return []
    best = {}
    for r in rows:
        k = (r[1], {"11013": 1, "11012": 2, "11014": 3, "11011": 4}.get(r[2], 0))
        if r[0] not in best or k > best[r[0]][0]:
            best[r[0]] = (k, r)
    out = []
    for _, r in best.values():
        out.append(dict(code=r[0], year=r[1], rcode=r[2], fs=r[3], rcept_no=r[4], name=r[5], m=json.loads(r[6] or "{}"), score=r[7], grade=r[8],
                        parts=json.loads(r[9] or "{}"), signals=json.loads(r[10] or "[]"), fetched=r[11]))
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    return out


def history(conn, code):
    """한 종목의 저장된 보고서들 (오래된 순) — 점수 추이"""
    try:
        rows = conn.execute("SELECT year, rcode, score, grade FROM fin_report WHERE code=? ORDER BY year, rcode", (code,)).fetchall()
    except sqlite3.Error:
        return []
    order = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
    return sorted([dict(year=r[0], rcode=r[1], score=r[2], grade=r[3]) for r in rows], key=lambda x: (x["year"], order.get(x["rcode"], 0)))


def run_job(db_path, key, cache_dir, status_path, limit=400, log=print):
    """별도 프로세스용: 진행 상황을 status_path(JSON)에 적으면서 run()을 돌림 (legis.read_status가 읽는 모양)"""
    st = dict(running=True, pid=os.getpid(), started=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), finished="",
              steps=[dict(name="재무제표 받기·분석 (DART)", state="run", msg="시작")])

    def save():
        try:
            tmp = status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, status_path)
        except OSError:
            pass
    save()
    last = {"t": 0.0}

    def progress(msg):
        if time.time() - last["t"] > 3:
            last["t"] = time.time()
            st["steps"][0]["msg"] = msg
            save()
    try:
        if not key:
            raise FinError("DART_API_KEY가 없어요 (설정에서 DART 키를 넣어 주세요)")
        res = run(db_path, key, cache_dir, limit=limit, log=log, progress=progress)
        st["steps"][0].update(state="ok", msg=f"대상 {res['대상']}개 · 분석 {res['분석']} · 보고서 없음 {res['없음']} · 실패 {res['실패']} · 최근에 받아 건너뜀 {res['건너뜀']}")
    except Exception as e:
        st["steps"][0].update(state="fail", msg=f"{type(e).__name__}: {str(e)[:160]}")
        log(f"재무제표 분석 실패: {e}")
    st["running"] = False
    st["finished"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save()
