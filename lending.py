# -*- coding: utf-8 -*-
"""
주식 대차거래 (공공데이터포털 '금융위원회_주식대차거래정보', 한국예탁결제원·금융투자협회·증권금융 자료).
공매도 자체는 아니고, 공매도를 하려면 먼저 주식을 빌려야 해서 공매도 압력의 가까운 지표로 보는 '대차잔고'입니다.

  엔드포인트  https://apis.data.go.kr/1160100/service/GetCMStckLnbInfoService
  getStckLnbDetail      주식 대차거래내역: 기준일자·시장구분·종목명·종목코드·체결·상환·잔고주수·잔고금액   (이 모듈이 씀)
  getStckLnbProgress    주식 대차거래추이: 기준일자·체결주수·상환주수·잔고주수·잔고금액                 (시장 전체 추이, 점검에서만)
  getStckLnbInvpnDetail 참여자별 주식 대차거래내역                                                     (쓰지 않음)
  일 호출 한도 10,000건. 키는 공공데이터포털 서비스키(DATA_GO_KR_KEY, 정책 수집과 같은 키 — 이 데이터셋 활용신청이 필요).

명세 화면에는 응답 필드의 '한글 설명'만 있고 실제 영문 이름은 없어서, 이름 규칙으로 필드를 찾습니다(find_fields).
못 찾으면 받은 필드를 그대로 보여 주는 오류를 냅니다 — 설정의 '연결 확인'에서 실제 응답을 볼 수 있어요.
"""
import datetime as dt
import re
import sqlite3
import time
from urllib.parse import unquote

import requests

BASE = "https://apis.data.go.kr/1160100/service/GetCMStckLnbInfoService/"
OPS = {"detail": "getStckLnbDetail", "progress": "getStckLnbProgress", "invpn": "getStckLnbInvpnDetail"}
PAGE_SIZE = 1000
MAX_PAGES = 8


class LendingError(Exception):
    pass


def num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _items(body):
    """data.go.kr 표준 응답에서 (resultCode, resultMsg, totalCount, 항목 목록)"""
    resp = body.get("response", body) if isinstance(body, dict) else {}
    head, bod = resp.get("header") or {}, resp.get("body") or {}
    items = bod.get("items")
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):            # 한 건이면 목록이 아니라 dict로 옴
        items = [items]
    return str(head.get("resultCode", "")), head.get("resultMsg", ""), int(num(bod.get("totalCount")) or 0), items or []


