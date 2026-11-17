# -*- coding: utf-8 -*-
"""
해외 통신·경제 매체 속보 제목 모으기 (구글 뉴스 RSS, 키 불필요).

로이터·AP·블룸버그는 무료 공식 피드가 없어서, 구글 뉴스 검색(site:)으로 나온 '제목·언론사·링크·발행 시각'만 받아요.
  - 기사 본문은 받지 않아요 (제목을 누르면 원문 사이트로 가요). 실시간이 아니라 몇 분~수십 분 늦을 수 있어요.
  - 제목을 키워드로 주제(연준·금리, 관세·무역, 환율, 유가, 반도체, 중국, 지정학, 한국 증시)로 나눠 저장해 둬요.
    나중에 '이런 뉴스 뒤에 지수가 어떻게 움직였나'를 살펴보는 자료가 돼요 (좋다/나쁘다 판단은 하지 않아요).
"""
import datetime as dt
import email.utils
import re
import sqlite3
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

import shortauto

FEEDS = [          # (이름, 검색어, hl, gl, ceid)
    ("로이터", "site:reuters.com (markets OR stocks OR Fed OR tariffs OR oil OR Korea OR chips) when:1d", "en-US", "US", "US:en"),
    ("AP", "site:apnews.com (economy OR markets OR stocks OR Fed OR tariffs OR oil) when:1d", "en-US", "US", "US:en"),
    ("블룸버그", "site:bloomberg.com (markets OR stocks OR Fed OR tariffs OR Korea OR chips) when:1d", "en-US", "US", "US:en"),
    ("연합뉴스", "site:yna.co.kr 속보 when:1d", "ko", "KR", "KR:ko"),
    ("연합뉴스 경제", "site:yna.co.kr (증시 OR 코스피 OR 환율 OR 금리) when:1d", "ko", "KR", "KR:ko"),
]
TOPICS = {
    "연준·금리": r"(?-i:\bFed\b)|FOMC|Powell|interest rates?|rate (?:cut|hike)|연준|기준금리|금리|파월",
    "관세·무역": r"tariffs?|trade war|trade deal|관세|무역",
    "환율": r"currency|\bdollar\b|\byen\b|(?-i:\bwon\b)|환율|달러|원화|엔화",
    "유가": r"\boil\b|crude|OPEC|유가|원유",
    "반도체": r"\bchips?\b|semiconductor|Nvidia|TSMC|Samsung|SK ?hynix|반도체|엔비디아|삼성전자|하이닉스",
    "중국": r"China|Chinese|Beijing|중국",
    "지정학": r"\bwar\b|missile|Iran|Israel|Ukraine|Russia|North Korea|전쟁|미사일|이란|이스라엘|우크라이나|북한",
    "한국 증시": r"KOSPI|KOSDAQ|Seoul|코스피|코스닥|증시",
}
TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPICS.items()}
BREAKING_RE = re.compile(r"속보|긴급|breaking|just in|\bflash\b", re.I)
DEFAULT_KEYWORDS = "속보,Fed,연준,FOMC,금리,관세,tariff,환율,반도체"
KEEP_DAYS = 400
MAX_ALERT = 5
ALERT_WINDOW_HOURS = 6


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS news_items (
        h TEXT PRIMARY KEY, title TEXT, source TEXT, feed TEXT, link TEXT, pub TEXT, topic TEXT, breaking INTEGER,
        added TEXT, alerted INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS news_pub ON news_items (pub);
    """)


def topics_of(title):
    return [k for k, rx in TOPIC_RE.items() if rx.search(title or "")]


def is_breaking(title):
    return bool(BREAKING_RE.search(title or ""))


def parse(xml_text):
    """[(제목, 언론사, 링크, 발행시각 UTC 'YYYY-MM-DD HH:MM')]"""
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        head, src = shortauto.split_title(it.findtext("title") or "")
        pub = ""
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
        if head and pub:
            out.append((head, src or (it.findtext("source") or "").strip(), (it.findtext("link") or "").strip(), pub))
    return out


def fetch(query, hl, gl, ceid, session=None, timeout=20):
    s = session or requests
    r = s.get(f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}",
              headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    return parse(r.text)


def ingest(conn, items, now=None):
    """items: [(feed 이름, (제목, 언론사, 링크, 발행시각))] → 새로 넣은 개수. 처음 넣는 날(표가 비어 있음)은 알림 대상에서 뺌."""
    ensure(conn)
    now = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d %H:%M")
    first = conn.execute("SELECT 1 FROM news_items LIMIT 1").fetchone() is None
    added = 0
    for feed, (title, src, link, pub) in items:
        h = shortauto.norm_hash(title)
        if conn.execute("SELECT 1 FROM news_items WHERE h=?", (h,)).fetchone():
            continue
        conn.execute("INSERT INTO news_items (h,title,source,feed,link,pub,topic,breaking,added,alerted) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (h, title[:200], src, feed, link if link.startswith(("http://", "https://")) else "", pub,
                      ",".join(topics_of(title)), 1 if is_breaking(title) else 0, now, 1 if first else 0))
        added += 1
    cut = ((dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M")
    conn.execute("DELETE FROM news_items WHERE pub < ?", (cut,))
    conn.commit()
    return added


def keyword_list(raw):
    return [k.strip().lower() for k in (raw or DEFAULT_KEYWORDS).split(",") if k.strip()]


def pending_alerts(conn, keywords, now=None):
    """아직 안 알린 새 기사 중 속보이거나 키워드가 든 최근 기사 → (알릴 목록, 확인한 전체 개수). 확인한 기사는 알림 처리됨으로 바꿈."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cut = (now - dt.timedelta(hours=ALERT_WINDOW_HOURS)).strftime("%Y-%m-%d %H:%M")
    rows = conn.execute("SELECT h, title, source, link, pub, breaking FROM news_items WHERE alerted=0 ORDER BY pub DESC").fetchall()
    kws = keyword_list(keywords)
    hit = [r for r in rows if r[4] >= cut and (r[5] or any(k in r[1].lower() for k in kws))]
    conn.execute("UPDATE news_items SET alerted=1 WHERE alerted=0")
    conn.commit()
    return hit[:MAX_ALERT], len(hit)


def sync(db_path, log=print, session=None):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        items, fails = [], 0
        for name, q, hl, gl, ceid in FEEDS:
            try:
                items += [(name, a) for a in fetch(q, hl, gl, ceid, session)]
            except Exception as e:
                fails += 1
                log(f"뉴스 검색 실패 ({name}): {type(e).__name__}")
        n = ingest(conn, items) if items else 0
        return {"기사": len(items), "새 기사": n, "검색 실패": fails}
    finally:
        conn.close()


def recent(conn, hours=48, topic="", source="", limit=200):
    """화면용: 최근 기사 [(제목, 언론사, 링크, 발행시각 KST 'MM-DD HH:MM', 주제 목록, 속보)] 최신순"""
    cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    try:
        rows = conn.execute("SELECT title, source, link, pub, topic, breaking, feed FROM news_items WHERE pub >= ? ORDER BY pub DESC LIMIT 2000", (cut,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for title, src, link, pub, tp, br, feed in rows:
        tl = [x for x in (tp or "").split(",") if x]
        if topic and topic not in tl:
            continue
        if source and feed != source:
            continue
        kst = (dt.datetime.strptime(pub, "%Y-%m-%d %H:%M") + dt.timedelta(hours=9)).strftime("%m-%d %H:%M")
        out.append((title, src or feed, link, kst, tl, bool(br)))
    return out[:limit]
