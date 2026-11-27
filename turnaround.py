"""흑자 전환 임박: 아직 영업적자인데 매출이 빠르게 늘고, 분기마다 손실이 줄거나 막 분기 흑자로 돌아선 종목.
분기별 영업이익률 추이와 매출 성장·현금흐름·현금 여력으로 점수화한다. 점수가 높아도 전환이 보장되지는 않는다."""
import json
import sqlite3

ORDER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def _f(x):
    return x if isinstance(x, (int, float)) else None


def quarter_margins(hist):
    """보고서 목록 [(연도, 보고서코드, m)] → 분기 영업이익률 추이 [(라벨, 비율)] (사업보고서는 연간 값이라 뺌)"""
    out = []
    for y, rc, m in sorted(hist, key=lambda x: (x[0], ORDER.get(x[1], 0))):
        if rc == "11011":
            continue
        op, rev = _f(m.get("op_q")), _f(m.get("rev_q"))
        q = {"11013": "1Q", "11012": "2Q", "11014": "3Q"}.get(rc, rc)
        if op is not None and rev and rev > 0:
            out.append((f"{y} {q}", op / rev))
    return out[-5:]


def score(m, hist=None, cons_next_op=None):
    """m: 최신 재무 지표, hist: [(연도, 보고서코드, m)] 과거 보고서들, cons_next_op: 다음 연도 영업이익 컨센서스 → dict(score, parts, good, risk, trend, q_profit) 또는 None(대상 아님)"""
    op, op_q, rev_q = _f(m.get("op")), _f(m.get("op_q")), _f(m.get("rev_q"))
    loss_ytd = op is not None and op < 0
    loss_q = op_q is not None and op_q < 0
    q_profit = op_q is not None and op_q >= 0 and loss_ytd             # 연초부터는 적자인데 이번 분기는 흑자
    if not (loss_ytd or loss_q):
        return None                                                    # 이미 흑자: 전환 대상이 아님
    good, risk, parts = [], [], {}
    # 성장 (25)
    g = 0
    ry, rq = _f(m.get("rev_yoy")), _f(m.get("rev_q_yoy"))
    if ry is not None:
        g = 25 if ry >= 1.0 else 20 if ry >= 0.5 else 14 if ry >= 0.3 else 7 if ry >= 0.15 else 0
        if g:
            good.append(f"매출 {ry:+.0%}")
        if rq is not None and ry > 0 and rq > ry + 0.05:
            g = min(25, g + 3)
            good.append(f"최근 분기 성장 가속 ({rq:+.0%})")
        if ry < 0:
            risk.append(f"매출이 줄고 있어요 ({ry:+.0%})")
    parts["성장"] = g
    # 손실 축소 (30)
    s = 0
    oqy = _f(m.get("op_q_py"))
    if op_q is not None and oqy is not None and oqy < 0 and op_q < 0 and op_q > oqy:
        r = (op_q - oqy) / abs(oqy)
        s += 15 if r >= 0.5 else 9 if r >= 0.2 else 4
        good.append(f"분기 영업손실 {r:.0%} 줄었어요 (전년 같은 분기 대비)")
    opm, opm_py = _f(m.get("opm")), _f(m.get("opm_py"))
    if opm is not None and opm_py is not None and opm < 0 and opm > opm_py:
        d = opm - opm_py
        s += 15 if d >= 0.10 else 9 if d >= 0.03 else 4
        good.append(f"영업이익률 {opm_py:.0%} → {opm:.0%}")
    parts["손실 축소"] = min(30, s)
    # 분기 흑자·근접 (20)
    z = 0
    if op_q is not None and rev_q and rev_q > 0:
        mg = op_q / rev_q
        if op_q >= 0:
            z = 20
            good.append("이번 분기 영업이익 흑자")
        elif mg >= -0.03:
            z, _ = 14, good.append(f"분기 영업이익률 {mg:.1%} — 손익분기 근처")
        elif mg >= -0.08:
            z, _ = 8, good.append(f"분기 영업이익률 {mg:.1%}")
    parts["분기 흑자"] = z
    # 추이 (10): 분기 영업이익률이 연속으로 개선
    trend = quarter_margins(hist or [])
    up = 0
    for a, b in zip(trend, trend[1:]):
        up = up + 1 if b[1] > a[1] else 0
    t = 10 if up >= 3 else 6 if up == 2 else 0
    if up >= 2:
        good.append(f"분기 영업이익률 {up}분기 연속 개선")
    parts["추이"] = t
    # 수익 구조·현금 (10)
    c = 0
    gpm, gpm_py = _f(m.get("gpm")), _f(m.get("gpm_py"))
    if gpm is not None and gpm > 0:
        c += 3
        if gpm_py is not None and gpm > gpm_py:
            c += 2
            good.append(f"매출총이익률 {gpm_py:.0%} → {gpm:.0%}")
    ocf = _f(m.get("ocf"))
    if ocf is not None and ocf > 0:
        c += 5
        good.append("영업현금흐름 흑자")
    parts["구조·현금"] = min(10, c)
    # 컨센서스 (5)
    k = 0
    if cons_next_op is not None and cons_next_op > 0:
        k = 5
        good.append("증권사 컨센서스가 영업이익 흑자를 예상")
    parts["전망"] = k
    # 위험: 현금 여력
    runway = None
    cash, months = _f(m.get("cash")), _f(m.get("months")) or 12
    if ocf is not None and ocf < 0 and cash is not None and cash > 0:
        monthly = -ocf / months
        runway = cash / monthly if monthly else None
        if runway is not None and runway < 12:
            risk.append(f"현금이 현재 속도로 약 {runway:.0f}개월치 — 증자·전환사채(희석) 가능성")
    ic, dr = _f(m.get("interest_cover")), _f(m.get("debt_ratio"))
    if ic is not None and ic < 1 and op is not None and op < 0:
        risk.append("영업이익으로 이자를 못 내요")
    if dr is not None and dr >= 2.0:
        risk.append(f"부채비율 {dr:.0%}")
    total = sum(parts.values())
    if runway is not None and runway < 9:
        total = max(0, total - 10)
    if ry is not None and ry < 0:
        total = max(0, total - 10)
    return dict(score=total, parts=parts, good=good, risk=risk, trend=trend, q_profit=q_profit, runway=runway, up=up)


