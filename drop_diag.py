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
RANK = {"na": -1, "off": 0, "watch": 1, "on": 2}


def _ago(pts, days):
    import deriv_bond as dvb
    return dvb._ago(pts, days)


def rate_state(us):
    """미국 10년 [(날짜, %)] → (state, 근거). 3개월 +0.50%p 이상 켜짐 · +0.25%p 이상 주의"""
    if not us:
        return "na", "미국 금리 자료가 아직 없어요"
    last, prev = us[-1][1], _ago(us, 91)
    if prev is None:
        return "na", f"미국 10년 {last:.2f}% (3개월 전 자료 없음)"
    d = last - prev
    return ("on" if d >= 0.5 else "watch" if d >= 0.25 else "off"), f"미국 10년 {last:.2f}% (3개월 {d:+.2f}%p)"


def fx_state(fx):
    """원/달러 [(날짜, 원)] → (state, 근거). 3개월 +3% 이상 켜짐 · +1.5% 이상 주의"""
    if not fx:
        return "na", "환율 자료가 아직 없어요 (ECOS 키)"
    last, prev = fx[-1][1], _ago(fx, 91)
    if not prev:
        return "na", f"{last:,.1f}원 (3개월 전 자료 없음)"
    d = last / prev - 1
    return ("on" if d >= 0.03 else "watch" if d >= 0.015 else "off"), f"{last:,.1f}원 (3개월 {d:+.1%})"


def credit_state(corp, ktb):
    """회사채 3년(AA-) − 국고채 3년 스프레드 → (state, 근거). 3개월 +0.15%p 이상 켜짐 · +0.08%p 이상 주의"""
    c, k = dict(corp), dict(ktb)
    sp = [(d, c[d] - k[d]) for d in sorted(set(c) & set(k))]
    if not sp:
        return "na", "회사채 스프레드 자료가 아직 없어요 (ECOS 키)"
    last, prev = sp[-1][1], _ago(sp, 91)
    if prev is None:
        return "na", f"스프레드 {last:+.2f}%p (3개월 전 자료 없음)"
    d = last - prev
    return ("on" if d >= 0.15 else "watch" if d >= 0.08 else "off"), f"스프레드 {last:+.2f}%p (3개월 {d:+.2f}%p)"


def lights(conn, today=None):
    """하락 원인별 신호등 [{id, name, state(on/watch/off/na), evidence, rule}]. 원인을 단정하지 않고 '지금 이 신호가 켜져 있나'만 알려 줘요.
    기준은 단순한 값이라 틀릴 수 있고, 여러 개가 동시에 켜지는 게 보통이에요."""
    import deriv_bond as dvb
    import stock_market as sm
    out = []
    st, ev = rate_state(dvb.series(conn, "bond:us10"))
    out.append(dict(id="rate", name="금리 충격", state=st, evidence=ev, rule="미국 10년 3개월 +0.50%p 이상 켜짐 · +0.25%p 이상 주의"))
    # 반도체 쏠림: 최근 5거래일(없으면 하루) 코스피 하락 중 삼성전자·SK하이닉스 몫
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
    sigs = {s["id"]: s for s in dvb.signals(conn)}
    it = dict(id="foreign", name="외국인 매도", state="na", evidence="외국인 순매수 자료가 아직 없어요", rule="외국인 5일·20일 합이 모두 순매도면 켜짐 · 엇갈리면 주의")
    sg = sigs.get("spot_foreign")
    if sg and sg.get("signal") is not None:
        it.update(state="on" if sg["signal"] < 0 else "watch" if sg["signal"] == 0 else "off", evidence=f"{sg['value']} · {sg['change']} (억원)")
    out.append(it)
    st, ev = fx_state(sm.series(conn, "ecos:usdkrw"))
    out.append(dict(id="fx", name="원화 약세", state=st, evidence=ev, rule="원/달러 3개월 +3% 이상 켜짐 · +1.5% 이상 주의"))
    st, ev = credit_state(sm.series(conn, "ecos:corp3"), sm.series(conn, "ecos:ktb3"))
    out.append(dict(id="credit", name="신용 경색", state=st, evidence=ev, rule="회사채 스프레드 3개월 +0.15%p 이상 켜짐 · +0.08%p 이상 주의"))
    try:
        import policy_impact
        st, ev = policy_impact.light(conn, today)
    except Exception:
        st, ev = "na", "정책·법안 자료를 읽지 못했어요"
    out.append(dict(id="policy", name="정책·법안", state=st, evidence=ev,
                    rule="최근 7일 밸류업·공매도·세제·상법·자본시장 제도 관련 법안이 통과되면 켜짐 · 관련 소식만 있으면 주의"))
    return out


