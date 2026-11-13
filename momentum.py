# -*- coding: utf-8 -*-
"""
모멘텀 (현대 요인 투자: Jegadeesh & Titman 1993, Carhart 1997, George & Hwang 2004).

'최근 오른 종목이 한동안 더 오르는 경향'을 점수로 옮긴 것. 학술 연구가 가장 많은 요인이지만, 기간·시장에 따라 크게 틀어지고
(2009·2020년 같은 급반등장에서 모멘텀 종목이 크게 무너진 적이 있어요) 거래비용·세금을 빼면 남는 게 줄어요. 투자 추천이 아니에요.

계산 (모두 종가 기준)
  12-1 수익률: 12개월 전 → 1개월 전 (가장 최근 1개월은 일부러 뺌 — 단기 반전 효과 때문)
  6-1 수익률:  6개월 전 → 1개월 전
  52주 고점 대비 위치: 현재가 ÷ 최근 52주 최고가
  200일 이동평균: 현재가가 200거래일 평균 위인가
데이터: 네이버 증권 일별 종가 (api.stock.naver.com/chart/domestic/item/{code}/day) — 종목당 한 번 호출에 1년 이상이 와요.
"""
import datetime as dt
import sqlite3
import time

import requests

URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
KEEP_DAYS = 420
REFRESH_DAYS = 2
MAX_FAIL_STREAK = 5


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS price_hist (code TEXT, d TEXT, close REAL, PRIMARY KEY (code, d));
    CREATE TABLE IF NOT EXISTS price_fetch (code TEXT PRIMARY KEY, fetched TEXT, n INTEGER);
    """)


def fetch(code, today=None, session=None, timeout=20):
    """[(YYYY-MM-DD, 종가)] 오래된 것부터"""
    today = today or dt.date.today()
    s = session or requests
    r = s.get(URL.format(code=code), headers=HEADERS, timeout=timeout,
              params={"startDateTime": (today - dt.timedelta(days=KEEP_DAYS)).strftime("%Y%m%d"),
                      "endDateTime": (today + dt.timedelta(days=1)).strftime("%Y%m%d")})
    r.raise_for_status()
    out = []
    for x in r.json():
        d, c = str(x.get("localDate", "")), x.get("closePrice")
        if len(d) == 8 and c:
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(c)))
    return sorted(out)


def store(conn, code, rows, today=None):
    ensure(conn)
    for d, c in rows:
        conn.execute("INSERT OR REPLACE INTO price_hist VALUES (?,?,?)", (code, d, c))
    conn.execute("INSERT OR REPLACE INTO price_fetch VALUES (?,?,?)", (code, (today or dt.date.today()).isoformat(), len(rows)))
    cut = ((today or dt.date.today()) - dt.timedelta(days=KEEP_DAYS + 30)).isoformat()
    conn.execute("DELETE FROM price_hist WHERE code=? AND d<?", (code, cut))
    conn.commit()
    return len(rows)


def sync(db_path, log=print, sleep=0.3, session=None, today=None, max_age=REFRESH_DAYS):
    """공시에 나온 종목(+점수표 종목)의 일별 종가를 받아 저장. 최근 max_age일 안에 받은 종목은 건너뜀."""
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
        fresh = {r[0] for r in conn.execute("SELECT code FROM price_fetch WHERE fetched >= ?",
                                           ((now - dt.timedelta(days=max_age)).isoformat(),))}
        todo = [c for c in codes if c not in fresh]
        saved = failed = streak = 0
        for i, code in enumerate(todo):
            if streak >= MAX_FAIL_STREAK:
                log(f"종가 이력: {MAX_FAIL_STREAK}번 연속 실패해서 나머지 {len(todo) - i}개는 다음에 받습니다.")
                break
            try:
                store(conn, code, fetch(code, now, session), now)
                saved += 1
                streak = 0
            except Exception as e:
                failed += 1
                streak += 1
                log(f"종가 이력 {code} 실패: {e}")
            time.sleep(sleep)
        log(f"종가 이력: 대상 {len(codes)}개 · 새로 저장 {saved} · 실패 {failed} · 최근에 받음 {len(codes) - len(todo)}")
        return {"대상": len(codes), "저장": saved, "건너뜀": len(codes) - len(todo), "실패": failed}
    finally:
        conn.close()


def load_all(conn):
    """{종목코드: [(날짜, 종가)…]} 오래된 것부터"""
    try:
        rows = conn.execute("SELECT code, d, close FROM price_hist ORDER BY code, d").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for code, d, c in rows:
        out.setdefault(code, []).append((d, c))
    return out


def _price_on(series, target):
    """target 날짜 이하의 마지막 종가. 시리즈가 그 날짜보다 늦게 시작하면 None"""
    t = target.isoformat()
    if not series or series[0][0] > (target + dt.timedelta(days=7)).isoformat():
        return None
    last = None
    for d, c in series:
        if d > t:
            break
        last = c
    return last


def metrics(series):
    """→ dict(ok, last, last_date, r12_1, r6_1, high_ratio, above_ma200, n) / 값이 없으면 None"""
    res = dict(ok=False, last=None, last_date=None, r12_1=None, r6_1=None, high_ratio=None, above_ma200=None, ma200=None, n=len(series or []))
    if not series or len(series) < 60:
        return res
    last_date = dt.date.fromisoformat(series[-1][0])
    last = series[-1][1]
    res.update(last=last, last_date=series[-1][0])
    m1 = _price_on(series, last_date - dt.timedelta(days=30))
    for key, days in (("r12_1", 365), ("r6_1", 182)):
        p0 = _price_on(series, last_date - dt.timedelta(days=days))
        if p0 and m1:
            res[key] = m1 / p0 - 1
    win = [c for d, c in series if d >= (last_date - dt.timedelta(days=365)).isoformat()]
    if win:
        res["high_ratio"] = last / max(win)
    if len(series) >= 200:
        ma = sum(c for d, c in series[-200:]) / 200
        res["ma200"], res["above_ma200"] = ma, last > ma
    res["ok"] = res["r6_1"] is not None or res["r12_1"] is not None
    return res


def _pct_rank(values, v):
    """values 안에서 v가 위에서 몇 % 지점인지 (1.0=최고)"""
    n = len(values)
    if n <= 1:
        return 0.5
    return sum(1 for x in values if x < v) / (n - 1)


def score_all(cards):
    """cards: [{'m': metrics dict…}] 목록 안에서 상대 순위로 점수(0~100)와 items를 채움.
    12-1 수익률 40 · 6-1 수익률 20 (둘 다 목록 안 순위) · 52주 고점 근처 20 · 200일선 위 20"""
    r12 = [c["m"]["r12_1"] for c in cards if c["m"]["r12_1"] is not None]
    r6 = [c["m"]["r6_1"] for c in cards if c["m"]["r6_1"] is not None]
    for c in cards:
        m = c["m"]
        items = []
        p = round(40 * _pct_rank(r12, m["r12_1"])) if m["r12_1"] is not None else 0
        items.append(("12-1개월 수익률 순위", p, 40, "1년치 가격이 모자라요" if m["r12_1"] is None else f"{m['r12_1']:+.0%} (목록 {len(r12)}개 중 상대 순위)"))
        p6 = round(20 * _pct_rank(r6, m["r6_1"])) if m["r6_1"] is not None else 0
        items.append(("6-1개월 수익률 순위", p6, 20, "6개월치 가격이 모자라요" if m["r6_1"] is None else f"{m['r6_1']:+.0%} (목록 {len(r6)}개 중 상대 순위)"))
        h = m["high_ratio"]
        ph = 0 if h is None else 20 if h >= 0.90 else 14 if h >= 0.80 else 8 if h >= 0.70 else 0
        items.append(("52주 고점 근처", ph, 20, "자료 없음" if h is None else f"고점의 {h:.0%} (≥90% 최고 · ≥80% 좋음 · ≥70% 보통)"))
        pm = 20 if m["above_ma200"] else 0
        items.append(("200일 평균선 위", pm, 20, "200거래일 미만이라 계산 못 함" if m["above_ma200"] is None else
                      f"현재가 {m['last']:,.0f} · 200일 평균 {m['ma200']:,.0f}"))
        c["items"], c["score"] = items, sum(i[1] for i in items)
