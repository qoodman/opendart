# -*- coding: utf-8 -*-
"""
법안 → 영향을 받을 수 있는 기업 짐작.

방법 (모두 근거를 같이 보여 줘요. 사람이 확인하는 후보 목록이지 결론이 아니에요):
  ① 기업명 언급: 법안 제목·제안이유에 상장사 이름이 그대로 나오면 (이름 3글자 이상) 가장 강한 근거
  ② 이름 일치: 법안 문구에 테마(면세점·화장품·밸류업 …)나 업종(은행·반도체와반도체장비 …) 이름이 그대로 나오면 그 테마·업종의 종목들
  ③ 개념 사전: 법안 문구의 말(예: '의료관광', '사후면세', '공매도')을 관련 테마·업종 이름으로 이어 주는 사람이 쓴 사전(CONCEPTS). 틀리거나 빠진 연결이 있을 수 있어요.
점수: 테마·업종이 작고 구체적일수록 높게(큰 테마는 낮게), 근거가 여러 개일수록 높게. 점수는 순서를 보는 용도지 확률이 아니에요.
법안 제목이 '○○법 일부개정법률안'처럼 평범하면 제안이유 본문이 있어야 잘 맞아요 (본문이 없으면 제목만으로 찾아서 약해요).
호재인지 악재인지는 판단하지 않아요 (같은 법안이 어떤 기업엔 호재, 어떤 기업엔 악재일 수 있어요).
"""
import math
import re

import theme_map

