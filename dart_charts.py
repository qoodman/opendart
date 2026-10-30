# -*- coding: utf-8 -*-
"""
대시보드 그래프 (외부 라이브러리 없이 SVG + 작은 스크립트)

- price_chart: 종목 주가 선 + 기관 매수 점(변동일·평균단가) + 기관 평단 점선. 손가락/마우스로 짚으면 그날 값 표시
- bar_list:    가로 막대 목록 (할인율, 매수금액 등) — HTML로 그려서 글자가 또렷함
- sparkline:   카드 안 작은 추세선 + 기관 평단 점선

색은 검증된 3색(파랑·주황·청록)만 씀. 넷째 제출인부터는 '기타'(회색)로 묶음.
"""
import json
from html import escape

# 차트 색 (밝은/어두운 화면). 텍스트는 데이터 색을 쓰지 않음.
CHART_CSS = """
:root{--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--sx:#9a9993;--price:#55585e;--grid:#e8e9ec;--ring:#fff}
@media (prefers-color-scheme:dark){:root{--s1:#3987e5;--s2:#d95926;--s3:#199e70;--sx:#7d7c77;
--price:#b9bcc2;--grid:#2c2f35;--ring:#1d1f23}}
.viz{position:relative;margin:6px 0 4px}.viz svg{display:block;width:100%;touch-action:pan-y}
.viz .tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:6px 9px;font-size:12px;line-height:1.45;box-shadow:0 2px 8px rgba(0,0,0,.12);
display:none;white-space:nowrap;z-index:2}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin:2px 0 4px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:0}
.legend i.ln{width:16px;height:2px;border-radius:1px;vertical-align:3px}
.legend i.dot{width:16px;height:2px;border-radius:0;vertical-align:3px;
background:repeating-linear-gradient(90deg,var(--muted) 0 2px,transparent 2px 5px)}
.legend i.dash{width:16px;height:0;border-top:2px dashed var(--fg);border-radius:0;vertical-align:3px}
.bars{display:grid;grid-template-columns:minmax(64px,34%) 1fr auto;gap:6px 8px;align-items:center;
font-size:13px;margin:6px 0 10px}.bars a{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bars .track{height:12px;position:relative}.bars .fill{position:absolute;left:0;top:0;bottom:0;
background:var(--s1);border-radius:0 4px 4px 0;min-width:2px}.bars .v{font-variant-numeric:tabular-nums;
color:var(--fg);text-align:right}
.spark{display:block;width:100%;height:38px;margin-top:6px}
"""

