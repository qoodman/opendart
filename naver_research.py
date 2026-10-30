# -*- coding: utf-8 -*-
"""
네이버 증권 리서치 → 종목분석 리포트 (2026년 새 네이버 증권 사이트 기준)

- 목록: 네이버 증권 앱·사이트가 쓰는 JSON 주소에서 받습니다.
  (새 사이트 주소를 먼저 시도하고, 안 되면 m.stock.naver.com 주소)
- 목표가·투자의견: 목록에 없으면 리포트 상세(JSON 또는 상세 화면)에서 읽습니다.
  처음 성공한 상세 주소를 계속 쓰고, 어느 것도 안 되면 그 회차엔 목표가 없이 저장합니다.
- 처음엔 최근 30일, 그다음부터는 마지막으로 받은 날 이후만. 상세는 한 번에 최대 400건.
"""
import re, json, time, sqlite3, datetime as dt
from pathlib import Path

import requests

import research

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Referer": "https://stock.naver.com/research/company",
           "Accept": "application/json, text/plain, */*"}
LIST_URLS = [
    "https://stock.naver.com/api/stockSecurity/researches/v2/company?page={page}&size={size}",
    "https://m.stock.naver.com/api/research/company?page={page}&pageSize={size}",
]
DETAIL_URLS = [
    "https://stock.naver.com/api/stockSecurity/researches/v2/{id}",
    "https://stock.naver.com/api/stockSecurity/researches/v2/company/{id}",
    "https://m.stock.naver.com/api/research/company/{id}",
    "https://m.stock.naver.com/research/company/{id}",        # 상세 화면 (HTML 안의 데이터)
]
PAGE_SIZE = 50
DETAIL_LIMIT = 400
PAUSE = 0.4


class NVError(Exception):
    pass


# ---------------- JSON 읽기 도우미 (이름이 조금 달라도 찾음) ----------------
def _keys(d):
    return {re.sub(r"[_\s]", "", k).lower(): k for k in d}


def pick(d, *names):
    if not isinstance(d, dict):
        return None
    ks = _keys(d)
    for n in names:
        k = ks.get(n.lower())
        if k is not None and d[k] not in (None, ""):
            return d[k]
    return None


def find_list(obj, depth=0):
    """응답 안에서 리포트 목록(딕셔너리들의 리스트)을 찾음"""
    if isinstance(obj, list):
        if not obj:
            return obj          # 빈 목록 = 마지막 쪽을 넘김
        if all(isinstance(x, dict) for x in obj[:3]):
            return obj
        return None
    if isinstance(obj, dict) and depth < 4:
        for k in ("result", "content", "contents", "items", "list", "data", "researches", "researchList"):
            if k in obj:
                got = find_list(obj[k], depth + 1)
                if got is not None:
                    return got
        for v in obj.values():
            got = find_list(v, depth + 1)
            if got is not None:
                return got
    return None


def find_dict(obj, depth=0):
    """상세 응답에서 리포트 정보가 든 딕셔너리"""
    if isinstance(obj, dict):
        if pick(obj, "goalPrice", "targetPrice", "priceTarget", "opinion", "investOpinion", "title"):
            return obj
        if depth < 4:
            for v in obj.values():
                got = find_dict(v, depth + 1)
                if got:
                    return got
    if isinstance(obj, list) and obj and depth < 4:
        return find_dict(obj[0], depth + 1)
    return None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if v > 0 else None
    d = re.sub(r"[^\d]", "", str(v))
    return int(d) if d and int(d) > 0 else None


def _date(v):
    m = re.search(r"(\d{2,4})[-./](\d{1,2})[-./](\d{1,2})", str(v or ""))
    if not m:
        return None
    y = int(m.group(1))
    return f"{y + 2000 if y < 100 else y}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _pdf(d):
    for v in (d or {}).values():
        if isinstance(v, str) and re.search(r"\.pdf(\?|$)", v, re.I):
            return v
    return ""


def target_of(d):
    return _num(pick(d, "goalPrice", "targetPrice", "priceTarget", "goal", "targetStockPrice", "tp"))


def opinion_of(d):
    v = pick(d, "opinion", "investOpinion", "opinionName", "investmentOpinion", "opinionType")
    if isinstance(v, dict):
        v = pick(v, "name", "text", "label", "code")
    return str(v).strip() if v else ""


def parse_item(x):
    code = pick(x, "itemCode", "stockCode", "code", "reutersCode")
    date = _date(pick(x, "writeDate", "date", "createdAt", "regDate", "publishDate"))
    title = pick(x, "title", "subject")
    if not code or not date or not title:
        return None
    code = re.sub(r"\D", "", str(code))[:6]
    if len(code) != 6:
        return None
    rid = pick(x, "researchId", "nid", "id")
    return dict(date=date, code=code, name=str(pick(x, "itemName", "stockName", "name") or ""),
                title=str(title), broker=str(pick(x, "brokerName", "broker", "firmName") or ""),
                author=str(pick(x, "analyst", "analystName", "writer") or ""),
                target=target_of(x), opinion=opinion_of(x), pdf=_pdf(x),
                rid=str(rid) if rid is not None else "",
                url=str(pick(x, "endUrl", "url", "link") or (f"https://m.stock.naver.com/research/company/{rid}" if rid else "")))


