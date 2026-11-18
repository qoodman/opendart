# -*- coding: utf-8 -*-
"""
법안 ↔ 개별 종목 연결과 반응 확인.

예) 글로벌텍스프리: 외국인 관광객이 미용성형 수술·시술을 받으면 낸 부가세 10%를 공항 등에서 돌려받는 제도(환급특례)의 일몰을 연장하자는 법안.
  법안 제목이 '조세특례제한법 일부개정법률안'처럼 평범해서 제목만으로는 알 수 없어요. 그래서 세 가지를 같이 써요.
  1) 감시 규칙: 종목 + 법안 이름 검색어(법 이름) + 본문 키워드. 국회에서 그 법 이름의 법안을 받아 와 제안이유 본문에 키워드가 있으면 그 종목에 자동 연결해요.
  2) 후보와 수동 연결: 본문을 못 읽었거나 키워드가 안 맞아도, 법 이름이 맞는 법안은 '후보'로 보여 주고 버튼으로 직접 종목에 연결할 수 있어요.
  3) 반응: 연결된 법안의 단계별 날짜(발의·소관위·법사위·본회의 처리 등)마다 그 종목이 같은 기간 지수(코스닥/코스피)보다 얼마나 더 움직였나를 보여 줘요
     (선반영 5일 · 이벤트 후 1·5·20거래일, 지수 대비 초과 %p).
주의: 법안은 발의 전에 이미 소문으로 반영됐을 수 있고, 통과돼도 시행은 나중이며, 같은 시기 다른 재료(실적·테마·시장 전체)가 훨씬 클 수 있어요.
  사례가 몇 개뿐이라 '법안 때문에 올랐다'고 읽으면 안 되는 참고 자료예요. 사고팔 시점을 알려주지 않아요.
"""
import datetime as dt
import json
import re
import sqlite3
import time

import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
PRICE_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
BASIC_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
PRICE_DAYS = 2200
PRICE_REFRESH_DAYS = 1
TEXT_PER_RUN = 15
MAX_PAGES = 3

SEED_RULE = dict(
    name="글로벌텍스프리 — 외국인 관광객 미용성형 부가세 환급(환급특례) 일몰 연장",
    law_names=["조세특례제한법", "부가가치세법", "관광진흥법"],
    keywords=["외국인관광객", "의료관광", "미용성형", "환급특례", "사후면세", "부가가치세 환급", "외국인 환자"],
    stocks=["204620"],
)

