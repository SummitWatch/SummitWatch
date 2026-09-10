# The Summit Manifest

A filterable directory of face-to-face executive events (CIO/CISO/CFO/CMO/CPO),
pulled from ~34 event-host companies, with a free weekly auto-refresh.

## What's in here

```
index.html                          the site itself (open directly, or host it)
events.json                         current event data (read by index.html)
scraper/scrape_events.py            the scraper that rebuilds events.json
scraper/requirements.txt            its Python dependencies
.github/workflows/update-events.yml runs the scraper weekly, for free
```

## Running it as-is (no setup)

Just open `index.html`. It will try to load `events.json` and fall back to a
built-in snapshot if that fails (which it will, if you just double-click the
file — browsers block local file-to-file fetches). To see the live-loading
behaviour, serve the folder locally:

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

## Setting up the free auto-update (recommended)

This is the "keep it current without me re-scraping by hand" part.

1. **Create a free GitHub repo** and push everything in this folder to it.
2. **Turn on GitHub Pages**: repo Settings → Pages → Deploy from branch →
   `main` → `/ (root)`. Your site is now live at
   `https://<you>.github.io/<repo>/` and updates automatically whenever
   `events.json` changes.
3. **The workflow runs itself**: `.github/workflows/update-events.yml` is
   already scheduled for Monday mornings (UTC) and free on public repos —
   GitHub doesn't meter Actions minutes for public repositories on standard
   runners. You can also trigger it manually any time from the repo's
   **Actions** tab → *Update event listings* → *Run workflow*.
4. **First run — check the output.** Go to the Actions tab after the first
   run, open the job log, and look at what `scrape_events.py` printed. Then
   look inside `scraper/debug/*.html` (also produced by the run — download
   the workflow artifact or run locally) to see the raw page the scraper
   worked from. If a host's events came back with blank city/country, that
   host's parser needs a small selector fix — see the next section.

## Why some hosts need manual tuning

I wrote the three custom parsers (Citrus Events, ABM Alliance, GDS Group)
from fetched-and-converted copies of each site, not from live DOM inspection,
because my own environment can't reach these websites directly. They're a
solid starting point but may drift when a site's markup changes, or may have
mis-guessed a selector on the first try. Treat `scraper/debug/*.html` as your
ground truth: if `scrape_events.py` isn't finding what you expect, open the
matching debug file and adjust the CSS selector or regex in the matching
`scrape_<host>()` function.

**Sites needing a real browser (JS-rendered), handled via optional
Playwright pass:** CxO Institute, The Millennium Alliance, Apex Assembly.
These render their event lists client-side, so a plain HTTP fetch returns an
empty shell. `scrape_js_rendered()` uses headless Chromium (via Playwright,
installed for free in the GitHub Actions runner) to load the page properly
before parsing. If you'd rather not carry that dependency, delete
`scrape_js_rendered` from the `SCRAPERS` list in `scrape_events.py` and drop
`playwright` from `requirements.txt` — those three hosts just won't
contribute events.

**Sites with bot detection:** elnevents.com blocked a direct fetch attempt
during development. If it blocks the scraper too, that host will silently
contribute zero events — nothing breaks, but you won't get fresh ELN data
without a different approach (e.g. a longer random delay, or asking ELN
directly for a feed).

## Adding a new host

1. Add it to `HOSTS` in `index.html` (for the directory listing).
2. If you want it in the auto-scraped set, write a `scrape_<name>()`
   function in `scrape_events.py` following the existing examples, and add
   it to the `SCRAPERS` list at the bottom.

## Extending the filters

`index.html` derives its Country / Time of year / Role dropdowns from
whatever's actually in the data — no code changes needed there when new
events appear.
