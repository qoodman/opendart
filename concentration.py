"""코스피 쏠림: 시가총액 상위 종목이 지수에서 차지하는 비중과, 그 종목들의 실제 변동성.
(두 종목이 지수의 절반이면 분산 효과가 거의 없고 지수 변동성이 그 두 종목에 좌우된다는 점을 숫자로 본다.)
네이버 증권 시세(시가총액 순 목록)와 종목 일별 종가를 쓰고, 키는 필요 없다."""
import datetime as dt
import math
import sqlite3
import time

import requests

URL = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TOP = 10        # 변동성을 계산할 상위 종목 수
PAGES = 5       # 시가총액 순 500개 (나머지는 합계에 영향이 거의 없음)


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conc_stock (code TEXT PRIMARY KEY, name TEXT, mcap REAL, rank INTEGER);
    CREATE TABLE IF NOT EXISTS conc_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def fetch_list(session=None):
    s = session or requests
    out = []
    for pg in range(1, PAGES + 1):
        j = None
        for _ in range(3):
            try:
                j = s.get(URL, params=dict(page=pg, pageSize=100), headers=HEADERS, timeout=30).json()
                break
            except (requests.RequestException, ValueError):
                time.sleep(2)
        if not j or not j.get("stocks"):
            break
        for x in j["stocks"]:
            if x.get("stockEndType") != "stock":
                continue
            try:
                v = float(str(x["marketValue"]).replace(",", ""))
            except (KeyError, ValueError):
                continue
            out.append((x["itemCode"], x["stockName"], v))
        time.sleep(0.3)
    return out


def sync(db_path, log=print, force=False, session=None):
    import bill_watch
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        today = dt.date.today().isoformat()
        last = conn.execute("SELECT value FROM conc_meta WHERE key='synced'").fetchone()
        if last and last[0] == today and not force:
            return "오늘 받음"
        rows = fetch_list(session)
        if len(rows) < 50:
            return "목록 받기 실패"
        rows.sort(key=lambda r: -r[2])
        conn.execute("DELETE FROM conc_stock")
        conn.executemany("INSERT INTO conc_stock VALUES (?,?,?,?)", [(c, n, v, i + 1) for i, (c, n, v) in enumerate(rows[:30])])
        conn.execute("INSERT OR REPLACE INTO conc_meta VALUES ('total', ?)", (str(sum(r[2] for r in rows)),))
        conn.execute("INSERT OR REPLACE INTO conc_meta VALUES ('synced', ?)", (today,))
        conn.commit()
        n = nf = 0
        import stock_value
        stock_value.ensure(conn)
        for code, name, _ in rows[:TOP]:
            try:
                bill_watch.ensure_stock(conn, code, name, session)
                bill_watch.refresh_prices(conn, code, session=session)
                n += 1
            except Exception as e:
                log(f"  ✖ {code}: {type(e).__name__}")
            try:        # 외국인·기관·개인 순매수 (최근 며칠)
                tr = stock_value.fetch_trend(code, session=session)
                conn.executemany("INSERT OR REPLACE INTO flow (code, date, foreign_qty, inst_qty, indiv_qty, fetched, foreign_ratio) VALUES (?,?,?,?,?,?,?)",
                                 [(code, r["date"], r["foreign"], r["inst"], r["indiv"], today, r.get("ratio")) for r in tr])
                conn.commit()
                nf += 1
            except Exception as e:
                log(f"  ✖ {code} 순매수: {type(e).__name__}")
            time.sleep(0.3)
        return f"{len(rows)}종목 합계 · 상위 {n}개 종가 · 순매수 {nf}개"
    finally:
        conn.close()


def _vol(cl, n):
    if len(cl) < n + 1:
        return None
    r = [cl[i] / cl[i - 1] - 1 for i in range(len(cl) - n, len(cl))]
    mu = sum(r) / len(r)
    return math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(250)


def analyze(conn):
    try:
        rows = conn.execute("SELECT code, name, mcap, rank FROM conc_stock ORDER BY rank").fetchall()
        tot = conn.execute("SELECT value FROM conc_meta WHERE key='total'").fetchone()
        synced = conn.execute("SELECT value FROM conc_meta WHERE key='synced'").fetchone()
    except sqlite3.Error:
        return None
    if not rows or not tot:
        return None
    total = float(tot[0])
    out = []
    for code, name, mcap, rank in rows:
        try:
            px = [r[0] for r in conn.execute("SELECT close FROM bill_px WHERE code=? ORDER BY d", (code,)).fetchall() if r[0]]
        except sqlite3.Error:
            px = []
        item = dict(code=code, name=name, mcap=mcap, w=mcap / total, rank=rank, v20=_vol(px, 20), v60=_vol(px, 60), v250=_vol(px, 250))
        try:
            fl = conn.execute("SELECT date, foreign_qty, inst_qty FROM flow WHERE code=? ORDER BY date DESC LIMIT 5", (code,)).fetchall()
        except sqlite3.Error:
            fl = []
        if fl and px:
            item["f5"] = sum((r[1] or 0) for r in fl) * px[-1] / 1e8       # 외국인 최근 5거래일 순매수 금액(억원, 종가 기준 어림)
            item["i5"] = sum((r[2] or 0) for r in fl) * px[-1] / 1e8
            item["f_last"] = fl[0][0]
        try:
            fr = conn.execute("SELECT foreign_ratio FROM flow WHERE code=? AND foreign_ratio IS NOT NULL ORDER BY date DESC LIMIT 6", (code,)).fetchall()
        except sqlite3.Error:
            fr = []
        if fr:
            item["fr"] = fr[0][0]
            item["fr_chg"] = fr[0][0] - fr[-1][0] if len(fr) >= 2 else None
        out.append(item)
    top = out[:TOP]
    have = [t for t in top if t["v20"] is not None]
    # 단순 추정: 비중 × 변동성 (종목 간 상관을 무시한 위험 비중, 실제 기여와 다를 수 있음)
    tw = sum(t["w"] * t["v20"] for t in have) or None
    for t in have:
        t["risk"] = t["w"] * t["v20"] / tw if tw else None
    # 상위 2 종목 비중은 보통주 기준 1·2위
    return dict(rows=out, total=total, top1=out[0]["w"], top2=out[0]["w"] + out[1]["w"], top5=sum(t["w"] for t in out[:5]),
                top10=sum(t["w"] for t in out[:10]), synced=synced[0] if synced else "", n_vol=len(have))
