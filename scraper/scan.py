from __future__ import annotations
import asyncio, hashlib, json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'; DEALS=DATA/'deals.json'; HISTORY=DATA/'history.json'
SOURCES=[
 ('Daily Deals','https://www.lowes.com/l/savings/daily-deals',100,True),
 ('Savings','https://www.lowes.com/l/savings',75,True),
 ('Back Aisle / Clearance','https://www.lowes.com/pl/The-back-aisle/2021454685607?refinement=2',95,False),
]
PRICE=re.compile(r'\$\s*([0-9]{1,5}(?:,[0-9]{3})*(?:\.\d{2})?)'); ITEM=re.compile(r'/pd/[^/?#]*/?(\d{5,12})(?:[/?#]|$)',re.I); PCT=re.compile(r'(?:save|off)\s*([0-9]{1,2})\s*%',re.I)
DEAL_WORDS=('featured deal','clearance','special value','instant savings','save ','deal')
def now(): return datetime.now(timezone.utc)
def iso(d=None): return (d or now()).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p,default):
 try:return json.loads(p.read_text())
 except:return default
def write(p,x): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,ensure_ascii=False))
def clean(href):
 u=urlsplit(urljoin('https://www.lowes.com',href));return urlunsplit(('https','www.lowes.com',u.path.rstrip('/'),'',''))
def pid(url):
 m=ITEM.search(url);return m.group(1) if m else hashlib.sha1(url.encode()).hexdigest()[:14]
def prices(text):
 vals=[]
 for x in PRICE.findall(text or ''):
  try:v=float(x.replace(',',''))
  except:continue
  if .5<=v<=50000 and all(abs(v-z)>.01 for z in vals):vals.append(v)
 ep=PCT.search(text or ''); explicit=float(ep.group(1)) if ep else None
 if len(vals)>=2:
  lo,hi=min(vals),max(vals)
  if hi>lo and hi/lo<=10:
   pct=round((hi-lo)/hi*100,1)
   if 3<=pct<=95:return round(lo,2),round(hi,2),pct
 return (round(vals[0],2) if vals else None),None,explicit
def category(t):
 t=(t or '').lower(); groups=[('Tools',('dewalt','kobalt','craftsman','drill','saw','impact','tool','battery','charger','compressor')),('Appliances',('refrigerator','washer','dryer','dishwasher','range','microwave','freezer','oven')),('Outdoor',('mower','trimmer','blower','chainsaw','grill','patio','shed','pressure washer')),('Building',('lumber','concrete','shingle','roof','drywall','fence','door','window','insulation')),('Electrical',('breaker','wire','outlet','switch','generator','extension cord')),('Plumbing',('faucet','toilet','sink','water heater','pipe','valve','shower')),('Flooring',('flooring','vinyl plank','laminate','tile','carpet','hardwood')),('Paint',('paint','primer','stain','caulk','sealant')),('Home',('lighting','fan','storage','shelf','cabinet','vanity','furniture','decor'))]
 for n,ws in groups:
  if any(w in t for w in ws):return n
 return 'Other'
def score(discount,priority,status,hasprice): return priority+(min(90,int(discount*1.5)) if discount is not None else 0)+(35 if status=='PRICE DROP' else 25 if status=='NEW' else 0)+(8 if hasprice else 0)
async def visit(page,url):
 try:
  await page.goto(url,wait_until='domcontentloaded',timeout=45000)
  for label in ('Accept All','Accept','I Agree','Close','No Thanks'):
   try:
    b=page.get_by_role('button',name=re.compile('^'+re.escape(label)+'$',re.I))
    if await b.count(): await b.first.click(timeout=1000)
   except:pass
  try:await page.wait_for_selector('a[href*="/pd/"]',timeout=12000)
  except:pass
  return True
 except (PlaywrightTimeoutError,Exception):return False
async def extract(page,source,url,priority):
 js=r'''()=>{const out=[],seen=new Set();for(const a of document.querySelectorAll('a[href*="/pd/"]')){if(out.length>=100)break;if(!a.href||seen.has(a.href))continue;let n=a,b=a;for(let i=0;i<6&&n;i++,n=n.parentElement){const t=(n.innerText||'').trim();if(t.length>=20&&t.length<=1800)b=n;if(t.includes('$')||/featured deal|clearance|special value|instant savings|save /i.test(t)){b=n;break}}const text=(b.innerText||a.innerText||'').replace(/\s+/g,' ').trim();const title=(a.innerText||a.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();const im=b.querySelector('img');if(title.length<5)continue;seen.add(a.href);out.push({href:a.href,title,text,image:im?(im.currentSrc||im.src||im.getAttribute('data-src')):null})}return out}'''
 try:rows=await page.evaluate(js)
 except:return []
 return [dict(r,source=source,source_url=url,priority=priority) for r in rows if clean(r.get('href','')).startswith('https://www.lowes.com/pd/')]