# 국회 법안 API 필드 → 단계 이름
STAGE_FIELDS = (("PROPOSE_DT", "발의"), ("COMMITTEE_DT", "소관위 회부"), ("CMT_PRESENT_DT", "소관위 상정"),
                ("CMT_PROC_DT", "소관위 처리"), ("LAW_PROC_DT", "법사위 처리"), ("PROC_DT", "본회의 처리"))


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS bill_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, law_names TEXT, keywords TEXT, codes TEXT, active INTEGER DEFAULT 1,
        baselined INTEGER DEFAULT 0, created TEXT);
    CREATE TABLE IF NOT EXISTS bill_track (bill_key TEXT, date TEXT, stage TEXT, PRIMARY KEY (bill_key, stage));
    CREATE TABLE IF NOT EXISTS bill_links (bill_key TEXT, code TEXT, source TEXT, PRIMARY KEY (bill_key, code));
    CREATE TABLE IF NOT EXISTS bill_text (bill_key TEXT PRIMARY KEY, fetched TEXT, text TEXT);
    CREATE TABLE IF NOT EXISTS bill_stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT, px_fetched TEXT);
    CREATE TABLE IF NOT EXISTS bill_px (code TEXT, d TEXT, close REAL, PRIMARY KEY (code, d));
    CREATE TABLE IF NOT EXISTS bill_alerted (k TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS bill_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


# ---------------- 규칙·연결 ----------------
def _norm(t):
    return re.sub(r"\s+", "", t or "")


def add_rule(conn, name, law_names, keywords, codes):
    ensure(conn)
    cur = conn.execute("INSERT INTO bill_rules (name, law_names, keywords, codes, created) VALUES (?,?,?,?,?)",
                       (name.strip(), json.dumps(law_names, ensure_ascii=False), json.dumps(keywords, ensure_ascii=False),
                        json.dumps(codes), dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    return cur.lastrowid


def rules(conn):
    try:
        rows = conn.execute("SELECT id, name, law_names, keywords, codes, active, baselined FROM bill_rules ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(id=i, name=n, law_names=json.loads(l or "[]"), keywords=json.loads(k or "[]"), codes=json.loads(c or "[]"),
                 active=bool(a), baselined=bool(b)) for i, n, l, k, c, a, b in rows]


def del_rule(conn, rule_id):
    conn.execute("DELETE FROM bill_links WHERE source=?", (f"규칙:{rule_id}",))
    conn.execute("DELETE FROM bill_rules WHERE id=?", (rule_id,))
    conn.commit()


def link(conn, bill_key, code, source="수동"):
    """법안을 종목에 연결. 수동 연결이면 이미 있는 단계는 '이미 알린 것'으로 쳐서, 그 뒤 새 단계부터 알림을 보냄"""
    ensure(conn)
    conn.execute("INSERT OR REPLACE INTO bill_links VALUES (?,?,?)", (bill_key, code, source))
    if source == "수동":
        conn.execute("INSERT OR IGNORE INTO bill_alerted VALUES (?)", (f"link:{bill_key}:{code}",))
        for _, st in tracks(conn, bill_key):
            conn.execute("INSERT OR IGNORE INTO bill_alerted VALUES (?)", (f"stage:{bill_key}:{st}",))
    conn.commit()


def unlink(conn, bill_key, code):
    conn.execute("DELETE FROM bill_links WHERE bill_key=? AND code=?", (bill_key, code))
    conn.commit()


def match_rule(rule, title, text):
    """법안 제목+제안이유에 규칙의 본문 키워드가 있으면 맞은 키워드 목록 (없으면 [])"""
    body = _norm(f"{title} {text}")
    return [k for k in rule["keywords"] if _norm(k) and _norm(k) in body]


def law_name_hit(rule, title):
    t = _norm(title)
    return any(_norm(n) and _norm(n) in t for n in rule["law_names"])


# ---------------- 종목 정보·주가 ----------------
def stock_info(code, session=None):
    """네이버에서 (이름, 시장 'KOSDAQ'/'KOSPI'). 실패하면 (None, None)"""
    try:
        r = (session or requests).get(BASIC_URL.format(code=code), headers=HEADERS, timeout=15)
        r.raise_for_status()
        d = r.json()
        return d.get("stockName"), (d.get("stockExchangeType") or {}).get("name")
    except Exception:
        return None, None


def ensure_stock(conn, code, name="", session=None):
    ensure(conn)
    row = conn.execute("SELECT name, market FROM bill_stocks WHERE code=?", (code,)).fetchone()
    if row and row[1]:
        return row
    nm, mk = stock_info(code, session)
    nm = nm or name or code
    conn.execute("INSERT OR REPLACE INTO bill_stocks (code, name, market, px_fetched) VALUES (?,?,?,COALESCE((SELECT px_fetched FROM bill_stocks WHERE code=?),''))",
                 (code, nm, mk or "", code))
    conn.commit()
    return nm, mk or ""


def fetch_prices(code, today=None, session=None):
    today = today or dt.date.today()
    r = (session or requests).get(PRICE_URL.format(code=code), headers=HEADERS, timeout=30,
                                  params={"startDateTime": (today - dt.timedelta(days=PRICE_DAYS)).strftime("%Y%m%d"),
                                          "endDateTime": (today + dt.timedelta(days=1)).strftime("%Y%m%d")})
    r.raise_for_status()
    out = []
    for x in r.json():
        d, c = str(x.get("localDate", "")), x.get("closePrice")
        if len(d) == 8 and c:
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(c)))
    return sorted(out)


def refresh_prices(conn, code, today=None, session=None, force=False):
    ensure(conn)
    today = today or dt.date.today()
    row = conn.execute("SELECT px_fetched FROM bill_stocks WHERE code=?", (code,)).fetchone()
    if row and row[0] and not force and row[0] >= (today - dt.timedelta(days=PRICE_REFRESH_DAYS)).isoformat():
        return 0
    rows = fetch_prices(code, today, session)
    conn.executemany("INSERT OR REPLACE INTO bill_px VALUES (?,?,?)", [(code, d, c) for d, c in rows])
    conn.execute("UPDATE bill_stocks SET px_fetched=? WHERE code=?", (today.isoformat(), code))
    conn.commit()
    return len(rows)


