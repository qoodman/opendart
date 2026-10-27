# -*- coding: utf-8 -*-
"""
DART 단순투자 평균매수단가 — 창 화면 앱 (Windows · macOS 공용)

실행: python dart_app.py
점검: python dart_app.py --selftest   (분석·엑셀 저장이 되는지 확인 후 종료)
"""
import os, sys, json, queue, threading, time, traceback, subprocess, datetime as dt, multiprocessing
from pathlib import Path

APP_TITLE = "DART 단순투자 평균매수단가"
SETTINGS = Path.home() / ".dart_simple_invest.json"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

COLORS = {"idle": "#9a9a9a", "ok": "#1f9d55", "warn": "#d69e00", "bad": "#d64545", "done": "#2b6cb0"}


def default_out_dir():
    docs = Path.home() / "Documents"
    return str((docs if docs.is_dir() else Path.home()) / "DART단순투자")


def open_path(p):
    try:
        if IS_WIN:
            os.startfile(p)  # noqa
        elif IS_MAC:
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


class KeepAwake:
    """작업 중 컴퓨터가 잠자기로 들어가 다운로드가 끊기지 않게 함 (화면은 꺼져도 됨)"""

    def __init__(self):
        self.proc = None

    def on(self):
        try:
            if IS_WIN:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            elif IS_MAC and self.proc is None:
                self.proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except Exception:
            pass

    def off(self):
        try:
            if IS_WIN:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            elif self.proc is not None:
                self.proc.terminate()
                self.proc = None
        except Exception:
            pass


