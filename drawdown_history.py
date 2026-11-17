# -*- coding: utf-8 -*-
"""
과거에 코스피가 비슷하게 빠진 뒤 어떻게 됐나.

'고점 대비 낙폭'은 직전 250거래일 최고 종가 대비 변화예요. 낙폭이 기준(-10/-15/-20/-25/-30%)을 처음 넘어선 날(회복했다가 다시 빠진 경우만 새 사례,
최소 60거래일 간격)을 사례로 모아, 그 뒤 1·3·6·12개월(20·60·120·250거래일) 수익률과 이후 6개월 안 추가 하락을 봐요.
주의: 독립된 사례가 몇 개 안 되고(보통 5~10개), 사례마다 원인(금융위기·코로나·금리 충격 등)이 달라서 '평균적으로 이렇게 된다'고 믿을 근거가 못 돼요.
  과거 기록일 뿐이고 앞으로를 보장하지 않으며 사고팔 시점을 알려주지 않아요.
"""
LEVELS = (10, 15, 20, 25, 30)
HORIZONS = ((20, "1개월"), (60, "3개월"), (120, "6개월"), (250, "12개월"))
LOOKBACK = 250
GAP = 60


def dd_series(closes, lookback=LOOKBACK):
    out = []
    for i, v in enumerate(closes):
        hi = max(closes[max(0, i - lookback + 1): i + 1])
        out.append(v / hi - 1 if hi else 0.0)
    return out


def episodes(points, level, gap=GAP):
    """points [(date, close)] → 낙폭이 -level%를 처음 넘어선 날의 사례 [{date, dd, fwd:{h: %}, more: 이후 6개월 추가 하락 %(없으면 None)}]"""
    closes = [v for _, v in points]
    dd = dd_series(closes)
    out, last = [], -10**9
    for i in range(1, len(points)):
        if dd[i] <= -level / 100 and dd[i - 1] > -level / 100 and i - last >= gap and i >= LOOKBACK // 2:
            last = i
            fwd = {h: (closes[i + h] / closes[i] - 1) * 100 for h, _ in HORIZONS if i + h < len(closes)}
            nxt = closes[i + 1: i + 121]
            more = (min(nxt) / closes[i] - 1) * 100 if len(nxt) >= 20 else None
            out.append(dict(date=points[i][0], dd=dd[i] * 100, fwd=fwd, more=more))
    return out


def pick_level(current_dd):
    """현재 낙폭(%, 음수)에 맞는 기준: 이미 넘어선 가장 깊은 기준. -10%보다 얕으면 10"""
    cur = abs(current_dd)
    ok = [lv for lv in LEVELS if cur >= lv]
    return max(ok) if ok else LEVELS[0]


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def summarize(eps):
    """horizon별 {n, median, up(오른 사례 비율 %), worst} + 추가 하락 {median, worst}"""
    out = {}
    for h, label in HORIZONS:
        v = [e["fwd"][h] for e in eps if h in e["fwd"]]
        out[h] = dict(label=label, n=len(v), median=_median(v), up=(100.0 * sum(1 for x in v if x > 0) / len(v)) if v else None, worst=min(v) if v else None)
    m = [e["more"] for e in eps if e["more"] is not None]
    out["more"] = dict(n=len(m), median=_median(m), worst=min(m) if m else None)
    return out


def analyze(points):
    """→ {current: 낙폭 정보, level, episodes, summary} 또는 None (기록이 짧을 때)"""
    pts = [(d, v) for d, v in points if v]
    if len(pts) < LOOKBACK + 60:
        return None
    dd = dd_series([v for _, v in pts])
    level = pick_level(dd[-1] * 100)
    eps = episodes(pts, level)
    return dict(current=dd[-1] * 100, level=level, episodes=eps, summary=summarize(eps), first=pts[0][0], last=pts[-1][0])