def prices(conn, code):
    try:
        return conn.execute("SELECT d, close FROM bill_px WHERE code=? ORDER BY d", (code,)).fetchall()
    except sqlite3.OperationalError:
        return []


# ---------------- 법안 본문 ----------------
def extract_summary(html):
    """국회 의안정보 상세 페이지 HTML → '제안이유 및 주요내용' 본문 (없으면 '')"""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    t = re.sub(r"\s+", " ", t)
    m = re.search(r"제안이유\s*및\s*주요\s*내용", t)
    if not m:
        m = re.search(r"제안이유", t)
    if not m:
        return ""
    body = t[m.end(): m.end() + 1800]
    for stop in ("관련법률", "검토보고서", "심사진행단계", "의안접수 정보", "회의정보"):
        i = body.find(stop)
        if i > 20:
            body = body[:i]
    return body.strip()


def fetch_text(url, session=None):
    r = (session or requests).get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    r.raise_for_status()
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = "utf-8"
    return extract_summary(r.text)


# ---------------- 법안 기록 (단계별 날짜) ----------------
def record_tracks(conn, key, row):
    """국회 API 한 줄에서 단계별 날짜를 저장 → 새로 저장한 단계 수"""
    ensure(conn)
    n = 0
    for field, stage in STAGE_FIELDS:
        d = str(row.get(field) or "").strip()
        if not d or not re.match(r"\d{4}-\d{2}-\d{2}", d[:10]):
            continue
        label = stage + (f"({row.get('PROC_RESULT')})" if field == "PROC_DT" and row.get("PROC_RESULT") else "")
        cur = conn.execute("INSERT OR IGNORE INTO bill_track VALUES (?,?,?)", (key, d[:10], label))
        n += cur.rowcount
    return n


def tracks(conn, bill_key):
    try:
        return conn.execute("SELECT date, stage FROM bill_track WHERE bill_key=? ORDER BY date", (bill_key,)).fetchall()
    except sqlite3.OperationalError:
        return []


# ---------------- 반응 ----------------
def _at(pts, idx):
    return pts[idx][1] if 0 <= idx < len(pts) else None


def _idx_on_or_before(pts, day):
    last = None
    for d, v in pts:
        if d > day:
            break
        last = v
    return last


def reaction(stock_pts, index_pts, event_date):
    """event_date(YYYY-MM-DD) 기준: 이벤트 후 첫 거래일 t, 기준가 = t 직전 종가.
    → dict(day, pre5, r1, r5, r20, ab5pre, ab1, ab5, ab20) (% / %p, 계산 못 하는 값은 None). 주가 자료가 이벤트 전후로 부족하면 None"""
    if not stock_pts:
        return None
    t = next((i for i, (d, _) in enumerate(stock_pts) if d >= event_date), None)
    if t is None or t < 6:                                  # 이벤트가 마지막 자료 뒤이거나 앞쪽 자료가 부족
        return None
    base, base_d = stock_pts[t - 1][1], stock_pts[t - 1][0]
    out = dict(day=stock_pts[t][0])

    def ret(i_from, i_to):
        a, b = _at(stock_pts, i_from), _at(stock_pts, i_to)
        if not a or not b:
            return None, None
        r = (b / a - 1) * 100
        ia, ib = _idx_on_or_before(index_pts, stock_pts[i_from][0]), _idx_on_or_before(index_pts, stock_pts[i_to][0])
        ab = r - (ib / ia - 1) * 100 if ia and ib else None
        return r, ab
    out["pre5"], out["ab5pre"] = ret(t - 6, t - 1)
    for h in (1, 5, 20):
        out[f"r{h}"], out[f"ab{h}"] = ret(t - 1, t - 1 + h) if t - 1 + h < len(stock_pts) else (None, None)
    return out


def summary_for_stock(conn, code, index_pts):
    """종목에 연결된 법안 이벤트들의 평균: {n, ab1, ab5, ab20 (평균 초과 %p), up(+5일 초과가 +인 비율)}"""
    ev = events_for_code(conn, code)
    px = prices(conn, code)
    rs = [reaction(px, index_pts, e["date"]) for e in ev]
    rs = [r for r in rs if r]

    def avg(k):
        v = [r[k] for r in rs if r.get(k) is not None]
        return sum(v) / len(v) if v else None
    v5 = [r["ab5"] for r in rs if r.get("ab5") is not None]
    return dict(n=len(rs), ab5pre=avg("ab5pre"), ab1=avg("ab1"), ab5=avg("ab5"), ab20=avg("ab20"),
                up=(100.0 * sum(1 for x in v5 if x > 0) / len(v5)) if v5 else None)


