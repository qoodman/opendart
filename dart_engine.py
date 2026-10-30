# -*- coding: utf-8 -*-
"""
DART '상황보고' + 본문 '단순투자' 공시 → 날짜별 평균 매수단가 정리 (엔진)

명령줄 버전(dart_simple_invest.py)과 앱(dart_app.py)이 함께 쓰는 핵심 로직입니다.

1단계: 공시 목록 조회 → dart_cache/reports_*.json 저장 (다시 실행하면 재사용)
2단계: 원문 병렬 다운로드 → dart_cache/*.zip 저장 (이미 받은 건 API 호출 안 함)
3단계: 저장된 원문을 여러 CPU로 병렬 분석 → 엑셀
"""
import io, os, re, json, time, zipfile, threading, datetime as dt
from dataclasses import dataclass
from functools import partial
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

import requests
import pandas as pd
from bs4 import BeautifulSoup

try:
    from bs4 import XMLParsedAsHTMLWarning
    import warnings
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

BASE = "https://opendart.fss.or.kr/api"
DATE_RE = re.compile(r"(\d{4})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})")
PARSE_VERSION = 3       # 분석 결과 형식이 바뀌면 올림 (저장된 분석 결과를 다시 만듦)
HEARTBEAT = 30          # 명령줄 버전의 ♥ 줄 간격(초)
WARN_IDLE = 90          # 응답 없음 경고(초)
DEAD_IDLE = 180         # 다시 시작 권장(초)


class DartError(Exception):
    """사용자에게 그대로 보여줄 오류"""


class QuotaExceeded(Exception):
    pass


class Stopped(Exception):
    """사용자가 중지함"""


def _norm_date(s):
    return re.sub(r"\D", "", s or "")


@dataclass
class Config:
    api_key: str
    start: str                      # YYYYMMDD
    end: str                        # YYYYMMDD
    keyword: str = "상황보고"         # 보고서명 검색어
    body_word: str = "단순투자"       # 본문 포함 단어
    out_dir: str = "."              # 결과 엑셀과 dart_cache가 저장될 폴더
    out_name: str = "단순투자_평균매수단가.xlsx"
    add_prices: bool = True         # 제출일 종가 넣기 (네이버 증권 시세, API 한도와 무관)
    workers: int = 8                # 동시 다운로드 수
    max_per_min: int = 900          # 분당 최대 호출 (과다호출 차단 방지)
    daily_budget: int = 39000       # 한 번 실행에서 쓸 최대 호출 수 (한도 4만 건 여유분)
    timeout: int = 30
    parse_workers: int = 0          # 3단계 동시 분석 수 (0 = CPU 수만큼, 작은 서버는 1)
    list_refresh: bool = False      # True면 저장된 목록을 쓰지 않고 매번 새로 조회 (서버용)

    @property
    def cache(self):
        return os.path.join(self.out_dir, "dart_cache")

    def validate(self):
        self.api_key = (self.api_key or "").strip()
        if not self.api_key or "여기에" in self.api_key:
            raise DartError("API 키를 넣어주세요. (opendart.fss.or.kr에서 발급)")
        self.start, self.end = _norm_date(self.start), _norm_date(self.end)
        try:
            s = dt.datetime.strptime(self.start, "%Y%m%d")
            e = dt.datetime.strptime(self.end, "%Y%m%d")
        except ValueError:
            raise DartError("날짜는 20260101처럼 8자리로 넣어주세요.")
        if s > e:
            raise DartError("시작일이 종료일보다 늦습니다.")
        self.keyword = (self.keyword or "").strip()
        self.body_word = (self.body_word or "").strip()
        if not self.keyword:
            raise DartError("보고서명 검색어를 넣어주세요.")


# ---------------- 3단계 분석 함수 (다른 프로세스에서 실행되므로 모듈 최상위에 둠) ----------------
def zpath(cache, rcept_no):
    return os.path.join(cache, f"{rcept_no}.zip")


