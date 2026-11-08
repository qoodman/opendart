# -*- coding: utf-8 -*-
"""
'오일전문가 점수표' (장기투자 종목 선정 점수표, 100점 만점).

  이익 창출력·저평가·지속 가능성  35점  PER 20 · PBR 5 · 이익 지속 5 · 중복 상장 5
  주주환원 의지                   40점  배당수익률 10 · 분기배당 5 · 연속 인상 5 · 자사주 매입·소각 7 · 소각 비율 8 · 자사주 보유 5
  미래 성장 잠재력·기업 경쟁력      25점  성장 잠재력 10 · 경영 10 · 세계적 브랜드 5

  등급: 80점 초과 A(적극 매수) · 70~80 B(매수 고려) · 50~70 미만 C(유지·홀딩) · 50 미만 D(추천하지 않음)

PER·PBR·배당수익률(35점)은 네이버 증권 값으로 자동 계산하고, 나머지 10개 항목(65점)은 정성적이거나
데이터가 없어서 직접 고릅니다. 고른 값은 results.sqlite 에 종목별로 저장합니다.
점수 기준은 표에 적힌 그대로 쓰고, 표에 없는 경우(적자 PER, 배당 없음 등)는 아래 함수에 주석으로 적어 두었습니다.
"""
import datetime as dt
import sqlite3

# ---------------- 점수표 ----------------
SECTIONS = {
    "A": "이익 창출력 · 저평가 · 지속 가능성",
    "B": "주주환원 의지",
    "C": "미래 성장 잠재력 · 기업 경쟁력",
}

# 직접 고르는 항목: (키, 구역, 이름, 만점, [(값, 글자, 점수)…], 도움말)
MANUAL = [
    ("sustain", "A", "이익의 지속 가능성 (정성 판단)", 5,
     [("ok", "대체로 지속 가능", 5), ("bad", "불안정한 이익 창출력", 0)], ""),
    ("dual", "A", "중복 상장 여부 (자회사·손자회사 상장 여부)", 5,
     [("single", "단독 상장", 5), ("dual", "중복 상장", 0)], ""),
    ("qdiv", "B", "분기 배당 실시 여부", 5,
     [("yes", "예", 5), ("no", "아니오", 0)], ""),
    ("divyears", "B", "배당 연속 인상 연수", 5,
     [("10", "10년 이상", 5), ("5", "5년 이상", 4), ("3", "3년 이상", 3), ("0", "해당 없음", 0)],
     "배당 동결 시 연속은 인정하나 인상은 인정하지 않음"),
    ("buyback", "B", "정기적 자사주 매입·소각 여부", 7,
     [("yes", "예", 7), ("no", "아니오", 0)], "최소 연 1회 이상"),
    ("burn", "B", "연간 소각 비율 (총주식수 대비)", 8,
     [("2", "2% 초과", 8), ("1.5", "1.5% 초과", 5), ("0.5", "0.5% 초과", 3), ("0", "0.5% 이하", 0)], ""),
    ("treasury", "B", "자사주 보유 비율", 5,
     [("none", "없음", 5), ("lt2", "2% 미만", 4), ("lt5", "5% 미만", 2), ("ge5", "5% 이상", 0)], ""),
    ("growth", "C", "미래 성장 잠재력 (정성 판단)", 10,
     [("very", "매우 높다", 10), ("high", "높다", 7), ("mid", "보통", 5), ("low", "낮다", 3)], ""),
    ("mgmt", "C", "기업 경영 (정성 판단)", 10,
     [("great", "우수한 경영자", 10), ("pro", "전문 경영자", 5), ("owner", "저조한 실적 오너 경영", 0)], ""),
    ("brand", "C", "세계적 브랜드 보유 여부", 5,
     [("yes", "있다", 5), ("no", "없다", 0)], ""),
]
MANUAL_BY_KEY = {m[0]: m for m in MANUAL}

# 자동 항목: (키, 구역, 이름, 만점, 기준 설명)
AUTO = [
    ("per", "A", "PER", 20, "5 미만 20 · 8 미만 15 · 10 미만 10 · 그 외 5 (적자 등 PER이 없으면 0점)"),
    ("pbr", "A", "PBR", 5, "0.3 미만 5 · 0.6 미만 4 · 1.0 미만 3 · 그 외 0"),
    ("dy", "B", "배당수익률", 10, "7% 초과 10 · 5% 초과 7 · 3% 초과 5 · 3% 이하 2 (배당이 없으면 0점)"),
]
AUTO_MAX = sum(a[3] for a in AUTO)
MAX_TOTAL = AUTO_MAX + sum(m[3] for m in MANUAL)       # 100
assert MAX_TOTAL == 100

# 화면에 보여 줄 항목 순서 (점수표와 같은 순서)
ORDER = ["per", "pbr", "sustain", "dual", "dy", "qdiv", "divyears", "buyback", "burn", "treasury",
         "growth", "mgmt", "brand"]
N_ITEMS = len(ORDER)
N_MANUAL = len(MANUAL)

GRADES = [("A", "장기투자 적합, 적극 매수"), ("B", "장기투자 적합, 매수 고려"),
          ("C", "장기투자 유지 (홀딩)"), ("D", "장기투자 추천하지 않음")]


def grade(total):
    """80점 초과 A · 70~80 B · 50~70 미만 C · 50 미만 D"""
    if total > 80:
        return GRADES[0]
    if total >= 70:
        return GRADES[1]
    if total >= 50:
        return GRADES[2]
    return GRADES[3]


