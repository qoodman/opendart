# -*- coding: utf-8 -*-
"""
주식 정책 레이더: 정부 보도자료·정책뉴스, 법령 개정, 국회 법안 중 주식시장에 영향을 주는 것.
(부동산 앱 policy.py와 같은 출처·같은 받는 방법, 주제만 주식용)

출처 (키는 이 앱 .env에 없으면 부동산 앱 .env에서 가져옴)
- 정책브리핑 보도자료·정책뉴스 API (공공데이터포털) — DATA_GO_KR_KEY
- 법제처 국가법령정보 lawSearch — LAW_OC
- 열린국회정보 '국회의원 발의법률안' — ASSEMBLY_API_KEY
첫 수집은 기준선(알림 없음), 그 뒤 새 항목만 알림 대상.
"""
import re, json, time, sqlite3, datetime as dt
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote

import requests

TOPICS = {
    "밸류업·주주환원": ("밸류업", "기업가치제고", "기업가치 제고", "주주환원", "자사주", "자기주식", "배당", "소각",
                  "코리아디스카운트", "코리아 디스카운트", "주주가치"),
    "공매도": ("공매도", "대차거래", "무차입"),
    "세제": ("금융투자소득세", "금투세", "증권거래세", "배당소득", "분리과세", "주식양도", "대주주 요건", "대주주요건",
           "ISA", "개인종합자산관리계좌", "상속세", "밸류업 세제"),
    "상법·지배구조": ("상법", "이사의 충실의무", "충실의무", "집중투표", "소액주주", "일반주주", "지배구조",
                 "감사위원", "물적분할", "합병비율", "의무공개매수", "스튜어드십"),
    "자본시장 제도": ("자본시장", "공시", "상장폐지", "상장", "IPO", "불공정거래", "시세조종", "주가조작",
                 "MSCI", "외국인 투자", "외국인투자", "대체거래소", "넥스트레이드", "증권시장", "코스피",
                 "코스닥", "거래시간", "투자자 보호", "투자자보호", "STO", "토큰증권", "ETF"),
    "금리·통화": ("기준금리", "통화정책", "금융통화위원회", "금통위", "환율", "외환"),
    "산업 지원": ("반도체", "이차전지", "2차전지", "인공지능", "AI", "원전", "방산", "방위산업", "바이오",
              "조선", "전력망", "K-칩스", "국가첨단전략산업"),
}
MARKET_WORDS = ("주식", "증시", "자본시장", "상장", "공시", "주주", "배당", "밸류업", "공매도", "투자자", "금융투자",
                "증권", "코스피", "코스닥", "상법", "지배구조", "자사주", "기업가치", "기준금리", "통화정책",
                "환율", "세제", "법인세", "세법", "지원", "투자", "산업")
STRONG = ("밸류업·주주환원", "공매도", "세제", "상법·지배구조", "자본시장 제도")
LAWS = ["자본시장과 금융투자업에 관한 법률", "상법", "소득세법", "조세특례제한법", "증권거래세법",
        "법인세법", "주식회사 등의 외부감사에 관한 법률", "금융소비자 보호에 관한 법률"]
BILL_KEYWORDS = ["상법", "자본시장과 금융투자업", "소득세법", "조세특례제한법", "증권거래세법", "법인세법",
                 "외부감사", "금융소비자 보호"]
ASSEMBLY_AGE = 22

PRESS_API = "https://apis.data.go.kr/1371000/pressReleaseService2/pressReleaseList2"
NEWS_API = "https://apis.data.go.kr/1371000/policyNewsService2/policyNewsList2"
LAW_URL = "https://www.law.go.kr/DRF/lawSearch.do"
BILL_URL = "https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubdpn"
HEADERS = {"User-Agent": "Mozilla/5.0 dart-simple-invest"}


