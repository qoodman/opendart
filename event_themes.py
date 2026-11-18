# -*- coding: utf-8 -*-
"""
이벤트 → 테마주 정리.
'한중 FTA 소식에 엔터주가 올랐다' 같은 외부 이벤트를 종류별로 정리하고, 각 종류가 닿는 테마·업종(네이버 증권 기준)의 종목과
이벤트 날짜 전후의 지수 대비 주가 반응을 한곳에 모아 봐요.

- EVENTS: 사람이 쓴 사전이에요. 어떤 테마가 오르내리는 경향이 있는지는 '가설'이고 틀릴 수 있어요. 호재/악재 방향은 이벤트 종류마다 정해 둔 것이라
  실제 반응은 날짜별 기록(event_log)에서 직접 확인하세요.
- 반응: 이벤트 날짜 직전 종가 기준 사전 5일, 이후 1·5·20일 지수 대비 초과수익(%p). 코스닥 종목은 코스닥, 그 외 코스피 대비.
- 같은 이벤트에 여러 테마가 한꺼번에 움직이는 것은 시장 전체·같은 날 다른 소식과 섞일 수 있어서 법안 때문이라는 식의 인과는 말하지 않아요.
"""
import re
import sqlite3

import theme_map

# (id, 분류, 이름, 설명, 방향, 주요 테마 정규식, 보조 테마 정규식, 업종 정규식, 주의)
# 방향: "호재" = 관련 테마가 보통 기대감으로 오르는 이벤트, "악재" = 내리는 이벤트, "갈림" = 테마마다 다름
EVENTS = [
    dict(id="cn_warm", cat="한중·중국", name="한중 관계 개선 (정상회담·FTA·한한령 해제 기대)", dir="호재",
         why="한류·방한 중국인 관광·중국 소비 기대. 한한령 해제 기대가 커지면 엔터·화장품·면세·여행·카지노·게임이 먼저 움직이는 경향이 있어요.",
         main=[r"^엔터테인먼트$", r"^화장품$", r"^면세점$", r"^여행$", r"^카지노$", r"^영화$", r"영상콘텐츠", r"^웹툰$", r"^게임$", r"호텔/리조트", r"국내 상장 중국기업"],
         sub=[r"모바일게임", r"^광고$", r"모바일콘텐츠", r"미디어\(방송", r"^항공/저가", r"백화점", r"^패션", r"^의류"],
         ind=[r"방송과엔터", r"게임엔터", r"^화장품$", r"호텔,레스토랑,레저", r"^백화점"],
         note="기대감이 선반영돼 '소문에 사고 뉴스에 팔기'로 끝난 사례가 많아요. 협상 진전 ≠ 실제 규제 해제라서 발표 뒤 반납하기도 해요."),
    dict(id="cn_cold", cat="한중·중국", name="한중 관계 악화 (보복성 규제·한한령 재강화)", dir="악재",
         why="중국 소비·관광 의존도가 큰 엔터·화장품·면세·여행·카지노가 반대로 내려가는 경향이 있어요.",
         main=[r"^엔터테인먼트$", r"^화장품$", r"^면세점$", r"^여행$", r"^카지노$", r"호텔/리조트", r"국내 상장 중국기업"],
         sub=[r"^게임$", r"^영화$", r"^광고$"], ind=[r"방송과엔터", r"^화장품$", r"호텔,레스토랑,레저"],
         note="2016~17년 사드 보복 때가 대표 사례예요. 반응 날짜를 직접 기록해 크기를 확인해 보세요."),
    dict(id="cn_reopen", cat="한중·중국", name="중국 경기 부양·리오프닝·단체관광 재개", dir="호재",
         why="중국 내수·관광객이 늘 거라는 기대. 면세·화장품·여행·카지노·소비재.",
         main=[r"^면세점$", r"^화장품$", r"^여행$", r"^카지노$", r"호텔/리조트", r"국내 상장 중국기업"],
         sub=[r"백화점", r"^항공/저가", r"^의류", r"^패션", r"^음식료"], ind=[r"^화장품$", r"호텔,레스토랑,레저", r"^백화점"],
         note="중국 소비 지표가 다음 달 숫자로 확인돼야 의미가 있어요."),
    dict(id="kcontent", cat="한류·콘텐츠", name="K-콘텐츠 해외 흥행·대형 공연·수상", dir="호재",
         why="소속 아티스트·제작사·굿즈·관광에 대한 기대. 개별 이벤트라 기획사·콘텐츠 중심.",
         main=[r"^엔터테인먼트$", r"영상콘텐츠", r"^웹툰$", r"^영화$", r"^게임$"], sub=[r"모바일콘텐츠", r"^광고$", r"^화장품$", r"^여행$"],
         ind=[r"방송과엔터", r"게임엔터"], note="종목별(소속 아티스트)로 크게 갈려서 테마 평균이 의미가 작아요."),
    dict(id="jp_warm", cat="한일·북한", name="한일 관계 개선", dir="호재",
         why="일본 관광·교류 확대 기대. 여행·항공·엔터가 반응하는 경향.",
         main=[r"^여행$", r"^항공/저가", r"^엔터테인먼트$"], sub=[r"^면세점$", r"호텔/리조트", r"^화장품$"], ind=[r"항공사"],
         note="일본 불매 때는 반대 (일본 의존 소비재가 내리고 국산 대체재가 오름)."),
    dict(id="nk_warm", cat="한일·북한", name="남북 관계 개선·경협 기대", dir="호재",
         why="대북 경협·철도·SOC·비료·시멘트 등 남북경협 테마. 이벤트가 끝나면 빠르게 식는 경향.",
         main=[r"남북경협", r"^철도$", r"시멘트", r"^비료$", r"해저터널", r"전후 재건"], sub=[r"^건설 대표주", r"^철강", r"^전선"], ind=[r"^건설$"],
         note="기대만으로 단기 급등락을 반복해서 변동성이 매우 커요. 방산은 반대로 내릴 수 있어요."),
    dict(id="nk_cold", cat="한일·북한", name="북한 도발·지정학 긴장", dir="갈림",
         why="방산·우주·보안은 오르고 여행·항공·소비는 내리는 경향.",
         main=[r"방위산업", r"우주항공", r"^드론$"], sub=[r"^전선", r"재난/안전", r"CCTV"], ind=[r"우주항공과국방"],
         note="피해 쪽(여행·항공·내수)은 일부러 목록에 넣지 않았어요. 필요하면 직접 기록으로 확인하세요."),
    dict(id="us_cn_trade", cat="미중·무역", name="미중 갈등·관세 부과·수출 규제", dir="갈림",
         why="반도체·희토류·2차전지 공급망, 수출주는 내리고 국산화·대체 수혜 테마는 오르는 경향.",
         main=[r"희귀금속", r"반도체 재료", r"반도체 장비", r"^철강", r"^전기차$", r"^2차전지"], sub=[r"자동차 대표주", r"^조선$", r"^해운$"], ind=[r"반도체와반도체장비", r"^자동차$"],
         note="같은 이벤트가 업종마다 호재/악재가 갈려서 방향을 정해 두지 않았어요. 반응을 보고 판단하세요."),
    dict(id="trade_deal", cat="미중·무역", name="무역 합의·관세 인하 (한미·한중 FTA 등)", dir="호재",
         why="수출 장벽이 낮아지는 업종(자동차·반도체·소비재·농산물 등)이 기대감으로 움직여요.",
         main=[r"자동차 대표주", r"^화장품$", r"^조선$", r"^철강", r"^의류", r"^수산$"], sub=[r"^해운$", r"^면세점$", r"^엔터테인먼트$"], ind=[r"^자동차$", r"^화장품$"],
         note="FTA는 협상 단계마다(타결·서명·발효) 날짜가 달라서 단계별로 기록하는 게 좋아요."),
    dict(id="rate_cut", cat="거시", name="금리 인하·인하 기대", dir="호재",
         why="이자 부담 큰 건설·리츠·성장주·증권이 기대감으로 오르는 경향.",
         main=[r"^증권$", r"^리츠", r"건설 대표주", r"건설 중소형", r"^바이오", r"제약업체"], sub=[r"^은행$"], ind=[r"^증권$", r"^건설$"],
         note="은행은 예대마진 때문에 반대일 수 있어요."),
    dict(id="fx_up", cat="거시", name="환율 급등 (원화 약세)", dir="갈림",
         why="수출주(자동차·반도체·조선)는 환율 수혜, 수입 비중 큰 내수·항공·정유는 부담.",
         main=[r"자동차 대표주", r"^조선$", r"^반도체 대표주"], sub=[r"^화장품$", r"^엔터테인먼트$"], ind=[r"^자동차$"],
         note="항공·정유·여행 같은 피해 업종은 사전에 넣지 않았어요."),
    dict(id="oil_up", cat="거시", name="유가 급등", dir="갈림",
         why="정유·가스·자원개발은 수혜, 항공·해운·화학은 비용 부담.",
         main=[r"^정유$", r"^LNG", r"자원개발", r"석유화학"], sub=[r"^해운$", r"^항공/저가"], ind=[r"^석유와가스$"],
         note="방향이 갈리므로 반응 기록으로 확인하세요."),
    dict(id="election", cat="정치·정책", name="선거·정권 교체·정책 공약", dir="갈림",
         why="정치인 테마(인맥주)와 공약 관련 정책 테마. 정책 테마는 입법 영향 기업 탭과 함께 보세요.",
         main=[r"^남북경협", r"^건설 대표주", r"^원자력발전$", r"신재생", r"^태양광", r"^풍력"], sub=[r"^밸류업", r"^지주사$"], ind=[],
         note="'정치인 테마'는 근거가 약한 경우가 많아 사전에 넣지 않았어요."),
    dict(id="war_pandemic", cat="돌발", name="전쟁·감염병·재난 같은 돌발 이벤트", dir="갈림",
         why="방산·백신·마스크·재난안전 테마가 급등하고 여행·항공·소비는 급락하는 경향.",
         main=[r"방위산업", r"우주항공", r"재난/안전", r"^백신", r"^마스크", r"진단키트", r"조류독감"], sub=[r"^의료기기", r"CCTV"], ind=[],
         note="일회성이라 날짜가 뚜렷해서 '이후 20일'이 빠르게 반납되는지 보세요."),
]
EVENT_BY_ID = {e["id"]: e for e in EVENTS}
SUGGEST = [      # 근거를 확인한 과거 이벤트 (날짜는 보도 기준). 오늘의 이벤트는 직접 입력.
    ("cn_warm", "2025-11-03", "한중 정상회담 (경주, 11/1) 이후 첫 거래일", "한한령 해제·FTA 2단계 기대감에 화장품주 강세 보도 (뉴시스 2025-10-30)"),
    ("cn_warm", "2026-01-05", "한중 정상회담 (베이징)", "엔터·게임·뷰티주 매수세 보도 (뉴시스 2026-01-02, 정상회담은 1/5)"),
]


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, d TEXT, event_id TEXT, title TEXT, note TEXT, added TEXT)")