# 주가 그래프 스크립트: <div class="viz" data-chart='{...}'> 를 찾아 실제 폭에 맞게 그림
CHART_JS = r"""
(function(){
const NS='http://www.w3.org/2000/svg';
const fmt=v=>Math.round(v).toLocaleString('ko-KR');
function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}
function nice(lo,hi,n){const span=hi-lo||Math.abs(hi)||1;const raw=span/n;const mag=Math.pow(10,Math.floor(Math.log10(raw)));
 const step=[1,2,2.5,5,10].map(m=>m*mag).find(s=>s>=raw);const a=Math.floor(lo/step)*step,b=Math.ceil(hi/step)*step;
 const t=[];for(let v=a;v<=b+step/2;v+=step)t.push(v);return t;}
function draw(box){
 const d=JSON.parse(box.dataset.chart);box.innerHTML='';
 const W=box.clientWidth||340,H=Math.max(200,Math.min(300,W*0.6)),m={l:52,r:12,t:14,b:24};
 const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,height:H,role:'img','aria-label':d.title||'주가 그래프'},box);
 const tip=document.createElement('div');tip.className='tip';box.appendChild(tip);
 const n=d.dates.length;if(!n){box.textContent='주가 데이터가 아직 없습니다.';return;}
 const idx=s=>{let lo=0,hi=n-1;if(s<=d.dates[0])return 0;if(s>=d.dates[n-1])return n-1;
   while(lo<hi){const mid=(lo+hi+1)>>1;if(d.dates[mid]<=s)lo=mid;else hi=mid-1;}return lo;};
 const vals=d.close.filter(v=>v!=null).concat(d.buys.map(b=>b.p));if(d.avg)vals.push(d.avg);if(d.target)vals.push(d.target);
 const ticks=nice(Math.min(...vals),Math.max(...vals),4);const y0=ticks[0],y1=ticks[ticks.length-1];
 const X=i=>m.l+(n<2?0:i*(W-m.l-m.r)/(n-1)),Y=v=>m.t+(1-(v-y0)/(y1-y0||1))*(H-m.t-m.b);
 for(const t of ticks){el('line',{x1:m.l,x2:W-m.r,y1:Y(t),y2:Y(t),stroke:'var(--grid)','stroke-width':1},svg);
  const tx=el('text',{x:m.l-6,y:Y(t)+4,'text-anchor':'end','font-size':11,fill:'var(--muted)'},svg);
  tx.textContent=t>=10000?(t/10000).toLocaleString('ko-KR',{maximumFractionDigits:2})+'만':fmt(t);}
 for(const i of [0,Math.floor((n-1)/2),n-1]){const tx=el('text',{x:X(i),y:H-6,'font-size':11,fill:'var(--muted)',
  'text-anchor':i==0?'start':i==n-1?'end':'middle'},svg);tx.textContent=d.dates[i].slice(2).replace(/-/g,'.');}
 let path='';d.close.forEach((v,i)=>{if(v!=null)path+=(path?'L':'M')+X(i).toFixed(1)+','+Y(v).toFixed(1);});
 el('path',{d:path,fill:'none',stroke:'var(--price)','stroke-width':2,'stroke-linejoin':'round'},svg);
 if(d.avg){el('line',{x1:m.l,x2:W-m.r,y1:Y(d.avg),y2:Y(d.avg),stroke:'var(--fg)','stroke-width':1.5,'stroke-dasharray':'5 4'},svg);
  const lb=el('text',{x:W-m.r-2,y:Y(d.avg)-5,'text-anchor':'end','font-size':11,fill:'var(--fg)'},svg);lb.textContent='기관 평단 '+fmt(d.avg);}
 if(d.target){el('line',{x1:m.l,x2:W-m.r,y1:Y(d.target),y2:Y(d.target),stroke:'var(--muted)','stroke-width':2,'stroke-dasharray':'1.5 4','stroke-linecap':'round'},svg);
  const lb=el('text',{x:m.l+4,y:Y(d.target)-5,'font-size':11,fill:'var(--muted)'},svg);lb.textContent='목표가 평균 '+fmt(d.target);}
 const cross=el('line',{y1:m.t,y2:H-m.b,stroke:'var(--muted)','stroke-width':1,visibility:'hidden'},svg);
 for(const b of d.buys){el('circle',{cx:X(idx(b.d)),cy:Y(b.p),r:5,fill:`var(--${b.c})`,stroke:'var(--ring)','stroke-width':2},svg);}
 const show=ev=>{const r=svg.getBoundingClientRect();const x=(ev.touches?ev.touches[0].clientX:ev.clientX)-r.left;
  const i=Math.max(0,Math.min(n-1,Math.round((x-m.l)/((W-m.l-m.r)/Math.max(n-1,1)))));
  cross.setAttribute('x1',X(i));cross.setAttribute('x2',X(i));cross.setAttribute('visibility','visible');
  const day=d.dates[i],hit=d.buys.filter(b=>idx(b.d)===i);
  tip.innerHTML=`<b>${day}</b><br>종가 ${d.close[i]!=null?fmt(d.close[i])+'원':'-'}`+hit.map(b=>
   `<br><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--${b.c});margin-right:4px"></i>${b.who} 매수 ${fmt(b.q)}주 @${fmt(b.p)}`).join('');
  tip.style.display='block';const tw=tip.offsetWidth;let left=X(i)+10;if(left+tw>W)left=X(i)-tw-10;
  tip.style.left=Math.max(0,left)+'px';tip.style.top='4px';};
 const hide=()=>{tip.style.display='none';cross.setAttribute('visibility','hidden');};
 svg.addEventListener('pointermove',show);svg.addEventListener('pointerdown',show);svg.addEventListener('pointerleave',hide);
}
const all=()=>document.querySelectorAll('.viz[data-chart]').forEach(draw);
all();let t;addEventListener('resize',()=>{clearTimeout(t);t=setTimeout(all,150);});
})();
"""

