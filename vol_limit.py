"""변동성 한도: 코스피의 실제(실현) 변동성으로 '하루·한 달 얼마나 흔들릴 수 있는지'와,
변동성 타깃팅 펀드의 투자 한도가 기계적으로 얼마나 줄었는지(손실한도 ÷ 변동성)를 계산한다.
가격만 쓰는 단순 계산이고, 거래소 VKOSPI(옵션 기반 예상치)와는 다른 값이다."""
import math
import sqlite3

import stock_market

DAYS = 250
BASE = 0.20  # 평상시 변동성 (글에서 쓰는 기준선, 2010년 이후 VKOSPI 평균 20 근처)


def _norm(d):
    d = "".join(ch for ch in str(d) if ch.isdigit())
    return d[:8]


def _vol(rets):
    if len(rets) < 5:
        return None
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(DAYS)


def kospi(conn):
    return [(_norm(p), v) for p, v in stock_market.series(conn, "idx:KOSPI") if v]


def level(v):
    if v is None:
        return ""
    if v < 0.25:
        return "평상시"
    if v < 0.35:
        return "큰 변동성"
    if v < 0.5:
        return "매우 큰 변동성"
    return "위기급 (2008·2020년에 보던 수준)"


def compute(conn):
    ser = kospi(conn)
    if len(ser) < 80:
        return None
    cl = [v for _, v in ser]
    rets = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl))]
    out = dict(date=ser[-1][0], n=len(ser))
    out["v20"] = _vol(rets[-20:])
    out["v60"] = _vol(rets[-60:])
    out["v250"] = _vol(rets[-250:])
    cur = out["v20"]
    out["day"] = cur / math.sqrt(DAYS)
    out["month"] = cur / math.sqrt(12)
    out["limit_mult"] = BASE / cur if cur else None  # 평상시 한도를 100%로 봤을 때 지금 남은 비율
    roll = [_vol(rets[i - 20:i]) for i in range(20, len(rets) + 1, 5)]
    roll = [x for x in roll if x]
    if roll:
        out["pct"] = sum(1 for x in roll if x <= cur) / len(roll)
        out["hist"] = [(ser[min(len(ser) - 1, 20 + i * 5)][0], x) for i, x in enumerate(roll)][-120:]
        out["hist_med"] = sorted(roll)[len(roll) // 2]
    big = [abs(r) for r in rets[-250:]]
    out["big5"] = sum(1 for x in big if x >= 0.05)
    out["big5_all"] = sum(1 for x in [abs(r) for r in rets] if x >= 0.05)
    # 외국인 현물 순매수와 코스피 등락 상관 (최근 60거래일)
    try:
        fl = conn.execute("SELECT d, foreign_v FROM dv_flow WHERE mkt='kospi' ORDER BY d").fetchall()
    except sqlite3.Error:
        fl = []
    fm = {_norm(d): v for d, v in fl if v is not None}
    pr = {ser[i][0]: rets[i - 1] for i in range(1, len(ser))}
    keys = sorted(set(fm) & set(pr))[-60:]
    if len(keys) >= 20:
        a, b = [fm[k] for k in keys], [pr[k] for k in keys]
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        sa = math.sqrt(sum((x - ma) ** 2 for x in a))
        sb = math.sqrt(sum((x - mb) ** 2 for x in b))
        out["corr"] = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa and sb else None
        out["corr_n"] = len(keys)
    return out
