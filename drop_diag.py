# -*- coding: utf-8 -*-
"""
하락 원인 점검: 코스피 하락에서 어느 종목·묶음이 얼마나 끌어내렸는지(시가총액 기여도)와, 고점 대비 낙폭.

기여도 = 종목 시가총액 변화 ÷ 시작일 코스피 전체 시가총액 (%p). 시가총액 가중 지수의 변화를 종목별로 나눠 놓은 근삿값이에요
  (공식 지수는 유동주식 비율·지수 편입 방식이 달라서 숫자가 조금 다를 수 있어요). 앱이 받아 둔 KRX 일별 시세(krx_daily)만 쓰고,
  보관한 거래일(기본 25일 안팎) 안에서만 계산해요. 원인을 단정하지 않고 '누가 얼마나 빠졌나'만 보여 줘요.
"""
import sqlite3

SEMI = {"005930": "삼성전자", "005935": "삼성전자우", "000660": "SK하이닉스"}


def days(conn):
    try:
        return [r[0] for r in conn.execute("SELECT DISTINCT bas_dd FROM krx_daily WHERE market='코스피' ORDER BY bas_dd")]
    except sqlite3.OperationalError:
        return []


def _caps(conn, day):
    return {c: (n, m) for c, n, m in conn.execute(
        "SELECT code, name, mktcap FROM krx_daily WHERE bas_dd=? AND market='코스피' AND mktcap > 0", (day,))}


def contrib(conn, start, end, top=8):
    """start → end 사이 기여도. {start, end, total, semi, other, down:[(이름, %p)], up:[(이름, %p)]} 또는 None"""
    a, b = _caps(conn, start), _caps(conn, end)
    base = sum(m for _, m in a.values())
    if not a or not b or base <= 0:
        return None
    rows = [(c, b[c][0] or a[c][0], (b[c][1] - a[c][1]) / base * 100) for c in a if c in b]
    total = sum(r[2] for r in rows)
    semi = sum(r[2] for r in rows if r[0] in SEMI)
    by = sorted(rows, key=lambda r: r[2])
    return dict(start=start, end=end, total=total, semi=semi, other=total - semi, n=len(rows),
                down=[(n, v) for _, n, v in by[:top] if v < 0], up=[(n, v) for _, n, v in reversed(by[-top:]) if v > 0])


def windows(conn):
    """[(라벨, 시작일, 끝일)] — 1거래일 · 5거래일 · 앱이 받아 둔 시세 중 시총 고점부터 · 받아 둔 시세 첫날부터 (내 보유 종목과 무관)"""
    ds = days(conn)
    if len(ds) < 2:
        return []
    end = ds[-1]
    out = [("하루 (직전 거래일 대비)", ds[-2], end)]
    if len(ds) >= 6:
        out.append(("최근 5거래일", ds[-6], end))
    tot = {}
    for d in ds:
        tot[d] = sum(m for _, m in _caps(conn, d).values())
    peak = max(tot, key=tot.get)
    if peak != end and peak not in (ds[-2], ds[-6] if len(ds) >= 6 else None):
        out.append((f"받아 둔 시세 중 시총 고점({peak[4:6]}/{peak[6:]})부터", peak, end))
    if ds[0] not in (peak, ds[-2], ds[-6] if len(ds) >= 6 else None):
        out.append((f"받아 둔 시세의 첫날({ds[0][4:6]}/{ds[0][6:]})부터", ds[0], end))
    return out


def drawdown(points, lookback=250):
    """[(YYYY-MM-DD, 종가)] → dict(last, high, high_date, dd(%), since_days) — 최근 lookback개 안의 고점 대비. 자료가 없으면 None"""
    pts = [(d, v) for d, v in points if v][-lookback:]
    if len(pts) < 2:
        return None
    hd, hv = max(pts, key=lambda x: x[1])
    d, v = pts[-1]
    return dict(last=v, last_date=d, high=hv, high_date=hd, dd=(v / hv - 1) * 100)


def by_industry(conn, start, end, top=6):
    """업종별 기여도(%p). industry_map(네이버 업종)이 있을 때만. {down:[(업종, %p)], up:[(업종, %p)], unmapped(%p), mapped_share(%)} 또는 None"""
    try:
        import industry
        m = industry.mapping(conn)
    except Exception:
        return None
    a, b = _caps(conn, start), _caps(conn, end)
    base = sum(x for _, x in a.values())
    if not m or not a or not b or base <= 0:
        return None
    agg, unmapped, mapped_cap = {}, 0.0, 0.0
    for c in a:
        if c not in b:
            continue
        pp = (b[c][1] - a[c][1]) / base * 100
        g = m.get(c)
        if g:
            agg[g] = agg.get(g, 0.0) + pp
            mapped_cap += a[c][1]
        else:
            unmapped += pp
    if not agg:
        return None
    by = sorted(agg.items(), key=lambda x: x[1])
    return dict(down=[x for x in by[:top] if x[1] < 0], up=[x for x in reversed(by[-top:]) if x[1] > 0], unmapped=unmapped, mapped_share=100.0 * mapped_cap / base)


