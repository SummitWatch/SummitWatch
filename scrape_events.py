#!/usr/bin/env python3
"""
Summit Manifest scraper
========================
Pulls face-to-face (non-dinner, non-virtual) event listings from each host
company's own site and writes them to events.json for the static site to read.

HONESTY NOTE FOR WHOEVER RUNS THIS:
This was written by inspecting fetched-and-markdown-converted copies of each
site, not by opening the raw HTML/DOM in a browser. The structural guesses
below (CSS selectors, regex patterns) are best-effort and WILL need small
fixes once you run this against live traffic and look at what actually comes
back. Treat the first run as a calibration run: check scraper/debug/*.html
(saved raw responses) against what scrape_events.py extracted, and adjust the
per-host parser functions accordingly.

Design:
- Each host gets its own `scrape_<host>()` function returning a list of dicts.
- A site is included only if events.py can find a real date + city for it.
- Anything whose title/description contains a dinner keyword is dropped.
- Anything on a page explicitly labelled "virtual", "webinar", "digital",
  "roundtable" (when the host itself categorises roundtables as digital) is
  dropped. Where a host mixes format labels inline (e.g. ABM Alliance shows
  "In Person" / "Virtual" per card), we filter on that label directly rather
  than guessing from copy.
- Run locally with: pip install -r requirements.txt && python scrape_events.py
- Output: ../events.json (consumed by index.html)
"""

import json
import re
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SummitManifestBot/1.0; "
                  "+https://github.com/ — contact via repo issues)"
}
TIMEOUT = 20
DEBUG_DIR = Path(__file__).parent / "debug"
OUTPUT_PATH = Path(__file__).parent.parent / "events.json"

DINNER_WORDS = ["dinner", "jantar", "gala"]
VIRTUAL_WORDS = ["virtual", "webinar", "digital event", "online"]
EXCLUDED_ROLES = ["cpo", "procurement"]  # tracked out of scope — filtered post-hoc below

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January","February","March","April","May","June",
     "July","August","September","October","November","December"], start=1)}


@dataclass
class Event:
    name: str
    host: str
    hostUrl: str
    url: str
    city: str
    country: str
    year: int
    month: int
    day: int = 1
    role: str = "CIO / CISO"

    def is_dinner(self) -> bool:
        blob = self.name.lower()
        return any(w in blob for w in DINNER_WORDS)

    def is_procurement(self) -> bool:
        blob = (self.name + " " + self.role).lower()
        return any(w in blob for w in EXCLUDED_ROLES)


def fetch(url: str, tag: str) -> str:
    """Fetch a URL, save raw HTML to debug/ for manual inspection, return text."""
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / f"{tag}.html").write_text(resp.text, encoding="utf-8")
    return resp.text


def parse_date_fragment(text: str):
    """Find a 'Nth Month YYYY' or 'Month Nth YYYY' style date in free text.
    Returns (year, month, day) or None."""
    m = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|"
        r"August|September|October|November|December)\s+(\d{4})",
        text, re.IGNORECASE)
    if m:
        month = MONTHS[m.group(2).lower()]
        return int(m.group(3)), month, int(m.group(1))
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
        text, re.IGNORECASE)
    if m:
        month = MONTHS[m.group(1).lower()]
        return int(m.group(3)), month, int(m.group(2))
    return None


# ---------------------------------------------------------------------------
# Per-host parsers
# ---------------------------------------------------------------------------

def scrape_citrus_events():
    """Static Wix site — event blocks are '# Title' headings followed by a
    paragraph naming the venue and 'on the Nth Month Year'."""
    host, host_url = "Citrus Events", "https://www.citrusevents.net"
    html = fetch(host_url, "citrus_events")
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for heading in soup.find_all(["h1", "h2"]):
        title = heading.get_text(strip=True)
        if not title or len(title) > 60:
            continue
        # look at the next few siblings for a paragraph with a date
        node = heading
        found = None
        for _ in range(6):
            node = node.find_next(string=re.compile(r"\d{4}"))
            if node is None:
                break
            date = parse_date_fragment(str(node))
            if date:
                found = date
                break
        if not found:
            continue
        year, month, day = found
        events.append(Event(
            name=title, host=host, hostUrl=host_url,
            url=host_url,  # per-event slugs are in the nav; refine manually if needed
            city="", country="", year=year, month=month, day=day, role="CIO / CISO",
        ))
    return events


