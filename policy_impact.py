# -*- coding: utf-8 -*-
"""
법안·법령·정책이 주식시장에 영향을 줬는지 보는 자료. 앱이 모으는 정책 소식(policy_items: 국회 발의법률안·법제처 법령 개정·보도자료)을 쓴다.

- 날짜별 이벤트: 법안은 처리일(없으면 발의일), 법령은 공포일, 정책은 보도일. 주제(밸류업·공매도·세제·상법 등)와 단계(법안 통과·법안 발의·심사·법령 개정)로 표시.
- 반응: 그 이벤트가 있던 날과 없던 날의 코스피 움직임 비교 (news_impact와 같은 방식, 참고용. 인과가 아님)
- 신호등: 최근 7일 안에 주식에 직접 닿는 주제(밸류업·공매도·세제·상법·자본시장 제도)의 법안이 통과됐거나 새 소식이 있었는지
주의: 법안은 발의·심사 단계에서 이미 시장에 반영돼 있을 수 있고, 통과돼도 시행일은 한참 뒤인 경우가 많아요. 한 날에 여러 소식이 겹쳐 효과를 가르기 어려워요.
"""
import datetime as dt
import json
import sqlite3

import news_impact

STRONG = ("밸류업·주주환원", "공매도", "세제", "상법·지배구조", "자본시장 제도")
STAGE_PASS, STAGE_BILL, STAGE_LAW = "법안 통과", "법안 발의·심사", "법령 개정"
PASS_WORDS = ("원안가결", "수정가결", "가결")
DROP_WORDS = ("폐기", "철회", "부결")


def _topics(raw):
    try:
        v = json.loads(raw or "[]")
        return [x for x in v if isinstance(x, str)]
    except ValueError:
        return []


def items(conn, kinds=("법안", "법령")):
    """[{date, kind, title, status, topics, stage, url}] 날짜가 있는 항목 (기준선 포함: 과거 법안도 이벤트로 씀)"""
    try:
        rows = conn.execute("SELECT date, kind, title, status, topics, url FROM policy_items WHERE key!='_meta' AND date != '' "
                            f"AND kind IN ({','.join('?' * len(kinds))}) ORDER BY date", kinds).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for d, kind, title, status, tp, url in rows:
        st = status or ""
        if kind == "법안":
            stage = STAGE_PASS if any(w in st for w in PASS_WORDS) and not any(w in st for w in ("폐기", "철회", "부결")) else \
                None if any(w in st for w in DROP_WORDS) else STAGE_BILL
        else:
            stage = STAGE_LAW
        if stage:
            out.append(dict(date=d, kind=kind, title=title, status=st, topics=_topics(tp), stage=stage, url=url or ""))
    return out


def event_rows(its):
    """news_impact.analyze 입력: [(pub 'YYYY-MM-DD HH:MM'(UTC), [태그...], 속보)] — 정오(한국) 무렵으로 둬서 코스피는 그날 반영"""
    return [(f"{i['date']} 03:00", list(dict.fromkeys(i["topics"] + [i["stage"]])), False) for i in its]


def analyze(conn, kospi_points):
    its = items(conn)
    return news_impact.analyze(event_rows(its), kospi_points, "KOSPI") if its else None


def light(conn, today=None):
    """신호등 한 줄: (state, evidence). state: on/watch/off/na"""
    today = today or dt.date.today()
    its = items(conn, ("법안", "법령", "보도자료", "정책뉴스"))
    if not its:
        return "na", "정책·법안 자료가 아직 없어요 (지금 수집 → 시장·정책만)"
    cut = (today - dt.timedelta(days=7)).isoformat()
    recent = [i for i in its if i["date"] >= cut and set(i["topics"]) & set(STRONG)]
    passed = [i for i in recent if i["stage"] == STAGE_PASS]
    if not recent:
        return "off", "최근 7일 주식에 직접 닿는 법안·정책 소식 없음"
    def short(i):
        return f"{i['title'][:36]}"
    if passed:
        return "on", f"최근 7일 법안 통과 {len(passed)}건 · 관련 소식 {len(recent)}건 — " + " / ".join(short(i) for i in passed[:2])
    return "watch", f"최근 7일 관련 소식 {len(recent)}건 (통과는 없음) — " + " / ".join(short(i) for i in recent[:2])


def recent_list(conn, days=14, today=None, limit=10):
    today = today or dt.date.today()
    cut = (today - dt.timedelta(days=days)).isoformat()
    return [i for i in reversed(items(conn)) if i["date"] >= cut and set(i["topics"]) & set(STRONG)][:limit]
