# -*- coding: utf-8 -*-
"""
KRX Open API (openapi.krx.co.kr) — 인증키는 AUTH_KEY 헤더, 서비스마다 이용신청·승인이 필요.

확인된 명세 (사용자가 올린 API 이용현황 화면):
  유가증권 일별매매정보  https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd      입력 basDd(YYYYMMDD)
      OutBlock_1: BAS_DD ISU_CD ISU_NM MKT_NM SECT_TP_NM TDD_CLSPRC CMPPREVDD_PRC FLUC_RT TDD_OPNPRC TDD_HGPRC TDD_LWPRC
                  ACC_TRDVOL ACC_TRDVAL MKTCAP LIST_SHRS
  유가증권 종목기본정보  https://data-dbg.krx.co.kr/svc/apis/sto/stk_isu_base_info  입력 basDd
      OutBlock_1: ISU_CD(표준코드) ISU_SRT_CD(단축코드) ISU_NM ISU_ABBRV ISU_ENG_NM LIST_DD MKT_TP_NM SECUGRP_NM SECT_TP_NM
                  KIND_STKCERT_TP_NM PARVAL LIST_SHRS(상장주식수)
코스닥(ksq_…)·코넥스(knx_…) 주소는 위 이름 규칙으로 짐작한 것이라 '연결 확인'에서 실제로 되는지 봅니다.
데이터는 전 거래일까지 (실시간 아님), 하루 10,000건 호출 한도.
"""
import datetime as dt
import re
import sqlite3
import time

import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis/sto/"
SERVICES = {                     # 시장 → (일별매매정보, 종목기본정보)
    "코스피": ("stk_bydd_trd", "stk_isu_base_info"),
    "코스닥": ("ksq_bydd_trd", "ksq_isu_base_info"),     # 주소는 이름 규칙으로 짐작 (확인 필요)
    "코넥스": ("knx_bydd_trd", "knx_isu_base_info"),     # 같음
}
SYNC_MARKETS = ("코스피", "코스닥")       # 매일 받는 시장 (코넥스는 점검에서만)


class KrxError(Exception):
    pass


def num(v):
    """'1,234' '-' '' None → float 또는 None"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def short_code(v):
    """표준코드(KR7005930003)면 단축코드 6자리로, 이미 6자리면 그대로"""
    v = (v or "").strip()
    if len(v) == 12 and v.startswith("KR"):
        return v[3:9]
    return v


def call(service, bas_dd, key, session=None, timeout=30):
    """한 서비스를 한 날짜로 호출 → OutBlock_1 줄 목록 (휴장일이면 []). 키 오류·미승인·한도 초과는 KrxError."""
    s = session or requests
    url = BASE + service
    headers = {"AUTH_KEY": key, "Accept": "application/json"}
    r = s.get(url, params={"basDd": bas_dd}, headers=headers, timeout=timeout)
    if r.status_code == 405:                         # GET이 막힌 서비스면 POST로
        r = s.post(url, json={"basDd": bas_dd}, headers=headers, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        raise KrxError(f"응답을 읽지 못했어요 (HTTP {r.status_code})")
    if r.status_code != 200 or (isinstance(body, dict) and "OutBlock_1" not in body and (body.get("respCode") or body.get("respMsg"))):
        code = (body.get("respCode") if isinstance(body, dict) else "") or r.status_code
        msg = (body.get("respMsg") if isinstance(body, dict) else "") or ""
        hint = {"401": "키가 틀렸거나 이 서비스 이용신청이 승인되지 않았어요", "403": "이 서비스 이용 권한이 없어요 (이용신청·승인 확인)",
                "429": "하루 호출 한도를 넘었어요"}.get(str(code), "")
        raise KrxError(f"KRX 응답 {code} {msg} {('— ' + hint) if hint else ''}".strip())
    rows = body.get("OutBlock_1") if isinstance(body, dict) else None
    return rows if isinstance(rows, list) else []


def recent_days(today=None, n=14):
    """오늘 이전 평일 YYYYMMDD 최근 n개 (최신부터). 휴장일은 호출해 보고 빈 결과면 건너뜀."""
    d = (today or dt.date.today())
    out = []
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS krx_daily (
        code TEXT, bas_dd TEXT, market TEXT, name TEXT, close REAL, mktcap REAL, list_shrs REAL, trdvol REAL, trdval REAL,
        PRIMARY KEY (code, bas_dd));
    CREATE TABLE IF NOT EXISTS krx_checked (bas_dd TEXT PRIMARY KEY, rows INTEGER);   -- 호출해 본 날짜 (휴장일은 rows=0)
    """)


