# -*- coding: utf-8 -*-
"""
입법 영향 기업: 지금 입법 과정에 있는 법안(심사 중 + 통과했지만 공포 전)이 어떤 기업에 닿을지, 언제쯤 공포될지를 한 화면에서.

- 단계: 발의 → 소관위 회부 → 소관위 상정 → 소관위 처리 → 법사위 처리 → 본회의 처리 → (정부 이송) → 공포. 국회 API가 알려 주는 단계 날짜로 진행 정도를 매겨요.
- 공포 시기
    · 본회의를 통과한 법안: 법제처 법령 검색에서 그 법의 공포일(의결일 이후)을 찾으면 '확정'(시행일 포함), 아직 없으면 '예상'.
      예상은 헌법 제53조(정부 이송 후 15일 이내 공포)에 따라 의결일 + 약 1~3주예요. 대통령 재의요구(거부권)가 나오면 달라져요.
    · 심사 중인 법안: 언제 통과할지 알 수 없어요. 남은 단계 수만 알려 주고, 임기(22대: 2028-05-29) 안에 처리되지 않으면 폐기돼요.
    · 시행 시기·적용기한: 제안이유 본문에 날짜(예: '2029년 12월 31일까지')나 '공포 후 N개월'이 있으면 그대로 보여 줘요 (본문이 없으면 비어요).
- 영향 기업: bill_impact(법안 문구 → 테마·업종·기업명)로 법안마다 미리 계산해 저장하고(수집 때), 화면은 저장된 값을 읽어요.
주의: 기업 후보는 짐작이고 호재·악재를 판단하지 않아요. 공포 '예상'은 일정을 보장하지 않아요.
"""
import datetime as dt
import json
import re
import sqlite3
import time

import bill_impact
import theme_map

