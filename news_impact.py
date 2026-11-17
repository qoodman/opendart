# -*- coding: utf-8 -*-
"""
뉴스가 있던 날과 없던 날의 지수 움직임 비교 (이벤트 스터디의 가장 단순한 형태).

주제별로 '그 주제 기사가 나온 날'의 코스피·S&P500 일간 변화를 모아서, 기사가 없던 날과 비교해요.
  - 기사 시각 → 반영되는 거래일: 코스피는 한국 15:30 전이면 그날, 이후면 다음 거래일 / S&P500은 미국 장 마감(UTC 21:30) 전이면 그날, 이후면 다음 거래일.
  - 보는 값: 평균 |변화| (얼마나 크게 움직였나), 평균 변화(방향), 1% 이상 움직인 날 비율.
주의: ① 일봉이라 기사가 원인인지 이미 난 가격 변화를 전한 건지 가를 수 없어요 (역인과) ② 같은 날 여러 주제가 겹치고 ③ 기사를 쌓은 기간이 짧으면 우연이 커서
  표본(기사가 있던 날 수)이 MIN_DAYS보다 적으면 숫자를 내지 않아요. 인과가 아니라 '같이 움직였나'만 보는 참고 자료예요.
"""
import bisect
import datetime as dt

MIN_DAYS = 15
BIG = 1.0            # % — 크게 움직인 날 기준


def returns(points):
    """[(YYYY-MM-DD, 종가)] → {날짜: 전 거래일 대비 % 변화}"""
    out, prev = {}, None
    for d, v in points:
        if prev and prev > 0 and v:
            out[d] = (v / prev - 1) * 100
        prev = v
    return out


def _map_day(day, trading):
    i = bisect.bisect_left(trading, day.isoformat())
    return trading[i] if i < len(trading) else None


def event_days(news_rows, trading, market):
    """news_rows: [(pub UTC 'YYYY-MM-DD HH:MM', [주제...], 속보)] → {주제: {거래일}} (속보는 '속보·긴급' 주제로도 셈)"""
    out = {}
    for pub, topics, breaking in news_rows:
        t = dt.datetime.strptime(pub, "%Y-%m-%d %H:%M")
        if market == "KOSPI":
            k = t + dt.timedelta(hours=9)
            day = k.date() if (k.hour, k.minute) < (15, 30) else k.date() + dt.timedelta(days=1)
        else:
            day = t.date() if (t.hour, t.minute) < (21, 30) else t.date() + dt.timedelta(days=1)
        d = _map_day(day, trading)
        if not d:
            continue
        for tp in list(topics) + (["속보·긴급"] if breaking else []):
            out.setdefault(tp, set()).add(d)
    return out


def analyze(news_rows, points, market):
    """→ {"기간": (첫 날, 끝 날), "전체": stats, "주제": {주제: stats}} 또는 None. stats = dict(n, mean_abs, mean, big)"""
    rets = returns(points)
    trading = sorted(rets)
    if not news_rows or not trading:
        return None
    ev = event_days(news_rows, trading, market)
    all_days = sorted(d for d in {x for s in ev.values() for x in s})
    if not all_days:
        return None
    lo, hi = all_days[0], all_days[-1]
    window = [d for d in trading if lo <= d <= hi]

    def stats(days):
        v = [rets[d] for d in days if d in rets]
        if not v:
            return None
        return dict(n=len(v), mean_abs=sum(abs(x) for x in v) / len(v), mean=sum(v) / len(v), big=100.0 * sum(1 for x in v if abs(x) >= BIG) / len(v))

    res = {"기간": (lo, hi), "전체": stats(window), "주제": {}}
    for tp, days in ev.items():
        d = [x for x in days if lo <= x <= hi]
        rest = [x for x in window if x not in days]
        a, b = stats(d), stats(rest)
        res["주제"][tp] = dict(event=a, rest=b, enough=bool(a and a["n"] >= MIN_DAYS and b and b["n"] >= MIN_DAYS))
    return res
