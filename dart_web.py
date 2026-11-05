# -*- coding: utf-8 -*-
"""
DART 단순투자 대시보드 (폰용, 파이썬 기본 기능만 사용)

    python dart_server.py web     → http://127.0.0.1:8766
서버에서는 Tailscale(serve)로만 공개해서 내 기기에서만 열립니다.
"""
import os, re, sys, json, time, sqlite3, subprocess, datetime as dt
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from dart_charts import CHART_CSS, CHART_JS, bar_list, price_chart, sparkline
from research import consensus, suspicious

PAGE_SIZE = 50
ROW_CHOICES = (10, 20, 50, 100)

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1b1d21;--muted:#666b73;--line:#e3e6ea;--accent:#2b5a9e;
--chip:#edf2f9;--up:#c23b3b;--down:#2b62c2;--warn:#a5442b}
@media (prefers-color-scheme:dark){:root{--bg:#141518;--card:#1d1f23;--fg:#eceef1;
--muted:#a0a5ad;--line:#30333a;--accent:#86aee8;--chip:#242a34;--up:#ef7d7d;--down:#7ea6ef;--warn:#e08a6f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:14px;
align-items:center;flex-wrap:wrap}header b{font-size:17px}header nav a{margin-right:12px;white-space:nowrap;display:inline-block}
.hrow{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%}
.runmenu{position:relative}.runmenu summary{list-style:none}.runmenu summary::-webkit-details-marker{display:none}
.runmenu .menu{position:absolute;right:0;top:calc(100% + 6px);z-index:5;background:var(--card);border:1px solid var(--line);
border-radius:12px;box-shadow:0 6px 18px rgba(0,0,0,.15);padding:6px;display:flex;flex-direction:column;min-width:210px}
.runmenu .menu button{background:none;color:var(--fg);text-align:left;padding:10px 12px;border-radius:8px}
.runmenu .menu button:hover{background:var(--chip)}.runmenu .menu .muted{margin-left:6px}
.runform{margin:0}.runbtn{font:inherit;font-size:14px;padding:6px 12px;border-radius:999px;border:0;
background:var(--accent);color:var(--bg);cursor:pointer;white-space:nowrap;margin-left:auto}
a.runbtn.busy{background:var(--chip);color:var(--fg);text-decoration:none}
main{max-width:980px;margin:0 auto;padding:14px 16px 40px}
form.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input,select{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:8px;
background:var(--card);color:var(--fg);min-width:0}input[type=search]{flex:1 1 180px}
button{font:inherit;padding:7px 14px;border:0;border-radius:8px;background:var(--accent);
color:var(--bg);cursor:pointer}button.ghost{background:var(--chip);color:var(--fg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 13px;
margin-bottom:8px}.row{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.muted{color:var(--muted);font-size:13px}.chip{display:inline-block;padding:1px 8px;
border-radius:999px;background:var(--chip);font-size:12px;margin-right:4px}
.big{font-size:17px;font-weight:600}.up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}
.status{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--muted);flex:none}
.dot.ok{background:#1f9d55}.dot.run{background:#d69e00}.dot.bad{background:#d64545}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:6px}
.bar span{display:block;height:100%;background:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);
padding:5px 6px;text-align:left;white-space:nowrap}th{color:var(--muted);font-weight:500}
.scroll{overflow-x:auto}.pager{display:flex;gap:6px;margin-top:12px;flex-wrap:wrap;align-items:center}
.pager a,.pager b{min-width:34px;padding:5px 8px;text-align:center;border:1px solid var(--line);
border-radius:8px;background:var(--card)}.pager b.cur{background:var(--accent);color:var(--bg);border-color:var(--accent)}
.num{display:inline-block;min-width:22px;margin-right:6px;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
details summary{cursor:pointer;color:var(--accent);margin-bottom:4px}
label.set{display:block;margin:12px 0 4px;font-weight:500}label.set input[type=checkbox]{width:18px;height:18px;
vertical-align:-3px;margin-right:6px}textarea{font:inherit;padding:7px 9px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg)}
h2{font-size:16px;margin:18px 0 8px}
"""

MANIFEST = json.dumps({
    "name": "DART 단순투자", "short_name": "DART 단순투자", "start_url": "/", "display": "standalone",
    "background_color": "#f7f8fa", "theme_color": "#2b5a9e",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
}, ensure_ascii=False).encode()


def icon_png(size):
    """홈 화면 아이콘: 파란 바탕에 흰 막대그래프 (이미지 라이브러리 없이 PNG 생성)"""
    import struct, zlib
    bg, fg = (43, 90, 158), (255, 255, 255)
    bars = [(0.24, 0.36, 0.58), (0.42, 0.54, 0.40), (0.60, 0.72, 0.26)]   # (x0, x1, top)
    rows = []
    for y in range(size):
        fy = y / size
        row = bytearray([0])
        for x in range(size):
            fx = x / size
            on = any(x0 <= fx <= x1 and top <= fy <= 0.76 for x0, x1, top in bars) or (0.20 <= fx <= 0.80 and 0.78 <= fy <= 0.81)
            row += bytes(fg if on else bg)
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


_ICONS = {}


_NAV = {"realestate": "", "links": [], "status": {}}   # 요청마다 설정·진행 상황으로 새로 채움


def run_widget():
    """모든 화면 위쪽 오른쪽: '지금 수집' 버튼, 수집 중이면 진행률"""
    st = _NAV.get("status") or {}
    if st.get("state") == "running" and time.time() - st.get("updated", 0) < 120:
        total, done = st.get("total") or 0, st.get("done") or 0
        pct = f" {done / total:.0%}" if total else ""
        return (f'<a class="runbtn busy" id="runbusy" href="/">수집 중{pct}</a>'
                "<script>(function(){const b=document.getElementById('runbusy');const t=setInterval(()=>{"
                "fetch('/health').then(r=>r.json()).then(s=>{if(s.state==='running'){"
                "b.textContent='수집 중'+(s.total?' '+Math.round(s.done/s.total*100)+'%':'');}"
                "else{clearInterval(t);b.textContent='수집 끝 · 새로고침';b.href=location.href;}}).catch(()=>{});},10000);})();"
                "</script>")
    return ('<details class="runmenu"><summary class="runbtn">지금 수집 ▾</summary>'
            '<form method="post" action="/run" class="menu">'
            '<button name="what" value="all">전체 <span class="muted">보고서 + 리포트</span></button>'
            '<button name="what" value="dart">보고서만 <span class="muted">DART 공시</span></button>'
            '<button name="what" value="reports">리포트만 <span class="muted">몇 분이면 끝</span></button>'
            '<button name="what" value="market">시장·정책만 <span class="muted">1~2분</span></button>'
            '<button name="what" value="reanalyze">다시 분석만 <span class="muted">DART 호출 없음</span></button>'
            '</form></details>')


def page(title, body, refresh=False, charts=False):
    other = _NAV["realestate"] or os.environ.get("REALESTATE_URL", "").strip()
    extra = f'<a href="{escape(other)}">부동산 ↗</a>' if other else ""
    extra += "".join(f'<a href="{escape(u)}">{escape(n)} ↗</a>' for n, u in _NAV["links"] if u != other)
    meta = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{meta}
<title>{escape(title)}</title><style>{CSS}{CHART_CSS}</style>
<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#2b5a9e">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png">
</head><body>
<header><div class="hrow"><b>DART 단순투자</b>{run_widget()}</div><nav><a href="/">보고서</a><a href="/ideas">아이디어</a><a href="/performance">성적표</a><a href="/reports">리포트</a><a href="/market">시장</a><a href="/policy">정책</a><a href="/calendar">달력</a><a href="/new">새 공시</a>
<a href="/settings">설정</a>{extra}</nav>
</header><main>{body}</main>{"<script>" + CHART_JS + "</script>" if charts else ""}</body></html>""".encode()


# ---------------- 표시 도우미 ----------------
def won(v):
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "-"


def gap(close, avg):
    """제출일 종가가 평균 매수단가보다 몇 % 위·아래인지"""
    try:
        g = float(close) / float(avg) - 1
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    cls = "up" if g > 0 else "down" if g < 0 else ""
    return f'<span class="{cls}">평균 대비 {g:+.1%}</span>'


def dart_link(rno):
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={quote(str(rno))}"


def naver_link(code):
    return f"https://finance.naver.com/item/main.naver?code={quote(str(code))}"


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def load_status(status_path):
    try:
        return json.loads(Path(status_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status_box(conn, st):
    """마지막 수집 상태 + 진행 중이면 진행 막대"""
    state = st.get("state")
    stale = time.time() - st.get("updated", 0) > 120
    if state == "running" and not stale:
        total, done = st.get("total") or 0, st.get("done") or 0
        pct = f"{done / total:.0%}" if total else ""
        eta = st.get("eta")
        eta_t = f" · 남은 시간 약 {eta / 60:.0f}분" if eta else ""
        idle = st.get("idle", 0)
        warn = (' <span class="warn">· DART 응답 지연</span>' if st.get("uses_api") and idle > 90 else "")
        bar = f'<div class="bar"><span style="width:{done / total * 100 if total else 0:.0f}%"></span></div>'
        return (f'<div class="card"><div class="status"><span class="dot run"></span><b>수집 중</b>'
                f'<span class="muted">{escape(st.get("stage", ""))} {done:,}/{total:,} {pct}{eta_t}</span></div>'
                f'<div class="muted">API 호출 {st.get("calls", 0):,}회 · 마지막 응답 {idle:.0f}초 전 · '
                f'오류 {st.get("errors", 0)}회{warn}</div>{bar}</div>'), True
    last = None
    if has_table(conn, "runs"):
        last = conn.execute("SELECT started, finished, ok, info FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    if not last:
        return ('<div class="card"><div class="status"><span class="dot"></span>'
                '<b>아직 수집 결과가 없습니다</b></div>'
                '<div class="muted">위쪽 오른쪽 <b>지금 수집</b>을 누르면 시작합니다.</div></div>'), False
    info = json.loads(last["info"] or "{}")
    if last["ok"]:
        part = "" if info.get("complete", True) else ' · <span class="warn">일부만 받음(다음 실행 때 이어서)</span>'
        txt = (f'<span class="dot ok"></span><b>마지막 수집 {escape(last["finished"])}</b>'
               f'<span class="muted">기간 {escape(info.get("start", ""))}~{escape(info.get("end", ""))} · '
               f'API {info.get("calls", 0):,}회{part}</span>')
    else:
        txt = (f'<span class="dot bad"></span><b>수집 실패 {escape(last["finished"])}</b>'
               f'<span class="muted warn">{escape(info.get("error", ""))}</span>')
    return f'<div class="card"><div class="status" style="flex-wrap:wrap">{txt}</div></div>', False


def run_button():
    return ('<form method="post" action="/run" style="margin-top:6px">'
            '<button class="ghost" type="submit">지금 수집</button></form>')


def eok(v):
    """원 → '12.3억' / '4,500만'"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{v / 1e8:,.1f}억" if v >= 1e8 else f"{v / 1e4:,.0f}만"


def summary_card(r, num=None):
    keys = r.keys()
    market = r["시장"] if "시장" in keys and r["시장"] else ""
    n = f'<span class="num">{num}</span>' if num else ""
    mk = f' <span class="chip">{escape(market)}</span>' if market else ""
    return (f'<div class="card"><div class="row"><div>{n}<a class="big" href="/stock?code={quote(str(r["종목코드"] or ""))}">'
            f'{escape(str(r["종목명"]))}</a> <span class="muted">{escape(str(r["종목코드"] or ""))}</span>{mk}</div>'
            f'<div class="muted">제출 {escape(str(r["제출일"]))}</div></div>'
            f'<div>{escape(str(r["제출인"]))} · 변동일 {escape(str(r["변동일"]))}</div>'
            f'<div>매수 {won(r["매수수량"])}주 ({eok(r["매수금액"])}) · 평균 <b>{won(r["평균매수단가"])}원</b>'
            f' · 제출일 종가 {won(r["제출일종가"])}원 {gap(r["제출일종가"], r["평균매수단가"])}</div></div>')


# ---------------- 화면 ----------------
SORTS = {"submit": ("제출일 최신", "제출일 DESC, 변동일 DESC"),
         "trade": ("변동일 최신", "변동일 DESC, 제출일 DESC"),
         "amount": ("매수금액 큰 순", "매수금액 DESC"),
         "gap": ("종가가 매수단가보다 낮은 순", "(제출일종가 * 1.0 / 평균매수단가) ASC")}


def key_form(msg="", bad=False, current=""):
    note = f'<div class="{"warn" if bad else "muted"}" style="margin-top:6px">{escape(msg)}</div>' if msg else ""
    now = f'<div class="muted">지금 키: {escape(current[:4])}…{escape(current[-4:])}</div>' if current else ""
    return (f'<div class="card"><b>DART API 키</b>{now}'
            '<div class="muted">opendart.fss.or.kr → 인증키 신청/관리에서 받은 40자리 키를 붙여넣으세요.</div>'
            '<form method="post" action="/settings/key" class="filters" style="margin:8px 0 0">'
            '<input type="text" name="key" autocomplete="off" autocapitalize="off" spellcheck="false" '
            'placeholder="API 키" style="flex:1 1 220px"><button>저장</button></form>' + note + '</div>')


def setting_field(key, label, kind, help_, val, choices, err):
    e = f'<div class="warn" style="font-size:13px">{escape(err)}</div>' if err else ""
    h = f'<div class="muted">{escape(help_)}</div>' if help_ else ""
    if kind == "bool":
        return (f'<label class="set"><input type="checkbox" name="{key}" value="1"{" checked" if val == "1" else ""}> '
                f'{escape(label)}</label>{h}{e}')
    if kind == "choice":
        ctl = select(key, choices.get(key, {}), val)
    elif kind == "links":
        lines = "\n".join(f"{n} | {u}" for n, u in (
            tuple(x.strip() for x in part.split("|", 1)) for part in val.split(";;") if "|" in part))
        ctl = (f'<textarea name="{key}" rows="3" style="width:100%" placeholder="코인 | https://coin.xxxx.ts.net">'
               f'{escape(lines)}</textarea>')
    else:
        typ = "number" if kind.startswith("int") else "text"
        ctl = (f'<input type="{typ}" name="{key}" value="{escape(val)}" autocomplete="off" autocapitalize="off" '
               f'spellcheck="false" style="width:100%">')
    return f'<label class="set">{escape(label)}</label>{ctl}{h}{e}'


def update_box(u):
    """설정 → 업데이트 카드 안쪽: 지금 버전, 새 버전 여부, 버튼, 최근 기록"""
    c = u.get("check") or {}
    if u.get("running"):
        state = '<div class="warn">업데이트하는 중입니다… 1~3분 뒤 새로고침하세요.</div>'
    elif c.get("error"):
        state = f'<div class="warn">{escape(c["error"])}</div>'
    elif c.get("behind"):
        items = "".join(f"<li>{escape(x)}</li>" for x in c.get("items", []))
        state = (f'<div><b>새 버전이 있습니다</b> ({c["behind"]}개 변경, {escape(c.get("checked", ""))} 확인)</div>'
                 f'<ul class="muted" style="margin:4px 0 0;padding-left:18px">{items}</ul>')
    elif "behind" in c:
        state = f'<div class="muted">최신 버전입니다 ({escape(c.get("checked", ""))} 확인)</div>'
    else:
        state = '<div class="muted">아직 확인하지 않았습니다.</div>'
    log = ""
    if u.get("log"):
        log = ('<details style="margin-top:6px"><summary>최근 업데이트 기록</summary>'
               f'<pre style="white-space:pre-wrap;font-size:12px;margin:6px 0 0">{escape(chr(10).join(u["log"]))}</pre></details>')
    return (f'<div class="muted">지금 버전: {escape(u.get("current", ""))}</div>{state}'
            '<div class="filters" style="margin-top:8px">'
            '<button class="ghost" formaction="/settings/update-check" formmethod="post">새 버전 확인</button>'
            '<button formaction="/settings/update-run" formmethod="post">지금 업데이트</button></div>' + log)


def _stamp_text(name):
    """dart-backup-20261103-030000.tar.gz → 2026-11-03 03:00:00"""
    m = re.search(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}" if m else name


def backup_box(S):
    """설정 → 백업: 지금 만들기, 목록, 다운로드 (기본은 API 키 뺀 파일)"""
    if not S or "backup_list" not in S:
        return ""
    items = S["backup_list"]()
    busy = S["backup_busy"]()
    if busy:
        state = '<div class="warn">백업을 만드는 중입니다… 잠시 뒤 새로고침하세요.</div>'
    else:
        state = '<div class="muted">최근 7개를 서버에 보관합니다. 매일 새벽 3시에도 자동으로 만들어요.</div>'
    rows = "".join(
        f'<div style="border-top:1px solid var(--line);padding:8px 0"><div class="row"><b>{escape(_stamp_text(name))}</b>'
        f'<span class="muted">{size:.1f}MB</span></div>'
        f'<div><a href="/backup/{escape(name)}">키 빼고 받기</a> · '
        f'<a class="muted" href="/backup/{escape(name)}?key=1" onclick="return confirm(\'API 키가 들어 있는 파일입니다. 받을까요?\')">'
        f'키 포함해서 받기</a></div></div>'
        for name, size, when in items[:7])
    run = ('<form method="post" action="/settings/backup-run" style="margin-top:6px">'
           f'<button{" disabled" if busy else ""}>지금 백업 만들기</button></form>')
    return (f'<div class="card"><b>백업</b>{state}{run}'
            + (rows or '<div class="muted" style="margin-top:6px">아직 백업이 없습니다.</div>')
            + '<div class="muted" style="margin-top:8px;font-size:12px">키 포함 파일은 API 키가 들어 있으니 '
              '안전한 곳에만 보관하세요. 맥으로 자동 내려받기는 맥에서 scripts/backup_to_mac.sh를 실행하세요.</div></div>')


def view_settings(get_key, q, S=None):
    cur = get_key() if get_key else ""
    msg, bad = "", False
    if q.get("saved"):
        msg = "저장했습니다. " + q.get("why", "")
    elif q.get("err"):
        msg, bad = q["err"], True
    body = key_form(msg, bad, cur)
    if q.get("saved"):
        body += run_button()
    if S:
        body += backup_box(S)
        errs = json.loads(q["errs"]) if q.get("errs") else {}
        note = ""
        if q.get("ok") is not None:
            note = (f'<div class="card">✔ 저장했습니다: {escape(q["ok"])}<div class="muted">다음 수집부터 적용됩니다.</div></div>'
                    if q["ok"] else '<div class="card muted">바뀐 설정이 없습니다.</div>')
        if errs:
            note += '<div class="card warn">고쳐야 할 칸이 있어 그 칸은 저장하지 않았습니다.</div>'
        if q.get("nt"):
            note += f'<div class="card">{escape(q["nt"])}</div>'
        nxt = S["next_run"]
        groups, form = {}, ""
        for g, key, label, kind, default, help_ in S["spec"]:
            groups.setdefault(g, []).append(setting_field(key, label, kind, help_, S["values"].get(key, ""),
                                                          S["choices"], errs.get(key)))
        for g, fields in groups.items():
            extra = ""
            if g == "수집" and nxt:
                extra = f'<div class="muted">다음 자동 수집: {nxt:%m-%d %H:%M}</div>'
            if g == "업데이트" and "update_info" in S:
                extra = update_box(S["update_info"]())
            form += f'<div class="card"><b>{g}</b>{extra}{"".join(fields)}</div>'
        body = (note + body + '<form method="post" action="/settings/save">' + form
                + '<button style="width:100%;padding:11px">설정 저장</button></form>'
                + '<form method="post" action="/settings/notify-test" style="margin-top:10px">'
                  '<button class="ghost" style="width:100%">시험 알림 보내기</button></form>')
    body += '<p><a href="/check">분석 점검 (평균단가가 이상할 때)</a> · <a href="/">← 보고서로</a></p>'
    return page("설정", body)


PERIODS = {"": "전체 기간", "7": "최근 1주", "30": "최근 1달", "90": "최근 3달", "180": "최근 6달"}
MARKET_OPTS = {"": "시장 전체", "코스피": "코스피", "코스닥": "코스닥", "코넥스": "코넥스"}
AMOUNTS = {"": "매수금액 전체", "100000000": "1억 이상", "1000000000": "10억 이상",
           "10000000000": "100억 이상", "100000000000": "1,000억 이상"}
PRICE_VS = {"": "종가 전체", "below": "종가 < 평균매수단가", "above": "종가 > 평균매수단가"}
FILTERS = {"d": PERIODS, "m": MARKET_OPTS, "amt": AMOUNTS, "pv": PRICE_VS}


def select(name, opts, cur):
    return (f'<select name="{name}">' + "".join(
        f'<option value="{escape(k)}"{" selected" if k == cur else ""}>{escape(v)}</option>'
        for k, v in opts.items()) + "</select>")


def rows_per(q, default):
    """한 번에 보여 줄 줄 수: 주소의 n(또는 폰에 기억해 둔 값), 없으면 화면별 기본값"""
    return int(q["n"]) if str(q.get("n", "")) in {str(c) for c in ROW_CHOICES} else default


def rows_bar(q, size):
    """'줄 수: 10 20 50 100' 선택 줄 (고른 값은 폰에 기억)"""
    base = {k: v for k, v in q.items() if k not in ("n", "p", "sp", "dp") and not k.startswith("_") and v != ""}
    path = q.get("_path", "/")
    chips = " ".join(
        (f'<b class="cur" style="padding:2px 8px;border-radius:8px;background:var(--accent);color:var(--bg)">{c}</b>' if c == size else
         f'<a href="{path}?{urlencode({**base, "n": c})}" style="padding:2px 8px">{c}</a>') for c in ROW_CHOICES)
    return f'<div class="muted" style="margin-top:6px">한 번에 보기: {chips} 줄 <span style="font-size:12px">(고르면 다음에도 기억)</span></div>'


def pager(total, pg, link, size=None):
    """1 2 3 … 처럼 번호로 된 페이지 이동"""
    size = size or PAGE_SIZE
    pages = max((total + size - 1) // size, 1)
    if pages == 1:
        return ""
    show = sorted({1, pages, *range(max(pg - 2, 1), min(pg + 2, pages) + 1)})
    out, prev = [], 0
    if pg > 1:
        out.append(f'<a href="{link(pg - 1)}">‹</a>')
    for n in show:
        if n - prev > 1:
            out.append('<span class="muted">…</span>')
        out.append(f'<b class="cur">{n}</b>' if n == pg else f'<a href="{link(n)}">{n}</a>')
        prev = n
    if pg < pages:
        out.append(f'<a href="{link(pg + 1)}">›</a>')
    return '<div class="pager">' + "".join(out) + "</div>"


def collect_box(S, q):
    """보고서 화면 위쪽: 지금 수집 조건(보고서명 검색어·본문 포함 단어)을 보고 바로 바꾸기"""
    if not S:
        return ""
    v = S["values"]
    kw, bw = v.get("DART_KEYWORD", ""), v.get("DART_BODY_WORD", "")
    msg = f'<div style="margin-top:6px">{escape(q["cm"])}</div>' if q.get("cm") else ""
    return (f'<details class="card"{" open" if q.get("cm") else ""}><summary>수집 조건: 보고서명 <b>{escape(kw)}</b>'
            f' · 본문 <b>{escape(bw)}</b> <span class="muted">바꾸기</span></summary>'
            '<form method="post" action="/settings/collect" style="margin-top:8px">'
            '<label class="set">보고서명 검색어</label>'
            f'<input type="text" name="DART_KEYWORD" value="{escape(kw)}" style="width:100%">'
            '<div class="muted">DART 주식등의대량보유상황보고서 중 제목에 이 말이 든 것만 모읍니다. 예: 상황보고, 일반, 약식</div>'
            '<label class="set">본문 포함 단어</label>'
            f'<input type="text" name="DART_BODY_WORD" value="{escape(bw)}" style="width:100%">'
            '<div class="muted">공시 본문에 이 말이 든 것만 분석합니다. 예: 단순투자, 일반투자, 경영참가</div>'
            '<div class="filters" style="margin-top:10px"><button name="run" value="0" class="ghost">저장</button>'
            '<button name="run" value="1">저장하고 지금 수집</button></div>'
            '<div class="muted">바꾸면 다음 수집부터 새 조건으로 모읍니다. 지금 목록은 이전 조건으로 모은 것입니다.</div>'
            f'{msg}</form></details>')


def summary_filter(conn, q):
    """보고서 화면의 검색·필터 → (칸 목록, 검색어, 제출인, 필터, 정렬, WHERE, 값들)"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    text, who = q.get("q", "").strip(), q.get("who", "").strip()
    f = {k: (q.get(k, "") if q.get(k, "") in opts else "") for k, opts in FILTERS.items()}
    sort = q.get("sort", "submit") if q.get("sort") in SORTS else "submit"

    conds, args = [], []
    if text:
        # 검색어 하나로 종목명·코드·제출인, 그리고 보고서 안의 보고자·보고서명·보유목적까지
        like = f"%{text}%"
        detail_q = ""
        if has_table(conn, "detail"):
            detail_q = (" OR 종목코드 IN (SELECT 종목코드 FROM detail WHERE 보고자 LIKE ? OR 보고서 LIKE ? "
                        "OR 보유목적 LIKE ? OR 회사 LIKE ?)")
        conds.append(f"(종목명 LIKE ? OR 종목코드 LIKE ? OR 제출인 LIKE ?{detail_q})")
        args += [like] * (7 if detail_q else 3)
    if who:
        conds.append("제출인 LIKE ?")
        args.append(f"%{who}%")
    if f["d"]:
        conds.append("제출일 >= ?")
        args.append((dt.date.today() - dt.timedelta(days=int(f["d"]))).isoformat())
    if f["m"] and "시장" in cols:
        conds.append("시장 = ?")
        args.append(f["m"])
    if f["amt"]:
        conds.append("매수금액 >= ?")
        args.append(float(f["amt"]))
    if f["pv"] or sort == "gap":
        conds.append("제출일종가 IS NOT NULL AND 평균매수단가 > 0")
    if f["pv"] == "below":
        conds.append("제출일종가 < 평균매수단가")
    elif f["pv"] == "above":
        conds.append("제출일종가 > 평균매수단가")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return cols, text, who, f, sort, where, args


def filtered_excel(conn, q):
    """지금 목록(검색·필터 적용)을 엑셀로. 보고서 제출일 내림차순(최신부터)."""
    import io
    from openpyxl import Workbook
    cols, text, who, f, sort, where, args = summary_filter(conn, q)
    cur = conn.execute(f"SELECT * FROM summary {where} ORDER BY 제출일 DESC, 종목명, 변동일 DESC", args)
    names = [d[0] for d in cur.description]
    wb = Workbook()
    ws = wb.active
    ws.title = "날짜별_평균매수단가"
    ws.append(names)
    for row in cur:
        ws.append(list(row))
    ws.freeze_panes = "A2"
    for i, n in enumerate(names, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = 14 if n not in ("종목명", "제출인") else 20
    info = wb.create_sheet("조건")
    info.append(["검색어", text]); info.append(["제출인", who])
    for k, v in f.items():
        if v:
            info.append([k, FILTERS[k][v]])
    info.append(["만든 때", dt.datetime.now().strftime("%Y-%m-%d %H:%M")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def view_home(conn, q, st, need_key=False, S=None):
    size = rows_per(q, PAGE_SIZE)
    from urllib.parse import urlencode
    box, running = status_box(conn, st)
    if need_key and not running:
        box = key_form("API 키가 없어서 수집할 수 없습니다. 여기에 넣으면 바로 저장됩니다.", True) + box
    box += collect_box(S, q)
    if not has_table(conn, "summary"):
        return page("DART 단순투자", box, refresh=running)
    cols, text, who, f, sort, where, args = summary_filter(conn, q)
    pg = int(q["p"]) if str(q.get("p", "")).isdigit() and int(q["p"]) > 0 else 1
    total = conn.execute(f"SELECT COUNT(*) FROM summary {where}", args).fetchone()[0]
    pages = max((total + size - 1) // size, 1)
    pg = min(pg, pages)
    rows = conn.execute(f"SELECT * FROM summary {where} ORDER BY {SORTS[sort][1]} LIMIT ? OFFSET ?",
                        args + [size, (pg - 1) * size]).fetchall()

    active = ([f"검색: {text}"] if text else []) + [FILTERS[k][v] for k, v in f.items() if v] \
        + ([f"제출인: {who}"] if who else [])
    form = (f'<form method="get"><div class="filters"><input type="search" name="q" value="{escape(text)}" '
            f'placeholder="종목명·코드·제출인·보고자·보고서명"><button>검색</button></div>'
            f'<details{" open" if active else ""}><summary>필터·정렬{f" ({len(active)})" if active else ""}</summary>'
            f'<div class="filters" style="margin-top:8px">{select("d", PERIODS, f["d"])}'
            + (select("m", MARKET_OPTS, f["m"]) if "시장" in cols else "")
            + f'{select("amt", AMOUNTS, f["amt"])}{select("pv", PRICE_VS, f["pv"])}'
            f'<input type="search" name="who" value="{escape(who)}" placeholder="제출인 (예: 국민연금)">'
            f'{select("sort", {k: v[0] for k, v in SORTS.items()}, sort)}<button>적용</button></div></details></form>')
    chips = ""
    if active or text:
        chips = ('<div style="margin:4px 0 10px">' + "".join(f'<span class="chip">{escape(a)}</span>' for a in active)
                 + f'<span class="muted">{total:,}건</span> · <a href="/">초기화</a></div>')
    start = (pg - 1) * size
    cards = "".join(summary_card(r, start + i + 1) for i, r in enumerate(rows)) \
        or '<p class="muted">조건에 맞는 매수 내역이 없습니다.</p>'
    keep = {k: v for k, v in {"q": text, "who": who, "sort": sort if sort != "submit" else "", **f}.items() if v}
    link = lambda n: "/?" + urlencode({**keep, **({"n": q["n"]} if q.get("n") else {}), "p": n})   # noqa: E731
    excel = ('<div class="filters" style="margin:0 0 10px">'
             f'<a class="chip" href="/excel?{urlencode({**keep, "list": 1})}">⬇ 지금 목록 엑셀 ({total:,}건)</a>'
             '<a class="chip" href="/excel">⬇ 전체 엑셀</a>'
             '<span class="muted">제출일 최신순</span></div>')
    info = f'<div class="muted" style="margin-top:6px">{total:,}건 중 {start + 1 if total else 0:,}–{min(start + size, total):,}</div>'
    return page("보고서 — DART 단순투자", box + form + chips + excel + cards + pager(total, pg, link, size) + info + rows_bar(q, size),
                refresh=running)


def view_new(conn, q):
    if not has_table(conn, "seen") or not has_table(conn, "detail"):
        return page("새 공시", '<p class="muted">아직 수집 결과가 없습니다.</p>')
    days = 14
    since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    reps = conn.execute("SELECT rcept_no, first_seen FROM seen WHERE first_seen != '기준선' AND first_seen >= ?"
                        " ORDER BY first_seen DESC, rcept_no DESC", (since,)).fetchall()
    if not reps:
        return page("새 공시", f'<p class="muted">최근 {days}일 동안 새로 들어온 단순투자 공시가 없습니다.<br>'
                             '처음 수집한 공시는 기준선이라 여기에 나오지 않습니다.</p>')
    html, cur = "", None
    for rep in reps:
        rows = conn.execute("SELECT * FROM detail WHERE 접수번호=?", (rep["rcept_no"],)).fetchall()
        if not rows:
            continue
        day = rep["first_seen"][:10]
        if day != cur:
            html += f"<h2>{escape(day)} 발견</h2>"
            cur = day
        r0 = rows[0]
        buys = [r for r in rows if (r["증감"] or 0) > 0 and (r["단가"] or 0) > 0]
        if buys:
            qty = sum(r["증감"] for r in buys)
            avg = sum(r["증감"] * r["단가"] for r in buys) / qty
            what = f'매수 {won(qty)}주 · 평균 <b>{won(avg)}원</b> {gap(r0["제출일종가"], avg)}'
        else:
            what = "매도·기타 변동 " + f"{sum((r['증감'] or 0) for r in rows):+,.0f}주"
        html += (f'<div class="card"><div class="row"><div><a class="big" href="/stock?code={quote(str(r0["종목코드"] or ""))}">'
                 f'{escape(str(r0["종목명"]))}</a> <span class="muted">{escape(str(r0["종목코드"] or ""))}</span></div>'
                 f'<a href="{dart_link(rep["rcept_no"])}">공시 원문 ↗</a></div>'
                 f'<div>{escape(str(r0["제출인"]))} · 제출 {escape(str(r0["제출일"]))} · 종가 {won(r0["제출일종가"])}원</div>'
                 f'<div>{what}</div></div>')
    return page("새 공시", html)


STOCK_ROWS = 10      # 종목 화면 표는 한 번에 10줄
GROUPS = {"date": "변동일별", "submit": "제출일별", "who": "보고자별"}


def _group_sort(rows, g, who_key):
    """묶음 기준대로 정렬 (같은 값끼리 이어지게). 기본은 변동일 최신순."""
    rows = list(rows)
    rows.sort(key=lambda r: str(r["제출일"]), reverse=True)
    rows.sort(key=lambda r: str(r["변동일"]), reverse=True)
    if g == "submit":
        rows.sort(key=lambda r: str(r["제출일"]), reverse=True)
    elif g == "who":
        rows.sort(key=lambda r: str(r[who_key] or ""))
    return rows


def _group_key(r, g, who_key):
    return str(r["변동일"] if g == "date" else r["제출일"] if g == "submit" else (r[who_key] or "(이름 없음)"))


def paged_table(rows, g, who_key, pg, link, header, row_html, group_note, anchor, size=None):
    """10줄씩 번호 페이지로 나눈 표. 같은 묶음(변동일·제출일·보고자)은 머리글 줄로 구분."""
    size = size or STOCK_ROWS
    rows = _group_sort(rows, g, who_key)
    total = len(rows)
    pages = max((total + size - 1) // size, 1)
    pg = min(max(pg, 1), pages)
    stats = {}
    for r in rows:
        stats.setdefault(_group_key(r, g, who_key), []).append(r)
    out, prev = [], None
    for r in rows[(pg - 1) * size: pg * size]:
        k = _group_key(r, g, who_key)
        if k != prev:
            out.append(f'<tr><td colspan="6" style="background:var(--chip);font-weight:600">{escape(k)}'
                       f' <span class="muted">{group_note(stats[k])}</span></td></tr>')
            prev = k
        out.append(row_html(r))
    start = (pg - 1) * size
    info = (f'<div class="muted" style="margin-top:6px">{total:,}줄 중 {start + 1 if total else 0:,}–'
            f'{min(start + size, total):,}</div>')
    return (f'<div class="scroll"><table>{header}{"".join(out)}</table></div>'
            + pager(total, pg, link, size) + info)


def _sg(x):
    return f"{x:+,.0f}" if x is not None else "-"


def flow_block(conn, code):
    """종목 화면: 최근 20거래일 외국인·기관·개인 순매수 (접는 표)."""
    try:
        import stock_value
        rs = stock_value.flows(conn, code, 20)
    except Exception:
        rs = []
    if not rs:
        return ""
    last5 = rs[-5:]
    f5 = sum(r[1] or 0 for r in last5)
    i5 = sum(r[2] or 0 for r in last5)
    body = "".join(
        f'<tr><td>{escape(d[:4])}-{escape(d[4:6])}-{escape(d[6:])}</td><td>{_sg(fo)}</td><td>{_sg(ins)}</td><td>{_sg(ind)}</td></tr>'
        for d, fo, ins, ind in reversed(rs))
    return (f'<details style="margin:8px 0"><summary>순매수 · 최근 5거래일 외국인 <b>{_sg(f5)}</b>주 · 기관 <b>{_sg(i5)}</b>주 '
            '<span class="muted">(펼치기 · 네이버 증권 기준, 참고용)</span></summary>'
            '<table><tr><th>날짜</th><th>외국인(주)</th><th>기관(주)</th><th>개인(주)</th></tr>'
            f'{body}</table></details>')


def value_map(conn):
    """종목별 PER·PBR·배당 (stock_value.py가 받아 둔 값). 없으면 빈 dict."""
    try:
        import stock_value
        return stock_value.values(conn)
    except Exception:
        return {}


def value_line(v):
    """종목 화면용 한 줄. 값이 하나도 없으면 빈 문자열."""
    if not v or all(v.get(k) is None for k in ("per", "pbr", "div")):
        return ""
    def f(x, unit):
        return f"{x:,.2f}{unit}" if x is not None else "-"
    return (f'<div class="muted" style="margin-top:6px">PER <b>{f(v.get("per"), "배")}</b> · '
            f'PBR <b>{f(v.get("pbr"), "배")}</b> · 배당 <b>{f(v.get("div"), "%")}</b> '
            '<span>(네이버 증권 기준, 참고용)</span></div>')


def view_stock(conn, code, q=None):
    q = q or {}
    if not code or not has_table(conn, "detail"):
        return None
    g = q.get("g") if q.get("g") in GROUPS else "date"
    sp = int(q["sp"]) if str(q.get("sp", "")).isdigit() else 1
    dp = int(q["dp"]) if str(q.get("dp", "")).isdigit() else 1
    size = rows_per(q, STOCK_ROWS)
    rows = conn.execute("SELECT * FROM detail WHERE 종목코드=? ORDER BY 변동일 DESC, 제출일 DESC", (code,)).fetchall()
    if not rows:
        return None
    name = rows[0]["종목명"]
    summ = conn.execute("SELECT * FROM summary WHERE 종목코드=? ORDER BY 변동일 DESC", (code,)).fetchall()
    head = (f'<div class="row"><div><span class="big">{escape(str(name))}</span> '
            f'<span class="muted">{escape(code)}</span></div>'
            f'<div><a href="{naver_link(code)}">네이버 증권 ↗</a></div></div>')
    keep_n = {"n": q["n"]} if q.get("n") else {}

    def link_s(n):
        return "/stock?" + urlencode({"code": code, **keep_n, **({"g": g} if g != "date" else {}), "sp": n,
                                      **({"dp": dp} if dp > 1 else {})}) + "#sum"

    def link_d(n):
        return "/stock?" + urlencode({"code": code, **keep_n, **({"g": g} if g != "date" else {}),
                                      **({"sp": sp} if sp > 1 else {}), "dp": n}) + "#det"

    def row_sum(r):
        return (f'<tr><td>{escape(str(r["변동일"]))}</td><td>{escape(str(r["제출인"]))}</td>'
                f'<td>{won(r["매수수량"])}</td><td>{won(r["평균매수단가"])}</td>'
                f'<td>{won(r["제출일종가"])} {gap(r["제출일종가"], r["평균매수단가"])}</td>'
                f'<td>{escape(str(r["제출일"]))}</td></tr>')

    def note_sum(rs):
        qty = sum(float(x["매수수량"] or 0) for x in rs)
        amt = sum(float(x["매수금액"] or 0) for x in rs)
        return f'{len(rs)}줄 · 매수 {won(qty)}주' + (f' · 평균 {won(amt / qty)}원' if qty else "")

    def row_det(r):
        return (f'<tr><td>{escape(str(r["변동일"]))}</td><td>{escape(str(r["보고자"]))}</td>'
                f'<td>{escape(str(r["방법"]))}</td><td>{won(r["증감"])}</td><td>{won(r["단가"])}</td>'
                f'<td><a href="{dart_link(r["접수번호"])}">{escape(str(r["제출일"]))}</a> '
                f'<a class="muted" href="/parse?r={quote(str(r["접수번호"]))}">표 확인</a></td></tr>')

    def note_det(rs):
        net = sum(float(x["증감"] or 0) for x in rs)
        return f'{len(rs)}줄 · 순증감 {net:+,.0f}주'

    group_bar = ('<div style="margin:8px 0">묶어 보기: ' + " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == g else
         f'<a class="chip" href="/stock?{urlencode({"code": code, **({"g": k} if k != "date" else {}), **({"n": q["n"]} if q.get("n") else {})})}#sum">{v}</a>')
        for k, v in GROUPS.items()) + '</div>' + rows_bar(q, size))
    t1 = paged_table(summ, g, "제출인", sp, link_s,
                     '<tr><th>변동일</th><th>제출인</th><th>매수수량</th><th>평균단가</th><th>제출일 종가</th><th>제출일</th></tr>',
                     row_sum, note_sum, "sum", size) if summ else ""
    t2 = paged_table(rows, g, "보고자", dp, link_d,
                     '<tr><th>변동일</th><th>보고자</th><th>방법</th><th>증감</th><th>단가</th><th>제출일(원문)</th></tr>',
                     row_det, note_det, "det", size)
    chart = ""
    tgt, n_br = consensus(conn, code)
    last_close = None
    if has_table(conn, "prices"):
        pr = conn.execute("SELECT date, close FROM prices WHERE code=? ORDER BY date", (code,)).fetchall()
        buys = [(r["변동일"], float(r["평균매수단가"]), str(r["제출인"]), float(r["매수수량"]))
                for r in summ if r["평균매수단가"] and r["매수수량"]]
        if pr:
            first = min([b[0] for b in buys] + [pr[-1]["date"]])
            pr = [p for p in pr if p["date"] >= first] or pr   # 첫 매수부터 오늘까지
            qty = sum(b[3] for b in buys)
            avg = sum(b[1] * b[3] for b in buys) / qty if qty else None
            last = pr[-1]
            last_close = last["close"]
            stat = (f'<div style="margin-top:8px">최근 종가 <b>{won(last["close"])}원</b> '
                    f'<span class="muted">({escape(last["date"])})</span>'
                    + (f' · 기관 평균매수단가 <b>{won(avg)}원</b> {gap(last["close"], avg)}' if avg else "")
                    + (f' · 증권사 목표가 평균 <b>{won(tgt)}원</b> ({n_br}곳) {upside(tgt, last["close"])}' if tgt else "")
                    + "</div>")
            chart = stat + price_chart([p["date"] for p in pr], [p["close"] for p in pr], buys, avg,
                                       title=f"{name} 주가와 기관 매수", target=tgt)
    reports = view_reports(conn, code, tgt, n_br, last_close)
    body = (head + value_line(value_map(conn).get(code)) + flow_block(conn, code) + chart + reports + group_bar + '<h2 id="sum">날짜별 평균 매수단가</h2>'
            + (t1 or '<p class="muted">매수 내역 없음</p>')
            + '<h2 id="det">모든 변동 내역</h2>' + t2)
    return page(f"{name} — DART 단순투자", body, charts=bool(chart))


OPINION_KO = {"buy": "매수", "strongbuy": "적극 매수", "hold": "중립", "neutral": "중립", "marketperform": "중립",
              "sell": "매도", "outperform": "비중 확대", "underperform": "비중 축소", "trading buy": "단기 매수",
              "tradingbuy": "단기 매수", "notrated": "의견 없음", "nr": "의견 없음"}


def op_ko(op):
    """투자의견 표기를 맞춤 (buy·Buy → 매수 등). 모르는 건 그대로."""
    if not op:
        return ""
    return OPINION_KO.get(re.sub(r"[\s_-]", "", str(op)).lower(), str(op))


def day_prices(conn, code, date):
    """그 날(휴장이면 직전 거래일)의 (시가, 종가). 없으면 (None, None)"""
    if not code or not date:
        return None, None
    try:
        r = conn.execute("SELECT open, close FROM prices WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                         (code, date)).fetchone()
    except sqlite3.OperationalError:          # 시가 칸이 없는 예전 표
        try:
            r = conn.execute("SELECT NULL, close FROM prices WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                             (code, date)).fetchone()
        except sqlite3.OperationalError:
            return None, None
    return (r[0], r[1]) if r else (None, None)


def close_on(conn, code, date):
    return day_prices(conn, code, date)[1]


def upside(target, close):
    """목표가가 종가보다 몇 % 위인지 (상승여력)"""
    try:
        u = float(target) / float(close) - 1
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    return f'<span class="{"up" if u > 0 else "down"}">상승여력 {u:+.1%}</span>'


def title_link(r, n=40):
    t = escape((r["title"] or "")[:n])
    href = r["pdf"] or (r["url"] if "url" in r.keys() else "")
    return f'<a href="{escape(href)}">{t}</a>' if href else t


SRC_CHIP = {"hk": "한경", "nv": "네이버"}


def view_reports(conn, code, tgt, n_br, close):
    """종목 화면의 '증권사 목표주가' 부분"""
    if not has_table(conn, "reports"):
        return ""
    rows = conn.execute("SELECT * FROM reports WHERE code=? ORDER BY date DESC LIMIT 20", (code,)).fetchall()
    if not rows:
        return '<h2>증권사 목표주가</h2><p class="muted">최근 증권사 리포트가 없습니다.</p>'
    head = (f'<p>최근 6개월 증권사별 마지막 목표가의 평균 <b>{won(tgt)}원</b> ({n_br}곳) {upside(tgt, close)}</p>'
            if tgt else "")
    trs = "".join(
        f'<tr><td>{escape(r["date"])}</td><td>{escape(r["broker"] or "")}</td>'
        f'<td>{won(r["target"]) if r["target"] else "-"}'
        f'{" ⚠" if suspicious(r["target"], close_on(conn, code, r["date"]) or close) else ""}</td>'
        f'<td>{won(day_prices(conn, code, r["date"])[0])}</td>'
        f'<td>{won(close_on(conn, code, r["date"]))}</td><td>{escape(op_ko(r["opinion"]))}</td>'
        f'<td>{title_link(r)} <span class="muted">{SRC_CHIP.get(r["source"], "")}</span></td></tr>' for r in rows)
    return (f'<h2>증권사 목표주가</h2>{head}'
            f'<div class="scroll"><table><tr><th>날짜</th><th>증권사</th><th>목표가</th><th>당일 시가</th><th>당일 종가</th><th>의견</th><th>제목</th></tr>'
            f'{trs}</table></div><p><a href="/reports?q={quote(code)}&d=180">이 종목 리포트 모두 보기 →</a></p>')


REPORT_PERIODS = {"7": "최근 1주", "30": "최근 1달", "90": "최근 3달", "180": "최근 6달"}
REPORT_SRC = {"": "출처 전체", "hk": "한경컨센서스", "nv": "네이버 증권"}
REPORT_ONLY = {"": "모든 종목", "dart": "기관 단순투자 종목만"}
# 상승여력(목표가 ÷ 최근 종가 − 1) 구간: 최소와 최대를 따로 골라 '50% 이상 · 80% 이하'처럼
REPORT_UPSIDE = {"": "상승여력 최소: 전체", "0": "0% 이상 (목표가 ≥ 지금 가격)",
                 **{str(p): f"{p}% 이상" for p in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200)},
                 "down": "목표가가 지금 가격보다 낮음"}
REPORT_UPMAX = {"": "최대: 제한 없음", **{str(p): f"{p}% 이하" for p in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200)}}
REPORT_SORTS = {"date": ("최신순", "date DESC, rowid DESC"), "upside": ("상승여력 큰 순", None),
                "target": ("목표가 있는 것만 · 최신순", "date DESC, rowid DESC")}


def summary_fold(r):
    text = r["summary"] if "summary" in r.keys() else None
    if not text:
        return ""
    return ('<details style="margin-top:6px"><summary>요약 보기</summary>'
            f'<div style="white-space:pre-wrap;font-size:14px;margin-top:4px">{escape(text)}</div></details>')


_DATA_DIR = {"path": None}


def reports_last_line():
    try:
        m = json.loads((Path(_DATA_DIR["path"]) / "reports_last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    parts = []
    for src, label in (("hk", "한경"), ("nv", "네이버")):
        v = (m.get("result") or {}).get(src)
        if v is None:
            continue
        parts.append(f"{label} {'실패' if isinstance(v, str) else f'새 {v}건'}")
    return (f'<div class="muted" style="margin-bottom:8px">리포트 마지막 수집 {escape(m.get("finished", ""))}'
            + (f' · {" · ".join(parts)}' if parts else "") + "</div>")


def view_report_list(conn, q):
    size = rows_per(q, PAGE_SIZE)
    if not has_table(conn, "reports"):
        return page("리포트", '<p class="muted">다음 수집이 끝나면 증권사 리포트가 여기에 모입니다.</p>')
    text, broker = q.get("q", "").strip(), q.get("b", "").strip()
    d = q.get("d") if q.get("d") in REPORT_PERIODS else "30"
    src = q.get("src") if q.get("src") in REPORT_SRC else ""
    only = q.get("only") if q.get("only") in REPORT_ONLY else ""
    up = q.get("up") if q.get("up") in REPORT_UPSIDE else ""
    upmax = q.get("upmax") if q.get("upmax") in REPORT_UPMAX else ""
    sort = q.get("sort") if q.get("sort") in REPORT_SORTS else "date"
    pg = int(q["p"]) if str(q.get("p", "")).isdigit() and int(q["p"]) > 0 else 1
    conds = ["date >= ?"]
    args = [(dt.date.today() - dt.timedelta(days=int(d))).isoformat()]
    if text:
        conds.append("(name LIKE ? OR code LIKE ? OR title LIKE ?)")
        args += [f"%{text}%"] * 3
    if broker:
        conds.append("broker LIKE ?")
        args.append(f"%{broker}%")
    if src:
        conds.append("source = ?")
        args.append(src)
    dart_codes = set()
    if has_table(conn, "summary"):
        dart_codes = {r[0] for r in conn.execute("SELECT DISTINCT 종목코드 FROM summary")}
    if only == "dart":
        conds.append("code IN (SELECT DISTINCT 종목코드 FROM summary)" if dart_codes else "0")
    if sort in ("target", "upside") or up or upmax:
        conds.append("target IS NOT NULL")
    where = " AND ".join(conds)
    closes = latest_closes(conn) if has_table(conn, "prices") else {}

    def up_ok(r):
        """상승여력(목표가 ÷ 최근 종가 − 1) 필터"""
        if not up and not upmax:
            return True
        c = closes.get(r["code"], (None, None))[1]
        if not c or not r["target"] or suspicious(r["target"], c):
            return False
        u = r["target"] / c - 1
        if up == "down":
            return u < 0
        if up and u < int(up) / 100:
            return False
        return not upmax or u <= int(upmax) / 100
    if sort == "upside" or up or upmax:
        rows = conn.execute(f"SELECT * FROM reports WHERE {where} ORDER BY date DESC, rowid DESC", args).fetchall()
        rows = [r for r in rows if r["code"] in closes and up_ok(r)
                and not suspicious(r["target"], closes[r["code"]][1])]
        if sort == "upside":
            rows.sort(key=lambda r: -(r["target"] / closes[r["code"]][1]))
        total = len(rows)
        pages = max((total + size - 1) // size, 1)
        pg = min(pg, pages)
        rows = rows[(pg - 1) * size: pg * size]
    else:
        total = conn.execute(f"SELECT COUNT(*) FROM reports WHERE {where}", args).fetchone()[0]
        pages = max((total + size - 1) // size, 1)
        pg = min(pg, pages)
        rows = conn.execute(f"SELECT * FROM reports WHERE {where} ORDER BY {REPORT_SORTS[sort][1]} LIMIT ? OFFSET ?",
                            args + [size, (pg - 1) * size]).fetchall()

    active = [x for x in (REPORT_SRC[src] if src else "", REPORT_ONLY[only] if only else "",
                          ("상승여력 " + " · ".join(x for x in (REPORT_UPSIDE[up] if up else "",
                                                                REPORT_UPMAX[upmax] if upmax else "") if x))
                          if (up or upmax) else "",
                          f"증권사: {broker}" if broker else "") if x]
    form = (f'<form method="get"><div class="filters"><input type="search" name="q" value="{escape(text)}" '
            f'placeholder="종목명·종목코드·제목"><button>보기</button></div>'
            f'<details{" open" if active or sort != "date" else ""}><summary>필터·정렬</summary>'
            f'<div class="filters" style="margin-top:8px">{select("d", REPORT_PERIODS, d)}{select("src", REPORT_SRC, src)}'
            f'{select("only", REPORT_ONLY, only)}{select("up", REPORT_UPSIDE, up)}{select("upmax", REPORT_UPMAX, upmax)}'
            f'<input type="search" name="b" value="{escape(broker)}" placeholder="증권사 (예: 키움)">'
            f'{select("sort", {k: v[0] for k, v in REPORT_SORTS.items()}, sort)}<button>적용</button></div></details></form>')
    chips = ('<div style="margin:4px 0 10px">' + "".join(f'<span class="chip">{escape(a)}</span>' for a in active)
             + f'<span class="muted">{REPORT_PERIODS[d]} · {total:,}건</span>'
             + (' · <a href="/reports">초기화</a>' if active or text else "") + "</div>")
    start = (pg - 1) * size
    cards = []
    for i, r in enumerate(rows, start + 1):
        in_dart = r["code"] in dart_codes
        stock_href = f'/stock?code={quote(r["code"])}' if in_dart else naver_link(r["code"])
        close = closes.get(r["code"], (None, None))[1]
        day_open, day_close = day_prices(conn, r["code"], r["date"])
        op = op_ko(r["opinion"])
        bad = suspicious(r["target"], day_close or close)
        tgt = (f'목표가 <b>{won(r["target"])}원</b>' if r["target"] else '<span class="muted">목표가 없음</span>') \
            + (f" · {escape(op)}" if op else "")
        if bad:
            tgt += ('<div class="warn" style="font-size:13px">⚠ 목표가가 이 종목 주가와 맞지 않습니다. 출처에서 종목이 '
                    '잘못 적힌 리포트일 수 있어 목표가 평균·상승여력 계산에서 뺐습니다.</div>')
        prices = []
        show_up = r["target"] and not bad
        if day_close:
            prices.append((f'리포트 날 시가 {won(day_open)}원 · ' if day_open else "리포트 날 ")
                          + f'종가 {won(day_close)}원 {upside(r["target"], day_close) if show_up else ""}')
        if close and close != day_close:
            prices.append(f'최근 종가 {won(close)}원 {upside(r["target"], close) if show_up else ""}')
        price_line = f'<div class="muted" style="color:var(--fg)">{" · ".join(prices)}</div>' if prices else ""
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span>'
            f'<a class="big" href="{stock_href}">{escape(r["name"] or r["code"])}</a> '
            f'<span class="muted">{escape(r["code"])}</span>'
            + (' <span class="chip">기관 단순투자</span>' if in_dart else "")
            + f'</div><div class="muted">{escape(r["date"])}</div></div>'
            f'<div>{title_link(r, 60)}</div>'
            f'<div class="muted">{escape(r["broker"] or "")}{" · " + escape(r["author"]) if r["author"] else ""}'
            f' · <span class="chip">{SRC_CHIP.get(r["source"], "")}</span></div>'
            f'<div>{tgt}</div>{price_line}{summary_fold(r)}</div>')
    keep = {k: v for k, v in {"q": text, "b": broker, "d": d if d != "30" else "", "src": src, "only": only, "up": up, "upmax": upmax,
                              "sort": sort if sort != "date" else ""}.items() if v}
    link = lambda n: "/reports?" + urlencode({**keep, **({"n": q["n"]} if q.get("n") else {}), "p": n})   # noqa: E731
    body = (reports_last_line() + form + chips + ("".join(cards) or '<p class="muted">조건에 맞는 리포트가 없습니다.</p>')
            + pager(total, pg, link, size) + rows_bar(q, size))
    return page("리포트", body)


# ---------------- 공시 표 확인 ----------------
def view_parse(conn, rno):
    """공시 하나의 세부변동내역 표 원문과, 프로그램이 읽은 증감·단가를 나란히"""
    if not re.fullmatch(r"\d{14}", rno or ""):
        return None
    import dart_engine as de
    from bs4 import BeautifulSoup
    cache = os.path.join(_DATA_DIR["path"] or ".", "dart_cache")
    xml = de.read_zip(de.zpath(cache, rno))
    if not xml:
        return page("표 확인", f'<div class="card">이 서버에 원문이 없습니다 ({escape(rno)}).</div>')
    note = ""
    try:
        pc = sqlite3.connect(os.path.join(cache, "parsed.sqlite"))
        vers = [r[0] for r in pc.execute("SELECT ver FROM parsed WHERE rcept_no=?", (rno,))]
        pc.close()
        if vers and max(vers) < de.PARSE_VERSION:
            note = ('<div class="card warn">이 공시는 아직 예전 방식으로 분석된 값이 화면에 쓰이고 있습니다. '
                    '위쪽 <b>지금 수집 → 다시 분석만</b>을 누르면 DART 호출 없이 새 방식으로 다시 분석합니다.</div>')
    except sqlite3.Error:
        pass
    stored = conn.execute("SELECT 변동일, 증감, 단가, 방법 FROM detail WHERE 접수번호=?", (rno,)).fetchall() \
        if has_table(conn, "detail") else []
    soup = BeautifulSoup(xml, de.PARSER)
    blocks = []
    for tb in soup.find_all("table"):
        head = tb.get_text(" ")
        if "변동일" not in head or "단가" not in head:
            continue
        desc, rows_ = de.explain_table(tb)
        trs = []
        for cells, res in rows_:
            if res:
                _, _, qty, price, _, _, how = res
                out = (f'<span class="up">증감 {won(qty)} · 단가 {won(price) if price is not None else "-"}</span>'
                       f'<div class="muted" style="font-size:12px">{escape(how)}</div>')
            else:
                out = '<span class="muted">-</span>'
            trs.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in cells) + f"<td>{out}</td></tr>")
        blocks.append(f'<div class="muted" style="margin-top:8px">인식한 칸: {escape(desc)}</div>'
                      f'<div class="scroll"><table>{"".join(trs)}</table></div>')
    saved = "".join(f'<tr><td>{escape(str(r["변동일"]))}</td><td>{escape(str(r["방법"]))}</td>'
                    f'<td>{won(r["증감"])}</td><td>{won(r["단가"])}</td></tr>' for r in stored)
    body = (f'<div class="row"><b>공시 {escape(rno)}</b><a href="{dart_link(rno)}">DART 원문 ↗</a></div>{note}'
            '<h2>원문 표와 읽은 값</h2><p class="muted">맨 오른쪽 칸이 프로그램이 읽은 증감·단가입니다. '
            '틀린 줄이 있으면 이 화면을 캡처해서 보내 주세요.</p>' + ("".join(blocks) or '<p class="muted">세부변동내역 표가 없습니다.</p>')
            + (f'<h2>화면에 쓰이는 값 (마지막 수집 기준)</h2><div class="scroll"><table><tr><th>변동일</th><th>방법</th>'
               f'<th>증감</th><th>단가</th></tr>{saved}</table></div>' if saved else ""))
    return page("표 확인", body)


def view_check(conn, st):
    """분석 점검: 마지막 수집, 분석 형식 버전별 공시 수, 수량=단가인 줄이 있는 공시"""
    import dart_engine as de
    out = []
    if has_table(conn, "runs"):
        r = conn.execute("SELECT started, finished, ok, info FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        if r:
            info = json.loads(r["info"] or "{}")
            out.append(f'<div class="card"><b>마지막 보고서 수집</b><div>{escape(r["started"])} 시작 → {escape(r["finished"])} '
                       f'{"끝남" if r["ok"] else "실패"}</div><div class="muted">{escape(info.get("error", ""))}</div></div>')
    if st.get("state") == "running":
        out.append(f'<div class="card warn">지금 수집 중: {escape(st.get("stage", ""))} {st.get("done", 0)}/{st.get("total", 0)}</div>')
    cache = os.path.join(_DATA_DIR["path"] or ".", "dart_cache")
    try:
        pc = sqlite3.connect(os.path.join(cache, "parsed.sqlite"))
        vers = pc.execute("SELECT ver, COUNT(*) FROM parsed GROUP BY ver ORDER BY ver").fetchall()
        pc.close()
        rows = "".join(f'<tr><td>{v}{" (지금 방식)" if v == de.PARSE_VERSION else " (예전)"}</td><td>{n:,}</td></tr>' for v, n in vers)
        old = sum(n for v, n in vers if v < de.PARSE_VERSION)
        out.append(f'<div class="card"><b>분석 형식별 공시 수</b><table>{rows}</table>'
                   + (f'<div class="warn">예전 방식 {old:,}건이 남아 있습니다. 위쪽 <b>지금 수집 → 다시 분석만</b>을 '
                      '누르면 DART 호출 없이 다시 분석해서 화면 값이 바뀝니다.</div>' if old else
                      '<div class="muted">모두 지금 방식으로 분석됐습니다.</div>') + '</div>')
    except sqlite3.Error as e:
        out.append(f'<div class="card muted">분석 기록을 읽지 못했습니다: {escape(str(e))}</div>')
    if has_table(conn, "detail"):
        bad = conn.execute("SELECT 접수번호, MAX(종목명) n, MAX(제출인) w, COUNT(*) c FROM detail "
                           "WHERE 단가 IS NOT NULL AND 증감 IS NOT NULL AND ABS(단가)=ABS(증감) AND 증감!=0 "
                           "GROUP BY 접수번호 ORDER BY c DESC LIMIT 15").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM detail WHERE 단가 IS NOT NULL AND ABS(단가)=ABS(증감) AND 증감!=0").fetchone()[0]
        items = "".join(f'<li><a href="/parse?r={quote(str(b["접수번호"]))}">{escape(str(b["n"]))} · {escape(str(b["w"]))}</a> '
                        f'<span class="muted">{b["c"]}줄</span></li>' for b in bad)
        out.append(f'<div class="card"><b>수량과 단가가 같은 줄 (화면에 쓰이는 값)</b><div>{total:,}줄</div>'
                   + (f'<ul style="padding-left:18px;margin:6px 0">{items}</ul><div class="muted">누르면 그 공시의 표를 볼 수 있습니다. '
                      '하나를 캡처해서 보내 주세요.</div>' if bad else '<div class="muted">없습니다.</div>') + '</div>')
    if has_table(conn, "detail"):
        nop = conn.execute("SELECT 접수번호, MAX(종목명) n, MAX(제출인) w, COUNT(*) c FROM detail "
                           "WHERE 증감 > 0 AND 단가 IS NULL GROUP BY 접수번호 ORDER BY c DESC LIMIT 15").fetchall()
        total_nop = conn.execute("SELECT COUNT(*) FROM detail WHERE 증감 > 0 AND 단가 IS NULL").fetchone()[0]
        items = "".join(f'<li><a href="/parse?r={quote(str(b["접수번호"]))}">{escape(str(b["n"]))} · {escape(str(b["w"]))}</a> '
                        f'<span class="muted">{b["c"]}줄</span></li>' for b in nop)
        out.append(f'<div class="card"><b>매수인데 단가를 읽지 못한 줄</b><div>{total_nop:,}줄</div>'
                   + (f'<ul style="padding-left:18px;margin:6px 0">{items}</ul><div class="muted">공시 원문에 단가가 없거나 '
                      '수량과 같게 적힌 경우, 틀린 평균단가 대신 비워 둡니다. 누르면 원문 표와 인식한 칸을 볼 수 있어요.</div>'
                      if nop else '<div class="muted">없습니다.</div>') + '</div>')
    return page("분석 점검", "".join(out))


# ---------------- 시장 ----------------
SIGNAL_TXT = {1: ("+1 우호", "up"), 0: ("0 중립", ""), -1: ("−1 부담", "down")}


def view_market(conn):
    import stock_market
    inds = stock_market.indicators(conn)
    name, score, n = stock_market.regime(inds)
    synced = stock_market.meta_get(conn, "synced", "")
    if not n:
        return page("시장", '<div class="card">아직 시장 지표가 없습니다. 위쪽 <b>지금 수집 → 시장·정책만</b>을 '
                          '눌러 주세요 (1~2분).</div>')
    cls = {"강세": "up", "약세": "down"}.get(name, "")
    hero = (f'<div class="card"><div class="muted">시장 국면 (지표 {n}개 점수 합)</div>'
            f'<div style="font-size:30px;font-weight:700" class="{cls}">{name} <span style="font-size:18px">{score:+d}</span></div>'
            '<div class="muted">합이 +3 이상이면 강세, −3 이하면 약세. 투자 판단을 대신하지 않는 참고용이고, '
            '지표마다 근거를 아래에 적었습니다.' + (f' · 마지막 수집 {escape(synced)}' if synced else "") + '</div></div>')
    cards = []
    for i in inds:
        txt, c = SIGNAL_TXT[i.signal]
        if not i.available:
            cards.append(f'<div class="card"><b>{escape(i.label)}</b><div class="muted">{escape(i.note or "자료 없음")}</div></div>')
            continue
        cards.append(
            f'<div class="card"><div class="row"><b>{escape(i.label)}</b>'
            f'<span class="chip {c}">{txt}</span></div>'
            f'<div><span class="big">{escape(i.value_text)}</span> <span class="muted">{escape(i.period)}</span></div>'
            + (f'<div>{escape(i.change_text)}</div>' if i.change_text else "")
            + f'<div class="muted">{escape(i.reason)}</div>'
            + sparkline(i.points)
            + (f'<div class="muted" style="font-size:12px">{escape(i.note)}</div>' if i.note else "") + "</div>")
    return page("시장", hero + "".join(cards))


# ---------------- 유대인 달력 ----------------
CAL_DAYS = {"30": "앞으로 1달", "90": "앞으로 3달", "180": "앞으로 6달", "365": "앞으로 1년"}


def jewish_test(conn):
    """'로쉬 하샤나에 팔고 욤 키푸르에 사라' 격언 검증 카드"""
    import jewish_calendar as jc
    import stock_market
    series = {"코스피": list(stock_market.series(conn, "idx:KOSPI")), "코스닥": list(stock_market.series(conn, "idx:KOSDAQ"))}
    series = {k: v for k, v in series.items() if v}
    if not series:
        test = ('<div class="card"><b>격언 검증</b><div class="muted">지수 기록이 아직 없습니다. 위쪽 '
                '<b>지금 수집 → 시장·정책만</b>을 누르면 코스피·코스닥 기록을 받아 검증합니다.</div></div>')
    else:
        rows = jc.rosh_to_yom(series)
        names = list(series)
        trs = "".join(
            f'<tr><td>{r["year"]}</td><td>{r["rh"]:%m-%d} → {r["yk"]:%m-%d}</td>'
            + "".join(f'<td>{gap_pct(r["chg"].get(n))}</td>' for n in names) + "</tr>" for r in reversed(rows))
        summ = []
        for n in names:
            vals = [r["chg"][n] for r in rows if n in r["chg"]]
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            down = sum(1 for v in vals if v < 0)
            base = jc.baseline(series[n])
            summ.append(f'<div><b>{n}</b>: {len(vals)}년 중 <b>{down}년 하락</b> · 평균 {gap_pct(avg)}'
                        + (f' <span class="muted">(아무 때나 같은 길이 평균 {base:+.2%})</span>' if base is not None else "")
                        + "</div>")
        test = (f'<div class="card"><b>격언 검증: 로쉬 하샤나에 팔고 욤 키푸르에 사라</b>'
                '<div class="muted">로쉬 하샤나 전날 종가 → 욤 키푸르 전날 종가 변화. 격언대로라면 이 기간에 내립니다. '
                '미국 증시 격언이라 한국 지수에도 맞는지 확인하는 참고 자료입니다.</div>'
                + "".join(summ)
                + f'<div class="scroll"><table><tr><th>해</th><th>기간</th>{"".join(f"<th>{n}</th>" for n in names)}</tr>'
                  f'{trs}</table></div></div>')
    return test


def view_calendar(conn, q):
    try:
        import market_calendar as mc
    except ImportError:
        return page("달력", '<div class="card">달력 계산 라이브러리가 아직 없습니다. 설정 → 업데이트를 한 번 해 주세요.</div>')
    today = dt.date.today()
    days = q.get("d") if q.get("d") in CAL_DAYS else "90"
    cat = q.get("c") if q.get("c") in mc.CATS + ["중요"] else ""
    rg = q.get("r") if q.get("r") in mc.REGIONS else ""
    evs = mc.events_between(today - dt.timedelta(days=1), today + dt.timedelta(days=int(days)), conn)
    if cat == "중요":
        evs = [e for e in evs if e["important"]]
    elif cat:
        evs = [e for e in evs if e["cat"] == cat]
    if rg:
        evs = [e for e in evs if e["region"] == rg]
    keep = {**({"d": days} if days != "90" else {}), **({"c": cat} if cat else {}), **({"r": rg} if rg else {})}

    def cal_link(**ch):
        return "/calendar?" + urlencode({k: v for k, v in {**keep, **ch}.items() if v})

    on = 'style="background:var(--accent);color:var(--bg)"'
    chips = "".join(
        (f'<b class="chip" {on}>{c or "전체"}</b>' if c == cat else f'<a class="chip" href="{cal_link(c=c)}">{c or "전체"}</a>')
        for c in ["", "중요"] + mc.CATS)
    region_chips = '<div style="margin-top:6px">지역: ' + "".join(
        (f'<b class="chip" {on}>{r or "전체"}</b>' if r == rg else f'<a class="chip" href="{cal_link(r=r)}">{r or "전체"}</a>')
        for r in [""] + mc.REGIONS) + "</div>"
    period = "".join(
        (f'<b>{v}</b>' if k == days else f'<a href="{cal_link(d=k)}">{v}</a>') + " " for k, v in CAL_DAYS.items())
    rows, month = [], None
    for e in evs:
        d = e["date"]
        if d.strftime("%Y-%m") != month:
            month = d.strftime("%Y-%m")
            rows.append(f'<h2>{d.year}년 {d.month}월</h2>')
        dd = (d - today).days
        when = "오늘" if dd == 0 else "어제" if dd < 0 else f"D-{dd}"
        tags = f'<span class="chip">{escape(e["cat"])}</span>' + (f'<span class="chip">{escape(e["region"])}</span>' if e["region"] else "") \
            + ('<span class="chip">예상</span>' if e["approx"] else "")
        delete = (f'<form method="post" action="/calendar/del" style="display:inline"><input type="hidden" name="id" value="{e["uid"]}">'
                  '<button class="ghost" style="padding:2px 8px;font-size:12px">지우기</button></form>') if e["uid"] else ""
        rows.append(
            f'<div class="card" style="{"border-color:var(--accent)" if e["important"] else ""}">'
            f'<div class="row"><div><b>{d.month}/{d.day} ({"월화수목금토일"[d.weekday()]})</b> '
            f'{"★ " if e["important"] else ""}{escape(e["title"])}</div><b class="{"up" if 0 <= dd <= 3 else "muted"}">{when}</b></div>'
            f'<div style="margin-top:4px">{tags}{delete}</div>'
            + (f'<div class="muted">{escape(e["note"])}</div>' if e["note"] else "") + "</div>")
    add = ('<details class="card"><summary>일정 직접 추가 (금통위·CPI·실적 발표 등)</summary>'
           '<form method="post" action="/calendar/add" style="margin-top:8px">'
           f'<input type="date" name="date" value="{today.isoformat()}" style="width:100%">'
           '<input type="text" name="title" placeholder="예: 한국은행 금통위 금리 결정" style="width:100%;margin-top:6px">'
           '<input type="text" name="note" placeholder="메모 (선택)" style="width:100%;margin-top:6px">'
           '<label class="set"><input type="checkbox" name="important" value="1" checked> 중요 (전날 알림)</label>'
           '<button style="margin-top:6px">추가</button></form>'
           '<div class="muted" style="margin-top:6px">한국은행 금통위·미국 CPI처럼 해마다 날짜가 바뀌는 일정은 '
           '발표된 날짜를 여기에 넣어 주세요.</div></details>')
    head = ('<div class="muted" style="margin-bottom:6px">주식시장에 영향을 주는 일정. ★는 중요, \'예상\'은 해마다 '
            '조금씩 달라지는 대략적인 날입니다.</div>'
            f'<div>{chips}</div>{region_chips}<div style="margin:8px 0">{period}</div>')
    body = head + add + ("".join(rows) or '<p class="muted">이 기간에 일정이 없습니다.</p>')
    if cat in ("", "유대"):
        body += "<h2>유대 명절과 증시</h2>" + jewish_test(conn)
    return page("달력", body)


def gap_pct(v):
    if v is None:
        return "-"
    return f'<span class="{"up" if v > 0 else "down" if v < 0 else ""}">{v:+.2%}</span>'


# ---------------- 정책 ----------------
POLICY_KINDS = {"": "종류 전체", "보도자료": "보도자료", "정책뉴스": "정책뉴스", "법령": "법령 개정", "법안": "국회 법안"}
POLICY_DAYS = {"30": "최근 1달", "90": "최근 3달", "365": "최근 1년"}


def view_policy(conn, q):
    size = rows_per(q, PAGE_SIZE)
    import stock_policy
    if not has_table(conn, "policy_items"):
        return page("정책", '<div class="card">아직 정책 소식이 없습니다. 위쪽 <b>지금 수집 → 시장·정책만</b>을 눌러 주세요.</div>')
    topic = q.get("t") if q.get("t") in stock_policy.TOPICS else ""
    kind = q.get("k") if q.get("k") in POLICY_KINDS else ""
    days = q.get("d") if q.get("d") in POLICY_DAYS else "90"
    text = q.get("q", "").strip()
    pg = int(q["p"]) if str(q.get("p", "")).isdigit() and int(q["p"]) > 0 else 1
    conds, args = ["key != '_meta'", "date >= ?"], [(dt.date.today() - dt.timedelta(days=int(days))).isoformat()]
    if topic:
        conds.append("topics LIKE ?")
        args.append(f'%"{topic}"%')
    if kind:
        conds.append("kind = ?")
        args.append(kind)
    if text:
        conds.append("(title LIKE ? OR summary LIKE ?)")
        args += [f"%{text}%"] * 2
    where = " AND ".join(conds)
    total = conn.execute(f"SELECT COUNT(*) FROM policy_items WHERE {where}", args).fetchone()[0]
    pages = max((total + size - 1) // size, 1)
    pg = min(pg, pages)
    rows = conn.execute(f"SELECT * FROM policy_items WHERE {where} ORDER BY date DESC, first_seen DESC LIMIT ? OFFSET ?",
                        args + [size, (pg - 1) * size]).fetchall()
    meta = conn.execute("SELECT first_seen FROM policy_items WHERE key='_meta'").fetchone()
    base = {k: v for k, v in {"k": kind, "d": days if days != "90" else "", "q": text}.items() if v}
    chips = "".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{escape(t)}</b>' if t == topic else
         f'<a class="chip" href="/policy?{urlencode({**base, "t": t})}">{escape(t)}</a>')
        for t in stock_policy.TOPICS) + (f' <a href="/policy?{urlencode(base)}">전체</a>' if topic else "")
    form = (f'<form method="get" class="filters" style="margin-top:10px"><input type="hidden" name="t" value="{escape(topic)}">'
            f'<input type="search" name="q" value="{escape(text)}" placeholder="제목·내용 검색">'
            f'{select("k", POLICY_KINDS, kind)}{select("d", POLICY_DAYS, days)}<button>보기</button></form>')
    recent = (dt.datetime.now() - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    cards = []
    for i, r in enumerate(rows, (pg - 1) * size + 1):
        tps = json.loads(r["topics"] or "[]")
        new = (not r["baseline"]) and (r["first_seen"] or "") >= recent
        title = escape(r["title"] or "")
        link = f'<a href="{escape(r["url"])}">{title}</a>' if r["url"] else title
        fold = (f'<details style="margin-top:6px"><summary>내용 보기</summary>'
                f'<div style="font-size:14px;margin-top:4px">{escape(r["summary"])}</div></details>') if r["summary"] else ""
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span>'
            f'<span class="chip">{escape(r["kind"] or "")}</span>'
            + (' <span class="chip up">새</span>' if new else "")
            + f' <span class="muted">{escape(r["source"] or "")}</span></div>'
            f'<div class="muted">{escape(r["date"] or "")}</div></div>'
            f'<div style="margin-top:4px">{link}</div>'
            f'<div style="margin-top:4px">' + "".join(f'<span class="chip">{escape(t)}</span>' for t in tps) + f'</div>{fold}</div>')
    link_fn = lambda n: "/policy?" + urlencode({**base, **({"n": q["n"]} if q.get("n") else {}), **({"t": topic} if topic else {}), "p": n})   # noqa: E731
    head = (f'<div class="muted" style="margin-bottom:8px">주식시장에 영향을 주는 정부 보도자료·법령 개정·국회 법안'
            + (f' · 마지막 수집 {escape(meta[0])}' if meta and meta[0] else "") + "</div>")
    return page("정책", head + f'<div>{chips}</div>' + form + f'<div class="muted" style="margin:6px 0">{total:,}건</div>'
                + ("".join(cards) or '<p class="muted">조건에 맞는 소식이 없습니다.</p>') + pager(total, pg, link_fn, size) + rows_bar(q, size))


# ---------------- 운용사별 성적표 ----------------
PERF_HORIZONS = {"30": "30일 뒤", "90": "90일 뒤", "180": "180일 뒤", "now": "오늘까지"}
PERF_MAX_DAYS = 1500    # 직접 입력할 수 있는 최대 보유 기간 (일)


def perf_h_ok(h):
    """'now'(오늘까지) 또는 1~1500 사이의 일수"""
    return h == "now" or (h.isdigit() and 1 <= int(h) <= PERF_MAX_DAYS)


def perf_label(h):
    return "오늘까지" if h == "now" else f"{h}일 뒤"
PERF_KINDS = {"": "기관·개인 전체", "inst": "기관만", "person": "개인(슈퍼개미 추정)만"}
PERF_MIN_N = 5          # 이보다 적은 건수는 '표본 적음'으로 표시


def _last_on(dates, vals, day, maxgap=10):
    """날짜 목록에서 day(포함) 이전 마지막 값 (maxgap일 넘게 떨어졌으면 None)"""
    import bisect
    i = bisect.bisect_right(dates, day.isoformat()) - 1
    if i < 0:
        return None
    if (day - dt.date.fromisoformat(dates[i])).days > maxgap:
        return None
    return vals[i]


def perf_events(conn):
    """기관 매수 한 건씩: (제출인, 종목코드, 제출일) — 같은 날 같은 종목은 한 번만"""
    return conn.execute(
        "SELECT 제출인, 종목코드, 제출일, SUM(매수수량) q FROM summary WHERE 매수수량 > 0 "
        "GROUP BY 제출인, 종목코드, 제출일").fetchall()


def view_performance(conn, q):
    import stock_market
    if not has_table(conn, "summary") or not has_table(conn, "prices"):
        return page("운용사별 성적표", '<div class="card">주가와 보고서 수집이 한 번 끝나야 볼 수 있습니다. '
                                    '위쪽 <b>지금 수집 → 보고서만</b>을 눌러 주세요.</div>')
    h = q.get("h") if perf_h_ok(q.get("h") or "") else "90"
    ex = q.get("ex") if q.get("ex") in ("0", "1") else "1"
    kind = q.get("k") if q.get("k") in PERF_KINDS else ""
    today = dt.date.today()

    prices = {}
    for code, d, c in conn.execute("SELECT code, date, close FROM prices WHERE close IS NOT NULL ORDER BY code, date"):
        prices.setdefault(code, ([], []))
        prices[code][0].append(d)
        prices[code][1].append(c)
    kp = list(stock_market.series(conn, "idx:KOSPI"))
    kd = [p for p, _ in kp]
    kv = [v for _, v in kp]

    names = {r[0]: r[1] for r in conn.execute("SELECT 종목코드, MAX(종목명) FROM summary GROUP BY 종목코드")}
    evs = []                                  # 건별 결과: 상세 화면과 목록이 같이 씀
    for who, code, sub, qty in perf_events(conn):
        if ex == "1" and who and "국민연금" in who:
            continue
        if kind == "person" and not is_person(who):
            continue
        if kind == "inst" and is_person(who):
            continue
        pr = prices.get(code)
        if not pr or not sub:
            continue
        start = dt.date.fromisoformat(sub)
        if h == "now":
            end = today                      # 오늘까지: 제출일부터 오늘 종가까지
        else:
            end = start + dt.timedelta(days=int(h))
            if end > today:
                continue                     # 아직 그만큼 지나지 않은 건은 제외
        p0, p1 = _last_on(pr[0], pr[1], start), _last_on(pr[0], pr[1], end)
        k0, k1 = _last_on(kd, kv, start), _last_on(kd, kv, end)
        if not p0 or not p1 or not k0 or not k1:
            continue
        r = p1 / p0 - 1
        m = k1 / k0 - 1
        evs.append(dict(who=who or "(이름 없음)", code=code, name=names.get(code) or code, sub=sub,
                        end=end.isoformat(), p0=p0, p1=p1, r=r, x=r - m))

    if q.get("who"):
        return view_performance_detail(q.get("who"), [e for e in evs if e["who"] == q.get("who")],
                                       h, ex, kind)

    per = {}
    for e in evs:
        acc = per.setdefault(e["who"], {"r": [], "x": []})
        acc["r"].append(e["r"])
        acc["x"].append(e["x"])

    rows = []
    for who, a in per.items():
        n = len(a["r"])
        rows.append(dict(who=who, n=n, win=sum(1 for v in a["r"] if v > 0) / n,
                         avg=sum(a["r"]) / n, excess=sum(a["x"]) / n))
    rows.sort(key=lambda r: (r["n"] < PERF_MIN_N, -r["excess"]))

    preset = h in PERF_HORIZONS
    chips = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == h else
         f'<a class="chip" href="/performance?{urlencode({"h": k, "ex": ex, **({"k": kind} if kind else {})})}">{v}</a>')
        for k, v in PERF_HORIZONS.items())
    custom_form = (f'<form method="get" class="filters" style="margin-top:6px"><input type="hidden" name="ex" value="{ex}">'
                   + (f'<input type="hidden" name="k" value="{kind}">' if kind else "")
                   + f'<input type="number" name="h" min="1" max="{PERF_MAX_DAYS}" step="1" placeholder="직접 입력 (일)" '
                   + (f'value="{h}"' if not preset else "") + ' style="width:9em">'
                   + '<button>보기</button>'
                   + (f' <span class="muted">{escape(perf_label(h))} 기준으로 보는 중</span>' if not preset else "")
                   + '</form>')
    kchips = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == kind else
         f'<a class="chip" href="/performance?{urlencode({"h": h, "ex": ex, **({"k": k} if k else {})})}">{v}</a>')
        for k, v in PERF_KINDS.items())
    toggle = (f'<div style="margin-bottom:6px">{kchips}</div>'
              f'<a class="chip" href="/performance?{urlencode({"h": h, "ex": "0" if ex == "1" else "1", **({"k": kind} if kind else {})})}">'
              + ("국민연금 포함해서 보기" if ex == "1" else "국민연금 빼고 보기") + "</a>")
    intro = ('<div class="card"><b>이 화면은 무엇인가요?</b><div class="muted" style="margin-top:4px">'
             '기관이 단순투자 공시를 낸 날(제출일)에 그 종목을 샀다고 가정하고, 그 뒤 수익률을 코스피 수익률과 비교했어요. '
             '<b>초과수익</b>이 플러스면 그 기관의 매수 뒤 종목이 시장보다 더 올랐다는 뜻이에요.</div>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 과거 성적이라 앞으로도 같다는 보장은 없어요. '
             f'건수가 {PERF_MIN_N}건 미만이면 우연일 수 있어요. 투자 추천이 아니니 참고만 하세요.</div></div>')
    cards = []
    for i, r in enumerate(rows, 1):
        ok = r["n"] >= PERF_MIN_N
        cls = "up" if r["excess"] > 0 else "down" if r["excess"] < 0 else ""
        wlink = "/performance?" + urlencode({"who": r["who"], "h": h, "ex": ex, **({"k": kind} if kind else {})})
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span><a class="big" href="{wlink}">{escape(r["who"])}</a></div>'
            + ('' if ok else '<span class="chip">표본 적음</span>') + "</div>"
            f'<div>코스피 대비 평균 <b class="{cls}">{r["excess"]:+.1%}</b> · 평균 수익률 {r["avg"]:+.1%}</div>'
            f'<div class="muted">{r["n"]}건 중 {r["win"]:.0%}가 올랐어요</div></div>')
    body = (intro + f'<div style="margin:8px 0"><span class="muted">보유 기간:</span> {chips}{custom_form}</div>'
            + f'<div style="margin-bottom:8px">{toggle}</div>'
            + ("".join(cards) or '<p class="muted">아직 계산할 건이 없어요. 제출일부터 그 기간이 지나야 해요.</p>'))
    return page("운용사별 성적표", body)


def view_performance_detail(who, evs, h, ex, kind):
    """운용사(또는 개인) 한 명의 건별 결과. 제목을 누르면 여기로 옵니다."""
    back_q = {"h": h, "ex": ex, **({"k": kind} if kind else {})}
    back = f'<a href="/performance?{urlencode(back_q)}">← 성적표로</a>'
    head = (f'<div class="row"><div><span class="big">{escape(who)}</span></div><div>{back}</div></div>')
    if not evs:
        return page(f"{who} — 성적표", head + f'<div class="card" style="margin-top:8px">'
                    f'{perf_label(h)} 결과를 계산할 건이 없어요. (국민연금 빼고 보기 설정이나 보유 기간을 바꿔 보세요)</div>')
    n = len(evs)
    win = sum(1 for e in evs if e["r"] > 0) / n
    avg = sum(e["r"] for e in evs) / n
    exc = sum(e["x"] for e in evs) / n
    cls = "up" if exc > 0 else "down" if exc < 0 else ""
    summary = (f'<div class="card" style="margin-top:8px">{n}건 · {perf_label(h)} 기준'
               + ('' if n >= PERF_MIN_N else ' <span class="chip">표본 적음</span>') + "<br>"
               f'코스피 대비 평균 <b class="{cls}">{exc:+.1%}</b> · 평균 수익률 {avg:+.1%} · '
               f'{win:.0%}가 올랐어요</div>')
    rows = "".join(
        f'<tr><td>{escape(e["sub"])}</td>'
        f'<td><a href="/stock?code={quote(e["code"])}">{escape(e["name"])}</a></td>'
        f'<td>{won(e["p0"])}</td><td>{won(e["p1"])}</td>'
        f'<td class="{"up" if e["r"] > 0 else "down" if e["r"] < 0 else ""}">{e["r"]:+.1%}</td>'
        f'<td class="{"up" if e["x"] > 0 else "down" if e["x"] < 0 else ""}">{e["x"]:+.1%}</td></tr>'
        for e in sorted(evs, key=lambda e: e["sub"], reverse=True))
    table = ('<div style="overflow-x:auto;margin-top:8px"><table><tr><th>제출일</th><th>종목</th>'
             f'<th>제출일 종가</th><th>{"오늘 종가" if h == "now" else perf_label(h) + " 종가"}</th><th>수익률</th><th>코스피 대비</th></tr>{rows}</table></div>')
    note = ('<p class="muted">제출일 종가에 샀다고 가정하고, 그 뒤 날짜 종가로 계산했어요. 과거 성적이라 앞으로도 같다는 보장은 없어요.</p>')
    return page(f"{who} — 성적표", head + summary + table + note)


# ---------------- 아이디어 ----------------
IDEA_TABS = {"cheap": "기관 평단보다 싼 종목", "cluster": "여러 기관이 함께 산 종목", "super": "슈퍼개미 움직임"}

# 공시 제출인이 개인인지 추정: 한글 이름 2~4자. 기관 이름(운용·증권·공단 등)이나 '외 N명'은 제외
_INST_WORDS = ("자산", "운용", "증권", "투자", "공단", "펀드", "은행", "보험", "재단", "캐피탈", "파트너스",
               "홀딩스", "자문", "신탁", "연금", "조합", "주식회사", "회사", "금융", "사모", "외")


_COMPANY_CACHE = {"key": None, "names": set()}
_COMPANY_WORDS = ("주식회사", "산업", "중공업", "전자", "화학", "건설", "제약", "바이오", "그룹", "홀딩스", "에너지",
                  "전기", "기계", "물산", "상사", "개발", "시스템", "통신", "해운", "항공", "철강", "식품", "생명",
                  "인베스트", "파트너스", "엔지니어링", "디스플레이", "반도체", "케미칼", "하이닉스", "은행", "카드")


def _norm_company(n):
    return re.sub(r"\s|㈜|주식회사", "", n or "")


def company_names():
    """DART 회사 목록(상장·비상장 전체)의 이름 집합. 캐시가 없으면 빈 집합."""
    path = Path(_DATA_DIR["path"] or ".") / "dart_cache" / "corp_names.json"
    try:
        key = path.stat().st_mtime
    except OSError:
        return set()
    if _COMPANY_CACHE["key"] != key:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            names = set()
            for group in ("stock", "corp"):
                names.update(_norm_company(v) for v in (data.get(group) or {}).values())
        except (OSError, ValueError):
            names = set()
        _COMPANY_CACHE.update(key=key, names=names)
    return _COMPANY_CACHE["names"]


def is_person(name, companies=None):
    """개인(이름으로 추정): 한글 2~4자, 기업 이름이 아니고 기업 말이 없음"""
    n = _norm_company(name).strip()
    if not re.fullmatch(r"[가-힣]{2,4}", n):
        return False
    if any(w in n for w in _INST_WORDS + _COMPANY_WORDS):
        return False
    return not (companies and n in companies)


def super_investors(conn, days, flt=None):
    """개인 제출인의 최근 days일 매수·매도를 사람별로 묶음. flt: q(이름 검색), amt(최소 매수금액), dir(buy/sell)"""
    flt = flt or {}
    companies = company_names()
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT 제출인, 종목코드, MAX(종목명) name, 제출일, "
        "SUM(CASE WHEN 증감>0 THEN 증감 END) buy_q, SUM(CASE WHEN 증감<0 THEN -증감 END) sell_q, "
        "SUM(CASE WHEN 증감>0 AND 단가 IS NOT NULL THEN 증감*단가 END) buy_amt, "
        "SUM(CASE WHEN 증감>0 AND 단가 IS NOT NULL THEN 증감 END) buy_q_p, "
        "SUM(증감) net, MAX(변동일) last FROM detail WHERE 제출일 >= ? GROUP BY 제출인, 종목코드, 제출일",
        (since,)).fetchall()
    closes = latest_closes(conn) if has_table(conn, "prices") else {}
    people = {}
    for r in rows:
        if not is_person(r["제출인"], companies) or not (r["buy_q"] or r["sell_q"]):
            continue                         # 기업·수량 없는 줄은 뺌
        p = people.setdefault(r["제출인"], {"name": r["제출인"], "last": "", "amt": 0.0, "bq": 0, "sq": 0, "items": []})
        p["last"] = max(p["last"], r["제출일"] or "")
        p["amt"] += r["buy_amt"] or 0
        p["bq"] += r["buy_q"] or 0
        p["sq"] += r["sell_q"] or 0
        p["items"].append(r)
    q = (flt.get("q") or "").strip()
    amt = float(flt.get("amt") or 0)
    d = flt.get("dir") or ""
    for p in people.values():
        # 종목별: 기간 안 평균 매수단가, 최근 제출 단가, 최근 종가, 수익률
        by = {}
        for r in sorted(p["items"], key=lambda r: r["제출일"] or ""):
            g = by.setdefault(r["종목코드"], {"name": r["name"] or r["종목코드"], "code": r["종목코드"],
                                              "cost": 0.0, "qty": 0, "last_unit": None, "last_sub": ""})
            if r["buy_q_p"] and r["buy_amt"]:
                g["cost"] += r["buy_amt"]
                g["qty"] += r["buy_q_p"]
                g["last_unit"] = r["buy_amt"] / r["buy_q_p"]
                g["last_sub"] = r["제출일"] or ""
        stocks = []
        for g in by.values():
            g["avg"] = g["cost"] / g["qty"] if g["qty"] else None
            c = closes.get(g["code"])
            g["close"] = c[1] if c else None
            g["ret"] = (g["close"] / g["avg"] - 1) if g["avg"] and g["close"] else None
            if g["avg"] or g["last_unit"]:
                stocks.append(g)
        p["stocks"] = stocks
        rets = [(g["ret"], g["cost"]) for g in stocks if g["ret"] is not None and g["cost"]]
        p["ret"] = sum(r * c for r, c in rets) / sum(c for _, c in rets) if rets else None
    q = (flt.get("q") or "").strip()
    amt = float(flt.get("amt") or 0)
    d = flt.get("dir") or ""
    out = []
    for p in people.values():
        if q and q not in p["name"]:
            continue
        if amt and p["amt"] < amt:
            continue
        if d == "buy" and not p["bq"]:
            continue
        if d == "sell" and not p["sq"]:
            continue
        out.append(p)
    srt = flt.get("sort") or "date"
    if srt == "amt":
        out.sort(key=lambda p: p["amt"], reverse=True)
    elif srt == "name":
        out.sort(key=lambda p: p["name"])
    elif srt == "ret":
        out.sort(key=lambda p: (p["ret"] is None, -(p["ret"] or 0)))
    else:
        out.sort(key=lambda p: (p["last"], p["amt"]), reverse=True)
    for p in out:
        p["items"].sort(key=lambda r: r["제출일"] or "", reverse=True)
    return out


NPS_OPTS = {"1": "국민연금 제외", "0": "국민연금 포함"}
WINDOWS = {"30": "최근 1달 매수", "90": "최근 3달 매수", "180": "최근 6달 매수", "365": "최근 1년 매수"}


def view_super(conn, q, days):
    flt = {"q": q.get("sq", ""), "amt": q.get("amt", ""), "dir": q.get("dir", ""), "sort": q.get("sort", "")}
    people = super_investors(conn, days, flt)
    if not people:
        return ('<div class="card">조건에 맞는 개인 공시가 없어요. 기간이나 필터를 바꿔 보세요.</div>')

    def pct(v):
        return "-" if v is None else f'<span class="{"up" if v > 0 else "down" if v < 0 else ""}">{v:+.1%}</span>'

    cards = []
    for i, p in enumerate(people[:30], 1):
        lines = []
        for g in sorted(p["stocks"], key=lambda g: -(g["cost"] or 0))[:6]:
            avg = f'평균 {won(g["avg"])}원' if g["avg"] else "평균 -"
            last = f'최근 제출 {won(g["last_unit"])}원 ({escape(g["last_sub"])})' if g["last_unit"] else ""
            now = f'현재 {won(g["close"])}원 · 수익률 {pct(g["ret"])}' if g["close"] else "현재가 없음"
            lines.append(f'<div style="margin-top:6px"><b>{escape(str(g["name"]))}</b> <span class="muted">{escape(str(g["code"]))}</span>'
                         f'<div>{avg} · {last}</div><div class="muted">{now}</div></div>')
        more = f'<div class="muted" style="margin-top:4px">종목 {len(p["stocks"])}개 중 6개만 표시</div>' if len(p["stocks"]) > 6 else ""
        act = " · ".join(x for x in (f'매수 {won(p["bq"])}주' if p["bq"] else "", f'매도 {won(p["sq"])}주' if p["sq"] else "",
                                     f'약 {eok(p["amt"])}' if p["amt"] else "") if x)
        who_link = f'/investor?{urlencode({"name": p["name"], "w": q.get("w", "90")})}'
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span><a class="big" href="{who_link}">{escape(p["name"])}</a></div>'
            f'<div class="muted">최근 제출 {escape(p["last"])}</div></div>'
            f'<div>{act}</div>'
            f'<div>추정 수익률(평균 매수가 대비 현재가): <b>{pct(p["ret"])}</b></div>'
            + "".join(lines) + more + "</div>")
    return ("".join(cards)
            + '<p class="muted" style="font-size:12px">수익률은 기간 안에서 공시로 확인된 매수 가격의 평균과 최근 종가로 계산한 추정치예요. '
              '단가가 없는 줄은 빼고 계산해요.</p>')


def latest_closes(conn):
    """종목코드 → (날짜, 최근 종가)"""
    return {r[0]: (r[2], r[1]) for r in conn.execute("SELECT code, close, MAX(date) FROM prices GROUP BY code")}


def tgt_line(conn, code, close):
    tgt, n = consensus(conn, code)
    return f'<div>증권사 목표가 평균 <b>{won(tgt)}원</b> ({n}곳) {upside(tgt, close)}</div>' if tgt else ""


def view_investor(conn, name, days):
    """슈퍼개미 한 사람의 상세: 종목별 평균·수익률, 공시 전체 기록(날짜순), 공시 원문·표 확인 링크"""
    people = [p for p in super_investors(conn, days, {"q": name}) if p["name"] == name]
    if not people:
        return page("슈퍼개미", f'<div class="card">{escape(name)}님의 최근 {days}일 공시를 찾지 못했어요.</div>')
    p = people[0]
    ret_txt = "-" if p["ret"] is None else f'{p["ret"]:+.1%}'
    head = (f'<div class="card"><div class="row"><div><b class="big">{escape(name)}</b> '
            f'<span class="muted">개인 투자자(이름으로 추정)</span></div>'
            f'<div class="muted">최근 제출 {escape(p["last"])}</div></div>'
            f'<div style="margin-top:6px">기간 안 매수 <b>{won(p["bq"])}주</b>'
            + (f' (약 {eok(p["amt"])})' if p["amt"] else "")
            + (f' · 매도 <b>{won(p["sq"])}주</b>' if p["sq"] else "")
            + f' · 추정 수익률 <b>{ret_txt}</b></div>'
            '<div class="muted" style="font-size:12px;margin-top:4px">수익률은 공시 매수 가격과 최근 종가로 계산한 추정치예요. 참고용입니다.</div></div>')
    stocks = ""
    for g in sorted(p["stocks"], key=lambda g: -(g["cost"] or 0)):
        ret = "-" if g["ret"] is None else f'<span class="{"up" if g["ret"] > 0 else "down"}">{g["ret"]:+.1%}</span>'
        stocks += (f'<tr><td><b>{escape(str(g["name"]))}</b><div class="muted">{escape(str(g["code"]))}</div></td>'
                   f'<td>{won(g["avg"]) if g["avg"] else "-"}</td><td>{won(g["last_unit"]) if g["last_unit"] else "-"}</td>'
                   f'<td>{won(g["close"]) if g["close"] else "-"}</td><td>{ret}</td></tr>')
    stock_tbl = ('<h2>종목별</h2><div class="scroll"><table><tr><th>종목</th><th>평균 매수가</th><th>최근 제출 단가</th>'
                 f'<th>현재가</th><th>수익률</th></tr>{stocks}</table></div>') if stocks else ""
    hist = ""
    for r in p["items"]:
        if r["buy_q"] and r["sell_q"]:
            act = f'매수 {won(r["buy_q"])}주 · 매도 {won(r["sell_q"])}주'
        elif r["buy_q"]:
            act = f'<span class="up">매수 {won(r["buy_q"])}주</span>' + (f' (약 {eok(r["buy_amt"])})' if r["buy_amt"] else "")
        else:
            act = f'<span class="down">매도 {won(r["sell_q"])}주</span>'
        hist += (f'<div class="card"><div class="row"><b>{escape(str(r["제출일"]))}</b>'
                 f'<span class="muted">변동일 {escape(str(r["last"]))}</span></div>'
                 f'<div>{escape(str(r["name"] or r["종목코드"]))} <span class="muted">{escape(str(r["종목코드"]))}</span> · {act}</div></div>')
    return page(f"{name} — 슈퍼개미", head + stock_tbl + "<h2>공시 기록</h2>" + (hist or '<p class="muted">기록 없음</p>')
                + f'<p><a href="/performance?k=person">개인 투자자 성적표 보기 →</a> · <a href="/ideas?t=super">← 슈퍼개미 목록</a></p>')


IDEA_SORTS = {"": "정렬: 기본", "amt": "정렬: 매수금액 큰 순", "n": "정렬: 기관 수 많은 순",
              "disc": "정렬: 평단 대비 많이 싼 순", "gain": "정렬: 평단 대비 많이 오른 순",
              "last": "정렬: 최근 매수일순", "name": "정렬: 이름순",
              "pbr": "정렬: PBR 낮은 순"}


def sort_ideas(ideas, srt, tab):
    """아이디어 목록 정렬. srt가 없으면 탭의 기본 순서 그대로."""
    if not srt:
        return ideas
    key = {
        "amt": lambda i: -i["r"]["amt"],
        "n": lambda i: (-i["r"]["n"], -i["r"]["amt"]),
        "disc": lambda i: i["disc"],
        "gain": lambda i: -i["disc"],
        "last": lambda i: str(i["r"]["last"] or ""),
        "name": lambda i: str(i["r"]["name"] or ""),
        "pbr": lambda i: (i["v"].get("pbr") is None, i["v"].get("pbr") or 0),
    }[srt]
    return sorted(ideas, key=key, reverse=(srt == "last"))


def view_ideas(conn, q):
    if not has_table(conn, "summary"):
        return page("아이디어", '<div class="card">보고서(DART) 수집이 한 번 끝나야 볼 수 있습니다. '
                              '위쪽 <b>지금 수집 → 보고서만</b>을 눌러 주세요.</div>')
    if q.get("t") == "super" and has_table(conn, "detail"):
        w = q.get("w") if q.get("w") in WINDOWS else "90"
        tabs = " ".join(
            (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == "super" else
             f'<a class="chip" href="/ideas?{urlencode({"t": k})}">{v}</a>') for k, v in IDEA_TABS.items())
        win = " ".join(
            (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == w else
             f'<a class="chip" href="/ideas?{urlencode({"t": "super", "w": k})}">{v}</a>')
            for k, v in (("30", "최근 1달"), ("90", "최근 3달"), ("180", "최근 6달"), ("365", "최근 1년")))
        try:
            listing = view_super(conn, q, int(w))
        except Exception as e:                # 한 부분이 실패해도 화면은 열리게
            listing = f'<div class="card warn">슈퍼개미 목록을 만드는 중 문제가 생겼습니다: {escape(str(e))[:120]}</div>'
        fq = escape(q.get("sq", ""))
        amt_opts = {"": "최소 매수금액 없음", "100000000": "1억 이상", "1000000000": "10억 이상", "10000000000": "100억 이상"}
        dir_opts = {"": "매수·매도 전체", "buy": "매수한 사람만", "sell": "매도한 사람만"}
        sort_opts = {"date": "정렬: 최근 제출일순", "amt": "정렬: 매수금액순", "name": "정렬: 이름순", "ret": "정렬: 수익률순"}
        form = (f'<form method="get" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="super">'
                f'<input type="hidden" name="w" value="{escape(w)}">'
                f'<input type="search" name="sq" value="{fq}" placeholder="이름 검색">'
                f'{select("amt", amt_opts, q.get("amt", ""))}{select("dir", dir_opts, q.get("dir", ""))}'
                f'{select("sort", sort_opts, q.get("sort", "date"))}<button>적용</button></form>')
        body = (tabs + f'<div style="margin:8px 0">{win}</div>' + form + listing
                + f'<p style="margin-top:10px"><a href="/performance?k=person">개인 투자자 성적표 보기 →</a></p>')
        return page("아이디어", body)
    if not has_table(conn, "prices"):
        return page("아이디어", '<div class="card">주가 데이터가 아직 없습니다. 위쪽 <b>지금 수집 → 보고서만</b>을 '
                              '한 번 돌리면 볼 수 있습니다. (설정 → 수집의 \'주가 받기\'가 켜져 있어야 합니다)</div>')
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    tab = q.get("t") if q.get("t") in IDEA_TABS else "cheap"
    w = q.get("w") if q.get("w") in WINDOWS else "90"
    ex = q.get("ex") if q.get("ex") in NPS_OPTS else "1"
    m = q.get("m") if q.get("m") in MARKET_OPTS and "시장" in cols else ""
    conds = ["변동일 >= ?", "매수수량 > 0", "평균매수단가 > 0"]
    args = [(dt.date.today() - dt.timedelta(days=int(w))).isoformat()]
    if ex == "1":
        conds.append("제출인 NOT LIKE '%국민연금%'")
    if m:
        conds.append("시장 = ?")
        args.append(m)
    market = "MAX(시장)" if "시장" in cols else "''"
    rows = conn.execute(
        f"SELECT 종목코드 code, MAX(종목명) name, {market} mk, COUNT(DISTINCT 제출인) n, "
        f"GROUP_CONCAT(DISTINCT 제출인) whos, SUM(매수수량) qty, SUM(매수금액) amt, MAX(변동일) last "
        f"FROM summary WHERE {' AND '.join(conds)} GROUP BY 종목코드", args).fetchall()
    closes = latest_closes(conn)
    ideas = []
    for r in rows:
        if r["code"] not in closes or not r["qty"]:
            continue
        day, close = closes[r["code"]]
        avg = r["amt"] / r["qty"]
        ideas.append(dict(r=r, avg=avg, close=close, day=day, disc=close / avg - 1))
    srt = q.get("s") if q.get("s") in IDEA_SORTS else ""
    vals = value_map(conn)
    for i in ideas:
        i["v"] = vals.get(i["r"]["code"]) or {}
    try:
        import stock_value
        fsum = stock_value.flow_sums(conn, [i["r"]["code"] for i in ideas[:30]], 5)
    except Exception:
        fsum = {}
    if tab == "cheap":
        ideas = sorted((i for i in ideas if i["disc"] < 0), key=lambda i: i["disc"])
        ideas = sort_ideas(ideas, srt, tab)
        chart = bar_list([(i["r"]["name"], f'/stock?code={quote(i["r"]["code"])}', i["disc"] * 100) for i in ideas[:12]],
                         lambda v: f"{v:+.1f}%")
        chart_title = "최근 종가가 기관 평균매수단가보다 몇 % 낮은지"
    else:
        ideas = sorted((i for i in ideas if i["r"]["n"] >= 2), key=lambda i: (-i["r"]["n"], -i["r"]["amt"]))
        ideas = sort_ideas(ideas, srt, tab)
        chart = bar_list([(i["r"]["name"], f'/stock?code={quote(i["r"]["code"])}', i["r"]["amt"]) for i in ideas[:12]],
                         lambda v: eok(v))
        chart_title = "함께 산 기관들의 매수금액 합계"

    base = {"w": w, "ex": ex, **({"m": m} if m else {})}
    tabs = " ".join(f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == tab
                    else f'<a class="chip" href="/ideas?{urlencode({**base, "t": k})}">{v}</a>' for k, v in IDEA_TABS.items())
    form = (f'<form method="get" class="filters" style="margin-top:10px"><input type="hidden" name="t" value="{tab}">'
            f'{select("w", WINDOWS, w)}' + (select("m", MARKET_OPTS, m) if "시장" in cols else "")
            + f'{select("ex", NPS_OPTS, ex)}{select("s", IDEA_SORTS, srt)}<button>적용</button></form>')
    perf_link = '<p><a href="/performance">운용사별 성적표 보기 →</a> (어느 기관의 매수가 과거에 잘 맞았는지)</p>'
    note = perf_link + ('<p class="muted">살펴볼 후보를 찾는 화면입니다. 공시는 매매 후 최대 5영업일 뒤에 나오고, '
            '국민연금처럼 큰 기관은 비중 조절로 사고팔 때가 많아 기본으로 뺐습니다.</p>')
    if not ideas:
        priced = sum(1 for r in rows if r["code"] in closes)
        why = (f'{WINDOWS[w]} 기록이 있는 종목이 없습니다. ' if not rows else
               f'{WINDOWS[w]} 기록이 있는 종목 {len(rows)}개 중 주가가 있는 종목 {priced}개에서 '
               + ("최근 종가가 기관 평단보다 낮은 종목이 없습니다." if tab == "cheap"
                  else "기관 2곳 이상이 함께 산 종목이 없습니다.") if rows else "")
        hint = "기간을 늘리거나 '국민연금 포함', 시장 전체로 바꿔 보세요." if len(rows) < 5 or tab == "cluster" else ""
        if rows and not priced:
            hint = "주가가 아직 없습니다. 위쪽 지금 수집 → 보고서만을 한 번 돌려 주세요."
        return page("아이디어", tabs + form + note + f'<div class="card">{escape(why)}<div class="muted">{escape(hint)}</div></div>')
    cards = []
    for k, i in enumerate(ideas[:30], 1):
        r = i["r"]
        pr = [p[0] for p in conn.execute("SELECT close FROM prices WHERE code=? ORDER BY date DESC LIMIT 120",
                                          (r["code"],)).fetchall()][::-1]
        whos = (r["whos"] or "").split(",")
        who_txt = ", ".join(whos[:3]) + (f" 외 {len(whos) - 3}곳" if len(whos) > 3 else "")
        mk = f' <span class="chip">{escape(r["mk"])}</span>' if r["mk"] else ""
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{k}</span>'
            f'<a class="big" href="/stock?code={quote(r["code"])}">{escape(r["name"])}</a> '
            f'<span class="muted">{escape(r["code"])}</span>{mk}</div>'
            f'<div class="muted">마지막 매수 {escape(r["last"])}</div></div>'
            f'<div>기관 {r["n"]}곳: {escape(who_txt)}</div>'
            f'<div>매수 {eok(r["amt"])} · 평단 <b>{won(i["avg"])}원</b> · 최근 종가 {won(i["close"])}원 '
            f'{gap(i["close"], i["avg"])}</div>{tgt_line(conn, r["code"], i["close"])}'
            + (f'<div class="muted">PER {i["v"]["per"]:.1f}배 · PBR {i["v"]["pbr"]:.2f}배</div>'
               if i["v"].get("pbr") is not None and i["v"].get("per") is not None else "")
            + (f'<div class="muted">최근 5일 외국인 {_sg(fsum[r["code"]][0])}주 · 기관 {_sg(fsum[r["code"]][1])}주</div>'
               if r["code"] in fsum else "")
            + f'{sparkline(pr, i["avg"])}</div>')
    body = (tabs + form + note + f"<h2>{chart_title}</h2>" + chart
            + '<div class="legend"><span><i class="ln" style="background:var(--price)"></i>최근 120거래일 종가</span>'
              '<span><i class="dash"></i>기관 평균매수단가</span></div>' + "".join(cards))
    return page("아이디어", body)


