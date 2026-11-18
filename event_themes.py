# -*- coding: utf-8 -*-
"""
테마주 ↔ 이벤트 사전.
'어떤 이벤트가 생기면 어떤 테마주가 뜨는가'를 테마(네이버 증권 기준) 쪽에서 보여 줘요.
- EVENT_DEFS: 이벤트 종류 (분류, 이름, 설명, 주의).
- RULES: 테마 이름 정규식 → 그 테마가 반응하는 이벤트들 (부호 '+' 오르는 쪽 · '-' 내리는 쪽 · '±' 방향 갈림, 역할 main=직접/sub=간접).
  모두 사람이 쓴 가설이라 틀리거나 빠진 연결이 있을 수 있어요. 진짜 반응은 날짜별 기록(event_log)으로 확인해요.
- 반응: 이벤트 날짜 직전 종가 기준 사전 5일, 이후 1·5·20일 지수 대비 초과수익(%p).
- 선반영·같은 날 다른 소식·시장 전체 움직임이 섞이므로 인과를 말하지 않아요. 투자 권유가 아니에요.
"""
import re
import sqlite3

# id: (분류, 이름, 설명, 주의)
EVENT_DEFS = {
 "cn_warm": ("한중·한일·북한", "중국과의 관계 개선", "한류·중국 관광·중국 소비 기대감", "기대가 먼저 반영돼 발표 뒤 되돌려지는 일이 많아요. 합의 진전이 곧 규제 해제는 아니에요."),
 "cn_cold": ("한중·한일·북한", "중국과의 갈등", "중국 소비·관광 의존 업종이 내려가는 방향", "과거 갈등 시기에 크게 내린 업종이 같은 방향으로 다시 반응하는 경향."),
 "cn_reopen": ("한중·한일·북한", "중국 경기 부양·방한 관광객 증가", "면세·화장품·호텔·카지노 등 인바운드 소비", "중국 소비 지표로 확인돼야 의미가 커요."),
 "jp_warm": ("한중·한일·북한", "일본과의 관계 개선·관광 교류 확대", "여행·항공·엔터 교류 기대", "갈등 시기에는 반대 방향."),
 "jp_conflict": ("한중·한일·북한", "일본과의 갈등 (수출 규제·불매)", "일본 의존 소재·부품 국산화, 대체재 수혜", "단기에 급등했다 식는 경향."),
 "nk_warm": ("한중·한일·북한", "남북 화해·경제협력", "경협·철도·SOC·평화공원·가스관", "이벤트가 끝나면 빠르게 식고 변동성이 커요."),
 "nk_cold": ("한중·한일·북한", "북한 도발·지정학 긴장", "방산·우주·보안·안전 테마는 오르는 방향", "여행·항공·내수는 반대로 내릴 수 있어요."),
 "war": ("전쟁·재난", "전쟁·테러 발발·확전", "방산·드론·에너지·안보 테마", "급등 뒤 20일 안에 반납하는 경우가 많아요."),
 "war_end": ("전쟁·재난", "휴전·종전 협상·재건 기대", "건설기계·시멘트·철강·인프라·전후 재건", "방산은 반대로 내릴 수 있어요."),
 "disaster": ("전쟁·재난", "지진·화재·대형 사고", "재난안전·CCTV·소방·보안 테마", "일회성이라 빠르게 식어요."),
 "hacking": ("전쟁·재난", "해킹·개인정보 유출 사고", "정보보안·바이오인식·보안 테마", "사고 직후 며칠만 반응하는 경우가 많아요."),
 "us_cn_trade": ("무역·거시", "강대국 간 통상 갈등 (관세·수출 규제)", "공급망 국산화·희토류·반도체 소부장은 오르고 수출주는 내리는 방향", "업종마다 호재/악재가 갈려요."),
 "trade_deal": ("무역·거시", "무역 합의·관세 인하·FTA 진전", "수출 장벽이 낮아지는 업종 기대", "협상 단계별 날짜가 달라요."),
 "rate_cut": ("무역·거시", "금리 인하·인하 기대", "이자 부담 큰 건설·리츠·성장주·증권·핀테크", "은행은 예대마진 때문에 반대일 수 있어요."),
 "rate_up": ("무역·거시", "금리 인상·고금리 장기화", "은행·보험 등 금융은 오르고 성장주·건설은 내리는 방향", "시장 전체 영향이 커서 개별 테마 효과가 묻혀요."),
 "fx_up": ("무역·거시", "환율 급등 (원화 약세)", "수출주 수혜", "수입 비중 큰 항공·정유·여행은 반대."),
 "fx_down": ("무역·거시", "환율 하락 (원화 강세)", "수입·내수·여행 비용 개선", "수출주는 반대."),
 "oil_up": ("무역·거시", "국제 유가·가스 가격 급등", "정유·가스·자원개발·셰일", "항공·해운·화학은 비용 부담."),
 "metal_up": ("무역·거시", "금속·원자재 가격 급등", "귀금속·비철금속·니켈·희토류", "가격 방향을 따라 움직이는 테마예요."),
 "lithium_up": ("무역·거시", "배터리 소재 가격 반등", "리튬·니켈·2차전지 소재", "가격 반등이 확인되기 전 기대감 위주."),
 "val_up": ("정책", "주주환원·지배구조 개선 정책", "저PBR 지주사·금융·증권·자산주", "정책 발표 전 기대로 선반영되는 편이에요. 입법 주가 분석 탭과 함께 보세요."),
 "ai_policy": ("정책", "AI·반도체 지원 정책·국가 투자", "AI·데이터센터·반도체·클라우드", "예산 규모가 확정되기 전 기대감 위주."),
 "energy_policy": ("정책", "에너지 정책 발표 (원전·재생에너지·전력망)", "원전·태양광·풍력·수소·ESS·전력설비", "정권·정책 방향에 민감해요."),
 "housing_policy": ("정책", "부동산 대책·공급 확대·재건축 정책", "건설·시멘트·리모델링·철도·리츠", "규제 강화 대책이면 반대."),
 "crypto_policy": ("정책", "가상자산 규제 완화·상품 승인·입법", "가상화폐·STO·블록체인·두나무·NFT", "코인 가격과 같이 움직여요."),
 "bio_policy": ("정책", "의료·약가·제약 정책 변화", "원격진료·의료기기·제약", "정책 방향에 따라 호재/악재가 갈려요."),
 "baby_policy": ("정책", "저출산·고령·돌봄 대책 발표", "출산·육아·실버·치매 테마", "예산이 구체화돼야 의미가 있어요."),
 "defense_budget": ("정책", "국방 예산 확대·무기 수출 계약", "방위산업·우주·드론", "수주 공시로 확인돼요."),
 "election": ("정책", "선거·정권 교체·공약 발표", "공약 관련 정책 테마·지역화폐·일자리", "정치인 연관 테마는 근거가 약해요."),
 "budget_soc": ("정책", "추경·SOC 예산 확대·지역 개발", "건설·시멘트·아스콘·철도·해저터널·지역화폐", "예산이 실제 집행돼야 실적으로 이어져요."),
 "nvidia_ai": ("기술·산업", "글로벌 빅테크의 AI 투자·실적 발표", "AI 반도체·HBM·서버·냉각·데이터센터·전력", "대형주 선반영이 크고 중소형 테마는 뒤따라 움직여요."),
 "memory_up": ("기술·산업", "메모리 가격 상승·고대역폭 메모리 공급 계약", "HBM·소캠·CXL·반도체 장비·소재", "가격 지표 확인이 중요해요."),
 "device_launch": ("기술·산업", "대형 스마트기기 신제품 공개·출시", "부품·카메라·디스플레이·MLCC·무선충전", "출시 몇 달 전 기대로 오르고 출시 뒤 식는 경향."),
 "ev_news": ("기술·산업", "전기차 수요·보조금·신차 공개", "전기차·충전·자동차부품·배터리", "보조금 축소면 반대."),
 "battery_news": ("기술·산업", "배터리 신기술 발표·대형 수주", "2차전지 소재·장비·전고체·나트륨", "수주 공시 전 기대감 위주."),
 "robot_news": ("기술·산업", "로봇·휴머노이드 신제품·양산 발표", "로봇·피지컬AI·스마트팩토리", "발표 후 빠르게 식는 경향."),
 "space_news": ("기술·산업", "우주 발사·위성 사업 이벤트", "우주항공·위성·UAM·우주태양광", "발사 성공/실패에 따라 갈려요."),
 "tech_hype": ("기술·산업", "신기술 화제 (양자·초전도체·핵융합 등)", "양자·초전도체·핵융합·그래핀", "루머성 급등이 많아 변동성이 매우 커요."),
 "game_content": ("기술·산업", "대형 콘텐츠 흥행·게임 출시·공연", "게임·웹툰·영화·엔터·음원·캐릭터", "개별 작품/아티스트 중심이라 테마 평균은 의미가 작아요."),
 "platform_news": ("기술·산업", "플랫폼·핀테크 규제 변화", "핀테크·마이데이터·인터넷은행·전자결제·토스·쿠팡", "규제 강화면 반대."),
 "clinical": ("바이오", "임상 결과·기술수출 계약·허가 승인", "신약·면역항암·바이오시밀러·유전자·줄기세포", "발표 직전 기대감으로 오르고 결과 뒤 빠지는 경우가 많아요."),
 "glp1": ("바이오", "신약 시장 개화 이슈 (대형 제약사 실적·승인)", "비만치료제·제약·건강기능식품", "해외 기업 이벤트에 동조해요."),
 "pandemic": ("바이오", "감염병 확산", "백신·진단키트·마스크·방역·음압병실", "확산 초기에만 크게 움직이는 경향이 있어요."),
 "animal_disease": ("바이오", "가축 전염병 발생", "백신·사료·육계·방역 등", "육류 가격에 따라 수혜가 갈려요."),
 "weather": ("계절·행사", "이상 기후·계절 요인", "계절 테마 (난방·냉방·제습·공기청정·복구)", "기상 이벤트 직후 며칠만 반응하는 편이에요."),
 "sports_event": ("계절·행사", "대형 스포츠 행사", "스포츠행사·광고·미디어·치킨/주류", "개최 몇 달 전부터 선반영돼요."),
 "ipo": ("계절·행사", "신규상장 열풍·스팩 합병 발표", "신규상장·기업인수목적회사·창투사", "상장 직후 급등락이 커요."),
 "derivs_expiry": ("시장 일정", "선물·옵션 만기일", "만기일 전후 지수·대형주 변동성 확대, 프로그램 매매 영향", "테마와 직접 연결되진 않고 시장 전체에 영향을 줘요. 휴장일이면 앞당겨져요."),
 "auto_chip": ("기술·산업", "자율주행 규제 완화·상용화 발표", "자율주행·스마트카·전기차·UAM", "허가 일정 확인이 필요해요."),
}
# (테마 이름 정규식, [(이벤트 id, 부호 +/-, 역할 'main'|'sub')...])  — 위에서부터 모두 적용
RULES = [
 (r"^엔터테인먼트$|^음원/음반$|^영화$|^영상콘텐츠$|^웹툰$|^캐릭터상품$", [("cn_warm","+","main"),("cn_cold","-","main"),("game_content","+","main"),("jp_warm","+","sub")]),
 (r"^화장품$|^면세점$|^백화점$|호텔/리조트|^카지노$|^여행$|^테마파크$|^야놀자|^미용기기|^보톡스", [("cn_warm","+","main"),("cn_cold","-","main"),("cn_reopen","+","main")]),
 (r"^렌터카", [("rate_cut","+","sub"),("oil_up","-","sub"),("cn_reopen","+","sub")]),
 (r"^여행$|^항공/저가|^야놀자|^호텔/리조트", [("jp_warm","+","main"),("fx_down","+","sub"),("oil_up","-","sub"),("pandemic","-","sub")]),
 (r"국내 상장 중국기업", [("cn_warm","+","main"),("cn_cold","-","main"),("cn_reopen","+","main")]),
 (r"^패션/의류|^화학섬유|^소매유통|^편의점|^홈쇼핑|^음식료업종|^김밥", [("cn_reopen","+","sub"),("rate_cut","+","sub")]),
 (r"^게임$|^모바일게임|^모바일콘텐츠|^SNS|^광고$|^미디어|^메타버스|가상현실|증강현실", [("game_content","+","main"),("cn_warm","+","sub")]),
 (r"日 수출 규제|日제품 불매", [("jp_conflict","+","main"),("jp_warm","-","sub")]),
 (r"남북경협|DMZ 평화|남-북-러|^철도$|^비료$|^시멘트|^아스콘|해저터널", [("nk_warm","+","main"),("budget_soc","+","sub")]),
 (r"방위산업|^드론|^스페이스X|우주항공|^UAM|^CCTV|보안주\(물리\)", [("nk_cold","+","main"),("war","+","main"),("defense_budget","+","main")]),
 (r"방위산업|우주항공|^스페이스X|우주태양광", [("space_news","+","main")]),
 (r"전후 재건|건설기계|^건설 |^철강|^강관|^피팅|^시멘트|^페인트|^아스콘|^건설 대표", [("war_end","+","main"),("housing_policy","+","main")]),
 (r"^건설 |건설 대표|리모델링|모듈러|^리츠|^GTX|서울고속버스터미널|부동산 보유|^시멘트|^아스콘", [("housing_policy","+","main"),("rate_cut","+","main"),("budget_soc","+","sub"),("rate_up","-","sub")]),
 (r"^철도$|^GTX|해저터널|^건설|^시멘트|^아스콘|^철강 ", [("budget_soc","+","main")]),
 (r"재난/안전|^CCTV|^전자파|^수자원", [("disaster","+","main"),("weather","+","sub")]),
 (r"^보안주|바이오인식|^딥페이크|^유심|^양자암호", [("hacking","+","main")]),
 (r"반도체 대표|^S7|^HBM|^소캠|^CXL|^반도체 |^시스템반도체|^전력반도체|^뉴로모픽|^퓨리오사|^유리 기판|^MLCC|^PCB", [("nvidia_ai","+","main"),("memory_up","+","main"),("ai_policy","+","main"),("us_cn_trade","±","main")]),
 (r"^AI 챗봇|^온디바이스|^지능형로봇|^의료AI|^데이터센터|^클라우드|^냉각시스템|^광통신|^통신장비|^SI|^NI|^음성인식|^키오스크|^스마트홈", [("nvidia_ai","+","main"),("ai_policy","+","main")]),
 (r"^데이터센터|^전력설비|^전선|^스마트그리드|^전력저장|^원자력발전$|^냉각", [("nvidia_ai","+","sub"),("energy_policy","+","main")]),
 (r"^아이폰|^갤럭시|^스마트폰|^폴더블|^플렉서블|^OLED|^마이크로 LED|^카메라모듈|^모바일솔루션|^무선충전|^삼성페이|^애플페이|^LED|^IT 대표|^페라이트|^전자결제", [("device_launch","+","main")]),
 (r"^전기차|^2차전지|^리튬|^니켈|^폐배터리|^자동차|^타이어|^리비안|^스마트카|^자율주행|^전기차 화재", [("ev_news","+","main"),("battery_news","+","sub"),("us_cn_trade","±","sub")]),
 (r"^2차전지|^리튬|^니켈|^폐배터리|^LED장비|^그래핀|^탄소나노", [("battery_news","+","main"),("lithium_up","+","main")]),
 (r"^자율주행|^스마트카|^UAM|^전기차\(충전", [("auto_chip","+","main")]),
 (r"^로봇|^피지컬 AI|^스마트팩토리|^공작기계|^3D 프린터", [("robot_news","+","main"),("ai_policy","+","sub")]),
 (r"^양자|^초전도체|^핵융합|^그래핀|^탄소나노|^페라이트", [("tech_hype","+","main")]),
 (r"^원자력|^스마트그리드|^전력설비|^전선|^수소|^태양광|^풍력|^고체산화물|^온실가스|^ESS|^전력저장|^우주태양광|^도시가스|^LNG|^LPG|^셰일", [("energy_policy","+","main")]),
 (r"^LNG|^LPG|^셰일|^정유|^주유소|^석유화학|^윤활유|^자원개발|^도시가스|^요소수|^조선$|^해운|^항공", [("oil_up","±","main")]),
 (r"^귀금속|^비철금속|^희귀금속|^니켈|^철강|^자원개발|^종합상사", [("metal_up","+","main")]),
 (r"희귀금속|^반도체 재료|^반도체 장비|^2차전지\(소재|^조선|^자동차 대표|^철강", [("us_cn_trade","±","main"),("trade_deal","+","sub")]),
 (r"^자동차|^조선|^철강|^종합 물류|^해운|^화장품|^음식료|^수산|^농업|^종합상사|^패션", [("trade_deal","+","main"),("fx_up","+","sub")]),
 (r"^자동차 대표|^조선|^반도체 대표|^S7|^철강 주요|^자동차부품|^갤럭시|^스마트폰", [("fx_up","+","main")]),
 (r"^환율하락|^항공|^여행|^소매유통|^음식료|^사료|^제지|^주류|^콩|^정유", [("fx_down","+","main")]),
 (r"^은행$|^증권$|^생명보험|^손해보험|^창투사|^인터넷은행|^카카오뱅크|^토스|^핀테크|^지역화폐|^화폐/금융", [("rate_up","+","main"),("rate_cut","+","main"),("platform_news","+","main")]),
 (r"^증권$|^창투사|^기업인수목적|^지주사|^코리아 밸류업|^밸류업|^부동산 보유|^은행$|^생명보험|^손해보험", [("val_up","+","main")]),
 (r"^가상화폐|^스테이블코인|^STO|^블록체인|^NFT|^두나무|^마이데이터|^화폐/금융|^핀테크|^전자결제", [("crypto_policy","+","main")]),
 (r"^마이데이터|^핀테크|^인터넷은행|^카카오뱅크|^토스|^쿠팡|^마켓컬리|^야놀자|^두나무|^전자결제|^재택|^콜드체인|^인터넷 대표", [("platform_news","±","main")]),
 (r"^원격진료|^의료기기|^제약업체|^치아 치료|^의료AI|^미용기기|^건강기능식품", [("bio_policy","±","main")]),
 (r"^면역항암|^바이오시밀러|^유전자|^줄기세포|^마이크로바이옴|^오가노이드|^mRNA|^제대혈|^제약업체|^치매|^탈모|^보톡스|^비만치료제", [("clinical","+","main")]),
 (r"^비만치료제|^건강기능식품|^제약업체", [("glp1","+","main")]),
 (r"^백신|^코로나|^마스크|^마이코플라스마|^모더나|^화이자|^mRNA|^제습기|^공기청정기", [("pandemic","+","main")]),
 (r"^구제역|^아프리카 돼지|^육계|^사료|^콩|^수산|^백신", [("animal_disease","+","main")]),
 (r"^겨울|^여름|^제습기|^공기청정기|^황사|^태풍|^수자원|^조림|^농업|^수산|^골판지|^아이스|^음식료", [("weather","+","main")]),
 (r"^스포츠행사|^골프|^자전거|^광고$|^미디어|^음식료|^주류|^김밥|^캐릭터", [("sports_event","+","sub")]),
 (r"^스포츠행사", [("sports_event","+","main")]),
 (r"신규상장|^기업인수목적|^창투사|^엔젤산업", [("ipo","+","main")]),
 (r"^고령화|^출산장려|^엔젤산업|^치매|^일자리|^교육|^키오스크", [("baby_policy","+","main"),("election","+","sub")]),
 (r"^일자리|^지역화폐|^재택근무|^교육|^출산장려|^고령화", [("election","+","main")]),
 (r"^건설 대표|^전기차\(충전|^스마트그리드|^수소|^원자력발전소 해체", [("energy_policy","+","sub")]),
 (r"^제지|^폐기물|^탈 플라스틱|^석유화학|^화학섬유|^페인트|^윤활유|^골판지", [("oil_up","-","sub"),("trade_deal","+","sub")]),
 (r"^온실가스|^폐기물|^탈 플라스틱|^수자원", [("energy_policy","+","sub")]),
 (r"^통신$|^5G|^스마트폰|^유심|^통신장비", [("device_launch","+","sub"),("rate_up","+","sub")]),
]

