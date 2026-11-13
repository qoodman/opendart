# -*- coding: utf-8 -*-
"""
증권사 리포트 모음 (한경컨센서스 + 네이버 증권 리서치)

모든 출처를 reports 표 하나에 모읍니다. 목표가 평균(consensus)도 출처를 합쳐 증권사별 최신 목표가로 계산합니다.
"""
import re, sqlite3, datetime as dt

SOURCES = {"hk": "한경컨센서스", "nv": "네이버 증권"}


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS reports (
        source TEXT, date TEXT, code TEXT, name TEXT, title TEXT, target INTEGER, opinion TEXT,
        author TEXT, broker TEXT, pdf TEXT, url TEXT, detail INTEGER DEFAULT 1, summary TEXT,
        PRIMARY KEY (source, date, code, broker, title))""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reports)")}
    if "summary" not in cols:      # 예전 표에 요약 칸 추가
        conn.execute("ALTER TABLE reports ADD COLUMN summary TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_code ON reports(code, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_date ON reports(date)")
    # 예전 표(hk_reports)에 받아 둔 것을 옮김 (한 번만)
    old = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hk_reports'").fetchone()
    if old:
        conn.execute("INSERT OR IGNORE INTO reports SELECT 'hk', date, code, name, title, target, opinion, "
                     "author, broker, pdf, '', 1, NULL FROM hk_reports")
        conn.execute("DROP TABLE hk_reports")
    conn.commit()


def last_date(conn, source):
    return conn.execute("SELECT MAX(date) FROM reports WHERE source=?", (source,)).fetchone()[0]


def store(conn, source, rows):
    """rows: dict(date, code, name, title, target, opinion, author, broker, pdf, url, detail, summary).
    이미 있는 리포트는 비어 있던 목표가·의견·요약만 채움. 새로 넣은 건수를 돌려줌."""
    before = conn.execute("SELECT COUNT(*) FROM reports WHERE source=?", (source,)).fetchone()[0]
    conn.executemany("""INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source, date, code, broker, title) DO UPDATE SET
          target=COALESCE(reports.target, excluded.target),
          opinion=CASE WHEN COALESCE(reports.opinion,'')='' THEN excluded.opinion ELSE reports.opinion END,
          summary=COALESCE(reports.summary, excluded.summary),
          detail=MAX(reports.detail, excluded.detail)""", [
        (source, r["date"], r["code"], r.get("name", ""), r.get("title", ""), r.get("target"),
         r.get("opinion", ""), r.get("author", ""), r.get("broker", ""), r.get("pdf", ""),
         r.get("url", ""), r.get("detail", 1), r.get("summary")) for r in rows])
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM reports WHERE source=?", (source,)).fetchone()[0] - before


def norm_broker(name):
    """'미래에셋증권' / '미래에셋' 처럼 출처마다 다른 이름을 같게"""
    n = re.sub(r"\s|\(.*?\)|㈜|주식회사", "", name or "")
    n = re.sub(r"(증권|투자증권|금융투자|리서치센터|리서치)$", "", n)
    return n or "?"


# 목표가가 주가의 이 범위를 벗어나면 출처의 종목 표기 오류로 봄 (예: 현대차 리포트가 삼영전자로 올라온 경우)
PLAUSIBLE = (0.2, 4.0)