def events_for_code(conn, code):
    """이 종목에 연결된 법안들의 단계 이벤트 [{bill_key, date, stage}]"""
    try:
        return [dict(bill_key=b, date=d, stage=s) for b, d, s in conn.execute(
            "SELECT t.bill_key, t.date, t.stage FROM bill_track t JOIN bill_links l ON l.bill_key=t.bill_key WHERE l.code=? ORDER BY t.date", (code,))]
    except sqlite3.OperationalError:
        return []


# ---------------- 동기화 ----------------
def _bill_row_to_item(r):
    from stock_policy import _norm_date, topics_of
    bid = r.get("BILL_ID") or r.get("BILL_NO")
    result = (r.get("PROC_RESULT") or "").strip() or "위원회 심사 중"
    who = (r.get("PROPOSER") or "").strip()
    title = f"{r.get('BILL_NAME', '')}{f' ({who})' if who else ''} · {result}"
    date = _norm_date(r.get("PROC_DT") or r.get("PROPOSE_DT") or "")
    tps = topics_of(r.get("BILL_NAME", ""), "", "") or ["자본시장 제도"]
    return bid, f"bill:{bid}", title, date, tps, result, r.get("DETAIL_LINK") or ""


def sync(db_path, assembly_key, log=print, session=None, today=None, text_fetcher=None, price_fetch=True):
    """감시 규칙마다 국회 법안을 받아 정책 소식 표에 넣고, 단계 날짜를 기록하고, 본문 키워드로 종목에 연결하고, 연결 종목 주가를 받음.
    → {"규칙": n, "법안": n, "새 연결": n, "본문": n, "주가": n, "알림": [문구...]}"""
    import stock_policy as sp
    conn = sqlite3.connect(db_path)
    out = dict(규칙=0, 법안=0, 새연결=0, 본문=0, 주가=0, 알림=[])
    fetcher = text_fetcher or fetch_text
    try:
        ensure(conn)
        sp.ensure(conn)
        if not conn.execute("SELECT value FROM bill_meta WHERE key='seeded'").fetchone():
            if not conn.execute("SELECT 1 FROM bill_rules").fetchone():
                add_rule(conn, SEED_RULE["name"], SEED_RULE["law_names"], SEED_RULE["keywords"], SEED_RULE["stocks"])
            conn.execute("INSERT OR REPLACE INTO bill_meta VALUES ('seeded', '1')")
            conn.commit()
        rs = [r for r in rules(conn) if r["active"]]
        out["규칙"] = len(rs)
        api = sp.Client("", "", assembly_key) if assembly_key else None
        texts_left = TEXT_PER_RUN
        for rule in rs:
            for code in rule["codes"]:
                try:
                    ensure_stock(conn, code, session=session)
                except Exception as e:
                    log(f"법안 감시: 종목 {code} 정보 실패 {type(e).__name__}")
            bills = {}
            for law in rule["law_names"]:
                if not api:
                    break
                for page in range(1, MAX_PAGES + 1):
                    try:
                        rows = api.bills(law, page)
                    except (requests.exceptions.RequestException, ValueError) as e:
                        log(f"법안 감시: {law} 검색 실패 ({type(e).__name__})")
                        break
                    for r in rows:
                        bid = r.get("BILL_ID") or r.get("BILL_NO")
                        if bid:
                            bills.setdefault(bid, r)
                    if len(rows) < 100:
                        break
            out["법안"] += len(bills)
            first = not conn.execute("SELECT 1 FROM policy_items WHERE kind='법안' LIMIT 1").fetchone()
            for bid, r in bills.items():
                _, key, title, date, tps, result, url = _bill_row_to_item(r)
                sp._upsert(conn, key, "법안", "국회", date, title, url, "", tps, first, status=result)
                record_tracks(conn, key, r)
            conn.commit()
            # 본문: 법 이름이 맞는 법안만, 아직 못 받은 것을 한 번에 몇 개씩
            for bid, r in bills.items():
                key = f"bill:{bid}"
                if texts_left <= 0:
                    break
                if not law_name_hit(rule, r.get("BILL_NAME", "")) or conn.execute("SELECT 1 FROM bill_text WHERE bill_key=?", (key,)).fetchone():
                    continue
                url = r.get("DETAIL_LINK") or ""
                if not url:
                    continue
                texts_left -= 1
                try:
                    txt = fetcher(url, session)
                except Exception as e:
                    log(f"법안 본문 실패 ({str(r.get('BILL_NAME', ''))[:20]}): {type(e).__name__}")
                    continue
                conn.execute("INSERT OR REPLACE INTO bill_text VALUES (?,?,?)", (key, (today or dt.date.today()).isoformat(), txt))
                out["본문"] += 1
            conn.commit()
            # 매칭 → 연결
            for bid, r in bills.items():
                key = f"bill:{bid}"
                row = conn.execute("SELECT text FROM bill_text WHERE bill_key=?", (key,)).fetchone()
                if match_rule(rule, r.get("BILL_NAME", ""), row[0] if row else ""):
                    for code in rule["codes"]:
                        if not conn.execute("SELECT 1 FROM bill_links WHERE bill_key=? AND code=?", (key, code)).fetchone():
                            link(conn, key, code, f"규칙:{rule['id']}")
                            out["새연결"] += 1
            # 본문을 못 읽어 자동 연결을 못 한 새 후보(법 이름만 맞음)는 요약 한 줄로 알려서 직접 확인하게 함
            cut = ((today or dt.date.today()) - dt.timedelta(days=14)).isoformat()
            new_c = []
            for bid, r in bills.items():
                key = f"bill:{bid}"
                if not law_name_hit(rule, r.get("BILL_NAME", "")) or str(r.get("PROPOSE_DT") or "")[:10] < cut:
                    continue
                row = conn.execute("SELECT text FROM bill_text WHERE bill_key=?", (key,)).fetchone()
                if (row and row[0]) or conn.execute("SELECT 1 FROM bill_links WHERE bill_key=?", (key,)).fetchone():
                    continue
                if conn.execute("SELECT 1 FROM bill_alerted WHERE k=?", (f"cand:{key}",)).fetchone():
                    continue
                conn.execute("INSERT INTO bill_alerted VALUES (?)", (f"cand:{key}",))
                new_c.append(str(r.get("BILL_NAME", ""))[:40])
            if new_c and rule["baselined"]:
                out["알림"].append(f"[후보] {new_c[0]}" + (f" 외 {len(new_c) - 1}건" if len(new_c) > 1 else "")
                                  + f" — 제안이유를 못 읽어 자동 연결 못 함 ({rule['name'][:20]}), 법안·종목 탭에서 확인")
            # 기준선: 규칙을 처음 돌릴 때는 지금까지의 연결·단계를 '이미 알린 것'으로 침
            if not rule["baselined"]:
                for key, code in conn.execute("SELECT bill_key, code FROM bill_links WHERE source=?", (f"규칙:{rule['id']}",)).fetchall():
                    conn.execute("INSERT OR IGNORE INTO bill_alerted VALUES (?)", (f"link:{key}:{code}",))
                    for _, st in tracks(conn, key):
                        conn.execute("INSERT OR IGNORE INTO bill_alerted VALUES (?)", (f"stage:{key}:{st}",))
                conn.execute("UPDATE bill_rules SET baselined=1 WHERE id=?", (rule["id"],))
            conn.commit()
        out["알림"] = out["알림"] + pending_alerts(conn)
        if price_fetch:
            for (code,) in conn.execute("SELECT DISTINCT code FROM bill_links").fetchall():
                try:
                    out["주가"] += 1 if refresh_prices(conn, code, today, session) else 0
                except Exception as e:
                    log(f"법안 감시: 주가 {code} 실패 {type(e).__name__}")
        log(f"법안 감시: 규칙 {out['규칙']} · 법안 {out['법안']} · 새 연결 {out['새연결']} · 본문 {out['본문']} · 주가 {out['주가']} · 알림 {len(out['알림'])}")
        return out
    finally:
        conn.close()


