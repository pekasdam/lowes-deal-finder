from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEALS = DATA / "deals.json"
HISTORY = DATA / "history.json"

SOURCES = [
    ("Daily Deals", "https://www.lowes.com/l/savings/daily-deals", 100, True),
    ("Savings", "https://www.lowes.com/l/savings", 75, True),
    ("Back Aisle / Clearance", "https://www.lowes.com/pl/The-back-aisle/2021454685607?refinement=2", 95, False),
]

PRICE = re.compile(r"\$\s*([0-9]{1,5}(?:,[0-9]{3})*(?:\.\d{2})?)")
ITEM = re.compile(r"/pd/[^/?#]*/?(\d{5,12})(?:[/?#]|$)", re.I)
PCT = re.compile(r"(?:save|off)\s*([0-9]{1,2})\s*%", re.I)
PRODUCT_PATH = re.compile(r"/pd/[^\"'<>?#\\\s]+(?:/\d{5,12})(?=[\"'<>?#&\\\s]|$)", re.I)
DEAL_WORDS = ("featured deal", "clearance", "special value", "instant savings", "save ", "deal")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def now():
    return datetime.now(timezone.utc)


def iso(d=None):
    return (d or now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))


def clean(href):
    href = (href or "").replace("\\/", "/").replace("\\u002F", "/")
    u = urlsplit(urljoin("https://www.lowes.com", href))
    return urlunsplit(("https", "www.lowes.com", u.path.rstrip("/"), "", ""))


def pid(url):
    match = ITEM.search(url)
    return match.group(1) if match else hashlib.sha1(url.encode()).hexdigest()[:14]


def title_from_url(url):
    try:
        chunk = urlsplit(url).path.split("/pd/", 1)[1].rsplit("/", 1)[0]
        title = unquote(chunk).replace("-", " ").replace("_", " ")
        return re.sub(r"\s+", " ", title).strip()[:180] or "Lowe's product"
    except Exception:
        return "Lowe's product"


def prices(text):
    vals = []
    for raw in PRICE.findall(text or ""):
        try:
            value = float(raw.replace(",", ""))
        except Exception:
            continue
        if 0.5 <= value <= 50000 and all(abs(value - old) > 0.01 for old in vals):
            vals.append(value)
    explicit_match = PCT.search(text or "")
    explicit = float(explicit_match.group(1)) if explicit_match else None
    if len(vals) >= 2:
        lo, hi = min(vals), max(vals)
        if hi > lo and hi / lo <= 10:
            pct = round((hi - lo) / hi * 100, 1)
            if 3 <= pct <= 95:
                return round(lo, 2), round(hi, 2), pct
    return (round(vals[0], 2) if vals else None), None, explicit


def category(text):
    t = (text or "").lower()
    groups = [
        ("Tools", ("dewalt", "kobalt", "craftsman", "drill", "saw", "impact", "tool", "battery", "charger", "compressor")),
        ("Appliances", ("refrigerator", "washer", "dryer", "dishwasher", "range", "microwave", "freezer", "oven")),
        ("Outdoor", ("mower", "trimmer", "blower", "chainsaw", "grill", "patio", "shed", "pressure washer")),
        ("Building", ("lumber", "concrete", "shingle", "roof", "drywall", "fence", "door", "window", "insulation")),
        ("Electrical", ("breaker", "wire", "outlet", "switch", "generator", "extension cord")),
        ("Plumbing", ("faucet", "toilet", "sink", "water heater", "pipe", "valve", "shower")),
        ("Flooring", ("flooring", "vinyl plank", "laminate", "tile", "carpet", "hardwood")),
        ("Paint", ("paint", "primer", "stain", "caulk", "sealant")),
        ("Home", ("lighting", "fan", "storage", "shelf", "cabinet", "vanity", "furniture", "decor")),
    ]
    for name, words in groups:
        if any(word in t for word in words):
            return name
    return "Other"


def score(discount, priority, status, has_price):
    return (
        priority
        + (min(90, int(discount * 1.5)) if discount is not None else 0)
        + (35 if status == "PRICE DROP" else 25 if status == "NEW" else 0)
        + (8 if has_price else 0)
    )


