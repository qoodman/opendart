# -*- coding: utf-8 -*-
"""
상장사 실적 흐름 (시가총액 상위 종목 합산 영업이익) — 투자 사이클의 '기업 실적 방향' 축.

- 네이버 증권 종목 재무(분기·연간, 컨센서스 포함)에서 영업이익을 받아 시가총액 상위 종목을 합산해 봐요. 키 불필요.
- 보는 것: ① 확정된 가장 최근 분기의 전년 동기 대비 합산 증감 ② 영업이익이 늘어난 종목 비율(폭) ③ 올해 컨센서스 대 지난해 실적
  ④ 컨센서스가 4주 전보다 올랐나/내렸나 (스냅샷을 쌓아 가며 계산하므로 처음 몇 주는 비어 있어요).
- 한계: 시총 상위 몇십 개라 대형주(반도체 등) 편중이 커요. 합산이라 한두 종목이 방향을 좌우할 수 있어서 중앙값·편중도를 같이 적어요.
  컨센서스는 증권사 추정이라 틀릴 수 있고, 적자 전환·흑자 전환은 증감률이 의미 없어서 따로 세요.
"""
import datetime as dt
import sqlite3
import time

import requests

FIN_URL = "https://m.stock.naver.com/api/stock/{code}/finance/{kind}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
           "Referer": "https://m.stock.naver.com/"}
TOP_N = 40
REFRESH_DAYS = 6


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS earn_fin (code TEXT, kind TEXT, period TEXT, op REAL, cons INTEGER, fetched TEXT, PRIMARY KEY (code, kind, period));
    CREATE TABLE IF NOT EXISTS earn_snap (code TEXT, period TEXT, op REAL, snap TEXT, PRIMARY KEY (code, period, snap));
    CREATE TABLE IF NOT EXISTS earn_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_fin(j):
    """네이버 재무 JSON → [(period 'YYYYMM', 영업이익(억원), 컨센서스 여부)]"""
    info = (j or {}).get("financeInfo") or {}
    cons = {t.get("key"): t.get("isConsensus") == "Y" for t in info.get("trTitleList", [])}
    for row in info.get("rowList", []):
        if row.get("title") == "영업이익":
            out = []
            for k, c in (row.get("columns") or {}).items():
                v = _num((c or {}).get("value"))
                if v is not None:
                    out.append((k, v, cons.get(k, False)))
            return sorted(out)
    return []


def fetch(code, session=None):
    s = session or requests
    res = {}
    for kind, name in (("quarter", "Q"), ("annual", "A")):
        r = s.get(FIN_URL.format(code=code, kind=kind), headers=HEADERS, timeout=20)
        if r.status_code != 200:
            continue
        res[name] = parse_fin(r.json())
    return res


def universe(conn, n=TOP_N * 2):
    """시가총액 상위 종목 코드. KRX 시세가 없으면 코스피 지수 구성 종목 일부로 대신해요."""
    try:
        rows = conn.execute("SELECT code FROM krx_daily WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_daily) AND mktcap IS NOT NULL ORDER BY mktcap DESC LIMIT ?", (n,)).fetchall()
        codes = [r[0] for r in rows if str(r[0]).isdigit() and len(str(r[0])) == 6]
        if codes:
            return codes
    except sqlite3.Error:
        pass
    try:
        j = requests.get("https://m.stock.naver.com/api/index/KOSPI/integration", headers=HEADERS, timeout=20).json()
        return [x["itemCode"] for x in j.get("enrollStocks", []) if x.get("itemCode")]
    except (requests.RequestException, ValueError, KeyError):
        return []


def sync(db_path, log=print, force=False, session=None, sleep=0.25):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        today = dt.date.today()
        last = conn.execute("SELECT value FROM earn_meta WHERE key='synced'").fetchone()
        if last and not force and last[0] >= (today - dt.timedelta(days=REFRESH_DAYS)).isoformat():
            return "최근에 받음"
        codes = universe(conn)
        if not codes:
            return "종목 목록 없음"
        ok = fail = 0
        for code in codes:
            if ok >= TOP_N:
                break
            try:
                d = fetch(code, session)
            except (requests.RequestException, ValueError) as e:
                fail += 1
                log(f"  ✖ {code}: {type(e).__name__}")
                continue
            if not d.get("Q") and not d.get("A"):
                continue
            for kind, pts in d.items():
                for period, op, cons in pts:
                    conn.execute("INSERT OR REPLACE INTO earn_fin VALUES (?,?,?,?,?,?)", (code, kind, period, op, int(cons), today.isoformat()))
                    if cons:
                        conn.execute("INSERT OR REPLACE INTO earn_snap VALUES (?,?,?,?)", (code, f"{kind}{period}", op, today.isoformat()))
            ok += 1
            time.sleep(sleep)
        conn.execute("INSERT OR REPLACE INTO earn_meta VALUES ('synced', ?)", (today.isoformat(),))
        conn.commit()
        log(f"[실적] 종목 {ok}개 받음 (실패 {fail})")
        return f"{ok}종목"
    finally:
        conn.close()


def _shift(p, quarters):
    y, m = int(p[:4]), int(p[4:])
    idx = y * 4 + (m - 1) // 3 + quarters
    return f"{idx // 4}{(idx % 4) * 3 + 3:02d}"


