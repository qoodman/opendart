# -*- coding: utf-8 -*-
"""
DART 단순투자 — 서버용 (오라클 클라우드 등 리눅스 서버에서 매일 자동 실행 + 폰 대시보드)

    python dart_server.py run           매일 실행할 수집·분석 (systemd 타이머가 부름)
    python dart_server.py web           폰용 대시보드 (기본 127.0.0.1:8766, Tailscale로만 공개)
    python dart_server.py net-check     서버에서 DART·네이버·ntfy에 접속되는지 확인
    python dart_server.py notify-test   ntfy 알림 시험
    python dart_server.py status        마지막 실행 결과 보기

설정은 같은 폴더의 .env 파일 (.env.example 참고).
"""
import os, re, sys, json, time, sqlite3, argparse, threading, traceback, datetime as dt, multiprocessing
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ---------------- 설정 ----------------
DOTENV_KEYS = set()   # .env에서 읽어 온 값들 (대시보드가 수집을 시작할 때 다시 읽게 빼 줌)


def read_env_file(path=ROOT / ".env"):
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_env(path=ROOT / ".env"):
    """간단한 .env 읽기 (KEY=VALUE, # 주석). 이미 있는 환경 변수가 우선."""
    for k, v in read_env_file(path).items():
        if k not in os.environ:
            os.environ[k] = v
            DOTENV_KEYS.add(k)


