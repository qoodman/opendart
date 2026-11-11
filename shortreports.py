# -*- coding: utf-8 -*-
"""
공매도 리포트 모음: 공매도 투자자(숏셀러)가 낸 '이 회사는 이상하다'는 조사 보고서를 나라별(한국·중국·일본·미국)로 요약해 둡니다.

꼭 알아둘 점
- 숏셀러는 주가가 내려야 돈을 버는 사람이라, 보고서는 '이해관계 있는 쪽의 주장'이에요. 맞을 때도, 틀릴 때도 있어요.
  그래서 카드마다 주장 · 회사 반응 · 주가 반응을 나눠 적고, 사실로 확정된 것처럼 쓰지 않아요.
- 이 모듈은 인터넷을 읽지 않아요. 기본 항목은 언론 보도 요약을 보고 적은 것이라 원문 보고서를 직접 확인한 게 아니에요
  ('확인 필요' 표시가 있는 것은 더 불확실). 원문 주소가 있으면 꼭 원문을 읽어 보세요.
- 새 보고서가 나오면 아래 '직접 추가'로 적어 두세요. 한국 상장사 대상 외국 숏 리포트는 아직 확인된 게 없어서 한국은 비어 있거나 얇아요.
"""
import datetime as dt
import re
import sqlite3

REGIONS = ["한국", "중국", "일본", "미국", "기타"]
STATUSES = ["이슈 진행 중", "회사가 반박", "조사·소송 중", "정리됨"]
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

