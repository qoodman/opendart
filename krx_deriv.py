# -*- coding: utf-8 -*-
"""
KRX Open API 파생상품·채권·금시장 일별매매정보 → 선물·채권 탭의 신호 (사용자가 올린 명세서 기준).

  drv/fut_bydd_trd   선물(주식선물 제외): PROD_NM MKT_NM(정규/야간) ISU_NM TDD_CLSPRC ACC_TRDVOL ACC_OPNINT_QTY(미결제약정)
  drv/opt_bydd_trd   옵션(주식옵션 제외): PROD_NM RGHT_TP_NM(CALL/PUT) ISU_NM IMP_VOLT(내재변동성) ACC_TRDVOL ACC_OPNINT_QTY
  drv/eqsfu_stk_bydd_trd · drv/eqkfu_ksq_bydd_trd   주식선물(유가·코스닥)
  drv/eqsop_bydd_trd · drv/eqkop_bydd_trd           주식옵션(유가·코스닥)
  bon/kts_bydd_trd   국채전문유통시장: BND_EXP_TP_NM(만기년수) GOVBND_ISU_TP_NM(종목구분) CLSPRC_YD(종가 수익률)
  gen/gold_bydd_trd  금시장: ISU_NM TDD_CLSPRC ACC_TRDVOL
상품 이름(예: '코스피200 선물')·종목 이름 형식은 명세서에 예시가 없어서 이름 규칙으로 찾고, 못 찾으면 '연결 확인'에서 받은 첫 줄을 보고 고치게 했어요.
일반채권·소액채권 시장은 신용등급 칸이 없어서 회사채 신용 스프레드는 만들지 않았어요.
"""
import datetime as dt
import re
import sqlite3
import time

import krx_api
from krx_api import KrxError, call, num

SVC = {"fut": "drv/fut_bydd_trd", "opt": "drv/opt_bydd_trd", "sfu": "drv/eqsfu_stk_bydd_trd", "kfu": "drv/eqkfu_ksq_bydd_trd",
       "sop": "drv/eqsop_bydd_trd", "kop": "drv/eqkop_bydd_trd", "kts": "bon/kts_bydd_trd", "gold": "gen/gold_bydd_trd"}
