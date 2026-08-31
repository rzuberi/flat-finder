"""Rightmove and OpenRent collectors, normalised to the Zoopla match schema.

Rightmove: search pages embed __NEXT_DATA__ JSON; ~1000-result cap per search
handled with recursive price-band splitting, like the Zoopla sweep.
OpenRent: one search page lists every matching property id + coordinates;
details come from the batched /search/propertiesbyid endpoint.
"""

import json
import random
import re
import time
from datetime import datetime

from curl_cffi import requests

RM_BASE = ("https://www.rightmove.co.uk/property-to-rent/find.html"
           "?locationIdentifier=REGION%5E87490&propertyTypes=flat"
           "&includeLetAgreed=false&sortType=6")
OR_BASE = ("https://www.openrent.co.uk/properties-to-rent/london"
           "?term=London")
DELAY = 2.0
RM_CAP = 950          # rightmove stops serving past ~1000 results per search
SHARE_WORDS = re.compile(r"\b(room in|double room|single room|house ?share|flat ?share|shared (house|flat|accommodation)|studio room)\b", re.I)
OUTDOOR_BALCONY = re.compile(r"\b(balcon|terrace|roof ?top)", re.I)
OUTDOOR_GARDEN = re.compile(r"\b(garden|patio)", re.I)


def _get(url: str) -> str | None:
    last = "?"
    for attempt in range(5):
        try:
            r = requests.get(url, impersonate="chrome", timeout=30)
        except Exception as e:      # DNS blips, timeouts: back off and retry
            last = str(e)[:80]
            time.sleep(20 * (attempt + 1) + random.uniform(0, 8))
            continue
        if r.status_code == 200:
            return r.text
        if r.status_code == 404:
            return None
        last = f"HTTP {r.status_code}"
        time.sleep(20 * (attempt + 1) + random.uniform(0, 8))
    raise RuntimeError(f"{last} for {url}")


# ---- Rightmove ---------------------------------------------------------------

def _rm_page(pmin: int, pmax: int, index: int) -> tuple[list[dict], int]:
    url = f"{RM_BASE}&minPrice={pmin}&maxPrice={pmax}&index={index}"
    html = _get(url)
    if html is None:
        return [], 0
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return [], 0
    res = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]
    total = int(str(res.get("resultCount", "0")).replace(",", ""))
    return res.get("properties", []), total


def _rm_band(pmin: int, pmax: int, found: dict) -> None:
    props, total = _rm_page(pmin, pmax, 0)
    if total > RM_CAP and (pmax - pmin) > 50:
        mid = (pmin + pmax) // 2
        time.sleep(DELAY)
        _rm_band(pmin, mid, found)
        time.sleep(DELAY)
        _rm_band(mid + 1, pmax, found)
        return
    index = 0
    while props:
        for p in props:
            found[p["id"]] = p
        index += 24
        if index >= min(total, 1000):
            break
        time.sleep(DELAY + random.uniform(0, 1))
        props, _ = _rm_page(pmin, pmax, index)
        if all(p["id"] in found for p in props):
            break


