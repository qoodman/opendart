# -*- coding: utf-8 -*-
"""
DART 단순투자 대시보드 (폰용, 파이썬 기본 기능만 사용)

    python dart_server.py web     → http://127.0.0.1:8766
서버에서는 Tailscale(serve)로만 공개해서 내 기기에서만 열립니다.
"""
import os, sys, json, time, sqlite3, subprocess, datetime as dt
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from dart_charts import CHART_CSS, CHART_JS, bar_list, price_chart, sparkline
from hk_consensus import consensus

PAGE_SIZE = 60

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1b1d21;--muted:#666b73;--line:#e3e6ea;--accent:#2b5a9e;
--chip:#edf2f9;--up:#c23b3b;--down:#2b62c2;--warn:#a5442b}
@media (prefers-color-scheme:dark){:root{--bg:#141518;--card:#1d1f23;--fg:#eceef1;
--muted:#a0a5ad;--line:#30333a;--accent:#86aee8;--chip:#242a34;--up:#ef7d7d;--down:#7ea6ef;--warn:#e08a6f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:14px;
align-items:baseline;flex-wrap:wrap}header b{font-size:17px}header nav a{margin-right:12px}
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


def page(title, body, refresh=False, charts=False):
    other = os.environ.get("REALESTATE_URL", "").strip()
    extra = f'<a href="{escape(other)}">부동산 ↗</a>' if other else ""
    meta = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{meta}
<title>{escape(title)}</title><style>{CSS}{CHART_CSS}</style>
<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#2b5a9e">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png">
</head><body>
<header><b>DART 단순투자</b><nav><a href="/">요약</a><a href="/ideas">아이디어</a><a href="/new">새 공시</a>
<a href="/excel">엑셀</a>{extra}</nav>
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
                '<b>아직 수집 결과가 없습니다</b></div>' + run_button() + '</div>'), False
    info = json.loads(last["info"] or "{}")
    if last["ok"]:
        part = "" if info.get("complete", True) else ' · <span class="warn">일부만 받음(다음 실행 때 이어서)</span>'
        txt = (f'<span class="dot ok"></span><b>마지막 수집 {escape(last["finished"])}</b>'
               f'<span class="muted">기간 {escape(info.get("start", ""))}~{escape(info.get("end", ""))} · '
               f'API {info.get("calls", 0):,}회{part}</span>')
    else:
        txt = (f'<span class="dot bad"></span><b>수집 실패 {escape(last["finished"])}</b>'
               f'<span class="muted warn">{escape(info.get("error", ""))}</span>')
    return f'<div class="card"><div class="status" style="flex-wrap:wrap">{txt}</div>{run_button()}</div>', False


def run_button():
    return ('<form method="post" action="/run" style="margin-top:6px;display:flex;gap:12px;align-items:center">'
            '<button class="ghost" type="submit">지금 수집</button><a class="muted" href="/settings">API 키 설정</a></form>')


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


def view_settings(get_key, q):
    cur = get_key() if get_key else ""
    msg, bad = "", False
    if q.get("saved"):
        msg = "저장했습니다. " + q.get("why", "")
    elif q.get("err"):
        msg, bad = q["err"], True
    body = key_form(msg, bad, cur)
    if q.get("saved"):
        body += run_button()
    body += '<p><a href="/">← 요약으로</a></p>'
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


def pager(total, pg, link):
    """1 2 3 … 처럼 번호로 된 페이지 이동"""
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
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


