"""Twice-weekly email of newly listed homes that fit the one-bedroom search,
ranked and described by Claude. Runs on GitHub Actions; needs ANTHROPIC_API_KEY
plus SMTP_* and DIGEST_TO in the environment.
"""

import json
import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
DATA = HERE / "docs" / "data.json"
DAYS = int(os.environ.get("DIGEST_DAYS") or (3 if date.today().weekday() == 3 else 4))
TOP_N = 10
CANDIDATES = 40
BUDGET = 2000          # target rent; cheaper is better, up to the hard cap below
HARD_CAP = 3000
DESTS = ["Waterloo", "St Thomas'", "King's Cross", "Liverpool St"]


def load_new_listings() -> list[dict]:
    listings = json.loads(DATA.read_text())["listings"]
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    out = []
    for l in listings:
        if l.get("unavailable") or l.get("beds") not in (1, 2):
            continue
        if l["price_num"] > HARD_CAP or not l.get("in_window") or l.get("short_let"):
            continue
        if (l.get("first_seen") or "") < since:
            continue
        out.append(l)
    return out


def heuristic(l: dict) -> float:
    """Rough fit score used only to pick which listings Claude sees."""
    s = 0.0
    s += max(0, BUDGET - l["price_num"]) / 150         # cheaper than budget is good
    s -= max(0, l["price_num"] - BUDGET) / 50           # over budget hurts more
    s += 6 if l["beds"] == 1 else 0
    s += 4 if l.get("outdoor") else 0
    pt = (l.get("pt") or {}).get("Waterloo")
    if pt is not None:
        s += max(0, 45 - pt) / 2.5                      # commute matters most
    s -= max(0, (l.get("zone") or 3) - 2) * 2           # outer zones cost
    if l.get("station_km") is not None:
        s += max(0, 1.0 - l["station_km"]) * 4
    if l.get("epc") in ("A", "B", "C"):
        s += 2
    if l.get("date_unknown"):
        s -= 3
    s += 3 if l.get("gym_in_building") else 0
    s += 2 if l.get("bills_included") else 0
    s += min(len(l.get("images") or []), 3)
    return s


def brief(l: dict) -> dict:
    """Compact view of a listing for the prompt."""
    return {
        "id": l["id"], "num": l["num"], "price_pcm": l["price_num"], "beds": l["beds"],
        "baths": l.get("baths"), "address": l["address"], "zone": l.get("zone"),
        "move_in": l.get("available") or ("unknown" if l.get("date_unknown") else "now"),
        "outdoor": l.get("outdoor") or [], "furnished": l.get("furnished"),
        "epc": l.get("epc"), "station": l.get("station"), "station_lines": l.get("station_lines"),
        "station_walk_mins": round(l["station_km"] * 1.35 / 4.8 * 60) if l.get("station_km") is not None else None,
        "floor_area_sqm": round(l["sqft"] / 10.764) if l.get("sqft") else None,
        "bills_included": l.get("bills_included"), "gym_in_building": l.get("gym_in_building"),
        "nearest_gym": f"{l['gym']} ({l['gym_km']} km)" if l.get("gym") else None,
        "public_transport_mins": l.get("pt"), "photos": len(l.get("images") or []),
        "description": l.get("summary", ""), "source": l.get("source") or "Zoopla",
    }


RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "why": {"type": "string"},
                    "drawbacks": {"type": "string"},
                },
                "required": ["id", "why", "drawbacks"],
                "additionalProperties": False,
            },
        },
        "intro": {"type": "string"},
    },
    "required": ["picks", "intro"],
    "additionalProperties": False,
}

SYSTEM = f"""You help one person find a flat to rent in London for themselves.
Their preferences, in order: one bedroom (two is acceptable but less ideal); rent
under £{BUDGET} a month, and the cheaper the better; a balcony, terrace or garden;
quick public transport to Waterloo and St Thomas' Hospital, and reasonable access
to King's Cross and Liverpool Street; close to a station; a gym in the building or
one nearby; bills included is a plus; a place that looks good and well kept in its
description; a decent floor area; a decent EPC rating.

You will get a JSON list of newly listed homes with their facts. Pick the
{TOP_N} best fits and order them best first. For each, write "why" as two or three
plain sentences on what makes it a good fit, using the concrete facts given
(price, travel minutes, outdoor space, station), and "drawbacks" as one or two
sentences on what is less good — for example over budget, far from Waterloo, no
outdoor space, unknown move-in date, or two bedrooms rather than one. Never invent
facts that are not in the data. Write the way a sharp friend would, not an estate
agent. Also write a one-sentence "intro" summarising the batch."""


def rank_with_claude(cands: list[dict]) -> dict:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps([brief(l) for l in cands])}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": RANK_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def travel_cell(l: dict) -> str:
    pt = l.get("pt") or {}
    parts = []
    for d in DESTS:
        if pt.get(d) is not None:
            parts.append(f"{d} {pt[d]}′")
    return " · ".join(parts) if parts else "—"


