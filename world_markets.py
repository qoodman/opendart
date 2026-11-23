# -*- coding: utf-8 -*-
"""
해외 증시 사이클 + 종목: 미국·일본·중국(홍콩 포함)·유럽 증시가 지금 어떤 국면인지 가격으로 판단하고, 과거에 그 판단이 이 시장에서 통했는지 검증하며,
각 시장의 대표 종목을 재무·밸류·추세·증권사 목표가로 점수화해요. 데이터는 네이버 증권(해외지수·해외주식), 키 불필요.

- 시장 국면: 가격 위치(200일선 위·아래와 기울기, 고점 대비 낙폭, 1·3·12개월 수익률)만 써요. 금리·실적 같은 축은 이 앱이 해외 시장 것은 갖고 있지 않아요.
- 검증: 지수 일별 기록(2005~)에서 매일 '200일선 단계'(1~4)를 매기고, 그 뒤 3개월·6개월 수익률을 단계별로 평균 내서 단계 구분이 그 시장에서 실제로 의미가 있었는지 봐요
  (4단계 뒤가 2단계 뒤보다 나빴는지). 날짜가 겹쳐서 표본이 독립적이지 않고, 지나간 기록이라 앞으로를 보장하지 않아요.
- 종목: 대표 종목 몇십 개(시총 큰 순으로 제가 고른 목록)라 시장 전체가 아니에요. 유럽은 네이버가 현지 종목을 안 다뤄서 미국에 상장된 ADR을 써요.
  점수는 단순 기준이고, 시장 국면에 따라 가중치를 조금 바꿔요 (상승장은 성장·추세, 하락장은 수익성·밸류). 투자 추천이 아니에요.
"""
import datetime as dt
import json
import re
import sqlite3
import statistics
import time

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
           "Referer": "https://m.stock.naver.com/"}
API = "https://api.stock.naver.com"

# (표시 이름, 네이버 지수 코드, 지역)
INDEXES = [
    ("S&P 500", ".INX", "미국"), ("나스닥 종합", ".IXIC", "미국"), ("다우존스", ".DJI", "미국"),
    ("닛케이 225", ".N225", "일본"),
    ("상해종합", ".SSEC", "중국"), ("CSI 300", ".CSI300", "중국"), ("항셍", ".HSI", "중국"),
    ("유로스톡스 50", ".STOXX50E", "유럽"), ("독일 DAX", ".GDAXI", "유럽"), ("영국 FTSE 100", ".FTSE", "유럽"), ("프랑스 CAC 40", ".FCHI", "유럽"),
]
# 지역의 대표 지수 (그 지역 종목의 국면 가중치를 정할 때)
REGION_MAIN = {"미국": ".INX", "일본": ".N225", "중국": ".CSI300", "유럽": ".STOXX50E", "한국": "KOSPI"}
REGIONS = ["미국", "일본", "중국", "유럽"]

# 대표 종목 (네이버 해외주식 코드). 못 받는 종목은 건너뜀. 유럽은 미국 상장 ADR.
UNIVERSE = {
    "미국": ["AAPL.O", "MSFT.O", "NVDA.O", "AMZN.O", "GOOGL.O", "META.O", "TSLA.O", "AVGO.O", "COST.O", "NFLX.O", "AMD.O", "LLY", "JPM", "V", "XOM", "WMT", "UNH", "MA", "HD", "ORCL", "PG", "JNJ", "KO", "BAC", "CVX", "ABBV", "CRM", "MRK"],
    "일본": ["7203.T", "6758.T", "8306.T", "9984.T", "6861.T", "8035.T", "9983.T", "6501.T", "7974.T", "4063.T", "6098.T", "8058.T"],
    "중국": ["600519.SS", "300750.SZ", "601318.SS", "600036.SS", "000333.SZ", "002594.SZ", "0700.HK", "9988.HK", "3690.HK", "1810.HK", "0005.HK", "1211.HK"],
    "유럽": ["ASML.O", "SAP", "NVO", "SHEL", "AZN.O", "TTE", "SNY.O", "UL", "DEO", "NVS", "HSBC", "BP"],
}
FINANCIAL = {"JPM", "BAC", "8306.T", "601318.SS", "600036.SS", "0005.HK", "HSBC"}     # 은행·보험: 영업이익률이 의미 없어 수익성은 ROA로만 봐요
FIN_ROWS = {"매출액": "rev", "EBIT": "ebit", "당기순이익": "ni", "세후손익": "ni2", "PER": "per", "PBR": "pbr", "배당수익률": "dy", "ROA": "roa", "EBITDA": "ebitda"}