# (개념 이름, 법안 문구 정규식, 주요 테마 정규식들, 보조 테마 정규식들, 업종 정규식들)
# 주요 = 그 개념과 직접 맞는 테마(가중 3.0), 보조 = 느슨하게 닿는 테마(1.2), 업종(1.0). 이름은 네이버 증권의 실제 테마·업종 이름 기준이에요.
CONCEPTS = [
    ("관광·면세", r"관광|면세|환급특례|택스\s*리펀|사후면세|외국인\s*관광객|입국|관광진흥", [r"^면세점$", r"^여행$", r"호텔/리조트", r"카지노", r"테마파크"], [r"화장품", r"백화점", r"항공/저가", r"야놀자", r"엔터테인먼트"], [r"호텔,레스토랑,레저", r"백화점과일반상점", r"항공사"]),
    ("미용·성형·의료관광", r"의료관광|외국인\s*환자|미용|성형|피부|시술|보톡스|필러", [r"미용기기", r"보톡스", r"치아 치료"], [r"화장품", r"탈모 치료", r"의료기기"], [r"건강관리장비와용품"]),
    ("공매도·시장 제도", r"공매도|대차거래|무차입|불공정거래|시세조종|주가조작|거래시간|거래소|자본시장|금융투자", [r"^증권$"], [r"STO", r"스테이블코인", r"가상화폐", r"블록체인", r"두나무", r"핀테크"], [r"^증권$", r"창업투자", r"기타금융"]),
    ("주주환원·배당·밸류업", r"배당|자사주|자기주식|소각|주주환원|밸류업|기업가치\s*제고|분리과세", [r"밸류업\(2[45]", r"코리아 밸류업", r"^지주사$"], [r"^은행$", r"^증권$", r"생명보험", r"손해보험"], [r"^은행$", r"^증권$", r"복합기업", r"손해보험", r"생명보험"]),
    ("상법·지배구조", r"상법|충실의무|집중투표|감사위원|물적분할|합병비율|의무공개매수|지배구조|소액주주", [r"^지주사$", r"밸류업\(2[45]", r"부동산 보유 자산주"], [r"^증권$"], [r"복합기업"]),
    ("세제(증권·양도·상속)", r"증권거래세|금융투자소득|양도소득세|대주주|상속세|증여세|종합부동산세", [r"^증권$", r"밸류업\(2[45]"], [r"부동산 보유 자산주", r"리츠"], [r"^증권$", r"^부동산$"]),
    ("가상자산·토큰증권", r"가상자산|암호화폐|스테이블\s*코인|토큰증권|디지털자산|전자지갑|블록체인", [r"가상화폐", r"STO", r"스테이블코인", r"두나무", r"블록체인", r"NFT"], [r"화폐/금융자동화", r"전자결제", r"핀테크"], []),
    ("금융(은행·보험·카드·핀테크)", r"은행법|보험업법|여신전문|카드\s*수수료|전자금융|핀테크|마이데이터|신용정보|대출|금리\s*상한|예금자|간편결제", [r"^은행$", r"인터넷은행", r"카카오뱅크", r"핀테크", r"마이데이터", r"전자결제", r"생명보험", r"손해보험", r"토스"], [r"삼성페이", r"애플페이", r"^증권$"], [r"^은행$", r"손해보험", r"생명보험", r"^카드$", r"기타금융"]),
    ("부동산·주택·건설", r"부동산|주택|재건축|재개발|정비사업|분양|전세|임대|건설산업|종합부동산|리츠|GTX|리모델링", [r"건설 대표주", r"건설 중소형", r"리모델링", r"리츠", r"모듈러주택", r"GTX", r"부동산 보유 자산주"], [r"시멘트", r"아스콘", r"건설기계", r"페인트"], [r"^건설$", r"건축자재", r"^부동산$"]),
    ("반도체", r"반도체|첨단전략산업|칩스|파운드리|HBM|소부장", [r"반도체 대표주", r"^S7", r"시스템반도체", r"HBM", r"반도체 장비", r"반도체 재료"], [r"전력반도체", r"유리 기판", r"CXL", r"반도체 기판", r"소캠"], [r"반도체와반도체장비"]),
    ("AI·데이터센터", r"인공지능|AI\b|데이터센터|클라우드|생성형|딥페이크|알고리즘", [r"AI 챗봇", r"온디바이스 AI", r"데이터센터", r"클라우드", r"지능형로봇/인공지능", r"퓨리오사", r"딥페이크"], [r"냉각시스템", r"CXL", r"HBM", r"음성인식"], [r"^소프트웨어$", r"IT서비스"]),
    ("개인정보·보안", r"개인정보|정보보호|사이버|해킹|보안|바이오인식", [r"보안주\(정보\)", r"보안주\(물리\)", r"바이오인식", r"CCTV"], [], [r"^소프트웨어$"]),
    ("2차전지·전기차", r"이차전지|2차전지|배터리|전기차|충전|전지|리튬", [r"^2차전지", r"^전기차", r"폐배터리", r"^리튬$", r"^니켈$"], [r"전력저장장치", r"자율주행", r"희귀금속"], [r"전기장비", r"자동차부품"]),
    ("원전·전력", r"원자력|원전|SMR|소형모듈|전기사업|송전|배전|전력망|전기요금|한국전력|전력수급|전력산업", [r"^원자력발전$", r"원자력발전소 해체", r"스마트그리드", r"전력설비", r"^전선$"], [r"핵융합", r"전력저장장치", r"도시가스"], [r"전기유틸리티", r"전기장비", r"복합유틸리티"]),
    ("신재생·수소·탄소", r"태양광|풍력|수소|재생에너지|탄소중립|탄소배출|배출권|ESS|연료전지|CCUS", [r"태양광에너지", r"풍력에너지", r"수소에너지", r"고체산화물", r"온실가스", r"우주태양광"], [r"전력저장장치", r"폐배터리", r"요소수"], [r"에너지장비및서비스", r"전기장비"]),
    ("방산·우주·드론", r"방위산업|방산|국방|무기|우주|위성|항공우주|드론|UAM", [r"방위산업", r"우주항공산업", r"드론", r"UAM", r"스페이스X", r"항공기부품"], [r"전후 재건"], [r"우주항공과국방"]),
    ("조선·해운·항만", r"조선|선박|해운|항만|해양|물류|컨테이너|해저", [r"^조선$", r"조선기자재", r"^해운$", r"종합 물류", r"해저터널"], [], [r"^조선$", r"해운사", r"항공화물운송과물류"]),
    ("항공·철도·운송", r"항공사업|항공|공항|택배|운송|화물|여객|철도|운수|버스|해운법", [r"항공/저가", r"항공기부품", r"^철도$", r"GTX", r"종합 물류", r"렌터카"], [], [r"항공사", r"도로와철도운송", r"운송인프라"]),
    ("자동차·모빌리티", r"자동차|자율주행|모빌리티|택시|플랫폼\s*운송|운행", [r"자율주행차", r"스마트카", r"자동차 대표주", r"자동차부품", r"^전기차$", r"타이어", r"렌터카"], [], [r"^자동차$", r"자동차부품"]),
    ("통신·방송·미디어", r"통신|방송|단말기|이동통신|OTT|미디어|요금제|주파수|통신사|중계", [r"^통신$", r"통신장비", r"^5G", r"광통신", r"미디어\(방송/신문\)", r"유심"], [r"영상콘텐츠", r"스마트폰"], [r"무선통신서비스", r"통신장비", r"다각화된통신"]),
    ("게임·콘텐츠·엔터", r"게임|콘텐츠|웹툰|영화|음악|한류|K[- ]?컬처|e스포츠|저작권|문화산업|웹소설", [r"^게임$", r"모바일게임", r"엔터테인먼트", r"영상콘텐츠", r"웹툰", r"^영화$", r"음원/음반", r"캐릭터", r"메타버스"], [], [r"게임엔터", r"방송과엔터"]),
    ("플랫폼·유통", r"플랫폼|전자상거래|온라인|배달|입점|소상공인|유통|대형마트|편의점|가맹|프랜차이즈|대규모유통|유통산업", [r"쿠팡", r"마켓컬리", r"소매유통", r"편의점", r"^백화점$", r"홈쇼핑", r"토스", r"야놀자"], [], [r"인터넷과카탈로그소매", r"백화점과일반상점", r"전문소매", r"양방향미디어", r"식품과기본식료품소매"]),
    ("제약·바이오·의료", r"의약품|약사법|제약|바이오|신약|임상|건강보험|의료법|의료기기|병원|백신|비대면\s*진료|의대", [r"제약업체", r"바이오시밀러", r"면역항암제", r"비만치료제", r"원격진료", r"의료AI", r"유전자 치료제", r"줄기세포", r"백신"], [r"의료기기", r"건강기능식품"], [r"^제약$", r"생물공학", r"건강관리", r"생명과학"]),
    ("저출산·고령·돌봄", r"저출산|출산|육아|아동|보육|영유아|고령|노인|요양|돌봄|실버|치매", [r"출산장려정책", r"고령화 사회", r"^치매", r"엔젤산업"], [], [r"건강관리업체"]),
    ("교육", r"교육|학원|사교육|대학|입시|교과서|학교", [r"교육/온라인 교육"], [], [r"교육서비스"]),
    ("식품·농업·수산", r"식품|농업|농산물|비료|사료|축산|수산|쌀|양곡|식량|가축|식품위생|건강기능식품", [r"^농업$", r"^비료$", r"^사료$", r"음식료업종", r"육계", r"^수산$", r"콩/대두", r"아프리카 돼지열병", r"구제역"], [r"김밥", r"콜드체인"], [r"^식품$", r"^음료$"]),
    ("주류·담배·세금", r"금연|흡연|담배|음주|주류|주세|개별소비세", [r"주류업"], [], [r"^담배$", r"^음료$"]),
    ("환경·폐기물", r"폐기물|재활용|플라스틱|수처리|미세먼지|오염|환경", [r"폐기물처리", r"탈 플라스틱", r"수자원", r"황사/미세먼지", r"공기청정기", r"온실가스"], [], []),
    ("철강·화학·자원", r"철강|화학물질|석유화학|정유|석유|가스|비철|희토류|핵심광물|광물|니켈", [r"철강", r"석유화학", r"^정유$", r"LNG", r"희귀금속", r"비철금속", r"자원개발"], [r"^니켈$", r"^리튬$"], [r"^철강$", r"^화학$", r"비철금속", r"석유와가스"]),
    ("노동·안전", r"노동|근로|최저임금|중대재해|노란봉투|주\s*4\.?5일|근로시간|정년|산업안전|파업", [r"재난/안전", r"일자리", r"재택근무"], [r"스마트팩토리"], []),
    ("공정거래·하도급", r"공정거래|하도급|불공정|담합|갑질|납품대금|대기업집단|지주회사", [r"^지주사$"], [r"건설 중소형"], [r"복합기업"]),
    ("중소·벤처·상장", r"중소기업|벤처|스타트업|창업|신규상장|기업공개", [r"창투사", r"기업인수목적회사", r"신규상장"], [], [r"창업투자"]),
    ("카지노·레저·골프", r"카지노|도박|사행|복권|경마|레저|골프|테마파크", [r"카지노", r"^골프$", r"테마파크", r"스포츠행사"], [], [r"호텔,레스토랑,레저", r"레저용장비"]),
    ("보험·연금", r"국민연금|퇴직연금|연금|보험료|개인연금|실손", [r"생명보험", r"손해보험"], [r"^증권$"], [r"생명보험", r"손해보험"]),
    ("외국인·환율·지수편입", r"외국인\s*투자|외환|환율|외국환|MSCI|선진국지수|관찰대상국", [r"환율하락 수혜", r"^증권$"], [], [r"^증권$", r"^은행$"]),
    ("로봇·스마트팩토리", r"로봇|스마트공장|스마트팩토리|자동화", [r"^로봇", r"피지컬 AI", r"스마트팩토리", r"지능형로봇"], [], [r"^기계$", r"전기장비"]),
    ("양자·메타버스·신기술", r"양자|메타버스|가상현실|증강현실", [r"양자암호", r"메타버스", r"가상현실", r"증강현실"], [], []),
    ("건설·SOC·공공조달", r"사회기반시설|SOC|공공조달|조달|건설공사|도로|댐|항만|지하|시설물", [r"건설 대표주", r"건설 중소형", r"해저터널", r"시멘트/레미콘", r"아스콘"], [r"^철도$"], [r"^건설$"]),
    ("패션·화장품", r"패션|의류|섬유|화장품|뷰티|명품|화장품법", [r"패션/의류", r"^화장품$"], [], [r"섬유,의류", r"^화장품$"]),
    ("광고·언론·출판", r"광고|언론|신문|출판|도서|간행물|포털", [r"^광고$", r"미디어\(방송/신문\)"], [], [r"^광고$", r"^출판$"]),
    ("재난·소방·치안", r"재난|소방|방재|CCTV|경호|경비|지진|화재", [r"재난/안전", r"CCTV", r"보안주\(물리\)"], [r"전기차 화재"], []),
]
STOP_NAMES = {"한국", "대한", "아이", "에이", "삼성", "현대", "엘지", "에스", "케이", "디지털", "코리아", "글로벌", "인터넷", "테크", "홀딩스", "그룹"}


