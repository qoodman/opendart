"""한국 시장 전체 외국인 지분: 종목마다 외국인 보유율(네이버)을 시가총액으로 가중해 코스피·코스닥 전체 외국인 지분율을 계산한다.
보통주만 보고(우선주·ETF·스팩 제외), 받은 날의 스냅샷을 쌓아 추이를 본다. 공식 통계(금융감독원·거래소)와는 조금 다를 수 있다."""
import datetime as dt
import json
import sqlite3
import time

import requests

import float_stock
import stock_value

HEADERS = stock_value.HEADERS


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS foreign_now (code TEXT PRIMARY KEY, name TEXT, market TEXT, mcap REAL, ratio REAL, bizdate TEXT, fetched TEXT);
    CREATE TABLE IF NOT EXISTS foreign_hist (date TEXT, market TEXT, ratio REAL, fvalue REAL, mcap REAL, n INTEGER, coverage REAL, PRIMARY KEY (date, market));
    """)


def fetch_ratio(code, session=None):
    """종목의 가장 최근 외국인 보유율 (날짜, %) 또는 None"""
    s = session or requests
    r = s.get(stock_value.TREND_URL.format(code=code), params=dict(pageSize=1), headers=HEADERS, timeout=15)
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    rows = stock_value.parse_trend(r.json())
    for x in rows:
        if x.get("ratio") is not None:
            return x["date"], x["ratio"]
    return None


def aggregate(rows):
    """[(market, mcap, ratio)] → {market: (비율%, 외국인 보유 시총(억), 시총(억), 종목 수)} 와 'ALL'"""
    out = {}
    for key in ("KOSPI", "KOSDAQ", "ALL"):
        sel = [(m, mc, r) for m, mc, r in rows if mc and r is not None and (key == "ALL" or m == key)]
        tot = sum(mc for _, mc, _ in sel)
        fv = sum(mc * r / 100.0 for _, mc, r in sel)
        out[key] = (fv / tot * 100 if tot else None, fv, tot, len(sel))
    return out


def run(conn, log=print, progress=None, session=None, today=None, sleep=0.25):
    ensure(conn)
    today = today or dt.date.today()
    listing = float_stock.fetch_listing(session)
    if len(listing) < 100:
        raise RuntimeError("종목 목록을 받지 못했어요")
    total_mc = {"KOSPI": 0.0, "KOSDAQ": 0.0}
    for code, name, mkt, mc, tv in listing:
        total_mc[mkt] = total_mc.get(mkt, 0.0) + mc
    done_today = {c for (c,) in conn.execute("SELECT code FROM foreign_now WHERE fetched=?", (today.isoformat(),))}
    ok = fail = 0
    for i, (code, name, mkt, mc, tv) in enumerate(listing):
        if progress and i % 20 == 0:
            progress(f"외국인 보유율 {i}/{len(listing)}")
        if code in done_today:
            conn.execute("UPDATE foreign_now SET mcap=?, market=?, name=? WHERE code=?", (mc, mkt, name, code))
            continue
        try:
            res = fetch_ratio(code, session)
        except Exception as e:
            fail += 1
            if fail >= 40 and ok == 0:
                raise RuntimeError(f"외국인 보유율을 받지 못해요 ({type(e).__name__})")
            continue
        if res:
            conn.execute("INSERT OR REPLACE INTO foreign_now VALUES (?,?,?,?,?,?,?)", (code, name, mkt, mc, res[1], res[0], today.isoformat()))
            ok += 1
        if (ok + fail) % 50 == 0:
            conn.commit()
        time.sleep(sleep)
    conn.commit()
    rows = conn.execute("SELECT market, mcap, ratio FROM foreign_now").fetchall()
    agg = aggregate(rows)
    for key, (ratio, fv, tot, n) in agg.items():
        cov = (tot / (total_mc["KOSPI"] + total_mc["KOSDAQ"] if key == "ALL" else total_mc.get(key) or 1)) if tot else 0
        conn.execute("INSERT OR REPLACE INTO foreign_hist VALUES (?,?,?,?,?,?,?)", (today.isoformat(), key, ratio, fv, tot, n, cov))
    conn.commit()
    log(f"[외국인 지분] 새로 받음 {ok}개 · 실패 {fail}개 · 전체 {agg['ALL'][0]:.2f}%" if agg["ALL"][0] is not None else "[외국인 지분] 계산 못함")
    return ok


def run_job(db_path, status_path, log=print):
    import os
    st = dict(running=True, pid=os.getpid(), started=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), finished="",
              steps=[dict(name="종목 목록(네이버) → 종목별 외국인 보유율 → 시총 가중 합계", state="run", msg="시작")])

    def save():
        try:
            tmp = status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, status_path)
        except OSError:
            pass
    save()
    last = {"t": 0.0}

    def prog(msg):
        if time.time() - last["t"] > 3:
            last["t"] = time.time()
            st["steps"][0]["msg"] = msg
            save()
    conn = sqlite3.connect(db_path)
    try:
        try:
            n = run(conn, log=log, progress=prog)
            st["steps"][0].update(state="ok", msg=f"새로 받음 {n}개")
        except Exception as e:
            st["steps"][0].update(state="fail", msg=f"{type(e).__name__}: {str(e)[:160]}")
            log(f"외국인 지분 실패: {e}")
    finally:
        conn.close()
    st["running"] = False
    st["finished"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save()


def load(conn):
    """→ dict(hist{market:[(date,ratio,fvalue)]}, top[...]) 또는 None"""
    try:
        h = conn.execute("SELECT date, market, ratio, fvalue, mcap, n, coverage FROM foreign_hist ORDER BY date").fetchall()
        top = conn.execute("SELECT code, name, market, mcap, ratio FROM foreign_now WHERE ratio IS NOT NULL ORDER BY mcap*ratio DESC LIMIT 10").fetchall()
        rise = []
    except sqlite3.Error:
        return None
    if not h:
        return None
    hist = {}
    for d, m, r, fv, mc, n, cov in h:
        hist.setdefault(m, []).append(dict(date=d, ratio=r, fvalue=fv, mcap=mc, n=n, cov=cov))
    return dict(hist=hist, top=[tuple(x) for x in top])
