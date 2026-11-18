# -*- coding: utf-8 -*-
"""
입법 단계별 주가 분석 (이벤트 스터디). 과거 법안의 단계 날짜 전후로, 그 법안에 닿는다고 짐작한 기업의 주가가 지수보다 얼마나 더 움직였는지를 평균 내요.

계산
  · 이벤트 = (법안, 기업, 단계 날짜). 단계 날짜 이후 첫 거래일을 D0로 두고, D-21일 종가를 기준으로
      초과수익 경로  CAR_k = (기업 수익률) − (지수 수익률),  k = -20 … +20  (지수: 코스닥 종목은 코스닥, 그 외 코스피)
  · 사전(D-20→D-1) = CAR_{-1} · 사후 10일 = CAR_{+10} − CAR_{-1} · 사후 20일 = CAR_{+20} − CAR_{-1}
  · 같은 기업·단계·날짜는 한 번만 셈. 같은 날 여러 이벤트는 날짜별 평균을 낸 뒤 날짜 수를 표본으로 t값을 내요 (한 날 몰린 법안이 표본을 부풀리지 않게)
  · 우연 검정(대조군): 날짜 묶음(같은 날 이벤트)마다 '무작위 날짜'를 하나 뽑아, 그 묶음의 기업들이 그날 보인 값으로 같은 방식의 평균을 구하기를 되풀이해요.
    같은 날짜 이벤트가 같은 무작위 날짜를 함께 받으므로, 같은 법안에 걸린 여러 종목이 지수 움직임 같은 공통 요인을 공유하는 효과가 대조군에도 그대로 들어가요.
    (종목마다 날짜를 따로 뽑으면 이 공통 요인이 사라져 우연을 너무 쉽게 '우연 밖'으로 판정해요 — 실제로 그 방식은 오탐이 기대치의 약 3배였어요.)
묶음: 단계별 · 단계×연결 확실도(기업명 직접 언급 / 개념·테마로 짐작) · 단계×결과(통과/폐기/심사 중) · 분야별.
꼭 알아야 할 한계 (화면에도 적어요)
  · 법안→기업 연결은 '짐작'이고 테마 구성도 '지금' 기준이에요 (과거에 속했던 종목·상장폐지된 종목이 빠진 생존 편향).
  · 결과(통과/폐기)별 비교는 '결과를 사후에 알고' 나눈 거라서 그 시점의 투자 신호가 아니에요 (선견 편향). 단계 도달 시점에 알 수 있는 정보로만 쓸 수 있는 건 단계별·연결 확실도별 결과예요.
  · 시장 전체 요인·같은 날 몰린 소식·소문에 의한 선반영이 섞여요. 표본이 작은 묶음은 우연일 가능성이 커요.
"""
import bisect
import datetime as dt
import json
import sqlite3
import time

import requests

try:
    import numpy as np
except Exception:                      # pandas가 이미 필요해서 보통 있음
    np = None

PRE, POST = 20, 20                    # 창: D-20 … D+20
STAGES = (("발의", "propose_dt"), ("소관위 회부", "committee_dt"), ("소관위 상정", "cmt_present_dt"),
          ("소관위 처리", "cmt_proc_dt"), ("법사위 처리", "law_proc_dt"), ("본회의 처리", "proc_dt"))
MIN_SCORE = 3.0                       # 이 점수 이상인 후보만 이벤트로 (연결이 느슨한 것 제외)
PX_START = "20150101"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
PRICE_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hist_map (bill_key TEXT, code TEXT, name TEXT, score REAL, tier TEXT, concept TEXT, PRIMARY KEY (bill_key, code));
    CREATE TABLE IF NOT EXISTS study_px (code TEXT, d TEXT, close REAL, PRIMARY KEY (code, d));
    CREATE TABLE IF NOT EXISTS study_stock (code TEXT PRIMARY KEY, name TEXT, market TEXT, px_fetched TEXT, n INTEGER);
    CREATE TABLE IF NOT EXISTS study_result (grp TEXT, gkey TEXT, payload TEXT, computed TEXT, PRIMARY KEY (grp, gkey));
    CREATE TABLE IF NOT EXISTS study_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def outcome(result):
    r = result or ""
    if any(w in r for w in ("폐기", "철회", "부결")):
        return "폐기"
    if any(w in r for w in ("원안가결", "수정가결", "가결")):
        return "통과"
    return "심사 중"