def num(s):
    s = re.sub(r"[^\d.\-]", "", s or "")
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def read_zip(path):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError):
        return ""
    txt = ""
    with z:
        for n in z.namelist():
            raw = z.read(n)
            for enc in ("utf-8", "cp949"):
                try:
                    txt += raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    pass
    return txt


_NUM_CELL = re.compile(r"^[-+△▲▽(]?\s*[\d,]+(?:\.\d+)?\s*\)?$")


def _is_num(c):
    return bool(_NUM_CELL.match((c or "").replace(" ", "")))


def _signed(c):
    """'(300)'·'△300'·'-300' → -300"""
    v = num(c)
    if v is None:
        return None
    t = (c or "").strip()
    if t.startswith(("(", "△", "▽", "-")) and v > 0:
        return -v
    return v


def parse_row(cells):
    """세부변동내역 한 줄 → (변동일 칸 위치, 증감, 단가, 방법, 종류) 또는 None.
    '변동 전 + 증감 = 변동 후'가 맞는 연속된 숫자 세 칸으로 증감을 찾고, 그다음 숫자 칸을 단가로 봅니다.
    (생년월일 칸이 날짜로 잡히거나 표 모양이 조금 달라도 칸이 밀리지 않게)"""
    dates = [i for i, c in enumerate(cells) if DATE_RE.search(c) and not _is_num(c)]
    if not dates:
        return None
    for k in range(dates[0] + 1, len(cells) - 2):
        a_, b_, c_ = cells[k], cells[k + 1], cells[k + 2]
        if not (_is_num(a_) and _is_num(b_) and _is_num(c_)):
            continue
        before, chg, after = num(a_), _signed(b_), num(c_)
        if None in (before, chg, after):
            continue
        tol = max(1.0, abs(after) * 1e-6)
        if abs(before + chg - after) <= tol:
            delta = chg
        elif abs(before - abs(chg) - after) <= tol:      # 감소인데 부호가 빠진 경우
            delta = -abs(chg)
        elif abs(before + abs(chg) - after) <= tol:
            delta = abs(chg)
        else:
            continue
        di = max(i for i in dates if i < k)               # 숫자 바로 앞의 날짜가 변동일
        price = None
        for j in range(k + 3, len(cells)):
            if _is_num(cells[j]):
                price = num(cells[j])
                break
        texts = [c for c in cells[di + 1:k] if c and not _is_num(c)]
        return di, delta, price, (texts[0] if texts else ""), (texts[1] if len(texts) > 1 else "")
    return None


def parse_one(rp, cache, body_word):
    xml = read_zip(zpath(cache, rp["rcept_no"]))
    if body_word and body_word not in xml:   # 본문 필터: 파싱 전에 문자열로 먼저 거름
        return []
    soup = BeautifulSoup(xml, PARSER)
    m = re.search(r"보유\s*목적[^가-힣]*([가-힣()\s]{2,30})", soup.get_text(" "))
    purpose = m.group(1).strip() if m else ""
    rows = []
    for tb in soup.find_all("table"):
        head = tb.get_text(" ")
        if "변동일" not in head or "단가" not in head:
            continue
        for tr in tb.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "te", "tu", "th"])]
            got = parse_row(cells)
            if got:
                di, qty, price, method, kind = got
            else:
                # 예전 방식 (날짜 칸 기준 고정 위치). 단가가 수량과 같으면 잘못 읽은 것이라 비움
                di = next((i for i, c in enumerate(cells) if DATE_RE.search(c)), None)
                if di is None or len(cells) < di + 7:
                    continue
                qty, price = num(cells[di + 4]), num(cells[di + 6])
                method, kind = cells[di + 1], cells[di + 2]
                if price is not None and qty is not None and abs(price) == abs(qty):
                    price = None
            y, mo, d = DATE_RE.search(cells[di]).groups()
            rows.append(dict(
                종목명=rp.get("stock_name") or rp["corp_name"], 종목코드=rp.get("stock_code"),
                회사=rp["corp_name"], 보고서=rp["report_nm"],
                제출일=_ymd(rp["rcept_dt"]), 접수번호=rp["rcept_no"], 보유목적=purpose,
                제출인=rp["flr_nm"], 보고자=cells[0],
                변동일=f"{y}-{int(mo):02d}-{int(d):02d}",
                방법=method, 종류=kind, 증감=qty, 단가=price))
    return rows


