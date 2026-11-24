"""시장 경보: 코스피 낙폭 단계와 실제 변동성 단계가 바뀌면 알려 준다 (상태가 바뀔 때만, 같은 상태는 다시 알리지 않음)."""
import datetime as dt
import sqlite3

import vol_limit

DD_STEPS = (-0.10, -0.20, -0.30, -0.40, -0.50)
VOL_LEVELS = ((0.25, "평상시"), (0.35, "큰 변동성"), (0.50, "매우 큰 변동성"), (9.0, "위기급"))


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS mkt_alert_state (key TEXT PRIMARY KEY, state INTEGER, text TEXT, updated TEXT)")


def dd_state(conn):
    ser = vol_limit.kospi(conn)
    if len(ser) < 250:
        return None
    cl = [v for _, v in ser]
    hi = max(cl[-250:])
    dd = cl[-1] / hi - 1
    k = sum(1 for s in DD_STEPS if dd <= s)
    return k, dd, ser[-1][0]


def vol_state(conn):
    r = vol_limit.compute(conn)
    if not r or not r.get("v20"):
        return None
    v = r["v20"]
    k = next(i for i, (lim, _) in enumerate(VOL_LEVELS) if v < lim)
    return k, v, r["limit_mult"]


import os

NPS = dict(fund=1684.0, dom=419.0, tgt=0.208, saa=0.06, base_date="20260731")


def _env_nps():
    """DART_NPS='기금조,국내주식조,목표%,허용%p,기준일' 로 기본값을 바꿀 수 있다 (예: 1700,430,20.8,6,20261031)"""
    raw = os.environ.get("DART_NPS", "").strip()
    if not raw:
        return
    try:
        a = [x.strip() for x in raw.split(",")]
        NPS.update(fund=float(a[0]), dom=float(a[1]), tgt=float(a[2]) / 100, saa=float(a[3]) / 100)
        if len(a) > 4 and a[4]:
            NPS["base_date"] = a[4]
    except (ValueError, IndexError):
        pass


_env_nps()


def nps_levels(base):
    """코스피 수준 → 국민연금 국내주식 비중 구간 경계 (다른 자산은 그대로라는 단순 가정)"""
    oth = NPS["fund"] - NPS["dom"]
    lv = lambda w: base * (w / (1 - w) * oth) / NPS["dom"]
    return dict(target=lv(NPS["tgt"]), upper=lv(NPS["tgt"] + NPS["saa"]), lower=lv(NPS["tgt"] - NPS["saa"]))


def nps_state(conn):
    ser = vol_limit.kospi(conn)
    base = next((v for d, v in reversed(ser) if d <= NPS["base_date"]), None)
    if not ser or not base:
        return None
    lv = nps_levels(base)
    cur = ser[-1][1]
    k = (1 if cur < lv["target"] else 0) + (1 if cur < lv["lower"] else 0)
    return k, cur, lv


def check(conn):
    """바뀐 항목의 (제목, 본문) 목록. 처음 보는 상태는 기록만 하고 알리지 않는다 (설치 직후 알림 방지)."""
    ensure(conn)
    msgs = []
    today = dt.date.today().isoformat()

    def step(key, k, text_up, text_down):
        row = conn.execute("SELECT state FROM mkt_alert_state WHERE key=?", (key,)).fetchone()
        if row is not None and row[0] != k:
            msgs.append(text_up if k > row[0] else text_down)
        conn.execute("INSERT OR REPLACE INTO mkt_alert_state VALUES (?,?,?,?)", (key, k, "", today))

    d = dd_state(conn)
    if d:
        k, dd, day = d
        label = "고점 대비 하락 없음" if k == 0 else f"고점 대비 {DD_STEPS[k - 1]:.0%} 이하"
        step("dd", k, ("코스피 낙폭 단계 악화", f"1년 고점 대비 {dd:+.1%} ({label}) · 기준 {day}"),
             ("코스피 낙폭 단계 개선", f"1년 고점 대비 {dd:+.1%} ({label}) · 기준 {day}"))
    v = vol_state(conn)
    if v:
        k, vol, mult = v
        nm = VOL_LEVELS[k][1]
        txt = f"최근 20거래일 변동성 {vol:.0%} ({nm}) · 평상시 한도의 {mult:.0%}"
        step("vol", k, ("코스피 변동성 단계 상승", txt), ("코스피 변동성 단계 하락", txt))
    n = nps_state(conn)
    if n:
        k, cur, lv = n
        names = ("목표비중 위", "목표비중 아래 (재량 매수 가능)", "허용범위 하단 아래 (의무매수 구간 후보)")
        txt = f"코스피 {cur:,.0f} · 국민연금 국내주식 비중 {names[k]} (목표 경계 {lv['target']:,.0f} · 하단 경계 {lv['lower']:,.0f}, 단순 추정)"
        step("nps", k, ("국민연금 리밸런싱 구간 진입", txt), ("국민연금 리밸런싱 구간 이탈", txt))
    conn.commit()
    return msgs


def brief(conn):
    """하루 한 번 보낼 시장 요약 (줄 목록). 자료가 없는 항목은 뺀다."""
    out = []
    d = dd_state(conn)
    if d:
        out.append(f"코스피 1년 고점 대비 {d[1]:+.1%} (기준 {d[2]})")
    v = vol_state(conn)
    if v:
        out.append(f"변동성 {v[1]:.0%} ({VOL_LEVELS[v[0]][1]}) · 평상시 한도의 {v[2]:.0%}")
    n = nps_state(conn)
    if n:
        k, cur, lv = n
        out.append(f"국민연금 목표 경계 {lv['target']:,.0f} · 하단 경계 {lv['lower']:,.0f} (코스피 {cur:,.0f})")
    try:
        import crash_response
        dg = crash_response.diagnose(conn)
        if dg:
            out.append("하락 유형: " + dg["text"].split("’")[0].split("‘")[-1] if "‘" in dg["text"] else "하락 유형: " + dg["text"][:30])
    except Exception:
        pass
    return out
