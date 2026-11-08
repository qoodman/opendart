# -*- coding: utf-8 -*-
"""
점수표(score_card.py)의 직접 고르는 항목 중 데이터로 추정할 수 있는 것을 자동으로 채움.
어느 항목이든 '자동 추정'이고, 사용자가 직접 고른 값이 있으면 그쪽이 우선합니다. 근거 숫자를 같이 저장해 화면에 보여 줍니다.

네이버 증권 재무 (키 불필요)
  sustain  이익의 지속 가능성   최근 3개 확정 연도의 영업이익·당기순이익이 모두 흑자면 '대체로 지속 가능', 하나라도 적자면 '불안정'
  qdiv     분기 배당 여부        최근 확정 4분기 중 3분기 이상 주당배당금이 있으면 '예'

DART OpenAPI (DART_API_KEY 필요, 키가 없거나 못 받으면 건너뜀)
  divyears 배당 연속 인상 연수   사업보고서 '배당에 관한 사항'(alotMatter) 주당 현금배당금을 최대 12년치 → 인상 연속(동결은 이어지되 연수에는 안 셈)
  treasury 자사주 보유 비율      '주식의 총수 현황'(stockTotqySttus) 자기주식수 ÷ 발행주식총수 (보통주)
  burn     연간 소각 비율        직전 사업연도 이익소각 수량(누적값의 연간 차이) ÷ 직전 연도 말 발행주식총수
  buyback  정기적 자사주 매입·소각  최근 3개 사업연도 모두에서 이익소각이 있었거나 자기주식수가 늘었으면 '예'
정성 항목(성장 잠재력·경영·브랜드)과 중복 상장은 자동으로 정할 수 없어서 직접 고릅니다.
"""
import datetime as dt
import io
import json
import os
import re
import sqlite3
import time
import zipfile

import requests

NAVER_FIN = "https://m.stock.naver.com/api/stock/{code}/finance/{kind}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
           "Referer": "https://m.stock.naver.com/"}
DART_BASE = "https://opendart.fss.or.kr/api"
DART_RETRY_DAYS = 30          # DART 값은 연 1회 바뀌므로 한 달에 한 번만 받음
FAIL_RETRY_DAYS = 1           # 실패하면 하루 뒤 다시


class AutoError(Exception):
    pass


# ---------------- 저장 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS scorecard_auto (code TEXT, item TEXT, value TEXT, note TEXT, source TEXT, updated TEXT,
        PRIMARY KEY (code, item));
    CREATE TABLE IF NOT EXISTS scorecard_auto_log (code TEXT, source TEXT, tried TEXT, ok INTEGER, error TEXT,
        PRIMARY KEY (code, source));
    """)


def load_auto(conn, code):
    """{항목: (값, 근거, 출처)}"""
    try:
        return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT item, value, note, source FROM scorecard_auto WHERE code=?", (code,))}
    except sqlite3.OperationalError:
        return {}


def load_auto_all(conn):
    out = {}
    try:
        for code, item, value, note, source in conn.execute("SELECT code, item, value, note, source FROM scorecard_auto"):
            out.setdefault(code, {})[item] = (value, note, source)
    except sqlite3.OperationalError:
        pass
    return out


def _num(v):
    """'1,444' '-' None '12.5' '-3' → float 또는 None"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None


# ---------------- 네이버 증권 재무 ----------------
def fetch_finance(code, kind, session=None, timeout=12):
    """kind: annual | quarter → {"periods": [(키, 컨센서스여부)], "rows": {항목: {키: 값|None}}}"""
    s = session or requests
    r = s.get(NAVER_FIN.format(code=code, kind=kind), headers=HEADERS, timeout=timeout)
    if r.status_code != 200:
        raise AutoError(f"네이버 재무 HTTP {r.status_code}")
    try:
        info = r.json()["financeInfo"]
        periods = [(t["key"], t.get("isConsensus") == "Y") for t in info["trTitleList"]]
        rows = {row["title"]: {k: _num((v or {}).get("value")) for k, v in (row.get("columns") or {}).items()}
                for row in info["rowList"]}
    except (ValueError, KeyError, TypeError):
        raise AutoError("네이버 재무 응답을 읽지 못함")
    return {"periods": periods, "rows": rows}