EVENTS = [dict(id=k, cat=v[0], name=v[1], why=v[2], note=v[3]) for k, v in EVENT_DEFS.items()]
EVENT_BY_ID = {e["id"]: e for e in EVENTS}
CATS = list(dict.fromkeys(e["cat"] for e in EVENTS))
SUGGEST = []      # 예시는 두지 않아요 (이벤트는 직접 기록)

# 이미 공표된 앞으로의 일정. 날짜는 보도·공식 안내 기준이고 바뀔 수 있어서 출처를 같이 적어요 (확인일 2026-11-18).
CALENDAR = [
    dict(d="2026-12-08", end="2026-12-09", title="미국 FOMC 정례회의 (결과는 한국시간 12/10 새벽)", events=["rate_cut", "rate_up", "fx_up"],
         src="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", note="출처마다 날짜가 달라 연준 공식 일정으로 한 번 더 확인하세요."),
    dict(d="2026-12-14", end="2026-12-15", title="G20 정상회의 (미국 마이애미)", events=["trade_deal", "us_cn_trade"],
         src="https://biz.sbs.co.kr/amp/article/20000292334", note="미 재무부 발표 보도 기준."),
    dict(d="2027-01-06", end="2027-01-09", title="CES 2027 (라스베이거스)", events=["device_launch", "robot_news", "ev_news", "auto_chip", "nvidia_ai"],
         src="https://www.ces.tech/about", note="주최측 안내 기준."),
    dict(d="2027-03-14", end="2027-03-18", title="NVIDIA GTC 2027 (산호세)", events=["nvidia_ai", "memory_up", "robot_news"],
         src="https://www.nvidia.com/en-us/gtc/faq/", note="본 프로그램은 15~18일로 안내되기도 해요."),
]


