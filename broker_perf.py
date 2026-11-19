# -*- coding: utf-8 -*-
"""
증권사 리포트 성적표: 리포트가 나온 날 종가를 기준으로 그 뒤 1개월·3개월 주가가 어떻게 됐나, 목표가에 닿았나를 증권사별로 모아 봐요.

- 주가가 있는 종목(재무제표 분석 종목·기관 매수 종목·법안 감시 종목)의 리포트만 셀 수 있어요. 지수는 코스피 기준이에요.
- 한계: 대상 종목이 지금 시총 상위·관심 종목이라 살아남은 종목 위주(생존 편향)이고, 시장이 오른 시기에는 거의 모든 증권사가 좋아 보여요.
  지수 대비(초과수익)를 같이 보되, 표본이 작으면 우연일 수 있고 '매수' 리포트가 대부분이라 의견 구분은 약해요. 과거 성적이 앞으로를 보장하지 않아요.
"""
import bisect
import datetime as dt
import sqlite3
import time

_CACHE = {"t": 0.0, "v": None}


def _series(conn, code):
    out = {}
    for q in ("SELECT d, close FROM study_px WHERE code=?", "SELECT d, close FROM bill_px WHERE code=?", "SELECT date, close FROM prices WHERE code=?"):
        try:
            for d, c in conn.execute(q, (code,)):
                if d and c:
                    out[d] = float(c)
        except sqlite3.Error:
            continue
    items = sorted(out.items())
    return [d for d, _ in items], [c for _, c in items]


def _at(dates, closes, d, tol=7, after=False):
    """d 이전(after=True면 d 이후) 첫 거래일의 (인덱스, 종가). tol일보다 멀면 None"""
    if not dates:
        return None
    if after:
        i = bisect.bisect_left(dates, d)
        if i >= len(dates):
            return None
    else:
        i = bisect.bisect_right(dates, d) - 1
        if i < 0:
            return None
    gap = abs((dt.date.fromisoformat(dates[i]) - dt.date.fromisoformat(d)).days)
    return (i, closes[i]) if gap <= tol else None


def compute(conn, today=None, days=730, use_cache=True):
    """→ {증권사: dict(n, n1, r1, x1, n3, r3, x3, win3, buy_n, buy_win3, hit_n, hit, gap_n, gap)} (r=평균 수익률, x=지수 대비 초과, win=올랐던 비율, hit=목표 도달률, gap=목표/당시종가-1 평균)"""
    if use_cache and _CACHE["v"] is not None and time.time() - _CACHE["t"] < 600:
        return _CACHE["v"]
    import research
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    until = (today - dt.timedelta(days=30)).isoformat()
    try:
        rows = conn.execute("SELECT code, date, broker, target, opinion FROM reports WHERE date>=? AND date<=? ORDER BY code, date", (since, until)).fetchall()
    except sqlite3.Error:
        return {}
    try:
        import stock_market
        idx = list(stock_market.series(conn, "idx:KOSPI"))
        idates, icloses = [d for d, _ in idx], [float(v) for _, v in idx]
    except Exception:
        idates, icloses = [], []
    agg = {}
    cache = {}
    last_day = today.isoformat()
    for code, d, broker, target, opinion in rows:
        if code not in cache:
            cache[code] = _series(conn, code)
        dates, closes = cache[code]
        p0 = _at(dates, closes, d)
        if not p0:
            continue
        b = research.norm_broker(broker)
        a = agg.setdefault(b, dict(n=0, r1=[], x1=[], r3=[], x3=[], buy3=[], hit=[], gap=[]))
        a["n"] += 1
        i0, c0 = p0
        ix0 = _at(idates, icloses, d)
        for hz, rk, xk in ((30, "r1", "x1"), (91, "r3", "x3")):
            tgt = (dt.date.fromisoformat(d) + dt.timedelta(days=hz)).isoformat()
            if tgt > last_day:
                continue
            p1 = _at(dates, closes, tgt, tol=10, after=True)
            if not p1:
                continue
            r = p1[1] / c0 - 1
            a[rk].append(r)
            ix1 = _at(idates, icloses, tgt, tol=10, after=True)
            if ix0 and ix1:
                a[xk].append(r - (ix1[1] / ix0[1] - 1))
            if hz == 91 and opinion and any(w in opinion for w in ("매수", "Buy", "BUY", "비중확대", "Outperform")):
                a["buy3"].append(r)
        if target:
            end = (dt.date.fromisoformat(d) + dt.timedelta(days=180)).isoformat()
            if end <= last_day and dates and dates[-1] >= end:
                j = bisect.bisect_right(dates, end)
                a["hit"].append(1 if max(closes[i0 + 1:j] or [0]) >= target else 0)
            if 0.2 <= target / c0 <= 4.0:
                a["gap"].append(target / c0 - 1)
    out = {}
    mean = lambda v: sum(v) / len(v) if v else None
    for b, a in agg.items():
        out[b] = dict(n=a["n"], n1=len(a["r1"]), r1=mean(a["r1"]), x1=mean(a["x1"]), n3=len(a["r3"]), r3=mean(a["r3"]), x3=mean(a["x3"]),
                      win3=(sum(1 for v in a["r3"] if v > 0) / len(a["r3"])) if a["r3"] else None,
                      buy_n=len(a["buy3"]), buy_win3=(sum(1 for v in a["buy3"] if v > 0) / len(a["buy3"])) if a["buy3"] else None,
                      hit_n=len(a["hit"]), hit=mean(a["hit"]), gap_n=len(a["gap"]), gap=mean(a["gap"]))
    _CACHE.update(t=time.time(), v=out)
    return out