# ---------------- 1) 법안 → 기업 연결 ----------------
def map_bills(db_path, log=print, progress=None):
    """과거 법안 제목으로 영향 기업 후보를 짐작해 hist_map에 저장 (연결이 약한 것은 버림). 테마 자료가 바뀌면 처음부터 다시."""
    import bill_impact
    import theme_map
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        data = theme_map.load(conn)
        if not data["테마"] and not data["업종"]:
            raise RuntimeError("테마·업종 자료가 없어요 (입법 영향 탭에서 '지금 준비하기'를 먼저 눌러 주세요)")
        stamp = "/".join(str(x) for x in theme_map._sig(conn)[1:])
        have = conn.execute("SELECT value FROM study_meta WHERE key='map_stamp'").fetchone()
        if have and have[0] == stamp:
            n = conn.execute("SELECT COUNT(*) FROM hist_map").fetchone()[0]
            return {"건너뜀": "이미 연결함", "연결": n}
        conn.execute("DELETE FROM hist_map")
        rows = conn.execute("SELECT key, name FROM hist_bills WHERE name != ''").fetchall()
        n_bills = kept = 0
        for i, (key, name) in enumerate(rows):
            r = bill_impact.analyze(conn, name, "", top=3, data=data)
            n_bills += 1
            first_concept = r["concepts"][0][0] if r["concepts"] else ""
            for s in r["stocks"]:
                if s["score"] < MIN_SCORE:
                    continue
                tier = "직접 언급" if (s["evidence"] and s["evidence"][0].startswith("법안 문구에 기업명")) else "개념·테마 짐작"
                conn.execute("INSERT OR REPLACE INTO hist_map VALUES (?,?,?,?,?,?)", (key, s["code"], s["name"], s["score"], tier, first_concept))
                kept += 1
            if i % 2000 == 0:
                conn.commit()
                if progress:
                    progress(f"{i}/{len(rows)}")
        conn.execute("INSERT OR REPLACE INTO study_meta VALUES ('map_stamp', ?)", (stamp,))
        conn.commit()
        log(f"법안→기업 연결: 법안 {n_bills}건 중 연결 {kept}쌍")
        return {"법안": n_bills, "연결": kept}
    finally:
        conn.close()


# ---------------- 2) 주가 ----------------
def fetch_px(code, session=None):
    r = (session or requests).get(PRICE_URL.format(code=code), headers=HEADERS, timeout=40,
                                  params={"startDateTime": PX_START, "endDateTime": (dt.date.today() + dt.timedelta(days=1)).strftime("%Y%m%d")})
    r.raise_for_status()
    out = []
    for x in r.json():
        d, c = str(x.get("localDate", "")), x.get("closePrice")
        if len(d) == 8 and c:
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(c)))
    return sorted(out)


def fetch_prices(db_path, log=print, progress=None, session=None, sleep=0.2, max_codes=None):
    """연결된 종목 중 주가가 없는 것을 받음 (한 종목 1~2번 호출). 실패한 종목은 건너뛰고 계속 → {받음, 실패, 전체}"""
    import bill_watch
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        codes = [c for (c,) in conn.execute("SELECT DISTINCT code FROM hist_map")]
        have = {c for (c,) in conn.execute("SELECT code FROM study_stock WHERE n > 0")}
        todo = [c for c in codes if c not in have][:max_codes]
        ok = fail = 0
        for i, code in enumerate(todo):
            try:
                rows = fetch_px(code, session)
                nm, mk = bill_watch.stock_info(code, session)
                conn.executemany("INSERT OR REPLACE INTO study_px VALUES (?,?,?)", [(code, d, c) for d, c in rows])
                conn.execute("INSERT OR REPLACE INTO study_stock VALUES (?,?,?,?,?)", (code, nm or "", mk or "", dt.date.today().isoformat(), len(rows)))
                ok += 1
            except Exception as e:
                fail += 1
                conn.execute("INSERT OR REPLACE INTO study_stock VALUES (?,?,?,?,?)", (code, "", "", dt.date.today().isoformat(), 0))
                if fail <= 3:
                    log(f"주가 {code} 실패: {type(e).__name__}")
            if i % 25 == 0:
                conn.commit()
                if progress:
                    progress(f"{i + 1}/{len(todo)}")
            time.sleep(sleep)
        conn.commit()
        log(f"주가: 받음 {ok} · 실패 {fail} · 대상 {len(todo)}/{len(codes)}")
        return {"받음": ok, "실패": fail, "대상": len(todo), "전체 종목": len(codes)}
    finally:
        conn.close()


