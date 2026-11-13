# -*- coding: utf-8 -*-
"""
투자 방법별 점수의 일별 기록: 종합 점수가 시간이 지나며 오르는지 내리는지 보려고, 수집할 때마다 하루 한 줄씩 저장합니다.
  - 점수는 그날 자료(재무·PER·종가)로 계산한 값이라, 자료가 갱신되면서 바뀐 것도 '변화'에 섞여요 (실제 사업이 좋아졌다는 뜻은 아니에요).
  - 마법공식·모멘텀은 그날 목록 안 상대 순위라서, 목록 구성이 달라지면 점수가 움직여요.
"""
import datetime as dt
import sqlite3

KEYS = ("lynch", "buffett", "graham", "magic", "fscore", "momentum")
KEEP_DAYS = 400


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS score_hist (code TEXT, d TEXT, avg REAL, lynch REAL, buffett REAL, graham REAL, "
                 "magic REAL, fscore REAL, momentum REAL, PRIMARY KEY (code, d))")


def save(conn, cards, today=None):
    """cards: overall_cards 결과 → 오늘 날짜로 저장(같은 날 다시 돌리면 덮어씀). 저장한 종목 수"""
    ensure(conn)
    d = (today or dt.date.today()).isoformat()
    n = 0
    for c in cards:
        if not c["sc"]:
            continue
        conn.execute("INSERT OR REPLACE INTO score_hist VALUES (?,?,?,?,?,?,?,?,?)",
                     (c["code"], d, c["avg"], *(c["sc"].get(k) for k in KEYS)))
        n += 1
    conn.execute("DELETE FROM score_hist WHERE d < ?", (((today or dt.date.today()) - dt.timedelta(days=KEEP_DAYS)).isoformat(),))
    conn.commit()
    return n


def load(conn, codes=None, keys=None):
    """{code: [(날짜, 평균)…]} 오래된 것부터. keys(방법 이름들)를 주면 그 방법들 점수만 평균냄"""
    cols = ", ".join(keys) if keys else "avg"
    keys = list(keys) if keys else None
    try:
        rows = conn.execute(f"SELECT code, d, {cols} FROM score_hist ORDER BY code, d").fetchall()
    except sqlite3.OperationalError:
        return {}
    want = set(codes) if codes is not None else None
    out = {}
    for row in rows:
        code, d = row[0], row[1]
        if want is not None and code not in want:
            continue
        if keys:
            vals = [v for v in row[2:] if v is not None]
            if not vals:
                continue
            a = sum(vals) / len(vals)
        else:
            a = row[2]
        out.setdefault(code, []).append((d, a))
    return out


def change(series, days=7, today=None):
    """→ (변화량, 비교한 날짜) 또는 None. days일 전 이전의 가장 가까운 기록과 비교 (기록이 days-2일치도 안 되면 None)"""
    if not series or len(series) < 2:
        return None
    last_d, last_v = series[-1]
    target = (dt.date.fromisoformat(last_d) - dt.timedelta(days=days)).isoformat()
    base = None
    for d, v in series[:-1]:
        if d <= target:
            base = (d, v)
    if base is None:
        first = series[0][0]
        if (dt.date.fromisoformat(last_d) - dt.date.fromisoformat(first)).days >= max(1, days - 2):
            base = series[0]
        else:
            return None
    return last_v - base[1], base[0]
