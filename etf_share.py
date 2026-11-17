# -*- coding: utf-8 -*-
"""
코스피·코스닥 ETF 거래대금 비중 = ETF 거래대금 ÷ (주식 + ETF 거래대금)  (하루 단위)

왜 보나: ETF·인덱스펀드 같은 패시브 자금은 가격과 실적을 따지지 않고 시가총액 비율대로 사서, 비중이 커질수록 시총 큰 종목으로 돈이 쏠리고
  장이 더 크게 오르내리는 경향이 있다는 이야기가 있어요 (가설). 이 값은 그 흐름을 가늠해 보는 참고 지표일 뿐 좋다/나쁘다를 말하지 않아요.

자료:
  1) KRX Open API 'ETF 일별매매정보' (etp/etf_bydd_trd, 이용신청이 따로 필요) — 되면 최근 거래일을 거슬러 채움
  2) 안 되면 네이버 증권 ETF 목록(키 불필요)을 장 마감 뒤(16시 이후)에 받아 그날 값을 쌓음 (과거는 못 채움)
  주식 거래대금은 krx_api가 받아 둔 krx_daily(코스피+코스닥 주식)의 합이라 KRX 키가 있어야 해요. 이 합에는 ETF·ETN이 들어 있지 않아요.
"""
import datetime as dt
import sqlite3

import requests

import krx_api

KRX_ETF_SERVICE = "etp/etf_bydd_trd"
NAVER_ETF_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
CLOSE_HOUR = 16            # 이 시각 이후에만 네이버 값을 그날 확정값으로 씀


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS etf_share_daily (
        bas_dd TEXT PRIMARY KEY, etf_val REAL, stock_val REAL, src TEXT);
    """)


def stock_total(conn, day):
    try:
        v = conn.execute("SELECT SUM(trdval) FROM krx_daily WHERE bas_dd=?", (day,)).fetchone()[0]
    except sqlite3.OperationalError:
        return None
    return v or None


def naver_total(session=None):
    """네이버 ETF 목록의 거래대금 합 (원). 'amonut'는 백만원 단위"""
    s = session or requests
    r = s.get(NAVER_ETF_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    rows = (r.json().get("result") or {}).get("etfItemList") or []
    vals = [x.get("amonut") for x in rows if isinstance(x.get("amonut"), (int, float))]
    return (sum(vals) * 1e6, len(vals)) if vals else (None, 0)


def sync(db_path, key="", log=print, today=None, now=None, session=None):
    now = now or dt.datetime.now()
    today = today or now.date()
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        have = {r[0] for r in conn.execute("SELECT bas_dd FROM etf_share_daily")}   # (분모가 빈 날도 ETF 값은 있으니 다시 받지 않음)
        try:
            days = [r[0] for r in conn.execute("SELECT DISTINCT bas_dd FROM krx_daily ORDER BY bas_dd DESC")]
        except sqlite3.OperationalError:
            days = []
        saved = krx_fail = 0
        if key:
            for day in [d for d in days if d not in have]:
                st = stock_total(conn, day)
                if not st:
                    continue
                try:
                    rows = krx_api.call(KRX_ETF_SERVICE, day, key, session=session)
                except krx_api.KrxError as e:
                    krx_fail += 1
                    log(f"ETF 거래대금(KRX) {day}: {e}")
                    break                                  # 승인이 안 된 서비스면 날마다 부르지 않고 네이버로 넘어감
                val = sum(v for v in (krx_api.num(r.get("ACC_TRDVAL")) for r in rows) if v)
                if val:
                    conn.execute("INSERT OR REPLACE INTO etf_share_daily VALUES (?,?,?,?)", (day, val, st, "KRX"))
                    saved += 1
            conn.commit()
        # 네이버: 평일 장 마감 뒤, 오늘 값이 아직 없을 때
        t = today.strftime("%Y%m%d")
        if today.weekday() < 5 and now.hour >= CLOSE_HOUR and t not in have:
            try:                                           # 주식 거래대금(KRX)은 보통 다음 거래일에 올라오므로 분모는 비워 두고 나중에 채움
                val, n = naver_total(session)
                if val:
                    conn.execute("INSERT OR REPLACE INTO etf_share_daily VALUES (?,?,?,?)", (t, val, stock_total(conn, t), "NAVER"))
                    conn.commit()
                    saved += 1
            except Exception as e:
                log(f"ETF 거래대금(네이버): {e}")
        for d, in conn.execute("SELECT bas_dd FROM etf_share_daily WHERE stock_val IS NULL").fetchall():     # 비워 둔 분모 채우기
            st = stock_total(conn, d)
            if st:
                conn.execute("UPDATE etf_share_daily SET stock_val=? WHERE bas_dd=?", (st, d))
        conn.commit()
        log(f"ETF 거래대금 비중: 새로 저장 {saved}일" + (" (KRX ETF 서비스 실패 — 이용신청 확인)" if krx_fail else ""))
        return {"저장": saved, "KRX 실패": krx_fail}
    finally:
        conn.close()


def series(conn):
    """[(bas_dd, 비중%)] 오래된 것부터"""
    try:
        rows = conn.execute("SELECT bas_dd, etf_val, stock_val FROM etf_share_daily ORDER BY bas_dd").fetchall()
    except sqlite3.OperationalError:
        return []
    return [(d, 100.0 * e / (e + s)) for d, e, s in rows if e and s]


def check(key, session=None, today=None):
    """설정 → 연결 확인용: KRX ETF 일별매매정보(이용신청 여부)와 네이버 ETF 목록. [{op, ok, day, total, sample, note}]"""
    today = today or dt.date.today()
    items = []
    it = dict(op=f"KRX ETF 일별매매정보 ({KRX_ETF_SERVICE})", ok=False, day="", total=0, sample=None, note="")
    if not key:
        it["note"] = "KRX 키가 없어요. 설정에서 먼저 저장해 주세요."
    else:
        try:
            for day in krx_api.recent_days(today, n=5):
                rows = krx_api.call(KRX_ETF_SERVICE, day, key, session=session)
                if rows:
                    it.update(ok=True, day=day, total=len(rows), sample=rows[0])
                    break
            else:
                it.update(ok=True, note="호출은 됐는데 최근 5거래일 자료가 비어 있어요")
        except krx_api.KrxError as e:
            it["note"] = f"{e} — KRX 오픈API에서 'ETF 일별매매정보'를 이용신청하세요. 신청 전이어도 네이버로 오늘 값부터 쌓여요."
        except Exception as e:
            it["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    items.append(it)
    it = dict(op="네이버 증권 ETF 목록 (키 불필요)", ok=False, day="", total=0, sample=None, note="")
    try:
        val, n = naver_total(session)
        it.update(ok=bool(val), total=n, day="지금 시각 기준 당일 누적", sample={"ETF 수": n, "거래대금 합(원)": f"{val:,.0f}" if val else "-"})
        if not val:
            it["note"] = "목록은 받았는데 거래대금 필드(amonut)를 못 찾았어요"
    except Exception as e:
        it["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    items.append(it)
    return items