async def discover(page):
 try:links=await page.eval_on_selector_all('a[href]','els=>els.map(a=>({href:a.href,text:(a.innerText||a.textContent||"").trim()}))')
 except:return []
 out=[]
 for x in links:
  h=x.get('href','');t=x.get('text','').lower()
  if h.startswith('https://www.lowes.com/') and '/pl/' in h and ('deal' in h.lower() or t in {'shop now','view all','shop deals'} or 'save' in t):
   u=urlunsplit((*urlsplit(h)[:3],urlsplit(h).query,''))
   if u not in out:out.append(u)
 return out[:12]
async def main():
 hist=read(HISTORY,{'products':{}}).get('products',{}); prev=read(DEALS,{'deals':[]}); previous={str(d.get('id')):d for d in prev.get('deals',[]) if d.get('id')}; found={}; errors=[]; collections=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--disable-dev-shm-usage','--no-sandbox']); ctx=await browser.new_context(viewport={'width':1440,'height':1000},locale='en-US',user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36');page=await ctx.new_page()
  for name,url,priority,disc in SOURCES:
   if not await visit(page,url):errors.append('Could not load '+name);continue
   for r in await extract(page,name,url,priority):
    u=clean(r['href']);i=pid(u);r['url']=u
    if i not in found or priority>found[i]['priority']:found[i]=r
   if disc:
    for u in await discover(page):
     if u not in [x[1] for x in collections]:collections.append((name+' collection',u,max(50,priority-5)))
   await page.wait_for_timeout(600)
  for name,url,priority in collections[:12]:
   if await visit(page,url):
    for r in await extract(page,name,url,priority):
     u=clean(r['href']);i=pid(u);r['url']=u
     if i not in found or priority>found[i]['priority']:found[i]=r
   await page.wait_for_timeout(500)
  await browser.close()
 n=now(); out=[]
 for i,r in found.items():
  cur,orig,disc=prices(r.get('text','')); h=hist.get(i,{}) if isinstance(hist.get(i),dict) else {}; last=h.get('last_price'); first=h.get('first_seen') or iso(n); status='SEEN BEFORE' if h.get('last_seen') else 'NEW'
  if cur is not None and isinstance(last,(int,float)) and cur<float(last)-.01:status='PRICE DROP'
  if cur is not None and orig is None and isinstance(last,(int,float)) and last>cur:orig=round(float(last),2);disc=round((orig-cur)/orig*100,1)
  keep=(disc is not None and disc>=15) or status=='PRICE DROP' or any(w in r.get('text','').lower() for w in DEAL_WORDS) or 'daily deals' in r['source'].lower() or 'clearance' in r['source'].lower() or 'back aisle' in r['source'].lower()
  if not keep:continue
  if cur is not None:h['last_price']=cur;h['lowest_price']=min(cur,float(h.get('lowest_price',cur)))
  h.update({'title':r['title'],'url':r['url'],'first_seen':first,'last_seen':iso(n)});hist[i]=h
  out.append({'id':i,'title':r['title'],'url':r['url'],'image':r.get('image'),'category':category(r['title']),'source':r['source'],'source_url':r['source_url'],'current_price':cur,'original_price':orig,'discount_pct':disc,'status':status,'first_seen':first,'last_seen':iso(n),'score':score(disc,r['priority'],status,cur is not None)})
 cutoff=n-timedelta(hours=48);ids={d['id'] for d in out}
 for i,d in previous.items():
  if i in ids:continue
  try:last=datetime.fromisoformat(d.get('last_seen','').replace('Z','+00:00'))
  except:continue
  if last>=cutoff:x=dict(d);x['status']='UNVERIFIED';x['score']=max(0,int(x.get('score',0))-40);out.append(x)
 best={}
 for d in out:
  if d['id'] not in best or d['score']>best[d['id']]['score']:best[d['id']]=d
 out=sorted(best.values(),key=lambda d:(d.get('score',0),d.get('discount_pct') or 0),reverse=True)
 stats={'total':len(out),'new':sum(d['status']=='NEW' for d in out),'price_drops':sum(d['status']=='PRICE DROP' for d in out),'with_price':sum(d['current_price'] is not None for d in out),'sources_scanned':len(SOURCES)+min(12,len(collections))}
 write(DEALS,{'generated_at':iso(n),'app':"Lowe's Deal Finder",'stats':stats,'scan_errors':errors,'deals':out});write(HISTORY,{'updated_at':iso(n),'products':hist});print(json.dumps(stats,indent=2))
if __name__=='__main__':asyncio.run(main())
