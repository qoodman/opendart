# -*- coding: utf-8 -*-
"""
네이버 증권 리서치 → 종목분석 리포트 (finance.naver.com/research/company_list.naver)

- 목록에서 종목·제목·증권사·PDF·작성일을 받고, 목표가·투자의견은 리포트 상세 페이지에서 읽습니다.
- 처음엔 최근 30일, 그다음부터는 마지막으로 받은 날 이후만. 상세는 한 번에 최대 400건(나머지는 다음 수집 때).
- 사이트에 부담이 없게 요청 사이에 쉼을 둡니다.
"""
import re, time, sqlite3, datetime as dt
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import research
from hk_consensus import decode

SITE = "https://finance.naver.com"
LIST_URL = SITE + "/research/company_list.naver"
READ_URL = SITE + "/research/company_read.naver"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Referer": LIST_URL}
DETAIL_LIMIT = 400
PAUSE = 0.4
DATE_RE = re.compile(r"(\d{2,4})\.(\d{1,2})\.(\d{1,2})")


class NVError(Exception):
    pass


def _soup(html):
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _date(text):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    y = int(m.group(1))
    y = y + 2000 if y < 100 else y
    return f"{y}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def parse_list(html):
    """목록 → [dict(date, code, name, title, broker, pdf, url, nid)]"""
    out = []
    for tr in _soup(html).find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        stock = tr.find("a", href=re.compile(r"code=\d{6}"))
        read = tr.find("a", href=re.compile(r"company_read\.naver"))
        if not stock or not read:
            continue
        code = re.search(r"code=(\d{6})", stock["href"]).group(1)
        date = None
        for td in reversed(tds):           # 작성일은 뒤쪽 칸 (조회수 앞)
            date = _date(td.get_text(strip=True))
            if date:
                break
        if not date:
            continue
        nid = re.search(r"nid=(\d+)", read["href"])
        # 증권사: 링크·첨부가 없는 글자 칸 중 제목 다음 칸
        cells = [td for td in tds if not td.find("a") and not td.find("img")]
        broker = ""
        for td in cells:
            t = td.get_text(strip=True)
            if t and not DATE_RE.search(t) and not t.replace(",", "").isdigit():
                broker = t
                break
        pdf = tr.find("a", href=re.compile(r"\.pdf", re.I))
        out.append(dict(
            date=date, code=code, name=stock.get_text(strip=True) or stock.get("title", ""),
            title=read.get_text(" ", strip=True), broker=broker,
            pdf=pdf["href"] if pdf else "", nid=nid.group(1) if nid else "",
            url=f"{READ_URL}?nid={nid.group(1)}" if nid else ""))
    return out


def parse_detail(html):
    """상세 페이지 → (목표가 또는 None, 투자의견)"""
    soup = _soup(html)
    box = soup.find(class_=re.compile("view_info")) or soup
    text = box.get_text(" ", strip=True)
    target = None
    m = re.search(r"목표\s*가[^\d]{0,12}([\d,]{3,})", text)
    if m:
        v = int(m.group(1).replace(",", ""))
        target = v if v > 0 else None
    op = ""
    m = re.search(r"투자\s*의견\s*[:|]?\s*([가-힣A-Za-z\-]+(?:\s?[A-Za-z]+)?)", text)
    if m and m.group(1) not in ("없음",):
        op = m.group(1)
    return target, op


def sync(db_path, debug_dir=None, log=print, first_days=30, session=None):
    session = session or requests.Session()
    conn = sqlite3.connect(db_path)
    try:
        research.ensure_table(conn)
        last = research.last_date(conn, "nv")
        today = dt.date.today()
        since = ((dt.date.fromisoformat(last) - dt.timedelta(days=2)) if last
                 else today - dt.timedelta(days=first_days)).isoformat()
        log(f"[리포트] 네이버 증권 {since} 이후 종목분석 리포트 받는 중")
        added, page, prev = 0, 1, None
        while page <= 300:
            r = session.get(LIST_URL, params={"page": page}, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                raise NVError(f"네이버 증권 응답 HTTP {r.status_code}")
            html = decode(r.content)
            rows = parse_list(html)
            if not rows:
                if page == 1 and debug_dir and len(html) > 2000:
                    p = Path(debug_dir) / "nv_debug.html"
                    p.write_text(html, encoding="utf-8")
                    raise NVError(f"네이버 증권 목록 형식을 읽지 못했습니다. 확인용 파일: {p}")
                break
            key = (rows[0]["date"], rows[0]["title"])
            if key == prev:
                break
            prev = key
            fresh = [dict(x, detail=0) for x in rows if x["date"] >= since]
            added += research.store(conn, "nv", fresh)
            if min(x["date"] for x in rows) < since:
                break
            page += 1
            time.sleep(PAUSE)
        # 목표가·투자의견: 상세를 아직 안 읽은 것부터 최신순으로
        todo = conn.execute("SELECT rowid, url FROM reports WHERE source='nv' AND detail=0 AND url!='' "
                            "ORDER BY date DESC LIMIT ?", (DETAIL_LIMIT,)).fetchall()
        got = 0
        for rowid, url in todo:
            try:
                d = session.get(url, headers=HEADERS, timeout=30)
                target, op = parse_detail(decode(d.content)) if d.status_code == 200 else (None, "")
            except requests.exceptions.RequestException:
                continue
            conn.execute("UPDATE reports SET target=?, opinion=?, detail=1 WHERE rowid=?", (target, op, rowid))
            got += target is not None
            if got % 50 == 0:
                conn.commit()
            time.sleep(PAUSE)
        conn.commit()
        left = conn.execute("SELECT COUNT(*) FROM reports WHERE source='nv' AND detail=0").fetchone()[0]
        log(f"[리포트] 네이버 증권 새 리포트 {added}건, 목표가 읽음 {got}/{len(todo)}건"
            + (f" (남은 {left}건은 다음 수집 때)" if left else ""))
        return added
    finally:
        conn.close()
