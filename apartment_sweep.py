#!/usr/bin/env python3
"""Daily Zoopla sweep: 2-bed London flats <= MAX_PRICE, available in the target window.

Fetches Zoopla search results (curl_cffi Chrome impersonation to pass Cloudflare),
decodes the Next.js flight payload embedded in the page, filters by available-from
date, flags balcony/garden via Zoopla's own feature-filtered searches, dedupes
against seen.json, and writes a markdown report.

Usage: .venv/bin/python apartment_sweep.py
"""

import json
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

from curl_cffi import requests

# ---- config ----------------------------------------------------------------
MAX_PRICE = 4000          # pcm
MIN_BEDS = 1
WINDOW_START = date(2026, 9, 25)   # earliest acceptable move-in
WINDOW_END = date(2026, 11, 30)    # latest acceptable move-in
MAX_ZONE = 6              # approximate TfL zone cutoff (listings beyond are dropped)
BASE = (
    "https://www.zoopla.co.uk/to-rent/property/london/"
    "?price_frequency=per_month&property_sub_type=flats&property_sub_type=studio"
)
# Zoopla caps pagination around 1000 results; bands above this get split.
BAND_CAP = 900
MIN_BAND_WIDTH = 50
TAG_SEARCHES = {
    "balcony/terrace": "&feature=has_balcony_terrace",
    "garden": "&feature=has_garden",
    "furnished": "&furnished_state=furnished",
    "unfurnished": "&furnished_state=unfurnished",
}
OUTDOOR_TAGS = ("balcony/terrace", "garden")
EXCLUDE_TAGS = {"House share", "Retirement"}
OUTDOOR_WORDS = re.compile(r"\b(balcon|garden|terrace|patio|roof ?top)", re.I)
SHARE_WORDS = re.compile(r"\b(room in|double room|single room|shared room|premium room|en.?suite room|room (available|to rent|share)|house ?share|home ?share|flat ?share|shared (house|flat|accommodation)|multiple occupation|co.?living|lodger|studio room)\b", re.I)
REQUEST_DELAY = 3.0
MAX_PAGES = 40

HERE = Path(__file__).parent
SEEN_FILE = HERE / "seen.json"
REPORT_FILE = HERE / "matches.md"
LOG_FILE = HERE / "sweep_log.txt"
RAW_FILE = HERE / "listings_raw.json"
SITE_DATA = HERE / "docs" / "data.json"

# ---- fetching / parsing ----------------------------------------------------

def fetch(url: str) -> str | None:
    """Fetch a page; None on 404 (treated as end of pagination)."""
    last = "?"
    for attempt in range(6):
        try:
            r = requests.get(url, impersonate="chrome", timeout=60)
        except Exception as e:  # timeouts, connection resets
            last = type(e).__name__
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.text
        if r.status_code == 404:
            return None
        # 429/403: back off hard — Cloudflare rate limits clear after a pause
        last = f"HTTP {r.status_code}"
        time.sleep(30 * (attempt + 1) + random.uniform(0, 10))
    raise RuntimeError(f"{last} for {url}")


