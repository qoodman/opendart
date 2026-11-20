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
ACTIVISTS = ["얼라인", "KCGI", "강성부", "플래시라이트", "차파트너스", "트러스톤", "라이프", "머스트", "FCP", "팰리서", "엘리엇", "ELLIOTT", "행동주의", "주주가치",
             "안다", "밸류파트너스", "서울마린", "파인밸류", "VIP", "에이치플러스", "아이온", "비케이", "KB자산"]
ACTIVIST_STRICT = ["얼라인", "KCGI", "강성부", "플래시라이트", "차파트너스", "트러스톤", "팰리서", "엘리엇", "ELLIOTT", "행동주의", "주주가치", "머스트", "FCP"]


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS dispute_event (rcept_no TEXT PRIMARY KEY, corp_code TEXT, corp_name TEXT, stock_code TEXT, rcept_dt TEXT, report_nm TEXT, flr_nm TEXT, kind TEXT, weight REAL, activist INTEGER);
    CREATE INDEX IF NOT EXISTS dispute_event_code ON dispute_event(stock_code, rcept_dt);
    CREATE TABLE IF NOT EXISTS dispute_state (code TEXT PRIMARY KEY, name TEXT, level TEXT, score REAL, payload TEXT, updated TEXT, prev_level TEXT, alerted TEXT);
    CREATE TABLE IF NOT EXISTS dispute_holder (code TEXT, reporter TEXT, pct REAL, tp TEXT, date TEXT, resn TEXT, PRIMARY KEY (code, reporter));
    CREATE TABLE IF NOT EXISTS dispute_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


# ---------------- 분류 ----------------
def _is_activist(name, strict=False):
    return any(a.lower() in (name or "").lower() for a in (ACTIVIST_STRICT if strict else ACTIVISTS))


def _same_company(flr, corp):
    n = lambda t: re.sub(r"[\s\(\)㈜]|주식회사", "", t or "")
    return bool(n(flr)) and (n(flr) == n(corp) or n(corp) in n(flr) or n(flr) in n(corp))


def classify(report_nm, flr_nm="", corp_name="", ptype=""):
    """공시 제목 → (kind, weight, activist(0/1)) 또는 None"""
    t = re.sub(r"\s+", "", report_nm or "")
    if re.search(r"경영권분쟁", t):
        return "litig", 4.0, 0
    if "가처분" in t:
        return "injunction", 3.0, 0
    if "공개매수" in t:
        return "tender", 4.0, 0
    if "의결권대리행사권유" in t or ptype == "D003":
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
def structure_score(holder_pct, ext_holders, pbr=None):
    """최대주주 측 지분과 외부 5% 보유자들 → dict(score, reasons[], gap)
    ext_holders: [dict(name, pct, tp('일반'/'약식'), activist(bool), chg(최근 증감 %p), date)] (최대주주 측 제외)"""
    reasons, score = [], 0
    gap = None
    ext = sorted([h for h in ext_holders if h.get("pct")], key=lambda h: -h["pct"])
    if holder_pct is not None and ext:
        gap = holder_pct - ext[0]["pct"]
        if gap <= 5:
            score += 3
            reasons.append(f"최대주주 측 {holder_pct:.1f}% vs 최대 외부 보유자 {ext[0]['pct']:.1f}% — 격차 {gap:.1f}%p (매우 작음)")
        elif gap <= 10:
            score += 2
            reasons.append(f"최대주주 측 {holder_pct:.1f}% vs 최대 외부 보유자 {ext[0]['pct']:.1f}% — 격차 {gap:.1f}%p")
        elif gap <= 20:
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
    inc = [h for h in ext if (h.get("chg") or 0) >= 0.5]
    if inc:
        score += 1
        reasons.append("최근 지분을 늘린 외부 보유자: " + ", ".join(f"{h['name']} +{h['chg']:.1f}%p" for h in inc[:3]))
    if pbr is not None and 0 < pbr < 0.7 and score:
        score += 1
        reasons.append(f"PBR {pbr:.2f} — 자산 대비 저평가라 지분 확보 유인이 있어요")
    return dict(score=score, reasons=reasons, gap=gap)


INSTITUTIONS = ["국민연금", "산업은행", "한국산업은행", "한국투자공사", "예금보험공사", "신용보증기금", "기술보증기금", "우정사업본부"]