def sync(db_path, key, log=print, want_days=10, today=None, session=None, sleep=0.2):
    """코스피·코스닥 일별매매정보를 최근 거래일부터 want_days개 받아 저장. 이미 있는 날짜는 건너뜀."""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        have = {r[0] for r in conn.execute("SELECT DISTINCT bas_dd FROM krx_daily")}
        holidays = {r[0] for r in conn.execute("SELECT bas_dd FROM krx_checked WHERE rows=0")}
        got_days, calls, saved, found = 0, 0, 0, set()
        for day in recent_days(today, n=want_days + 6):
            if got_days >= want_days:
                break
            if day in have:
                got_days += 1
                continue
            if day in holidays:                    # 이미 호출해 보니 데이터가 없던 날 (휴장일)
                continue
            day_rows = 0
            for market in SYNC_MARKETS:
                svc = SERVICES[market][0]
                calls += 1
                try:
                    rows = call(svc, day, key, session=session)
                except KrxError as e:
                    if market == SYNC_MARKETS[0]:
                        raise                       # 키·승인 문제는 바로 알림 (코스닥 실패는 아래에서 따로)
                    log(f"KRX {market} {day}: {e}")
                    rows = []
                for r in rows:
                    code = short_code(r.get("ISU_CD") or r.get("ISU_SRT_CD"))
                    if not code:
                        continue
                    conn.execute("INSERT OR REPLACE INTO krx_daily VALUES (?,?,?,?,?,?,?,?,?)",
                                 (code, day, market, r.get("ISU_NM"), num(r.get("TDD_CLSPRC")), num(r.get("MKTCAP")),
                                  num(r.get("LIST_SHRS")), num(r.get("ACC_TRDVOL")), num(r.get("ACC_TRDVAL"))))
                    day_rows += 1
                time.sleep(sleep)
            if day_rows:
                found.add(day)
            # 더 늦은 거래일에 자료가 있을 때만 '휴장'으로 기록 (가장 최근 날짜는 자료가 아직 안 올라온 것일 수 있어서 다음에 다시 시도)
            if day_rows or any(d > day for d in have | found):
                conn.execute("INSERT OR REPLACE INTO krx_checked VALUES (?,?)", (day, day_rows))
            conn.commit()
            if day_rows:                           # 휴장일은 빈 결과라 세지 않음
                got_days += 1
                saved += day_rows
        conn.execute("DELETE FROM krx_daily WHERE bas_dd < ?",
                     ((dt.datetime.strptime(max(have | {recent_days(today, 1)[0]}), "%Y%m%d") - dt.timedelta(days=60)).strftime("%Y%m%d"),))
        conn.commit()
        log(f"KRX 시세: 호출 {calls}회 · 새로 저장 {saved}줄 · 보관 {got_days}거래일")
        return {"호출": calls, "저장": saved, "거래일": got_days}
    finally:
        conn.close()


def latest(conn):
    """{종목코드: {bas_dd, market, name, close, mktcap, list_shrs, trdvol, trdval}} 가장 최근 거래일 기준"""
    try:
        rows = conn.execute(
            "SELECT code, bas_dd, market, name, close, mktcap, list_shrs, trdvol, trdval FROM krx_daily "
            "WHERE bas_dd = (SELECT MAX(bas_dd) FROM krx_daily)").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: dict(bas_dd=r[1], market=r[2], name=r[3], close=r[4], mktcap=r[5], list_shrs=r[6], trdvol=r[7], trdval=r[8])
            for r in rows}


def avg_trdval(conn, code, n=10):
    """최근 n거래일 평균 거래대금 (원). 없으면 None"""
    try:
        v = [r[0] for r in conn.execute("SELECT trdval FROM krx_daily WHERE code=? AND trdval IS NOT NULL "
                                        "ORDER BY bas_dd DESC LIMIT ?", (code, n))]
    except sqlite3.OperationalError:
        return None
    return sum(v) / len(v) if v else None


def check(key, session=None, today=None):
    """연결 확인: 서비스마다 가장 최근 거래일을 찾아 호출. [{service, market, ok, day, rows, sample, note}]"""
    out = []
    for market, (trd, base) in SERVICES.items():
        for label, svc in (("일별매매정보", trd), ("종목기본정보", base)):
            item = dict(service=svc, market=market, label=label, ok=False, day="", rows=0, sample=None, note="")
            try:
                for day in recent_days(today, n=6):
                    rows = call(svc, day, key, session=session)
                    if rows:
                        item.update(ok=True, day=day, rows=len(rows), sample=rows[0])
                        break
                else:
                    item["note"] = "최근 6일 평일에 데이터가 없어요 (주소가 맞는지 확인 필요)"
            except KrxError as e:
                item["note"] = str(e)
            except requests.RequestException as e:
                item["note"] = f"연결 실패: {type(e).__name__}"
            out.append(item)
    return out
