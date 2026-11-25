"""품절주 찾기: 시장에서 실제로 거래될 수 있는 주식(유통주식)이 적은 종목.
유통 비율 = 100% − 최대주주 측 지분 − 자기주식 (단순 유통), 거기서 5% 이상 보유자까지 빼면 '실질' 유통.
네이버 증권 시총·거래대금 목록 + DART(최대주주 현황·주식 총수·5% 대량보유)로 계산한다. 투자 권유가 아니다."""
import datetime as dt
import json
import sqlite3
import time

import requests

import dispute

HEADERS = {"User-Agent": "Mozilla/5.0"}
LIST_URL = "https://m.stock.naver.com/api/stocks/marketValue/{mkt}"
DART = "https://opendart.fss.or.kr/api"
REFRESH_DAYS = 30      # 사업보고서 기준이라 한 달에 한 번이면 충분


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS float_stock (code TEXT PRIMARY KEY, name TEXT, market TEXT, mcap REAL, tv REAL, holder_pct REAL, treasury_pct REAL,
        big5_pct REAL, float_pct REAL, float_real REAL, issued REAL, fetched TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS float_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def fetch_listing(session=None, pages=40):
    """코스피·코스닥 일반 종목 (코드, 이름, 시장, 시총(억), 거래대금(백만)) — 시총 순"""
    s = session or requests
    out = []
    for mkt in ("KOSPI", "KOSDAQ"):
        for pg in range(1, pages + 1):
            j = None
            for _ in range(3):
                try:
                    j = s.get(LIST_URL.format(mkt=mkt), params=dict(page=pg, pageSize=100), headers=HEADERS, timeout=30).json()
                    break
                except (requests.RequestException, ValueError):
                    time.sleep(2)
            st = (j or {}).get("stocks") or []
            if not st:
                break
            for x in st:
                if x.get("stockEndType") != "stock":
                    continue
                code = x.get("itemCode") or ""
                name = x.get("stockName") or ""
                if not code.isdigit() or len(code) != 6 or code[-1] != "0":      # 우선주(끝자리 0 아님)는 뺌
                    continue
                try:
                    mc = float(str(x["marketValue"]).replace(",", ""))
                    tv = float(str(x.get("accumulatedTradingValue") or "0").replace(",", "") or 0)
                except (KeyError, ValueError):
                    continue
                out.append((code, name, mkt, mc, tv))
            time.sleep(0.25)
    return out


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def fetch_treasury(corp, key, today, session=None):
    """발행주식 총수와 자기주식 수 → (발행주식, 자기주식 %) (못 받으면 (None, None))"""
    s = session or requests
    year = today.year - 1 if today.month >= 4 else today.year - 2
    r = s.get(f"{DART}/stockTotqySttus.json", params=dict(crtfc_key=key, corp_code=corp, bsns_year=str(year), reprt_code="11011"), timeout=30)
    j = r.json() if r.status_code == 200 else {}
    if str(j.get("status")) == "020":
        raise dispute.DisputeError("DART 하루 호출 한도를 넘었어요")
    if str(j.get("status")) != "000":
        return None, None
    best = None
    for row in j.get("list") or []:
        if (row.get("se") or "").strip() in ("합계", "보통주"):
            iss, tr = _num(row.get("istc_totqy")), _num(row.get("tesstk_co")) or 0.0
            if iss:
                best = (iss, tr / iss * 100)
                if (row.get("se") or "").strip() == "보통주":
                    break
    return best or (None, None)


def compute(holder_pct, treasury_pct, ext):
    """→ (단순 유통 %, 실질 유통 %, 5% 보유자 합 %) 음수는 0으로"""
    if holder_pct is None:
        return None, None, None
    tr = treasury_pct or 0.0
    simple = max(0.0, 100.0 - holder_pct - tr)
    big = sum(h["pct"] for h in ext if h.get("pct"))
    return simple, max(0.0, simple - big), big


def run(conn, key, log=print, limit=2500, progress=None, session=None, today=None):
    import os
    from score_auto import corp_code_for
    ensure(conn)
    today = today or dt.date.today()
    base = os.environ.get("DART_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    cache = os.path.join(os.path.expanduser(base), "dart_cache")
    listing = fetch_listing(session)
    if len(listing) < 100:
        raise RuntimeError("종목 목록을 받지 못했어요")
    log(f"[품절주] 목록 {len(listing)}종목")
    fresh = {c for c, f in conn.execute("SELECT code, fetched FROM float_stock") if f and f >= (today - dt.timedelta(days=REFRESH_DAYS)).isoformat()}
    # 시총·거래대금은 매번 갱신, 지분은 한 달에 한 번
    for code, name, mkt, mc, tv in listing:
        conn.execute("INSERT INTO float_stock (code, name, market, mcap, tv) VALUES (?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, mcap=excluded.mcap, tv=excluded.tv",
                     (code, name, mkt, mc, tv))
    conn.commit()
    todo = [x for x in listing if x[0] not in fresh][:limit]
    done = failed = 0
    for i, (code, name, mkt, mc, tv) in enumerate(todo):
        if progress and i % 10 == 0:
            progress(f"지분·자기주식 {i}/{len(todo)}")
        try:
            corp = corp_code_for(code, cache, key, session=session)
            if not corp:
                continue
            hp, ext, insiders = dispute.fetch_structure(code, corp, key, today, session)
            if hp is None:
                continue
            iss, trp = fetch_treasury(corp, key, today, session)
            simple, real, big = compute(hp, trp, ext)
            detail = dict(ext=[dict(name=h["name"], pct=h["pct"], tp=h.get("tp")) for h in ext][:6])
            conn.execute("UPDATE float_stock SET holder_pct=?, treasury_pct=?, big5_pct=?, float_pct=?, float_real=?, issued=?, fetched=?, detail=? WHERE code=?",
                         (hp, trp, big, simple, real, iss, today.isoformat(), json.dumps(detail, ensure_ascii=False), code))
            done += 1
            if done % 25 == 0:
                conn.commit()
        except dispute.DisputeError as e:
            log(f"  ✖ {e}")
            break
        except Exception as e:
            failed += 1
            log(f"  ✖ {code}: {type(e).__name__}")
        time.sleep(0.12)
    conn.execute("INSERT OR REPLACE INTO float_meta VALUES ('synced', ?)", (today.isoformat(),))
    conn.commit()
    log(f"[품절주] 지분 새로 계산 {done}개 · 실패 {failed}개 · 남은 {max(0, len([x for x in listing if x[0] not in fresh]) - len(todo) + (len(todo) - done - failed))}개")
    return done


def run_job(db_path, key, status_path, log=print):
    """별도 프로세스용. 진행 상황을 status_path(JSON)에 적음"""
    import os
    st = dict(running=True, pid=os.getpid(), started=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), finished="",
              steps=[dict(name="종목 목록·시가총액·거래대금 (네이버)", state="run", msg="시작"), dict(name="최대주주 지분·자기주식·5% 보유자 (DART)", state="wait", msg="")])

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
        st["steps"][0].update(state="ok", msg="완료")
        st["steps"][1]["state"] = "run"
        if time.time() - last["t"] > 3:
            last["t"] = time.time()
            st["steps"][1]["msg"] = msg
            save()
    conn = sqlite3.connect(db_path)
    try:
        try:
            if not key:
                raise dispute.DisputeError("DART_API_KEY가 없어요")
            n = run(conn, key, log=log, progress=prog)
            st["steps"][0].update(state="ok", msg="완료")
            st["steps"][1].update(state="ok", msg=f"새로 계산 {n}개")
        except Exception as e:
            idx = 0 if st["steps"][0]["state"] == "run" else 1
            st["steps"][idx].update(state="fail", msg=f"{type(e).__name__}: {str(e)[:160]}")
            log(f"품절주 분석 실패: {e}")
    finally:
        conn.close()
    st["running"] = False
    st["finished"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save()


def load(conn):
    ensure(conn)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM float_stock WHERE float_pct IS NOT NULL")]
    for r in rows:
        r["turnover"] = (r["tv"] / r["mcap"]) if r.get("mcap") and r.get("tv") is not None else None      # 오늘 거래대금 ÷ 시총 (%)
        try:
            r["detail"] = json.loads(r.get("detail") or "{}")
        except ValueError:
            r["detail"] = {}
    return rows
