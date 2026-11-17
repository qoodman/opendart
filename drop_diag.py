# -*- coding: utf-8 -*-
"""
하락 원인 점검: 코스피 하락에서 어느 종목·묶음이 얼마나 끌어내렸는지(시가총액 기여도)와, 고점 대비 낙폭.

기여도 = 종목 시가총액 변화 ÷ 시작일 코스피 전체 시가총액 (%p). 시가총액 가중 지수의 변화를 종목별로 나눠 놓은 근삿값이에요
  (공식 지수는 유동주식 비율·지수 편입 방식이 달라서 숫자가 조금 다를 수 있어요). 앱이 받아 둔 KRX 일별 시세(krx_daily)만 쓰고,
  보관한 거래일(기본 25일 안팎) 안에서만 계산해요. 원인을 단정하지 않고 '누가 얼마나 빠졌나'만 보여 줘요.
"""
import sqlite3

SEMI = {"005930": "삼성전자", "005935": "삼성전자우", "000660": "SK하이닉스"}


def days(conn):
    try:
        return [r[0] for r in conn.execute("SELECT DISTINCT bas_dd FROM krx_daily WHERE market='코스피' ORDER BY bas_dd")]
    except sqlite3.OperationalError:
        return []


def _caps(conn, day):
    return {c: (n, m) for c, n, m in conn.execute(
        "SELECT code, name, mktcap FROM krx_daily WHERE bas_dd=? AND market='코스피' AND mktcap > 0", (day,))}


def contrib(conn, start, end, top=8):
    """start → end 사이 기여도. {start, end, total, semi, other, down:[(이름, %p)], up:[(이름, %p)]} 또는 None"""
    a, b = _caps(conn, start), _caps(conn, end)
    base = sum(m for _, m in a.values())
    if not a or not b or base <= 0:
        return None
    rows = [(c, b[c][0] or a[c][0], (b[c][1] - a[c][1]) / base * 100) for c in a if c in b]
    total = sum(r[2] for r in rows)
    semi = sum(r[2] for r in rows if r[0] in SEMI)
    by = sorted(rows, key=lambda r: r[2])
    return dict(start=start, end=end, total=total, semi=semi, other=total - semi, n=len(rows),
                down=[(n, v) for _, n, v in by[:top] if v < 0], up=[(n, v) for _, n, v in reversed(by[-top:]) if v > 0])


def windows(conn):
    """[(라벨, 시작일, 끝일)] — 1거래일 · 5거래일 · 보유 기간 중 시총 고점부터 · 보유 시작부터"""
    ds = days(conn)
    if len(ds) < 2:
        return []
    end = ds[-1]
    out = [("하루 (직전 거래일 대비)", ds[-2], end)]
    if len(ds) >= 6:
        out.append(("최근 5거래일", ds[-6], end))
    tot = {}
    for d in ds:
        tot[d] = sum(m for _, m in _caps(conn, d).values())
    peak = max(tot, key=tot.get)
    if peak != end and peak not in (ds[-2], ds[-6] if len(ds) >= 6 else None):
        out.append((f"보유 기간 고점({peak[4:6]}/{peak[6:]})부터", peak, end))
    if ds[0] not in (peak, ds[-2], ds[-6] if len(ds) >= 6 else None):
        out.append((f"보유 시작({ds[0][4:6]}/{ds[0][6:]})부터", ds[0], end))
    return out


def drawdown(points, lookback=250):
    """[(YYYY-MM-DD, 종가)] → dict(last, high, high_date, dd(%), since_days) — 최근 lookback개 안의 고점 대비. 자료가 없으면 None"""
    pts = [(d, v) for d, v in points if v][-lookback:]
    if len(pts) < 2:
        return None
    hd, hv = max(pts, key=lambda x: x[1])
    d, v = pts[-1]
    return dict(last=v, last_date=d, high=hv, high_date=hd, dd=(v / hv - 1) * 100)
