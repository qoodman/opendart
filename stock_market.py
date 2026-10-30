# -*- coding: utf-8 -*-
"""
주식 시장 온도판: 지수 추세·금리·환율·통화량, 그리고 우리 데이터(기관 단순투자 매수, 증권사 목표가 변화)로
시장 국면을 '강세 / 중립 / 약세'로 판단합니다. 지표마다 +1(돈이 들어오는 쪽) / 0 / -1(빠지는 쪽)을 매기고
합합니다. 투자 판단을 대신하지 않는 참고 지표이고, 각 점수의 근거를 같이 보여줍니다.

출처
- 코스피·코스닥 일별: 네이버 일별 시세 (주가와 같은 곳, 키 불필요)
- 기준금리·국고채·환율·M2: 한국은행 ECOS (ECOS_API_KEY, 부동산 앱 .env에 있으면 그대로 씀)
  통계 코드는 바뀔 수 있어서 '표 이름'으로 찾고, 찾은 코드를 기억해 둡니다 (부동산 앱과 같은 방식).
"""
import re, json, time, sqlite3, datetime as dt
from dataclasses import dataclass, field

import requests

ECOS_BASE = "https://ecos.bok.or.kr/api"
START_YEAR = 2018
INDEX_URL = "https://fchart.stock.naver.com/sise.nhn"


class MarketError(Exception):
    pass


# ---------------- 저장 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS macro_series (series_id TEXT, period TEXT, value REAL, PRIMARY KEY (series_id, period));
    CREATE TABLE IF NOT EXISTS macro_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def save_series(conn, sid, points):
    conn.executemany("INSERT OR REPLACE INTO macro_series VALUES (?,?,?)", [(sid, p, v) for p, v in points.items()])
    conn.commit()
    return len(points)


def series(conn, sid):
    try:
        return conn.execute("SELECT period, value FROM macro_series WHERE series_id=? ORDER BY period", (sid,)).fetchall()
    except sqlite3.OperationalError:
        return []


