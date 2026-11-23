"""자산 동반 하락 대응: 지금 하락이 어떤 유형인지(가격으로 구분), 과거 비슷한 하락에서 자산별로 어땠는지,
낙폭 단계별 분할 매수 규칙이 한 번에 사는 것보다 어땠는지(코스피 과거 기록). 가격만 쓴 단순 분류이고 조언이 아니다."""
import math

import world_markets as wm
import vol_limit

KEYS = ("SPY", "TLT.O", "IEF.O", "SHY.O", "GLD", "UUP", "VNQ", "DBC", "USO", "HYG", "IBIT.O")
LABEL = {"SPY": "미국 주식", "TLT.O": "장기 국채", "IEF.O": "중기 국채", "SHY.O": "단기 국채", "GLD": "금", "UUP": "달러",
         "VNQ": "리츠(부동산)", "DBC": "원자재", "USO": "원유", "HYG": "하이일드 채권", "IBIT.O": "비트코인"}

# 과거 하락 구간 (직접 고른 날짜 — 자의적일 수 있음). (이름, 시작, 바닥)
EPISODES = [
    ("2011 유럽 재정위기", "2011-05-02", "2011-10-03"),
    ("2015~16 중국·유가 쇼크", "2015-07-20", "2016-02-11"),
    ("2018 4분기 금리 쇼크", "2018-09-20", "2018-12-24"),
    ("2020 코로나 급락", "2020-02-19", "2020-03-23"),
    ("2022 금리 급등", "2022-01-03", "2022-10-12"),
]


def _at(ser, date, after=True):
    """date 이후(없으면 이전) 첫 종가"""
    if not ser:
        return None
    for d, v in ser:
        if d >= date:
            return d, v
    return ser[-1]


def _ret(ser, d0, d1):
    a, b = _at(ser, d0), _at(ser, d1)
    if not a or not b or a[0] > d1 or a[1] <= 0:
        return None
    return b[1] / a[1] - 1


def _plus(date, days):
    import datetime as dt
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


def _series(conn):
    out = {}
    for k in KEYS:
        s = [(d, v) for d, v in wm.asset_series(conn, k) if v]
        s = [(f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d, v) for d, v in s]
        if len(s) > 60:
            out[k] = s
    return out


def diagnose(conn):
    """지금 하락의 유형 (최근 3개월 자산별 수익으로) → dict(rows, kind, text, down_share)"""
    ser = _series(conn)
    rows = []
    for k in KEYS:
        s = ser.get(k)
        if not s or len(s) < 70:
            continue
        r3 = s[-1][1] / s[-64][1] - 1
        r1 = s[-1][1] / s[-22][1] - 1
        hi = max(v for _, v in s[-252:])
        rows.append(dict(key=k, name=LABEL[k], r1m=r1, r3m=r3, dd=s[-1][1] / hi - 1))
    if len(rows) < 5:
        return None
    g = {r["key"]: r for r in rows}
    down = sum(1 for r in rows if r["r3m"] < 0)
    stocks = g.get("SPY", {}).get("r3m")
    bonds = g.get("IEF.O", {}).get("r3m")
    gold = g.get("GLD", {}).get("r3m")
    usd = g.get("UUP", {}).get("r3m")
    kind, text = "mixed", "뚜렷한 유형으로 나뉘지 않아요."
    if stocks is not None and stocks < -0.03:
        if bonds is not None and bonds < -0.01:
            kind, text = "rate", "주식과 채권이 같이 내리는 ‘금리 상승형’(2022년 같은)에 가까워요. 채권이 방어가 못 되는 유형이라 현금·단기채·달러 쪽이 상대적으로 버텼어요."
        if gold is not None and gold < -0.03 and usd is not None and usd > 0.01:
            kind, text = "liquidity", "금까지 내리고 달러가 오르는 ‘유동성 위기형’(2008년 가을·2020년 3월 같은)에 가까워요. 모든 자산이 현금화되는 구간이라 달러·현금 외엔 방어가 약했어요."
        elif bonds is not None and bonds > 0.01 and kind == "mixed":
            kind, text = "growth", "주식은 내리는데 국채는 오르는 ‘경기 둔화형’이에요. 국채와 방어 섹터가 방어가 되고, 금리 인하 기대가 커지는 유형이에요."
        elif kind == "mixed":
            kind, text = "stock", "주식 중심 하락이에요. 다른 자산군은 아직 크게 무너지지 않았어요."
    elif stocks is not None:
        kind, text = "calm", "미국 주식은 3개월 기준 크게 내리지 않았어요. 국내 하락이 해외와 다른 흐름일 수 있어요."
    return dict(rows=rows, kind=kind, text=text, down=down, n=len(rows))


def episode_table(conn):
    """과거 하락 구간별 자산 수익 (시작→바닥) 과 바닥 후 6개월"""
    ser = _series(conn)
    out = []
    for name, d0, d1 in EPISODES:
        cells = {}
        for k in KEYS:
            s = ser.get(k)
            if not s or s[0][0] > d0:
                cells[k] = None
                continue
            r = _ret(s, d0, d1)
            r2 = _ret(s, d1, _plus(d1, 182))
            cells[k] = (r, r2)
        out.append(dict(name=name, d0=d0, d1=d1, cells=cells))
    return out


def tranche_study(conn, levels=(-0.2, -0.3, -0.4), horizon=250):
    """코스피 과거 기록: 고점 대비 -20%를 처음 넘는 시점마다
    (A) 그날 전액 매수  (B) -20/-30/-40%에 3등분 매수(도달 못 한 분량은 현금)
    → 첫 신호 후 horizon(거래일) 뒤 평가 수익률 비교"""
    ser = vol_limit.kospi(conn)
    cl = [v for _, v in ser]
    n = len(cl)
    if n < 750:
        return None
    peak = cl[0]
    in_ep = False
    eps = []
    i = 0
    while i < n:
        peak = max(peak, cl[i])
        dd = cl[i] / peak - 1
        if not in_ep and dd <= levels[0]:
            in_ep = True
            start = i
            pk = peak
        if in_ep and cl[i] >= pk:      # 고점 회복 → 구간 끝
            in_ep = False
        if in_ep and i == start:
            end = min(n - 1, start + horizon)
            if end - start >= horizon * 0.8:
                wA = cl[end] / cl[start] - 1
                tot, cash = 0.0, 0.0
                for lv in levels:
                    hit = next((j for j in range(start, end + 1) if cl[j] / pk - 1 <= lv), None)
                    if hit is None:
                        cash += 1 / len(levels)
                    else:
                        tot += (1 / len(levels)) * (cl[end] / cl[hit] - 1)
                eps.append(dict(date=ser[start][0], lump=wA, stage=tot, cash=cash, depth=min(cl[j] / pk - 1 for j in range(start, end + 1))))
        i += 1
    return eps or None