# ---------------- 날짜별 기록과 변화 ----------------
def ensure_history(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS diag_lights (day TEXT, id TEXT, name TEXT, state TEXT, evidence TEXT, PRIMARY KEY (day, id));
    """)


def snapshot(conn, today=None):
    """오늘 신호등을 하루 한 줄씩 저장 → (이전 기록, 오늘 기록) {id: state}. 이전 기록이 없으면 이전은 {} (첫 기록은 알림 없이 기준선)"""
    import datetime as dt
    ensure_history(conn)
    day = (today or dt.date.today()).isoformat()
    prev_day = conn.execute("SELECT MAX(day) FROM diag_lights WHERE day < ?", (day,)).fetchone()[0]
    prev = {r[0]: r[1] for r in conn.execute("SELECT id, state FROM diag_lights WHERE day=?", (prev_day,))} if prev_day else {}
    ls = lights(conn, today)
    conn.executemany("INSERT OR REPLACE INTO diag_lights VALUES (?,?,?,?,?)", [(day, x["id"], x["name"], x["state"], x["evidence"]) for x in ls])
    conn.execute("DELETE FROM diag_lights WHERE day < date(?, '-400 days')", (day,))
    conn.commit()
    return prev, {x["id"]: x["state"] for x in ls}, ls


def changes(prev, now, ls):
    """알림용: 새로 켜진 신호·꺼진 신호 → ([켜진 이름], [꺼진 이름]). 이전 기록이 없으면 둘 다 빈 목록"""
    if not prev:
        return [], []
    names = {x["id"]: x["name"] for x in ls}
    on = [names[i] for i, st in now.items() if st == "on" and prev.get(i) not in (None, "on", "na")]
    off = [names[i] for i, st in now.items() if prev.get(i) == "on" and st in ("off", "watch")]
    return on, off


def history(conn, days=60):
    """[(day, {id: state})] 오래된 것부터 (최근 days일)"""
    try:
        rows = conn.execute("SELECT day, id, state FROM diag_lights ORDER BY day").fetchall()
    except sqlite3.OperationalError:
        return []
    by = {}
    for d, i, st in rows:
        by.setdefault(d, {})[i] = st
    return sorted(by.items())[-days:]


def lead_table(conn, level=15):
    """과거 코스피가 -level%를 처음 넘어선 날 앞뒤로, 자료가 있는 세 신호(금리·환율·신용)가 켜져 있었나.
    [{date, dd, lights: {id: (그날 상태, 이전 60일 안에 처음 켜진 날 또는 None, 근거 없음이면 None)}}]"""
    import deriv_bond as dvb
    import drawdown_history as dh
    import stock_market as sm
    kospi = list(sm.series(conn, "idx:KOSPI"))
    eps = dh.episodes(kospi, level) if len(kospi) > dh.LOOKBACK + 60 else []
    us, fx = dvb.series(conn, "bond:us10"), sm.series(conn, "ecos:usdkrw")
    corp, ktb = sm.series(conn, "ecos:corp3"), sm.series(conn, "ecos:ktb3")
    import datetime as dt

    def upto(pts, day):
        return [p for p in pts if p[0] <= day]
    out = []
    for e in eps:
        d0 = dt.date.fromisoformat(e["date"])
        row = {}
        for sid, fn in (("rate", lambda day: rate_state(upto(us, day))), ("fx", lambda day: fx_state(upto(fx, day))),
                        ("credit", lambda day: credit_state(upto(corp, day), upto(ktb, day)))):
            st0, _ = fn(e["date"])
            if st0 == "na":
                row[sid] = None
                continue
            first = None
            for back in range(60, -1, -5):                  # 60일 전부터 5일 간격으로 처음 켜진 날
                day = (d0 - dt.timedelta(days=back)).isoformat()
                if fn(day)[0] == "on":
                    first = day
                    break
            row[sid] = (st0, first)
        out.append(dict(date=e["date"], dd=e["dd"], lights=row))
    return out
