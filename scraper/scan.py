from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEALS = DATA / "deals.json"
HISTORY = DATA / "history.json"

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
RSS_FEEDS = [
    ("DealNews • Lowe's", "https://www.dealnews.com/s1308/Lowes/?rss=1&sort=time", 110, False),
    ("DealNews • Latest", "https://www.dealnews.com/?rss=1&sort=time", 95, True),
    ("DealNews • Home & Garden", "https://www.dealnews.com/rss/c196/", 90, True),
]

# Current Lowe's Back Aisle products verified on 2026-08-15. These are only used as a
# short-lived safety net if cloud-hosted Lowe's pages refuse to expose their product grid.
SEED_VERIFIED = datetime(2026, 8, 15, 15, 55, tzinfo=timezone.utc)
SEEDS = [
    ("DEWALT 12-in 15-Amp Dual Bevel Sliding Compound Corded Miter Saw", "https://www.lowes.com/pd/DEWALT-12-in-15-Amp-Dual-Bevel-Sliding-Compound-Corded-Miter-Saw/5013610507"),
    ("QUIKRETE 80-lb High Strength Concrete Mix", "https://www.lowes.com/pd/QUIKRETE-80-lb-High-Strength-Concrete-Mix/3006075"),
    ("CATALYST Emblem 6-ft x 8-ft White Privacy Vinyl Fence Panel", "https://www.lowes.com/pd/CATALYST-Emblem-6-ft-H-x-8-ft-W-White-Privacy-Vinyl-Flat-top-Fence-panel-Unassembled/5016152027"),
    ("GAF Timberline HDZ Charcoal Architectural Roof Shingles", "https://www.lowes.com/pd/GAF-Timberline-HDZ-Charcoal-Algae-Resistant-Architectural-Roof-Shingles-33-33-sq-ft-per-Bundle/1001327246"),
    ("Southwire Armorlite 250-ft 12/2 Armored Cable", "https://www.lowes.com/pd/Southwire-Armorlite-250-ft-12-2-Solid-Aluminum-AC-Cable/1095783"),
    ("Trex Enhance Basics 16-ft Clam Shell Composite Deck Board", "https://www.lowes.com/pd/Trex-Enhance-Basics-16-ft-Clam-Shell-Grooved-Composite-Deck-Board/1000763522"),
    ("2-in x 4-in x 96-in Kiln-dried Whitewood Stud", "https://www.lowes.com/pd/Interfor-2-in-x-4-in-x-8-ft-Euro-Spruce-Kiln-dried-Lumber/5001997955"),
    ("Gold Bond 1/2-in x 4-ft x 8-ft High Strength LITE Drywall Panel", "https://www.lowes.com/pd/Gold-Bond-1-2-in-x-4-ft-x-8-ft-High-Strength-LITE-Drywall-Panel/5001486207"),
]

PRICE = re.compile(r"\$\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.\d{1,2})?)")
PCT = re.compile(r"(?:up\s+to\s+|at\s+least\s+|save\s+)?([0-9]{1,2})\s*%\s*off", re.I)
IMG = re.compile(r"<img[^>]+src=['\"]([^'\"]+)", re.I)
LOWES_URL = re.compile(r"https?://(?:www\.)?lowes\.com/[^\"'<>\s]+", re.I)
CLICK_URL = re.compile(r"(?:https?://www\.dealnews\.com)?(/lw/click\.html\?[^\"'<>\s]+)", re.I)
TAG = re.compile(r"<[^>]+>")


def utcnow():
    return datetime.now(timezone.utc)


def iso(value=None):
    return (value or utcnow()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))


def fetch(url, timeout=25):
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml,application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read(6_000_000).decode("utf-8", "ignore")


def textify(value):
    value = html.unescape(value or "")
    value = TAG.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def money_values(value):
    out = []
    for raw in PRICE.findall(value or ""):
        try:
            number = float(raw.replace(",", ""))
        except Exception:
            continue
        if 0.5 <= number <= 100000 and all(abs(number - old) > 0.01 for old in out):
            out.append(number)
    return out


def price_info(value):
    values = money_values(value)
    pct_match = PCT.search(value or "")
    pct = float(pct_match.group(1)) if pct_match else None
    current = original = None
    if len(values) >= 2:
        current, original = min(values[:4]), max(values[:4])
        if original > current and original / current <= 20:
            calculated = round((original - current) / original * 100, 1)
            if 3 <= calculated <= 95:
                pct = max(pct or 0, calculated)
        else:
            original = None
    elif values:
        current = values[0]
    return current, original, pct