def _done(fin, n):
    """확정(컨센서스 아님) 기간 키 중 가장 최근 n개, 오래된 것부터"""
    keys = [k for k, cons in fin["periods"] if not cons]
    return keys[-n:]


def estimate_naver(annual, quarter):
    """→ {항목: (값, 근거)}. 데이터가 모자라면 그 항목은 빼서 직접 고르게 둠."""
    out = {}
    yrs = _done(annual, 3)
    op, ni = annual["rows"].get("영업이익", {}), annual["rows"].get("당기순이익", {})
    if len(yrs) == 3 and all(op.get(y) is not None and ni.get(y) is not None for y in yrs):
        bad = [f"{y[:4]}년" for y in yrs if op[y] <= 0 or ni[y] <= 0]
        label = f"{yrs[0][:4]}~{yrs[-1][:4]}년"
        if bad:
            out["sustain"] = ("bad", f"{label} 중 적자가 있어요 ({', '.join(bad)} 영업이익 또는 순이익 ≤ 0)")
        else:
            out["sustain"] = ("ok", f"{label} 3개 연도 모두 영업이익·순이익 흑자")
    qs = _done(quarter, 4)
    dps = quarter["rows"].get("주당배당금", {})
    if len(qs) == 4:
        paid = [q for q in qs if (dps.get(q) or 0) > 0]
        txt = f"최근 4분기({qs[0][:4]}.{qs[0][4:]}~{qs[-1][:4]}.{qs[-1][4:]}) 중 {len(paid)}분기 배당"
        out["qdiv"] = ("yes" if len(paid) >= 3 else "no", txt)
    return out