# ---------------- 저장 ----------------
def ensure(conn):
    import stock_market
    stock_market.ensure(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS world_stock (code TEXT PRIMARY KEY, region TEXT, name TEXT, payload TEXT, fetched TEXT);
    CREATE TABLE IF NOT EXISTS world_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def _get(url, params=None, session=None, tries=2):
    s = session or requests
    for k in range(tries):
        r = s.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 409:
            return None                                   # 이 서비스가 다루지 않는 종목
        time.sleep(1.0 * (k + 1))
    return None


def _chart(path, start, session=None):
    j = _get(f"{API}/chart/foreign/{path}/day", dict(startDateTime=start, endDateTime=(dt.date.today() + dt.timedelta(days=1)).strftime("%Y%m%d")), session)
    out = {}
    for x in j or []:
        d, c = str(x.get("localDate", "")), x.get("closePrice")
        if len(d) == 8 and c is not None:
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = float(c)
    return out


def sync_indexes(conn, log=print, session=None, sleep=0.3):
    import stock_market
    ensure(conn)
    n = 0
    for name, sym, _ in INDEXES:
        try:
            pts = _chart(f"index/{sym}", "20050101", session)
        except requests.RequestException as e:
            log(f"  ✖ {name}: {type(e).__name__}")
            continue
        if pts:
            stock_market.save_series(conn, f"widx:{sym}", pts)
            n += 1
        time.sleep(sleep)
    conn.execute("INSERT OR REPLACE INTO world_meta VALUES ('idx_synced', ?)", (dt.datetime.now().strftime("%Y-%m-%d %H:%M"),))
    conn.commit()
    return n


# ---------------- 국면 ----------------
def price_state(closes):
    """종가 목록(오래된 순) → dict(last, ma, slope, below_ma, dd, r1m, r3m, r12, low60) 또는 None (260개 미만)"""
    if len(closes) < 260:
        return None
    last = closes[-1]
    hi = max(closes[-250:])
    ma = sum(closes[-200:]) / 200
    ma_prev = sum(closes[-220:-20]) / 200
    return dict(last=last, ma=ma, slope=ma / ma_prev - 1, below_ma=last < ma, dd=last / hi - 1, r1m=last / closes[-22] - 1, r3m=last / closes[-64] - 1,
                r12=last / closes[-252] - 1, low60=last <= min(closes[-60:]) * 1.03)


def stage_of(below, slope):
    """와인스타인식 단계: 2 상승(200일선 위·상승) · 4 하락(아래·하락) · 3 꺾임 초입(아래인데 200일선은 아직 상승) · 1 바닥·반등(위인데 200일선은 아직 하락·평평)"""
    if not below and slope > 0:
        return 2
    if below and slope < 0:
        return 4
    if below:
        return 3
    return 1


STAGE_NAME = {1: "1단계 바닥·반등", 2: "2단계 상승", 3: "3단계 꺾임 초입", 4: "4단계 하락"}


def stage_stats(closes, horizons=(60, 120)):
    """매일의 단계를 매기고 그 뒤 h거래일 수익률을 단계별로 평균 → {단계: dict(n, m60, med60, win60, m120)} 와 baseline(전체 평균)"""
    n = len(closes)
    if n < 500:
        return None
    ma = [None] * n
    run = 0.0
    for i, v in enumerate(closes):
        run += v
        if i >= 200:
            run -= closes[i - 200]
        if i >= 199:
            ma[i] = run / 200
    by = {1: [], 2: [], 3: [], 4: []}
    for i in range(220, n - horizons[0]):
        if ma[i] is None or ma[i - 20] is None:
            continue
        st = stage_of(closes[i] < ma[i], ma[i] / ma[i - 20] - 1)
        r1 = closes[i + horizons[0]] / closes[i] - 1
        r2 = closes[i + horizons[1]] / closes[i] - 1 if i + horizons[1] < n else None
        by[st].append((r1, r2))
    out = {}
    allr = []
    for st, lst in by.items():
        if not lst:
            continue
        r1 = [a for a, _ in lst]
        r2 = [b for _, b in lst if b is not None]
        allr += r1
        out[st] = dict(n=len(lst), m60=sum(r1) / len(r1), med60=statistics.median(r1), win60=sum(1 for v in r1 if v > 0) / len(r1), m120=(sum(r2) / len(r2)) if r2 else None)
    base = (sum(allr) / len(allr)) if allr else None
    return dict(stages=out, base=base, span=n)


DD_BUCKETS = [(-0.05, "고점 대비 0~-5%"), (-0.10, "-5~-10%"), (-0.20, "-10~-20%"), (-0.30, "-20~-30%"), (-9.0, "-30% 이하")]


def dd_bucket_of(dd):
    for i, (lo, _) in enumerate(DD_BUCKETS):
        if dd > lo:
            return i
    return len(DD_BUCKETS) - 1


def dd_stats(closes, horizons=(60, 120)):
    """직전 250거래일 고점 대비 낙폭 구간별로 그 뒤 3개월·6개월 수익률 평균 → {구간 인덱스: dict(n, m60, win60, m120)}"""
    n = len(closes)
    if n < 500:
        return None
    by = {i: [] for i in range(len(DD_BUCKETS))}
    for i in range(250, n - horizons[0]):
        dd = closes[i] / max(closes[i - 249:i + 1]) - 1
        r1 = closes[i + horizons[0]] / closes[i] - 1
        r2 = closes[i + horizons[1]] / closes[i] - 1 if i + horizons[1] < n else None
        by[dd_bucket_of(dd)].append((r1, r2))
    out = {}
    for b, lst in by.items():
        if len(lst) < 20:
            continue
        r1 = [a_ for a_, _ in lst]
        r2 = [b_ for _, b_ in lst if b_ is not None]
        out[b] = dict(n=len(lst), m60=sum(r1) / len(r1), win60=sum(1 for v in r1 if v > 0) / len(r1), m120=(sum(r2) / len(r2)) if r2 else None)
    return out


def verdict_stats(ss):
    """단계 구분이 의미 있었나: 2단계 뒤 3개월 평균 − 4단계 뒤 3개월 평균 (%p)와 한 줄 평가"""
    if not ss or 2 not in ss["stages"] or 4 not in ss["stages"]:
        return None, "기록이 짧아 판단하지 못했어요"
    d = ss["stages"][2]["m60"] - ss["stages"][4]["m60"]
    if d >= 0.03:
        return d, "단계 구분이 통했어요 (상승 단계 뒤가 하락 단계 뒤보다 뚜렷하게 좋았어요)"
    if d >= 0.01:
        return d, "단계 구분이 약하게 통했어요"
    if d > -0.01:
        return d, "단계 구분이 거의 의미가 없었어요"
    return d, "단계 구분이 거꾸로였어요 (하락 단계 뒤가 더 좋았어요 — 저점 반등이 컸던 시장)"


# ---------------- 종목 ----------------
def _num(v):
    t = str(v if v is not None else "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def fetch_stock(code, session=None):
    """네이버 해외주식 한 종목 → dict(name, exchange, close, fin{키: {기간: 값}}, periods[(기간, 컨센서스여부)], cons{target, recomm}, closes[(날짜, 종가)]) 또는 None"""
    basic = _get(f"{API}/stock/{code}/basic", session=session, tries=1)
    if not basic:
        return None
    out = dict(code=code, name=basic.get("stockName") or code, name_en=basic.get("stockNameEng") or "", exchange=basic.get("stockExchangeName") or "", close=_num(basic.get("closePrice")))
    fin = _get(f"{API}/stock/{code}/finance/annual", session=session)
    out["fin"], out["periods"] = {}, []
    if fin:
        out["periods"] = [(t.get("key"), t.get("isConsensus") == "Y") for t in fin.get("trTitleList", [])]
        for row in fin.get("rowList", []):
            k = FIN_ROWS.get(row.get("title"))
            if k:
                out["fin"][k] = {p: _num((c or {}).get("value")) for p, c in (row.get("columns") or {}).items()}
        out["unit"] = fin.get("unit", "")
    integ = _get(f"{API}/stock/{code}/integration", session=session)
    ci = (integ or {}).get("consensusInfo") or {}
    out["cons"] = dict(target=_num(ci.get("priceTargetMean")), recomm=_num(ci.get("recommMean")), high=_num(ci.get("priceTargetHigh")), low=_num(ci.get("priceTargetLow")), date=ci.get("createDate"))
    pts = _chart(f"item/{code}", (dt.date.today() - dt.timedelta(days=800)).strftime("%Y%m%d"), session)
    out["closes"] = sorted(pts.items())
    if not out["close"] and out["closes"]:
        out["close"] = out["closes"][-1][1]
    return out


def sync_stocks(conn, log=print, session=None, sleep=0.35, max_age_days=6, regions=None):
    ensure(conn)
    today = dt.date.today()
    n = 0
    for region in (regions or REGIONS):
        for code in UNIVERSE.get(region, []):
            r = conn.execute("SELECT fetched FROM world_stock WHERE code=?", (code,)).fetchone()
            if r and r[0] >= (today - dt.timedelta(days=max_age_days)).isoformat():
                continue
            try:
                d = fetch_stock(code, session)
            except requests.RequestException as e:
                log(f"  ✖ {code}: {type(e).__name__}")
                continue
            if not d:
                continue
            conn.execute("INSERT OR REPLACE INTO world_stock VALUES (?,?,?,?,?)", (code, region, d["name"], json.dumps(d, ensure_ascii=False), today.isoformat()))
            conn.commit()
            n += 1
            time.sleep(sleep)
    log(f"[해외 종목] {n}개 받음")
    return n


def sync(db_path, log=print, session=None):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        return dict(지수=sync_indexes(conn, log, session), 종목=sync_stocks(conn, log, session))
    finally:
        conn.close()


def _growth(a, b):
    return None if (a is None or b is None or b <= 0) else a / b - 1


def stock_metrics(d):
    """fetch_stock 결과 → 지표 dict"""
    fin, per_ = d["fin"], d["periods"]
    act = [p for p, c in per_ if not c]
    cons = [p for p, c in per_ if c]
    m = {}
    if len(act) >= 1:
        L = act[-1]
        P = act[-2] if len(act) >= 2 else None
        g = lambda k, p: (fin.get(k) or {}).get(p) if p else None
        rev, ebit, ni = g("rev", L), g("ebit", L), g("ni", L)
        m.update(period=L, rev=rev, ebit=ebit, ni=ni)
        m["rev_yoy"] = _growth(rev, g("rev", P))
        m["ebit_yoy"] = _growth(ebit, g("ebit", P))
        m["ni_yoy"] = _growth(ni, g("ni", P))
        if g("ebit", P) is not None and g("ebit", P) <= 0 < (ebit or 0):
            m["turn"] = True
        if rev and ebit is not None and d.get("code") not in FINANCIAL:
            m["ebit_margin"] = ebit / rev
        if d.get("code") in FINANCIAL:
            m["financial"] = True
        if rev and g("rev", P) and g("ebit", P) is not None and d.get("code") not in FINANCIAL:
            m["ebit_margin_prev"] = g("ebit", P) / g("rev", P)
        m["roa"] = g("roa", L)
        m["per"], m["pbr"], m["dy"] = g("per", L), g("pbr", L), g("dy", L)
    if cons:
        C = cons[0]
        crev = (fin.get("rev") or {}).get(C)
        if crev and m.get("rev"):
            m["fwd_rev"] = crev / m["rev"] - 1
        cebit = (fin.get("ebit") or {}).get(C)
        if cebit is not None and m.get("ebit") and m["ebit"] > 0:
            m["fwd_ebit"] = cebit / m["ebit"] - 1
        m["fwd_period"] = C
    tg, close = d["cons"].get("target"), d.get("close")
    if tg and close and 0.2 <= tg / close <= 4.0:
        m["upside"] = tg / close - 1
    m["recomm"] = d["cons"].get("recomm")
    ps = price_state([c for _, c in d["closes"]])
    if ps:
        m["trend"] = ps
    return m


def phase_weights(situation_key):
    """시장 국면에 따라 점수 가중치를 조금 바꿈: 상승 → 성장·추세, 하락 → 수익성·밸류. 합이 100이 되게 정규화"""
    base = dict(growth=30, profit=25, value=20, trend=15, consensus=10)
    adj = {"up": dict(growth=1.15, trend=1.25, value=0.8), "down": dict(profit=1.2, value=1.2, trend=0.7, growth=0.9), "turn_down": dict(profit=1.15, value=1.1, trend=0.8),
           "rebound": dict(trend=1.1, value=1.1), "flat": dict(profit=1.1, value=1.1)}.get(situation_key, {})
    w = {k: v * adj.get(k, 1.0) for k, v in base.items()}
    tot = sum(w.values())
    return {k: v * 100 / tot for k, v in w.items()}


def score_stock(m, situation_key=None):
    """지표 → dict(total, parts{항목: 점수/만점}, reasons[(종류, 문장)]) — 값이 없는 항목은 중립(절반)으로 둠"""
    w = phase_weights(situation_key)
    parts, reasons = {}, []
    pct = lambda v: f"{v:+.0%}"

    def tier(v, rules, default=0.0):
        for th, f in rules:
            if v >= th:
                return f
        return default
    # 성장
    g = []
    if m.get("rev_yoy") is not None:
        g.append(tier(m["rev_yoy"], [(0.20, 1.0), (0.10, 0.8), (0.03, 0.55), (0.0, 0.35), (-0.05, 0.15)]))
        reasons.append(("good" if m["rev_yoy"] >= 0.10 else "warn" if m["rev_yoy"] < 0 else "info", f"매출 전년 대비 {pct(m['rev_yoy'])}"))
    if m.get("turn"):
        g.append(1.0)
        reasons.append(("good", "영업이익 흑자 전환"))
    elif m.get("ebit_yoy") is not None:
        g.append(tier(m["ebit_yoy"], [(0.30, 1.0), (0.10, 0.8), (0.0, 0.55), (-0.15, 0.25)]))
        reasons.append(("good" if m["ebit_yoy"] >= 0.10 else "warn" if m["ebit_yoy"] < -0.10 else "info", f"영업이익 전년 대비 {pct(m['ebit_yoy'])}"))
    if m.get("fwd_rev") is not None:
        g.append(tier(m["fwd_rev"], [(0.15, 1.0), (0.07, 0.75), (0.0, 0.5), (-0.05, 0.2)]))
        reasons.append(("info", f"증권사 예상 매출 {m.get('fwd_period', '')} {pct(m['fwd_rev'])}"))
    parts["성장"] = (sum(g) / len(g) if g else 0.5) * w["growth"]
    # 수익성
    p = []
    if m.get("ebit_margin") is not None:
        p.append(tier(m["ebit_margin"], [(0.25, 1.0), (0.15, 0.8), (0.08, 0.55), (0.0, 0.25)]))
        chg = "" if m.get("ebit_margin_prev") is None else f" (전년 {m['ebit_margin_prev']:.1%})"
        reasons.append(("good" if m["ebit_margin"] >= 0.15 else "warn" if m["ebit_margin"] < 0.03 else "info", f"영업이익률 {m['ebit_margin']:.1%}{chg}"))
    if m.get("roa") is not None:
        p.append(tier(m["roa"], [(15, 1.0), (8, 0.8), (4, 0.55), (0, 0.25)]))
    parts["수익성"] = (sum(p) / len(p) if p else 0.5) * w["profit"]
    # 밸류
    v = []
    per, pbr = m.get("per"), m.get("pbr")
    if per is not None:
        v.append(0.0 if per <= 0 else 1.0 if per <= 12 else 0.8 if per <= 20 else 0.55 if per <= 30 else 0.3 if per <= 45 else 0.1)
        reasons.append(("good" if 0 < per <= 15 else "warn" if per > 45 or per <= 0 else "info", f"PER {per:.1f}" + (f" · PBR {pbr:.1f}" if pbr is not None else "")))
    if pbr is not None and pbr > 0:
        v.append(1.0 if pbr <= 1.5 else 0.8 if pbr <= 3 else 0.5 if pbr <= 6 else 0.25 if pbr <= 12 else 0.1)
    if m.get("dy"):
        v.append(tier(m["dy"], [(4, 1.0), (2, 0.8), (1, 0.6)], 0.4))
    parts["밸류"] = (sum(v) / len(v) if v else 0.5) * w["value"]
    # 추세
    t = []
    tr = m.get("trend")
    if tr:
        t.append(0.2 if tr["below_ma"] else 0.8 + (0.2 if tr["slope"] > 0 else 0.0))
        t.append(tier(tr["r12"], [(0.30, 1.0), (0.10, 0.8), (0.0, 0.55), (-0.15, 0.3)]))
        reasons.append(("good" if not tr["below_ma"] and tr["slope"] > 0 else "warn" if tr["below_ma"] and tr["slope"] < 0 else "info",
                        f"주가 200일선 {'아래' if tr['below_ma'] else '위'}·기울기 {tr['slope']:+.1%} · 12개월 {pct(tr['r12'])} · 고점 대비 {tr['dd']:+.0%}"))
    parts["추세"] = (sum(t) / len(t) if t else 0.5) * w["trend"]
    # 증권사 컨센서스
    c = []
    if m.get("upside") is not None:
        c.append(tier(m["upside"], [(0.30, 1.0), (0.15, 0.8), (0.05, 0.6), (-0.05, 0.4), (-0.2, 0.2)]))
        reasons.append(("good" if m["upside"] >= 0.15 else "warn" if m["upside"] < -0.05 else "info", f"증권사 목표가 평균 상승여력 {pct(m['upside'])}"))
    if m.get("recomm") is not None:
        c.append(tier(m["recomm"], [(4.2, 1.0), (3.7, 0.8), (3.0, 0.5)], 0.25))          # 5점 만점(높을수록 매수)
    parts["컨센서스"] = (sum(c) / len(c) if c else 0.5) * w["consensus"]
    total = sum(parts.values())
    return dict(total=total, parts=parts, weights=w, reasons=reasons)


# ---------------- 읽기 ----------------
def index_series(conn, sym):
    import stock_market
    return list(stock_market.series(conn, f"widx:{sym}")) if sym != "KOSPI" else list(stock_market.series(conn, "idx:KOSPI"))


def load_stocks(conn, region=None):
    try:
        rows = conn.execute("SELECT code, region, name, payload, fetched FROM world_stock" + (" WHERE region=?" if region else ""), ((region,) if region else ())).fetchall()
    except sqlite3.Error:
        return []
    return [dict(code=r[0], region=r[1], name=r[2], d=json.loads(r[3]), fetched=r[4]) for r in rows]


# ---------------- 다자산 (ETF) ----------------
# (네이버 해외주식 코드, 이름, 자산군 키, 설명)
ASSETS = [
    ("SPY", "미국 대형주 (S&P500)", "eq_us", "S&P500 추종"), ("QQQ.O", "미국 나스닥100", "eq_us", "대형 기술주 중심"), ("IWM", "미국 소형주 (러셀2000)", "eq_us", "소형주"),
    ("VT", "전 세계 주식", "eq_us", "선진·신흥 전체"), ("EFA", "선진국(미국 제외)", "eq_dm", "유럽·일본 등"), ("EWJ", "일본 주식", "eq_dm", "MSCI Japan"), ("VGK", "유럽 주식", "eq_dm", "FTSE Europe"),
    ("EEM", "신흥국 주식", "eq_em", "MSCI 신흥국"), ("FXI", "중국 대형주", "eq_em", "중국 대형 H주"), ("MCHI.O", "중국 주식 (MSCI)", "eq_em", "MSCI China"), ("KWEB.K", "중국 인터넷", "eq_em", "중국 플랫폼 기업"),
    ("INDA.K", "인도 주식", "eq_em", "MSCI India"), ("EWZ", "브라질 주식", "eq_em", "MSCI Brazil"), ("EWY", "한국 주식 (미국 상장)", "eq_em", "MSCI Korea"),
    ("XLK", "미국 기술 섹터", "sec_cyc", "기술"), ("SMH.O", "반도체", "sec_cyc", "반도체 기업"), ("XLI", "미국 산업재", "sec_cyc", "산업재"), ("XLF", "미국 금융", "sec_cyc", "금융"), ("ITA", "미국 방산·항공", "sec_cyc", "방위산업"),
    ("XLE", "미국 에너지 섹터", "commodity", "에너지 기업"), ("XLV", "미국 헬스케어", "sec_def", "헬스케어"), ("XLP", "미국 필수소비재", "sec_def", "필수소비재"), ("XLU", "미국 유틸리티", "sec_def", "유틸리티"),
    ("SCHD.K", "미국 배당 성장", "dividend", "배당 성장주"), ("VYM", "미국 고배당", "dividend", "고배당주"), ("USMV.K", "미국 저변동성", "dividend", "변동성 낮은 주식"),
    ("TLT.O", "미국 장기국채 (20년+)", "bond_long", "금리 하락에 민감"), ("IEF.O", "미국 중기국채 (7~10년)", "bond_long", "중기 국채"), ("SHY.O", "미국 단기국채 (1~3년)", "bond_short", "금리 변동에 둔감"),
    ("BND.O", "미국 종합채권", "bond_short", "채권 전체"), ("TIP", "미국 물가연동채", "bond_short", "인플레이션 방어"), ("LQD", "미국 투자등급 회사채", "bond_short", "신용 위험 낮은 회사채"),
    ("HYG", "미국 하이일드 회사채", "bond_hy", "위험 선호에 민감"), ("EMB.O", "신흥국 달러채", "bond_hy", "신흥국 국채"),
    ("GLD", "금", "gold", "금 현물"), ("IAU", "금 (저비용)", "gold", "금 현물"), ("SLV", "은", "gold", "은 현물"), ("GDX", "금광 기업", "gold", "금광 주식 (변동성 큼)"),
    ("USO", "원유", "commodity", "선물 기반 (롤오버 비용)"), ("DBC", "원자재 종합", "commodity", "에너지·금속·농산물"), ("CPER.K", "구리", "commodity", "경기에 민감한 금속"),
    ("VNQ", "미국 리츠(부동산)", "reit", "부동산 투자 신탁"),
    ("UUP", "달러 (인덱스)", "dollar", "달러 강세에 투자"),
    ("IBIT.O", "비트코인", "crypto", "비트코인 현물 ETF"), ("ETHA.O", "이더리움", "crypto", "이더리움 현물 ETF"),
]
CLASS_NAME = {"eq_us": "미국 주식", "eq_dm": "선진국 주식", "eq_em": "신흥국 주식", "sec_cyc": "경기민감 섹터", "sec_def": "방어 섹터", "dividend": "배당·저변동", "bond_long": "장기·중기 국채",
              "bond_short": "단기·종합 채권", "bond_hy": "하이일드·신흥국채", "gold": "금·은", "commodity": "원자재·에너지", "reit": "부동산(리츠)", "dollar": "달러", "crypto": "가상자산"}
CLASS_GROUP = {"eq_us": "주식", "eq_dm": "주식", "eq_em": "주식", "sec_cyc": "주식", "sec_def": "주식", "dividend": "주식", "bond_long": "채권", "bond_short": "채권", "bond_hy": "채권",
               "gold": "실물·원자재", "commodity": "실물·원자재", "reit": "대체", "dollar": "대체", "crypto": "대체"}
# 국면(전 세계 주식시장 기준) × 자산군 궁합 0~1 — 투자 시계·경기 사이클에서 흔히 쓰는 생각의 틀을 옮긴 것 (일반론, 검증된 값이 아님)
CLASS_FIT = {
    "up": dict(eq_us=.8, eq_dm=.7, eq_em=.7, sec_cyc=.85, sec_def=.45, dividend=.55, bond_long=.35, bond_short=.4, bond_hy=.7, gold=.45, commodity=.6, reit=.6, dollar=.3, crypto=.75),
    "turn_down": dict(eq_us=.5, eq_dm=.45, eq_em=.4, sec_cyc=.4, sec_def=.7, dividend=.7, bond_long=.6, bond_short=.7, bond_hy=.35, gold=.7, commodity=.45, reit=.45, dollar=.65, crypto=.35),
    "down": dict(eq_us=.35, eq_dm=.3, eq_em=.25, sec_cyc=.25, sec_def=.75, dividend=.65, bond_long=.65, bond_short=.8, bond_hy=.3, gold=.8, commodity=.4, reit=.35, dollar=.75, crypto=.2),
    "rebound": dict(eq_us=.65, eq_dm=.6, eq_em=.65, sec_cyc=.7, sec_def=.5, dividend=.55, bond_long=.5, bond_short=.5, bond_hy=.6, gold=.5, commodity=.55, reit=.55, dollar=.4, crypto=.55),
    "flat": dict(eq_us=.55, eq_dm=.5, eq_em=.45, sec_cyc=.5, sec_def=.6, dividend=.7, bond_long=.55, bond_short=.65, bond_hy=.5, gold=.6, commodity=.5, reit=.5, dollar=.5, crypto=.45),
}


def sync_assets(conn, log=print, session=None, sleep=0.3):
    ensure(conn)
    import stock_market
    conn.execute("CREATE TABLE IF NOT EXISTS world_asset (code TEXT PRIMARY KEY, cls TEXT, name TEXT, name_en TEXT, fetched TEXT)")
    n = 0
    for code, name, cls, _ in ASSETS:
        try:
            pts = _chart(f"item/{code}", "20100101", session)
        except requests.RequestException as e:
            log(f"  ✖ {code}: {type(e).__name__}")
            continue
        if len(pts) < 60:
            continue
        stock_market.save_series(conn, f"wasset:{code}", pts)
        conn.execute("INSERT OR REPLACE INTO world_asset VALUES (?,?,?,?,?)", (code, cls, name, "", dt.date.today().isoformat()))
        n += 1
        time.sleep(sleep)
    conn.commit()
    log(f"[해외 자산] {n}개 받음")
    try:
        sync_fx(conn, log=log, session=session)
    except Exception as e:
        log(f"  ✖ 환율: {type(e).__name__}")
    return n


FX_URL = API + "/marketindex/exchange/FX_USDKRW/prices"


def sync_fx(conn, log=print, session=None, sleep=0.25, max_pages=120):
    """원/달러 환율 일별 (하나은행 매매기준율, 네이버). 이미 받은 날짜까지만 새로 받음 → series 'fx:usdkrw'"""
    import stock_market
    have = stock_market.series(conn, "fx:usdkrw")
    last = have[-1][0] if have else ""
    first = have[0][0] if have else ""
    full = bool(have) and first <= "2010-06-30"      # 과거까지 이미 받았으면 새 날짜만, 아니면 끝까지 이어서
    s_ = session or requests
    pts = {}
    stop = False
    n_saved = 0
    for pg in range(1, max_pages + 1):
        j = None
        for _ in range(5):
            try:
                j = s_.get(FX_URL, params=dict(page=pg, pageSize=60), headers=HEADERS, timeout=12).json()
                break
            except (requests.RequestException, ValueError):
                time.sleep(3)
        if not j:
            break
        for x in j:
            d, c = str(x.get("localTradedAt", "")), str(x.get("closePrice", "")).replace(",", "")
            if len(d) != 10 or not c:
                continue
            if first <= d <= last:
                stop = full
                continue
            try:
                pts[d] = float(c)
            except ValueError:
                pass
        if stop or min((str(x.get("localTradedAt", "9")) for x in j), default="9") < "2010-01-01":
            break
        if pg % 10 == 0 and pts:               # 중간에 끊겨도 받은 만큼은 남게 10쪽마다 저장
            stock_market.save_series(conn, "fx:usdkrw", pts)
            conn.commit()
            pts = {}
            n_saved += 10 * 60
        time.sleep(sleep)
    if pts:
        stock_market.save_series(conn, "fx:usdkrw", pts)
        conn.commit()
    log(f"[환율] 새로 받음 (마지막 쪽 {pg})")
    return len(pts) + n_saved


def fx_series(conn):
    import stock_market
    return list(stock_market.series(conn, "fx:usdkrw"))


def krw_returns(ser, fx):
    """USD 가격 ser × 환율 fx → 원화 기준 (r1m, r3m, r12). 환율은 같은 날 이전 가장 가까운 값"""
    import bisect
    if not ser or not fx:
        return None
    fd = [d for d, _ in fx]
    fv = [v for _, v in fx]

    def at(date):
        i = bisect.bisect_right(fd, date) - 1
        return fv[i] if i >= 0 else None

    def kr(i):
        f = at(ser[i][0])
        return ser[i][1] * f if f else None
    n = len(ser)
    cur = kr(n - 1)
    out = {}
    for k, off in (("r1m", 21), ("r3m", 63), ("r12", 252)):
        if n > off and cur:
            b = kr(n - 1 - off)
            out[k] = cur / b - 1 if b else None
        else:
            out[k] = None
    return out


def asset_series(conn, code):
    import stock_market
    return list(stock_market.series(conn, f"wasset:{code}"))


def asset_metrics(ser, spy_rets=None):
    """[(날짜, 종가)] → dict(last, r1m, r3m, r6m, r12m, cagr3, cagr5, vol, dd, ps, stage, corr, span) 또는 None"""
    import math
    cl = [v for _, v in ser]
    ps = price_state(cl)
    if not ps:
        return None
    rets = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl))]
    last252 = rets[-252:]
    mu = sum(last252) / len(last252)
    var = sum((x - mu) ** 2 for x in last252) / max(1, len(last252) - 1)
    m = dict(last=cl[-1], r1m=ps["r1m"], r3m=ps["r3m"], r12=ps["r12"], dd=ps["dd"], ps=ps, vol=math.sqrt(var) * math.sqrt(252), stage=stage_of(ps["below_ma"], ps["slope"]), span=len(cl))
    m["r6m"] = cl[-1] / cl[-127] - 1 if len(cl) > 127 else None
    for yrs, k in ((3, "cagr3"), (5, "cagr5")):
        n = 252 * yrs
        m[k] = (cl[-1] / cl[-n - 1]) ** (1 / yrs) - 1 if len(cl) > n + 1 else None
    if spy_rets:
        a, b = last252, spy_rets[-len(last252):]
        k = min(len(a), len(b))
        if k > 100:
            a, b = a[-k:], b[-k:]
            ma, mb = sum(a) / k, sum(b) / k
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
            m["corr"] = cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None
    return m


