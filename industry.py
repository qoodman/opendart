# -*- coding: utf-8 -*-
"""
종목 → 업종(네이버 증권 업종 79개) 매핑. 하락 점검의 '업종별 기여도'에 써요. 키 불필요, 주 1회만 새로 받아요 (호출 100번 안팎).
  업종 목록   https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100          → groups[{no, name, totalCount}]
  구성 종목   https://m.stock.naver.com/api/stocks/industry/{no}?page=1&pageSize=100     → stocks[{itemCode, stockName}], totalCount
우선주·일부 종목은 업종에 안 들어 있을 수 있어요 (그 종목은 '분류 없음').
"""
import datetime as dt
import sqlite3
import time

import requests

BASE = "https://m.stock.naver.com/api/stocks/industry"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
REFRESH_DAYS = 7


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS industry_map (code TEXT PRIMARY KEY, group_no INTEGER, group_name TEXT);
    CREATE TABLE IF NOT EXISTS industry_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def _get(session, url, **params):
    r = (session or requests).get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_all(session=None, sleep=0.2, log=print):
    """→ {종목코드: (업종번호, 업종이름)}"""
    groups = _get(session, BASE, page=1, pageSize=100).get("groups") or []
    out = {}
    for g in groups:
        page, got, total = 1, 0, g.get("totalCount") or 0
        while True:
            d = _get(session, f"{BASE}/{g['no']}", page=page, pageSize=100)
            stocks = d.get("stocks") or []
            for s in stocks:
                if s.get("itemCode"):
                    out.setdefault(s["itemCode"], (g["no"], g["name"]))
            got += len(stocks)
            if not stocks or got >= (d.get("totalCount") or total):
                break
            page += 1
            time.sleep(sleep)
        time.sleep(sleep)
    return out


def sync(db_path, log=print, session=None, today=None, force=False):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        now = (today or dt.date.today())
        row = conn.execute("SELECT value FROM industry_meta WHERE key='synced'").fetchone()
        if row and not force and (now - dt.date.fromisoformat(row[0])).days < REFRESH_DAYS:
            return {"건너뜀": "최근에 받음"}
        m = fetch_all(session, log=log)
        if len(m) < 500:                                 # 일부만 받았으면 기존 표를 지우지 않음
            log(f"업종 매핑: {len(m)}종목만 받아져서 저장하지 않았어요")
            return {"저장": 0}
        conn.execute("DELETE FROM industry_map")
        conn.executemany("INSERT INTO industry_map VALUES (?,?,?)", [(c, n, nm) for c, (n, nm) in m.items()])
        conn.execute("INSERT OR REPLACE INTO industry_meta VALUES ('synced', ?)", (now.isoformat(),))
        conn.commit()
        log(f"업종 매핑: {len(m)}종목 · 업종 {len({v[0] for v in m.values()})}개")
        return {"저장": len(m)}
    finally:
        conn.close()


def mapping(conn):
    try:
        return {c: n for c, n in conn.execute("SELECT code, group_name FROM industry_map")}
    except sqlite3.OperationalError:
        return {}