def second_thursday(y, m):
    import datetime as dt
    d = dt.date(y, m, 1)
    return d + dt.timedelta(days=(3 - d.weekday()) % 7 + 7)


def upcoming(conn, today=None, days=150, law_rows=()):
    """앞으로 days일 안의 계획된 이벤트 → [dict(d, end, title, kind, events[id...], src, note)] 날짜순.
    kind: 공표 일정(CALENDAR) · 계산(선물옵션 만기) · 직접 입력(event_log의 미래 날짜) · 법 시행(law_rows: 법안 시행 예정)"""
    import datetime as dt
    today = today or dt.date.today()
    lo, hi = today.isoformat(), (today + dt.timedelta(days=days)).isoformat()
    out = []
    for c in CALENDAR:
        if (c["end"] or c["d"]) >= lo and c["d"] <= hi:
            out.append(dict(c, kind="공표 일정"))
    y, m = today.year, today.month
    for k in range(0, days // 28 + 2):
        yy, mm = y + (m - 1 + k) // 12, (m - 1 + k) % 12 + 1
        d = second_thursday(yy, mm).isoformat()
        if lo <= d <= hi:
            q = mm in (3, 6, 9, 12)
            out.append(dict(d=d, end="", title="선물·옵션 " + ("동시만기일 (분기)" if q else "만기일"), events=["derivs_expiry"], kind="계산", src="", note="매월 둘째 목요일 · 휴장이면 앞당겨져요"))
    for lr in log_rows(conn):
        if lo <= lr["d"] <= hi:
            out.append(dict(d=lr["d"], end="", title=lr["title"], events=[lr["event_id"]], kind="직접 입력", src="", note=lr["note"] or "", log_id=lr["id"]))
    for r in law_rows:
        out.append(r)
    return sorted(out, key=lambda x: (x["d"], x["title"]))

_RANK = {"main": 0, "sub": 1}


def theme_events(name):
    """테마 이름 → [dict(event_id, sign, role)] (강한 연결 먼저). 같은 이벤트는 가장 강한 연결 하나만."""
    best = {}
    for pat, evs in RULES:
        if not re.search(pat, name):
            continue
        for eid, sign, role in evs:
            cur = best.get(eid)
            if cur is None or _RANK[role] < _RANK[cur["role"]]:
                best[eid] = dict(event_id=eid, sign=sign, role=role)
            elif cur is not None and cur["sign"] != sign and role == cur["role"]:
                cur["sign"] = "±"
    return sorted(best.values(), key=lambda x: (_RANK[x["role"]], list(EVENT_DEFS).index(x["event_id"])))


def groups(ev, data):
    """이벤트 → 이 이벤트에 반응한다고 사전에 적힌 테마. [(종류, 이름, 역할, [코드...], 부호)]"""
    out = []
    for name, codes in (data.get("테마") or {}).items():
        for t in theme_events(name):
            if t["event_id"] == ev["id"]:
                out.append(("테마", name, t["role"], list(codes), t["sign"]))
    return out


def mcaps(conn):
    """KRX 시세의 최근 시가총액 {코드: 원}. 없으면 {}"""
    try:
        return {c: m for c, m in conn.execute("SELECT code, mktcap FROM krx_daily WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_daily)") if m}
    except sqlite3.Error:
        return {}


def members(ev, data, top=40, mcap=None):
    """→ [(코드, 이름, 점수, [근거 테마...])]. mcap이 있으면 시가총액 큰 순, 없으면 점수 순.
    점수는 작은 테마(구체적)와 직접 연결일수록 높게 — 순서용이에요."""
    sc, why = {}, {}
    for kind, name, role, codes, _sign in groups(ev, data):
        w = (3.0 if role == "main" else 1.2) / (1 + len(codes) / 40.0)
        for c in codes:
            sc[c] = sc.get(c, 0) + w
            why.setdefault(c, []).append(name)
    names = data.get("이름", {})
    if mcap:
        ranked = sorted(sc, key=lambda c: (-(mcap.get(c) or 0), -sc[c], c))
    else:
        ranked = sorted(sc, key=lambda c: (-sc[c], c))
    return [(c, names.get(c, c), round(sc[c], 2), why[c][:4]) for c in ranked[:top]]


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, d TEXT, event_id TEXT, title TEXT, note TEXT, added TEXT)")


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
