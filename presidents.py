# -*- coding: utf-8 -*-
"""
정권(대통령 재임기)별 코스피 움직임. 앱이 받아 둔 코스피 일별 종가(idx:KOSPI)만으로 계산해요.

주의: 지수는 배당을 뺀 가격지수고, 같은 기간 세계 증시·금리·환율·반도체 경기 같은 것의 영향이 훨씬 커서
  '정권이 만든 결과'로 읽으면 안 돼요. 어느 시기를 어떻게 끊느냐에 따라 모양이 크게 달라지는 참고 자료예요.
재임 기간은 취임일 ~ 직무가 끝난 날(탄핵 파면은 결정일)이고, 권한대행 기간은 어느 쪽에도 넣지 않았어요.
"""
import datetime as dt

TERMS = [                        # (이름, 시작, 끝(None이면 지금까지), 비고)
    ("노무현", dt.date(2003, 2, 25), dt.date(2008, 2, 24), ""),
    ("이명박", dt.date(2008, 2, 25), dt.date(2013, 2, 24), ""),
    ("박근혜", dt.date(2013, 2, 25), dt.date(2017, 3, 10), "탄핵 파면"),
    ("문재인", dt.date(2017, 5, 10), dt.date(2022, 5, 9), ""),
    ("윤석열", dt.date(2022, 5, 10), dt.date(2025, 4, 4), "탄핵 파면"),
    ("이재명", dt.date(2025, 6, 4), None, "재임 중"),
]


def analyze(points, today=None, terms=TERMS):
    """points: [(YYYY-MM-DD, 종가)] 오래된 것부터 → [dict(name, note, start, end, years, first, last, ret, ann, mdd, best, partial)]
    시작 값은 취임 직전 마지막 종가(없으면 자료의 첫 값). 자료가 취임일보다 늦게 시작하면 partial=True"""
    today = today or dt.date.today()
    pts = [(dt.date.fromisoformat(d), v) for d, v in points if v]
    out = []
    if not pts:
        return out
    for name, start, end, note in terms:
        end_eff = min(end or today, today)
        inside = [(d, v) for d, v in pts if start <= d <= end_eff]
        if len(inside) < 2:
            continue
        before = [(d, v) for d, v in pts if d < start]
        base_d, base_v = before[-1] if before else inside[0]
        partial = not before or (inside[0][0] - start).days > 10
        path = [(base_d, base_v)] + inside if before else inside
        last_d, last_v = inside[-1]
        years = max((last_d - base_d).days / 365.25, 1e-9)
        ret = last_v / base_v - 1
        peak, mdd = path[0][1], 0.0
        for _, v in path:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        hi = max(path, key=lambda x: x[1])
        out.append(dict(name=name, note=note, start=base_d, end=last_d, years=years, first=base_v, last=last_v, ret=ret,
                        ann=(last_v / base_v) ** (1 / years) - 1 if years >= 0.5 else None, mdd=mdd, high=hi[1], high_d=hi[0], partial=partial))
    return out
