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
CATS = ["휴장", "만기", "지수", "실적·공시", "통화정책", "미국 지표", "정치", "유대", "내 일정"]


def _first_wed_on_or_after(d):
    return d + dt.timedelta(days=(2 - d.weekday()) % 7)


def _kr_election(term_end, days_before, kr_days):
    """공직선거법: 임기만료일 전 N일 이후 첫 수요일, 공휴일과 겹치면 다음 주 수요일
    (2012~2026 총선·지방선거 8번이 모두 이 규칙과 일치함을 확인)"""
    d = _first_wed_on_or_after(term_end - dt.timedelta(days=days_before))
    while d in kr_days:
        d += dt.timedelta(days=7)
    return d


def kr_elections(years):
    """{날짜: 이름} 국회의원 총선거(2028+4n년, 임기 5/29 만료)·전국동시지방선거(2026+4n년, 6/30 만료)"""
    base = holidays.country_holidays("KR", years=list(years) + [min(years) - 1, max(years) + 1], language="ko")
    plain = {d for d, n in base.items() if "선거" not in n}
    out = {}
    for y in years:
        if (y - 2024) % 4 == 0:
            out[_kr_election(dt.date(y, 5, 29), 50, plain)] = f"제{22 + (y - 2024) // 4}대 국회의원 총선거"
        if (y - 2026) % 4 == 0:
            out[_kr_election(dt.date(y, 6, 30), 30, plain)] = f"제{9 + (y - 2026) // 4}회 전국동시지방선거"
    return out


def us_election_day(y):
    """11월 첫째 월요일 다음 날(화요일)"""
    d = dt.date(y, 11, 1)
    d += dt.timedelta(days=(0 - d.weekday()) % 7)
    return d + dt.timedelta(days=1)


def kr_assembly_session(y, closed):
    """정기국회: 9/1 개회(공휴일이면 다음 날), 100일 이내 → 폐회"""
    d = dt.date(y, 9, 1)
    while d.weekday() >= 5 or d in closed:
        d += dt.timedelta(days=1)
    return d, d + dt.timedelta(days=99)


def elections_ahead(today=None, years=6):
    """앞으로 몇 년 안의 주요 선거 (달력 기간과 상관없이)"""
    today = today or dt.date.today()
    ys = range(today.year, today.year + years + 1)
    out = [dict(date=d, name=n, region="국내", note="선거일은 공휴일이라 국내 증시 휴장") for d, n in kr_elections(ys).items()]
    for y in ys:
        if y % 2 == 0:
            out.append(dict(date=us_election_day(y), region="미국",
                            name="미국 대통령 선거" if y % 4 == 0 else "미국 중간선거 (상·하원)",
                            note="한국 시간 수요일 오전부터 개표 결과"))
    out.append(dict(date=None, region="국내", name="제22대 대통령 선거 (2030년)", note="날짜가 확정되면 달력에서 직접 추가해 주세요"))
    return sorted([o for o in out if o["date"] is None or o["date"] >= today], key=lambda o: (o["date"] is None, o["date"] or today))


def _closed_sets(years):
    kr = holidays.country_holidays("KR", years=years, language="ko")
    krx = {d: n for d, n in kr.items()}
    for d, n in kr_elections(years).items():       # 앞으로의 선거일도 휴장 (라이브러리에 아직 없음)
        krx.setdefault(d, "선거일")
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
        # ---- 정치 ----
        if y % 2 == 0:
            ue = us_election_day(y)
            out.append(_ev(ue, "미국 대통령 선거" if y % 4 == 0 else "미국 중간선거 (상·하원 선거)", "정치", "미국",
                           "한국 시간 수요일 오전부터 개표 결과, 결과에 따라 정책 기대와 변동성이 달라질 수 있음", important=True))
        if y % 4 == 1 and y > 2000:
            out.append(_ev(dt.date(y, 1, 20), "미국 대통령 취임식", "정치", "미국", important=True))
        if y % 2 == 1:
            out.append(_ev(dt.date(y, 1, 3), "미국 새 의회 개원", "정치", "미국"))
        out.append(_ev(dt.date(y, 9, 30), "미국 회계연도 말 (연방 예산 시한)", "정치", "미국",
                       "예산·임시예산 미통과 시 연방정부 일부 업무정지(셧다운) 위험", important=True))
        op, cl = kr_assembly_session(y, krx)
        out.append(_ev(op, "정기국회 개회", "정치", "국내", "100일 이내, 예산안·세법 심사"))
        out.append(_ev(_biz(dt.date(y, 9, 3), krx, -1), "정부 예산안 국회 제출 시한", "정치", "국내", "국가재정법: 회계연도 개시 120일 전"))
        out.append(_ev(dt.date(y, 12, 2), "예산안 법정 처리 시한", "정치", "국내",
                       "헌법상 시한이지만 넘겨 처리되는 해도 많음", important=True))
        out.append(_ev(cl, "정기국회 폐회 (100일)", "정치", "국내", "세법 개정안 통과 여부 확인"))
        for d, n in kr_elections([y]).items():
            out.append(_ev(d, n, "정치", "국내", "선거일은 공휴일 → 국내 증시 휴장", important=True))
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