SEEDS = [
    dict(seed="culper_nvda_2026", region="미국", firm="Culper Research", target="엔비디아 (NVDA)", report_date="2026-05-13",
         title="Culper Research의 엔비디아 'The China Problem' 보고서",
         status="이슈 진행 중",
         claims=("컬퍼 리서치는 엔비디아의 중국 관련 매출·유통 구조에 문제가 있다고 주장했어요 (보고서 제목 'The China Problem'). "
                 "구체적인 세부 주장은 원문을 직접 확인하지 못해서 여기엔 적지 않았어요."),
         response="엔비디아는 보고서가 나온 뒤 입장을 내고 주장에 반박했다고 보도됐어요 (반박 내용 세부는 확인 필요).",
         reaction="주가 반응은 확인하지 못했어요. 숏셀러 보고서 직후 주가는 대개 단기 출렁임이 크지만 장기 방향과는 다를 수 있어요.",
         korea_link=("가설: 엔비디아 수요 전망이 흔들리면 HBM을 공급하는 SK하이닉스·삼성전자 같은 한국 반도체주에도 심리적 영향이 갈 수 있어요. "
                     "다만 보고서 자체가 한국 기업을 다룬 것은 아니에요."),
         source="언론 보도 요약 (2026-05 ~ 2026-11, 원문 미확인). 6개월 지난 뒤 다시 이슈가 된 것으로 보임 — 최근 전개(이사회 조사 보도 등)는 확인 필요",
         source_url="", verified=0),
    dict(seed="culper_others", region="미국", firm="Culper Research", target="T1 Energy · ADMA · NexGen · Tarsus · AppLovin 등",
         report_date="", title="컬퍼 리서치의 그 밖의 보고서들 (2026)",
         status="이슈 진행 중",
         claims="컬퍼 리서치가 2026년에 이들 종목을 다룬 보고서를 냈다고 보도됐어요. 종목별 주장은 아직 정리하지 못했어요.",
         response="종목별 회사 반응은 확인하지 못했어요.",
         reaction="", korea_link="", source="언론 보도에서 목록만 확인 (원문 미확인)", source_url="", verified=0),
    dict(seed="gotham_sumitomo_2026", region="일본", firm="Gotham City Research", target="스미토모 제약 · 스미토모 화학",
         report_date="2026-08-03", title="Gotham City의 스미토모 제약·화학 보고서 ('¥116 billion Swiss Shuffle')",
         status="회사가 반박",
         claims=("고담 시티는 스미토모 화학·스미토모 제약 사이 스위스 거래를 통해 약 1,160억엔 규모로 이익이 부풀려졌다고 주장했어요. "
                 "보고서에 해당 종목 공매도 포지션을 가지고 있다고 밝혔어요."),
         response="스미토모 제약은 자세한 반박 자료를 내고 이익을 부풀렸다는 주장을 부인했어요.",
         reaction="주가 반응은 확인하지 못했어요.",
         korea_link="", source="인도 제약 매체 보도 (2026-08) 등 언론 요약, 원문 미확인", source_url="", verified=1),
    dict(seed="scorpion_lasertec_2024", region="일본", firm="Scorpion Capital", target="레이저텍 (6920)",
         report_date="2024-06", title="Scorpion Capital의 레이저텍 보고서 ('colossal fraud')",
         status="정리됨",
         claims="반도체 EUV 검사 장비 업체 레이저텍이 매출과 이익을 계속 부풀려 왔다고 주장했어요 ('거대한 사기', '째깍거리는 시한폭탄').",
         response="회사 반응은 확인하지 못했어요.",
         reaction="2024-06 보고 직후 주가 반응은 확인하지 못했어요. 2025~2026년 후속 보고는 찾지 못했어요.",
         korea_link="", source="포춘 등 언론 보도 (2024-06), 원문 미확인", source_url="", verified=1),
    dict(seed="mw_nidec_2016", region="일본", firm="Muddy Waters", target="니덱 (6594)",
         report_date="2016", title="Muddy Waters의 니덱 보고서 (2016) — 2026년 회계 문제와는 별개",
         status="정리됨",
         claims=("2016년 보고서는 당시 큰 주목을 못 받았고 숏은 곧 정리됐다고 해요. 니덱은 지난해부터 회계 문제로 도쿄거래소 상장 폐지 가능성까지 "
                 "거론되는 중인데, 이 보도에서는 옛 보고서와 지금 사태의 직접 연결은 확인되지 않았어요."),
         response="", reaction="", korea_link="", source="더 스타 등 언론 보도 (2026-11-06), 원문 미확인", source_url="", verified=0),
    dict(seed="jr_tti_2026", region="중국", firm="Jehoshaphat Research", target="테크트로닉 인더스트리즈 (TTI, 홍콩 0669)",
         report_date="2026-05", title="Jehoshaphat Research가 TTI를 다시 공격 (홍콩 상장 전동공구 기업)",
         status="회사가 반박",
         claims="TTI가 홈디포를 최소 4년간 대규모로 속였다고 주장했어요 (정확한 내용은 원문 확인 필요).",
         response="TTI는 주장을 부인했어요.",
         reaction="보도 시점에 TTI 주가가 장중 최대 7% 내려 3개월 저점까지 갔다고 해요 (날짜는 확인 필요).",
         korea_link="", source="더 스탠더드(홍콩) 보도, 원문 미확인", source_url="", verified=0),
    dict(seed="cn_history", region="중국", firm="여러 곳", target="니오 · 미니소 · 카센 · 보스덩 등",
         report_date="", title="중국계 기업을 겨냥한 예전 숏 리포트 (참고용 사례)",
         status="정리됨",
         claims=("그라이즐리(니오, 2020), 블루 오카(미니소·카센), 보니타스(보스덩, 2019) 같은 곳이 중국·홍콩 상장사를 겨냥한 보고서를 냈다고 보도됐어요. "
                 "회사별 결과는 제각각이라 정리하지 않았어요."),
         response="", reaction="", korea_link="", source="언론 보도에서 목록만 확인", source_url="", verified=0),
    dict(seed="kr_context_2026", region="한국", firm="(보고서 아님) 금융감독원·한국거래소", target="불법 공매도 점검",
         report_date="2026-11-01", title="한국은 외국 숏 리포트보다 '불법 공매도' 단속이 이슈예요",
         status="조사·소송 중",
         claims=("컬퍼 같은 외국 숏셀러가 한국 상장사를 직접 겨냥한 보고서는 이번 조사에서 확인하지 못했어요. "
                 "대신 금감원이 2026-11-01 불법 공매도 긴급 점검에 들어갔고(약 5개 종목 대상 보도), 한국은 무차입 공매도가 금지돼 있어요. "
                 "올해 초에는 대차거래잔고가 사상 최대(약 139조원)를 기록했다는 보도도 있었어요."),
         response="", reaction="",
         korea_link="이 대시보드의 '시장' 메뉴나 종목 화면의 공매도·대차 정보와 같이 보면 좋아요.",
         source="SBS 영문 · 머니투데이 등 언론 보도, 원문 미확인", source_url="", verified=0),
]