def parse_detail_html(html):
    """상세 화면 HTML(안에 든 데이터 포함) → (목표가, 의견, pdf)"""
    u = html.replace('\\"', '"').replace("\\u0026", "&")
    t = None
    m = re.search(r'"(?:goalPrice|targetPrice|priceTarget)"\s*:\s*"?([\d,]+)', u)
    if m:
        t = _num(m.group(1))
    if t is None:
        text = re.sub(r"<[^>]+>", " ", u)
        m = re.search(r"목표\s*(?:주)?가[^\d]{0,15}([\d,]{3,})", text)
        if m:
            t = _num(m.group(1))
    op = ""
    m = re.search(r'"(?:opinion|investOpinion|opinionName)"\s*:\s*"([^"]{1,20})"', u)
    if m:
        op = m.group(1)
    pdf = ""
    m = re.search(r'https?://[^"\'\s<>]+\.pdf', u)
    if m:
        pdf = m.group(0)
    return t, op, pdf


# ---------------- 받기 ----------------
_LIST_OK = None     # 이번 실행에서 통한 목록 주소
_DETAIL_OK = None   # 이번 실행에서 통한 상세 주소


def fetch_list(session, page):
    global _LIST_OK
    urls = [_LIST_OK] if _LIST_OK else LIST_URLS
    last_err = ""
    for u in urls:
        try:
            r = session.get(u.format(page=page, size=PAGE_SIZE), headers=HEADERS, timeout=30)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            items = find_list(r.json())
        except (requests.exceptions.RequestException, ValueError) as e:
            last_err = type(e).__name__
            continue
        if items is None:
            last_err = "목록 없음"
            continue
        _LIST_OK = u
        return [x for x in (parse_item(i) for i in items) if x], r.text
    raise NVError(f"네이버 증권 리포트 목록을 받지 못했습니다 ({last_err})")


def fetch_detail(session, rid, debug):
    """(목표가, 의견, pdf) 또는 None(상세 주소가 하나도 안 통함)"""
    global _DETAIL_OK
    urls = [_DETAIL_OK] if _DETAIL_OK else DETAIL_URLS
    for u in urls:
        try:
            r = session.get(u.format(id=rid), headers=HEADERS, timeout=30)
        except requests.exceptions.RequestException:
            continue
        if r.status_code != 200:
            continue
        ctype = r.headers.get("Content-Type", "")
        if "json" in ctype:
            try:
                d = find_dict(r.json()) or {}
            except ValueError:
                continue
            t, op, pdf = target_of(d), opinion_of(d), _pdf(d)
        else:
            t, op, pdf = parse_detail_html(r.text)
        if debug is not None and u not in debug:
            debug[u] = r.text[:4000]
        if t is not None or op:
            _DETAIL_OK = u
            return t, op, pdf
    return None


def sync(db_path, debug_dir=None, log=print, first_days=30, session=None):
    global _LIST_OK, _DETAIL_OK
    _LIST_OK = _DETAIL_OK = None
    session = session or requests.Session()
    conn = sqlite3.connect(db_path)
    try:
        research.ensure_table(conn)
        last = research.last_date(conn, "nv")
        today = dt.date.today()
        since = ((dt.date.fromisoformat(last) - dt.timedelta(days=2)) if last
                 else today - dt.timedelta(days=first_days)).isoformat()
        log(f"[리포트] 네이버 증권 {since} 이후 종목분석 리포트 받는 중")
        added, page, prev, sample = 0, 1, None, ""
        while page <= 400:
            rows, raw = fetch_list(session, page)
            sample = sample or raw[:6000]
            if not rows:
                break
            key = (rows[0]["date"], rows[0]["title"])
            if key == prev:
                break
            prev = key
            fresh = [dict(x, detail=0 if x["target"] is None and x["rid"] else 1) for x in rows if x["date"] >= since]
            added += research.store(conn, "nv", fresh)
            if min(x["date"] for x in rows) < since:
                break
            page += 1
            time.sleep(PAUSE)
        # 목표가가 목록에 없던 것은 상세에서
        todo = conn.execute("SELECT rowid, url FROM reports WHERE source='nv' AND detail=0 "
                            "ORDER BY date DESC LIMIT ?", (DETAIL_LIMIT,)).fetchall()
        got, misses, debug = 0, 0, {}
        for rowid, url in todo:
            rid = re.search(r"(\d+)\D*$", url or "")
            if not rid:
                conn.execute("UPDATE reports SET detail=1 WHERE rowid=?", (rowid,))
                continue
            res = fetch_detail(session, rid.group(1), debug)
            if res is None and _DETAIL_OK:
                # 상세 주소는 맞는데 이 리포트엔 목표가가 없음 (산업·비평가 리포트 등)
                conn.execute("UPDATE reports SET detail=1 WHERE rowid=?", (rowid,))
                continue
            if res is None:
                misses += 1
                if misses >= 3 and not _DETAIL_OK:
                    log("  ! 네이버 리포트 상세에서 목표가를 찾지 못했습니다. 이번엔 목록만 저장합니다.")
                    break
                continue
            t, op, pdf = res
            conn.execute("UPDATE reports SET target=COALESCE(?, target), opinion=CASE WHEN ?!='' THEN ? ELSE opinion END, "
                         "pdf=CASE WHEN ?!='' THEN ? ELSE pdf END, detail=1 WHERE rowid=?",
                         (t, op, op, pdf, pdf, rowid))
            got += t is not None
            time.sleep(PAUSE)
        conn.commit()
        if debug_dir:
            Path(debug_dir, "nv_sample.json").write_text(
                json.dumps({"list_url": _LIST_OK, "detail_url": _DETAIL_OK, "list_sample": sample,
                            "detail_samples": debug}, ensure_ascii=False, indent=1), encoding="utf-8")
        left = conn.execute("SELECT COUNT(*) FROM reports WHERE source='nv' AND detail=0").fetchone()[0]
        log(f"[리포트] 네이버 증권 새 리포트 {added}건 (목록 주소: {(_LIST_OK or '').split('?')[0]}), "
            f"목표가 읽음 {got}건" + (f", 남은 {left}건은 다음 수집 때" if left else ""))
        return added
    finally:
        conn.close()
