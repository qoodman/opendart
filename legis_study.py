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
import re
import sqlite3
import time

import requests

try:
    import numpy as np
except Exception:                      # pandas가 이미 필요해서 보통 있음
    np = None

PRE, POST = 20, 20                    # 창: D-20 … D+20
STAGES = (("발의", "propose_dt"), ("소관위 회부", "committee_dt"), ("소관위 상정", "cmt_present_dt"),
          ("소관위 처리", "cmt_proc_dt"), ("법사위 처리", "law_proc_dt"), ("본회의 처리", "proc_dt"),
          ("공포", "promul_dt"), ("시행", "effective_dt"))
MIN_NOTICE_DAYS = 14               # '시행' 이벤트는 공포 뒤 이만큼 지나서 시행되는 것만 (즉시 시행은 사전에 알려진 시행일이 아니라서 뺌)
STEPS = ("과거 법안 받기", "공포·시행일 확인 (법제처)", "법안 → 기업 연결", "과거 제안이유 받기", "종목 주가 받기", "분석 계산")
MIN_SCORE = 3.0                       # 이 점수 이상인 후보만 이벤트로 (연결이 느슨한 것 제외)
PX_START = "20150101"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
PRICE_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"


def ensure(conn):
    import bill_history
    bill_history.ensure(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hist_map (bill_key TEXT, code TEXT, name TEXT, score REAL, tier TEXT, concept TEXT, PRIMARY KEY (bill_key, code));
    CREATE TABLE IF NOT EXISTS study_px (code TEXT, d TEXT, close REAL, PRIMARY KEY (code, d));
    CREATE TABLE IF NOT EXISTS study_stock (code TEXT PRIMARY KEY, name TEXT, market TEXT, px_fetched TEXT, n INTEGER);
    CREATE TABLE IF NOT EXISTS study_result (grp TEXT, gkey TEXT, payload TEXT, computed TEXT, PRIMARY KEY (grp, gkey));
    CREATE TABLE IF NOT EXISTS study_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    for tbl, col in (("study_px", "vol REAL"), ("study_stock", "vol_ok INTEGER")):        # 거래량 (거래대금 규모·사전 거래량 급증 조건용)
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass


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


# ---------------- 1.5) 공포일·시행일 (법제처 연혁) ----------------
def find_promulgations(db_path, law_oc, log=print, progress=None, session=None):
    """통과한 과거 법안의 법을 법제처 연혁에서 찾아, 의결 뒤 90일 안의 가장 이른 공포일·시행일을 hist_bills에 적음.
    법 이름마다 한 번만 조회하고(연혁은 법제처 연혁 캐시에 저장, 30일), 이어받기 가능 → {법 수, 공포 확인, 시행 확인}"""
    import legis as lg
    import stock_policy as sp
    if not law_oc:
        raise RuntimeError("법제처 키(LAW_OC)가 없어요 — 공포·시행 단계는 이 키가 있어야 분석돼요 (다른 단계는 계속 분석해요)")
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        lg.ensure(conn)
        api = sp.Client("", law_oc, "")
        rows = conn.execute("SELECT key, name, proc_dt, proc_result FROM hist_bills WHERE proc_dt != '' AND (promul_dt IS NULL OR promul_dt = '')").fetchall()
        rows = [r for r in rows if outcome(r[3]) == "통과"]
        by_law = {}
        for key, name, proc_dt, _ in rows:
            law = lg.law_name(name)
            if law:
                by_law.setdefault(law, []).append((key, proc_dt))
        n_law = n_p = n_e = fail = 0
        for i, (law, items) in enumerate(by_law.items()):
            try:
                vs = lg.fetch_versions(conn, api, law, max_age_days=30)
            except Exception as e:
                fail += 1
                if fail <= 3:
                    log(f"연혁 {law[:14]} 실패: {type(e).__name__}")
                continue
            n_law += 1
            for key, proc_dt in items:
                v = lg.pick_version(vs, proc_dt)
                if v:
                    conn.execute("UPDATE hist_bills SET promul_dt=?, effective_dt=? WHERE key=?", (v["promulgated"], v.get("effective") or "", key))
                    n_p += 1
                    n_e += 1 if v.get("effective") else 0
            if i % 20 == 0:
                conn.commit()
                if progress:
                    progress(f"{i + 1}/{len(by_law)}법")
            time.sleep(0.3)
        conn.commit()
        log(f"공포·시행일: 법 {n_law}개 조회 · 공포 {n_p}건 · 시행 {n_e}건 (실패 {fail})")
        return {"법": len(by_law), "조회": n_law, "공포": n_p, "시행": n_e, "실패": fail}
    finally:
        conn.close()


# ---------------- 2) 주가 ----------------
def fetch_px(code, session=None):
    """[(날짜, 종가, 거래량)] 오래된 것부터 (2015년~). 거래량은 네이버 일봉의 accumulatedTradingVolume"""
    r = (session or requests).get(PRICE_URL.format(code=code), headers=HEADERS, timeout=40,
                                  params={"startDateTime": PX_START, "endDateTime": (dt.date.today() + dt.timedelta(days=1)).strftime("%Y%m%d")})
    r.raise_for_status()
    out = []
    for x in r.json():
        d, c = str(x.get("localDate", "")), x.get("closePrice")
        if len(d) == 8 and c:
            v = x.get("accumulatedTradingVolume")
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(c), float(v) if v not in (None, "") else None))
    return sorted(out)


