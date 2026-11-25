"""텐배거(주가 10배) 체질 점검: 과거에 크게 오른 종목들에서 자주 보이는 특징(작은 시가총액, 높은 매출 성장, 흑자 전환·마진 개선,
건전한 재무, 상승 추세)을 재무제표 분석 결과로 점수화한다. 점수가 높다고 10배가 되는 것이 아니고, 대부분은 10배에 못 미친다."""
import json
import sqlite3


def _f(x):
    return x if isinstance(x, (int, float)) else None


def score(m, mcap=None, trend=None, per=None):
    """m: 재무 지표 dict (fin_analyzer.metrics), mcap: 시가총액(억원), trend: dict(above_ma, r3m) → dict(score, parts, good[], risk[])"""
    good, risk, parts = [], [], {}
    # 1) 매출 성장 (30)
    g = 0
    ry, rq = _f(m.get("rev_yoy")), _f(m.get("rev_q_yoy"))
    if ry is not None:
        g = 30 if ry >= 1.0 else 24 if ry >= 0.5 else 18 if ry >= 0.3 else 8 if ry >= 0.15 else 0
        if g:
            good.append(f"매출 {ry:+.0%} 성장")
        if rq is not None and ry > 0 and rq > ry + 0.05:
            g = min(30, g + 3)
            good.append(f"최근 분기 성장 가속 ({rq:+.0%})")
    parts["성장"] = g
    # 2) 이익 전환·개선 (25)
    p = 0
    op, op_py, opm, opm_py, oy = _f(m.get("op")), _f(m.get("op_py")), _f(m.get("opm")), _f(m.get("opm_py")), _f(m.get("op_yoy"))
    if op is not None and op_py is not None and op > 0 >= op_py:
        p += 20
        good.append("영업이익 흑자 전환")
    elif oy is not None and oy > 0 and (op is None or op > 0):         # 적자 폭이 줄어든 것은 증가율로 세지 않음
        add = 16 if oy >= 1.0 else 10 if oy >= 0.3 else 4
        p += add
        good.append(f"영업이익 {oy:+.0%}")
    if opm is not None and opm >= 0.10:
        p += 5
        good.append(f"영업이익률 {opm:.0%}")
    if opm is not None and opm_py is not None and opm - opm_py >= 0.03:
        p += 5
        good.append(f"영업이익률 {opm - opm_py:+.1%}p 개선")
    parts["이익"] = min(25, p)
    # 3) 재무 안정 (15)
    s = 0
    dr, ic, oc = _f(m.get("debt_ratio")), _f(m.get("interest_cover")), _f(m.get("ocf_to_ni"))
    if dr is not None:
        s += 8 if dr < 0.5 else 5 if dr < 1.0 else 2 if dr < 2.0 else 0
        if dr >= 2.0:
            risk.append(f"부채비율 {dr:.0%}")
    if ic is not None:
        s += 4 if ic >= 5 else 2 if ic >= 2 else 0
        if ic < 1:
            risk.append("이자도 못 갚는 수준 (이자보상배율 1 미만)")
    if oc is not None and oc >= 0.8:
        s += 3
        good.append("이익이 현금으로 들어옴")
    parts["안정"] = min(15, s)
    if op is not None and op < 0:
        risk.append("영업적자")
        if _f(m.get("fcf")) is not None and m["fcf"] < 0:
            risk.append("현금도 계속 빠져나감 (잉여현금흐름 −)")
    # 4) 크기 (15): 작을수록 10배 여지가 큼, 너무 작으면 위험
    z = 0
    if mcap:
        z = 15 if 300 <= mcap < 3000 else 8 if 3000 <= mcap < 10000 else 6 if 100 <= mcap < 300 else 0
        if mcap >= 10000:
            risk.append(f"시총 {mcap:,.0f}억 — 10배가 되려면 시총 {mcap * 10 / 10000:,.0f}조라 어려움")
        elif mcap < 100:
            risk.append(f"시총 {mcap:,.0f}억 — 너무 작아 거래·상장 유지 위험")
        elif mcap < 3000:
            good.append(f"시총 {mcap:,.0f}억 (작은 편)")
    parts["크기"] = z
    # 5) 주가 추세·밸류 (10)
    t = 0
    if trend:
        if trend.get("above_ma"):
            t += 4
            good.append("200일선 위")
        if (trend.get("r3m") or 0) > 0:
            t += 3
    if per is not None and 0 < per < 25:
        t += 3
        good.append(f"PER {per:.1f}")
    elif per is not None and per >= 60:
        risk.append(f"PER {per:.0f} — 이미 비싸게 평가됨")
    parts["추세"] = t
    total = parts["성장"] + parts["이익"] + parts["안정"] + parts["크기"] + parts["추세"]
    if op is not None and op < 0:
        total = max(0, total - 10)           # 적자는 감점
    return dict(score=total, parts=parts, good=good, risk=risk)


def rank(conn, trend_fn=None, limit=400):
    """재무제표 분석이 된 종목을 점수순으로 → [dict(code, name, score, parts, good, risk, mcap, float_real)]"""
    try:
        rows = conn.execute("SELECT code, name, m, year, rcode FROM fin_report WHERE m IS NOT NULL").fetchall()
    except sqlite3.Error:
        return []
    order = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
    best = {}
    for r in rows:
        k = (r[3], order.get(r[4], 0))
        if r[0] not in best or k > best[r[0]][0]:
            best[r[0]] = (k, r)
    mc = {}
    try:
        for c, mcap, fr, nm in conn.execute("SELECT code, mcap, float_real, name FROM float_stock"):
            mc[c] = (mcap, fr, nm)
    except sqlite3.Error:
        pass
    pers = {}
    try:
        pers = {c: p for c, p in conn.execute("SELECT code, per FROM valuation")}
    except sqlite3.Error:
        pass
    out = []
    for code, (_, r) in best.items():
        try:
            m = json.loads(r[2] or "{}")
        except ValueError:
            continue
        mcap, fr, nm = mc.get(code, (None, None, None))
        tr = trend_fn(code) if trend_fn else None
        sc = score(m, mcap, tr, pers.get(code))
        sc.update(code=code, name=r[1] or nm or code, mcap=mcap, float_real=fr, period=f"{r[3]} {r[4]}", has_size=mcap is not None)
        out.append(sc)
    out.sort(key=lambda x: -x["score"])
    return out[:limit]
