# -*- coding: utf-8 -*-
"""
과거 국회 발의법률안(20·21·22대)과 단계별 날짜를 받아 두는 곳. '입법 단계별 주가 분석'의 원자료예요.
  - 열린국회정보 의원 발의법률안 목록을 대(AGE)별로 100건씩 받아요. 법안 하나에 단계 날짜가 여러 개 붙어 와요:
      발의(PROPOSE_DT) · 소관위 회부(COMMITTEE_DT) · 소관위 상정(CMT_PRESENT_DT) · 소관위 처리(CMT_PROC_DT) · 법사위 처리(LAW_PROC_DT) · 본회의 처리(PROC_DT, 결과 PROC_RESULT)
  - 이어받기: 대마다 '다음에 받을 쪽'을 기억해서 중간에 끊겨도 이어서 받아요.
  - 앱의 '정책 소식' 표(policy_items)와 섞지 않고 따로 둬요 (수만 건이라 화면이 어지러워지므로).
주의: 이 API가 20·21대까지 실제로 받아지는지, 단계 날짜 필드가 다 있는지는 서버에서 확인해야 해요 (설정 → 법안·종목 확인 / 입법 주가 분석 화면의 '받을 수 있는 범위').
"""
import datetime as dt
import sqlite3
import time

import requests

AGES = (20, 21, 22)
STAGE_FIELDS = (("PROPOSE_DT", "발의"), ("COMMITTEE_DT", "소관위 회부"), ("CMT_PRESENT_DT", "소관위 상정"),
                ("CMT_PROC_DT", "소관위 처리"), ("LAW_PROC_DT", "법사위 처리"), ("PROC_DT", "본회의 처리"))


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hist_bills (
        key TEXT PRIMARY KEY, age INTEGER, name TEXT, proposer TEXT, propose_dt TEXT, committee_dt TEXT, cmt_present_dt TEXT,
        cmt_proc_dt TEXT, law_proc_dt TEXT, proc_dt TEXT, proc_result TEXT, url TEXT);
    CREATE INDEX IF NOT EXISTS hist_bills_age ON hist_bills (age);
    CREATE TABLE IF NOT EXISTS hist_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    for col in ("promul_dt", "effective_dt"):             # 법제처 연혁에서 찾은 공포일·시행일 (통과한 법안만)
        try:
            conn.execute(f"ALTER TABLE hist_bills ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass


def _d(v):
    v = str(v or "").strip()[:10]
    return v if len(v) == 10 and v[4] == "-" else ""


def probe(api, ages=AGES):
    """각 대의 전체 건수·발의일 범위·단계 날짜 필드 채움 정도를 첫 쪽·마지막 쪽으로 살펴봄 → [dict(age, total, first, last, fields, error)]"""
    out = []
    for age in ages:
        it = dict(age=age, total=0, first="", last="", fields={}, error="")
        try:
            rows, total = api.bills_page("", 1, 100, age)
            it["total"] = total or 0
            sample = list(rows)
            if total and total > 100:
                last_rows, _ = api.bills_page("", (total + 99) // 100, 100, age)
                sample += last_rows
            ds = [_d(r.get("PROPOSE_DT")) for r in sample if _d(r.get("PROPOSE_DT"))]
            if ds:
                it["first"], it["last"] = min(ds), max(ds)
            for f, lab in STAGE_FIELDS:
                it["fields"][lab] = sum(1 for r in sample if _d(r.get(f)))
            it["sample_n"] = len(sample)
        except Exception as e:
            it["error"] = f"{type(e).__name__}: {str(e)[:100]}"
        out.append(it)
    return out


def sync(db_path, api, ages=AGES, max_pages=400, log=print, progress=None, sleep=0.25):
    """대별로 이어받기. max_pages는 이번 호출에서 받을 최대 쪽 수. → {age: {받음, 전체, 끝났나}}"""
    conn = sqlite3.connect(db_path)
    res = {}
    try:
        ensure(conn)
        pages_done = 0
        for age in ages:
            nxt = conn.execute("SELECT value FROM hist_meta WHERE key=?", (f"next_page_{age}",)).fetchone()
            page = int(nxt[0]) if nxt else 1
            total = None
            got = 0
            while pages_done < max_pages:
                rows, total = api.bills_page("", page, 100, age)
                if not rows:
                    conn.execute("INSERT OR REPLACE INTO hist_meta VALUES (?, ?)", (f"done_{age}", dt.date.today().isoformat()))
                    break
                for r in rows:
                    bid = r.get("BILL_ID") or r.get("BILL_NO")
                    if not bid:
                        continue
                    conn.execute("INSERT INTO hist_bills (key, age, name, proposer, propose_dt, committee_dt, cmt_present_dt, cmt_proc_dt, law_proc_dt, proc_dt, proc_result, url) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET age=excluded.age, name=excluded.name, proposer=excluded.proposer, "
                                 "propose_dt=excluded.propose_dt, committee_dt=excluded.committee_dt, cmt_present_dt=excluded.cmt_present_dt, cmt_proc_dt=excluded.cmt_proc_dt, "
                                 "law_proc_dt=excluded.law_proc_dt, proc_dt=excluded.proc_dt, proc_result=excluded.proc_result, url=excluded.url", (
                        f"h:{bid}", age, str(r.get("BILL_NAME") or "")[:200], str(r.get("PROPOSER") or "")[:60], _d(r.get("PROPOSE_DT")),
                        _d(r.get("COMMITTEE_DT")), _d(r.get("CMT_PRESENT_DT")), _d(r.get("CMT_PROC_DT")), _d(r.get("LAW_PROC_DT")),
                        _d(r.get("PROC_DT")), str(r.get("PROC_RESULT") or "")[:30], str(r.get("DETAIL_LINK") or "")[:300]))
                got += len(rows)
                page += 1
                pages_done += 1
                conn.execute("INSERT OR REPLACE INTO hist_meta VALUES (?, ?)", (f"next_page_{age}", str(page)))
                conn.commit()
                if progress:
                    progress(f"{age}대 {min(page * 100 - 100, total or 0)}/{total or '?'}")
                if total and (page - 1) * 100 >= total:
                    conn.execute("INSERT OR REPLACE INTO hist_meta VALUES (?, ?)", (f"done_{age}", dt.date.today().isoformat()))
                    break
                time.sleep(sleep)
            res[age] = dict(받음=got, 전체=total, 끝남=bool(conn.execute("SELECT 1 FROM hist_meta WHERE key=?", (f"done_{age}",)).fetchone()))
        conn.commit()
        log(f"과거 법안: {res}")
        return res
    finally:
        conn.close()


def coverage(conn):
    """화면용: 대별 저장된 법안 수·발의일 범위·끝났는지"""
    out = []
    try:
        for age, n, lo, hi in conn.execute("SELECT age, COUNT(*), MIN(NULLIF(propose_dt,'')), MAX(propose_dt) FROM hist_bills GROUP BY age ORDER BY age"):
            done = conn.execute("SELECT 1 FROM hist_meta WHERE key=?", (f"done_{age}",)).fetchone()
            out.append(dict(age=age, n=n, first=lo or "", last=hi or "", done=bool(done)))
    except sqlite3.OperationalError:
        pass
    return out