# ---------------- DART ----------------
def dart_json(path, key, session=None, timeout=20, **params):
    """DART JSON 호출 → (status, 본문). 한도 초과(020)는 AutoError."""
    s = session or requests
    r = s.get(f"{DART_BASE}/{path}", params={"crtfc_key": key, **params}, timeout=timeout)
    if r.status_code != 200:
        raise AutoError(f"DART HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        raise AutoError("DART 응답을 읽지 못함")
    st = str(body.get("status", ""))
    if st == "020":
        raise AutoError("DART 하루 호출 한도를 넘었어요")
    if st not in ("000", "013"):          # 013: 데이터 없음
        raise AutoError(f"DART {st} {body.get('message', '')}".strip())
    return st, body


def corp_code_for(stock_code, cache_dir, key, session=None):
    """종목코드 → DART 기업고유번호. 회사 목록(corpCode.xml)을 한 번 받아 dart_cache/corp_map.json 에 둠."""
    path = os.path.join(cache_dir, "corp_map.json")
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        m = None
    if m is None or (stock_code not in m and time.time() - os.path.getmtime(path) > 86400):
        s = session or requests
        r = s.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
        if r.status_code != 200:
            raise AutoError(f"DART 회사 목록 HTTP {r.status_code}")
        import xml.etree.ElementTree as ET
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                root = ET.fromstring(z.read(z.namelist()[0]))
        except Exception:
            raise AutoError("DART 회사 목록을 읽지 못함")
        m = {}
        for it in root.iter("list"):
            sc, cc = (it.findtext("stock_code") or "").strip(), (it.findtext("corp_code") or "").strip()
            if sc and cc:
                m[sc] = cc
        os.makedirs(cache_dir, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f)
        os.replace(tmp, path)
    cc = m.get(stock_code)
    if not cc:
        raise AutoError("DART 회사 목록에 이 종목이 없어요")
    return cc


def last_business_year(today=None):
    """가장 최근에 사업보고서가 나온 사업연도 (3월 말 제출 → 4월부터 전년도)"""
    today = today or dt.date.today()
    return today.year - 1 if today.month >= 4 else today.year - 2


def _common(rows):
    """보통주 줄 (없으면 합계 줄)"""
    pick = [r for r in rows if "보통" in (r.get("se") or r.get("stock_knd") or "")]
    return pick[0] if pick else next((r for r in rows if "합계" in (r.get("se") or "")), None)


def dividend_history(code_corp, key, today=None, session=None, max_windows=4):
    """주당 현금배당금(보통주) {연도: 원}. 사업보고서 하나가 당기·전기·전전기 3년을 주므로 3년씩 건너뛰며 받음."""
    y0 = last_business_year(today)
    dps = {}
    for w in range(max_windows):
        b = y0 - 3 * w
        st, body = dart_json("alotMatter.json", key, session=session, corp_code=code_corp,
                             bsns_year=str(b), reprt_code="11011")
        if st != "000":
            break
        rows = [r for r in body.get("list", []) if "주당 현금배당금" in (r.get("se") or "").replace("(", " ")]
        row = next((r for r in rows if "보통" in (r.get("stock_knd") or "")), rows[0] if rows else None)
        if not row:
            break
        for yr, field in ((b, "thstrm"), (b - 1, "frmtrm"), (b - 2, "lwfr")):
            v = _num(row.get(field))
            if v is not None:
                dps.setdefault(yr, v)
        # 연속이 중간에 끊겼으면 더 오래된 해는 볼 필요가 없음 (끊긴 해가 받은 가장 오래된 해보다 새 해)
        if streak(dps, y0)[1] > min(dps):
            break
    return dps


def streak(dps, y0):
    """연속 인상 연수 (동결은 이어가되 연수에 안 셈, 감소·무배당은 끊김). → (연수, 거슬러 올라간 마지막 해)"""
    n, y = 0, y0
    if (dps.get(y) or 0) <= 0:
        return 0, y
    while (y - 1) in dps:
        if dps[y] > dps[y - 1]:
            n += 1
        elif dps[y] < dps[y - 1] or dps[y] <= 0:
            break
        y -= 1
    return n, y


def share_history(code_corp, key, today=None, session=None):
    """주식의 총수 현황(보통주) {사업연도: {total, treasury, burn_cum}} 최근 4개 사업연도"""
    y0 = last_business_year(today)
    out = {}
    for b in range(y0, y0 - 4, -1):
        st, body = dart_json("stockTotqySttus.json", key, session=session, corp_code=code_corp,
                             bsns_year=str(b), reprt_code="11011")
        if st != "000":
            continue
        row = _common(body.get("list", []))
        if not row:
            continue
        total, tre = _num(row.get("istc_totqy")), _num(row.get("tesstk_co"))
        if total:
            out[b] = {"total": total, "treasury": tre or 0.0, "burn_cum": _num(row.get("profit_incnr"))}
    return out


def estimate_dart(stock_code, key, cache_dir, today=None, session=None):
    """→ {항목: (값, 근거)}"""
    corp = corp_code_for(stock_code, cache_dir, key, session=session)
    out = {}
    y0 = last_business_year(today)
    dps = dividend_history(corp, key, today=today, session=session)
    if dps:
        n, _ = streak(dps, y0)
        out["divyears"] = ("10" if n >= 10 else "5" if n >= 5 else "3" if n >= 3 else "0",
                           f"{y0}년 기준 배당 연속 인상 {n}년 (주당 배당금 {y0}년 {dps.get(y0, 0):,.0f}원, "
                           f"확인한 기간 {min(dps)}~{max(dps)}년, 동결은 이어가되 연수에 안 셈)")
    sh = share_history(corp, key, today=today, session=session)
    if y0 in sh:
        cur = sh[y0]
        ratio = cur["treasury"] / cur["total"]
        out["treasury"] = ("none" if cur["treasury"] <= 0 else "lt2" if ratio < 0.02 else "lt5" if ratio < 0.05 else "ge5",
                           f"{y0}년 말 자기주식 {cur['treasury']:,.0f}주 ÷ 발행주식 {cur['total']:,.0f}주 = {ratio:.2%}")
    burns = {}
    for b in (y0, y0 - 1, y0 - 2):
        if b in sh and (b - 1) in sh and sh[b]["burn_cum"] is not None and sh[b - 1]["burn_cum"] is not None:
            burns[b] = max(sh[b]["burn_cum"] - sh[b - 1]["burn_cum"], 0.0)
    if y0 in burns:
        base = sh[y0 - 1]["total"]
        r = burns[y0] / base
        out["burn"] = ("2" if r > 0.02 else "1.5" if r > 0.015 else "0.5" if r > 0.005 else "0",
                       f"{y0}년 이익소각 {burns[y0]:,.0f}주 ÷ {y0 - 1}년 말 발행주식 {base:,.0f}주 = {r:.2%}")
    if all(b in sh and (b - 1) in sh for b in (y0, y0 - 1, y0 - 2)) and len(burns) == 3:
        acted, notes = 0, []
        for b in (y0, y0 - 1, y0 - 2):
            grew = sh[b]["treasury"] > sh[b - 1]["treasury"]
            did = burns[b] > 0 or grew
            acted += 1 if did else 0
            notes.append(f"{b}년 {'소각' if burns[b] > 0 else '보유 증가' if grew else '없음'}")
        out["buyback"] = ("yes" if acted == 3 else "no", "최근 3개 사업연도: " + ", ".join(notes))
    return out


# ---------------- 저장 + 한 번에 새로고침 ----------------
def _logged_recently(conn, code, source, days_ok, days_fail, now):
    r = conn.execute("SELECT tried, ok FROM scorecard_auto_log WHERE code=? AND source=?", (code, source)).fetchone()
    if not r:
        return False
    age = (now - dt.date.fromisoformat(r[0])).days
    return age < (days_ok if r[1] else days_fail)


def _store(conn, code, source, est, now, err=""):
    for item, (value, note) in est.items():
        conn.execute("INSERT OR REPLACE INTO scorecard_auto VALUES (?,?,?,?,?,?)",
                     (code, item, value, note, source, now.isoformat()))
    conn.execute("INSERT OR REPLACE INTO scorecard_auto_log VALUES (?,?,?,?,?)",
                 (code, source, now.isoformat(), 0 if err else 1, err))
    conn.commit()


def refresh(db_path, code, dart_key="", cache_dir="", session=None, today=None, force=False):
    """한 종목의 자동 추정을 새로 계산해 저장. → 실패 안내 문구 목록 (비어 있으면 모두 성공)
    네이버는 하루에 한 번, DART는 한 달에 한 번(실패하면 하루 뒤 다시)만 받음."""
    now = today or dt.date.today()
    errors = []
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        if force or not _logged_recently(conn, code, "네이버", 1, FAIL_RETRY_DAYS, now):
            try:
                est = estimate_naver(fetch_finance(code, "annual", session), fetch_finance(code, "quarter", session))
                _store(conn, code, "네이버", est, now)
            except Exception as e:
                _store(conn, code, "네이버", {}, now, str(e)[:100])
                errors.append(f"네이버 재무를 받지 못했어요 ({str(e)[:60]})")
        if dart_key and (force or not _logged_recently(conn, code, "DART", DART_RETRY_DAYS, FAIL_RETRY_DAYS, now)):
            try:
                est = estimate_dart(code, dart_key, cache_dir, today=now, session=session)
                _store(conn, code, "DART", est, now)
            except Exception as e:
                _store(conn, code, "DART", {}, now, str(e)[:100])
                errors.append(f"DART 값을 받지 못했어요 ({str(e)[:60]})")
        return errors
    finally:
        conn.close()