def asset_scores(m, cls, phase=None):
    """자산 지표 → {short, mid, long: dict(score, reasons[])}. 가격 기록만으로 보는 모멘텀·추세·위험조정 수익·분산 기여와 국면 궁합"""
    ps = m["ps"]
    fit = (CLASS_FIT.get(phase or "") or {}).get(cls)
    lin = lambda v, lo, hi: None if v is None else max(0.0, min(1.0, (v - lo) / (hi - lo)))
    trend = (0.75 if not ps["below_ma"] else 0.2) + (0.2 if ps["slope"] > 0 else 0.0)
    volv = lin(-m["vol"], -0.40, -0.08)
    sharpe12 = lin(m["r12"] / m["vol"] if m["vol"] else None, -0.5, 1.5)
    out = {}
    rs = {}

    def comb(items):
        have = [(w, v, r) for w, v, r in items if v is not None]
        if not have:
            return dict(score=None, reasons=[])
        tw = sum(w for w, _, _ in have)
        sc = sum(w * v for w, v, _ in have) / tw * 100
        rs_ = [r for w, v, r in sorted(have, key=lambda x: -(x[0] * x[1])) if v >= 0.65 and r][:3]
        return dict(score=sc, reasons=rs_)
    out["short"] = comb([(35, lin(m["r3m"] * 0.6 + m["r1m"] * 0.4, -0.12, 0.2), f"최근 1·3개월 {m['r1m']:+.0%}·{m['r3m']:+.0%}로 모멘텀이 좋아요"),
                         (25, trend, "200일선 위에서 오르는 추세"), (15, volv, f"변동성이 낮아요 (연 {m['vol']:.0%})"),
                         (25, fit, "지금 시장 국면에 잘 맞는 자산군")])
    out["mid"] = comb([(35, lin((m["r12"] * 0.6 + (m["r6m"] if m["r6m"] is not None else m["r12"]) * 0.4), -0.15, 0.35), f"6·12개월 수익 {m['r12']:+.0%}"),
                       (20, trend, "추세가 살아 있어요"), (20, sharpe12, "변동성 대비 수익이 좋아요"), (25, fit, "지금 시장 국면에 잘 맞는 자산군")])
    lt = m["cagr5"] if m["cagr5"] is not None else m["cagr3"]
    ltv = None if lt is None else (lin(lt, 0.0, 0.14))
    cr = lin((lt or 0) / m["vol"] if (lt is not None and m["vol"]) else None, 0.0, 0.8)
    div = None if m.get("corr") is None else lin(-m["corr"], -0.9, 0.5)
    out["long"] = comb([(35, ltv, f"장기 연 수익 {lt:+.0%}" if lt is not None else ""), (25, cr, "장기 위험조정 수익이 좋아요"), (20, div, f"미국 주식과 상관이 낮아 분산 효과가 커요 (상관 {m.get('corr', 0):.2f})"),
                        (10, lin(m["dd"], -0.35, -0.02), "낙폭이 크지 않아요"), (10, fit, "지금 시장 국면에 잘 맞는 자산군")])
    return out
