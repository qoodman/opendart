# -*- coding: utf-8 -*-
"""
테마·업종 → 종목 자료 (네이버 증권, 키 불필요). 법안이 어떤 기업에 닿을지 짐작하는 데 써요.
  테마 목록   https://m.stock.naver.com/api/stocks/theme?page=1&pageSize=100          → groups[{no, name, totalCount}] (265개: 면세점·호텔/리조트·화장품·미용기기·의료기기·밸류업·지주사 …)
  구성 종목   https://m.stock.naver.com/api/stocks/theme/{no}?page=1&pageSize=100     → stocks[{itemCode, stockName}], totalCount
업종(79개)의 구성 종목은 industry.py가 받아 둔 industry_map을 그대로 써요. 테마는 사람이 관리하는 분류라 틀리거나 빠진 종목이 있을 수 있어요. 주 1회 새로 받아요.
"""
import datetime as dt
import sqlite3
import time

import requests

BASE = "https://m.stock.naver.com/api/stocks/theme"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
REFRESH_DAYS = 7


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS theme_names (no INTEGER PRIMARY KEY, name TEXT, total INTEGER);
    CREATE TABLE IF NOT EXISTS theme_stocks (no INTEGER, code TEXT, name TEXT, PRIMARY KEY (no, code));
    CREATE TABLE IF NOT EXISTS theme_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def _get(session, url, **params):
    r = (session or requests).get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_all(session=None, sleep=0.2, log=print):
    """→ ({no: (이름, 전체 수)}, {no: [(코드, 이름)]})"""
    names, page = {}, 1
    while True:
        d = _get(session, BASE, page=page, pageSize=100)
        gs = d.get("groups") or []
        for g in gs:
            names[g["no"]] = (g["name"], g.get("totalCount") or 0)
        if len(gs) < 100 or len(names) >= (d.get("totalCount") or 10 ** 9):
            break
        page += 1
        time.sleep(sleep)
    members, fails = {}, 0
    for no, (nm, total) in names.items():
        got, p, rows = 0, 1, []
        try:
            while True:
                d = _get(session, f"{BASE}/{no}", page=p, pageSize=100)
                st = d.get("stocks") or []
                rows += [(s["itemCode"], s.get("stockName") or "") for s in st if s.get("itemCode")]
                got += len(st)
                if not st or got >= (d.get("totalCount") or total):
                    break
                p += 1
                time.sleep(sleep)
        except Exception as e:
            fails += 1
            log(f"테마 {nm} 실패: {type(e).__name__}")
        members[no] = rows
        time.sleep(sleep)
    return names, members, fails


def sync(db_path, log=print, session=None, today=None, force=False):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        now = today or dt.date.today()
        row = conn.execute("SELECT value FROM theme_meta WHERE key='synced'").fetchone()
        if row and not force and (now - dt.date.fromisoformat(row[0])).days < REFRESH_DAYS:
            return {"건너뜀": "최근에 받음"}
        names, members, fails = fetch_all(session, log=log)
        if len(names) < 100 or fails > len(names) * 0.2:
            log(f"테마: {len(names)}개 중 {fails}개 실패해서 기존 표를 그대로 둬요")
            return {"저장": 0, "실패": fails}
        conn.execute("DELETE FROM theme_names")
        conn.execute("DELETE FROM theme_stocks")
        conn.executemany("INSERT INTO theme_names VALUES (?,?,?)", [(n, nm, t) for n, (nm, t) in names.items()])
        conn.executemany("INSERT OR IGNORE INTO theme_stocks VALUES (?,?,?)", [(n, c, nm) for n, rows in members.items() for c, nm in rows])
        conn.execute("INSERT OR REPLACE INTO theme_meta VALUES ('synced', ?)", (now.isoformat(),))
        conn.commit()
        n_st = conn.execute("SELECT COUNT(DISTINCT code) FROM theme_stocks").fetchone()[0]
        log(f"테마: {len(names)}개 · 종목 {n_st}개 (실패 {fails})")
        return {"저장": len(names), "종목": n_st, "실패": fails}
    finally:
        conn.close()


def load(conn):
    """→ {"테마": {이름: [코드...]}, "업종": {이름: [코드...]}, "이름": {코드: 종목명}}. 표가 없으면 빈 값"""
    out = {"테마": {}, "업종": {}, "이름": {}, "번호": {}}
    try:
        nm = {n: name for n, name in conn.execute("SELECT no, name FROM theme_names")}
        for no, code, sname in conn.execute("SELECT no, code, name FROM theme_stocks"):
            if no in nm:
                out["테마"].setdefault(nm[no], []).append(code)
                out["번호"][nm[no]] = no
            if sname:
                out["이름"].setdefault(code, sname)
    except sqlite3.OperationalError:
        pass
    try:
        for code, g in conn.execute("SELECT code, group_name FROM industry_map"):
            out["업종"].setdefault(g, []).append(code)
    except sqlite3.OperationalError:
        pass
    try:
        for code, n in conn.execute("SELECT code, name FROM krx_daily WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_daily)"):
            if n:
                out["이름"][code] = n
    except sqlite3.OperationalError:
        pass
    return out