def swing_holders(holder_pct, ext):
    """캐스팅보트: 최대주주 측과 맞서는 가장 큰 외부 보유자(경영참여 보고자 우선)의 격차보다 큰 지분을 가진 제3의 보유자 → [dict(name, pct, inst)]
    격차가 작을수록 제3자의 한 표가 승패를 가르므로, 격차보다 지분이 큰 보유자를 캐스팅보트로 봐요."""
    if holder_pct is None or not ext:
        return []
    gen = sorted([h for h in ext if h.get("tp") == "일반" and h.get("pct")], key=lambda h: -h["pct"])
    top = gen[0] if gen else max(ext, key=lambda h: h.get("pct") or 0)
    gap = abs(holder_pct - top["pct"])
    out = []
    for h in ext:
        if h["name"] == top["name"] or not h.get("pct"):
            continue
        if h["pct"] >= max(gap, 3.0):
            out.append(dict(name=h["name"], pct=h["pct"], inst=any(i in h["name"] for i in INSTITUTIONS), gap=gap, tp=h.get("tp")))
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
    return "가능성 높음" if sc >= 5 else "가능성" if sc >= 3 else None


def fetch_structure(code, corp, key, today=None, session=None):
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
    hy = call("hyslrSttus.json", bsns_year=str(year), reprt_code="11011")
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
    ms = call("majorstock.json")
    latest = {}
    for r in ms:
        who = (r.get("repror") or "").strip()
        d = r.get("rcept_dt") or ""
        if not who or who in names:
            continue
        if who not in latest or d > latest[who]["date"]:
            latest[who] = dict(name=who, pct=_num(r.get("stkrt")), chg=_num(r.get("stkrt_irds")), tp=("일반" if "일반" in (r.get("report_tp") or "") else "약식" if "약식" in (r.get("report_tp") or "") else ""),
                               date=d, resn=(r.get("report_resn") or "")[:40], activist=_is_activist(who))
    ext = [h for h in latest.values() if h["pct"] and h["pct"] >= 5]
    return holder_pct, ext


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
        for i, code in enumerate(cand[:enrich_limit]):
            if progress and i % 3 == 0:
                progress(f"지분 구조 {i}/{min(len(cand), enrich_limit)}")
            try:
                cc = corp.get(code)
                hp, ext = fetch_structure(code, cc, key, today, session)
            except (DisputeError, requests.RequestException) as e:
                log(f"  ✖ {code}: {e}")
                if "한도" in str(e):
                    break
                continue
            st_ = structure_score(hp, ext, pbr.get(code))
            states[code]["struct"] = dict(holder_pct=hp, ext=ext, swing=swing_holders(hp, ext), **st_)
            conn.execute("DELETE FROM dispute_holder WHERE code=?", (code,))
            conn.executemany("INSERT OR REPLACE INTO dispute_holder VALUES (?,?,?,?,?,?)", [(code, h["name"], h["pct"], h["tp"], h["date"], h["resn"]) for h in ext])
            time.sleep(0.15)
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
    out = 0
    for code, s_ in states.items():
        level = s_["ev"]["level"] if s_["ev"] else None
        sc = s_["ev"]["score"] if s_["ev"] else 0
        pot = potential_level(s_["struct"]["score"]) if s_["struct"] else None
        if level is None and pot:
            level, sc = pot, float(s_["struct"]["score"])
        if level is None:
            continue
        prev = conn.execute("SELECT level, alerted FROM dispute_state WHERE code=?", (code,)).fetchone()
        payload = dict(ev=s_["ev"], struct=s_["struct"], meeting=s_.get("meeting"), proxies=s_.get("proxies") or [], events=[e for e in sorted(by[code], key=lambda e: e["date"], reverse=True) if e["kind"] != "agm"][:30])
        conn.execute("INSERT OR REPLACE INTO dispute_state VALUES (?,?,?,?,?,?,?,?)",
                     (code, s_["name"], level, sc, json.dumps(payload, ensure_ascii=False), today.isoformat(), prev[0] if prev else None, prev[1] if prev else None))
        out += 1
    # 더는 해당 없는 종목은 지움
    conn.execute("DELETE FROM dispute_state WHERE updated < ?", (today.isoformat(),))
    conn.commit()
    log(f"[지분분쟁] 판정 종목 {out}개")
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