def latest_excel(conn):
    if not has_table(conn, "runs"):
        return None
    for (info,) in conn.execute("SELECT info FROM runs WHERE ok=1 ORDER BY rowid DESC LIMIT 5"):
        p = json.loads(info or "{}").get("excel")
        if p and os.path.exists(p):
            return p
    return None


# ---------------- 서버 ----------------
def make_handler(db_path, status_path, run_cmd, child_env=None, get_key=None, save_key=None, settings_api=None):
    def settings():
        if not settings_api:
            return None
        S = settings_api()
        _NAV["realestate"], _NAV["links"] = S["realestate"], S["links"]
        return S

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, ctype, data, headers=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            for k, v in (*headers, *getattr(self, "_extra", ())):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/manifest.webmanifest":
                return self._send(200, "application/manifest+json", MANIFEST)
            if url.path in ("/icon-192.png", "/icon-512.png"):
                size = 192 if "192" in url.path else 512
                if size not in _ICONS:
                    _ICONS[size] = icon_png(size)
                return self._send(200, "image/png", _ICONS[size], [("Cache-Control", "max-age=86400")])
            if url.path == "/favicon.ico":
                return self._send(204, "text/plain", b"")
            S = settings()
            st = load_status(status_path)
            _NAV["status"] = st
            # 한 번에 보는 줄 수: 주소의 n을 폰에 기억해 두고, 주소에 없으면 기억한 값을 씀
            self._extra = []
            q["_path"] = url.path
            saved = (re.search(r"(?:^|;\s*)rows=(\d+)", self.headers.get("Cookie") or "") or [None, ""])[1]
            if q.get("n") in {str(c) for c in ROW_CHOICES}:
                if q["n"] != saved:
                    self._extra.append(("Set-Cookie", f"rows={q['n']}; Path=/; Max-Age=31536000; SameSite=Lax"))
            elif saved in {str(c) for c in ROW_CHOICES}:
                q["n"] = saved
            if url.path == "/health":
                return self._send(200, "application/json", json.dumps(st, ensure_ascii=False).encode())
            conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True) if Path(db_path).exists() else sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            try:
                if url.path.startswith("/backup/") and settings_api:
                    name = url.path[len("/backup/"):]
                    data = settings()["backup_file"](name, q.get("key") == "1")
                    if data is None:
                        return self._send(404, "text/plain; charset=utf-8", "백업 파일이 없습니다.".encode("utf-8"))
                    return self._send(200, "application/gzip", data,
                                      [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")])
                if url.path == "/excel" and q.get("list") and has_table(conn, "summary"):
                    data = filtered_excel(conn, q)
                    fname = quote(f"단순투자_목록_{dt.date.today():%Y%m%d}.xlsx")
                    return self._send(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                      data, [("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")])
                if url.path == "/excel":
                    p = latest_excel(conn)
                    if not p:
                        body = page("엑셀", '<p class="muted">아직 만들어진 엑셀이 없습니다.</p>')
                    else:
                        data = Path(p).read_bytes()
                        fname = quote(os.path.basename(p))
                        return self._send(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                          data, [("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")])
                elif url.path == "/":
                    body = view_home(conn, q, st, need_key=bool(get_key) and not get_key(), S=S)
                elif url.path == "/settings":
                    body = view_settings(get_key, q, S)
                elif url.path == "/check":
                    body = view_check(conn, st)
                elif url.path == "/parse":
                    body = view_parse(conn, q.get("r", ""))
                elif url.path == "/calendar":
                    body = view_calendar(conn, q)
                elif url.path == "/market":
                    body = view_market(conn)
                elif url.path == "/policy":
                    body = view_policy(conn, q)
                elif url.path == "/reports":
                    body = view_report_list(conn, q)
                elif url.path == "/investor":
                    w = q.get("w") if q.get("w") in WINDOWS else "90"
                    body = view_investor(conn, q.get("name", ""), int(w))
                elif url.path == "/performance":
                    body = view_performance(conn, q)
                elif url.path == "/ideas":
                    body = view_ideas(conn, q)
                elif url.path == "/new":
                    body = view_new(conn, q)
                elif url.path == "/stock":
                    body = view_stock(conn, q.get("code", ""), q)
                else:
                    body = None
            finally:
                conn.close()
            if body is None:
                return self._send(404, "text/html; charset=utf-8",
                                  page("없는 페이지", '<p>찾는 페이지나 종목이 없습니다.</p><p><a href="/">보고서로</a></p>'))
            self._send(200, "text/html; charset=utf-8", body)

        def _start_run(self, what="all"):
            """수집 시작 (이미 돌고 있으면 False). what: all / dart / reports"""
            st = load_status(status_path)
            running = st.get("state") == "running" and time.time() - st.get("updated", 0) < 120
            if running or not run_cmd:
                return False
            what = what if what in ("all", "dart", "reports", "market", "reanalyze") else "all"
            subprocess.Popen(run_cmd + ["--only", what], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, env=child_env)
            time.sleep(1.5)   # 상태 파일이 생길 시간
            return True

        def _redirect(self, where):
            self.send_response(303)
            self.send_header("Location", where)
            self.end_headers()

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/settings/save" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore"),
                                                     keep_blank_values=True).items()}
                S = settings()
                changed, errors = S["apply"](form)
                settings()
                qs = {"ok": ", ".join(changed)}
                if errors:
                    qs["errs"] = json.dumps(errors, ensure_ascii=False)
                return self._redirect("/settings?" + urlencode(qs))
            if path in ("/calendar/add", "/calendar/del"):
                n = min(int(self.headers.get("Content-Length") or 0), 8192)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore")).items()}
                import market_calendar as mc
                conn = sqlite3.connect(db_path)
                try:
                    mc.ensure(conn)
                    if path.endswith("add") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", form.get("date", "")) \
                            and form.get("title", "").strip():
                        conn.execute("INSERT INTO cal_events (date, title, note, important) VALUES (?,?,?,?)",
                                     (form["date"], form["title"].strip()[:100], form.get("note", "").strip()[:200],
                                      1 if form.get("important") else 0))
                    elif path.endswith("del") and str(form.get("id", "")).isdigit():
                        conn.execute("DELETE FROM cal_events WHERE id=?", (int(form["id"]),))
                    conn.commit()
                finally:
                    conn.close()
                return self._redirect("/calendar")
            if path == "/settings/collect" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore"),
                                                     keep_blank_values=True).items()}
                changed, errors = settings()["apply"](form, only=["DART_KEYWORD", "DART_BODY_WORD"])
                if errors:
                    msg = "✖ " + " ".join(errors.values())
                else:
                    msg = "✔ 저장했습니다." if changed else "바뀐 조건이 없습니다."
                    if form.get("run") == "1":
                        if self._start_run():
                            msg += " 수집을 시작했습니다."
                        else:
                            msg += " 이미 수집 중이라 끝난 뒤 다시 눌러 주세요."
                return self._redirect("/?" + urlencode({"cm": msg}))
            if path == "/settings/backup-run" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                self.rfile.read(n)
                ok, why = settings()["backup_start"]()
                return self._redirect("/settings?" + urlencode({"nt": ("✔ " if ok else "✖ ") + why}))
            if path in ("/settings/update-check", "/settings/update-run") and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                self.rfile.read(n)          # 함께 온 설정 칸 값은 쓰지 않음
                S = settings()
                if path.endswith("check"):
                    res = S["check_update"]()
                    msg = (f"✖ {res['error']}" if res.get("error") else
                           f"새 버전이 있습니다 ({res['behind']}개 변경)" if res.get("behind") else "✔ 최신 버전입니다")
                else:
                    ok, why = S["start_update"]()
                    msg = ("✔ " if ok else "✖ ") + why
                return self._redirect("/settings?" + urlencode({"nt": msg}))
            if path == "/settings/notify-test" and settings_api:
                ok, why = settings()["test_notify"]()
                return self._redirect("/settings?" + urlencode({"nt": ("✔ " if ok else "✖ ") + why}))
            if path == "/settings/key" and save_key:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
                key = "".join((form.get("key") or [""])[0].split())
                if not key.isalnum() or not 20 <= len(key) <= 64:
                    return self._redirect("/settings?err=" + quote("키 형식이 맞지 않습니다. 40자리 영문·숫자 키를 그대로 붙여넣으세요."))
                ok, why = save_key(key)
                if not ok:
                    return self._redirect("/settings?err=" + quote(why))
                return self._redirect("/settings?saved=1&why=" + quote(why))
            if path != "/run":
                return self._send(404, "text/plain", b"not found")
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = parse_qs(self.rfile.read(n).decode("utf-8", "ignore")) if n else {}
            self._start_run((form.get("what") or ["all"])[0])
            back = urlparse(self.headers.get("Referer") or "/")
            where = (back.path or "/") + (f"?{back.query}" if back.query else "")
            self.send_response(303)
            self.send_header("Location", where if where.startswith("/") else "/")
            self.end_headers()

        def log_message(self, *args):  # 조용히
            pass

    return Handler


def serve(db_path, host="127.0.0.1", port=8766, status_path=None, child_env=None, get_key=None, save_key=None,
          settings_api=None):
    db_path = Path(db_path)
    status_path = status_path or db_path.parent / "status.json"
    _DATA_DIR["path"] = str(db_path.parent)
    run_cmd = [sys.executable, str(Path(__file__).resolve().parent / "dart_server.py"), "run"]
    server = ThreadingHTTPServer((host, port), make_handler(db_path, status_path, run_cmd, child_env, get_key, save_key, settings_api))
    print(f"대시보드: http://{host}:{port}  (끄려면 Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