def fetch_prices(db_path, log=print, progress=None, session=None, sleep=0.2, max_codes=None):
    """연결된 종목 중 주가(와 거래량)가 없는 것을 받음 (한 종목 1~2번 호출). 실패한 종목은 건너뛰고 계속 → {받음, 실패, 전체}"""
    import bill_watch
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        codes = [c for (c,) in conn.execute("SELECT DISTINCT code FROM hist_map")]
        have = {c for (c,) in conn.execute("SELECT code FROM study_stock WHERE n > 0 AND COALESCE(vol_ok, 0) = 1")}      # 거래량까지 받은 종목은 건너뜀
        todo = [c for c in codes if c not in have][:max_codes]
        ok = fail = 0
        for i, code in enumerate(todo):
            try:
                rows = fetch_px(code, session)
                nm, mk = bill_watch.stock_info(code, session)
                conn.executemany("INSERT OR REPLACE INTO study_px (code, d, close, vol) VALUES (?,?,?,?)", [(code, d, c, v) for d, c, v in rows])
                has_vol = 1 if any(v is not None for _, _, v in rows) else 0
                conn.execute("INSERT OR REPLACE INTO study_stock (code, name, market, px_fetched, n, vol_ok) VALUES (?,?,?,?,?,?)",
                             (code, nm or "", mk or "", dt.date.today().isoformat(), len(rows), has_vol))
                ok += 1
            except Exception as e:
                fail += 1
                conn.execute("INSERT OR REPLACE INTO study_stock (code, name, market, px_fetched, n, vol_ok) VALUES (?,?,?,?,?,0)", (code, "", "", dt.date.today().isoformat(), 0))
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


# ---------------- 2.5) 과거 법안 제안이유 (방향 판정용) ----------------
def fetch_hist_texts(db_path, limit=1500, log=print, progress=None, sleep=0.3, fetcher=None):
    """기업에 연결된 과거 법안 중 제안이유가 없는 것을 최근·진행 단계 순으로 limit건 받아 bill_text에 저장 (수혜/부담 방향 판정에 씀).
    연속 5번 실패하면 멈춰요. 이어받기 가능 → {성공, 실패, 대상}"""
    import bill_watch
    fetcher = fetcher or bill_watch.fetch_text
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        bill_watch.ensure(conn)
        have = {k for (k,) in conn.execute("SELECT bill_key FROM bill_text")}
        rows = conn.execute("SELECT b.key, b.url, COALESCE(NULLIF(b.proc_dt,''), NULLIF(b.cmt_proc_dt,''), b.propose_dt) d FROM hist_bills b "
                            "WHERE b.url != '' AND b.key IN (SELECT DISTINCT bill_key FROM hist_map) ORDER BY (b.proc_dt != '') DESC, d DESC").fetchall()
        todo = [(k, u) for k, u, _ in rows if k not in have][:limit]
        ok = fail = streak = 0
        for i, (key, url) in enumerate(todo):
            try:
                txt = fetcher(url)
                streak = 0
            except Exception:
                fail += 1
                streak += 1
                if streak >= 5:
                    log("과거 제안이유: 5번 연속 실패해서 멈췄어요")
                    break
                continue
            conn.execute("INSERT OR REPLACE INTO bill_text VALUES (?,?,?)", (key, dt.date.today().isoformat(), txt))
            ok += 1
            if ok % 25 == 0:
                conn.commit()
                if progress:
                    progress(f"{i + 1}/{len(todo)}")
            time.sleep(sleep)
        conn.commit()
        log(f"과거 제안이유: 대상 {len(todo)} · 성공 {ok} · 실패 {fail}")
        if todo and not ok and fail:
            raise RuntimeError(f"제안이유를 한 건도 못 받았어요 ({fail}건 실패) — 국회 페이지 접속 문제예요. 방향(수혜/부담) 분석은 제목 단어로만 해서 범위가 좁아져요")
        return {"성공": ok, "실패": fail, "대상": len(todo)}
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


SUP_WORDS = ("지원", "육성", "진흥", "촉진", "활성화", "감면", "면제", "공제", "보조", "융자", "장려", "특례", "비과세", "완화")
BUR_WORDS = ("처벌", "과징금", "과태료", "벌금", "금지", "제재", "규제", "의무", "환수", "상한", "제한", "취소", "강화")


def title_sign(name):
    """법안 제목의 단어로 수혜(+1)/부담(-1)/모름(0). 법 이름 자체('조세특례제한법'의 '특례'·'제한')는 빼고 부제목 같은 나머지만 봐요 — 제목만으로는 거칠어요."""
    import legis as lg
    n = re.sub(r"\s+", "", name or "")
    law = re.sub(r"\s+", "", lg.law_name(name) or "")
    if law:
        n = n.replace(law, "", 1)
    sup = sum(1 for w in SUP_WORDS if w in n)
    bur = sum(1 for w in BUR_WORDS if w in n)
    return 1 if sup and not bur else -1 if bur and not sup else 0