VER = "2"                    # 개념 사전·계산 방식이 바뀌면 올려서 저장값을 다시 계산 (2: 테마 구성 종목도 저장)
TERM_END = "2028-05-29"      # 22대 국회 임기 만료
STAGES = (("발의", 1), ("소관위 회부", 2), ("소관위 상정", 3), ("소관위 처리", 4), ("법사위 처리", 5), ("본회의 처리", 6))
STAGE_NAMES = {0: "발의 전", 1: "발의", 2: "소관위 회부", 3: "소관위 상정", 4: "소관위 처리", 5: "법사위 처리", 6: "본회의 통과"}
PASS_WORDS = ("원안가결", "수정가결", "가결")
DROP_WORDS = ("폐기", "철회", "부결")


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS bill_impact_cache (
        key TEXT PRIMARY KEY, sig TEXT, concepts TEXT, groups TEXT, stocks TEXT, n INTEGER, has_text INTEGER, computed TEXT);
    CREATE TABLE IF NOT EXISTS bill_promul (
        bill_key TEXT PRIMARY KEY, law TEXT, promulgated TEXT, effective TEXT, kind TEXT, checked TEXT);
    CREATE TABLE IF NOT EXISTS law_versions (law TEXT, promulgated TEXT, effective TEXT, kind TEXT, fetched TEXT, PRIMARY KEY (law, promulgated, effective));
    CREATE TABLE IF NOT EXISTS law_fetch (law TEXT PRIMARY KEY, fetched TEXT, n INTEGER);
    CREATE TABLE IF NOT EXISTS legis_watch (code TEXT PRIMARY KEY, name TEXT, added TEXT, baselined INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS legis_feedback (
        scope TEXT, ref TEXT, code TEXT, name TEXT, verdict TEXT, created TEXT, PRIMARY KEY (scope, ref, code));
    CREATE TABLE IF NOT EXISTS legis_alerted (k TEXT PRIMARY KEY);
    """)
    try:                                        # 예전 DB에는 members 열이 없어서 한 번 늘림 (읽기 전용 연결에서는 호출하지 않아요)
        conn.execute("ALTER TABLE bill_impact_cache ADD COLUMN members TEXT")
    except sqlite3.OperationalError:
        pass


def status_kind(status):
    st = status or ""
    if any(w in st for w in DROP_WORDS):
        return "폐기"
    if any(w in st for w in PASS_WORDS):
        return "통과"
    return "심사 중"


def bill_name(title):
    """'조세특례제한법 일부개정법률안 (홍길동의원 등 10인) · 위원회 심사 중' → '조세특례제한법 일부개정법률안'"""
    t = re.sub(r"\s*\([^()]*(?:의원|위원장|정부|의장)[^()]*\)\s*(?:·.*)?$", "", title or "")
    return t.split(" · ")[0].strip()


def law_name(title):
    n = bill_name(title)
    n = re.sub(r"\s*(?:일부개정|전부개정|폐지|제정)?\s*법률안.*$|\s*일부개정안.*$|\s*전부개정안.*$|\s*제정안.*$", "", n).strip()
    return n


def stage_info(tracks, status):
    """tracks [(날짜, 단계 이름)] → (진행 단계 0~6, 이름, 마지막 단계 날짜)"""
    idx, last = 0, ""
    for d, st in tracks:
        for pre, i in STAGES:
            if st.startswith(pre):
                idx = max(idx, i)
                last = max(last, d)
    if status_kind(status) == "통과":
        idx = 6
    return idx, STAGE_NAMES.get(idx, ""), last


def text_dates(text):
    """제안이유에 나온 날짜들 → [(YYYY-MM-DD, 앞뒤 문맥)] (적용기한·시행일 짐작용)"""
    out = []
    for m in re.finditer(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text or ""):
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue
        ctx = (text[max(0, m.start() - 22): m.end() + 14]).strip()
        out.append((d, ctx))
    return out


def effective_hint(text):
    """'공포 후 N개월 시행'·'공포한 날부터 시행' 같은 문구 → 짧은 설명 (없으면 '')"""
    t = text or ""
    m = re.search(r"공포\s*(?:한)?\s*(?:후|날부터)\s*(\d+)\s*(개월|일|년)\s*(?:이 경과한 날|이내)?[^.]{0,12}시행", t)
    if m:
        return f"본문: 공포 후 {m.group(1)}{m.group(2)} 시행"
    if re.search(r"공포\s*한?\s*날부터\s*시행", t):
        return "본문: 공포한 날부터 시행"
    return ""


def promulgation(status, tracks, promul, text="", today=None):
    """→ dict(kind: 'done'|'est'|'pending'|'none', date, until, effective, note, text_dates)"""
    today = today or dt.date.today()
    out = dict(kind="none", date="", until="", effective="", note="", dates=[d for d, _ in text_dates(text)][:3], hint=effective_hint(text))
    sk = status_kind(status)
    if sk == "폐기":
        out["note"] = "폐기·철회돼 공포되지 않아요"
        return out
    if promul and promul.get("promulgated"):
        out.update(kind="done", date=promul["promulgated"], effective=promul.get("effective") or "", note="법제처에서 확인한 공포일")
        return out
    if sk == "통과":
        passed = next((d for d, st in reversed(tracks) if st.startswith("본회의 처리")), "")
        if not passed:
            out.update(kind="est", note="본회의 통과 (공포 대기)")
            return out
        d0 = dt.date.fromisoformat(passed)
        mid, late = d0 + dt.timedelta(days=14), d0 + dt.timedelta(days=21)
        late_note = "" if today <= late else " (예상 시점이 지났어요 — 법제처 반영이 늦거나 재의요구일 수 있어요)"
        out.update(kind="est", date=mid.isoformat(), until=late.isoformat(),
                   note=f"의결 {passed} · 정부 이송 후 15일 이내 공포 → 보통 의결 후 1~3주{late_note}")
        return out
    idx, _, _ = stage_info(tracks, status)
    left = 6 - idx
    out.update(kind="pending", note=f"본회의까지 {left}단계 남음 · 통과 뒤 공포까지 보통 1~3주 · 임기({TERM_END}) 안에 처리되지 않으면 폐기")
    return out


# ---------------- 저장값 계산 ----------------
def _stamp(conn):
    parts = []
    for tbl in ("theme_meta", "industry_meta"):
        try:
            r = conn.execute(f"SELECT value FROM {tbl} WHERE key='synced'").fetchone()
            parts.append(r[0] if r else "")
        except sqlite3.OperationalError:
            parts.append("")
    return "/".join(parts)


def _sig(text, stamp):
    return f"{len(text or '')}|{stamp}|{VER}"


def in_process(conn, days=400):
    """[(key, title, date, url, status)] 폐기가 아닌 최근 법안"""
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        rows = conn.execute("SELECT key, title, date, url, status FROM policy_items WHERE kind='법안' AND key!='_meta' AND date >= ? ORDER BY date DESC", (cut,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r for r in rows if status_kind(r[4]) != "폐기"]


def _members_of(r):
    """분석 결과의 '가장 잘 맞는 작은 테마·업종' 구성 종목 → [[코드, 이름, 테마·업종 이름, 점수]] (한 종목은 가장 높은 점수 하나만).
    점수 상위 목록에서 밀려도 관심 종목·종목 화면에서 놓치지 않게 따로 저장해요."""
    best = {}
    for m in r["members"]:
        for code, name in m["stocks"]:
            if code not in best or m["score"] > best[code][3]:
                best[code] = [code, name, m["name"], round(m["score"], 1)]
    return list(best.values())


def refresh_cache(db_path, log=print, days=400):
    """입법 중인 법안마다 영향 기업을 계산해 저장 (바뀐 것만) → {계산, 건너뜀}"""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        data = theme_map.load(conn)
        if not data["테마"] and not data["업종"]:
            return {"건너뜀": "테마·업종 자료 없음"}
        stamp = _stamp(conn)
        texts = {}
        try:
            texts = {k: t for k, t in conn.execute("SELECT bill_key, text FROM bill_text")}
        except sqlite3.OperationalError:
            pass
        have = {k: s for k, s in conn.execute("SELECT key, sig FROM bill_impact_cache")}
        done = skipped = 0
        live = set()
        for key, title, date, url, status in in_process(conn, days):
            live.add(key)
            text = texts.get(key, "")
            sig = _sig(text, stamp)
            if have.get(key) == sig:
                skipped += 1
                continue
            r = bill_impact.analyze(conn, bill_name(title), text, top=15, data=data)
            conn.execute("INSERT OR REPLACE INTO bill_impact_cache (key, sig, concepts, groups, stocks, n, has_text, computed, members) VALUES (?,?,?,?,?,?,?,?,?)", (
                key, sig, json.dumps([c for c, _ in r["concepts"]], ensure_ascii=False),
                json.dumps([[g[0], g[1], g[4]] for g in r["groups"][:8]], ensure_ascii=False),
                json.dumps([[s["code"], s["name"], s["score"], (s["evidence"] or [""])[0]] for s in r["stocks"]], ensure_ascii=False),
                len(r["stocks"]), 1 if r["has_text"] else 0, dt.date.today().isoformat(),
                json.dumps(_members_of(r), ensure_ascii=False)))
            done += 1
            if done % 200 == 0:
                conn.commit()
        conn.execute("DELETE FROM bill_impact_cache WHERE key NOT IN (SELECT key FROM policy_items WHERE kind='법안')")
        conn.commit()
        log(f"입법 영향 기업: 계산 {done} · 그대로 {skipped}")
        return {"계산": done, "그대로": skipped}
    finally:
        conn.close()


def fetch_texts(db_path, limit=100, log=print, days=400, sleep=0.3, fetcher=None):
    """입법 중인 법안 중 제안이유가 없는 것을 최신 순으로 limit개 받음. 연속 5번 실패하면 멈춤 (국회 페이지가 막혔을 때 두드리지 않게)"""
    import bill_watch
    fetcher = fetcher or bill_watch.fetch_text
    conn = sqlite3.connect(db_path)
    try:
        bill_watch.ensure(conn)
        have = {k for (k,) in conn.execute("SELECT bill_key FROM bill_text")}
        todo = [(k, u) for k, t, d, u, s in in_process(conn, days) if k not in have and u][:limit]
        ok = fail = streak = 0
        for key, url in todo:
            try:
                txt = fetcher(url)
                streak = 0
            except Exception:
                fail += 1
                streak += 1
                if streak >= 5:
                    log("제안이유 받기: 5번 연속 실패해서 멈췄어요 (국회 페이지 접근을 확인해 주세요)")
                    break
                continue
            conn.execute("INSERT OR REPLACE INTO bill_text VALUES (?,?,?)", (key, dt.date.today().isoformat(), txt))
            ok += 1
            if ok % 20 == 0:
                conn.commit()
            time.sleep(sleep)
        conn.commit()
        log(f"제안이유 받기: 대상 {len(todo)} · 성공 {ok} · 실패 {fail}")
        return {"성공": ok, "실패": fail, "대상": len(todo)}
    finally:
        conn.close()


MAX_GAP_DAYS = 90                  # 본회의 의결 뒤 이 기간 안의 공포만 그 법안의 공포로 봄 (더 나중 개정을 잘못 잇지 않게)


def pick_version(versions, passed):
    """연혁 [{promulgated, effective, kind}]에서 의결일(passed) 이후 MAX_GAP_DAYS 안의 가장 이른 공포 버전. 없으면 None"""
    try:
        d0 = dt.date.fromisoformat(passed)
    except (TypeError, ValueError):
        return None
    hi = (d0 + dt.timedelta(days=MAX_GAP_DAYS)).isoformat()
    cands = [v for v in versions if v.get("promulgated") and passed <= v["promulgated"] <= hi]
    return min(cands, key=lambda v: (v["promulgated"], v.get("effective") or "")) if cands else None


def fetch_versions(conn, api, name, max_age_days=3):
    """법 하나의 연혁을 법제처에서 받아 law_versions에 저장(며칠 동안은 다시 받지 않음) → [{promulgated, effective, kind}]"""
    ensure(conn)
    row = conn.execute("SELECT fetched FROM law_fetch WHERE law=?", (name,)).fetchone()
    today = dt.date.today()
    if not row or row[0] < (today - dt.timedelta(days=max_age_days)).isoformat():
        vs = api.law_versions(name)
        for v in vs:
            conn.execute("INSERT OR REPLACE INTO law_versions VALUES (?,?,?,?,?)", (name, v["promulgated"], v.get("effective") or "", v.get("kind") or "", today.isoformat()))
        conn.execute("INSERT OR REPLACE INTO law_fetch VALUES (?,?,?)", (name, today.isoformat(), len(vs)))
        conn.commit()
    return [dict(promulgated=p, effective=e, kind=k) for p, e, k in conn.execute(
        "SELECT promulgated, effective, kind FROM law_versions WHERE law=? ORDER BY promulgated, effective", (name,))]


def sync_promul(db_path, law_oc, log=print, days=150, session=None):
    """통과한 법안의 법을 법제처 연혁에서 찾아 공포일·시행일 저장. 의결일 뒤 90일 안의 가장 이른 공포만 그 법안의 공포로 봄. → {확인, 공포}"""
    import stock_policy as sp
    if not law_oc:
        return {"건너뜀": "법제처 키 없음"}
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        api = sp.Client("", law_oc, "")
        checked = found = 0
        today = dt.date.today()
        for key, title, date, url, status in in_process(conn, days):
            if status_kind(status) != "통과":
                continue
            row = conn.execute("SELECT promulgated, checked FROM bill_promul WHERE bill_key=?", (key,)).fetchone()
            if row and (row[0] or (row[1] and row[1] >= (today - dt.timedelta(days=2)).isoformat())):
                continue
            tr = [(d, s) for d, s in conn.execute("SELECT date, stage FROM bill_track WHERE bill_key=? ORDER BY date", (key,))]
            passed = next((d for d, st in reversed(tr) if st.startswith("본회의 처리")), date or "")
            name = law_name(title)
            if not name or not passed:
                continue
            try:
                vs = fetch_versions(conn, api, name)
            except Exception as e:
                log(f"공포일 확인 실패 ({name[:14]}): {type(e).__name__}")
                continue
            checked += 1
            v = pick_version(vs, passed)
            if v:
                found += 1
            conn.execute("INSERT OR REPLACE INTO bill_promul VALUES (?,?,?,?,?,?)", (
                key, name, v["promulgated"] if v else "", (v.get("effective") or "") if v else "", (v.get("kind") or "") if v else "", today.isoformat()))
            time.sleep(0.3)
        conn.commit()
        log(f"공포일 확인: {checked}건 조회 · 공포 확인 {found}건")
        return {"확인": checked, "공포": found}
    finally:
        conn.close()


# ---------------- 화면용 읽기 (쓰기 없음: 대시보드는 DB를 읽기 전용으로 열어요) ----------------
_MEMO = {}          # 서버가 아직 계산 못 한 법안을 화면에서 즉석 계산한 결과 (프로세스가 떠 있는 동안 유지)


def _q(conn, sql, args=()):
    """표가 없거나 읽을 수 없으면 빈 목록 (한 표가 비었다고 화면 전체가 '법안 없음'이 되지 않게)"""
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def diagnose(conn):
    """화면 진단용 개수들: 법안 수, 입법 중, 계산됨, 본문, 테마·업종, 공포일 확인"""
    def n(sql):
        r = _q(conn, sql)
        return r[0][0] if r else 0
    return dict(
        bills=n("SELECT COUNT(*) FROM policy_items WHERE kind='법안' AND key!='_meta'"),
        in_process=len(in_process(conn)),
        tracks=n("SELECT COUNT(*) FROM bill_track"),
        cached=n("SELECT COUNT(*) FROM bill_impact_cache"),
        texts=n("SELECT COUNT(*) FROM bill_text WHERE text != ''"),
        themes=n("SELECT COUNT(*) FROM theme_names"),
        theme_stocks=n("SELECT COUNT(DISTINCT code) FROM theme_stocks"),
        industries=n("SELECT COUNT(*) FROM industry_map"),
        promul=n("SELECT COUNT(*) FROM bill_promul WHERE promulgated != ''"),
    )


def load_rows(conn, days=400, live_limit=150):
    """입법 중인 법안 전체 → [dict(...)]. 서버가 영향 기업을 아직 계산하지 않은 법안은 최근 것부터 live_limit개까지 즉석에서 계산해 채움."""
    tracks = {}
    for k, d, s in _q(conn, "SELECT bill_key, date, stage FROM bill_track ORDER BY date"):
        tracks.setdefault(k, []).append((d, s))
    cache = {}
    rows_c = _q(conn, "SELECT key, concepts, groups, stocks, n, has_text, members FROM bill_impact_cache")
    if not rows_c:                                          # members 열이 없는 예전 DB
        rows_c = [tuple(r) + (None,) for r in _q(conn, "SELECT key, concepts, groups, stocks, n, has_text FROM bill_impact_cache")]
    for k, c, g, s, n, ht, mb in rows_c:
        cache[k] = dict(concepts=json.loads(c or "[]"), groups=json.loads(g or "[]"), stocks=json.loads(s or "[]"), n=n, has_text=bool(ht),
                        members=json.loads(mb or "[]"))
    promul = {}
    for k, law, p, e, kd, ck in _q(conn, "SELECT bill_key, law, promulgated, effective, kind, checked FROM bill_promul"):
        promul[k] = dict(law=law, promulgated=p, effective=e, kind=kd)
    texts = {k: t for k, t in _q(conn, "SELECT bill_key, text FROM bill_text WHERE text != ''")}
    fb = feedback(conn)
    data = None
    live = 0
    out = []
    for key, title, date, url, status in in_process(conn, days):
        tr = tracks.get(key, [])
        idx, label, last = stage_info(tr, status)
        c = cache.get(key)
        if c is None:                                    # 서버 계산 전: 즉석 계산 (테마·업종 자료가 있을 때만)
            txt0 = texts.get(key, "")
            memo = _MEMO.get((key, len(txt0)))
            if memo is None and live < live_limit:
                if data is None:
                    data = theme_map.load(conn)
                if data["테마"] or data["업종"]:
                    r = bill_impact.analyze(conn, bill_name(title), txt0, top=15, data=data)
                    memo = dict(concepts=[x for x, _ in r["concepts"]], groups=[[g[0], g[1], g[4]] for g in r["groups"][:8]],
                                stocks=[[s["code"], s["name"], s["score"], (s["evidence"] or [""])[0]] for s in r["stocks"]], n=len(r["stocks"]), has_text=r["has_text"],
                                members=_members_of(r))
                    _MEMO[(key, len(txt0))] = memo
                    live += 1
            c = memo or dict(concepts=[], groups=[], stocks=[], n=0, has_text=False, members=[])
        started = next((d for d, s in tr if s.startswith("발의")), date or "")
        txt = texts.get(key, "")
        stocks, removed, added = apply_feedback(c["stocks"], c["concepts"], key, fb)
        gone = {x[0] for x in removed}
        top = {x[0] for x in stocks}
        mem = [m for m in c.get("members", []) if m[0] not in gone and m[0] not in top]          # 점수 상위 밖의 테마 구성 종목
        out.append(dict(key=key, title=title, name=bill_name(title), date=date or "", url=url or "", status=status or "", kind=status_kind(status),
                        stage=idx, stage_label=label, last=last or date or "", started=started, tracks=tr,
                        promul=promulgation(status, tr, promul.get(key), txt), concepts=c["concepts"], groups=c["groups"],
                        stocks=stocks, members=mem, removed=removed, added=added, n=len(stocks), has_text=bool(txt) or c["has_text"],
                        summary=summary_line(txt), nature=nature_hint(title, txt)))
    return out


# ---------------- 화면에서 '지금 준비하기' ----------------
def prepare(db_path, assembly_key, law_oc, status_path, text_limit=30, log=print):
    """대시보드 버튼용: 테마·업종 → 국회 법안 목록 → 제안이유 → 공포일 → 영향 기업 계산을 차례로 돌리고, 단계마다 결과·오류를 status_path(JSON)에 적음.
    각 단계는 따로 try 해서 한 단계가 실패해도 나머지를 계속 시도하고, 실패 이유를 그대로 화면에 보여 줌."""
    import industry
    import bill_feed
    names = ["테마·업종 자료", "국회 법안 목록", "제안이유 본문", "공포일(법제처)", "영향 기업 계산"]
    steps = [dict(name=n, state="wait", msg="") for n in names]
    import os
    st = dict(running=True, pid=os.getpid(), started=time.strftime("%Y-%m-%d %H:%M:%S"), finished="", steps=steps)

    def save():
        try:
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        except OSError:
            pass

    def run(i, fn):
        steps[i]["state"] = "run"
        save()
        try:
            res = fn()
            steps[i]["state"], steps[i]["msg"] = "ok", (res if isinstance(res, str) else json.dumps(res, ensure_ascii=False))[:300]
        except Exception as e:
            steps[i]["state"], steps[i]["msg"] = "fail", f"{type(e).__name__}: {str(e)[:260]}"
        save()
    save()

    def themes():
        a = theme_map.sync(db_path, log=log)
        b = industry.sync(db_path, log=log)
        return f"테마 {a} · 업종 {b}"
    run(0, themes)

    def feed():
        if not assembly_key:
            raise RuntimeError("열린국회정보 키(ASSEMBLY_API_KEY)가 없어요. 이 앱 .env 또는 부동산 앱 .env에 넣어 주세요")
        r = bill_feed.sync(db_path, assembly_key, log=log)
        if "실패" in r:
            raise RuntimeError(r["실패"])
        if not r.get("최근"):
            raise RuntimeError(f"목록은 받았는데 최근 법안이 0건이에요 (전체 {r.get('전체')}건, 정렬 {r.get('순서')}) — 설정 → 법안·종목 확인의 응답을 캡처해 주세요")
        return r
    run(1, feed)
    def texts():
        r = fetch_texts(db_path, text_limit, log=log)
        if r["대상"] and not r["성공"] and r["실패"]:
            raise RuntimeError(f"제안이유를 한 건도 못 받았어요 ({r['실패']}건 실패) — 국회 의안정보 페이지에 접속하지 못했거나 페이지 모양이 달라요. "
                               "본문 없이도 제목으로 후보를 찾고, 법안 상세에서 내용을 붙여 넣을 수 있어요")
        return r
    run(2, texts)
    run(3, lambda: sync_promul(db_path, law_oc, log=log))
    run(4, lambda: refresh_cache(db_path, log=log))
    st["running"] = False
    st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    return st


def _pid_alive(pid):
    import os
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.path.isdir("/proc"):
        try:                                   # 끝났는데 아직 회수되지 않은 좀비('Z')는 죽은 것으로 침
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                return f.read().rsplit(")", 1)[1].split()[0] != "Z"
        except (OSError, IndexError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_status(status_path):
    """준비 상태 파일 읽기. '진행 중'으로 적혀 있어도 그 작업 프로세스가 죽었으면(서버 재시작 등) 중단된 것으로 고쳐서 돌려줌
    (안 그러면 화면이 영원히 '진행 중'으로 남아 계속 새로고침하고 버튼도 막혀요)"""
    try:
        with open(status_path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if st.get("running"):
        alive = _pid_alive(st.get("pid"))
        if not alive and not st.get("pid"):                    # 예전 버전이 적은 상태: 시작한 지 20분이 넘으면 죽은 것으로 봄
            try:
                alive = 0 <= time.time() - time.mktime(time.strptime(st.get("started", ""), "%Y-%m-%d %H:%M:%S")) < 1200
            except (ValueError, TypeError, OverflowError):
                alive = False
        if not alive:
            st["running"] = False
            st["stale"] = True
            for stp in st.get("steps", []):
                if stp.get("state") in ("run", "wait"):
                    stp["state"], stp["msg"] = "fail", "중단됐어요 (서버가 재시작되었거나 작업이 끊겼어요) — 다시 준비하기를 눌러 주세요"
    return st


# ---------------- 제안이유 핵심 요약 · 성격 힌트 · 비슷한 법안 묶기 ----------------
_PURPOSE = re.compile(r"(?:이에|따라서|이를\s*(?:개선|해소|보완)하기\s*위하여)?\s*([^.]*?(?:하려는|려는|개정하려는|신설하려는|도입하려는|확대하려는|강화하려는|연장하려는|완화하려는|폐지하려는)\s*것임)")


def _clean_sentence(s):
    s = re.sub(r"\(\s*(?:안|제안)\s*제[^)]*\)", "", s)               # (안 제107조의2) 같은 조문 표시
    s = re.sub(r"^\s*(?:이에|따라서|그런데|한편|또한)\s*,?\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.。")
    s = re.sub(r"려는\s*것임$", "려 함", s)                           # '연장하려는 것임' → '연장하려 함'
    s = re.sub(r"\s*것임$", "", s)
    return s.strip(" ,.。")


def summary_line(text, limit=110):
    """제안이유 본문 → 법안이 하려는 일 한 줄 (목적 문장 '…하려는 것임'을 우선, 없으면 첫 문장). 본문이 없으면 ''"""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return ""
    t = re.sub(r"^.{0,12}제안이유\s*(?:및\s*주요\s*내용)?\s*[:：]?\s*", "", t)
    sents = [x.strip() for x in re.split(r"(?<=[.。])\s+|(?<=것임)\s+(?=[가-힣A-Za-z0-9])", t) if x.strip()]
    pick = ""
    for s in sents:                                                  # 목적 문장은 보통 '이에 … 하려는 것임'
        if re.search(r"(?:하려는|려는)\s*것임", s):
            pick = s
            break
    if not pick and sents:
        pick = sents[0]
    out = _clean_sentence(pick)
    if len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


SUPPORT_STRONG = ("세액공제", "감면", "면제", "특례", "환급", "지원금", "보조금", "융자", "인센티브", "비과세", "분리과세", "일몰 연장", "적용기한을 연장", "규제 완화", "규제를 완화", "완화")
SUPPORT_WEAK = ("지원", "육성", "촉진", "활성화", "허용", "혜택", "확대", "공제")
BURDEN_STRONG = ("처벌", "과징금", "과태료", "벌칙", "벌금", "금지", "제재", "환수", "의무화", "규제 강화", "규제를 강화", "부과", "징수", "상한", "폐지", "취소")
BURDEN_WEAK = ("의무", "제한", "강화", "인상", "신고", "보고", "등록")


def nature_hint(title, text):
    """법안 문구의 단어로 '수혜 가능 / 부담 가능 / 혼재' 힌트 → dict(kind, support, burden). 본문이 없으면 kind='none'.
    호재·악재 판단이 아니에요: 같은 법안이 기업마다 다르게 닿고, 단어만으로는 방향을 확정할 수 없어요 (예: '지원 강화'의 '강화')."""
    body = text or ""                                  # 제목은 쓰지 않아요 (법 이름 '조세특례제한법'의 '특례'·'제한'이 단어로 잡혀 오염되므로)
    if len(body.strip()) < 30:
        return dict(kind="none", support=[], burden=[])

    def count(strong, weak):
        hit_s = [w for w in strong if w in body]
        hit_w = [w for w in weak if w in body and not any(w in s for s in hit_s)]
        return hit_s, hit_w
    ss, sw = count(SUPPORT_STRONG, SUPPORT_WEAK)
    bs, bw = count(BURDEN_STRONG, BURDEN_WEAK)
    # '지원 강화'처럼 지원이 붙은 '강화'는 부담이 아님
    if "강화" in bw and re.search(r"(?:지원|육성|공제|감면|혜택|보호)[^.]{0,6}강화", body) and not re.search(r"(?:규제|처벌|의무|과징금|제재)[^.]{0,6}강화", body):
        bw = [w for w in bw if w != "강화"]
    sup, bur = 2 * len(ss) + len(sw), 2 * len(bs) + len(bw)
    if sup >= 2 and bur >= 2 and abs(sup - bur) < 2:
        kind = "mixed"
    elif sup >= bur + 2:
        kind = "support"
    elif bur >= sup + 2:
        kind = "burden"
    else:
        kind = "none"
    return dict(kind=kind, support=(ss + sw)[:5], burden=(bs + bw)[:5])


def group_key(row):
    """비슷한 법안 묶는 기준: 같은 법 + 같은 분야(개념 앞 두 개). 분야가 안 잡힌 법안은 법 이름만으로 묶지 않고 낱개로 둠"""
    law = law_name(row["title"]) or row["name"]
    if not row["concepts"]:
        return (law, row["key"])
    return (law, tuple(sorted(row["concepts"][:2])))


def group_rows(rows):
    """[row] → [dict(rep=대표 법안, items=[전부], n)]. 대표는 가장 앞선 단계(같으면 최근 변동이 늦은 것)"""
    groups = {}
    for r in rows:
        groups.setdefault(group_key(r), []).append(r)
    out = []
    for items in groups.values():
        items.sort(key=lambda r: (r["stage"], r["last"] or ""), reverse=True)
        out.append(dict(rep=items[0], items=items, n=len(items)))
    return out



# ---------------- 관심 종목 · 후보 직접 고치기 · 단계 변화 알림 ----------------
def watch_list(conn):
    """[(코드, 이름)] 관심 종목"""
    return [(c, n or c) for c, n in _q(conn, "SELECT code, name FROM legis_watch ORDER BY added, code")]


def feedback(conn):
    """{'concept': {분야: {코드: (verdict, 이름)}}, 'bill': {법안키: {코드: (verdict, 이름)}}}"""
    fb = {"concept": {}, "bill": {}}
    for scope, ref, code, name, verdict in _q(conn, "SELECT scope, ref, code, name, verdict FROM legis_feedback"):
        fb.setdefault(scope, {}).setdefault(ref, {})[code] = (verdict, name or code)
    return fb


def apply_feedback(stocks, concepts, key, fb):
    """계산된 후보 [[코드, 이름, 점수, 근거]]에 사용자의 수정을 반영 → (후보, 제외한 것, 직접 추가한 것).
    제외/추가 항목은 [(코드, 이름, 이유, scope, ref)] — 되돌리기에 scope·ref가 필요해요.
    분야(개념) 수정은 그 분야의 모든 법안에, 법안 수정은 그 법안에만 적용돼요. 같은 기업에 둘이 충돌하면 법안 수정이 이겨요.
    '직접 추가'한 기업은 점수 순위 맨 위에 와요."""
    rules = {}
    for cname in concepts or []:
        for code, (v, nm) in fb.get("concept", {}).get(cname, {}).items():
            rules[code] = (v, nm, f"분야 '{cname}'", "concept", cname)
    for code, (v, nm) in fb.get("bill", {}).get(key, {}).items():
        rules[code] = (v, nm, "이 법안", "bill", key)
    out, removed, added, seen = [], [], [], set()
    for code, name, score, ev in stocks:
        r = rules.get(code)
        if r and r[0] == "no":
            removed.append((code, name, r[2], r[3], r[4]))
            continue
        if r and r[0] == "yes":
            score, ev = max(score, 90.0), f"직접 추가 ({r[2]}) · {ev}"
            added.append((code, name, r[2], r[3], r[4]))
        out.append([code, name, score, ev])
        seen.add(code)
    for code, (v, nm, why, sc, rf) in rules.items():
        if v == "yes" and code not in seen:
            out.append([code, nm, 90.0, f"직접 추가 ({why})"])
            added.append((code, nm, why, sc, rf))
        elif v == "no" and code not in seen:
            pass                                             # 원래 후보에 없던 기업의 '제외'는 화면에 안 보임
    out.sort(key=lambda x: (-x[2], x[1]))
    return out, removed, added


def watch_hits(row, wcodes):
    """이 법안에서 관심 종목이 걸린 항목 [[코드, 이름, 점수, 근거]] — 상위 후보에 있으면 그 점수, 점수 밖이어도 '테마 구성 종목'이면 약하게 포함"""
    hits = [x for x in row["stocks"] if x[0] in wcodes]
    seen = {x[0] for x in hits}
    for code, name, theme, sc in row.get("members", []):
        if code in wcodes and code not in seen:
            hits.append([code, name, sc, f"테마·업종 {theme}의 구성 종목"])
    return hits


def watch_alerts(db_path, log=print):
    """관심 종목에 영향을 주는 법안의 새 등장·단계 진행·공포(확정/예상)를 알림 문구로 (이미 알린 것은 제외).
    관심 종목을 새로 넣은 직후 첫 계산은 지금까지의 것을 '이미 알린 것'으로 쳐서 알림이 쏟아지지 않게 함. → [문구...]"""
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        watch = watch_list(conn)
        if not watch:
            return []
        rows = load_rows(conn)
        done = {k for (k,) in conn.execute("SELECT k FROM legis_alerted")}
        base = {c: b for c, b in conn.execute("SELECT code, baselined FROM legis_watch")}
        msgs, new_keys = [], []
        for code, wname in watch:
            fresh = []
            for r in rows:
                hit = next(iter(watch_hits(r, {code})), None)
                if not hit:
                    continue
                pm = r["promul"]
                ks = [(f"new:{r['key']}:{code}", f"[새 법안] {wname} ← {r['name'][:42]} ({r['stage_label'] or '발의'})"),
                      (f"stage:{r['key']}:{code}:{r['stage']}", f"[{r['stage_label'] or '발의'}] {wname} ← {r['name'][:42]}")]
                if pm["kind"] == "done":
                    ks.append((f"prom:{r['key']}:{code}:done", f"[공포 확정 {pm['date']}] {wname} ← {r['name'][:42]}" + (f" (시행 {pm['effective']})" if pm["effective"] else "")))
                if pm["kind"] == "done" and pm.get("effective"):              # 시행이 7일 안으로 다가오면 한 번 더
                    try:
                        left = (dt.date.fromisoformat(pm["effective"]) - dt.date.today()).days
                    except ValueError:
                        left = -1
                    if 0 <= left <= 7:
                        ks.append((f"eff:{r['key']}:{code}:{pm['effective']}", f"[시행 임박 {pm['effective']} (D-{left})] {wname} ← {r['name'][:42]}"))
                if pm["kind"] == "est" and pm["date"]:
                    ks.append((f"prom:{r['key']}:{code}:est", f"[공포 예상 {pm['date'][5:]}경] {wname} ← {r['name'][:42]}"))
                for k, m in ks:
                    if k not in done:
                        fresh.append((k, m))
            if not base.get(code):                                   # 첫 계산: 조용히 기준선만
                new_keys += [k for k, _ in fresh]
                conn.execute("UPDATE legis_watch SET baselined=1 WHERE code=?", (code,))
                continue
            # 같은 법안의 '새 법안'과 '단계'가 한꺼번에 나오면 '새 법안' 한 줄만
            seen_new = {k.split(":")[1] for k, _ in fresh if k.startswith("new:")}
            for k, m in fresh:
                if k.startswith("stage:") and k.split(":")[1] in seen_new:
                    new_keys.append(k)
                    continue
                new_keys.append(k)
                msgs.append(m)
        conn.executemany("INSERT OR IGNORE INTO legis_alerted VALUES (?)", [(k,) for k in new_keys])
        conn.commit()
        log(f"관심 종목 입법 알림: 관심 {len(watch)}종목 · 새 알림 {len(msgs)}건")
        return msgs
    finally:
        conn.close()


def for_stock(conn, code, limit=6):
    """종목 화면용: 이 종목에 영향을 줄 수 있는 입법 중 법안 (저장된 영향 기업 계산 + 직접 추가분). 전체를 읽지 않고 이 종목이 든 것만 가볍게 읽어요."""
    like = f'%"{code}"%'
    got = _q(conn, "SELECT key, concepts, stocks, members FROM bill_impact_cache WHERE stocks LIKE ? OR members LIKE ?", (like, like))
    if not got:                                               # members 열이 없는 예전 DB
        got = [tuple(r) + (None,) for r in _q(conn, "SELECT key, concepts, stocks FROM bill_impact_cache WHERE stocks LIKE ?", (like,))]
    cand = {}
    for k, c, st, mb in got:
        stocks_ = json.loads(st or "[]")
        if not any(x[0] == code for x in stocks_):             # 상위 후보에는 없고 테마 구성 종목으로만 걸린 경우: 약한 후보로 넣음
            m = next((x for x in json.loads(mb or "[]") if x[0] == code), None)
            if m:
                stocks_.append([m[0], m[1], m[3], f"테마·업종 {m[2]}의 구성 종목"])
        cand[k] = (json.loads(c or "[]"), stocks_)
    for (k,) in _q(conn, "SELECT ref FROM legis_feedback WHERE scope='bill' AND code=? AND verdict='yes'", (code,)):
        if k not in cand:
            row = _q(conn, "SELECT concepts, stocks FROM bill_impact_cache WHERE key=?", (k,))
            cand[k] = (json.loads(row[0][0] or "[]"), json.loads(row[0][1] or "[]")) if row else ([], [])
    if not cand:
        return []
    fb = feedback(conn)
    out = []
    for key, (concepts, stocks) in cand.items():
        kept, _, _ = apply_feedback(stocks, concepts, key, fb)
        hit = next((x for x in kept if x[0] == code), None)
        if not hit:
            continue
        pi = _q(conn, "SELECT title, date, url, status FROM policy_items WHERE key=?", (key,))
        if not pi or status_kind(pi[0][3]) == "폐기":
            continue
        title, date, url, status = pi[0]
        tr = [(d, st) for d, st in _q(conn, "SELECT date, stage FROM bill_track WHERE bill_key=? ORDER BY date", (key,))]
        idx, label, last = stage_info(tr, status)
        pm = _q(conn, "SELECT law, promulgated, effective, kind FROM bill_promul WHERE bill_key=?", (key,))
        promul_row = dict(law=pm[0][0], promulgated=pm[0][1], effective=pm[0][2], kind=pm[0][3]) if pm else None
        tx = _q(conn, "SELECT text FROM bill_text WHERE bill_key=? AND text != ''", (key,))
        txt = tx[0][0] if tx else ""
        out.append(dict(key=key, name=bill_name(title), kind=status_kind(status), stage=idx, stage_label=label, last=last or date or "",
                        started=next((d for d, s in tr if s.startswith("발의")), date or ""), promul=promulgation(status, tr, promul_row, txt),
                        score=hit[2], evidence=hit[3], summary=summary_line(txt), nature=nature_hint(title, txt), has_text=bool(txt)))
    out.sort(key=lambda r: (r["stage"], r["last"], r["score"]), reverse=True)
    return out[:limit]


# ---------------- 입법 과정별 주가 변화 (법안 하나) ----------------
_STAGE_PREFIX = (("발의", "발의"), ("소관위 회부", "소관위 회부"), ("소관위 상정", "소관위 상정"), ("소관위 처리", "소관위 처리"),
                 ("법사위 처리", "법사위 처리"), ("본회의 처리", "본회의 처리"))


def stage_events(conn, key, today=None):
    """이 법안의 입법 단계 날짜 + 공포 + 시행 → [dict(date, label, kind('stage'|'promul'|'effective'), future(bool), est(bool))] 날짜순.
    공포는 법제처에서 확인된 날짜가 있으면 확정, 통과했지만 아직이면 '예상'(의결+2주, 주가 계산 없음). 시행일은 공포가 확인됐을 때만 알 수 있어요."""
    today = (today or dt.date.today()).isoformat()
    out = []
    seen = set()
    for d, st in _q(conn, "SELECT date, stage FROM bill_track WHERE bill_key=? ORDER BY date", (key,)):
        lab = next((name for pre, name in _STAGE_PREFIX if st.startswith(pre)), st)
        if (lab, d) in seen:
            continue
        seen.add((lab, d))
        out.append(dict(date=d, label=lab, kind="stage", future=d > today, est=False))
    pm = _q(conn, "SELECT promulgated, effective FROM bill_promul WHERE bill_key=?", (key,))
    if pm and pm[0][0]:
        out.append(dict(date=pm[0][0], label="공포", kind="promul", future=pm[0][0] > today, est=False))
        eff = pm[0][1]
        if eff:
            out.append(dict(date=eff, label="시행", kind="effective", future=eff > today, est=False))
    out.sort(key=lambda e: (e["date"], {"stage": 0, "promul": 1, "effective": 2}[e["kind"]]))
    return out


def price_series(conn, code):
    """종목 일별 종가 [(날짜, 종가)]: 입법 주가 분석용(study_px, 2015~) 우선, 없으면 법안 감시용(bill_px)"""
    for tbl in ("study_px", "bill_px"):
        rows = _q(conn, f"SELECT d, close FROM {tbl} WHERE code=? ORDER BY d", (code,))
        if len(rows) >= 30:
            return [(r[0], r[1]) for r in rows]
    return []


def market_of(conn, code):
    for tbl in ("bill_stocks", "study_stock"):
        r = _q(conn, f"SELECT market FROM {tbl} WHERE code=?", (code,))
        if r and r[0][0]:
            return r[0][0]
    return ""


def reaction_ext(stock_pts, index_pts, date):
    """bill_watch.reaction(선반영 5일·이후 1/5/20일, 지수 대비 %p)에 '단계 직전 종가 이후 현재까지(since)'를 더함. 계산 못 하면 None"""
    import bill_watch
    r = bill_watch.reaction(stock_pts, index_pts, date)
    if not r:
        return None
    t = next((i for i, (d, _) in enumerate(stock_pts) if d >= date), None)
    if t is not None and t >= 1:
        base, last = stock_pts[t - 1][1], stock_pts[-1][1]
        ib, il = bill_watch._idx_on_or_before(index_pts, stock_pts[t - 1][0]), bill_watch._idx_on_or_before(index_pts, stock_pts[-1][0])
        r["since"] = ((last / base - 1) - (il / ib - 1)) * 100 if base and ib and il else None
        r["since_n"] = len(stock_pts) - 1 - t + 1
    else:
        r["since"], r["since_n"] = None, 0
    return r


def bill_stage_prices(conn, key, stocks, today=None):
    """법안 하나 × 종목 여럿의 입법 과정별 주가 변화. stocks=[(코드, 이름)] → [dict(code, name, has_prices, rows=[dict(event, reaction|None)])]"""
    import stock_market
    events = stage_events(conn, key, today)
    out = []
    for code, name in stocks:
        px = price_series(conn, code)
        mk = market_of(conn, code)
        idx = list(stock_market.series(conn, "idx:KOSDAQ" if mk == "KOSDAQ" else "idx:KOSPI"))
        rows = []
        for e in events:
            react = None if e["future"] or not px else reaction_ext(px, idx, e["date"])
            rows.append(dict(event=e, reaction=react))
        out.append(dict(code=code, name=name, has_prices=bool(px), last_price_date=px[-1][0] if px else "", market=mk or "KOSPI", rows=rows))
    return out
