# -*- coding: utf-8 -*-
"""
공매도 리포트 자동 수집: 뉴스 검색(구글 뉴스 RSS)에서 숏셀러 보고서 기사 제목을 찾아 '공매도 리포트' 탭에 후보로 올립니다.

- 올라오는 것은 '기사 제목 + 링크 + 기사 날짜'뿐이에요. 보고서 주장·회사 반응·주가 반응은 비어 있고,
  항상 '확인 필요' + '자동 수집' 표시가 붙어요. 원문을 읽고 사람이 채우거나 필요 없으면 지우세요 (지운 기사는 다시 안 올라와요).
- 알려진 공매도 업체 이름이 제목에 있거나, 'short seller'·'숏셀러' 같은 말과 '보고서·주장' 같은 말이 같이 있는 제목만 후보로 삼아요.
  그래도 관련 없는 기사가 섞일 수 있어요.
- 한 번 돌 때 새 후보는 최대 20개, 자동 후보는 최대 60개까지만 두고, 안 건드린 오래된 후보(120일↑)는 정리해요. 하루 한 번만 확인해요.
"""
import datetime as dt
import email.utils
import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

import shortreports as sr

FEEDS = [
    ("\"short seller\" report when:14d", "en-US", "US", "US:en"),
    ("\"short seller\" China OR \"Hong Kong\" report when:30d", "en-US", "US", "US:en"),
    ("\"short seller\" Japan OR Tokyo report when:30d", "en-US", "US", "US:en"),
    ("\"short seller\" Korea OR Korean OR Samsung OR Hynix when:30d", "en-US", "US", "US:en"),
    ("숏셀러 보고서 when:30d", "ko", "KR", "KR:ko"),
    ("공매도 리포트 숏셀러 when:30d", "ko", "KR", "KR:ko"),
]
FIRMS = ["Culper", "Hindenburg", "Muddy Waters", "Spruce Point", "Gotham City", "Wolfpack", "Grizzly", "Blue Orca", "Jehoshaphat",
         "Fuzzy Panda", "Kerrisdale", "Hunterbrook", "Scorpion Capital", "Bonitas", "Viceroy", "Citron", "J Capital", "Iceberg Research",
         "Morpheus Research", "Sunesis", "Glaucus", "Mad Hedge", "Bleecker Street", "Probe Capital", "Chanos", "Nate Anderson", "Carson Block"]
FIRM_RE = re.compile("|".join(re.escape(f) for f in FIRMS), re.I)
EN_SELLER = re.compile(r"short[- ]?seller|short report|short[- ]selling firm", re.I)
EN_CLAIM = re.compile(r"report|alleg|accus|claims?|says|fraud|probe|rebut|denies|responds|hits back", re.I)
KO_SELLER = re.compile(r"숏셀러|공매도 (리포트|보고서|전문|업체|투자자)|공매도 세력")
KO_CLAIM = re.compile(r"보고서|리포트|주장|의혹|반박|폭로")
NOISE = re.compile(r"불법 공매도|공매도 재개|공매도 금지|공매도 잔고|대차잔고|short interest|short squeeze|etf", re.I)
REGION_RULES = [("한국", re.compile(r"Korea|Korean|Seoul|Samsung|Hynix|KOSPI|KOSDAQ|한국|코스피|코스닥|삼성|하이닉스", re.I)),
                ("중국", re.compile(r"China|Chinese|Hong Kong|Shanghai|Shenzhen|Alibaba|Tencent|NIO|BYD|중국|홍콩", re.I)),
                ("일본", re.compile(r"Japan|Japanese|Tokyo|Nikkei|TSE|일본|도쿄", re.I)),
                ("미국", re.compile(r"Nasdaq|NYSE|Wall Street|U\.S\.|US-listed|S&P|Nvidia|Tesla|Apple|Amazon|미국|나스닥|뉴욕", re.I))]
MAX_NEW, MAX_AUTO, PRUNE_DAYS = 20, 60, 120