def bill_sign(name, text):
    """제안이유 본문이 있으면 본문 단어(legis.nature_hint), 없으면 제목 단어로 수혜(+1)/부담(-1)/모름(0)"""
    import legis as lg
    if text and len(text.strip()) >= 30:
        k = lg.nature_hint("", text)["kind"]
        return 1 if k == "support" else -1 if k == "burden" else 0
    return title_sign(name)


def sponsors(proposer):
    """공동발의 인원: 정부안 0, '… 등 10인' → 10, 이름 하나 → 1, 모르면 -1"""
    s = proposer or ""
    if not s.strip():
        return -1
    if "정부" in s:
        return 0
    m = re.search(r"(\d+)\s*인", s)
    return int(m.group(1)) if m else 1


def _liq_arrays(P, vol):
    """D0 기준 미리 알 수 있는 값: 거래대금 규모(D-80…D-21 평균 종가×거래량)와 사전 거래량 급증(D-5…D-1 평균 ÷ D-80…D-21 평균). 거래량이 없으면 nan"""
    n = len(P)
    liq = np.full(n, np.nan)
    surge = np.full(n, np.nan)
    if vol is None or np.all(np.isnan(vol)) or n < 90:
        return liq, surge
    v = np.nan_to_num(vol, nan=0.0)
    cs = np.concatenate([[0.0], np.cumsum(P * v)])
    cv = np.concatenate([[0.0], np.cumsum(v)])
    t = np.arange(81, n)
    liq[t] = (cs[t - 20] - cs[t - 80]) / 60.0
    base = (cv[t - 20] - cv[t - 80]) / 60.0
    recent = (cv[t] - cv[t - 5]) / 5.0
    with np.errstate(divide="ignore", invalid="ignore"):
        surge[t] = np.where(base > 0, recent / base, np.nan)
    return liq, surge


def _p_two(draws, obs):
    d = draws[~np.isnan(draws)]
    if obs is None or len(d) < 50:
        return None
    return float((1 + (np.abs(d) >= abs(obs)).sum()) / (len(d) + 1))


def _p_up(draws, obs):
    d = draws[~np.isnan(draws)]
    if obs is None or len(d) < 50:
        return None
    return float((1 + (d >= obs).sum()) / (len(d) + 1))


def _abs_threshold(mat, rows, lo, hi, rng, q=0.9):
    sub = mat[np.unique(rows)][:, lo:hi].ravel()
    sub = sub[~np.isnan(sub)]
    if len(sub) > 300000:
        sub = rng.choice(sub, 300000, replace=False)
    return float(np.quantile(np.abs(sub), q)) if len(sub) else None


def _suite(vals, sign, rows, cl, k, mat, lo, hi, B, rng, want_tail=True):
    """사후 10일 값(vals)에 대한 한 묶음 통계: 부호 있는 평균(우연 p), 움직임 크기(|값| 평균을 평소와 비교한 배율·p), 큰 움직임 비율(평소 상위 10% 크기 이상인 비율), 방향 보정 평균.
    대조군은 날짜 묶음마다 무작위 달력 위치를 하나 뽑아 그날 같은 종목들이 보인 값을 써요 (같은 날 이벤트는 같은 무작위 날짜를 함께 받음)."""
    obs = dict(sig=_cluster_stat(vals, cl, k), abs=_cluster_stat(np.abs(vals), cl, k))
    thr = _abs_threshold(mat, rows, lo, hi, rng) if want_tail else None
    a = np.abs(vals)
    if thr is not None:
        obs["tail"] = _cluster_stat(np.where(np.isnan(a), np.nan, (a >= thr).astype(np.float64)), cl, k)
    sg = None
    if sign is not None and np.any(sign != 0):
        sg = np.where(sign != 0, sign.astype(np.float64), np.nan)
        obs["al"] = _cluster_stat(vals * sg, cl, k)
    d_sig, d_abs = np.empty(B), np.empty(B)
    d_tail = np.empty(B) if thr is not None else None
    d_al = np.empty(B) if sg is not None else None
    for b in range(B):
        r = rng.integers(lo, hi, size=k)
        v = mat[rows, r[cl]].astype(np.float64)
        av = np.abs(v)
        d_sig[b] = _cluster_stat(v, cl, k)
        d_abs[b] = _cluster_stat(av, cl, k)
        if d_tail is not None:
            d_tail[b] = _cluster_stat(np.where(np.isnan(av), np.nan, (av >= thr).astype(np.float64)), cl, k)
        if d_al is not None:
            d_al[b] = _cluster_stat(v * sg, cl, k)
    out = dict(post10_p=_p_two(d_sig, obs["sig"]))
    base_abs = float(np.nanmean(d_abs))
    out.update(abs_mean=None if np.isnan(obs["abs"]) else round(obs["abs"] * 100, 3), abs_base=round(base_abs * 100, 3),
               abs_ratio=round(obs["abs"] / base_abs, 3) if base_abs > 0 and not np.isnan(obs["abs"]) else None, abs_p=_p_up(d_abs, obs["abs"]))
    if thr is not None:
        te = float(np.nanmean(d_tail))
        out.update(tail=None if np.isnan(obs["tail"]) else round(obs["tail"], 4), tail_exp=round(te, 4), tail_p=_p_up(d_tail, obs["tail"]), tail_thr=round(thr * 100, 2))
    if d_al is not None:
        n_al = int(np.sum(~np.isnan(vals * sg)))
        out.update(al_n=n_al, al_post10=None if np.isnan(obs["al"]) else round(obs["al"] * 100, 3), al_p=_p_two(d_al, obs["al"]))
    return out