SLOTS = ["s1", "s2", "s3"]


def price_chart(dates, closes, buys, avg=None, title="", target=None):
    """dates/closes: 같은 길이 목록. buys: [(변동일 YYYY-MM-DD, 단가, 제출인, 수량)].
    매수금액이 큰 제출인 3곳까지 색을 주고 나머지는 '기타'로 묶음. (HTML 조각 반환)"""
    weight = {}
    for d, p, who, q in buys:
        weight[who] = weight.get(who, 0) + p * q
    top = [w for w, _ in sorted(weight.items(), key=lambda x: -x[1])[:3]]
    color = {w: SLOTS[i] for i, w in enumerate(top)}
    data = dict(dates=dates, close=closes, avg=avg, title=title, target=target,
                buys=[dict(d=d, p=p, who=who, q=q, c=color.get(who, "sx")) for d, p, who, q in buys])
    legend = ['<span><i class="ln" style="background:var(--price)"></i>종가</span>']
    if avg:
        legend.append('<span><i class="dash"></i>기관 평균매수단가</span>')
    if target:
        legend.append('<span><i class="dot"></i>증권사 평균 목표가</span>')
    legend += [f'<span><i style="background:var(--{color[w]})"></i>{escape(w)}</span>' for w in top]
    if len(weight) > 3:
        legend.append('<span><i style="background:var(--sx)"></i>기타 제출인</span>')
    payload = escape(json.dumps(data, ensure_ascii=False), quote=True)
    return (f'<div class="legend">{"".join(legend)}</div>'
            f'<div class="viz" data-chart="{payload}"></div>')


def bar_list(items, fmt):
    """items: [(이름, 링크, 값)] 값의 크기로 막대 길이. fmt(값) → 오른쪽 숫자 글자."""
    if not items:
        return ""
    peak = max(abs(v) for _, _, v in items) or 1
    rows = "".join(
        f'<a href="{escape(href)}" title="{escape(name)}">{escape(name)}</a>'
        f'<div class="track"><div class="fill" style="width:{abs(v) / peak * 100:.1f}%"></div></div>'
        f'<span class="v">{escape(fmt(v))}</span>' for name, href, v in items)
    return f'<div class="bars viz">{rows}</div>'


def sparkline(closes, avg=None):
    """카드용 작은 추세선 (최근 종가들) + 기관 평단 점선. 축·글자 없음."""
    vals = [v for v in closes if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals + ([avg] if avg else [])), max(vals + ([avg] if avg else []))
    span = (hi - lo) or 1
    W, H, n = 300, 38, len(closes)
    y = lambda v: 3 + (1 - (v - lo) / span) * (H - 6)   # noqa: E731
    pts = " ".join(f"{i * W / (n - 1):.1f},{y(v):.1f}" for i, v in enumerate(closes) if v is not None)
    avg_line = (f'<line x1="0" x2="{W}" y1="{y(avg):.1f}" y2="{y(avg):.1f}" stroke="var(--fg)" '
                f'stroke-width="1.2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>') if avg else ""
    return (f'<svg class="spark viz" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true">{avg_line}'
            f'<polyline points="{pts}" fill="none" stroke="var(--price)" stroke-width="1.8" '
            f'vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>')
