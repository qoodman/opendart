# -*- coding: utf-8 -*-
"""
선물·옵션·채권 신호판 (주식 투자자가 시장 분위기를 읽는 용도).

데이터 (네이버 증권, 키 불필요)
  - 코스피200 선물(FUT) · 코스피200 지수(KPI200) 일별 종가 → 베이시스(선물 − 현물)
  - 선물 시장 투자자별 순매수(계약 수): 개인·외국인·기관 (날짜를 지정해 과거도 받음)
  - 코스피 현물 투자자별 순매수(억원): 외국인·기관·개인
  - 국고채 3년·10년·30년, 미국 국채 10년 금리
못 구한 것(화면에도 '준비 중'으로 적음): 옵션 풋/콜 비율, 변동성지수(VKOSPI), 회사채 신용 스프레드.
  → KRX Open API의 파생상품 서비스가 승인되면 연결할 수 있어요 (지금은 이용신청한 서비스에 없음).

만기일은 계산으로 구해요 (코스피200 선물·옵션: 매월 둘째 목요일, 3·6·9·12월은 '쿼드러플 위칭').
공휴일이면 앞당겨지는데 그건 반영하지 않아요 (확인 필요).
"""
import datetime as dt
import sqlite3
import time

import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
CHART = "https://api.stock.naver.com/chart/domestic/index/{sym}/day"
TREND = "https://m.stock.naver.com/api/index/{sym}/trend"
BOND_P = "https://m.stock.naver.com/front-api/marketIndex/prices"
BONDS = {"kr3": ("KR3YT=RR", "국고채 3년"), "kr10": ("KR10YT=RR", "국고채 10년"), "kr30": ("KR30YT=RR", "국고채 30년"),
         "us10": ("US10YT=RR", "미국 국채 10년")}