def _abs_ratio(vals, rows, cl, k, mat_abs, lo, hi, B, rng):
    """특징 분석용 가벼운 버전: (움직임 크기 배율, 우연 p). 대조군도 같은 방식(날짜 묶음별 무작위 위치)"""
    if k < 3:
        return None, None
    obs = _cluster_stat(np.abs(vals), cl, k)
    d = np.empty(B)
    for b in range(B):
        r = rng.integers(lo, hi, size=k)
        d[b] = _cluster_stat(mat_abs[rows, r[cl]].astype(np.float64), cl, k)
    base = float(np.nanmean(d))
    if np.isnan(obs) or not base > 0:
        return None, None
    return round(obs / base, 3), _p_up(d, obs)


STAGE_SETS = (("발의", ("발의",)), ("심사", ("소관위 회부", "소관위 상정", "소관위 처리", "법사위 처리")), ("확정", ("본회의 처리", "공포", "시행")))
TRAIN_FRAC = 0.6                         # 이벤트 날짜의 앞 60%는 학습, 뒤 40%는 검증
# '강하게 반응하는 조건' 판정 기준: 학습 기간에서 찾고(p·배율), 검증 기간에서 재현돼야 '재현됨'. 값은 무효과 합성 세계의 오탐률을 직접 재서 정했어요.
TRAIN_MIN_RATIO, TRAIN_P = 1.12, 0.01
TEST_MIN_RATIO, TEST_P = 1.05, 0.05