for _s in SEEDS:
    _s.setdefault("price_move", "")

# 가격 정보 보강 (v2): 기존 항목 중 사용자가 안 고친 것만 덮어쓰고, 새 항목은 추가
UPDATES = {
    "culper_nvda_2026": dict(
        price_move="보고 당일 +3% · 공개 후 약 -20% (확인 필요)",
        reaction=("보고서가 나온 2026-05-13 당일 엔비디아 주가는 오히려 약 3% 올랐고 시장은 크게 신경 쓰지 않았다고 해요. "
                  "6개월 뒤인 2026-11-10 보도에서는 컬퍼가 숏 포지션을 공개한 뒤 약 20% 내렸다고 하고, 11-09 하루 -4.27%였다는 보도도 있어요. "
                  "다만 이 하락이 보고서 때문이라고 단정할 수 없어요 — 같은 시기 젠슨 황 CEO와 중국계 인물의 관계 논란·이사회 조사 보도가 겹쳤어요 "
                  "(컬퍼가 몇 달 전 이 관계를 지적했다고 알려졌어요). 컬퍼가 숏을 얼마나 아직 갖고 있는지는 공개되지 않았어요."),
        source="Seoul Economic Daily 2026-11-10 · Fox Business · 24/7 Wall St. 등 보도 요약, 컬퍼 원문 PDF 주소는 검색에서만 확인 (내용 미확인)"),
}
NEW_SEEDS = [
    dict(seed="hunterbrook_csg_2026", region="기타", firm="Hunterbrook", target="CSG (체코 방산업체, 암스테르담 상장)",
         report_date="2026-05-04", title="Hunterbrook의 CSG 보고서 — 상장 후 하루 최대 낙폭",
         status="회사가 반박",
         claims="CSG의 사업 모델에 의문을 제기하는 보고서를 냈어요 (세부 주장은 확인 필요).",
         response="CSG는 보고서가 부정확하고 선택적이라며 반박했어요.",
         reaction="보도 당일 주가가 기록적으로 하루 18% 떨어졌다고 해요 (Bloomberg 헤드라인).",
         price_move="당일 -18%", korea_link="", source="Bloomberg 2026-05-04 (헤드라인·요약만 확인)", source_url="", verified=0),
    dict(seed="jr_gildan_2026", region="미국", firm="Jehoshaphat Research", target="길단 액티브웨어 (GIL, 캐나다·뉴욕 상장)",
         report_date="2026-06-16", title="Jehoshaphat Research의 길단 보고서 — 매출 당겨오기 주장",
         status="조사·소송 중",
         claims="티셔츠 제조사 길단이 고객에게 과다 주문을 유도해 매출을 부풀렸고, 약 5억달러 매출을 앞당겨 잡았다고 주장했어요.",
         response="회사 반응은 확인하지 못했어요 (주주 집단소송 모집 공고가 나왔어요 — 소송 결과가 아니라 소송을 하려는 변호사 공고예요).",
         reaction="당일 종가 70.39달러로 약 18.75% 하락했어요.",
         price_move="당일 -19% (종가 $70.39)", korea_link="", source="The Globe and Mail · GlobeNewswire 2026-11-10 공고, 원문 미확인", source_url="", verified=1),
    dict(seed="spruce_resideo_2026", region="미국", firm="Spruce Point Capital", target="레지데오 (REZI)",
         report_date="2026-01", title="Spruce Point의 레지데오 보고서 — '강한 매도' 의견",
         status="이슈 진행 중",
         claims="레지데오에 강한 매도 의견을 내고 장기적으로 25~50% 하락 여지가 있다고 추정했어요.",
         response="회사 반응은 확인하지 못했어요.", reaction="당일 약 4% 하락에 그쳤어요.",
         price_move="당일 -4%", korea_link="", source="요약 보도 (원문 미확인)", source_url="", verified=0),
    dict(seed="wolfpack_flotek", region="미국", firm="Wolfpack Research", target="플로텍 인더스트리즈 (FTK)",
         report_date="", title="Wolfpack Research의 플로텍 보고서 — 4억달러 계약 해지 주장",
         status="정리됨",
         claims="4억달러 계약이 해지됐는데 회사가 공시하지 않았다고 주장했어요.",
         response="회사 반응은 확인하지 못했어요.", reaction="보도 당일 약 21% 하락했어요 (날짜는 확인 못 함 — 예전 사례일 수 있어요).",
         price_move="당일 -21%", korea_link="", source="Investing.com 보도 요약", source_url="", verified=0),
    dict(seed="blueorca_hesai", region="중국", firm="Blue Orca Capital", target="헤사이 그룹 (HSAI, 나스닥 상장 중국 라이다 업체)",
         report_date="", title="Blue Orca Capital의 헤사이 보고서 — 투자자 오도 주장",
         status="회사가 반박",
         claims="헤사이가 투자자를 오도했다고 주장했어요 (세부 주장은 확인 필요).",
         response="회사 반응은 확인하지 못했어요.", reaction="보도 당일 주가가 7% 넘게 내려 마감했어요 (날짜 확인 필요, 2025년 초로 보임).",
         price_move="당일 -7%", korea_link="", source="벤징가 코리아 보도 요약, 원문 미확인", source_url="", verified=0),
]
for _s in NEW_SEEDS:
    _s.setdefault("price_move", "")


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS short_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, seed TEXT, region TEXT, firm TEXT, target TEXT, report_date TEXT, title TEXT,
        status TEXT, claims TEXT, response TEXT, reaction TEXT, korea_link TEXT, source TEXT, source_url TEXT,
        verified INTEGER DEFAULT 1, added TEXT, updated TEXT, price_move TEXT);
    CREATE TABLE IF NOT EXISTS short_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    if "price_move" not in [r[1] for r in conn.execute("PRAGMA table_info(short_reports)")]:
        conn.execute("ALTER TABLE short_reports ADD COLUMN price_move TEXT")


