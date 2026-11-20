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