def analyze(conn):
    """→ dict 또는 None(자료 없음): n, period(최근 확정 분기), yoy_sum, qoq_sum, breadth, median_yoy, top2_share, fwd(올해 컨센서스/작년 실적-1), rev(4주 전 대비 컨센서스 변화), score, label, notes[]"""
    try:
        q = {}
        for code, period, op, cons in conn.execute("SELECT code, period, op, cons FROM earn_fin WHERE kind='Q'"):
            q.setdefault(code, {})[period] = (op, cons)
        a = {}
        for code, period, op, cons in conn.execute("SELECT code, period, op, cons FROM earn_fin WHERE kind='A'"):
            a.setdefault(code, {})[period] = (op, cons)
        synced = (conn.execute("SELECT value FROM earn_meta WHERE key='synced'").fetchone() or [""])[0]
    except sqlite3.Error:
        return None
    if not q:
        return None
    # 확정(컨센서스 아님) 분기 중 가장 최근이면서 종목 70% 이상이 갖고 있는 분기
    from collections import Counter
    cnt = Counter(p for d in q.values() for p, (_, c) in d.items() if not c)
    need = 0.7 * len(q)
    cands = sorted([p for p, k in cnt.items() if k >= need], reverse=True)
    if not cands:
        return None
    L = cands[0]
    P, PQ = _shift(L, -4), _shift(L, -1)
    yoy_pairs = [(d[L][0], d[P][0]) for d in q.values() if L in d and P in d and not d[L][1] and not d[P][1]]
    qoq_pairs = [(d[L][0], d[PQ][0]) for d in q.values() if L in d and PQ in d and not d[L][1] and not d[PQ][1]]
    if len(yoy_pairs) < 8:
        return None
    s_now, s_prev = sum(x for x, _ in yoy_pairs), sum(y for _, y in yoy_pairs)
    yoy = (s_now / s_prev - 1) if s_prev > 0 else None
    up = sum(1 for x, y in yoy_pairs if x > y)
    breadth = up / len(yoy_pairs)
    chg = sorted((x / y - 1) for x, y in yoy_pairs if y > 0)
    med = chg[len(chg) // 2] if chg else None
    tot = sorted((x for x, _ in yoy_pairs), reverse=True)
    top2 = (sum(tot[:2]) / sum(tot)) if sum(tot) > 0 else None
    qoq = None
    if len(qoq_pairs) >= 8:
        sp = sum(y for _, y in qoq_pairs)
        qoq = (sum(x for x, _ in qoq_pairs) / sp - 1) if sp > 0 else None
    # 올해 컨센서스 대 작년 실적
    fwd = None
    ypairs = []
    for d in a.values():
        cons_y = sorted(p for p, (_, c) in d.items() if c)
        act_y = sorted(p for p, (_, c) in d.items() if not c)
        if cons_y and act_y:
            ypairs.append((d[cons_y[0]][0], d[act_y[-1]][0]))
    if len(ypairs) >= 8:
        sp = sum(y for _, y in ypairs)
        fwd = (sum(x for x, _ in ypairs) / sp - 1) if sp > 0 else None
    # 컨센서스 수정: 같은 종목·기간의 가장 최근 스냅샷 대비 21일 이상 전 스냅샷
    rev = None
    try:
        snaps = {}
        for code, period, op, snap in conn.execute("SELECT code, period, op, snap FROM earn_snap WHERE period LIKE 'A%'"):
            snaps.setdefault((code, period), []).append((snap, op))
        pairs = []
        for lst in snaps.values():
            lst.sort()
            new = lst[-1]
            old = [x for x in lst if (dt.date.fromisoformat(new[0]) - dt.date.fromisoformat(x[0])).days >= 21]
            if old:
                pairs.append((new[1], old[-1][1]))
        if len(pairs) >= 8:
            sp = sum(y for _, y in pairs)
            rev = (sum(x for x, _ in pairs) / sp - 1) if sp > 0 else None
    except (sqlite3.Error, ValueError):
        rev = None
    score = 0
    notes = []
    if yoy is not None:
        score += 1 if yoy > 0.10 else -1 if yoy < -0.10 else 0
    score += 1 if breadth >= 0.6 else -1 if breadth <= 0.4 else 0
    if fwd is not None:
        score += 1 if fwd > 0.10 else -1 if fwd < -0.10 else 0
    if rev is not None:
        score += 1 if rev > 0.02 else -1 if rev < -0.02 else 0
    label = "개선" if score >= 2 else "악화" if score <= -2 else "혼조·정체"
    if top2 is not None and top2 > 0.5:
        notes.append(f"합산 영업이익의 {top2:.0%}를 상위 2개 종목이 차지해요 — 반도체 등 소수 대형주가 방향을 좌우해요")
    if med is not None and yoy is not None and (yoy > 0.1) != (med > 0.1):
        notes.append(f"합산({yoy:+.0%})과 종목 중앙값({med:+.0%})이 달라요 — 일부 종목이 끌고 가는 모양이에요")
    return dict(n=len(yoy_pairs), period=L, yoy=yoy, qoq=qoq, breadth=breadth, median=med, top2=top2, fwd=fwd, rev=rev, score=score, label=label, notes=notes, synced=synced)