MARKETS = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}   # DART corp_cls


def _ymd(s):
    s = _norm_date(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def summarize(detail):
    df = pd.DataFrame(detail)
    if df.empty:
        return df, df
    if "제출일종가" not in df.columns:
        df["제출일종가"] = None
    # 제출일 옆에 종가가 오도록 열 순서 정리
    cols = [c for c in df.columns if c != "제출일종가"]
    cols.insert(cols.index("제출일") + 1, "제출일종가")
    df = df[cols]
    buy = df[(df["증감"] > 0) & df["단가"].notna() & (df["단가"] > 0)].copy()
    buy["금액"] = buy["증감"] * buy["단가"]
    keys = ["종목명", "종목코드"] + (["시장"] if "시장" in buy.columns else []) + ["제출인", "제출일", "제출일종가", "변동일"]
    summary = (buy.groupby(keys, dropna=False)
                  .agg(매수수량=("증감", "sum"), 매수금액=("금액", "sum"))
                  .reset_index())
    summary["평균매수단가"] = (summary["매수금액"] / summary["매수수량"]).round(0)
    summary = summary.sort_values(["종목명", "변동일", "제출일"])
    return summary, df


# ---------------- 제출일 주가 (네이버 증권 일별 시세) ----------------
PRICE_URL = "https://fchart.stock.naver.com/sise.nhn"
_PRICE_RE = re.compile(rb'data="(\d{8})\|([\d.]*)\|[\d.]*\|[\d.]*\|([\d.]+)\|')


def parse_ohlc(content):
    """네이버 일별 시세 응답 → ({YYYYMMDD: 종가}, {YYYYMMDD: 시가})"""
    closes, opens = {}, {}
    for d, o, c in _PRICE_RE.findall(content or b""):
        closes[d.decode()] = float(c)
        if o:
            opens[d.decode()] = float(o)
    return closes, opens


def parse_prices(content):
    """네이버 일별 시세 응답 → {YYYYMMDD: 종가}"""
    return parse_ohlc(content)[0]


def price_on(prices, ymd):
    """ymd(YYYY-MM-DD) 당일 종가. 휴장일이면 직전 거래일 종가."""
    import bisect
    key = _norm_date(ymd)
    days = sorted(prices)
    i = bisect.bisect_right(days, key)
    return prices[days[i - 1]] if i else None


def write_excel(path, summary, df):
    with pd.ExcelWriter(path) as w:
        for name, d in (("날짜별_평균매수단가", summary), ("세부변동내역_전체", df)):
            d.to_excel(w, sheet_name=name, index=False)
            ws = w.sheets[name]
            ws.freeze_panes = "A2"
            # 열 너비를 내용에 맞춤 (한글은 두 칸으로 계산)
            for i, col in enumerate(d.columns, 1):
                vals = [str(col)] + [str(v) for v in d[col].head(500).tolist()]
                width = max(sum(2 if ord(ch) > 127 else 1 for ch in v) for v in vals)
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(width + 2, 8), 50)