def view_home(conn, q, st, need_key=False):
    from urllib.parse import urlencode
    box, running = status_box(conn, st)
    if need_key and not running:
        box = key_form("API 키가 없어서 수집할 수 없습니다. 여기에 넣으면 바로 저장됩니다.", True) + box
    if not has_table(conn, "summary"):
        return page("DART 단순투자", box, refresh=running)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    text, who = q.get("q", "").strip(), q.get("who", "").strip()
    f = {k: (q.get(k, "") if q.get(k, "") in opts else "") for k, opts in FILTERS.items()}
    sort = q.get("sort", "submit") if q.get("sort") in SORTS else "submit"
    pg = int(q["p"]) if str(q.get("p", "")).isdigit() and int(q["p"]) > 0 else 1

    conds, args = [], []
    if text:
        conds.append("(종목명 LIKE ? OR 종목코드 LIKE ?)")
        args += [f"%{text}%"] * 2
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
    total = conn.execute(f"SELECT COUNT(*) FROM summary {where}", args).fetchone()[0]
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    pg = min(pg, pages)
    rows = conn.execute(f"SELECT * FROM summary {where} ORDER BY {SORTS[sort][1]} LIMIT ? OFFSET ?",
                        args + [PAGE_SIZE, (pg - 1) * PAGE_SIZE]).fetchall()

    active = [FILTERS[k][v] for k, v in f.items() if v] + ([f"제출인: {who}"] if who else [])
    form = (f'<form method="get"><div class="filters"><input type="search" name="q" value="{escape(text)}" '
            f'placeholder="종목명·종목코드"><button>보기</button></div>'
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
    start = (pg - 1) * PAGE_SIZE
    cards = "".join(summary_card(r, start + i + 1) for i, r in enumerate(rows)) \
        or '<p class="muted">조건에 맞는 매수 내역이 없습니다.</p>'
    keep = {k: v for k, v in {"q": text, "who": who, "sort": sort if sort != "submit" else "", **f}.items() if v}
    link = lambda n: "/?" + urlencode({**keep, "p": n})   # noqa: E731
    info = f'<div class="muted" style="margin-top:6px">{total:,}건 중 {start + 1 if total else 0:,}–{min(start + PAGE_SIZE, total):,}</div>'
    return page("DART 단순투자", box + form + chips + cards + pager(total, pg, link) + info, refresh=running)


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


def view_stock(conn, code):
    if not code or not has_table(conn, "detail"):
        return None
    rows = conn.execute("SELECT * FROM detail WHERE 종목코드=? ORDER BY 변동일 DESC, 제출일 DESC", (code,)).fetchall()
    if not rows:
        return None
    name = rows[0]["종목명"]
    summ = conn.execute("SELECT * FROM summary WHERE 종목코드=? ORDER BY 변동일 DESC", (code,)).fetchall()
    head = (f'<div class="row"><div><span class="big">{escape(str(name))}</span> '
            f'<span class="muted">{escape(code)}</span></div>'
            f'<div><a href="{naver_link(code)}">네이버 증권 ↗</a></div></div>')
    t1 = "".join(f'<tr><td>{escape(str(r["변동일"]))}</td><td>{escape(str(r["제출인"]))}</td>'
                 f'<td>{won(r["매수수량"])}</td><td>{won(r["평균매수단가"])}</td>'
                 f'<td>{won(r["제출일종가"])} {gap(r["제출일종가"], r["평균매수단가"])}</td>'
                 f'<td>{escape(str(r["제출일"]))}</td></tr>' for r in summ)
    t2 = "".join(f'<tr><td>{escape(str(r["변동일"]))}</td><td>{escape(str(r["보고자"]))}</td>'
                 f'<td>{escape(str(r["방법"]))}</td><td>{won(r["증감"])}</td><td>{won(r["단가"])}</td>'
                 f'<td><a href="{dart_link(r["접수번호"])}">{escape(str(r["제출일"]))}</a></td></tr>' for r in rows)
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
    body = (head + chart + reports + "<h2>날짜별 평균 매수단가</h2>"
            + (f'<div class="scroll"><table><tr><th>변동일</th><th>제출인</th><th>매수수량</th><th>평균단가</th>'
               f'<th>제출일 종가</th><th>제출일</th></tr>{t1}</table></div>' if t1 else '<p class="muted">매수 내역 없음</p>')
            + "<h2>모든 변동 내역</h2>"
            + f'<div class="scroll"><table><tr><th>변동일</th><th>보고자</th><th>방법</th><th>증감</th><th>단가</th>'
              f'<th>제출일(원문)</th></tr>{t2}</table></div>')
    return page(f"{name} — DART 단순투자", body, charts=bool(chart))


def upside(target, close):
    """목표가가 종가보다 몇 % 위인지 (상승여력)"""
    try:
        u = float(target) / float(close) - 1
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    return f'<span class="{"up" if u > 0 else "down"}">상승여력 {u:+.1%}</span>'


def title_link(r):
    t = escape((r["title"] or "")[:40])
    return f'<a href="{escape(r["pdf"])}">{t}</a>' if r["pdf"] else t


def view_reports(conn, code, tgt, n_br, close):
    """종목 화면의 '증권사 목표주가' 부분"""
    if not has_table(conn, "hk_reports"):
        return ""
    rows = conn.execute("SELECT * FROM hk_reports WHERE code=? ORDER BY date DESC LIMIT 20", (code,)).fetchall()
    if not rows:
        return '<h2>증권사 목표주가</h2><p class="muted">최근 한경컨센서스 리포트가 없습니다.</p>'
    head = (f'<p>최근 6개월 증권사별 마지막 목표가의 평균 <b>{won(tgt)}원</b> ({n_br}곳) {upside(tgt, close)}</p>'
            if tgt else "")
    trs = "".join(
        f'<tr><td>{escape(r["date"])}</td><td>{escape(r["broker"] or "")}</td>'
        f'<td>{won(r["target"]) if r["target"] else "-"}</td><td>{escape(r["opinion"] or "")}</td>'
        f'<td>{title_link(r)}</td></tr>' for r in rows)
    return (f'<h2>증권사 목표주가 <span class="muted">한경컨센서스</span></h2>{head}'
            f'<div class="scroll"><table><tr><th>날짜</th><th>증권사</th><th>목표가</th><th>의견</th><th>제목</th></tr>'
            f'{trs}</table></div>')


# ---------------- 아이디어 ----------------
IDEA_TABS = {"cheap": "기관 평단보다 싼 종목", "cluster": "여러 기관이 함께 산 종목"}
WINDOWS = {"30": "최근 1달 매수", "90": "최근 3달 매수", "180": "최근 6달 매수", "365": "최근 1년 매수"}
NPS_OPTS = {"1": "국민연금 제외", "0": "국민연금 포함"}


def latest_closes(conn):
    """종목코드 → (날짜, 최근 종가)"""
    return {r[0]: (r[2], r[1]) for r in conn.execute("SELECT code, close, MAX(date) FROM prices GROUP BY code")}


def tgt_line(conn, code, close):
    tgt, n = consensus(conn, code)
    return f'<div>증권사 목표가 평균 <b>{won(tgt)}원</b> ({n}곳) {upside(tgt, close)}</div>' if tgt else ""


def view_ideas(conn, q):
    if not has_table(conn, "summary") or not has_table(conn, "prices"):
        return page("아이디어", '<p class="muted">다음 수집이 끝나면 볼 수 있습니다. (주가 데이터 필요)</p>')
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
    if tab == "cheap":
        ideas = sorted((i for i in ideas if i["disc"] < 0), key=lambda i: i["disc"])
        chart = bar_list([(i["r"]["name"], f'/stock?code={quote(i["r"]["code"])}', i["disc"] * 100) for i in ideas[:12]],
                         lambda v: f"{v:+.1f}%")
        chart_title = "최근 종가가 기관 평균매수단가보다 몇 % 낮은지"
    else:
        ideas = sorted((i for i in ideas if i["r"]["n"] >= 2), key=lambda i: (-i["r"]["n"], -i["r"]["amt"]))
        chart = bar_list([(i["r"]["name"], f'/stock?code={quote(i["r"]["code"])}', i["r"]["amt"]) for i in ideas[:12]],
                         lambda v: eok(v))
        chart_title = "함께 산 기관들의 매수금액 합계"

    base = {"w": w, "ex": ex, **({"m": m} if m else {})}
    tabs = " ".join(f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == tab
                    else f'<a class="chip" href="/ideas?{urlencode({**base, "t": k})}">{v}</a>' for k, v in IDEA_TABS.items())
    form = (f'<form method="get" class="filters" style="margin-top:10px"><input type="hidden" name="t" value="{tab}">'
            f'{select("w", WINDOWS, w)}' + (select("m", MARKET_OPTS, m) if "시장" in cols else "")
            + f'{select("ex", NPS_OPTS, ex)}<button>적용</button></form>')
    note = ('<p class="muted">살펴볼 후보를 찾는 화면입니다. 공시는 매매 후 최대 5영업일 뒤에 나오고, '
            '국민연금처럼 큰 기관은 비중 조절로 사고팔 때가 많아 기본으로 뺐습니다.</p>')
    if not ideas:
        return page("아이디어", tabs + form + note + '<p class="muted">조건에 맞는 종목이 없습니다.</p>')
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
            f'{gap(i["close"], i["avg"])}</div>{tgt_line(conn, r["code"], i["close"])}{sparkline(pr, i["avg"])}</div>')
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
def make_handler(db_path, status_path, run_cmd, child_env=None, get_key=None, save_key=None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, ctype, data, headers=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            for k, v in headers:
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
            st = load_status(status_path)
            if url.path == "/health":
                return self._send(200, "application/json", json.dumps(st, ensure_ascii=False).encode())
            conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True) if Path(db_path).exists() else sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            try:
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
                    body = view_home(conn, q, st, need_key=bool(get_key) and not get_key())
                elif url.path == "/settings":
                    body = view_settings(get_key, q)
                elif url.path == "/ideas":
                    body = view_ideas(conn, q)
                elif url.path == "/new":
                    body = view_new(conn, q)
                elif url.path == "/stock":
                    body = view_stock(conn, q.get("code", ""))
                else:
                    body = None
            finally:
                conn.close()
            if body is None:
                return self._send(404, "text/html; charset=utf-8",
                                  page("없는 페이지", '<p>찾는 페이지나 종목이 없습니다.</p><p><a href="/">요약으로</a></p>'))
            self._send(200, "text/html; charset=utf-8", body)

        def _redirect(self, where):
            self.send_response(303)
            self.send_header("Location", where)
            self.end_headers()

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
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
            st = load_status(status_path)
            running = st.get("state") == "running" and time.time() - st.get("updated", 0) < 120
            if not running and run_cmd:
                subprocess.Popen(run_cmd, start_new_session=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, env=child_env)
                time.sleep(1.5)   # 상태 파일이 생길 시간
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *args):  # 조용히
            pass

    return Handler


def serve(db_path, host="127.0.0.1", port=8766, status_path=None, child_env=None, get_key=None, save_key=None):
    db_path = Path(db_path)
    status_path = status_path or db_path.parent / "status.json"
    run_cmd = [sys.executable, str(Path(__file__).resolve().parent / "dart_server.py"), "run"]
    server = ThreadingHTTPServer((host, port), make_handler(db_path, status_path, run_cmd, child_env, get_key, save_key))
    print(f"대시보드: http://{host}:{port}  (끄려면 Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
