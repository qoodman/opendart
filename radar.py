# -*- coding: utf-8 -*-
"""
대비 레이더: 평소 주식 화면에는 안 보이지만 수급·시장을 흔들 수 있는 '뜬금없는' 외부 변수를 모아 둡니다.
(예: 영국 연금 상속세 개편 → 영국계 자금 매도, 결제 주기 변경, 세제 개편, 지수 편입 심사 …)

변수마다 시행·발생일, 영향 가설, 확인할 지표, 대비 메모를 적고 상태(감시중→진행 중→반영됨→해소)를 관리합니다.
날짜가 있으면 달력(cal_events)에 ★로 넣어서 전날 저녁 알림이 가게 합니다.
이 모듈은 뉴스를 스스로 읽지 않습니다. 사람이 (또는 Claude가) 확인한 변수를 적어 두는 곳이고,
기사 제목만 보고 넣은 항목은 '확인 필요'로 따로 표시합니다.
"""
import datetime as dt
import re
import sqlite3

KINDS = ["세제·연금", "해외 자금 흐름", "제도·시장구조", "지수·편입", "지정학·거시", "산업·규제", "기타"]
DIRECTIONS = ["악재 우려", "호재 가능", "불확실"]
STATUSES = ["감시중", "진행 중", "반영됨", "해소"]
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 처음에 넣어 두는 항목. 기사에서 확인된 것만 verified=1, 제목만 본 것은 verified=0 (화면에 '확인 필요'로 표시)
SEEDS = [
    dict(seed="uk_pension_iht",
         title="영국계 97조원 순매도, 이유는? (연금 상속세 vs 런던 법인 경유 레버리지 자금)",
         kind="해외 자금 흐름", direction="불확실", status="진행 중",
         when_date="2027-04-06", when_note="영국 연금 상속세 과세 시작 (기사는 '내년 4월', 블로그는 2027-04-06)",
         summary=("사실: 금감원 집계에서 영국계가 올해 1~9월 약 97조원(6~9월 76조원)을 순매도했어요. 영국은 내년 4월부터 미사용 연금에도 상속세를 물립니다.\n\n"
                  "해석 A (매일경제 기사·한국투자증권 염동찬 연구원): 연금 가입자가 연금을 남기기보다 인출해 쓸 유인이 커져 영국계 자금이 한국 주식을 판 것. "
                  "다만 연구원도 영국계 매도에서 이 요인의 비중을 구분하기는 어렵다고 단서를 달았어요.\n\n"
                  "해석 B (네이버 블로그 ranto28, 2026-11-07의 반론): 금감원 국적은 '투자자 등록지' 기준이라, 런던 법인(미국계 투자은행의 영국 법인 등)이 "
                  "헤지펀드의 스왑(TRS)·레버리지 포지션을 헤지하려고 사고판 물량이 '영국계'로 잡힐 수 있어요. 그러면 97조원은 영국 노인의 연금 인출이 아니라 "
                  "글로벌 레버리지 자금의 정리로 읽어야 해요.\n\n"
                  "규모 점검: 영국 금융감독청(FCA) 자료로 연금 인출액은 2025/26년 약 912억 파운드로 전년(709억)보다 약 203억 파운드 늘었어요 "
                  "(약 40조원, 환율 1파운드≈1,970원 가정). 늘어난 인출 전부에 한국 비중 1~2%를 곱해도 0.4~0.8조원이고, 전체 인출액(약 180조원)으로 계산해도 "
                  "2~4조원이에요. 97조원을 연금 인출로 설명하기는 어려워요. (연금 인출이 늘었다는 사실과 그 원인이 세제 불안이라는 업계 설명 자체는 확인됐어요.)\n\n"
                  "아직 모르는 것: 영국계 매도 중 런던 IB 법인 몫이 얼마인지는 공개 통계로 확인하지 못했어요. 해석 B도 입증된 것은 아니에요."),
         impact=("연금·세제 요인이라면 시행(2027-04) 전까지 완만하게 이어지는 매도, 헤지펀드·스왑 포지션 정리라면 증거금·레버리지 한도가 바뀔 때 "
                 "급하게 움직이고 되돌아올 수 있어요 (가설). 어느 쪽이든 외국인 순매도가 커지면 대형주·반도체처럼 외국인 비중이 큰 종목이 먼저 흔들릴 수 있어요."),
         check_items=("영국계 월간 순매매가 얼마나 급하게 바뀌는지(레버리지 요인은 급등락, 연금 요인은 완만) · 같은 시기 미국계 순매매와 같이 움직이는지 · "
                      "반도체 등 레버리지 거래(스왑·DMA)와 증거금 변화 보도 · 5% 공시에 잡히는 런던 소재 IB 법인 · 원/달러 환율"),
         prep=("'영국이 팔았다 = 영국 사람들 돈'으로 단정하지 말고, 영국계 순매도 소식이 나오면 연금·세제 요인과 글로벌 레버리지 정리 요인 중 어느 쪽에 가까운지 "
               "먼저 구분하기. 레버리지 요인이면 레버리지가 쏠린 종목의 변동성에 유의."),
         source="매일경제 2026-11-06 · 반론: 네이버 블로그 ranto28 2026-11-07 · FCA 연금 인출 자료 2025/26 (언론 보도로 확인)",
         source_url="https://blog.naver.com/ranto28/224467966071", verified=1),
    dict(seed="us_inflow",
         title="미국계 자금은 7월부터 순매수 (영국계 매도의 반대 신호)",
         kind="해외 자금 흐름", direction="호재 가능", status="진행 중",
         when_date="2026-07-01", when_note="7월부터 진행 중",
         summary=("같은 보고서에서 미국계 자금은 올해 65조원을 순매도했지만 7월부터는 순매수 흐름이라고 했어요. "
                  "영국계는 보유 시가총액이 연초 수준까지 낮아지고 월간 순매도 규모가 줄어 순매도 여력이 줄었다고 봤습니다. "
                  "순매도 종료를 단정하기는 어렵다는 단서가 붙어 있어요."),
         impact="영국계 매도를 미국계 매수가 얼마나 받아 주는지가 외국인 수급의 방향을 좌우할 수 있어요 (가설).",
         check_items="미국계·영국계 월별 순매수 · 외국인 보유 시가총액 추이",
         prep="한쪽 신호만 보지 말고 국적별 흐름이 엇갈리는지 같이 확인.",
         source="매일경제 2026-11-06 · 한국투자증권 염동찬 연구원 보고서",
         verified=1),
    dict(seed="settle_2028",
         title="주식 판 돈을 다음날 찾을 수 있게 (2028년부터)",
         kind="제도·시장구조", direction="불확실", status="감시중",
         when_date="2028-01-01", when_note="2028년부터 (기사 제목만 확인 · 일자 확인 필요)",
         summary="매일경제 인기기사 제목만 확인했어요: '주식 판 돈, 2028년부터 다음날 찾는다는데 개미들 주의할 이것'. 본문을 읽고 내용을 채워 주세요.",
         impact="", check_items="", prep="", source="매일경제 인기기사 (2026-11-06 확인, 제목만)", verified=0),
    dict(seed="crypto_tax_2029",
         title="코인 손익 합산 과세 (2029년부터)",
         kind="세제·연금", direction="불확실", status="감시중",
         when_date="2029-01-01", when_note="2029년부터 (기사 제목만 확인 · 일자 확인 필요)",
         summary="매일경제 에디터 픽 제목만 확인했어요: '2029년부터 코인 손해·이익 합산해 과세'. 본문을 읽고 내용을 채워 주세요.",
         impact="", check_items="", prep="", source="매일경제 에디터 픽 (2026-11-06 확인, 제목만)", verified=0),
    dict(seed="nhis_pension_dividend",
         title="연금·배당 소득자 건강보험료 부과 확대 논란",
         kind="세제·연금", direction="불확실", status="감시중",
         when_date="", when_note="시기 미확인 (기사 제목만 확인)",
         summary=("매일경제 주요뉴스 제목만 확인했어요: '세금 다 냈는데 건보료도 내라? 연금·배당 소득자 등 초비상'. "
                  "본문을 읽고 대상과 시행 시기를 채워 주세요."),
         impact="배당 투자자의 세후 수익률에 영향을 줄 수 있어요 (가설, 내용 확인 필요).",
         check_items="", prep="", source="매일경제 주요뉴스 (2026-11-06 확인, 제목만)", verified=0),
]


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS risk_radar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, seed TEXT, title TEXT, kind TEXT, direction TEXT, status TEXT,
        when_date TEXT, when_note TEXT, summary TEXT, impact TEXT, check_items TEXT, prep TEXT, source TEXT,
        source_url TEXT, verified INTEGER DEFAULT 1, cal_id INTEGER, added TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS radar_meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS cal_events (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, title TEXT, note TEXT,
        important INTEGER DEFAULT 1);
    """)


def _clip(s, n):
    return (s or "").strip()[:n]


def _sync_cal(conn, rid):
    """날짜가 있고 아직 안 끝난 변수는 달력에 ★로 넣고(전날 저녁 알림), 아니면 뺌"""
    r = conn.execute("SELECT title, when_date, status, summary, cal_id FROM risk_radar WHERE id=?", (rid,)).fetchone()
    if not r:
        return
    title, when, status, summary, cal_id = r
    want = (bool(DATE_RE.fullmatch(when or "")) and _valid_date(when) and status != "해소"
            and dt.date.fromisoformat(when) >= dt.date.today())          # 지난 날짜는 달력에 안 넣음
    if want:
        text, note = f"[변수] {title}"[:100], (summary or "")[:120]
        if cal_id and conn.execute("SELECT 1 FROM cal_events WHERE id=?", (cal_id,)).fetchone():
            conn.execute("UPDATE cal_events SET date=?, title=?, note=?, important=1 WHERE id=?", (when, text, note, cal_id))
        else:
            cur = conn.execute("INSERT INTO cal_events (date, title, note, important) VALUES (?,?,?,1)", (when, text, note))
            conn.execute("UPDATE risk_radar SET cal_id=? WHERE id=?", (cur.lastrowid, rid))
    elif cal_id:
        conn.execute("DELETE FROM cal_events WHERE id=?", (cal_id,))
        conn.execute("UPDATE risk_radar SET cal_id=NULL WHERE id=?", (rid,))


def migrate_uk(conn):
    """예전에 넣은 영국 항목('연금 때문' 단정)을 해석 논쟁 내용으로 바꿈. 사용자가 직접 고친 항목이면 건드리지 않고 새 항목을 따로 추가."""
    if conn.execute("SELECT 1 FROM radar_meta WHERE key='uk_interp_v2'").fetchone():
        return
    now = dt.date.today().isoformat()
    seed_v2 = next(x for x in SEEDS if x["seed"] == "uk_pension_iht")
    row = conn.execute("SELECT id, added, updated FROM risk_radar WHERE seed='uk_pension_iht'").fetchone()
    if row and row[1] == row[2]:                         # 고친 적 없음 → 새 내용으로 교체
        conn.execute("UPDATE risk_radar SET title=?, kind=?, direction=?, status=?, when_date=?, when_note=?, summary=?, impact=?,"
                     " check_items=?, prep=?, source=?, source_url=?, verified=?, updated=?, added=? WHERE id=?",
                     (seed_v2["title"], seed_v2["kind"], seed_v2["direction"], seed_v2["status"], seed_v2["when_date"],
                      seed_v2["when_note"], seed_v2["summary"], seed_v2["impact"], seed_v2["check_items"], seed_v2["prep"],
                      seed_v2["source"], seed_v2["source_url"], seed_v2["verified"], now, now, row[0]))
        _sync_cal(conn, row[0])
    elif row:                                            # 사용자가 고쳤음 → 반론 정리를 별도 항목으로
        cur = conn.execute(
            "INSERT INTO risk_radar (seed, title, kind, direction, status, when_date, when_note, summary, impact, check_items,"
            " prep, source, source_url, verified, added, updated) VALUES ('uk_interp_v2',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("[해석 논쟁] " + seed_v2["title"], seed_v2["kind"], seed_v2["direction"], seed_v2["status"], "", seed_v2["when_note"],
             seed_v2["summary"], seed_v2["impact"], seed_v2["check_items"], seed_v2["prep"], seed_v2["source"],
             seed_v2["source_url"], seed_v2["verified"], now, now))
    conn.execute("INSERT OR IGNORE INTO radar_meta VALUES ('uk_interp_v2', ?)", (now,))
    conn.commit()


def seed(conn):
    """처음 한 번만 기본 항목을 넣음 (지운 항목이 다시 생기지 않게 표시를 남김)"""
    ensure(conn)
    if conn.execute("SELECT 1 FROM radar_meta WHERE key='seeded_v1'").fetchone():
        migrate_uk(conn)
        return 0
    now = dt.date.today().isoformat()
    n = 0
    for s in SEEDS:
        cur = conn.execute(
            "INSERT INTO risk_radar (seed, title, kind, direction, status, when_date, when_note, summary, impact, check_items,"
            " prep, source, source_url, verified, added, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["seed"], s["title"], s["kind"], s["direction"], s["status"], s["when_date"], s["when_note"], s["summary"],
             s["impact"], s["check_items"], s["prep"], s["source"], "", s["verified"], now, now))
        _sync_cal(conn, cur.lastrowid)
        n += 1
    conn.execute("INSERT INTO radar_meta VALUES ('seeded_v1', ?)", (now,))
    conn.execute("INSERT OR IGNORE INTO radar_meta VALUES ('uk_interp_v2', ?)", (now,))
    conn.commit()
    return n


FIELDS = [("title", 100), ("when_note", 100), ("summary", 1500), ("impact", 600), ("check_items", 600), ("prep", 600),
          ("source", 200), ("source_url", 300)]


def _clean(form):
    out = {k: _clip(form.get(k), n) for k, n in FIELDS}
    out["kind"] = form.get("kind") if form.get("kind") in KINDS else "기타"
    out["direction"] = form.get("direction") if form.get("direction") in DIRECTIONS else "불확실"
    d = (form.get("when_date") or "").strip()
    out["when_date"] = d if DATE_RE.fullmatch(d) and _valid_date(d) else ""
    out["verified"] = 0 if form.get("unverified") else 1
    if out["source_url"] and not out["source_url"].startswith(("http://", "https://")):
        out["source_url"] = ""
    return out


def _valid_date(d):
    try:
        dt.date.fromisoformat(d)
        return True
    except ValueError:
        return False


def add(conn, form):
    ensure(conn)
    c = _clean(form)
    if not c["title"]:
        return None
    now = dt.date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO risk_radar (title, kind, direction, status, when_date, when_note, summary, impact, check_items, prep,"
        " source, source_url, verified, added, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c["title"], c["kind"], c["direction"], "감시중", c["when_date"], c["when_note"], c["summary"], c["impact"],
         c["check_items"], c["prep"], c["source"], c["source_url"], c["verified"], now, now))
    _sync_cal(conn, cur.lastrowid)
    conn.commit()
    return cur.lastrowid


def edit(conn, rid, form):
    ensure(conn)
    c = _clean(form)
    if not c["title"] or not conn.execute("SELECT 1 FROM risk_radar WHERE id=?", (rid,)).fetchone():
        return False
    conn.execute(
        "UPDATE risk_radar SET title=?, kind=?, direction=?, when_date=?, when_note=?, summary=?, impact=?, check_items=?,"
        " prep=?, source=?, source_url=?, verified=?, updated=? WHERE id=?",
        (c["title"], c["kind"], c["direction"], c["when_date"], c["when_note"], c["summary"], c["impact"], c["check_items"],
         c["prep"], c["source"], c["source_url"], c["verified"], dt.date.today().isoformat(), rid))
    _sync_cal(conn, rid)
    conn.commit()
    return True


def set_status(conn, rid, status):
    if status not in STATUSES:
        return False
    conn.execute("UPDATE risk_radar SET status=?, updated=? WHERE id=?", (status, dt.date.today().isoformat(), rid))
    _sync_cal(conn, rid)
    conn.commit()
    return True


def delete(conn, rid):
    r = conn.execute("SELECT cal_id FROM risk_radar WHERE id=?", (rid,)).fetchone()
    if r and r[0]:
        conn.execute("DELETE FROM cal_events WHERE id=?", (r[0],))
    conn.execute("DELETE FROM risk_radar WHERE id=?", (rid,))
    conn.commit()


def list_all(conn, today=None):
    """[{…, dday: 정수|None}] — 읽기 전용 연결에서도 호출 가능 (표가 없으면 [])"""
    today = today or dt.date.today()
    try:
        cur = conn.execute("SELECT id, seed, title, kind, direction, status, when_date, when_note, summary, impact,"
                           " check_items, prep, source, source_url, verified, added, updated FROM risk_radar")
    except sqlite3.OperationalError:
        return []
    keys = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        r = dict(zip(keys, row))
        r["dday"] = (dt.date.fromisoformat(r["when_date"]) - today).days if DATE_RE.fullmatch(r["when_date"] or "") else None
        out.append(r)
    return out


def dday_text(n):
    if n is None:
        return ""
    return "오늘" if n == 0 else f"D-{n}" if n > 0 else f"{-n}일 지남"


# ======================= 국가별 영향력 지도 =======================
# 한국 상장주식을 많이 가진 외국인 투자자의 국적(금융감독원 '외국인 증권투자 동향' 기준)별로 비중·최근 순매매·성격·민감 요인을 정리.
# 숫자는 금감원 집계를 인용한 언론 보도에서 가져왔고(원문 직접 확인은 못 함), 해석·민감 요인은 사람이 쓴 '가설'입니다.
# 숫자는 매달 금감원 보도자료가 나오면 화면의 '국가 현황 갱신'에서 고칠 수 있습니다.
TOTAL = "외국인 전체"
SRC_2025 = "금감원 집계 (이코노미스트·파이낸셜뉴스 보도, 2026-02)"
SRC_2026_01 = "금감원 '외국인 증권투자 동향' 2026년 1월분 (언론 보도 인용)"
SRC_FEB = "금감원 2026년 2월분 (언론 보도 인용)"
SRC_ART = "매일경제 2026-11-06 · 한국투자증권 염동찬 연구원 보고서"

# (국가, 기준월, 보유 조원, 비중 %(None이면 전체 대비 계산), 출처)
SNAPSHOTS = [
    (TOTAL, "2025-12", 1326.8, None, SRC_2025), (TOTAL, "2026-01", 1701.446, 32.0, SRC_2026_01),
    ("미국", "2025-12", 546.0, 41.2, SRC_2025), ("영국", "2025-12", 144.0, None, SRC_2025),
    ("싱가포르", "2025-12", 88.0, None, SRC_2025), ("룩셈부르크", "2025-12", 70.0, None, SRC_2025),
    ("아일랜드", "2025-12", 58.0, None, SRC_2025), ("호주", "2025-12", 47.0, None, SRC_2025),
    ("네덜란드", "2025-12", 44.0, None, SRC_2025), ("노르웨이", "2025-12", 36.0, None, SRC_2025),
    ("캐나다", "2025-12", 34.0, None, SRC_2025), ("케이맨제도", "2025-12", 30.3, None, SRC_2025),
    ("중국", "2025-12", 30.2, None, SRC_2025),
    ("미국", "2026-01", 698.053, 41.0, SRC_2026_01), ("영국", "2026-01", 182.0, 10.7, SRC_2026_01),
    ("싱가포르", "2026-01", 112.1, 6.6, SRC_2026_01), ("룩셈부르크", "2026-01", 92.0, 5.4, SRC_2026_01),
    ("아일랜드", "2026-01", 76.2, 4.5, SRC_2026_01),
]
# (국가, 기간 끝 월, 기간 이름, 순매매 조원(+매수/−매도), 출처)
FLOWS = [
    ("아일랜드", "2025-12", "2025년 연간", 6.9, SRC_2025), ("미국", "2025-12", "2025년 연간", 4.5, SRC_2025),
    ("영국", "2025-12", "2025년 연간", -8.1, SRC_2025), ("싱가포르", "2025-12", "2025년 연간", -7.2, SRC_2025),
    ("아일랜드", "2026-01", "2026년 1월", 2.332, SRC_2026_01), ("영국", "2026-01", "2026년 1월", 1.758, SRC_2026_01),
    ("룩셈부르크", "2026-01", "2026년 1월", 1.35, SRC_2026_01), ("미국", "2026-01", "2026년 1월", -7.795, SRC_2026_01),
    ("미국", "2026-02", "2026년 2월", -8.673, SRC_FEB), ("영국", "2026-02", "2026년 2월", -4.653, SRC_FEB),
    ("캐나다", "2026-02", "2026년 2월", -1.524, SRC_FEB),
    ("영국", "2026-09", "2026년 1~9월", -97.0, SRC_ART), ("영국", "2026-09", "2026년 6~9월", -76.0, SRC_ART),
    ("미국", "2026-09", "2026년 1~9월(올해)", -65.0, SRC_ART),
]
# 국가별 해석(가설). type: 성격 한 줄, read: 영향력 해석, triggers: 이런 소식이 나오면 의심, checks: 확인할 지표
PROFILES = {
    "미국": dict(type="장기·패시브 중심 · 방향을 정하는 1위",
        read="보유의 약 41%라 한국 수급의 방향을 정하는 나라예요. 연기금·뮤추얼펀드·ETF 같은 장기 자금이 많아 평소엔 느리게 움직이지만, "
             "비중을 바꾸면 규모가 커요 (올해 1월 7.8조·2월 8.7조 순매도, 1~9월 65조원 순매도 후 7월부터 순매수로 돌아섰다는 분석).",
        triggers="미국 금리·달러 방향 · AI·반도체 사이클 (한국 시총에서 반도체 비중이 커요) · 미국 증시 급락 시 위험자산 일괄 축소 · MSCI 등 신흥국 비중 조정",
        checks="미국계 월간 순매매(금감원) · 원/달러 환율 · 필라델피아 반도체지수 · 미 10년 금리"),
    "영국": dict(type="'영국 사람 돈'이 아닐 수 있음 (등록지 기준) · 단기 성향",
        read="보유 비중은 약 11%인데 매매 변동이 커요. 금감원 국적은 투자자 등록지 기준이라, 런던 법인(미국계 투자은행의 영국 법인 등)이 헤지펀드의 스왑·레버리지 "
             "포지션을 헤지하려고 사고판 물량도 영국계로 잡힐 수 있다는 반론이 있어요 (블로그 2026-11-07, 공개 통계로는 검증하지 못했어요). "
             "기사는 영국 연금 상속세(2027-04-06 시작)를 이유로 들었지만, 연금 인출 증가분 약 40조원에 한국 비중 1~2%를 곱해도 0.4~0.8조원이라 "
             "올해 1~9월 순매도 97조원을 설명하기 어려워요. 2025년 11월 1~24일 영국계 순매도가 외국인 전체 순매도의 36.9%(한국거래소 집계)였던 것도 "
             "짧은 호흡의 큰 매매를 시사해요.",
        triggers="글로벌 헤지펀드 레버리지 축소·증거금 인상(미국 IB의 런던 법인 경유) · 반도체 등 레버리지가 쏠린 종목의 급락 · 영국 연금·상속세 개편 · 파운드·영국 금리",
        checks="영국계 월간 순매매의 급변동 여부(레버리지 요인은 급하게 움직였다 되돌아오고, 연금 요인은 완만) · 미국계 순매매와 같이 움직이는지 · 5% 공시의 런던 소재 IB 법인"),
    "싱가포르": dict(type="국부펀드·아시아 운용 허브 (가설)",
        read="3위권 보유국이에요. 국부펀드·패밀리오피스·아시아 지역 운용사 자금이 섞여 있다고 보이고(가설), 2025년에는 7.2조원 순매도였어요. "
             "싱가포르 거점이 아시아 내 자산배분을 바꿀 때 한국 비중도 같이 움직일 수 있어요.",
        triggers="아시아 신흥국 자금 이동(인도·중국 대비 한국 선호) · 싱가포르 달러 · 국부펀드 자산배분 변경",
        checks="싱가포르계 월간 순매매 · 아시아 신흥국 지수 대비 코스피 상대 흐름"),
    "룩셈부르크": dict(type="펀드 설립 거점 (국적이 곧 투자자는 아님)",
        read="룩셈부르크·아일랜드·케이맨은 글로벌 펀드가 세워지는 곳이라, 이 숫자는 '그 나라 사람'이 아니라 '그곳에 등록된 펀드'의 매매예요(가설). "
             "유럽 등 여러 나라 투자자의 자금이 섞여서, 이 나라들의 순매매는 글로벌 펀드 자금의 유입·유출 신호로 읽는 편이 맞아요.",
        triggers="글로벌 신흥국 펀드 환매·설정 · 유럽 투자자 위험 선호 · EU 펀드 규제 변경",
        checks="룩셈부르크·아일랜드 합산 순매매 · 신흥국 ETF 자금 유출입"),
    "아일랜드": dict(type="펀드 설립 거점 · ETF 자금 (가설)",
        read="ETF·펀드 거점이라 패시브 자금 유입이 잡히기 쉬워요(가설). 2025년 6.9조원, 올해 1월 2.3조원 순매수로 순매수를 이끈 나라였어요.",
        triggers="신흥국·한국 ETF 자금 유입 · 글로벌 ETF 리밸런싱", checks="아일랜드계 월간 순매수 · 한국 비중 큰 ETF 자금흐름"),
    "호주": dict(type="퇴직연금 자금 (가설)", read="2025년말 약 47조원(약 3.5%). 퇴직연금(슈퍼애뉴에이션) 같은 장기 자금이 해외 배분을 바꿀 때 영향이 있을 수 있어요(가설).",
        triggers="호주 달러 · 호주 퇴직연금의 해외주식 배분 변경", checks="호주계 월간 순매매"),
    "네덜란드": dict(type="연금 자금 (가설)", read="2025년말 약 44조원(약 3.3%). 네덜란드는 연금 제도 개편이 진행 중이라는 보도가 있어(확인 필요), 대형 연금의 자산배분 변경이 변수가 될 수 있어요.",
        triggers="네덜란드 연금 제도 개편에 따른 자산 재배분 · 유로화", checks="네덜란드계 월간 순매매"),
    "노르웨이": dict(type="국부펀드 (가설)", read="2025년말 약 36조원(약 2.7%). 국부펀드는 운용 지침·벤치마크 지수가 바뀌면 일정 규모가 한꺼번에 움직일 수 있어요(가설).",
        triggers="국부펀드 운용 지침·제외 종목 변경 · 벤치마크 지수 변경 · 유가", checks="노르웨이계 월간 순매매"),
    "캐나다": dict(type="연금·운용사 자금 (가설)", read="2025년말 약 34조원(약 2.6%). 올해 2월 1.5조원 순매도가 눈에 띄었어요. 연금 자금은 장기 보유지만 자산배분 조정 때 한꺼번에 움직여요(가설).",
        triggers="캐나다 연금의 신흥국 배분 변경 · 캐나다 금리", checks="캐나다계 월간 순매매"),
    "케이맨제도": dict(type="헤지펀드 설립 거점 (가설)", read="약 30조원(약 2.3%). 헤지펀드가 많이 등록된 곳이라 단기 포지션 조정이 순매매로 잡힐 수 있어요(가설).",
        triggers="헤지펀드 레버리지 축소·위험 회피 장세", checks="케이맨계 월간 순매매 · 시장 변동성(VIX·VKOSPI)"),
    "중국": dict(type="자본통제·정책 변수 (가설)", read="약 30조원(약 2.3%). 자본 이동이 정책에 크게 좌우돼서, 비중은 작아도 정책 발표 한 번에 흐름이 바뀔 수 있어요(가설).",
        triggers="중국 자본통제·해외투자 쿼터 변경 · 위안화 · 한중 관계", checks="중국계 월간 순매매 · 위안/원 환율"),
}
COMMON = ("공통 변수: 원/달러 환율 · MSCI·FTSE 지수 정기 조정(리밸런싱) · 한국 세제·배당·밸류업 정책 · 국내 시장 변동성. "
          "국적은 '투자자 등록지' 기준이라 실제 돈의 주인과 다를 수 있어요 (미국 IB의 런던 법인, 룩셈부르크·아일랜드·케이맨의 펀드, 조세회피지역 법인 등).")


def ensure_countries(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS radar_snap (country TEXT, asof TEXT, holding_trn REAL, share_pct REAL, source TEXT,
        PRIMARY KEY (country, asof));
    CREATE TABLE IF NOT EXISTS radar_flow (country TEXT, period_end TEXT, period TEXT, net_trn REAL, source TEXT,
        PRIMARY KEY (country, period_end, period));
    """)