def _norm(t):
    return re.sub(r"\s+", "", t or "")


def _size_factor(n):
    """작고 구체적인 테마·업종일수록 조금 높게: 10종목 이하 1.0, 100종목 0.67, 1000종목 0.5"""
    return 1.0 / (1.0 + 0.5 * max(0.0, math.log10(max(n, 1) / 10.0)))


def _theme_name_tokens(name):
    """테마 이름에서 글에 그대로 나올 만한 덩어리: '밸류업(24년 …)' → 밸류업 / '호텔/리조트' → 호텔, 리조트"""
    base = re.sub(r"\(.*?\)", " ", name)
    parts = [p.strip() for p in re.split(r"[/·,]", base) if p.strip()]
    return [p for p in parts if len(_norm(p)) >= 2 and re.search(r"[가-힣A-Za-z]", p)]


_CONCEPT_RE = None


def _concepts():
    """CONCEPTS의 정규식을 한 번만 컴파일"""
    global _CONCEPT_RE
    if _CONCEPT_RE is None:
        _CONCEPT_RE = [(n, re.compile(t), [re.compile(r) for r in p], [re.compile(r) for r in sec], [re.compile(r) for r in ind])
                       for n, t, p, sec, ind in CONCEPTS]
    return _CONCEPT_RE