def pending_alerts(conn):
    """연결된 법안의 새 연결·새 단계 알림 문구 목록 (알리고 나면 '알림 처리됨'으로 바꿈)"""
    msgs = []
    names = {c: n for c, n in conn.execute("SELECT code, name FROM bill_stocks")}
    for key, code, src in conn.execute("SELECT bill_key, code, source FROM bill_links").fetchall():
        t = conn.execute("SELECT title FROM policy_items WHERE key=?", (key,)).fetchone()
        title = (t[0] if t else key)[:50]
        k = f"link:{key}:{code}"
        if not conn.execute("SELECT 1 FROM bill_alerted WHERE k=?", (k,)).fetchone():
            conn.execute("INSERT INTO bill_alerted VALUES (?)", (k,))
            msgs.append(f"[연결] {names.get(code, code)} ← {title}")
        for d, st in tracks(conn, key):
            k = f"stage:{key}:{st}"
            if not conn.execute("SELECT 1 FROM bill_alerted WHERE k=?", (k,)).fetchone():
                conn.execute("INSERT INTO bill_alerted VALUES (?)", (k,))
                msgs.append(f"[{st} {d}] {names.get(code, code)} ← {title}")
    conn.commit()
    return msgs


def candidates(conn, days=365):
    """연결 안 된 후보: 감시 규칙의 법 이름이 제목에 들어 있는 최근 법안 [{key, title, date, url, status, rule, has_text}]"""
    try:
        rows = conn.execute("SELECT key, title, date, url, status FROM policy_items WHERE kind='법안' AND key!='_meta' AND date >= ? ORDER BY date DESC",
                            ((dt.date.today() - dt.timedelta(days=days)).isoformat(),)).fetchall()
    except sqlite3.OperationalError:
        return []
    linked = {k for (k,) in conn.execute("SELECT DISTINCT bill_key FROM bill_links")}
    rs = [r for r in rules(conn) if r["active"]]
    texts = {k for (k,) in conn.execute("SELECT bill_key FROM bill_text WHERE text != ''")}
    out = []
    for key, title, date, url, status in rows:
        if key in linked:
            continue
        for rule in rs:
            if law_name_hit(rule, title):
                out.append(dict(key=key, title=title, date=date, url=url, status=status, rule=rule["name"], has_text=key in texts))
                break
    return out