def fmt_min(sec):
    if sec is None:
        return "-"
    m = int(sec // 60)
    return f"{m // 60}시간 {m % 60}분" if m >= 60 else f"{m}분"


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        self.tk, self.ttk = tk, ttk
        self.root = root
        self.q = queue.Queue()
        self.engine = None
        self.worker = None
        self.awake = KeepAwake()
        self.last_path = None
        self.log_file = None

        root.title(APP_TITLE)
        root.minsize(620, 560)
        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        s = self._load_settings()
        today = dt.date.today()
        self.v = {
            "api_key": tk.StringVar(value=s.get("api_key", "")),
            "start": tk.StringVar(value=s.get("start", f"{today.year}0101")),
            "end": tk.StringVar(value=s.get("end", today.strftime("%Y%m%d"))),
            "keyword": tk.StringVar(value=s.get("keyword", "상황보고")),
            "body_word": tk.StringVar(value=s.get("body_word", "단순투자")),
            "out_dir": tk.StringVar(value=s.get("out_dir", default_out_dir())),
        }

        # ---- 설정 ----
        r = 0
        ttk.Label(main, text="API 키").grid(row=r, column=0, sticky="w", pady=3)
        self.key_entry = ttk.Entry(main, textvariable=self.v["api_key"], show="•")
        self.key_entry.grid(row=r, column=1, sticky="ew", pady=3)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(main, text="보기", variable=self.show_key,
                        command=lambda: self.key_entry.config(show="" if self.show_key.get() else "•")
                        ).grid(row=r, column=2, sticky="w", padx=(6, 0))
        r += 1
        ttk.Label(main, text="기간").grid(row=r, column=0, sticky="w", pady=3)
        dates = ttk.Frame(main)
        dates.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Entry(dates, textvariable=self.v["start"], width=10).pack(side="left")
        ttk.Label(dates, text=" ~ ").pack(side="left")
        ttk.Entry(dates, textvariable=self.v["end"], width=10).pack(side="left")
        ttk.Label(dates, text="  (예: 20260101)", foreground="#777").pack(side="left")
        r += 1
        ttk.Label(main, text="보고서명 검색어").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(main, textvariable=self.v["keyword"], width=20).grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        ttk.Label(main, text="본문 포함 단어").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(main, textvariable=self.v["body_word"], width=20).grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        ttk.Label(main, text="저장 폴더").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(main, textvariable=self.v["out_dir"]).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(main, text="찾아보기…", command=self._pick_dir).grid(row=r, column=2, padx=(6, 0))
        r += 1

        # ---- 버튼 ----
        btns = ttk.Frame(main)
        btns.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        self.btn_start = ttk.Button(btns, text="▶ 시작", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="■ 중지", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.btn_excel = ttk.Button(btns, text="엑셀 열기", command=lambda: open_path(self.last_path),
                                    state="disabled")
        self.btn_excel.pack(side="right")
        ttk.Button(btns, text="폴더 열기", command=self._open_dir).pack(side="right", padx=6)
        r += 1

        # ---- 상태 ----
        box = ttk.LabelFrame(main, text="상태", padding=10)
        box.grid(row=r, column=0, columnspan=3, sticky="ew")
        box.columnconfigure(1, weight=1)
        self.light = tk.Label(box, text="●", font=("", 26), fg=COLORS["idle"])
        self.light.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        self.lbl_state = ttk.Label(box, text="대기 중", font=("", 13, "bold"))
        self.lbl_state.grid(row=0, column=1, sticky="w")
        self.lbl_hint = ttk.Label(box, text="설정을 확인하고 ▶ 시작을 누르세요.", foreground="#666",
                                  wraplength=520, justify="left")
        self.lbl_hint.grid(row=1, column=1, sticky="w")
        self.bar = ttk.Progressbar(box, mode="determinate", maximum=1)
        self.bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        self.lbl_prog = ttk.Label(box, text="")
        self.lbl_prog.grid(row=3, column=0, columnspan=2, sticky="w")
        self.lbl_stats = ttk.Label(box, text="", foreground="#555")
        self.lbl_stats.grid(row=4, column=0, columnspan=2, sticky="w")
        r += 1

        # ---- 기록 ----
        ttk.Label(main, text="기록").grid(row=r, column=0, sticky="w", pady=(10, 2))
        r += 1
        self.logbox = ScrolledText(main, height=12, state="disabled", wrap="word")
        self.logbox.grid(row=r, column=0, columnspan=3, sticky="nsew")
        main.rowconfigure(r, weight=1)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(500, self.tick)

    # ---------- 설정 저장 ----------
    def _load_settings(self):
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self):
        try:
            SETTINGS.write_text(json.dumps({k: v.get() for k, v in self.v.items()}, ensure_ascii=False),
                                encoding="utf-8")
            if not IS_WIN:
                os.chmod(SETTINGS, 0o600)   # API 키가 들어 있으므로 본인만 읽게
        except Exception:
            pass

    def _pick_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.v["out_dir"].get() or str(Path.home()))
        if d:
            self.v["out_dir"].set(d)

    def _open_dir(self):
        d = self.v["out_dir"].get()
        if d and os.path.isdir(d):
            open_path(d)

    # ---------- 기록 ----------
    def log(self, msg):
        self.q.put(("log", msg))

    def _append(self, msg):
        line = msg if msg.endswith("\n") else msg + "\n"
        self.logbox.config(state="normal")
        self.logbox.insert("end", line)
        n = int(self.logbox.index("end-1c").split(".")[0])
        if n > 3000:
            self.logbox.delete("1.0", f"{n - 3000}.0")
        self.logbox.see("end")
        self.logbox.config(state="disabled")
        if self.log_file:
            try:
                self.log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}")
                self.log_file.flush()
            except Exception:
                pass

    # ---------- 실행 ----------
    def start(self):
        from tkinter import messagebox
        from dart_engine import Config, Engine, DartError
        cfg = Config(**{k: v.get() for k, v in self.v.items()})
        try:
            cfg.validate()
            os.makedirs(cfg.cache, exist_ok=True)
        except DartError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        except OSError as e:
            messagebox.showwarning(APP_TITLE, f"저장 폴더를 만들 수 없습니다.\n{e}")
            return
        self.v["start"].set(cfg.start)
        self.v["end"].set(cfg.end)
        self._save_settings()
        try:
            self.log_file = open(os.path.join(cfg.out_dir, "실행기록.txt"), "a", encoding="utf-8")
        except OSError:
            self.log_file = None

        self.engine = Engine(cfg, log=self.log)
        self.last_path = None
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_excel.config(state="disabled")
        self._append(f"===== 시작 {time.strftime('%Y-%m-%d %H:%M')}  ({cfg.start}~{cfg.end}) =====")
        self.awake.on()
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()

    def _work(self):
        from dart_engine import DartError, Stopped
        try:
            self.q.put(("done", self.engine.run()))
        except Stopped:
            self.q.put(("stopped", None))
        except DartError as e:
            self.q.put(("error", str(e)))
        except Exception as e:
            self.log(traceback.format_exc())
            self.q.put(("error", f"예상치 못한 오류: {type(e).__name__}: {e}"))

    def stop(self):
        if self.engine and self.worker and self.worker.is_alive():
            self.engine.stop.set()
            self.btn_stop.config(state="disabled")
            self._set_state("idle", "중지하는 중…", "진행 중인 요청이 끝나면 멈춥니다(최대 30초).")

    def _finish(self):
        self.awake.off()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None

    # ---------- 화면 갱신 ----------
    def _set_state(self, color, title, hint=""):
        self.light.config(fg=COLORS[color])
        self.lbl_state.config(text=title)
        self.lbl_hint.config(text=hint)

    def tick(self):
        from tkinter import messagebox
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._append(data)
                    continue
                self._finish()
                if kind == "done":
                    self.bar.config(value=self.bar["maximum"])
                    if data["path"]:
                        self.last_path = data["path"]
                        self.btn_excel.config(state="normal")
                        if data["complete"]:
                            self._set_state("done", "완료",
                                            f"{os.path.basename(data['path'])} 저장됨 · 엑셀 열기를 누르세요.")
                        else:
                            self._set_state("warn", "부분 완료",
                                            "다 받지 못해 부분 결과입니다. 다음에 다시 시작하면 남은 것만 이어서 받습니다.")
                    else:
                        self._set_state("done", "완료 (결과 없음)", "조건에 맞는 매매내역이 없습니다.")
                elif kind == "stopped":
                    self._append("중지했습니다. 받은 건 저장돼 있어서 다시 시작하면 이어서 받습니다.")
                    self._set_state("idle", "중지됨", "다시 ▶ 시작을 누르면 이어서 받습니다.")
                elif kind == "error":
                    self._append(f"오류: {data}")
                    self._set_state("bad", "오류", data)
                    messagebox.showerror(APP_TITLE, data)
        except queue.Empty:
            pass

        if self.engine and self.worker and self.worker.is_alive() and not self.engine.stop.is_set():
            self._show_status(self.engine.status())
        elif self.engine:
            s = self.engine.status()
            self.lbl_stats.config(text=f"경과 {fmt_min(s['elapsed'])} · API 호출 {s['calls']}회 · 오류 {s['errors']}회")
        self.root.after(1000, self.tick)

    def _show_status(self, s):
        stage, done, total = s["stage"], s["done"], s["total"]
        if s["uses_api"]:
            idle = s["idle"]
            if idle > 180:
                self._set_state("bad", f"{stage} · 응답 없음",
                                f"{idle / 60:.0f}분째 DART 응답이 없습니다. ■ 중지 후 다시 ▶ 시작하세요. 받은 건 저장돼 있어요.")
            elif idle > 90:
                self._set_state("warn", f"{stage} · 응답 지연",
                                f"{idle:.0f}초째 응답이 없습니다. 3분 넘게 이어지면 중지 후 다시 시작하세요.")
            else:
                self._set_state("ok", f"{stage} · 정상 작동 중", "창을 닫지 말고 기다리세요. 화면이 꺼져도 계속 받습니다.")
            last = f"마지막 응답 {idle:.0f}초 전"
        else:
            self._set_state("ok", f"{stage} · 정상 작동 중",
                            "인터넷을 쓰지 않는 단계라 오래 걸려도 정상입니다." if stage.startswith("3단계") else "")
            last = "API 사용 안 함"
        if total:
            self.bar.config(maximum=total, value=done)
            unit = "구간" if stage.startswith("1단계") else "건"
            eta = f" · 남은 시간 약 {fmt_min(s['eta'])}" if s["eta"] is not None and done < total else ""
            self.lbl_prog.config(text=f"{done:,} / {total:,}{unit} ({done / total:.0%}){eta}")
        else:
            self.bar.config(maximum=1, value=0)
            self.lbl_prog.config(text="")
        self.lbl_stats.config(text=f"경과 {fmt_min(s['elapsed'])} · API 호출 {s['calls']:,}회 · {last} · "
                                   f"대기중 요청 {s['inflight']}개 · 오류 {s['errors']}회")

    def on_close(self):
        from tkinter import messagebox
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "작업 중입니다. 종료할까요?\n받은 건 저장돼 있어서 다음에 이어서 받습니다."):
                return
            if self.engine:
                self.engine.stop.set()
            self.awake.off()
            self.root.destroy()
            os._exit(0)
        self.awake.off()
        self.root.destroy()


def main():
    if "--selftest" in sys.argv:
        from dart_engine import selftest
        try:
            import tkinter  # 설치본에 화면 라이브러리가 들어갔는지 확인
            tkinter.Tcl()
            selftest()
        except Exception:
            traceback.print_exc()
            return 1
        print("selftest ok")
        return 0
    import tkinter as tk
    root = tk.Tk()
    try:
        from tkinter import ttk
        style = ttk.Style(root)
        if IS_WIN and "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()   # 설치본(.exe/.app)에서 병렬 분석이 동작하려면 필요
    sys.exit(main())
