# -*- coding: utf-8 -*-
"""
투자처 종합: 앱이 모은 모든 신호(재무제표 점수·공시 단서·증권사 목표가·린치/버핏/그레이엄/F-스코어·모멘텀·기관 매수·지분분쟁·사이클 국면)를
단기(1~3개월) · 중기(3~12개월) · 장기(1~3년 이상) 관점으로 다르게 가중해서 종목을 골라 줘요.

- '최적'이 아니라 '지금 앱이 가진 신호로 볼 때 그 관점에 맞는 후보'예요. 점수는 제가 정한 가중치이고, 과거 성과로 검증한 값이 아니에요
  (그래서 선정 기록을 날마다 남겨 두고 시간이 지난 뒤 실제로 어땠는지 아래 '과거 선정 성과'로 보여 줘요).
- 자료가 없는 항목은 점수에서 빼고 다시 정규화하며, 자료가 얼마나 찼는지(자료 충분도)를 같이 표시해요. 충분도 50% 미만은 순위에서 뺐어요.
- 위험 신호(영업적자·감사 이슈·경고 다수·지분분쟁 치열·과열)는 점수를 깎지 않고 '주의'로 따로 붙여요. 투자 추천이 아니에요.
"""
import datetime as dt
import json
import sqlite3

HORIZONS = {"short": ("단기", "1~3개월"), "mid": ("중기", "3~12개월"), "long": ("장기", "1~3년 이상")}
DEFENSIVE = ("음식료", "제약", "의료", "통신", "유틸리티", "전력", "가스", "보험", "담배", "생명보험", "손해보험", "필수소비", "화장품", "식품", "건강관리")
CYCLICAL = ("반도체", "전자", "IT", "소프트웨어", "기계", "자동차", "화학", "철강", "조선", "건설", "에너지", "소재", "디스플레이", "운송", "항공")


def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _lin(v, lo, hi):
    """lo 이하 0, hi 이상 1 (선형)"""
    if v is None:
        return None
    if hi == lo:
        return None
    return _clip((v - lo) / (hi - lo))


class _Acc:
    def __init__(self):
        self.items = []             # (이름, 값 0..1 또는 None, 가중치, 이유(좋을 때))

    def add(self, name, val, w, reason=""):
        self.items.append((name, val, w, reason))

    def result(self):
        tot = sum(w for _, _, w, _ in self.items)
        have = [(n, v, w, r) for n, v, w, r in self.items if v is not None]
        hw = sum(w for _, _, w, _ in have)
        if not have or tot <= 0:
            return dict(score=None, cov=0.0, reasons=[], parts=[])
        score = sum(v * w for _, v, w, _ in have) / hw * 100
        contrib = sorted(have, key=lambda x: -(x[1] * x[2]))
        reasons = [r for n, v, w, r in contrib if v >= 0.7 and r][:4]
        return dict(score=score, cov=hw / tot, reasons=reasons, parts=[(n, v, w) for n, v, w, _ in have])


def _per_score(per, th):
    """PER → 0..1 (적자는 0). th=(좋음, 보통, 비쌈) 경계"""
    if per is None:
        return None
    if per <= 0:
        return 0.0
    return 1.0 if per <= th[0] else 0.7 if per <= th[1] else 0.35 if per <= th[2] else 0.1


def _pbr_score(pbr, th):
    if pbr is None or pbr <= 0:
        return None
    return 1.0 if pbr <= th[0] else 0.7 if pbr <= th[1] else 0.35 if pbr <= th[2] else 0.1


def _industry_fit(group, phase):
    """사이클 국면과 업종의 궁합 0..1 (업종 이름 키워드로 방어/경기민감 구분). 모르면 None"""
    if not group or not phase:
        return None
    de = any(k in group for k in DEFENSIVE)
    cy = any(k in group for k in CYCLICAL)
    if phase in ("down", "turn_down"):
        return 0.8 if de else 0.35 if cy else 0.5
    if phase == "up":
        return 0.8 if cy else 0.45 if de else 0.55
    if phase == "rebound":
        return 0.7 if cy else 0.5
    return 0.5


def flags_of(f):
    out = []
    if f.get("audit_opinion") and f["audit_opinion"] != "적정":
        out.append("감사의견이 적정이 아니에요")
    if f.get("op") is not None and f["op"] < 0:
        out.append("영업적자")
    if f.get("warn_n", 0) >= 2:
        out.append(f"재무 경고 신호 {f['warn_n']}개")
    if f.get("clue_warn_n", 0) >= 2:
        out.append(f"공시 단서 경고 {f['clue_warn_n']}개")
    if f.get("debt_ratio") is not None and f["debt_ratio"] > 3.0:
        out.append(f"부채비율 {f['debt_ratio']:.0%}")
    if f.get("dispute") in ("치열", "시작"):
        out.append(f"지분분쟁 {f['dispute']} 단계 — 변동성이 매우 커요")
    if f.get("r3m") is not None and f["r3m"] > 0.5:
        out.append(f"최근 3개월 {f['r3m']:+.0%} — 과열 가능")
    if f.get("trend_down") and (f.get("per") or 0) > 40:
        out.append("하락 추세인데 PER이 높아요")
    return out