def decode_flight(html: str) -> str:
    """Join and unescape all Next.js flight payload chunks."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
    out = []
    for c in chunks:
        try:
            out.append(json.loads('"' + c + '"'))
        except json.JSONDecodeError:
            pass
    return "".join(out)


def extract_listings(html: str) -> tuple[list[dict], int]:
    text = decode_flight(html)
    key = '"regularListingsFormatted":'
    i = text.find(key)
    if i == -1:
        return [], 0
    listings, _ = json.JSONDecoder().raw_decode(text[i + len(key):])
    m = re.search(r'"totalResults":(\d+)', text)
    total = int(m.group(1)) if m else len(listings)
    return listings, total


def collect_band(base_url: str, pmin: int, pmax: int, found: dict[str, dict]) -> None:
    """Paginate one price band into `found`, splitting the band if it exceeds the cap."""
    band_url = f"{base_url}&price_min={pmin}&price_max={pmax}"
    html = fetch(band_url)
    if html is None:
        return
    listings, total = extract_listings(html)
    if total > BAND_CAP and (pmax - pmin) > MIN_BAND_WIDTH:
        mid = (pmin + pmax) // 2
        time.sleep(REQUEST_DELAY + random.uniform(0, 1.5))
        collect_band(base_url, pmin, mid, found)
        time.sleep(REQUEST_DELAY + random.uniform(0, 1.5))
        collect_band(base_url, mid + 1, pmax, found)
        return
    band_found: dict[str, dict] = {}
    pn = 1
    while True:
        for lst in listings:
            band_found[str(lst["listingId"])] = lst
        if not listings or len(band_found) >= total or pn >= MAX_PAGES:
            break
        pn += 1
        time.sleep(REQUEST_DELAY + random.uniform(0, 1.5))
        html = fetch(f"{band_url}&pn={pn}")
        if html is None:
            break
        listings, _ = extract_listings(html)
        if all(str(l["listingId"]) in band_found for l in listings):
            break
    found.update(band_found)


def collect_all(base_url: str) -> dict[str, dict]:
    """Collect a full search via price-band segmentation. Returns {listing_id: listing}."""
    found: dict[str, dict] = {}
    collect_band(base_url, 0, MAX_PRICE, found)
    return found

# ---- filtering -------------------------------------------------------------

def parse_price_pcm(price_str: str, beds) -> float | None:
    """True monthly cost from Zoopla's display string.

    'priceUnformatted' holds the weekly value for weekly-advertised lets, so it
    can't be trusted. '£931 pppm' is per person -> multiply by bedrooms.
    """
    m = re.match(r"£([\d,.]+)\s*(pcm|pw|pppm|pppw)", price_str or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    if unit in ("pw", "pppw"):
        val = val * 52 / 12
    if unit in ("pppm", "pppw"):
        val = val * (beds if isinstance(beds, int) and beds > 0 else 1)
    return round(val)


def extract_epc(listing_id: str, html: str | None) -> str:
    """EPC rating letter from a detail page; each site marks it differently."""
    if not html:
        return ""
    if listing_id.startswith("or"):
        m = re.search(r"EPC Rating</td>\s*<td>([A-G])", html)
    elif listing_id.startswith("rm"):
        m = (re.search(r"(?i)EPC\s*Rating[^A-Za-z0-9]{0,6}([A-G])\b", html)
             or re.search(r'epcRating\\?":\\?"([A-G])', html))
    else:
        m = re.search(r'epcRating\\?":\\?"([A-G])', html)
    return m.group(1) if m else ""


def parse_available(raw: str) -> date | None:
    """'12th Oct 2026' -> date; 'immediately' or unparseable -> None."""
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw or "")
    try:
        return datetime.strptime(cleaned.strip(), "%d %b %Y").date()
    except ValueError:
        return None


def in_window(d: date | None) -> bool:
    return d is not None and WINDOW_START <= d <= WINDOW_END


STATIONS = json.loads((HERE / "stations.json").read_text()) if (HERE / "stations.json").exists() else {}


def nearest_station(lat: float, lng: float) -> tuple[str, float] | tuple[None, None]:
    """Nearest tube/rail/DLR/overground station from the TfL open-data list."""
    if not STATIONS:
        return None, None
    from math import cos, radians
    best_name, best_d2 = None, None
    coslat = cos(radians(lat))
    for name, (slat, slng) in STATIONS.items():
        d2 = (slat - lat) ** 2 + ((slng - lng) * coslat) ** 2
        if best_d2 is None or d2 < best_d2:
            best_name, best_d2 = name, d2
    return best_name, round(111.32 * best_d2 ** 0.5, 2)


# Approximate TfL zone from distance to Charing Cross. Zone boundaries are not
# circles, so this is a heuristic — radii chosen so known stations land right.
CENTRE = (51.5074, -0.1278)
ZONE_RADII_KM = [3.0, 7.0, 11.0, 15.0, 19.5, 24.5]  # outer edge of zones 1-6


def approx_zone(lat: float, lng: float) -> int | None:
    from math import asin, cos, radians, sin, sqrt
    dlat, dlng = radians(lat - CENTRE[0]), radians(lng - CENTRE[1])
    a = sin(dlat / 2) ** 2 + cos(radians(CENTRE[0])) * cos(radians(lat)) * sin(dlng / 2) ** 2
    km = 2 * 6371 * asin(sqrt(a))
    for zone, edge in enumerate(ZONE_RADII_KM, start=1):
        if km <= edge:
            return zone
    return None

# ---- main ------------------------------------------------------------------

def main() -> None:
    log = lambda msg: print(f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}")

    if "--from-cache" in sys.argv and RAW_FILE.exists():
        all_listings = {str(l["listingId"]): l for l in json.loads(RAW_FILE.read_text())}
        log(f"main search (cached): {len(all_listings)} listings")
    else:
        all_listings = collect_all(BASE)
        RAW_FILE.write_text(json.dumps(list(all_listings.values())))
        log(f"main search: {len(all_listings)} listings")

    tag_cache = HERE / "tag_ids.json"
    if "--from-cache" in sys.argv and tag_cache.exists():
        tag_ids = {k: set(v) for k, v in json.loads(tag_cache.read_text()).items()}
        log("tag filters: cached")
    else:
        tag_ids = {}
        for label, param in TAG_SEARCHES.items():
            time.sleep(REQUEST_DELAY + random.uniform(0, 1.5))
            try:
                ids = set(collect_all(BASE + param))
            except RuntimeError as e:
                # tag flags are nice-to-have; don't fail the whole sweep
                log(f"{label} filter failed ({e}); skipping")
                ids = set()
            tag_ids[label] = ids
            log(f"{label} filter: {len(ids)} listings")
        tag_cache.write_text(json.dumps({k: sorted(v) for k, v in tag_ids.items()}))

    matches = []
    for lid, lst in all_listings.items():
        raw_avail = lst.get("availableFrom", "")
        avail = parse_available(raw_avail)
        # keep everything with a known availability (now or any future date);
        # the site's move-in range filter narrows from there
        if avail is None and raw_avail != "immediately":
            continue
        text_blob = f"{lst.get('title','')} {lst.get('summaryDescription','')}"
        if SHARE_WORDS.search(text_blob):
            continue
        listing_tags = {t.get("content") for t in (lst.get("tags") or []) if isinstance(t, dict)}
        if listing_tags & EXCLUDE_TAGS:
            continue
        pos = lst.get("pos") or {}
        zone = approx_zone(pos.get("lat", 0), pos.get("lng", 0)) if pos else None
        if zone is None or zone > MAX_ZONE:
            continue
        outdoor = sorted(t for t in OUTDOOR_TAGS if lid in tag_ids.get(t, ()))
        if not outdoor and OUTDOOR_WORDS.search(text_blob):
            outdoor = ["mentioned in description"]
        furnished = ("furnished" if lid in tag_ids.get("furnished", ())
                     else "unfurnished" if lid in tag_ids.get("unfurnished", ()) else None)
        feats = {f.get("iconId"): f.get("content") for f in lst.get("features", [])}
        images = [f"https://lid.zoocdn.com/645/430/{h}" for h in (lst.get("gallery") or [])[:4]]
        if not images and (lst.get("image") or {}).get("src"):
            images = [lst["image"]["src"]]
        price_pcm = parse_price_pcm(lst.get("price", ""), feats.get("bed"))
        if price_pcm is None or price_pcm > MAX_PRICE:
            continue
        st_name, st_km = nearest_station(pos["lat"], pos["lng"])
        matches.append({
            "id": lid,
            "address": lst.get("address", ""),
            "price": lst.get("price", ""),
            "price_num": price_pcm,
            "beds": feats.get("bed") or 0,
            "baths": feats.get("bath"),
            "receptions": feats.get("chair"),
            "zone": zone,
            "lat": round(pos["lat"], 5),
            "lng": round(pos["lng"], 5),
            "station": st_name,
            "station_km": st_km,
            "available": avail.isoformat() if avail else None,
            "in_window": in_window(avail),
            "outdoor": outdoor,
            "furnished": furnished,
            "published": lst.get("publishedOn", ""),
            "url": "https://www.zoopla.co.uk" + lst["listingUris"]["detail"],
            "summary": (lst.get("summaryDescription") or "")[:220],
            "images": images,
        })

    # ---- Rightmove + OpenRent, deduplicated against Zoopla ----
    import other_sites
    others_file = HERE / "other_sites_raw.json"
    try:
        if "--from-cache" in sys.argv and others_file.exists():
            others = json.loads(others_file.read_text())
            log(f"other sites (cached): {len(others)}")
        else:
            others = other_sites.collect_rightmove(MAX_PRICE)
            log(f"rightmove: {len(others)}")
            openrent = other_sites.collect_openrent(MAX_PRICE)
            log(f"openrent: {len(openrent)}")
            others += openrent
            others_file.write_text(json.dumps(others))
    except Exception as e:
        log(f"other sites failed ({e}); continuing with Zoopla only")
        others = json.loads(others_file.read_text()) if others_file.exists() else []

    def same_flat(a, b):
        if a["beds"] != b["beds"] or abs((a["price_num"] or 0) - (b["price_num"] or 0)) > 100:
            return False
        dx = (a["lng"] - b["lng"]) * 78.6   # km per degree lng at London's latitude
        dy = (a["lat"] - b["lat"]) * 111.3
        return dx * dx + dy * dy <= 0.12 ** 2

    merged = 0
    for o in others:
        if o.get("lat") is None:
            continue
        zone = approx_zone(o["lat"], o["lng"])
        if zone is None or zone > MAX_ZONE:
            continue
        dup = next((m for m in matches if same_flat(m, o)), None)
        if dup:
            dup.setdefault("also_on", {})[o["source"]] = o["url"]
            merged += 1
            continue
        o["zone"] = zone
        o["station"], o["station_km"] = nearest_station(o["lat"], o["lng"])
        o["in_window"] = in_window(date.fromisoformat(o["available"])) if o.get("available") else False
        matches.append(o)
    log(f"other sites: {merged} merged into Zoopla listings, "
        f"{len([m for m in matches if m.get('source')])} added as new")

    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}
    new_ids = [m["id"] for m in matches if m["id"] not in seen]
    today = date.today().isoformat()

    # availability: resurrect recently de-listed flats, flagged, for 30 days
    current_ids = {m["id"] for m in matches}
    prev = json.loads(SITE_DATA.read_text()).get("listings", []) if SITE_DATA.exists() else []
    for old in prev:
        oid = old["id"]
        if oid in current_ids or oid not in seen:
            continue
        gone = seen[oid].setdefault("gone_since", today)
        if (date.today() - date.fromisoformat(gone)).days > 30:
            continue
        old["unavailable"] = True
        matches.append(old)
    for m in matches:
        if not m.get("unavailable") and m["id"] in seen:
            seen[m["id"]].pop("gone_since", None)

    next_num = max((e.get("num", 0) for e in seen.values()), default=0) + 1
    for m in matches:
        entry = seen.setdefault(m["id"], {"first_seen": today})
        if "num" not in entry:
            entry["num"] = next_num
            next_num += 1
    SEEN_FILE.write_text(json.dumps(seen, indent=1))
    for m in matches:
        m["num"] = seen[m["id"]]["num"]
        m["first_seen"] = seen[m["id"]]["first_seen"]

    # EPC: rating lives on detail pages; fetch a budget per run, cache forever
    epc_file = HERE / "epc_cache.json"
    epc = json.loads(epc_file.read_text()) if epc_file.exists() else {}
    if "--from-cache" not in sys.argv or "--epc" in sys.argv:
        todo = sorted((m for m in matches if m["id"] not in epc and not m.get("unavailable")),
                      key=lambda m: not m["in_window"])
        for m in todo[:250]:
            time.sleep(REQUEST_DELAY + random.uniform(0, 1.5))
            try:
                html = fetch(m["url"])
            except RuntimeError:
                break
            epc[m["id"]] = extract_epc(m["id"], html)
        epc_file.write_text(json.dumps(epc))
        log(f"EPC cache: {len(epc)} cached, {max(0, len(todo) - 250)} still missing")
    for m in matches:
        if epc.get(m["id"]):
            m["epc"] = epc[m["id"]]

    # real public-transport times via TfL journey planner, budgeted per run
    import pt_times
    try:
        pt_budget = 0 if ("--from-cache" in sys.argv and "--pt" not in sys.argv) else 400
        pt_cache = pt_times.fill_cache(matches, budget=pt_budget, log=log)
        pt_times.annotate(matches, pt_cache)
    except Exception as e:
        log(f"PT times failed ({e}); estimates only")

    SITE_DATA.parent.mkdir(exist_ok=True)
    SITE_DATA.write_text(json.dumps({
        "generated": datetime.now().isoformat(timespec="minutes"),
        "criteria": {
            "max_price": MAX_PRICE, "min_beds": MIN_BEDS, "max_zone": MAX_ZONE,
            "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        },
        "listings": sorted(matches, key=lambda m: m["num"]),
    }, indent=1))

    # markdown report covers the target window only; the site shows everything
    report_matches = [m for m in matches if m["in_window"]]
    report_matches.sort(key=lambda m: (not m["outdoor"], m["zone"], m["available"]))

    lines = [
        f"# Apartment sweep — {today}",
        "",
        f"2+ bed flats in London (zones 1–{MAX_ZONE}) ≤ £{MAX_PRICE} pcm, "
        f"available {WINDOW_START} to {WINDOW_END}.",
        f"**{len(report_matches)} matches** ({len(new_ids)} new since last run).",
        "",
    ]
    for m in report_matches:
        tag = " 🆕" if m["id"] in new_ids else ""
        outdoor = f" — **{', '.join(m['outdoor'])}**" if m["outdoor"] else ""
        lines += [
            f"### [{m['address']}]({m['url']}){tag}",
            f"{m['price']} · {m['beds']} bed · ~zone {m['zone']} · "
            f"available **{m['available']}**{outdoor} · listed {m['published']}",
            f"> {m['summary']}",
            "",
        ]
    REPORT_FILE.write_text("\n".join(lines))

    summary = (f"{len(all_listings)} total, {len(matches)} on site, "
               f"{len(report_matches)} in window, {len(new_ids)} new")
    log(summary)
    with LOG_FILE.open("a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M} {summary}\n")

    if new_ids and sys.stdout.isatty() is False:
        import subprocess
        subprocess.run([
            "osascript", "-e",
            f'display notification "{len(new_ids)} new flats in your window" '
            f'with title "Apartment sweep"',
        ], check=False)


if __name__ == "__main__":
    main()