def topics_of(title, subtitle="", body=""):
    """제목·부제에 주식 관련 말이 있고, 주제어가 하나라도 맞으면 주제 목록"""
    head = re.sub(r"\s+", "", f"{title}{subtitle}")
    text = re.sub(r"\s+", "", f"{title}{subtitle}{body}")
    found = [name for name, words in TOPICS.items() if any(w.replace(" ", "") in text for w in words)]
    if not found:
        return []
    strong_in_head = any(any(w.replace(" ", "") in head for w in TOPICS[n]) for n in STRONG)
    if strong_in_head:
        return found
    # 금리·산업 같은 넓은 주제는 제목·부제에 주식·투자 관련 말이 있을 때만
    if any(w in head for w in MARKET_WORDS) and any(any(w.replace(" ", "") in head for w in TOPICS[n]) for n in found):
        return found
    return []


def _norm_date(text):
    t = str(text or "").strip()
    try:
        return parsedate_to_datetime(t).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    d = re.sub(r"\D", "", t)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else ""


def parse_press(xml_text):
    """정책브리핑 보도자료·정책뉴스 API 응답 (부동산 앱과 같은 해석)"""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        raise ValueError(f"XML이 아닌 응답: {xml_text[:150]}") from e
    code = (root.findtext(".//resultCode") or "").strip()
    if code and code not in ("0", "00", "000", "0000", "03"):
        raise ValueError(f"{code} {(root.findtext('.//resultMsg') or '').strip()}")
    out = []
    for el in root.iter():
        kids = {c.tag.lower(): (c.text or "").strip() for c in el}
        if "title" not in kids or el.tag.lower() in ("channel", "response", "header"):
            continue
        pick = lambda *ks: next((kids[k] for k in ks if kids.get(k)), "")  # noqa: E731
        body = re.sub(r"<[^>]+>|&nbsp;", " ", pick("datacontents", "description", "abstract"))
        link = pick("originalurl", "link", "url", "referenceidentifier")
        out.append({"title": kids["title"], "link": link,
                    "date": _norm_date(pick("approvedate", "modifydate", "regdate", "issueddate", "createddate", "pubdate")),
                    "summary": re.sub(r"\s+", " ", f"{kids.get('subtitle1', '')} {body}").strip()[:600],
                    "subtitle": kids.get("subtitle1", ""),
                    "ministry": pick("ministercode", "ministername", "creator", "publisher", "contributor"),
                    "id": pick("newsitemid", "localid") or link})
    return out


def url_candidates(url):
    m = re.match(r"(.*/)([A-Za-z]+?)(2?)/([A-Za-z]+?)(2?)$", url)
    if not m:
        return []
    base, svc, _, op, _ = m.groups()
    return [f"{base}{svc}{a}/{op}{b}" for a in ("2", "") for b in ("", "2")]


_SECRET = re.compile(r"((?:serviceKey|KEY|OC)=)[^&\s']+", re.I)


def _err(e):
    return _SECRET.sub(r"\1***", " ".join(str(e).split()) or type(e).__name__)[:260]


def _find_rows(data):
    """열린국회정보 응답 안의 row 목록과 결과 코드"""
    rows, code = [], ""

    def walk(x):
        nonlocal rows, code
        if isinstance(x, dict):
            if "row" in x and isinstance(x["row"], list):
                rows = x["row"]
            if "RESULT" in x and isinstance(x["RESULT"], dict):
                code = x["RESULT"].get("CODE", code)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return rows, code