def _rx(pats, name):
    return any(re.search(p, name) for p in pats)


def groups(ev, data):
    """이벤트 사전 → 맞는 테마·업종. [(종류, 이름, 역할 'main'|'sub', [코드...])] (사전에 있어도 현재 네이버 목록에 없으면 빠짐)"""
    out = []
    for kind, key, role, pats in (("테마", "테마", "main", ev["main"]), ("테마", "테마", "sub", ev["sub"]), ("업종", "업종", "main", ev["ind"])):
        for name, codes in (data.get(key) or {}).items():
            if _rx(pats, name) and not any(g[1] == name and g[0] == kind for g in out):
                out.append((kind, name, role, list(codes)))
    return out


def members(ev, data, top=40):
    """→ [(코드, 이름, 점수, [근거 테마...])]. 작은 테마(구체적)와 주요 테마일수록 높게. 점수는 순서용이에요."""
    sc, why = {}, {}
    for kind, name, role, codes in groups(ev, data):
        w = (3.0 if role == "main" else 1.2) * (1.0 if kind == "테마" else 0.6) / (1 + len(codes) / 40.0)
        for c in codes:
            sc[c] = sc.get(c, 0) + w
            why.setdefault(c, []).append(name)
    names = data.get("이름", {})
    ranked = sorted(sc, key=lambda c: (-sc[c], c))[:top]
    return [(c, names.get(c, c), round(sc[c], 2), why[c][:4]) for c in ranked]


