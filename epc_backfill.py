"""One-off EPC backfill: fetch ratings for every in-window Zoopla listing
missing one. Saves the cache every 25 fetches so progress survives interruption.
"""

import json
import random
import re
import time
from pathlib import Path

from apartment_sweep import extract_epc, fetch

HERE = Path(__file__).parent
EPC_FILE = HERE / "epc_cache.json"

data = json.loads((HERE / "docs" / "data.json").read_text())
epc = json.loads(EPC_FILE.read_text()) if EPC_FILE.exists() else {}

todo = [l for l in data["listings"]
        if l.get("in_window") and not l.get("unavailable") and l["id"] not in epc]
print(f"{len(todo)} in-window listings missing EPC", flush=True)

done = 0
for l in todo:
    time.sleep(3 + random.uniform(0, 1.5))
    try:
        html = fetch(l["url"])
    except RuntimeError as e:
        print(f"stopping at {done}: {e}", flush=True)
        break
    epc[l["id"]] = extract_epc(l["id"], html)
    done += 1
    if done % 25 == 0:
        EPC_FILE.write_text(json.dumps(epc))
        print(f"{done}/{len(todo)}", flush=True)

EPC_FILE.write_text(json.dumps(epc))
print(f"backfill done: {done} fetched, cache now {len(epc)}", flush=True)