class Client:
    def __init__(self, press_key="", law_oc="", assembly_key="", delay=0.3):
        self.press_key = unquote(press_key) if "%" in (press_key or "") else press_key
        self.law_oc, self.assembly_key, self.delay = law_oc, assembly_key, delay
        self.http = requests.Session()
        self._working = {}

    def _get(self, url, params):
        r = self.http.get(url, params=params, headers=HEADERS, timeout=30)
        if "charset" not in r.headers.get("Content-Type", "").lower():
            r.encoding = "utf-8"          # 글자 방식이 안 적혀 오면 UTF-8로 (한글 깨짐 방지)
        time.sleep(self.delay)
        if r.status_code >= 400:
            body = re.sub(r"<[^>]+>", " ", r.text)
            raise ValueError(f"HTTP {r.status_code}: {' '.join(body.split())[:200]}")
        return r

    def press(self, start, end, url):
        r = self._get(url, {"serviceKey": self.press_key, "startDate": start.strftime("%Y%m%d"),
                            "endDate": end.strftime("%Y%m%d")})
        return parse_press(r.text)

    def press_range(self, days, url):
        """최근 days일을 3일씩 나눠 받음. 주소가 안 되면 이름 후보(…Service2/…List2 등)를 차례로."""
        today = dt.date.today()
        url = self._working.get(url, url)
        first_start = max(today - dt.timedelta(days=2), today - dt.timedelta(days=days - 1))
        last_err = None
        for cand in dict.fromkeys([url, *url_candidates(url)]):
            try:
                first = self.press(first_start, today, cand)
            except ValueError as e:
                if "NO_OPENAPI" in str(e) or "HTTP 404" in str(e):
                    last_err = e
                    continue
                raise
            self._working[url] = cand
            url = cand
            break
        else:
            raise last_err or ValueError("주소 후보가 모두 실패했습니다")
        out, end = first, first_start - dt.timedelta(days=1)
        while (today - end).days < days:
            start = max(end - dt.timedelta(days=2), today - dt.timedelta(days=days - 1))
            out += self.press(start, end, url)
            end = start - dt.timedelta(days=1)
        return out

    def laws(self, name):
        r = self._get(LAW_URL, {"OC": self.law_oc, "target": "law", "type": "JSON", "query": name, "display": 20})
        try:
            data = r.json()
        except ValueError as e:
            raise ValueError(f"법제처 응답이 JSON이 아닙니다 (OC 승인 전일 수 있음): {r.text[:120]}") from e
        found = []

        def walk(x):
            if isinstance(x, dict):
                if any(k in x for k in ("법령명한글", "법령명")):
                    found.append(x)
                    return
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(data)
        out = []
        for row in found:
            g = lambda *ks: next((str(row[k]).strip() for k in ks if row.get(k) not in (None, "")), "")  # noqa: E731
            out.append({"name": g("법령명한글", "법령명"), "promulgated": _norm_date(g("공포일자")),
                        "effective": _norm_date(g("시행일자")), "kind": g("제개정구분명", "제개정구분"),
                        "org": g("소관부처명")})
        return out

    def bills(self, keyword):
        r = self._get(BILL_URL, {"KEY": self.assembly_key, "Type": "json", "pIndex": 1, "pSize": 100,
                                 "AGE": ASSEMBLY_AGE, "BILL_NAME": keyword})
        rows, code = _find_rows(r.json())
        if code and not str(code).endswith(("000", "200")):
            raise ValueError(f"열린국회정보 오류: {code}")
        return rows


# ---------------- 저장 ----------------
def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS policy_items (
        key TEXT PRIMARY KEY, kind TEXT, source TEXT, date TEXT, title TEXT, url TEXT, summary TEXT,
        topics TEXT, first_seen TEXT, baseline INTEGER DEFAULT 0, status TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_policy_date ON policy_items(date)")
    conn.commit()


def _upsert(conn, key, kind, source, date, title, url, summary, topics, baseline, status=""):
    """새로 넣었으면 True. 법안은 처리 결과(status)가 바뀌면 갱신하고 새 소식으로 침."""
    old = conn.execute("SELECT status FROM policy_items WHERE key=?", (key,)).fetchone()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    if old is None:
        conn.execute("INSERT INTO policy_items VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (key, kind, source, date, title, url, summary, json.dumps(topics, ensure_ascii=False),
                      now, 1 if baseline else 0, status))
        return True
    if status and status != (old[0] or ""):
        conn.execute("UPDATE policy_items SET status=?, title=?, date=?, first_seen=?, baseline=0 WHERE key=?",
                     (status, title, date, now, key))
        return True
    return False


def law_link(name):
    slug = quote(re.sub(r"\s", "", name))
    return f"https://www.law.go.kr/법령/{slug}"