KEEP_DAYS = 400


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS kd_opt (d TEXT, prod TEXT, put_vol REAL, call_vol REAL, put_oi REAL, call_oi REAL, PRIMARY KEY (d, prod));
    CREATE TABLE IF NOT EXISTS kd_fut (d TEXT, prod TEXT, vol REAL, oi REAL, close REAL, PRIMARY KEY (d, prod));
    CREATE TABLE IF NOT EXISTS kd_iv (d TEXT PRIMARY KEY, iv REAL, n INTEGER);
    CREATE TABLE IF NOT EXISTS kd_curve (d TEXT, tenor TEXT, yld REAL, PRIMARY KEY (d, tenor));
    CREATE TABLE IF NOT EXISTS kd_gold (d TEXT PRIMARY KEY, name TEXT, close REAL, vol REAL);
    CREATE TABLE IF NOT EXISTS kd_stock (d TEXT, kind TEXT, prod TEXT, vol REAL, oi REAL, put_vol REAL, call_vol REAL, PRIMARY KEY (d, kind, prod));
    CREATE TABLE IF NOT EXISTS kd_checked (d TEXT PRIMARY KEY, rows INTEGER);
    """)


def sget(r, *names):
    for n in names:
        if n in r and r[n] not in (None, ""):
            return r[n]
    return None


# ---------------- 한 날짜 분량을 줄 목록에서 풀어 저장 ----------------
def is_put(r):
    t = str(sget(r, "RGHT_TP_NM") or "").upper()
    return "PUT" in t or "풋" in t


def is_call(r):
    t = str(sget(r, "RGHT_TP_NM") or "").upper()
    return "CALL" in t or "콜" in t


def agg_options(rows):
    """{PROD_NM: [put_vol, call_vol, put_oi, call_oi]}"""
    out = {}
    for r in rows:
        prod = str(sget(r, "PROD_NM") or "").strip()
        if not prod:
            continue
        v, oi = num(sget(r, "ACC_TRDVOL")) or 0, num(sget(r, "ACC_OPNINT_QTY")) or 0
        a = out.setdefault(prod, [0, 0, 0, 0])
        if is_put(r):
            a[0] += v
            a[2] += oi
        elif is_call(r):
            a[1] += v
            a[3] += oi
    return out


def agg_futures(rows):
    """{PROD_NM: [거래량, 미결제약정, 대표 종가]} 정규장만 (야간 제외). 종가는 거래량이 가장 큰 종목 것"""
    out, best = {}, {}
    for r in rows:
        if "야간" in str(sget(r, "MKT_NM") or ""):
            continue
        prod = str(sget(r, "PROD_NM") or "").strip()
        if not prod:
            continue
        v, oi, c = num(sget(r, "ACC_TRDVOL")) or 0, num(sget(r, "ACC_OPNINT_QTY")) or 0, num(sget(r, "TDD_CLSPRC"))
        a = out.setdefault(prod, [0, 0, None])
        a[0] += v
        a[1] += oi
        if c and v >= best.get(prod, -1):
            best[prod], a[2] = v, c
    return out


STRIKE_RE = re.compile(r"(\d{6})\D+([\d.,]+)\s*$")


def parse_option_name(name):
    """'코스피200 C 202612 350.0' 같은 이름에서 (만기 YYYYMM, 행사가). 못 읽으면 None"""
    m = STRIKE_RE.search(str(name or "").strip())
    if not m:
        return None
    try:
        return m.group(1), float(m.group(2).replace(",", ""))
    except ValueError:
        return None


def main_prod(prods, must="코스피200", skip=("미니", "위클리", "목요일", "월요일", "KRX", "변동성", "섹터")):
    """상품 이름들 중 대표(코스피200 본상품): '코스피200'이 들어가고 미니·위클리가 없는 것 중 거래량이 큰 것. prods: {이름: 거래량}"""
    cand = {k: v for k, v in prods.items() if must in k and not any(x in k for x in skip)}
    if not cand:
        cand = {k: v for k, v in prods.items() if "코스피" in k and not any(x in k for x in skip)}
    return max(cand, key=cand.get) if cand else None


def atm_iv(rows, spot, today_yyyymm):
    """대표 옵션 줄들에서 가장 가까운 만기·현물 근처(±3%) 행사가의 내재변동성 평균 → (평균 %, 개수) 또는 None"""
    parsed = []
    for r in rows:
        iv = num(sget(r, "IMP_VOLT"))
        pn = parse_option_name(sget(r, "ISU_NM"))
        if iv and iv > 0 and pn:
            parsed.append((pn[0], pn[1], iv))
    parsed = [x for x in parsed if x[0] >= today_yyyymm]
    if not parsed or not spot:
        return None
    exp = min(x[0] for x in parsed)
    near = [x for x in parsed if x[0] == exp and abs(x[1] / spot - 1) <= 0.03]
    if not near:
        near = sorted((x for x in parsed if x[0] == exp), key=lambda x: abs(x[1] - spot))[:4]
    return (sum(x[2] for x in near) / len(near), len(near)) if near else None


def agg_stock(rows, options):
    """{PROD_NM: [거래량, 미결제약정, 풋거래량, 콜거래량]}"""
    out = {}
    for r in rows:
        prod = str(sget(r, "PROD_NM") or "").strip()
        if not prod:
            continue
        v, oi = num(sget(r, "ACC_TRDVOL")) or 0, num(sget(r, "ACC_OPNINT_QTY")) or 0
        a = out.setdefault(prod, [0, 0, 0, 0])
        a[0] += v
        a[1] += oi
        if options:
            a[2 if is_put(r) else 3] += v if (is_put(r) or is_call(r)) else 0
    return out


def curve_from(rows):
    """국채전문유통시장 → {만기 이름: 수익률%} (같은 만기가 여러 개면 '지표' 종목, 없으면 평균)"""
    groups = {}
    for r in rows:
        tenor = str(sget(r, "BND_EXP_TP_NM") or "").strip()
        y = num(sget(r, "CLSPRC_YD"))
        if not tenor or not y or y <= 0:
            continue
        groups.setdefault(tenor, []).append((("지표" in str(sget(r, "GOVBND_ISU_TP_NM") or "")), y))
    out = {}
    for tenor, lst in groups.items():
        pick = [y for ind, y in lst if ind] or [y for _, y in lst]
        out[tenor] = sum(pick) / len(pick)
    return out


def tenor_key(t):
    m = re.search(r"([\d.]+)", t)
    return float(m.group(1)) if m else 999.0


def gold_from(rows):
    """대표 종목: 거래량 큰 것 (이름, 종가, 거래량)"""
    best = None
    for r in rows:
        v, c = num(sget(r, "ACC_TRDVOL")) or 0, num(sget(r, "TDD_CLSPRC"))
        if c and (best is None or v > best[2]):
            best = (str(sget(r, "ISU_NM") or ""), c, v)
    return best


def store_day(conn, day, data, spot=None):
    """data: {'fut': rows, 'opt': rows, ...} → 저장한 줄 수"""
    d = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    n = 0
    fut = agg_futures(data.get("fut", []))
    for prod, (v, oi, c) in fut.items():
        conn.execute("INSERT OR REPLACE INTO kd_fut VALUES (?,?,?,?,?)", (d, prod, v, oi, c))
        n += 1
    opt_rows = data.get("opt", [])
    opt = agg_options(opt_rows)
    for prod, (pv, cv, po, co) in opt.items():
        conn.execute("INSERT OR REPLACE INTO kd_opt VALUES (?,?,?,?,?,?)", (d, prod, pv, cv, po, co))
        n += 1
    mp = main_prod({p: a[0] + a[1] for p, a in opt.items()})
    if mp:
        res = atm_iv([r for r in opt_rows if str(sget(r, "PROD_NM") or "").strip() == mp], spot, day[:6])
        if res:
            conn.execute("INSERT OR REPLACE INTO kd_iv VALUES (?,?,?)", (d, res[0], res[1]))
    for kind, keys, options in (("F", ("sfu", "kfu"), False), ("O", ("sop", "kop"), True)):
        agg = {}
        for k in keys:
            for prod, a in agg_stock(data.get(k, []), options).items():
                b = agg.setdefault(prod, [0, 0, 0, 0])
                for i in range(4):
                    b[i] += a[i]
        for prod, (v, oi, pv, cv) in agg.items():
            conn.execute("INSERT OR REPLACE INTO kd_stock VALUES (?,?,?,?,?,?,?)", (d, kind, prod, v, oi, pv, cv))
            n += 1
    for tenor, y in curve_from(data.get("kts", [])).items():
        conn.execute("INSERT OR REPLACE INTO kd_curve VALUES (?,?,?)", (d, tenor, y))
        n += 1
    g = gold_from(data.get("gold", []))
    if g:
        conn.execute("INSERT OR REPLACE INTO kd_gold VALUES (?,?,?,?)", (d, g[0], g[1], g[2]))
        n += 1
    return n


def sync(db_path, key, log=print, want_days=20, today=None, session=None, sleep=0.2):
    """최근 거래일부터 want_days일을 받아 저장 (이미 있는 날은 건너뜀). 키·승인 문제는 KrxError"""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        have = {r[0] for r in conn.execute("SELECT d FROM kd_checked WHERE rows>0")}
        zero = {r[0] for r in conn.execute("SELECT d FROM kd_checked WHERE rows=0")}
        spot = {r[0]: r[1] for r in conn.execute("SELECT d, value FROM dv_series WHERE sid='kpi200'")} if _has(conn, "dv_series") else {}
        got = saved = calls = 0
        fails = {}
        for day in krx_api.recent_days(today, n=want_days + 6):
            if got >= want_days:
                break
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            if iso in have:
                got += 1
                continue
            if iso in zero:
                continue
            data = {}
            for name, svc in SVC.items():
                calls += 1
                try:
                    data[name] = call(svc, day, key, session=session)
                except KrxError as e:
                    if name == "fut" and not fails:
                        raise                        # 키·승인 문제는 바로 알림
                    fails[name] = str(e)[:60]
                    data[name] = []
                time.sleep(sleep)
            n = store_day(conn, day, data, spot.get(iso))
            more_recent = any(d > iso for d in have)
            if n or more_recent:
                conn.execute("INSERT OR REPLACE INTO kd_checked VALUES (?,?)", (iso, n))
            conn.commit()
            if n:
                got += 1
                saved += n
        cut = ((today or dt.date.today()) - dt.timedelta(days=KEEP_DAYS)).isoformat()
        for t in ("kd_opt", "kd_fut", "kd_iv", "kd_curve", "kd_gold", "kd_stock"):
            conn.execute(f"DELETE FROM {t} WHERE d < ?", (cut,))
        conn.commit()
        log(f"KRX 파생·채권: 호출 {calls}회 · 저장 {saved}줄 · {got}거래일" + (f" · 일부 서비스 실패 {fails}" if fails else ""))
        return {"호출": calls, "저장": saved, "거래일": got, "실패": fails}
    finally:
        conn.close()


def _has(conn, t):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())


# ---------------- 읽기 (화면용) ----------------
def _q(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def put_call(conn):
    """대표 코스피200 옵션의 날짜별 풋/콜 거래량 비율 [(날짜, 비율, 풋, 콜)]"""
    rows = _q(conn, "SELECT d, prod, put_vol, call_vol FROM kd_opt ORDER BY d")
    by_day = {}
    for d, prod, pv, cv in rows:
        by_day.setdefault(d, {})[prod] = (pv, cv)
    if not by_day:
        return []
    totals = {}
    for d, prods in by_day.items():
        for p, (pv, cv) in prods.items():
            totals[p] = totals.get(p, 0) + pv + cv
    mp = main_prod(totals)
    out = []
    for d in sorted(by_day):
        if mp in by_day[d]:
            pv, cv = by_day[d][mp]
            if cv > 0:
                out.append((d, pv / cv, pv, cv))
    return out


def futures_oi(conn):
    rows = _q(conn, "SELECT d, prod, vol, oi FROM kd_fut ORDER BY d")
    totals = {}
    for d, prod, v, oi in rows:
        totals[prod] = totals.get(prod, 0) + v
    mp = main_prod(totals)
    return [(d, oi, v) for d, prod, v, oi in rows if prod == mp], mp


def iv_series(conn):
    return [(d, iv) for d, iv, n in _q(conn, "SELECT d, iv, n FROM kd_iv ORDER BY d")]


def curve_latest(conn):
    """→ (날짜, [(만기 이름, 수익률, 3개월 전 수익률 또는 None)]) 만기 짧은 순"""
    r = _q(conn, "SELECT MAX(d) FROM kd_curve")
    if not r or not r[0][0]:
        return None, []
    last = r[0][0]
    ago = (dt.date.fromisoformat(last) - dt.timedelta(days=91)).isoformat()
    prev = _q(conn, "SELECT MAX(d) FROM kd_curve WHERE d <= ?", (ago,))
    pd_ = prev[0][0] if prev and prev[0][0] else None
    now = dict(_q(conn, "SELECT tenor, yld FROM kd_curve WHERE d=?", (last,)))
    old = dict(_q(conn, "SELECT tenor, yld FROM kd_curve WHERE d=?", (pd_,))) if pd_ else {}
    return last, [(t, now[t], old.get(t)) for t in sorted(now, key=tenor_key)]


def gold_series(conn):
    return [(d, c) for d, n, c, v in _q(conn, "SELECT d, name, close, vol FROM kd_gold ORDER BY d")]


def stock_deriv(conn, name):
    """종목 이름이 상품 이름에 들어 있는 주식선물·옵션의 최근 거래일 {'F': (거래량, 미결제), 'O': (거래량, 미결제, 풋/콜 비율)} (없으면 {})"""
    name = (name or "").strip()
    if len(name) < 2:
        return {}
    r = _q(conn, "SELECT MAX(d) FROM kd_stock")
    if not r or not r[0][0]:
        return {}
    out = {}
    for kind, prod, v, oi, pv, cv in _q(conn, "SELECT kind, prod, vol, oi, put_vol, call_vol FROM kd_stock WHERE d=?", (r[0][0],)):
        if name in prod:
            a = out.setdefault(kind, [0, 0, 0, 0])
            a[0] += v
            a[1] += oi
            a[2] += pv
            a[3] += cv
    res = {}
    for kind, (v, oi, pv, cv) in out.items():
        res[kind] = (v, oi) if kind == "F" else (v, oi, (pv / cv) if cv else None)
    res["date"] = r[0][0]
    return res if len(res) > 1 else {}


def stock_top(conn, n=10):
    """최근 거래일 주식선물·옵션 거래량 상위 [(종류, 상품, 거래량, 미결제)]"""
    r = _q(conn, "SELECT MAX(d) FROM kd_stock")
    if not r or not r[0][0]:
        return None, []
    rows = _q(conn, "SELECT kind, prod, vol, oi FROM kd_stock WHERE d=? ORDER BY vol DESC LIMIT ?", (r[0][0], n))
    return r[0][0], rows


# ---------------- 신호판 항목 (deriv_bond.signals 와 같은 모양) ----------------
def _avg(vals):
    return sum(vals) / len(vals) if vals else None


def signals(conn):
    out = []
    pc = put_call(conn)
    if len(pc) >= 5:
        r5 = _avg([x[1] for x in pc[-5:]])
        sig = -1 if r5 > 1.2 else 1 if r5 < 0.8 else 0
        out.append(dict(id="put_call", label="옵션 풋/콜 거래량 비율 (코스피200)", value=f"{pc[-1][1]:.2f}", change=f"5일 평균 {r5:.2f}",
                        signal=sig, points=[x[1] for x in pc[-60:]], date=pc[-1][0], note="1보다 크면 풋(하락에 거는 권리)이 더 많이 거래됐다는 뜻이에요.",
                        reason=("풋 거래가 많아요 — 하락에 대비하는 심리가 커요. 다만 너무 쏠리면 오히려 바닥 신호로 보는 시각도 있어요 (가설)" if sig < 0 else
                                "콜 거래가 많아요 — 상승 기대가 커요. 너무 쏠리면 과열 신호일 수도 있어요 (가설)" if sig > 0 else "풋·콜이 비슷해요")))
    ivs = iv_series(conn)
    if len(ivs) >= 5:
        last = ivs[-1][1]
        base = _avg([v for _, v in ivs[-60:]])
        sig = -1 if last > base * 1.15 else 1 if last < base * 0.9 else 0
        out.append(dict(id="iv", label="내재변동성 (ATM 옵션 평균, 변동성지수 대용)", value=f"{last:.1f}%", change=f"최근 평균 {base:.1f}%",
                        signal=sig, points=[v for _, v in ivs[-60:]], date=ivs[-1][0],
                        note="옵션 가격에 담긴 시장의 예상 변동 폭이에요. 거래소의 VKOSPI와 계산법이 달라서 숫자는 같지 않고, 높을수록 불안하다는 방향만 같아요.",
                        reason=("평소보다 불안이 커졌어요 (가설)" if sig < 0 else "평소보다 차분해요 (가설)" if sig > 0 else "평소 수준이에요")))
    oi, mp = futures_oi(conn)
    if len(oi) >= 5:
        last, prev = oi[-1][1], oi[-6][1] if len(oi) >= 6 else oi[0][1]
        out.append(dict(id="oi", label=f"선물 미결제약정 ({mp})", value=f"{last:,.0f}", change=f"5일 전 대비 {last - prev:+,.0f}",
                        signal=None, points=[x[1] for x in oi[-60:]], date=oi[-1][0],
                        note="아직 청산 안 된 선물 계약 수예요. 늘면서 가격이 오르면 새 돈이 들어온 상승, 줄면서 오르면 숏커버(되사기)일 수 있어요 (가설).",
                        reason=""))
    gs = gold_series(conn)
    if len(gs) >= 5:
        last = gs[-1][1]
        out.append(dict(id="gold", label="KRX 금시장 종가 (거래량 많은 종목)", value=f"{last:,.0f}", change=f"{len(gs)}거래일 전 대비 {(last / gs[0][1] - 1):+.1%}",
                        signal=None, points=[v for _, v in gs[-60:]], date=gs[-1][0], note="안전자산 선호가 커지면 오르는 경향이 있어요 (참고용).", reason=""))
    return out