def horizon_scores(f, phase=None):
    """종목 특징 dict → {short, mid, long: dict(score, cov, reasons, parts)} + flags
    f 키: fin_score, parts, rev_yoy, op_yoy, op, opm, roe, debt_ratio, ocf_to_ni, fcf, turn, score_delta, warn_n, clue_warn_n, insider_net, shr, rnd, audit_opinion,
          upside, tg_up, tg_dn, nrep, per, pbr, div, lynch, buffett, graham, fscore, momentum, guru_avg, trend_up, trend_down, r3m, r12, above_ma, inst_n, inst_gap, dispute, group"""
    g = lambda k: f.get(k)
    parts = g("parts") or {}
    # ----- 단기 -----
    s = _Acc()
    mom = g("momentum")
    s.add("모멘텀", None if mom is None else _clip(mom / 100), 30, f"모멘텀 점수 {mom:.0f}" if mom is not None else "")
    if mom is None and g("above_ma") is not None:                     # 점수가 없으면 가격 추세로 대신
        s.items[-1] = ("모멘텀", (0.75 if g("above_ma") else 0.2) + (0.15 if (g("r3m") or 0) > 0 else 0.0), 30, "주가가 200일선 위에서 오르는 중" if g("above_ma") else "")
    cat = []
    if g("insider_net") is not None:
        cat.append(1.0 if g("insider_net") > 0 else 0.3 if g("insider_net") == 0 else 0.0)
    if g("tg_up") is not None or g("tg_dn") is not None:
        u, d = g("tg_up") or 0, g("tg_dn") or 0
        cat.append(1.0 if u > d else 0.5 if u == d else 0.1)
    if g("score_delta") is not None:
        cat.append(_lin(g("score_delta"), -10, 10))
    if g("turn"):
        cat.append(1.0)
    if g("inst_n") is not None:
        cat.append(_lin(g("inst_n"), 0, 5))
    cat = [c for c in cat if c is not None]
    s.add("이벤트·수급", (sum(cat) / len(cat)) if cat else None, 25, "내부자 순매수·목표가 상향·점수 상승·기관 매수 등 단기 촉매가 있어요")
    s.add("실적 모멘텀", None if g("op_yoy") is None and g("rev_yoy") is None else sum(v for v in (_lin(g("op_yoy"), -0.2, 0.5), _lin(g("rev_yoy"), -0.05, 0.25)) if v is not None) / max(1, sum(1 for v in (g("op_yoy"), g("rev_yoy")) if v is not None)), 20,
          f"매출 {g('rev_yoy'):+.0%}" + (f" · 영업이익 {g('op_yoy'):+.0%}" if g("op_yoy") is not None else "") if g("rev_yoy") is not None else "실적이 좋아지고 있어요")
    s.add("가격 매력", None if g("inst_gap") is None else _lin(-g("inst_gap"), -0.1, 0.25), 15, f"기관 평균 매수가보다 {-g('inst_gap'):.0%} 낮아요" if g("inst_gap") is not None and g("inst_gap") < 0 else "")
    s.add("목표가 여력", None if g("upside") is None else _lin(g("upside"), -0.05, 0.35), 10, f"증권사 목표가 평균 상승여력 {g('upside'):+.0%}" if g("upside") is not None else "")
    short = s.result()
    # ----- 중기 -----
    m = _Acc()
    growth = parts.get("성장")
    m.add("실적 개선", None if growth is None else _clip(growth / 30), 30, "재무 점수의 성장 항목이 높아요")
    m.add("컨센서스", None if g("upside") is None else _lin(g("upside"), -0.05, 0.35), 20, f"증권사 목표가 평균 상승여력 {g('upside'):+.0%}" if g("upside") is not None else "")
    vals = [v for v in (_per_score(g("per"), (12, 20, 30)), _pbr_score(g("pbr"), (1.2, 2.5, 5))) if v is not None]
    m.add("밸류", (sum(vals) / len(vals)) if vals else None, 15, (f"PER {g('per'):.1f}" + (f" · PBR {g('pbr'):.2f}" if g("pbr") is not None else "")) if g("per") is not None else "밸류가 부담스럽지 않아요")
    m.add("추세", None if g("above_ma") is None and g("momentum") is None else (_clip(g("momentum") / 100) if g("momentum") is not None else (0.8 if g("above_ma") else 0.25)), 15, "주가 추세가 살아 있어요")
    safe = None if (parts.get("현금") is None and parts.get("안정성") is None) else ((parts.get("현금") or 0) / 25 * 0.5 + (parts.get("안정성") or 0) / 20 * 0.5)
    m.add("재무 건전", safe, 10, "현금흐름과 안정성이 받쳐 줘요")
    m.add("사이클 궁합", _industry_fit(g("group"), phase), 10, f"지금 국면에 맞는 업종({g('group')})" if g("group") else "")
    mid = m.result()
    # ----- 장기 -----
    l = _Acc()
    l.add("재무 우량", None if g("fin_score") is None else _clip(g("fin_score") / 100), 30, f"재무제표 점수 {g('fin_score'):.0f}" if g("fin_score") is not None else "")
    q = [v for v in (_lin(g("roe"), 0.04, 0.20), _lin(g("opm"), 0.02, 0.18), _lin(g("ocf_to_ni"), 0.3, 1.2)) if v is not None]
    l.add("수익성·현금", (sum(q) / len(q)) if q else None, 20, "ROE·영업이익률·현금 전환이 좋아요")
    gu = g("guru_avg")
    l.add("가치투자 점수", None if gu is None else _clip(gu / 100), 20, f"린치·버핏·그레이엄·F 평균 {gu:.0f}" if gu is not None else "")
    st = []
    if g("debt_ratio") is not None:
        st.append(1.0 if g("debt_ratio") <= 1.0 else 0.6 if g("debt_ratio") <= 2.0 else 0.25)
    if g("audit_opinion"):
        st.append(1.0 if g("audit_opinion") == "적정" else 0.0)
    if g("dispute") is not None:
        st.append(0.2 if g("dispute") in ("치열", "시작") else 0.6)
    else:
        st.append(0.8)
    l.add("안정성", (sum(st) / len(st)) if st else None, 10, "부채·감사·지배구조가 안정적이에요")
    sh = []
    if g("div") is not None:
        sh.append(_lin(g("div"), 0, 4))
    if g("shr"):
        sh.append(1.0)
    l.add("주주환원", (sum(sh) / len(sh)) if sh else None, 10, "배당·자사주로 이익을 돌려줘요")
    lv = [v for v in (_per_score(g("per"), (15, 25, 40)), _pbr_score(g("pbr"), (1.5, 3, 6))) if v is not None]
    l.add("가격 부담 적음", (sum(lv) / len(lv)) if lv else None, 10, "가격이 부담스럽지 않아요")
    long = l.result()
    return dict(short=short, mid=mid, long=long, flags=flags_of(f))