def add(conn, d, event_id, title, note=""):
    import datetime as dt
    ensure(conn)
    if event_id not in EVENT_BY_ID:
        return False, "이벤트 종류를 골라 주세요."
    try:
        dt.date.fromisoformat(d)
    except ValueError:
        return False, "날짜를 YYYY-MM-DD 로 넣어 주세요."
    title = (title or "").strip()[:80] or EVENT_BY_ID[event_id]["name"]
    if conn.execute("SELECT 1 FROM event_log WHERE d=? AND event_id=? AND title=?", (d, event_id, title)).fetchone():
        return False, "이미 같은 기록이 있어요."
    conn.execute("INSERT INTO event_log (d, event_id, title, note, added) VALUES (?,?,?,?,?)",
                 (d, event_id, title, (note or "").strip()[:300], dt.date.today().isoformat()))
    conn.commit()
    return True, f"{d} '{title}' 기록을 추가했어요."


def log_rows(conn):
    try:
        return [dict(id=r[0], d=r[1], event_id=r[2], title=r[3], note=r[4]) for r in
                conn.execute("SELECT id, d, event_id, title, note FROM event_log ORDER BY d DESC, id DESC")]
    except sqlite3.OperationalError:
        return []


def reactions(conn, d, codes):
    """이벤트 날짜 d × 종목들 → [dict(code, name, has_prices, r)] (r: legis.reaction_ext 결과 또는 None)"""
    import legis as lg
    import stock_market
    out = []
    idx_cache = {}
    for code, name in codes:
        px = lg.price_series(conn, code)
        mk = lg.market_of(conn, code) or "KOSPI"
        if mk not in idx_cache:
            idx_cache[mk] = list(stock_market.series(conn, "idx:KOSDAQ" if mk == "KOSDAQ" else "idx:KOSPI"))
        r = lg.reaction_ext(px, idx_cache[mk], d) if px else None
        out.append(dict(code=code, name=name, has_prices=bool(px), r=r))
    return out


def summarize(rs):
    """종목별 반응 목록 → 키별 평균·중앙값·플러스 비율"""
    out = {}
    for k in ("ab5pre", "ab1", "ab5", "ab20", "since"):
        v = sorted(x["r"][k] for x in rs if x["r"] and x["r"].get(k) is not None)
        if v:
            out[k] = dict(n=len(v), mean=sum(v) / len(v), med=v[len(v) // 2], up=sum(1 for a in v if a > 0) / len(v))
    return out
