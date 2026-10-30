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
        author TEXT, broker TEXT, pdf TEXT, url TEXT, detail INTEGER DEFAULT 1,
        PRIMARY KEY (source, date, code, broker, title))""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_code ON reports(code, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_reports_date ON reports(date)")
    # 예전 표(hk_reports)에 받아 둔 것을 옮김 (한 번만)
    old = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hk_reports'").fetchone()
    if old:
        conn.execute("INSERT OR IGNORE INTO reports SELECT 'hk', date, code, name, title, target, opinion, "
                     "author, broker, pdf, '', 1 FROM hk_reports")
        conn.execute("DROP TABLE hk_reports")
    conn.commit()


def last_date(conn, source):
    return conn.execute("SELECT MAX(date) FROM reports WHERE source=?", (source,)).fetchone()[0]


def store(conn, source, rows):
    """rows: dict(date, code, name, title, target, opinion, author, broker, pdf, url, detail). 새로 넣은 건수."""
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (source, r["date"], r["code"], r.get("name", ""), r.get("title", ""), r.get("target"),
         r.get("opinion", ""), r.get("author", ""), r.get("broker", ""), r.get("pdf", ""),
         r.get("url", ""), r.get("detail", 1)) for r in rows])
    conn.commit()
    return conn.total_changes - before


def norm_broker(name):
    """'미래에셋증권' / '미래에셋' 처럼 출처마다 다른 이름을 같게"""
    n = re.sub(r"\s|\(.*?\)|㈜|주식회사", "", name or "")
    n = re.sub(r"(증권|투자증권|금융투자|리서치센터|리서치)$", "", n)
    return n or "?"


def consensus(conn, code, days=180):
    """증권사별 가장 최근 목표가(최근 days일)의 평균 → (평균, 증권사 수) 또는 (None, 0)"""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        rows = conn.execute("SELECT broker, target FROM reports WHERE code=? AND date>=? AND target IS NOT NULL "
                            "ORDER BY date DESC", (code, since)).fetchall()
    except sqlite3.OperationalError:
        return None, 0
    latest = {}
    for broker, target in rows:
        latest.setdefault(norm_broker(broker), target)
    if not latest:
        return None, 0
    return sum(latest.values()) / len(latest), len(latest)


def sync_all(db_path, debug_dir=None, log=print, use_hk=True, use_nv=True, first_days=None):
    """켜진 출처를 모두 받음. {출처: 새 건수 또는 '실패: …'}"""
    conn = sqlite3.connect(db_path)
    ensure_table(conn)
    conn.close()
    out = {}
    if use_hk:
        try:
            import hk_consensus
            out["hk"] = hk_consensus.sync(db_path, debug_dir=debug_dir, log=log, first_days=first_days or 180)
        except Exception as e:
            out["hk"] = f"실패: {str(e)[:200]}"
            log(f"[리포트] 한경컨센서스 실패: {e}")
    if use_nv:
        try:
            import naver_research
            out["nv"] = naver_research.sync(db_path, debug_dir=debug_dir, log=log, first_days=first_days or 30)
        except Exception as e:
            out["nv"] = f"실패: {str(e)[:200]}"
            log(f"[리포트] 네이버 증권 실패: {e}")
    return out