def _prep(data):
    """테마·업종 자료에서 법안마다 되풀이되던 계산을 한 번만 (data 안에 저장해 재사용):
    이름 토큰, 개념별로 맞는 테마·업종, 기업명 정규화, 그룹 크기"""
    pr = data.get("_prep")
    if pr is not None:
        return pr
    toks = []
    for kind, table in (("테마", data["테마"]), ("업종", data["업종"])):
        for name in table:
            ts = [(tok, _norm(tok)) for tok in _theme_name_tokens(name) if len(_norm(tok)) >= 2]
            if ts:
                toks.append((kind, name, ts))
    concept_groups = []
    for cname, trig, primary, secondary, inds in _concepts():
        prim, sec_, ind_ = [], [], []
        for name in data["테마"]:
            if any(r.search(name) for r in primary):
                prim.append(name)
            elif any(r.search(name) for r in secondary):
                sec_.append(name)
        for name in data["업종"]:
            if any(r.search(name) for r in inds):
                ind_.append(name)
        concept_groups.append((cname, trig, prim, sec_, ind_))
    mention = []
    for code, nm in data["이름"].items():
        n = _norm(nm)
        if len(n) >= 3 and n not in STOP_NAMES:
            mention.append((code, nm, n))
    sizes = {("테마", n): len(c) for n, c in data["테마"].items()} | {("업종", n): len(c) for n, c in data["업종"].items()}
    pr = dict(toks=toks, concept_groups=concept_groups, mention=mention, sizes=sizes)
    data["_prep"] = pr
    return pr


