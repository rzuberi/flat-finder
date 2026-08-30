"""Real public-transport times via the TfL Journey Planner.

Listings are snapped to ~1km grid cells; one journey query per cell per
destination, cached forever in pt_cache.json. A budget per run keeps the sweep
polite; coverage builds over a few runs, in-window listings first.
"""

import json
import random
import time
from pathlib import Path

from curl_cffi import requests

HERE = Path(__file__).parent
CACHE = HERE / "pt_cache.json"
DESTS = {
    "Waterloo": (51.5031, -0.1132),
    "St Thomas'": (51.4980, -0.1187),
    "King's Cross": (51.5308, -0.1238),
    "Liverpool St": (51.5178, -0.0817),
}


def cell_key(lat: float, lng: float) -> str:
    return f"{round(lat, 2)},{round(lng, 2)}"


def _journey_mins(from_lat: float, from_lng: float, to: tuple) -> int | None:
    url = (f"https://api.tfl.gov.uk/Journey/JourneyResults/"
           f"{from_lat},{from_lng}/to/{to[0]},{to[1]}")
    try:
        r = requests.get(url, impersonate="chrome", timeout=30)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    durations = [j.get("duration") for j in r.json().get("journeys", []) if j.get("duration")]
    return min(durations) if durations else None


def fill_cache(listings: list[dict], budget: int = 400, log=print) -> dict:
    """Query missing cells for the given listings, newest-window first."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = []
    seen_cells = set()
    for l in sorted(listings, key=lambda m: not m.get("in_window", False)):
        if l.get("lat") is None:
            continue
        key = cell_key(l["lat"], l["lng"])
        if key in seen_cells:
            continue
        seen_cells.add(key)
        missing = [d for d in DESTS if d not in cache.get(key, {})]
        if missing:
            todo.append((key, l["lat"], l["lng"], missing))
    calls = 0
    for key, lat, lng, missing in todo:
        for dest in missing:
            if calls >= budget:
                break
            mins = _journey_mins(round(lat, 2), round(lng, 2), DESTS[dest])
            calls += 1
            if mins is not None:
                cache.setdefault(key, {})[dest] = mins
            time.sleep(1 + random.uniform(0, 0.6))
        if calls >= budget:
            break
    CACHE.write_text(json.dumps(cache))
    log(f"TfL PT cache: {len(cache)} cells, {calls} queries this run, "
        f"{sum(len(m) for _, _, _, m in todo) - calls} still missing")
    return cache


def annotate(listings: list[dict], cache: dict) -> None:
    for l in listings:
        if l.get("lat") is None:
            continue
        times = cache.get(cell_key(l["lat"], l["lng"]))
        if times:
            l["pt"] = times
