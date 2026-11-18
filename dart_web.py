# -*- coding: utf-8 -*-
"""
DART 단순투자 대시보드 (폰용, 파이썬 기본 기능만 사용)

    python dart_server.py web     → http://127.0.0.1:8766
서버에서는 Tailscale(serve)로만 공개해서 내 기기에서만 열립니다.
"""
import os, re, sys, json, time, sqlite3, subprocess, threading, datetime as dt
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from dart_charts import CHART_CSS, CHART_JS, bar_list, line_chart, net_line, price_chart, sparkline
import position_flow as pf
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
.runbar{display:flex;gap:8px;align-items:center;margin-left:auto}.runbar .runbtn{margin-left:0}
.runbtn.alt{background:var(--chip);color:var(--fg)}.runmenu .dot2{color:var(--up)}
form.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
textarea{font:inherit;width:100%;margin-top:6px;padding:7px 9px;border:1px solid var(--line);border-radius:8px;
background:var(--card);color:var(--fg)}
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


_NAV = {"realestate": "", "links": [], "status": {}, "has_update": False}   # 요청마다 설정·진행 상황으로 새로 채움


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


def _app_version():
    try:
        from version import __version__
        return __version__
    except Exception:
        return ""


def update_widget():
    """모든 화면 위쪽: '업데이트' 메뉴. 화면을 떠나지 않고 새 버전 확인·업데이트 결과를 메뉴 안에서 바로 보여 줌.
    메뉴를 열었을 때 마지막 확인이 10분 넘었으면 자동으로 한 번 확인."""
    if not _NAV.get("has_update"):
        return ""
    chk = {}
    try:
        chk = json.loads((Path(_DATA_DIR["path"] or ".") / "update_check.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    behind = int(chk.get("behind") or 0) if isinstance(chk, dict) else 0
    dot = ' <span class="dot2" id="updot">●</span>' if behind else ' <span class="dot2" id="updot" style="display:none">●</span>'
    if chk.get("error"):
        st = f'확인 실패: {chk["error"]}'
    elif behind:
        st = f'새 버전이 있습니다 ({behind}개 변경, {chk.get("checked", "")} 확인)'
    elif "behind" in chk:
        st = f'최신 버전입니다 ({chk.get("checked", "")} 확인)'
    else:
        st = "아직 확인하지 않았어요"
    items = "".join(f"<li>{escape(x)}</li>" for x in (chk.get("items") or [])[:4]) if behind else ""
    try:
        age = max(0, int(time.time() - time.mktime(time.strptime(chk.get("checked", ""), "%Y-%m-%d %H:%M"))))
    except (ValueError, TypeError, OverflowError):
        age = 10 ** 9
    ver = _app_version()
    return (f'<details class="runmenu" id="updmenu" data-age="{age}"><summary class="runbtn alt">업데이트{dot} ▾</summary>'
            '<form method="post" class="menu" onsubmit="return false">'
            f'<div class="muted" style="padding:2px 8px;font-size:12px">지금 앱 v{escape(ver)}</div>'
            f'<div id="upstat" style="padding:2px 8px;font-size:13px">{escape(st)}</div>'
            f'<ul id="upitems" class="muted" style="margin:0;padding:0 8px 0 24px;font-size:12px">{items}</ul>'
            '<button type="submit" formaction="/settings/update-check" onclick="return dartUpd(\'check\')">새 버전 확인</button>'
            '<button type="submit" formaction="/settings/update-run" onclick="return dartUpd(\'run\')">지금 업데이트</button>'
            '</form></details>'
            "<script>function dartUpd(k){"
            "if(k==='run'&&!confirm('지금 업데이트할까요? 1~3분 걸리고 화면이 잠깐 끊길 수 있습니다.'))return false;"
            "var s=document.getElementById('upstat'),l=document.getElementById('upitems'),d=document.getElementById('updot');"
            "if(!s)return true;s.textContent=k==='check'?'확인 중…':'시작하는 중…';"
            "fetch('/settings/update-'+k+'?ajax=1',{method:'POST'}).then(function(r){return r.json()}).then(function(j){"
            "s.textContent=j.msg;l.innerHTML='';(j.items||[]).slice(0,4).forEach(function(t){var li=document.createElement('li');li.textContent=t;l.appendChild(li)});"
            "if(d)d.style.display=j.behind?'':'none';}).catch(function(){s.textContent='확인하지 못했어요 (연결을 확인해 주세요)'});return false}"
            "(function(){var m=document.getElementById('updmenu');if(!m)return;m.addEventListener('toggle',function(){"
            "if(m.open&&+m.dataset.age>600&&!m.dataset.done){m.dataset.done=1;dartUpd('check')}})})();</script>")


def header_actions():
    return f'<div class="runbar">{update_widget()}{run_widget()}</div>'


_CTX = threading.local()      # 요청마다 따로: 화면 맨 위에 붙일 탭 줄 (아이디어·시장 묶음)


def page(title, body, refresh=False, charts=False):
    body = getattr(_CTX, "pre", "") + body
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
<header><div class="hrow"><b>DART 단순투자</b>{header_actions()}</div><nav><a href="/">보고서</a><a href="/ideas">아이디어</a><a href="/market">시장</a>
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


def krx_key_card(S, msg="", bad=False):
    """설정 → KRX Open API 키 (공매도·시세 같은 KRX 데이터용). 키는 앞뒤 4자리만 보여 줌."""
    if not S or "save_krx_key" not in S:
        return ""
    cur = S["krx_key"]() if "krx_key" in S else ""
    now = (f'<div class="muted">지금 키: {escape(cur[:4])}…{escape(cur[-4:])} (저장됨)</div>' if cur
           else '<div class="muted">아직 저장된 키가 없어요.</div>')
    note = f'<div class="{"warn" if bad else ""}" style="margin-top:6px">{escape(msg)}</div>' if msg else ""
    return ('<div class="card"><b>KRX Open API 키</b>' + now +
            '<div class="muted">openapi.krx.co.kr → 마이페이지 → API 인증키 신청에서 받은 키를 붙여넣으세요. '
            '키 승인과는 별도로 <b>서비스 이용신청</b>에서 쓸 서비스를 신청하고 승인받아야 그 서비스를 부를 수 있어요.</div>'
            '<form method="post" action="/settings/krx-key" class="filters" style="margin:8px 0 0">'
            '<input type="password" name="key" autocomplete="off" autocapitalize="off" spellcheck="false" '
            'placeholder="KRX 인증키" style="flex:1 1 220px"><button>저장</button></form>' + note + '</div>')


def ext_check_card(S):
    if not S or "ext_check" not in S:
        return ""
    krx_ok = bool(S["krx_key"]()) if "krx_key" in S else False
    dg_ok = bool(S["datagokr_key"]()) if "datagokr_key" in S else False
    return ('<div class="card"><b>외부 데이터 연결 확인</b>'
            '<div class="muted">KRX 시세(시가총액·상장주식수)와 주식 대차거래(대차잔고)가 서버에서 실제로 받아지는지 확인해요. '
            '받은 응답의 필드 이름도 그대로 보여 주니, 이상하면 이 화면을 캡처해서 보내 주세요.</div>'
            '<form method="post" action="/settings/ext-check" class="filters" style="margin:8px 0 0">'
            f'<button name="src" value="krx"{"" if krx_ok else " disabled"}>KRX 확인</button>'
            f'<button name="src" value="lending"{"" if dg_ok else " disabled"}>대차거래 확인</button>'
            '<button name="src" value="bonds">DART 채권·증자 확인</button>'
            '<button name="src" value="news">속보(뉴스) 확인</button>'
            f'<button name="src" value="etf"{"" if krx_ok else " disabled"}>ETF 거래대금 확인</button>'
            '<button name="src" value="ecos">회사채 금리(ECOS) 확인</button>'
            '<button name="src" value="policy">정책·법안 확인</button>'
            '<button name="src" value="bills">법안·종목 확인</button></form>'
            + ('' if krx_ok else '<div class="muted">KRX 키를 먼저 저장해 주세요.</div>')
            + ('' if dg_ok else '<div class="muted">공공데이터포털 키(DATA_GO_KR_KEY)가 없어서 대차거래는 확인할 수 없어요.</div>') + '</div>')


def view_ext_check(res):
    """연결 확인 결과 화면"""
    back = '<p><a href="/settings">← 설정으로</a></p>'
    if res.get("error"):
        return page("연결 확인", f'<div class="card warn">{escape(res["error"])}</div>' + back)

    def kv(d, limit=18):
        rows = "".join(f'<tr><td>{escape(str(k))}</td><td>{escape(str(v))}</td></tr>' for k, v in list(d.items())[:limit])
        return f'<div class="scroll"><table>{rows}</table></div>'
    cards = []
    for it in res["items"]:
        if res["src"] == "krx":
            title = f'{it["market"]} {it["label"]} <span class="muted">{escape(it["service"])}</span>'
            info = f'기준일 {escape(it["day"])} · {it["rows"]:,}줄' if it["ok"] else ""
            fields = ""
        else:
            title = f'<span class="muted">{escape(it["op"])}</span>'
            info = f'기준일 {escape(it["day"])} · 전체 {it["total"]:,}줄' if it["ok"] else ""
            f = it.get("fields")
            fields = ('<div style="margin-top:6px"><b style="font-size:13px">역할별로 찾은 필드</b>' + kv(
                {role: f"{key or '못 찾음'}" + (f" = {it['sample'].get(key)}" if key else "") for role, key in f.items()}) + "</div>") if f else ""
        mark = '<b style="color:var(--accent)">✔ 됩니다</b>' if it["ok"] else '<b class="warn">✖ 안 돼요</b>'
        sample = (f'<details style="margin-top:6px"><summary class="muted">받은 첫 줄 (필드 이름 확인용)</summary>{kv(it["sample"])}</details>'
                  if it.get("sample") else "")
        cards.append(f'<div class="card"><div class="row"><b>{title}</b>{mark}</div>'
                     + (f'<div class="muted">{info}</div>' if info else "")
                     + (f'<div class="warn" style="margin-top:4px">{escape(it["note"])}</div>' if it.get("note") else "")
                     + fields + sample + '</div>')
    head = {"krx": "KRX Open API 연결 확인", "lending": "주식 대차거래 연결 확인", "bonds": "DART 채권·증자 공시 연결 확인",
            "news": "해외 통신 속보 연결 확인", "etf": "ETF 거래대금 연결 확인", "ecos": "회사채 금리(ECOS) 연결 확인", "policy": "정책·법안 연결 확인", "bills": "법안·종목 연결 확인"}[res["src"]]
    return page(head, f'<h2>{head}</h2>' + "".join(cards)
                + '<p class="muted">✖가 있으면 이유를 적어 두었어요. 대차거래는 필드 이름을 이름 규칙으로 찾기 때문에, '
                  '\'역할별로 찾은 필드\'가 틀려 보이면 이 화면을 캡처해서 보내 주세요.</p>' + back)


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
        body += krx_key_card(S, q.get("kn", ""), q.get("kbad") == "1")
        body += ext_check_card(S)
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


def net_trend(conn, code):
    """종목 하나에서 제출인별 누적 순증감(주): 변동일 순으로 공시의 증감을 쌓은 값."""
    try:
        rows = conn.execute(
            "SELECT 제출인, 변동일, 증감 FROM detail WHERE 종목코드=? AND 증감 IS NOT NULL AND 변동일 IS NOT NULL "
            "ORDER BY 변동일", (code,)).fetchall()
    except sqlite3.OperationalError:
        return []
    per = {}
    for who, day, d in rows:
        try:
            d = float(d)
        except (TypeError, ValueError):
            continue
        a = per.setdefault(who or "(이름 없음)", {"days": {}, "buy": 0.0, "sell": 0.0})
        a["days"][day] = a["days"].get(day, 0.0) + d
        if d > 0:
            a["buy"] += d
        else:
            a["sell"] -= d
    out = []
    for who, a in per.items():
        cum, series = 0.0, []
        for day in sorted(a["days"]):
            cum += a["days"][day]
            series.append((day, cum))
        out.append(dict(who=who, total=cum, buy=a["buy"], sell=a["sell"],
                        first=series[0][0], last=series[-1][0], series=series))
    # 공시 요약표에서 읽은 보유비율 (분석 형식 6 이상으로 다시 분석한 공시만 있음)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(detail)")}
        ratio_rows = conn.execute(
            "SELECT 제출인, 제출일, MAX(직전보유비율), MAX(이번보유비율), MAX(이번보유수량) FROM detail "
            "WHERE 종목코드=? AND 이번보유비율 IS NOT NULL GROUP BY 제출인, 접수번호 ORDER BY 제출일, 접수번호",
            (code,)).fetchall() if "이번보유비율" in cols else []
    except sqlite3.OperationalError:
        ratio_rows = []
    by_who = {}
    for who, sub, pp, np_, nq in ratio_rows:
        by_who.setdefault(who or "(이름 없음)", []).append((sub, pp, np_, nq))
    for r in out:
        hist = by_who.get(r["who"])
        if hist:
            r["ratios"] = [h[2] for h in hist]
            r["hold"] = dict(date=hist[-1][0], prev=hist[-1][1], now=hist[-1][2], qty=hist[-1][3])
    out.sort(key=lambda r: (-abs(r["total"]), r["who"]))
    return out


def _stake(r, k, pos):
    """한 제출인의 '전체 주식 중 몇 %'. → dict(pct, qty, shrs, src, reported, diff, est) 또는 None
    우선순위: ① 보고서 보유수량 ÷ KRX 상장주식수 ② 공시에 적힌 보유비율 ③ 공시 증감 합(추정) ÷ 상장주식수"""
    shrs = k["list_shrs"] if k and k.get("list_shrs") else None
    h = r.get("hold")
    reported = h["now"] if h and h.get("now") is not None else None
    if h and h.get("qty") and shrs:
        pct = h["qty"] / shrs * 100
        return dict(pct=pct, qty=h["qty"], shrs=shrs, src="보고서 보유수량 ÷ 상장주식수", reported=reported,
                    diff=(pct - reported) if reported is not None else None, est=False)
    if reported is not None:
        return dict(pct=reported, qty=h.get("qty"), shrs=shrs, src="공시 요약표에 적힌 보유비율", reported=reported, diff=None, est=False)
    if pos and shrs and pos["remain"]:
        return dict(pct=pos["remain"] / shrs * 100, qty=pos["remain"], shrs=shrs, src="공시 증감 합(추정) ÷ 상장주식수",
                    reported=None, diff=None, est=True)
    return None


def _stake_html(r, st, h):
    """카드 맨 위 지분율 줄: 전체 주식의 X% + 계산 근거, 공시 요약표 비율 비교, 직전 대비 변화"""
    if not st:
        return ""
    qty = f'{won(st["qty"])}주' if st["qty"] else ""
    base = (f'{qty} ÷ 상장주식수 {won(st["shrs"])}주' if st["shrs"] and st["qty"] else escape(st["src"]))
    head = (f'<div style="margin-top:6px">전체 주식의 {"약 " if st["est"] else ""}<b style="font-size:17px">{st["pct"]:.2f}%</b>'
            f'{" <span class=chip>추정</span>" if st["est"] else ""} <span class="muted">({base})</span></div>')
    lines = []
    if st["reported"] is not None and st["src"].startswith("보고서"):
        if abs(st["diff"]) <= 0.3:
            lines.append(f'공시 요약표 비율 {st["reported"]:.2f}% <b style="color:var(--accent)">✔ 계산과 맞아요</b>')
        else:
            lines.append(f'공시 요약표 비율 {st["reported"]:.2f}% <span class="warn">⚠ 계산과 {st["diff"]:+.2f}%p 차이 '
                         '(증자·소각으로 주식수가 바뀌었거나 읽은 값이 틀렸을 수 있어요)</span>')
    elif st["reported"] is not None:
        lines.append(f'공시 요약표에 적힌 비율이에요 (상장주식수를 받으면 직접 계산해서 비교해요)')
    if st["est"]:
        lines.append('공시에 잡힌 증감을 쌓은 추정이에요. 처음 공시 이전 보유분은 빠져 있어서 실제와 다를 수 있어요 '
                     '(다시 분석한 공시에는 보고서 보유수량으로 계산해요)')
    if h and h.get("prev") is not None and h.get("now") is not None:
        d = h["now"] - h["prev"]
        lines.append(f'직전 보고서 {h["prev"]:.2f}% → <span class="{"up" if d > 0 else "down" if d < 0 else ""}">{d:+.2f}%p</span>'
                     + f' · {escape(str(h["date"]))} 공시')
    elif h and h.get("date"):
        lines.append(f'신규 보고 · {escape(str(h["date"]))} 공시')
    return head + "".join(f'<div class="muted" style="font-size:12px">{x}</div>' for x in lines)


def trend_block(conn, code, show=8):
    """종목 화면: 제출인별로 지분을 늘렸는지 줄였는지 (공시에 나온 증감의 누적)."""
    items = net_trend(conn, code)
    if not items:
        return ""
    krx_k = _ext_data(conn)[0].get(code)
    POS = pf.compute(conn)
    stakes = {r["who"]: _stake(r, krx_k, POS.get((r["who"], code))) for r in items}
    firm = [st["pct"] for st in stakes.values() if st and not st["est"]]       # 보고서·공시 비율로 확인된 값만
    up = sum(1 for r in items if r["total"] > 0)
    down = sum(1 for r in items if r["total"] < 0)
    flat = len(items) - up - down

    def card(r):
        cls = "up" if r["total"] > 0 else "down" if r["total"] < 0 else ""
        arrow = "▲ 늘림" if r["total"] > 0 else "▼ 줄임" if r["total"] < 0 else "변동 없음"
        h = r.get("hold")
        hold = _stake_html(r, stakes.get(r["who"]), h) + (sparkline(r["ratios"]) if h and len(r.get("ratios", [])) >= 2 else "")
        return (f'<div class="card"><div class="row"><div><b>{escape(r["who"])}</b></div>'
                f'<div class="{cls}"><b>{r["total"]:+,.0f}주</b> {arrow}</div></div>{hold}'
                f'<div class="muted">매수 {r["buy"]:,.0f}주 · 매도 {r["sell"]:,.0f}주 · '
                f'{escape(r["first"])} ~ {escape(r["last"])}</div>{net_line(r["series"])}</div>')

    head_cards = "".join(card(r) for r in items[:show])
    rest = items[show:]
    more = (f'<details><summary class="muted">나머지 {len(rest)}곳 더 보기</summary>'
            + "".join(card(r) for r in rest) + "</details>") if rest else ""
    return ('<h2>지분 변화 추이</h2>'
            f'<div class="muted" style="margin-bottom:6px">늘린 곳 <b class="up">{up}</b> · 줄인 곳 <b class="down">{down}</b>'
            + (f' · 변동 없음 {flat}' if flat else "") + '</div>'
            + (f'<div style="margin-bottom:6px">공시로 확인된 보유 합계 <b>약 {sum(firm):.2f}%</b> '
               f'<span class="muted">({len(firm)}곳 · 함께 보고한 지분은 겹칠 수 있어요)</span></div>' if len(firm) >= 2 else "")
            + head_cards + more
            + '<p class="muted">아래 추이 선과 순증감은 공시에 나온 증감(주)을 변동일 순서로 쌓은 값이에요. '
              '<b>이 기간 공시에 잡힌 변화량의 합</b>이고, 처음 공시 이전 보유분은 들어 있지 않아요. '
              '같은 거래가 정정 공시로 다시 올라오면 겹쳐 셀 수 있어요. '
              '보유비율은 공시 요약표에 적힌 값이고, 다시 분석한 공시에만 나와요.</p>')


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


DERIV_LEARN = [
    ("선물이 뭐예요?", "정해진 날(만기)에 정해진 가격으로 사고팔기로 미리 약속하는 거래예요. 코스피200 선물은 '코스피200 지수'를 사고파는 약속이라, 주식을 직접 안 사도 시장 전체가 오르내리는 쪽에 베팅하거나 내 주식이 떨어질 때를 대비(헤지)할 수 있어요. 적은 돈(증거금)으로 큰 금액을 거래해서 손익이 크게 커져요."),
    ("옵션은 뭐예요?", "정해진 날에 정해진 가격으로 '살 권리(콜)' 또는 '팔 권리(풋)'를 사고파는 거래예요. 권리를 산 사람은 손해가 낸 돈(프리미엄)까지로 제한되고, 권리를 판 사람은 손실이 훨씬 커질 수 있어요. 개인이 옵션을 팔다가 크게 잃는 경우가 많아서 초보에게 가장 위험한 쪽으로 알려져 있어요."),
    ("베이시스는 뭐예요?", "선물 가격 − 현물(지수) 가격이에요. 선물이 비싸면(플러스) 시장이 상승을 기대하는 쪽, 싸면(마이너스) 하락에 대비하는 쪽으로 읽어요. 만기가 다가오면 0에 가까워지고, 배당·금리 영향도 있어서 숫자 하나보다 '변하는 방향'이 더 중요해요."),
    ("외국인 선물 순매수는 왜 봐요?", "외국인은 현물보다 선물을 먼저, 크게 움직이는 경우가 많다고 해요. 그래서 외국인이 선물을 며칠 계속 사면 상승 쪽, 계속 팔면 하락 쪽 신호로 참고해요. 다만 선물을 팔면서 현물을 사는 차익거래·헤지일 수도 있어서, 현물 순매수와 같이 봐야 해요 (이 화면에도 같이 나와요)."),
    ("만기일·쿼드러플 위칭은 뭐예요?", "코스피200 선물·옵션은 매월 둘째 목요일이 만기예요. 3·6·9·12월은 지수 선물·옵션과 개별주식 선물·옵션이 한꺼번에 만기라서 '쿼드러플 위칭'이라고 불러요. 만기 직전에는 프로그램 매매가 몰려 장 막판에 주가가 크게 흔들릴 수 있어요. 달력에 자동으로 표시해 둬요 (공휴일이면 앞당겨질 수 있어요)."),
    ("채권 가격과 금리는 반대예요", "이미 발행된 채권은 금리가 오르면 값이 내려가고, 금리가 내리면 값이 올라가요. 새 채권이 더 높은 금리를 주면 예전 채권은 덜 매력적이라서 그래요. 그래서 '금리 상승 = 채권 값 하락'이에요."),
    ("국고채 금리는 주식에 왜 중요해요?", "국고채는 가장 안전한 투자의 기준 금리예요. 안전한 채권이 높은 이자를 주면 위험한 주식에 돈을 둘 이유가 줄어요(주식 가치 평가에도 더 큰 할인율을 적용해요). 특히 먼 미래에 돈을 버는 성장주가 금리 상승에 약하다고 알려져 있어요."),
    ("장단기 금리차가 마이너스(역전)면?", "보통은 오래 빌려줄수록(10년) 이자를 더 받아서 10년 금리가 3년보다 높아요. 반대로 10년이 더 낮으면 '앞으로 경기가 나빠져서 금리가 내려갈 것'이라고 시장이 본다는 뜻이라, 경기 침체 신호로 자주 인용돼요. 틀릴 때도 많아요."),
    ("한미 금리차는요?", "미국 금리가 한국보다 많이 높으면 달러 자산을 가진 쪽이 유리해서, 원화가 약해지고 외국인이 한국 자산을 팔 압력이 생길 수 있다고 해요(가설). 환율·외국인 순매수와 같이 보세요."),
    ("풋/콜 비율·내재변동성·미결제약정은요?", "풋/콜 비율은 하락에 거는 권리(풋) 거래량 ÷ 상승에 거는 권리(콜) 거래량이에요. 1보다 크면 하락 대비 심리가 큰 거예요. 내재변동성은 옵션 가격에 담긴 '앞으로 얼마나 흔들릴 것 같은지'이고 높을수록 불안해요(거래소 VKOSPI와 같은 방향 지표). 미결제약정은 아직 청산 안 된 선물 계약 수라서, 가격과 같이 움직이면 새 돈이 들어왔다는 뜻일 수 있어요. 회사채 신용 스프레드(회사채 AA- 3년 − 국고채 3년 금리)는 위기감이 퍼질 때 벌어지고, 한국은행 ECOS 자료로 계산해요."),
]


def deriv_spark(points):
    return sparkline(points[-120:]) if points and len(points) >= 2 else ""


def view_derivs(conn, q):
    import deriv_bond as dvb
    tab = "learn" if q.get("t") == "learn" else "signals"
    tabs = '<div style="margin:0 0 8px">' + " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == tab else
         f'<a class="chip" href="/derivs{("?t=" + k) if k != "signals" else ""}">{v}</a>')
        for k, v in (("signals", "신호판"), ("learn", "배우기 (쉬운 설명)"))) + '</div>'
    if tab == "learn":
        body = ('<div class="card"><b>선물·옵션·채권, 주식 투자자가 알아야 할 것</b><div class="muted" style="margin-top:4px">'
                '용어를 쉬운 말로 풀었어요. 직접 거래하라는 뜻이 아니에요 — 선물·옵션은 원금 이상 잃을 수 있어서 이 화면은 시장 분위기를 읽는 용도예요.</div></div>'
                + "".join(f'<details class="card"><summary><b>{escape(t)}</b></summary><div style="margin-top:6px">{escape(x)}</div></details>' for t, x in DERIV_LEARN))
        return page("선물·채권 배우기", tabs + body)
    sigs = dvb.signals(conn)
    tot, n, text = dvb.regime(sigs)
    cards = ""
    for sg in sigs:
        chip = ("" if sg["signal"] is None else
                f'<span class="chip {"up" if sg["signal"] > 0 else "down" if sg["signal"] < 0 else ""}">{"우호" if sg["signal"] > 0 else "부담" if sg["signal"] < 0 else "중립"}</span>')
        if not sg["value"] and sg["note"]:
            cards += f'<div class="card"><b>{escape(sg["label"])}</b><div class="muted" style="margin-top:4px">{escape(sg["note"])}</div></div>'
            continue
        cards += (f'<div class="card"><div class="row"><b>{escape(sg["label"])}</b><div><b class="big">{escape(sg["value"])}</b></div></div>'
                  f'<div style="margin:4px 0">{chip} <span class="muted">{escape(sg["change"])}</span></div>'
                  + (f'<div>{escape(sg["reason"])}</div>' if sg["reason"] else "") + deriv_spark(sg["points"])
                  + (f'<div class="muted" style="font-size:12px;margin-top:4px">{escape(sg["note"])}</div>' if sg["note"] else "")
                  + (f'<div class="muted" style="font-size:12px">기준 {escape(sg.get("date", ""))}</div>' if sg.get("date") else "") + '</div>')
    ex = dvb.expiries(dt.date.today(), 3)
    exp = ('<div class="card"><b>다음 선물·옵션 만기일</b>' + "".join(
        f'<div style="margin-top:4px">{d.isoformat()} <b>D-{(d - dt.date.today()).days}</b>'
        + (' <span class="chip warn">쿼드러플 위칭</span>' if quad else "") + '</div>' for d, quad in ex)
        + '<div class="muted" style="font-size:12px;margin-top:4px">매월 둘째 목요일 (공휴일이면 앞당겨질 수 있어요, 확인 필요). 달력에도 들어가요.</div></div>')
    have_krx = any(sg["id"] in ("put_call", "iv", "oi") for sg in sigs)
    pending = '' if have_krx else ('<div class="card"><b>아직 못 받는 지표</b><div class="muted" style="margin-top:4px">'
               + ('옵션 풋/콜 비율 · 내재변동성 · 미결제약정 · 금 가격은 KRX 키를 저장하고 <b>지금 수집 → 시장·정책만</b>을 한 번 돌리면 들어와요 (설정 → KRX 확인에서 서비스가 되는지 볼 수 있어요).')
               + '</div></div>')
    try:
        import krx_deriv
        cdate, curve = krx_deriv.curve_latest(conn)
        sdate, stop = krx_deriv.stock_top(conn, 10)
    except Exception:
        cdate, curve, sdate, stop = None, [], None, []
    curve_card = ""
    if curve:
        rows = "".join(f'<tr><td>{escape(t)}</td><td>{y:.3f}%</td><td>{("%+.2f%%p" % (y - o)) if o is not None else "-"}</td></tr>' for t, y, o in curve)
        curve_card = ('<div class="card"><b>국채 수익률 곡선 (KRX 국채전문유통시장)</b><div class="muted">기준 ' + escape(cdate)
                      + '</div><div class="scroll"><table><tr><th>만기</th><th>수익률</th><th>3개월 전 대비</th></tr>' + rows + '</table></div>'
                      '<div class="muted" style="font-size:12px;margin-top:4px">만기가 길수록 높으면 정상, 짧은 쪽이 더 높으면(역전) 경기 둔화 신호로 읽어요 (가설).</div></div>')
    stock_card = ""
    if stop:
        rows = "".join(f'<tr><td>{"선물" if k == "F" else "옵션"}</td><td>{escape(p)}</td><td>{v:,.0f}</td><td>{oi:,.0f}</td></tr>' for k, p, v, oi in stop)
        stock_card = ('<div class="card"><b>단일종목 선물·옵션 거래 많은 상품</b><div class="muted">기준 ' + escape(sdate)
                      + '</div><div class="scroll"><table><tr><th>종류</th><th>상품</th><th>거래량</th><th>미결제</th></tr>' + rows + '</table></div>'
                      '<div class="muted" style="font-size:12px;margin-top:4px">거래가 몰리는 종목은 투기·헤지 수요가 크다는 뜻이라 변동성이 커질 수 있어요 (참고용).</div></div>')
    head = (f'<div class="card"><b>선물·채권 신호판</b><div style="margin-top:4px">지금 신호: <b>{text}</b> '
            f'<span class="muted">(신호 {n}개 합 {tot:+d})</span></div>'
            '<div class="muted" style="margin-top:6px"><b>주의:</b> 신호는 \'가설\'이에요. 선물·채권 숫자가 주식의 앞날을 보장하지 않고, 기준값은 제가 정한 단순한 값이에요. 투자 추천이 아니에요. '
            '용어가 낯설면 위쪽 <a href="/derivs?t=learn">배우기</a>를 먼저 보세요.</div></div>')
    return page("선물·채권", tabs + head + cards + curve_card + stock_card + exp + pending)


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
    body = (head + value_line(value_map(conn).get(code)) + stock_deriv_line(conn, name) + cap_rank_line(conn, code) + legis_block(conn, code) + bonds_block(conn, code) + stock_scores_block(conn, code, name) + flow_block(conn, code) + ext_block(conn, code) + chart + reports
            + trend_block(conn, code) + group_bar + '<h2 id="sum">날짜별 평균 매수단가</h2>'
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


REPORT_PERIODS = {"7": "최근 1주", "30": "최근 1달", "90": "최근 3달", "180": "최근 6달", "365": "최근 1년"}
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
    # 보유 현황 표(보유주식등의 수·보유비율): 변동 내역 표가 아니어서 위에서는 빠지므로 원문 그대로 따로 보여 줌
    holds = []
    for tb in soup.find_all("table"):
        head = tb.get_text(" ")
        if "변동일" in head and "단가" in head:
            continue
        if not re.search(r"보유\s*비율|보유\s*주식\s*등의\s*수|지분율", head):
            continue
        grid = [row for row in de.expand_table(tb) if any(c.strip() for c in row)]
        if len(grid) < 2:                       # 제목만 있는 표는 건너뜀
            continue
        trs = "".join("<tr>" + "".join(f"<td>{escape(' '.join(c.split()))}</td>" for c in row) + "</tr>"
                      for row in grid[:40])
        holds.append(f'<div class="scroll"><table>{trs}</table></div>')
    hold_html = ('<h2>보유 현황 표 (원문 그대로)</h2><p class="muted">보유주식등의 수와 보유비율이 들어 있는 표입니다. '
                 '프로그램은 아직 이 표를 읽지 않습니다.</p>' + "".join(holds[:4])) if holds else ""
    saved = "".join(f'<tr><td>{escape(str(r["변동일"]))}</td><td>{escape(str(r["방법"]))}</td>'
                    f'<td>{won(r["증감"])}</td><td>{won(r["단가"])}</td></tr>' for r in stored)
    body = (f'<div class="row"><b>공시 {escape(rno)}</b><a href="{dart_link(rno)}">DART 원문 ↗</a></div>{note}'
            '<h2>원문 표와 읽은 값</h2><p class="muted">맨 오른쪽 칸이 프로그램이 읽은 증감·단가입니다. '
            '틀린 줄이 있으면 이 화면을 캡처해서 보내 주세요.</p>' + ("".join(blocks) or '<p class="muted">세부변동내역 표가 없습니다.</p>')
            + (f'<h2>화면에 쓰이는 값 (마지막 수집 기준)</h2><div class="scroll"><table><tr><th>변동일</th><th>방법</th>'
               f'<th>증감</th><th>단가</th></tr>{saved}</table></div>' if saved else "") + hold_html)
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
    return page("시장", hero + cycle_brief_card(conn) + "".join(cards) + presidents_card(conn))


def cycle_brief_card(conn):
    """시장 온도 화면: 투자 사이클 요약 한 줄과 바로가기 (자료가 부족하면 빈 문자열)"""
    try:
        import cycle_theory as cy
        ind = cy.indicators(conn)
        asm = cy.assess(ind)
        syn = cy.synthesize(ind, asm)
    except Exception:
        return ""
    sit = syn.get("situation")
    if not sit:
        return ""
    chips = " ".join(f'<span class="chip {"up" if k == "up" else "down" if k == "down" else ""}">{cy.TONE_LABEL[k]} {len(v)}</span>' for k, v in syn["groups"].items() if v)
    return ('<div class="card"><div class="row"><b>투자 사이클 — 여러 이론에 비춰 본 지금</b><a class="chip" href="/cycle">자세히 →</a></div>'
            f'<div style="margin:6px 0"><span class="chip" style="font-weight:700">{escape(sit["label"])}</span> {chips}</div>'
            f'<div class="muted" style="font-size:12px">{escape(sit["desc"])} 이론 8개가 같은 가격·금리를 보는 후보 판단이라 판정이 아니에요.</div></div>')


def presidents_card(conn):
    """정권(대통령 재임기)별 코스피: 취임 직전 종가 → 재임 마지막 날 종가 (앱이 받아 둔 코스피 일별 종가로 계산)"""
    try:
        import presidents
        import stock_market
        rows = presidents.analyze(list(stock_market.series(conn, "idx:KOSPI")), ref_points=list(stock_market.series(conn, "idx:SPX")))
    except Exception:
        return ""
    if not rows:
        return ""
    trs = ""
    for r in rows:
        nm = escape(r["name"]) + (f' <span class="muted">{escape(r["note"])}</span>' if r["note"] else "")
        trs += (f'<tr><td>{nm}{" *" if r["partial"] else ""}</td><td>{r["start"]:%Y-%m-%d} → {r["end"]:%Y-%m-%d}<br><span class="muted">{r["years"]:.1f}년</span></td>'
                f'<td>{r["first"]:,.0f} → {r["last"]:,.0f}</td><td>{gap_pct(r["ret"])}</td>'
                f'<td>{"" if r["ann"] is None else gap_pct(r["ann"])}</td><td>{gap_pct(r["mdd"])}</td>'
                + (f'<td>{gap_pct(r["cmp"]["ref"])}<br><span class="muted">{r["cmp"]["start"]:%y-%m} 부터 코스피 {gap_pct(r["cmp"]["kospi"])}</span></td><td>{r["cmp"]["diff"] * 100:+.1f}%p</td>'
                   if r["cmp"] else '<td class="muted">-</td><td class="muted">-</td>') + '</tr>')
    return ('<div class="card"><b>정권별 코스피</b>'
            '<div class="muted">취임 직전 종가 → 재임 마지막 날(또는 오늘) 종가. 지수는 배당을 뺀 가격지수고, 세계 증시·금리·환율·반도체 경기의 영향이 훨씬 커서 '
            '정권이 만든 결과로 읽으면 안 돼요 (참고 자료). 권한대행 기간은 어느 쪽에도 넣지 않았어요. S&P500은 2010-07부터 기록이 있어서 그 이전 정권은 비교하지 못하고, 같은 구간으로 맞춰 코스피와 차이(%p)를 냈어요(원화 기준이 아닌 지수 점수 변화라 환율은 빠져 있어요). 최대 낙폭은 재임 중 고점 대비 가장 크게 빠진 폭이에요.</div>'
            '<div class="scroll"><table><tr><th>정권</th><th>기간</th><th>코스피</th><th>총 변화</th><th>연환산</th><th>최대 낙폭</th><th>S&amp;P500 같은 기간</th><th>코스피 − S&amp;P</th></tr>'
            f'{trs}</table></div>'
            + ('<div class="muted" style="font-size:12px">* 앱이 받아 둔 코스피 기록이 그 정권 중간에 시작해서, 기록의 첫날부터 계산했어요.</div>' if any(r["partial"] for r in rows) else "")
            + '</div>')


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


def view_news(conn, q):
    """시장 → 속보: 로이터·AP·블룸버그·연합 제목 (구글 뉴스 검색). 주제·언론사로 거르기"""
    import news
    topic = q.get("topic", "") if q.get("topic", "") in news.TOPICS else ""
    feeds = [f[0] for f in news.FEEDS]
    src = q.get("src", "") if q.get("src", "") in feeds else ""
    hours = {"6": 6, "24": 24, "72": 72}.get(q.get("h", ""), 24)
    rows = news.recent(conn, hours=hours, topic=topic, source=src)

    def link(label, **kw):
        cur = {"topic": topic, "src": src, "h": str(hours)}
        cur.update(kw)
        href = "/news?" + urlencode({k: v for k, v in cur.items() if v})
        on = all(cur.get(k) == v for k, v in kw.items())
        return (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{escape(label)}</b>' if on else f'<a class="chip" href="{href}">{escape(label)}</a>')

    filt = ('<div style="margin:6px 0">' + link("전체 주제", topic="") + " " + " ".join(link(t, topic=t) for t in news.TOPICS) + '</div>'
            '<div style="margin:6px 0">' + link("전체 언론사", src="") + " " + " ".join(link(f, src=f) for f in feeds) + " · "
            + " ".join(link(f"{h}시간", h=str(h)) for h in (6, 24, 72)) + '</div>')
    if not rows:
        body = ('<div class="card">아직 받은 기사가 없어요. 서버가 10분마다 확인해서 설정한 간격(기본 20분)마다 받아요. 설정에서 \'해외 통신 속보 제목 받기\'가 켜져 있는지 확인해 주세요.</div>')
    else:
        items = ""
        for title, src_name, lk, when, tl, br in rows:
            t = f'<a href="{escape(lk)}" target="_blank" rel="noopener noreferrer">{escape(title)}</a>' if lk else escape(title)
            items += (f'<div style="margin:8px 0">{"<span class=\"chip warn\">속보</span> " if br else ""}{t}'
                      f'<div class="muted" style="font-size:12px">{escape(src_name)} · {when} (한국 시간) '
                      + "".join(f'<span class="chip">{escape(x)}</span> ' for x in tl) + '</div></div>')
        body = f'<div class="card">{items}</div>'
    msg = q.get("msg", "")
    refresh = ('<form method="post" action="/news/refresh" class="filters" style="margin:6px 0"><button>지금 받기</button>'
               + (f' <span class="{"muted" if not msg.startswith("✖") else "warn"}">{escape(msg)}</span>' if msg else
                  ' <span class="muted">서버가 설정한 간격마다 자동으로 받지만, 바로 확인하고 싶을 때 눌러요</span>')
               + ' <a class="chip" href="/settings">연결 확인(설정)</a></form>')
    note = ('<div class="muted" style="font-size:12px">구글 뉴스 검색으로 제목·링크만 모았어요 (본문은 원문 사이트에서). 실시간이 아니라 몇 분~수십 분 늦을 수 있고, '
            '주제 표시는 제목 단어로 단순하게 나눈 거라 틀릴 수 있어요. 기사가 주가를 움직였다는 뜻이 아니에요.</div>')
    return page("속보", refresh + filt + body + note + news_impact_card(conn))


def news_impact_card(conn):
    """속보 화면 아래: 주제별로 기사가 있던 날과 없던 날의 코스피·S&P500 움직임 비교 (기사가 쌓인 기간 안에서)"""
    try:
        import news
        import news_impact as ni
        import stock_market
        rows = conn.execute("SELECT pub, topic, breaking FROM news_items").fetchall()
        rows = [(p, [x for x in (t or "").split(",") if x], b) for p, t, b in rows]
        mk = [("코스피", "KOSPI", list(stock_market.series(conn, "idx:KOSPI"))), ("S&P500", "SPX", list(stock_market.series(conn, "idx:SPX")))]
    except Exception:
        return ""
    head = ('<div class="card"><b>뉴스가 있던 날 vs 없던 날, 지수는 어땠나</b>'
            '<div class="muted">주제별로 그 기사가 나온 날의 지수 변화를 모아 기사가 없던 날과 비교해요. 일봉이라 기사가 원인인지 이미 난 가격 변화를 전한 건지는 가릴 수 없고(역인과), '
            f'같은 날 여러 주제가 겹쳐요. 기사가 있던 날이 {ni.MIN_DAYS}일 미만이면 숫자를 내지 않아요. 인과가 아니라 \'같이 크게 움직였나\'만 보는 참고 자료예요.</div>')
    body = ""
    order = list(news.TOPICS) + ["속보·긴급"]
    for label, key, pts in mk:
        res = ni.analyze(rows, pts, key)
        if not res:
            body += f'<div style="margin-top:8px"><b>{label}</b> <span class="muted">지수 기록이나 기사가 아직 부족해요</span></div>'
            continue
        lo, hi = res["기간"]
        base = res["전체"]
        trs = ""
        for tp in order:
            st = res["주제"].get(tp)
            if not st:
                continue
            if not st["enough"]:
                trs += f'<tr><td>{escape(tp)}</td><td colspan="4" class="muted">기사가 있던 날 {st["event"]["n"] if st["event"] else 0}일 — 더 쌓이면 보여요</td></tr>'
                continue
            e, r = st["event"], st["rest"]
            trs += (f'<tr><td>{escape(tp)}</td><td>{e["n"]}일</td><td>{e["mean_abs"]:.2f}% <span class="muted">(없던 날 {r["mean_abs"]:.2f}%)</span></td>'
                    f'<td>{gap_pct(e["mean"] / 100)} <span class="muted">({gap_pct(r["mean"] / 100)})</span></td><td>{e["big"]:.0f}% <span class="muted">({r["big"]:.0f}%)</span></td></tr>')
        body += (f'<div style="margin-top:10px"><b>{label}</b> <span class="muted">{lo} ~ {hi}, 거래일 {base["n"]}일 평균 움직임 {base["mean_abs"]:.2f}%</span>'
                 '<div class="scroll"><table><tr><th>주제</th><th>기사 있던 날</th><th>평균 |변화| (없던 날)</th><th>평균 변화 (없던 날)</th><th>±1% 넘은 날 (없던 날)</th></tr>'
                 f'{trs}</table></div></div>')
    return head + body + '</div>'


def industry_line(conn, a, b):
    """하락 점검: 기간 a→b 업종별 기여도 한 줄 (업종 매핑이 있을 때)"""
    import drop_diag as dd
    r = dd.by_industry(conn, a, b)
    if not r:
        return ""
    fmt = lambda xs: " · ".join(f"{escape(n)} {v:+.2f}" for n, v in xs) or "-"
    return (f'<div class="muted" style="font-size:13px">업종별 — 가장 끌어내린(%p): {fmt(r["down"])}</div>'
            f'<div class="muted" style="font-size:13px">업종별 — 가장 받쳐 준(%p): {fmt(r["up"][:3])}'
            + (f' · 업종 분류 없음 {r["unmapped"]:+.2f}' if abs(r["unmapped"]) >= 0.005 else "") + '</div>')


def history_card(conn):
    """하락 점검: 과거 코스피가 비슷하게 빠진 뒤의 기록 (KOSPI 일별 종가, 앱이 받아 둔 기간)"""
    try:
        import drawdown_history as dh
        import stock_market
        res = dh.analyze(list(stock_market.series(conn, "idx:KOSPI")))
    except Exception:
        return ""
    if not res:
        return ('<div class="card"><b>과거에 비슷하게 빠진 뒤엔?</b><div class="muted">코스피 기록이 아직 짧아요 (1년 반 이상 필요). 지금 수집 → 시장·정책만을 눌러 주세요.</div></div>')
    cur, lv, eps, sm = res["current"], res["level"], res["episodes"], res["summary"]
    near = "" if abs(cur) >= lv else f' <span class="muted">(지금 낙폭은 {cur:.1f}%라 가장 얕은 기준 -{lv}%로 봤어요)</span>'
    if not eps:
        return f'<div class="card"><b>과거에 비슷하게 빠진 뒤엔?</b><div class="muted">{res["first"]} 이후 -{lv}%를 넘은 사례가 없어요.</div></div>'
    trs = ""
    for e in eps:
        cells = "".join(f'<td>{gap_pct(e["fwd"][h] / 100) if h in e["fwd"] else "-"}</td>' for h, _ in dh.HORIZONS)
        trs += f'<tr><td>{e["date"]}</td><td>{e["dd"]:.1f}%</td>{cells}<td>{"-" if e["more"] is None else gap_pct(e["more"] / 100)}</td></tr>'
    med = "".join(f'<td><b>{"-" if sm[h]["median"] is None else gap_pct(sm[h]["median"] / 100)}</b><br><span class="muted">{sm[h]["up"]:.0f}% 상승</span></td>' for h, _ in dh.HORIZONS)
    more = sm["more"]
    return ('<div class="card"><b>과거에 비슷하게 빠진 뒤엔?</b>'
            f'<div class="muted">코스피가 최근 250거래일 고점 대비 -{lv}%를 처음 넘어선 날(회복 후 다시 빠진 경우만 새 사례)을 모아, 그날 이후 수익률과 6개월 안 추가 하락을 봤어요 ({res["first"]} ~ {res["last"]}).{near}</div>'
            f'<div class="scroll"><table><tr><th>그날</th><th>그날 낙폭</th>{"".join(f"<th>+{l}</th>" for _, l in dh.HORIZONS)}<th>6개월 안 추가 하락</th></tr>{trs}'
            f'<tr><td colspan="2"><b>중앙값</b></td>{med}<td><b>{"-" if more["median"] is None else gap_pct(more["median"] / 100)}</b></td></tr></table></div>'
            f'<div class="muted" style="font-size:12px;margin-top:4px">사례가 {len(eps)}개뿐이고 원인(금융위기·코로나·금리 충격 등)이 제각각이라 평균을 믿을 근거가 못 돼요. '
            '처음 넘어선 날 이후라서 단기 수익률이 낮게 나오기 쉬운 구조이기도 해요. 과거 기록일 뿐 앞으로를 보장하지 않고, 사고팔 시점을 알려주지 않아요.</div></div>')


def lights_card(conn):
    """하락 점검 맨 위: 원인별 신호등 (금리·반도체 쏠림·외국인·환율·신용). 지금 켜진 신호만 알려 주고 대처는 말하지 않음"""
    try:
        import drop_diag as dd
        ls = dd.lights(conn)
    except Exception:
        return ""
    on = [x for x in ls if x["state"] == "on"]
    known = [x for x in ls if x["state"] != "na"]
    cls = {"on": "down", "watch": "warn", "off": "up", "na": ""}
    rows = "".join(
        f'<div style="margin:8px 0"><div class="row"><b>{escape(x["name"])}</b><span class="chip {cls[x["state"]]}">{dd.STATE_TXT[x["state"]]}</span></div>'
        f'<div>{escape(x["evidence"])}</div><div class="muted" style="font-size:12px">기준: {escape(x["rule"])}</div></div>' for x in ls)
    head = (f'지금 켜진 신호 <b>{len(on)}개</b> / 확인 가능 {len(known)}개 ' + ("— " + " · ".join(escape(x["name"]) for x in on) if on else ""))
    hist = dd.history(conn, 30)
    if len(hist) >= 2:
        dots = {"on": ("#e5534b", "켜짐"), "watch": ("#d4a72c", "주의"), "off": ("#3fb950", "꺼짐"), "na": ("#8b949e", "자료 없음")}
        trs = ""
        for x in ls:
            cells = "".join(f'<span title="{d} {dots[st.get(x["id"], "na")][1]}" style="display:inline-block;width:8px;height:14px;margin-right:1px;'
                            f'background:{dots[st.get(x["id"], "na")][0]}"></span>' for d, st in hist)
            trs += f'<div style="margin:3px 0"><span style="display:inline-block;width:92px;font-size:13px">{escape(x["name"])}</span>{cells}</div>'
        hist_html = (f'<div style="margin-top:10px"><b style="font-size:13px">최근 {len(hist)}일 신호 기록</b> <span class="muted" style="font-size:12px">(빨강 켜짐 · 노랑 주의 · 초록 꺼짐 · 회색 자료 없음, 오른쪽이 최근)</span>{trs}</div>')
    else:
        hist_html = '<div class="muted" style="font-size:12px;margin-top:8px">신호 기록은 시장·정책 수집 때마다 하루 한 줄씩 쌓여요. 이틀 이상 쌓이면 여기에 보여요.</div>'
    return ('<div class="card"><b>원인 신호등</b>'
            f'<div style="margin-top:4px">{head}</div>'
            f'{rows}{hist_html}'
            '<div class="muted" style="font-size:12px;margin-top:6px">기준은 단순한 값이라 틀릴 수 있어요. 신호가 여러 개 동시에 켜지는 게 보통이고, 원인마다 살펴볼 것이 달라요 '
            '(금리 → 이자 부담·금리 방향 신호, 쏠림 → 업종 비중과 실적, 외국인 → 빌린 돈·강제 매도 여부, 환율·신용 → 자금 흐름, 정책·법안 → 어떤 법안이 어느 단계인지와 시행 시기). '
            '이 카드는 지금 어떤 신호가 켜져 있는지만 알려 주고, 어떻게 대처할지는 말하지 않아요. 내 보유 기간·레버리지·비중이 대처를 더 크게 좌우해요.</div></div>')


def lead_card(conn):
    """하락 점검: 과거 코스피가 -15%를 처음 넘어선 날, 자료가 있는 신호(금리·환율·신용)가 켜져 있었나"""
    try:
        import drop_diag as dd
        rows = dd.lead_table(conn, 15)
    except Exception:
        return ""
    if not rows:
        return ""
    names = {"rate": "금리", "fx": "환율", "credit": "신용"}
    trs = ""
    for r in rows:
        cells = ""
        for sid in ("rate", "fx", "credit"):
            v = r["lights"].get(sid)
            if v is None:
                cells += '<td class="muted">자료 없음</td>'
            else:
                st, first = v
                cells += f'<td>{dd.STATE_TXT[st]}' + (f'<br><span class="muted">{first[5:]}부터 켜짐</span>' if first else "") + '</td>'
        trs += f'<tr><td>{r["date"]}<br><span class="muted">{r["dd"]:.0f}%</span></td>{cells}</tr>'
    return ('<div class="card"><b>과거 하락 때는 어떤 신호가 먼저 켜졌나</b>'
            '<div class="muted">코스피가 고점 대비 -15%를 처음 넘어선 날의 신호 상태와, 그 전 60일 안에 처음 켜진 날이에요. 앱에 기록이 있는 금리·환율·신용만 다시 계산해요 '
            '(미국 금리는 최근 약 2년, 환율·신용은 2018년부터). 사례가 몇 개뿐이라 "이 신호가 먼저 켜지면 하락한다"는 증거가 못 돼요.</div>'
            f'<div class="scroll"><table><tr><th>-15% 넘은 날</th>{"".join(f"<th>{n}</th>" for n in names.values())}</tr>{trs}</table></div></div>')


def policy_card(conn):
    """하락 점검: 법안·법령·정책 소식이 코스피에 미친 영향 (같이 움직였나) + 최근 관련 소식"""
    try:
        import news_impact as ni
        import policy_impact as pi
        import stock_market
        res = pi.analyze(conn, list(stock_market.series(conn, "idx:KOSPI")))
        recent = pi.recent_list(conn)
    except Exception:
        return ""
    head = ('<div class="card"><b>법안·정책이 주식에 영향을 줬나</b>'
            '<div class="muted">국회 발의법률안·법제처 법령 개정·정책 소식을 주제별로 모아, 그 소식이 있던 날과 없던 날의 코스피 움직임을 비교해요. '
            '법안은 발의·심사 때 이미 시장에 반영됐을 수 있고, 통과돼도 시행은 한참 뒤인 경우가 많아요. 한 날에 여러 소식이 겹쳐 효과를 가르기 어려워서 '
            f'인과가 아니라 \'같이 크게 움직였나\'만 보는 참고 자료예요 (소식이 있던 날이 {ni.MIN_DAYS}일 미만이면 숫자를 내지 않아요).</div>')
    body = ""
    if res:
        order = list(pi.STRONG) + [pi.STAGE_PASS, pi.STAGE_BILL, pi.STAGE_LAW] + ["산업 지원", "금리·통화"]
        trs = ""
        for tp in order:
            st = res["주제"].get(tp)
            if not st:
                continue
            if not st["enough"]:
                trs += f'<tr><td>{escape(tp)}</td><td colspan="4" class="muted">소식이 있던 날 {st["event"]["n"] if st["event"] else 0}일 — 더 쌓이면 보여요</td></tr>'
                continue
            e, r = st["event"], st["rest"]
            trs += (f'<tr><td>{escape(tp)}</td><td>{e["n"]}일</td><td>{e["mean_abs"]:.2f}% <span class="muted">({r["mean_abs"]:.2f}%)</span></td>'
                    f'<td>{gap_pct(e["mean"] / 100)} <span class="muted">({gap_pct(r["mean"] / 100)})</span></td><td>{e["big"]:.0f}% <span class="muted">({r["big"]:.0f}%)</span></td></tr>')
        lo, hi = res["기간"]
        body += (f'<div style="margin-top:8px"><span class="muted">{lo} ~ {hi}, 괄호는 소식이 없던 날</span>'
                 '<div class="scroll"><table><tr><th>주제·단계</th><th>소식 있던 날</th><th>평균 |변화|</th><th>평균 변화</th><th>±1% 넘은 날</th></tr>'
                 f'{trs}</table></div></div>')
    else:
        body += '<div class="muted" style="margin-top:6px">법안·정책 기록이 아직 없어요. 지금 수집 → 시장·정책만을 눌러 주세요 (국회·법제처 키 필요).</div>'
    if recent:
        lis = "".join(f'<div style="margin-top:4px"><span class="chip">{escape(i["stage"])}</span> {i["date"]} '
                      + (f'<a href="{escape(i["url"])}" target="_blank" rel="noopener noreferrer">{escape(i["title"][:60])}</a>' if i["url"] else escape(i["title"][:60]))
                      + f' <span class="muted">{escape(", ".join(i["topics"][:2]))}</span></div>' for i in recent)
        body += f'<div style="margin-top:10px"><b style="font-size:13px">최근 2주 주식에 직접 닿는 소식</b>{lis}</div>'
    return head + body + '</div>'


def view_diag(conn, q):
    """시장 → 하락 점검: 낙폭, 누가 끌어내렸나(시총 기여도), 금리·외국인·환율 한 화면. 원인을 단정하지 않고 사실만 모음"""
    import deriv_bond as dvb
    import drop_diag as dd
    import stock_market
    # 1) 고점 대비 낙폭
    rows = ""
    for label, sid in (("코스피", "idx:KOSPI"), ("코스닥", "idx:KOSDAQ"), ("S&amp;P500", "idx:SPX")):
        r = dd.drawdown(list(stock_market.series(conn, sid)))
        if r:
            rows += (f'<tr><td>{label}</td><td>{r["last"]:,.0f} <span class="muted">{r["last_date"]}</span></td>'
                     f'<td>{r["high"]:,.0f} <span class="muted">{r["high_date"]}</span></td><td class="down">{r["dd"]:+.1f}%</td></tr>')
    dd_card = ('<div class="card"><b>고점 대비 얼마나 빠졌나</b><div class="muted">최근 약 1년(250거래일) 안의 최고 종가 대비.</div>'
               + (f'<div class="scroll"><table><tr><th>지수</th><th>지금</th><th>1년 내 고점</th><th>낙폭</th></tr>{rows}</table></div>' if rows else
                  '<div class="muted">지수 기록이 아직 없어요. 지금 수집 → 시장·정책만을 눌러 주세요.</div>') + '</div>')
    # 2) 누가 끌어내렸나
    wins = dd.windows(conn)
    if not wins:
        ct = ('<div class="card"><b>누가 끌어내렸나 (시가총액 기여도)</b><div class="muted">KRX 시세가 아직 부족해요. KRX 키를 저장하고 수집하면 '
              '(거래일 2일 이상) 계산돼요.</div></div>')
    else:
        parts = ""
        for label, a, b in wins:
            r = dd.contrib(conn, a, b)
            if not r:
                continue
            fmt = lambda xs: " · ".join(f"{escape(n)} {v:+.2f}" for n, v in xs) or "-"
            parts += (f'<div style="margin-top:10px"><b>{escape(label)}</b> <span class="muted">{a[4:6]}/{a[6:]} → {b[4:6]}/{b[6:]}</span>'
                      f'<div>코스피 시총 변화 <b class="{"down" if r["total"] < 0 else "up"}">{r["total"]:+.2f}%</b> = '
                      f'삼성전자·SK하이닉스 {r["semi"]:+.2f}%p + 그 외 {r["other"]:+.2f}%p</div>'
                      f'<div class="muted" style="font-size:13px">가장 끌어내린 종목(%p): {fmt(r["down"][:6])}</div>'
                      f'<div class="muted" style="font-size:13px">가장 받쳐 준 종목(%p): {fmt(r["up"][:4])}</div>'
                      + industry_line(conn, a, b) + '</div>')
        ct = ('<div class="card"><b>누가 끌어내렸나 (시가총액 기여도)</b>'
              '<div class="muted">종목 시가총액 변화 ÷ 시작일 코스피 전체 시가총액. 공식 지수와는 유동주식 비율 등으로 조금 달라요. '
              '앱이 받아 둔 KRX 시세(약 25거래일) 안에서만 계산해요. "받아 둔 시세 중 고점·첫날"은 내 보유 종목이 아니라 그 저장 기간 안의 기준일이에요 (위쪽 1년 고점과는 달라요).</div>' + (parts or '<div class="muted">계산할 자료가 부족해요.</div>') + '</div>')
    # 3) 금리·외국인·환율·신용
    sigs = {s["id"]: s for s in dvb.signals(conn)}
    inds = {i.key: i for i in stock_market.indicators(conn)}
    items = []
    if "kr_us" in sigs:
        items.append(("한미 금리차·미국 10년", sigs["kr_us"]))
    for k in ("kr10", "curve", "credit", "spot_foreign", "fut_foreign"):
        if k in sigs:
            items.append((sigs[k]["label"], sigs[k]))
    trs = ""
    for label, sg in items:
        chip = ("" if sg["signal"] is None else
                f'<span class="chip {"up" if sg["signal"] > 0 else "down" if sg["signal"] < 0 else ""}">{"우호" if sg["signal"] > 0 else "부담" if sg["signal"] < 0 else "중립"}</span>')
        trs += (f'<tr><td>{escape(label)}</td><td><b>{escape(sg["value"] or "-")}</b></td><td>{escape(sg["change"])}</td><td>{chip}</td></tr>')
    fx = inds.get("usdkrw")
    if fx and fx.available:
        trs += f'<tr><td>원/달러 환율</td><td><b>{escape(fx.value_text)}</b></td><td>{escape(fx.change_text)}</td><td></td></tr>'
    chk = ('<div class="card"><b>금리·외국인·환율</b><div class="muted">미국 금리가 오르고 외국인이 팔며 환율이 오르면 한국 증시에는 겹치는 부담이에요. 각 줄의 자세한 설명과 그래프는 선물·채권 탭에 있어요.</div>'
           + (f'<div class="scroll"><table><tr><th>지표</th><th>지금</th><th>변화</th><th>신호</th></tr>{trs}</table></div>' if trs else
              '<div class="muted">자료가 아직 없어요 (지금 수집 → 시장·정책만).</div>') + '</div>')
    note = ('<div class="muted" style="font-size:12px">이 화면은 누가 얼마나 빠졌고 어떤 지표가 같이 움직이는지 사실만 모아요. 하락의 원인을 단정하거나 '
            '사고팔 시점을 알려주지 않아요. 기사로 본 원인(AI 규제·금리·외국인 매도)은 시장 → 속보 탭에서 확인하세요.</div>')
    return page("하락 점검", lights_card(conn) + dd_card + ct + history_card(conn) + lead_card(conn) + policy_card(conn) + chk + note)


def _ab_cell(r, ab, raw):
    """초과수익(%p)과 그 아래 주가 변화(%) 한 칸"""
    if not r or r.get(ab) is None:
        return '<td class="muted">-</td>'
    v = r[ab]
    raw_txt = f'<br><span class="muted" style="font-size:11px">주가 {r[raw]:+.1f}%</span>' if r.get(raw) is not None else ""
    return f'<td><b class="{"up" if v > 0 else "down" if v < 0 else ""}">{v:+.1f}%p</b>{raw_txt}</td>'


def _link_form(key, label="연결"):
    return (f'<form method="post" action="/bills/link" class="filters" style="margin:4px 0 0">'
            f'<input type="hidden" name="bill" value="{escape(key)}">'
            f'<input type="text" name="stock" placeholder="종목 이름 또는 6자리 코드" autocomplete="off" style="flex:1 1 150px">'
            f'<button>{label}</button></form>')


def bills_watch_html(conn, q):
    """시장 → 법안·종목 → 내 종목 감시: 법안을 개별 종목에 연결하고, 법안 단계별로 그 종목이 지수보다 얼마나 움직였나를 봄"""
    import bill_watch as bw
    import stock_market
    msg = q.get("msg", "")
    head = ('<div class="card"><b>법안 ↔ 종목</b>'
            '<div class="muted">법안이 특정 종목의 사업에 닿을 때(예: 외국인 관광객 미용성형 부가세 환급 연장 → 글로벌텍스프리) 그 법안을 종목에 연결해 두고, '
            '발의·소관위·법사위·본회의 같은 단계 날짜마다 그 종목이 같은 기간 지수(코스닥/코스피)보다 얼마나 더 움직였는지 봐요. '
            '법안은 발의 전에 이미 소문으로 반영됐을 수 있고 시행은 나중이며 다른 재료가 더 클 수 있어서, 법안 때문에 움직였다고 읽으면 안 되는 참고 자료예요 (사례가 적을수록 더 그래요).</div>'
            '<form method="post" action="/bills/refresh" class="filters" style="margin:8px 0 0"><button>지금 확인 (국회 법안 받기)</button>'
            + (f' <span class="{"warn" if msg.startswith("✖") else "muted"}">{escape(msg)}</span>' if msg else "")
            + '<a class="chip" href="/settings">연결 확인(설정)</a></form></div>')
    # 규칙
    rs = bw.rules(conn)
    try:
        names = {c: n for c, n in conn.execute("SELECT code, name FROM bill_stocks")}
    except sqlite3.OperationalError:
        names = {}
    rl = ""
    for r in rs:
        rl += (f'<div style="margin:8px 0"><div class="row"><b>{escape(r["name"])}</b>'
               f'<form method="post" action="/bills/del_rule" style="margin:0" onsubmit="return confirm(\'이 규칙과 규칙이 자동으로 이은 연결을 지울까요?\')">'
               f'<input type="hidden" name="id" value="{r["id"]}"><button>삭제</button></form></div>'
               f'<div class="muted" style="font-size:13px">종목: {escape(", ".join(names.get(c, c) for c in r["codes"]))} · 법 이름: {escape(", ".join(r["law_names"]) or "-")}</div>'
               f'<div class="muted" style="font-size:13px">본문 키워드: {escape(", ".join(r["keywords"]) or "-")}</div></div>')
    rules_card = ('<div class="card"><b>감시 규칙</b><div class="muted">종목 + 국회에서 받을 법 이름 + 제안이유 본문 키워드. 법 이름의 법안을 받아 와 본문에 키워드가 있으면 그 종목에 자동 연결하고, '
                  '새 연결·새 단계(소관위·법사위·본회의)가 생기면 알려요. 본문을 못 읽어도 아래 후보에서 직접 연결할 수 있어요.</div>'
                  + (rl or '<div class="muted" style="margin-top:6px">아직 규칙이 없어요. 수집을 한 번 돌리면 글로벌텍스프리 규칙이 기본으로 들어가요.</div>')
                  + '<details style="margin-top:8px"><summary><b>규칙 추가</b></summary>'
                    '<form method="post" action="/bills/add_rule" style="margin-top:6px">'
                    '<label class="set">규칙 이름</label><input type="text" name="name" placeholder="예: OO — 의료관광 부가세 환급 연장" style="width:100%">'
                    '<label class="set">종목 (이름 또는 코드, 쉼표로 여러 개)</label><input type="text" name="stocks" placeholder="예: 글로벌텍스프리" style="width:100%">'
                    '<label class="set">국회에서 받을 법 이름 (쉼표로)</label><input type="text" name="laws" placeholder="예: 조세특례제한법, 부가가치세법" style="width:100%">'
                    '<label class="set">제안이유 본문 키워드 (하나라도 있으면 연결, 쉼표로)</label><input type="text" name="kws" placeholder="예: 외국인관광객, 미용성형, 환급특례" style="width:100%">'
                    '<button style="margin-top:8px">규칙 추가</button></form></details></div>')
    # 연결된 법안 → 종목별
    try:
        linked = conn.execute("SELECT l.code, l.bill_key, l.source, p.title, p.url, p.status, p.date FROM bill_links l "
                              "LEFT JOIN policy_items p ON p.key=l.bill_key ORDER BY l.code, p.date DESC").fetchall()
    except sqlite3.OperationalError:
        linked = []
    by_code = {}
    for code, key, src, title, url, status, date in linked:
        by_code.setdefault(code, []).append((key, src, title or key, url or "", status or "", date or ""))
    cards = ""
    for code, bills in by_code.items():
        try:
            mk = (conn.execute("SELECT market FROM bill_stocks WHERE code=?", (code,)).fetchone() or [""])[0]
        except sqlite3.OperationalError:
            mk = ""
        idx = list(stock_market.series(conn, "idx:KOSDAQ" if mk == "KOSDAQ" else "idx:KOSPI"))
        px = bw.prices(conn, code)
        sm = bw.summary_for_stock(conn, code, idx)
        spark = sparkline([v for _, v in px[-250:]]) if len(px) >= 2 else ""
        sm_txt = ""
        if sm["n"]:
            f = lambda v: "-" if v is None else f'{v:+.1f}%p'
            sm_txt = (f'<div style="margin:4px 0">이벤트 {sm["n"]}건 평균 (지수 대비): 선반영 5일 <b>{f(sm["ab5pre"])}</b> · 이벤트 후 1일 <b>{f(sm["ab1"])}</b> · 5일 <b>{f(sm["ab5"])}</b> · 20일 <b>{f(sm["ab20"])}</b>'
                      + (f' <span class="muted">(5일 초과가 +인 이벤트 {sm["up"]:.0f}%)</span>' if sm["up"] is not None else "") + '</div>')
        elif not px:
            sm_txt = '<div class="muted">주가를 아직 못 받았어요 (지금 확인 버튼 또는 다음 수집).</div>'
        body = ""
        for key, src, title, url, status, date in bills:
            t = f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(title[:70])}</a>' if url else escape(title[:70])
            trs = ""
            for d, st in bw.tracks(conn, key):
                rr = bw.reaction(px, idx, d)
                trs += (f'<tr><td>{d}<br><span class="muted">{escape(st)}</span></td>'
                        + (_ab_cell(rr, "ab5pre", "pre5") + _ab_cell(rr, "ab1", "r1") + _ab_cell(rr, "ab5", "r5") + _ab_cell(rr, "ab20", "r20") if rr else
                           '<td colspan="4" class="muted">그 날짜 앞뒤 주가가 부족해요 (너무 최근이거나 주가 기록 시작 전)</td>') + '</tr>')
            body += (f'<div style="margin-top:10px"><div>{t}</div>'
                     f'<div class="muted" style="font-size:12px">{escape(status)} · 연결: {"직접" if src == "수동" else "규칙(본문 키워드)"}'
                     f'<form method="post" action="/bills/unlink" style="display:inline;margin-left:6px"><input type="hidden" name="bill" value="{escape(key)}">'
                     f'<input type="hidden" name="code" value="{escape(code)}"><button style="padding:0 6px">연결 해제</button></form></div>'
                     + (f'<div class="scroll"><table><tr><th>단계</th><th>선반영 5일</th><th>이벤트 후 1일</th><th>5일</th><th>20일</th></tr>{trs}</table></div>' if trs else
                        '<div class="muted" style="font-size:12px">단계 날짜가 아직 없어요 (다음 수집 때 채워져요).</div>') + '</div>')
        cards += (f'<div class="card"><div class="row"><b><a href="{naver_link(code)}" target="_blank" rel="noopener noreferrer">{escape(names.get(code, code))}</a> '
                  f'<span class="muted">{escape(code)} · {escape(mk or "시장 미확인")}</span></b></div>{spark}{sm_txt}{body}'
                  '<div class="muted" style="font-size:12px;margin-top:6px">숫자는 지수 대비 초과(%p), 그 아래는 주가 자체 변화. 선반영 5일은 이벤트 직전 5거래일, 이벤트 후는 이벤트 직전 종가 대비예요.</div></div>')
    if not cards:
        cards = '<div class="card"><b>연결된 법안</b><div class="muted">아직 없어요. 수집이 돌거나 위 \'지금 확인\'을 누르면 규칙에 맞는 법안이 연결돼요. 아래 후보·검색에서 직접 연결해도 돼요.</div></div>'
    # 후보 + 검색
    cand = bw.candidates(conn)
    ct = ""
    for c in cand[:25]:
        t = f'<a href="{escape(c["url"])}" target="_blank" rel="noopener noreferrer">{escape(c["title"][:70])}</a>' if c["url"] else escape(c["title"][:70])
        ct += (f'<div style="margin:8px 0">{t}<div class="muted" style="font-size:12px">{escape(c["date"])} · {escape(c["status"])} · '
               f'{"제안이유를 읽었지만 키워드가 안 맞았어요" if c["has_text"] else "제안이유를 못 읽었어요"}</div>{_link_form(c["key"])}</div>')
    cand_card = ('<div class="card"><b>후보 (법 이름은 맞는데 아직 종목에 안 이은 법안)</b><div class="muted">'
                 '제목만으로는 내용을 알 수 없는 법안이 많아요 (예: \'조세특례제한법 일부개정법률안\'). 링크를 눌러 제안이유를 확인하고 해당하면 종목에 연결하세요.</div>'
                 + (ct or '<div class="muted" style="margin-top:6px">후보가 없어요.</div>') + '</div>')
    kw = (q.get("q") or "").strip()
    sr = ""
    if kw:
        try:
            rows = conn.execute("SELECT key, title, date, url, status FROM policy_items WHERE kind='법안' AND key!='_meta' AND title LIKE ? ORDER BY date DESC LIMIT 20",
                                (f"%{kw}%",)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for key, title, date, url, status in rows:
            t = f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(title[:70])}</a>' if url else escape(title[:70])
            sr += f'<div style="margin:8px 0">{t}<div class="muted" style="font-size:12px">{escape(date)} · {escape(status or "")}</div>{_link_form(key)}</div>'
        sr = sr or '<div class="muted" style="margin-top:6px">받아 둔 법안 중에 없어요. 법 이름을 감시 규칙에 넣고 \'지금 확인\'을 눌러 받아 보세요.</div>'
    search_card = ('<div class="card"><b>받아 둔 법안 검색</b><form method="get" action="/bills" class="filters" style="margin:6px 0 0">'
                   f'<input type="text" name="q" value="{escape(kw)}" placeholder="법안 제목 일부 (예: 조세특례)" style="flex:1 1 180px"><button>검색</button></form>{sr}</div>')
    return head + cards + rules_card + cand_card + search_card


def _mcap_text(v):
    if not v:
        return "-"
    return f"{v / 1e12:.1f}조" if v >= 1e12 else f"{v / 1e8:,.0f}억"


def _fb_form(action, key, **hidden):
    inputs = "".join(f'<input type="hidden" name="{k}" value="{escape(str(v))}">' for k, v in hidden.items())
    return inputs


def _impact_html(res, bill_key="", fb=None, concepts=None):
    """bill_impact.analyze 결과 → 개념·테마·업종·기업 표. bill_key가 있으면 '연결' 버튼과 후보 수정(제외·추가·되돌리기)을 붙임.
    fb=(removed, added) 는 후보 수정 결과 [(코드, 이름, 이유, scope, ref)]"""
    out = ""
    if res.get("note"):
        out += f'<div class="muted" style="margin:6px 0">{escape(res["note"])}</div>'
    if res["concepts"]:
        out += ('<div style="margin:6px 0"><b style="font-size:13px">법안에서 읽은 개념</b> '
                + " ".join(f'<span class="chip" title="{escape(", ".join(h))}">{escape(c)}</span>' for c, h in res["concepts"]) + '</div>')
    if res.get("members"):
        li = ""
        for m in res["members"]:
            li += (f'<div style="margin:6px 0"><b style="font-size:13px">{escape(m["kind"])} {escape(m["name"])}</b> <span class="muted" style="font-size:12px">({m["size"]}종목 · {escape(m["why"])})</span><div style="font-size:13px">'
                   + " · ".join(f'<a href="{naver_link(c)}" target="_blank" rel="noopener noreferrer">{escape(n)}</a>' for c, n in m["stocks"]) + '</div></div>')
        out += ('<details open style="margin:8px 0"><summary><b>가장 잘 맞는 작은 테마·업종의 종목 전체</b> <span class="muted" style="font-size:12px">(점수 순위에서 밀려도 놓치지 않게)</span></summary>' + li + '</details>')
    if res["groups"]:
        gr = "".join(f'<tr><td>{escape(k)}</td><td>{escape(n)}</td><td class="muted">{escape(w)}</td><td>{c}</td></tr>' for k, n, w, c, _ in res["groups"][:14])
        out += ('<details style="margin:6px 0"><summary><b style="font-size:13px">관련 테마·업종 ' + f'{len(res["groups"])}개</b></summary>'
                f'<div class="scroll"><table><tr><th>종류</th><th>이름</th><th>근거</th><th>종목 수</th></tr>{gr}</table></div></details>')
    if res["stocks"]:
        mc = {}
        try:
            mc = {c: m for c, m in _CTX_MCAP.get("m", {}).items()}
        except Exception:
            pass
        trs = ""
        for s in res["stocks"]:
            btn = ""
            if bill_key:
                cm = re.search(r"개념 '([^']+)'", (s["evidence"] or [""])[0])
                concept = cm.group(1) if cm else (concepts or [""])[0]
                base = _fb_form("", bill_key, back=bill_key, code=s["code"], name=s["name"])
                btn = (f'<form method="post" action="/bills/link" style="margin:0 0 3px"><input type="hidden" name="bill" value="{escape(bill_key)}">'
                       f'<input type="hidden" name="stock" value="{escape(s["code"])}"><button style="padding:0 6px" title="이 종목에 법안을 이어 단계별 주가 반응을 봐요">연결</button></form>'
                       f'<form method="post" action="/legis/fb_no" style="margin:0 0 3px">{base}<input type="hidden" name="scope" value="bill"><input type="hidden" name="ref" value="{escape(bill_key)}">'
                       '<button style="padding:0 6px" title="이 법안에서만 후보에서 뺍니다">이 법안 ✕</button></form>'
                       + (f'<form method="post" action="/legis/fb_no" style="margin:0">{base}<input type="hidden" name="scope" value="concept"><input type="hidden" name="ref" value="{escape(concept)}">'
                          f'<button style="padding:0 6px" title="분야 \'{escape(concept)}\' 법안 전체에서 후보에서 뺍니다">이 분야 ✕</button></form>' if concept else ""))
            trs += (f'<tr><td><a href="{naver_link(s["code"])}" target="_blank" rel="noopener noreferrer">{escape(s["name"])}</a><br><span class="muted" style="font-size:11px">{escape(s["code"])}</span></td>'
                    f'<td><b>{s["score"]:.1f}</b></td><td style="font-size:13px">{"<br>".join(escape(e) for e in s["evidence"])}</td>'
                    f'<td>{_mcap_text(mc.get(s["code"]))}</td><td>{btn}</td></tr>')
        out += ('<div style="margin-top:8px"><b>영향을 받을 수 있는 기업 후보</b> <span class="muted" style="font-size:12px">(점수는 순서를 보는 용도, 호재·악재 판단 아님)</span>'
                f'<div class="scroll"><table><tr><th>기업</th><th>점수</th><th>근거</th><th>시총</th><th></th></tr>{trs}</table></div></div>')
    if bill_key:
        removed, added = fb or ([], [])
        opts = f'<option value="bill:{escape(bill_key)}">이 법안만</option>' + "".join(f'<option value="concept:{escape(c)}">분야 전체: {escape(c)}</option>' for c in (concepts or [])[:4])
        out += ('<div style="margin-top:10px"><b style="font-size:13px">후보 직접 고치기</b> <span class="muted" style="font-size:12px">빠진 기업은 여기서 추가하고, 엉뚱한 기업은 위 표의 ✕로 빼요. 분야 전체로 하면 같은 분야의 다른 법안에도 반영돼요.</span>'
                f'<form method="post" action="/legis/fb_yes" class="filters" style="margin:4px 0 0"><input type="hidden" name="back" value="{escape(bill_key)}">'
                f'<input type="text" name="stock" placeholder="추가할 기업 (이름 또는 6자리 코드)" style="flex:1 1 170px"><select name="target">{opts}</select><button>후보에 추가</button></form></div>')
        mods = ""
        for lab, items in (("직접 추가한 기업", added), ("제외한 기업", removed)):
            if not items:
                continue
            chips = "".join(
                f'<form method="post" action="/legis/fb_del" style="display:inline;margin:0 4px 0 0"><input type="hidden" name="back" value="{escape(bill_key)}">'
                f'<input type="hidden" name="scope" value="{escape(sc)}"><input type="hidden" name="ref" value="{escape(rf)}"><input type="hidden" name="code" value="{escape(code)}">'
                f'<span class="chip" title="{escape(why)}">{escape(nm)}</span> <button style="padding:0 6px" title="수정을 되돌려요">되돌리기</button></form>' for code, nm, why, sc, rf in items)
            mods += f'<div style="margin-top:4px;font-size:13px"><span class="muted">{lab}:</span> {chips}</div>'
        out += mods
    return out


_CTX_MCAP = {}


def _bill_status_kind(status):
    st = status or ""
    if any(w in st for w in ("원안가결", "수정가결", "가결")) and "폐기" not in st:
        return "통과"
    if any(w in st for w in ("폐기", "철회", "부결")):
        return "폐기"
    return "심사 중"


def _bills_tabs(t):
    return '<div style="margin:0 0 8px">' + " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == t else f'<a class="chip" href="/bills?t={k}">{v}</a>')
        for k, v in (("list", "법안 조회"), ("find", "내용으로 기업 찾기"), ("watch", "내 종목 감시"))) + '</div>'


def _ab_td(v, raw=False):
    if v is None:
        return '<td class="muted">-</td>'
    return f'<td><b class="{"up" if v > 0 else "down" if v < 0 else ""}">{v:+.1f}%p</b></td>'


def stage_prices_card(conn, key, cand, related=()):
    """법안 상세: 입법 과정별 주가 변화. 이 법안이 지나온 단계(발의…본회의·공포·시행)마다 관심·연결·후보 종목의 지수 대비 초과수익을,
    과거 평균('입법 주가 분석')과 나란히. cand=[(코드, 이름)] 후보 종목 (후보 수정 반영)"""
    import legis as lg
    import legis_study as ls
    watch = lg.watch_list(conn)
    linked = [(c, n) for c, n in lg._q(conn, "SELECT l.code, COALESCE(s.name, l.code) FROM bill_links l LEFT JOIN bill_stocks s ON s.code=l.code WHERE l.bill_key=?", (key,))]
    wset = {c for c, _ in watch}
    rel = {x[0] for x in cand} | set(related)                       # 후보 + 이 법안의 관련 테마·업종 구성 종목 전체
    pool = [(c, n) for c, n in watch if c in rel] + linked + list(cand)
    stocks, seen = [], set()
    for c, n in pool:
        if c not in seen:
            seen.add(c)
            stocks.append((c, n))
        if len(stocks) >= 6:
            break
    if not stocks or not lg.stage_events(conn, key):
        return ""
    data = lg.bill_stage_prices(conn, key, stocks)
    ref, _ = ls.results(conn)

    def refcell(label):
        p = ref.get(("단계", label))
        if not p:
            return '<td class="muted">-</td>'
        v, cls = _study_verdict(p)
        pre = "-" if p["pre"] is None else f'{p["pre"]:+.1f}'
        p10 = "-" if p["post10"] is None else f'{p["post10"]:+.1f}'
        return f'<td style="font-size:12px">사전 {pre} · 사후10 {p10}%<br><span class="chip {cls}" style="font-size:11px">{escape(v)}</span></td>'
    body = ""
    missing = []
    for d in data:
        head = (f'<div style="margin-top:10px"><a href="{naver_link(d["code"])}" target="_blank" rel="noopener noreferrer"><b>{"★ " if d["code"] in wset else ""}{escape(d["name"])}</b></a> '
                f'<span class="muted" style="font-size:12px">{escape(d["code"])} · 지수: {"코스닥" if d["market"] == "KOSDAQ" else "코스피"}'
                + (f' · 주가 기준 {escape(d["last_price_date"])}' if d["has_prices"] else "") + '</span></div>')
        if not d["has_prices"]:
            missing.append(d["code"])
            body += head + '<div class="muted" style="font-size:13px">주가를 아직 못 받았어요 (아래 \'주가 받기\').</div>'
            continue
        trs = ""
        for r in d["rows"]:
            e, rc = r["event"], r["reaction"]
            lab = f'{escape(e["label"])}<br><span class="muted" style="font-size:11px">{escape(e["date"])}' + (" · 예정" if e["future"] else "") + '</span>'
            if e["future"]:
                days = (dt.date.fromisoformat(e["date"]) - dt.date.today()).days
                trs += f'<tr><td>{lab}</td><td colspan="5" class="muted">D-{days}일 뒤 (아직 주가 변화 없음)</td>{refcell(e["label"])}</tr>'
            elif not rc:
                trs += f'<tr><td>{lab}</td><td colspan="5" class="muted">그 날짜 앞뒤 주가가 부족해요</td>{refcell(e["label"])}</tr>'
            else:
                trs += (f'<tr><td>{lab}</td>{_ab_td(rc["ab5pre"])}{_ab_td(rc["ab1"])}{_ab_td(rc["ab5"])}{_ab_td(rc["ab20"])}{_ab_td(rc.get("since"))}{refcell(e["label"])}</tr>')
        body += head + ('<div class="scroll"><table><tr><th>단계</th><th>선반영<br><span class="muted" style="font-size:11px">직전 5일</span></th><th>이후 1일</th><th>5일</th><th>20일</th>'
                        f'<th>단계 이후<br><span class="muted" style="font-size:11px">현재까지</span></th><th>과거 평균<br><span class="muted" style="font-size:11px">참고</span></th></tr>{trs}</table></div>')
    btn = ""
    if missing:
        btn = ('<form method="post" action="/legis/px_bill" class="filters" style="margin:8px 0 0">'
               f'<input type="hidden" name="back" value="{escape(key)}"><input type="hidden" name="codes" value="{escape(",".join(missing))}">'
               f'<button>주가 받기 ({len(missing)}종목)</button><span class="muted" style="font-size:12px">네이버에서 일별 종가를 받아요 (종목당 몇 초)</span></form>')
    note = ('<div class="muted" style="font-size:12px;margin-top:8px">숫자는 <b>지수 대비 초과수익(%p)</b>이에요 (코스닥 종목은 코스닥, 그 외 코스피). 선반영 5일은 단계 직전 5거래일, 이후는 단계 직전 종가 대비예요. '
            '한 법안의 몇 건은 우연·시장 전체·다른 재료의 영향이 커서 법안 때문이라고 읽으면 안 돼요. \'과거 평균\'은 입법 주가 분석(여러 법안 평균)의 같은 단계 결과예요'
            + ('' if ref else ' — 아직 계산 전이라 비어 있어요 (아이디어 → 입법 주가 분석)') + '.</div>')
    return f'<div class="card"><b>입법 과정별 주가 변화</b>{body}{btn}{note}</div>'


def view_bills(conn, q):
    """시장 → 법안·종목: 법안 조회 → 영향 기업 후보 / 내용을 붙여 넣어 기업 찾기 / 내 종목 감시"""
    import bill_impact as bi
    import bill_watch as bw
    import theme_map
    t = q.get("t") if q.get("t") in ("list", "find", "watch", "bill") else "list"
    msg = q.get("msg", "")
    flash = f'<div class="card {"warn" if msg.startswith("✖") else ""}">{escape(msg)}</div>' if msg else ""
    if t == "watch":
        return page("법안·종목", _bills_tabs(t) + flash + bills_watch_html(conn, q))
    try:
        _CTX_MCAP["m"] = {c: m for c, m in conn.execute("SELECT code, mktcap FROM krx_daily WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_daily)")}
    except sqlite3.OperationalError:
        _CTX_MCAP["m"] = {}
    data = theme_map.load(conn)
    no_theme = not data["테마"] and not data["업종"]
    warn = ('<div class="card warn">테마·업종 자료가 아직 없어서 기업을 짐작할 수 없어요. 서버에서 업데이트한 뒤 <b>지금 수집 → 시장·정책만</b>을 한 번 돌려 주세요 (테마 265개·업종 79개를 받아요).</div>'
            if no_theme else "")
    if t == "find":
        text = (q.get("text") or "").strip()
        title = (q.get("title") or "").strip()
        form = ('<div class="card"><b>법안 내용으로 영향 기업 찾기</b>'
                '<div class="muted">법안 제목과 제안이유(또는 뉴스·요약)를 붙여 넣으면, 그 내용에 나오는 말을 테마·업종·기업명과 이어서 영향을 받을 수 있는 기업 후보를 보여 줘요. '
                '국회 사이트에서 본문을 자동으로 못 읽을 때나, 아직 발의 전인 법안 내용을 미리 볼 때 써요.</div>'
                '<form method="get" action="/bills" style="margin-top:6px"><input type="hidden" name="t" value="find">'
                f'<label class="set">법안 제목 (선택)</label><input type="text" name="title" value="{escape(title)}" style="width:100%">'
                f'<label class="set">제안이유·주요내용</label><textarea name="text" rows="7" style="width:100%" placeholder="예: 외국인관광객이 미용성형 의료용역을 제공받는 경우 부가가치세 환급특례의 적용기한을 연장…">{escape(text)}</textarea>'
                '<button style="margin-top:6px">기업 찾기</button></form></div>')
        res = _impact_html(bi.analyze(conn, title, text, data=data)) if (title or text) and not no_theme else ""
        return page("법안·종목", _bills_tabs(t) + flash + warn + form + (f'<div class="card">{res}</div>' if res else ""))
    if t == "bill":
        key = q.get("key", "")
        row = conn.execute("SELECT title, date, url, status, topics FROM policy_items WHERE key=?", (key,)).fetchone() if key else None
        if not row:
            return page("법안·종목", _bills_tabs("list") + '<div class="card">법안을 찾지 못했어요.</div>')
        title, date, url, status, topics = row
        txt_row = conn.execute("SELECT text FROM bill_text WHERE bill_key=?", (key,)).fetchone() if has_table(conn, "bill_text") else None
        text = (txt_row[0] if txt_row else "") or ""
        tl = "".join(f'<span class="chip">{escape(s)} {escape(d)}</span> ' for d, s in bw.tracks(conn, key))
        t_html = f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(title)}</a>' if url else escape(title)
        text_html = (f'<details open><summary><b style="font-size:13px">제안이유·주요내용 (국회 의안정보)</b></summary><div style="margin-top:4px;font-size:14px">{escape(text[:900])}{"…" if len(text) > 900 else ""}</div></details>'
                     if text else '<div class="muted">제안이유 본문이 없어요. 아래 버튼으로 국회 페이지에서 가져오거나, 내용을 직접 붙여 넣으면 더 정확해져요.</div>')
        tools = ('<div style="margin-top:8px"><form method="post" action="/bills/fetch_text" class="filters" style="margin:0 0 6px">'
                 f'<input type="hidden" name="bill" value="{escape(key)}"><button>제안이유 가져오기 (국회 페이지)</button></form>'
                 '<details><summary class="muted">내용을 직접 붙여 넣기</summary>'
                 f'<form method="post" action="/bills/save_text"><input type="hidden" name="bill" value="{escape(key)}">'
                 '<textarea name="text" rows="6" style="width:100%" placeholder="제안이유 및 주요내용"></textarea><button style="margin-top:4px">저장하고 다시 분석</button></form></details></div>')
        linked = conn.execute("SELECT l.code, COALESCE(s.name, l.code) FROM bill_links l LEFT JOIN bill_stocks s ON s.code=l.code WHERE l.bill_key=?", (key,)).fetchall() if has_table(conn, "bill_links") else []
        lk = ('<div style="margin-top:6px"><b style="font-size:13px">연결된 종목</b> ' + " ".join(
            f'<span class="chip">{escape(n)}</span>' for _, n in linked) + ' <a class="chip" href="/bills?t=watch">반응 보기(내 종목 감시)</a></div>') if linked else ""
        import legis as _lg
        _sm = _lg.summary_line(text)
        _nt = _lg.nature_hint(title, text)
        head = (f'<div class="card"><div class="muted" style="font-size:12px"><a href="/bills">← 법안 조회</a></div><div style="margin-top:4px"><b>{t_html}</b> {_nature_chip(_nt)}</div>'
                + (f'<div style="font-size:14px;margin-top:4px">→ {escape(_sm)}</div>' if _sm else "")
                + (f'<div class="muted" style="font-size:12px;margin-top:2px">단어로 본 힌트: 지원 쪽 {escape(", ".join(_nt["support"]) or "-")} · 규제 쪽 {escape(", ".join(_nt["burden"]) or "-")} (기업마다 다르게 닿아요, 호재·악재 판단이 아니에요)</div>' if _nt["kind"] != "none" else "")
                + f'<div class="muted" style="font-size:13px">{escape(date or "")} · {escape(status or "")}</div>'
                f'<div style="margin-top:4px">{tl}</div>{lk}{text_html}{tools}</div>')
        res = ""
        _stage_card = ""
        if not no_theme:
            import legis as _lg2
            _r = bi.analyze(conn, _lg2.bill_name(title), text, data=data)
            _cn = [c for c, _ in _r["concepts"]]
            _new, _rem, _add = _lg2.apply_feedback([[x["code"], x["name"], x["score"], (x["evidence"] or [""])[0]] for x in _r["stocks"]], _cn, key, _lg2.feedback(conn))
            _ev = {x["code"]: x["evidence"] for x in _r["stocks"]}
            _r["stocks"] = [dict(code=c_, name=n_, score=sc_, evidence=([e_] if c_ not in _ev else _ev[c_]), groups=[]) for c_, n_, sc_, e_ in _new]
            res = _impact_html(_r, key, fb=(_rem, _add), concepts=_cn)
            _rel = {x["code"] for x in _r["stocks"]} | {c_ for m_ in _r["members"] for c_, _n in m_["stocks"]}
            _stage_card = stage_prices_card(conn, key, [(x[0], x[1]) for x in _new[:5]], related=_rel)
        return page("법안·종목", _bills_tabs("list") + flash + warn + head + _stage_card + (f'<div class="card">{res}</div>' if res else "")
                    + '<div class="muted" style="font-size:12px">후보는 법안 문구와 테마·업종 이름·개념 사전을 이은 짐작이에요. 같은 법안이 기업마다 호재일 수도 악재일 수도 있어서 방향은 판단하지 않아요. '
                      '\'연결\'을 누르면 그 종목에 법안을 이어 단계별 주가 반응을 볼 수 있어요.</div>')
    # 목록
    kw = (q.get("kw") or "").strip()
    days = q.get("d") if q.get("d") in ("30", "90", "180", "0") else "90"
    st = q.get("s") if q.get("s") in ("통과", "심사 중", "폐기") else ""
    only_mkt = q.get("m") == "1"
    page_no = max(0, int(q.get("p", "0") or 0)) if str(q.get("p", "0")).isdigit() else 0
    where, args = ["kind='법안'", "key!='_meta'"], []
    if kw:
        where.append("title LIKE ?")
        args.append(f"%{kw}%")
    if days != "0":
        where.append("date >= ?")
        args.append((dt.date.today() - dt.timedelta(days=int(days))).isoformat())
    if only_mkt:
        where.append("topics != '[]' AND topics != ''")
    try:
        rows = conn.execute(f"SELECT key, title, date, url, status, topics FROM policy_items WHERE {' AND '.join(where)} ORDER BY date DESC", args).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if st:
        rows = [r for r in rows if _bill_status_kind(r[4]) == st]
    total = len(rows)
    per = 30
    rows = rows[page_no * per: (page_no + 1) * per]
    texts = {}
    if has_table(conn, "bill_text"):
        texts = {k: tx for k, tx in conn.execute("SELECT bill_key, text FROM bill_text WHERE text != ''")}
    items = ""
    for key, title, date, url, status, topics in rows:
        try:
            tps = json.loads(topics or "[]")
        except ValueError:
            tps = []
        pre = ""
        if not no_theme:
            r = bi.analyze(conn, title, texts.get(key, ""), top=4, data=data)
            if r["stocks"] and (texts.get(key) or r["concepts"]):
                pre = ('<div style="font-size:13px;margin-top:2px">후보: ' + ", ".join(escape(s["name"]) for s in r["stocks"][:4])
                       + f' <span class="muted">({"본문" if texts.get(key) else "제목"} 기준)</span></div>')
        kind = _bill_status_kind(status)
        import legis as _lg
        sm_line = _lg.summary_line(texts.get(key, ""))
        nt_chip = _nature_chip(_lg.nature_hint(title, texts.get(key, "")))
        pre = (f'<div style="font-size:13px;margin-top:2px">→ {escape(sm_line)}</div>' if sm_line else "") + pre
        items += (f'<div style="margin:10px 0"><div><a href="/bills?t=bill&key={quote(key)}"><b>{escape(title[:80])}</b></a> {nt_chip}</div>'
                  f'<div class="muted" style="font-size:12px">{escape(date or "")} · <span class="chip {"up" if kind == "통과" else "down" if kind == "폐기" else ""}">{kind}</span> '
                  + "".join(f'<span class="chip">{escape(x)}</span> ' for x in tps[:3]) + f'</div>{pre}</div>')

    def sel(name, opts, cur):
        return f'<select name="{name}">' + "".join(f'<option value="{v}"{" selected" if v == cur else ""}>{l}</option>' for v, l in opts) + '</select>'
    form = ('<form method="get" action="/bills" class="filters" style="margin:6px 0 0"><input type="hidden" name="t" value="list">'
            f'<input type="text" name="kw" value="{escape(kw)}" placeholder="법안 제목 일부 (예: 조세특례, 상법)" style="flex:1 1 170px">'
            + sel("d", (("30", "최근 30일"), ("90", "최근 90일"), ("180", "최근 180일"), ("0", "전체")), days)
            + sel("s", (("", "상태 전체"), ("심사 중", "심사 중"), ("통과", "통과"), ("폐기", "폐기")), st)
            + f'<label style="white-space:nowrap"><input type="checkbox" name="m" value="1"{" checked" if only_mkt else ""}> 시장 관련만</label><button>조회</button></form>')
    nav = ""
    if total > per:
        base = {"t": "list", "kw": kw, "d": days, "s": st, "m": "1" if only_mkt else ""}
        prev = f'<a class="chip" href="/bills?{urlencode({**base, "p": page_no - 1})}">← 이전</a> ' if page_no > 0 else ""
        nxt = f'<a class="chip" href="/bills?{urlencode({**base, "p": page_no + 1})}">다음 →</a>' if (page_no + 1) * per < total else ""
        nav = f'<div style="margin-top:6px">{prev}<span class="muted">{page_no * per + 1}~{min(total, (page_no + 1) * per)} / {total}건</span> {nxt}</div>'
    head = ('<div class="card"><b>법안 조회</b>'
            '<div class="muted">국회 발의법률안을 조회하고, 법안을 누르면 그 법안이 영향을 줄 수 있는 기업 후보를 근거와 함께 보여 줘요. 목록의 \'후보\'는 간단 미리보기고, 법안 제목이 평범하면 본문이 있어야 정확해요.</div>'
            '<form method="post" action="/bills/refresh" class="filters" style="margin:6px 0 0"><button>국회에서 새로 받기</button></form>' + form + '</div>')
    body = f'<div class="card">{items or "<div class=muted>조건에 맞는 법안이 없어요. 받아 둔 법안이 없다면 위 버튼이나 수집을 먼저 돌려 주세요.</div>"}{nav}</div>'
    return page("법안·종목", _bills_tabs("list") + flash + warn + head + body)


LEGIS_SORTS = {"bill": (("recent", "최근 단계 변동순"), ("promul", "공포 임박순"), ("stocks", "영향 기업 많은 순"), ("stage", "진행 단계 높은 순"), ("new", "발의 최신순"), ("watch", "관심 종목 영향순"), ("eff", "시행 임박순")),
               "stock": (("cnt", "관련 법안 많은 순"), ("soon", "공포 임박순"), ("cap", "시가총액 큰 순"), ("stage", "진행 단계 높은 순"))}
LEGIS_STAGE_FILTERS = (("", "입법 중 전체"), ("early", "소관위 심사 (1~4단계)"), ("law", "법사위 계류 (5단계)"), ("pass", "본회의 통과·공포 대기"), ("done", "공포 확인됨"), ("eff", "시행 예정 (공포 후 시행 전)"))


def _legis_promul_text(pm):
    """공포 시기 한 줄 (확정 / 예상 / 미정)"""
    k = pm["kind"]
    if k == "done":
        return (f'<b class="up">공포 확정 {escape(pm["date"])}</b>' + (f' · 시행 {escape(pm["effective"])}' if pm["effective"] else ""))
    if k == "est":
        return (f'<b>공포 예상 {escape(pm["date"][5:])} 전후</b>' + (f' <span class="muted">(늦어도 {escape(pm["until"][5:])}경)</span>' if pm["until"] else "")
                if pm["date"] else f'<b>공포 대기</b>')
    if k == "pending":
        return '<span class="muted">공포 시기 미정</span>'
    return f'<span class="muted">{escape(pm["note"])}</span>'


def _legis_sort_key(sort):
    far = "9999-12-31"

    def promul_date(r):
        pm = r["promul"]
        return pm["date"] if pm["kind"] in ("done", "est") and pm["date"] else far
    return {"recent": lambda r: r["last"] or "", "new": lambda r: r["started"] or "", "stocks": lambda r: r["n"], "stage": lambda r: (r["stage"], r["last"] or ""),
            "promul": promul_date, "watch": lambda r: r.get("watch", 0.0),
            "eff": lambda r: (r["promul"]["effective"] if r["promul"]["kind"] == "done" and r["promul"]["effective"] >= dt.date.today().isoformat() else far)}[sort], sort in ("recent", "new", "stocks", "stage", "watch")


def legis_prep_card(conn, diag, total_all, n_cand):
    """입법 영향 탭 맨 위: 무엇이 준비됐고 무엇이 비었는지 진단 + '지금 준비하기' 버튼 + 진행 상황. → (html, 진행 중 여부)"""
    import legis as lg
    d = Path(_DATA_DIR["path"] or ".")
    st = lg.read_status(str(d / "legis_status.json"))
    run = None
    try:
        run = json.loads((d / "market_run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    running = bool(st and st.get("running"))
    problems = []
    if not diag["themes"] or not diag["industries"]:
        problems.append(f'테마·업종 자료 없음 (테마 {diag["themes"]}개, 업종 매핑 {diag["industries"]}종목)')
    if not diag["bills"]:
        problems.append("받아 둔 국회 법안이 없어요 (국회 법안 목록을 못 받았어요)")
    elif not diag["in_process"]:
        problems.append("법안은 있는데 최근 입법 중인 법안이 없어요 (모두 폐기됐거나 기간 밖)")
    if diag["in_process"] and not n_cand and diag["themes"]:
        problems.append("영향 기업 후보가 하나도 안 잡혔어요 (제안이유 본문이 없는 법안이 많을 수 있어요)")
    ok = not problems and total_all > 0
    icon = {"ok": "✔", "fail": "✖", "run": "…", "wait": "·"}
    steps = ""
    if st:
        for s_ in st.get("steps", []):
            steps += (f'<div style="margin:3px 0"><b>{icon.get(s_["state"], "")} {escape(s_["name"])}</b>'
                      f' <span class="{"warn" if s_["state"] == "fail" else "muted"}" style="font-size:12px">{escape(s_.get("msg", ""))}</span></div>')
        steps = (f'<div style="margin-top:8px"><b style="font-size:13px">{"진행 중" if running else "마지막 준비 결과"}</b> '
                 f'<span class="muted" style="font-size:12px">{escape(st.get("started", ""))} ~ {escape(st.get("finished", "") or "…")}</span>{steps}</div>')
    last = ""
    if run and isinstance(run.get("out"), dict):
        keys = [(k, run["out"][k]) for k in ("themes", "industry", "bill_feed", "legis", "bill_watch", "policy") if k in run["out"]]
        if keys:
            last = ('<details style="margin-top:6px"><summary class="muted">마지막 시장·정책 수집(' + escape(run.get("when", "")) + ') 결과</summary>'
                    + "".join(f'<div style="font-size:12px;margin:2px 0"><b>{escape(k)}</b>: <span class="muted">{escape(json.dumps(v, ensure_ascii=False, default=str)[:260])}</span></div>' for k, v in keys) + '</details>')
        else:
            last = f'<div class="muted" style="font-size:12px;margin-top:6px">마지막 시장·정책 수집({escape(run.get("when", ""))})에 법안·테마 단계 기록이 없어요 — 서버가 아직 새 버전이 아닐 수 있어요.</div>'
    elif not run:
        last = '<div class="muted" style="font-size:12px;margin-top:6px">시장·정책 수집 기록이 없어요 (서버가 새 버전으로 한 번 수집해야 기록돼요).</div>'
    counts = (f'법안 {diag["bills"]}건 · 입법 중 {diag["in_process"]} · 단계 기록 {diag["tracks"]} · 영향 기업 계산됨 {diag["cached"]} · 제안이유 {diag["texts"]} · '
              f'테마 {diag["themes"]}(종목 {diag["theme_stocks"]}) · 업종 매핑 {diag["industries"]}종목 · 공포일 확인 {diag["promul"]}')
    btn = ('<form method="post" action="/legis/prepare" class="filters" style="margin:8px 0 0">'
           f'<button{" disabled" if running else ""}>{"준비하는 중…" if running else "지금 준비하기"}</button>'
           '<span class="muted" style="font-size:12px">테마·업종 → 국회 법안 → 제안이유 → 공포일 → 영향 기업을 차례로 받아 계산해요 (처음엔 5~8분)</span></form>')
    msg = ""
    body = (f'<div class="muted" style="font-size:13px">{escape(counts)}</div>' + btn + steps + last)
    if ok and not running:
        return (f'<details class="card"><summary><b>준비 상태</b> <span class="muted">✔ 정상 — 필요하면 다시 준비</span></summary>{body}</details>', False)
    head = ('<b>준비 상태</b> ' + ("<span class=\"warn\">진행 중</span>" if running else "<span class=\"warn\">비어 있는 게 있어요</span>"))
    probs = "".join(f'<div class="warn" style="margin-top:4px">· {escape(x)}</div>' for x in problems)
    return (f'<div class="card">{head}{probs}{body}</div>', running)


_NATURE_TXT = {"support": ("수혜 가능 힌트", "up"), "burden": ("부담 가능 힌트", "down"), "mixed": ("지원·규제 혼재", "warn")}


def _nature_chip(n):
    """법안 문구의 단어로 본 지원/규제 힌트 칩 (판단이 아니라 힌트. 기업마다 다르게 닿을 수 있음)"""
    if not n or n.get("kind") not in _NATURE_TXT:
        return ""
    lab, cls = _NATURE_TXT[n["kind"]]
    words = " · ".join(([f"지원 {', '.join(n['support'][:3])}"] if n["support"] else []) + ([f"규제 {', '.join(n['burden'][:3])}"] if n["burden"] else []))
    return f'<span class="chip {cls}" title="{escape(words)} — 단어로 본 힌트예요. 기업마다 다르게 닿고, 호재·악재 판단이 아니에요">{lab}</span>'


def _summary_div(r):
    return (f'<div style="font-size:13px;margin-top:3px">→ {escape(r["summary"])}</div>' if r.get("summary")
            else ('<div class="muted" style="font-size:12px;margin-top:3px">제안이유 본문이 아직 없어요 (제목만 기준)</div>' if not r.get("has_text") else ""))


def _legis_card(r, prog_fn, sub=False, wcodes=frozenset()):
    import legis as _lgw
    """법안 한 건 카드 (대표 법안). sub=True면 묶음 안에서 보여 줄 짧은 줄"""
    if sub:
        return (f'<div style="margin:6px 0;font-size:13px"><a href="/bills?t=bill&key={quote(r["key"])}">{escape(r["name"][:70])}</a> '
                f'<span class="muted">{r["stage"]}/6 {escape(r["stage_label"] or "-")} · 발의 {escape(r["started"] or "-")}</span> {_nature_chip(r["nature"])}'
                + (f'<div class="muted">→ {escape(r["summary"])}</div>' if r.get("summary") else "") + '</div>')
    kind_cls = "up" if r["kind"] == "통과" else ""
    wh = _lgw.watch_hits(r, wcodes) if wcodes else []                                   # 관심 종목은 점수 순위 밖이어도(테마 구성 종목) 앞에 ★로
    wset = {x[0] for x in wh}
    ordered = wh + [x for x in r["stocks"] if x[0] not in wset]
    chips = "".join((f'<span class="chip" style="font-weight:700" title="관심 종목 · {escape(e)}">★ {escape(nm)}</span> ' if code in wset
                     else f'<span class="chip" title="{escape(e)}">{escape(nm)}</span> ') for code, nm, sc, e in ordered[:6]) or '<span class="muted">후보 없음 (개념 사전·테마에 안 잡힌 분야이거나 본문이 필요해요)</span>'
    more = f' <span class="muted">+{r["n"] - 6}</span>' if r["n"] > 6 else ""
    extra = ""
    if r["promul"]["dates"]:
        extra += f' · 본문 날짜 {escape(", ".join(r["promul"]["dates"]))}'
    if r["promul"]["hint"]:
        extra += f' · {escape(r["promul"]["hint"])}'
    return (f'<div><a href="/bills?t=bill&key={quote(r["key"])}"><b>{escape(r["name"][:80])}</b></a> '
            f'<span class="chip {kind_cls}">{r["kind"]}</span> {_nature_chip(r["nature"])}</div>'
            + _summary_div(r)
            + f'<div style="font-size:13px;margin-top:3px">{prog_fn(r)} {escape(r["stage_label"] or "-")} ({r["stage"]}/6) <span class="muted">· 최근 단계 {escape((r["last"] or "")[5:])} · 발의 {escape(r["started"] or "-")}</span></div>'
            f'<div style="font-size:13px;margin-top:2px">{_legis_promul_text(r["promul"])}<span class="muted">{extra}</span></div>'
            + ("" if r["promul"]["kind"] in ("done", "none") else f'<div class="muted" style="font-size:12px">{escape(r["promul"]["note"])}</div>')
            + (f'<div style="margin-top:3px">{" ".join(f"<span class=chip>{escape(c)}</span>" for c in r["concepts"][:3])}</div>' if r["concepts"] else "")
            + f'<div style="margin-top:4px"><span class="muted" style="font-size:12px">영향 기업 후보 ({"본문" if r["has_text"] else "제목"} 기준):</span> {chips}{more}</div>')


def legis_watch_card(conn, watch, rows):
    """입법 영향 탭 맨 위: 관심 종목 ★ — 추가·삭제·가져오기, 관심 종목에 영향 주는 법안 수"""
    wcodes = {c for c, _ in watch}
    import legis as _lgw
    n_rel = sum(1 for r in rows if _lgw.watch_hits(r, wcodes)) if wcodes else 0
    chips = "".join(f'<form method="post" action="/legis/watch_del" style="display:inline;margin:0 4px 4px 0"><input type="hidden" name="code" value="{escape(c)}">'
                    f'<span class="chip">★ {escape(n)}</span><button style="padding:0 6px" title="관심 종목에서 빼기">✕</button></form>' for c, n in watch)
    state = (f'<div style="margin-top:4px">관심 종목에 영향을 줄 수 있는 입법 중 법안 <b>{n_rel}건</b> '
             f'<a class="chip" href="/ideas?t=legis&f=1&w=1&ne=1&gr=1&s=watch">관심 종목 관련만 보기</a></div>') if watch else ""
    forms = ('<details style="margin-top:6px"' + ("" if watch else " open") + '><summary><b style="font-size:13px">관심 종목 추가</b></summary>'
             '<form method="post" action="/legis/watch_add" class="filters" style="margin:6px 0 0"><input type="text" name="stock" placeholder="종목 이름 또는 6자리 코드 (쉼표로 여러 개)" style="flex:1 1 200px"><button>추가</button></form>'
             '<form method="post" action="/legis/watch_import" class="filters" style="margin:6px 0 0"><button>내 종목 점수·법안 감시 종목 가져오기</button></form></details>')
    return ('<div class="card"><b>★ 관심 종목</b> <span class="muted" style="font-size:12px">관심 종목에 영향을 줄 수 있는 법안이 새로 생기거나 단계가 바뀌면(소관위·법사위·본회의·공포) 알려 줘요 (설정 → 알림에서 끄기)</span>'
            + (f'<div style="margin-top:6px">{chips}</div>' if watch else '<div class="muted" style="margin-top:6px">아직 없어요. 종목을 추가하면 그 종목에 닿는 법안만 모아 보고 알림을 받을 수 있어요.</div>')
            + state + forms + '</div>')


def legis_block(conn, code):
    """종목 화면: 이 종목에 영향을 줄 수 있는 입법 중 법안 (저장된 영향 기업 계산이 있을 때만)"""
    try:
        import legis as lg
        rows = lg.for_stock(conn, code)
    except Exception:
        return ""
    if not rows:
        return ""
    lis = ""
    for r in rows:
        pm = r["promul"]
        lis += (f'<div style="margin:8px 0"><a href="/bills?t=bill&key={quote(r["key"])}"><b>{escape(r["name"][:60])}</b></a> '
                f'<span class="chip {"up" if r["kind"] == "통과" else ""}">{r["kind"]}</span> {_nature_chip(r["nature"])}'
                + (f'<div style="font-size:13px">→ {escape(r["summary"])}</div>' if r["summary"] else "")
                + f'<div class="muted" style="font-size:12px">{r["stage"]}/6 {escape(r["stage_label"] or "-")} · 발의 {escape(r["started"] or "-")} · ' + _legis_promul_text(pm) + '</div></div>')
    return ('<div class="card"><div class="row"><b>입법 중인 법안 (이 종목에 영향 가능)</b><a class="chip" href="/ideas?t=legis&f=1&c=' + quote(code) + '&ne=1&gr=1">모두 보기</a></div>'
            '<div class="muted" style="font-size:12px">법안 문구를 테마·업종·기업명에 이은 짐작이에요. 호재·악재 판단이 아니고 틀릴 수 있어요.</div>' + lis + '</div>')


def view_legis(conn, q):
    """아이디어 → 입법 영향 기업: 지금 입법 중인 법안이 영향을 줄 기업 후보와 공포 시기를 법안별·기업별로, 필터·정렬 포함"""
    import legis as lg
    import theme_map
    rows = lg.load_rows(conn)
    diag = lg.diagnose(conn)
    watch = lg.watch_list(conn)
    wcodes = frozenset(c for c, _ in watch)
    only_watch = q.get("w") == "1"
    mode = "stock" if q.get("m") == "stock" else "bill"
    submitted = "f" in q
    only_cand = (q.get("ne") == "1") if submitted else True
    only_text = q.get("tx") == "1"
    group_on = (q.get("gr") == "1") if submitted else True
    st = q.get("st") if q.get("st") in dict(LEGIS_STAGE_FILTERS) else ""
    days = q.get("d") if q.get("d") in ("90", "180", "365", "0") else "365"
    cpt = q.get("cp", "")
    grp = (q.get("g") or "").strip()
    comp = (q.get("c") or "").strip()
    kw = (q.get("kw") or "").strip()
    sorts = dict(LEGIS_SORTS[mode])
    sort = q.get("s") if q.get("s") in sorts else LEGIS_SORTS[mode][0][0]
    try:
        page_no = int(q.get("p", "0"))
    except ValueError:
        page_no = 0
    page_no = max(0, page_no)
    names = {}
    mcap = {}
    try:
        mcap = {c: m for c, m in conn.execute("SELECT code, mktcap FROM krx_daily WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_daily)")}
    except sqlite3.OperationalError:
        pass
    total_all = len(rows)
    n_cand = sum(1 for r in rows if r["n"])
    n_text = sum(1 for r in rows if r["has_text"])
    concept_count = {}
    for r in rows:
        for c in r["concepts"]:
            concept_count[c] = concept_count.get(c, 0) + 1
    # ---- 필터
    cut = (dt.date.today() - dt.timedelta(days=int(days))).isoformat() if days != "0" else ""
    sel = []
    for r in rows:
        if cut and (r["started"] or r["date"]) < cut:
            continue
        pk = r["promul"]["kind"]
        if st == "early" and not (r["kind"] == "심사 중" and r["stage"] <= 4):
            continue
        if st == "law" and not (r["kind"] == "심사 중" and r["stage"] == 5):
            continue
        if st == "pass" and not (r["kind"] == "통과" and pk != "done"):
            continue
        if st == "done" and pk != "done":
            continue
        if st == "eff" and not (pk == "done" and r["promul"]["effective"] and r["promul"]["effective"] >= dt.date.today().isoformat()):
            continue
        if only_cand and not r["n"]:
            continue
        if only_watch and not lg.watch_hits(r, wcodes):
            continue
        if only_text and not r["has_text"]:
            continue
        if cpt and cpt not in r["concepts"]:
            continue
        if kw and kw not in r["title"]:
            continue
        if grp and not any(grp in g[1] for g in r["groups"]):
            continue
        if comp and not any(comp.replace(" ", "") in (s[1] or "").replace(" ", "") or comp == s[0] for s in r["stocks"]):
            continue
        sel.append(r)
    # ---- 화면 위쪽: 설명·요약·필터
    def select(name, opts, cur):
        return f'<select name="{name}">' + "".join(f'<option value="{escape(v)}"{" selected" if v == cur else ""}>{escape(l)}</option>' for v, l in opts) + '</select>'
    cp_opts = [("", "분야 전체")] + [(c, f"{c} ({n})") for c, n in sorted(concept_count.items(), key=lambda x: -x[1])]
    form = ('<form method="get" action="/ideas" class="filters" style="margin:8px 0 0;flex-wrap:wrap"><input type="hidden" name="t" value="legis"><input type="hidden" name="f" value="1">'
            f'<input type="hidden" name="m" value="{mode}">'
            + select("st", LEGIS_STAGE_FILTERS, st)
            + select("d", (("90", "발의 90일 내"), ("180", "발의 180일 내"), ("365", "발의 1년 내"), ("0", "전체 기간")), days)
            + select("cp", cp_opts, cpt)
            + f'<input type="text" name="g" value="{escape(grp)}" placeholder="테마·업종 (예: 면세점)" style="flex:1 1 130px">'
            + f'<input type="text" name="c" value="{escape(comp)}" placeholder="기업 (이름·코드)" style="flex:1 1 120px">'
            + f'<input type="text" name="kw" value="{escape(kw)}" placeholder="법안 제목" style="flex:1 1 120px">'
            + select("s", LEGIS_SORTS[mode], sort)
            + f'<label style="white-space:nowrap"><input type="checkbox" name="ne" value="1"{" checked" if only_cand else ""}> 후보 있는 것만</label>'
            + f'<label style="white-space:nowrap"><input type="checkbox" name="tx" value="1"{" checked" if only_text else ""}> 본문 읽은 것만</label>'
            + f'<label style="white-space:nowrap"><input type="checkbox" name="gr" value="1"{" checked" if group_on else ""}> 비슷한 법안 묶기</label>'
            + f'<label style="white-space:nowrap"><input type="checkbox" name="w" value="1"{" checked" if only_watch else ""}> ★ 관심 종목 관련만</label>'
            '<button>적용</button></form>')
    base = {"t": "legis", "f": "1", "st": st, "d": days, "cp": cpt, "g": grp, "c": comp, "kw": kw, "ne": "1" if only_cand else "", "tx": "1" if only_text else "", "gr": "1" if group_on else "", "w": "1" if only_watch else ""}
    mode_tabs = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{lab}</b>' if k == mode else
         f'<a class="chip" href="/ideas?{urlencode({**base, "m": k})}">{lab}</a>') for k, lab in (("bill", "법안별로 보기"), ("stock", "기업별로 보기")))
    warn, running = legis_prep_card(conn, diag, total_all, n_cand)
    if q.get("msg"):
        warn = f'<div class="card {"warn" if q["msg"].startswith("✖") else ""}">{escape(q["msg"])}</div>' + warn
    head = ('<div class="card"><b>입법 영향 기업</b>'
            '<div class="muted">지금 국회에서 입법 중인 법안(심사 중 + 통과했지만 공포 전)이 어떤 기업에 닿을 수 있는지와 <b>공포 시기</b>를 한 화면에서 봐요. '
            '법안을 하나씩 열지 않아도 수집 때 미리 계산해 둔 결과가 나와요. 기업 후보는 법안 문구를 테마·업종·기업명에 이은 짐작이고 호재·악재는 판단하지 않아요. '
            '공포 \'예상\'은 일정을 보장하지 않아요 (통과 후 정부 이송 15일 이내 공포, 대통령 재의요구 시 달라짐).</div>'
            f'<div class="muted" style="margin-top:4px">입법 중 {total_all}건 · 영향 기업 후보 있음 {n_cand}건 · 제안이유 본문을 읽음 {n_text}건 · 지금 조건에 맞는 법안 {len(sel)}건</div>'
            f'<div style="margin-top:6px">{mode_tabs}</div>{form}</div>')
    per = 25

    def pager(total):
        if total <= per:
            return ""
        b = {**base, "m": mode, "s": sort}
        prev = f'<a class="chip" href="/ideas?{urlencode({**b, "p": page_no - 1})}">← 이전</a> ' if page_no > 0 else ""
        nxt = f'<a class="chip" href="/ideas?{urlencode({**b, "p": page_no + 1})}">다음 →</a>' if (page_no + 1) * per < total else ""
        return f'<div style="margin-top:8px">{prev}<span class="muted">{page_no * per + 1}~{min(total, (page_no + 1) * per)} / {total}</span> {nxt}</div>'

    def prog(r):
        dots = "".join(f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:2px;background:{"var(--accent)" if i <= r["stage"] else "#8884"}"></span>' for i in range(1, 7))
        return dots
    key_fn, rev = _legis_sort_key(sort) if mode == "bill" else (None, True)
    if mode == "bill":
        groups = lg.group_rows(sel) if group_on else [dict(rep=r, items=[r], n=1) for r in sel]

        def sort_row(g):          # 묶음 전체를 대표하는 값으로 정렬 (최근 단계는 가장 최근, 단계는 가장 앞선 것, 공포는 가장 빠른 것)
            its = g["items"]
            pr = min((x for x in its if x["promul"]["kind"] in ("done", "est") and x["promul"]["date"]), key=lambda x: x["promul"]["date"], default=g["rep"])
            wsc = max((h[2] for x in its for h in lg.watch_hits(x, wcodes)), default=0.0)
            return {"last": max((x["last"] or "") for x in its), "started": max((x["started"] or "") for x in its), "n": g["rep"]["n"],
                    "stage": max(x["stage"] for x in its), "promul": pr["promul"], "watch": wsc}
        groups.sort(key=lambda g: key_fn(sort_row(g)), reverse=rev)
        shown = groups[page_no * per: (page_no + 1) * per]
        cards = ""
        for g in shown:
            r = g["rep"]
            sub = ""
            if g["n"] > 1:
                inner = "".join(_legis_card(x, prog, sub=True) for x in g["items"][1:])
                sub = (f'<details style="margin-top:6px"><summary class="muted" style="font-size:13px">같은 법·분야의 비슷한 개정안 {g["n"] - 1}건 더 보기</summary>{inner}</details>')
            cards += f'<div style="margin:12px 0;padding-bottom:10px;border-bottom:1px solid #8883">{_legis_card(r, prog, wcodes=wcodes)}{sub}</div>'
        body = f'<div class="card">{cards or "<div class=muted>조건에 맞는 법안이 없어요. 위 필터를 풀거나 \'후보 있는 것만\'을 꺼 보세요.</div>"}{pager(len(groups))}</div>'
        return page("입법 영향 기업", legis_watch_card(conn, watch, rows) + head + warn + body, refresh=running)
    # ---- 기업별
    agg = {}
    for r in sel:
        extra = [h for h in lg.watch_hits(r, wcodes) if h[0] not in {x[0] for x in r["stocks"]}] if only_watch else []
        for code, nm, sc, ev in list(r["stocks"]) + extra:
            if comp and not (comp.replace(" ", "") in (nm or "").replace(" ", "") or comp == code):
                continue
            if only_watch and code not in wcodes:
                continue
            a = agg.setdefault(code, dict(code=code, name=nm, bills=[], gset=set(), stage=0, soon="", score=0.0))
            a["bills"].append((r, sc, ev))
            a["gset"].add(lg.group_key(r) if group_on else r["key"])
            a["stage"] = max(a["stage"], r["stage"])
            a["score"] += sc
            pm = r["promul"]
            if pm["kind"] in ("done", "est") and pm["date"] and (not a["soon"] or pm["date"] < a["soon"]):
                a["soon"] = pm["date"]
    items = list(agg.values())
    far = "9999-12-31"
    keyf = {"cnt": (lambda a: (len(a["gset"]), a["score"]), True), "soon": (lambda a: a["soon"] or far, False),
            "cap": (lambda a: mcap.get(a["code"]) or 0, True), "stage": (lambda a: (a["stage"], len(a["bills"])), True)}[sort]
    items.sort(key=keyf[0], reverse=keyf[1])
    shown = items[page_no * per: (page_no + 1) * per]
    trs = ""
    for a in shown:
        bl = sorted(a["bills"], key=lambda x: -x[1])
        lis = "".join(f'<div style="font-size:13px"><a href="/bills?t=bill&key={quote(r["key"])}">{escape(r["name"][:44])}</a> '
                      f'<span class="muted">{r["stage"]}/6 · {_legis_promul_text(r["promul"]) if r["promul"]["kind"] in ("done", "est") else "공포 미정"}</span></div>' for r, _, _ in bl[:3])
        more = f'<div class="muted" style="font-size:12px">… 외 {len(bl) - 3}건</div>' if len(bl) > 3 else ""
        soon = f'{a["soon"][5:]}' if a["soon"] else "-"
        trs += (f'<tr><td><a href="{naver_link(a["code"])}" target="_blank" rel="noopener noreferrer"><b>{"★ " if a["code"] in wcodes else ""}{escape(a["name"])}</b></a><br><span class="muted" style="font-size:11px">{escape(a["code"])} · 시총 {_mcap_text(mcap.get(a["code"]))}</span></td>'
                f'<td><b>{len(a["gset"])}</b>' + (f'<br><span class="muted" style="font-size:11px">개정안 {len(a["bills"])}건</span>' if len(a["bills"]) != len(a["gset"]) else "") + f'</td><td>{a["stage"]}/6</td><td>{soon}</td><td>{lis}{more}</td></tr>')
    body = ('<div class="card"><div class="muted" style="margin-bottom:4px">같은 기업이 여러 법안에 걸쳐 있으면 한 줄로 묶어 보여 줘요. 공포 열은 그 기업에 닿는 법안 중 가장 빠른 확정·예상 공포일이에요.</div>'
            + (f'<div class="scroll"><table><tr><th>기업</th><th>법안 수</th><th>최고 단계</th><th>가장 빠른 공포</th><th>관련 법안 (영향 점수 높은 순)</th></tr>{trs}</table></div>' if trs else
               '<div class="muted">조건에 맞는 기업이 없어요. 필터를 풀어 보세요.</div>') + pager(len(items)) + '</div>')
    return page("입법 영향 기업", legis_watch_card(conn, watch, rows) + head + warn + body, refresh=running)


def _path_svg(path, w=150, h=40):
    """평균 초과수익 경로(D-20…D+20, %) 작은 그림: 점선=D0(단계 날짜), 가는 선=0%"""
    if not path or len(path) < 3:
        return ""
    lo, hi = min(path + [0.0]), max(path + [0.0])
    rg = (hi - lo) or 1.0
    y = lambda v: h - 3 - (v - lo) / rg * (h - 6)
    n = len(path) - 1
    pts = " ".join(f"{1 + i * (w - 2) / n:.1f},{y(v):.1f}" for i, v in enumerate(path))
    d0 = 1 + 20 * (w - 2) / 40
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="vertical-align:middle" role="img" aria-label="D-20부터 D+20까지 평균 초과수익 경로">'
            f'<line x1="1" x2="{w - 1}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="currentColor" stroke-opacity=".3"/>'
            f'<line x1="{d0:.1f}" x2="{d0:.1f}" y1="0" y2="{h}" stroke="currentColor" stroke-opacity=".5" stroke-dasharray="3 2"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="1.6"/></svg>')


def _p_text(p):
    if p is None:
        return "-"
    return "&lt;0.01" if p < 0.01 else f"{p:.2f}"


def _study_verdict(p):
    """(문구, 칩 클래스): 표본이 충분(날짜 30개 이상)하고 우연(같은 종목의 무작위 날짜)과 확실히 다를 때(p<0.01)만 '우연 밖'. 여러 묶음을 동시에 보니 기준을 엄격히."""
    if p["n_dates"] < 30:
        return "표본 적음", ""
    bits, cls = [], ""
    # 우연 대조군(p<0.01)과 날짜 군집 t값(|t|≥3)이 둘 다 동의할 때만 (독립된 두 방법이 모두 같은 말을 해야 표시)
    if p.get("pre_p") is not None and p["pre_p"] < 0.01 and p.get("pre") is not None and abs(p.get("pre_t") or 0) >= 3:
        bits.append("사전 " + ("상승" if p["pre"] > 0 else "하락"))
    if p.get("post10_p") is not None and p["post10_p"] < 0.01 and p.get("post10") is not None and abs(p.get("post10_t") or 0) >= 3:
        bits.append("사후 " + ("상승" if p["post10"] > 0 else "하락"))
        cls = "up" if p["post10"] > 0 else "down"
    if not bits:
        return "뚜렷한 패턴 없음", ""
    return " · ".join(bits) + " (우연 밖)", cls


def _study_row(label, p):
    v, cls = _study_verdict(p)
    pre = "-" if p["pre"] is None else f'{p["pre"]:+.1f}% <span class="muted" style="font-size:11px">(p {_p_text(p.get("pre_p"))})</span>'
    p10 = "-" if p["post10"] is None else f'{p["post10"]:+.1f}% <span class="muted" style="font-size:11px">(p {_p_text(p.get("post10_p"))})</span>'
    p20 = "-" if p["post20"] is None else f'{p["post20"]:+.1f}%'
    return (f'<tr><td>{escape(label)}</td><td>{p["n_events"]:,}<br><span class="muted" style="font-size:11px">날짜 {p["n_dates"]:,} · 종목 {p["n_stocks"]:,}</span></td>'
            f'<td>{_path_svg(p["path"])}</td><td>{pre}</td><td>{p10}</td><td>{p20}</td><td><span class="chip {cls}">{escape(v)}</span></td></tr>')


def _study_table(rows_html):
    return ('<div class="scroll"><table><tr><th>구분</th><th>이벤트</th><th>평균 초과수익 경로<br><span class="muted" style="font-size:11px">D-20…D+20, 점선=단계 날짜</span></th>'
            '<th>사전<br><span class="muted" style="font-size:11px">D-20→D-1</span></th><th>사후 10일<br><span class="muted" style="font-size:11px">D0→D+10</span></th>'
            f'<th>사후 20일</th><th>판정</th></tr>{rows_html}</table></div>')


def _amp_verdict(p):
    """움직임의 크기(방향 무시)가 평소(같은 종목의 무작위 날짜)보다 큰가: 표본 날짜 30개 이상, 우연 p<0.01, 배율 1.08 이상"""
    if p["n_dates"] < 30:
        return "표본 적음", ""
    r, ap = p.get("abs_ratio"), p.get("abs_p")
    if r is not None and ap is not None and ap < 0.01 and r >= 1.08:
        return f"평소보다 큰 움직임 ({r:.2f}배)", "warn"
    return "평소와 비슷", ""


def study_amp_card(res, meta):
    """입법 주가 분석: '평균은 작아 보여도 영향이 큰 경우' 구분 — ① 움직임의 크기(방향 무시) ② 방향별(수혜/부담) ③ 강하게 반응하는 조건(학습→검증) ④ 반응이 컸던 사례"""
    order = ["발의", "소관위 회부", "소관위 상정", "소관위 처리", "법사위 처리", "본회의 처리", "공포", "시행"]
    # ① 크기
    rows1 = ""
    for st_ in order:
        p = res.get(("단계", st_))
        if not p or p.get("abs_ratio") is None:
            continue
        v, cls = _amp_verdict(p)
        small_mean = p.get("post10") is not None and abs(p["post10"]) < 1.0
        hint = '<br><span class="muted" style="font-size:11px">평균은 작은데 크기는 큼 → 방향이 갈리거나 일부만 크게 움직여요</span>' if (cls == "warn" and small_mean) else ""
        tail = "-" if p.get("tail") is None else f'{p["tail"]:.0%} <span class="muted" style="font-size:11px">(평소 {p["tail_exp"]:.0%}, p {_p_text(p.get("tail_p"))})</span>'
        rows1 += (f'<tr><td>{escape(st_)}</td><td>{p["n_events"]:,}<br><span class="muted" style="font-size:11px">날짜 {p["n_dates"]:,}</span></td>'
                  f'<td>{"-" if p.get("post10") is None else format(p["post10"], "+.1f") + "%"}</td>'
                  f'<td><b>{p["abs_mean"]:.1f}%</b> <span class="muted" style="font-size:11px">(평소 {p["abs_base"]:.1f}%)</span></td>'
                  f'<td>{p["abs_ratio"]:.2f}배 <span class="muted" style="font-size:11px">(p {_p_text(p.get("abs_p"))})</span></td><td>{tail}</td>'
                  f'<td><span class="chip {cls}">{escape(v)}</span>{hint}</td></tr>')
    t1 = ('<div class="scroll"><table><tr><th>단계</th><th>이벤트</th><th>평균<br><span class="muted" style="font-size:11px">사후 10일(방향 포함)</span></th>'
          '<th>움직임의 크기<br><span class="muted" style="font-size:11px">|사후 10일| 평균</span></th><th>평소 대비</th>'
          f'<th>큰 움직임 비율<br><span class="muted" style="font-size:11px">평소 상위 10% 크기 이상</span></th><th>판정</th></tr>{rows1}</table></div>') if rows1 else ""
    # ② 방향별
    rows2 = ""
    cover = float(meta.get("sign_cover", 0) or 0)
    for st_ in order:
        a, b = res.get(("방향", f"{st_}|수혜 힌트")), res.get(("방향", f"{st_}|부담 힌트"))
        whole = res.get(("단계", st_))
        if not (a or b):
            continue
        cell = lambda p: ('<td class="muted">표본 부족</td>' if not p else
                          f'<td>{p["post10"]:+.1f}%<br><span class="muted" style="font-size:11px">이벤트 {p["n_events"]:,} · p {_p_text(p.get("post10_p"))}</span></td>')
        al = ""
        if whole and whole.get("al_post10") is not None:
            al = f'{whole["al_post10"]:+.1f}% <span class="muted" style="font-size:11px">(p {_p_text(whole.get("al_p"))}, 방향 있는 {whole.get("al_n", 0):,}건)</span>'
        split = (a and b and a["post10"] is not None and b["post10"] is not None and a["post10"] > 1 and b["post10"] < -1
                 and (a.get("post10_p") or 1) < 0.05 and (b.get("post10_p") or 1) < 0.05)
        verdict = '<span class="chip warn">방향이 갈려 평균에서 상쇄</span>' if split else '<span class="chip">-</span>'
        rows2 += f'<tr><td>{escape(st_)}</td>{cell(a)}{cell(b)}<td>{al or "-"}</td><td>{verdict}</td></tr>'
    t2 = ('<div class="scroll"><table><tr><th>단계</th><th>수혜 힌트 법안</th><th>부담 힌트 법안</th><th>방향 보정 평균<br><span class="muted" style="font-size:11px">부담은 부호를 뒤집어 합침</span></th><th>판정</th></tr>'
          f'{rows2}</table></div>') if rows2 else ""
    # ③ 강하게 반응하는 조건
    feat = {}
    for (g, k), p in res.items():
        if g == "특징":
            feat.setdefault(p["set"], []).append(p)
    t3 = ""
    set_label = {"발의": "발의 단계", "심사": "심사 단계 (소관위 회부·상정·처리, 법사위)", "확정": "확정 단계 (본회의·공포·시행)"}
    for sname in ("발의", "심사", "확정"):
        ps = feat.get(sname)
        if not ps:
            continue
        base = next((p for p in ps if p["status"] == "기준"), None)
        good = sorted([p for p in ps if p["status"] == "재현됨"], key=lambda p: -(p["test"]["ratio"] or 0))
        part = sorted([p for p in ps if p["status"] == "학습에서만"], key=lambda p: -(p["train"]["ratio"] or 0))[:5]
        fmt = lambda d: ("-" if d["ratio"] is None else f'{d["ratio"]:.2f}배 <span class="muted" style="font-size:11px">(p {_p_text(d.get("p"))}, 날짜 {d["n_dates"]})</span>')
        trs = ""
        for p in good + part:
            chip = '<span class="chip up">재현됨</span>' if p["status"] == "재현됨" else '<span class="chip">학습에서만 (검증에서 안 맞음)</span>'
            trs += f'<tr><td>{escape(p["feature"])}: <b>{escape(p["bucket"])}</b></td><td>{fmt(p["train"])}</td><td>{fmt(p["test"])}</td><td>{chip}</td></tr>'
        head_base = ""
        if base:
            head_base = (f'<div class="muted" style="font-size:12px">이 단계 묶음 전체의 평소 대비 크기: 학습 {fmt(base["train"])} · 검증 {fmt(base["test"])} '
                         f'(학습/검증은 {escape(base.get("split", ""))}을 경계로 앞/뒤 기간)</div>')
        if trs:
            body = f'<div class="scroll"><table><tr><th>조건</th><th>학습 기간<br><span class="muted" style="font-size:11px">평소 대비 크기</span></th><th>검증 기간</th><th>판정</th></tr>{trs}</table></div>'
        else:
            body = '<div class="muted" style="margin-top:4px">재현된 조건이 없어요 — 구분되는 조건이 없거나, 표본이 작아 구분하기 어렵다는 뜻이에요.</div>'
        t3 += f'<div style="margin-top:10px"><b style="font-size:13px">{escape(set_label[sname])}</b>{head_base}{body}</div>'
    # ④ 사례
    cs = res.get(("사례", "top"))
    t4 = ""
    if cs and cs.get("cases"):
        trs = ""
        for c in cs["cases"][:20]:
            same = f'{c["same_move"]}/{c["same_n"]}' if c["same_n"] else "-"
            sg = {1: "수혜", -1: "부담", 0: "-"}.get(c.get("sign", 0), "-")
            trs += (f'<tr><td>{escape(c["name"])}<br><span class="muted" style="font-size:11px">{escape(c["code"])}</span></td><td>{escape(c["stage"])}<br><span class="muted" style="font-size:11px">{escape(c["date"])}</span></td>'
                    f'<td style="font-size:13px">{escape(c["bill"][:44])}<br><span class="muted" style="font-size:11px">{escape(c["tier"])} · {sg}</span></td>'
                    f'<td>{c["pre"]:+.1f}%</td><td><b class="{"up" if c["post10"] > 0 else "down"}">{c["post10"]:+.1f}%</b></td><td>{same}</td></tr>')
        ref = f' 같은 규모의 이벤트를 <b>무작위 날짜</b>에서 뽑아도 상위 30개의 하한이 평균 {cs["ref30"]:.1f}% 나와요.' if cs.get("ref30") is not None else ""
        t4 = ('<div style="margin-top:12px"><b style="font-size:13px">반응이 컸던 사례 (지수 대비 사후 10일 상위 20건)</b>'
              f'<div class="muted" style="font-size:12px">⚠ 결과를 보고 고른 사례라서 법안 때문이라는 증거가 아니에요.{ref} 마지막 열은 같은 날 다른 이벤트 중 같은 방향으로 3% 넘게 움직인 수예요 — 많으면 시장·업종 전체 움직임일 가능성이 커요.</div>'
              f'<div class="scroll"><table><tr><th>종목</th><th>단계</th><th>법안</th><th>사전</th><th>사후 10일</th><th>같은 날 동반</th></tr>{trs}</table></div></div>')
    if not (t1 or t2 or t3 or t4):
        return ""
    cov = (f'<div class="muted" style="font-size:12px;margin-top:4px">방향(수혜/부담) 판정은 제안이유 본문이 있으면 본문 단어로, 없으면 제목 단어로 해서 거칠어요. 지금 이벤트의 {cover:.0%}만 방향이 정해졌어요 '
           '(나머지는 방향 모름으로 두고 방향 분석에서 뺐어요).</div>') if t2 else ""
    return ('<div class="card"><b>평균은 작아 보여도 영향이 큰 경우 구분하기</b>'
            '<div class="muted" style="font-size:13px;margin:2px 0 6px">평균은 두 가지 이유로 영향을 가려요. ① 수혜 법안은 오르고 부담 법안은 내려 <b>방향이 상쇄</b>되거나, ② 대부분은 0 근처인데 <b>일부만 크게</b> 움직여 평균에 묻혀요. '
            '그래서 방향을 무시한 <b>크기</b>, 방향을 맞춘 <b>방향별</b> 평균, 그리고 크게 움직이는 <b>조건</b>을 따로 봐요.</div>'
            + (f'<div style="margin-top:8px"><b style="font-size:13px">① 움직임의 크기 (방향 무시)</b>{t1}</div>' if t1 else "")
            + (f'<div style="margin-top:12px"><b style="font-size:13px">② 방향별 (수혜 힌트 / 부담 힌트)</b>{t2}{cov}</div>' if t2 else "")
            + (f'<div style="margin-top:12px"><b style="font-size:13px">③ 강하게 반응하는 조건 — 학습 기간에서 찾고 검증 기간에서 재현될 때만 인정</b>'
               '<div class="muted" style="font-size:12px">조건: 연결 확실도 · 점수 · 소속 테마 수 · 거래대금 규모 · 사전 거래량 급증 · 공동발의 인원 · 법안 성격 (한 가지 또는 두 가지 조합). '
               '이벤트 날짜의 앞 60%에서 찾고 뒤 40%에서 같은 조건이 맞는지 확인해요. 효과가 뚜렷하면(평소의 약 1.3배↑) 거의 찾지만 약하면(약 1.2배) 절반쯤만 찾아요 — '
               '못 찾았다고 없다는 뜻은 아니에요.</div>' + t3 + '</div>' if t3 else "")
            + t4 + '</div>')


def _ev_react_table(rs, mc=None):
    """종목별 반응 표 + 평균 줄"""
    import event_themes as et
    f = lambda v: '<td class="muted">-</td>' if v is None else f'<td class="{"up" if v > 0 else "down" if v < 0 else ""}">{v:+.1f}</td>'
    trs = ""
    for x in rs:
        r = x["r"]
        if not x["has_prices"]:
            trs += f'<tr><td>{escape(x["name"])}</td><td>{_mcap_text((mc or {}).get(x["code"]))}</td><td colspan="5" class="muted">주가를 아직 못 받았어요</td></tr>'
        elif not r:
            trs += f'<tr><td>{escape(x["name"])}</td><td>{_mcap_text((mc or {}).get(x["code"]))}</td><td colspan="5" class="muted">이 날짜 전후 주가가 부족해요</td></tr>'
        else:
            trs += f'<tr><td><a href="{naver_link(x["code"])}" target="_blank" rel="noopener noreferrer">{escape(x["name"])}</a></td><td>{_mcap_text((mc or {}).get(x["code"]))}</td>{f(r.get("ab5pre"))}{f(r.get("ab1"))}{f(r.get("ab5"))}{f(r.get("ab20"))}{f(r.get("since"))}</tr>'
    sm = et.summarize(rs)
    if sm:
        cells = "".join((f'<td><b>{sm[k]["mean"]:+.1f}</b><br><span class="muted" style="font-size:11px">중앙 {sm[k]["med"]:+.1f} · 플러스 {sm[k]["up"]:.0%}</span></td>' if k in sm else '<td class="muted">-</td>')
                        for k in ("ab5pre", "ab1", "ab5", "ab20", "since"))
        trs += f'<tr style="background:var(--chip)"><td><b>평균 ({max(v["n"] for v in sm.values())}종목)</b></td><td></td>{cells}</tr>'
    return ('<div class="scroll"><table><tr><th>종목</th><th>시총</th><th>사전 5일</th><th>이후 1일</th><th>5일</th><th>20일</th><th>현재까지</th></tr>' + trs + '</table></div>'
            '<div class="muted" style="font-size:11px">지수 대비 초과수익(%p). 기준은 이벤트 직전 종가, 사전은 이벤트 5거래일 전부터.</div>')


def _fold(card_html, open_=False):
    """'<div class="card"><b>제목</b>…</div>' → 제목을 눌러 접고 펼 수 있는 카드"""
    m = re.match(r'<div class="card"><b>(.*?)</b>(.*)</div>\Z', card_html, re.S)
    if not m:
        return card_html
    return (f'<div class="card"><details{" open" if open_ else ""}><summary style="cursor:pointer"><b>{m.group(1)}</b></summary>{m.group(2)}</details></div>')


def _ev_chip(t, with_link=True):
    import event_themes as et
    ev = et.EVENT_BY_ID[t["event_id"]]
    arrow = {"+": "▲", "-": "▼", "±": "▲▼"}[t["sign"]]
    cls = "up" if t["sign"] == "+" else "down" if t["sign"] == "-" else ""
    sub = ' <span class="muted" style="font-size:10px">간접</span>' if t["role"] == "sub" else ""
    return (f'<a class="chip {cls}" style="text-decoration:none;display:inline-block;margin:1px" href="/ideas?t=events&ev={ev["id"]}" '
            f'title="{escape(ev["why"])}">{arrow} {escape(ev["name"].split(" (")[0])}{sub}</a>')


def view_events(conn, q):
    """아이디어 → 이벤트 테마: 테마주별로 '어떤 이벤트가 생기면 뜨는지', 이벤트별 테마·종목, 날짜별 주가 반응 기록"""
    import event_themes as et
    import theme_map
    data = theme_map.load(conn)
    mc = et.mcaps(conn)
    nm = data.get("이름", {})
    msg = q.get("msg", "")
    flash = f'<div class="card {"warn" if msg.startswith("✖") else ""}">{escape(msg)}</div>' if msg else ""
    intro = ('<div class="card"><b>이벤트 테마</b><div class="muted" style="font-size:13px"><b>테마주별로 어떤 이벤트가 생기면 뜨는지</b>(▲ 오르는 쪽 · ▼ 내리는 쪽 · ▲▼ 방향이 갈림)를 정리했어요. '
             '이벤트 이름을 누르면 그 이벤트에 반응하는 테마와 종목(시가총액 순)이 나와요. 연결은 사람이 쓴 가설이고, 진짜 반응은 맨 아래 <b>이벤트 기록</b>에 날짜를 넣어 직접 확인해요 '
             '(선반영·같은 날 다른 소식·시장 전체 움직임이 섞여요). 투자 권유가 아니에요.</div></div>')
    if not data["테마"]:
        return page("이벤트 테마", flash + intro + '<div class="card muted">테마 자료가 아직 없어요. 설정에서 테마 받기를 먼저 해 주세요.</div>')

    def stocks_html(codes, n=8):
        top = sorted(codes, key=lambda c: -(mc.get(c) or 0))[:n]
        return " · ".join(f'<a href="{naver_link(c)}" target="_blank" rel="noopener noreferrer">{escape(nm.get(c, c))}</a>' + (f' <span class="muted" style="font-size:11px">{_mcap_text(mc.get(c))}</span>' if mc.get(c) else "") for c in top)
    import datetime as _dt0

    # ---- 앞으로 계획된 이벤트
    import datetime as _dt1
    today = _dt1.date.today()
    law_rows = []
    try:
        import legis as _lg
        for r in _lg.load_rows(conn, live_limit=0):
            pm = r.get("promul") or {}
            if pm.get("kind") == "done" and pm.get("effective") and today.isoformat() <= pm["effective"] <= (today + _dt1.timedelta(days=150)).isoformat():
                gnames = [g[1] for g in (r.get("groups") or [])][:4]
                law_rows.append(dict(d=pm["effective"], end="", title="법 시행: " + r["name"][:48], kind="법 시행", events=[], src="", note="공포 " + (pm.get("date") or ""), themes=gnames))
    except Exception:
        law_rows = []
    udays = {"60": 60, "150": 150, "365": 365}.get(q.get("ud", ""), 150)
    show_mkt = q.get("um") == "1"
    ups = et.upcoming(conn, today, udays, law_rows)

    def theme_rows(ev_, top_n=6):
        out_ = {"+": [], "-": [], "±": []}
        for k_, n_, r_, c_, sg_ in sorted(et.groups(ev_, data), key=lambda g: (0 if g[2] == "main" else 1, -len(g[3]))):
            if r_ == "main" and len(out_[sg_]) < top_n:
                out_[sg_].append((n_, c_))
        return out_

    def theme_line(sg, items):
        if not items:
            return ""
        lab = {"+": "▲ 오를 수 있는 테마", "-": "▼ 내릴 수 있는 테마", "±": "▲▼ 방향이 갈리는 테마"}[sg]
        cls = "up" if sg == "+" else "down" if sg == "-" else ""
        lis = "".join(f'<div style="margin:3px 0"><span class="chip {cls}">{escape(n_)}</span> <span class="muted" style="font-size:11px">{len(c_)}종목</span> '
                      f'<span style="font-size:12px">{stocks_html(c_, 5)}</span></div>' for n_, c_ in items)
        return f'<div style="margin-top:4px"><b style="font-size:12px">{lab}</b>{lis}</div>'
    ucards, n_up, n_hid = "", 0, 0
    for u in ups:
        evs_ = [et.EVENT_BY_ID[e_] for e_ in u["events"] if e_ in et.EVENT_BY_ID]
        blocks = ""
        for ev_ in evs_:
            tr_ = theme_rows(ev_)
            if any(tr_.values()):
                blocks += (f'<details style="margin-top:6px"{" open" if len(evs_) == 1 else ""}><summary style="cursor:pointer"><b>{"결과가 이렇다면 — " if len(evs_) > 1 else ""}{escape(ev_["name"])}</b>'
                           f'<span class="muted" style="font-size:12px"> — {escape(ev_["why"])}</span></summary>'
                           + theme_line("+", tr_["+"]) + theme_line("±", tr_["±"]) + theme_line("-", tr_["-"])
                           + f'<div class="muted" style="font-size:11px;margin-top:2px">⚠ {escape(ev_["note"])} <a href="/ideas?t=events&ev={ev_["id"]}">이 이벤트 전체 보기</a></div></details>')
        if u.get("themes"):
            blocks += '<div style="margin-top:6px"><b style="font-size:12px">이 법과 닿는 테마·업종</b> ' + " ".join(f'<span class="chip">{escape(n_)}</span>' for n_ in u["themes"]) + '</div>'
        dd = (_dt1.date.fromisoformat(u["d"]) - today).days
        when = u["d"] + (f" ~ {u['end'][5:]}" if u.get("end") else "")
        srcl = f' · <a href="{escape(u["src"])}" target="_blank" rel="noopener noreferrer">출처</a>' if u.get("src") else ""
        head = (f'<b>{"오늘" if dd == 0 else f"D-{dd}"}</b> · {escape(when)} · <b>{escape(u["title"])}</b> <span class="chip">{escape(u["kind"])}</span>'
                f'<span class="muted" style="font-size:11px"> {escape(u.get("note") or "")}{srcl}</span>')
        if not blocks:
            if show_mkt:
                ucards += f'<div style="padding:5px 0;border-top:1px solid var(--line);font-size:13px">{head} <span class="muted" style="font-size:11px">· 시장 전체에 영향 (테마 직접 연결 없음)</span></div>'
            else:
                n_hid += 1
            continue
        n_up += 1
        ucards += (f'<details{" open" if n_up <= 1 else ""} style="border-top:1px solid var(--line);padding:6px 0"><summary style="cursor:pointer">{head}</summary>{blocks}</details>')
    sel_ud = "".join(f'<option value="{v}"{" selected" if v == str(udays) else ""}>{lab}</option>' for v, lab in (("60", "60일 안"), ("150", "150일 안"), ("365", "1년 안")))
    uflt = ('<form method="get" action="/ideas" class="filters" style="margin:6px 0"><input type="hidden" name="t" value="events">'
            f'<select name="ud">{sel_ud}</select> <label style="font-size:13px"><input type="checkbox" name="um" value="1"{" checked" if show_mkt else ""}> 시장 전체 일정(만기일 등)도 보기</label><button>보기</button></form>')
    upcard = ('<div class="card"><b>이벤트 일정 — 앞으로 계획된 이벤트와 그때 뜰 수 있는 테마</b>'
              '<div class="muted" style="font-size:12px">이미 공표된 일정, 매월 반복되는 일정(계산), 시행이 정해진 법, 직접 넣은 예정 이벤트를 날짜순으로 모았어요. 이벤트마다 그 종류에 반응한다고 사전에 적힌 테마와 대표 종목(시가총액 순)을 붙였어요. '
              '날짜는 출처 기준이라 바뀔 수 있고, 테마의 방향과 크기는 이벤트 결과에 달려 있어요.</div>' + uflt
              + (ucards if ucards else '<div class="muted" style="margin-top:6px">이 기간에 알려진 일정이 없어요. 아래 “이벤트 기록·예정 추가”로 직접 넣을 수 있어요.</div>')
              + (f'<div class="muted" style="font-size:12px;margin-top:6px">테마 직접 연결이 없는 시장 일정 {n_hid}건은 숨겼어요 (위 체크박스로 보기).</div>' if n_hid else "")
              + '</div>')
    # ---- 이벤트 하나 보기
    evcard = ""
    sel = et.EVENT_BY_ID.get(q.get("ev", ""))
    if sel:
        gs = et.groups(sel, data)
        rows = "".join(f'<tr><td><b>{escape(n)}</b></td><td>{"▲" if sg == "+" else "▼" if sg == "-" else "▲▼"}{" (간접)" if r == "sub" else ""}</td><td>{len(c)}</td><td style="font-size:12px">{stocks_html(c)}</td></tr>'
                       for k, n, r, c, sg in sorted(gs, key=lambda g: (0 if g[2] == "main" else 1, -len(g[3]))))
        mem = et.members(sel, data, top=15, mcap=mc)
        evcard = (f'<div class="card"><b>{escape(sel["name"])}</b> <span class="chip">{escape(sel["cat"])}</span> <a href="/ideas?t=events" class="muted" style="font-size:12px">닫기</a>'
                  f'<div style="font-size:13px;margin:4px 0">{escape(sel["why"])}</div><div class="muted" style="font-size:12px">⚠ {escape(sel["note"])}</div>'
                  + (f'<div class="scroll"><table><tr><th>테마</th><th>방향</th><th>종목 수</th><th>대표 종목 (시총 순)</th></tr>{rows}</table></div>' if rows else '<div class="muted">지금 네이버 테마 목록에 맞는 테마가 없어요.</div>')
                  + (f'<div style="font-size:13px;margin-top:6px"><b>{"시가총액 큰 순" if mc else "점수 순"} 종목</b> ' + stocks_html([m[0] for m in mem], 15) + '</div>' if mem else "")
                  + '<form method="post" action="/events/add" class="filters" style="margin-top:8px">'
                  f'<input type="hidden" name="event_id" value="{sel["id"]}"><input name="d" value="{_dt0.date.today().isoformat()}" size="10"> '
                  '<input name="title" placeholder="무슨 일인지 (제목)" size="34"><button>이 이벤트 기록하기</button></form></div>')
    # ---- 테마별 표
    kw = (q.get("th") or "").strip()
    ecat = q.get("ec") or ""
    trs, n_show, n_match = "", 0, 0
    for tname in sorted(data["테마"]):
        te = et.theme_events(tname)
        if not te:
            continue
        codes = data["테마"][tname]
        if kw and kw not in tname and not any(kw in nm.get(c, "") for c in codes):
            continue
        if ecat and not any(et.EVENT_BY_ID[t["event_id"]]["cat"] == ecat for t in te):
            continue
        n_match += 1
        if n_show >= 60 and q.get("all") != "1":
            continue
        n_show += 1
        trs += (f'<tr><td><b>{escape(tname)}</b><br><span class="muted" style="font-size:11px">{len(codes)}종목</span></td>'
                f'<td>{"".join(_ev_chip(t) for t in te[:7])}</td><td style="font-size:12px">{stocks_html(codes, 6)}</td></tr>')
    cat_opts = '<option value="">모든 분류</option>' + "".join(f'<option value="{escape(c)}"{" selected" if c == ecat else ""}>{escape(c)}</option>' for c in et.CATS)
    flt = ('<form method="get" action="/ideas" class="filters"><input type="hidden" name="t" value="events">'
           f'<input name="th" value="{escape(kw)}" placeholder="테마 이름 또는 종목" size="22"> <select name="ec">{cat_opts}</select><button>찾기</button></form>')
    more = (f'<div class="muted" style="margin-top:6px">{n_match}개 중 {n_show}개를 보여 줘요. <a href="/ideas?{urlencode({"t": "events", "th": kw, "ec": ecat, "all": "1"})}">전체 보기</a></div>'
            if n_match > n_show else f'<div class="muted" style="margin-top:6px">{n_match}개 테마</div>')
    table = ('<div class="card"><b>테마별로 보기 — 이런 이벤트가 생기면 뜬다(▲)</b>' + flt
             + (f'<div class="scroll"><table><tr><th>테마</th><th>반응하는 이벤트 (누르면 상세)</th><th>대표 종목{' (시총 순)' if mc else ''}</th></tr>{trs}</table></div>' if trs else '<div class="muted" style="margin-top:6px">조건에 맞는 테마가 없어요.</div>')
             + more + '</div>')
    # ---- 이벤트 기록
    opts = "".join(f'<option value="{e["id"]}">{escape(e["cat"])} · {escape(e["name"])}</option>' for e in et.EVENTS)
    import datetime as _dt
    addf = ('<div class="card"><b>이벤트 기록·예정 추가</b><form method="post" action="/events/add" class="filters" style="margin-top:6px">'
            f'<select name="event_id">{opts}</select> <input name="d" value="{_dt.date.today().isoformat()}" size="10" title="이벤트 날짜(장 마감 후 소식이면 다음 거래일)"> '
            '<input name="title" placeholder="제목 (무슨 일인지)" size="30"> <input name="note" placeholder="메모(선택)" size="24"><button>추가</button></form>'
            '<div class="muted" style="font-size:12px">지난 날짜면 주가 반응을, 앞으로의 날짜면 위 “앞으로 계획된 이벤트”에 올라가요. 날짜는 소식이 시장에 반영된 날(장 마감 뒤라면 다음 거래일)로 넣어요. 기준가는 그 날짜 직전 종가예요.</div>'
            + "".join(f'<form method="post" action="/events/add" style="display:inline"><input type="hidden" name="event_id" value="{a}"><input type="hidden" name="d" value="{d}">'
                      f'<input type="hidden" name="title" value="{escape(t)}"><input type="hidden" name="note" value="{escape(n)}"><button class="chip" style="cursor:pointer">+ {d} {escape(t)}</button></form> '
                      for a, d, t, n in et.SUGGEST) + '</div>')
    logs = et.log_rows(conn)
    logs_n = 0
    ordtxt = "시가총액 큰 순" if mc else "점수 순 (KRX 시세가 없어 시가총액은 못 썼어요)"
    body = ""
    for lr in logs:
        ev = et.EVENT_BY_ID.get(lr["event_id"])
        if not ev or lr["d"] > today.isoformat():
            continue
        mem = et.members(ev, data, top=12, mcap=mc)
        rs = et.reactions(conn, lr["d"], [(c, n) for c, n, _, _ in mem])
        need = [x["code"] for x in rs if not x["has_prices"]]
        pxb = ('<form method="post" action="/events/px" class="filters" style="margin:6px 0 0">'
               f'<input type="hidden" name="codes" value="{",".join(x["code"] for x in rs)}"><button>주가 받기 ({len(need)}종목 없음)</button></form>') if need else ""
        logs_n += 1
        body += (f'<div style="border-top:1px solid var(--line);padding:8px 0"><div style="display:flex;justify-content:space-between;gap:8px"><div><b>{escape(lr["d"])} · {escape(lr["title"])}</b> '
                 f'<span class="chip">{escape(ev["cat"])}</span>'
                 + (f'<div class="muted" style="font-size:12px">{escape(lr["note"])}</div>' if lr["note"] else "") +
                 f'</div><form method="post" action="/events/del"><input type="hidden" name="id" value="{lr["id"]}"><button class="chip" style="cursor:pointer">삭제</button></form></div>'
                 f'<div class="muted" style="font-size:12px;margin:4px 0">{ordtxt} 상위 {len(rs)}종목 (테마: {escape(", ".join(dict.fromkeys(w for m in mem for w in m[3][:1])))})</div>'
                 + _ev_react_table(rs, mc) + pxb + '</div>')
    if not logs:
        body = '<div class="card muted">지난 이벤트 기록이 아직 없어요. 위에서 이벤트와 날짜를 추가하면 연결 종목의 주가 반응이 나와요.</div>'
    logfold = _fold('<div class="card"><b>이벤트 기록과 주가 반응</b>' + body + '</div>') if logs_n else body
    return page("이벤트 테마", flash + _fold(intro) + _fold(upcard, True) + evcard + _fold(table) + _fold(addf) + logfold)


def view_cycle(conn, q):
    """아이디어 → 투자 사이클: 유명한 사이클 이론 정리 + 지금 자료로 보는 점검표 + 하락장에서 생각할 질문"""
    import cycle_theory as cy
    try:
        ind = cy.indicators(conn)
    except Exception:
        ind = dict(rows=[], price=None, rate=None, candidates=[])
    intro = ('<div class="card"><b>투자 사이클</b><div class="muted" style="font-size:13px">시장은 오르고 내리는 순환을 반복한다는 생각을 정리한 유명한 이론들이에요. '
             '<b>지금이 몇 번째 국면인지 맞히는 도구가 아니라 생각의 틀</b>이에요. 사이클은 지나간 뒤에야 선명해지고, 같은 모양이 반복된다는 보장도 없어요. '
             '아래 설명은 이론의 요약이라 원전과 다를 수 있고, 투자 권유가 아니에요.</div></div>')
    rows = "".join(f'<div style="border-top:1px solid var(--line);padding:6px 0"><div style="display:flex;justify-content:space-between;gap:8px"><span>{escape(r["label"])}</span><b style="text-align:right">{escape(r["value"])}</b></div>'
                   f'<div class="muted" style="font-size:12px">{escape(r["note"])}</div></div>' for r in ind["rows"])
    cand = "".join(f'<li><b>{escape(n)}</b> — {escape(w)}</li>' for n, w in ind["candidates"])
    live = ('<div class="card"><b>지금 자료로 보는 점검표</b>'
            '<div class="muted" style="font-size:12px">앱이 받아 둔 코스피·기준금리·국고채·환율·M2로 계산했어요. 기업 실적, 거래량, 투자자 심리, 물가·성장률은 여기서 계산하지 않아요.</div>'
            + (f'<div style="margin-top:6px">{rows}</div>' if rows else
               '<div class="muted" style="margin-top:6px">코스피·금리 자료가 아직 부족해요 (시장·정책 데이터를 먼저 받아 주세요).</div>')
            + (f'<div style="margin-top:8px"><b style="font-size:13px">우라가미 틀로 본 후보 (금리 방향 × 주가 추세만 반영)</b><ul style="margin:4px 0;padding-left:18px;font-size:13px">{cand}</ul>'
               '<div class="muted" style="font-size:12px">실적 방향은 반영하지 않았어요. 후보일 뿐 판정이 아니에요.</div></div>' if cand else "")
            + '<div style="margin-top:6px"><a class="chip" href="/diag">하락 점검 (과거 비슷하게 빠진 뒤 기록) ↗</a></div></div>')
    yrs = {"1": 1, "3": 3, "5": 5, "0": 0}.get(q.get("cr", "3"), 3)
    try:
        cd = cy.chart_data(conn, yrs)
    except Exception:
        cd = {}
    rchips = " ".join((f'<b class="chip" style="background:var(--accent);color:var(--bg)">{lab}</b>' if str(v) == str(q.get("cr", "3")) else f'<a class="chip" href="/cycle?cr={v}">{lab}</a>')
                      for v, lab in (("1", "1년"), ("3", "3년"), ("5", "5년"), ("0", "전체")))

    def gblock(title, note, html):
        return f'<div style="margin-top:10px"><b style="font-size:13px">{title}</b><div class="muted" style="font-size:12px">{note}</div>{html}</div>'
    graphs = ""
    if cd.get("kospi"):
        graphs += gblock("코스피와 200일 이동평균", "가격이 점선(200일선)을 아래로 뚫고, 점선도 꺾이면 와인스타인의 4단계에 가까워요.", line_chart(cd["kospi"]))
        graphs += gblock("코스피 고점 대비 낙폭", "직전 250거래일 최고 종가 대비 %. 깊이와 회복 기간을 한눈에 봐요.", line_chart(cd["dd"], yfmt="{:.0f}%", zero=True, area=True, height=150))
    if any(x["pts"] for x in cd.get("rate", [])):
        graphs += gblock("기준금리와 국고채 금리", "금리가 오르는 중인지 내리는 중인지가 우라가미·투자 시계의 핵심 축이에요. 국고채 3년이 기준금리보다 앞서 움직이는 경우가 많아요.", line_chart(cd["rate"], yfmt="{:.2f}"))
    if any(x["pts"] for x in cd.get("fx", [])):
        graphs += gblock("원/달러 환율", "원화 약세(환율 상승)가 급해지면 외국인 수급과 함께 증시에 부담이 되는 경우가 많아요.", line_chart(cd["fx"]))
    if any(x["pts"] for x in cd.get("m2", [])):
        graphs += gblock("M2 광의통화 증가율 (전년 동월 대비)", "유동성이 늘어나는 속도예요. 둔화되면 금융장세 조건이 약해져요.", line_chart(cd["m2"], yfmt="{:.1f}%", zero=True, area=True, height=150))
    gcard = ('<div class="card"><details open><summary style="cursor:pointer"><b>그래프</b></summary>'
             f'<div style="margin:6px 0">{rchips}</div>{graphs or "<div class=muted>그래프로 그릴 자료가 아직 없어요.</div>"}</div></details></div>')
    try:
        asm = cy.assess(ind)
    except Exception:
        asm = []
    asm_by = {a["id"]: a for a in asm}

    def asm_box(a, full=True):
        ev = "".join(f'<li>{escape(e)}</li>' for e in a["evidence"])
        mi = ", ".join(a["missing"])
        return (f'<div style="margin:2px 0"><span class="chip" style="font-weight:600">{escape(a["verdict"])}</span> '
                f'<span class="muted" style="font-size:12px">확신도 {escape(a["conf"])}</span></div>'
                + (f'<ul style="margin:4px 0;padding-left:18px;font-size:12px">{ev}</ul>' if (full and ev) else "")
                + (f'<div style="font-size:12px;margin-top:2px">{escape(a["note"])}</div>' if (full and a["note"]) else "")
                + (f'<div class="muted" style="font-size:12px;margin-top:2px">판단에 더 필요한데 앱에 없는 것: {escape(mi)}</div>' if (full and mi) else ""))
    syn = cy.synthesize(ind, asm) if asm else None
    sit = syn["situation"] if syn else None
    tone_cls = {"up": "up", "down": "down", "turn": "", "flat": "", "none": ""}
    acard = ""
    if syn:
        bl = "".join(f'<li style="margin:4px 0">{re.sub(r"[*][*](.+?)[*][*]", lambda m_: "<b>" + m_.group(1) + "</b>", escape(x))}</li>' for x in syn["lines"])
        cnt = " ".join(f'<span class="chip {tone_cls[k]}">{cy.TONE_LABEL[k]} {len(v)}</span>' for k, v in syn["groups"].items())
        rowsx = "".join(
            f'<div style="border-top:1px solid var(--line);padding:6px 0"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b style="font-size:13px">{escape(a["name"])}</b>'
            f'<span class="chip {tone_cls[a["tone"]]}" style="white-space:nowrap">{cy.TONE_LABEL[a["tone"]]}</span></div>{asm_box(a, False)}</div>' for a in asm)
        wrows = "".join(f'<div style="border-top:1px solid var(--line);padding:6px 0"><b style="font-size:13px">{escape(w)}</b> <span class="muted" style="font-size:12px">지금: {escape(n_)}</span>'
                        f'<div style="font-size:12px">{escape(c_)}</div></div>' for w, n_, c_ in syn["watch"])
        acard = ('<div class="card"><details open><summary style="cursor:pointer"><b>지금 국면을 여러 이론에 비춰 보면</b></summary>'
                 + (f'<div style="margin:8px 0"><span class="chip" style="font-size:14px;font-weight:700">{escape(sit["label"])}</span></div>' if sit else "")
                 + f'<ul style="padding-left:18px;margin:4px 0;font-size:13px">{bl}</ul>'
                 + f'<div style="margin:6px 0">{cnt}</div>'
                 + '<div class="muted" style="font-size:12px">상승·정체·하락·바닥 모두 같은 틀로 봤어요. 판정이 아니라 후보이고, 이론마다 앱에 없는 정보(실적·거래량·심리·물가 등)가 필요해서 확신도를 적었어요. '
                   '이론들은 같은 가격·금리를 보는 경우가 많아 서로 독립적인 증거가 아니에요. 개수가 많다고 확률이 높은 게 아니에요.</div>'
                 + f'<div style="margin-top:8px"><b style="font-size:13px">이론별 후보</b>{rowsx}</div>'
                 + f'<div style="margin-top:10px"><b style="font-size:13px">앞으로 판단을 바꿀 변수</b>{wrows}</div>'
                 + '</details></div>')
    sit_key = sit["key"] if sit else None
    groups_tips = [("up", "상승기에 생각해 볼 것", cy.TIPS_UP), ("flat", "정체(횡보)기에 생각해 볼 것", cy.TIPS_FLAT),
                   ("rebound", "바닥 다지기·반등기에 생각해 볼 것", cy.TIPS_TURN), ("down", "하락기에 생각해 볼 것", cy.TIPS_DOWN)]
    cur_key = "down" if sit_key in ("turn_down", "down") else sit_key
    tdet = ""
    for k_, lab_, lst_ in groups_tips:
        li_ = "".join(f'<li style="margin:6px 0"><b>{escape(t_)}</b><div class="muted" style="font-size:13px">{escape(d_)}</div></li>' for t_, d_ in lst_)
        tdet += (f'<details{" open" if k_ == cur_key else ""} style="border-top:1px solid var(--line);padding:6px 0"><summary style="cursor:pointer"><b>{lab_}</b>'
                 + (' <span class="chip">지금 상황</span>' if k_ == cur_key else "") + f'</summary><ol style="padding-left:20px;margin:6px 0">{li_}</ol></details>')
    tipc = ('<div class="card"><details open><summary style="cursor:pointer"><b>상황별로 생각해 볼 것</b></summary>'
            '<div class="muted" style="font-size:12px;margin-top:4px">종목이나 시점을 추천하는 게 아니라, 어떤 이론을 쓰든 공통으로 나오는 점검 질문이에요. 지금 상황에 해당하는 항목이 펼쳐져 있어요.</div>'
            + tdet + '</details></div>')
    th = ""
    for t in cy.THEORIES:
        ph = "".join(f'<tr><td><b>{escape(a)}</b></td><td>{escape(b)}</td><td style="font-size:12px">{escape(c)}</td><td style="font-size:12px">{escape(d)}</td></tr>' for a, b, c, d in t["phases"])
        th += (f'<details style="border-top:1px solid var(--line);padding:8px 0"><summary style="cursor:pointer"><b>{escape(t["name"])}</b>'
               f'<span class="muted" style="font-size:12px"> — {escape(t["who"])}</span></summary>'
               f'<div class="muted" style="font-size:12px;margin-top:4px">보는 축: {escape(t["axes"])}</div>'
               + (f'<div style="margin:6px 0;padding:6px 8px;border:1px solid var(--line);border-radius:8px"><b style="font-size:13px">지금 판단 (후보)</b>{asm_box(asm_by[t["id"]])}</div>' if t["id"] in asm_by else "") +
               f'<div style="font-size:13px;margin:6px 0">{escape(t["idea"])}</div>'
               f'<div class="scroll"><table><tr><th>국면</th><th>모습</th><th>신호</th><th>특징</th></tr>{ph}</table></div>'
               + "".join(f'<div style="margin-top:6px;font-size:13px;{"background:var(--chip);border-radius:6px;padding:4px 6px" if hl else ""}"><b>{lab} 읽는 법</b>{" (지금 상황)" if hl else ""} {escape(t[key])}</div>'
                         for key, lab, hl in (("up", "상승기", cur_key == "up"), ("flat", "정체기", cur_key == "flat"), ("down", "하락기", cur_key == "down"))) +
               f'<div class="muted" style="font-size:12px;margin-top:4px">⚠ 한계: {escape(t["limit"])}</div></details>')
    thc = ('<div class="card"><b>유명한 사이클 이론 정리</b><div class="muted" style="font-size:12px">이름을 눌러 펼쳐요.</div>' + th + '</div>')
    return page("투자 사이클", intro + acard + live + gcard + tipc + _fold(thc, False))


def view_legstudy(conn, q):
    """아이디어 → 입법 주가 분석: 과거 법안의 단계 날짜 전후로, 연결된 기업의 주가가 지수보다 얼마나 더 움직였나 (이벤트 스터디)"""
    import bill_history
    import legis as lg
    import legis_study as ls
    d = Path(_DATA_DIR["path"] or ".")
    st = lg.read_status(str(d / "legstudy_status.json"))
    running = bool(st and st.get("running"))
    res, meta = ls.results(conn)
    cov = bill_history.coverage(conn)

    def n(sql):
        r = lg._q(conn, sql)
        return r[0][0] if r else 0
    cnt = dict(bills=n("SELECT COUNT(*) FROM hist_bills"), pairs=n("SELECT COUNT(*) FROM hist_map"), priced=n("SELECT COUNT(*) FROM study_stock WHERE n > 0"))
    msg = q.get("msg", "")
    flash = f'<div class="card {"warn" if msg.startswith("✖") else ""}">{escape(msg)}</div>' if msg else ""
    intro = ('<div class="card"><b>입법 주가 분석</b>'
             '<div class="muted">과거 법안의 <b>단계 날짜</b>(발의 · 소관위 회부·상정·처리 · 법사위 · 본회의) 전후로, 그 법안에 닿는다고 짐작한 기업의 주가가 <b>지수보다 얼마나 더 움직였는지</b>를 평균 낸 기록이에요. '
             '같은 기업의 <b>무작위 날짜</b>를 대조군으로 삼아, 우연으로 나올 만한 크기인지(p값)도 함께 봐요. 투자 권유가 아니라 과거 기록 분석이에요.</div>'
             '<details style="margin-top:6px"><summary><b style="font-size:13px">꼭 알아 둘 한계 (투자 지표로 쓰기 전에)</b></summary>'
             '<ul class="muted" style="margin:6px 0 0;padding-left:18px;font-size:13px">'
             '<li><b>법안 → 기업 연결은 짐작</b>이에요. 테마 구성도 \'지금\' 기준이라, 과거에 속했던 종목과 상장폐지된 종목이 빠진 <b>생존 편향</b>이 있어요.</li>'
             '<li><b>통과/폐기별 비교는 선견 편향</b>이에요. 결과를 사후에 알고 나눈 것이라 그 시점의 투자 신호가 아니에요. 단계에 도달한 시점에 알 수 있는 정보로 나눈 <b>단계별·연결 확실도별</b> 결과만 지표 후보가 될 수 있어요.</li>'
             '<li>시장 전체 요인(정권·금리), 같은 날 몰린 소식, 소문에 의한 선반영이 섞여요. 표본이 작은 묶음은 우연일 가능성이 커요.</li>'
             '<li>여러 묶음을 동시에 보면 우연히 \'유의\'해 보이는 것이 몇 개는 꼭 나와요. 그래서 우연 대조군 p&lt;0.01, 날짜 군집 |t|≥3, 날짜 30개 이상을 <b>모두</b> 만족할 때만 \'우연 밖\'으로 표시해요. 묶음끼리 같은 이벤트를 공유하므로(예: 단계별과 분야별) 우연 하나가 여러 줄로 겹쳐 보일 수도 있어요.</li>'
             '<li>거래비용·유동성(특히 소형주)은 반영하지 않았어요. 가격 흐름이 보였다고 수익이 났을 거라는 뜻이 아니에요.</li></ul></details></div>')
    # ---- 준비 상태
    cov_txt = " · ".join(f'{c["age"]}대 {c["n"]:,}건({c["first"][:7]}~{c["last"][:7]}{"" if c["done"] else ", 받는 중"})' for c in cov) or "아직 받은 과거 법안이 없어요"
    icon = {"ok": "✔", "fail": "✖", "run": "…", "wait": "·"}
    steps = ""
    if st:
        for s_ in st.get("steps", []):
            steps += (f'<div style="margin:3px 0"><b>{icon.get(s_["state"], "")} {escape(s_["name"])}</b> '
                      f'<span class="{"warn" if s_["state"] == "fail" else "muted"}" style="font-size:12px">{escape(s_.get("msg", ""))}</span></div>')
        steps = (f'<div style="margin-top:8px"><b style="font-size:13px">{"진행 중" if running else "마지막 실행 결과"}</b> '
                 f'<span class="muted" style="font-size:12px">{escape(st.get("started", ""))} ~ {escape(st.get("finished", "") or "…")}</span>{steps}</div>')
    state = (f'<div class="muted" style="font-size:13px">받아 둔 과거 법안: {escape(cov_txt)}<br>기업에 연결된 법안-기업 쌍 {cnt["pairs"]:,} · 주가를 받은 종목 {cnt["priced"]:,} · '
             f'분석한 이벤트 {int(meta.get("n_events", 0) or 0):,}건' + (f' · 마지막 계산 {escape(meta.get("computed", ""))}' if meta.get("computed") else "") + '</div>')
    btn = ('<form method="post" action="/legstudy/run" class="filters" style="margin:8px 0 0">'
           f'<button{" disabled" if running else ""}>{"진행 중…" if running else ("다시 받고 분석하기" if res else "과거 법안·주가 받고 분석하기")}</button>'
           '<span class="muted" style="font-size:12px">별도 프로세스로 돌려서 화면은 그대로 쓸 수 있어요 (처음엔 20~40분, 분석 계산 단계에서 서버 메모리를 잠시 300MB쯤 써요). 먼저 \'입법 영향 기업\' 탭에서 준비를 마쳐 두세요.</span></form>')
    prep = (f'<div class="card"><b>준비 상태</b>{state}{btn}{steps}</div>')
    if not res:
        body = ('<div class="card"><div class="muted">아직 분석 결과가 없어요. 위 버튼으로 과거 법안과 주가를 받아 분석하세요. '
                '과거 법안이 받아지는 범위(20·21·22대)는 설정 → 외부 데이터 연결 확인 → <b>법안·종목 확인</b>의 \'과거 국회 법안\' 줄에서 먼저 볼 수 있어요.</div></div>')
        return page("입법 주가 분석", flash + intro + prep + body, refresh=running)
    # ---- 요약
    sig = []
    LEG = ("단계", "단계×연결", "단계×결과", "분야")
    for (grp, key), p in res.items():
        if grp not in LEG or p["n_dates"] < 30:
            continue
        v, _ = _study_verdict(p)
        if "우연 밖" in v:
            sig.append((grp, key, p, v))
    sig.sort(key=lambda x: -x[2]["n_dates"])
    n_tested = sum(1 for (g_, _k), p in res.items() if g_ in LEG and p["n_dates"] >= 30)
    lines = "".join(f'<li><b>{escape(g)} · {escape(k.replace("|", " · "))}</b>: {escape(v)} — 사전 {p["pre"]:+.1f}%, 사후 10일 {p["post10"]:+.1f}% (날짜 {p["n_dates"]:,}개)</li>' for g, k, p, v in sig[:8])
    summary = ('<div class="card"><b>한눈에 보기</b>'
               + (f'<ul style="margin:6px 0 0;padding-left:18px">{lines}</ul>' if sig else
                  '<div style="margin-top:6px"><b>뚜렷한 패턴이 없어요.</b> 표본이 충분한 묶음 중 우연과 확실히 다른 것이 없었어요. 법안 단계 소식만으로 매매할 근거가 이 데이터에서는 확인되지 않았어요.</div>')
               + f'<div class="muted" style="font-size:12px;margin-top:6px">표본 충분(날짜 30개 이상) 묶음 {n_tested}개를 동시에 봤으므로, 우연만으로도 p&lt;0.01이 약 {n_tested * 2 * 0.01:.1f}개 나올 수 있어요. '
                 '위 항목이 많지 않다면 우연일 가능성을 먼저 의심하세요. 사전 상승은 \'소문·기대로 미리 오른 것\', 사후 하락은 \'재료 소멸\'과 같은 모양이에요.</div></div>')
    # ---- 단계별
    order = [s for s, _ in ls.STAGES]
    t1 = "".join(_study_row(s, res[("단계", s)]) for s in order if ("단계", s) in res)
    t2 = "".join(_study_row(f"{s} · {tier}", res[("단계×연결", f"{s}|{tier}")]) for s in order for tier in ("직접 언급", "개념·테마 짐작") if ("단계×연결", f"{s}|{tier}") in res)
    t3 = "".join(_study_row(f"{s} · {oc}", res[("단계×결과", f"{s}|{oc}")]) for s in order for oc in ("통과", "폐기", "심사 중") if ("단계×결과", f"{s}|{oc}") in res)
    sec = lambda title, note, tbl: (f'<div class="card"><b>{title}</b><div class="muted" style="font-size:13px;margin:2px 0 4px">{note}</div>{tbl}</div>') if tbl else ""
    b1 = sec("단계별", "단계 날짜(D0)를 기준으로 연결된 기업의 지수 대비 초과수익. 이 시점에 알 수 있는 정보라서 지표 후보가 될 수 있는 구분이에요.", _study_table(t1) if t1 else "")
    b2 = sec("연결 확실도별", "법안 문구에 기업 이름이 직접 나온 경우와 개념·테마로 짐작한 경우를 나눠 봐요. 직접 언급이 더 정확한 연결이에요 (표본은 적어요).", _study_table(t2) if t2 else "")
    b3 = sec("결과별 (통과 / 폐기 / 심사 중) — 사후 정보", "<b class=\"warn\">결과를 사후에 알고 나눈 것이라 투자 신호가 아니에요.</b> \'통과한 법안은 올랐다\'는 모양이 나와도 그 시점에는 통과 여부를 몰랐어요. 어떤 법안이 어떤 길을 갔는지 보는 용도예요.", _study_table(t3) if t3 else "")
    # ---- 분야별
    ct = ""
    for stage in ("발의", "소관위 처리", "본회의 처리"):
        rows = sorted(((k.split("|", 1)[1], p) for (g, k), p in res.items() if g == "분야" and k.startswith(stage + "|")), key=lambda x: -x[1]["n_dates"])[:10]
        if rows:
            ct += f'<div style="margin-top:8px"><b style="font-size:13px">{escape(stage)} 단계</b>' + _study_table("".join(_study_row(c, p) for c, p in rows)) + "</div>"
    b4 = sec("분야별", "법안이 읽힌 분야(개념)별. 표본 15건·날짜 8개 이상인 묶음만 나와요.", ct)
    return page("입법 주가 분석", flash + intro + prep + summary + study_amp_card(res, meta) + b1 + b2 + b3 + b4, refresh=running)


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
    """기관 매수 한 건씩: (제출인, 종목코드, 제출일, 매수수량, 매수금액) — 같은 날 같은 종목은 한 번만"""
    return conn.execute(
        "SELECT 제출인, 종목코드, 제출일, SUM(매수수량) q, SUM(매수금액) amt FROM summary WHERE 매수수량 > 0 "
        "GROUP BY 제출인, 종목코드, 제출일").fetchall()


# 성적표 정렬 (목록: 운용사 단위 / 상세: 건별)
PERF_SORTS = {"": "정렬: 코스피 대비 높은 순", "ret": "정렬: 평균 수익률 높은 순", "win": "정렬: 오른 비율 높은 순",
              "worst": "정렬: 최악 수익률 (손실 큰 순)", "amt": "정렬: 매수금액 큰 순", "n": "정렬: 건수 많은 순",
              "last": "정렬: 최근 제출일순", "hold": "정렬: 평균 보유기간 긴 순", "name": "정렬: 이름순"}
PERF_SORTS_DETAIL = {"": "정렬: 제출일 최신순", "old": "정렬: 제출일 오래된 순", "exc": "정렬: 코스피 대비 높은 순",
                     "ret": "정렬: 수익률 높은 순", "amt": "정렬: 매수금액 큰 순", "hold": "정렬: 보유기간 긴 순",
                     "name": "정렬: 종목명순"}


def sort_perf(rows, s):
    """운용사 목록 정렬. 표본이 적은 곳(PERF_MIN_N 미만)은 항상 뒤로."""
    def key(r):
        val = {"": -r["excess"], "ret": -r["avg"], "win": -r["win"], "worst": r["worst"], "amt": -r["amt"],
               "n": -r["n"], "last": -dt.date.fromisoformat(r["last"]).toordinal(), "hold": -r["hold"],
               "name": r["who"]}.get(s, -r["excess"])
        return (r["n"] < PERF_MIN_N, val)
    return sorted(rows, key=key)


def sort_perf_events(evs, s):
    d = lambda e: dt.date.fromisoformat(e["sub"]).toordinal()
    keys = {"": lambda e: -d(e), "old": d, "exc": lambda e: -e["x"], "ret": lambda e: -e["r"],
            "amt": lambda e: -e["amt"], "hold": lambda e: -e["hold"], "name": lambda e: e["name"]}
    return sorted(evs, key=keys.get(s, keys[""]))


def perf_sort_form(opts, cur, hidden):
    hid = "".join(f'<input type="hidden" name="{k}" value="{escape(str(v))}">'
                  for k, v in hidden.items() if v not in (None, ""))
    return f'<form method="get" class="filters" style="margin:6px 0">{hid}{select("s", opts, cur)}<button>적용</button></form>'


def perf_compute(conn, h, ex, kind=""):
    """성적표 건별 결과: 기관이 공시를 낸 날 종가에 샀다고 보고 h(일 또는 'now') 뒤 수익률과 코스피 대비 초과수익.
    성적표 화면과 종목 순위가 같이 씀. [{who, code, name, sub, end, p0, p1, r, x, amt, hold}]"""
    import stock_market
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
    for who, code, sub, qty, amt in perf_events(conn):
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
                        end=end.isoformat(), p0=p0, p1=p1, r=r, x=r - m,
                        amt=float(amt or 0), hold=(end - start).days))
    return evs


def view_performance(conn, q):
    if not has_table(conn, "summary") or not has_table(conn, "prices"):
        return page("운용사별 성적표", '<div class="card">주가와 보고서 수집이 한 번 끝나야 볼 수 있습니다. '
                                    '위쪽 <b>지금 수집 → 보고서만</b>을 눌러 주세요.</div>')
    h = q.get("h") if perf_h_ok(q.get("h") or "") else "90"
    ex = q.get("ex") if q.get("ex") in ("0", "1") else "1"
    kind = q.get("k") if q.get("k") in PERF_KINDS else ""
    evs = perf_compute(conn, h, ex, kind)

    s = q.get("s") if q.get("s") in (PERF_SORTS_DETAIL if q.get("who") else PERF_SORTS) else ""
    keep = {"s": s} if s else {}
    if q.get("who"):
        return view_performance_detail(q.get("who"), [e for e in evs if e["who"] == q.get("who")],
                                       h, ex, kind, s)

    per = {}
    for e in evs:
        acc = per.setdefault(e["who"], {"r": [], "x": [], "amt": 0.0, "last": "", "hold": []})
        acc["r"].append(e["r"])
        acc["x"].append(e["x"])
        acc["amt"] += e["amt"]
        acc["last"] = max(acc["last"], e["sub"])
        acc["hold"].append(e["hold"])

    rows = []
    for who, a in per.items():
        n = len(a["r"])
        rows.append(dict(who=who, n=n, win=sum(1 for v in a["r"] if v > 0) / n,
                         avg=sum(a["r"]) / n, excess=sum(a["x"]) / n, amt=a["amt"], last=a["last"],
                         hold=sum(a["hold"]) / n, worst=min(a["r"])))
    rows = sort_perf(rows, s)

    preset = h in PERF_HORIZONS
    chips = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == h else
         f'<a class="chip" href="{perf_url(h=k, ex=ex, **({"k": kind} if kind else {}), **keep)}">{v}</a>')
        for k, v in PERF_HORIZONS.items())
    custom_form = (f'<form method="get" class="filters" style="margin-top:6px"><input type="hidden" name="t" value="perf"><input type="hidden" name="ex" value="{ex}">'
                   + (f'<input type="hidden" name="k" value="{kind}">' if kind else "")
                   + (f'<input type="hidden" name="s" value="{s}">' if s else "")
                   + f'<input type="number" name="h" min="1" max="{PERF_MAX_DAYS}" step="1" placeholder="직접 입력 (일)" '
                   + (f'value="{h}"' if not preset else "") + ' style="width:9em">'
                   + '<button>보기</button>'
                   + (f' <span class="muted">{escape(perf_label(h))} 기준으로 보는 중</span>' if not preset else "")
                   + '</form>')
    kchips = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == kind else
         f'<a class="chip" href="{perf_url(h=h, ex=ex, **({"k": k} if k else {}), **keep)}">{v}</a>')
        for k, v in PERF_KINDS.items())
    toggle = (f'<div style="margin-bottom:6px">{kchips}</div>'
              f'<a class="chip" href="{perf_url(h=h, ex="0" if ex == "1" else "1", **({"k": kind} if kind else {}), **keep)}">'
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
        wlink = perf_url(who=r["who"], h=h, ex=ex, **({"k": kind} if kind else {}), **keep)
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span><a class="big" href="{wlink}">{escape(r["who"])}</a></div>'
            + ('' if ok else '<span class="chip">표본 적음</span>') + "</div>"
            f'<div>코스피 대비 평균 <b class="{cls}">{r["excess"]:+.1%}</b> · 평균 수익률 {r["avg"]:+.1%}</div>'
            f'<div class="muted">{r["n"]}건 중 {r["win"]:.0%}가 올랐어요 · 최악 {r["worst"]:+.1%} · '
            f'매수 합계 {eok(r["amt"])} · 평균 보유 {r["hold"]:.0f}일 · 최근 제출 {escape(r["last"])}</div></div>')
    sort_form = perf_sort_form(PERF_SORTS, s, {"t": "perf", "h": h, "ex": ex, "k": kind})
    body = (intro + f'<div style="margin:8px 0"><span class="muted">보유 기간:</span> {chips}{custom_form}</div>'
            + f'<div style="margin-bottom:8px">{toggle}</div>' + sort_form
            + ("".join(cards) or '<p class="muted">아직 계산할 건이 없어요. 제출일부터 그 기간이 지나야 해요.</p>'))
    return page("운용사별 성적표", body)


def view_performance_detail(who, evs, h, ex, kind, s=""):
    """운용사(또는 개인) 한 명의 건별 결과. 제목을 누르면 여기로 옵니다."""
    keep = {"s": s} if s else {}
    back_q = {"h": h, "ex": ex, **({"k": kind} if kind else {}), **keep}
    back = f'<a href="{perf_url(**back_q)}">← 성적표로</a>'
    head = (f'<div class="row"><div><span class="big">{escape(who)}</span></div><div>{back}</div></div>')
    if not evs:
        return page(f"{who} — 성적표", head + f'<div class="card" style="margin-top:8px">'
                    f'{perf_label(h)} 결과를 계산할 건이 없어요. (국민연금 빼고 보기 설정이나 보유 기간을 바꿔 보세요)</div>')
    n = len(evs)
    win = sum(1 for e in evs if e["r"] > 0) / n
    avg = sum(e["r"] for e in evs) / n
    exc = sum(e["x"] for e in evs) / n
    amt = sum(e["amt"] for e in evs)
    hold = sum(e["hold"] for e in evs) / n
    worst = min(e["r"] for e in evs)
    cls = "up" if exc > 0 else "down" if exc < 0 else ""
    summary = (f'<div class="card" style="margin-top:8px">{n}건 · {perf_label(h)} 기준'
               + ('' if n >= PERF_MIN_N else ' <span class="chip">표본 적음</span>') + "<br>"
               f'코스피 대비 평균 <b class="{cls}">{exc:+.1%}</b> · 평균 수익률 {avg:+.1%} · '
               f'{win:.0%}가 올랐어요 · 최악 {worst:+.1%}<br>'
               f'<span class="muted">매수 합계 {eok(amt)} · 평균 보유 {hold:.0f}일</span></div>')
    def cl(v):
        return "up" if v > 0 else "down" if v < 0 else ""
    rows = "".join(
        f'<tr><td>{escape(e["sub"])}</td>'
        f'<td><a href="/stock?code={quote(e["code"])}">{escape(e["name"])}</a></td>'
        f'<td>{eok(e["amt"])}</td><td>{won(e["p0"])}</td><td>{won(e["p1"])}</td>'
        f'<td>{e["hold"]}일</td>'
        f'<td class="{cl(e["r"])}">{e["r"]:+.1%}</td><td class="{cl(e["x"])}">{e["x"]:+.1%}</td></tr>'
        for e in sort_perf_events(evs, s))
    table = ('<div style="overflow-x:auto;margin-top:8px"><table><tr><th>제출일</th><th>종목</th><th>매수금액</th>'
             f'<th>제출일 종가</th><th>{"오늘 종가" if h == "now" else perf_label(h) + " 종가"}</th><th>보유</th>'
             f'<th>수익률</th><th>코스피 대비</th></tr>{rows}</table></div>')
    sort_form = perf_sort_form(PERF_SORTS_DETAIL, s, {"t": "perf", "who": who, "h": h, "ex": ex, "k": kind})
    note = ('<p class="muted">제출일 종가에 샀다고 가정하고, 그 뒤 날짜 종가로 계산했어요. 과거 성적이라 앞으로도 같다는 보장은 없어요.</p>')
    return page(f"{who} — 성적표", head + summary + sort_form + table + note)


# ---------------- 아이디어 ----------------
IDEA_TABS = {"overall": "종합", "mine": "내 종목 점수", "cheap": "기관 평단보다 싼 종목", "cluster": "여러 기관이 함께 산 종목", "super": "슈퍼개미 움직임",
             "rank": "종목 순위", "score": "점수표 (오일전문가)", "perf": "운용사 성적표",
             "lynch": "피터 린치", "buffett": "워렌 버핏",
             "graham": "벤저민 그레이엄", "magic": "마법공식", "fscore": "F-스코어", "momentum": "모멘텀",
             "legis": "입법 영향 기업", "legstudy": "입법 주가 분석", "events": "이벤트 테마"}
IDEA_GROUPS = [["overall", "mine", "cheap", "cluster", "super", "legis", "rank", "lynch", "buffett", "graham", "magic", "fscore", "momentum", "score", "perf", "legstudy", "events"]]     # 위쪽 메뉴의 '아이디어' 하나로 합침 (예전 '분석' 탭들 포함)


IDEA_SECTIONS = [("점수", ["overall", "mine"]), ("투자 방법", ["lynch", "buffett", "graham", "magic", "fscore", "momentum"]),
                 ("종목 찾기", ["cheap", "cluster", "super", "legis", "events", "rank"]), ("평가·성적", ["score", "perf", "legstudy"])]


def idea_tab_html(active, link=None):
    """아이디어 탭 줄: 묶음 이름과 함께 줄을 나눠 보여 줌. link(k) → 주소 (기본 /ideas?t=k)"""
    link = link or (lambda k: "/ideas?" + urlencode({"t": k}))
    rows = ""
    for label, keys in IDEA_SECTIONS:
        chips = " ".join(
            (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{IDEA_TABS[k]}</b>' if k == active else
             f'<a class="chip" href="{link(k)}">{IDEA_TABS[k]}</a>') for k in keys)
        rows += f'<div style="margin:0 0 4px"><span class="muted" style="font-size:12px;margin-right:4px">{label}</span>{chips}</div>'
    return rows


def idea_group_tabs(active):
    """active 탭이 속한 묶음의 탭들 [(키, 이름)]"""
    grp = next((g for g in IDEA_GROUPS if active in g), IDEA_GROUPS[0])
    return [(k, IDEA_TABS[k]) for k in grp]

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
    # 매도 단가·차익·잔량: 수집한 공시 전체 이력 기준 (기간과 상관없이 '아직 안 팔았는지'를 봄)
    POS = pf.compute(conn)
    sold_who = {w for (w, _), ps in POS.items() if ps["sold"]}
    for p in people.values():
        p["ever_sold"] = p["name"] in sold_who
        realized = []
        for g in p["stocks"]:
            g["pos"] = POS.get((p["name"], g["code"]))
            if g["pos"] and g["pos"]["realized_cost"]:
                realized.append(g["pos"]["realized"])
        p["realized"] = sum(realized)
        p["realized_known"] = bool(realized)
    q = (flt.get("q") or "").strip()
    amt = float(flt.get("amt") or 0)
    d = flt.get("dir") or ""
    out = []
    for p in people.values():
        if q and q not in p["name"]:
            continue
        if d == "never" and p["ever_sold"]:
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
    elif srt == "gain":
        out.sort(key=lambda p: (not p["realized_known"], -p["realized"]))
    else:
        out.sort(key=lambda p: (p["last"], p["amt"]), reverse=True)
    for p in out:
        p["items"].sort(key=lambda r: r["제출일"] or "", reverse=True)
    return out


NPS_OPTS = {"1": "국민연금 제외", "0": "국민연금 포함"}
WINDOWS = {"30": "최근 1달 매수", "90": "최근 3달 매수", "180": "최근 6달 매수", "365": "최근 1년 매수"}


def big_won(v):
    """원 → 1.2조 / 3,450억 / 8만 (시가총액·거래대금용)"""
    if v is None:
        return "-"
    return f"{v / 1e12:,.1f}조" if v >= 1e12 else f"{v / 1e8:,.0f}억" if v >= 1e8 else f"{v / 1e4:,.0f}만"


def _ext_data(conn):
    """KRX 시세(시가총액·상장주식수)와 대차잔고 (없으면 빈 dict)"""
    try:
        import krx_api, lending
        return krx_api.latest(conn), lending.latest(conn)
    except Exception:
        return {}, {}


def lend_summary(conn, code, list_shrs=None):
    """종목 하나의 대차잔고 요약: {date, shs, pct(상장주식수 대비), chg5, hist}. 데이터가 없으면 None"""
    try:
        import lending
        hist = lending.history(conn, code, 20)
    except Exception:
        return None
    if not hist or not hist[-1][1]:
        return None
    return dict(date=hist[-1][0], shs=hist[-1][1], pct=(hist[-1][1] / list_shrs if list_shrs else None),
                chg5=lending.change(hist, 5), hist=hist)


def ext_block(conn, code):
    """종목 화면: KRX 시가총액·상장주식수·거래대금, 대차잔고와 최근 변화"""
    krx, _ = _ext_data(conn)
    k = krx.get(code)
    ls = lend_summary(conn, code, k["list_shrs"] if k else None)
    if not k and not ls:
        return ""
    lines = []
    if k:
        avg = None
        try:
            import krx_api
            avg = krx_api.avg_trdval(conn, code, 10)
        except Exception:
            pass
        lines.append(f'<div>시가총액 <b>{big_won(k["mktcap"])}</b> · 상장주식수 {won(k["list_shrs"])}주 · 거래대금 {big_won(k["trdval"])}'
                     + (f' <span class="muted">(10일 평균 {big_won(avg)})</span>' if avg else "")
                     + f' <span class="muted">· KRX {escape(k["bas_dd"])}</span></div>')
    if ls:
        chg = (f' · 5거래일 <b class="{"up" if ls["chg5"] > 0 else "down" if ls["chg5"] < 0 else ""}">{ls["chg5"]:+.1%}</b>'
               if ls["chg5"] is not None else "")
        lines.append(f'<div>대차잔고 <b>{won(ls["shs"])}주</b>'
                     + (f' (상장주식수의 {ls["pct"]:.2%})' if ls["pct"] else "") + chg
                     + f' <span class="muted">· {escape(ls["date"])} 기준</span></div>'
                     + (sparkline([h[1] for h in ls["hist"]]) if len(ls["hist"]) >= 2 else ""))
        lines.append('<div class="muted" style="font-size:12px">대차잔고는 빌려 간 주식 수예요. 공매도를 하려면 먼저 빌려야 해서 '
                     '참고하지만, 공매도 잔고와 같은 숫자는 아니에요.</div>')
    return '<div class="card" style="margin-top:8px">' + "".join(lines) + '</div>'


def _ext_line(i):
    """아이디어 카드 한 줄: 시가총액, 기관 매수금액의 시총 비중, 대차잔고"""
    k, ls = i.get("krx"), i.get("lend")
    parts = []
    if k and k.get("mktcap"):
        r = i["r"]
        parts.append(f'시총 {big_won(k["mktcap"])} · 기관 매수 {eok(r["amt"])}은 시총의 {r["amt"] / k["mktcap"]:.2%}')
    if ls:
        parts.append(f'대차잔고 {won(ls["shs"])}주' + (f' ({ls["pct"]:.2%})' if ls["pct"] else "")
                     + (f' · 5일 {ls["chg5"]:+.0%}' if ls["chg5"] is not None else ""))
    return f'<div class="muted">{" · ".join(parts)}</div>' if parts else ""


def money(v):
    """부호가 붙은 금액: +2.3억 / −3,000만 / +850원"""
    if v is None:
        return "-"
    a, sign = abs(v), "+" if v > 0 else "−" if v < 0 else ""
    return f"{sign}{a / 1e8:,.1f}억" if a >= 1e8 else f"{sign}{a / 1e4:,.0f}만" if a >= 1e4 else f"{sign}{a:,.0f}원"


def gain_html(v, pct=None):
    if v is None:
        return '<span class="muted">-</span>'
    cls = "up" if v > 0 else "down" if v < 0 else ""
    return f'<b class="{cls}">{money(v)}</b>' + (f' <span class="{cls}">({pct:+.0%})</span>' if pct is not None else "")


def pos_line(p, close):
    """한 제출인의 한 종목: 매수 평단 · 매도 단가 · 실현 차익 · 잔량 · 평가 손익 (한 줄 HTML)"""
    parts = [f'매수 평단 {won(p["avg_buy"])}원' if p["avg_buy"] else "매수 평단 -"]
    if p["sold"]:
        parts.append(f'매도 {won(p["sell_q"])}주' + (f' @ {won(p["avg_sell"])}원' if p["avg_sell"] else ""))
        parts.append("실현 차익 " + gain_html(p["realized"], p["realized_pct"]) if p["realized_cost"]
                     else '<span class="muted">차익 계산 불가 (산 가격을 모르는 매도)</span>')
        if p["unmatched_sell"] and p["realized_cost"]:
            parts.append(f'<span class="muted">산 가격 모르는 매도 {won(p["unmatched_sell"])}주는 차익에서 뺌</span>')
    else:
        parts.append("<b>아직 매도 안 함</b>")
    src = f'{p["remain_src"]}' + (f' {p["report_pct"]:.2f}%' if p["remain_src"] == "보고서" and p["report_pct"] is not None else "")
    parts.append(f'잔량 <b>{won(p["remain"])}주</b> <span class="muted">({escape(src)})</span>')
    u, upct = pf.unrealized(p, close)
    if u is not None:
        parts.append("남은 주식 평가 손익 " + gain_html(u, upct))
    return " · ".join(parts)


def _pos_block(i):
    """아이디어 카드: 이 종목을 산 기관들의 매도 현황"""
    pl = i.get("pl") or []
    if not pl:
        return ""
    s_ = i["pos"]
    head = ('<div style="margin-top:6px"><b>매도 현황</b> <span class="muted">기관 %d곳 중 ' % s_["n"]
            + (f'<b>{s_["sold_n"]}곳이 매도</b> · 아직 안 판 곳 {s_["n"] - s_["sold_n"]}곳' if s_["sold_n"]
               else "<b>아직 아무도 안 팔았어요</b>") + "</span></div>")
    shown = sorted(pl, key=lambda p: -(p["buy_amt"] or 0))[:5]
    lines = "".join(f'<div style="font-size:13px;margin-top:3px"><b>{escape(p["who"])}</b> — {pos_line(p, i["close"])}</div>'
                    for p in shown)
    more = f'<div class="muted" style="font-size:12px">{len(pl)}곳 중 5곳만 표시</div>' if len(pl) > 5 else ""
    total = (f'<div class="muted" style="font-size:12px;margin-top:3px">실현 차익 합계 {gain_html(s_["realized"])} · 평균 매도 단가 '
             f'{won(s_["avg_sell"]) if s_["avg_sell"] else "-"}원 · 남은 잔량 합계 {won(s_["remain"])}주</div>' if s_["sold_n"] else "")
    return head + lines + more + total


SOLD_OPTS = {"": "매도 여부: 전체", "none": "아직 아무도 안 판 종목만", "some": "매도한 기관이 있는 종목만"}


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
                         f'<div>{avg} · {last}</div><div class="muted">{now}</div>'
                         + (f'<div style="font-size:13px;margin-top:2px">{pos_line(g["pos"], g["close"])}</div>' if g.get("pos") else "")
                         + '</div>')
        more = f'<div class="muted" style="margin-top:4px">종목 {len(p["stocks"])}개 중 6개만 표시</div>' if len(p["stocks"]) > 6 else ""
        act = " · ".join(x for x in (f'매수 {won(p["bq"])}주' if p["bq"] else "", f'매도 {won(p["sq"])}주' if p["sq"] else "",
                                     f'약 {eok(p["amt"])}' if p["amt"] else "") if x)
        who_link = f'/investor?{urlencode({"name": p["name"], "w": q.get("w", "90")})}'
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span><a class="big" href="{who_link}">{escape(p["name"])}</a></div>'
            f'<div class="muted">최근 제출 {escape(p["last"])}</div></div>'
            f'<div>{act}</div>'
            f'<div>추정 수익률(평균 매수가 대비 현재가): <b>{pct(p["ret"])}</b></div>'
            + (f'<div>실현 차익 합계 {gain_html(p["realized"])} <span class="muted">(매도한 종목만, 추정)</span></div>' if p.get("ever_sold") and p.get("realized_known") else "")
            + ("" if p.get("ever_sold") else '<div class="muted">공시에서 아직 매도한 기록이 없어요</div>')
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
        ps = g.get("pos")
        sell = ("-" if not ps else "아직 안 팖" if not ps["sold"] else
                f'{won(ps["sell_q"])}주' + (f' @ {won(ps["avg_sell"])}원' if ps["avg_sell"] else ""))
        stocks += (f'<tr><td><b>{escape(str(g["name"]))}</b><div class="muted">{escape(str(g["code"]))}</div></td>'
                   f'<td>{won(g["avg"]) if g["avg"] else "-"}</td><td>{won(g["last_unit"]) if g["last_unit"] else "-"}</td>'
                   f'<td>{won(g["close"]) if g["close"] else "-"}</td><td>{ret}</td>'
                   f'<td>{sell}</td><td>{gain_html(ps["realized"], ps["realized_pct"]) if ps and ps["realized_cost"] else "-"}</td>'
                   f'<td>{won(ps["remain"]) + "주" if ps else "-"}</td></tr>')
    stock_tbl = ('<h2>종목별</h2><div class="scroll"><table><tr><th>종목</th><th>평균 매수가</th><th>최근 제출 단가</th>'
                 f'<th>현재가</th><th>수익률</th><th>매도</th><th>실현 차익</th><th>잔량</th></tr>{stocks}</table></div>'
                 '<p class="muted" style="font-size:12px">매도·차익·잔량은 수집한 공시 전체를 평균단가법으로 따라간 추정이에요. '
                 '공시 이전에 산 주식을 판 경우는 차익에서 빠지고, 잔량은 보고서에 적힌 보유수량(있으면) 또는 공시 증감 합이에요.</p>') if stocks else ""
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
                + f'<p><a href="/ideas?t=perf&k=person">개인 투자자 성적표 보기 →</a> · <a href="/ideas?t=super">← 슈퍼개미 목록</a></p>')


IDEA_SORTS = {"": "정렬: 기본", "amt": "정렬: 매수금액 큰 순", "n": "정렬: 기관 수 많은 순",
              "disc": "정렬: 평단 대비 많이 싼 순", "gain": "정렬: 평단 대비 많이 오른 순",
              "last": "정렬: 최근 매수일순", "name": "정렬: 이름순",
              "pbr": "정렬: PBR 낮은 순", "rgain": "정렬: 실현 차익 큰 순", "remain": "정렬: 남은 잔량 많은 순",
              "lend": "정렬: 대차잔고 많이 늘어난 순 (5일)"}


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
        "rgain": lambda i: -((i.get("pos") or {}).get("realized") or 0),
        "remain": lambda i: -((i.get("pos") or {}).get("remain") or 0),
        "lend": lambda i: (((i.get("lend") or {}).get("chg5")) is None, -(((i.get("lend") or {}).get("chg5")) or 0)),
    }[srt]
    return sorted(ideas, key=key, reverse=(srt == "last"))


def perf_url(**kw):
    """성적표는 아이디어 탭 안에 있음"""
    return "/ideas?" + urlencode({"t": "perf", **kw})


MARKET_HUB = {"/market": "시장 온도", "/cycle": "투자 사이클", "/policy": "정책", "/radar": "대비 레이더", "/diag": "하락 점검", "/bills": "법안·종목", "/news": "속보", "/shorts": "공매도 리포트", "/derivs": "선물·채권", "/calendar": "달력"}


REPORT_HUB = {"/": "보고서", "/new": "새 공시", "/reports": "증권사 리포트"}


def hub_tabs_row(path):
    """시장·보고서 묶음 화면 맨 위의 탭 줄. 묶음에 안 속한 화면은 빈 문자열."""
    for hub in (MARKET_HUB, REPORT_HUB):
        if path in hub:
            return '<div style="margin:0 0 8px">' + " ".join(
                (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == path else
                 f'<a class="chip" href="{k}">{v}</a>') for k, v in hub.items()) + '</div>'
    return ""


def view_ideas(conn, q):
    if q.get("t") not in IDEA_TABS:         # 위쪽 '아이디어'를 누르면 종합부터
        q = {**q, "t": "overall"}
    if q.get("t") == "mine":
        return view_mine(conn, q)
    if q.get("t") == "events":
        _CTX.pre = idea_tabs_row("events")
        try:
            return view_events(conn, q)
        finally:
            _CTX.pre = ""
    if q.get("t") == "legstudy":
        _CTX.pre = idea_tabs_row("legstudy")
        try:
            return view_legstudy(conn, q)
        finally:
            _CTX.pre = ""
    if q.get("t") == "legis":
        _CTX.pre = idea_tabs_row("legis")
        try:
            return view_legis(conn, q)
        finally:
            _CTX.pre = ""
    if q.get("t") == "overall":
        _CTX.pre = idea_tabs_row("overall")
        try:
            return view_overall(conn, q)
        finally:
            _CTX.pre = ""
    if q.get("t") in GURU_INFO:
        _CTX.pre = idea_tabs_row(q["t"])
        try:
            return view_guru(conn, q)
        finally:
            _CTX.pre = ""
    if q.get("t") in ("rank", "perf"):
        _CTX.pre = idea_tabs_row(q["t"])
        try:
            return view_ranking(conn, q) if q["t"] == "rank" else view_performance(conn, q)
        finally:
            _CTX.pre = ""
    if q.get("t") == "score":
        return view_score(conn, q)
    if not has_table(conn, "summary"):
        return page("아이디어", '<div class="card">보고서(DART) 수집이 한 번 끝나야 볼 수 있습니다. '
                              '위쪽 <b>지금 수집 → 보고서만</b>을 눌러 주세요.</div>')
    if q.get("t") == "super" and has_table(conn, "detail"):
        w = q.get("w") if q.get("w") in WINDOWS else "90"
        tabs = idea_tab_html("super")
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
        dir_opts = {"": "매수·매도 전체", "buy": "매수한 사람만", "sell": "매도한 사람만", "never": "아직 한 번도 안 판 사람만"}
        sort_opts = {"date": "정렬: 최근 제출일순", "amt": "정렬: 매수금액순", "name": "정렬: 이름순", "ret": "정렬: 수익률순",
                     "gain": "정렬: 실현 차익순"}
        form = (f'<form method="get" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="super">'
                f'<input type="hidden" name="w" value="{escape(w)}">'
                f'<input type="search" name="sq" value="{fq}" placeholder="이름 검색">'
                f'{select("amt", amt_opts, q.get("amt", ""))}{select("dir", dir_opts, q.get("dir", ""))}'
                f'{select("sort", sort_opts, q.get("sort", "date"))}<button>적용</button></form>')
        body = (tabs + f'<div style="margin:8px 0">{win}</div>' + form + listing
                + f'<p style="margin-top:10px"><a href="/ideas?t=perf&k=person">개인 투자자 성적표 보기 →</a></p>')
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
    sold = q.get("sold") if q.get("sold") in SOLD_OPTS else ""
    POS = pf.compute(conn)                      # 매도 단가·차익·잔량 (수집한 공시 전체 이력 기준)
    for i in ideas:
        code = i["r"]["code"]
        i["pl"] = [POS[(w_, code)] for w_ in (i["r"]["whos"] or "").split(",") if (w_, code) in POS]
        i["pos"] = pf.summarize(i["pl"]) if i["pl"] else None
    KRX, LEND = _ext_data(conn)
    for i in ideas:
        i["krx"] = KRX.get(i["r"]["code"])
        i["lend"] = lend_summary(conn, i["r"]["code"], i["krx"]["list_shrs"] if i["krx"] else None) if LEND else None
    if sold == "none":
        ideas = [i for i in ideas if i["pos"] and i["pos"]["none_sold"]]
    elif sold == "some":
        ideas = [i for i in ideas if i["pos"] and not i["pos"]["none_sold"]]
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
    tabs = idea_tab_html(tab, lambda k: "/ideas?" + urlencode({**base, "t": k}))
    form = (f'<form method="get" class="filters" style="margin-top:10px"><input type="hidden" name="t" value="{tab}">'
            f'{select("w", WINDOWS, w)}' + (select("m", MARKET_OPTS, m) if "시장" in cols else "")
            + f'{select("ex", NPS_OPTS, ex)}{select("sold", SOLD_OPTS, sold)}{select("s", IDEA_SORTS, srt)}<button>적용</button></form>')
    perf_link = '<p><a href="/ideas?t=perf">운용사별 성적표 보기 →</a> (어느 기관의 매수가 과거에 잘 맞았는지)</p>'
    note = perf_link + ('<p class="muted">살펴볼 후보를 찾는 화면입니다. 공시는 매매 후 최대 5영업일 뒤에 나오고, '
            '국민연금처럼 큰 기관은 비중 조절로 사고팔 때가 많아 기본으로 뺐습니다. '
            '매도 단가·차익·잔량은 수집한 공시 이력을 평균단가법으로 따라간 추정이에요 '
            '(공시 이전에 산 주식을 판 경우는 차익에서 빠지고, 정정 공시가 겹치면 어긋날 수 있어요).</p>')
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
    shown_codes = [i["r"]["code"] for i in ideas[:30]]
    reps = report_map(conn, shown_codes)
    try:
        ov = {c["code"]: c for c in overall_cards(conn, [dict(code=i["r"]["code"], name=i["r"]["name"], mk=i["r"]["mk"], n=i["r"]["n"]) for i in ideas[:30]])}
    except Exception:
        ov = {}
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
            + (f'<div style="margin-top:4px"><a class="chip {"up" if ov[r["code"]]["avg"] >= GURU_PASS else ""}" href="/ideas?{urlencode({"t": "mine", "q": r["code"]})}">'
               f'종합 점수 {ov[r["code"]]["avg"]:.0f} · 통과 {ov[r["code"]]["passed"]}/{len(ov[r["code"]]["sc"])}</a></div>' if r["code"] in ov and ov[r["code"]]["sc"] else "")
            + report_box(conn, r["code"], r["name"], reps, i["close"])
            + _ext_line(i) + _pos_block(i) + f'{sparkline(pr, i["avg"])}</div>')
    body = (tabs + form + note + f"<h2>{chart_title}</h2>" + chart
            + '<div class="legend"><span><i class="ln" style="background:var(--price)"></i>최근 120거래일 종가</span>'
              '<span><i class="dash"></i>기관 평균매수단가</span></div>' + "".join(cards))
    return page("아이디어", body)


# ---------------- 종목 순위 ----------------
RANK_MAX = 92          # 항목별 최고점의 합 (기관 24 + 평단 15 + 목표가 15 + 수급 10 + 밸류 10 + 지분 8 + 운용사 성적 10)
RANK_SORTS = {"": "정렬: 종합 점수순", "up": "정렬: 목표가 상승여력순", "disc": "정렬: 기관 평단보다 싼 순",
              "amt": "정렬: 기관 매수금액순", "n": "정렬: 함께 산 기관 수순", "card": "정렬: 점수표 점수순"}
RANK_HELP = (
    "<b>기관이 함께 산 종목</b> 1곳당 8점 (최대 24점) · "
    "<b>현재가가 기관 평균매수단가보다 쌀수록</b> 최대 15점 (평단보다 10% 이상 비싸면 -5점) · "
    "<b>증권사 목표가 상승여력</b> 최대 15점 (2곳 이상 의견이 있을 때만) · "
    "<b>최근 5거래일 순매수</b> 외국인·기관 각 +5점 (순매도면 각 -3점) · "
    "<b>PER·PBR</b> 낮을수록 최대 10점 (높으면 감점) · "
    "<b>공시 기간 안 지분을 늘린 곳 − 줄인 곳</b> 1곳당 ±4점 (최대 ±8점) · "
    "<b>산 운용사의 과거 성적</b> 90일 뒤 코스피 대비 평균 초과수익, 이 종목을 뺀 5건 이상 기록이 있는 곳만 (−5~+10점)")


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rank_stocks(conn, w, ex, m):
    """공시에 나온 종목에 항목별 점수를 매겨 합침. 없는 데이터는 0점이고 '없음'으로 표시."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    since = (dt.date.today() - dt.timedelta(days=int(w))).isoformat()
    conds, args = ["변동일 >= ?", "매수수량 > 0", "평균매수단가 > 0"], [since]
    if ex == "1":
        conds.append("제출인 NOT LIKE '%국민연금%'")
    if m and "시장" in cols:
        conds.append("시장 = ?")
        args.append(m)
    market = "MAX(시장)" if "시장" in cols else "''"
    rows = conn.execute(
        f"SELECT 종목코드 code, MAX(종목명) name, {market} mk, COUNT(DISTINCT 제출인) n, "
        f"GROUP_CONCAT(DISTINCT 제출인) whos, SUM(매수수량) qty, SUM(매수금액) amt, MAX(변동일) last "
        f"FROM summary WHERE {' AND '.join(conds)} GROUP BY 종목코드", args).fetchall()
    closes = latest_closes(conn)
    rows = [r for r in rows if r["code"] in closes and r["qty"]]
    codes = [r["code"] for r in rows]
    vals = value_map(conn)
    try:
        import stock_value
        fsum = stock_value.flow_sums(conn, codes, 5)
    except Exception:
        fsum = {}
    nets = {}
    try:
        for code, who, d in conn.execute(
                "SELECT 종목코드, 제출인, SUM(증감) FROM detail WHERE 변동일 >= ? AND 증감 IS NOT NULL "
                "GROUP BY 종목코드, 제출인", (since,)):
            if ex == "1" and who and "국민연금" in who:
                continue
            nets.setdefault(code, {})[who] = d or 0
    except sqlite3.OperationalError:
        pass
    # 산 운용사의 과거 성적(성적표와 같은 계산, 90일 뒤). 이 종목의 기록은 빼고 평균을 내서 자기 결과가 되먹임되지 않게 함
    try:
        evs = perf_compute(conn, "90", ex, "")
    except Exception:
        evs = []
    tot, tot_code = {}, {}
    for e in evs:
        a = tot.setdefault(e["who"], [0, 0.0])
        a[0] += 1
        a[1] += e["x"]
        b = tot_code.setdefault((e["who"], e["code"]), [0, 0.0])
        b[0] += 1
        b[1] += e["x"]
    # 점수표 점수: 직접 평가한 종목은 총점, 아니면 PER·PBR·배당 3항목(35점 만점)
    card_of = {}
    try:
        import score_card as sc, score_auto
        evaluated = dict(sc.evaluated(conn))
        allans, allauto = sc.load_all(conn), score_auto.load_auto_all(conn)
        for code in codes:
            v = vals.get(code) or {}
            if code in evaluated:
                rr = sc.compute(v, allans.get(code, {}), allauto.get(code))
                card_of[code] = dict(kind="eval", total=rr["total"], grade=rr["grade"][0], full=rr["full"])
            elif any(v.get(k) is not None for k in ("per", "pbr", "div")):
                card_of[code] = dict(kind="auto", total=sc.auto_total(v)[0])
    except Exception:
        card_of = {}
    out = []
    for r in rows:
        code = r["code"]
        close = closes[code][1]
        avg = r["amt"] / r["qty"]
        disc = close / avg - 1
        f, missing = [], []                         # f: (이름, 점수, 설명)
        f.append(("기관 동시 매수", min(r["n"], 3) * 8, f'{r["n"]}곳'))
        f.append(("평단 대비", round(_clamp(-disc * 100 * 0.5, -5, 15), 1), f"{disc:+.1%}"))
        tgt, nb = consensus(conn, code)
        up = None
        if tgt and nb >= 2:
            up = float(tgt) / float(close) - 1
            f.append(("목표가 여력", round(_clamp(up * 100 * 0.4, 0, 15), 1), f"{up:+.0%} ({nb}곳)"))
        else:
            missing.append("목표가")
        if code in fsum:
            fo, ins = fsum[code]
            pts = (5 if fo > 0 else -3 if fo < 0 else 0) + (5 if ins > 0 else -3 if ins < 0 else 0)
            f.append(("수급(5일)", pts, f"외국인 {fo:+,.0f} · 기관 {ins:+,.0f}"))
        else:
            missing.append("수급")
        v = vals.get(code) or {}
        per, pbr = v.get("per"), v.get("pbr")
        if per is not None or pbr is not None:
            pts, txt = 0, []
            if pbr is not None:
                pts += 5 if 0 < pbr <= 1 else 2 if 0 < pbr <= 1.5 else -3 if pbr > 5 else 0
                txt.append(f"PBR {pbr:.2f}")
            if per is not None:
                pts += 5 if 0 < per <= 10 else 2 if 0 < per <= 15 else -3 if per > 30 else 0
                txt.append(f"PER {per:.1f}")
            f.append(("밸류", pts, " · ".join(txt)))
        else:
            missing.append("PER·PBR")
        net = nets.get(code)
        if net:
            u = sum(1 for x in net.values() if x > 0)
            d = sum(1 for x in net.values() if x < 0)
            f.append(("지분 증감", _clamp((u - d) * 4, -8, 8), f"늘린 {u}곳 · 줄인 {d}곳"))
        else:
            missing.append("지분")
        means = []
        for who in (r["whos"] or "").split(","):
            n_all, x_all = tot.get(who, (0, 0.0))
            n_c, x_c = tot_code.get((who, code), (0, 0.0))
            if n_all - n_c >= PERF_MIN_N:
                means.append((x_all - x_c) / (n_all - n_c))
        if means:
            mean = sum(means) / len(means)
            f.append(("운용사 성적", round(_clamp(mean * 100 * 0.5, -5, 10), 1), f"초과수익 평균 {mean:+.1%} ({len(means)}곳)"))
        else:
            missing.append("운용사 성적")
        out.append(dict(r=r, close=close, avg=avg, disc=disc, up=up, f=f, missing=missing,
                        score=round(sum(x[1] for x in f), 1), card=card_of.get(code)))
    return out


def _rank_card_line(i):
    """순위 카드에 점수표 점수 한 줄 (직접 평가한 종목은 총점·등급, 아니면 자동 3항목 점수와 평가하기 링크)"""
    c = i.get("card")
    link = score_url(code=i["r"]["code"])
    if not c:
        return f'<div class="muted"><a href="{link}">점수표로 평가하기 →</a></div>'
    if c["kind"] == "eval":
        return (f'<div style="margin-top:4px">점수표 <b>{c["total"]:g}점</b> {c["grade"]}등급'
                f'{"" if c["full"] else " (잠정)"} · <a href="{link}">자세히</a></div>')
    return (f'<div class="muted" style="margin-top:4px">점수표 자동 3항목 {c["total"]:g}/35점 · '
            f'<a href="{link}">직접 평가하기 →</a></div>')


def view_ranking(conn, q):
    if not has_table(conn, "summary") or not has_table(conn, "prices"):
        return page("종목 순위", '<div class="card">보고서(DART)와 주가 수집이 한 번 끝나야 볼 수 있습니다. '
                              '위쪽 <b>지금 수집 → 보고서만</b>을 눌러 주세요.</div>')
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    w = q.get("w") if q.get("w") in WINDOWS else "90"
    ex = q.get("ex") if q.get("ex") in NPS_OPTS else "1"
    m = q.get("m") if q.get("m") in MARKET_OPTS and "시장" in cols else ""
    srt = q.get("s") if q.get("s") in RANK_SORTS else ""
    items = rank_stocks(conn, w, ex, m)
    key = {"": lambda i: -i["score"], "up": lambda i: (i["up"] is None, -(i["up"] or 0)),
           "disc": lambda i: i["disc"], "amt": lambda i: -i["r"]["amt"],
           "n": lambda i: (-i["r"]["n"], -i["score"]),
           "card": lambda i: ((i["card"] or {}).get("kind") != "eval", i["card"] is None, -(i["card"] or {}).get("total", 0))}[srt]
    items = sorted(items, key=key)
    form = (f'<form method="get" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="rank">{select("w", WINDOWS, w)}'
            + (select("m", MARKET_OPTS, m) if "시장" in cols else "")
            + f'{select("ex", NPS_OPTS, ex)}{select("s", RANK_SORTS, srt)}<button>적용</button></form>')
    intro = ('<div class="card"><b>좋은 종목 순위</b><div class="muted" style="margin-top:4px">'
             '기관·개인 5% 공시와 주가, 증권사 목표가, 수급, PER·PBR을 항목별로 점수 내서 더한 순위예요. '
             '살펴볼 후보를 추리는 용도예요.</div>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 점수와 항목별 비중은 제가 정한 단순 기준이고, '
             '과거 수익률로 검증한 것이 아니에요. 점수가 높다고 오른다는 뜻이 아니고, 투자 추천이 아니에요. '
             '공시는 매매 후 최대 5영업일 뒤에 나와요.</div>'
             f'<details style="margin-top:6px"><summary class="muted">점수는 이렇게 계산해요</summary>'
             f'<div class="muted" style="margin-top:4px">{RANK_HELP}. 최고 {RANK_MAX}점.</div></details></div>')
    cards = []
    for k, i in enumerate(items[:30], 1):
        r = i["r"]
        mk = f' <span class="chip">{escape(r["mk"])}</span>' if r["mk"] else ""
        chips = " ".join(
            f'<span class="chip {"up" if pts > 0 else "down" if pts < 0 else ""}" title="{escape(txt)}">'
            f'{escape(name)} {pts:+g}</span>' for name, pts, txt in i["f"])
        detail = " · ".join(f"{escape(name)} {escape(txt)}" for name, pts, txt in i["f"])
        miss = (f'<div class="muted">데이터 없음(0점 처리): {escape(", ".join(i["missing"]))}</div>' if i["missing"] else "")
        pct = _clamp(i["score"] / RANK_MAX * 100, 0, 100)
        cards.append(
            f'<div class="card"><div class="row"><div><span class="num">{k}</span>'
            f'<a class="big" href="/stock?code={quote(r["code"])}">{escape(r["name"])}</a> '
            f'<span class="muted">{escape(r["code"])}</span>{mk}</div>'
            f'<div><b class="big">{i["score"]:g}</b><span class="muted">점</span></div></div>'
            f'<div style="height:5px;background:var(--chip);border-radius:3px;margin:6px 0">'
            f'<div style="height:5px;width:{pct:.0f}%;background:var(--accent);border-radius:3px"></div></div>'
            f'<div>{chips}</div><div class="muted" style="margin-top:4px">{detail}</div>'
            + _rank_card_line(i) +
            f'<div class="muted">기관 매수 {eok(r["amt"])} · 평단 {won(i["avg"])}원 · 종가 {won(i["close"])}원 · '
            f'마지막 매수 {escape(r["last"])}</div>{miss}</div>')
    body = (intro + form + ("".join(cards) or '<p class="muted">조건에 맞는 종목이 없어요. 기간을 늘려 보세요.</p>'))
    return page("종목 순위", body)


# ---------------- 점수표 (오일전문가 100점 점수표) ----------------
_DB = {"path": None, "get_key": None}    # serve()가 채움: 점수표에서 값을 새로 받아 저장할 때 씀 (DART 키 포함)
SCORE_CODE = re.compile(r"[0-9A-Za-z]{6}")
SCORE_TABS = {"mine": "내가 평가한 종목", "auto": "자동 3항목 순위 (35점 만점)"}


def _score_names(conn):
    names = {}
    try:
        names.update({r[0]: r[1] for r in conn.execute("SELECT 종목코드, MAX(종목명) FROM summary GROUP BY 종목코드")})
    except sqlite3.OperationalError:
        pass
    try:
        for c, n in conn.execute("SELECT code, name FROM scorecard_stock"):
            names.setdefault(c, n)
    except sqlite3.OperationalError:
        pass
    return names


def score_url(**kw):
    """점수표는 아이디어 탭 안에 있음"""
    return "/ideas?" + urlencode({"t": "score", **kw})


def idea_tabs_row(active="score"):
    return '<div style="margin:0 0 8px">' + idea_tab_html(active) + '</div>'


def score_search_form(q=""):
    return ('<form method="get" action="/ideas" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="score">'
            f'<input type="search" name="q" value="{escape(q)}" placeholder="종목 이름 또는 6자리 코드 (예: 005930)">'
            '<button>점수 보기</button></form>')


def score_nav(active=""):
    tabs = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == active else
         f'<a class="chip" href="{score_url(view="rank", st=k)}">{v}</a>') for k, v in SCORE_TABS.items())
    return f'<div style="margin:8px 0">{tabs}</div>'


def _pts(p, mx):
    if p is None:
        return '<span class="muted">-</span>'
    cls = "up" if p == mx else "down" if p == 0 else ""
    return f'<b class="{cls}">{p:g}</b><span class="muted">/{mx}</span>'


def score_detail(conn, code, name, note=""):
    import score_card as sc
    import score_auto
    vals = value_map(conn).get(code) or {}
    answers = sc.load(conn, code)
    r = sc.compute(vals, answers, score_auto.load_auto(conn, code))
    g, gtext = r["grade"]
    pct = _clamp(r["total"], 0, 100)
    fetched = ""
    try:
        row = conn.execute("SELECT fetched FROM valuation WHERE code=?", (code,)).fetchone()
        fetched = row[0] if row else ""
    except sqlite3.OperationalError:
        pass
    in_dart = False
    try:
        in_dart = bool(conn.execute("SELECT 1 FROM summary WHERE 종목코드=? LIMIT 1", (code,)).fetchone())
    except sqlite3.OperationalError:
        pass
    links = (f'<a href="/stock?code={quote(code)}">공시 화면</a> · ' if in_dart else "") + f'<a href="{naver_link(code)}">네이버 증권 ↗</a>'
    head = (f'<div class="row"><div><span class="big">{escape(name or code)}</span> <span class="muted">{escape(code)}</span></div>'
            f'<div>{links}</div></div>')
    if r["full"]:
        status = (f'<div class="muted">13개 항목을 모두 반영한 점수예요. (자동 {3 + r["n_auto"]}개 · 직접 {r["n_manual"]}개)</div>')
    else:
        gb, _ = r["grade_best"]
        status = (f'<div class="muted">{r["n_known"]}/{sc.N_ITEMS}개 항목 기준이에요 (자동 {3 + r["n_auto"]}개 · 직접 {r["n_manual"]}개). '
                  f'못 채운 {len(r["missing"])}개를 모두 만점으로 받아도 최대 <b>{r["best"]:g}점 ({gb}등급)</b>이에요. '
                  f'아래에서 고르고 저장하면 점수가 바뀌어요.</div>')
    top = (f'<div class="card"><div class="row"><div><span class="big" style="font-size:30px">{r["total"]:g}</span>'
           f'<span class="muted"> / 100점</span></div>'
           f'<div><b class="big">{g}등급</b>{"" if r["full"] else " <span class=chip>잠정</span>"}'
           f'<div class="muted">{escape(gtext)}</div></div></div>'
           f'<div style="height:6px;background:var(--chip);border-radius:3px;margin:8px 0">'
           f'<div style="height:6px;width:{pct:.0f}%;background:var(--accent);border-radius:3px"></div></div>{status}</div>')
    blocks = []
    for sec in sc.SECTIONS:
        d = r["sections"][sec]
        rows = []
        for it in d["items"]:
            if it["auto"]:
                right = f'<b>{escape(it["text"])}</b> → {_pts(it["points"], it["max"])}점'
                sub = f'<div class="muted" style="font-size:12px">{escape(it["rule"])}</div>'
                if it["points"] is None:
                    sub += '<div class="warn" style="font-size:12px">값을 받지 못해 점수에 넣지 않았어요</div>'
            else:
                first = f"자동 추정 사용 ({it['auto_text']})" if it["auto_text"] else "미입력"
                opts = f'<option value="">{escape(first)}</option>' + "".join(
                    f'<option value="{escape(v)}"{" selected" if v == it["value"] else ""}>{escape(lbl)} ({pt}점)</option>'
                    for v, lbl, pt in it["options"])
                right = f'<select name="{it["key"]}" style="max-width:100%">{opts}</select> {_pts(it["points"], it["max"])}점'
                sub = f'<div class="muted" style="font-size:12px">{escape(it["help"])}</div>' if it["help"] else ""
                if it["source"] == "자동":
                    sub += (f'<div style="font-size:12px"><span class="chip">자동 추정 · {escape(it["auto_from"])}</span> '
                            f'<span class="muted">{escape(it["auto_note"])}</span></div>')
                elif it["source"] == "직접":
                    sub += '<div style="font-size:12px"><span class="chip">직접 선택</span>'
                    if it["auto_text"]:
                        sub += f' <span class="muted">자동 추정은 \'{escape(it["auto_text"])}\' ({escape(it["auto_note"])})</span>'
                    sub += '</div>'
            rows.append(f'<div style="padding:7px 0;border-top:1px solid var(--line)"><div class="row"><div>{escape(it["name"])}'
                        f' <span class="muted">(만점 {it["max"]})</span></div><div>{right}</div></div>{sub}</div>')
        blocks.append(f'<div class="card"><div class="row"><b>{escape(d["name"])}</b>'
                      f'<span>{d["got"]:g}<span class="muted">/{d["max"]}점</span></span></div>{"".join(rows)}</div>')
    form = (f'<form method="post" action="/score/save"><input type="hidden" name="code" value="{escape(code)}">'
            f'<input type="hidden" name="name" value="{escape(name or "")}">' + "".join(blocks)
            + '<button style="width:100%;padding:11px">저장하고 내 순위에 넣기</button></form>'
            + ('<form method="post" action="/score/save" style="margin-top:8px">'
               f'<input type="hidden" name="code" value="{escape(code)}"><input type="hidden" name="remove" value="1">'
               '<button class="ghost" style="width:100%">내 순위에서 빼기 (직접 고른 값도 지움)</button></form>'
               if code in dict(sc.evaluated(conn)) else ""))
    foot = ('<p class="muted">PER·PBR·배당수익률은 네이버 증권 값이고 '
            + (f'{escape(fetched)}에 받았어요. ' if fetched else "") +
            '점수 기준은 \'오일전문가 점수표\'를 그대로 썼어요. 표에 없는 경우(적자·자본잠식·배당 없음)는 0점으로 봤어요. '
            '\'자동 추정\' 항목은 재무·공시 숫자로 계산한 추정값이라 틀릴 수 있고, 직접 고르면 그 값이 우선해요. '
            '투자 추천이 아니에요.</p>')
    return page(f"{name or code} — 점수표", idea_tabs_row() + score_search_form() + (note or "") + head + top + form + foot)


def score_rank(conn, q):
    import score_card as sc
    tab = q.get("st") if q.get("st") in SCORE_TABS else "mine"
    names = _score_names(conn)
    vals = value_map(conn)
    rows = []
    if tab == "mine":
        import score_auto
        allans, allauto = sc.load_all(conn), score_auto.load_auto_all(conn)
        for code, name in sc.evaluated(conn):
            r = sc.compute(vals.get(code) or {}, allans.get(code, {}), allauto.get(code))
            rows.append((r["total"], r["best"], code, names.get(code) or name or code, r))
        rows.sort(key=lambda x: (-x[0], -x[1], x[3]))
        cards = []
        for k, (total, best, code, name, r) in enumerate(rows, 1):
            g, gtext = r["grade"]
            cards.append(
                f'<div class="card"><div class="row"><div><span class="num">{k}</span>'
                f'<a class="big" href="{score_url(code=code)}">{escape(name)}</a> <span class="muted">{escape(code)}</span></div>'
                f'<div><b class="big">{total:g}</b><span class="muted">점</span> <span class="chip">{g}등급'
                f'{"" if r["full"] else " 잠정"}</span></div></div>'
                f'<div class="muted">{r["n_known"]}/{sc.N_ITEMS}개 항목 반영'
                + ("" if r["full"] else f" · 최대 {best:g}점까지 가능") + f' · {escape(gtext)}</div></div>')
        body = (score_nav(tab) + ("".join(cards) or '<p class="muted">아직 직접 평가한 종목이 없어요. 위에서 종목을 찾아 항목을 고르고 저장하면 여기에 모여요.</p>')
                + '<p class="muted">점수는 입력한 항목만 더한 값이에요. 못 채운 항목은 0점으로 더하지 않고 \'잠정\'으로 표시해요.</p>')
    else:
        for code, v in vals.items():
            tot, miss = sc.auto_total(v)
            rows.append((tot, miss, code, names.get(code) or code, v))
        rows.sort(key=lambda x: (-x[0], x[1], x[4].get("per") is None or x[4]["per"] <= 0, x[4].get("per") or 0, x[3]))
        cards = []
        for k, (tot, miss, code, name, v) in enumerate(rows[:50], 1):
            ap = sc.auto_points(v)
            chips = " ".join(
                f'<span class="chip">{label} {txt} {ap[key] if ap[key] is not None else "-"}점</span>'
                for key, label, txt in (
                    ("per", "PER", f'{v["per"]:.1f}' if v.get("per") is not None else "-"),
                    ("pbr", "PBR", f'{v["pbr"]:.2f}' if v.get("pbr") is not None else "-"),
                    ("dy", "배당", f'{v["div"]:.2f}%' if v.get("div") is not None else "-")))
            cards.append(
                f'<div class="card"><div class="row"><div><span class="num">{k}</span>'
                f'<a class="big" href="{score_url(code=code)}">{escape(name)}</a> <span class="muted">{escape(code)}</span></div>'
                f'<div><b class="big">{tot:g}</b><span class="muted">/{sc.AUTO_MAX}점</span></div></div><div>{chips}</div></div>')
        body = (score_nav(tab) + '<div class="card"><div class="muted">PER·PBR·배당수익률 3항목(35점)만 자동으로 계산한 순위예요. '
                '나머지 65점은 직접 판단해야 해서 들어 있지 않아요. 점수가 같으면 PER이 낮은 순이에요. '
                '적자·자본잠식 기업은 0점 처리돼서 아래로 가요.</div></div>'
                + ("".join(cards) or '<p class="muted">받아 둔 PER·PBR 값이 아직 없어요. 시장·정책 수집을 한 번 돌려 주세요.</p>'))
    return page("점수표 순위", idea_tabs_row() + body)


def view_score(conn, q):
    """아이디어 → 점수표: 종목 검색, 종목 하나의 점수, 점수 순위"""
    if q.get("view") == "rank":
        return score_rank(conn, q)
    raw = (q.get("code") or q.get("q") or "").strip()
    if not raw:
        import score_card as sc
        mine = sc.evaluated(conn)
        names = _score_names(conn)
        recent = "".join(f'<a class="chip" href="{score_url(code=c)}">{escape(names.get(c) or n or c)}</a> ' for c, n in mine[:12])
        intro = ('<div class="card"><b>오일전문가 점수표</b><div class="muted" style="margin-top:4px">'
                 '종목을 넣으면 100점 만점으로 몇 점인지 계산해요. PER·PBR·배당수익률(35점)은 자동이고, '
                 '나머지 10개 항목(65점)은 직접 골라요. 80점 초과 A · 70~80 B · 50~70 C · 50 미만 D.</div></div>')
        return page("점수표", idea_tabs_row() + score_search_form() + intro + score_nav("")
                    + (f'<h2>최근 평가한 종목</h2><div>{recent}</div>' if recent else ""))
    # 종목 찾기: 6자리 코드면 그대로, 아니면 이름으로 찾음
    if SCORE_CODE.fullmatch(raw):
        code = raw.upper()
        names = _score_names(conn)
        name = names.get(code)
        err = ""
        path = _DB["path"]
        if path and Path(path).exists():
            try:
                import stock_value
                got, err = stock_value.refresh(path, code)
                name = got or name
            except Exception as e:
                err = f"값을 받지 못했어요 ({str(e)[:60]})"
        errs = [err] if err else []
        if path and Path(path).exists():
            try:
                import score_auto
                key = _DB["get_key"]() if _DB.get("get_key") else ""
                errs += score_auto.refresh(path, code, dart_key=key,
                                           cache_dir=str(Path(_DATA_DIR["path"] or ".") / "dart_cache"))
            except Exception as e:
                errs.append(f"자동 추정을 만들지 못했어요 ({str(e)[:60]})")
        note = ('<div class="card warn">' + "<br>".join(escape(e) for e in errs)
                + '<div class="muted">예전에 받아 둔 값이 있으면 그걸로 보여 드려요. 못 받은 항목은 직접 고를 수 있어요.</div></div>') if errs else ""
        if q.get("saved"):
            note = '<div class="card">✔ 저장했어요.</div>' + note
        return score_detail(conn, code, name, note)
    names = _score_names(conn)
    hits = [(c, n) for c, n in names.items() if raw.replace(" ", "") in (n or "").replace(" ", "")]
    if len(hits) == 1:
        return score_detail(conn, hits[0][0], hits[0][1], "")
    if not hits:
        return page("점수표", idea_tabs_row() + score_search_form(raw) + '<div class="card">이름으로 찾지 못했어요. 공시에 나온 종목이나 이미 평가한 종목만 이름으로 찾을 수 있어요. '
                    '다른 종목은 <b>6자리 종목 코드</b>(예: 삼성전자 005930)로 넣어 주세요.</div>')
    links = "".join(f'<div class="card"><a class="big" href="{score_url(code=c)}">{escape(n)}</a> '
                    f'<span class="muted">{escape(c)}</span></div>' for c, n in sorted(hits, key=lambda h: h[1] or "")[:30])
    return page("점수표", idea_tabs_row() + score_search_form(raw) + f'<p class="muted">{len(hits)}개 종목이 맞아요. 하나를 골라 주세요.</p>' + links)


# ---------------- 대비 레이더 (뜬금없는 외부 변수) ----------------
RADAR_FILTERS = {"": "진행·감시 중", "done": "반영됨·해소", "all": "전체"}
RADAR_HINTS = (
    "외국인 자금의 국적별 순매수(영국·미국 등) · 해외 세제·연금·상속세 개편 · 국내 세제·건보료(배당·연금·코인) · "
    "결제 주기·거래시간·공매도 같은 시장 제도 · 지수 편입·제외 심사(MSCI·FTSE) · 환율·금리 · 산업 규제")


def _radar_form(it=None, action="/radar/add", label="추가"):
    it = it or {}
    v = lambda k: escape(str(it.get(k) or ""))
    import radar as rd
    opt = lambda items, cur: "".join(f'<option value="{escape(x)}"{" selected" if x == cur else ""}>{escape(x)}</option>' for x in items)
    return (f'<form method="post" action="{action}" style="margin-top:8px">'
            + (f'<input type="hidden" name="id" value="{it["id"]}">' if it.get("id") else "")
            + f'<input type="text" name="title" value="{v("title")}" placeholder="제목 (예: 영국 연금 상속세 과세)" style="width:100%" required>'
            f'<div class="filters" style="margin-top:6px"><select name="kind">{opt(rd.KINDS, it.get("kind"))}</select>'
            f'<select name="direction">{opt(rd.DIRECTIONS, it.get("direction"))}</select>'
            f'<input type="date" name="when_date" value="{v("when_date")}" title="시행·발생일 (모르면 비워 두세요)">'
            f'<input type="text" name="when_note" value="{v("when_note")}" placeholder="날짜 메모 (예: 내년 4월부터)" style="flex:1 1 160px"></div>'
            f'<textarea name="summary" rows="4" placeholder="무슨 일인지 (기사 요약)">{v("summary")}</textarea>'
            f'<textarea name="impact" rows="2" placeholder="주식시장에 어떤 영향이 있을 것 같은지 (가설)" style="margin-top:6px">{v("impact")}</textarea>'
            f'<textarea name="check_items" rows="2" placeholder="확인할 지표 (예: 영국계 월간 순매도, 환율)" style="margin-top:6px">{v("check_items")}</textarea>'
            f'<textarea name="prep" rows="2" placeholder="대비 메모 (내가 할 일)" style="margin-top:6px">{v("prep")}</textarea>'
            f'<input type="text" name="source" value="{v("source")}" placeholder="출처 (예: 매일경제 2026-11-06)" style="width:100%;margin-top:6px">'
            f'<input type="text" name="source_url" value="{v("source_url")}" placeholder="기사 주소 (선택)" style="width:100%;margin-top:6px">'
            f'<label class="set"><input type="checkbox" name="unverified" value="1"{" checked" if it and not it.get("verified", 1) else ""}> '
            '확인 필요 (제목만 보고 적었어요)</label>'
            f'<button style="margin-top:6px">{label}</button></form>')


def _radar_card(it):
    import radar as rd
    dday = rd.dday_text(it["dday"])
    dcls = "warn" if it["dday"] is not None and 0 <= it["dday"] <= 30 else "muted"
    chips = (f'<span class="chip">{escape(it["kind"])}</span> '
             f'<span class="chip {"down" if it["direction"] == "악재 우려" else "up" if it["direction"] == "호재 가능" else ""}">{escape(it["direction"])}</span> '
             f'<span class="chip">{escape(it["status"])}</span>'
             + ('' if it["verified"] else ' <span class="chip warn">확인 필요</span>'))
    when = (f'<div class="{dcls}"><b>{escape(dday)}</b> · {escape(it["when_date"])}' if dday else '<div class="muted">날짜 미정')
    when += (f' · {escape(it["when_note"])}' if it["when_note"] else "") + "</div>"
    blocks = "".join(
        f'<div style="margin-top:6px"><b style="font-size:13px">{label}</b><div style="white-space:pre-wrap">{escape(it[k])}</div></div>'
        for label, k in (("무슨 일", "summary"), ("영향 가설", "impact"), ("확인할 지표", "check_items"), ("대비 메모", "prep")) if it[k])
    src = ""
    if it["source"] or it["source_url"]:
        t = escape(it["source"] or "출처")
        src = (f'<div class="muted" style="margin-top:6px">출처: '
               + (f'<a href="{escape(it["source_url"])}">{t}</a>' if it["source_url"] else t) + "</div>")
    status_form = ('<form method="post" action="/radar/status" class="filters" style="margin:8px 0 0">'
                   f'<input type="hidden" name="id" value="{it["id"]}">'
                   + select("status", {s_: s_ for s_ in rd.STATUSES}, it["status"]) + '<button class="ghost">상태 바꾸기</button></form>')
    manage = (f'<details style="margin-top:6px"><summary class="muted">수정 · 삭제</summary>'
              + _radar_form(it, "/radar/edit", "수정 저장")
              + f'<form method="post" action="/radar/del" style="margin-top:6px" onsubmit="return confirm(\'이 변수를 지울까요?\')">'
                f'<input type="hidden" name="id" value="{it["id"]}"><button class="ghost">삭제</button></form></details>')
    return (f'<div class="card"><div class="row"><b class="big">{escape(it["title"])}</b></div>'
            f'<div style="margin:4px 0">{chips}</div>{when}{blocks}{src}{status_form}{manage}</div>')


def _radar_countries(conn):
    """대비 레이더 맨 위: 한국 주식을 많이 가진 외국인 국적별 영향력 지도"""
    import radar as rd
    rows, _ = rd.country_overview(conn)
    if not rows:
        return ""
    top5 = [r for r in rows[:5] if r["share"] is not None]
    top5_sum = sum(r["share"] for r in top5)
    asofs = sorted({r["asof"] for r in top5})
    mixed = len(asofs) > 1
    head = ('<div class="card"><b>국가별 영향력 지도</b><div class="muted" style="margin-top:4px">'
            '한국 상장주식을 많이 가진 외국인 투자자를 <b>국적별</b>로 봐요. 영향력은 <b>비중</b>(방향을 정하는 힘) × '
            '<b>매매 성향</b>(얼마나 자주·크게 움직이나) × <b>세제·정책 민감도</b>를 같이 봐야 해요. '
            '비중은 작아도 매매가 잦은 나라가 단기 흐름을 흔들기도 해요.</div>'
            f'<div style="margin-top:6px">상위 {len(top5)}개국 합계 <b>{top5_sum:.1f}%</b>'
            + (f' <span class="muted">(기준월 {escape(asofs[0])}~{escape(asofs[-1])} 혼합)</span>' if mixed
               else f' <span class="muted">({escape(asofs[0])} 기준)</span>')
            + f' · 1위 {escape(top5[0]["country"])} 단독 <b>{top5[0]["share"]:.1f}%</b></div>'
            '<div class="muted" style="margin-top:4px">숫자는 금융감독원 \'외국인 증권투자 동향\' 집계를 인용한 언론 보도에서 가져왔어요 '
            '(원문은 직접 확인하지 못했어요). 해석과 민감 요인은 \'가설\'이에요. 최신 숫자는 금감원 보도자료로 고쳐 주세요.</div></div>')
    cards = []
    for k, r in enumerate(rows, 1):
        pr = r["profile"] or {}
        share = r["share"]
        bar = (f'<div style="height:6px;background:var(--chip);border-radius:3px;margin:6px 0">'
               f'<div style="height:6px;width:{_clamp(share / 41 * 100 if share else 0, 2, 100):.0f}%;background:var(--accent);border-radius:3px"></div></div>')
        change = ""
        if r["prev_share"] is not None and share is not None:
            d = share - r["prev_share"]
            change = f' <span class="muted">({r["prev_share"]:.1f}% → {d:+.1f}%p)</span>'
        last = r["flows"][0] if r["flows"] else None
        lastchip = (f' <span class="chip {"down" if last[1] < 0 else "up"}">{escape(last[0])} {last[1]:+.1f}조</span>' if last else "")
        flows = "".join(
            f'<div class="muted">· {escape(per)}: <b class="{"down" if net < 0 else "up"}">{net:+,.1f}조원</b> <span style="font-size:12px">— {escape(src)}</span></div>'
            for per, net, src in r["flows"][:6])
        body = ""
        if pr:
            for label, key in (("해석(가설)", "read"), ("이런 소식이 나오면 의심", "triggers"), ("확인할 지표", "checks")):
                body += f'<div style="margin-top:6px"><b style="font-size:13px">{label}</b><div style="white-space:pre-wrap">{escape(pr[key])}</div></div>'
        cards.append(
            f'<details class="card"><summary><b>{k}. {escape(r["country"])}</b> '
            + (f'<b>{share:.1f}%</b>' if share is not None else "")
            + (' <span class="muted">(계산)</span>' if r["share_calc"] else "") + change
            + (f'<div class="muted">{escape(pr["type"])}</div>' if pr else "") + bar + lastchip + '</summary>'
            f'<div class="muted" style="margin-top:6px">{escape(r["asof"])} 보유 {r["holding"]:,.1f}조원 · {escape(r["source"])}</div>'
            + (f'<div style="margin-top:6px"><b style="font-size:13px">최근 순매매</b>{flows}</div>' if flows else "")
            + body + '</details>')
    upd = ('<details class="card"><summary>국가 현황 갱신 (금감원 보도자료가 나오면)</summary>'
           '<form method="post" action="/radar/countries" style="margin-top:8px">'
           '<textarea name="data" rows="6" placeholder="한 줄에 한 나라:&#10;국가,기준월,보유(조원),비중(%),순매매(조원),기간 이름&#10;미국,2026-10,720.5,41.3,-3.2,2026년 10월&#10;영국,2026-10,170"></textarea>'
           '<div class="muted" style="margin-top:4px">비중·순매매·기간 이름은 비워도 돼요 (비중을 비우면 \'외국인 전체\' 보유액 대비로 계산). '
           '\'외국인 전체,2026-10,총보유액\' 줄도 넣어 두면 비중이 맞게 계산돼요. 같은 나라·같은 기준월은 덮어써요.</div>'
           '<button style="margin-top:6px">반영</button></form></details>')
    return (head + "".join(cards) + f'<div class="muted" style="margin:6px 0">{escape(rd.COMMON)}</div>' + upd)


LONDON_IB = ("morgan stanley", "goldman sachs", "j.p. morgan", "jpmorgan", "merrill", "ubs", "barclays", "citigroup", "credit suisse",
             "deutsche", "bnp", "hsbc", "nomura", "macquarie", "societe generale", "bank of america", "international", " plc")


def _radar_checklist():
    """숫자가 맞아도 해석이 틀릴 수 있다: 해석 점검 체크리스트 + 규모 점검기 (브라우저에서 바로 계산)"""
    return (
        '<details class="card"><summary><b>해석 점검 · 규모 점검기</b></summary>'
        '<div class="muted" style="margin-top:6px">뉴스의 숫자가 맞아도 \'왜\'라는 해석은 틀릴 수 있어요. 이 영국 사례가 그랬어요. 변수를 적기 전에 네 가지를 먼저 물어보세요.</div>'
        '<div style="margin-top:6px">① <b>규모</b>: 이 원인이 설명할 수 있는 금액이 관찰된 금액만큼 되나요? (아래 점검기)<br>'
        '② <b>집계 기준</b>: 이 숫자의 \'국적·구분\'이 무엇을 뜻하나요? (등록지·펀드 거점·수탁은행이면 실제 돈의 주인과 다를 수 있어요)<br>'
        '③ <b>대안 설명</b>: 같은 숫자를 다르게 설명할 수 있나요? (예: 연금 인출 대신 글로벌 레버리지 자금 정리)<br>'
        '④ <b>시점</b>: 원인 사건보다 매도·매수가 먼저 시작되지 않았나요? 속도는 원인과 맞나요(완만한지 급한지)?</div>'
        '<div style="margin-top:8px"><b style="font-size:13px">규모 점검기</b> <span class="muted">(예시는 영국 연금 사례)</span></div>'
        '<div style="margin-top:6px">'
        '<div style="display:flex;gap:8px;align-items:center;margin-top:4px"><span class="muted" style="flex:1 1 11em">원인 후보 금액 (조원)</span><input type="number" id="sc_cause" value="40" step="any" style="width:7.5em"></div>'
        '<div style="display:flex;gap:8px;align-items:center;margin-top:4px"><span class="muted" style="flex:1 1 11em">한국 비중 하한 (%)</span><input type="number" id="sc_lo" value="1" step="any" style="width:7.5em"></div>'
        '<div style="display:flex;gap:8px;align-items:center;margin-top:4px"><span class="muted" style="flex:1 1 11em">한국 비중 상한 (%)</span><input type="number" id="sc_hi" value="2" step="any" style="width:7.5em"></div>'
        '<div style="display:flex;gap:8px;align-items:center;margin-top:4px"><span class="muted" style="flex:1 1 11em">관찰된 금액 (조원)</span><input type="number" id="sc_obs" value="97" step="any" style="width:7.5em"></div></div>'
        '<div id="sc_out" style="margin-top:6px"></div>'
        '<div class="muted" style="margin-top:4px">원인 후보 금액 = 원인이 움직인 돈(예: 영국 연금 인출 증가분 약 40조원). 한국 비중 = 그 돈이 한국 주식에 들어 있을 비율. '
        '계산 결과는 \'이 원인만으로 설명되는 최대치\'이고, 작다고 그 원인이 아예 없다는 뜻은 아니에요.</div>'
        '<script>(function(){const g=id=>parseFloat(document.getElementById(id).value);'
        'function run(){const c=g("sc_cause"),lo=g("sc_lo"),hi=g("sc_hi"),o=g("sc_obs"),out=document.getElementById("sc_out");'
        'if(!(c>=0&&lo>=0&&hi>=0&&o>0)){out.textContent="숫자를 넣어 주세요.";return;}'
        'const a=c*lo/100,b=c*hi/100,ra=a/o*100,rb=b/o*100;'
        'let v=rb<10?"이 원인만으로는 크기가 안 맞아요.":rb<50?"일부만 설명해요.":"상당 부분을 설명할 수 있어요.";'
        'out.innerHTML="설명되는 금액 <b>"+a.toFixed(1)+" ~ "+b.toFixed(1)+"조원</b> = 관찰된 "+o+"조원의 <b>"+ra.toFixed(1)+"% ~ "+rb.toFixed(1)+"%</b> → "+v;}'
        '["sc_cause","sc_lo","sc_hi","sc_obs"].forEach(id=>document.getElementById(id).addEventListener("input",run));run();})();</script>'
        '</details>')


def _radar_london_check(conn):
    """우리 5% 공시 데이터에서 대형 IB·'International'·'plc'가 들어간 제출인 (런던 법인 경유 헤지 물량 반론을 가늠해 보는 용도)"""
    try:
        like = " OR ".join("LOWER(제출인) LIKE ?" for _ in LONDON_IB)
        rows = conn.execute(
            f"SELECT 제출인, COUNT(DISTINCT 접수번호) n, SUM(증감) net, MIN(변동일) a, MAX(변동일) b FROM detail "
            f"WHERE {like} GROUP BY 제출인 ORDER BY n DESC LIMIT 12", [f"%{k}%" for k in LONDON_IB]).fetchall()
    except sqlite3.OperationalError:
        return ""
    body = "".join(
        f'<tr><td>{escape(str(r["제출인"]))}</td><td>{r["n"]}</td><td class="{"up" if (r["net"] or 0) > 0 else "down" if (r["net"] or 0) < 0 else ""}">'
        f'{(r["net"] or 0):+,.0f}</td><td>{escape(str(r["a"]))} ~ {escape(str(r["b"]))}</td></tr>' for r in rows)
    table = ('<div class="scroll"><table><tr><th>제출인</th><th>공시</th><th>순증감(주)</th><th>변동일</th></tr>' + body + '</table></div>'
             if rows else '<p class="muted">이름에 대형 투자은행·International·plc가 들어간 제출인이 아직 없어요.</p>')
    return ('<details class="card"><summary><b>데이터로 확인해 보기: 우리 공시에 잡힌 IB·해외 법인</b></summary>'
            '<div class="muted" style="margin-top:6px">영국계 순매도를 \'런던 법인이 스왑 헤지로 판 물량\'이라고 보는 반론이 맞는지, '
            '5% 공시에서 가늠해 보는 칸이에요. 이름에 대형 투자은행이나 International·plc가 들어간 제출인을 모았어요. '
            '이 앱은 \'단순투자\' 보고서만 모아서 일부만 보여요. 이름만으로 어느 나라 법인인지는 알 수 없어서 참고용이에요.</div>' + table + '</details>')


def view_radar(conn, q):
    import radar as rd
    path = _DB["path"]
    if path and Path(path).exists():
        try:
            w = sqlite3.connect(path)
            try:
                rd.seed(w)
                rd.seed_countries(w)
            finally:
                w.close()
        except sqlite3.Error:
            pass
    flt = q.get("f") if q.get("f") in RADAR_FILTERS else ""
    kind = q.get("k") if q.get("k") in rd.KINDS else ""
    items = rd.list_all(conn)
    shown = [i for i in items
             if (flt == "all" or (flt == "done") == (i["status"] in ("반영됨", "해소"))) and (not kind or i["kind"] == kind)]
    upcoming = sorted((i for i in shown if i["dday"] is not None and i["dday"] >= 0), key=lambda i: i["dday"])
    started = sorted((i for i in shown if i["dday"] is not None and i["dday"] < 0), key=lambda i: -i["dday"])
    undated = [i for i in shown if i["dday"] is None]
    chips = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{v}</b>' if k == flt else
         f'<a class="chip" href="/radar?{urlencode({"f": k, **({"k": kind} if kind else {})})}">{v}</a>')
        for k, v in RADAR_FILTERS.items())
    kform = ('<form method="get" action="/radar" class="filters" style="margin:6px 0">'
             + (f'<input type="hidden" name="f" value="{flt}">' if flt else "")
             + select("k", {"": "분류 전체", **{x: x for x in rd.KINDS}}, kind) + '<button class="ghost">보기</button></form>')
    intro = ('<div class="card"><b>대비 레이더</b><div class="muted" style="margin-top:4px">'
             '평소 주식 화면에는 안 보이지만 수급·시장을 흔들 수 있는 \'뜬금없는\' 외부 변수를 모아 두는 곳이에요. '
             '날짜가 있는 변수는 달력에 ★로 들어가서 하루 전 저녁에 알림이 와요.</div>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 영향은 \'가설\'이에요. 실제로 시장이 그렇게 움직인다는 보장이 없고, '
             '\'확인 필요\'는 기사 제목만 보고 적은 항목이라 본문으로 내용을 채워야 해요. 투자 추천이 아니에요.</div>'
             f'<details style="margin-top:6px"><summary class="muted">이런 변수를 찾아보세요</summary>'
             f'<div class="muted" style="margin-top:4px">{RADAR_HINTS}</div></details></div>')
    add = ('<details class="card"><summary>변수 직접 추가</summary>' + _radar_form() +
           '<div class="muted" style="margin-top:6px">기사를 읽다가 \'이게 주식에 영향 주겠는데?\' 싶은 걸 적어 두세요. '
           '날짜를 넣으면 달력에 들어가요.</div></details>')
    def section(title, rows):
        return (f'<h2>{title} <span class="muted" style="font-size:13px">({len(rows)})</span></h2>'
                + "".join(_radar_card(i) for i in rows)) if rows else ""
    notice = ""
    if q.get("cn"):
        notice = f'<div class="card">{escape(q["cn"])}</div>'
    body = (intro + notice + _radar_countries(conn) + _radar_checklist() + _radar_london_check(conn)
            + '<h2>대비 변수 목록</h2>' + f'<div style="margin:8px 0">{chips}</div>{kform}' + add
            + section("날짜가 다가오는 변수", upcoming) + section("이미 시작됐거나 지난 변수", started)
            + section("날짜 미정", undated)
            + ("" if shown else '<p class="muted">이 조건에 맞는 변수가 없어요.</p>'))
    return page("대비 레이더", body)


def _short_form(it=None, action="/shorts/add", label="추가"):
    import shortreports as sr
    it = it or {}
    v = lambda k: escape(str(it.get(k) or ""))
    opt = lambda items, cur: "".join(f'<option value="{escape(x)}"{" selected" if x == cur else ""}>{escape(x)}</option>' for x in items)
    return (f'<form method="post" action="{action}" style="margin-top:8px">'
            + (f'<input type="hidden" name="id" value="{it["id"]}">' if it.get("id") else "")
            + f'<input type="text" name="title" value="{v("title")}" placeholder="제목 (예: 컬퍼 리서치의 ○○ 보고서)" style="width:100%" required>'
            f'<div class="filters" style="margin-top:6px"><select name="region">{opt(sr.REGIONS, it.get("region"))}</select>'
            f'<select name="status">{opt(sr.STATUSES, it.get("status"))}</select>'
            f'<input type="date" name="report_date" value="{v("report_date")}" title="보고서 나온 날 (모르면 비워 두세요)"></div>'
            f'<div class="filters" style="margin-top:6px"><input type="text" name="firm" value="{v("firm")}" placeholder="공매도 투자자 (예: Culper Research)" style="flex:1 1 160px">'
            f'<input type="text" name="target" value="{v("target")}" placeholder="대상 기업" style="flex:1 1 160px"></div>'
            f'<textarea name="claims" rows="4" placeholder="보고서의 주장 (요약)">{v("claims")}</textarea>'
            f'<textarea name="response" rows="2" placeholder="회사 반응·반박" style="margin-top:6px">{v("response")}</textarea>'
            f'<input type="text" name="price_move" value="{v("price_move")}" placeholder="주가 변화 한 줄 (예: 당일 -18%, 이후 -30%)" style="width:100%;margin-top:6px">'
            f'<textarea name="reaction" rows="2" placeholder="주가 반응 자세히 (보고 전후 가격·날짜)" style="margin-top:6px">{v("reaction")}</textarea>'
            f'<textarea name="korea_link" rows="2" placeholder="한국 주식과의 연결 (가설)" style="margin-top:6px">{v("korea_link")}</textarea>'
            f'<input type="text" name="source" value="{v("source")}" placeholder="출처" style="width:100%;margin-top:6px">'
            f'<input type="text" name="source_url" value="{v("source_url")}" placeholder="원문·기사 주소 (선택)" style="width:100%;margin-top:6px">'
            f'<label class="set"><input type="checkbox" name="unverified" value="1"{" checked" if it and not it.get("verified", 1) else ""}> '
            '확인 필요 (기사 요약만 보고 적었어요)</label>'
            f'<button style="margin-top:6px">{label}</button></form>')


def _short_card(it):
    import shortreports as sr
    chips = (f'<span class="chip">{escape(it["region"])}</span> <span class="chip">{escape(it["status"])}</span>'
             + (f' <span class="chip down">{escape(it["price_move"])}</span>' if it.get("price_move") else '')
             + (' <span class="chip">자동 수집 후보</span>' if str(it.get("seed") or "").startswith("auto:") else '')
             + ('' if it["verified"] else ' <span class="chip warn">확인 필요</span>'))
    who = " · ".join(x for x in (escape(it["firm"]), escape(it["target"]), escape(it["report_date"])) if x)
    blocks = "".join(
        f'<div style="margin-top:6px"><b style="font-size:13px">{label}</b><div style="white-space:pre-wrap">{escape(it[k])}</div></div>'
        for label, k in (("보고서 주장", "claims"), ("회사 반응", "response"), ("주가 반응", "reaction"), ("한국 주식과의 연결 (가설)", "korea_link"))
        if it[k])
    src = ""
    if it["source"] or it["source_url"]:
        t = escape(it["source"] or "출처")
        src = (f'<div class="muted" style="margin-top:6px">출처: '
               + (f'<a href="{escape(it["source_url"])}">{t}</a>' if it["source_url"] else t) + "</div>")
    status_form = ('<form method="post" action="/shorts/status" class="filters" style="margin:8px 0 0">'
                   f'<input type="hidden" name="id" value="{it["id"]}">'
                   + select("status", {s_: s_ for s_ in sr.STATUSES}, it["status"]) + '<button class="ghost">상태 바꾸기</button></form>')
    manage = ('<details style="margin-top:6px"><summary class="muted">수정 · 삭제</summary>'
              + _short_form(it, "/shorts/edit", "수정 저장")
              + f'<form method="post" action="/shorts/del" style="margin-top:6px" onsubmit="return confirm(\'이 항목을 지울까요?\')">'
                f'<input type="hidden" name="id" value="{it["id"]}"><button class="ghost">삭제</button></form></details>')
    return (f'<div class="card"><b class="big">{escape(it["title"])}</b>'
            f'<div style="margin:4px 0">{chips}</div><div class="muted">{who}</div>{blocks}{src}{status_form}{manage}</div>')


def view_shorts(conn, q):
    import shortreports as sr
    path = _DB["path"]
    if path and Path(path).exists():
        try:
            w = sqlite3.connect(path)
            try:
                sr.seed(w)
            finally:
                w.close()
        except sqlite3.Error:
            pass
    reg = q.get("r") if q.get("r") in sr.REGIONS else ""
    items = sr.list_all(conn)
    counts = {r_: sum(1 for i in items if i["region"] == r_) for r_ in sr.REGIONS}
    shown = [i for i in items if not reg or i["region"] == reg]
    chips = " ".join(
        (f'<b class="chip" style="background:var(--accent);color:var(--bg)">{label}</b>' if k == reg else
         f'<a class="chip" href="/shorts{("?r=" + quote(k)) if k else ""}">{label}</a>')
        for k, label in [("", f"전체 {len(items)}")] + [(r_, f"{r_} {counts[r_]}") for r_ in sr.REGIONS])
    try:
        import shortauto
        last, n_auto = shortauto.status(conn)
    except Exception:
        last, n_auto = "", 0
    auto_line = ('<div class="muted" style="margin-top:6px"><b>자동 수집:</b> 수집할 때(하루 한 번) 뉴스 검색에서 숏셀러 보고서 기사를 찾아 후보로 올려요. '
                 '올라오는 건 기사 제목·링크·날짜뿐이고 \'자동 수집 후보\'·\'확인 필요\'가 붙어요. 관련 없으면 삭제하세요 (지운 기사는 다시 안 올라와요). '
                 + (f'마지막 확인 {escape(last)} · 후보 {n_auto}개.' if last else '아직 한 번도 확인하지 못했어요 (지금 수집 → 시장·정책만).') + '</div>')
    intro = ('<div class="card"><b>공매도 리포트</b><div class="muted" style="margin-top:4px">'
             '공매도 투자자(숏셀러)가 \'이 회사는 이상하다\'며 낸 조사 보고서를 나라별로 요약해 둔 곳이에요. '
             '주장 · 회사 반응 · 주가 반응을 나눠 적었어요.</div>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 숏셀러는 주가가 내려야 돈을 버는 쪽이라 보고서는 \'이해관계 있는 쪽의 주장\'이에요. '
             '사실로 확정된 게 아니고, 틀린 경우도 많아요. 여기 내용은 언론 보도 요약이라 원문을 직접 확인한 게 아니에요 '
             '(\'확인 필요\'는 더 불확실). 빨간 칩의 주가 변화도 보도에서 가져온 숫자예요(당일 등락률 위주, 이후 흐름은 다른 요인이 섞여요). 원문을 꼭 읽어 보세요. 투자 추천이 아니에요.</div>'
             '<div class="muted" style="margin-top:6px">한국 상장사를 직접 겨냥한 외국 숏 리포트는 찾지 못해서 한국 항목은 얇아요. '
             '새 보고서가 나오면 아래에서 직접 추가해 주세요.</div>' + auto_line + '</div>')
    add = ('<details class="card"><summary>보고서 직접 추가</summary>' + _short_form() + '</details>')
    body = (intro + f'<div style="margin:8px 0">{chips}</div>' + add + "".join(_short_card(i) for i in shown)
            + ("" if shown else '<p class="muted">이 나라 항목이 아직 없어요.</p>'))
    return page("공매도 리포트", body)


GURU_INFO = {
    "lynch": dict(
        title="피터 린치 방법",
        idea="이해할 수 있는 기업을 골라, 이익이 얼마나 빨리 크는지에 비해 주가가 싼지(PEG)를 봐요. 종목을 6가지 유형으로 나눠서 유형마다 다르게 접근해요.",
        how=["유형 분류: 급성장(이익 연 20%↑) · 우량(10~20%) · 저성장(~10%) · 경기순환(이익이 크게 오르내림) · 턴어라운드(적자에서 회복) · 자산주(PBR 낮음)",
             "PEG = PER ÷ 이익 성장률(EPS 3년 연평균, 50%까지만 인정). 1 아래면 매력적, 2 위면 비싸다고 봐요",
             "점수(100): PEG 35 · 성장률 20 · 부채비율 15 · 이익이 꾸준히 흑자 10 · ROE 10 · PER 상식 선 10"],
        miss="린치가 가장 중요하게 본 것은 숫자로 못 봐요: 내가 이해하는 사업인지, 제품·서비스가 눈에 보이는지, 아직 월가가 주목하지 않은 종목인지, 재고·현금 흐름 같은 세부 항목.",
        sorts={"": "정렬: 린치 점수순", "peg": "정렬: PEG 낮은 순", "growth": "정렬: 이익 성장률 높은 순"}),
    "buffett": dict(
        title="워렌 버핏 방법",
        idea="오래 가는 경쟁 우위가 있는 좋은 기업을, 합리적인 가격에 사요. 그런 기업의 흔적은 높은 ROE와 영업이익률, 낮은 부채, 꾸준히 느는 이익이에요.",
        how=["점수(100): ROE 25 · 부채비율 15 · 이익이 꾸준하고 늘어남 15 · 영업이익률 15 · 가격(PER) 15 · 이익 성장 15",
             "ROE 평균 15%↑이고 가장 낮은 해도 12%↑, 영업이익률 평균 15%↑, 이익이 꾸준히 흑자면 '해자 후보' 표시",
             "확정된 최근 3개 연도만 봐요 (버핏은 10년 이상 봐요)"],
        miss="버핏은 사업을 이해하는지(능력 범위), 경쟁 우위가 왜 오래가는지, 경영진의 정직함과 자본 배분 능력을 가장 중요하게 봐요. 모두 숫자로 못 봐요. 금융업은 부채비율이 구조상 높아서 이 방식에 불리해요.",
        sorts={"": "정렬: 버핏 점수순", "roe": "정렬: ROE 높은 순", "per": "정렬: PER 낮은 순"}),
}
GURU_INFO.update({
    "graham": dict(
        title="벤저민 그레이엄 방법 (고전)",
        idea="'현명한 투자자'(1949)의 방어적 투자자 기준: 재무가 튼튼하고, 적자 없이 배당을 꾸준히 하고, 이익이 늘고, 이익·자산에 비해 싸게 거래되는 기업만 사요. 싸게 사서 생기는 '안전마진'이 핵심이에요.",
        how=["점수(100): 부채비율 15 · 이익 안정(적자 없음) 15 · 배당 지속 15 · 이익 증가 15 · PER 20 · PBR 10 · PER×PBR 10",
             "기준값: PER 15 이하 · PBR 1.5 이하 · PER×PBR 22.5 이하 · 부채비율 100% 이하. 모두 통과하면 '그레이엄 통과' 표시",
             "원래는 10~20년 기록과 유동비율을 보는데, 여기서는 최근 확정 3개 연도와 부채비율로 줄였어요"],
        miss="그레이엄은 최소 규모, 오랜 기간의 이익·배당 기록, 유동비율(단기 지급 능력), 순유동자산 대비 가격(넷넷)을 봐요. 자료가 없어 못 넣었어요. 금융업·성장주는 이 방식에 불리해요.",
        sorts={"": "정렬: 그레이엄 점수순", "per": "정렬: PER 낮은 순", "pbr": "정렬: PBR 낮은 순"}),
    "magic": dict(
        title="그린블라트 마법공식 (고전·현대 사이)",
        idea="조엘 그린블라트의 '주식시장을 이기는 작은 책'(2005): '싼 값(이익수익률)'과 '좋은 회사(자본수익률)' 두 가지 순위만 더해서 종합 1~30위를 사고, 1년 뒤 바꿔요.",
        how=["이익수익률 = 1 ÷ PER, 자본수익률 = ROE. 각각 지금 목록 안에서 순위를 매기고 두 순위를 더해 종합 순위를 만들어요",
             "점수(100) = 이익수익률 순위 50 + ROE 순위 50 (목록 안에서 상대 비교라, 종목 수가 달라지면 점수도 바뀌어요)",
             "원래는 EBIT÷기업가치와 투하자본수익률인데 네이버 재무에 없어서 PER·ROE로 바꿨어요. 금융·공기업은 원래 제외하고, 적자 종목은 빼요"],
        miss="기업가치(순부채 포함)와 투하자본이 없어서 빚이 많은 회사는 ROE가 부풀려 보일 수 있어요(부채비율 200% 넘으면 표시). 원래는 시가총액 일정 이상 종목에서 30개를 같은 비중으로 1년 보유해요.",
        sorts={"": "정렬: 종합순위순", "ey": "정렬: 이익수익률 높은 순", "roe": "정렬: ROE 높은 순"}),
    "fscore": dict(
        title="피오트로스키 F-스코어 (현대 학술)",
        idea="회계학자 조지프 피오트로스키(2000)가 '싸지만 부실한 회사'를 걸러내려고 만든 점검표예요. 수익성·재무 안정·효율이 '좋아지고 있는지' 예/아니오로 세어요. 싼 종목 안에서 점수가 높은 쪽이 더 나았다는 연구예요.",
        how=["원래 9개 중 이 화면은 7개: 순이익 흑자 · 영업이익 흑자 · ROE 개선 · 부채비율 감소 · 영업이익률 개선 · EPS 증가 · 주식 수 안 늘림(추정)",
             "점수(100) = 통과한 개수 ÷ 계산된 개수. 7개 중 6개 이상이면 '개선 중' 표시",
             "최근 두 확정 연도를 비교해요 (올해가 작년보다 나아졌는지)"],
        miss="영업현금흐름, 유동비율, 자산회전율은 자료가 없어서 빠졌어요. 항목 일부는 비슷한 숫자로 대신했어요(ROA→순이익·ROE, 매출총이익률→영업이익률). 가격(싼지)은 안 봐서 '싼 종목 중에서' 쓰는 필터예요.",
        sorts={"": "정렬: F 점수순", "roe": "정렬: ROE 높은 순"}),
})
GURU_INFO["momentum"] = dict(
    title="모멘텀 (현대 요인 투자)",
    idea="'최근 오른 종목이 한동안 더 오르는 경향'(Jegadeesh·Titman 1993, Carhart 1997)을 점수로 옮겼어요. 학술 연구가 가장 많은 요인이지만 시기에 따라 크게 틀어져요.",
    how=["12-1 수익률 40점: 12개월 전 → 1개월 전 수익률 (최근 1개월은 단기 반전 때문에 일부러 뺌). 지금 목록 안에서의 상대 순위",
         "6-1 수익률 20점: 6개월 전 → 1개월 전, 같은 방식",
         "52주 고점 근처 20점 (George·Hwang 2004): 현재가가 1년 최고가의 90% 이상이면 만점",
         "200일 평균선 위 20점: 추세가 살아 있는지"],
    miss="기업 가치는 전혀 안 봐요 — 오르는 이유가 실적인지 거품인지 몰라요. 급반등장(2009·2020)에서 모멘텀 종목이 크게 무너진 적이 있고, 매매 비용·세금을 빼면 남는 게 줄어요. 점수는 이 목록 안에서의 상대 순위라 종목 수가 바뀌면 달라져요.",
    sorts={"": "정렬: 모멘텀 점수순", "r12": "정렬: 12-1 수익률 높은 순", "high": "정렬: 52주 고점에 가까운 순"})
GURU_PASS = 60


def view_guru(conn, q):
    """아이디어 → 피터 린치 / 워렌 버핏: 기관이 산 종목을 두 사람의 방법을 단순한 숫자로 옮겨 점수 냄"""
    import guru
    kind = q["t"]
    info = GURU_INFO[kind]
    if not has_table(conn, "summary"):
        return page(info["title"], '<div class="card">보고서(DART) 수집이 한 번 끝나야 볼 수 있습니다.</div>')
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    w = q.get("w") if q.get("w") in WINDOWS else "365"
    ex = q.get("ex") if q.get("ex") in NPS_OPTS else "1"
    m = q.get("m") if q.get("m") in MARKET_OPTS and "시장" in cols else ""
    flt = "pass" if q.get("f") == "pass" else ""
    rp = "has" if q.get("rp") == "has" else ""
    srt = q.get("s") if q.get("s") in info["sorts"] else ""
    conds, args = ["변동일 >= ?", "매수수량 > 0", "평균매수단가 > 0"], [(dt.date.today() - dt.timedelta(days=int(w))).isoformat()]
    if ex == "1":
        conds.append("제출인 NOT LIKE '%국민연금%'")
    if m:
        conds.append("시장 = ?")
        args.append(m)
    market = "MAX(시장)" if "시장" in cols else "''"
    rows = conn.execute(f"SELECT 종목코드 code, MAX(종목명) name, {market} mk, COUNT(DISTINCT 제출인) n, SUM(매수금액) amt "
                        f"FROM summary WHERE {' AND '.join(conds)} GROUP BY 종목코드", args).fetchall()
    fin, vals = guru.load_all(conn), value_map(conn)
    if kind == "momentum":
        import momentum
        fin = {c: [1] for c in momentum.load_all(conn)}          # 재무 대신 종가 이력이 있는지만 확인
        hist = momentum.load_all(conn)
    cards, no_data = [], 0
    for r in rows:
        ys = fin.get(r["code"])
        v = vals.get(r["code"]) or {}
        per, pbr = v.get("per"), v.get("pbr")
        if kind == "momentum":
            mm = momentum.metrics(hist.get(r["code"]))
            if not mm["ok"]:
                no_data += 1
                continue
            res = dict(ok=True, score=0, items=[], m=mm)
            res.update(code=r["code"], name=r["name"], mk=r["mk"], n=r["n"], amt=r["amt"], per=per, pbr=pbr, act=[dict(period=mm["last_date"] or "")])
            cards.append(res)
            continue
        res = (guru.lynch(ys, per, pbr) if kind == "lynch" else guru.buffett(ys, per, pbr) if kind == "buffett"
               else guru.graham(ys, per, pbr) if kind == "graham" else guru.fscore(ys, per, pbr) if kind == "fscore"
               else dict(ok=True, score=0, items=[])) if ys else None
        if res and kind == "magic":
            mi = guru.magic_inputs(ys, per)
            if not mi:
                res = None
            else:
                res.update(ey=mi[0], roe_last=mi[1])
        if not res or not res["ok"]:
            no_data += 1
            continue
        act = guru.actual(ys, 3)
        res.update(code=r["code"], name=r["name"], mk=r["mk"], n=r["n"], amt=r["amt"], per=per, pbr=pbr, act=act)
        cards.append(res)
    if kind == "magic":
        guru.magic_rank(cards)
    if kind == "momentum":
        momentum.score_all(cards)
    if flt:
        cards = [c for c in cards if c["score"] >= GURU_PASS]
    key = {"": lambda c: -c["score"],
           "peg": lambda c: (c.get("peg") is None, c.get("peg") or 0),
           "growth": lambda c: (c.get("growth") is None, -(c.get("growth") or 0)),
           "roe": lambda c: -(sum(y["roe"] for y in c["act"] if y.get("roe") is not None) / max(1, sum(1 for y in c["act"] if y.get("roe") is not None))),
           "per": lambda c: (not c["per"] or c["per"] <= 0, c["per"] or 0),
           "pbr": lambda c: (not c["pbr"] or c["pbr"] <= 0, c["pbr"] or 0),
           "ey": lambda c: -c.get("ey", 0),
           "r12": lambda c: (c.get("m", {}).get("r12_1") is None, -(c.get("m", {}).get("r12_1") or 0)),
           "high": lambda c: (c.get("m", {}).get("high_ratio") is None, -(c.get("m", {}).get("high_ratio") or 0))}[srt]
    if kind == "magic" and not srt:
        key = lambda c: c["rank_all"]
    reps = report_map(conn, [c["code"] for c in cards])
    if rp:
        cards = [c for c in cards if c["code"] in reps]
    closes = latest_closes(conn) if has_table(conn, "prices") else {}
    cards.sort(key=key)
    form = (f'<form method="get" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="{kind}">'
            f'{select("w", WINDOWS, w)}' + (select("m", MARKET_OPTS, m) if "시장" in cols else "") + f'{select("ex", NPS_OPTS, ex)}'
            f'{select("f", {"": "점수 전체", "pass": f"{GURU_PASS}점 이상만"}, flt)}{select("s", info["sorts"], srt)}'
            + select("rp", {"": "리포트 전체", "has": f"증권사 리포트 있는 종목만"}, rp) + '<button>적용</button></form>')
    how = "".join(f"<li>{escape(x)}</li>" for x in info["how"])
    intro = (f'<div class="card"><b>{info["title"]}</b><div class="muted" style="margin-top:4px">{escape(info["idea"])}</div>'
             f'<details style="margin-top:6px"><summary class="muted">숫자로 어떻게 옮겼나</summary>'
             f'<ul class="muted" style="margin:6px 0 0;padding-left:18px">{how}</ul></details>'
             f'<details style="margin-top:4px"><summary class="muted">숫자로 못 보는 것</summary>'
             f'<div class="muted" style="margin-top:4px">{escape(info["miss"])}</div></details>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 이 방법의 원칙을 재무 숫자 몇 개로 줄인 단순 점수예요. '
             '점수 구간은 제가 정한 기준이고 실제 투자자·연구자의 판단과 달라요. 재무 방식은 최근 3개 연도, 모멘텀은 최근 1년 가격만 봐서 장기 평가에는 모자라요. 투자 추천이 아니에요.</div></div>')
    if not fin:
        return page(info["title"], intro + form + '<div class="card">연간 재무·종가 자료가 아직 없어요. <b>지금 수집 → 시장·정책만</b>을 한 번 돌려 주세요 (재무는 주 1회 갱신).</div>')
    out = []
    for i, c in enumerate(cards[:30], 1):
        chips = " ".join(f'<span class="chip {"up" if p == mx else "down" if p == 0 else ""}" title="{escape(txt)}">{escape(nm.split(" (")[0])} {p}/{mx}</span>'
                         for nm, p, mx, txt in c["items"])
        detail = "".join(f'<div style="margin-top:3px"><b>{escape(nm)}</b> {p}/{mx}점 <span class="muted">— {escape(txt)}</span></div>' for nm, p, mx, txt in c["items"])
        last = c["act"][-1]
        if kind == "lynch":
            g = f'{c["growth"]:+.0%}' if c["growth"] is not None else "-"
            peg = f'{c["peg"]:.2f}' if c["peg"] else "-"
            fwd = (f' · 예상(컨센서스) 성장 {c["fwd_growth"]:+.0%}' + (f' → PEG {c["peg_fwd"]:.2f}' if c["peg_fwd"] else "")) if c["fwd_growth"] is not None else ""
            head = (f'<span class="chip">{escape(c["category"])}</span> ' + " ".join(f'<span class="chip warn">{escape(t)}</span>' for t in c["tags"]))
            numbers = (f'PER {c["per"]:.1f}배' if c["per"] else "PER -") + f' · 이익 성장률 {g} · <b>PEG {peg}</b>{fwd}'
            hint = f'<div class="muted" style="font-size:12px;margin-top:3px">{escape(guru.LYNCH_HINT[c["category"]])}</div>'
        elif kind == "graham":
            head = ('<span class="chip" style="background:var(--accent);color:var(--bg)">그레이엄 통과</span>' if c["passed"] else "")
            numbers = ((f'PER {c["per"]:.1f}배' if c["per"] else "PER -") + (f' · PBR {c["pbr"]:.2f}배' if c["pbr"] else "")
                       + (f' · PER×PBR {c["product"]:.1f}' if c["product"] else "")
                       + (f' · 부채비율 {last["debt"]:.0f}%' if last.get("debt") is not None else ""))
            hint = ""
        elif kind == "magic":
            head = f'<span class="chip" style="background:var(--accent);color:var(--bg)">종합 {c["rank_all"]}위 / {c["rank_n"]}</span>'
            if last.get("debt") is not None and last["debt"] > 200:
                head += ' <span class="chip warn">부채 많음 — ROE가 부풀었을 수 있어요</span>'
            numbers = f'이익수익률 {c["ey"]:.1%} (PER {c["per"]:.1f}배) · ROE {c["roe_last"]:.1f}%'
            hint = ""
        elif kind == "momentum":
            mm = c["m"]
            f = lambda v: f"{v:+.0%}" if v is not None else "-"
            head = ('<span class="chip" style="background:var(--accent);color:var(--bg)">추세 위</span>' if mm["above_ma200"] else
                    '<span class="chip warn">200일선 아래</span>' if mm["above_ma200"] is False else "")
            numbers = (f'12-1개월 {f(mm["r12_1"])} · 6-1개월 {f(mm["r6_1"])}'
                       + (f' · 52주 고점의 {mm["high_ratio"]:.0%}' if mm["high_ratio"] is not None else "") + f' · 기준일 {mm["last_date"]}')
            hint = ""
        elif kind == "fscore":
            head = (f'<span class="chip" style="background:var(--accent);color:var(--bg)">개선 중</span> ' if c["total"] and c["passed"] >= c["total"] - 1 and c["total"] >= 6 else "")
            head += f'<span class="chip">{c["passed"]}/{c["total"]} 통과</span>'
            numbers = (f'ROE {last["roe"]:.1f}%' if last.get("roe") is not None else "ROE -") + (f' · PER {c["per"]:.1f}배' if c["per"] else "")
            hint = ""
        else:
            roes = [y["roe"] for y in c["act"] if y.get("roe") is not None]
            opms = [y["opm"] for y in c["act"] if y.get("opm") is not None]
            head = '<span class="chip" style="background:var(--accent);color:var(--bg)">해자 후보</span>' if c["moat"] else ""
            numbers = ((f'ROE 평균 {sum(roes) / len(roes):.1f}%' if roes else "ROE -")
                       + (f' · 영업이익률 평균 {sum(opms) / len(opms):.1f}%' if opms else "")
                       + (f' · 부채비율 {last["debt"]:.0f}%' if last.get("debt") is not None else "")
                       + (f' · PER {c["per"]:.1f}배' if c["per"] else ""))
            hint = ""
        mk = f' <span class="chip">{escape(c["mk"])}</span>' if c["mk"] else ""
        out.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span>'
            f'<a class="big" href="/stock?code={quote(c["code"])}">{escape(c["name"])}</a> <span class="muted">{escape(c["code"])}</span>{mk}</div>'
            f'<div><b class="big">{c["score"]}</b><span class="muted">/100</span></div></div>'
            f'<div style="height:5px;background:var(--chip);border-radius:3px;margin:6px 0"><div style="height:5px;width:{c["score"]}%;background:var(--accent);border-radius:3px"></div></div>'
            f'<div>{head}</div><div style="margin-top:4px">{numbers}</div>{hint}<div style="margin-top:4px">{chips}</div>'
            + report_box(conn, c["code"], c["name"], reps, closes.get(c["code"], (None, None))[1]) +
            f'<details style="margin-top:4px"><summary class="muted">점수 근거</summary>{detail}'
            f'<div class="muted" style="font-size:12px;margin-top:4px">기준 연도 {escape(c["act"][0]["period"][:4])}~{escape(last["period"][:4])} · '
            f'기관 매수 {c["n"]}곳 · <a href="{score_url(code=c["code"])}">점수표로 평가하기 →</a></div></details></div>')
    note = (f'<p class="muted">{len(rows)}개 종목 중 자료로 계산된 {len(rows) - no_data}개' + (f' (자료 부족 {no_data}개 제외)' if no_data else "")
            + f'에서 {len(cards)}개' + (f' ({GURU_PASS}점 이상)' if flt else "") + f' — 위 {min(len(cards), 30)}개를 보여 줘요.</p>')
    return page(info["title"], intro + form + note + ("".join(out) or '<p class="muted">조건에 맞는 종목이 없어요. 기간을 늘리거나 점수 필터를 풀어 보세요.</p>'))


REPORT_DAYS = 365


def report_map(conn, codes, days=REPORT_DAYS):
    """{종목코드: [최근 days일 증권사 리포트 dict (최신순)]} — 표가 없으면 {}"""
    if not codes or not has_table(conn, "reports"):
        return {}
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = {}
    for i in range(0, len(codes), 500):
        part = codes[i:i + 500]
        cur = conn.execute("SELECT code, date, broker, title, opinion, target, pdf, url FROM reports "
                           f"WHERE date >= ? AND code IN ({','.join('?' * len(part))}) ORDER BY date DESC, rowid DESC", [since] + part)
        for code, d, broker, title, opinion, target, pdf, url in cur:
            out.setdefault(code, []).append(dict(date=d, broker=broker, title=title, opinion=opinion, target=target, link=pdf or url or ""))
    return out


def short_hits(conn, name):
    """종목 이름이 대상 기업·제목에 들어 있는 공매도 리포트 [(id, 제목, 날짜)] (이름이 너무 짧으면 안 찾음)"""
    name = (name or "").strip()
    if len(name) < 2:
        return []
    try:
        import shortreports as sr
        return [(r["id"], r["title"], r["report_date"]) for r in sr.list_all(conn)
                if name in (r["target"] or "") or name in (r["title"] or "")]
    except Exception:
        return []


def report_box(conn, code, name, reps, close=None):
    """아이디어 카드 안의 '이 기업에 리포트가 있나' 칸: 증권사 리포트(최근 1년) + 공매도 리포트"""
    rs = reps.get(code) or []
    if rs:
        brokers = len({r["broker"] for r in rs})
        tgt, nb = consensus(conn, code)
        head = (f'<span class="chip" style="background:var(--accent);color:var(--bg)">증권사 리포트 {len(rs)}건</span> '
                f'<span class="muted">최근 {REPORT_DAYS}일 · {brokers}곳</span>')
        if tgt:
            head += f' · 목표가 평균 <b>{won(tgt)}원</b>' + (f' {upside(tgt, close)}' if close else "")
        lines = ""
        for r in rs[:2]:
            t = escape((r["title"] or "")[:40])
            t = f'<a href="{escape(r["link"])}">{t}</a>' if r["link"].startswith(("http://", "https://")) else t
            lines += (f'<div class="muted" style="font-size:12px">{escape(r["date"])} · {escape(r["broker"] or "")} · {t}'
                      + (f' · {escape(r["opinion"])}' if r["opinion"] else "") + (f' · 목표 {won(r["target"])}원' if r["target"] else "") + '</div>')
        more = f'<a href="/reports?{urlencode({"q": code, "d": str(REPORT_DAYS)})}">리포트 모두 보기 →</a>' if len(rs) > 2 else ""
        body = head + lines + (f'<div style="font-size:12px">{more}</div>' if more else "")
    else:
        body = '<span class="muted">증권사 리포트: 최근 1년 없음 (다루는 증권사가 없거나 아직 수집 전)</span>'
    sh = short_hits(conn, name)
    if sh:
        body += ('<div style="margin-top:3px"><span class="chip warn">공매도 리포트 %d건</span> ' % len(sh)
                 + " · ".join(f'<a href="/shorts">{escape(t[:30])}</a>' for _, t, _ in sh[:2]) + '</div>')
    return f'<div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--chip)">{body}</div>'


OVERALL_METHODS = [("lynch", "린치"), ("buffett", "버핏"), ("graham", "그레이엄"), ("magic", "마법공식"), ("fscore", "F"), ("momentum", "모멘텀")]
VALUE_KEYS = ("lynch", "buffett", "graham", "magic", "fscore")


def overall_cards(conn, rows):
    """rows: [{code, name, mk, n}] → 종목마다 여섯 방법 점수(sc)·평균(avg)·통과 수(passed)·가치/추세 엇갈림(split)을 계산한 카드 목록"""
    import guru
    import momentum
    fin, vals, hist = guru.load_all(conn), value_map(conn), momentum.load_all(conn)
    cards = []
    for r in rows:
        ys, v = fin.get(r["code"]), vals.get(r["code"]) or {}
        per, pbr = v.get("per"), v.get("pbr")
        sc, extra, det = {}, {}, {}
        if ys:
            for key, fn in (("lynch", guru.lynch), ("buffett", guru.buffett), ("graham", guru.graham), ("fscore", guru.fscore)):
                res = fn(ys, per, pbr)
                if res["ok"]:
                    sc[key] = res["score"]
                    det[key] = res["items"]
            mi = guru.magic_inputs(ys, per)
            if mi:
                extra["magic"] = dict(ey=mi[0], roe_last=mi[1])
        mm = momentum.metrics(hist.get(r["code"]))
        if mm["ok"]:
            extra["momentum"] = dict(m=mm)
        if sc or extra:
            cards.append(dict(code=r["code"], name=r["name"], mk=r["mk"], n=r["n"], sc=sc, extra=extra, per=per, pbr=pbr, det=det))
    mg = [dict(c["extra"]["magic"]) for c in cards if "magic" in c["extra"]]
    guru.magic_rank(mg)
    it = iter(mg)
    mo = [c["extra"]["momentum"] for c in cards if "momentum" in c["extra"]]
    momentum.score_all(mo)
    for c in cards:
        if "magic" in c["extra"]:
            mgc = next(it)
            c["sc"]["magic"] = mgc["score"]
            c["det"]["magic"] = mgc["items"]
        if "momentum" in c["extra"]:
            c["sc"]["momentum"] = c["extra"]["momentum"]["score"]
            c["det"]["momentum"] = c["extra"]["momentum"]["items"]
        sc = c["sc"]
        c["avg"] = sum(sc.values()) / len(sc) if sc else 0
        vv = [sc[k] for k in VALUE_KEYS if k in sc]
        c["value"] = sum(vv) / len(vv) if vv else None
        c["trend"] = sc.get("momentum")
        c["passed"] = sum(1 for x in sc.values() if x >= GURU_PASS)
        c["split"] = (abs(c["value"] - c["trend"]) if c["value"] is not None and c["trend"] is not None else 0)
    return cards


def snapshot_scores(db_path, log=print, today=None):
    """종합 점수를 오늘 날짜로 저장 (수집 때 실행). 기관이 최근 1년 안에 산 종목(국민연금 제외) 기준"""
    import scorehist
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not has_table(conn, "summary"):
            return {"저장": 0}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
        market = "MAX(시장)" if "시장" in cols else "''"
        since = ((today or dt.date.today()) - dt.timedelta(days=365)).isoformat()
        rows = [dict(r) for r in conn.execute(
            f"SELECT 종목코드 code, MAX(종목명) name, {market} mk, COUNT(DISTINCT 제출인) n FROM summary "
            "WHERE 변동일 >= ? AND 매수수량 > 0 AND 평균매수단가 > 0 AND 제출인 NOT LIKE '%국민연금%' GROUP BY 종목코드", (since,))]
        n = scorehist.save(conn, overall_cards(conn, rows), today)
        log(f"종합 점수 기록: {n}개 종목")
        return {"저장": n}
    finally:
        conn.close()


def bonds_block(conn, code):
    """종목 화면·내 종목 점수: 최근 3년 전환사채·신주인수권부사채·교환사채·유상증자 결정 공시 (DART 주요사항보고)"""
    try:
        import dart_bonds
    except Exception:
        return ""
    rows = dart_bonds.for_stock(conn, code)
    if not rows:
        if dart_bonds.checked(conn, code):
            return '<div class="muted" style="margin-top:4px">채권·증자 공시: 최근 3년 전환사채·신주인수권부사채·유상증자 결정 공시 없음 (DART 주요사항보고 기준)</div>'
        return ""
    n1 = dart_bonds.recent_count(rows, 365)
    kinds = {"CB": "전환사채", "BW": "신주인수권부사채", "EB": "교환사채", "유증": "유상증자"}
    lines = ""
    for r in rows[:8]:
        extra = (f' · 규모 {escape(r["amount"])}' if r["amount"] else "") + (f' · 가격 {escape(r["price"])}' if r["price"] else "")
        lines += (f'<div style="margin-top:3px"><span class="chip">{kinds.get(r["kind"], r["kind"])}</span> {escape(r["date"])} '
                  f'<a href="{dart_link(r["rcept_no"])}">{escape(r["title"][:40])}</a><span class="muted">{extra}</span></div>')
    return ('<div class="card"><div class="row"><b>채권·증자 공시</b>'
            + (f'<span class="chip warn">최근 1년 {n1}건</span>' if n1 else '<span class="muted">최근 1년 없음</span>') + '</div>'
            f'<div class="muted" style="font-size:12px;margin-top:2px">전환사채·신주인수권부사채는 주식으로 바뀌면 주식 수가 늘어(희석) 주가에 부담이 될 수 있고, 유상증자도 새 주식을 발행해요. '
            '좋고 나쁨은 용도·조건에 따라 달라서 판단은 하지 않고 공시만 모았어요. 규모·가격은 DART 응답에서 읽은 값이라 비어 있거나 틀릴 수 있어요 — 공시 원문을 확인하세요 (최근 3년, 최대 8건).</div>'
            f'{lines}</div>')


def cap_rank_line(conn, code):
    """종목 화면: 시장 안 시가총액 순위와 비중 한 줄 (KRX 시세가 있을 때). 시총 비율로 사는 패시브(지수·ETF) 자금이 얼마나 따라붙는 종목인지 가늠하는 참고용"""
    try:
        import krx_api
        r = krx_api.cap_rank(conn, code)
    except Exception:
        return ""
    if not r:
        return ""
    big = r["rank"] <= 10
    return (f'<div class="muted" style="margin-top:4px">시가총액 {escape(r["market"])} {r["rank"]}위 / {r["total"]}종목 · 시장 안 비중 {r["share"]:.2f}%'
            f'{" (상위 10개 합 " + format(r["top10"], ".0f") + "%)" if big else ""} · 시총 비율로 사는 지수·ETF 자금은 비중이 큰 종목에 더 많이 들어가요 (참고용, {escape(r["bas_dd"])})</div>')


def stock_deriv_line(conn, name):
    """종목 화면: 이 종목의 단일종목 선물·옵션이 있으면 한 줄 (KRX 파생상품 자료가 있을 때만)"""
    try:
        import krx_deriv
        d = krx_deriv.stock_deriv(conn, name)
    except Exception:
        return ""
    if not d:
        return ""
    parts = []
    if "F" in d:
        parts.append(f'주식선물 거래 {d["F"][0]:,.0f}계약 · 미결제 {d["F"][1]:,.0f}')
    if "O" in d:
        pc = f' · 풋/콜 {d["O"][2]:.2f}' if d["O"][2] is not None else ""
        parts.append(f'주식옵션 거래 {d["O"][0]:,.0f}{pc}')
    return f'<div class="muted" style="margin-top:4px">단일종목 파생상품({escape(d["date"])}): {" · ".join(parts)}</div>'


def score_one(conn, code, name):
    """종목 하나의 여섯 방법 점수 카드 (기관이 최근 1년 안에 산 종목(국민연금 제외) 목록 안에서 비교). 계산 못 하면 None"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")} if has_table(conn, "summary") else set()
    rows = []
    if cols:
        market = "MAX(시장)" if "시장" in cols else "''"
        since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        rows = [dict(r) for r in conn.execute(
            f"SELECT 종목코드 code, MAX(종목명) name, {market} mk, COUNT(DISTINCT 제출인) n FROM summary "
            "WHERE 변동일 >= ? AND 매수수량 > 0 AND 평균매수단가 > 0 AND 제출인 NOT LIKE '%국민연금%' GROUP BY 종목코드", (since,))]
    if not any(r["code"] == code for r in rows):
        rows.append(dict(code=code, name=name, mk="", n=0))
    return next((c for c in overall_cards(conn, rows) if c["code"] == code), None)


def stock_scores_block(conn, code, name):
    """종목 화면: 여섯 가지 투자 방법 점수를 한 칸에 모아 보여 줌 (종합 탭과 같은 기준)"""
    try:
        card = score_one(conn, code, name)
    except Exception:
        return ""
    if not card or not card["sc"]:
        return ""
    lines = ""
    for k, lab in OVERALL_METHODS:
        v = card["sc"].get(k)
        bar = (f'<div style="flex:1;height:6px;background:var(--chip);border-radius:3px"><div style="height:6px;width:{v}%;background:var(--accent);border-radius:3px"></div></div>'
               if v is not None else '<div class="muted" style="flex:1;font-size:12px">자료 부족</div>')
        lines += (f'<div style="display:flex;align-items:center;gap:8px;margin-top:4px"><a href="/ideas?t={k}" style="width:70px">{lab}</a>{bar}'
                  f'<b style="width:30px;text-align:right" class="{"up" if v is not None and v >= GURU_PASS else ""}">{"-" if v is None else v}</b></div>')
    tag = ""
    if card["split"] >= 50:
        tag = f' <span class="chip warn">엇갈림: 가치·품질 {card["value"]:.0f} vs 추세 {card["trend"]:.0f}</span>'
    hist_line = ""
    try:
        import scorehist
        ser = scorehist.load(conn, [code]).get(code, [])
        ch = scorehist.change(ser, 7)
        if ch:
            hist_line = (f'<div style="margin-top:6px">7일 전보다 <b class="{"up" if ch[0] > 0 else "down" if ch[0] < 0 else ""}">{ch[0]:+.1f}점</b> '
                         f'<span class="muted">(기준 {escape(ch[1])})</span></div>')
        if len(ser) >= 2:
            hist_line += sparkline([v for _, v in ser[-90:]])
    except Exception:
        pass
    return ('<div class="card"><div class="row"><b>투자 방법별 점수</b>'
            f'<div><b class="big">{card["avg"]:.0f}</b><span class="muted">/100 평균 · 통과 {card["passed"]}/{len(card["sc"])}</span></div></div>'
            f'{tag}{lines}{hist_line}'
            '<div class="muted" style="font-size:12px;margin-top:6px">단순화한 점수예요 (마법공식·모멘텀은 기관이 산 종목 목록 안 상대 순위). 이름을 누르면 그 방법 설명과 순위가 나와요. 투자 추천이 아니에요.</div></div>')


def parse_methods(v):
    """'lynch.buffett' → 고른 방법 목록 (순서 유지). 비었거나 이상하면 전부"""
    on = [k for k, _ in OVERALL_METHODS if k in (v or "").split(".")]
    return on or [k for k, _ in OVERALL_METHODS]


def restat(c, on):
    """고른 방법(on)만으로 평균·통과 수·가치/추세 엇갈림을 다시 계산"""
    sc = {k: v for k, v in c["sc"].items() if k in on}
    c["sc"] = sc
    c["avg"] = sum(sc.values()) / len(sc) if sc else 0
    vv = [sc[k] for k in VALUE_KEYS if k in sc]
    c["value"] = sum(vv) / len(vv) if vv else None
    c["trend"] = sc.get("momentum")
    c["passed"] = sum(1 for x in sc.values() if x >= GURU_PASS)
    c["split"] = abs(c["value"] - c["trend"]) if c["value"] is not None and c["trend"] is not None else 0


def naver_autocomplete(raw, timeout=5):
    """네이버 증권 검색 자동완성으로 이름 → [(코드, 이름)]. 안 되면 []"""
    try:
        import requests
        r = requests.get("https://ac.stock.naver.com/ac", params={"q": raw, "target": "stock"}, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
    except Exception:
        return []
    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            code = str(o.get("code") or o.get("cd") or o.get("itemCode") or "")
            name = o.get("name") or o.get("nm") or o.get("stockName") or ""
            if SCORE_CODE.fullmatch(code) and name and code not in seen:
                seen.add(code)
                out.append((code, str(name)))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out[:20]


def known_names(conn):
    """코드 → 이름: 공시·점수표·리포트·KRX 시세에 나온 모든 종목"""
    names = _score_names(conn)
    for sql in ("SELECT code, MAX(name) FROM reports GROUP BY code", "SELECT code, MAX(name) FROM krx_daily GROUP BY code"):
        try:
            for c, n in conn.execute(sql):
                if c and n:
                    names.setdefault(c, n)
        except sqlite3.OperationalError:
            pass
    return names


def resolve_stock(conn, raw):
    """입력(이름 일부 또는 6자리 코드) → [(코드, 이름)]"""
    raw = (raw or "").strip()
    names = known_names(conn)
    if SCORE_CODE.fullmatch(raw):
        return [(raw.upper(), names.get(raw.upper()) or "")]
    key = raw.replace(" ", "")
    hits = [(c, n) for c, n in names.items() if key and key in (n or "").replace(" ", "")]
    exact = [h for h in hits if (h[1] or "").replace(" ", "") == key]
    if exact:
        return exact[:1]
    if not hits:
        hits = naver_autocomplete(raw)
    return sorted(hits, key=lambda h: (len(h[1] or ""), h[1] or ""))[:30]


def ensure_stock_data(code):
    """이 종목의 재무·종가·PER/PBR이 없거나 오래됐으면 네이버에서 받아 저장 → (이름 또는 None, [안내 문구])"""
    path = _DB["path"]
    notes, name = [], None
    if not path or not Path(path).exists():
        return None, notes
    try:
        import guru, momentum, score_auto, stock_value
    except Exception as e:
        return None, [f"모듈을 불러오지 못했어요 ({e})"]
    conn = sqlite3.connect(path)
    try:
        guru.ensure(conn)
        momentum.ensure(conn)
        today = dt.date.today()
        fin = conn.execute("SELECT fetched FROM fin_fetch WHERE code=?", (code,)).fetchone()
        if not fin or fin[0] < (today - dt.timedelta(days=guru.REFRESH_DAYS)).isoformat():
            try:
                guru.store(conn, code, score_auto.fetch_finance(code, "annual"))
            except Exception as e:
                notes.append(f"연간 재무를 받지 못했어요 ({str(e)[:50]})")
        pf = conn.execute("SELECT fetched FROM price_fetch WHERE code=?", (code,)).fetchone()
        if not pf or pf[0] < (today - dt.timedelta(days=momentum.REFRESH_DAYS)).isoformat():
            try:
                momentum.store(conn, code, momentum.fetch(code))
            except Exception as e:
                notes.append(f"종가 이력을 받지 못했어요 ({str(e)[:50]})")
    finally:
        conn.close()
    try:                                  # 채권·증자 공시 (DART 키가 있을 때만)
        import dart_bonds
        dk = _DB["get_key"]() if _DB.get("get_key") else ""
        if dk:
            bc = sqlite3.connect(path)
            try:
                dart_bonds.refresh_code(bc, code, dk, str(Path(_DATA_DIR["path"] or ".") / "dart_cache")) if not dart_bonds.checked(bc, code) else None
            except Exception as e:
                notes.append(f"채권·증자 공시를 받지 못했어요 ({str(e)[:50]})")
            finally:
                bc.close()
    except Exception:
        pass
    try:
        name, err = stock_value.refresh(path, code)
        if err:
            notes.append(err)
    except Exception as e:
        notes.append(f"PER·PBR을 받지 못했어요 ({str(e)[:50]})")
    return name, notes


def view_mine(conn, q):
    """아이디어 → 내 종목 점수: 종목 이름·코드를 넣으면 여섯 가지 방법 점수를 계산 (기관이 안 산 종목도 됨)"""
    raw = (q.get("q") or q.get("code") or "").strip()
    form = ('<form method="get" action="/ideas" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="mine">'
            f'<input type="search" name="q" value="{escape(raw)}" placeholder="종목 이름 또는 6자리 코드 (예: 삼성전자, 005930)" style="flex:1 1 220px">'
            '<button>점수 보기</button></form>')
    intro = ('<div class="card"><b>내 종목 점수</b><div class="muted" style="margin-top:4px">'
             '종목을 넣으면 린치 · 버핏 · 그레이엄 · 마법공식 · F-스코어 · 모멘텀 점수를 한 번에 계산해요. 기관이 안 산 종목도 돼요 '
             '(재무·가격을 네이버에서 바로 받아 와서 몇 초 걸려요).</div>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 단순화한 점수예요. 마법공식·모멘텀은 \'기관이 최근 1년 안에 산 종목 목록\' 안에서 순위를 매기니 '
             '그 목록과 비교한 상대 점수예요. 투자 추천이 아니에요.</div></div>')
    if not raw:
        names = known_names(conn)
        recent = []
        try:
            recent = [r[0] for r in conn.execute("SELECT code FROM score_hist GROUP BY code ORDER BY MAX(d) DESC, MAX(avg) DESC LIMIT 12")]
        except sqlite3.OperationalError:
            pass
        chips = "".join(f'<a class="chip" href="/ideas?{urlencode({"t": "mine", "q": c})}">{escape(names.get(c) or c)}</a> ' for c in recent)
        return page("내 종목 점수", idea_tabs_row("mine") + form + intro + (f'<h2>점수가 기록된 종목</h2><div>{chips}</div>' if chips else ""))
    hits = resolve_stock(conn, raw)
    if not hits:
        return page("내 종목 점수", idea_tabs_row("mine") + form + '<div class="card">이름으로 찾지 못했어요. <b>6자리 종목 코드</b>(예: 삼성전자 005930)로 넣어 주세요.</div>')
    if len(hits) > 1:
        links = "".join(f'<div class="card"><a class="big" href="/ideas?{urlencode({"t": "mine", "q": c})}">{escape(n or c)}</a> <span class="muted">{escape(c)}</span></div>' for c, n in hits)
        return page("내 종목 점수", idea_tabs_row("mine") + form + f'<p class="muted">{len(hits)}개 종목이 맞아요. 하나를 골라 주세요.</p>' + links)
    code, name = hits[0]
    got_name, notes = ensure_stock_data(code)
    name = got_name or name or code
    path = _DB["path"]
    try:
        c2 = sqlite3.connect(f"file:{path}?mode=ro", uri=True) if path else conn
        c2.row_factory = sqlite3.Row
    except sqlite3.Error:
        c2 = conn
    try:
        card = score_one(c2, code, name)
        reps = report_map(c2, [code])
        close = None
        try:
            import research
            close = research.latest_close(c2, code)
        except Exception:
            pass
        rbox = report_box(c2, code, name, reps, close)
        in_dart = has_table(c2, "detail") and c2.execute("SELECT 1 FROM detail WHERE 종목코드=? LIMIT 1", (code,)).fetchone()
    finally:
        if c2 is not conn:
            c2.close()
    warn = ('<div class="card warn">' + "<br>".join(escape(n) for n in notes) + '<div class="muted">받지 못한 자료는 점수에서 빠지거나 예전에 받아 둔 값을 써요.</div></div>') if notes else ""
    if not card or not card["sc"]:
        return page("내 종목 점수", idea_tabs_row("mine") + form + warn + f'<div class="card"><b>{escape(name)}</b> <span class="muted">{escape(code)}</span><div style="margin-top:4px">점수를 계산할 자료가 모자라요 (연간 재무 확정 연도 2개 미만이거나 가격 이력이 짧아요). 신규 상장·적자 기업·ETF는 안 될 수 있어요.</div></div>')
    sections = ""
    for k, lab in OVERALL_METHODS:
        v = card["sc"].get(k)
        if v is None:
            sections += f'<details class="card"><summary><b>{lab}</b> <span class="muted">자료 부족 — 계산 못 했어요</span></summary></details>'
            continue
        items = "".join(f'<div style="margin-top:3px"><b>{escape(nm)}</b> {p}/{mx}점 <span class="muted">— {escape(txt)}</span></div>' for nm, p, mx, txt in card["det"].get(k, []))
        sections += (f'<details class="card" open><summary><b>{lab}</b> <b class="big {"up" if v >= GURU_PASS else ""}">{v}</b><span class="muted">/100</span> '
                     f'<a href="/ideas?t={k}" style="font-size:12px">이 방법 설명·순위 →</a></summary>'
                     f'<div style="height:5px;background:var(--chip);border-radius:3px;margin:6px 0"><div style="height:5px;width:{v}%;background:var(--accent);border-radius:3px"></div></div>{items}</details>')
    tag = (f'<span class="chip warn">엇갈림: 가치·품질 {card["value"]:.0f} vs 추세 {card["trend"]:.0f}</span> ' if card["split"] >= 50 else "")
    head = (f'<div class="card"><div class="row"><div><b class="big">{escape(name)}</b> <span class="muted">{escape(code)}</span></div>'
            f'<div><b class="big">{card["avg"]:.0f}</b><span class="muted">/100 평균</span></div></div>'
            f'<div style="margin-top:4px">{tag}<span class="chip">통과 {card["passed"]}/{len(card["sc"])}</span> '
            + (f'<span class="muted">PER {card["per"]:.1f}배</span> ' if card["per"] else "") + (f'<span class="muted">PBR {card["pbr"]:.2f}배</span>' if card["pbr"] else "") + '</div>'
            + rbox
            + f'<div class="muted" style="font-size:12px;margin-top:6px">'
            + (f'<a href="/stock?code={quote(code)}">공시·기관 매수 보기 →</a> · ' if in_dart else '기관 공시에는 없는 종목이에요 · ')
            + f'<a href="{score_url(code=code)}">오일전문가 점수표로 평가하기 →</a> · <a href="{naver_link(code)}">네이버 증권 ↗</a></div></div>')
    try:
        c3 = sqlite3.connect(f"file:{path}?mode=ro", uri=True) if path else conn
        bb = bonds_block(c3, code)
        if c3 is not conn:
            c3.close()
    except sqlite3.Error:
        bb = ""
    return page(f"{name} 점수", idea_tabs_row("mine") + form + warn + head + bb + sections)


def view_overall(conn, q):
    """아이디어 → 종합: 기관이 산 종목마다 여섯 가지 방법의 점수를 한 줄로 모아 보여 줌"""
    import guru
    import momentum
    if not has_table(conn, "summary"):
        return page("종합", '<div class="card">보고서(DART) 수집이 한 번 끝나야 볼 수 있습니다.</div>')
    cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
    w = q.get("w") if q.get("w") in WINDOWS else "365"
    ex = q.get("ex") if q.get("ex") in NPS_OPTS else "1"
    m = q.get("m") if q.get("m") in MARKET_OPTS and "시장" in cols else ""
    srt = q.get("s") if q.get("s") in ("", "pass", "split", "value", "trend", "up", "down") else ""
    rp = "has" if q.get("rp") == "has" else ""
    conds, args = ["변동일 >= ?", "매수수량 > 0", "평균매수단가 > 0"], [(dt.date.today() - dt.timedelta(days=int(w))).isoformat()]
    if ex == "1":
        conds.append("제출인 NOT LIKE '%국민연금%'")
    if m:
        conds.append("시장 = ?")
        args.append(m)
    market = "MAX(시장)" if "시장" in cols else "''"
    rows = conn.execute(f"SELECT 종목코드 code, MAX(종목명) name, {market} mk, COUNT(DISTINCT 제출인) n "
                        f"FROM summary WHERE {' AND '.join(conds)} GROUP BY 종목코드", args).fetchall()
    closes = latest_closes(conn) if has_table(conn, "prices") else {}
    cards = overall_cards(conn, rows)
    fin, hist = guru.load_all(conn), momentum.load_all(conn)
    on = parse_methods(q.get("u"))
    all_on = len(on) == len(OVERALL_METHODS)
    if not all_on:
        for c in cards:
            restat(c, on)
        cards = [c for c in cards if c["sc"]]
    key = {"": lambda c: (-c["avg"], -c["passed"]), "pass": lambda c: (-c["passed"], -c["avg"]),
           "split": lambda c: -c["split"], "value": lambda c: (c["value"] is None, -(c["value"] or 0)),
           "trend": lambda c: (c["trend"] is None, -(c["trend"] or 0))}.get(srt, lambda c: (-c["avg"], -c["passed"]))
    import scorehist
    shist = scorehist.load(conn, [c["code"] for c in cards], None if all_on else on)
    for c in cards:
        ch = scorehist.change(shist.get(c["code"], []), 7)
        c["chg"] = ch[0] if ch else None
    key_chg = {"up": lambda c: (c["chg"] is None, -(c["chg"] or 0)), "down": lambda c: (c["chg"] is None, c["chg"] or 0)}
    if srt in key_chg:
        key = key_chg[srt]
    reps = report_map(conn, [c["code"] for c in cards])
    if rp:
        cards = [c for c in cards if c["code"] in reps]
    cards.sort(key=key)
    form = ('<form method="get" class="filters" style="margin:8px 0"><input type="hidden" name="t" value="overall"><input type="hidden" name="u" value="{".".join(on)}">'
            f'{select("w", WINDOWS, w)}' + (select("m", MARKET_OPTS, m) if "시장" in cols else "") + f'{select("ex", NPS_OPTS, ex)}'
            + select("rp", {"": "리포트 전체", "has": f"증권사 리포트 있는 종목만 (최근 {REPORT_DAYS}일)"}, rp)
            + select("s", {"": "정렬: 평균 점수순", "pass": "정렬: 60점 넘은 방법 많은 순", "value": "정렬: 가치·품질 평균순",
                           "trend": "정렬: 모멘텀(추세)순", "split": "정렬: 가치와 추세가 엇갈리는 순",
                           "up": "정렬: 7일간 점수 오른 순", "down": "정렬: 7일간 점수 내린 순"}, srt) + '<button>적용</button></form>')
    base = {k: v for k, v in {"t": "overall", "w": w, "m": m, "ex": ex, "s": srt, "rp": rp}.items() if v}

    def toggle(k):
        nxt = [x for x, _ in OVERALL_METHODS if (x in on) != (x == k)]
        return "/ideas?" + urlencode({**base, **({"u": ".".join(nxt)} if nxt and len(nxt) < len(OVERALL_METHODS) else {})})
    picker = ('<div style="margin:8px 0"><div class="muted" style="font-size:12px;margin-bottom:4px">볼 방법 고르기 (누르면 켜고 끔 · 평균·통과·정렬이 고른 방법만으로 다시 계산돼요)</div>'
              + " ".join((f'<a class="chip" style="background:var(--accent);color:var(--bg)" href="{toggle(k)}">✓ {lab}</a>' if k in on else
                          f'<a class="chip" href="{toggle(k)}">{lab}</a>') for k, lab in OVERALL_METHODS)
              + (f' <a class="chip" href="/ideas?{urlencode(base)}">전부 켜기</a>' if not all_on else "") + '</div>')
    intro = ('<div class="card"><b>종합</b><div class="muted" style="margin-top:4px">기관이 산 종목마다 린치 · 버핏 · 그레이엄 · 마법공식 · F-스코어 · 모멘텀 점수를 한 줄에 모았어요. '
             '여러 방법이 함께 높은 종목은 어느 한 가지 기준의 착시일 가능성이 낮고, 방법끼리 엇갈리면 이유를 따져 볼 신호예요.</div>'
             '<details style="margin-top:6px"><summary class="muted">보는 법</summary><ul class="muted" style="margin:6px 0 0;padding-left:18px">'
             '<li>평균 점수: 계산된 방법들의 점수 평균 (자료가 모자라 계산 못 한 방법은 빼고 평균)</li>'
             f'<li>통과: {GURU_PASS}점 넘은 방법 수 / 계산된 방법 수</li>'
             '<li>가치·품질 = 린치·버핏·그레이엄·마법공식·F-스코어 평균, 추세 = 모멘텀. 둘이 50점 넘게 벌어지면 \'엇갈림\' 표시 — '
             '예: 싸고 좋은데 가격은 내리는 중(바닥 가능성? 아직 악재일 가능성?), 또는 비싼데 오르는 중</li>'
             '<li>각 점수의 기준과 한계는 같은 아이디어 안의 각 방법 탭에 적혀 있어요. 마법공식·모멘텀 점수는 이 목록 안 상대 순위라서 종목 수가 달라지면 바뀌어요</li></ul></details>'
             '<div class="muted" style="margin-top:6px"><b>주의:</b> 단순화한 점수들의 평균이라 \'좋은 종목\'의 증명이 아니에요. 사업을 이해하는지·경영진·경쟁 우위 같은 정성 요소는 하나도 안 들어가요. 투자 추천이 아니에요.</div></div>')
    if not fin and not hist:
        return page("종합", intro + picker + form + '<div class="card">재무·종가 자료가 아직 없어요. <b>지금 수집 → 시장·정책만</b>을 한 번 돌려 주세요.</div>')
    out = []
    for i, c in enumerate(cards[:40], 1):
        chips = " ".join(
            (f'<span class="chip {"up" if c["sc"][k] >= GURU_PASS else ""}">{lab} {c["sc"][k]}</span>' if k in c["sc"] else f'<span class="chip muted">{lab} -</span>')
            for k, lab in OVERALL_METHODS if k in on)
        tags = ""
        if c["split"] >= 50:
            tags = (f'<span class="chip warn">엇갈림: 가치·품질 {c["value"]:.0f} vs 추세 {c["trend"]:.0f}</span> ')
        mk = f' <span class="chip">{escape(c["mk"])}</span>' if c["mk"] else ""
        if c.get("chg") is not None:
            tags += f'<span class="chip {"up" if c["chg"] > 0 else "down" if c["chg"] < 0 else ""}">7일 {c["chg"]:+.1f}점</span> '
        val = (f'PER {c["per"]:.1f}배' if c["per"] else "PER -") + (f' · PBR {c["pbr"]:.2f}배' if c["pbr"] else "")
        out.append(
            f'<div class="card"><div class="row"><div><span class="num">{i}</span>'
            f'<a class="big" href="/stock?code={quote(c["code"])}">{escape(c["name"])}</a> <span class="muted">{escape(c["code"])}</span>{mk}</div>'
            f'<div><b class="big">{c["avg"]:.0f}</b><span class="muted">/100 평균</span></div></div>'
            f'<div style="height:5px;background:var(--chip);border-radius:3px;margin:6px 0"><div style="height:5px;width:{c["avg"]:.0f}%;background:var(--accent);border-radius:3px"></div></div>'
            f'<div>{tags}<span class="chip">통과 {c["passed"]}/{len(c["sc"])}</span> <span class="muted">{val} · 기관 {c["n"]}곳</span></div>'
            f'<div style="margin-top:6px">{chips}</div>'
            + report_box(conn, c["code"], c["name"], reps, closes.get(c["code"], (None, None))[1]) +
            f'<div class="muted" style="font-size:12px;margin-top:4px">'
            f'<a href="{score_url(code=c["code"])}">점수표로 평가하기 →</a></div></div>')
    note = f'<p class="muted">{len(rows)}개 종목 중 점수가 하나라도 계산된 {len(cards)}개 — 위 {min(len(cards), 40)}개를 보여 줘요.</p>'
    return page("종합", intro + picker + form + note + ("".join(out) or '<p class="muted">조건에 맞는 종목이 없어요. 기간을 늘려 보세요.</p>'))


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
        _NAV["has_update"] = "start_update" in S
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
            _CTX.pre = hub_tabs_row(url.path)
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
                    return self._redirect(perf_url(**{k: v for k, v in q.items() if not k.startswith("_")}))
                elif url.path == "/ideas" and q.get("t") == "cycle":      # 투자 사이클은 시장 묶음으로 옮겼어요 (예전 주소 호환)
                    return self._redirect("/cycle" + (("?" + urlencode({"cr": q["cr"]})) if q.get("cr") else ""))
                elif url.path == "/cycle":
                    body = view_cycle(conn, q)
                elif url.path == "/ideas":
                    body = view_ideas(conn, q)
                elif url.path == "/ranking":
                    return self._redirect("/ideas?" + urlencode({"t": "rank", **{k: v for k, v in q.items() if not k.startswith("_")}}))
                elif url.path == "/radar":
                    body = view_radar(conn, q)
                elif url.path == "/bills":
                    body = view_bills(conn, q)
                elif url.path == "/diag":
                    body = view_diag(conn, q)
                elif url.path == "/news":
                    body = view_news(conn, q)
                elif url.path == "/shorts":
                    body = view_shorts(conn, q)
                elif url.path == "/derivs":
                    body = view_derivs(conn, q)
                elif url.path == "/score":
                    return self._redirect(score_url(**{k: v for k, v in q.items() if not k.startswith("_")}))
                elif url.path == "/new":
                    body = view_new(conn, q)
                elif url.path == "/stock":
                    body = view_stock(conn, q.get("code", ""), q)
                else:
                    body = None
            finally:
                _CTX.pre = ""
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
            if path == "/radar/countries":
                n = min(int(self.headers.get("Content-Length") or 0), 32768)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore"),
                                                     keep_blank_values=True).items()}
                import radar as rd
                conn = sqlite3.connect(db_path)
                try:
                    rd.ensure(conn)
                    rd.ensure_countries(conn)
                    ok, errors = rd.import_text(conn, form.get("data", ""))
                finally:
                    conn.close()
                msg = f"✔ {ok}줄 반영했어요." + (f" 오류 {len(errors)}줄: " + " / ".join(errors[:3]) if errors else "")
                return self._redirect("/radar?" + urlencode({"cn": msg}))
            if path in ("/radar/add", "/radar/edit", "/radar/status", "/radar/del"):
                n = min(int(self.headers.get("Content-Length") or 0), 16384)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore"),
                                                     keep_blank_values=True).items()}
                import radar as rd
                conn = sqlite3.connect(db_path)
                try:
                    rd.ensure(conn)
                    rid = int(form["id"]) if str(form.get("id", "")).isdigit() else None
                    if path == "/radar/add":
                        rd.add(conn, form)
                    elif rid is not None and path == "/radar/edit":
                        rd.edit(conn, rid, form)
                    elif rid is not None and path == "/radar/status":
                        rd.set_status(conn, rid, form.get("status", ""))
                    elif rid is not None and path == "/radar/del":
                        rd.delete(conn, rid)
                finally:
                    conn.close()
                return self._redirect("/radar")
            if path in ("/shorts/add", "/shorts/edit", "/shorts/status", "/shorts/del"):
                n = min(int(self.headers.get("Content-Length") or 0), 16384)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore"),
                                                     keep_blank_values=True).items()}
                import shortreports as sr
                conn = sqlite3.connect(db_path)
                try:
                    sr.ensure(conn)
                    rid = int(form["id"]) if str(form.get("id", "")).isdigit() else None
                    if path == "/shorts/add":
                        sr.add(conn, form)
                    elif rid is not None and path == "/shorts/edit":
                        sr.edit(conn, rid, form)
                    elif rid is not None and path == "/shorts/status":
                        sr.set_status(conn, rid, form.get("status", ""))
                    elif rid is not None and path == "/shorts/del":
                        sr.delete(conn, rid)
                finally:
                    conn.close()
                return self._redirect("/shorts")
            if path == "/score/save":
                n = min(int(self.headers.get("Content-Length") or 0), 8192)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore"),
                                                     keep_blank_values=True).items()}
                code = form.get("code", "")
                if not SCORE_CODE.fullmatch(code):
                    return self._redirect(score_url())
                import score_card as sc
                conn = sqlite3.connect(db_path)
                try:
                    if form.get("remove"):
                        sc.remove(conn, code)
                        return self._redirect(score_url(view="rank", st="mine"))
                    sc.save(conn, code, form.get("name", ""), form)
                finally:
                    conn.close()
                return self._redirect(score_url(code=code, saved=1))
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
                ajax = parse_qs(urlparse(self.path).query).get("ajax") == ["1"]
                res = {}
                if path.endswith("check"):
                    res = S["check_update"]()
                    msg = (f"✖ {res['error']}" if res.get("error") else
                           f"새 버전이 있습니다 ({res['behind']}개 변경)" if res.get("behind") else "✔ 최신 버전입니다")
                else:
                    ok, why = S["start_update"]()
                    msg = ("✔ " if ok else "✖ ") + why
                if ajax:        # 헤더 '업데이트' 메뉴: 화면을 떠나지 않고 결과만 돌려줌
                    return self._send(200, "application/json; charset=utf-8", json.dumps(
                        {"msg": msg, "behind": int(res.get("behind") or 0), "items": res.get("items") or [], "checked": res.get("checked", "")},
                        ensure_ascii=False).encode())
                return self._redirect("/settings?" + urlencode({"nt": msg}))
            if path == "/settings/notify-test" and settings_api:
                ok, why = settings()["test_notify"]()
                return self._redirect("/settings?" + urlencode({"nt": ("✔ " if ok else "✖ ") + why}))
            if path == "/settings/ext-check" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
                src = (form.get("src") or [""])[0]
                if src not in ("krx", "lending", "bonds", "news", "etf", "ecos", "policy", "bills"):
                    return self._redirect("/settings")
                try:
                    res = settings()["ext_check"](src)
                except Exception as e:
                    res = {"src": src, "error": f"확인 중 오류: {type(e).__name__}: {str(e)[:120]}"}
                return self._send(200, "text/html; charset=utf-8", view_ext_check(res))
            if path == "/legis/prepare" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                self.rfile.read(n)
                S = settings()
                ok, why = S["legis_prepare"]() if "legis_prepare" in S else (False, "이 서버 버전은 지원하지 않아요")
                return self._redirect("/ideas?" + urlencode({"t": "legis", "msg": ("✔ " if ok else "✖ ") + why}))
            if path == "/legstudy/run" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                self.rfile.read(n)
                S = settings()
                ok, why = S["legstudy_run"]() if "legstudy_run" in S else (False, "이 서버 버전은 지원하지 않아요")
                return self._redirect("/ideas?" + urlencode({"t": "legstudy", "msg": ("✔ " if ok else "✖ ") + why}))
            if path.startswith("/events/") and settings_api:
                act = path[len("/events/"):]
                if act not in ("add", "del", "px"):
                    return self._redirect("/ideas?t=events")
                n = min(int(self.headers.get("Content-Length") or 0), 8192)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore")).items()}
                S = settings()
                ok, why = S["event_action"](act, form) if "event_action" in S else (False, "이 서버 버전은 지원하지 않아요")
                return self._redirect("/ideas?" + urlencode({"t": "events", "msg": ("✔ " if ok else "✖ ") + why}))
            if path.startswith("/legis/") and path != "/legis/prepare" and settings_api:
                act = path[len("/legis/"):]
                if act not in ("watch_add", "watch_del", "watch_import", "fb_no", "fb_yes", "fb_del", "px_bill"):
                    return self._redirect("/ideas?t=legis")
                n = min(int(self.headers.get("Content-Length") or 0), 8192)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore")).items()}
                if ":" in form.get("target", ""):               # 후보 직접 추가 폼: '이 법안만' / '분야 전체: …'
                    form["scope"], form["ref"] = form["target"].split(":", 1)
                S = settings()
                ok, why = S["legis_action"](act, form) if "legis_action" in S else (False, "이 서버 버전은 지원하지 않아요")
                back = {"t": "bill", "key": form["back"]} if form.get("back", "").startswith("bill:") else {"t": "legis"}
                if act.startswith("watch"):
                    back = {"t": "legis"}
                url = ("/bills?" if back["t"] == "bill" else "/ideas?") + urlencode({**back, "msg": ("✔ " if ok else "✖ ") + why})
                return self._redirect(url)
            if path.startswith("/bills/") and settings_api:
                act = path[len("/bills/"):]
                if act not in ("refresh", "add_rule", "del_rule", "link", "unlink", "fetch_text", "save_text"):
                    return self._redirect("/bills")
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "ignore")).items()}
                S = settings()
                ok, why = S["bills_action"](act, form) if "bills_action" in S else (False, "이 서버 버전은 지원하지 않아요")
                back = {"t": "bill", "key": form.get("bill", "")} if act in ("fetch_text", "save_text") or (act == "link" and form.get("bill", "").startswith("bill:") and (self.headers.get("Referer") or "").find("t=bill") >= 0) else {}
                return self._redirect("/bills?" + urlencode({**back, "msg": ("✔ " if ok else "✖ ") + why}))
            if path == "/news/refresh" and settings_api:
                S = settings()
                ok, why = S["news_refresh"]() if "news_refresh" in S else (False, "이 서버 버전은 지금 받기를 지원하지 않아요")
                return self._redirect("/news?" + urlencode({"msg": ("✔ " if ok else "✖ ") + why}))
            if path == "/settings/krx-key" and settings_api:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
                ok, why = settings()["save_krx_key"]((form.get("key") or [""])[0])
                return self._redirect("/settings?" + urlencode({"kn": why, **({} if ok else {"kbad": "1"})}))
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
    _DB["path"] = str(db_path)
    _DB["get_key"] = get_key
    run_cmd = [sys.executable, str(Path(__file__).resolve().parent / "dart_server.py"), "run"]
    server = ThreadingHTTPServer((host, port), make_handler(db_path, status_path, run_cmd, child_env, get_key, save_key, settings_api))
    print(f"대시보드: http://{host}:{port}  (끄려면 Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
