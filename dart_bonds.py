# -*- coding: utf-8 -*-
"""
DART 주요사항보고서 중 '주식 수가 늘 수 있는 결정' 공시를 종목별로 모아 둡니다.
  전환사채권발행결정(CB) · 신주인수권부사채권발행결정(BW) · 교환사채권발행결정(EB) · 유상증자결정

왜 보나: 전환사채·신주인수권부사채는 나중에 주식으로 바뀌면 주식 수가 늘어(희석) 주가에 부담이 될 수 있고,
  유상증자는 새 주식을 발행해 자금을 모으는 일이라 같은 이유로 주가가 흔들리는 경우가 많아요. 반대로 성장 투자를 위한 것일 수도 있어요.
  (이 앱은 좋다/나쁘다를 판단하지 않고, '있다'는 사실과 금액·가격만 보여 줘요.)

방법: DART 공시검색(list.json)에서 주요사항보고(pblntf_ty=B)를 종목별로 받아 보고서 이름으로 가려내요 (필드 이름에 안 기대서 안정적).
  금액·가격은 상세 API(cvbdIsDecsn 등)에서 이름 규칙으로 찾아 보너스로 붙이고, 못 찾으면 빈칸이에요 ('연결 확인'에서 응답을 볼 수 있어요).
"""
import datetime as dt
import json
import re
import sqlite3
import time

import score_auto
from score_auto import AutoError, dart_json, corp_code_for

KINDS = [("CB", "전환사채", "전환사채권발행결정", "cvbdIsDecsn.json"),
         ("BW", "신주인수권부사채", "신주인수권부사채권발행결정", "bdwtIsDecsn.json"),
         ("EB", "교환사채", "교환사채권발행결정", "exbdIsDecsn.json"),
         ("유증", "유상증자", "유상증자결정", "piicDecsn.json")]
REFRESH_DAYS = 7
YEARS = 3
MAX_FAIL_STREAK = 5