KEEP_DAYS = 800
FLOW_BACKFILL_DAYS = 90


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS dv_series (sid TEXT, d TEXT, value REAL, PRIMARY KEY (sid, d));
    CREATE TABLE IF NOT EXISTS dv_flow (mkt TEXT, d TEXT, person REAL, foreign_v REAL, inst REAL, PRIMARY KEY (mkt, d));
    CREATE TABLE IF NOT EXISTS dv_meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS cal_events (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, title TEXT, note TEXT,
        important INTEGER DEFAULT 1);
    """)


def num(v):
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def _get(session, url, **params):
    r = (session or requests).get(url, params=params or None, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_chart(sym, today=None, session=None, days=KEEP_DAYS):
    today = today or dt.date.today()
    rows = _get(session, CHART.format(sym=sym), startDateTime=(today - dt.timedelta(days=days)).strftime("%Y%m%d"),
                endDateTime=(today + dt.timedelta(days=1)).strftime("%Y%m%d"))
    return [(f"{x['localDate'][:4]}-{x['localDate'][4:6]}-{x['localDate'][6:]}", float(x["closePrice"]))
            for x in rows if x.get("closePrice") is not None]


def fetch_trend(sym, bizdate=None, session=None):
    """→ (YYYY-MM-DD, 개인, 외국인, 기관) 선물은 계약 수, 코스피는 억원"""
    d = _get(session, TREND.format(sym=sym), **({"bizdate": bizdate} if bizdate else {}))
    b = str(d.get("bizdate", ""))
    if len(b) != 8:
        return None
    return (f"{b[:4]}-{b[4:6]}-{b[6:]}", num(d.get("personalValue")), num(d.get("foreignValue")), num(d.get("institutionalValue")))


def fetch_bond(code, pages=1, session=None):
    out = []
    for p in range(1, pages + 1):
        d = _get(session, BOND_P, category="bond", reutersCode=code, page=p, pageSize=10)
        rows = d.get("result") or []
        for x in rows:
            if x.get("closePrice") is not None and x.get("localTradedAt"):
                out.append((x["localTradedAt"][:10], float(x["closePrice"])))
        if len(rows) < 10:
            break
        time.sleep(0.15)
    return out


def _save_series(conn, sid, pts):
    conn.executemany("INSERT OR REPLACE INTO dv_series VALUES (?,?,?)", [(sid, d, v) for d, v in pts])


def weekdays_back(today, n):
    d, out = today, []
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def sync(db_path, log=print, session=None, today=None, sleep=0.15):
    today = today or dt.date.today()
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        res = {"실패": 0}
        for sid, sym in (("fut", "FUT"), ("kpi200", "KPI200")):
            try:
                pts = fetch_chart(sym, today, session)
                _save_series(conn, sid, pts)
                res[sid] = len(pts)
            except Exception as e:
                res["실패"] += 1
                log(f"선물·채권: {sym} 시세 실패 ({type(e).__name__})")
        have_bond = {r[0] for r in conn.execute("SELECT DISTINCT sid FROM dv_series WHERE sid LIKE 'bond:%'")}
        for key, (code, label) in BONDS.items():
            try:
                pts = fetch_bond(code, pages=2 if f"bond:{key}" in have_bond else 26, session=session)
                _save_series(conn, f"bond:{key}", pts)
                res[key] = len(pts)
            except Exception as e:
                res["실패"] += 1
                log(f"선물·채권: {label} 실패 ({type(e).__name__})")
        # 투자자별 순매수: 최근 거래일 + 아직 없는 최근 평일 (처음에는 90일 가까이 채움, 휴장일은 건너뜀)
        have = {(r[0], r[1]) for r in conn.execute("SELECT mkt, d FROM dv_flow")}
        first = not have
        targets = [None] + [d.strftime("%Y%m%d") for d in weekdays_back(today, FLOW_BACKFILL_DAYS * 5 // 7 if first else 7)]
        n = 0
        for biz in targets:
            for mkt, sym in (("fut", "FUT"), ("kospi", "KOSPI")):
                if biz and (mkt, f"{biz[:4]}-{biz[4:6]}-{biz[6:]}") in have:
                    continue
                try:
                    t = fetch_trend(sym, biz, session)
                except Exception as e:
                    res["실패"] += 1
                    if res["실패"] >= 8:
                        log("선물·채권: 연속 실패가 많아 투자자 순매수는 다음에 이어 받습니다")
                        break
                    continue
                if t and (biz is None or t[0].replace("-", "") == biz):     # 휴장일은 직전 거래일이 돌아와서 건너뜀
                    conn.execute("INSERT OR REPLACE INTO dv_flow VALUES (?,?,?,?,?)", (mkt, t[0], t[1], t[2], t[3]))
                    n += 1
                time.sleep(sleep)
            else:
                continue
            break
        res["순매수"] = n
        conn.execute("DELETE FROM dv_series WHERE d < ?", ((today - dt.timedelta(days=KEEP_DAYS + 30)).isoformat(),))
        conn.commit()
        res["만기일"] = add_expiries(conn, today)
        log(f"선물·채권: {res}")
        return res
    finally:
        conn.close()


# ---------------- 만기일 ----------------
def second_thursday(y, m):
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(3 - d.weekday()) % 7)      # 첫 목요일
    return d + dt.timedelta(days=7)


def expiries(today=None, months=8):
    """[(날짜, '쿼드러플 위칭' 여부)] 오늘 이후 앞으로 months개월의 선물·옵션 만기일 (공휴일 보정 없음)"""
    today = today or dt.date.today()
    out, y, m = [], today.year, today.month
    for _ in range(months + 1):
        d = second_thursday(y, m)
        if d >= today:
            out.append((d, m % 3 == 0))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out[:months]


def add_expiries(conn, today=None):
    """달력(cal_events)에 만기일을 넣음 (같은 제목·날짜는 중복 안 넣음). 새로 넣은 개수"""
    n = 0
    for d, quad in expiries(today, 6):
        title = "[만기일] 선물·옵션 동시만기 (쿼드러플 위칭)" if quad else "[만기일] 코스피200 옵션·선물 만기"
        if conn.execute("SELECT 1 FROM cal_events WHERE date=? AND title=?", (d.isoformat(), title)).fetchone():
            continue
        note = ("주가지수·개별주식 선물·옵션이 한꺼번에 만기 — 막판 거래가 몰려 변동성이 커질 수 있어요. " if quad else "옵션·선물 만기 — 장 막판 변동성에 주의. ") + "공휴일이면 날짜가 앞당겨질 수 있어요(확인 필요)."
        conn.execute("INSERT INTO cal_events (date, title, note, important) VALUES (?,?,?,?)", (d.isoformat(), title, note, 1 if quad else 0))
        n += 1
    conn.commit()
    return n


# ---------------- 읽기 ----------------
def series(conn, sid):
    try:
        return conn.execute("SELECT d, value FROM dv_series WHERE sid=? ORDER BY d", (sid,)).fetchall()
    except sqlite3.OperationalError:
        return []


def flow(conn, mkt):
    try:
        return conn.execute("SELECT d, person, foreign_v, inst FROM dv_flow WHERE mkt=? ORDER BY d", (mkt,)).fetchall()
    except sqlite3.OperationalError:
        return []


def basis(conn):
    """[(날짜, 선물 − 현물)] 같은 날짜 종가끼리"""
    f, s = dict(series(conn, "fut")), dict(series(conn, "kpi200"))
    return [(d, round(f[d] - s[d], 2)) for d in sorted(set(f) & set(s))]


def _ago(pts, days):
    """days일 전 이전의 가장 가까운 값"""
    if not pts:
        return None
    t = (dt.date.fromisoformat(pts[-1][0]) - dt.timedelta(days=days)).isoformat()
    v = None
    for d, x in pts:
        if d <= t:
            v = x
    return v


def signals(conn):
    """[dict(id, label, value, change, signal(+1/0/-1/None), reason, points, note)] — 자료가 없으면 note만"""
    out = []

    # 선물 외국인 순매수
    fl = flow(conn, "fut")
    it = dict(id="fut_foreign", label="외국인 선물 순매수 (계약)", value="", change="", signal=None, reason="", points=[], note="")
    if len(fl) >= 5:
        fv = [r[2] or 0 for r in fl]
        s5, s20 = sum(fv[-5:]), sum(fv[-20:])
        cum, c = [], 0
        for x in fv[-60:]:
            c += x
            cum.append(c)
        it.update(value=f"오늘 {fv[-1]:+,.0f}", change=f"5일 합 {s5:+,.0f} · 20일 합 {s20:+,.0f}", points=cum, date=fl[-1][0])
        it["signal"] = 1 if s5 > 0 and s20 > 0 else -1 if s5 < 0 and s20 < 0 else 0
        it["reason"] = ("외국인이 선물을 사 모으는 중 (상승 베팅이 우세, 가설)" if it["signal"] > 0 else
                        "외국인이 선물을 팔아 온 중 (하락 베팅·헤지, 가설)" if it["signal"] < 0 else "5일·20일 방향이 엇갈려요")
    else:
        it["note"] = "투자자별 자료가 아직 모이는 중이에요 (지금 수집 → 시장·정책만)"
    out.append(it)

    # 선물 기관
    if len(fl) >= 5:
        iv = [r[3] or 0 for r in fl]
        s5 = sum(iv[-5:])
        out.append(dict(id="fut_inst", label="기관 선물 순매수 (계약)", value=f"오늘 {iv[-1]:+,.0f}", change=f"5일 합 {s5:+,.0f}",
                        signal=None, reason="기관은 현물 포지션을 선물로 헤지하는 경우가 많아서, 선물 매도가 곧 비관은 아니에요 (참고용)", points=[], note=""))

    # 베이시스
    bs = basis(conn)
    it = dict(id="basis", label="베이시스 (선물 − 현물, pt)", value="", change="", signal=None, reason="", points=[], note="")
    if len(bs) >= 5:
        v5 = sum(v for _, v in bs[-5:]) / 5
        it.update(value=f"{bs[-1][1]:+.2f}", change=f"5일 평균 {v5:+.2f}", points=[v for _, v in bs[-60:]], date=bs[-1][0])
        it["signal"] = 1 if v5 > 0.5 else -1 if v5 < -0.5 else 0
        it["reason"] = ("선물이 현물보다 비싸요 — 시장 참여자들이 상승을 기대하는 쪽 (콘탱고)" if it["signal"] > 0 else
                        "선물이 현물보다 싸요 — 하락에 대비해 헤지가 늘어난 쪽 (백워데이션)" if it["signal"] < 0 else "선물·현물 차이가 작아요")
        it["note"] = "근월물 종가 기준 단순 차이예요. 만기가 가까울수록 0에 가까워지고 배당 영향도 있어서 큰 의미는 변화 방향에 둬요."
    else:
        it["note"] = "선물·현물 시세가 아직 없어요"
    out.append(it)

    # 현물 외국인
    sp = flow(conn, "kospi")
    if len(sp) >= 5:
        fv = [r[2] or 0 for r in sp]
        s5 = sum(fv[-5:])
        out.append(dict(id="spot_foreign", label="외국인 코스피 현물 순매수 (억원)", value=f"오늘 {fv[-1]:+,.0f}", change=f"5일 합 {s5:+,.0f} · 20일 합 {sum(fv[-20:]):+,.0f}",
                        signal=1 if s5 > 0 and sum(fv[-20:]) > 0 else -1 if s5 < 0 and sum(fv[-20:]) < 0 else 0,
                        reason="현물(실제 주식)에서 외국인이 사고 있는지 — 선물 방향과 같은 쪽이면 신호가 더 믿을 만해요", points=[], note=""))

    # 채권
    ser = {k: series(conn, f"bond:{k}") for k in BONDS}
    for key, label, step in (("kr3", "국고채 3년 금리", 0.25), ("kr10", "국고채 10년 금리", 0.25)):
        pts = ser[key]
        it = dict(id=key, label=label, value="", change="", signal=None, reason="", points=[v for _, v in pts[-250:]], note="")
        if pts:
            last, prev = pts[-1][1], _ago(pts, 91)
            it.update(value=f"{last:.3f}%", date=pts[-1][0])
            if prev is not None:
                d = last - prev
                it["change"] = f"3개월 {d:+.2f}%p"
                it["signal"] = 1 if d <= -step else -1 if d >= step else 0
                it["reason"] = ("금리 하락 — 채권 값은 오르고, 주식 가치 평가엔 우호적 (가설)" if it["signal"] > 0 else
                                "금리 상승 — 채권 값은 내리고, 주식(특히 성장주)에 부담 (가설)" if it["signal"] < 0 else "금리 큰 변화 없음")
        else:
            it["note"] = "금리 자료가 아직 없어요"
        out.append(it)
    k3, k10 = dict(ser["kr3"]), dict(ser["kr10"])
    sp_pts = [(d, k10[d] - k3[d]) for d in sorted(set(k3) & set(k10))]
    it = dict(id="curve", label="장단기 금리차 (10년 − 3년)", value="", change="", signal=None, reason="", points=[v for _, v in sp_pts[-250:]], note="")
    if sp_pts:
        last = sp_pts[-1][1]
        it.update(value=f"{last:+.2f}%p", date=sp_pts[-1][0], signal=-1 if last < 0 else 1 if last >= 0.5 else 0,
                  reason=("금리 역전 — 경기 둔화 신호로 읽혀요 (가설)" if last < 0 else "금리차 충분 — 경기 기대가 나쁘지 않은 쪽" if last >= 0.5 else "금리차가 좁아요"))
    else:
        it["note"] = "금리 자료가 아직 없어요"
    out.append(it)
    u10 = dict(ser["us10"])
    gap = [(d, k10[d] - u10[d]) for d in sorted(set(k10) & set(u10))]
    it = dict(id="kr_us", label="한미 금리차 (한국 10년 − 미국 10년)", value="", change="", signal=None, reason="", points=[v for _, v in gap[-250:]], note="")
    if ser["us10"]:
        last_us, prev_us = ser["us10"][-1][1], _ago(ser["us10"], 91)
        if gap:
            it.update(value=f"{gap[-1][1]:+.2f}%p", date=gap[-1][0])
        it["change"] = f"미국 10년 {last_us:.2f}%" + (f" (3개월 {last_us - prev_us:+.2f}%p)" if prev_us is not None else "")
        g = gap[-1][1] if gap else None
        it["signal"] = None if g is None else -1 if g <= -1.0 else 1 if g >= 0 else 0
        it["reason"] = ("미국 금리가 한국보다 많이 높아요 — 달러 자산이 유리해서 원화 약세·외국인 자금 이탈 압력이 생길 수 있어요 (가설)" if g is not None and g <= -1.0 else
                        "한국 금리가 미국과 비슷하거나 높아요 — 자금 이탈 압력이 작은 편 (가설)" if g is not None and g >= 0 else "미국이 조금 높은 보통 수준")
    else:
        it["note"] = "미국 금리 자료가 아직 없어요"
    out.append(it)
    return out


def regime(sigs):
    """신호가 있는 항목의 합 → (점수, 개수, 문구)"""
    vals = [s["signal"] for s in sigs if s.get("signal") is not None]
    if not vals:
        return 0, 0, "자료 모으는 중"
    tot = sum(vals)
    return tot, len(vals), ("우호적인 쪽" if tot >= 2 else "조심할 쪽" if tot <= -2 else "엇갈리는 중")
