"""거래정지 종목: 네이버 증권 시세 목록의 거래 상태(운영/정지)로 코스피·코스닥 거래정지 종목을 하루 한 번 모아 둔다.
화면에서 '거래정지 제외'로 거르거나 '거래정지' 표시를 붙이는 데 쓴다."""
import datetime as dt
import sqlite3
import time

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
URL = "https://m.stock.naver.com/api/stocks/marketValue/{mkt}"
_CACHE = {"t": 0.0, "v": frozenset()}


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS trade_halt (code TEXT PRIMARY KEY, name TEXT, halted INTEGER, status TEXT, fetched TEXT)")


def fetch(session=None, pages=40):
    """코스피·코스닥 일반 종목 [(코드, 이름, 정지 여부, 상태 문구)] — ETF·ETN은 뺌"""
    s = session or requests
    out = []
    for mkt in ("KOSPI", "KOSDAQ"):
        for pg in range(1, pages + 1):
            j = None
            for _ in range(3):
                try:
                    j = s.get(URL.format(mkt=mkt), params=dict(page=pg, pageSize=100), headers=HEADERS, timeout=30).json()
                    break
                except (requests.RequestException, ValueError):
                    time.sleep(2)
            st = (j or {}).get("stocks") or []
            if not st:
                break
            for x in st:
                if x.get("stockEndType") != "stock":
                    continue
                ts = x.get("tradeStopType") or {}
                halted = (ts.get("name") or "").upper() == "HALTED" or (x.get("tradableStatus") or "") == "halt"
                out.append((x.get("itemCode") or "", x.get("stockName") or "", 1 if halted else 0, ts.get("text") or ""))
            time.sleep(0.2)
    return out


def sync(db_path, log=print, session=None, today=None):
    """하루 한 번 목록을 새로 받아 거래정지 표를 통째로 바꿈 → 거래정지 종목 수"""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        day = (today or dt.date.today()).isoformat()
        last = conn.execute("SELECT MAX(fetched) FROM trade_halt").fetchone()[0]
        if last == day:
            return -1
        rows = fetch(session)
        if len(rows) < 500:
            raise RuntimeError("종목 목록을 제대로 받지 못했어요")
        conn.execute("DELETE FROM trade_halt")
        conn.executemany("INSERT OR REPLACE INTO trade_halt VALUES (?,?,?,?,?)", [(c, n, h, st, day) for c, n, h, st in rows if c])
        conn.commit()
        n = sum(1 for r in rows if r[2])
        log(f"[거래정지] 종목 {len(rows):,}개 중 거래정지 {n}개")
        return n
    finally:
        conn.close()


def halted(conn):
    """거래정지 종목 코드 집합 (60초 재사용). 자료가 없으면 빈 집합"""
    if time.time() - _CACHE["t"] < 60:
        return _CACHE["v"]
    try:
        v = frozenset(c for (c,) in conn.execute("SELECT code FROM trade_halt WHERE halted=1"))
    except sqlite3.Error:
        v = frozenset()
    _CACHE.update(t=time.time(), v=v)
    return v


def last_synced(conn):
    try:
        return conn.execute("SELECT MAX(fetched) FROM trade_halt").fetchone()[0] or ""
    except sqlite3.Error:
        return ""