# ---------------- 선정 기록과 성과 ----------------
def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS pick_hist (d TEXT, horizon TEXT, code TEXT, name TEXT, rank INTEGER, score REAL, close REAL, PRIMARY KEY (d, horizon, code));
    """)


def snapshot(conn, picks, closes, today=None, top=15):
    """picks: {horizon: [(code, name, score)]} 상위 목록 → pick_hist에 그날 기록 (같은 날은 덮어씀)"""
    ensure(conn)
    d = (today or dt.date.today()).isoformat()
    n = 0
    for hz, lst in picks.items():
        for i, (code, name, score) in enumerate(lst[:top], 1):
            conn.execute("INSERT OR REPLACE INTO pick_hist VALUES (?,?,?,?,?,?,?)", (d, hz, code, name, i, score, closes.get(code)))
            n += 1
    conn.commit()
    return n


def performance(conn, price_of, index_ret, today=None, min_days=20):
    """기록한 선정이 min_days일 넘게 지난 것들의 성과 → {horizon: dict(n, mean, excess, win, days)}
    price_of(code) → 최신 종가, index_ret(d) → 그 날 이후 지수 수익률(없으면 None)"""
    today = today or dt.date.today()
    try:
        rows = conn.execute("SELECT d, horizon, code, close FROM pick_hist WHERE close IS NOT NULL").fetchall()
    except sqlite3.Error:
        return {}
    acc = {}
    for d, hz, code, c0 in rows:
        age = (today - dt.date.fromisoformat(d)).days
        if age < min_days:
            continue
        c1 = price_of(code)
        if not c1 or not c0:
            continue
        r = c1 / c0 - 1
        ir = index_ret(d)
        a = acc.setdefault(hz, dict(r=[], x=[], ages=[]))
        a["r"].append(r)
        a["ages"].append(age)
        if ir is not None:
            a["x"].append(r - ir)
    out = {}
    for hz, a in acc.items():
        n = len(a["r"])
        out[hz] = dict(n=n, mean=sum(a["r"]) / n, excess=(sum(a["x"]) / len(a["x"])) if a["x"] else None, win=sum(1 for v in a["r"] if v > 0) / n, days=sum(a["ages"]) / n)
    return out