def check(assembly_key, conn=None, session=None):
    """설정 → 연결 확인용: 국회 법안 검색, 법안 상세 페이지 본문 추출, 네이버 종목 정보·주가. [{op, ok, day, total, sample, note}]"""
    import stock_policy as sp
    items = []
    it = dict(op="국회 발의법률안 검색 (조세특례제한법)", ok=False, day="", total=0, sample=None, note="")
    first = None
    if not assembly_key:
        it["note"] = "열린국회정보 키(ASSEMBLY_API_KEY)가 없어요"
    else:
        try:
            rows = sp.Client("", "", assembly_key).bills("조세특례제한법")
            it.update(ok=True, total=len(rows), sample={k: rows[0].get(k) for k in ("BILL_NAME", "PROPOSE_DT", "PROC_RESULT", "COMMITTEE", "DETAIL_LINK", "PROC_DT", "CMT_PROC_DT")} if rows else None,
                      day=(rows[0].get("PROPOSE_DT", "") if rows else ""))
            first = rows[0] if rows else None
            if not rows:
                it["note"] = "받을 수는 있는데 결과가 비어 있어요"
        except Exception as e:
            it["note"] = f"{type(e).__name__}: {str(e)[:140]}"
    items.append(it)
    it = dict(op="국회 발의법률안 전체 목록 (법안 조회용)", ok=False, day="", total=0, sample=None, note="")
    if assembly_key:
        try:
            import bill_feed
            api = sp.Client("", "", assembly_key)
            rows, info = bill_feed.fetch_recent(api, 30, max_pages=3)
            it.update(ok=bool(rows), total=info["total"], day=max((bill_feed._dt(r) for r in rows), default=""),
                      sample={"22대 전체 법안 수": info["total"], "목록 순서": info["order"], "최근 30일(앞 3쪽 안)": len(rows),
                              "가장 최근 법안": (rows[0].get("BILL_NAME") if rows else "-")})
            if not rows:
                it["note"] = "목록은 받았는데 최근 30일 법안이 없어요 (정렬 방식이나 기간을 확인해야 해요 — 이 화면을 캡처해서 보내 주세요)"
        except Exception as e:
            it["note"] = f"{type(e).__name__}: {str(e)[:140]}"
    else:
        it["note"] = "열린국회정보 키가 없어요"
    items.append(it)
    it = dict(op="과거 국회 법안 (입법 주가 분석용: 20·21·22대 범위와 단계 날짜)", ok=False, day="", total=0, sample=None, note="")
    if assembly_key:
        try:
            import bill_history
            pr = bill_history.probe(sp.Client("", "", assembly_key))
            got = [p for p in pr if p["total"]]
            smp = {}
            for p in pr:
                smp[f"{p['age']}대"] = (f"{p['total']:,}건 · 발의 {p['first'] or '-'} ~ {p['last'] or '-'} · 단계 날짜(표본 {p.get('sample_n', 0)}건 중) " +
                                      ", ".join(f"{k} {v}" for k, v in p["fields"].items())) if p["total"] else (p["error"] or "받아지지 않아요 (0건)")
            it.update(ok=bool(got), total=sum(p["total"] for p in pr), day=max((p["last"] for p in pr), default=""), sample=smp)
            if len(got) < len(pr):
                it["note"] = "일부 대는 받아지지 않았어요. 받아지는 대까지만 분석해요 (분석 범위가 좁아져요)."
        except Exception as e:
            it["note"] = f"{type(e).__name__}: {str(e)[:140]}"
    else:
        it["note"] = "열린국회정보 키가 없어요"
    items.append(it)
    it = dict(op="법안 상세 페이지에서 제안이유 읽기", ok=False, day="", total=0, sample=None, note="")
    if first and first.get("DETAIL_LINK"):
        try:
            txt = fetch_text(first["DETAIL_LINK"], session)
            it.update(ok=bool(txt), total=len(txt), sample={"주소": first["DETAIL_LINK"], "읽은 글자 수": len(txt), "앞부분": txt[:160] or "(못 읽음)"})
            if not txt:
                it["note"] = "페이지는 열렸는데 '제안이유' 부분을 못 찾았어요 — 이 경우 본문 키워드 대신 '후보'에서 직접 종목에 연결하세요 (화면을 캡처해서 보내 주시면 읽는 방법을 고칠게요)"
        except Exception as e:
            it["note"] = f"{type(e).__name__}: {str(e)[:140]} — 본문을 못 읽어도 '후보'에서 직접 연결할 수 있어요"
    else:
        it["note"] = "위 검색이 되어야 확인할 수 있어요"
    items.append(it)
    code = SEED_RULE["stocks"][0]
    it = dict(op=f"네이버 종목 정보·주가 ({code})", ok=False, day="", total=0, sample=None, note="")
    nm, mk = stock_info(code, session)
    if nm:
        try:
            px = fetch_prices(code, session=session)
            it.update(ok=bool(px), total=len(px), day=px[-1][0] if px else "", sample={"이름": nm, "시장": mk, "첫 주가일": px[0][0] if px else "-", "마지막 종가": px[-1][1] if px else "-"})
        except Exception as e:
            it["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    else:
        it["note"] = "종목 정보를 못 받았어요"
    items.append(it)
    if conn is not None:
        try:
            nt = conn.execute("SELECT COUNT(*) FROM theme_names").fetchone()[0]
            ns = conn.execute("SELECT COUNT(DISTINCT code) FROM theme_stocks").fetchone()[0]
            ni = conn.execute("SELECT COUNT(*) FROM industry_map").fetchone()[0]
            items.append(dict(op="테마·업종 자료 (법안 → 기업 짐작에 씀)", ok=nt > 0 and ni > 0, day="", total=nt,
                              sample={"테마": nt, "테마에 든 종목": ns, "업종에 매핑된 종목": ni},
                              note="" if nt and ni else "아직 없어요. 지금 수집 → 시장·정책만을 한 번 돌려 주세요."))
        except sqlite3.OperationalError:
            items.append(dict(op="테마·업종 자료 (법안 → 기업 짐작에 씀)", ok=False, day="", total=0, sample=None, note="아직 없어요. 지금 수집 → 시장·정책만을 한 번 돌려 주세요."))
        try:
            n_r, n_l, n_t = (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("bill_rules", "bill_links", "bill_text"))
            items.append(dict(op="이 앱에 저장된 감시 현황", ok=True, day="", total=n_l, sample={"감시 규칙": n_r, "연결된 법안": n_l, "본문을 읽은 법안": n_t}, note=""))
        except sqlite3.OperationalError:
            items.append(dict(op="이 앱에 저장된 감시 현황", ok=False, day="", total=0, sample=None, note="아직 수집 전이에요 (지금 수집 → 시장·정책만)"))
    return items