# ---------------- 3) 이벤트 스터디 ----------------
def _ord(d):
    return dt.date.fromisoformat(d).toordinal()


def _load_index(conn, sid):
    rows = conn.execute("SELECT period, value FROM macro_series WHERE series_id=? ORDER BY period", (sid,)).fetchall()
    return np.array([_ord(p) for p, _ in rows], dtype=np.int64), np.array([v for _, v in rows], dtype=float)


def _aligned_index(stock_days, idx_days, idx_vals):
    """종목 거래일마다 그날 이하의 마지막 지수값 (지수 기록 이전이면 nan)"""
    pos = np.searchsorted(idx_days, stock_days, side="right") - 1
    out = np.where(pos >= 0, idx_vals[np.clip(pos, 0, None)], np.nan)
    return out


def _stat_arrays(P, I):
    """각 t(D0)에서의 pre = CAR_-1(기준 D-21), post10 = CAR_+10 − CAR_-1, post20: 길이 len(P) 배열 (계산 못 하는 곳은 nan)"""
    n = len(P)
    pre = np.full(n, np.nan)
    p10 = np.full(n, np.nan)
    p20 = np.full(n, np.nan)
    t = np.arange(21, n - POST)
    if len(t):
        sr = lambda a, b: P[a] / P[b] - 1
        ir = lambda a, b: I[a] / I[b] - 1
        pre[t] = sr(t - 1, t - 21) - ir(t - 1, t - 21)
        p10[t] = sr(t + 10, t - 1) - ir(t + 10, t - 1)
        p20[t] = sr(t + 20, t - 1) - ir(t + 20, t - 1)
    return pre, p10, p20


def _path(P, I, t):
    ks = np.arange(t - PRE, t + POST + 1)
    return (P[ks] / P[t - 21] - 1) - (I[ks] / I[t - 21] - 1)


def _clustered(values, dates):
    """날짜별 평균 → 날짜 수를 표본으로 평균·t값. (평균, t값, 날짜 수). values·dates는 numpy 배열"""
    ok = ~np.isnan(values)
    v, d = values[ok], dates[ok]
    if not len(v):
        return None, None, 0
    _, inv = np.unique(d, return_inverse=True)
    m = np.bincount(inv, weights=v) / np.bincount(inv)
    if len(m) < 3:
        return float(m.mean()), None, len(m)
    sd = m.std(ddof=1)
    return float(m.mean()), (float(m.mean() / (sd / np.sqrt(len(m)))) if sd > 0 else None), len(m)


def _cluster_stat(vals, cl, k):
    """날짜 묶음별 평균 → 묶음 평균들의 평균 (관측값 계산과 같은 정의). vals의 nan은 건너뜀"""
    ok = ~np.isnan(vals)
    cnt = np.bincount(cl[ok], minlength=k)
    sm = np.bincount(cl[ok], weights=vals[ok], minlength=k)
    m = cnt > 0
    return float((sm[m] / cnt[m]).mean()) if m.any() else np.nan


def _placebo_p(observed, rows, cl, k, mat, lo, hi, B, rng):
    """대조군 p값: 날짜 묶음(k개)마다 달력에서 무작위 위치를 하나 뽑아(lo~hi), 묶음 안 기업들이 그날 보인 값으로 같은 통계를 B번 구해 |관측값|보다 큰 비율 (+1 보정).
    rows: 이벤트별 종목 행, cl: 이벤트별 묶음 번호, mat[종목 행, 달력 위치]"""
    if observed is None or k < 3:
        return None
    stats = np.empty(B)
    for b in range(B):
        r = rng.integers(lo, hi, size=k)
        stats[b] = _cluster_stat(mat[rows, r[cl]], cl, k)
    stats = stats[~np.isnan(stats)]
    if len(stats) < 50:
        return None
    return float((1 + (np.abs(stats) >= abs(observed)).sum()) / (len(stats) + 1))