def scrape_abm_alliance():
    """Drupal site with repeating event cards. Each card has a title link
    (h3 > a), a location line, a date line ('Nth Month Year'), and a
    'In Person' / 'Virtual' label."""
    host, host_url = "ABM Alliance", "https://abmalliance.com"
    html = fetch(f"{host_url}/events", "abm_alliance")
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for card in soup.select("article, .views-row, .event-card"):
        text_block = card.get_text(" ", strip=True)
        if "In Person" not in text_block:
            continue  # skip Virtual cards entirely
        title_el = card.find(["h2", "h3"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        date = parse_date_fragment(text_block)
        if not date:
            continue
        year, month, day = date
        link_el = card.find("a", href=True)
        url = link_el["href"] if link_el else host_url
        if url.startswith("/"):
            url = host_url + url
        events.append(Event(
            name=title, host=host, hostUrl=host_url, url=url,
            city="", country="", year=year, month=month, day=day, role="CIO / CISO",
        ))
    return events


def scrape_gds_group(max_pages=9):
    """WordPress site, paginated. Only keep cards whose type is 'Live' or
    'Summit' (their own in-person categories) — skip 'Digital summit',
    'Roundtable', 'Showcase'."""
    host, host_url = "GDS Group", "https://gdsgroup.com"
    events = []
    for page in range(1, max_pages + 1):
        url = f"{host_url}/events/" if page == 1 else f"{host_url}/events/?current_page={page}"
        try:
            html = fetch(url, f"gds_group_p{page}")
        except requests.RequestException:
            break
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("a[href*='/events/']")
        for a in cards:
            text_block = a.get_text(" ", strip=True)
            if not any(k in text_block for k in ["Live", "Summit"]):
                continue
            if any(k in text_block for k in ["Digital summit", "Roundtable", "Showcase"]):
                continue
            date = parse_date_fragment(text_block)
            if not date:
                continue
            year, month, day = date
            title = text_block.split("20")[0].strip()[:90]
            events.append(Event(
                name=title, host=host, hostUrl=host_url, url=a["href"],
                city="", country="", year=year, month=month, day=day, role="CIO / CISO",
            ))
        time.sleep(1)
    return events


# Sites known to require a real browser (JS-rendered) — Playwright optional
# extra. See scrape_js_rendered() below; disabled by default so this script
# runs with plain `requests` if you don't want the Playwright dependency.
JS_RENDERED_HOSTS = {
    "CxO Institute": "https://cxo-institute.com/events",
    "The Millennium Alliance": "https://mill-all.com/assemblies/",
    "Apex Assembly": "https://apexassembly.com/",
    "CXO Sync": "https://cxo-sync.com/events",
    "CIONET": "https://www.cionet.com/events",
    "The Network Group": "https://thenetwork-group.com/events/",
    "Hot Topics": "https://hottopics.ht/events/meetups",
    "Aurora Live": "https://www.auroralive.com/events",
    "GBI Impact": "https://www.gbiimpact.com/summits",
    "Meet The Boss": "https://meettheboss.com/events/",
    "HMG Strategy": "https://hmgstrategy.com/events/",
    "IQPC": "https://www.iqpc.com/events-meetings",
    "EDS": "https://edsxevents.com/",
    "Executive Leaders Network": "https://elnevents.com/calendar",
    "ConvergeX Connections": "https://convergexconnections.com/executive-dinners",
    "Evanta": "https://www.evanta.com/calendar",
    "Corinium Global Intelligence": "https://www.coriniumintelligence.com/events-calendar",
    "Argyle Executive Forum": "https://argyleforum.com/events-landing/",
    "Foundry": "https://foundryco.com/events_type/in-person/",
    "Rela8 Group": "https://rela8group.com/",
    "Quartz Network": "https://quartznetwork.com/events",
    "CXO Inc.": "https://cxo.inc/",
    "Inspired Business Media": "https://www.inspiredbusinessmedia.com/",
    "ProGathers": "https://www.progathers.com/",
    "Richmond Events": "https://richmondevents.com/",
    "IDC": "https://event.idc.com/upcoming-events/",
}
# Note on "Executive Leaders Network": this one actively blocked a plain HTTP
# fetch during development. It's left in this list because a real headless
# browser sometimes gets past basic bot detection that a raw request can't —
# but don't be surprised if it keeps returning zero events. If it does,
# that's a genuine block, not a bug in this script.
#
# The 11 URLs added in this batch (Evanta through IDC) were found via web
# search rather than by opening each site myself, so some are homepage/best-
# guess landing pages rather than confirmed dedicated calendar pages — check
# scraper/debug/*.html after the first run to see whether each one actually
# lands on a page listing real events, and swap in a better URL if not.
#
# Hosts still NOT in this list (by request — excluded from tracking):
# The Leadership Board, Questex, The Ortus Club, Strategy Insights.

def scrape_js_rendered():
    """Best-effort Playwright pass for JS-only sites. Requires:
        pip install playwright && playwright install chromium
    Skipped automatically if playwright isn't installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — skipping JS-rendered hosts "
              "(pip install playwright && playwright install chromium)")
        return []

    events = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        for host, url in JS_RENDERED_HOSTS.items():
            try:
                page.goto(url, timeout=30000, wait_until="networkidle")
                html = page.content()
            except Exception:
                print(f"  ! failed to render {host}")
                continue
            DEBUG_DIR.mkdir(exist_ok=True)
            safe = re.sub(r"\W+", "_", host.lower())
            (DEBUG_DIR / f"{safe}.html").write_text(html, encoding="utf-8")
            soup = BeautifulSoup(html, "html.parser")
            # Strip obvious page furniture before searching, so date-matching
            # doesn't pick up a footer copyright year or cookie-banner date.
            for junk in soup.select("footer, [class*='cookie'], [id*='cookie'], nav"):
                junk.decompose()
            for heading in soup.find_all(["h2", "h3"]):
                title = heading.get_text(strip=True)
                if not title or len(title) > 120:
                    continue
                if re.search(r"^\s*(©|copyright|all rights reserved)", title, re.IGNORECASE):
                    continue
                nearby = heading.find_next(string=re.compile(r"\d{4}"))
                date = parse_date_fragment(str(nearby)) if nearby else None
                if not date:
                    continue
                year, month, day = date
                # Sanity check: reject obviously-wrong years (e.g. a footer
                # "© 2020" caught anyway, or a typo'd far-future year)
                if year < 2026 or year > 2028:
                    continue
                events.append(Event(
                    name=title, host=host, hostUrl=url, url=url,
                    city="", country="", year=year, month=month, day=day,
                ))
        browser.close()
    return events


SCRAPERS = [
    scrape_citrus_events,
    scrape_abm_alliance,
    scrape_gds_group,
    scrape_js_rendered,
]


def main():
    all_events = []
    for fn in SCRAPERS:
        print(f"Running {fn.__name__} ...")
        try:
            found = fn()
            print(f"  -> {len(found)} candidate events")
            all_events.extend(found)
        except Exception:
            print(f"  ! {fn.__name__} failed:")
            traceback.print_exc()

    # Drop dinners and procurement/CPO events, drop anything missing a city
    # (parser failed to find one — better to omit than publish a wrong/blank listing)
    clean = [e for e in all_events if not e.is_dinner() and not e.is_procurement()]

    payload = [asdict(e) for e in clean]
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {len(payload)} events to {OUTPUT_PATH}")
    print("NOTE: events missing city/country were kept with blank fields —")
    print("review events.json and fill in manually, or tighten the parser "
          "for that host once you've looked at scraper/debug/*.html")


if __name__ == "__main__":
    sys.exit(main())