def set_env_value(key, value, if_empty=False, path=ROOT / ".env"):
    """.env 의 한 값을 바꿈 (없으면 추가). 바꿨으면 True."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            found = True
            if if_empty and line.split("=", 1)[1].strip():
                return False
            lines[i] = f"{key}={value}"
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return True


def check_dart_key(key):
    """DART API 키 확인. (정상 여부, 설명)"""
    import requests
    try:
        r = requests.get("https://opendart.fss.or.kr/api/list.json",
                         params=dict(crtfc_key=key, page_count=1), timeout=15)
        st = r.json().get("status")
    except Exception as e:
        return None, f"DART에 접속하지 못해 확인은 못 했습니다({type(e).__name__})."
    return {"000": (True, "정상"), "013": (True, "정상"), "010": (False, "등록되지 않은 키입니다."),
            "011": (False, "사용할 수 없는 키입니다."), "012": (False, "접근할 수 없는 IP입니다."),
            "020": (True, "정상 키지만 오늘 호출 한도를 넘었습니다."),
            }.get(st, (None, f"DART 응답 상태 {st}"))


def env(key, default=""):
    v = os.environ.get(key, "").strip()
    return v if v else default


def data_dir():
    d = Path(env("DART_DATA_DIR", str(ROOT / "data"))).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def results_db():
    return data_dir() / "results.sqlite"


def status_file():
    return data_dir() / "status.json"


def make_config():
    from dart_engine import Config
    today = dt.date.today()
    start = env("DART_START") or (today - dt.timedelta(days=int(env("DART_DAYS", "365")))).strftime("%Y%m%d")
    return Config(
        api_key=env("DART_API_KEY"), start=start, end=today.strftime("%Y%m%d"),
        keyword=env("DART_KEYWORD", "상황보고"), body_word=env("DART_BODY_WORD", "단순투자"),
        out_dir=str(data_dir()), add_prices=env("DART_PRICES", "1") != "0",
        workers=int(env("DART_WORKERS", "4")), parse_workers=int(env("DART_PARSE_WORKERS", "1")),
        daily_budget=int(env("DART_DAILY_BUDGET", "39000")), list_refresh=True)


# ---------------- 설정 화면에서 바꿀 수 있는 값 ----------------
AMOUNT_OPTS = {"0": "모든 공시", "100000000": "매수 1억 이상", "1000000000": "매수 10억 이상",
               "10000000000": "매수 100억 이상"}
SETTINGS_SPEC = [
    # (그룹, 키, 이름, 종류, 기본값, 설명)
    ("수집", "DART_DAYS", "조회 기간(일)", "int:30:1825", "365", "오늘부터 며칠 전까지 공시를 봅니다. 늘리면 다음 수집 때 그만큼 더 받습니다."),
    ("수집", "DART_START", "시작일 고정", "date", "", "예: 20260101. 비우면 위의 조회 기간을 씁니다."),
    ("수집", "DART_KEYWORD", "보고서명 검색어", "text", "상황보고", ""),
    ("수집", "DART_BODY_WORD", "본문 포함 단어", "text", "단순투자", "바꾸면 저장된 원문을 다시 분석합니다."),
    ("수집", "DART_RUN_TIMES", "자동 수집 시각", "times", "20:13", "24시간 형식. 여러 번이면 쉼표로: 08:30, 20:13"),
    ("수집", "DART_WEEKDAYS_ONLY", "평일에만 자동 수집", "bool", "0", ""),
    ("수집", "DART_PRICES", "주가 받기 (네이버 증권)", "bool", "1", "제출일 종가·최근 종가·그래프에 필요합니다."),
    ("수집", "HK_CONSENSUS", "증권사 리포트 받기: 한경컨센서스", "bool", "1", "목록에 목표가가 바로 나옵니다."),
    ("수집", "NAVER_RESEARCH", "증권사 리포트 받기: 네이버 증권", "bool", "1",
     "목표가·투자의견은 리포트마다 상세 화면에서 읽습니다(하루 최대 400건)."),
    ("알림", "DART_NOTIFY", "새 공시 알림 보내기", "bool", "1", ""),
    ("알림", "NTFY_TOPIC", "ntfy 주제", "text?", "", "폰 ntfy 앱에서 구독한 주제 이름. 부동산 앱과 같게 두면 한 곳에서 받습니다."),
    ("알림", "DART_NOTIFY_MIN_AMOUNT", "알림 기준", "choice", "0", "이보다 작은 매수만 있는 공시는 알리지 않습니다."),
    ("알림", "DART_NOTIFY_EX_NPS", "국민연금 공시는 알리지 않기", "bool", "0", ""),
    ("업데이트", "DART_AUTO_UPDATE", "매일 새벽 5시에 자동 업데이트", "bool", "0",
     "GitHub에 새 버전이 있으면 받아서 설치합니다. 수집 중이면 다음 날로 미룹니다."),
    ("화면", "DASHBOARD_URL", "이 대시보드 주소", "url", "", "알림을 눌렀을 때 열 주소. 예: https://dart.xxxx.ts.net"),
    ("화면", "REALESTATE_URL", "부동산 대시보드 주소", "url", "", "위쪽 메뉴의 '부동산 ↗' 링크"),
    ("화면", "NAV_LINKS", "다른 대시보드 바로가기", "links", "", "한 줄에 하나씩 '이름 | 주소'. 위쪽 메뉴에 붙습니다."),
    ("성능", "DART_WORKERS", "동시 다운로드 수", "int:1:8", "4", "부동산 앱이 느려지면 2로 줄이세요."),
    ("성능", "DART_PARSE_WORKERS", "동시 분석 수", "int:1:4", "1", "메모리 1GB 서버는 1을 권장합니다."),
]
CHOICES = {"DART_NOTIFY_MIN_AMOUNT": AMOUNT_OPTS}


def parse_links(raw):
    """'이름|주소;;이름|주소' 또는 여러 줄 → [(이름, 주소)]"""
    out = []
    for part in raw.replace(";;", "\n").splitlines():
        if "|" in part:
            name, url = (x.strip() for x in part.split("|", 1))
            if name and url.startswith(("http://", "https://")):
                out.append((name, url))
    return out


def clean_setting(kind, raw):
    """화면에서 들어온 값 → (.env에 쓸 값, 오류 문구 또는 None)"""
    raw = (raw or "").strip()
    if kind.startswith("int"):
        _, lo, hi = kind.split(":")
        if not raw.isdigit() or not int(lo) <= int(raw) <= int(hi):
            return None, f"{lo}~{hi} 사이 숫자로 넣어주세요."
        return raw, None
    if kind == "bool":
        return ("1" if raw in ("1", "on", "true") else "0"), None
    if kind == "date":
        d = re.sub(r"\D", "", raw)
        if not d:
            return "", None
        try:
            dt.datetime.strptime(d, "%Y%m%d")
        except ValueError:
            return None, "20260101처럼 8자리 날짜로 넣어주세요."
        return d, None
    if kind == "times":
        times = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
        out = []
        for t in times:
            m = re.fullmatch(r"(\d{1,2}):?(\d{2})", t)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                return None, "20:13처럼 시:분으로 넣어주세요."
            out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
        if not out:
            return None, "시각을 하나 이상 넣어주세요."
        return ", ".join(sorted(set(out))), None
    if kind == "url":
        if raw and not raw.startswith(("http://", "https://")):
            return None, "https:// 로 시작하는 주소를 넣어주세요."
        return raw.rstrip("/"), None
    if kind == "links":
        links = parse_links(raw)
        bad = [l for l in raw.splitlines() if l.strip() and "|" not in l]
        if bad:
            return None, f"'이름 | 주소' 형식이 아닌 줄이 있습니다: {bad[0][:30]}"
        return ";;".join(f"{n}|{u}" for n, u in links), None
    if kind == "choice":
        return raw, None
    if kind == "text" and not raw:
        return None, "비워 둘 수 없습니다."
    if any(c in raw for c in "\n\r"):
        return None, "한 줄로 넣어주세요."
    return raw, None


def current_settings():
    vals = read_env_file()
    return {key: vals.get(key, "") or default for _, key, _, _, default, _ in SETTINGS_SPEC}


def apply_settings(form, only=None):
    """설정 화면 저장. only를 주면 그 키들만. (바뀐 이름 목록, {키: 오류})"""
    cur, changed, errors = current_settings(), [], {}
    for _, key, label, kind, default, _ in SETTINGS_SPEC:
        if only is not None and key not in only:
            continue
        if kind == "bool":
            raw = form.get(key, "0")          # 체크 해제된 칸은 전송되지 않음
        elif key not in form:
            continue
        else:
            raw = form[key]
        if kind == "choice" and raw not in CHOICES.get(key, {}):
            continue
        val, err = clean_setting(kind, raw)
        if err:
            errors[key] = err
            continue
        if val != cur.get(key, ""):
            set_env_value(key, val)
            changed.append(label)
    return changed, errors


def run_times():
    out = []
    for t in env("DART_RUN_TIMES", "20:13").split(","):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", t)
        if m:
            out.append(dt.time(int(m.group(1)), int(m.group(2))))
    return sorted(out) or [dt.time(20, 13)]


def next_auto_run(now=None):
    now = now or dt.datetime.now()
    weekdays = env("DART_WEEKDAYS_ONLY", "0") == "1"
    for add in range(0, 8):
        day = now.date() + dt.timedelta(days=add)
        if weekdays and day.weekday() >= 5:
            continue
        for t in run_times():
            when = dt.datetime.combine(day, t)
            if when > now:
                return when
    return None


# ---------------- 알림 (ntfy) ----------------
def notify(title, message, click="", tags=("chart_with_upwards_trend",)):
    """ntfy 앱으로 푸시. NTFY_TOPIC이 없으면 화면에만 출력."""
    topic = env("NTFY_TOPIC")
    print(f"[알림] {title}\n{message}", flush=True)
    if not topic:
        return False
    import requests
    payload = {"topic": topic, "title": title, "message": message, "tags": list(tags)}
    if click:
        payload["click"] = click
    # 한글 제목은 HTTP 헤더에 못 넣어서 JSON 방식으로 보냄
    r = requests.post(env("NTFY_SERVER", "https://ntfy.sh").rstrip("/"), json=payload, timeout=15)
    if r.status_code >= 300:
        raise RuntimeError(f"ntfy 발송 실패: {r.status_code} {r.text[:200]}")
    return True


def won(v):
    try:
        return f"{float(v):,.0f}원"
    except (TypeError, ValueError):
        return "-"


def notify_filter(by_rep):
    """설정의 알림 기준(최소 매수금액, 국민연금 제외)에 맞는 공시만"""
    min_amt = float(env("DART_NOTIFY_MIN_AMOUNT", "0") or 0)
    ex_nps = env("DART_NOTIFY_EX_NPS", "0") == "1"
    out = {}
    for rno, rows in by_rep.items():
        if ex_nps and "국민연금" in (rows[0].get("제출인") or ""):
            continue
        amt = sum((r.get("증감") or 0) * (r.get("단가") or 0) for r in rows if (r.get("증감") or 0) > 0)
        if amt < min_amt:
            continue
        out[rno] = rows
    return out


def describe_new(rows_by_report):
    """새 공시 묶음 → 알림 문장 줄들 (보고서마다 한 줄)"""
    lines = []
    for rno, rows in rows_by_report.items():
        r0 = rows[0]
        buys = [r for r in rows if (r.get("증감") or 0) > 0 and (r.get("단가") or 0) > 0]
        sells = [r for r in rows if (r.get("증감") or 0) < 0]
        who = r0.get("제출인") or ""
        if buys:
            qty = sum(r["증감"] for r in buys)
            avg = sum(r["증감"] * r["단가"] for r in buys) / qty
            txt = f"{r0['종목명']} — {who} 매수 {qty:,.0f}주 평균 {won(avg)}"
        elif sells:
            txt = f"{r0['종목명']} — {who} 매도 {-sum(r['증감'] for r in sells):,.0f}주"
        else:
            txt = f"{r0['종목명']} — {who} 변동 보고"
        if r0.get("제출일종가") is not None:
            txt += f" (제출일 종가 {won(r0['제출일종가'])})"
        lines.append(txt)
    return lines


# ---------------- 결과 저장 ----------------
SUMMARY_COLS = ["종목명", "종목코드", "시장", "제출인", "제출일", "제출일종가", "변동일", "매수수량", "매수금액", "평균매수단가"]
DETAIL_COLS = ["종목명", "종목코드", "회사", "보고서", "제출일", "제출일종가", "접수번호", "보유목적", "제출인",
               "보고자", "변동일", "방법", "종류", "증감", "단가", "시장"]


def save_results(detail, summary, run_info, prices=None, opens=None):
    """대시보드가 읽을 results.sqlite 갱신. 새로 본 공시(접수번호) 목록을 돌려줌."""
    import pandas as pd
    path = results_db()
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS seen (rcept_no TEXT PRIMARY KEY, first_seen TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS runs (started TEXT, finished TEXT, ok INTEGER,"
                     " info TEXT)")
        seen = {r[0] for r in conn.execute("SELECT rcept_no FROM seen")}
        first_run = not seen
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        reports = sorted({d["접수번호"] for d in detail})
        new = [r for r in reports if r not in seen]
        conn.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)",
                         [(r, "기준선" if first_run else now) for r in new])
        conn.commit()
    finally:
        conn.close()
    # 표 두 개는 통째로 바꿈 (읽는 쪽이 중간 상태를 보지 않게 임시 표에 쓴 뒤 이름 바꾸기)
    conn = sqlite3.connect(path)
    try:
        df = pd.DataFrame(detail) if detail else pd.DataFrame(columns=DETAIL_COLS)
        if summary.empty and not len(summary.columns):
            summary = pd.DataFrame(columns=SUMMARY_COLS)
        df.to_sql("detail_new", conn, if_exists="replace", index=False)
        summary.to_sql("summary_new", conn, if_exists="replace", index=False)
        with conn:
            conn.execute("DROP TABLE IF EXISTS detail")
            conn.execute("DROP TABLE IF EXISTS summary")
            conn.execute("ALTER TABLE detail_new RENAME TO detail")
            conn.execute("ALTER TABLE summary_new RENAME TO summary")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_detail_code ON detail(종목코드)")
            if prices is not None:
                write_prices(conn, prices, opens)
            conn.execute("CREATE INDEX IF NOT EXISTS ix_detail_rno ON detail(접수번호)")
            conn.execute("INSERT INTO runs VALUES (?, ?, 1, ?)",
                         (run_info["started"], now, json.dumps(run_info, ensure_ascii=False)))
    finally:
        conn.close()
    return ([] if first_run else new), first_run


def add_report_prices(eng, prices):
    """리포트가 나온 종목도 주가를 받아 둠 (리포트 날 종가·상승여력·그래프용). 하루 한 번 받은 건 재사용."""
    from concurrent.futures import ThreadPoolExecutor
    try:
        conn = sqlite3.connect(results_db())
        since = (dt.date.today() - dt.timedelta(days=200)).isoformat()
        rows = conn.execute("SELECT code, MIN(date) FROM reports WHERE date >= ? GROUP BY code", (since,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return prices
    need = {c: d.replace("-", "") for c, d in rows if c and c not in prices}
    if not need:
        return prices
    print(f"[리포트] 리포트 종목 주가 {len(need)}개 받는 중", flush=True)
    out = dict(prices)

    def one(item):
        code, since_d = item
        try:
            return code, eng.fetch_prices(code, since_d)
        except Exception:
            return code, {}
    with ThreadPoolExecutor(max(1, min(4, eng.cfg.workers))) as ex:
        for code, p in ex.map(one, need.items()):
            if p:
                out[code] = p
    print(f"[리포트] 리포트 종목 주가 {sum(1 for c in need if c in out)}/{len(need)}개", flush=True)
    return out


def write_prices(conn, prices, opens=None):
    """그래프·리포트용 일별 시가·종가를 prices 표에 덧붙임 (같은 날은 새 값으로)"""
    conn.execute("CREATE TABLE IF NOT EXISTS prices (code TEXT, date TEXT, close REAL, open REAL, "
                 "PRIMARY KEY (code, date))")
    if "open" not in {r[1] for r in conn.execute("PRAGMA table_info(prices)")}:
        conn.execute("ALTER TABLE prices ADD COLUMN open REAL")
    opens = opens or {}
    conn.executemany("INSERT OR REPLACE INTO prices (code, date, close, open) VALUES (?, ?, ?, ?)",
                     [(c, f"{d[:4]}-{d[4:6]}-{d[6:]}", v, opens.get(c, {}).get(d))
                      for c, ps in prices.items() for d, v in ps.items()])


def reports_meta_path():
    return data_dir() / "reports_last.json"


def record_reports(result):
    try:
        reports_meta_path().write_text(json.dumps(
            {"finished": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "result": result}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


def cmd_run_reports(args):
    """리포트만 수집: 한경컨센서스·네이버 리포트 + 리포트 종목 주가 (DART 공시는 건드리지 않음)"""
    from dart_engine import Engine
    import research
    cfg = make_config()
    with RunLock():
        os.makedirs(cfg.cache, exist_ok=True)
        eng = Engine(cfg)
        eng._set_stage("리포트 수집")
        stop = threading.Event()

        def beat():
            while not stop.wait(5):
                write_status(eng, "running")
        threading.Thread(target=beat, daemon=True).start()
        write_status(eng, "running")
        try:
            res = research.sync_all(results_db(), debug_dir=data_dir(), log=print,
                                    use_hk=env("HK_CONSENSUS", "1") != "0", use_nv=env("NAVER_RESEARCH", "1") != "0")
            record_reports(res)
            if env("DART_PRICES", "1") != "0":
                eng._set_stage("리포트 종목 주가")
                prices = add_report_prices(eng, {})
                conn = sqlite3.connect(results_db())
                with conn:
                    write_prices(conn, prices, eng.opens)
                conn.close()
        except Exception as e:
            stop.set()
            traceback.print_exc()
            write_status(eng, "failed", {"error": f"리포트 수집 실패: {e}"})
            return 1
        stop.set()
        write_status(eng, "done", {"stage": "리포트 수집 완료"})
        print(f"리포트 수집 완료: {res}", flush=True)
        return 0


def record_failure(started, message):
    try:
        conn = sqlite3.connect(results_db())
        conn.execute("CREATE TABLE IF NOT EXISTS runs (started TEXT, finished TEXT, ok INTEGER, info TEXT)")
        conn.execute("INSERT INTO runs VALUES (?, ?, 0, ?)",
                     (started, dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                      json.dumps({"error": message}, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------- 매일 실행 ----------------
class RunLock:
    """수집이 두 개 동시에 돌지 않게 (타이머 + 폰의 '지금 수집')"""

    def __init__(self):
        self.path = data_dir() / "run.lock"
        self.f = None

    def __enter__(self):
        import fcntl
        self.f = open(self.path, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise SystemExit("이미 수집이 진행 중입니다.")
        return self

    def __exit__(self, *a):
        self.f.close()


def is_running():
    import fcntl
    try:
        with open(data_dir() / "run.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


def write_status(eng, state, extra=None):
    s = eng.status() if eng else {}
    s.update(state=state, updated=time.time(), heartbeat=eng.heartbeat_line() if eng else "")
    if extra:
        s.update(extra)
    tmp = status_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, status_file())


def cmd_run(args):
    only = getattr(args, "only", None) or "all"
    if only == "reports":
        return cmd_run_reports(args)
    from dart_engine import Engine, DartError, Stopped, summarize, HEARTBEAT
    cfg = make_config()
    started = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    url = env("DASHBOARD_URL")
    with RunLock():
        eng = Engine(cfg)
        stop = threading.Event()

        def beat():   # 30초마다 생존 신호 + 대시보드용 진행 상황
            n = 0
            while not stop.wait(5):
                write_status(eng, "running")
                n += 5
                if n % HEARTBEAT == 0:
                    print(eng.heartbeat_line(), flush=True)
        threading.Thread(target=beat, daemon=True).start()
        write_status(eng, "running")
        print(f"DART 수집 시작 {started}  기간 {cfg.start}~{cfg.end}", flush=True)
        try:
            res = eng.run()
        except (DartError, Stopped) as e:
            stop.set()
            msg = str(e) or "중지됨"
            write_status(eng, "failed", {"error": msg})
            record_failure(started, msg)
            if not args.quiet:
                try:
                    notify("DART 수집 실패", msg, click=url, tags=("warning",))
                except Exception as ne:
                    print(f"알림 실패: {ne}", flush=True)
            return 1
        except Exception as e:
            stop.set()
            traceback.print_exc()
            msg = f"예상치 못한 오류: {type(e).__name__}: {e}"
            write_status(eng, "failed", {"error": msg})
            record_failure(started, msg)
            try:
                notify("DART 수집 실패", msg, click=url, tags=("warning",))
            except Exception:
                pass
            return 1
        stop.set()
        detail = getattr(eng, "detail", [])
        summary, _ = summarize(detail)
        info = dict(started=started, start=cfg.start, end=cfg.end, calls=res["calls"],
                    complete=res["complete"], rows=len(detail), excel=res["path"] or "")
        use_hk = only == "all" and env("HK_CONSENSUS", "1") != "0"
        use_nv = only == "all" and env("NAVER_RESEARCH", "1") != "0"
        if use_hk or use_nv:
            # 증권사 리포트·목표주가. 실패해도 DART 결과는 그대로 저장
            write_status(eng, "running", {"stage": "증권사 리포트"})
            try:
                import research
                info["reports"] = research.sync_all(results_db(), debug_dir=data_dir(), log=print,
                                                    use_hk=use_hk, use_nv=use_nv)
                record_reports(info["reports"])
            except Exception as e:
                info["reports"] = {"error": str(e)[:300]}
                print(f"리포트 수집 실패: {e}", flush=True)
        prices = getattr(eng, "prices", None)
        if prices is not None and (use_hk or use_nv):
            write_status(eng, "running", {"stage": "리포트 종목 주가"})
            try:
                prices = add_report_prices(eng, prices)
            except Exception as e:
                print(f"리포트 종목 주가 실패: {e}", flush=True)
        new, first_run = save_results(detail, summary, info, prices, getattr(eng, "opens", None))
        write_status(eng, "done", {"new": len(new), "complete": res["complete"]})
        if first_run:
            print(f"첫 실행: 기준선 저장 (공시 {len({d['접수번호'] for d in detail})}건). 다음부터 새 공시를 알립니다.")
        elif new and not args.quiet and env("DART_NOTIFY", "1") != "0":
            by_rep = {}
            for d in detail:
                if d["접수번호"] in new:
                    by_rep.setdefault(d["접수번호"], []).append(d)
            by_rep = notify_filter(by_rep)
            lines = describe_new(by_rep)
            body = "\n".join(lines[:8]) + (f"\n…외 {len(lines) - 8}건" if len(lines) > 8 else "")
            try:
                if by_rep:
                    notify(f"새 단순투자 공시 {len(by_rep)}건", body,
                           click=(url.rstrip("/") + "/new") if url else "")
            except Exception as e:
                print(f"알림 실패: {e}", flush=True)
        print(f"완료: 매매내역 {len(detail)}행 · 새 공시 {len(new)}건 · API 호출 {res['calls']}회", flush=True)
        if not res["complete"]:
            print("※ 다 받지 못했습니다(호출 한도 등). 다음 실행 때 이어서 받습니다.")
        return 0


def last_run_started():
    try:
        conn = sqlite3.connect(results_db())
        row = conn.execute("SELECT MAX(started) FROM runs").fetchone()
        conn.close()
        return row[0] or ""
    except sqlite3.Error:
        return ""


def cmd_tick(args):
    """10분마다 타이머가 부름: 설정한 자동 수집 시각이 지났고 아직 안 돌았으면 수집."""
    now = dt.datetime.now()
    try:
        auto_update_tick(now)
    except Exception as e:
        print(f"자동 업데이트 확인 실패: {e}", flush=True)
    if env("DART_WEEKDAYS_ONLY", "0") == "1" and now.weekday() >= 5:
        return 0
    due = [dt.datetime.combine(now.date(), t) for t in run_times() if dt.datetime.combine(now.date(), t) <= now]
    if not due:
        return 0
    slot = max(due).strftime("%Y-%m-%d %H:%M")
    state = data_dir() / "last_auto.txt"
    done = state.read_text().strip() if state.exists() else ""
    if not done and last_run_started() >= slot:
        done = slot          # 처음 바꾼 날: 이미 그 시각 이후에 돌았으면 다시 돌지 않음
        state.write_text(done)
    if done >= slot or is_running():
        return 0
    state.write_text(slot)   # 실패해도 10분마다 되풀이하지 않게 먼저 기록
    print(f"자동 수집 시각 {slot} → 수집 시작", flush=True)
    return cmd_run(argparse.Namespace(quiet=False, only="all"))


# ---------------- 프로그램 업데이트 ----------------
def _git(*args, timeout=60):
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def update_log():
    return data_dir() / "update.log"


def update_running():
    import subprocess
    r = subprocess.run(["systemctl", "is-active", "--quiet", "dart-update"], capture_output=True)
    return r.returncode == 0


def update_info():
    """설정 화면용: 지금 버전, 마지막 확인 결과, 마지막 업데이트 기록"""
    _, cur = _git("log", "-1", "--format=%h · %cd", "--date=format:%Y-%m-%d %H:%M")
    try:
        check = json.loads((data_dir() / "update_check.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        check = {}
    try:
        log = update_log().read_text(encoding="utf-8").splitlines()[-12:]
    except OSError:
        log = []
    return dict(current=cur, check=check, log=log, running=update_running())


def check_update():
    """GitHub에 새 버전이 있는지 확인 → {'behind': n, 'items': [제목…]} 또는 {'error': …}"""
    code, out = _git("fetch", "-q", timeout=60)
    if code != 0:
        res = {"error": f"GitHub 확인 실패: {out[-200:]}"}
    else:
        _, n = _git("rev-list", "--count", "HEAD..@{u}")
        _, items = _git("log", "--format=%s", "HEAD..@{u}")
        res = {"behind": int(n or 0), "items": items.splitlines()[:10]}
    res["checked"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    (data_dir() / "update_check.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def start_update():
    """업데이트를 별도 작업으로 시작 (대시보드가 재시작돼도 끊기지 않게). (성공 여부, 문구)"""
    import subprocess, pwd
    if is_running():
        return False, "수집 중에는 업데이트하지 않습니다. 끝난 뒤 다시 눌러 주세요."
    if update_running():
        return False, "이미 업데이트 중입니다."
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        return False, "이 서버에서는 화면에서 업데이트할 권한이 없습니다. SSH로 scripts/update_server.sh를 실행하세요."
    user = pwd.getpwuid(os.getuid())
    log = update_log()
    cmd = ["sudo", "-n", "systemd-run", "--unit=dart-update", "--collect", f"--uid={user.pw_name}",
           f"--setenv=HOME={user.pw_dir}", f"--setenv=PATH={user.pw_dir}/.local/bin:/usr/local/bin:/usr/bin:/bin",
           "/bin/bash", "-c", f"'{ROOT}/scripts/update_server.sh' >> '{log}' 2>&1"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return False, f"업데이트를 시작하지 못했습니다: {(r.stderr or r.stdout)[-200:]}"
    return True, "업데이트를 시작했습니다. 1~3분 뒤 이 화면을 새로고침하세요. 중간에 화면이 잠깐 끊길 수 있습니다."


def auto_update_tick(now):
    """설정에서 켜면 매일 05:07 이후 한 번 업데이트"""
    if env("DART_AUTO_UPDATE", "0") != "1" or now.time() < dt.time(5, 7):
        return
    state = data_dir() / "last_update_day.txt"
    today = now.date().isoformat()
    if (state.read_text().strip() if state.exists() else "") == today or is_running():
        return
    state.write_text(today)
    res = check_update()
    if res.get("behind"):
        ok, msg = start_update()
        print(f"자동 업데이트: {msg}", flush=True)


def cmd_update(args):
    import subprocess
    return subprocess.call(["bash", str(ROOT / "scripts" / "update_server.sh")])


# ---------------- 그 밖의 명령 ----------------
def cmd_net_check(args):
    import requests
    checks = [
        ("DART OpenAPI", f"https://opendart.fss.or.kr/api/list.json?crtfc_key={env('DART_API_KEY', 'x')}&page_count=1"),
        ("네이버 증권 시세", "https://fchart.stock.naver.com/sise.nhn?symbol=005930&timeframe=day&count=2&requestType=0"),
        ("ntfy 알림", env("NTFY_SERVER", "https://ntfy.sh")),
    ]
    bad = 0
    for name, url in checks:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            note = ""
            if name.startswith("DART"):
                st = r.json().get("status")
                note = {"000": "정상", "013": "정상(조회 결과 없음)", "010": "API 키가 등록되지 않음",
                        "011": "사용할 수 없는 키", "020": "오늘 호출 한도 초과"}.get(st, f"상태 {st}")
                ok = st in ("000", "013")
            elif name.startswith("네이버"):
                ok = b"item data=" in r.content
                note = "정상" if ok else "시세 형식이 다름"
            else:
                ok = r.status_code < 500
                note = f"HTTP {r.status_code}"
        except Exception as e:
            ok, note = False, type(e).__name__
        bad += not ok
        print(f"  {'✔' if ok else '✖'} {name}: {note}")
    if bad:
        print("\n✖ 항목이 있습니다. 해외 서버라면 그 사이트가 해외 접속을 막는 것일 수 있습니다.")
    return 1 if bad else 0


def cmd_notify_test(args):
    if not env("NTFY_TOPIC"):
        print("NTFY_TOPIC이 비어 있습니다. .env에 부동산 앱과 같은 주제를 넣으세요.")
        return 1
    notify("DART 알림 시험", "이 알림이 보이면 설정 완료입니다.", click=env("DASHBOARD_URL"))
    print("보냈습니다. 폰의 ntfy 앱을 확인하세요.")
    return 0


def cmd_notify_test_web():
    """설정 화면의 '시험 알림' 버튼. (성공 여부, 문구)"""
    if not env("NTFY_TOPIC"):
        return False, "ntfy 주제가 비어 있습니다."
    try:
        notify("DART 알림 시험", "이 알림이 보이면 설정 완료입니다.", click=env("DASHBOARD_URL"))
        return True, "시험 알림을 보냈습니다. 폰의 ntfy 앱을 확인하세요."
    except Exception as e:
        return False, f"보내지 못했습니다: {e}"


def cmd_status(args):
    try:
        print(json.dumps(json.loads(status_file().read_text(encoding="utf-8")), ensure_ascii=False, indent=1))
    except OSError:
        print("아직 실행 기록이 없습니다.")
    return 0


def cmd_set_env(args):
    """.env 의 한 값을 바꿈. 값은 입력(stdin)으로 받음 (API 키가 명령줄에 남지 않게)."""
    value = sys.stdin.read().strip()
    if set_env_value(args.key, value, if_empty=args.if_empty):
        print(f"{args.key}를 저장했습니다.")
    else:
        print(f"{args.key}는 이미 있어서 그대로 둡니다.")
    return 0


def cmd_hk_check(args):
    """리포트(한경컨센서스·네이버 증권)를 한 번 받아 보고 결과를 보여 줌 (설치 후 확인용)"""
    import research
    res = research.sync_all(results_db(), debug_dir=data_dir(), log=print, first_days=args.days or None,
                            use_hk=args.source in ("all", "hk"), use_nv=args.source in ("all", "nv"))
    conn = sqlite3.connect(results_db())
    bad = 0
    for src, label in research.SOURCES.items():
        if src not in res:
            continue
        cnt = conn.execute("SELECT COUNT(*), COUNT(target), COUNT(DISTINCT code) FROM reports WHERE source=?",
                           (src,)).fetchone()
        if isinstance(res[src], str):
            bad += 1
            print(f"✖ {label}: {res[src]}")
        else:
            print(f"✔ {label}: 저장된 리포트 {cnt[0]}건 (목표가 있는 것 {cnt[1]}건, 종목 {cnt[2]}개), 이번에 새로 {res[src]}건")
    q = "SELECT source, date, name, code, broker, target, opinion FROM reports"
    rows = conn.execute(q + (" WHERE code=?" if args.code else "") + " ORDER BY date DESC LIMIT 10",
                        (args.code,) if args.code else ()).fetchall()
    for r in rows:
        print("  ", " | ".join(str(x) for x in r))
    if args.code:
        avg, n_b = research.consensus(conn, args.code)
        print(f"  → 증권사 {n_b}곳 목표가 평균 {avg:,.0f}원" if avg else "  → 최근 180일 목표가 없음")
    return 1 if bad else 0


def cmd_web(args):
    from dart_web import serve
    # 수집을 시작할 때 .env를 새로 읽도록, .env에서 온 값은 빼고 넘김
    child_env = {k: v for k, v in os.environ.items() if k not in DOTENV_KEYS}

    def save_key(key):
        ok, why = check_dart_key(key)
        if ok is False:
            return False, why
        set_env_value("DART_API_KEY", key)
        return True, why
    def settings_api():
        # 대시보드가 매 요청마다 .env를 새로 읽게 (설정 화면에서 바꾼 값이 바로 반영)
        os.environ.update({k: v for k, v in read_env_file().items() if k in DOTENV_KEYS or k not in os.environ})
        DOTENV_KEYS.update(read_env_file())
        return dict(spec=SETTINGS_SPEC, choices=CHOICES, values=current_settings(), apply=apply_settings,
                    next_run=next_auto_run(), links=parse_links(env("NAV_LINKS")),
                    realestate=env("REALESTATE_URL"), test_notify=cmd_notify_test_web,
                    update_info=update_info, check_update=check_update, start_update=start_update)
    serve(results_db(), host=args.host, port=args.port, child_env=child_env,
          get_key=lambda: read_env_file().get("DART_API_KEY", "") or os.environ.get("DART_API_KEY", ""),
          save_key=save_key, settings_api=settings_api)
    return 0


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description="DART 단순투자 서버")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="수집·분석 한 번 실행")
    r.add_argument("--quiet", action="store_true", help="알림 보내지 않기")
    r.add_argument("--only", choices=["all", "dart", "reports"], default="all",
                   help="all: 보고서+리포트, dart: DART 보고서만, reports: 증권사 리포트만")
    w = sub.add_parser("web", help="대시보드")
    w.add_argument("--host", default=env("DART_WEB_HOST", "127.0.0.1"))
    w.add_argument("--port", type=int, default=int(env("DART_WEB_PORT", "8766")))
    sub.add_parser("tick", help="자동 수집 시각이면 수집 (타이머용)")
    sub.add_parser("update", help="최신 코드 받아 설치")
    sub.add_parser("net-check")
    sub.add_parser("notify-test")
    sub.add_parser("status")
    for name in ("reports-check", "hk-check"):
        hk = sub.add_parser(name, help="증권사 리포트(한경컨센서스·네이버 증권) 받아 보기")
        hk.add_argument("code", nargs="?", default="", help="종목코드 (예: 005930)")
        hk.add_argument("--days", type=int, default=0, help="처음 받을 기간(일). 기본: 한경 180, 네이버 30")
        hk.add_argument("--source", choices=["all", "hk", "nv"], default="all")
    se = sub.add_parser("set-env", help=".env 값 바꾸기 (값은 입력으로)")
    se.add_argument("key")
    se.add_argument("--if-empty", action="store_true", help="비어 있을 때만")
    a = p.parse_args(argv)
    return {"run": cmd_run, "web": cmd_web, "net-check": cmd_net_check, "notify-test": cmd_notify_test,
            "status": cmd_status, "set-env": cmd_set_env, "hk-check": cmd_hk_check, "reports-check": cmd_hk_check,
            "tick": cmd_tick, "update": cmd_update}[a.cmd](a)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