def compute(db_path, log=print, B=500, seed=7, progress=None):
    """저장된 법안·연결·주가로 이벤트 스터디를 계산해 study_result에 저장 → {이벤트, 묶음}"""
    if np is None:
        raise RuntimeError("numpy가 필요해요 (pandas와 함께 설치돼 있어야 해요)")
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        idx = {"KOSPI": _load_index(conn, "idx:KOSPI"), "KOSDAQ": _load_index(conn, "idx:KOSDAQ")}
        if not len(idx["KOSPI"][0]):
            raise RuntimeError("코스피 지수 기록이 없어요 (지금 수집 → 시장·정책만을 먼저 돌려 주세요)")
        mk = {c: (m or "") for c, m in conn.execute("SELECT code, market FROM study_stock WHERE n > 0")}
        # 이벤트 모으기: (code, stage) → [(발생일, 법안, tier, outcome, concept)]
        ev_by_code = {}
        sel = ", ".join(c for _, c in STAGES)
        q = (f"SELECT b.key, b.proc_result, {sel}, m.code, m.tier, m.concept FROM hist_bills b JOIN hist_map m ON m.bill_key=b.key")
        n_raw = 0
        for row in conn.execute(q):
            key, res = row[0], row[1]
            dates = row[2:2 + len(STAGES)]
            code, tier, concept = row[2 + len(STAGES):]
            if code not in mk:
                continue
            for (stage, _), d in zip(STAGES, dates):
                if d and d >= "2015-03-01":
                    ev_by_code.setdefault(code, []).append((stage, d, key, tier, outcome(res), concept))
                    n_raw += 1
        # 이벤트를 파이썬 딕셔너리가 아니라 숫자 배열(열별 리스트 → numpy)로 모아 메모리를 아껴요 (수십만 건)
        STG = {s_: i for i, (s_, _) in enumerate(STAGES)}
        TIERS, OUTS, CONCEPTS = {}, {}, {}
        col = {k_: [] for k_ in ("stage", "row", "pos", "t0", "tier", "out", "concept", "pre", "p10", "p20")}
        paths = []
        code_of_row = []
        cal_all = idx["KOSPI"][0]                                      # 달력: 코스피 거래일 (종목별 값을 이 달력 위치에 올림)
        cal = cal_all[cal_all >= _ord("2015-01-01")]
        series = []
        for ci, (code, evs) in enumerate(ev_by_code.items()):
            rows = conn.execute("SELECT d, close FROM study_px WHERE code=? ORDER BY d", (code,)).fetchall()
            if len(rows) < 60:
                continue
            days = np.array([_ord(d) for d, _ in rows], dtype=np.int64)
            P = np.array([c for _, c in rows], dtype=float)
            idays, ivals = idx["KOSDAQ" if mk.get(code) == "KOSDAQ" and len(idx["KOSDAQ"][0]) else "KOSPI"]
            I = _aligned_index(days, idays, ivals)
            pre, p10, p20 = _stat_arrays(P, I)
            cpos = np.searchsorted(cal, days)
            okc = (cpos < len(cal)) & (cal[np.clip(cpos, 0, len(cal) - 1)] == days)      # 달력에 있는 날만
            row = len(series)
            code_of_row.append(code)
            series.append((cpos[okc], pre[okc].astype(np.float32), p10[okc].astype(np.float32)))
            seen = {}
            for stage, d, key, tier, oc, concept in evs:
                o = _ord(d)
                t0 = int(np.searchsorted(days, o, side="left"))
                if t0 < 21 or t0 + POST >= len(days) or np.isnan(pre[t0]) or np.isnan(p10[t0]) or not okc[t0]:
                    continue
                k = (stage, t0)
                if k in seen:                              # 같은 기업·단계·날짜는 한 번 (연결 확실도는 높은 쪽)
                    e = seen[k]
                    if tier == "직접 언급":
                        e[0] = "직접 언급"
                    if e[1] != oc:
                        e[1] = "섞임"
                    continue
                seen[k] = [tier, oc, concept, t0]
            if seen:
                t0s = np.array([v[3] for v in seen.values()], dtype=np.int64)
                ks = list(seen.keys())
                pa = np.arange(-PRE, POST + 1)
                pidx = t0s[:, None] + pa[None, :]
                ref = t0s - 21
                car = (P[pidx] / P[ref][:, None] - 1) - (I[pidx] / I[ref][:, None] - 1)           # 이벤트 × 41
                paths.append(car.astype(np.float32))
                for j, ((stage, t0), (tier, oc, concept, _)) in enumerate(zip(ks, seen.values())):
                    col["stage"].append(STG[stage]); col["row"].append(row); col["pos"].append(int(cpos[t0])); col["t0"].append(int(days[t0]))
                    col["tier"].append(TIERS.setdefault(tier, len(TIERS))); col["out"].append(OUTS.setdefault(oc, len(OUTS)))
                    col["concept"].append(CONCEPTS.setdefault(concept or "", len(CONCEPTS)))
                    col["pre"].append(pre[t0]); col["p10"].append(p10[t0]); col["p20"].append(p20[t0] if not np.isnan(p20[t0]) else np.nan)
            if progress and ci % 100 == 0:
                progress(f"{ci}/{len(ev_by_code)} 종목")
        n_ev = len(col["stage"])
        A = {k_: np.array(v_) for k_, v_ in col.items()}
        del col
        PATHS = np.concatenate(paths, axis=0) if paths else np.empty((0, PRE + POST + 1), dtype=np.float32)
        del paths
        # 달력 행렬: 대조군에서 '그날 그 종목이 보인 값'을 바로 꺼내려고 (종목 × 달력 위치)
        mat_pre = np.full((len(series), len(cal)), np.nan, dtype=np.float32)
        mat_p10 = np.full((len(series), len(cal)), np.nan, dtype=np.float32)
        for r_, (cp, a_, b_) in enumerate(series):
            mat_pre[r_, cp] = a_
            mat_p10[r_, cp] = b_
        lo_pos, hi_pos = 25, len(cal) - 25
        log(f"이벤트: 원자료 {n_raw} → 계산 가능 {n_ev}건")
        rng = np.random.default_rng(seed)
        inv = lambda d_: {v: k for k, v in d_.items()}
        TIER_N, OUT_N, CON_N = inv(TIERS), inv(OUTS), inv(CONCEPTS)
        stage_names = [s_ for s_, _ in STAGES]
        groups = {}

        def add(grp, gkey, i):
            groups.setdefault((grp, gkey), []).append(i)
        for i in range(n_ev):
            st_ = stage_names[A["stage"][i]]
            tier_, out_, con_ = TIER_N[A["tier"][i]], OUT_N[A["out"][i]], CON_N[A["concept"][i]]
            add("단계", st_, i)
            add("단계×연결", f"{st_}|{tier_}", i)
            add("단계×결과", f"{st_}|{out_}", i)
            if con_ and st_ in ("발의", "소관위 처리", "본회의 처리"):
                add("분야", f"{st_}|{con_}", i)
        conn.execute("DELETE FROM study_result")
        today = dt.date.today().isoformat()
        stored = 0
        for (grp, gkey), ii in groups.items():
            ii = np.array(ii, dtype=np.int64)
            dates = A["t0"][ii]
            n_dates = int(len(np.unique(dates)))
            if len(ii) < 15 or n_dates < 8:
                continue
            pre_m, pre_t, _ = _clustered(A["pre"][ii], dates)
            p10_m, p10_t, _ = _clustered(A["p10"][ii], dates)
            p20_m, p20_t, _ = _clustered(A["p20"][ii], dates)
            rows_ = A["row"][ii]
            _, cl_ = np.unique(dates, return_inverse=True)
            kk = int(cl_.max()) + 1
            Bn = B if len(ii) <= 20000 else max(100, B // 4)
            n_stocks = int(len(np.unique(rows_)))
            payload = dict(
                n_events=int(len(ii)), n_dates=n_dates, n_stocks=n_stocks,
                path=[round(float(x) * 100, 3) for x in PATHS[ii].mean(axis=0)],
                pre=None if pre_m is None else round(pre_m * 100, 3), pre_t=None if pre_t is None else round(pre_t, 2),
                post10=None if p10_m is None else round(p10_m * 100, 3), post10_t=None if p10_t is None else round(p10_t, 2),
                post20=None if p20_m is None else round(p20_m * 100, 3), post20_t=None if p20_t is None else round(p20_t, 2),
                pre_p=_placebo_p(pre_m, rows_, cl_, kk, mat_pre, lo_pos, hi_pos, Bn, rng), post10_p=_placebo_p(p10_m, rows_, cl_, kk, mat_p10, lo_pos, hi_pos, Bn, rng))
            conn.execute("INSERT OR REPLACE INTO study_result VALUES (?,?,?,?)", (grp, gkey, json.dumps(payload, ensure_ascii=False), today))
            stored += 1
        conn.execute("INSERT OR REPLACE INTO study_meta VALUES ('computed', ?)", (dt.datetime.now().strftime("%Y-%m-%d %H:%M"),))
        conn.execute("INSERT OR REPLACE INTO study_meta VALUES ('n_events', ?)", (str(n_ev),))
        conn.commit()
        log(f"이벤트 스터디: 이벤트 {n_ev}건 · 저장된 묶음 {stored}")
        return {"이벤트": n_ev, "묶음": stored}
    finally:
        conn.close()


def results(conn):
    """화면용 → {(grp, gkey): payload}, 메타"""
    out = {}
    try:
        for grp, gkey, payload in conn.execute("SELECT grp, gkey, payload FROM study_result"):
            out[(grp, gkey)] = json.loads(payload)
        meta = dict(conn.execute("SELECT key, value FROM study_meta").fetchall())
    except sqlite3.OperationalError:
        return {}, {}
    return out, meta


# ---------------- 한 번에 돌리기 (화면 버튼 → 별도 프로세스) ----------------
def run_all(db_path, assembly_key, status_path, log=print):
    """과거 법안 받기 → 법안·기업 연결 → 주가 받기 → 이벤트 스터디 계산을 차례로. 단계마다 진행·오류를 status_path(JSON)에 적음.
    각 단계는 따로 try 해서 한 단계가 실패해도 이유를 화면에 보여 주고, 앞 단계 결과가 있으면 다음 단계는 계속 시도해요."""
    import os
    import bill_history
    import stock_policy as sp
    names = ["과거 법안 받기", "법안 → 기업 연결", "종목 주가 받기", "분석 계산"]
    steps = [dict(name=n, state="wait", msg="") for n in names]
    st = dict(running=True, pid=os.getpid(), started=time.strftime("%Y-%m-%d %H:%M:%S"), finished="", steps=steps)
    last = {"t": 0.0}

    def save():
        try:
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        except OSError:
            pass

    def prog(i):
        def _p(msg):
            steps[i]["msg"] = msg
            if time.time() - last["t"] > 3:
                last["t"] = time.time()
                save()
        return _p

    def run(i, fn):
        steps[i]["state"] = "run"
        save()
        try:
            res = fn(prog(i))
            steps[i]["state"], steps[i]["msg"] = "ok", (res if isinstance(res, str) else json.dumps(res, ensure_ascii=False))[:300]
        except Exception as e:
            steps[i]["state"], steps[i]["msg"] = "fail", f"{type(e).__name__}: {str(e)[:280]}"
        save()
    save()

    def hist(p):
        if not assembly_key:
            raise RuntimeError("열린국회정보 키(ASSEMBLY_API_KEY)가 없어요")
        api = sp.Client("", "", assembly_key)
        r = bill_history.sync(db_path, api, max_pages=900, log=log, progress=p)
        if not any(v.get("전체") for v in r.values()):
            raise RuntimeError("과거 법안을 한 건도 못 받았어요 — 설정 → 법안·종목 확인의 '과거 국회 법안' 줄을 확인해 주세요")
        return r
    run(0, hist)
    run(1, lambda p: map_bills(db_path, log=log, progress=p))
    run(2, lambda p: fetch_prices(db_path, log=log, progress=p))
    run(3, lambda p: compute(db_path, log=log, progress=p))
    st["running"] = False
    st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    return st