def call(op, key, params=None, session=None, timeout=40):
    """한 오퍼레이션 호출 → (totalCount, 항목 목록). 키 오류·한도 초과·응답 이상은 LendingError."""
    s = session or requests
    q = {"serviceKey": unquote(key or ""), "resultType": "json", "pageNo": 1, "numOfRows": PAGE_SIZE, **(params or {})}
    r = s.get(BASE + OPS[op], params=q, timeout=timeout)
    text = getattr(r, "text", "") or ""
    if text.lstrip().startswith("<"):                           # 키 오류 등은 resultType=json이어도 XML로 옴
        msg = re.search(r"<(?:returnAuthMsg|resultMsg|errMsg)>([^<]*)<", text)
        raise LendingError("공공데이터포털 오류: " + (msg.group(1) if msg else text[:80]).strip()
                           + " — 키가 맞는지, 이 데이터셋 활용신청이 승인됐는지 확인하세요")
    if r.status_code != 200:
        raise LendingError(f"공공데이터포털 HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        raise LendingError("응답을 읽지 못했어요")
    code, msg, total, items = _items(body)
    if code not in ("", "00", "0"):
        raise LendingError(f"공공데이터포털 {code} {msg}".strip())
    return total, items


# ---------------- 응답 필드 찾기 ----------------
_SHARES = ("shrs", "shs", "qty", "stkcnt", "cnt", "ju")
_AMOUNT = ("amt", "amount", "won")
_BAL = ("blnc", "bal", "jan", "remain")


def _has(k, words):
    return any(w in k for w in words)


def find_fields(item):
    """한 줄의 키들에서 날짜·코드·이름·시장·체결·상환·잔고주수·잔고금액을 찾음 → {역할: 키 또는 None}"""
    keys = list(item)
    low = {k: k.lower() for k in keys}
    out = dict.fromkeys(("date", "code", "name", "market", "settle", "repay", "balance_shs", "balance_amt"))
    out["date"] = next((k for k in keys if re.fullmatch(r"\d{8}", str(item[k] or "")) and ("dt" in low[k] or "date" in low[k] or "ymd" in low[k])), None) \
        or next((k for k in keys if re.fullmatch(r"\d{8}", str(item[k] or ""))), None)
    codes = [k for k in keys if low[k].endswith("cd") or "code" in low[k]]
    out["code"] = (next((k for k in codes if re.fullmatch(r"[0-9A-Za-z]{6}", str(item[k] or ""))), None)
                   or next((k for k in codes if re.fullmatch(r"KR\d{10}", str(item[k] or ""))), None))
    out["market"] = next((k for k in keys if ("mrkt" in low[k] or "mkt" in low[k]) and k != out["code"]), None)
    out["name"] = next((k for k in keys if low[k].endswith("nm") and k != out["market"] and "mrkt" not in low[k] and "mkt" not in low[k]), None)
    bal_amt = next((k for k in keys if _has(low[k], _BAL) and _has(low[k], _AMOUNT)), None)
    bal_shs = next((k for k in keys if _has(low[k], _BAL) and _has(low[k], _SHARES) and k != bal_amt), None)
    out["balance_amt"], out["balance_shs"] = bal_amt, bal_shs
    # 날짜·코드·이름·시장으로 쓴 칸과, 숫자가 아닌 칸은 체결·상환 후보에서 뺌 (예: srtnCd 안의 'rtn'이 상환으로 잡히던 문제)
    used = {bal_amt, bal_shs, out["date"], out["code"], out["name"], out["market"]}
    numeric = [k for k in keys if k not in used and num(item[k]) is not None]
    out["repay"] = next((k for k in numeric if _has(low[k], ("rpym", "repay", "sanghwan", "redemp", "return", "rdmp"))), None)
    used.add(out["repay"])
    out["settle"] = next((k for k in numeric if k not in used and _has(low[k], ("cncl", "chegyeol", "exec", "cnts", "settle", "ctrt", "new", "trd", "lend"))
                          and _has(low[k], _SHARES)), None)
    return out


def parse_item(item, fields=None):
    f = fields or find_fields(item)
    if not (f["code"] and f["date"] and f["balance_shs"]):
        raise LendingError("응답에서 날짜·종목코드·잔고주수 필드를 못 찾았어요. 받은 필드: " + ", ".join(item.keys()))
    code = str(item[f["code"]]).strip()
    if len(code) == 12 and code.startswith("KR"):
        code = code[3:9]
    return dict(date=str(item[f["date"]]), code=code, name=item.get(f["name"]) if f["name"] else None,
                market=item.get(f["market"]) if f["market"] else None,
                settle=num(item.get(f["settle"])) if f["settle"] else None,
                repay=num(item.get(f["repay"])) if f["repay"] else None,
                balance_shs=num(item[f["balance_shs"]]),
                balance_amt=num(item.get(f["balance_amt"])) if f["balance_amt"] else None)


def fetch_day(day, key, session=None, sleep=0.2):
    """한 날짜의 전 종목 대차거래내역 (여러 쪽) → [parse_item 결과]"""
    rows, total, page = [], None, 1
    fields = None
    while page <= MAX_PAGES:
        total, items = call("detail", key, {"basDt": day, "pageNo": page}, session=session)
        if not items:
            break
        if fields is None:
            fields = find_fields(items[0])
        rows += [parse_item(it, fields) for it in items]
        if len(rows) >= total:
            break
        page += 1
        time.sleep(sleep)
    return rows


# ---------------- 저장 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lending (date TEXT, code TEXT, name TEXT, market TEXT, settle REAL, repay REAL,
        balance_shs REAL, balance_amt REAL, PRIMARY KEY (code, date));
    CREATE TABLE IF NOT EXISTS lending_checked (date TEXT PRIMARY KEY, rows INTEGER);
    """)


def _days(today, n):
    d, out = (today or dt.date.today()), []
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out


def sync(db_path, key, log=print, want_days=10, today=None, session=None, sleep=0.2):
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM lending")}
        off = {r[0] for r in conn.execute("SELECT date FROM lending_checked WHERE rows=0")}
        got, calls, saved, found = 0, 0, 0, set()
        for day in _days(today, want_days + 6):
            if got >= want_days:
                break
            if day in have:
                got += 1
                continue
            if day in off:
                continue
            rows = fetch_day(day, key, session=session, sleep=sleep)
            calls += 1
            for r in rows:
                conn.execute("INSERT OR REPLACE INTO lending VALUES (?,?,?,?,?,?,?,?)",
                             (r["date"] or day, r["code"], r["name"], r["market"], r["settle"], r["repay"], r["balance_shs"], r["balance_amt"]))
            if rows:
                found.add(day)
                got += 1
                saved += len(rows)
            if rows or any(d > day for d in have | found):
                conn.execute("INSERT OR REPLACE INTO lending_checked VALUES (?,?)", (day, len(rows)))
            conn.commit()
        latest = max(have | found) if (have | found) else None
        if latest:
            conn.execute("DELETE FROM lending WHERE date < ?",
                         ((dt.datetime.strptime(latest, "%Y%m%d") - dt.timedelta(days=60)).strftime("%Y%m%d"),))
            conn.commit()
        log(f"대차거래: 호출 {calls}일치 · 새로 저장 {saved}줄 · 보관 {got}거래일")
        return {"호출": calls, "저장": saved, "거래일": got}
    finally:
        conn.close()


def latest(conn):
    """{종목코드: {date, balance_shs, balance_amt, settle, repay, name}} 가장 최근 날짜"""
    try:
        rows = conn.execute("SELECT code, date, balance_shs, balance_amt, settle, repay, name FROM lending "
                            "WHERE date = (SELECT MAX(date) FROM lending)").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: dict(date=r[1], balance_shs=r[2], balance_amt=r[3], settle=r[4], repay=r[5], name=r[6]) for r in rows}


def history(conn, code, n=20):
    """[(날짜, 잔고주수, 체결, 상환)] 오래된 날짜부터"""
    try:
        rows = conn.execute("SELECT date, balance_shs, settle, repay FROM lending WHERE code=? ORDER BY date DESC LIMIT ?",
                            (code, n)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [tuple(r) for r in rows][::-1]


def change(hist, back=5):
    """back거래일 전 대비 잔고 변화율. 계산 못 하면 None"""
    if len(hist) <= back or not hist[-1][1] or not hist[-1 - back][1]:
        return None
    return hist[-1][1] / hist[-1 - back][1] - 1


def check(key, session=None, today=None):
    """연결 확인: 오퍼레이션별로 가장 최근 날짜를 찾아 호출 → 받은 필드와 찾은 역할을 그대로 보여 줌"""
    out = []
    for op in ("detail", "progress"):
        item = dict(op=OPS[op], ok=False, day="", total=0, sample=None, fields=None, note="")
        try:
            for day in _days(today, 6):
                total, items = call(op, key, {"basDt": day, "numOfRows": 3, "pageNo": 1}, session=session)
                if items:
                    item.update(ok=True, day=day, total=total, sample=items[0])
                    if op == "detail":
                        item["fields"] = find_fields(items[0])
                    break
            else:
                item["note"] = "최근 6일 평일에 데이터가 없어요"
        except LendingError as e:
            item["note"] = str(e)
        except requests.RequestException as e:
            item["note"] = f"연결 실패: {type(e).__name__}"
        out.append(item)
    return out