# ---------------- 자동 항목 계산 ----------------
def points_per(per):
    if per is None:
        return None
    if per <= 0:                 # 표에 없는 경우: 적자라 PER이 없거나 음수면 0점
        return 0
    return 20 if per < 5 else 15 if per < 8 else 10 if per < 10 else 5


def points_pbr(pbr):
    if pbr is None:
        return None
    if pbr <= 0:                 # 표에 없는 경우: 자본잠식 등으로 0 이하면 0점
        return 0
    return 5 if pbr < 0.3 else 4 if pbr < 0.6 else 3 if pbr < 1.0 else 0


def points_dy(dy):
    if dy is None:
        return None
    if dy <= 0:                  # 표에 없는 경우: 배당을 안 하면 0점 ('3% 이하 2점'은 배당이 있을 때만)
        return 0
    return 10 if dy > 7 else 7 if dy > 5 else 5 if dy > 3 else 2


def auto_points(vals):
    """{per, pbr, div} → {per: 점수|None, pbr: …, dy: …}"""
    vals = vals or {}
    return {"per": points_per(vals.get("per")), "pbr": points_pbr(vals.get("pbr")), "dy": points_dy(vals.get("div"))}


def auto_total(vals):
    """자동 3항목 합계 (값이 없는 항목은 0으로 치고, 몇 개를 못 채웠는지 같이 돌려줌)"""
    ap = auto_points(vals)
    return sum(v or 0 for v in ap.values()), sum(1 for v in ap.values() if v is None)


# ---------------- 전체 계산 ----------------
def compute(vals, answers):
    """vals: {per, pbr, div}, answers: {키: 값} → 항목별 점수와 합계.
    값을 모르는(미입력·데이터 없음) 항목은 0점으로 더하지 않고 '미확정'으로 두고, 최대 가능 점수를 같이 계산."""
    ap = auto_points(vals)
    shown = {"per": vals.get("per"), "pbr": vals.get("pbr"), "dy": vals.get("div")}
    items = {}
    for key, sec, name, mx, rule in AUTO:
        v = shown[key]
        unit = {"per": "배", "pbr": "배", "dy": "%"}[key]
        items[key] = dict(key=key, section=sec, name=name, max=mx, points=ap[key], auto=True, rule=rule,
                          text=("-" if v is None else f"{v:,.2f}{unit}"))
    for key, sec, name, mx, opts, help_ in MANUAL:
        got = next((o for o in opts if o[0] == (answers or {}).get(key)), None)
        items[key] = dict(key=key, section=sec, name=name, max=mx, points=got[2] if got else None, auto=False,
                          help=help_, text=got[1] if got else "미입력", options=opts, value=got[0] if got else "")
    ordered = [items[k] for k in ORDER]
    known = sum(i["points"] for i in ordered if i["points"] is not None)
    missing = [i for i in ordered if i["points"] is None]
    best = known + sum(i["max"] for i in missing)
    sections = {}
    for sec in SECTIONS:
        its = [i for i in ordered if i["section"] == sec]
        sections[sec] = dict(name=SECTIONS[sec], items=its, max=sum(i["max"] for i in its),
                             got=sum(i["points"] for i in its if i["points"] is not None))
    return dict(items=ordered, sections=sections, total=known, best=best, full=not missing, missing=missing,
                grade=grade(known), grade_best=grade(best), n_known=N_ITEMS - len(missing))


# ---------------- 저장 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS scorecard (code TEXT, item TEXT, value TEXT, updated TEXT, PRIMARY KEY (code, item));
    CREATE TABLE IF NOT EXISTS scorecard_stock (code TEXT PRIMARY KEY, name TEXT, updated TEXT);
    """)


def save(conn, code, name, answers):
    """직접 고른 값 저장. 빈 값('미입력')이면 그 항목을 지움. 표에 없는 값은 무시."""
    ensure(conn)
    now = dt.date.today().isoformat()
    for key, _, _, _, opts, _ in MANUAL:
        v = (answers.get(key) or "").strip()
        if v and v in {o[0] for o in opts}:
            conn.execute("INSERT OR REPLACE INTO scorecard VALUES (?,?,?,?)", (code, key, v, now))
        else:
            conn.execute("DELETE FROM scorecard WHERE code=? AND item=?", (code, key))
    if conn.execute("SELECT COUNT(*) FROM scorecard WHERE code=?", (code,)).fetchone()[0]:
        conn.execute("INSERT OR REPLACE INTO scorecard_stock VALUES (?,?,?)", (code, (name or "")[:60], now))
    else:
        conn.execute("DELETE FROM scorecard_stock WHERE code=?", (code,))
    conn.commit()


def load(conn, code):
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT item, value FROM scorecard WHERE code=?", (code,))}
    except sqlite3.OperationalError:
        return {}


def evaluated(conn):
    """직접 평가한 종목 [(code, name)] — 최근 평가순"""
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT code, name FROM scorecard_stock ORDER BY updated DESC, code")]
    except sqlite3.OperationalError:
        return []


def load_all(conn):
    """{code: {키: 값}}"""
    out = {}
    try:
        for code, item, value in conn.execute("SELECT code, item, value FROM scorecard"):
            out.setdefault(code, {})[item] = value
    except sqlite3.OperationalError:
        pass
    return out
