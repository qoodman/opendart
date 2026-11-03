# -*- coding: utf-8 -*-
"""
유대인 달력 (히브리력 명절)과 월가 격언 검증

- 명절 날짜는 히브리력을 양력으로 바꿔 계산합니다 (convertdate). 해외(디아스포라) 기준 기간.
- 월가 격언 "로쉬 하샤나에 팔고 욤 키푸르에 사라(Sell Rosh Hashanah, buy Yom Kippur)"를
  코스피·코스닥 일별 종가로 해마다 확인합니다. 투자 판단을 대신하지 않는 참고 자료입니다.
"""
import datetime as dt

from convertdate import hebrew

MONTHS = {1: "니산", 2: "이야르", 3: "시완", 4: "탐무즈", 5: "아브", 6: "엘룰", 7: "티쉬리", 8: "헤쉬반",
          9: "키슬레브", 10: "테베트", 11: "쉬바트", 12: "아다르", 13: "아다르 II"}

# (이름, 설명, 히브리 월, 일, 기간(일)) — 월 12는 윤년이면 아다르 II(13)로 바꿈 (푸림)
HOLIDAYS = [
    ("로쉬 하샤나", "유대 새해", 7, 1, 2),
    ("욤 키푸르", "속죄일, 가장 큰 명절", 7, 10, 1),
    ("수코트", "초막절", 7, 15, 7),
    ("심핫 토라", "율법 기쁨의 날", 7, 23, 1),
    ("하누카", "빛의 축제", 9, 25, 8),
    ("푸림", "부림절", 12, 14, 1),
    ("유월절", "페사흐", 1, 15, 8),
    ("샤부옷", "칠칠절", 3, 6, 2),
    ("티샤 베아브", "성전 파괴 추모일", 5, 9, 1),
]
NOTES = {
    "로쉬 하샤나": "월가 격언: 로쉬 하샤나에 팔고",
    "욤 키푸르": "… 욤 키푸르에 사라 (Sell Rosh Hashanah, buy Yom Kippur)",
}


def _g(y, m, d):
    return dt.date(*hebrew.to_gregorian(y, m, d))


def holidays_of_hebrew_year(hy):
    """히브리력 hy년의 명절 (티쉬리부터 엘룰까지, 니산 이후는 같은 해 번호)"""
    out = []
    for name, desc, m, d, days in HOLIDAYS:
        mm = 13 if (m == 12 and hebrew.leap(hy)) else m
        start = _g(hy, mm, d)
        out.append(dict(name=name, desc=desc, start=start, end=start + dt.timedelta(days=days - 1),
                        days=days, hebrew=f"{MONTHS[mm]} {d}일", note=NOTES.get(name, "")))
    return out


def holidays_between(start, end):
    hy0 = hebrew.from_gregorian(start.year, start.month, start.day)[0] - 1
    hy1 = hebrew.from_gregorian(end.year, end.month, end.day)[0] + 1
    out = [h for hy in range(hy0, hy1 + 1) for h in holidays_of_hebrew_year(hy)]
    return sorted((h for h in out if h["end"] >= start and h["start"] <= end), key=lambda h: h["start"])


def today_hebrew(today=None):
    t = today or dt.date.today()
    y, m, d = hebrew.from_gregorian(t.year, t.month, t.day)
    return f"히브리력 {y}년 {MONTHS[m]} {d}일"


def _close_on_or_before(series, day):
    """[(YYYY-MM-DD, 값)] 날짜순 → day(포함) 이전 마지막 종가"""
    key = day.isoformat()
    best = None
    for p, v in series:
        if p <= key:
            best = (p, v)
        else:
            break
    return best


def rosh_to_yom(series_by_name, years_back=25):
    """해마다 로쉬 하샤나 전날 종가 → 욤 키푸르 전날 종가 변화율 (격언대로면 이 기간에 내림)
    series_by_name: {'코스피': [(날짜, 종가)…], …}"""
    rows = []
    this_hy = hebrew.from_gregorian(*dt.date.today().timetuple()[:3])[0]
    for hy in range(this_hy - years_back, this_hy + 1):
        rh, yk = _g(hy, 7, 1), _g(hy, 7, 10)
        if yk > dt.date.today():
            continue
        row = dict(year=rh.year, rh=rh, yk=yk, chg={})
        for name, s in series_by_name.items():
            a = _close_on_or_before(s, rh - dt.timedelta(days=1))
            b = _close_on_or_before(s, yk - dt.timedelta(days=1))
            if a and b and a[0] >= (rh - dt.timedelta(days=10)).isoformat() and a[0] != b[0]:
                row["chg"][name] = b[1] / a[1] - 1
        if row["chg"]:
            rows.append(row)
    return rows


def baseline(series, days=9):
    """비교용: 아무 날이나 같은 길이(달력 기준 days일) 동안의 평균 변화율"""
    if len(series) < 50:
        return None
    dates = [dt.date.fromisoformat(p) for p, _ in series]
    vals = [v for _, v in series]
    out, j = [], 0
    for i, d0 in enumerate(dates):
        target = d0 + dt.timedelta(days=days)
        while j < len(dates) and dates[j] <= target:
            j += 1
        if j - 1 > i and dates[j - 1] >= target - dt.timedelta(days=3):
            out.append(vals[j - 1] / vals[i] - 1)
    return sum(out) / len(out) if out else None
