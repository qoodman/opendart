# -*- coding: utf-8 -*-
"""
지분분쟁(경영권 분쟁) 레이더: 분쟁 가능성이 있는 종목, 막 시작한 종목, 치열하게 싸우는 종목을 DART 공시로 찾아 보여 줘요.

어떻게 찾나 (DART OpenAPI 공시 목록을 회사 구분 없이 훑어요):
  분쟁이 '벌어지고 있다'는 신호 — 공시 제목으로 잡아요
    · 주주측 위임장 권유 (회사가 아닌 쪽이 의결권을 모음 = 표 대결)   · 공개매수
    · 경영권 분쟁 소송, 가처분(신주발행금지·의결권행사금지·주총금지 등)   · 주주제안, 소수주주권 행사, 임시주총 소집 청구·허가
    · 최대주주 변경   · 대량보유상황보고서(일반) = 경영권에 영향을 주려는 목적의 5% 보유 (행동주의 펀드 포함)
    · (보조) 자기주식 취득·처분, 제3자 배정 유상증자, 횡령·배임, 풍문 해명 — 분쟁 중일 때 방어·공방 수단으로 자주 나와서 다른 신호와 같이 있을 때만 점수에 반영
  분쟁 '가능성'이 있다는 신호 — 구조로 봐요 (분쟁 공시가 없는 종목도 포함)
    · 최대주주 측 지분과 가장 큰 외부 5% 보유자의 지분 격차(작을수록 위험)   · 경영참여 목적 5% 보유자, 행동주의 펀드 존재
    · 외부 보유자의 최근 지분 확대   · 최대주주 지분율이 낮음

단계: 치열 > 시작 > 징후 > 가능성. 점수는 제가 정한 단순 기준(최근 공시일수록 크게)이고, 공시 제목으로 읽기 때문에 놓치거나 과하게 잡을 수 있어요.
공시는 사실이지만 '누가 이길지·주가가 어떻게 될지'는 알려 주지 않아요. 투자 추천이 아니에요.
"""
import datetime as dt
import json
import re
import sqlite3
import time

import requests

DART_BASE = "https://opendart.fss.or.kr/api"
HOT_KINDS = {"litig", "injunction", "tender", "proxy_ext", "proposal", "owner_chg"}
KIND_LABEL = {
    "litig": "경영권 분쟁 소송", "injunction": "가처분", "tender": "공개매수", "proxy_ext": "주주측 위임장 권유(표 대결)", "proxy_co": "회사 위임장 권유",
    "proposal": "주주제안·소수주주권·임시주총 청구", "owner_chg": "최대주주 변경", "blk_gen": "경영참여 목적 5% 보유(대량보유 일반)", "owner_buy": "최대주주 측 지분 변동",
    "defense": "자기주식·제3자 배정(방어·우호지분)", "clarify": "풍문·보도 해명", "legal": "횡령·배임", "egm": "임시주주총회 소집", "agm": "주주총회 소집공고",
}
KIND_ICON = {"litig": "⚖", "injunction": "⚖", "tender": "📢", "proxy_ext": "🗳", "proxy_co": "🗳", "proposal": "📝", "owner_chg": "👑", "blk_gen": "🎯", "owner_buy": "📈",
             "defense": "🛡", "clarify": "❓", "legal": "🚨", "egm": "📅", "agm": "📅"}
# 행동주의 성향으로 알려진 투자자·펀드 이름의 일부 (제출인 이름에 들어 있으면 표시). 직접 고칠 수 있게 상수로 둠
ACTIVISTS = ["얼라인", "KCGI", "강성부", "플래시라이트", "차파트너스", "트러스톤", "라이프", "머스트", "FCP", "팰리서", "엘리엇", "ELLIOTT", "행동주의", "주주가치", "미리캐피탈", "Miri Capital", "Miri Strategic", "스팍스", "SPARX", "Starboard", "Third Point", "Cevian", "Oasis Management",
             "안다", "밸류파트너스", "서울마린", "파인밸류", "VIP", "에이치플러스", "아이온", "비케이", "KB자산"]
