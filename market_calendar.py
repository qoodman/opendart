# -*- coding: utf-8 -*-
"""
금융 달력: 주식시장에 영향을 주는 일정

- 규칙으로 정확히 계산: 국내·미국 휴장일(holidays), 국내 옵션 만기(둘째 목)·동시만기(3·6·9·12월),
  코스피200 정기변경(6·12월 동시만기 다음 영업일), 미국 옵션 만기(셋째 금)·쿼드러플 위칭,
  분기·반기·사업보고서 제출 마감
- 공식 발표 일정: FOMC (연준이 미리 공표한 해만 넣음)
- 대략적인 일정('예상' 표시): MSCI 분기 리뷰 발표, 잠정실적 시즌, 미국 고용보고서
- 유대 명절 (jewish_calendar)
- 직접 추가한 일정 (금통위·CPI 등): cal_events 표
"""
import datetime as dt
import sqlite3

import holidays

# 연준이 공표한 FOMC 회의 (둘째 날 오후 2시 ET 결정 → 한국 시간 다음 날 새벽 3~4시 발표)
FOMC = {
    2026: [(1, 27, 28), (3, 17, 18), (4, 28, 29), (6, 16, 17), (7, 28, 29), (9, 15, 16), (10, 27, 28), (12, 8, 9)],
}
US_NAMES = {"New Year's Day": "새해", "Martin Luther King Jr. Day": "마틴 루서 킹 데이",
            "Washington's Birthday": "대통령의 날", "Good Friday": "성금요일", "Memorial Day": "현충일",
            "Juneteenth National Independence Day": "준틴스", "Independence Day": "독립기념일",
            "Labor Day": "노동절", "Thanksgiving Day": "추수감사절", "Christmas Day": "성탄절"}
CATS = ["휴장", "만기", "지수", "실적·공시", "통화정책", "미국 지표", "유대", "내 일정"]


def _closed_sets(years):
    kr = holidays.country_holidays("KR", years=years, language="ko")
    krx = {d: n for d, n in kr.items()}
    for y in years:
        krx.setdefault(dt.date(y, 5, 1), "근로자의 날")
        krx.setdefault(dt.date(y, 12, 31), "연말 휴장")
    nyse = dict(holidays.financial_holidays("NYSE", years=years))
    return krx, nyse


def _biz(d, closed, step=-1):
    """영업일로 맞춤 (휴장·주말이면 step 방향으로 옮김)"""
    while d.weekday() >= 5 or d in closed:
        d += dt.timedelta(days=step)
    return d


def _nth_weekday(y, m, weekday, n):
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_biz(y, m, closed):
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    return _biz(d, closed, -1)


def _ev(date, title, cat, region, note="", approx=False, important=False, uid=None):
    return dict(date=date, title=title, cat=cat, region=region, note=note, approx=approx,
                important=important, uid=uid)


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cal_events (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, "
                 "title TEXT, note TEXT, important INTEGER DEFAULT 1)")
    conn.commit()


def user_events(conn, start, end):
    try:
        rows = conn.execute("SELECT id, date, title, note, important FROM cal_events WHERE date BETWEEN ? AND ?",
                            (start.isoformat(), end.isoformat())).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_ev(dt.date.fromisoformat(d), t, "내 일정", "", n or "", important=bool(imp), uid=i)
            for i, d, t, n, imp in rows]