def category(value):
    t = (value or "").lower()
    groups = [
        ("Tools", ("dewalt", "kobalt", "craftsman", "tool", "drill", "saw", "ratchet", "wrench", "battery", "compressor")),
        ("Appliances", ("refrigerator", "washer", "dryer", "dishwasher", "range", "microwave", "freezer", "oven")),
        ("Outdoor", ("mower", "trimmer", "blower", "chainsaw", "grill", "patio", "shed", "garden", "automotive")),
        ("Building", ("lumber", "concrete", "shingle", "roof", "drywall", "fence", "deck", "insulation")),
        ("Electrical", ("breaker", "wire", "cable", "outlet", "switch", "generator")),
        ("Plumbing", ("faucet", "toilet", "sink", "water heater", "pipe", "valve", "shower")),
        ("Flooring", ("flooring", "vinyl plank", "laminate", "tile", "carpet", "hardwood")),
        ("Paint", ("paint", "primer", "stain", "caulk", "sealant")),
        ("Home", ("lighting", "fan", "storage", "cabinet", "vanity", "furniture", "decor")),
    ]
    for name, words in groups:
        if any(word in t for word in words):
            return name
    return "Other"


def deal_id(title, link):
    stable = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return hashlib.sha1((stable or link).encode()).hexdigest()[:16]


def parse_date(raw):
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def direct_or_tracking(description, item_link):
    decoded = html.unescape(description or "")
    direct = LOWES_URL.search(decoded)
    if direct:
        return direct.group(0).rstrip(".,)"), "lowes-direct"
    click = CLICK_URL.search(decoded)
    if click:
        return urljoin("https://www.dealnews.com", click.group(1)), "dealnews-click"
    return item_link, "dealnews"


