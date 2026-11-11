# -*- coding: utf-8 -*-
"""
투자 대가의 방법을 재무 숫자로 옮긴 단순 점수: 피터 린치 · 워렌 버핏.

두 사람이 공개적으로 강조한 원칙을 숫자 몇 개로 줄인 것이라, 실제 두 사람의 판단과는 다릅니다.
정성적인 부분(사업을 이해하는지, 경영진, 경쟁 우위의 이유, 손에 잡히는 제품)은 숫자로 볼 수 없어서 빠져 있고,
구체적인 기준값(점수 구간)은 제가 정한 단순 기준입니다. 투자 추천이 아니에요.

데이터: 네이버 증권 연간 재무(finance/annual)의 확정 연도(보통 3개년) + 현재 PER(valuation 표).
  연간 EPS·PER·PBR·ROE·부채비율·영업이익률·순이익률·영업이익·당기순이익·주당배당금

피터 린치 (One Up on Wall Street): 종목을 6가지로 나누고(저성장·우량·급성장·경기순환·턴어라운드·자산주),
  PEG(PER ÷ 이익 성장률, 1 아래면 매력적, 2 위면 비쌈), 이익 성장률, 낮은 부채, 이익의 꾸준함을 봅니다.
워렌 버핏: 높은 ROE(자본 효율)와 영업이익률(가격 결정력), 낮은 부채, 꾸준히 늘어나는 이익을 '좋은 기업'의 흔적으로 보고,
  그런 기업을 합리적인 가격에 살 때(안전마진)를 찾습니다.
"""
import datetime as dt
import sqlite3
import time

import score_auto

METRICS = {"EPS": "eps", "PER": "per", "PBR": "pbr", "ROE": "roe", "부채비율": "debt", "영업이익률": "opm",
           "순이익률": "npm", "영업이익": "op", "당기순이익": "ni", "주당배당금": "dps", "BPS": "bps"}
REFRESH_DAYS = 7
CAP_GROWTH = 0.50
MAX_FAIL_STREAK = 5