def rank(conn, limit=300):
    """재무제표 분석이 된 종목 중 적자(또는 분기 적자)인 종목을 점수순으로 → [dict]"""
    try:
        rows = conn.execute("SELECT code, name, year, rcode, m FROM fin_report WHERE m IS NOT NULL").fetchall()
    except sqlite3.Error:
        return []
    by = {}
    for code, name, y, rc, m_ in rows:
        try:
            m = json.loads(m_ or "{}")
        except ValueError:
            continue
        by.setdefault(code, {"name": name, "h": []})["h"].append((y, rc, m))
    cons = {}
    try:        # 다음 해(가장 큰 연도) 연간 영업이익 컨센서스
        for code, per, op in conn.execute("SELECT code, period, op FROM earn_fin WHERE kind='A' AND cons=1 ORDER BY period"):
            cons[code] = op
    except sqlite3.Error:
        pass
    mc = {}
    try:
        for c, mcap, fr in conn.execute("SELECT code, mcap, float_real FROM float_stock"):
            mc[c] = (mcap, fr)
    except sqlite3.Error:
        pass
    out = []
    for code, d in by.items():
        d["h"].sort(key=lambda x: (x[0], ORDER.get(x[1], 0)))
        y, rc, m = d["h"][-1]
        sc = score(m, d["h"][:-1] + [d["h"][-1]], cons.get(code))
        if not sc:
            continue
        sc.update(code=code, name=d["name"] or code, period=f"{y} {rc}", mcap=(mc.get(code) or (None, None))[0])
        out.append(sc)
    out.sort(key=lambda r: -r["score"])
    return out[:limit]