def compute(db_path, log=print, B=500, seed=7, progress=None):
    """저장된 법안·연결·주가로 이벤트 스터디를 계산해 study_result에 저장 → {이벤트, 묶음}"""
    if np is None:
        raise RuntimeError("numpy가 필요해요 (pandas와 함께 설치돼 있어야 해요)")
    import theme_map
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        idx = {"KOSPI": _load_index(conn, "idx:KOSPI"), "KOSDAQ": _load_index(conn, "idx:KOSDAQ")}
        if not len(idx["KOSPI"][0]):
            raise RuntimeError("코스피 지수 기록이 없어요 (지금 수집 → 시장·정책만을 먼저 돌려 주세요)")
        mk = {c: (m or "") for c, m in conn.execute("SELECT code, market FROM study_stock WHERE n > 0")}
        try:
            texts = {k_: t_ for k_, t_ in conn.execute("SELECT bill_key, text FROM bill_text WHERE text != ''") if k_.startswith("h:")}
        except sqlite3.OperationalError:
            texts = {}
        names_by_code = {c: n for c, n in conn.execute("SELECT code, name FROM study_stock")}
        tdata = theme_map.load(conn)
        n_theme = {}
        for _, codes_ in tdata["테마"].items():
            for c_ in codes_:
                n_theme[c_] = n_theme.get(c_, 0) + 1
        # 이벤트 모으기: code → [(단계, 날짜, 법안키, tier, outcome, concept, score, sign, sponsors)]
        ev_by_code = {}
        sel = ", ".join(c for _, c in STAGES)
        q = (f"SELECT b.key, b.proc_result, b.proposer, b.name, {sel}, m.code, m.tier, m.concept, m.score FROM hist_bills b JOIN hist_map m ON m.bill_key=b.key")
        n_raw = 0
        bsign, bspon, bname = {}, {}, {}
        nst = len(STAGES)
        for row in conn.execute(q):
            key, res, prop, bnm = row[0], row[1], row[2], row[3]
            dates = row[4:4 + nst]
            code, tier, concept, score = row[4 + nst:]
            if code not in mk:
                continue
            if key not in bsign:
                bsign[key] = bill_sign(bnm, texts.get(key, ""))
                bspon[key] = sponsors(prop)
                bname[key] = bnm
            dmap = {st_: dd for (st_, _), dd in zip(STAGES, dates)}
            for (stage, _), d in zip(STAGES, dates):
                if stage == "시행" and d and dmap.get("공포"):          # 즉시 시행은 제외
                    try:
                        if (dt.date.fromisoformat(d) - dt.date.fromisoformat(dmap["공포"])).days < MIN_NOTICE_DAYS:
                            continue
                    except ValueError:
                        continue
                if d and d >= "2015-03-01":
                    ev_by_code.setdefault(code, []).append((stage, d, key, tier, outcome(res), concept, float(score or 0), bsign[key], bspon[key]))
                    n_raw += 1
        STG = {s_: i for i, (s_, _) in enumerate(STAGES)}
        TIERS, OUTS, CONCEPTS = {}, {}, {}
        bill_keys, bill_ix = [], {}
        col = {k_: [] for k_ in ("stage", "row", "pos", "t0", "tier", "out", "concept", "pre", "p10", "p20", "bill", "score", "sign", "spon", "liq", "surge", "ntheme")}
        paths, code_of_row, series = [], [], []
        cal_all = idx["KOSPI"][0]
        cal = cal_all[cal_all >= _ord("2015-01-01")]
        for ci, (code, evs) in enumerate(ev_by_code.items()):
            rows = conn.execute("SELECT d, close, vol FROM study_px WHERE code=? ORDER BY d", (code,)).fetchall()
            if len(rows) < 60:
                continue
            days = np.array([_ord(d) for d, _, _ in rows], dtype=np.int64)
            P = np.array([c for _, c, _ in rows], dtype=float)
            vol = np.array([np.nan if v is None else v for _, _, v in rows], dtype=float)
            idays, ivals = idx["KOSDAQ" if mk.get(code) == "KOSDAQ" and len(idx["KOSDAQ"][0]) else "KOSPI"]
            I = _aligned_index(days, idays, ivals)
            pre, p10, p20 = _stat_arrays(P, I)
            liq, surge = _liq_arrays(P, vol)
            cpos = np.searchsorted(cal, days)
            okc = (cpos < len(cal)) & (cal[np.clip(cpos, 0, len(cal) - 1)] == days)
            row = len(series)
            code_of_row.append(code)
            series.append((cpos[okc], pre[okc].astype(np.float32), p10[okc].astype(np.float32)))
            seen = {}
            for stage, d, key, tier, oc, concept, score, sgn, spn in evs:
                o = _ord(d)
                t0 = int(np.searchsorted(days, o, side="left"))
                if t0 < 21 or t0 + POST >= len(days) or np.isnan(pre[t0]) or np.isnan(p10[t0]) or not okc[t0]:
                    continue
                k = (stage, t0)
                if k in seen:                              # 같은 기업·단계·날짜는 한 번 (연결 확실도는 높은 쪽, 방향이 엇갈리면 모름)
                    e = seen[k]
                    if tier == "직접 언급":
                        e[0] = "직접 언급"
                    if e[1] != oc:
                        e[1] = "섞임"
                    e[5] = max(e[5], score)
                    if e[6] != sgn:
                        e[6] = 0
                    continue
                seen[k] = [tier, oc, concept, t0, key, score, sgn, spn]
            if seen:
                t0s = np.array([v[3] for v in seen.values()], dtype=np.int64)
                ks = list(seen.keys())
                pa = np.arange(-PRE, POST + 1)
                pidx = t0s[:, None] + pa[None, :]
                ref = t0s - 21
                car = (P[pidx] / P[ref][:, None] - 1) - (I[pidx] / I[ref][:, None] - 1)
                paths.append(car.astype(np.float32))
                for (stage, t0), (tier, oc, concept, _, key, score, sgn, spn) in zip(ks, seen.values()):
                    if key not in bill_ix:
                        bill_ix[key] = len(bill_keys)
                        bill_keys.append(key)
                    col["stage"].append(STG[stage]); col["row"].append(row); col["pos"].append(int(cpos[t0])); col["t0"].append(int(days[t0]))
                    col["tier"].append(TIERS.setdefault(tier, len(TIERS))); col["out"].append(OUTS.setdefault(oc, len(OUTS)))
                    col["concept"].append(CONCEPTS.setdefault(concept or "", len(CONCEPTS)))
                    col["pre"].append(pre[t0]); col["p10"].append(p10[t0]); col["p20"].append(p20[t0] if not np.isnan(p20[t0]) else np.nan)
                    col["bill"].append(bill_ix[key]); col["score"].append(score); col["sign"].append(sgn); col["spon"].append(spn)
                    col["liq"].append(liq[t0]); col["surge"].append(surge[t0]); col["ntheme"].append(n_theme.get(code, 0))
            if progress and ci % 100 == 0:
                progress(f"{ci}/{len(ev_by_code)} 종목")
        n_ev = len(col["stage"])
        A = {k_: np.array(v_) for k_, v_ in col.items()}
        del col
        PATHS = np.concatenate(paths, axis=0) if paths else np.empty((0, PRE + POST + 1), dtype=np.float32)
        del paths
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
        stage_i = {n_: i for i, n_ in enumerate(stage_names)}
        sign_cover = float(np.mean(A["sign"] != 0)) if n_ev else 0.0
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
            sg_ = A["sign"][i]
            if sg_ != 0:
                add("방향", f"{st_}|{'수혜 힌트' if sg_ > 0 else '부담 힌트'}", i)
        conn.execute("DELETE FROM study_result")
        today = dt.date.today().isoformat()
        stored = 0

        def payload_for(ii, Bn):
            dates = A["t0"][ii]
            _, cl_ = np.unique(dates, return_inverse=True)
            kk = int(cl_.max()) + 1
            rows_ = A["row"][ii]
            pre_m, pre_t, _ = _clustered(A["pre"][ii], dates)
            p10_m, p10_t, _ = _clustered(A["p10"][ii], dates)
            p20_m, p20_t, _ = _clustered(A["p20"][ii], dates)
            sgn = A["sign"][ii]
            suite = _suite(A["p10"][ii].astype(np.float64), sgn if np.mean(sgn != 0) >= 0.1 and np.sum(sgn != 0) >= 30 else None,
                           rows_, cl_, kk, mat_p10, lo_pos, hi_pos, Bn, rng)
            pl = dict(
                n_events=int(len(ii)), n_dates=int(kk), n_stocks=int(len(np.unique(rows_))),
                path=[round(float(x) * 100, 3) for x in PATHS[ii].mean(axis=0)],
                pre=None if pre_m is None else round(pre_m * 100, 3), pre_t=None if pre_t is None else round(pre_t, 2),
                post10=None if p10_m is None else round(p10_m * 100, 3), post10_t=None if p10_t is None else round(p10_t, 2),
                post20=None if p20_m is None else round(p20_m * 100, 3), post20_t=None if p20_t is None else round(p20_t, 2),
                pre_p=_placebo_p(pre_m, rows_, cl_, kk, mat_pre, lo_pos, hi_pos, Bn, rng), n_sign=int(np.sum(sgn != 0)))
            pl.update(suite)
            return pl
        for (grp, gkey), ii in groups.items():
            ii = np.array(ii, dtype=np.int64)
            n_dates = int(len(np.unique(A["t0"][ii])))
            if len(ii) < 15 or n_dates < 8:
                continue
            Bn = B if len(ii) <= 20000 else max(100, B // 4)
            conn.execute("INSERT OR REPLACE INTO study_result VALUES (?,?,?,?)", (grp, gkey, json.dumps(payload_for(ii, Bn), ensure_ascii=False), today))
            stored += 1
        # ---- 강하게 반응하는 조건 (학습 → 검증) ----
        def halves(x):
            ok = ~np.isnan(x)
            out = np.full(len(x), -1, dtype=np.int8)
            if ok.sum() >= 30:
                med = np.quantile(x[ok], 0.5)
                out[ok] = np.where(x[ok] <= med, 0, 1)
            return out
        spon = A["spon"]
        feats = [("연결", np.where(A["tier"] == TIERS.get("직접 언급", -9), 0, 1).astype(np.int8) if n_ev else np.array([], dtype=np.int8), ["직접 언급", "개념·테마 짐작"]),
                 ("점수", halves(A["score"].astype(float)), ["낮음", "높음"]),
                 ("소속 테마 수", np.where(A["ntheme"] <= 0, -1, np.where(A["ntheme"] == 1, 0, 1)).astype(np.int8), ["1개(특화)", "2개 이상"]),
                 ("거래대금 규모", halves(A["liq"].astype(float)), ["하위 절반", "상위 절반"]),
                 ("사전 거래량", np.where(np.isnan(A["surge"]), -1, (A["surge"] >= 2.0).astype(np.int8)).astype(np.int8), ["보통", "급증(2배↑)"]),
                 ("공동발의", np.where(spon < 0, -1, np.where(spon == 0, 0, np.where(spon < 10, 1, 2))).astype(np.int8), ["정부안", "1~9인", "10인 이상"]),
                 ("법안 성격", np.where(A["sign"] > 0, 0, np.where(A["sign"] < 0, 1, -1)).astype(np.int8), ["수혜 힌트", "부담 힌트"])]
        mat_abs = np.abs(mat_p10)
        Bf = min(B, 300)
        n_feat = 0
        for set_name, stages_ in STAGE_SETS:
            sidx = np.where(np.isin(A["stage"], [stage_i[x] for x in stages_]))[0]
            if len(sidx) < 200:
                continue
            ud = np.unique(A["t0"][sidx])
            split = int(np.quantile(ud, TRAIN_FRAC))
            split_pos = int(np.searchsorted(cal, split))
            ranges = {"train": (lo_pos, max(lo_pos + 10, split_pos)), "test": (min(split_pos, hi_pos - 10), hi_pos)}
            sel_split = {"train": sidx[A["t0"][sidx] <= split], "test": sidx[A["t0"][sidx] > split]}

            def run(ii, which):
                if len(ii) < 30:
                    return dict(n=int(len(ii)), n_dates=0, ratio=None, p=None)
                dates = A["t0"][ii]
                _, cl_ = np.unique(dates, return_inverse=True)
                kk = int(cl_.max()) + 1
                if kk < 12:
                    return dict(n=int(len(ii)), n_dates=kk, ratio=None, p=None)
                lo_, hi_ = ranges[which]
                ratio, pv = _abs_ratio(A["p10"][ii].astype(np.float64), A["row"][ii], cl_, kk, mat_abs, lo_, hi_, Bf if len(ii) <= 20000 else 100, rng)
                return dict(n=int(len(ii)), n_dates=kk, ratio=ratio, p=pv)
            base = {w: run(sel_split[w], w) for w in ("train", "test")}
            conn.execute("INSERT OR REPLACE INTO study_result VALUES (?,?,?,?)", ("특징", f"{set_name}|전체", json.dumps(dict(
                set=set_name, feature="전체", bucket="전체", train=base["train"], test=base["test"], status="기준", split=dt.date.fromordinal(split).isoformat()), ensure_ascii=False), today))
            # 버킷: 특징 하나씩 + 두 특징 조합 (효과가 '소형주이면서 기업명 직접 언급'처럼 교차 조건에만 있을 수 있어서)
            buckets = []
            for fname, fa, labels in feats:
                for bi, blab in enumerate(labels):
                    buckets.append((f"{fname}={blab}", fname, blab, (fa == bi)))
            for i1 in range(len(feats)):
                for i2 in range(i1 + 1, len(feats)):
                    f1, f2 = feats[i1], feats[i2]
                    for b1, l1 in enumerate(f1[2]):
                        m1 = (f1[1] == b1)
                        if not m1.any():
                            continue
                        for b2, l2 in enumerate(f2[2]):
                            buckets.append((f"{f1[0]}={l1} + {f2[0]}={l2}", f"{f1[0]}+{f2[0]}", f"{l1} + {l2}", m1 & (f2[1] == b2)))
            for key_, fname, blab, m in buckets:
                if not m.any():
                    continue
                r = {w: run(sel_split[w][m[sel_split[w]]], w) for w in ("train", "test")}
                tr, te = r["train"], r["test"]
                ok_tr = (tr["ratio"] is not None and tr["n_dates"] >= 25 and tr["ratio"] >= TRAIN_MIN_RATIO and (tr["p"] or 1) < TRAIN_P
                         and tr["ratio"] >= (base["train"]["ratio"] or 1) + 0.05)
                ok_te = (te["ratio"] is not None and te["n_dates"] >= 15 and te["ratio"] >= TEST_MIN_RATIO and (te["p"] or 1) < TEST_P
                         and te["ratio"] >= (base["test"]["ratio"] or 1) + 0.02)
                thin = (tr["n_dates"] < 25) or (te["n_dates"] < 15)
                status = "재현됨" if ok_tr and ok_te else "학습에서만" if ok_tr else "표본 부족" if thin else "없음"
                if status in ("없음", "표본 부족") and "+" in fname:          # 조합 버킷은 의미 있는 것(학습 통과)만 저장해 표를 작게
                    continue
                conn.execute("INSERT OR REPLACE INTO study_result VALUES (?,?,?,?)", ("특징", f"{set_name}|{key_}", json.dumps(dict(
                    set=set_name, feature=fname, bucket=blab, train=tr, test=te, status=status, split=dt.date.fromordinal(split).isoformat()), ensure_ascii=False), today))
                n_feat += 1
        # ---- 반응이 컸던 사례 (사후 선택이라 참고용) ----
        big_stage = [stage_i[x] for x in ("소관위 처리", "법사위 처리", "본회의 처리", "공포", "시행")]
        cidx = np.where(np.isin(A["stage"], big_stage) & ~np.isnan(A["p10"]))[0]
        cases = []
        if len(cidx):
            top = cidx[np.argsort(-np.abs(A["p10"][cidx]))[:30]]
            by_date = {}
            for i in range(n_ev):
                by_date.setdefault(int(A["t0"][i]), []).append(i)
            for i in top:
                same = [j for j in by_date.get(int(A["t0"][i]), []) if j != i and not np.isnan(A["p10"][j])]
                move = [j for j in same if np.sign(A["p10"][j]) == np.sign(A["p10"][i]) and abs(A["p10"][j]) >= 0.03]
                code_ = code_of_row[int(A["row"][i])]
                cases.append(dict(stage=stage_names[A["stage"][i]], date=dt.date.fromordinal(int(A["t0"][i])).isoformat(), code=code_, name=names_by_code.get(code_, code_),
                                  bill=bname.get(bill_keys[int(A["bill"][i])], ""), pre=round(float(A["pre"][i]) * 100, 2), post10=round(float(A["p10"][i]) * 100, 2),
                                  tier=TIER_N[A["tier"][i]], sign=int(A["sign"][i]), same_n=len(same), same_move=len(move)))
            # 무작위 날짜에서도 상위 30개 하한이 얼마나 되는지 (같은 규모의 이벤트를 무작위로 뽑아 비교)
            lows = []
            rows_c = A["row"][cidx]
            _, clc = np.unique(A["t0"][cidx], return_inverse=True)
            kc = int(clc.max()) + 1
            for _ in range(60):
                r = rng.integers(lo_pos, hi_pos, size=kc)
                v = np.abs(mat_p10[rows_c, r[clc]].astype(np.float64))
                v = v[~np.isnan(v)]
                if len(v) >= 30:
                    lows.append(float(np.partition(v, -30)[-30]))
            ref30 = round(float(np.mean(lows)) * 100, 2) if lows else None
        else:
            ref30 = None
        conn.execute("INSERT OR REPLACE INTO study_result VALUES ('사례', 'top', ?, ?)", (json.dumps(dict(cases=cases, ref30=ref30, n_pool=int(len(cidx))), ensure_ascii=False), today))
        conn.execute("INSERT OR REPLACE INTO study_meta VALUES ('computed', ?)", (dt.datetime.now().strftime("%Y-%m-%d %H:%M"),))
        conn.execute("INSERT OR REPLACE INTO study_meta VALUES ('n_events', ?)", (str(n_ev),))
        conn.execute("INSERT OR REPLACE INTO study_meta VALUES ('sign_cover', ?)", (f"{sign_cover:.3f}",))
        for k_, arr_ in (("thr_score", A["score"]), ("thr_liq", A["liq"])):          # 법안 상세에서 같은 기준(상·하위 절반)으로 조건을 맞춰 보려고 중앙값을 남김
            v_ = arr_.astype(float)
            v_ = v_[~np.isnan(v_)]
            if len(v_) >= 30:
                conn.execute("INSERT OR REPLACE INTO study_meta VALUES (?, ?)", (k_, f"{float(np.quantile(v_, 0.5)):.6f}"))
        conn.commit()
        log(f"이벤트 스터디: 이벤트 {n_ev}건 · 묶음 {stored} · 특징 {n_feat} · 방향 판정 범위 {sign_cover:.0%}")
        return {"이벤트": n_ev, "묶음": stored, "특징": n_feat, "방향 판정": f"{sign_cover:.0%}"}
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
def run_all(db_path, assembly_key, status_path, log=print, law_oc=""):
    """과거 법안 받기 → 법안·기업 연결 → 주가 받기 → 이벤트 스터디 계산을 차례로. 단계마다 진행·오류를 status_path(JSON)에 적음.
    각 단계는 따로 try 해서 한 단계가 실패해도 이유를 화면에 보여 주고, 앞 단계 결과가 있으면 다음 단계는 계속 시도해요."""
    import os
    import bill_history
    import stock_policy as sp
    names = list(STEPS)
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
    run(1, lambda p: find_promulgations(db_path, law_oc, log=log, progress=p))
    run(2, lambda p: map_bills(db_path, log=log, progress=p))
    run(3, lambda p: fetch_hist_texts(db_path, log=log, progress=p))
    run(4, lambda p: fetch_prices(db_path, log=log, progress=p))
    run(5, lambda p: compute(db_path, log=log, progress=p))
    st["running"] = False
    st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    return st


STAGE_SET_OF = {"발의": "발의", "소관위 회부": "심사", "소관위 상정": "심사", "소관위 처리": "심사", "법사위 처리": "심사", "본회의 처리": "확정", "공포": "확정", "시행": "확정"}


def match_bill(conn, title, text, stocks, tdata, proposer=""):
    """지금 보는 법안 × 후보 종목이 입법 주가 분석에서 '재현됨'으로 나온 조건에 해당하는지.
    stocks=[dict(code, name, score, evidence)] → (conds, thr_ok): conds=[dict(set, feature, bucket, train, test, hits=[종목 이름], unknown=[필요한데 모르는 특징])]
    계산할 수 있는 특징: 연결 확실도 · 점수 · 소속 테마 수 · 공동발의(발의자 정보가 있을 때) · 법안 성격. 거래대금·사전 거래량은 주가·거래량이 있어야 해서 '확인 불가'로 둬요."""
    res, meta = results(conn)
    conds = [p for (g, _), p in res.items() if g == "특징" and p.get("status") == "재현됨"]
    if not conds:
        return [], True
    thr = {k: float(meta[k]) for k in ("thr_score", "thr_liq") if k in meta}
    n_theme = {}
    for _, codes_ in (tdata.get("테마") or {}).items():
        for c_ in codes_:
            n_theme[c_] = n_theme.get(c_, 0) + 1
    sign = bill_sign(title, text)
    spn = sponsors(proposer) if proposer else None
    feats = {}
    for st in stocks:
        f = {}
        f["연결"] = "직접 언급" if (st.get("evidence") and str(st["evidence"][0]).startswith("법안 문구에 기업명")) else "개념·테마 짐작"
        if "thr_score" in thr:
            f["점수"] = "높음" if st["score"] > thr["thr_score"] else "낮음"
        nt = n_theme.get(st["code"], 0)
        if nt > 0:
            f["소속 테마 수"] = "1개(특화)" if nt == 1 else "2개 이상"
        if spn is not None and spn >= 0:
            f["공동발의"] = "정부안" if spn == 0 else "1~9인" if spn < 10 else "10인 이상"
        if sign:
            f["법안 성격"] = "수혜 힌트" if sign > 0 else "부담 힌트"
        feats[st["code"]] = (st["name"], f)
    out = []
    for p in conds:
        names = p["feature"].split("+")
        labs = [x.strip() for x in p["bucket"].split(" + ")] if "+" in p["feature"] else [p["bucket"]]
        hits, unknown = [], set()
        for code, (nm, f) in feats.items():
            ok, miss = True, []
            for fn, lab in zip(names, labs):
                if fn not in f:
                    miss.append(fn)
                elif f[fn] != lab:
                    ok = False
            if not ok:
                continue
            if miss:
                unknown.update(miss)
            else:
                hits.append(nm)
        if hits or unknown:
            out.append(dict(set=p["set"], feature=p["feature"], bucket=p["bucket"], train=p["train"], test=p["test"], hits=hits, unknown=sorted(unknown)))
    return out, True
