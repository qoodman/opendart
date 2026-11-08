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
         title="영국 연금 상속세 과세 시작 → 영국계 자금 매도 압력",
         kind="세제·연금", direction="악재 우려", status="진행 중",
         when_date="2027-04-01", when_note="내년 4월부터 (정확한 일자는 확인 필요)",
         summary=("영국은 연금 수령자가 사망해도 남은 연금에 세금을 안 물렸는데, 내년 4월부터 상속세를 물립니다. "
                  "법안은 올해 3월 확정, 세부 절차는 5월 공개. 한국투자증권 염동찬 연구원은 영국계 자금이 올해 1~9월 97조원을 "
                  "순매도했고, 그중 76조원이 6~9월 넉 달에 몰렸다고 분석했어요. 연금을 상속용으로 남기기보다 인출해 쓸 유인이 커져서 "
                  "한국 주식 매도에도 영향을 준 것으로 해석했습니다 (영향 비중을 따로 가르기는 어렵다고 단서를 달았어요)."),
         impact="외국인(특히 영국계) 순매도가 이어지면 지수 상단이 눌리고, 외국인 비중이 큰 대형주가 먼저 영향을 받을 수 있어요 (가설).",
         check_items="영국계 월간 순매도 규모가 줄고 있는지 · 외국인 전체 순매수 흐름 · 원/달러 환율 · 시행 시기(4월) 전후 변동성",
         prep="시행 전까지 매도가 이어질 수 있다는 전제로 수급 의존이 큰 종목 비중을 점검하고, 한 번에 사지 않고 나눠 접근하는 쪽을 고려.",
         source="매일경제 2026-11-06 · 한국투자증권 염동찬 연구원 '외국인 자금 동향과 영국계 매도: 영국 상속세 개정의 나비효과'",
         verified=1),
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


def seed(conn):
    """처음 한 번만 기본 항목을 넣음 (지운 항목이 다시 생기지 않게 표시를 남김)"""
    ensure(conn)
    if conn.execute("SELECT 1 FROM radar_meta WHERE key='seeded_v1'").fetchone():
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