def sync(db_path, press_key="", law_oc="", assembly_key="", log=print):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        first = conn.execute("SELECT COUNT(*) FROM policy_items").fetchone()[0] == 0
        api = Client(press_key, law_oc, assembly_key)
        stats = {"보도자료": 0, "법령": 0, "법안": 0, "새 항목": 0, "오류": []}
        if press_key:
            for kind, url in (("보도자료", PRESS_API), ("정책뉴스", NEWS_API)):
                try:
                    got = api.press_range(30 if first else 5, url)
                except (requests.exceptions.RequestException, ValueError) as e:
                    stats["오류"].append(f"{kind}: {_err(e)}")
                    log(f"  ✖ {kind}: {_err(e)}")
                    continue
                for it in got:
                    tps = topics_of(it["title"], it.get("subtitle", ""), it["summary"])
                    if not tps:
                        continue
                    stats["보도자료"] += 1
                    # 같은 발표가 보도자료·정책뉴스로 두 번 오는 경우가 많아 제목으로 하나로 묶음
                    key = "press:" + re.sub(r"[\s\W]", "", it["title"])[:80]
                    if _upsert(conn, key, kind, it.get("ministry") or "정책브리핑", it["date"], it["title"],
                               it["link"], it["summary"], tps, first):
                        stats["새 항목"] += 1
        if law_oc:
            for name in LAWS:
                try:
                    rows = api.laws(name)
                except (requests.exceptions.RequestException, ValueError) as e:
                    stats["오류"].append(f"법령 {name}: {_err(e)}")
                    continue
                for v in rows:
                    if re.sub(r"\s", "", v["name"]) != re.sub(r"\s", "", name) or not v["promulgated"]:
                        continue
                    stats["법령"] += 1
                    title = f"{v['name']} {v['kind'] or '개정'} (공포 {v['promulgated']}, 시행 {v['effective'] or '-'})"
                    tps = topics_of(v["name"], "", "") or ["자본시장 제도"]
                    if _upsert(conn, f"law:{v['name']}:{v['promulgated']}", "법령", v["org"] or "법제처",
                               v["promulgated"], title, law_link(v["name"]), "", tps, first):
                        stats["새 항목"] += 1
        if assembly_key:
            seen = set()
            for kw in BILL_KEYWORDS:
                try:
                    rows = api.bills(kw)
                except (requests.exceptions.RequestException, ValueError) as e:
                    stats["오류"].append(f"법안 {kw}: {_err(e)}")
                    continue
                for r in rows:
                    bid = r.get("BILL_ID") or r.get("BILL_NO")
                    if not bid or bid in seen:
                        continue
                    seen.add(bid)
                    stats["법안"] += 1
                    result = (r.get("PROC_RESULT") or "").strip() or "위원회 심사 중"
                    who = (r.get("PROPOSER") or "").strip()
                    title = f"{r.get('BILL_NAME', '')}{f' ({who})' if who else ''} · {result}"
                    date = _norm_date(r.get("PROC_DT") or r.get("PROPOSE_DT") or "")
                    tps = topics_of(r.get("BILL_NAME", ""), "", "") or ["자본시장 제도"]
                    if _upsert(conn, f"bill:{bid}", "법안", "국회", date, title, r.get("DETAIL_LINK") or "",
                               "", tps, first, status=result):
                        stats["새 항목"] += 1
        conn.commit()
        conn.execute("INSERT OR REPLACE INTO policy_items (key, kind, source, date, title, first_seen) "
                     "VALUES ('_meta', '_meta', '', '', '', ?)", (dt.datetime.now().strftime("%Y-%m-%d %H:%M"),))
        conn.commit()
        log(f"[정책] 보도자료 {stats['보도자료']} · 법령 {stats['법령']} · 법안 {stats['법안']} · 새 {stats['새 항목']}"
            + (f" · 오류 {len(stats['오류'])}" if stats["오류"] else "") + (" (첫 수집: 알림 없이 기준선)" if first else ""))
        stats["첫 수집"] = first
        return stats
    finally:
        conn.close()


def new_items(conn, since):
    """알림용: since 이후 처음 본(기준선 아닌) 항목"""
    try:
        return conn.execute("SELECT * FROM policy_items WHERE key!='_meta' AND baseline=0 AND first_seen>=? "
                            "ORDER BY date DESC", (since,)).fetchall()
    except sqlite3.OperationalError:
        return []
