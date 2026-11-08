# -*- coding: utf-8 -*-
"""
제출인(기관·개인) × 종목별 매수·매도 이력으로 '얼마에 사서 얼마에 팔았나, 차익이 얼마인가, 얼마나 남았나'를 계산.

  - 공시 이력(detail 표의 변동일·증감·단가)을 날짜순으로 따라가며 평균단가법으로 계산합니다.
    매수하면 평균 매입가를 갱신하고, 매도하면 (매도 단가 - 그 시점 평균 매입가) × 수량이 실현 차익.
  - 공시로 모은 기간보다 앞서 산 주식을 파는 경우는 원가를 알 수 없어서 차익에서 빼고 '원가 모르는 매도'로 따로 셉니다.
  - 단가가 없는 줄(증여·상속 등)은 수량에는 넣지만 평균 단가·차익 계산에서는 뺍니다.
  - 남은 잔량: 보고서 요약표의 '이번 보고서 보유주식수'(분석 형식 6 이상으로 다시 분석한 공시에 있음)가 있으면 그것,
    없으면 공시 기간 안 순증감(증감 합)을 씁니다. 실제 보유와 다를 수 있는 추정이에요.
  - 같은 거래가 정정 공시로 다시 올라오면 겹쳐 셀 수 있어요.
"""
import sqlite3


def compute(conn):
    """{(제출인, 종목코드): 포지션 dict}. detail 표가 없으면 {}."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(detail)")}
        has_rep = "이번보유수량" in cols
        sel = "제출인, 종목코드, 변동일, 증감, 단가, 제출일, 접수번호" + (", 이번보유수량, 이번보유비율" if has_rep else "")
        rows = conn.execute(f"SELECT {sel} FROM detail WHERE 증감 IS NOT NULL AND 변동일 IS NOT NULL "
                            "AND 제출인 IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return {}
    rows = [tuple(r) for r in rows]
    rows.sort(key=lambda r: (r[2], 0 if r[3] > 0 else 1, r[6] or ""))       # 변동일 → 같은 날은 매수 먼저 → 접수번호
    pos = {}
    for r in rows:
        who, code, day, qty, price, sub, rno = r[:7]
        p = pos.setdefault((who, code), dict(
            who=who, code=code, first=day, last_buy="", last_sell="", net=0.0,
            buy_q=0.0, buy_q_p=0.0, buy_amt=0.0, sell_q=0.0, sell_q_p=0.0, sell_amt=0.0,
            known_qty=0.0, known_cost=0.0, realized=0.0, realized_cost=0.0, matched_q=0.0, unmatched_sell=0.0,
            report_qty=None, report_pct=None, report_date="", _rep_key=("", "")))
        priced = price is not None and price > 0
        p["net"] += qty
        if qty > 0:
            p["buy_q"] += qty
            p["last_buy"] = max(p["last_buy"], day)
            if priced:
                p["buy_q_p"] += qty
                p["buy_amt"] += qty * price
                p["known_qty"] += qty
                p["known_cost"] += qty * price
        elif qty < 0:
            sq = -qty
            p["sell_q"] += sq
            p["last_sell"] = max(p["last_sell"], day)
            if priced:
                p["sell_q_p"] += sq
                p["sell_amt"] += sq * price
            avg = p["known_cost"] / p["known_qty"] if p["known_qty"] > 0 else None
            matched = min(sq, p["known_qty"]) if avg is not None else 0.0
            if matched > 0:
                if priced:
                    p["realized"] += matched * (price - avg)
                    p["realized_cost"] += matched * avg
                    p["matched_q"] += matched
                p["known_qty"] -= matched
                p["known_cost"] -= matched * avg
            p["unmatched_sell"] += sq - matched
        if len(r) > 7 and r[7] is not None:                                   # 보고서 요약표의 보유수량 (가장 최근 제출 기준)
            key = (sub or "", rno or "")
            if key >= p["_rep_key"]:
                p["_rep_key"] = key
                p["report_qty"], p["report_pct"], p["report_date"] = r[7], r[8], sub or ""
    for p in pos.values():
        p.pop("_rep_key", None)
        p["avg_buy"] = p["buy_amt"] / p["buy_q_p"] if p["buy_q_p"] else None
        p["avg_sell"] = p["sell_amt"] / p["sell_q_p"] if p["sell_q_p"] else None
        p["avg_cost"] = p["known_cost"] / p["known_qty"] if p["known_qty"] > 0 else None
        p["realized_pct"] = p["realized"] / p["realized_cost"] if p["realized_cost"] else None
        p["sold"] = p["sell_q"] > 0
        if p["report_qty"] is not None:
            p["remain"], p["remain_src"] = p["report_qty"], "보고서"
        else:
            p["remain"], p["remain_src"] = max(p["net"], 0.0), "공시 증감 합"
    return pos


def unrealized(p, close):
    """남은 주식(원가를 아는 몫)의 평가 손익 (원, 비율). 계산 못 하면 (None, None)."""
    if not close or not p.get("avg_cost") or p["known_qty"] <= 0:
        return None, None
    return (close - p["avg_cost"]) * p["known_qty"], close / p["avg_cost"] - 1


def summarize(positions):
    """여러 포지션(한 종목에 투자한 여러 기관)을 합침"""
    ps = list(positions)
    sold = [p for p in ps if p["sold"]]
    sq, sa = sum(p["sell_q_p"] for p in ps), sum(p["sell_amt"] for p in ps)
    return dict(n=len(ps), sold_n=len(sold), none_sold=not sold,
                realized=sum(p["realized"] for p in ps), realized_cost=sum(p["realized_cost"] for p in ps),
                sell_q=sum(p["sell_q"] for p in ps), avg_sell=sa / sq if sq else None,
                remain=sum(p["remain"] for p in ps))