def render(picks: list[dict], by_id: dict, intro: str, total_new: int) -> str:
    cards = []
    for i, p in enumerate(picks, 1):
        l = by_id.get(p["id"])
        if not l:
            continue
        img = (l.get("images") or [""])[0]
        outdoor = ", ".join(l.get("outdoor") or []) or "no outdoor space"
        station = ""
        if l.get("station"):
            walk = round(l["station_km"] * 1.35 / 4.8 * 60) if l.get("station_km") is not None else None
            lines = f" ({', '.join(l['station_lines'])})" if l.get("station_lines") else ""
            station = f"{l['station']}{lines}" + (f" · {walk} min walk" if walk is not None else "")
        gym = "in the building" if l.get("gym_in_building") else (f"{l['gym']}, {l['gym_km']} km" if l.get("gym") else "—")
        extras = " · ".join(x for x in [f"{round(l['sqft'] / 10.764)} m²" if l.get("sqft") else "",
                                        "bills included" if l.get("bills_included") else ""] if x)
        move = l.get("available") or ("move-in date unknown" if l.get("date_unknown") else "available now")
        cards.append(f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4d8c4;border-radius:12px;margin:0 0 18px;background:#fffaf2;">
<tr><td style="padding:14px 16px 6px;font:600 17px -apple-system,Segoe UI,Roboto,sans-serif;color:#2a2419;">
  #{i} &nbsp; {l['price']} &nbsp;·&nbsp; {l['beds']} bed &nbsp;·&nbsp; {l['address']}</td></tr>
<tr><td style="padding:0 16px;">{f'<img src="{img}" width="100%" style="border-radius:8px;max-height:260px;object-fit:cover;display:block;" alt="">' if img else ''}</td></tr>
<tr><td style="padding:10px 16px 0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#2a2419;">
  <table role="presentation" cellpadding="0" cellspacing="0" style="font-size:13px;color:#4a4033;">
    <tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Move in</td><td>{move}</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Outdoor</td><td>{outdoor}</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Station</td><td>{station or '—'}</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Public transport</td><td>{travel_cell(l)}</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Gym</td><td>{gym}</td></tr>
    {f'<tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Also</td><td>{extras}</td></tr>' if extras else ''}
    <tr><td style="padding:2px 14px 2px 0;color:#7a6f5e;">Zone / EPC</td><td>zone {l.get('zone')}{' · EPC ' + l['epc'] if l.get('epc') else ''}{' · ' + l['furnished'] if l.get('furnished') else ''}</td></tr>
  </table>
  <p style="margin:10px 0 4px;">{p['why']}</p>
  <p style="margin:0 0 10px;color:#8a5a2b;"><b>But:</b> {p['drawbacks']}</p>
  <p style="margin:0 0 14px;"><a href="{l['url']}" style="color:#b0651f;font-weight:600;">View on {l.get('source') or 'Zoopla'} →</a>
  {' '.join(f'&nbsp; <a href="{u}" style="color:#b0651f;">also on {s} →</a>' for s, u in (l.get('also_on') or {}).items())}
  &nbsp; <a href="https://www.londonflat.xyz/" style="color:#7a6f5e;">#{l['num']} on the site</a></p>
</td></tr></table>""")
    return f"""<!doctype html><html><body style="margin:0;background:#f7f1e6;padding:20px 10px;">
<div style="max-width:620px;margin:0 auto;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#2a2419;">
<h1 style="font-size:22px;margin:0 0 4px;">🌿 New one-bedroom flats</h1>
<p style="margin:0 0 6px;color:#7a6f5e;">{date.today():%A %-d %B} · {total_new} new listings in the last {DAYS} days matched the search; here are the {len(cards)} best fits.</p>
<p style="margin:0 0 18px;">{intro}</p>
{''.join(cards)}
<p style="color:#7a6f5e;font-size:13px;">Full list with filters and map: <a href="https://www.londonflat.xyz/" style="color:#b0651f;">londonflat.xyz</a></p>
</div></body></html>"""


def send(html: str, subject: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = os.environ["DIGEST_TO"]
    msg.set_content("This email is best viewed as HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def main() -> None:
    new = load_new_listings()
    print(f"{len(new)} new listings in the last {DAYS} days fit the search")
    if not new:
        print("nothing to send")
        return
    cands = sorted(new, key=heuristic, reverse=True)[:CANDIDATES]
    ranked = rank_with_claude(cands)
    by_id = {l["id"]: l for l in cands}
    html = render(ranked["picks"][:TOP_N], by_id, ranked["intro"], len(new))
    out = HERE / "digest_preview.html"
    out.write_text(html)
    print(f"wrote {out}")
    if "--no-send" in sys.argv:
        return
    send(html, f"New one-bedroom flats — {date.today():%-d %b}")
    print("sent to", os.environ["DIGEST_TO"])


if __name__ == "__main__":
    main()