def seed_countries(conn):
    ensure(conn)
    ensure_countries(conn)
    if conn.execute("SELECT 1 FROM radar_meta WHERE key='countries_v1'").fetchone():
        return 0
    for c, asof, h, sh, src in SNAPSHOTS:
        conn.execute("INSERT OR IGNORE INTO radar_snap VALUES (?,?,?,?,?)", (c, asof, h, sh, src))
    for c, pe, per, net, src in FLOWS:
        conn.execute("INSERT OR IGNORE INTO radar_flow VALUES (?,?,?,?,?)", (c, pe, per, net, src))
    conn.execute("INSERT INTO radar_meta VALUES ('countries_v1', ?)", (dt.date.today().isoformat(),))
    conn.commit()
    return len(SNAPSHOTS) + len(FLOWS)


def country_overview(conn):
    """[{country, asof, holding, share, share_calc, source, flows:[(기간, 순매매, 출처)…], profile}] 비중 큰 순.
    비중이 비어 있으면 같은 기준월의 '외국인 전체' 보유액 대비로 계산(share_calc=True)."""
    try:
        snaps = conn.execute("SELECT country, asof, holding_trn, share_pct, source FROM radar_snap ORDER BY asof").fetchall()
        flows = conn.execute("SELECT country, period_end, period, net_trn, source FROM radar_flow").fetchall()
    except sqlite3.OperationalError:
        return [], {}
    total = {a: h for c, a, h, sh, src in snaps if c == TOTAL}
    latest, prev = {}, {}
    for c, a, h, sh, src in snaps:
        if c == TOTAL:
            continue
        if sh is None and a in total and total[a]:
            sh, calc = round(h / total[a] * 100, 1), True
        else:
            calc = False
        if c in latest:
            prev[c] = latest[c]
        latest[c] = dict(country=c, asof=a, holding=h, share=sh, share_calc=calc, source=src)
    for c, row in latest.items():
        row["prev_share"] = prev[c]["share"] if c in prev else None
        row["prev_asof"] = prev[c]["asof"] if c in prev else None
        fl = sorted((f for f in flows if f[0] == c), key=lambda f: (f[1], f[2]), reverse=True)
        row["flows"] = [(f[2], f[3], f[4]) for f in fl]
        row["profile"] = PROFILES.get(c)
    out = sorted(latest.values(), key=lambda r: -(r["share"] or 0))
    return out, {"total": total}


