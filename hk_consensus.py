# -*- coding: utf-8 -*-
"""
한경컨센서스(consensus.hankyung.com) 기업 리포트 목록 → 증권사 목표주가

- 리포트 '목록'의 작성일·제목·목표가(적정가격)·투자의견·작성자·증권사만 가져옵니다 (PDF 본문은 받지 않음).
- 처음엔 최근 180일, 그다음부터는 마지막으로 받은 날 이후만 받습니다. 사이트에 부담이 없게 1초에 1쪽.
- 표의 열 순서가 바뀌어도 열 이름(작성일·제목·적정가격·투자의견·작성자·제공출처)으로 찾습니다.
"""
import re, time, sqlite3, datetime as dt
from pathlib import Path

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://consensus.hankyung.com/analysis/list"
SITE = "https://consensus.hankyung.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Referer": SITE + "/analysis/list"}
PAGE_SIZE = 80
DATE_RE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")


class HKError(Exception):
    pass


def _text(el):
    return el.get_text(" ", strip=True) if el else ""


def decode(content):
    for enc in ("utf-8", "cp949"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            pass
    return content.decode("utf-8", "ignore")


def _col(headers, *names):
    for i, h in enumerate(headers):
        if any(n in h for n in names):
            return i
    return None


def parse_list(html):
    """목록 HTML → [dict(date, code, name, title, target, opinion, author, broker, pdf)]"""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        heads = [_text(th) for th in table.find_all("th")]
        c_target = _col(heads, "적정가격", "목표가", "목표주가")
        c_date = _col(heads, "작성일", "일자", "날짜")
        c_title = _col(heads, "제목")
        if c_target is None or c_date is None or c_title is None:
            continue
        c_op, c_au, c_br = _col(heads, "투자의견", "의견"), _col(heads, "작성자"), _col(heads, "제공출처", "증권사", "출처")
        for tr in table.find_all("tr"):
            tds = tr.find_all("td", recursive=False) or tr.find_all("td")
            if len(tds) <= max(c_target, c_date, c_title):
                continue
            m = DATE_RE.search(_text(tds[c_date]))
            if not m:
                continue
            title_td = tds[c_title]
            a = title_td.find("strong") or title_td.find("a") or title_td
            title = _text(a)
            code = None
            mc = re.search(r"\((\d{6})\)", title)
            if mc:
                code = mc.group(1)
            else:
                for link in tr.find_all("a", href=True):
                    mm = re.search(r"(?:business_code|code)=(\d{6})", link["href"])
                    if mm:
                        code = mm.group(1)
                        break
            if not code:
                continue
            name = title.split("(")[0].strip()
            digits = re.sub(r"[^\d]", "", _text(tds[c_target]))
            target = int(digits) if digits and int(digits) > 0 else None
            pdf = ""
            for link in tr.find_all("a", href=True):
                if "downpdf" in link["href"] or link["href"].lower().endswith(".pdf"):
                    pdf = link["href"] if link["href"].startswith("http") else SITE + link["href"]
                    break
            out.append(dict(
                date=f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", code=code, name=name,
                title=title, target=target,
                opinion=_text(tds[c_op]) if c_op is not None and c_op < len(tds) else "",
                author=_text(tds[c_au]) if c_au is not None and c_au < len(tds) else "",
                broker=_text(tds[c_br]) if c_br is not None and c_br < len(tds) else "",
                pdf=pdf))
    return out


def fetch_page(session, sdate, edate, page):
    r = session.get(LIST_URL, headers=HEADERS, timeout=30, params=dict(
        sdate=sdate, edate=edate, now_page=page, search_value="", report_type="CO",
        pagenum=PAGE_SIZE, search_text="", business_code=""))
    if r.status_code != 200:
        raise HKError(f"한경컨센서스 응답 HTTP {r.status_code}")
    return decode(r.content)


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS hk_reports (
        date TEXT, code TEXT, name TEXT, title TEXT, target INTEGER, opinion TEXT,
        author TEXT, broker TEXT, pdf TEXT, PRIMARY KEY (date, code, broker, title))""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hk_code ON hk_reports(code, date)")


def sync(db_path, debug_dir=None, first_days=180, log=print, stop=None, session=None):
    """새 리포트를 받아 hk_reports 표에 저장. 새로 저장한 건수를 돌려줌."""
    session = session or requests.Session()
    conn = sqlite3.connect(db_path)
    try:
        ensure_table(conn)
        last = conn.execute("SELECT MAX(date) FROM hk_reports").fetchone()[0]
        today = dt.date.today()
        since = (dt.date.fromisoformat(last) - dt.timedelta(days=3)) if last else today - dt.timedelta(days=first_days)
        sdate, edate = since.isoformat(), today.isoformat()
        log(f"[목표주가] 한경컨센서스 {sdate}~{edate} 기업 리포트 받는 중")
        added, page, prev_first = 0, 1, None
        while page <= 400:
            if stop is not None and stop.is_set():
                break
            html = fetch_page(session, sdate, edate, page)
            rows = parse_list(html)
            if not rows:
                if page == 1 and len(html) > 2000 and debug_dir:
                    # 목록이 비어 있는데 페이지는 왔으면 형식이 바뀐 것: 확인용으로 저장
                    p = Path(debug_dir) / "hk_debug.html"
                    p.write_text(html, encoding="utf-8")
                    raise HKError(f"한경컨센서스 목록 형식을 읽지 못했습니다. 확인용 파일: {p}")
                break
            key = (rows[0]["date"], rows[0]["title"])
            if key == prev_first:    # 마지막 쪽을 넘어서 같은 쪽이 반복됨
                break
            prev_first = key
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO hk_reports VALUES (?,?,?,?,?,?,?,?,?)",
                             [(r["date"], r["code"], r["name"], r["title"], r["target"], r["opinion"],
                               r["author"], r["broker"], r["pdf"]) for r in rows])
            conn.commit()
            added += conn.total_changes - before
            if min(r["date"] for r in rows) < sdate:
                break
            page += 1
            time.sleep(1.0)
        log(f"[목표주가] 새 리포트 {added}건 ({page}쪽)")
        return added
    finally:
        conn.close()


def consensus(conn, code, days=180):
    """증권사별 가장 최근 목표가(최근 days일)의 평균. (평균, 증권사 수) 또는 (None, 0)"""
    try:
        since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT broker, target FROM hk_reports WHERE code=? AND date>=? AND target IS NOT NULL "
            "ORDER BY date DESC", (code, since)).fetchall()
    except sqlite3.OperationalError:
        return None, 0
    latest = {}
    for broker, target in rows:
        latest.setdefault(broker or "?", target)
    if not latest:
        return None, 0
    return sum(latest.values()) / len(latest), len(latest)
