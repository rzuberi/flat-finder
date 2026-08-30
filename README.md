# flat-finder

Flat hunting site for London. Scrapes Zoopla, Rightmove and OpenRent once a day, merges duplicate listings and puts everything on one page with filters, a map and travel times.

Live at [londonflat.xyz](https://www.londonflat.xyz).

## Parts

- `apartment_sweep.py` — daily scrape, writes `docs/data.json`
- `other_sites.py` — Rightmove and OpenRent collectors
- `pt_times.py` — journey times from the TfL API
- `docs/` — the site itself (static, served by Vercel and GitHub Pages)
- `supabase.sql` — schema for the likes table
- `sweep_and_push.sh` — what the daily job runs

## Running

    python3 -m venv .venv
    .venv/bin/pip install curl_cffi
    .venv/bin/python apartment_sweep.py
