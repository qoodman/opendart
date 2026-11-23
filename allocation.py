"""자산배분 비교: 유명한 배분 틀(60/40·올웨더형·영구 포트폴리오 등)을 같은 기간에 매월 비중 맞추기로 돌렸을 때의 성적.
가격(종가)만 쓰고 배당·이자는 빼서 채권·배당 자산이 불리하게 나온다. 과거 성적이 앞날을 보장하지 않는다."""
import math

import world_markets as wm

PORTFOLIOS = [
    ("미국 주식 100%", "가장 단순한 주식 몰빵", {"SPY": 1.0}),
    ("주식 60 · 채권 40", "가장 흔한 기본 배분", {"SPY": 0.6, "IEF.O": 0.4}),
    ("올웨더형", "경제 4국면(성장·침체·물가↑·물가↓)에 골고루", {"SPY": 0.30, "TLT.O": 0.40, "IEF.O": 0.15, "GLD": 0.075, "DBC": 0.075}),
    ("영구 포트폴리오", "주식·장기채·금·현금 25%씩", {"SPY": 0.25, "TLT.O": 0.25, "GLD": 0.25, "SHY.O": 0.25}),
    ("방어형 (금·달러·단기채)", "위기 때 버티기 쪽", {"GLD": 0.34, "UUP": 0.33, "SHY.O": 0.33}),
    ("주식 + 금 + 달러", "주식 하락 때 금·달러로 받치기", {"SPY": 0.5, "GLD": 0.25, "UUP": 0.25}),
]
EPISODES = [("2020 코로나", "2020-02-19", "2020-03-23"), ("2022 금리 급등", "2022-01-03", "2022-10-12")]


def _prices(conn, codes, krw):
    data = {}
    for c in codes:
        s = wm.asset_series(conn, c)
        if len(s) < 300:
            return None
        data[c] = dict(s)
    fx = dict(wm.fx_series(conn)) if krw else None
    dates = sorted(set.intersection(*[set(d) for d in data.values()]))
    if krw:
        if not fx:
            return None
        fd = sorted(fx)
        import bisect
        out_dates, conv = [], {}
        for d in dates:
            i = bisect.bisect_right(fd, d) - 1
            if i >= 0:
                conv[d] = fx[fd[i]]
                out_dates.append(d)
        dates = out_dates
        for c in data:
            data[c] = {d: data[c][d] * conv[d] for d in dates}
    return dates, data


def backtest(conn, weights, krw=False, start=None, end=None, min_len=120):
    """매월 첫 거래일에 비중을 다시 맞추는 일별 포트폴리오 가치 → [(날짜, 가치)] (시작 100)"""
    pr = _prices(conn, list(weights), krw)
    if not pr:
        return None
    dates, data = pr
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    if len(dates) < min_len:
        return None
    val = 100.0
    out = [(dates[0], val)]
    hold = {c: val * w / data[c][dates[0]] for c, w in weights.items()}
    for i in range(1, len(dates)):
        d = dates[i]
        val = sum(hold[c] * data[c][d] for c in weights)
        out.append((d, val))
        if d[:7] != dates[i - 1][:7]:
            hold = {c: val * w / data[c][d] for c, w in weights.items()}
    return out


def stats(curve):
    if not curve or len(curve) < 120:
        return None
    v = [x for _, x in curve]
    rets = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    yrs = len(rets) / 252
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    w12 = min((v[i] / v[i - 252] - 1 for i in range(252, len(v))), default=None)
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1 if yrs > 0 else None
    return dict(cagr=cagr, vol=sd, mdd=mdd, worst12=w12, total=v[-1] / v[0] - 1, start=curve[0][0], end=curve[-1][0], years=yrs,
                ratio=(cagr / sd) if sd and cagr is not None else None)


def compare(conn, krw=False):
    rows = []
    common_start = None
    curves = []
    for name, desc, w in PORTFOLIOS:
        c = backtest(conn, w, krw)
        curves.append((name, desc, w, c))
        if c:
            common_start = max(common_start or c[0][0], c[0][0])
    for name, desc, w, c in curves:
        if not c:
            continue
        c2 = [x for x in c if x[0] >= common_start]
        st = stats(c2)
        if not st:
            continue
        eps = {}
        for en, d0, d1 in EPISODES:
            cc = backtest(conn, w, krw, start=d0, end=d1, min_len=10)
            eps[en] = (cc[-1][1] / cc[0][1] - 1) if cc else None
        rows.append(dict(name=name, desc=desc, w=w, st=st, eps=eps, curve=c2))
    return dict(rows=rows, start=common_start) if rows else None
