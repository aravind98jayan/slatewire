# SLATEWIRE

A desktop cybersecurity news reader. Fetches live RSS feeds from 41 sources
across 30 countries, sorts stories into STRIKE / SHIELD / FALLOUT / RADAR,
flags severity, marks CVEs that are already exploited in the wild (CISA KEV),
searches headlines, descriptions, briefs and the archive, builds a local daily
digest with an optional AI summary, writes short AI briefs you can archive with
your own notes, and shows source locations on a rotating globe with real
coastline data -- all in its own window, no browser needed.

## 1. Install Python dependencies

```bash
cd slatewire
pip install -r requirements.txt
```

`requirements.txt` uses upper-bounded ranges (for example `flask>=3.1,<4`),
so a future major release of a dependency cannot silently change behaviour
or drop a supported OS. The versions this release was tested with are listed
as comments in the file.

### Linux-specific note (pywebview needs a webview backend)

On Linux, `pywebview` needs either GTK+WebKit2 or Qt installed system-side --
it doesn't bundle its own browser engine. On Debian 13 (trixie) and other
recent Debian/Ubuntu/Kali/Parrot releases:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1 python3-gi-cairo
```

(Older releases may only have `gir1.2-webkit2-4.0` -- use that instead if
`-4.1` isn't found.) If GTK gives you trouble, `pywebview` can use Qt instead:

```bash
pip install PyQt5 PyQtWebEngine
```

## 2. Add your Gemini API key (free, no credit card)

The easiest way is inside the app: **Settings -> AI briefs**, paste the key and
press **Save**. It is stored in `%APPDATA%\SLATEWIRE\secrets.json` on Windows
(`~/.config/SLATEWIRE/secrets.json` on Linux), outside the app folder, so an
update never erases it. The key is never sent back to the page after saving.

If you prefer a file, either open `config.py` and paste your key in:

```python
GEMINI_API_KEY = "your-api-key"
```

...or (recommended for scripts) set it as an environment variable instead, so
you never have a real key sitting in a source file:

```bash
export SLATEWIRE_GEMINI_API_KEY="your-api-key"
```

The order is: a key saved in Settings, then `config.py` / the environment
variable, then no key at all.

Get one at https://aistudio.google.com/apikey -- sign in with a Google
account, no credit card or phone verification needed. This is Google's free
tier for the Gemini API: roughly 1,500 requests/day on Flash models, far
more than this app will ever use for daily briefs. This is what generates
the briefs (6-8 lines for the Hot News teaser, 10-15 lines for a full
article brief). Without a key, the app still runs and shows
headlines/categories -- briefs just fall back to a real excerpt pulled
straight from the source article instead of an AI-written summary. That
excerpt is decoded with the article page's own charset and stays **in the
article's original language**: briefs are the only part that translates, so
a Czech or Japanese story is only in English once a key writes its brief. The
brief card marks that text with a small "source excerpt" pill, and the Hot
News card shows a SOURCE EXCERPT chip, so it is never mistaken for an
AI-written brief.

**Note:** on Google's free tier, prompts may be used to help improve their
models (per their terms). Nothing sensitive is sent here -- just article
titles and short excerpts -- but worth knowing.

**Don't commit a real key to a public GitHub repo.** A key saved in Settings
never touches the source tree. If you edit `config.py` instead, add it to your
`.gitignore` before pushing, or use the environment variable above.

## 3. Run it

**Quick way (terminal):**
```bash
python app.py
```

**As a real installed-feeling app (recommended, one-time setup):**
```bash
bash install_launcher.sh
```
This registers SLATEWIRE in your Xfce application menu. After running it
once, search for "SLATEWIRE" in the Whisker Menu / app search and launch
it from there -- no terminal window, just the app window, like any other
installed program. (If you ever move or rename the unzipped folder, re-run
`install_launcher.sh` so the shortcut points to the new location.)

Either way: first launch fetches fresh news in the background (the window
opens right away rather than waiting on it); hit **Refresh** any time to
pull again -- unless you switch on Auto-refresh in Settings (every 15, 30 or
60 minutes).

## Same-story clustering

When several outlets pick up the same story, the feed shows one row instead of
three near-identical headlines. The row carries a small **"N sources"** chip
(the number of distinct outlets) and, where the page can name them, the Hot
News card and the brief list them under **"Also reported by"**. The
representative is the earliest published report, so the original write-up stays
at the top.

Nothing is deleted: every article is still in its category, and the data file
keeps the same shape with a few extra fields per article (`cluster_id`,
`is_representative`, `dupe_count`, `source_count`, `also_reported_by`). If you
want to see the underlying duplicates, turn on **Settings -> Feed & display ->
Show duplicate stories**; each duplicate row is tagged "duplicate".

The grouping is local and needs no API: a shared CVE id is the strongest match,
followed by a headline similarity score confirmed by the RSS summary, and
stories more than 72 hours apart are never merged. The trade-off is tuned for
precision -- a few cross-language or single-product stories stay separate
rather than risk merging two different events.

## Known exploited vulnerabilities (CISA KEV)

A CVE number in a headline is usually just a number. SLATEWIRE checks every
article against CISA's
[Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
-- the authoritative list of flaws that are already being used by attackers --
and marks matches with a **KNOWN EXPLOITED** tag. When CISA ties the flaw to
ransomware the tag reads **KNOWN EXPLOITED · RANSOMWARE**. Hot News adds
Exploited/Due rows to AT A GLANCE, and the brief card shows the matching CVE
ids, the date CISA added the flaw and its remediation due date.

The catalog is downloaded from CISA (public, no API key) and cached in
`kev_cache.json` for a day, so refreshes are cheap and the flag keeps working
offline with the last copy. If the download ever fails, the last good catalog
is used; if there is none, the rest of the app still works with no KEV flags.

## Search

The search bar (top right) has two speeds. While you type, it instantly filters
the headlines already on screen, so the list never feels laggy. A moment later
it runs a full-text search on the backend over the **title, description, AI
brief, archive note and source name**, including stories that have aged out of
the five-day feed and anything you saved in the archive.

Results are ranked by where the words match (a headline hit counts for much
more than a brief hit) with a small recency boost, and each row shows a snippet
from the matching field with the query words highlighted. A quoted phrase has
to appear inside a single field. Filters narrow the same search by category,
severity, source, date range, scope (feed + archive, feed only, archive only),
**Has CVE**, **Known exploited** and **Include duplicates** (duplicate rows are
hidden by default, matching the feed). Sources hidden in Settings are skipped
too; picking one in the Source filter still searches it.

Search is local and additive: no external service, no new data file, and no
schema change. It reads the existing `news_data.json`, `archive.json`,
`briefs_cache.json` and `settings.json` (for hidden sources) and never starts a
feed fetch.

Press **/** anywhere outside a text box or an open dialog to jump to the search
box, with the current query selected so you can replace it. The box remembers
the last eight queries you submitted with Enter (or whose result you opened) and
offers them in a drop-down when it is empty; **Clear** in that drop-down empties
the list. The filter row can also save named presets: set the filters you want,
type a name, and choose **Save filters**; pick it later from the **Saved preset**
menu. Saving again under the same name updates that preset, and **Delete**
removes the selected one. Changing a filter manually takes over until you pick
a preset again (the name stays in the field, so **Save filters** still updates
it). Recent queries and presets live in the browser's local storage (like the
theme), capped at eight searches and twelve presets, so no data file or schema
changes.

## Daily digest

The **Digest** tab turns the current feed into a one-page morning read. It
counts the stories in the chosen window (24 hours by default, from 1 hour to 7
days), breaks them down by severity, category and source, and lists the top
stories, the critical ones, and every known-exploited CVE with its vendor,
product, CISA add date and remediation due date. Duplicate rows and sources
hidden in Settings are skipped; both can be controlled, and the window and
top-story count are adjustable.

The summary paragraph is written locally from those counts, so the digest works
offline and with no API key. **AI summary** optionally sends just the counts,
top-story titles and CVE facts to the same Gemini key used for briefs and
returns a few plain sentences; the reply is cached in memory per digest. If
there is no key or the API fails, the local summary stays on screen and the
note says why.

The digest is additive: it reads the existing `news_data.json` and
`settings.json`, writes no new files and never starts a feed fetch. The
optional AI reply is not saved to disk.

## Source health

The **Sources** pop-up (click "N/M sources reachable" on the home page) still
shows the latest refresh, and now keeps a short history for every source in
`source_health.json`. Each row shows how many checks it has had, the rolling
success rate over the last 20, consecutive failures, the average response time
and when it last worked. A source that failed twice in a row is marked
**failing** (three in a row raises an alert flag), a feed that answers but has
no usable articles is **empty**, and a feed whose newest article is older than
21 days is **quiet** (shown in muted grey, not amber -- it is not failing, it
just has nothing new). Problem sources are listed first. Nothing extra is
fetched for this -- every refresh already reports the data. To tidy the list
without deleting anything, **Hide quiet** turns off every quiet feed in one go;
they stay in the pop-up with their switch off, and any can be turned back on.

The history survives restarts. **Reset health history** in the same pop-up
forgets it after a confirmation; the next refresh starts a new one. **Source
health alerts** in Settings -> Notifications is off by default; when switched
on, a single toast appears when a source reaches three failures in a row.

Source health is local and additive: no external service and no change to the
existing feed payload keys. It is calculated from the normal fetches and stored
only on this PC.

## Export

The download button beside the search bar (**Export**) saves whatever the page
is showing as a file: the current feed, one category (with its severity filter),
the archive, the current search with all its filters, or the current digest.
Pick **Markdown** for something readable, **CSV** for a spreadsheet, or
**JSON** for scripts and backups.

Each file carries context metadata: when it was exported, the app version, the
dataset, the row count and the filters that were active, so a file still makes
sense months later. Markdown keeps headings, links, briefs and your archive
notes; CSV repeats the same context columns on every row (with a UTF-8 BOM so
Excel opens it correctly); JSON wraps the rows in a metadata block. Only
`http(s)` links are written, Markdown inline markup is escaped, and CSV cells
that a spreadsheet could treat as a formula are prefixed with an apostrophe.

**Export & save** writes the file into `exports/` next to the app (the folder
is created on demand and old exports are never overwritten -- a second file in
the same second gets a `-2` suffix). **Open exports folder** shows the folder in
Explorer. The same content is also available as a download from
`GET /api/export?...`; nothing is uploaded and no network request is made.

## Version history

The current version is shown in the About popup (click the SLATEWIRE title)
and is set in `config.py`. The full list of changes is in `CHANGELOG.md`.
The notes below summarise what the current build does.

- **Export** -- save the feed, a category, the archive, a search or the digest
  as Markdown, CSV or JSON, with context metadata and safe filenames (see the
  Export section above).
- **Adaptive animation** -- the globe and the background network run from
  30 to 240 fps, chosen from the display's real refresh rate and the measured
  draw cost (a fast panel is used fully, a slow machine stays smooth). Motion
  is time-based, so the cap changes smoothness, not speed.
- **Custom sources and OPML** -- add any RSS/Atom feed from the Sources panel,
  or import a whole OPML subscription file. Custom feeds are fetched alongside
  the built-ins, can be hidden or removed again, and get a "custom" tag in the
  list. Pick a country already on the globe and its marker lights up; for a new
  country, give approximate coordinates and the globe adds a marker for it.
- **Source watchdog** -- a source that is permanently gone (HTTP 404/410, DNS
  failure, or a page that is no longer RSS/Atom) is retired after three
  consecutive hard failures instead of being retried forever. Timeouts, 403s
  and 5xx answers stay temporary. Retired feeds leave the fetch, the totals and
  the globe, but stay listed under "Retired" with their reason and a Restore
  button. A feed that simply has nothing new for 21 days is only "quiet": it is
  shown in muted grey, not as a failure, and **Hide quiet** turns all of them
  off at once without deleting anything.
- **(older note) 17 sources across 13 countries** -- added JPCERT/CC (Japan), WeLiveSecurity
  (Slovakia), Securelist (Russia), Security Affairs (Italy), Check Point
  Research (Israel), ASEC/AhnLab (South Korea), Bitdefender Labs (Romania),
  Avast Blog (Czech Republic) and SensePost (South Africa) alongside the original 8. All shown as markers on the
  globe, which now uses real simplified world-coastline data.
- **Notes on archived articles** -- add/edit/delete a personal note on
  anything you've saved, from the Archived tab.
- **Search** -- full-text search over titles, descriptions, briefs, notes and
  the archive, with filters and match highlighting (see the Search section
  above). The original headline-only filter is still the instant fast path.
  Press **/** to jump to the box, revisit the last eight queries from its
  drop-down, and save up to twelve named filter presets.
- **CVE detection** -- any CVE number in a headline or brief renders as a
  clickable badge linking straight to its NVD detail page.
- **Severity-aware sorting** -- articles now sort by severity first, then
  by real publish time within each severity band (previously sorted by
  raw date-string text, which silently ordered by weekday name).
- **Split brief lengths** -- the Hot News teaser (6-8 lines) and a full
  article brief (10-15 lines) are now two distinct prompts instead of
  reusing the same one.
- A rotating one-line quote, 15 in the pool, changing every 2 days.

## Known limitations worth knowing about before wiring in a real key

- **Check Point Research's feed URL** (`research.checkpoint.com/feed/`)
  wasn't independently confirmed the way the other 12 were -- if it shows
  up in the "unreachable sources" count, that's expected; swap in a
  working URL in `fetcher.py`'s `SOURCES` list, or drop it.
- The `/api/brief` and `/api/archive` endpoints now check that a submitted
  article `link` matches one from the last fetch, as a basic guard against
  the server being used to fetch arbitrary URLs. This is fine for a
  single-user local app; if this ever becomes a public-facing service,
  that check should be hardened further.
- **Source status (v1.5.4, history in v1.11.0, watchdog in v1.13.0):** click "N/M sources reachable" on the home page (it opens the Sources pop-up). Every source shows a dot -- green = fetched, amber = empty,
  grey = quiet (newest article older than 21 days), red = failed with the reason (HTTP 404,
  timed out, cannot connect, not valid RSS/Atom ...) -- plus how many articles came back
  and how old the newest one is. Since v1.11.0 it also keeps a history: success rate, failure
  streaks, average latency and the last time each source worked. Problem sources are listed first. The 36 government-CERT
  feeds added in v1.5.4 came from a public CERT registry and were NOT proven from the
  build sandbox: expect a few red ones on the first refresh, and remove them with the
  toggle (or delete the entry in fetcher.py). Items in non-English feeds show a language tag.
  "Hide quiet" turns off all quiet feeds at once without removing them.
  Since v1.13.0 a source that fails three refreshes in a row for a permanent reason
  (HTTP 404/410, DNS failure, or a page that is no longer a feed) is retired automatically:
  it is skipped, leaves the totals and the globe, and moves to a "Retired" section with a
  Restore button. Timeouts, 403s and 5xx answers stay temporary and never retire a feed, and
  restoring a source keeps its earlier history. Custom feeds added from the panel are tagged
  CUSTOM, can be hidden like any other source, and have their own Remove button.
- **Built-in limits (v1.5.3):** a feed is refused above 3 MB or after 20 s; an
  article page above 1.5 MB or 15 s; a request body above 1 MB; the archive
  holds at most 2000 articles (note 2000 characters); the briefs cache keeps
  the newest 500; titles are cut at 300 characters and links at 2000; at most
  12 sources are fetched at once. Only http(s) article links are kept.
- The backend only answers the app's own window: a request with another Host
  name, or a change request without the app's own header, is refused (403).
- Cookie-banner text in an article's excerpt is mitigated (the first two
  paragraphs of a page are skipped), not eliminated -- some sites still
  slip through.

## What gets stored on disk (all local, all yours)

- `news_data.json` -- the latest fetch (overwritten each refresh)
- `history.json` -- articles from today and yesterday (older ones are removed
  automatically; archived ones are kept separately in `archive.json`)
- `read.json` -- which articles you have opened for 5+ seconds (kept 14 days)
- `settings.json` -- your choices from the Settings popup (grouped into
  Appearance, Feed & display, Notifications and AI briefs)
- `%APPDATA%\SLATEWIRE\secrets.json` (Windows; `~/.config/SLATEWIRE/secrets.json`
  on Linux) -- only the optional Gemini key saved in Settings -> AI briefs. It
  lives outside the app folder, so an update cannot erase it, and Remove in
  Settings deletes it.
- `briefs_cache.json` -- briefs already generated, so re-opening an article
  doesn't re-call the API and re-spend tokens (placeholder/fallback text is
  no longer cached, so adding a key later gives you real briefs right away)
- `kev_cache.json` -- the last downloaded CISA KEV catalog (refreshed daily),
  so the "KNOWN EXPLOITED" flags work offline too
- `source_health.json` -- per-source check history (success rate, failure
  streaks, latency, last success/error); "Reset health history" in the Sources
  pop-up clears it
- `custom_sources.json` -- feeds you added yourself (typed in or imported from
  OPML); safe to edit or delete, the app rebuilds it from the Sources panel
- `archive.json` -- your saved articles and notes (source of truth);
  `archive.json.bak` is the previous good copy, restored automatically if
  `archive.json` is ever found damaged (the damaged file is kept as
  `archive.json.corrupt-<time>`)
- `archive.txt` -- the same archive, mirrored as plain readable text, updated
  automatically any time you archive/remove something or edit a note
- `exports/` -- files you saved with the Export button (Markdown, CSV or JSON);
  safe to delete any time
- Browser local storage -- the theme choice, the last eight recent searches and
  up to twelve saved search presets (no personal data beyond the queries you
  chose to search)

Nothing here is sent anywhere except the calls described below.

## What actually talks to the network

- **Fetching**: direct HTTP GET requests to each news source's public RSS
  feed -- reads text only (titles, short summaries, links, dates), never
  executes or downloads anything from them.
- **Briefs**: one request per article to Google's Gemini API
  (`generativelanguage.googleapis.com`), sending only that article's title +
  source excerpt. No browsing history, no personal data, no memory between
  requests.
- **KEV catalog**: once a day, a plain GET for CISA's public
  `known_exploited_vulnerabilities.json`. No account, no data sent -- only the
  catalog is downloaded.
- **Search**: none. Queries are answered locally by the backend from data
  already on disk; no query text leaves the machine.
- **Digest**: none by default. The structured digest and its summary paragraph
  are built locally. Pressing **AI summary** makes one Gemini request with the
  digest's counts, top-story titles and CVE facts -- the same key and provider
  as briefs -- and the reply is cached in memory only.
- **Source health**: none. Success rates, streaks and latencies are calculated
  from the normal feed fetches and saved only in `source_health.json`.
- **Export**: none. Markdown, CSV and JSON files are built and written locally;
  nothing is uploaded.

## If a source won't load

Feed URLs occasionally change. If `errors` shows a source name repeatedly,
open `fetcher.py`, find it in `SOURCES`, and swap in a current feed URL for
that outlet -- everything else (classification, severity, briefs, archive)
is unaffected by which sources are listed. If you add or remove a source,
also update the `MAP_SOURCES` array near the top of the globe script in
`static/dashboard.html` so the source-map markers and legend stay in sync.
Removing the last source for a country retires that country's marker and
legend entry automatically; `tests/test_sources.py` fails if the two lists
drift apart.

## Credits

The globe's coastline clipping uses [d3-geo](https://github.com/d3/d3-geo) (ISC license), bundled locally as
`static/d3-geo.min.js` so the app works offline. Coastline data is simplified from Natural Earth (public domain).

## Windows

See README_WINDOWS.txt in the folder above this one (INSTALL.bat, then SLATEWIRE.bat or the Desktop shortcut).