# ---------------- 엔진 ----------------
class Engine:
    def __init__(self, cfg, log=None, stop=None):
        self.cfg = cfg
        self.log = log or (lambda m: print(m, flush=True))
        self.stop = stop or threading.Event()   # 사용자 중지
        self._halt = threading.Event()          # 한도 초과 등으로 다운로드만 중단
        self._lock = threading.Lock()
        self._next_ok = 0.0
        self.calls = self.errors = self.inflight = 0
        self.opens = {}     # 종목코드 → {YYYYMMDD: 시가} (서버 대시보드용)
        self.started = self.last_ok = time.time()
        self.stage, self.done, self.total, self.stage_t0 = "준비", 0, 0, time.time()
        self.session = requests.Session()
        n = max(1, cfg.workers)
        self.session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=n, pool_maxsize=n))

    # ---- 상태 ----
    def _set_stage(self, name, total=0):
        with self._lock:
            self.stage, self.done, self.total, self.stage_t0 = name, 0, total, time.time()

    def _progress(self, done, total=None):
        with self._lock:
            self.done = done
            if total is not None:
                self.total = total

    def status(self):
        with self._lock:
            now = time.time()
            el = now - self.stage_t0
            eta = el / self.done * (self.total - self.done) if self.done and self.total else None
            return dict(stage=self.stage, elapsed=now - self.started, calls=self.calls,
                        idle=now - self.last_ok, inflight=self.inflight, errors=self.errors,
                        done=self.done, total=self.total, eta=eta,
                        uses_api=self.stage.startswith(("1단계", "2단계")))

    def heartbeat_line(self):
        s = self.status()
        stage = s["stage"]
        if stage.startswith("3단계") and s["total"]:
            stage = f"{stage} {s['done']}/{s['total']}"
        msg = (f"  ♥ {time.strftime('%H:%M:%S')} [{stage}] 경과 {s['elapsed'] / 60:.0f}분 · "
               f"API 호출 {s['calls']}회 · 마지막 응답 {s['idle']:.0f}초 전 · "
               f"대기중 요청 {s['inflight']}개 · 오류 {s['errors']}회")
        if s["uses_api"] and s["idle"] > WARN_IDLE:
            msg += ("\n  ⚠ 90초 넘게 DART 응답이 없습니다. 3분 넘게 이어지면 "
                    "중지하고 다시 실행하세요(받은 건 저장돼 있음).")
        return msg

    # ---- API 호출 (속도·건수 관리) ----
    def get(self, url, **params):
        if self.stop.is_set() or self._halt.is_set():
            raise Stopped
        params["crtfc_key"] = self.cfg.api_key
        with self._lock:
            if self.calls >= self.cfg.daily_budget:
                raise QuotaExceeded
            self.calls += 1
            now = time.monotonic()
            wait = max(0.0, self._next_ok - now)
            self._next_ok = max(now, self._next_ok) + 60.0 / self.cfg.max_per_min
        if wait and self.stop.wait(wait):
            raise Stopped
        for attempt in range(4):
            if self.stop.is_set():
                raise Stopped
            with self._lock:
                self.inflight += 1
            try:
                r = self.session.get(url, params=params, timeout=self.cfg.timeout)
                with self._lock:
                    self.last_ok = time.time()
                return r
            except requests.exceptions.RequestException as e:
                with self._lock:
                    self.errors += 1
                self.log(f"  ! 연결 오류({attempt + 1}/4): {type(e).__name__}")
            finally:
                with self._lock:
                    self.inflight -= 1
            if self.stop.wait(2 * (attempt + 1)):
                raise Stopped
        raise DartError("DART 서버에 연결할 수 없습니다. 인터넷 연결이나 회사망 차단 여부를 확인하세요.")

    # ---- 1단계: 목록 ----
    def list_reports(self):
        c = self.cfg
        path = os.path.join(c.cache, f"reports_{c.start}_{c.end}.json")
        if os.path.exists(path) and not c.list_refresh:
            with open(path, encoding="utf-8") as f:
                out = json.load(f)
            self.log(f"[1단계] 저장된 목록 사용: {len(out)}건")
            return out
        s = dt.datetime.strptime(c.start, "%Y%m%d")
        end = dt.datetime.strptime(c.end, "%Y%m%d")
        windows = []
        while s <= end:
            e = min(s + dt.timedelta(days=89), end)
            windows.append((s, e))
            s = e + dt.timedelta(days=1)
        self._progress(0, len(windows))
        out = []
        for wi, (s, e) in enumerate(windows):
            page = 1
            while True:
                try:
                    r = self.get(f"{BASE}/list.json", bgn_de=s.strftime("%Y%m%d"), end_de=e.strftime("%Y%m%d"),
                                 pblntf_detail_ty="D001", page_no=page, page_count=100).json()
                except QuotaExceeded:
                    raise DartError("이번 실행의 호출 한도를 다 썼습니다. 내일 다시 실행하세요.")
                except ValueError:
                    raise DartError("DART 응답을 읽을 수 없습니다. 잠시 뒤 다시 실행하세요.")
                st = r.get("status")
                if st == "013":
                    break
                if st != "000":
                    raise DartError(f"DART 오류 {st}: {r.get('message')}")
                out += [x for x in r["list"] if c.keyword in x["report_nm"]]
                self.log(f"[1단계] {s:%Y-%m-%d}~{e:%Y-%m-%d}  {page}/{r['total_page']}쪽  누적 {len(out)}건")
                if page >= int(r["total_page"]):
                    break
                page += 1
            self._progress(wi + 1)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, path)
        return out

    # ---- 종목명: DART 전체 회사 목록(corpCode.xml, 호출 1회, 7일간 재사용) ----
    def corp_names(self):
        path = os.path.join(self.cfg.cache, "corp_names.json")
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        try:
            data = self.get(f"{BASE}/corpCode.xml").content
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                root = ET.fromstring(z.read(z.namelist()[0]))
        except (Stopped, KeyboardInterrupt):
            raise
        except Exception as e:
            self.log(f"  ! 종목명 목록을 받지 못해 공시의 회사명을 씁니다({type(e).__name__}).")
            return {"stock": {}, "corp": {}}
        names = {"stock": {}, "corp": {}}
        for it in root.iter("list"):
            nm = (it.findtext("corp_name") or "").strip()
            sc = (it.findtext("stock_code") or "").strip()
            cc = (it.findtext("corp_code") or "").strip()
            if nm and sc:
                names["stock"][sc] = nm
            if nm and cc:
                names["corp"][cc] = nm
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(names, f, ensure_ascii=False)
        os.replace(tmp, path)
        self.log(f"[1단계] 종목명 목록 {len(names['stock'])}개 종목")
        return names

    def add_stock_names(self, reports):
        names = self.corp_names()
        for rp in reports:
            rp["stock_name"] = (names["stock"].get((rp.get("stock_code") or "").strip())
                                or names["corp"].get(rp.get("corp_code") or "")
                                or rp.get("corp_name", ""))

    # ---- 2단계: 원문 다운로드 ----
    def download(self, rcept_no):
        r = self.get(f"{BASE}/document.xml", rcept_no=rcept_no)
        data = r.content
        if data[:2] == b"PK":
            p = zpath(self.cfg.cache, rcept_no)
            tmp = p + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
            return "ok"
        head = data[:500].decode("utf-8", "ignore")
        if "<status>020" in head:
            return "quota"                      # 한도 초과
        if "<status>800" in head or "<status>901" in head or "<status>011" in head:
            return "blocked"
        return "skip"                           # 원문 없음 등

    def download_all(self, reports):
        cache = self.cfg.cache
        todo = [r["rcept_no"] for r in reports if not os.path.exists(zpath(cache, r["rcept_no"]))]
        have = len(reports) - len(todo)
        self.log(f"[2단계] 저장됨 {have}건 / 새로 받을 것 {len(todo)}건")
        self._progress(0, len(todo))
        if not todo:
            return True
        done, halted = 0, False
        with ThreadPoolExecutor(max(1, self.cfg.workers)) as ex:
            futs = {ex.submit(self.download, n): n for n in todo}
            for f in as_completed(futs):
                if f.cancelled():
                    continue
                try:
                    res = f.result()
                except QuotaExceeded:
                    res = "quota"
                except Stopped:
                    res = "stopped"
                except DartError as e:
                    res = "error"
                    if not halted:
                        self.log(f"  ! {e}")
                if res in ("quota", "blocked", "error") and not halted:
                    halted = True
                    self._halt.set()
                    why = {"quota": "호출 한도 초과", "blocked": "DART 접근 차단",
                           "error": "연결 실패"}[res]
                    self.log(f"\n  ! 다운로드 중단({why}). 다음에 다시 실행하면 남은 것만 이어서 받습니다.")
                    for g in futs:
                        g.cancel()
                if res == "stopped" and self.stop.is_set():
                    for g in futs:
                        g.cancel()
                done += 1
                self._progress(done)
                if done % 100 == 0 or done == len(todo):
                    st = self.status()
                    eta = f"{st['eta'] / 60:.0f}분" if st["eta"] is not None else "-"
                    self.log(f"[2단계] {done}/{len(todo)}  호출 {self.calls}회  남은시간 약 {eta}")
        if self.stop.is_set():
            raise Stopped
        return not halted

    # ---- 3단계: 분석 ----
    def analyze(self, reports):
        """원문 분석. 한 번 분석한 공시는 parsed.sqlite에 저장해 두고 다시 분석하지 않음."""
        import sqlite3
        cache = self.cfg.cache
        avail = [r for r in reports if os.path.exists(zpath(cache, r["rcept_no"]))]
        bw = self.cfg.body_word
        db = sqlite3.connect(os.path.join(cache, "parsed.sqlite"))
        db.execute("CREATE TABLE IF NOT EXISTS parsed (rcept_no TEXT, body_word TEXT, ver INTEGER,"
                   " rows TEXT, PRIMARY KEY (rcept_no, body_word))")
        done = {k: json.loads(v) for k, v in db.execute(
            "SELECT rcept_no, rows FROM parsed WHERE body_word=? AND ver=?", (bw, PARSE_VERSION))}
        todo = [r for r in avail if r["rcept_no"] not in done]
        self._progress(len(avail) - len(todo), len(avail))
        workers = self.cfg.parse_workers or os.cpu_count() or 1
        self.log(f"[3단계] 원문 {len(avail)}건 중 이미 분석 {len(avail) - len(todo)}건, "
                 f"새로 분석 {len(todo)}건 ({workers}개 동시)")
        fn = partial(parse_one, cache=cache, body_word=bw)
        pending = []

        def save(rp, rows, i):
            done[rp["rcept_no"]] = rows
            pending.append((rp["rcept_no"], bw, PARSE_VERSION, json.dumps(rows, ensure_ascii=False)))
            if len(pending) >= 500:
                flush()
            self._progress(len(avail) - len(todo) + i)
            if i % 1000 == 0 or i == len(todo):
                self.log(f"[3단계] 새로 분석 {i}/{len(todo)}")

        def flush():
            if pending:
                db.executemany("INSERT OR REPLACE INTO parsed VALUES (?,?,?,?)", pending)
                db.commit()
                pending.clear()

        try:
            if todo and workers > 1:
                try:
                    with ProcessPoolExecutor(workers) as ex:
                        for i, (rp, rows) in enumerate(zip(todo, ex.map(fn, todo, chunksize=50)), 1):
                            if self.stop.is_set():
                                ex.shutdown(wait=False, cancel_futures=True)
                                raise Stopped
                            save(rp, rows, i)
                except (BrokenProcessPool, OSError, PermissionError) as e:
                    # 일부 PC에서 병렬 처리가 막히면 한 개씩 처리
                    self.log(f"  ! 병렬 분석 불가({type(e).__name__}) → 한 개씩 분석합니다.")
                    flush()
            left = [r for r in todo if r["rcept_no"] not in done]
            for i, rp in enumerate(left, len(todo) - len(left) + 1):
                if self.stop.is_set():
                    raise Stopped
                save(rp, fn(rp), i)
        finally:
            flush()
            db.close()

        detail = []
        for rp in avail:
            name = rp.get("stock_name") or rp["corp_name"]
            market = MARKETS.get(rp.get("corp_cls", ""), "")
            for row in done.get(rp["rcept_no"], []):
                # 종목명은 최신 목록 기준, 시장 구분은 공시 목록에서
                detail.append({**row, "종목명": name, "시장": market})
        self.log(f"[3단계] 분석 완료  매매내역 {len(detail)}행")
        return detail

    # ---- 4단계: 제출일 주가 ----
    def fetch_prices(self, code, since):
        """종목 하나의 일별 종가. 오늘 받은 게 있으면 재사용."""
        folder = os.path.join(self.cfg.cache, "prices")
        path = os.path.join(folder, f"{code}.json")
        today = dt.date.today().strftime("%Y%m%d")
        try:
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("fetched") == today and c.get("since", "99999999") <= since and "opens" in c:
                self.opens[code] = c["opens"]
                return c["prices"]
        except (OSError, ValueError):
            pass
        days = (dt.date.today() - dt.datetime.strptime(since, "%Y%m%d").date()).days
        count = min(max(days + 30, 30), 8000)
        r = self.session.get(PRICE_URL, timeout=self.cfg.timeout, headers={"User-Agent": "Mozilla/5.0"},
                             params=dict(symbol=code, timeframe="day", count=count, requestType=0))
        prices, opens = parse_ohlc(r.content)
        self.opens[code] = opens
        if prices:
            os.makedirs(folder, exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(fetched=today, since=since, prices=prices, opens=opens), f)
            os.replace(tmp, path)
        return prices

    def add_prices(self, detail):
        if not self.cfg.add_prices or not detail:
            return
        need = {}
        for row in detail:
            code = (row.get("종목코드") or "").strip()
            if re.fullmatch(r"[0-9A-Z]{6}", code or ""):
                d = _norm_date(row["제출일"])
                need[code] = min(need.get(code, d), d)
        self._progress(0, len(need))
        self.log(f"[4단계] 제출일 주가 조회 ({len(need)}개 종목, 네이버 증권)")
        got, fails = {}, 0
        with ThreadPoolExecutor(max(1, self.cfg.workers)) as ex:
            futs = {ex.submit(self.fetch_prices, c, s): c for c, s in need.items()}
            for i, f in enumerate(as_completed(futs), 1):
                if self.stop.is_set():
                    for g in futs:
                        g.cancel()
                    raise Stopped
                try:
                    got[futs[f]] = f.result()
                except Exception:
                    fails += 1
                self._progress(i)
        if fails == len(need) and need:
            self.log("  ! 주가를 받지 못했습니다(네이버 증권 접속 불가). 제출일종가 칸은 비워 둡니다.")
        elif fails:
            self.log(f"  ! {fails}개 종목은 주가를 받지 못해 비워 둡니다.")
        self.prices = got   # 서버 대시보드 그래프용 (종목코드 → {YYYYMMDD: 종가})
        for row in detail:
            p = got.get((row.get("종목코드") or "").strip())
            row["제출일종가"] = price_on(p, row["제출일"]) if p else None

    # ---- 엑셀 ----
    def save_excel(self, detail, complete):
        summary, df = summarize(detail)
        if df.empty:
            self.log("결과 없음: 조건에 맞는 매매내역이 없습니다.")
            return None
        name = self.cfg.out_name if complete else self.cfg.out_name.replace(".xlsx", "_부분.xlsx")
        path = os.path.join(self.cfg.out_dir, name)
        try:
            write_excel(path, summary, df)
        except PermissionError:
            # 같은 이름의 엑셀이 열려 있으면 시간을 붙여 따로 저장
            path = path.replace(".xlsx", f"_{time.strftime('%H%M%S')}.xlsx")
            write_excel(path, summary, df)
        return path

    # ---- 전체 실행 ----
    def run(self):
        """완료 시 dict(path, complete, calls, rows) 반환. 중지 시 Stopped, 오류 시 DartError."""
        self.cfg.validate()
        try:
            os.makedirs(self.cfg.cache, exist_ok=True)
        except OSError as e:
            raise DartError(f"저장 폴더를 만들 수 없습니다: {e}")
        self._set_stage("1단계 목록")
        reports = self.list_reports()
        self.add_stock_names(reports)
        self._set_stage("2단계 다운로드")
        complete = self.download_all(reports)
        self._set_stage("3단계 분석")
        detail = self.analyze(reports)
        self.reports = reports
        if self.cfg.add_prices:
            self._set_stage("4단계 주가")
            self.add_prices(detail)
        self._set_stage("엑셀 저장")
        self.detail = detail
        path = self.save_excel(detail, complete)
        self._set_stage("완료")
        if path:
            self.log(f"완료 → {path}  (이번 실행 API 호출 {self.calls}회)")
            if not complete:
                self.log("※ 다 받지 못해 부분 결과입니다. 다음에 다시 실행하면 나머지만 받아 전체 결과를 만듭니다.")
        return dict(path=path, complete=complete, calls=self.calls, rows=len(detail))


