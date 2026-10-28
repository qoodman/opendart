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
import os, sys, json, time, sqlite3, argparse, threading, traceback, datetime as dt, multiprocessing
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ---------------- 설정 ----------------
def load_env(path=ROOT / ".env"):
    """간단한 .env 읽기 (KEY=VALUE, # 주석). 이미 있는 환경 변수가 우선."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)
    except OSError:
        pass


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
SUMMARY_COLS = ["종목명", "종목코드", "제출인", "제출일", "제출일종가", "변동일", "매수수량", "매수금액", "평균매수단가"]
DETAIL_COLS = ["종목명", "종목코드", "회사", "보고서", "제출일", "제출일종가", "접수번호", "보유목적", "제출인",
               "보고자", "변동일", "방법", "종류", "증감", "단가"]


def save_results(detail, summary, run_info):
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
            conn.execute("CREATE INDEX IF NOT EXISTS ix_detail_rno ON detail(접수번호)")
            conn.execute("INSERT INTO runs VALUES (?, ?, 1, ?)",
                         (run_info["started"], now, json.dumps(run_info, ensure_ascii=False)))
    finally:
        conn.close()
    return ([] if first_run else new), first_run


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
        new, first_run = save_results(detail, summary, info)
        write_status(eng, "done", {"new": len(new), "complete": res["complete"]})
        if first_run:
            print(f"첫 실행: 기준선 저장 (공시 {len({d['접수번호'] for d in detail})}건). 다음부터 새 공시를 알립니다.")
        elif new and not args.quiet:
            by_rep = {}
            for d in detail:
                if d["접수번호"] in new:
                    by_rep.setdefault(d["접수번호"], []).append(d)
            lines = describe_new(by_rep)
            body = "\n".join(lines[:8]) + (f"\n…외 {len(lines) - 8}건" if len(lines) > 8 else "")
            try:
                notify(f"새 단순투자 공시 {len(new)}건", body,
                       click=(url.rstrip("/") + "/new") if url else "")
            except Exception as e:
                print(f"알림 실패: {e}", flush=True)
        print(f"완료: 매매내역 {len(detail)}행 · 새 공시 {len(new)}건 · API 호출 {res['calls']}회", flush=True)
        if not res["complete"]:
            print("※ 다 받지 못했습니다(호출 한도 등). 다음 실행 때 이어서 받습니다.")
        return 0


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


def cmd_status(args):
    try:
        print(json.dumps(json.loads(status_file().read_text(encoding="utf-8")), ensure_ascii=False, indent=1))
    except OSError:
        print("아직 실행 기록이 없습니다.")
    return 0


def cmd_set_env(args):
    """.env 의 한 값을 바꿈. 값은 입력(stdin)으로 받음 (API 키가 명령줄에 남지 않게)."""
    value = sys.stdin.read().strip()
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == args.key:
            found = True
            if args.if_empty and line.split("=", 1)[1].strip():
                print(f"{args.key}는 이미 있어서 그대로 둡니다.")
                return 0
            lines[i] = f"{args.key}={value}"
    if not found:
        lines.append(f"{args.key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"{args.key}를 저장했습니다.")
    return 0


def cmd_web(args):
    from dart_web import serve
    serve(results_db(), host=args.host, port=args.port)
    return 0


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description="DART 단순투자 서버")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="수집·분석 한 번 실행")
    r.add_argument("--quiet", action="store_true", help="알림 보내지 않기")
    w = sub.add_parser("web", help="대시보드")
    w.add_argument("--host", default=env("DART_WEB_HOST", "127.0.0.1"))
    w.add_argument("--port", type=int, default=int(env("DART_WEB_PORT", "8766")))
    sub.add_parser("net-check")
    sub.add_parser("notify-test")
    sub.add_parser("status")
    se = sub.add_parser("set-env", help=".env 값 바꾸기 (값은 입력으로)")
    se.add_argument("key")
    se.add_argument("--if-empty", action="store_true", help="비어 있을 때만")
    a = p.parse_args(argv)
    return {"run": cmd_run, "web": cmd_web, "net-check": cmd_net_check, "notify-test": cmd_notify_test,
            "status": cmd_status, "set-env": cmd_set_env}[a.cmd](a)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
