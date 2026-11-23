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
    conn.commit()
    return msgs