def fallback_rows(raw_html, source, source_url, priority, limit=120):
    if not raw_html:
        return []
    blob = html_lib.unescape(raw_html).replace("\\u002F", "/").replace("\\/", "/")
    rows = []
    seen = set()
    for match in PRODUCT_PATH.finditer(blob):
        product_url = clean(match.group(0))
        if product_url in seen or not ITEM.search(product_url):
            continue
        seen.add(product_url)
        start = max(0, match.start() - 700)
        end = min(len(blob), match.end() + 1100)
        context = re.sub(r"<[^>]+>", " ", blob[start:end])
        context = re.sub(r"\s+", " ", context).strip()[:1800]
        rows.append(
            {
                "href": product_url,
                "title": title_from_url(product_url),
                "text": context,
                "image": None,
                "source": source,
                "source_url": source_url,
                "priority": priority,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def http_html(url):
    try:
        request = Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            },
        )
        with urlopen(request, timeout=30) as response:
            return response.read(8_000_000).decode("utf-8", "ignore")
    except Exception:
        return ""


async def visit(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        for label in ("Accept All", "Accept", "I Agree", "Close", "No Thanks"):
            try:
                button = page.get_by_role("button", name=re.compile("^" + re.escape(label) + "$", re.I))
                if await button.count():
                    await button.first.click(timeout=1000)
            except Exception:
                pass
        # Lowe's product grids are often lazy-loaded. A few short scrolls are more reliable
        # than waiting only for a selector at the top of the page.
        for _ in range(5):
            try:
                await page.evaluate("window.scrollBy(0, Math.max(900, innerHeight * .9))")
                await page.wait_for_timeout(650)
            except Exception:
                break
        try:
            await page.wait_for_selector('a[href*="/pd/"]', timeout=6000)
        except Exception:
            pass
        return True
    except Exception:
        return False


async def extract(page, source, source_url, priority):
    js = r'''()=>{const out=[],seen=new Set();for(const a of document.querySelectorAll('a[href]')){if(out.length>=120)break;const raw=a.getAttribute('href')||'';const href=a.href||raw;if(!raw.includes('/pd/')&&!href.includes('/pd/'))continue;if(!href||seen.has(href))continue;let n=a,b=a;for(let i=0;i<7&&n;i++,n=n.parentElement){const t=(n.innerText||'').trim();if(t.length>=15&&t.length<=2200)b=n;if(t.includes('$')||/featured deal|clearance|special value|instant savings|save /i.test(t)){b=n;break}}const text=(b.innerText||a.innerText||'').replace(/\s+/g,' ').trim();let title=(a.innerText||a.getAttribute('aria-label')||a.getAttribute('title')||'').replace(/\s+/g,' ').trim();const im=b.querySelector('img');seen.add(href);out.push({href,title,text,image:im?(im.currentSrc||im.src||im.getAttribute('data-src')):null})}return out}'''
    try:
        rows = await page.evaluate(js)
    except Exception:
        rows = []
    output = []
    for row in rows:
        product_url = clean(row.get("href", ""))
        if not product_url.startswith("https://www.lowes.com/pd/") or not ITEM.search(product_url):
            continue
        if len((row.get("title") or "").strip()) < 5:
            row["title"] = title_from_url(product_url)
        output.append(dict(row, href=product_url, source=source, source_url=source_url, priority=priority))
    if output:
        return output, "dom"

    # Fallback 1: the page may contain server-rendered /pd/ URLs even when Lowe's
    # client-side code never hydrates the product-card anchors for a headless browser.
    try:
        raw_html = await page.content()
    except Exception:
        raw_html = ""
    output = fallback_rows(raw_html, source, source_url, priority)
    if output:
        return output, "page-html"

    # Fallback 2: make a normal document request. This catches Lowe's variants that
    # return useful product markup to a document request but not to the browser session.
    raw_html = await asyncio.to_thread(http_html, source_url)
    output = fallback_rows(raw_html, source, source_url, priority)
    if output:
        return output, "http-html"
    return [], "none"


async def discover(page):
    try:
        links = await page.eval_on_selector_all(
            "a[href]",
            'els=>els.map(a=>({href:a.href||a.getAttribute("href")||"",text:(a.innerText||a.textContent||"").trim()}))',
        )
    except Exception:
        return []
    out = []
    for entry in links:
        href = entry.get("href", "")
        text = entry.get("text", "").lower()
        if href.startswith("https://www.lowes.com/") and "/pl/" in href and (
            "deal" in href.lower() or text in {"shop now", "view all", "shop deals"} or "save" in text
        ):
            parts = urlsplit(href)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
            if url not in out:
                out.append(url)
    return out[:12]


async def main():
    hist = read(HISTORY, {"products": {}}).get("products", {})
    prev = read(DEALS, {"deals": []})
    previous = {str(d.get("id")): d for d in prev.get("deals", []) if d.get("id")}
    found = {}
    errors = []
    collections = []
    diagnostics = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-US", user_agent=UA)
        page = await context.new_page()

        for name, url, priority, should_discover in SOURCES:
            if not await visit(page, url):
                errors.append("Could not load " + name)
                diagnostics.append({"source": name, "method": "load-failed", "candidates": 0})
                continue
            rows, method = await extract(page, name, url, priority)
            diagnostics.append({"source": name, "method": method, "candidates": len(rows)})
            for row in rows:
                product_url = clean(row["href"])
                item_id = pid(product_url)
                row["url"] = product_url
                if item_id not in found or priority > found[item_id]["priority"]:
                    found[item_id] = row
            if should_discover:
                for discovered_url in await discover(page):
                    if discovered_url not in [x[1] for x in collections]:
                        collections.append((name + " collection", discovered_url, max(50, priority - 5)))

        for name, url, priority in collections[:12]:
            if not await visit(page, url):
                continue
            rows, method = await extract(page, name, url, priority)
            diagnostics.append({"source": name, "method": method, "candidates": len(rows)})
            for row in rows:
                product_url = clean(row["href"])
                item_id = pid(product_url)
                row["url"] = product_url
                if item_id not in found or priority > found[item_id]["priority"]:
                    found[item_id] = row
        await browser.close()

    n = now()
    out = []
    for item_id, row in found.items():
        cur, original, discount = prices(row.get("text", ""))
        h = hist.get(item_id, {}) if isinstance(hist.get(item_id), dict) else {}
        last_price = h.get("last_price")
        first_seen = h.get("first_seen") or iso(n)
        status = "SEEN BEFORE" if h.get("last_seen") else "NEW"
        if cur is not None and isinstance(last_price, (int, float)) and cur < float(last_price) - 0.01:
            status = "PRICE DROP"
        if cur is not None and original is None and isinstance(last_price, (int, float)) and last_price > cur:
            original = round(float(last_price), 2)
            discount = round((original - cur) / original * 100, 1)

        source_lower = row["source"].lower()
        trusted_deal_source = "daily deals" in source_lower or "clearance" in source_lower or "back aisle" in source_lower
        keep = (
            (discount is not None and discount >= 15)
            or status == "PRICE DROP"
            or any(word in row.get("text", "").lower() for word in DEAL_WORDS)
            or trusted_deal_source
        )
        if not keep:
            continue

        if cur is not None:
            h["last_price"] = cur
            h["lowest_price"] = min(cur, float(h.get("lowest_price", cur)))
        h.update({"title": row["title"], "url": row["url"], "first_seen": first_seen, "last_seen": iso(n)})
        hist[item_id] = h
        out.append(
            {
                "id": item_id,
                "title": row["title"],
                "url": row["url"],
                "image": row.get("image"),
                "category": category(row["title"]),
                "source": row["source"],
                "source_url": row["source_url"],
                "current_price": cur,
                "original_price": original,
                "discount_pct": discount,
                "status": status,
                "first_seen": first_seen,
                "last_seen": iso(n),
                "score": score(discount, row["priority"], status, cur is not None),
            }
        )

    cutoff = n - timedelta(hours=48)
    ids = {d["id"] for d in out}
    for item_id, deal in previous.items():
        if item_id in ids:
            continue
        try:
            last = datetime.fromisoformat(deal.get("last_seen", "").replace("Z", "+00:00"))
        except Exception:
            continue
        if last >= cutoff:
            old = dict(deal)
            old["status"] = "UNVERIFIED"
            old["score"] = max(0, int(old.get("score", 0)) - 40)
            out.append(old)

    best = {}
    for deal in out:
        if deal["id"] not in best or deal["score"] > best[deal["id"]]["score"]:
            best[deal["id"]] = deal
    out = sorted(best.values(), key=lambda d: (d.get("score", 0), d.get("discount_pct") or 0), reverse=True)
    stats = {
        "total": len(out),
        "new": sum(d["status"] == "NEW" for d in out),
        "price_drops": sum(d["status"] == "PRICE DROP" for d in out),
        "with_price": sum(d["current_price"] is not None for d in out),
        "sources_scanned": len(diagnostics),
        "raw_candidates": len(found),
    }
    if not out and not errors:
        errors.append("Lowe's pages loaded but no product links were extracted. The next hourly scan will retry automatically.")
    write(DEALS, {"generated_at": iso(n), "app": "Lowe's Deal Finder", "stats": stats, "scan_errors": errors, "diagnostics": diagnostics, "deals": out})
    write(HISTORY, {"updated_at": iso(n), "products": hist})
    print(json.dumps({"stats": stats, "diagnostics": diagnostics, "errors": errors}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