# 상세 응답에서 값을 찾는 이름 후보 (DART 필드 이름은 한 번도 확인 못 했어서 여러 개를 시도)
AMOUNT_KEYS = ("bd_fta", "fdpp_fclt", "fdpp_op", "nstk_ostk_cnt")      # 사채 권면총액 / (유증) 자금 용도들
PRICE_KEYS = ("cv_prc", "ex_prc", "slprc", "nstk_isstk_prc", "ic_prc")


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS dart_bonds (
        code TEXT, rcept_no TEXT, rcept_dt TEXT, kind TEXT, title TEXT, amount TEXT, price TEXT, detail TEXT,
        PRIMARY KEY (code, rcept_no));
    CREATE TABLE IF NOT EXISTS dart_bonds_fetch (code TEXT PRIMARY KEY, fetched TEXT, n INTEGER);
    """)


def classify(title):
    t = re.sub(r"\s+", "", title or "")
    for k, label, kw, _ in KINDS:
        if kw in t:
            return k
    return None


def pick(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", "-"):
            return str(v)
    return ""


def fetch_list(corp, key, today=None, session=None):
    """종목 하나의 최근 YEARS년 주요사항보고 중 해당 공시 → [(rcept_no, 날짜, 종류, 제목)]"""
    today = today or dt.date.today()
    out, page = [], 1
    while page <= 5:
        st, body = dart_json("list.json", key, session=session, corp_code=corp, pblntf_ty="B",
                             bgn_de=(today - dt.timedelta(days=365 * YEARS)).strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"),
                             page_no=str(page), page_count="100")
        rows = body.get("list") or []
        for r in rows:
            k = classify(r.get("report_nm"))
            if k and r.get("rcept_no"):
                d = str(r.get("rcept_dt") or "")
                out.append((r["rcept_no"], f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d, k, (r.get("report_nm") or "").strip()))
        if page >= int(body.get("total_page") or 1):
            break
        page += 1
    return out


def fetch_details(corp, key, kind_path, today=None, session=None):
    """상세 API (해당 종류의 최근 YEARS년 전체) → {rcept_no: 응답 한 줄}. 실패하면 {}"""
    today = today or dt.date.today()
    try:
        st, body = dart_json(kind_path, key, session=session, corp_code=corp,
                             bgn_de=(today - dt.timedelta(days=365 * YEARS)).strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"))
    except AutoError:
        return {}
    return {r.get("rcept_no"): r for r in (body.get("list") or []) if r.get("rcept_no")}


def refresh_code(conn, code, key, cache_dir, today=None, session=None):
    """한 종목을 받아 저장 → 새로 알게 된 공시 수"""
    ensure(conn)
    today = today or dt.date.today()
    corp = corp_code_for(code, cache_dir, key, session=session)
    items = fetch_list(corp, key, today, session)
    have = {r[0] for r in conn.execute("SELECT rcept_no FROM dart_bonds WHERE code=?", (code,))}
    new = [i for i in items if i[0] not in have]
    details = {}
    for kind in {i[2] for i in new}:
        path = next(p for k, _, _, p in KINDS if k == kind)
        details.update(fetch_details(corp, key, path, today, session))
    for rcept, d, kind, title in new:
        row = details.get(rcept) or {}
        amount = pick(row, AMOUNT_KEYS[:1]) if kind != "유증" else pick(row, ("nstk_ostk_cnt",))
        price = pick(row, PRICE_KEYS)
        conn.execute("INSERT OR REPLACE INTO dart_bonds VALUES (?,?,?,?,?,?,?,?)",
                     (code, rcept, d, kind, title, amount, price, json.dumps(row, ensure_ascii=False)[:1500] if row else ""))
    conn.execute("INSERT OR REPLACE INTO dart_bonds_fetch VALUES (?,?,?)", (code, today.isoformat(), len(items)))
    conn.commit()
    return len(new)


def sync(db_path, key, cache_dir, log=print, sleep=0.3, session=None, today=None, max_age=REFRESH_DAYS):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        try:
            codes = [r[0] for r in conn.execute("SELECT DISTINCT 종목코드 FROM summary WHERE 종목코드 IS NOT NULL AND 종목코드 != ''")]
        except sqlite3.OperationalError:
            return {"대상": 0, "새 공시": 0, "실패": 0}
        try:
            codes += [r[0] for r in conn.execute("SELECT code FROM scorecard_stock") if r[0] not in codes]
        except sqlite3.OperationalError:
            pass
        now = today or dt.date.today()
        fresh = {r[0] for r in conn.execute("SELECT code FROM dart_bonds_fetch WHERE fetched >= ?", ((now - dt.timedelta(days=max_age)).isoformat(),))}
        todo = [c for c in codes if c not in fresh]
        new = failed = streak = 0
        for i, code in enumerate(todo):
            if streak >= MAX_FAIL_STREAK:
                log(f"채권·증자 공시: {MAX_FAIL_STREAK}번 연속 실패해서 나머지 {len(todo) - i}개는 다음에 받습니다.")
                break
            try:
                new += refresh_code(conn, code, key, cache_dir, now, session)
                streak = 0
            except AutoError as e:
                if "회사 목록에 이 종목이 없어요" in str(e):          # 상장폐지·코드 불일치: 실패로 세지 않고 기록만
                    conn.execute("INSERT OR REPLACE INTO dart_bonds_fetch VALUES (?,?,0)", (code, now.isoformat()))
                    conn.commit()
                    continue
                failed += 1
                streak += 1
                log(f"채권·증자 공시 {code} 실패: {e}")
            time.sleep(sleep)
        log(f"채권·증자 공시: 대상 {len(codes)}개 · 새 공시 {new}건 · 실패 {failed} · 최근에 받음 {len(codes) - len(todo)}")
        return {"대상": len(codes), "새 공시": new, "실패": failed}
    finally:
        conn.close()


def for_stock(conn, code):
    """[{rcept_no, date, kind, title, amount, price}] 최신순 (표가 없으면 [])"""
    try:
        rows = conn.execute("SELECT rcept_no, rcept_dt, kind, title, amount, price FROM dart_bonds WHERE code=? ORDER BY rcept_dt DESC", (code,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(rcept_no=r[0], date=r[1], kind=r[2], title=r[3], amount=r[4], price=r[5]) for r in rows]


def recent_count(rows, days=365, today=None):
    cut = ((today or dt.date.today()) - dt.timedelta(days=days)).isoformat()
    return sum(1 for r in rows if r["date"] >= cut)


def checked(conn, code):
    """이 종목을 받아 본 적이 있나 (공시가 없어서 비어 있는 것과 아직 안 받은 것을 구분)"""
    try:
        return conn.execute("SELECT fetched FROM dart_bonds_fetch WHERE code=?", (code,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def check(key, cache_dir, session=None, today=None, sample_code="005930"):
    """설정 → 연결 확인용: 공시검색(list.json)과 상세 API 4개. view_ext_check 의 둘째 모양 [{op, ok, day, total, sample, note}]"""
    today = today or dt.date.today()
    items = []
    try:
        corp = corp_code_for(sample_code, cache_dir, key, session=session)
    except Exception as e:
        return [dict(op="회사 목록 (corpCode.xml)", ok=False, day="", total=0, sample=None, note=str(e))]
    it = dict(op=f"공시검색 list.json (주요사항보고, 종목 {sample_code})", ok=False, day="", total=0, sample=None, note="")
    try:
        st, body = dart_json("list.json", key, session=session, corp_code=corp, pblntf_ty="B",
                             bgn_de=(today - dt.timedelta(days=365 * YEARS)).strftime("%Y%m%d"), end_de=today.strftime("%Y%m%d"), page_count="100")
        rows = body.get("list") or []
        it.update(ok=True, total=len(rows), sample=rows[0] if rows else None, day=(rows[0].get("rcept_dt") if rows else ""))
        if not rows:
            it["note"] = "이 종목은 최근 3년 주요사항보고가 없어요 (다른 종목으로도 확인해 보세요)"
    except Exception as e:
        it["note"] = str(e)
    items.append(it)
    for kind, label, _, path in KINDS:
        it = dict(op=f"상세 {path} ({label})", ok=False, day="", total=0, sample=None, note="")
        try:
            st, body = dart_json(path, key, session=session, corp_code=corp, bgn_de=(today - dt.timedelta(days=365 * YEARS)).strftime("%Y%m%d"),
                                 end_de=today.strftime("%Y%m%d"))
            rows = body.get("list") or []
            it.update(ok=True, total=len(rows), sample=rows[0] if rows else None, day=(rows[0].get("rcept_no", "")[:8] if rows else ""))
            if not rows:
                it["note"] = "받을 수는 있는데 이 종목엔 해당 공시가 없어요 (정상일 수 있어요)"
        except Exception as e:
            it["note"] = str(e)
        items.append(it)
    return items


def recent_rows(conn, days=10, today=None):
    """최근 days일 안에 접수된 공시 [(code, rcept_no, rcept_dt, kind, title, amount, price)] (알림용)"""
    cut = ((today or dt.date.today()) - dt.timedelta(days=days)).isoformat()
    try:
        return conn.execute("SELECT code, rcept_no, rcept_dt, kind, title, amount, price FROM dart_bonds WHERE rcept_dt >= ? ORDER BY rcept_dt DESC", (cut,)).fetchall()
    except sqlite3.OperationalError:
        return []