# ---------------- 저장·수집 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS fin_annual (
        code TEXT, period TEXT, consensus INTEGER, eps REAL, per REAL, pbr REAL, roe REAL, debt REAL, opm REAL, npm REAL,
        op REAL, ni REAL, dps REAL, bps REAL, fetched TEXT, PRIMARY KEY (code, period));
    CREATE TABLE IF NOT EXISTS fin_fetch (code TEXT PRIMARY KEY, fetched TEXT, ok INTEGER);
    """)


def store(conn, code, fin, today=None):
    """score_auto.fetch_finance(code,'annual') 결과를 연도별 한 줄씩 저장 → 저장한 연도 수"""
    today = (today or dt.date.today()).isoformat()
    n = 0
    for key, consensus in fin["periods"]:
        vals = {col: fin["rows"].get(title, {}).get(key) for title, col in METRICS.items()}
        if all(v is None for v in vals.values()):
            continue
        conn.execute("INSERT OR REPLACE INTO fin_annual (code, period, consensus, eps, per, pbr, roe, debt, opm, npm, op, ni, dps, bps, fetched)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (code, key, 1 if consensus else 0, *(vals[c] for c in ("eps", "per", "pbr", "roe", "debt", "opm", "npm", "op", "ni", "dps", "bps")), today))
        n += 1
    conn.execute("INSERT OR REPLACE INTO fin_fetch VALUES (?,?,1)", (code, today))
    conn.commit()
    return n


def sync(db_path, log=print, sleep=0.4, session=None, today=None, max_age=REFRESH_DAYS):
    """공시에 나온 종목(+점수표 종목)의 연간 재무를 받아 저장. 최근 max_age일 안에 받은 종목은 건너뜀."""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        try:
            codes = [r[0] for r in conn.execute("SELECT DISTINCT 종목코드 FROM summary WHERE 종목코드 IS NOT NULL AND 종목코드 != ''")]
        except sqlite3.OperationalError:
            return {"대상": 0, "저장": 0, "건너뜀": 0, "실패": 0}
        try:
            codes += [r[0] for r in conn.execute("SELECT code FROM scorecard_stock") if r[0] not in codes]
        except sqlite3.OperationalError:
            pass
        now = today or dt.date.today()
        fresh = {r[0] for r in conn.execute("SELECT code FROM fin_fetch WHERE fetched >= ?",
                                           ((now - dt.timedelta(days=max_age)).isoformat(),))}
        todo = [c for c in codes if c not in fresh]
        saved = failed = streak = 0
        for i, code in enumerate(todo):
            if streak >= MAX_FAIL_STREAK:
                log(f"재무: {MAX_FAIL_STREAK}번 연속 실패해서 나머지 {len(todo) - i}개는 다음에 받습니다.")
                break
            try:
                store(conn, code, score_auto.fetch_finance(code, "annual", session), today=now)
                saved += 1
                streak = 0
            except Exception as e:
                failed += 1
                streak += 1
                log(f"재무 {code} 실패: {e}")
            time.sleep(sleep)
        log(f"재무(연간): 대상 {len(codes)}개 · 새로 저장 {saved} · 실패 {failed} · 최근에 받음 {len(codes) - len(todo)}")
        return {"대상": len(codes), "저장": saved, "건너뜀": len(codes) - len(todo), "실패": failed}
    finally:
        conn.close()


def load_all(conn):
    """{종목코드: [연도 dict…]} 오래된 연도부터"""
    try:
        cur = conn.execute("SELECT code, period, consensus, eps, per, pbr, roe, debt, opm, npm, op, ni, dps, bps FROM fin_annual ORDER BY code, period")
    except sqlite3.OperationalError:
        return {}
    keys = ["code", "period", "consensus", "eps", "per", "pbr", "roe", "debt", "opm", "npm", "op", "ni", "dps", "bps"]
    out = {}
    for row in cur.fetchall():
        d = dict(zip(keys, row))
        out.setdefault(d["code"], []).append(d)
    return out


def actual(years, n=3):
    """확정 연도(컨센서스 아님) 최근 n개, 오래된 것부터"""
    return [y for y in years if not y["consensus"]][-n:]


def forward(years):
    """컨센서스(예상) 연도 중 가장 가까운 것"""
    f = [y for y in years if y["consensus"]]
    return f[0] if f else None


# ---------------- 피터 린치 ----------------
LYNCH_HINT = {
    "급성장주": "이익이 빠르게 느는 기업. 성장이 이어지는 동안 보유하고, PEG가 1 아래일 때 매력적이에요. 성장이 눈에 띄게 꺾이면 점검해야 해요.",
    "우량주": "꾸준히 크는 큰 기업. 크게 오르기보다 안정적으로 보유하고, 많이 오르면 일부 차익을 생각해요.",
    "저성장주": "성장이 느린 기업. 오르는 것보다 배당이 포인트라서 배당 여력과 안정성을 같이 봐요.",
    "경기순환주": "경기에 따라 이익이 크게 오르내리는 기업. 이익이 최고일 때 PER이 낮아 보여서 오히려 고점일 수 있어요.",
    "턴어라운드": "적자나 부진에서 막 회복한 기업. 회복이 이어지는지, 부채·현금이 버틸 만한지 확인이 먼저예요.",
    "적자 기업": "최근 연도 이익이 없는 기업. 린치는 이익이 없는 회사를 신중하게 봐서, 이 점수 방식으로는 매력이 낮게 나와요.",
    "판단 불가": "연간 재무가 모자라서(확정 연도 2개 미만) 유형을 나누지 못했어요.",
}


def _cagr(first, last, intervals):
    if first is None or last is None or first <= 0 or last <= 0 or intervals < 1:
        return None
    return (last / first) ** (1 / intervals) - 1


def lynch(years, per_now, pbr_now=None):
    """→ dict(category, tags, growth, fwd_growth, peg, peg_fwd, score, items[(이름, 점수, 만점, 설명)], ok)
    years: 연도 dict 목록 (오래된 순), per_now: 현재 PER."""
    act = actual(years, 3)
    eps = [y["eps"] for y in act]
    res = dict(ok=False, category="판단 불가", tags=[], growth=None, fwd_growth=None, peg=None, peg_fwd=None, score=0, items=[])
    if len(act) < 2 or any(e is None for e in eps):
        return res
    res["ok"] = True
    g = _cagr(eps[0], eps[-1], len(eps) - 1)
    fw = forward(years)
    fwd_g = (fw["eps"] / eps[-1] - 1) if fw and fw.get("eps") and eps[-1] and eps[-1] > 0 and fw["eps"] > 0 else None
    res["growth"], res["fwd_growth"] = g, fwd_g
    # 유형
    yoy = [(eps[i] / eps[i - 1] - 1) for i in range(1, len(eps)) if eps[i - 1] and eps[i - 1] > 0]
    if eps[-1] <= 0:
        cat = "적자 기업"
    elif any(e <= 0 for e in eps[:-1]):
        cat = "턴어라운드"
    elif yoy and max(yoy) > 0.3 and min(yoy) < -0.3:
        cat = "경기순환주"
    elif g is None:
        cat = "판단 불가"
    elif g >= 0.20:
        cat = "급성장주"
    elif g >= 0.10:
        cat = "우량주"
    else:
        cat = "저성장주"
    res["category"] = cat
    if pbr_now is not None and 0 < pbr_now < 0.7 and eps[-1] > 0:
        res["tags"].append("자산주 후보 (PBR 낮음)")
    # PEG (과거 3년 성장률 기준 / 예상 성장률 기준)
    if g is not None and g < 0:
        res["tags"].append("이익이 줄고 있어요")
    # 성장률이 50%를 넘으면 바닥에서 반등한 경우가 많아서, PEG는 50%까지만 인정해서 계산 (린치도 너무 높은 성장은 오래 못 간다고 경계)
    if per_now and per_now > 0 and g and g > 0:
        res["peg"] = per_now / (min(g, CAP_GROWTH) * 100)
        if g > CAP_GROWTH:
            res["tags"].append("성장률 50% 초과 (PEG는 50%로 계산)")
    if per_now and per_now > 0 and fwd_g and fwd_g > 0:
        res["peg_fwd"] = per_now / (min(fwd_g, CAP_GROWTH) * 100)
    items = []
    peg = res["peg"]
    pts = 0 if peg is None else 35 if peg <= 0.5 else 28 if peg <= 1.0 else 15 if peg <= 1.5 else 5 if peg <= 2.0 else 0
    items.append(("PEG (PER ÷ 이익 성장률)", pts, 35,
                  "계산 못 함 (성장률이 0 이하이거나 PER이 없어요)" if peg is None else f"{peg:.2f} (≤0.5 최고 · ≤1 좋음 · ≤1.5 보통 · ≤2 낮음)"))
    pts = 0 if g is None else (20 if 0.20 <= g <= 0.50 else 8 if g > 0.50 else 12 if g >= 0.10 else 5 if g > 0 else 0)
    items.append(("이익 성장률 (EPS 연평균)", pts, 20,
                  "계산 못 함" if g is None else f"{g:+.1%}" + (" — 너무 높으면 오래 못 가는 경우가 많아요" if g > 0.5 else "")))
    debt = act[-1].get("debt")
    pts = 0 if debt is None else 15 if debt <= 50 else 10 if debt <= 100 else 3 if debt <= 200 else 0
    items.append(("부채비율 (낮을수록)", pts, 15, "자료 없음" if debt is None else f"{debt:.0f}% (≤50 최고 · ≤100 좋음 · ≤200 보통)"))
    pts = 10 if all(e > 0 for e in eps) else 0
    items.append(("이익이 꾸준히 흑자", pts, 10, f"최근 {len(eps)}개 연도 EPS " + ("모두 흑자" if pts else "적자 연도가 있어요")))
    roe = act[-1].get("roe")
    pts = 0 if roe is None else 10 if roe >= 15 else 6 if roe >= 10 else 2 if roe >= 5 else 0
    items.append(("ROE (자본 효율)", pts, 10, "자료 없음" if roe is None else f"{roe:.1f}%"))
    pts = 0 if not per_now or per_now <= 0 else 10 if per_now <= 15 else 6 if per_now <= 25 else 0
    items.append(("PER이 상식 선인지", pts, 10, "PER 없음" if not per_now else f"{per_now:.1f}배 (≤15 좋음 · ≤25 보통)"))
    res["items"] = items
    res["score"] = sum(i[1] for i in items)
    return res


# ---------------- 워렌 버핏 ----------------
def buffett(years, per_now, pbr_now=None):
    """→ dict(score, items, moat, ok). 확정 연도 2개 이상이 있어야 점수를 냄"""
    act = actual(years, 3)
    res = dict(ok=False, score=0, items=[], moat=False)
    if len(act) < 2:
        return res
    res["ok"] = True
    roes = [y["roe"] for y in act if y.get("roe") is not None]
    opms = [y["opm"] for y in act if y.get("opm") is not None]
    eps = [y["eps"] for y in act if y.get("eps") is not None]
    items = []
    if roes:
        avg, mn = sum(roes) / len(roes), min(roes)
        pts = 25 if avg >= 15 and mn >= 12 else 15 if avg >= 12 else 8 if avg >= 8 else 0
        items.append(("ROE (자본으로 얼마나 버는가)", pts, 25, f"평균 {avg:.1f}% · 가장 낮은 해 {mn:.1f}% (평균 15%↑·최저 12%↑ 최고)"))
    else:
        avg = None
        items.append(("ROE (자본으로 얼마나 버는가)", 0, 25, "자료 없음"))
    debt = act[-1].get("debt")
    pts = 0 if debt is None else 15 if debt <= 50 else 9 if debt <= 100 else 4 if debt <= 150 else 0
    items.append(("부채비율 (적을수록)", pts, 15, "자료 없음" if debt is None else f"{debt:.0f}% (≤50 최고 · ≤100 좋음 · ≤150 보통) — 금융업은 구조상 높아요"))
    ni = [y["ni"] for y in act if y.get("ni") is not None]
    op = [y["op"] for y in act if y.get("op") is not None]
    steady = len(ni) == len(act) and len(op) == len(act) and all(v > 0 for v in ni) and all(v > 0 for v in op)
    rising = len(eps) >= 2 and all(eps[i] > eps[i - 1] for i in range(1, len(eps))) and eps[-1] > 0     # 해마다 실제로 늘어야 함 (제자리는 아님)
    pts = (10 if steady else 0) + (5 if rising else 0)
    items.append(("이익이 꾸준하고 늘어나는가", pts, 15,
                  f"{len(act)}개 연도 " + ("영업이익·순이익 모두 흑자" if steady else "적자 연도가 있어요") + (" · EPS 해마다 증가" if rising else "")))
    if opms:
        oavg = sum(opms) / len(opms)
        pts = 15 if oavg >= 20 else 10 if oavg >= 12 else 5 if oavg >= 7 else 0
        items.append(("영업이익률 (가격 결정력의 흔적)", pts, 15, f"평균 {oavg:.1f}% (≥20 최고 · ≥12 좋음 · ≥7 보통)"))
    else:
        oavg = None
        items.append(("영업이익률 (가격 결정력의 흔적)", 0, 15, "자료 없음"))
    pts = 0 if not per_now or per_now <= 0 else 15 if per_now <= 12 else 10 if per_now <= 15 else 5 if per_now <= 20 else 0
    items.append(("가격이 합리적인가 (안전마진)", pts, 15, "PER 없음 (적자)" if not per_now or per_now <= 0 else f"PER {per_now:.1f}배 (≤12 최고 · ≤15 좋음 · ≤20 보통)"))
    g = _cagr(eps[0], eps[-1], len(eps) - 1) if len(eps) >= 2 else None
    pts = 0 if g is None else 15 if g >= 0.12 else 10 if g >= 0.08 else 5 if g > 0 else 0
    items.append(("이익 성장 (EPS 연평균)", pts, 15, "계산 못 함" if g is None else f"{g:+.1%} (≥12% 최고 · ≥8% 좋음)"))
    res["items"] = items
    res["score"] = sum(i[1] for i in items)
    res["moat"] = bool(avg is not None and oavg is not None and avg >= 15 and min(roes) >= 12 and oavg >= 15 and min(opms) >= 10 and steady)
    return res