def latest_close(conn, code, on=None):
    """종목의 최근 종가 (on을 주면 그 날 이전 마지막 종가)"""
    try:
        if on:
            r = conn.execute("SELECT close FROM prices WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                             (code, on)).fetchone()
        else:
            r = conn.execute("SELECT close FROM prices WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def suspicious(target, close):
    """목표가가 주가와 맞지 않으면 True (종목이 잘못 붙었을 가능성)"""
    if not target or not close:
        return False
    ratio = target / close
    return not (PLAUSIBLE[0] <= ratio <= PLAUSIBLE[1])


def core_title(title):
    """'삼영전자(005680) 4분기 물량…' → '4분기물량…' (앞의 종목 표기를 빼고 비교용으로)"""
    t = re.sub(r"^[^()]{1,30}\(\d{6}\)\s*", "", title or "")
    return re.sub(r"[\s\W_]+", "", t)


def reconcile(conn, log=print):
    """한경 리포트의 종목이 네이버의 같은 리포트(같은 날·증권사·제목)와 다르면 네이버 종목으로 바로잡음.
    (한경 목록 제목에 종목이 잘못 적힌 경우가 있음)"""
    try:
        nv = conn.execute("SELECT date, broker, title, code, name FROM reports WHERE source='nv'").fetchall()
    except sqlite3.OperationalError:
        return 0
    index = {}
    for date, broker, title, code, name in nv:
        index.setdefault((date, norm_broker(broker), core_title(title)), (code, name))
    fixed = 0
    for rowid, date, broker, title, code in conn.execute(
            "SELECT rowid, date, broker, title, code FROM reports WHERE source='hk'").fetchall():
        key = core_title(title)
        if len(key) < 4:
            continue
        hit = None
        for d in (date, (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat(),
                  (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()):
            hit = index.get((d, norm_broker(broker), key))
            if hit:
                break
        if hit and hit[0] and hit[0] != code:
            new_title = re.sub(r"^[^()]{1,30}\(\d{6}\)", f"{hit[1]}({hit[0]})", title, count=1)
            dup = conn.execute("SELECT 1 FROM reports WHERE source='hk' AND date=? AND code=? AND broker=? AND title=?",
                               (date, hit[0], broker, new_title)).fetchone()
            if dup:      # 이미 바로잡은 것이 있으면 다시 들어온 잘못된 줄은 지움
                conn.execute("DELETE FROM reports WHERE rowid=?", (rowid,))
            else:
                conn.execute("UPDATE reports SET code=?, name=?, title=? WHERE rowid=?", (hit[0], hit[1], new_title, rowid))
            fixed += 1
            log(f"  · 종목 바로잡음: {title[:30]} → {hit[1]}({hit[0]})")
    conn.commit()
    return fixed


def consensus(conn, code, days=180):
    """증권사별 가장 최근 목표가(최근 days일)의 평균 → (평균, 증권사 수) 또는 (None, 0).
    주가와 맞지 않는 목표가(종목 표기 오류 의심)는 뺌."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        rows = conn.execute("SELECT broker, target FROM reports WHERE code=? AND date>=? AND target IS NOT NULL "
                            "ORDER BY date DESC", (code, since)).fetchall()
    except sqlite3.OperationalError:
        return None, 0
    close = latest_close(conn, code)
    latest = {}
    for broker, target in rows:
        if suspicious(target, close):
            continue
        latest.setdefault(norm_broker(broker), target)
    if not latest:
        return None, 0
    return sum(latest.values()) / len(latest), len(latest)


def sync_all(db_path, debug_dir=None, log=print, use_hk=True, use_nv=True, first_days=None):
    """켜진 출처를 모두 받음. {출처: 새 건수 또는 '실패: …'}"""
    conn = sqlite3.connect(db_path)
    ensure_table(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS research_meta (key TEXT PRIMARY KEY, value TEXT)")
    # 최근 1년치 리포트를 보려고, 처음 한 번만 1년 전까지 거슬러 채움 (두 출처가 모두 성공했을 때만 완료로 기록)
    backfill = not conn.execute("SELECT 1 FROM research_meta WHERE key='year_backfill'").fetchone() and not first_days
    conn.commit()
    conn.close()
    out = {}
    if use_hk:
        try:
            import hk_consensus
            out["hk"] = hk_consensus.sync(db_path, debug_dir=debug_dir, log=log, first_days=first_days or 180,
                                            backfill_days=365 if backfill else None)
        except Exception as e:
            out["hk"] = f"실패: {str(e)[:200]}"
            log(f"[리포트] 한경컨센서스 실패: {e}")
    if use_nv:
        try:
            import naver_research
            out["nv"] = naver_research.sync(db_path, debug_dir=debug_dir, log=log, first_days=first_days or (365 if backfill else 30))
        except Exception as e:
            out["nv"] = f"실패: {str(e)[:200]}"
            log(f"[리포트] 네이버 증권 실패: {e}")
    if backfill and all(isinstance(out.get(k), int) for k, on in (("hk", use_hk), ("nv", use_nv)) if on):
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT OR REPLACE INTO research_meta VALUES ('year_backfill', ?)", (dt.date.today().isoformat(),))
        conn.commit()
        conn.close()
        log("[리포트] 최근 1년치 채우기를 마쳤습니다")
    try:
        conn = sqlite3.connect(db_path)
        n = reconcile(conn, log)
        conn.close()
        if n:
            out["fixed"] = n
            log(f"[리포트] 한경 리포트 종목 {n}건을 네이버 기준으로 바로잡았습니다")
    except sqlite3.Error as e:
        log(f"  ! 리포트 종목 대조 실패: {e}")
    return out