ACTIVIST_STRICT = ["얼라인", "KCGI", "강성부", "플래시라이트", "차파트너스", "트러스톤", "팰리서", "엘리엇", "ELLIOTT", "행동주의", "주주가치", "머스트", "FCP", "미리캐피탈", "MIRI CAPITAL", "MIRI STRATEGIC", "THE MIRI", "스팍스", "SPARX", "STARBOARD", "THIRD POINT", "CEVIAN", "OASIS MANAGEMENT"]


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS dispute_event (rcept_no TEXT PRIMARY KEY, corp_code TEXT, corp_name TEXT, stock_code TEXT, rcept_dt TEXT, report_nm TEXT, flr_nm TEXT, kind TEXT, weight REAL, activist INTEGER);
    CREATE INDEX IF NOT EXISTS dispute_event_code ON dispute_event(stock_code, rcept_dt);
    CREATE TABLE IF NOT EXISTS dispute_state (code TEXT PRIMARY KEY, name TEXT, level TEXT, score REAL, payload TEXT, updated TEXT, prev_level TEXT, alerted TEXT);
    CREATE TABLE IF NOT EXISTS dispute_holder (code TEXT, reporter TEXT, pct REAL, tp TEXT, date TEXT, resn TEXT, PRIMARY KEY (code, reporter));
    CREATE TABLE IF NOT EXISTS dispute_meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS dispute_tag (code TEXT, name TEXT, role TEXT, updated TEXT, PRIMARY KEY (code, name));
    CREATE TABLE IF NOT EXISTS dispute_struct (code TEXT PRIMARY KEY, fetched TEXT, hp REAL, ext TEXT, insiders TEXT);
    """)
    for stmt in ("ALTER TABLE dispute_struct ADD COLUMN ally REAL", "ALTER TABLE dispute_tag ADD COLUMN pct REAL", "ALTER TABLE dispute_struct ADD COLUMN meta TEXT"):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass


# ---------------- 분류 ----------------
def _is_activist(name, strict=False):
    return any(a.lower() in (name or "").lower() for a in (ACTIVIST_STRICT if strict else ACTIVISTS))


def norm_name(t):
    """회사·사람 이름 비교용: 주식회사/㈜/(주)/유한회사, 공백·괄호·점 제거, 소문자"""
    t = re.sub(r"주식회사|유한회사|유한책임회사|㈜|\(주\)|\(유\)", "", t or "")
    return re.sub(r"[\s\(\)\[\]·\.,\-_&]", "", t).lower()


GROUP_PREFIX = ["삼성", "현대", "기아", "SK", "LG", "롯데", "한화", "GS", "포스코", "POSCO", "두산", "신세계", "CJ", "한진", "금호", "효성", "대림", "코오롱", "동원", "농심", "오뚜기",
                "영풍", "고려아연", "LS", "HD현대", "DB", "카카오", "네이버", "NAVER", "아모레", "태평양", "셀트리온", "KT", "하이브", "넥슨", "엔씨", "넷마블", "BGF", "HDC", "한솔", "세아", "OCI",
                "한국타이어", "금양", "KCC", "삼양", "하림", "교보", "미래에셋", "대신", "한라", "동국", "태광", "부영", "이랜드", "종근당", "유한", "대웅", "동아", "녹십자", "일동", "한미", "오리온", "CJ"]


def is_group_affiliate(name, corp_name, insiders=()):
    """같은 그룹(재벌) 계열사인지 어림: 보고자 이름이 대기업 그룹 이름으로 시작하고 그 이름을 대상 회사나 최대주주 측 명단도 쓰거나,
    대상 회사 이름과 앞 3글자 이상 같을 때. 사업보고서 명단에 없는 계열사(자산운용·보험·물산 등)를 거르려는 용도"""
    n, c = norm_name(name), norm_name(corp_name)
    if not n or not c:
        return False
    if len(c) >= 3 and n[:3] == c[:3] and not n.startswith(c[:1] * 2):
        return True
    for g in GROUP_PREFIX:
        gn = norm_name(g)
        if n.startswith(gn):
            if c.startswith(gn) or any(norm_name(i).startswith(gn) for i in insiders):
                return True
    return False


def is_insider(name, insiders):
    """최대주주 측(최대주주·특수관계인·계열회사)인지. 이름 표기가 달라도(㈜한진칼 / 주식회사 한진칼) 같은 곳으로 봄.
    insiders: 최대주주 및 특수관계인 이름 집합(정규화 전)"""
    n = norm_name(name)
    if not n:
        return False
    for i in insiders:
        m = norm_name(i)
        if not m:
            continue
        if n == m or (len(m) >= 3 and (m in n or n in m) and min(len(m), len(n)) >= 3):
            return True
    return False


def _same_company(flr, corp):
    n = lambda t: re.sub(r"[\s\(\)㈜]|주식회사", "", t or "")
    return bool(n(flr)) and (n(flr) == n(corp) or n(corp) in n(flr) or n(flr) in n(corp))


def classify(report_nm, flr_nm="", corp_name="", ptype=""):
    """공시 제목 → (kind, weight, activist(0/1)) 또는 None"""
    t = re.sub(r"\s+", "", report_nm or "")
    if re.search(r"경영권분쟁", t):
        return "litig", 4.0, 0
    if "가처분" in t:
        # 대기업은 특허·노동·거래 소송 가처분도 많아서, 경영권·주주총회·신주 발행처럼 지배구조와 관련된 말이 있을 때만 분쟁 신호로 봄
        if re.search(r"경영권|의결권|주주총회|주총|신주|이사|해임|선임|주주명부|대표이사|주식처분|전환사채|신주인수권|유상증자|제3자배정", t):
            return "injunction", 3.0, 0
        return None
    if "공개매수" in t:
        if _same_company(flr_nm, corp_name):          # 회사가 자기 주식을 사들이는 공개매수는 분쟁이 아님 (주주환원)
            return "defense", 1.0, 0
        return "tender", 4.0, 0
    if "의결권대리행사권유" in t or "위임장권유" in t:        # 조회 유형 코드(D003)는 믿지 않고 공시 제목으로만 판단 — 코드로 보면 임원·주요주주 소유상황보고서까지 위임장 권유로 잡혔음
        return ("proxy_co", 1.0, 0) if _same_company(flr_nm, corp_name) else ("proxy_ext", 4.0, 1 if _is_activist(flr_nm) else 0)
    if re.search(r"주주제안|소수주주권|주주총회소집(허가|청구)|임시주주총회소집(허가|청구)", t):
        return "proposal", 3.0, 0
    if "최대주주변경" in t:
        return "owner_chg", 3.0, 0
    if re.search(r"대량보유상황보고서\(일반\)", t):
        act = 1 if _is_activist(flr_nm) else 0
        return "blk_gen", 3.0 if act else 2.0, act
    if re.search(r"최대주주등소유주식변동신고서|최대주주.*(시간외|장외)", t):
        return "owner_buy", 1.0, 0
    if re.search(r"자기주식(취득|처분)결정|제3자배정", t):
        return "defense", 1.0, 0
    if re.search(r"횡령|배임", t):
        return "legal", 1.0, 0
    if re.search(r"풍문|보도에대한(해명|조회)|조회공시", t):
        return "clarify", 1.0, 0
    if re.search(r"주주총회소집(공고|결의)|임시주주총회", t):
        return ("egm", 1.0, 0) if "임시" in t else ("agm", 0.0, 0)          # 정기 소집공고는 점수 없이 주총 날짜 확인용으로만 저장
    return None


# ---------------- DART 훑기 ----------------
class DisputeError(Exception):
    pass


def _list(key, bgn, end, session=None, ty=None, detail=None, page=1):
    s = session or requests
    params = dict(crtfc_key=key, bgn_de=bgn, end_de=end, page_no=str(page), page_count="100")
    if ty:
        params["pblntf_ty"] = ty
    if detail:
        params["pblntf_detail_ty"] = detail
    r = s.get(f"{DART_BASE}/list.json", params=params, timeout=40)
    if r.status_code != 200:
        raise DisputeError(f"DART HTTP {r.status_code}")
    try:
        j = r.json()
    except ValueError:
        raise DisputeError("DART 응답을 읽지 못함")
    st = str(j.get("status", ""))
    if st == "020":
        raise DisputeError("DART 하루 호출 한도를 넘었어요")
    if st == "013":
        return [], 0
    if st != "000":
        raise DisputeError(f"DART {st} {j.get('message', '')}".strip())
    return j.get("list") or [], int(j.get("total_page") or 1)


QUERIES = [("D", "D001"), ("D", "D003"), ("D", "D004"), ("B", None), ("I", "I001")]


def scan(conn, key, bgn, end, log=print, session=None, sleep=0.12, progress=None):
    """bgn~end(YYYYMMDD) 공시를 훑어 분쟁 신호를 dispute_event에 저장 → 새로 저장한 건수. 90일 넘는 구간은 나눠서 불러요."""
    ensure(conn)
    s = dt.datetime.strptime(bgn, "%Y%m%d")
    e = dt.datetime.strptime(end, "%Y%m%d")
    new = 0
    seen = 0
    wins = []
    while s <= e:
        w_end = min(s + dt.timedelta(days=89), e)
        wins.append((s, w_end))
        s = w_end + dt.timedelta(days=1)
    step = 0
    total = len(wins) * len(QUERIES)
    for ws, we in wins:
        for ty, detail in QUERIES:
            step += 1
            if progress:
                progress(f"{step}/{total} 구간 · 새 신호 {new}건")
            page = 1
            while True:
                rows, pages = _list(key, ws.strftime("%Y%m%d"), we.strftime("%Y%m%d"), session, ty, detail, page)
                for x in rows:
                    seen += 1
                    if not x.get("stock_code"):
                        continue                                                    # 상장사만
                    c = classify(x.get("report_nm"), x.get("flr_nm"), x.get("corp_name"), detail or "")
                    if not c:
                        continue
                    kind, w, act = c
                    d = x.get("rcept_dt") or ""
                    d = f"{d[:4]}-{d[4:6]}-{d[6:]}" if re.fullmatch(r"\d{8}", d) else d
                    cur = conn.execute("INSERT OR IGNORE INTO dispute_event VALUES (?,?,?,?,?,?,?,?,?,?)",
                                       (x["rcept_no"], x.get("corp_code"), x.get("corp_name"), x["stock_code"], d, x.get("report_nm"), x.get("flr_nm"), kind, w, act))
                    new += cur.rowcount
                if page >= pages:
                    break
                page += 1
                time.sleep(sleep)
            conn.commit()
            time.sleep(sleep)
    log(f"[지분분쟁] 공시 {seen:,}건 훑음 · 새 신호 {new}건")
    return new


# ---------------- 단계 판정 ----------------
def _decay(days):
    return 1.0 if days <= 30 else 0.7 if days <= 60 else 0.4 if days <= 90 else 0.15


def assess_events(evs, today=None):
    """한 종목의 최근 신호들 → dict(level, score, kinds, hot, recent30, activist, last) 또는 None.
    evs: [dict(date, kind, weight, activist)]"""
    today = today or dt.date.today()
    rows = []
    for e in evs:
        try:
            days = (today - dt.date.fromisoformat(e["date"])).days
        except (ValueError, TypeError):
            continue
        if days < 0 or days > 180:
            continue
        rows.append((days, e))
    if not rows:
        return None
    score = sum(e["weight"] * _decay(d) for d, e in rows)
    kinds = {e["kind"] for _, e in rows}
    hot = kinds & HOT_KINDS
    recent30 = sum(1 for d, _ in rows if d <= 30)
    recent_hot = {e["kind"] for d, e in rows if d <= 90 and e["kind"] in HOT_KINDS}
    act = any(e.get("activist") for _, e in rows)
    amplifiers = bool(kinds & {"defense", "clarify", "legal", "owner_buy"})
    level = None
    if (len(recent_hot) >= 3 or (score >= 9 and recent30 >= 2) or ("proxy_ext" in recent_hot and recent_hot & {"injunction", "litig"})
            or ("tender" in recent_hot and recent_hot & {"proxy_ext", "injunction", "litig"}) or ("owner_chg" in recent_hot and amplifiers and score >= 6)):
        level = "치열"
    elif recent_hot and score >= 2.5:
        level = "시작"
    elif (act or "blk_gen" in kinds) and score >= 2:
        level = "징후"
    if level is None:
        return None
    last = max(e["date"] for _, e in rows)
    return dict(level=level, score=round(score, 1), kinds=sorted(kinds), hot=sorted(hot), recent30=recent30, activist=act, last=last)


# ---------------- 구조(가능성) ----------------
INSTITUTIONS = ["국민연금", "산업은행", "한국산업은행", "한국투자공사", "예금보험공사", "신용보증기금", "기술보증기금", "우정사업본부"]
NEUTRAL_EXTRA = ["공제회", "연금", "정책금융", "수출입은행", "진흥공단", "예금보험", "자산관리공사", "주택금융공사"]
# 지분은 커도 경영권을 다투지 않고 표만 행사하는 외국계 대형 운용사·국부펀드 (행동주의로 알려진 이름은 제외)
FOREIGN_PASSIVE = ["블랙록", "BLACKROCK", "뱅가드", "VANGUARD", "노르게스", "NORGES", "노르웨이", "스테이트스트리트", "STATE STREET", "캐피탈그룹", "CAPITAL GROUP", "CAPITAL RESEARCH",
                   "CAPITAL INTERNATIONAL", "템플턴", "TEMPLETON", "프랭클린", "FRANKLIN", "피델리티", "FIDELITY", "티로웨", "T. ROWE", "T.ROWE", "JP모간", "JPMORGAN", "J.P. MORGAN",
                   "다이멘셔널", "DIMENSIONAL", "슈로더", "SCHRODER", "아문디", "AMUNDI", "인베스코", "INVESCO", "웰링턴", "WELLINGTON", "베일리", "BAILLIE", "싱가포르투자청",
                   "아부다비", "ABU DHABI", "쿠웨이트", "KUWAIT", "사우디", "카타르투자청", "QATAR", "캘리포니아", "CALIFORNIA", "캐나다연금", "CANADA PENSION", "ABERDEEN", "에버딘", "UBS", "골드만", "GOLDMAN",
                   "모건스탠리", "MORGAN STANLEY", "시티뱅크", "CITIBANK", "씨티", "HSBC", "BNP", "AQR", "노무라", "NOMURA", "ISHARES", "아이셰어즈"]


def is_neutral_inst(name):
    """국민연금·산업은행 같은 공적 기관, 블랙록·노르웨이 중앙은행 같은 외국계 대형 운용사: 경영권 분쟁의 한쪽이 아니라 표를 쥐는 캐스팅보트로만 봄"""
    n = name or ""
    if any(i in n for i in INSTITUTIONS + NEUTRAL_EXTRA):
        return True
    if _is_activist(n, strict=True):
        return False
    u = n.upper()
    return any(f.upper() in u for f in FOREIGN_PASSIVE)


def _neutral(h):
    """보유자 한 명이 중립(캐스팅보트)인가: 내가 정한 역할이 있으면 그것을 따르고, 없으면 이름으로 판단"""
    role = h.get("role")
    if role == "challenger":
        return False
    if role == "neutral":
        return True
    return is_neutral_inst(h.get("name"))


def contestable(holder_pct, ext_holders):
    """외부 보유자가 최대주주 측(우호 지분 포함)의 경영권을 실제로 흔들 만한가. 5% 이상 가졌다는 것만으로는 분쟁이 아니라서,
    최대주주 측 지분이 충분히 적고 도전자 지분이 그에 견줄 만할 때만 True:
    ① 격차 10%p 이내  ② 도전자 지분이 최대주주 측의 절반 이상  ③ 경영참여·행동주의 도전자가 격차 15%p 이내이거나 최대주주 측의 35% 이상
    ④ 도전자들을 합치면 최대주주 측의 70% 이상 (둘 이상일 때)"""
    ch = sorted([h for h in ext_holders if h.get("pct") and not _neutral(h)], key=lambda h: -h["pct"])
    if holder_pct is None or not ch or holder_pct <= 0:
        return False
    top = ch[0]
    gap, ratio = holder_pct - top["pct"], top["pct"] / holder_pct
    if gap <= 10 or ratio >= 0.5:
        return True
    if (top.get("tp") == "일반" or top.get("activist")) and (gap <= 15 or ratio >= 0.35):
        return True
    if len(ch) >= 2 and sum(h["pct"] for h in ch) >= 0.7 * holder_pct:
        return True
    if any(h.get("slow") for h in ch) and (gap <= 20 or ratio >= 0.3):          # 오래 모아 온 도전자는 격차가 조금 커도 위험 후보
        return True
    return False


def structure_score(holder_pct, ext_holders, pbr=None):
    """최대주주 측 지분과 외부 5% 보유자들 → dict(score, reasons[], gap)
    ext_holders: [dict(name, pct, tp('일반'/'약식'), activist(bool), chg(최근 증감 %p), date)] (최대주주 측 제외)"""
    reasons, score = [], 0
    gap = None
    # 맞서는 쪽(도전자)은 공적 기관(국민연금·산업은행 등)을 뺀 5% 보유자. 공적 기관은 분쟁 당사자가 아니라 캐스팅보트로만 봄
    ext = sorted([h for h in ext_holders if h.get("pct") and not _neutral(h)], key=lambda h: -h["pct"])
    if not ext:                # 맞서는 쪽(공적 기관 제외 5% 보유자)이 없으면 분쟁 구조가 아님 — 지분율이 낮다는 이유만으로 가능성을 매기지 않음
        return dict(score=0, reasons=[], gap=None, contest=False)
    if not contestable(holder_pct, ext):      # 5% 이상 보유자가 있어도 최대주주 측과 격차가 크면 경영권 분쟁으로 보지 않음 (예: 행동주의 펀드 6% vs 최대주주 측 45%)
        top = ext[0]
        return dict(score=0, gap=(holder_pct - top["pct"]) if holder_pct is not None else None, contest=False,
                    reasons=[f"외부 5% 보유자({top['name']} {top['pct']:.1f}%)가 있지만 최대주주 측" + (f" {holder_pct:.1f}%" if holder_pct is not None else "") + "에 견줄 지분이 아니라 경영권을 흔들기 어려워요 — 분쟁으로 보지 않아요"])
    if holder_pct is not None and ext:
        gap = holder_pct - ext[0]["pct"]
        if gap <= 5:
            score += 3
            reasons.append(f"최대주주 측 {holder_pct:.1f}% vs 최대 외부 보유자 {ext[0]['pct']:.1f}% — 격차 {gap:.1f}%p (매우 작음)")
        elif gap <= 10:
            score += 2
            reasons.append(f"최대주주 측 {holder_pct:.1f}% vs 최대 외부 보유자 {ext[0]['pct']:.1f}% — 격차 {gap:.1f}%p")
        elif gap <= 15:
            score += 1
            reasons.append(f"최대주주 측과 최대 외부 보유자 격차 {gap:.1f}%p")
    if holder_pct is not None and holder_pct < 30:
        score += 1
        reasons.append(f"최대주주 측 지분율이 낮음({holder_pct:.1f}%)")
    gen = [h for h in ext if h.get("tp") == "일반"]
    if gen:
        score += 2
        reasons.append("경영참여 목적(일반) 5% 보유자: " + ", ".join(f"{h['name']} {h['pct']:.1f}%" for h in gen[:3]))
    act = [h for h in ext if h.get("activist")]
    if act:
        score += 2
        reasons.append("행동주의 성향 보유자: " + ", ".join(h["name"] for h in act[:3]))
    today_ = dt.date.today()
    sh = []
    for h in ext:
        d = h.get("shift") or ""
        if len(d) == 8 and d.isdigit():
            try:
                if (today_ - dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))).days <= 365:
                    sh.append((h, d))
            except ValueError:
                pass
    if sh:
        score += 3
        reasons.append("단순투자(약식)로 지분을 모으다 경영참여(일반)로 목적을 바꾼 보유자: " + ", ".join(f"{h['name']} {h['pct']:.1f}% ({d[:4]}-{d[4:6]}-{d[6:]})" for h, d in sh[:3]))
    slow = [h for h in ext if h.get("slow")]
    if slow:
        score += 3
        reasons.append("1년 가까이 천천히 지분을 모아 온 보유자(분쟁은 보통 이렇게 시작해요): " + ", ".join(
            f"{h['name']} {h.get('acc_months', 0)}개월째 " + (f"1년 전 {h['pct_1y']:.1f}% → " if h.get("pct_1y") is not None else f"{h.get('first_pct') or 0:.1f}% → ") + f"{h['pct']:.1f}% (최근 1년 {h.get('acc_up', 0)}번 늘림)" for h in sorted(slow, key=lambda h: -(h.get("grow") or 0))[:3]))
    acc = [h for h in ext if (h.get("grow") or 0) >= 2.0 and h["pct"] >= 5 and not h.get("slow")]
    if acc:
        score += 2
        reasons.append("지분을 꾸준히 늘려 온 보유자(야금야금 모으는 중): " + ", ".join(
            f"{h['name']} {h.get('first_pct') or 0:.1f}%→{h['pct']:.1f}%" + (" (단순투자 보고)" if h.get("tp") == "약식" else "") for h in sorted(acc, key=lambda h: -(h.get("grow") or 0))[:3]))
    inc = [h for h in ext if (h.get("chg") or 0) >= 0.5]
    if inc:
        score += 1
        reasons.append("최근 지분을 늘린 외부 보유자: " + ", ".join(f"{h['name']} +{h['chg']:.1f}%p" for h in inc[:3]))
    if pbr is not None and 0 < pbr < 0.7 and score:
        score += 1
        reasons.append(f"PBR {pbr:.2f} — 자산 대비 저평가라 지분 확보 유인이 있어요")
    return dict(score=score, reasons=reasons, gap=gap, contest=True)


STRUCT_TTL_DAYS = 7          # 지분 구조(최대주주 지분·5% 보유자)를 이 기간 안에는 다시 받지 않음
MAJORITY = 50.0          # 최대주주 측 지분이 이 값 이상이면 분쟁이 없는 종목으로 봄




def swing_holders(holder_pct, ext):
    """캐스팅보트: 최대주주 측과 맞서는 가장 큰 외부 보유자(경영참여 보고자 우선)의 격차보다 큰 지분을 가진 제3의 보유자 → [dict(name, pct, inst)]
    격차가 작을수록 제3자의 한 표가 승패를 가르므로, 격차보다 지분이 큰 보유자를 캐스팅보트로 봐요."""
    if holder_pct is None or not ext:
        return []
    ch = [h for h in ext if not _neutral(h)]
    if not ch:                 # 맞서는 쪽이 없으면 캐스팅보트도 없음
        return []
    gen = sorted([h for h in ch if h.get("tp") == "일반" and h.get("pct")], key=lambda h: -h["pct"])
    top = gen[0] if gen else max(ch, key=lambda h: h.get("pct") or 0)
    gap = abs(holder_pct - top["pct"])
    out = []
    for h in ext:
        if h["name"] == top["name"] or not h.get("pct"):
            continue
        neutral = _neutral(h)
        decisive = h["pct"] >= max(gap, 3.0)
        if decisive or (neutral and h["pct"] >= 5):          # 공적 기관은 지분이 격차보다 작아도 표를 쥔 캐스팅보트 후보로 보여 줌
            out.append(dict(name=h["name"], pct=h["pct"], inst=neutral, gap=gap, tp=h.get("tp"), decisive=decisive))
    return sorted(out, key=lambda x: -x["pct"])


_MEET = re.compile(r"일\s*시\s*[:：]?\s*(\d{4})\s*[년.\-]\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})")


def meeting_date(text):
    """주주총회 소집공고 본문 → 개최일 'YYYY-MM-DD' 또는 ''"""
    m = _MEET.search(text or "")
    if not m:
        m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", (text or "")[:3000])
    if not m:
        return ""
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


# 안건 분류 (지배구조·경영권에 직접 닿는 안건은 key=True)
_AGENDA_TYPES = [
    (r"이사.{0,6}해임|해임.{0,4}이사|감사.{0,4}해임", "이사·감사 해임", True),
    (r"이사.{0,4}(선임|선출)|사내이사|사외이사|기타비상무", "이사 선임", True),
    (r"감사위원.{0,8}(선임|분리)|감사.{0,4}선임", "감사(위원) 선임", True),
    (r"정관.{0,4}(변경|개정)", "정관 변경", True),
    (r"집중투표", "집중투표제", True),
    (r"합병|분할|영업양수|영업양도|주식.{0,3}교환|주식.{0,3}이전", "합병·분할", True),
    (r"자기주식.{0,6}(소각|취득)|주식소각|감자", "자사주·소각·감자", False),
    (r"배당", "배당", False),
    (r"보수한도|이사.{0,4}보수|퇴직금", "이사 보수·퇴직금", False),
    (r"주식매수선택권|스톡옵션", "스톡옵션", False),
    (r"재무제표|결산", "재무제표 승인", False),
    (r"신주|유상증자|전환사채|신주인수권|주식.{0,4}발행", "신주·사채 발행", True),
]


def classify_agenda(title):
    for pat, label, key_ in _AGENDA_TYPES:
        if re.search(pat, title):
            return label, key_
    return "기타", False


def parse_agenda(text):
    """소집공고·위임장 권유 본문 → [dict(no, title, type, key, proposal(주주제안 표시 여부))] (제1호 의안 …). 못 찾으면 []"""
    out, seen = [], set()
    lines = (text or "").split("\n")
    for ln in lines:
        flat = ln.replace("\t", " ").strip()
        m = re.match(r"^[\-\*ㆍ·○◦\s]*(?:제\s*)?(\d{1,2})\s*(?:호\s*)?(?:의안|안건)\s*[:：.\-]?\s*(.{4,120})$", flat)
        if not m:
            continue
        no, title = m.group(1), m.group(2).strip()
        if no in seen or len(title) < 4:
            continue
        seen.add(no)
        label, key_ = classify_agenda(title)
        out.append(dict(no=int(no), title=title[:110], type=label, key=key_, proposal=("주주제안" in title or "주주제안" in flat)))
        if len(out) >= 15:
            break
    return out


def record_date(text):
    """주주총회 소집공고의 의결권 행사 기준일(주주명부 확정일) → 'YYYY-MM-DD' 또는 ''"""
    for m in re.finditer(r"기준일[^\d]{0,12}(\d{4})\s*[년.\-]\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})", text or ""):
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue
    return ""


def parse_proxy(text):
    """위임장 권유 참고서류 본문 → dict(period, agenda[], purpose) (읽은 것만)"""
    out = {}
    m = re.search(r"권유\s*기간\s*[:：]?\s*([^\n]{6,80})", text or "")
    if m:
        out["period"] = re.sub(r"\s+", " ", m.group(1)).strip()
    ag = parse_agenda(text)
    if ag:
        out["agenda"] = ag
    m = re.search(r"(?:권유의\s*취지|권유\s*취지|권유\s*목적)\s*[:：]?\s*([^\n]{10,200})", text or "")
    if m:
        out["purpose"] = re.sub(r"\s+", " ", m.group(1)).strip()
    return out


def find_proxies(events, key, today=None, session=None, limit=4):
    """최근 90일 위임장 권유 공시 → [dict(filer, date, rcept_no, ext(주주측 여부), period, agenda, purpose)] (제출인마다 가장 최근 것)"""
    import fin_clues
    today = today or dt.date.today()
    seen, out = set(), []
    for e in sorted([e for e in events if e["kind"] in ("proxy_ext", "proxy_co") and e.get("rcept_no")], key=lambda e: e["date"], reverse=True):
        if (today - dt.date.fromisoformat(e["date"])).days > 90 or e.get("flr") in seen:
            continue
        seen.add(e.get("flr"))
        try:
            txt = fin_clues.fetch_document_text(e["rcept_no"], key, session)
        except Exception:
            txt = ""
        info = parse_proxy(txt) if txt else {}
        out.append(dict(filer=e.get("flr") or "", date=e["date"], rcept_no=e["rcept_no"], ext=(e["kind"] == "proxy_ext"), **info))
        if len(out) >= limit:
            break
    return out


def find_meeting(events, key, today=None, session=None):
    """종목의 최근 주총 소집공고(정기·임시) 중 아직 안 열린 것의 날짜 → dict(date, rcept_no, title, kind) 또는 None"""
    import fin_clues
    today = today or dt.date.today()
    cands = sorted([e for e in events if e["kind"] in ("egm", "agm") and e.get("rcept_no")], key=lambda e: e["date"], reverse=True)
    for e in cands[:2]:
        if (today - dt.date.fromisoformat(e["date"])).days > 75:
            continue
        try:
            txt = fin_clues.fetch_document_text(e["rcept_no"], key, session)
        except Exception:
            continue
        d = meeting_date(txt)
        if d and d >= (today - dt.timedelta(days=2)).isoformat():
            return dict(date=d, rcept_no=e["rcept_no"], title=e.get("title") or "", kind=e["kind"], agenda=parse_agenda(txt), record_date=record_date(txt))
    return None


def potential_level(sc):
    return "가능성 높음" if sc >= 6 else "가능성" if sc >= 4 else None


COMPANY_WORDS = ["㈜", "(주)", "주식회사", "홀딩스", "그룹", "물산", "생명", "화재", "전자", "증권", "은행", "건설", "재단", "투자", "자산운용", "파트너스", "펀드", "회사", "지주", "산업", "제약", "화학", "에너지", "금융", "보험", "카드", "캐피탈", "개발"]


def _is_company(name):
    n = name or ""
    return any(w in n for w in COMPANY_WORDS) or len(norm_name(n)) >= 5


def _cache_dir():
    import os
    base = os.environ.get("DART_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return os.path.join(os.path.expanduser(base), "dart_cache")


def affiliate_names(corp, corp_name, hy_names, key, today, session=None):
    """공시의 '타법인 출자 현황'으로 본 계열·우호 회사 이름: 대상 회사가 출자한 법인들 + 최대주주 법인(지주사 등)이 출자한 법인들.
    5% 보고자가 여기에 있으면 같은 그룹이거나 서로 지분을 가진 우호 관계라 분쟁 상대로 보지 않음"""
    s = session or requests
    year = today.year - 1 if today.month >= 4 else today.year - 2
    out = set()

    def otr(cc):
        try:
            r = s.get(f"{DART_BASE}/otrCprInvstmntSttus.json", params=dict(crtfc_key=key, corp_code=cc, bsns_year=str(year), reprt_code="11011"), timeout=30)
            j = r.json() if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            return []
        if str(j.get("status")) == "020":
            raise DisputeError("DART 하루 호출 한도를 넘었어요")
        return [(x.get("inv_prm") or "").strip() for x in (j.get("list") or []) if (x.get("inv_prm") or "").strip()] if str(j.get("status")) == "000" else []
    out.update(otr(corp))
    owners = [n for n in hy_names if _is_company(n)][:2]          # 최대주주 법인(지주사 등) — 사업보고서 명단의 앞쪽
    if owners:
        try:
            from score_auto import corp_code_by_name
            for nm in owners:
                cc = corp_code_by_name(nm, _cache_dir(), key, session)
                if cc and cc != corp:
                    out.update(otr(cc))
                    out.add(nm)
        except DisputeError:
            raise
        except Exception:
            pass
    return {n for n in out if len(norm_name(n)) >= 3 and norm_name(n) != norm_name(corp_name)}


STRUCT_META = {}          # 종목코드 → 마지막으로 받은 지분 자료의 기준 (최대주주 현황 기간, 5% 주주 표를 읽은 보고서)
PERIOD_LABEL = {"11011": "사업보고서", "11012": "반기보고서", "11013": "1분기보고서", "11014": "3분기보고서"}


def hy_periods(today):
    """최대주주 현황을 받을 (연도, 보고서코드) 후보를 최신순으로: 3분기 → 반기 → 1분기 → 직전 사업보고서.
    연말 사업보고서만 쓰면 최대주주 지분이 길게는 1년 가까이 묵은 값이라, 그 뒤 분기·반기 보고서가 나왔으면 그걸 먼저 씀"""
    y = today.year
    out = []
    if today >= dt.date(y, 11, 20):
        out.append((y, "11014"))
    if today >= dt.date(y, 8, 20):
        out.append((y, "11012"))
    if today >= dt.date(y, 5, 20):
        out.append((y, "11013"))
    out.append((y - 1 if today.month >= 4 else y - 2, "11011"))
    return out


_HDR_WORDS = {"구분", "주주명", "성명", "성명(법인명)", "법인명", "소유주식수", "지분율", "비고", "주식수", "소유 주식수", "5%이상주주", "합계", "계", "최대주주", "우리사주조합", "소액주주", "-", "주식"}


def parse_holder_table(text):
    """정기보고서(사업·반기) 본문 줄 텍스트(표 칸은 탭) → [dict(name, pct, shares)] 5% 이상 주주 표.
    '5% 이상 주주' 또는 '주식의 분포/소유현황' 아래의 표에서 이름 칸과 지분율(%) 칸을 읽음"""
    lines = text.split("\n") if isinstance(text, str) else list(text)
    out = []
    for i, ln in enumerate(lines):
        flat = re.sub(r"\s+", "", ln)
        if not (("5%이상" in flat and "주주" in flat) or "주식의분포" in flat):
            continue
        for j in range(i + 1, min(i + 28, len(lines))):
            row = lines[j]
            rf = re.sub(r"\s+", "", row)
            if re.match(r"^(나|다|라|마|3|4|5)\.", rf) and j > i + 1 and "5%" not in rf:
                break
            cells = [c.strip() for c in row.split("\t") if c.strip()]
            if len(cells) < 2:
                continue
            pct, shares, name = None, None, None
            for c in cells:
                cn = c.replace(",", "").replace("%", "").strip()
                if re.fullmatch(r"-?\d+(\.\d+)?", cn):
                    v = float(cn)
                    if 0 < v <= 100 and "." in cn or (c.endswith("%") and 0 < v <= 100):
                        pct = v
                    elif v > 100:
                        shares = v
                elif name is None and re.search(r"[A-Za-z가-힣]", c) and re.sub(r"\s+", "", c) not in _HDR_WORDS and not re.fullmatch(r"\(?[가-힣]\)?\.?", c.strip()):
                    name = c
            if name and pct is not None and pct >= 5:
                out.append(dict(name=name, pct=pct, shares=shares))
    seen, res = set(), []
    for h in out:
        k = norm_name(h["name"])
        if k and k not in seen:
            seen.add(k)
            res.append(h)
    return res


def report_holders(corp, key, today, session=None):
    """가장 최근 사업·반기보고서의 '5% 이상 주주' 표 → ([dict(name, pct)], (보고서 이름, 접수일, 접수번호)) 못 읽으면 ([], None).
    분기보고서에는 이 표가 없어서 사업·반기보고서만 봄"""
    s = session or requests
    r = s.get(f"{DART_BASE}/list.json", params=dict(crtfc_key=key, corp_code=corp, bgn_de=(today - dt.timedelta(days=430)).strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"),
                                                      pblntf_ty="A", page_count="20"), timeout=30)
    j = r.json() if r.status_code == 200 else {}
    if str(j.get("status")) == "020":
        raise DisputeError("DART 하루 호출 한도를 넘었어요")
    rows = [x for x in (j.get("list") or []) if re.search(r"사업보고서|반기보고서", x.get("report_nm") or "")]
    rows.sort(key=lambda x: (x.get("rcept_dt") or "", "정정" not in (x.get("report_nm") or "")), reverse=True)
    if not rows:
        return [], None
    top = rows[0]
    from fin_clues import fetch_document_text
    text = fetch_document_text(top.get("rcept_no", ""), key, s)
    return parse_holder_table(text), (top.get("report_nm", ""), top.get("rcept_dt", ""), top.get("rcept_no", ""))


def fetch_structure(code, corp, key, today=None, session=None, corp_name=""):
    """한 종목의 최대주주 측 지분율과 외부 5% 보유자 → (holder_pct, [ext...]) (못 받으면 (None, []))"""
    today = today or dt.date.today()
    year = today.year - 1 if today.month >= 4 else today.year - 2
    s = session or requests

    def call(path, **p):
        r = s.get(f"{DART_BASE}/{path}", params=dict(crtfc_key=key, corp_code=corp, **p), timeout=30)
        j = r.json() if r.status_code == 200 else {}
        if str(j.get("status")) == "020":
            raise DisputeError("DART 하루 호출 한도를 넘었어요")
        return j.get("list") or [] if str(j.get("status")) == "000" else []
    hy, hy_label = [], ""
    for y_, rc_ in hy_periods(today):
        hy = call("hyslrSttus.json", bsns_year=str(y_), reprt_code=rc_)
        if hy:
            hy_label = f"{y_} {PERIOD_LABEL.get(rc_, rc_)}"
            break
    STRUCT_META[code] = dict(hy=hy_label)
    holder_pct, names = None, set()
    for r in hy:
        nm = (r.get("nm") or "").strip()
        v = _num(r.get("trmend_posesn_stock_qota_rt"))
        if nm in ("계", "합계") and v is not None:
            holder_pct = v
        elif nm:
            names.add(nm)
    if holder_pct is None:
        holder_pct = sum(_num(r.get("trmend_posesn_stock_qota_rt")) or 0 for r in hy) or None
    hy_order = [(r.get("nm") or "").strip() for r in hy if (r.get("nm") or "").strip() not in ("", "계", "합계")]
    try:
        aff = affiliate_names(corp, corp_name, hy_order, key, today, s)
    except DisputeError:
        raise
    except Exception:
        aff = set()
    ms = call("majorstock.json")
    latest = {}
    hist = {}
    for r in sorted(ms, key=lambda r: r.get("rcept_dt") or ""):
        who = (r.get("repror") or "").strip()
        if who and not is_insider(who, names) and not is_insider(who, aff) and not is_group_affiliate(who, corp_name, names):
            tp0 = "일반" if "일반" in (r.get("report_tp") or "") else "약식" if "약식" in (r.get("report_tp") or "") else ""
            hist.setdefault(who, []).append((r.get("rcept_dt") or "", tp0, _num(r.get("stkrt")), (r.get("report_resn") or "")))
    ally = {}
    for r in ms:
        who = (r.get("repror") or "").strip()
        d = r.get("rcept_dt") or ""
        if who and not is_insider(who, names) and (is_insider(who, aff) or is_group_affiliate(who, corp_name, names)):
            pc_ = _num(r.get("stkrt"))             # 최대주주 명단에는 없지만 계열·우호 회사로 본 5% 보유자: 우호 지분으로 더함
            if pc_ is not None and (who not in ally or d > ally[who][0]):
                ally[who] = (d, pc_)
        if not who or is_insider(who, names) or is_insider(who, aff) or is_group_affiliate(who, corp_name, names):          # 최대주주 본인·특수관계인·타법인 출자 공시로 본 계열·우호 회사·같은 그룹 계열사는 맞서는 쪽이 아님
            continue
        if who not in latest or d > latest[who]["date"]:
            latest[who] = dict(name=who, pct=_num(r.get("stkrt")), chg=_num(r.get("stkrt_irds")), tp=("일반" if "일반" in (r.get("report_tp") or "") else "약식" if "약식" in (r.get("report_tp") or "") else ""),
                               date=d, resn=(r.get("report_resn") or "")[:40], activist=_is_activist(who), rcept_no=r.get("rcept_no") or "")
    ext = [h for h in latest.values() if h["pct"] and h["pct"] >= 5]
    for h in ext:
        h.update(purpose_shift(hist.get(h["name"], []), today))
    # 사업·반기보고서의 '5% 이상 주주' 표에만 있는 보유자(대량보유 보고 API에 안 잡히는 외국계 펀드 등)를 더함
    try:
        rh, rinfo = report_holders(corp, key, today, s)
    except DisputeError:
        raise
    except Exception:
        rh, rinfo = [], None
    if rinfo:
        STRUCT_META.setdefault(code, {})["report"] = f"{rinfo[0]} ({rinfo[1][:4]}-{rinfo[1][4:6]}-{rinfo[1][6:]})" if len(rinfo[1]) == 8 else rinfo[0]
    for h in rh:
        nm = h["name"]
        if is_insider(nm, names) or is_insider(nm, aff) or is_group_affiliate(nm, corp_name, names):
            continue
        if any(is_insider(nm, [e["name"]]) or is_insider(e["name"], [nm]) for e in ext):
            continue
        ext.append(dict(name=nm, pct=h["pct"], tp="", chg=None, date=(rinfo[1] if rinfo else ""), resn="정기보고서 5% 주주 표", activist=_is_activist(nm), rcept_no=(rinfo[2] if rinfo else ""),
                        src="report", shift="", grow=0.0, first_pct=h["pct"], track=[], acc_months=0, pct_1y=None, acc_up=0, slow=False))
    ally_pct = sum(p for _, p in ally.values() if p >= 5)
    return holder_pct, ext, sorted(names) + sorted(aff)[:150], round(ally_pct, 2)


def data_warnings(friendly_pct, ext, today=None):
    """지분 자료가 이상하게 보이는 경우를 알려 줌: ① 5% 보유자가 최대주주 측보다 큼(연말 사업보고서가 옛 자료이거나 최대주주 변경·공개매수·인수 진행, 특별관계자 합산)
    ② 최대주주 측 + 5% 보유자 합이 100% 초과(보고 지분이 겹치거나 예정분) ③ 1년 넘은 5% 보고"""
    today = today or dt.date.today()
    w = []
    ch = [h for h in ext if h.get("pct") and not _neutral(h)]
    if friendly_pct is not None and ch:
        top = max(ch, key=lambda h: h["pct"])
        if top["pct"] > friendly_pct:
            w.append(f"{top['name']} {top['pct']:.1f}%가 최대주주 측 {friendly_pct:.1f}%보다 커요 — 최대주주 변경·공개매수·인수가 진행 중이거나 사업보고서(연말) 자료가 옛 것이거나 특별관계자 지분이 합산된 값일 수 있어요")
        tot = friendly_pct + sum(h["pct"] for h in ext if h.get("pct") and not h.get("manual"))
        if tot > 100.5:
            w.append(f"최대주주 측 + 5% 보유자 지분을 합치면 {tot:.0f}%라 100%를 넘어요 — 보고된 지분이 겹치거나 예정분(계약·공개매수)일 수 있어요")
    for h in ext:
        d = h.get("date") or ""
        if len(d) == 8 and d.isdigit():
            try:
                if (today - dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))).days > 365 and not h.get("manual"):
                    w.append(f"{h['name']}의 마지막 5% 보고가 1년이 넘었어요({d[:4]}-{d[4:6]}-{d[6:]}) — 지금 지분이 다를 수 있어요")
                    break
            except ValueError:
                pass
    return w


def purpose_shift(rows, today=None):
    """한 보고자의 보고 이력 [(날짜, 일반/약식, 지분율, 사유)] → dict(shift=단순투자→경영참여 전환 날짜 또는 '', first_pct, grow(%p))
    처음엔 단순투자(약식)로 지분을 모으다 나중에 경영참여(일반)로 바꾸는 흐름을 잡는다."""
    rows = sorted(rows, key=lambda r: r[0])
    shift, seen_simple = "", False
    for d, tp, pct, resn in rows:
        if tp == "약식":
            seen_simple = True
        elif tp == "일반" and seen_simple:
            shift = d                      # 약식 보고 뒤에 처음 나온 일반 보고 = 보유 목적을 경영참여로 바꾼 때
            break
    if not shift:                          # 사유에 '보유목적' 변경이 적힌 일반 보고
        for d, tp, pct, resn in rows:
            if tp == "일반" and "목적" in (resn or ""):
                shift = d
                break
    pcts = [r[2] for r in rows if r[2]]
    out = dict(shift=shift, first_pct=pcts[0] if pcts else None, grow=(pcts[-1] - pcts[0]) if len(pcts) >= 2 else 0.0, n_rep=len(rows))
    out.update(accumulation(rows, today))
    return out


def _to_date(d):
    try:
        return dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    except (ValueError, TypeError):
        return None


def accumulation(rows, today=None):
    """보고 이력 [(날짜, 일반/약식, 지분율, 사유)] → 오랫동안 천천히 지분을 늘려 왔는지.
    경영권 분쟁은 보통 1년 전부터 지분을 모으면서 천천히 시작되므로, 첫 보고 이후 기간·1년 전 지분·늘린 횟수로 '장기 누적'을 본다.
    → dict(track=[[날짜, 지분율]…최근 10개], acc_months=첫 보고 이후 개월, pct_1y=1년 전 지분(없으면 None), acc_up=최근 1년 지분을 늘린 보고 수, slow=장기 누적 여부)"""
    today = today or dt.date.today()
    pts = [(_to_date(r[0]), r[2]) for r in sorted(rows, key=lambda r: r[0]) if r[2] and _to_date(r[0])]
    if not pts:
        return dict(track=[], acc_months=0, pct_1y=None, acc_up=0, slow=False)
    first_d = pts[0][0]
    months = max(0, (today.year - first_d.year) * 12 + today.month - first_d.month)
    cut = today - dt.timedelta(days=365)
    before = [p for d, p in pts if d <= cut]
    pct_1y = before[-1] if before else None
    recent = [(d, p) for d, p in pts if d > cut]
    prev = pct_1y if pct_1y is not None else (recent[0][1] if recent else None)
    ups, last = 0, prev
    for d, p in recent:
        if last is not None and p > last + 0.3:
            ups += 1
        last = p
    cur = pts[-1][1]
    slow = bool(months >= 9 and cur - pts[0][1] >= 2.0 and ups >= 2)
    return dict(track=[[d.strftime("%Y%m%d"), p] for d, p in pts[-10:]], acc_months=months, pct_1y=pct_1y, acc_up=ups, slow=slow)


def _num(v):
    t = str(v if v is not None else "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


# ---------------- 한 번에 ----------------
def analyze(conn, key=None, today=None, log=print, session=None, enrich_limit=60, progress=None):
    """저장된 신호로 종목별 단계를 매기고, 신호가 있거나 경영참여 보유자가 있는 종목은 지분 구조도 받아 '가능성'까지 계산 → dispute_state 저장"""
    ensure(conn)
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=180)).isoformat()
    by = {}
    names = {}
    corp = {}
    for code, name, cc, d, nm, flr, kind, w, act, rn in conn.execute("SELECT stock_code, corp_name, corp_code, rcept_dt, report_nm, flr_nm, kind, weight, activist, rcept_no FROM dispute_event WHERE rcept_dt >= ?", (since,)):
        if kind in ("injunction", "tender", "litig", "proxy_ext", "proxy_co"):            # 분류 규칙이 바뀌었을 수 있어 제목으로 다시 매김 (예전에 잘못 잡힌 가처분·자기주식 공개매수를 걸러냄)
            re_ = classify(nm, flr or "", name or "")
            if re_ is None:
                continue
            kind, w, act = re_
        if kind in ("blk_gen", "proxy_ext", "proposal", "tender") and is_neutral_inst(flr):
            continue                      # 국민연금·산업은행 같은 공적 기관의 보고는 분쟁 신호로 세지 않음 (캐스팅보트로만 봄)
        by.setdefault(code, []).append(dict(date=d, kind=kind, weight=w, activist=act, title=nm, flr=flr, rcept_no=rn))
        names[code] = name
        corp[code] = cc
    states = {}
    for code, evs in by.items():
        a = assess_events(evs, today)
        states[code] = dict(name=names[code], ev=a, pot=None, struct=None)
    # 구조: 신호가 있는 종목 + 일반 5% 보유 신호가 1건 이상인 종목
    cand = [c for c, evs in by.items() if (states[c]["ev"] or any(e["kind"] == "blk_gen" for e in evs))]
    cand.sort(key=lambda c: -(states[c]["ev"]["score"] if states[c]["ev"] else 0))
    pbr = {}
    try:
        pbr = {c: p for c, p in conn.execute("SELECT code, pbr FROM valuation")}
    except sqlite3.Error:
        pass
    if key:
        cached = {r[0]: r for r in conn.execute("SELECT code, fetched, hp, ext, insiders, ally, meta FROM dispute_struct")}
        tags, manual = {}, {}
        for tc, tn, tr, tp_ in conn.execute("SELECT code, name, role, pct FROM dispute_tag"):
            if tn != "*":
                tags.setdefault(tc, {})[norm_name(tn)] = tr
                if tp_ is not None and tr in ("challenger", "ally"):
                    manual.setdefault(tc, []).append((tn, tr, tp_))
        ttl = (today - dt.timedelta(days=STRUCT_TTL_DAYS)).isoformat()
        n_fetch = 0
        for i, code in enumerate(cand):
            if progress and i % 3 == 0:
                progress(f"지분 구조 {i}/{len(cand)}")
            c_ = cached.get(code)
            if c_ and c_[1] >= ttl:                       # 며칠 안에 받은 지분 구조는 다시 받지 않음 (그만큼 더 많은 종목을 훑게 됨)
                hp, ext, insiders, ally = c_[2], json.loads(c_[3] or "[]"), json.loads(c_[4] or "[]"), (c_[5] or 0.0)
                STRUCT_META[code] = json.loads(c_[6] or "{}") if c_[6] else {}
            else:
                if n_fetch >= enrich_limit:
                    continue
                try:
                    cc = corp.get(code)
                    hp, ext, insiders, ally = fetch_structure(code, cc, key, today, session, corp_name=names.get(code, ""))
                    n_fetch += 1
                except (DisputeError, requests.RequestException) as e:
                    log(f"  ✖ {code}: {e}")
                    if "한도" in str(e):
                        break
                    continue
                conn.execute("INSERT OR REPLACE INTO dispute_struct (code, fetched, hp, ext, insiders, ally, meta) VALUES (?,?,?,?,?,?,?)",
                             (code, today.isoformat(), hp, json.dumps(ext, ensure_ascii=False), json.dumps(insiders, ensure_ascii=False), ally, json.dumps(STRUCT_META.get(code) or {}, ensure_ascii=False)))
                time.sleep(0.15)
            # 내가 정한 역할(우호·중립·도전자)을 반영: 우호는 우호 지분에 더하고, 중립은 캐스팅보트로만, 도전자는 공적 기관이라도 도전자로 봄
            mine = tags.get(code) or {}
            if mine or manual.get(code):
                ext2, ally2 = [], ally or 0.0
                for h in ext:
                    role = mine.get(norm_name(h["name"]))
                    if role == "ignore":                  # 지분을 가진 쪽이 아니라고 내가 정함 (예: 공개매수 진행 중인 인수 목적 회사의 예정 지분)
                        continue
                    if role == "ally":
                        ally2 += h.get("pct") or 0.0
                        continue
                    if role:
                        h = dict(h, role=role)
                    ext2.append(h)
                for nm, role, pc_ in manual.get(code, []):          # 내가 직접 넣은 보유자 (DART 5% 보고에 없는 쪽, 예: 맥쿼리)
                    if role == "ally":
                        ally2 += pc_
                    elif not any(norm_name(h["name"]) == norm_name(nm) for h in ext2):
                        ext2.append(dict(name=nm, pct=pc_, tp="일반", activist=False, chg=None, date="", resn="직접 추가", role="challenger", manual=True, shift="", grow=0.0, first_pct=pc_))
                ext, ally = ext2, ally2
            pending = []
            for h in ext:            # 공개매수·인수 계약 진행 중인 보고자의 지분은 아직 실제 보유가 아닐 수 있음 → 보유자 목록·지분 비교에서 빼고 '진행 중'으로 따로 보여 줌
                if not h.get("manual") and (any(e["kind"] == "tender" and e.get("flr") and is_insider(h["name"], [e["flr"]]) for e in by[code]) or re.search(r"계약|공개매수", h.get("resn") or "")):
                    h["pending"] = True
                    pending.append(dict(name=h["name"], pct=h.get("pct"), date=h.get("date"), resn=h.get("resn"), rcept_no=h.get("rcept_no")))
            if pending:
                ext = [h for h in ext if not h.get("pending")]
            hp_eff = (hp + ally) if (hp is not None and ally) else hp          # 최대주주 측 + 계열·우호 회사 5% 보유분 = 우호 지분
            warn = data_warnings(hp_eff, ext, today)
            st_ = structure_score(hp_eff, ext, pbr.get(code))
            states[code]["struct"] = dict(holder_pct=hp, ally_pct=ally or 0.0, friendly_pct=hp_eff, ext=ext, swing=swing_holders(hp_eff, ext), insiders=insiders[:12], warn=warn, pending=pending, meta=STRUCT_META.get(code) or {}, **st_)
            # 공시한 쪽이 최대주주 측이면(지주사·계열사 포함) 분쟁 신호에서 뺌: 일반 5% 보고·외부 권유·주주제안·공개매수
            keep, dropped = [], []
            for e in by[code]:
                if e["kind"] in ("blk_gen", "proxy_ext", "proposal", "tender") and (is_insider(e.get("flr"), insiders) or is_group_affiliate(e.get("flr"), names.get(code, ""), insiders)
                                                                                   or (mine.get(norm_name(e.get("flr"))) in ("ally", "neutral"))):
                    dropped.append(e)
                else:
                    keep.append(e)
            if dropped:
                by[code] = keep
                states[code]["ev"] = assess_events(keep, today)
                states[code]["dropped"] = [dict(date=e["date"], kind=e["kind"], flr=e.get("flr"), title=e["title"]) for e in dropped][:10]
            # 최대주주 측 지분이 50% 이상이면 분쟁이 생길 수 없다고 보고 이 종목은 판정에서 통째로 뺌
            if hp_eff is not None and hp_eff >= MAJORITY:
                states[code]["majority"] = hp_eff
            conn.execute("DELETE FROM dispute_holder WHERE code=?", (code,))
            conn.executemany("INSERT OR REPLACE INTO dispute_holder VALUES (?,?,?,?,?,?)", [(code, h["name"], h["pct"], h["tp"], h["date"], h["resn"]) for h in ext])
    if key:
        for code in [c for c in states if states[c]["ev"]][:40]:
            try:
                states[code]["meeting"] = find_meeting(by[code], key, today, session)
            except Exception:
                states[code]["meeting"] = None
            try:
                states[code]["proxies"] = find_proxies(by[code], key, today, session)
            except Exception:
                states[code]["proxies"] = []
    for code in [c for c in states if states[c]["ev"] and states[c]["ev"]["level"] in ("치열", "시작")][:40]:       # 신호 공시일 전후 주가를 보여 주려고 일별 주가도 받아 둠
        try:
            import bill_watch
            bill_watch.ensure_stock(conn, code, names.get(code, ""))
            bill_watch.refresh_prices(conn, code)
        except Exception:
            pass
    out = 0
    skipped = weak = 0
    kept = []
    try:
        excluded = {c for (c,) in conn.execute("SELECT code FROM dispute_tag WHERE role='exclude'")}      # 내가 '분쟁 아님'으로 뺀 종목
    except sqlite3.Error:
        excluded = set()
    for code, s_ in states.items():
        if code in excluded:
            continue
        if s_.get("majority") is not None:          # 최대주주 측 50% 이상 → 분쟁 판정 대상 아님
            skipped += 1
            continue
        level = s_["ev"]["level"] if s_["ev"] else None
        sc = s_["ev"]["score"] if s_["ev"] else 0
        pot = potential_level(s_["struct"]["score"]) if s_["struct"] else None
        if level is None and pot:
            level, sc = pot, float(s_["struct"]["score"])
        if level is None:
            continue
        stc = s_["struct"]
        ev_ = s_["ev"] or {}
        hard = set(ev_.get("hot") or []) - {"proposal", "owner_chg"}          # 소송·가처분·공개매수·외부 위임장 권유는 그 자체가 분쟁 신호
        if ev_ and not hard:
            # 5% 일반 보고·행동주의 이름·주주제안·최대주주 변경만으로는 분쟁이 아님: 최대주주 측 우호 지분이 적어 실제로 흔들 수 있을 때만 인정
            if level == "징후" and not (stc and stc.get("contest")):
                weak += 1
                continue
            if level in ("시작", "치열") and (not stc or stc.get("contest") is False):          # 지분 구조를 확인 못 했거나 흔들 만한 구조가 아니면 뺌
                weak += 1
                continue
        prev = conn.execute("SELECT level, alerted FROM dispute_state WHERE code=?", (code,)).fetchone()
        payload = dict(ev=s_["ev"], struct=s_["struct"], dropped=s_.get("dropped") or [], majority=s_.get("majority"), meeting=s_.get("meeting"), proxies=s_.get("proxies") or [], events=[e for e in sorted(by[code], key=lambda e: e["date"], reverse=True) if e["kind"] != "agm"][:30])
        kept.append(code)
        conn.execute("INSERT OR REPLACE INTO dispute_state VALUES (?,?,?,?,?,?,?,?)",
                     (code, s_["name"], level, sc, json.dumps(payload, ensure_ascii=False), today.isoformat(), prev[0] if prev else None, prev[1] if prev else None))
        out += 1
    # 더는 해당 없는 종목은 지움
    conn.execute("DELETE FROM dispute_state WHERE updated < ?", (today.isoformat(),))
    if kept:
        conn.execute("DELETE FROM dispute_state WHERE code NOT IN (%s)" % ",".join("?" * len(kept)), kept)
    else:
        conn.execute("DELETE FROM dispute_state")
    conn.commit()
    log(f"[지분분쟁] 판정 종목 {out}개" + (f" (최대주주 측 {MAJORITY:.0f}% 이상이라 뺀 종목 {skipped}개)" if skipped else "") + (f" (경영권을 흔들 만한 구조가 아니라 뺀 약한 신호 {weak}개)" if weak else ""))
    return out


LEVEL_ORDER = {"치열": 0, "시작": 1, "징후": 2, "가능성 높음": 3, "가능성": 4}


def load_states(conn):
    try:
        rows = conn.execute("SELECT code, name, level, score, payload, updated, prev_level, alerted FROM dispute_state").fetchall()
    except sqlite3.Error:
        return []
    out = [dict(code=r[0], name=r[1], level=r[2], score=r[3], payload=json.loads(r[4] or "{}"), updated=r[5], prev_level=r[6], alerted=r[7]) for r in rows]
    out.sort(key=lambda s: (LEVEL_ORDER.get(s["level"], 9), -s["score"]))
    return out


def run_job(db_path, key, status_path, log=print, days_first=120):
    """별도 프로세스용: 공시를 훑고 판정. 진행 상황을 status_path(JSON)에 적음"""
    import os
    st = dict(running=True, pid=os.getpid(), started=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), finished="",
              steps=[dict(name="공시 훑기 (위임장·공개매수·소송·가처분·주주제안·최대주주 변경·경영참여 5%)", state="run", msg="시작"), dict(name="종목별 단계 판정·지분 구조", state="wait", msg="")])

    def save():
        try:
            tmp = status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, status_path)
        except OSError:
            pass
    save()
    last = {"t": 0.0}

    def prog(i):
        def f(msg):
            if time.time() - last["t"] > 3:
                last["t"] = time.time()
                st["steps"][i]["msg"] = msg
                save()
        return f
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        try:
            if not key:
                raise DisputeError("DART_API_KEY가 없어요")
            r = conn.execute("SELECT value FROM dispute_meta WHERE key='scanned'").fetchone()
            today = dt.date.today()
            bgn = (dt.date.fromisoformat(r[0]) - dt.timedelta(days=3)) if r else today - dt.timedelta(days=days_first)
            n = scan(conn, key, bgn.strftime("%Y%m%d"), today.strftime("%Y%m%d"), log=log, progress=prog(0))
            conn.execute("INSERT OR REPLACE INTO dispute_meta VALUES ('scanned', ?)", (today.isoformat(),))
            conn.commit()
            st["steps"][0].update(state="ok", msg=f"{bgn.isoformat()} ~ {today.isoformat()} · 새 신호 {n}건")
            st["steps"][1]["state"] = "run"
            save()
            k = analyze(conn, key, log=log, progress=prog(1))
            st["steps"][1].update(state="ok", msg=f"판정 종목 {k}개")
        except Exception as e:
            idx = 0 if st["steps"][0]["state"] == "run" else 1
            st["steps"][idx].update(state="fail", msg=f"{type(e).__name__}: {str(e)[:160]}")
            log(f"지분분쟁 분석 실패: {e}")
    finally:
        conn.close()
    st["running"] = False
    st["finished"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save()