# ---------------- 자체 점검 (설치본이 제대로 만들어졌는지 확인용) ----------------
SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT><BODY>
<P>보유목적 : 단순투자</P>
<TABLE>
<TR><TD>보고자</TD><TD>변동일</TD><TD>취득/처분방법</TD><TD>주식등의 종류</TD>
<TD>변동 전</TD><TD>증감</TD><TD>변동 후</TD><TD>취득/처분 단가</TD><TD>비고</TD></TR>
<TR><TD>테스트운용</TD><TD>2026년 03월 02일</TD><TD>장내매수(+)</TD><TD>보통주</TD>
<TD>1,000</TD><TD>500</TD><TD>1,500</TD><TD>12,000</TD><TD>-</TD></TR>
<TR><TD>테스트운용</TD><TD>2026.03.02</TD><TD>장내매수(+)</TD><TD>보통주</TD>
<TD>1,500</TD><TD>500</TD><TD>2,000</TD><TD>14,000</TD><TD>-</TD></TR>
<TR><TD>테스트운용</TD><TD>2026-03-03</TD><TD>장내매도(-)</TD><TD>보통주</TD>
<TD>2,000</TD><TD>-300</TD><TD>1,700</TD><TD>15,000</TD><TD>-</TD></TR>
</TABLE></BODY></DOCUMENT>"""


def selftest(workdir=None):
    """샘플 공시를 만들어 3단계 분석·엑셀 저장까지 돌려 봄. 성공하면 0."""
    import tempfile
    d = workdir or tempfile.mkdtemp(prefix="dart_selftest_")
    cache = os.path.join(d, "dart_cache")
    os.makedirs(cache, exist_ok=True)
    rp = dict(rcept_no="20260302000001", corp_name="테스트", stock_code="000000",
              report_nm="주식등의대량보유상황보고서(일반)", rcept_dt="20260302", flr_nm="테스트운용")
    with zipfile.ZipFile(zpath(cache, rp["rcept_no"]), "w") as z:
        z.writestr(f"{rp['rcept_no']}.xml", SAMPLE_XML.encode("utf-8"))
    fn = partial(parse_one, cache=cache, body_word="단순투자")
    with ProcessPoolExecutor(2) as ex:
        rows = list(ex.map(fn, [rp]))[0]
    assert len(rows) == 3 and rows[0]["종목명"] == "테스트", rows
    summary, df = summarize(rows)
    assert len(summary) == 1 and summary.iloc[0]["평균매수단가"] == 13000, summary
    assert summary.iloc[0]["제출일"] == "2026-03-02", summary
    px = parse_prices(b'<item data="20260227|1|2|1|9900|10" />\n<item data="20260303|1|2|1|10100|10" />')
    assert price_on(px, "2026-03-02") == 9900 and price_on(px, "2026-03-03") == 10100, px
    out = os.path.join(d, "selftest.xlsx")
    write_excel(out, summary, df)
    assert os.path.getsize(out) > 0
    return 0