def _clip(s, n):
    return (s or "").strip()[:n]


COLS = ("seed", "region", "firm", "target", "report_date", "title", "status", "claims", "response", "reaction", "korea_link",
        "source", "source_url", "verified", "price_move")


def _insert_seed(conn, s, now):
    conn.execute("INSERT INTO short_reports (%s, added, updated) VALUES (%s)" % (", ".join(COLS), ",".join("?" * (len(COLS) + 2))),
                 [s[c] for c in COLS] + [now, now])


def seed(conn):
    """기본 항목을 넣음. v1: 처음 한 번 / v2: 주가 정보 보강 + 새 항목 (지운 항목은 다시 안 생기고, 사용자가 고친 항목은 덮어쓰지 않음)"""
    ensure(conn)
    now = dt.date.today().isoformat()
    n = 0
    if not conn.execute("SELECT 1 FROM short_meta WHERE key='seeded_v1'").fetchone():
        for s in SEEDS:
            _insert_seed(conn, s, now)
            n += 1
        conn.execute("INSERT INTO short_meta VALUES ('seeded_v1', ?)", (now,))
    if not conn.execute("SELECT 1 FROM short_meta WHERE key='seeded_v2'").fetchone():
        for key, upd in UPDATES.items():
            row = conn.execute("SELECT id, added, updated FROM short_reports WHERE seed=?", (key,)).fetchone()
            if row and row[1] == row[2]:                      # 고친 적 없음
                sets = ", ".join(f"{k}=?" for k in upd)
                conn.execute(f"UPDATE short_reports SET {sets}, updated=?, added=? WHERE id=?", list(upd.values()) + [now, now, row[0]])
        for s in NEW_SEEDS:
            _insert_seed(conn, s, now)
            n += 1
        conn.execute("INSERT INTO short_meta VALUES ('seeded_v2', ?)", (now,))
    conn.commit()
    return n


