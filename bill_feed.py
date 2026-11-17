# -*- coding: utf-8 -*-
"""
국회 발의법률안 전체(22대) 중 최근 것을 받아 '법안 조회'에 보여 줘요. (지금까지는 감시 규칙의 법 이름으로만 받았어요.)
방법: 열린국회정보 발의법률안 목록을 키워드 없이 받아 전체 건수를 알고, 목록이 오래된 순인지 최신 순인지 첫 쪽과 마지막 쪽 날짜로 알아낸 뒤
  최신 쪽부터 거슬러 올라가며 N일 안 법안만 저장해요 (더 오래된 쪽이 나오면 멈춤). 이미 받은 법안은 처리 결과가 바뀔 때만 갱신해요.
시장과 무관해 보이는 법안(주제어가 안 걸린 것)도 모두 저장하되 정책 알림 대상에서는 빼요 (알림이 넘치지 않게).
"""
import datetime as dt
import sqlite3

import requests

import bill_watch

DEFAULT_DAYS = 120
MAX_PAGES = 45


def _dt(row):
    return str(row.get("PROPOSE_DT") or "")[:10]


def fetch_recent(api, days=DEFAULT_DAYS, today=None, max_pages=MAX_PAGES, log=print):
    """→ (rows, info). rows: 최근 days일 안에 발의된 법안, info: {total, pages, order}"""
    today = today or dt.date.today()
    cut = (today - dt.timedelta(days=days)).isoformat()
    first, total = api.bills_page("", 1)
    if not first or not total:
        return [], dict(total=total or 0, pages=0, order="?")
    last_page = (total + 99) // 100
    if last_page == 1:
        rows = first
        return [r for r in rows if _dt(r) >= cut], dict(total=total, pages=1, order="?")
    last, _ = api.bills_page("", last_page)
    d_first = max((_dt(r) for r in first), default="")
    d_last = max((_dt(r) for r in last), default="")
    newest_first = d_first >= d_last
    order = "최신 순" if newest_first else "오래된 순"
    pages = range(1, last_page + 1) if newest_first else range(last_page, 0, -1)
    out, n = [], 0
    for p in pages:
        if n >= max_pages:
            break
        rows = first if p == 1 else last if p == last_page else api.bills_page("", p)[0]
        n += 1
        out += [r for r in rows if _dt(r) >= cut]
        dates = [_dt(r) for r in rows if _dt(r)]
        if dates and max(dates) < cut:
            break
    return out, dict(total=total, pages=n, order=order)


def sync(db_path, assembly_key, days=DEFAULT_DAYS, log=print, today=None, api=None):
    import stock_policy as sp
    if not assembly_key and api is None:
        return {"건너뜀": "열린국회정보 키 없음"}
    conn = sqlite3.connect(db_path)
    try:
        sp.ensure(conn)
        bill_watch.ensure(conn)
        api = api or sp.Client("", "", assembly_key)
        try:
            rows, info = fetch_recent(api, days, today)
        except (requests.exceptions.RequestException, ValueError) as e:
            log(f"법안 목록 실패: {type(e).__name__}: {str(e)[:100]}")
            return {"실패": str(e)[:80]}
        new = 0
        for r in rows:
            bid, key, title, date, tps, result, url = bill_watch._bill_row_to_item(r)
            if not bid:
                continue
            market_topics = sp.topics_of(r.get("BILL_NAME", ""), "", "")
            base = not market_topics                       # 시장 주제어가 없는 법안은 알림 대상에서 뺌
            if sp._upsert(conn, key, "법안", "국회", date, title, url, "", market_topics, base, status=result):
                new += 1
            bill_watch.record_tracks(conn, key, r)
        conn.commit()
        log(f"법안 목록: 전체 {info['total']}건({info['order']}) 중 최근 {days}일 {len(rows)}건 · 새 {new} · {info['pages']}쪽")
        return {"전체": info["total"], "최근": len(rows), "새": new, "순서": info["order"]}
    finally:
        conn.close()