def import_text(conn, text):
    """'국가,기준월(YYYY-MM),보유(조원),비중(%)[,순매매(조원)[,기간 이름]]' 줄을 반영. → (반영 줄 수, 오류 목록)"""
    ensure_countries(conn)
    ok, errors = 0, []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,\t]", line)]
        try:
            country, asof = parts[0][:20], parts[1]
            if not country or not re.fullmatch(r"\d{4}-\d{2}", asof) or not 1 <= int(asof[5:]) <= 12:
                raise ValueError("기준월은 2026-02 처럼")
            holding = float(parts[2].replace(",", ""))
            share = float(parts[3].replace("%", "")) if len(parts) > 3 and parts[3] else None
            if holding < 0 or (share is not None and not 0 <= share <= 100):
                raise ValueError("숫자 범위")
            net = float(parts[4].replace(",", "")) if len(parts) > 4 and parts[4] else None
            period = parts[5][:30] if len(parts) > 5 and parts[5] else f"{asof[:4]}년 {int(asof[5:])}월"
        except (IndexError, ValueError) as e:
            errors.append(f"{n}번째 줄: {line[:40]} ({e if str(e) else '형식'})")
            continue
        conn.execute("INSERT OR REPLACE INTO radar_snap VALUES (?,?,?,?,?)", (country, asof, holding, share, "직접 입력"))
        if net is not None:
            conn.execute("INSERT OR REPLACE INTO radar_flow VALUES (?,?,?,?,?)", (country, asof, period, net, "직접 입력"))
        ok += 1
    conn.commit()
    return ok, errors