def ensure(conn):
    sr.ensure(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS short_seen (h TEXT PRIMARY KEY, seen TEXT)")


def split_title(t):
    """구글 뉴스 제목은 '기사 제목 - 언론사' 형태 → (제목, 언론사)"""
    t = re.sub(r"\s+", " ", (t or "").strip())
    if " - " in t:
        head, _, src = t.rpartition(" - ")
        if 0 < len(src) <= 40:
            return head.strip(), src.strip()
    return t, ""


def norm_hash(title):
    k = re.sub(r"[^0-9a-z가-힣]+", "", title.lower())[:80]
    return hashlib.sha1(k.encode("utf-8")).hexdigest()[:14]


def is_candidate(title):
    if NOISE.search(title):
        return False
    if FIRM_RE.search(title):
        return True
    return bool((EN_SELLER.search(title) and EN_CLAIM.search(title)) or (KO_SELLER.search(title) and KO_CLAIM.search(title)))


def guess_region(title):
    for name, rx in REGION_RULES:
        if rx.search(title):
            return name
    return "기타"


def parse_feed(xml_text):
    """[(제목, 언론사, 링크, 날짜 YYYY-MM-DD)]"""
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        raw = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        head, tail = split_title(raw)
        d = ""
        try:
            d = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            pass
        if head:
            out.append((head, src or tail, link, d))
    return out


def fetch_feed(query, hl, gl, ceid, session=None, timeout=20):
    s = session or requests
    r = s.get(f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}",
              headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    return parse_feed(r.text)


def ingest(conn, articles, today=None):
    """후보 기사를 표에 넣음 → 새로 넣은 개수"""
    ensure(conn)
    now = (today or dt.date.today()).isoformat()
    have_auto = conn.execute("SELECT COUNT(*) FROM short_reports WHERE seed LIKE 'auto:%'").fetchone()[0]
    added = 0
    for head, src, link, d in sorted(articles, key=lambda a: a[3], reverse=True):
        if added >= MAX_NEW or have_auto + added >= MAX_AUTO:
            break
        if not is_candidate(head):
            continue
        h = norm_hash(head)
        if conn.execute("SELECT 1 FROM short_seen WHERE h=?", (h,)).fetchone():
            continue
        m = FIRM_RE.search(head)
        conn.execute("INSERT OR IGNORE INTO short_seen VALUES (?,?)", (h, now))
        conn.execute(
            "INSERT INTO short_reports (seed, region, firm, target, report_date, title, status, claims, response, reaction, korea_link,"
            " source, source_url, verified, price_move, added, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("auto:" + h, guess_region(head), m.group(0) if m else "", "", d, head[:120], "이슈 진행 중",
             "자동 수집한 기사 제목이에요. 보고서 내용·회사 반응·주가 반응은 아직 비어 있어요 — 원문을 읽고 수정해서 채우거나, 관련 없으면 삭제해 주세요. "
             "(날짜는 기사 날짜예요, 보고서가 나온 날과 다를 수 있어요)", "", "", "",
             f"자동 수집 · {src or '뉴스'} 기사", link if link.startswith(("http://", "https://")) else "", 0, "", now, now))
        added += 1
    conn.commit()
    return added


def prune(conn, today=None):
    cut = ((today or dt.date.today()) - dt.timedelta(days=PRUNE_DAYS)).isoformat()
    cur = conn.execute("DELETE FROM short_reports WHERE seed LIKE 'auto:%' AND added = updated AND added < ?", (cut,))
    conn.commit()
    return cur.rowcount


def sync(db_path, log=print, session=None, today=None, force=False):
    """하루 한 번: 뉴스 검색 → 후보 올리기. 검색이 막혀 있으면 실패 개수만 알리고 넘어감."""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        now = (today or dt.date.today()).isoformat()
        last = conn.execute("SELECT value FROM short_meta WHERE key='auto_last'").fetchone()
        if last and last[0] == now and not force:
            return {"건너뜀": "오늘 이미 확인"}
        arts, fails = [], 0
        for q, hl, gl, ceid in FEEDS:
            try:
                arts += fetch_feed(q, hl, gl, ceid, session)
            except Exception as e:
                fails += 1
                log(f"공매도 리포트 검색 실패 ({q[:25]}…): {type(e).__name__}")
        if fails == len(FEEDS):
            return {"새 후보": 0, "검색 실패": fails}            # 오늘 확인한 것으로 치지 않음 → 다음에 다시 시도
        n = ingest(conn, arts, today)
        pr = prune(conn, today)
        conn.execute("INSERT OR REPLACE INTO short_meta VALUES ('auto_last', ?)", (now,))
        conn.commit()
        log(f"공매도 리포트: 기사 {len(arts)}건 중 새 후보 {n}개 · 정리 {pr}개 · 검색 실패 {fails}")
        return {"기사": len(arts), "새 후보": n, "정리": pr, "검색 실패": fails}
    finally:
        conn.close()


def status(conn):
    """(마지막 확인일 또는 '', 자동 후보 개수) — 읽기 전용 연결에서도 호출 가능"""
    try:
        last = conn.execute("SELECT value FROM short_meta WHERE key='auto_last'").fetchone()
        n = conn.execute("SELECT COUNT(*) FROM short_reports WHERE seed LIKE 'auto:%'").fetchone()[0]
        return (last[0] if last else ""), n
    except sqlite3.OperationalError:
        return "", 0