def events_between(start, end, conn=None):
    years = list(range(start.year - 1, end.year + 2))
    krx, nyse = _closed_sets(years)
    out = []
    for d, n in krx.items():
        if d.weekday() < 5:
            out.append(_ev(d, f"국내 증시 휴장 · {n}", "휴장", "국내", important=True))
    for d, n in nyse.items():
        out.append(_ev(d, f"미국 증시 휴장 · {US_NAMES.get(n, n)}", "휴장", "미국"))

    for y in years:
        for m in range(1, 13):
            quarter_end = m in (3, 6, 9, 12)
            # 국내 옵션 만기: 둘째 목요일 (휴장이면 앞 영업일)
            kexp = _biz(_nth_weekday(y, m, 3, 2), krx, -1)
            if quarter_end:
                out.append(_ev(kexp, "선물·옵션 동시만기 (네 마녀의 날)", "만기", "국내",
                               "만기 물량 정리로 장 막판 변동성이 커질 수 있음", important=True))
            else:
                out.append(_ev(kexp, "옵션 만기", "만기", "국내"))
            if m in (6, 12):
                out.append(_ev(_biz(kexp + dt.timedelta(days=1), krx, 1), "코스피200·코스닥150 정기변경 반영",
                               "지수", "국내", "편입·편출 종목 수급 변화", important=True))
            # 미국 옵션 만기: 셋째 금요일
            uexp = _biz(_nth_weekday(y, m, 4, 3), nyse, -1)
            if quarter_end:
                out.append(_ev(uexp, "미국 쿼드러플 위칭 (분기 만기)", "만기", "미국",
                               "한국 시간 토요일 새벽 마감", important=True))
            else:
                out.append(_ev(uexp, "미국 옵션 만기", "만기", "미국"))
            # MSCI 분기 리뷰: 2·5·8·11월
            if m in (2, 5, 8, 11):
                out.append(_ev(_nth_weekday(y, m, 1, 2), "MSCI 분기 리뷰 발표", "지수", "글로벌",
                               "한국 시간 다음 날 아침. 정확한 날은 MSCI 공지 확인", approx=True))
                out.append(_ev(_last_biz(y, m, nyse), "MSCI 리밸런싱 반영 (장 마감)", "지수", "글로벌",
                               "편입·편출 종목 외국인 수급 변화", important=True))
            # 잠정실적 시즌 시작: 1·4·7·10월 첫째 주 후반 (삼성전자·LG전자 잠정실적)
            if m in (1, 4, 7, 10):
                out.append(_ev(_biz(dt.date(y, m, 7), krx, 1), "분기 잠정실적 시즌 시작", "실적·공시",
                               "국내", "삼성전자·LG전자 잠정실적이 보통 이 무렵", approx=True))
            # 미국 고용보고서: 보통 첫째 금요일
            jobs = _nth_weekday(y, m, 4, 1)
            if jobs in nyse:
                jobs += dt.timedelta(days=7)        # 첫째 금요일이 휴장이면 보통 다음 주로
            out.append(_ev(jobs, "미국 고용보고서", "미국 지표", "미국",
                           "보통 첫째 금요일 · 한국 시간 밤 10시 30분 (미국 서머타임 기간엔 9시 30분)", approx=True))
        # 보고서 제출 마감 (마감일이 휴일이면 다음 영업일)
        for (m, d), name in {(3, 31): "사업보고서 제출 마감", (5, 15): "1분기보고서 제출 마감",
                             (8, 14): "반기보고서 제출 마감", (11, 14): "3분기보고서 제출 마감"}.items():
            out.append(_ev(_biz(dt.date(y, m, d), krx, 1), name, "실적·공시", "국내",
                           "이 날까지 실적 공시가 몰림"))
        for m, d1, d2 in FOMC.get(y, []):
            out.append(_ev(dt.date(y, m, d2), "FOMC 금리 결정", "통화정책", "미국",
                           f"{m}월 {d1}~{d2}일 회의, 한국 시간 다음 날 새벽 3~4시 발표", important=True))
    try:
        import jewish_calendar as jc
        for h in jc.holidays_between(start, end):
            out.append(_ev(h["start"], f"{h['name']} ({h['desc']})", "유대", "",
                           h["note"] or (f"{h['days']}일간" if h["days"] > 1 else ""),
                           important=h["name"] in ("로쉬 하샤나", "욤 키푸르")))
    except ImportError:
        pass
    if conn is not None:
        out += user_events(conn, start, end)
    out = [e for e in out if start <= e["date"] <= end]
    return sorted(out, key=lambda e: (e["date"], not e["important"], e["cat"]))