def analyze(conn, title, text="", top=20, data=None):
    """→ dict(concepts, groups, members, stocks, has_text, note)
    concepts: [(개념, [맞은 문구])]   groups: [(종류, 이름, 근거 문구, 종목 수, 점수)]
    stocks: [{code, name, score, evidence:[문구...], groups:[이름...]}] 점수 높은 순"""
    data = data or theme_map.load(conn)
    body = f"{title or ''} {text or ''}"
    nb = _norm(body)
    out = dict(concepts=[], groups=[], stocks=[], members=[], has_text=bool((text or "").strip()), note="")
    if not data["테마"] and not data["업종"]:
        out["note"] = "테마·업종 자료가 아직 없어요 (지금 수집 → 시장·정책만, 또는 설정에서 테마 받기)"
        return out
    pr = _prep(data)
    ev = {}                                  # (종류, 이름) → [(가중치, 근거)]

    def add(kind, name, weight, why):
        ev.setdefault((kind, name), []).append((weight, why))
    # ② 이름 일치: 긴 이름은 강하게, 짧은 일반어('안전'·'증권' 같은 2글자)는 약하게
    for kind, name, ts in pr["toks"]:
        for tok, n_tok in ts:
            if n_tok in nb:
                n = len(n_tok)
                add(kind, name, (3.0 if n >= 3 else 1.5) * (1.0 if kind == "테마" else 0.7), f"'{tok}' 문구")
                break
    # ③ 개념 사전: 주요 테마 3.0 · 보조 1.2 · 업종 1.0 (맞은 문구가 여러 가지면 최대 30% 가산)
    for cname, trig, prim, sec_, ind_ in pr["concept_groups"]:
        hits = sorted({m.group(0) for m in trig.finditer(body)})
        if not hits:
            continue
        out["concepts"].append((cname, hits[:6]))
        strength = min(1.3, 1.0 + 0.1 * (len(hits) - 1))
        why = f"개념 '{cname}' ({', '.join(hits[:3])})"
        for name in prim:
            add("테마", name, 3.0 * strength, why)
        for name in sec_:
            add("테마", name, 1.2 * strength, why)
        for name in ind_:
            add("업종", name, 1.0 * strength, why)
    sizes = pr["sizes"]
    stock = {}
    for (kind, name), items in ev.items():
        items.sort(key=lambda x: -x[0])
        ws = [w for w, _ in items]
        gw = (ws[0] + 0.5 * sum(ws[1:3])) * _size_factor(sizes[(kind, name)])
        out["groups"].append((kind, name, items[0][1], sizes[(kind, name)], round(gw, 2)))
        table = data["테마"] if kind == "테마" else data["업종"]
        for code in table.get(name, []):
            s = stock.setdefault(code, dict(code=code, name=data["이름"].get(code, code), gws=[], evidence=[], groups=[], score=0.0))
            s["gws"].append((gw, f"{kind} {name} ← {items[0][1]}"))
            s["groups"].append(name)
    # 종목 점수 = 가장 강한 근거 + 나머지 두 개의 20% (여러 약한 테마에 걸친 종목이 직접 관련 종목을 이기지 못하게)
    for s in stock.values():
        s["gws"].sort(key=lambda x: -x[0])
        g = [x[0] for x in s["gws"]]
        s["score"] = g[0] + 0.2 * sum(g[1:3])
        s["evidence"] = [x[1] for x in s["gws"][:3]]
    # ① 기업명 언급
    for code, nm, n in pr["mention"]:
        if n in nb:
            s = stock.setdefault(code, dict(code=code, name=nm, gws=[], evidence=[], groups=[], score=0.0))
            s["score"] += 6.0
            s["evidence"].insert(0, f"법안 문구에 기업명 '{nm}'이 나와요")
    out["groups"].sort(key=lambda g: -g[4])
    # 점수 순위에서 밀려도 놓치지 않게: 가장 맞는 작은 테마·업종의 구성 종목 전체를 따로 (이름 목록)
    out["members"] = []
    for kind, name, why, size, gw in out["groups"]:
        if len(out["members"]) >= 6 or gw < 1.5 or size > 60:
            continue
        table = data["테마"] if kind == "테마" else data["업종"]
        out["members"].append(dict(kind=kind, name=name, why=why, score=gw, size=size,
                                   stocks=[(c, data["이름"].get(c, c)) for c in table.get(name, [])][:30]))
    out["stocks"] = sorted(stock.values(), key=lambda s: (-s["score"], s["name"]))[:top]
    for s in out["stocks"]:
        s["score"] = round(s["score"], 2)
        s["groups"] = list(dict.fromkeys(s["groups"]))
        s.pop("gws", None)
    if not out["has_text"]:
        out["note"] = "제안이유 본문이 없어서 제목만으로 찾았어요. 본문을 가져오거나 아래에 붙여 넣으면 더 정확해요."
    if not out["stocks"]:
        out["note"] = (out["note"] + " " if out["note"] else "") + "맞는 개념·테마가 없어요 (개념 사전에 없는 분야일 수 있어요)."
    return out
