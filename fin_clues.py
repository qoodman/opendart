# -*- coding: utf-8 -*-
"""
보고서의 숫자 말고 '단서': 공시 본문과 DART 세부 항목에서 투자 판단에 힌트가 될 만한 것들을 모아요.

받는 것 (종목마다):
  ① 감사의견·감사인·핵심감사사항 (사업보고서) — 비적정 의견, 감사인 교체, 강조사항, 감사인이 '위험하다'고 짚은 영역(재고·매출 인식·개발비·손상·소송 …)
  ② 직원 현황 — 직원 수 증감, 1인 평균 급여, 평균 근속 (사람이 늘어나는 회사인가)
  ③ 주주환원 — 배당성향·주당배당금, 자기주식 취득·소각
  ④ 임원·주요주주 내부자 거래 — 최근 120일 순매수/순매도 (자기 회사 주식을 사는지 파는지)
  ⑤ 최대주주 지분율과 변동
  ⑥ 최근 보고서 본문에서 읽은 것 (정확하지 않을 수 있어서 원문 발췌를 같이 보여 줘요):
     연구개발비·매출 대비 비율·개발비 자산화 비중, 특허(지적재산권) 건수, 재고자산 구성·회전율, 수주잔고,
     소송·우발부채·계속기업 같은 위험 문구(‘해당사항 없음’은 걸러 냄), 증설·양산·임상·허가 같은 성장 문구
한계: 본문 읽기는 회사마다 표 모양이 달라 못 읽는 경우가 많고, 읽은 값이 틀릴 수 있어요. 단서는 '살펴볼 곳'을 알려 주는 것이지 결론이 아니에요.
"""
import datetime as dt
import html
import io
import json
import re
import sqlite3
import time
import zipfile

import requests

DART_BASE = "https://opendart.fss.or.kr/api"
INSIDER_DAYS = 120
RISK_WORDS = ["계속기업", "횡령", "배임", "감사범위", "관리종목", "불성실공시", "상장폐지", "채무불이행", "부도", "회생", "워크아웃", "자본잠식",
              "소송", "우발부채", "지급보증", "내부회계관리제도 비적정", "의견거절", "한정"]
GROWTH_WORDS = ["증설", "양산", "임상 3상", "임상3상", "품목허가", "신규 수주", "공급계약", "수주 증가", "신제품 출시", "시설투자"]
KAM_TAGS = [("재고", "재고자산"), ("매출", "수익인식"), ("매출", "매출 인식"), ("개발비", "개발비"), ("손상", "손상"), ("소송", "소송"), ("우발", "우발"),
            ("파생", "파생"), ("특수관계", "특수관계"), ("대손", "대손"), ("영업권", "영업권"), ("계속기업", "계속기업")]


class ClueError(Exception):
    pass


def ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS fin_clue (code TEXT PRIMARY KEY, year INTEGER, rcept_no TEXT, payload TEXT, fetched TEXT);
    """)


# ---------------- DART 호출 ----------------
def _json(path, key, session=None, **params):
    s = session or requests
    r = s.get(f"{DART_BASE}/{path}", params={"crtfc_key": key, **params}, timeout=30)
    if r.status_code != 200:
        raise ClueError(f"DART HTTP {r.status_code}")
    try:
        j = r.json()
    except ValueError:
        raise ClueError("DART 응답을 읽지 못함")
    st = str(j.get("status", ""))
    if st == "020":
        raise ClueError("DART 하루 호출 한도를 넘었어요")
    if st in ("013", "014"):
        return []
    if st != "000":
        raise ClueError(f"DART {st} {j.get('message', '')}".strip())
    return j.get("list") or []


def _num(v):
    t = str(v if v is not None else "").replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", t)
        return float(m.group(0)) if m else None


# ---------------- 구조화 항목 ----------------
def audit_clue(rows):
    """감사의견 행들 → 단서 목록 [(kind, text)] 과 상세 dict. rows는 당기·전기·전전기 순(보통)"""
    if not rows:
        return [], {}
    cur = rows[0]
    op = (cur.get("adt_opinion") or "").strip()
    auditor = (cur.get("adtor") or "").strip()
    out = []
    if op:
        if "적정" in op and not any(w in op for w in ("한정", "부적정", "거절")):
            out.append(("good", f"감사의견 {op} ({auditor})"))
        else:
            out.append(("warn", f"감사의견 '{op}' — 적정이 아니에요 ({auditor})"))
    prev = rows[1] if len(rows) > 1 else None
    if prev and prev.get("adtor") and auditor and prev["adtor"].strip() != auditor:
        out.append(("warn", f"감사인이 바뀌었어요 ({prev['adtor'].strip()} → {auditor}) — 교체 사유를 확인해 보세요"))
    emph = (cur.get("emphs_matter") or cur.get("adt_reprt_spcmnt_matter") or "").strip()
    kam = (cur.get("core_adt_matter") or "").strip()
    if emph and emph not in ("-", "해당사항 없음", "해당사항없음"):
        bad = "계속기업" in emph
        out.append(("warn" if bad else "info", ("감사보고서 강조사항: " + emph[:160]) + ("…" if len(emph) > 160 else "")))
    tags = []
    if kam and kam not in ("-", "해당사항 없음"):
        for tag, w in KAM_TAGS:
            if w in kam and tag not in tags:
                tags.append(tag)
        out.append(("info", "감사인이 중요하게 본 영역(핵심감사사항): " + (", ".join(tags) if tags else kam[:100])))
    return out, dict(opinion=op, auditor=auditor, kam_tags=tags)


def staff_clue(cur_rows, prev_rows):
    """직원 현황(당기·전기) → 단서 + 숫자"""
    def agg(rows):
        tot = pay = ten = None
        for r in rows or []:
            if (r.get("fo_bbm") or "").strip() in ("합계", "계", "") and (r.get("sexdstn") or "").strip() in ("계", "합계", "") and _num(r.get("sm")):
                tot = _num(r.get("sm"))
                pay = _num(r.get("jan_salary_am"))
                ten = _num(r.get("avrg_cnwk_sdytrn"))
                break
        if tot is None and rows:                     # 합계 줄을 못 찾으면 합산
            tot = sum(_num(r.get("sm")) or 0 for r in rows)
            pays = [_num(r.get("jan_salary_am")) for r in rows if _num(r.get("jan_salary_am"))]
            pay = sum(pays) / len(pays) if pays else None
            tens = [_num(r.get("avrg_cnwk_sdytrn")) for r in rows if _num(r.get("avrg_cnwk_sdytrn"))]
            ten = sum(tens) / len(tens) if tens else None
        return tot, pay, ten
    c, p = agg(cur_rows), agg(prev_rows)
    if not c[0]:
        return [], {}
    out = []
    info = dict(staff=c[0], staff_prev=p[0], pay=c[1], tenure=c[2])
    if p[0]:
        g = c[0] / p[0] - 1
        out.append(("good" if g >= 0.05 else "warn" if g <= -0.10 else "info", f"직원 {c[0]:,.0f}명 (전년 {p[0]:,.0f}명, {g:+.0%})" + (" — 인력을 늘리는 중" if g >= 0.05 else " — 인력 감축" if g <= -0.10 else "")))
    else:
        out.append(("info", f"직원 {c[0]:,.0f}명"))
    if c[1]:
        out.append(("info", f"1인 평균 급여 {c[1] / 1e6:,.0f}백만원" + (f" · 평균 근속 {c[2]:.1f}년" if c[2] else "")))
    return out, info


def return_clue(div_rows, tess_rows):
    """배당(alotMatter)·자기주식 현황 → 단서"""
    out, info = [], {}
    for r in div_rows or []:
        se = (r.get("se") or "")
        if "배당성향" in se and "현금" in se:
            v = _num(r.get("thstrm"))
            if v is not None:
                info["payout"] = v
                out.append(("good" if v >= 20 else "info", f"현금배당성향 {v:.1f}%"))
        elif "주당 현금배당금" in se.replace(" ", " ") or ("주당" in se and "배당금" in se and "현금" in se):
            v, p = _num(r.get("thstrm")), _num(r.get("frmtrm"))
            if v is not None and "dps" not in info:
                info["dps"] = v
                out.append(("good" if (p is not None and v > p) else "info", f"주당 현금배당금 {v:,.0f}원" + (f" (전년 {p:,.0f}원)" if p is not None else "")))
    acq = inc = 0.0
    for r in tess_rows or []:
        if (r.get("stock_knd") or "").strip() not in ("보통주", ""):
            continue
        a, i = _num(r.get("change_qy_acqs")), _num(r.get("change_qy_incnr"))
        if a and (r.get("acqs_mth1") or "") in ("합계", "소계", "총계") or (a and not r.get("acqs_mth1")):
            pass
        acq += a or 0
        inc += i or 0
    if acq > 0 or inc > 0:
        info["buyback"], info["burn"] = acq, inc
        out.append(("good", f"자기주식 {'소각 ' + format(inc, ',.0f') + '주 · ' if inc else ''}{'취득 ' + format(acq, ',.0f') + '주' if acq else ''} (보통주, 당기)"))
    return out, info


def insider_clue(rows, today=None):
    """임원·주요주주 소유보고 → 최근 120일 순매수/순매도 단서 (증감 주식 수 합)"""
    today = today or dt.date.today()
    lo = (today - dt.timedelta(days=INSIDER_DAYS)).isoformat()
    buy = sell = 0.0
    buyers, sellers, n = set(), set(), 0
    for r in rows or []:
        d = (r.get("rcept_dt") or "")
        d = f"{d[:4]}-{d[4:6]}-{d[6:]}" if re.fullmatch(r"\d{8}", d) else d
        if d < lo:
            continue
        chg = _num(r.get("sp_stock_lmp_irds_cnt"))
        if not chg:
            continue
        n += 1
        who = (r.get("repror") or "").strip()
        if chg > 0:
            buy += chg
            buyers.add(who)
        else:
            sell += -chg
            sellers.add(who)
    if not n:
        return [], dict(insider_net=0)
    net = buy - sell
    info = dict(insider_buy=buy, insider_sell=sell, insider_net=net, insider_buyers=len(buyers), insider_sellers=len(sellers))
    kind = "good" if net > 0 and len(buyers) >= 2 else "warn" if net < 0 and len(sellers) >= 3 else "info"
    return [(kind, f"최근 {INSIDER_DAYS}일 내부자 거래: 매수 {buy:,.0f}주({len(buyers)}명) · 매도 {sell:,.0f}주({len(sellers)}명) → 순{'매수' if net > 0 else '매도'} {abs(net):,.0f}주")], info


def holder_clue(rows):
    """최대주주 현황 → 지분율과 변동"""
    best = None
    for r in rows or []:
        rt = _num(r.get("trmend_posesn_stock_qota_rt"))
        if rt is None:
            continue
        if (r.get("nm") or "").strip() in ("계", "합계"):
            best = ("계", rt, _num(r.get("bsis_posesn_stock_qota_rt")))
            break
        if best is None:
            best = (r.get("nm") or "", rt, _num(r.get("bsis_posesn_stock_qota_rt")))
    if not best:
        return [], {}
    nm, rt, pv = best
    txt = f"최대주주 측 지분 {rt:.1f}%" + (f" (기초 {pv:.1f}% → {rt - pv:+.1f}%p)" if pv is not None else "")
    kind = "warn" if (pv is not None and rt - pv <= -3) else "good" if (pv is not None and rt - pv >= 1) else "info"
    return [(kind, txt)], dict(holder_pct=rt, holder_chg=None if pv is None else rt - pv)


# ---------------- 공시 본문 ----------------
def fetch_document_text(rcept_no, key, session=None):
    """공시 원문(zip의 XML) → 줄 단위 텍스트 (표의 칸은 탭). 못 받으면 ''"""
    s = session or requests
    r = s.get(f"{DART_BASE}/document.xml", params=dict(crtfc_key=key, rcept_no=rcept_no), timeout=60)
    if r.status_code != 200 or not r.content[:2] == b"PK":
        return ""
    z = zipfile.ZipFile(io.BytesIO(r.content))
    parts = []
    for n in z.namelist():
        if n.lower().endswith((".xml", ".html", ".htm")):
            raw = z.read(n)
            try:
                parts.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                parts.append(raw.decode("cp949", "ignore"))
    return xml_to_text("\n".join(parts))


def xml_to_text(x):
    x = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", x, flags=re.S | re.I)
    x = re.sub(r"</(TR|P|TITLE|SECTION-\d|LIBRARY|BODY|DIV|LI)>", "\n", x, flags=re.I)
    x = re.sub(r"<br\s*/?>", "\n", x, flags=re.I)
    x = re.sub(r"</(TD|TH|TE|TU)>", "\t", x, flags=re.I)
    x = re.sub(r"<[^>]+>", " ", x)
    x = html.unescape(x)
    lines = []
    for ln in x.split("\n"):
        ln = re.sub(r"[  ]+", " ", ln)
        ln = re.sub(r"\s*\t\s*", "\t", ln).strip(" \t")
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def _nums(line):
    return [float(v.replace(",", "")) for v in re.findall(r"-?\d[\d,]*\.?\d*", line) if re.search(r"\d", v)]


def _unit_before(lines, i, back=12):
    for j in range(i, max(-1, i - back), -1):
        m = re.search(r"단위\s*[:：]\s*([^\)\s,]+)", lines[j])
        if m:
            return m.group(1)
    return ""


def _snip(s, n=160):
    s = re.sub(r"\s+", " ", s.replace("\t", " | ")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def read_rnd(lines):
    """연구개발: 비용 합계, 매출 대비 비율, 개발비(무형자산) 자산화 → (단서들, 상세)"""
    out, info = [], {}
    for i, ln in enumerate(lines):
        flat = ln.replace("\t", " ")
        if "매출액 비율" in flat.replace(" ", "") or ("매출액" in flat and "비율" in flat and "연구개발" in flat):
            nums = [v for v in _nums(flat.split("]")[-1] if "]" in flat else flat) if 0 <= v <= 100]
            if nums and "rnd_ratio" not in info:
                info["rnd_ratio"] = nums[0]
                info["rnd_ratio_src"] = _snip(ln)
        if re.search(r"연구개발\s*비용\s*계|연구개발비\s*합계|연구개발비용\s*합계", flat) and "rnd_total" not in info:
            nums = _nums(re.sub(r"연구개발\s*비용\s*계|연구개발비\s*합계|연구개발비용\s*합계", "", flat))
            if nums:
                info["rnd_total"] = nums[0]
                info["rnd_unit"] = _unit_before(lines, i)
                info["rnd_total_src"] = _snip(ln)
        if "개발비" in flat and ("무형자산" in flat) and "rnd_cap" not in info and re.search(r"회계처리|자산화|개발비\(무형자산\)", flat):
            nums = _nums(flat.split("무형자산")[-1])
            if nums:
                info["rnd_cap"] = nums[0]
                info["rnd_cap_src"] = _snip(ln)
    if "rnd_ratio" in info:
        r = info["rnd_ratio"]
        out.append(("good" if r >= 5 else "info", f"연구개발비 / 매출액 {r:.2f}% ({'매출 대비 연구개발 비중이 높은 편' if r >= 5 else '보고서에 적힌 값'})", info["rnd_ratio_src"]))
    elif "rnd_total" in info:
        out.append(("info", f"연구개발비용 합계 {info['rnd_total']:,.0f} {info.get('rnd_unit', '')}", info["rnd_total_src"]))
    if info.get("rnd_cap") and info.get("rnd_total") and info["rnd_total"] > 0 and info["rnd_cap"] / info["rnd_total"] >= 0.5:
        out.append(("warn", f"연구개발비의 {info['rnd_cap'] / info['rnd_total']:.0%}를 개발비(무형자산)로 자산화 — 비용 처리 대신 자산으로 잡으면 이익이 좋아 보일 수 있어요", info.get("rnd_cap_src", "")))
    return out, info


def read_patents(lines):
    """특허(지적재산권): 등록번호 개수 또는 '특허 N건' 문구"""
    txt = "\n".join(lines)
    nos = set(re.findall(r"\b10-\d{7}\b", txt)) | set(re.findall(r"\b10-\d{4}-\d{7}\b", txt))
    stated = None
    src = ""
    for ln in lines:
        for m in re.finditer(r"특허[^\n。]{0,40}?(\d[\d,]*)\s*건", ln):
            v = int(m.group(1).replace(",", ""))
            if stated is None or v > stated:
                stated, src = v, _snip(ln)
    out, info = [], {}
    if nos or stated:
        n = max(len(nos), stated or 0)
        info["patents"] = n
        out.append(("info", f"특허(지적재산권) 약 {n:,}건" + (" (등록번호를 센 값)" if len(nos) >= (stated or 0) and nos else " (본문에 적힌 건수)"), src or (f"등록번호 예: {sorted(nos)[0]}" if nos else "")))
    return out, info


def read_inventory(lines):
    """재고자산: 회전율, 구성(원재료·재공품·제품·상품)"""
    out, info = [], {}
    comp = {}
    for i, ln in enumerate(lines):
        flat = ln.replace("\t", " ")
        m = re.search(r"재고자산\s*회전율[^\d\-]{0,60}(-?\d+\.?\d*)\s*(?:회|배)?", flat)
        if m and "inv_turn" not in info and 0 < float(m.group(1)) < 500:
            info["inv_turn"] = float(m.group(1))
            info["inv_turn_src"] = _snip(ln)
        first = flat.split(" ")[0] if flat else ""
        head = ln.split("\t")[0].strip()
        if head in ("원재료", "재공품", "제품", "상품", "저장품", "반제품", "미착품") and head not in comp:
            n = _nums("\t".join(ln.split("\t")[1:]))
            if n:
                comp[head] = n[0]
    tot = sum(v for v in comp.values() if v > 0)
    if tot > 0 and len(comp) >= 2:
        info["inv_comp"] = comp
        fin = (comp.get("제품", 0) + comp.get("상품", 0)) / tot
        info["inv_finished_share"] = fin
        out.append(("info", "재고 구성: " + " · ".join(f"{k} {v / tot:.0%}" for k, v in comp.items() if v > 0) + (" — 팔 물건(제품·상품) 비중이 큼" if fin >= 0.6 else ""), ""))
    if "inv_turn" in info:
        t = info["inv_turn"]
        out.append(("info", f"재고자산회전율 {t:.1f}회 (클수록 재고가 빨리 팔려요, 업종마다 달라요)", info["inv_turn_src"]))
    return out, info


def read_backlog(lines):
    """수주잔고"""
    for i, ln in enumerate(lines):
        flat = ln.replace("\t", " ")
        if re.search(r"수주\s*잔[고액]", flat) and not re.search(r"해당사항|해당 사항", flat):
            n = [v for v in _nums(flat) if v >= 1]
            if n:
                unit = _unit_before(lines, i)
                return [("info", f"수주잔고 {n[-1]:,.0f} {unit}".strip(), _snip(ln))], dict(backlog=n[-1], backlog_unit=unit)
    return [], {}


def scan_words(lines, words, kind, limit=4):
    """문구 스캔(줄 단위): '해당사항 없음'·'없습니다' 같은 부정 문맥의 줄은 거르고 문장을 발췌. → [(kind, text, snippet)]"""
    out = []
    neg = re.compile(r"해당\s*사항\s*(이\s*)?없|없습니다|존재하지\s*않|발생하지\s*않|아니|제외|않았|않습니다")
    for w in words:
        hits = [_snip(ln, 170) for ln in lines if w in ln and not neg.search(ln)]
        if hits:
            out.append((kind, f"본문에 '{w}' 언급 {len(hits)}곳", hits[0]))
        if len(out) >= limit:
            break
    return out


def read_document(lines):
    """본문 → (단서 [(kind, text, 발췌)], 상세 dict)"""
    clues, info = [], {}
    for fn in (read_rnd, read_patents, read_inventory, read_backlog):
        try:
            c, i = fn(lines)
        except Exception:
            c, i = [], {}
        clues += c
        info.update(i)
    clues += scan_words(lines, RISK_WORDS, "warn", 4)
    clues += scan_words(lines, GROWTH_WORDS, "good", 3)
    return clues, info


# ---------------- 한 종목 ----------------
def collect(code, corp, key, rcept_no, year, today=None, session=None, sleep=0.15, with_doc=True):
    """종목 하나의 단서 모으기 → dict(items=[(kind, text, 발췌)], info). 항목마다 따로 실패해도 나머지는 계속"""
    today = today or dt.date.today()
    base_year = today.year - 1 if today.month >= 4 else today.year - 2          # 사업보고서가 나온 가장 최근 연도
    items, info, errs = [], {}, []

    def step(name, fn):
        try:
            c, i = fn()
            for x in c:
                items.append(x if len(x) == 3 else (x[0], x[1], ""))
            info.update(i)
        except ClueError as e:
            errs.append(f"{name}: {e}")
            if "한도" in str(e):
                raise
        finally:
            time.sleep(sleep)
    step("감사의견", lambda: audit_clue(_json("accnutAdtorNmNdAdtOpinion.json", key, session, corp_code=corp, bsns_year=str(base_year), reprt_code="11011")))
    step("직원", lambda: staff_clue(_json("empSttus.json", key, session, corp_code=corp, bsns_year=str(base_year), reprt_code="11011"),
                                  _json("empSttus.json", key, session, corp_code=corp, bsns_year=str(base_year - 1), reprt_code="11011")))
    step("주주환원", lambda: return_clue(_json("alotMatter.json", key, session, corp_code=corp, bsns_year=str(base_year), reprt_code="11011"),
                                       _json("tesstkAcqsDspsSttus.json", key, session, corp_code=corp, bsns_year=str(base_year), reprt_code="11011")))
    step("내부자", lambda: insider_clue(_json("elestock.json", key, session, corp_code=corp), today))
    step("최대주주", lambda: holder_clue(_json("hyslrSttus.json", key, session, corp_code=corp, bsns_year=str(base_year), reprt_code="11011")))
    if with_doc and rcept_no:
        def doc():
            txt = fetch_document_text(rcept_no, key, session)
            if not txt:
                raise ClueError("공시 원문을 못 받았어요")
            return read_document(txt.split("\n"))
        step("본문", doc)
    info["warn_n"] = sum(1 for k, _, _ in items if k == "warn")
    info["good_n"] = sum(1 for k, _, _ in items if k == "good")
    return dict(items=items, info=info, errors=errs, base_year=base_year)


def store(conn, code, year, rcept_no, res, today=None):
    ensure(conn)
    conn.execute("INSERT OR REPLACE INTO fin_clue VALUES (?,?,?,?,?)", (code, year, rcept_no, json.dumps(res, ensure_ascii=False), (today or dt.date.today()).isoformat()))


def load(conn, code):
    try:
        r = conn.execute("SELECT payload, fetched, year, rcept_no FROM fin_clue WHERE code=?", (code,)).fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    d = json.loads(r[0])
    d["fetched"], d["year"], d["rcept_no"] = r[1], r[2], r[3]
    return d


def load_all_info(conn):
    """{코드: info dict} — 목록 필터용"""
    try:
        return {c: json.loads(p).get("info", {}) for c, p in conn.execute("SELECT code, payload FROM fin_clue")}
    except sqlite3.Error:
        return {}


def run(db_path, key, cache_dir, log=print, progress=None, today=None, session=None, max_age_days=14, sleep=0.15, limit=None):
    """재무제표 분석이 끝난 종목마다 단서를 모아 저장. 같은 보고서로 최근 max_age_days일 안에 모은 종목은 건너뜀. → dict"""
    import score_auto
    conn = sqlite3.connect(db_path)
    try:
        ensure(conn)
        today = today or dt.date.today()
        try:
            rows = conn.execute("SELECT code, year, rcept_no, name FROM fin_report ORDER BY year DESC, rcode DESC").fetchall()
        except sqlite3.Error:
            return dict(대상=0, 받음=0, 실패=0, 건너뜀=0, 메모="재무제표 분석을 먼저 해 주세요")
        seen, todo = set(), []
        for c, y, rn, nm in rows:
            if c not in seen:
                seen.add(c)
                todo.append((c, y, rn, nm))
        if limit:
            todo = todo[:limit]
        have = {c: (rn, f) for c, rn, f in conn.execute("SELECT code, rcept_no, fetched FROM fin_clue")}
        res = dict(대상=len(todo), 받음=0, 실패=0, 건너뜀=0)
        streak = 0
        for i, (code, year, rn, nm) in enumerate(todo):
            if progress and i % 3 == 0:
                progress(f"{i}/{len(todo)} 종목")
            h = have.get(code)
            if h and h[0] == rn and h[1] >= (today - dt.timedelta(days=max_age_days)).isoformat():
                res["건너뜀"] += 1
                continue
            if streak >= 6:
                log("연속 실패가 많아 중단합니다. 다음에 이어서 받아요.")
                break
            try:
                corp = score_auto.corp_code_for(code, cache_dir, key, session=session)
                got = collect(code, corp, key, rn, year, today, session, sleep)
                store(conn, code, year, rn, got, today)
                conn.commit()
                res["받음"] += 1
                streak = 0
            except (score_auto.AutoError, ClueError, requests.RequestException) as e:
                res["실패"] += 1
                streak += 1
                log(f"  ✖ {code} {nm}: {e}")
                if "한도" in str(e):
                    break
        log(f"[공시 단서] {res}")
        return res
    finally:
        conn.close()


def run_job(db_path, key, cache_dir, status_path, log=print, limit=None):
    """별도 프로세스용: 진행 상황을 status_path(JSON)에 적으면서 run()을 돌림"""
    import os
    st = dict(running=True, pid=os.getpid(), started=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), finished="",
              steps=[dict(name="공시 단서 받기 (감사의견·직원·배당·내부자·본문)", state="run", msg="시작")])

    def save():
        try:
            tmp = status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, status_path)
        except OSError:
            pass
    save()
    last = {"t": 0.0}

    def progress(msg):
        if time.time() - last["t"] > 3:
            last["t"] = time.time()
            st["steps"][0]["msg"] = msg
            save()
    try:
        if not key:
            raise ClueError("DART_API_KEY가 없어요")
        res = run(db_path, key, cache_dir, log=log, progress=progress, limit=limit)
        st["steps"][0].update(state="ok", msg=" · ".join(f"{k} {v}" for k, v in res.items()))
    except Exception as e:
        st["steps"][0].update(state="fail", msg=f"{type(e).__name__}: {str(e)[:160]}")
        log(f"공시 단서 받기 실패: {e}")
    st["running"] = False
    st["finished"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save()
