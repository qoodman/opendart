# -*- coding: utf-8 -*-
"""
종목 밸류에이션: PER·PBR·배당수익률·EPS·BPS (네이버 증권 종목 요약, 키 불필요).

- 공시에 나온 종목만 받습니다 (summary 테이블의 종목코드).
- 하루에 한 번만 받고, 받은 값은 results.sqlite 의 valuation 테이블에 둡니다.
- 적자 기업은 PER이 없을 수 있습니다 ('-' 또는 'N/A'): 그때는 빈칸으로 둡니다.
- 참고 지표입니다. 장부 기준 값이라 실적 발표 전후에 늦게 바뀔 수 있습니다.
"""
import re, time, sqlite3, datetime as dt

import requests

INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}
# 네이버 응답의 항목 코드 → 우리 칼럼
WANT = {"per": "per", "pbr": "pbr", "dividendyieldratio": "div", "eps": "eps", "bps": "bps"}
WANT_KEY = {"PER": "per", "PBR": "pbr", "배당수익률": "div", "EPS": "eps", "BPS": "bps"}
MAX_FAIL_STREAK = 5          # 이만큼 연속 실패하면 오늘은 그만 (차단·점검 중일 때 시간 낭비 방지)


class ValueError_(Exception):
    pass


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS valuation (
        code TEXT PRIMARY KEY, per REAL, pbr REAL, div REAL, eps REAL, bps REAL, fetched TEXT);
    """)


def _num(text):
    """'12.34배' '2.1%' '1,234' '-' 'N/A' → float 또는 None"""
    if text is None:
        return None
    s = str(text).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def parse(payload):
    """네이버 integration 응답 → {per, pbr, div, eps, bps} (없는 값은 None)"""
    out = {v: None for v in WANT.values()}
    items = payload.get("totalInfos") if isinstance(payload, dict) else None
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").lower()
        key = str(it.get("key") or it.get("name") or "")
        col = WANT.get(code) or WANT_KEY.get(key)
        if col:
            out[col] = _num(it.get("value"))
    return out


def fetch(code, session=None, timeout=10):
    s = session or requests
    r = s.get(INTEGRATION_URL.format(code=code), headers=HEADERS, timeout=timeout)
    if r.status_code != 200:
        raise ValueError_(f"HTTP {r.status_code}")
    try:
        return parse(r.json())
    except ValueError:
        raise ValueError_("응답을 읽지 못함")


def sync(db_path, log=print, sleep=0.4, session=None):
    """공시 종목들의 밸류에이션을 받아 저장. 오늘 받은 종목은 건너뜀."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure(conn)
        try:
            codes = [r[0] for r in conn.execute(
                "SELECT DISTINCT 종목코드 FROM summary WHERE 종목코드 IS NOT NULL AND 종목코드 != ''")]
        except sqlite3.OperationalError:
            return {"대상": 0, "저장": 0, "건너뜀": 0, "실패": 0}
        today = dt.date.today().isoformat()
        done = {r[0] for r in conn.execute("SELECT code FROM valuation WHERE fetched=?", (today,))}
        todo = [c for c in codes if c not in done]
        saved = failed = streak = 0
        for i, code in enumerate(todo):
            if streak >= MAX_FAIL_STREAK:
                log(f"밸류에이션: {MAX_FAIL_STREAK}번 연속 실패해서 나머지 {len(todo) - i}개는 다음에 받습니다.")
                break
            try:
                v = fetch(code, session=session)
                conn.execute("INSERT OR REPLACE INTO valuation VALUES (?,?,?,?,?,?,?)",
                             (code, v["per"], v["pbr"], v["div"], v["eps"], v["bps"], today))
                conn.commit()
                saved += 1
                streak = 0
            except Exception as e:
                failed += 1
                streak += 1
                log(f"밸류에이션 {code} 실패: {e}")
            time.sleep(sleep)
        log(f"밸류에이션: 대상 {len(codes)}개 · 새로 저장 {saved} · 실패 {failed} · 오늘 이미 받음 {len(done & set(codes))}")
        return {"대상": len(codes), "저장": saved, "건너뜀": len(codes) - len(todo), "실패": failed}
    finally:
        conn.close()


def values(conn):
    """{code: {per, pbr, div, eps, bps}} — 테이블이 없으면 빈 dict"""
    try:
        rows = conn.execute("SELECT code, per, pbr, div, eps, bps FROM valuation").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: {"per": r[1], "pbr": r[2], "div": r[3], "eps": r[4], "bps": r[5]} for r in rows}