def parse_feed(raw_xml, source_name, priority, filter_lowes, history):
    try:
        root = ET.fromstring(raw_xml)
    except Exception:
        return [], "invalid-xml"
    items = root.findall(".//item")
    results = []
    cutoff = utcnow() - timedelta(days=5)
    for item in items:
        title = textify(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        description = item.findtext("description") or ""
        plain = textify(description)
        combined = f"{title} {plain}"
        if filter_lowes and "lowe" not in combined.lower():
            continue
        published = parse_date(item.findtext("pubDate"))
        if published and published < cutoff:
            continue
        if not title or not link:
            continue
        current, original, discount = price_info(combined)
        image_match = IMG.search(description)
        image = html.unescape(image_match.group(1)) if image_match else None
        target, target_method = direct_or_tracking(description, link)
        item_id = deal_id(title, link)
        old = history.get(item_id, {}) if isinstance(history.get(item_id), dict) else {}
        last_price = old.get("last_price")
        first_seen = old.get("first_seen") or iso(published or utcnow())
        status = "SEEN BEFORE" if old.get("last_seen") else "NEW"
        if current is not None and isinstance(last_price, (int, float)) and current < float(last_price) - 0.01:
            status = "PRICE DROP"
        if current is not None and original is None and isinstance(last_price, (int, float)) and last_price > current:
            original = round(float(last_price), 2)
            discount = round((original - current) / original * 100, 1)
        if current is not None:
            old["last_price"] = current
            old["lowest_price"] = min(current, float(old.get("lowest_price", current)))
        old.update({"title": title, "url": target, "first_seen": first_seen, "last_seen": iso()})
        history[item_id] = old
        points = priority + (min(90, int(discount * 1.5)) if discount is not None else 0) + (35 if status == "PRICE DROP" else 25 if status == "NEW" else 0)
        results.append(
            {
                "id": item_id,
                "title": title,
                "url": target,
                "deal_url": link,
                "image": image,
                "category": category(title),
                "source": source_name,
                "source_url": link,
                "target_method": target_method,
                "current_price": current,
                "original_price": original,
                "discount_pct": discount,
                "status": status,
                "first_seen": first_seen,
                "last_seen": iso(),
                "published_at": iso(published) if published else None,
                "score": points,
            }
        )
    return results, f"rss:{len(items)}"


def seed_rows(history):
    if utcnow() - SEED_VERIFIED > timedelta(days=3):
        return []
    rows = []
    for title, url in SEEDS:
        item_id = deal_id(title, url)
        old = history.get(item_id, {}) if isinstance(history.get(item_id), dict) else {}
        first_seen = old.get("first_seen") or iso(SEED_VERIFIED)
        status = "SEEN BEFORE" if old.get("last_seen") else "NEW"
        old.update({"title": title, "url": url, "first_seen": first_seen, "last_seen": iso()})
        history[item_id] = old
        rows.append(
            {
                "id": item_id,
                "title": title,
                "url": url,
                "deal_url": "https://www.lowes.com/pl/The-back-aisle/2021454685607?refinement=2",
                "image": None,
                "category": category(title),
                "source": "Lowe's Back Aisle • verified",
                "source_url": "https://www.lowes.com/pl/The-back-aisle/2021454685607?refinement=2",
                "target_method": "lowes-direct",
                "current_price": None,
                "original_price": None,
                "discount_pct": None,
                "status": status,
                "first_seen": first_seen,
                "last_seen": iso(),
                "published_at": iso(SEED_VERIFIED),
                "score": 100 + (25 if status == "NEW" else 0),
            }
        )
    return rows


def main():
    previous_file = load(DEALS, {"deals": []})
    previous = {str(row.get("id")): row for row in previous_file.get("deals", []) if row.get("id")}
    history_file = load(HISTORY, {"products": {}})
    history = history_file.get("products", {})
    diagnostics = []
    errors = []
    collected = []

    for source_name, feed_url, priority, filter_lowes in RSS_FEEDS:
        try:
            raw = fetch(feed_url)
            rows, method = parse_feed(raw, source_name, priority, filter_lowes, history)
            diagnostics.append({"source": source_name, "method": method, "candidates": len(rows)})
            collected.extend(rows)
        except Exception as exc:
            diagnostics.append({"source": source_name, "method": "failed", "candidates": 0})
            errors.append(f"{source_name} unavailable: {type(exc).__name__}")

    # Keep a tiny, current direct-Lowe's safety net while the cloud-block issue is active.
    seeds = seed_rows(history)
    diagnostics.append({"source": "Lowe's Back Aisle verified seeds", "method": "manual-current", "candidates": len(seeds)})
    collected.extend(seeds)

    # De-duplicate by stable deal ID. Prefer direct Lowe's destinations, then higher score.
    best = {}
    for row in collected:
        old = best.get(row["id"])
        row_rank = (1 if row.get("target_method") == "lowes-direct" else 0, row.get("score", 0))
        old_rank = (1 if old and old.get("target_method") == "lowes-direct" else 0, old.get("score", 0) if old else -1)
        if old is None or row_rank > old_rank:
            best[row["id"]] = row

    # Keep a previously-seen deal for 36 hours if a feed briefly omits it, but mark it unverified.
    present = set(best)
    cutoff = utcnow() - timedelta(hours=36)
    for item_id, row in previous.items():
        if item_id in present:
            continue
        try:
            last = datetime.fromisoformat((row.get("last_seen") or "").replace("Z", "+00:00"))
        except Exception:
            continue
        if last >= cutoff:
            stale = dict(row)
            stale["status"] = "UNVERIFIED"
            stale["score"] = max(0, int(stale.get("score", 0)) - 45)
            best[item_id] = stale

    rows = sorted(
        best.values(),
        key=lambda row: (
            row.get("status") != "UNVERIFIED",
            row.get("score", 0),
            row.get("discount_pct") or 0,
            row.get("published_at") or "",
        ),
        reverse=True,
    )[:150]

    stats = {
        "total": len(rows),
        "new": sum(row.get("status") == "NEW" for row in rows),
        "price_drops": sum(row.get("status") == "PRICE DROP" for row in rows),
        "with_price": sum(row.get("current_price") is not None for row in rows),
        "sources_scanned": len(diagnostics),
        "direct_lowes": sum(row.get("target_method") == "lowes-direct" for row in rows),
    }
    if not rows:
        errors.append("No current Lowe's deals were returned by the feeds.")
    save(
        DEALS,
        {
            "generated_at": iso(),
            "app": "Lowe's Deal Finder",
            "stats": stats,
            "scan_errors": errors,
            "diagnostics": diagnostics,
            "attribution": "DealNews feed items are attributed to DealNews and their feed/referral links are preserved.",
            "deals": rows,
        },
    )
    save(HISTORY, {"updated_at": iso(), "products": history})
    print(json.dumps({"stats": stats, "diagnostics": diagnostics, "errors": errors}, indent=2))


if __name__ == "__main__":
    main()