FIELDS = [("firm", 80), ("target", 100), ("title", 120), ("claims", 1500), ("response", 800), ("reaction", 500),
          ("korea_link", 500), ("source", 200), ("source_url", 300), ("price_move", 60)]


def _valid_date(d):
    try:
        dt.date.fromisoformat(d)
        return True
    except ValueError:
        return False


def _clean(form):
    out = {k: _clip(form.get(k), n) for k, n in FIELDS}
    out["region"] = form.get("region") if form.get("region") in REGIONS else "기타"
    out["status"] = form.get("status") if form.get("status") in STATUSES else "이슈 진행 중"
    d = (form.get("report_date") or "").strip()
    out["report_date"] = d if DATE_RE.fullmatch(d) and _valid_date(d) else ""
    out["verified"] = 0 if form.get("unverified") else 1
    if out["source_url"] and not out["source_url"].startswith(("http://", "https://")):
        out["source_url"] = ""
    return out


def add(conn, form):
    ensure(conn)
    c = _clean(form)
    if not c["title"]:
        return None
    now = dt.date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO short_reports (region, firm, target, report_date, title, status, claims, response, reaction, korea_link,"
        " source, source_url, verified, price_move, added, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c["region"], c["firm"], c["target"], c["report_date"], c["title"], c["status"], c["claims"], c["response"],
         c["reaction"], c["korea_link"], c["source"], c["source_url"], c["verified"], c["price_move"], now, now))
    conn.commit()
    return cur.lastrowid


def edit(conn, rid, form):
    ensure(conn)
    c = _clean(form)
    if not c["title"] or not conn.execute("SELECT 1 FROM short_reports WHERE id=?", (rid,)).fetchone():
        return False
    conn.execute(
        "UPDATE short_reports SET region=?, firm=?, target=?, report_date=?, title=?, status=?, claims=?, response=?, reaction=?,"
        " korea_link=?, source=?, source_url=?, verified=?, price_move=?, updated=? WHERE id=?",
        (c["region"], c["firm"], c["target"], c["report_date"], c["title"], c["status"], c["claims"], c["response"], c["reaction"],
         c["korea_link"], c["source"], c["source_url"], c["verified"], c["price_move"], dt.date.today().isoformat(), rid))
    conn.commit()
    return True


def set_status(conn, rid, status):
    if status not in STATUSES:
        return False
    conn.execute("UPDATE short_reports SET status=?, updated=? WHERE id=?", (status, dt.date.today().isoformat(), rid))
    conn.commit()
    return True


def delete(conn, rid):
    conn.execute("DELETE FROM short_reports WHERE id=?", (rid,))
    conn.commit()


def list_all(conn):
    """[{…}] 보고일 최신순 (날짜 없는 것은 뒤). 읽기 전용 연결에서도 호출 가능 (표가 없으면 [])"""
    try:
        cur = conn.execute("SELECT id, seed, region, firm, target, report_date, title, status, claims, response, reaction,"
                           " korea_link, source, source_url, verified, added, updated, price_move FROM short_reports")
    except sqlite3.OperationalError:
        return []
    keys = [d[0] for d in cur.description]
    rows = [dict(zip(keys, r)) for r in cur.fetchall()]
    rows.sort(key=lambda r: (r["report_date"] or "", r["id"]), reverse=True)
    return rows