# ---------------- 원인 신호등 ----------------
STATE_TXT = {"on": "켜짐", "watch": "주의", "off": "꺼짐", "na": "자료 없음"}


def lights(conn):
    """하락 원인별 신호등 [{id, name, state(on/watch/off/na), evidence, rule}]. 원인을 단정하지 않고 '지금 이 신호가 켜져 있나'만 알려 줘요.
    기준은 단순한 값이라 틀릴 수 있고, 여러 개가 동시에 켜지는 게 보통이에요."""
    import deriv_bond as dvb
    import stock_market
    out = []
    # 1) 금리 충격: 미국 10년 3개월 변화
    us = dvb.series(conn, "bond:us10")
    it = dict(id="rate", name="금리 충격", state="na", evidence="미국 금리 자료가 아직 없어요", rule="미국 10년 3개월 +0.50%p 이상 켜짐 · +0.25%p 이상 주의")
    if us:
        last, prev = us[-1][1], dvb._ago(us, 91)
        if prev is not None:
            d = last - prev
            it.update(state="on" if d >= 0.5 else "watch" if d >= 0.25 else "off", evidence=f"미국 10년 {last:.2f}% (3개월 {d:+.2f}%p)")
        else:
            it["evidence"] = f"미국 10년 {last:.2f}% (3개월 전 자료 없음)"
    out.append(it)
    # 2) 업종 쏠림: 최근 5거래일(없으면 하루) 코스피 하락 중 삼성전자·SK하이닉스 몫
    it = dict(id="concentration", name="반도체 쏠림", state="na", evidence="KRX 시세가 아직 부족해요", rule="코스피 시총 -1% 이하일 때 두 종목 몫 60% 이상 켜짐 · 40% 이상 주의")
    wins = windows(conn)
    pick = next((w for w in wins if w[0].startswith("최근 5")), wins[0] if wins else None)
    if pick:
        r = contrib(conn, pick[1], pick[2])
        if r:
            if r["total"] > -1.0:
                it.update(state="off", evidence=f"{pick[0]} 코스피 시총 {r['total']:+.2f}% (하락이 -1% 미만이라 따지지 않아요)")
            else:
                share = r["semi"] / r["total"]
                it.update(state="on" if share >= 0.6 else "watch" if share >= 0.4 else "off",
                          evidence=f"{pick[0]} 코스피 {r['total']:+.2f}% 중 삼성전자·SK하이닉스 {r['semi']:+.2f}%p ({share * 100:.0f}%)")
    out.append(it)
    # 3) 외국인 수급: 선물·채권 탭의 외국인 코스피 현물 순매수 신호 (5일·20일 합 방향)
    sigs = {s["id"]: s for s in dvb.signals(conn)}
    it = dict(id="foreign", name="외국인 매도", state="na", evidence="외국인 순매수 자료가 아직 없어요", rule="외국인 5일·20일 합이 모두 순매도면 켜짐 · 엇갈리면 주의")
    sg = sigs.get("spot_foreign")
    if sg and sg.get("signal") is not None:
        it.update(state="on" if sg["signal"] < 0 else "watch" if sg["signal"] == 0 else "off", evidence=f"{sg['value']} · {sg['change']} (억원)")
    out.append(it)
    # 4) 환율: 원/달러 3개월 변화
    inds = {i.key: i for i in stock_market.indicators(conn)}
    fx = inds.get("usdkrw")
    it = dict(id="fx", name="원화 약세", state="na", evidence="환율 자료가 아직 없어요 (ECOS 키)", rule="원/달러 3개월 +3% 이상 켜짐 · +1.5% 이상 주의")
    if fx and fx.available and fx.change_text:
        try:
            d = float(fx.change_text.split()[-1].replace("%", "")) / 100
            it.update(state="on" if d >= 0.03 else "watch" if d >= 0.015 else "off", evidence=f"{fx.value_text} ({fx.change_text})")
        except ValueError:
            it["evidence"] = f"{fx.value_text} ({fx.change_text})"
    out.append(it)
    # 5) 신용: 회사채 스프레드 3개월 변화 (선물·채권 탭의 신호)
    it = dict(id="credit", name="신용 경색", state="na", evidence="회사채 스프레드 자료가 아직 없어요 (ECOS 키)", rule="회사채 스프레드 3개월 +0.15%p 이상 켜짐")
    sg = sigs.get("credit")
    if sg and sg.get("signal") is not None:
        it.update(state="on" if sg["signal"] < 0 else "off", evidence=f"{sg['value']} ({sg['change']})")
    out.append(it)
    return out