def meta_get(conn, key, default=None):
    try:
        r = conn.execute("SELECT value FROM macro_meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default
    except (sqlite3.OperationalError, ValueError):
        return default


def meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO macro_meta VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
    conn.commit()


# ---------------- 한국은행 ECOS (부동산 앱 macro.py와 같은 방식) ----------------
@dataclass(frozen=True)
class Target:
    key: str
    label: str
    unit: str
    cycle: str                       # 원하는 주기 D(일)/M(월)
    table_all: tuple
    table_any: tuple = ()
    table_not: tuple = ()
    item_all: tuple = ()
    item_not: tuple = ()


ECOS_TARGETS = [
    Target("base_rate", "기준금리", "%", "M", ("기준금리",), item_all=("기준금리",)),
    Target("ktb3", "국고채 3년 금리", "%", "D", ("시장금리",), ("일별",), ("월", "분기"), item_all=("국고채", "3년")),
    Target("ktb10", "국고채 10년 금리", "%", "D", ("시장금리",), ("일별",), ("월", "분기"), item_all=("국고채", "10년")),
    Target("usdkrw", "원/달러 환율", "원", "D", ("환율",), ("대원화", "매매기준율", "일별"), ("월평균", "재정"),
           item_all=("미국", "달러")),
    Target("m2", "M2 광의통화 (평잔)", "십억원", "M", ("M2", "평잔"), ("계절조정",), ("말잔",), item_all=("M2",)),
]


def candidates(t, names, cycles):
    scored = []
    for code, name in names:
        n = re.sub(r"\s+", "", name or "")
        if not all(w in n for w in t.table_all) or any(w in n for w in t.table_not):
            continue
        score = sum(2 for w in t.table_any if w in n) - len(n) / 100
        if t.cycle in (cycles.get(code) or ""):
            score += 3
        scored.append((score, code))
    return [c for _, c in sorted(scored, key=lambda x: -x[0])]


def pick_item(t, items, strict=True):
    for it in items:
        name = re.sub(r"\s+", "", it.get("ITEM_NAME", ""))
        if strict and (not all(w in name for w in t.item_all) or any(w in name for w in t.item_not)):
            continue
        return it
    return None


class Ecos:
    def __init__(self, key, delay=0.3):
        if not key:
            raise MarketError("ECOS_API_KEY가 없습니다")
        self.key, self.delay, self.http = key, delay, requests.Session()

    def _get(self, service, *parts, start=1, end=1000):
        url = "/".join([ECOS_BASE, service, self.key, "json", "kr", str(start), str(end), *map(str, parts)])
        r = self.http.get(url, timeout=30)
        r.encoding = r.encoding if "charset" in r.headers.get("Content-Type", "").lower() else "utf-8"
        time.sleep(self.delay)
        if r.status_code >= 400:
            raise MarketError(f"ECOS HTTP {r.status_code}")
        data = r.json()
        if service in data:
            return data[service].get("row") or []
        res = data.get("RESULT") or {}
        if res.get("CODE") == "INFO-200":
            return []
        raise MarketError(f"ECOS 오류: {res.get('CODE')} {res.get('MESSAGE')}")

    def tables(self):
        return self._get("StatisticTableList", start=1, end=10000)

    def items(self, stat):
        return self._get("StatisticItemList", stat, start=1, end=1000)

    def search(self, stat, cycle, start, end, item):
        rows, page = [], 1
        while True:
            got = self._get("StatisticSearch", stat, cycle, start, end, item, start=(page - 1) * 1000 + 1, end=page * 1000)
            rows += got
            if len(got) < 1000:
                return rows
            page += 1


def discover(api, log=print):
    tables = api.tables()
    names = [(t["STAT_CODE"], t.get("STAT_NAME", "")) for t in tables if t.get("SRCH_YN", "Y") == "Y"]
    cycles = {t["STAT_CODE"]: t.get("CYCLE", "") for t in tables}
    found = {}
    for t in ECOS_TARGETS:
        cands = candidates(t, names, cycles)[:6]
        chosen = None
        for code in cands:
            items = api.items(code)
            same = [x for x in items if x.get("CYCLE") == t.cycle] or items
            it = pick_item(t, same)
            if it:
                chosen = (code, it)
                break
        if not chosen:
            log(f"  ✖ {t.label}: ECOS 표를 찾지 못했습니다")
            continue
        code, it = chosen
        found[t.key] = {"stat": code, "item": it["ITEM_CODE"], "cycle": it.get("CYCLE") or t.cycle,
                        "table": dict(names).get(code, ""), "item_name": it.get("ITEM_NAME", "")}
        log(f"  ✔ {t.label}: {code} {found[t.key]['table']} / {it.get('ITEM_NAME', '')}")
    return found


def _period(t):
    t = str(t)
    if re.fullmatch(r"\d{8}", t):
        return f"{t[:4]}-{t[4:6]}-{t[6:]}"
    if re.fullmatch(r"\d{6}", t):
        return f"{t[:4]}-{t[4:]}"
    return t


def sync_ecos(conn, key, log=print):
    api = Ecos(key)
    codes = meta_get(conn, "ecos_codes", {}) or {}
    if len(codes) < len(ECOS_TARGETS):
        codes.update({k: v for k, v in discover(api, log).items() if k not in codes})
        meta_set(conn, "ecos_codes", codes)
    today = dt.date.today()
    out = {}
    for t in ECOS_TARGETS:
        c = codes.get(t.key)
        if not c:
            continue
        cyc = c.get("cycle", t.cycle)
        start = f"{START_YEAR}0101" if cyc == "D" else f"{START_YEAR}01"
        end = today.strftime("%Y%m%d") if cyc == "D" else today.strftime("%Y%m")
        try:
            rows = api.search(c["stat"], cyc, start, end, c["item"])
        except (MarketError, requests.exceptions.RequestException) as e:
            log(f"  ✖ {t.label}: {e}")
            continue
        pts = {}
        for r in rows:
            try:
                pts.setdefault(_period(r.get("TIME", "")), float(str(r.get("DATA_VALUE")).replace(",", "")))
            except (TypeError, ValueError):
                pass
        if t.key == "base_rate" and cyc == "D":     # 기준금리 일별 → 월말 값
            m = {}
            for p in sorted(pts):
                m[p[:7]] = pts[p]
            pts = m
        out[t.label] = save_series(conn, f"ecos:{t.key}", pts)
    return out


# ---------------- 지수 (네이버 일별 시세) ----------------
def sync_index(conn, log=print):
    out = {}
    for sym in ("KOSPI", "KOSDAQ"):
        try:
            r = requests.get(INDEX_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"},
                             params=dict(symbol=sym, timeframe="day", count=1500, requestType=0))
            rows = re.findall(rb'data="(\d{8})\|[\d.]*\|[\d.]*\|[\d.]*\|([\d.]+)\|', r.content)
        except requests.exceptions.RequestException as e:
            log(f"  ✖ {sym}: {type(e).__name__}")
            continue
        pts = {f"{d[:4].decode()}-{d[4:6].decode()}-{d[6:].decode()}": float(v) for d, v in rows}
        if pts:
            out[sym] = save_series(conn, f"idx:{sym}", pts)
    return out


def sync(db_path, ecos_key="", log=print):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        res = {"지수": sync_index(conn, log)}
        if ecos_key:
            try:
                res["ECOS"] = sync_ecos(conn, ecos_key, log)
            except (MarketError, requests.exceptions.RequestException, ValueError) as e:
                res["ECOS"] = f"실패: {e}"
                log(f"  ✖ ECOS: {e}")
        else:
            res["ECOS"] = "키 없음"
        meta_set(conn, "synced", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        log(f"[시장] {res}")
        return res
    finally:
        conn.close()


# ---------------- 지표와 점수 ----------------
@dataclass
class Indicator:
    key: str
    label: str
    value_text: str = "-"
    period: str = ""
    change_text: str = ""
    signal: int = 0
    reason: str = ""
    points: list = field(default_factory=list)
    note: str = ""

    @property
    def available(self):
        return self.value_text != "-"


def _ago(points, days):
    """points(날짜순 [(YYYY-MM[-DD], v)]) 마지막 값에서 days일 전쯤의 값"""
    if not points:
        return None
    last = points[-1][0]
    try:
        end = dt.date.fromisoformat(last if len(last) == 10 else last + "-28")
    except ValueError:
        return None
    target = (end - dt.timedelta(days=days)).isoformat()[:len(last)]
    prev = [v for p, v in points if p <= target]
    return prev[-1] if prev else None


def _fmt(v, digits=2, unit=""):
    if v is None:
        return "-"
    return f"{v:,.{digits}f}{unit}"


def _index_ind(conn, sym, label):
    pts = list(series(conn, f"idx:{sym}"))
    ind = Indicator(f"idx_{sym}", f"{label} 추세", points=[v for _, v in pts[-250:]])
    if len(pts) < 60:
        ind.note = "지수 자료가 아직 없습니다"
        return ind
    vals = [v for _, v in pts]
    last = vals[-1]
    ma200 = sum(vals[-200:]) / min(200, len(vals))
    chg60 = last / vals[-61] - 1 if len(vals) > 60 else 0
    gap = last / ma200 - 1
    ind.value_text, ind.period = f"{last:,.2f}", pts[-1][0]
    ind.change_text = f"200일 평균 대비 {gap:+.1%} · 60거래일 {chg60:+.1%}"
    if gap > 0.03 and chg60 > 0:
        ind.signal, ind.reason = 1, "200일 평균 위에서 오르는 중 (상승 추세)"
    elif gap < -0.03 and chg60 < 0:
        ind.signal, ind.reason = -1, "200일 평균 아래에서 내리는 중 (하락 추세)"
    else:
        ind.reason = "200일 평균 근처 (방향 탐색)"
    return ind


def _ecos(conn, key):
    return list(series(conn, f"ecos:{key}"))


def indicators(conn):
    out = [_index_ind(conn, "KOSPI", "코스피"), _index_ind(conn, "KOSDAQ", "코스닥")]

    # 기준금리: 6개월 변화
    pts = _ecos(conn, "base_rate")
    ind = Indicator("base_rate", "기준금리", points=[v for _, v in pts[-36:]])
    if pts:
        last, prev = pts[-1][1], _ago(pts, 182)
        ind.value_text, ind.period = _fmt(last, 2, "%"), pts[-1][0]
        if prev is not None:
            d = last - prev
            ind.change_text = f"6개월 {d:+.2f}%p"
            ind.signal = 1 if d < 0 else -1 if d > 0 else 0
            ind.reason = "금리 인하 (주식에 우호적)" if d < 0 else "금리 인상 (부담)" if d > 0 else "6개월째 동결"
    else:
        ind.note = "ECOS 키가 있으면 보입니다"
    out.append(ind)

    # 국고채 3년: 3개월 변화
    pts = _ecos(conn, "ktb3")
    ind = Indicator("ktb3", "국고채 3년 금리", points=[v for _, v in pts[-250:]])
    if pts:
        last, prev = pts[-1][1], _ago(pts, 91)
        ind.value_text, ind.period = _fmt(last, 3, "%"), pts[-1][0]
        if prev is not None:
            d = last - prev
            ind.change_text = f"3개월 {d:+.2f}%p"
            ind.signal = 1 if d <= -0.25 else -1 if d >= 0.25 else 0
            ind.reason = ("시장금리 하락 (주식 가치에 우호적)" if ind.signal > 0 else
                          "시장금리 상승 (부담)" if ind.signal < 0 else "시장금리 큰 변화 없음")
    else:
        ind.note = "ECOS 키가 있으면 보입니다"
    out.append(ind)

    # 장단기 금리차 (10년 − 3년)
    p10, p3 = dict(_ecos(conn, "ktb10")), dict(_ecos(conn, "ktb3"))
    common = sorted(set(p10) & set(p3))
    spread = [(p, p10[p] - p3[p]) for p in common]
    ind = Indicator("spread", "장단기 금리차 (10년 − 3년)", points=[v for _, v in spread[-250:]])
    if spread:
        last = spread[-1][1]
        ind.value_text, ind.period = _fmt(last, 2, "%p"), spread[-1][0]
        ind.signal = -1 if last < 0 else 1 if last >= 0.5 else 0
        ind.reason = ("금리 역전 (경기 둔화 신호)" if last < 0 else
                      "금리차 충분 (경기 기대 양호)" if last >= 0.5 else "금리차 좁음")
    else:
        ind.note = "ECOS 키가 있으면 보입니다"
    out.append(ind)

    # 원/달러 환율: 3개월 변화 (원화 강세 = 외국인 자금 유입에 우호적)
    pts = _ecos(conn, "usdkrw")
    ind = Indicator("usdkrw", "원/달러 환율", points=[v for _, v in pts[-250:]])
    if pts:
        last, prev = pts[-1][1], _ago(pts, 91)
        ind.value_text, ind.period = _fmt(last, 1, "원"), pts[-1][0]
        if prev:
            d = last / prev - 1
            ind.change_text = f"3개월 {d:+.1%}"
            ind.signal = 1 if d <= -0.03 else -1 if d >= 0.03 else 0
            ind.reason = ("원화 강세 (외국인 자금 유입에 우호적)" if ind.signal > 0 else
                          "원화 약세 (외국인 자금 이탈 부담)" if ind.signal < 0 else "환율 안정")
    else:
        ind.note = "ECOS 키가 있으면 보입니다"
    out.append(ind)

    # M2 증가율 (전년 대비)
    pts = _ecos(conn, "m2")
    yoy = []
    m = dict(pts)
    for p, v in pts:
        y = f"{int(p[:4]) - 1:04d}{p[4:]}"
        if m.get(y):
            yoy.append((p, (v / m[y] - 1) * 100))
    ind = Indicator("m2", "통화량 (M2) 증가율", points=[v for _, v in yoy[-36:]])
    if yoy:
        last = yoy[-1][1]
        ind.value_text, ind.period = _fmt(last, 1, "%"), yoy[-1][0]
        ind.change_text = "전년 같은 달 대비"
        ind.signal = 1 if last >= 7 else -1 if last < 4 else 0
        ind.reason = "돈이 빠르게 풀리는 중" if last >= 7 else "통화량 증가 둔화" if last < 4 else "보통 수준"
    else:
        ind.note = "ECOS 키가 있으면 보입니다"
    out.append(ind)

    out.append(_buy_flow(conn))
    out.append(_target_revisions(conn))
    return out


def _buy_flow(conn):
    """기관 단순투자 매수금액: 최근 30일 vs 그 전 30일 (변동일 기준)"""
    ind = Indicator("buy_flow", "기관 단순투자 매수 흐름")
    try:
        today = dt.date.today()
        d30, d60 = (today - dt.timedelta(days=30)).isoformat(), (today - dt.timedelta(days=60)).isoformat()
        cur = conn.execute("SELECT COALESCE(SUM(매수금액),0), COUNT(DISTINCT 종목코드) FROM summary WHERE 변동일>=?", (d30,)).fetchone()
        prev = conn.execute("SELECT COALESCE(SUM(매수금액),0) FROM summary WHERE 변동일>=? AND 변동일<?", (d60, d30)).fetchone()[0]
        weeks = conn.execute("SELECT substr(변동일,1,7), SUM(매수금액) FROM summary WHERE 변동일>=? GROUP BY 1 ORDER BY 1",
                             ((today - dt.timedelta(days=365)).isoformat(),)).fetchall()
    except sqlite3.OperationalError:
        ind.note = "보고서 수집 후 보입니다"
        return ind
    ind.points = [v or 0 for _, v in weeks]
    ind.value_text = f"{cur[0] / 1e8:,.0f}억 · {cur[1]}종목"
    ind.period = "최근 30일"
    if prev:
        r = cur[0] / prev
        ind.change_text = f"그 전 30일 대비 {r - 1:+.0%}"
        ind.signal = 1 if r >= 1.2 else -1 if r <= 0.8 else 0
        ind.reason = ("기관이 사들이는 금액이 늘어남" if ind.signal > 0 else
                      "기관 매수 금액이 줄어듦" if ind.signal < 0 else "비슷한 수준")
    else:
        ind.reason = "비교할 이전 자료가 부족합니다"
    ind.note = "공시는 매매 후 최대 5영업일 뒤에 나와서 최근 며칠은 덜 잡힐 수 있습니다"
    return ind


def _target_revisions(conn):
    """최근 30일 증권사 목표가: 같은 증권사의 직전 목표가보다 올린 것 vs 내린 것"""
    ind = Indicator("revisions", "증권사 목표가 상향 · 하향")
    try:
        rows = conn.execute("SELECT code, broker, date, target FROM reports WHERE target IS NOT NULL "
                            "ORDER BY code, broker, date").fetchall()
    except sqlite3.OperationalError:
        ind.note = "리포트 수집 후 보입니다"
        return ind
    import research
    since = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    last, up, down, monthly = {}, 0, 0, {}
    for code, broker, date, target in rows:
        k = (code, research.norm_broker(broker))
        if k in last and last[k] != target:
            mon = date[:7]
            u, d = monthly.get(mon, (0, 0))
            if target > last[k]:
                monthly[mon] = (u + 1, d)
                if date >= since:
                    up += 1
            else:
                monthly[mon] = (u, d + 1)
                if date >= since:
                    down += 1
        last[k] = target
    ind.points = [(u - d) / max(u + d, 1) * 100 for _, (u, d) in sorted(monthly.items())[-12:]]
    if up + down == 0:
        ind.reason = "최근 30일 목표가 변경이 없습니다"
        return ind
    share = up / (up + down)
    ind.value_text, ind.period = f"상향 {up} · 하향 {down}", "최근 30일"
    ind.change_text = f"상향 비율 {share:.0%}"
    ind.signal = 1 if share >= 0.6 else -1 if share <= 0.4 else 0
    ind.reason = ("증권사들이 목표가를 올리는 쪽 (실적 기대 상승)" if ind.signal > 0 else
                  "목표가를 내리는 쪽 (실적 기대 하락)" if ind.signal < 0 else "올림과 내림이 비슷")
    ind.note = "같은 증권사가 같은 종목 목표가를 바꾼 경우만 셉니다"
    return ind


def regime(inds):
    score = sum(i.signal for i in inds if i.available)
    n = sum(1 for i in inds if i.available)
    if score >= 3:
        return "강세", score, n
    if score <= -3:
        return "약세", score, n
    return "중립", score, n