def collect_rightmove(max_price: int) -> list[dict]:
    found: dict = {}
    _rm_band(0, max_price, found)
    out = []
    for p in found.values():
        if p.get("commercial") or p.get("development") or p.get("students"):
            continue
        text = f"{p.get('propertyTypeFullDescription','')} {p.get('summary','')}"
        if SHARE_WORDS.search(text):
            continue
        beds = p.get("bedrooms")
        if not beds:
            if (p.get("propertySubType") or "").lower() != "studio":
                continue
            beds = 0
        price = p.get("price") or {}
        pcm = price.get("amount") if price.get("frequency") == "monthly" else None
        if not pcm or pcm > max_price:
            continue
        avail = None
        if p.get("letAvailableDate"):
            avail = p["letAvailableDate"][:10]
        loc = p.get("location") or {}
        imgs = [i["srcUrl"] for i in (p.get("propertyImages") or {}).get("images", [])[:4]]
        outdoor = []
        if OUTDOOR_BALCONY.search(text):
            outdoor.append("balcony/terrace")
        if OUTDOOR_GARDEN.search(text):
            outdoor.append("garden")
        out.append({
            "id": f"rm{p['id']}",
            "source": "Rightmove",
            "address": p.get("displayAddress", ""),
            "price": f"£{pcm:,.0f} pcm".replace(".0", ""),
            "price_num": round(pcm),
            "beds": beds,
            "baths": p.get("bathrooms"),
            "receptions": None,
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
            "available": avail,
            "date_unknown": avail is None,
            "outdoor": outdoor,
            "furnished": None,
            "published": (p.get("firstVisibleDate") or "")[:10],
            "url": "https://www.rightmove.co.uk" + p.get("propertyUrl", ""),
            "summary": (p.get("summary") or "")[:220],
            "images": imgs,
        })
    return out


# ---- OpenRent ----------------------------------------------------------------

def collect_openrent(max_price: int) -> list[dict]:
    html = _get(f"{OR_BASE}&prices_max={max_price}")
    if not html:
        return []
    def arr(name):
        m = re.search(rf"var {name} = (\[.*?\]);", html, re.S)
        if not m:
            return []
        import ast
        return ast.literal_eval(re.sub(r",\s*\]", "]", m.group(1)))
    ids = arr("PROPERTYIDS")
    lats = arr("PROPERTYLISTLATITUDES")
    lngs = arr("PROPERTYLISTLONGITUDES")
    coords = dict(zip(ids, zip(lats, lngs)))
    out = []
    for i in range(0, len(ids), 20):
        batch = ids[i:i + 20]
        qs = "&".join(f"ids={x}" for x in batch)
        r = requests.get(f"https://www.openrent.co.uk/search/propertiesbyid?{qs}",
                         impersonate="chrome", timeout=30)
        if r.status_code != 200:
            time.sleep(15)
            continue
        for p in r.json():
            if p.get("letAgreed") or p.get("isMultiRoom"):
                continue
            title = p.get("title", "")
            bm = re.match(r"(\d+) Bed", title)
            if not bm and not title.startswith("Studio"):
                continue
            beds = int(bm.group(1)) if bm else 0
            details = p.get("details") or []
            pcm = p.get("rentPerMonth")
            if not pcm or pcm > max_price:
                continue
            lat, lng = coords.get(p["id"], (None, None))
            desc = p.get("description", "").strip()
            outdoor = []
            if OUTDOOR_BALCONY.search(desc):
                outdoor.append("balcony/terrace")
            if OUTDOOR_GARDEN.search(desc):
                outdoor.append("garden")
            img = p.get("imageUrl", "")
            out.append({
                "id": f"or{p['id']}",
                "source": "OpenRent",
                "address": title.split(", ", 1)[-1],
                "price": f"£{pcm:,.0f} pcm",
                "price_num": round(pcm),
                "beds": beds,
                "baths": next((int(x[0]) for x in details if "Bath" in x), None),
                "receptions": None,
                "lat": lat,
                "lng": lng,
                "available": None,
                "date_unknown": True,
                "outdoor": outdoor,
                "furnished": ("furnished" if "Furnished" in details
                              else "unfurnished" if "Unfurnished" in details else None),
                "published": "",
                "url": f"https://www.openrent.co.uk/{p['id']}",
                "summary": desc[:220],
                "images": [("https:" + img) if img.startswith("//") else img] if img else [],
            })
        time.sleep(DELAY + random.uniform(0, 1))
    return out


if __name__ == "__main__":
    rm = collect_rightmove(3000)
    print(f"rightmove: {len(rm)}")
    orl = collect_openrent(3000)
    print(f"openrent: {len(orl)}")
    json.dump(rm + orl, open("other_sites_raw.json", "w"))
    print("saved other_sites_raw.json", datetime.now())
