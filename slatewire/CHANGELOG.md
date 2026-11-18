# SLATEWIRE changelog

Version numbers follow `MAJOR.MINOR.PATCH`. A round of new features raises the
MINOR number, a bug-fix-only round raises PATCH. The current version lives in
one place: `VERSION` in `config.py` (it is shown in the About popup).

## v1.13.0 -- Source watchdog, custom feeds, search polish and a live animation readout
- A source that fails three refreshes in a row for a permanent reason (HTTP 404/410, DNS resolution failure,
  or a page that is no longer RSS/Atom) is retired automatically instead of being retried forever. Timeouts,
  HTTP 403 and 5xx answers are treated as temporary and never retire a feed.
- Retired feeds are skipped by every fetch and by the background notifier (so they do not slow refreshes
  down), and they no longer count toward the source totals or the globe. They stay visible at the bottom of
  the Sources pop-up under "Retired", with the last reason and a Restore button.
- Restoring clears the retirement and the hard-failure count but keeps the normal history (attempts, success
  rate, latency), then refreshes immediately so the feed is tried again. New `POST /api/sources/revive`.
- Both one-shot notification flags are cleared at the start of every refresh, so a retired feed cannot
  re-toast on every page load. Retirement sends one notification (it honours the master notifications
  switch); the opt-in failure-streak toast is unchanged.
- New `custom_sources.py` (pure module) plus `custom_sources.json`: add any RSS/Atom feed from the Sources
  panel, or import an OPML file (nested folders, namespaces and duplicate URLs handled). Custom feeds are
  validated (http/https, unique name and URL, optional 2-3 letter country, latitude/longitude, language),
  capped at 60, fetched alongside the built-ins and removed with a button that also clears their history and
  hidden flag. Endpoints: `GET/POST /api/sources/custom`, `POST /api/sources/custom/import`,
  `POST /api/sources/custom/remove`.
- The globe now takes its markers from the live source map: a custom feed in an existing country lights that
  marker, and a new country with coordinates gets a marker and legend entry without editing `MAP_SOURCES`.
  Custom rows carry a "custom" tag in the Sources panel.
- Settings -> Diagnostics has a new "Animation" row with a live readout from the frame governor: the current
  fps cap, the panel's measured refresh rate, and the frames per second and draw cost of the globe and
  background loops (previously only visible in DevTools). The values are sampled while the panel is open and
  the timer stops when it closes; reduced-motion mode says so instead.
- Search polish: `/` focuses the search box from anywhere outside a text field or an open dialog (the current
  query is selected); the box remembers the last eight queries submitted with Enter or whose result you opened
  and lists them in a drop-down when empty, with a Clear action; and the filter row can save up to twelve named
  presets (Save filters / Saved preset / Delete) that restore every filter, including the checkboxes. Recent
  queries and presets live in localStorage, like the theme, so there is no new data file or settings key.
- Quiet feeds: the "may be dead" wording is gone. A feed with no new articles for 21 days is shown as
  "quiet for 21+ days" in muted grey instead of amber -- it is not a failure. The Sources summary counts it as
  quiet rather than stale. A new **Hide quiet** button turns off every quiet feed at once; nothing is deleted,
  the rows stay in the pop-up with their switch off, and any can be turned back on.
- New tests cover the hard/soft failure split, the retirement threshold, one-shot flags, revive, the fetch
  skip, custom-source validation, OPML parsing, add/merge/remove and `fetcher.all_sources()`. Total suite is
  168 tests, all passing.
- Version: v1.12.1 -> v1.13.0.

### Fixed in the rebuilt v1.13.0 package
- A dashboard opened in a normal browser (or left over from an earlier run) is no longer mistaken for the app
  window. Its heartbeats are counted separately in the report and can no longer overwrite the window's
  snapshot, double the heartbeat count or make the session log look like two apps. Each app-window heartbeat
  now carries a short page id in the log.
- The launch readiness probe uses a new tiny `GET /api/ping` instead of downloading the whole dashboard, so
  the report no longer shows a second `GET /` that never happened in the window.
- Click events in the report name the button's label (`aria-label`/`title`) and the panel it sits in, so an
  anonymous `click button` is now e.g. `click button "Settings" in #settings-panel .ghost`.
- A clean shutdown writes a "clean" flag into the running marker before deleting it, so a locked marker (for
  example antivirus holding the file) cannot produce a false "the previous run ended unexpectedly" warning.
- Four new tests cover the page/heartbeat separation. Total suite is 168 tests, all passing.

### Smoother background animation (same rebuilt v1.13.0 package)
- The network background is redrawn the same way but at a fraction of the cost: each dot is a pre-made
  glow sprite (one `drawImage`) instead of an arc with a `shadowBlur`, and connection lines are batched into
  eight brightness buckets (about 16 strokes a frame) instead of a separate stroke per line -- the old code
  could issue several hundred draw calls per frame. On the test machine the background loop's draw cost went
  from several milliseconds a frame to about 0.1-0.2 ms.
- The pointer light now eases toward the cursor between mouse events and fades out when the pointer leaves
  the window, so lines no longer snap to every mousemove; the attraction also tapers off near its radius
  instead of switching off at the edge.
- Theme colours are read when the theme actually changes (a `MutationObserver`) instead of every 60th frame,
  removing a periodic style recalc that showed up as a hitch.
- The canvas now follows the display's pixel density (up to 2x, capped at 3.2 megapixels), so dots and lines
  are sharp instead of being upscaled from CSS resolution; very wide screens still draw at reduced resolution.
- Frame governor: when the adaptive cap sits below the panel's refresh, it now snaps to an exact divisor of
  the measured rate when that stays within 20% of the cap (so a 165 Hz panel does not halve to 82.5 fps for
  the 144 tier, and skips are evenly spaced rather than beating), and otherwise keeps the cap as chosen.
  Settings -> Diagnostics -> Animation shows the effective rate.
- The look is unchanged: same dot counts, link distances, colours and twinkle; reduced-motion, idle and
  unfocused behaviour is the same.

### A 3D background, and briefs that only contain news text
- The background is now a depth field instead of a flat drift. Every node sits at a random distance from a
  virtual camera: near nodes are drawn larger and brighter and swing further when the camera moves, far ones
  are smaller and dimmer and barely move. The camera leans toward the pointer and drifts on its own when the
  pointer is still, so the parallax is visible without touching anything; nodes creep slowly toward the camera
  and fade in and out at the ends of the depth range instead of popping.
- Density went from one node per 17,000 px to one per 5,800 px, depth now spans 1,200 world units, and every
  dot is drawn at exactly one sprite pixel per device pixel. The dots are smaller than the previous build's --
  the largest core is about 5 px across instead of 8, and the glow around it is tighter -- and near nodes pick a
  larger sprite rather than being upscaled (upscaling is what made lit dots look blurred before). The nearest
  few are drawn as soft out-of-focus discs, the way a lens renders a close point of light.
- Four cues carry the depth: near nodes are larger and brighter, they sit further from the centre of the screen
  (perspective spread), they travel faster across it, and the camera swings wide toward the pointer, so the near
  plane visibly slides against the far one. A slow dolly breathes the whole field toward and away from the
  camera, so a still scene keeps moving. The camera drift was previously slow enough (a ~57 s cycle) that the
  parallax was easy to miss.
- The mesh is now sparse on purpose: links only form between nodes at nearly the same depth, only in the front
  half of the field, and each node takes part in at most one -- a few constellations against a background of
  loose dust. That separation is what reads as depth; a link through every neighbour flattened the field into a
  screen of wire. On the light themes the whole field is dimmed, so the same alpha does not look like scribble
  on cream.
- The field covers the whole frame. The projection pulls far nodes toward the centre, so an even scatter leaves
  the outer band empty and the window looks like it fades out before its own edge -- the corners worst of all,
  because only a node close to both edges lands there. Base positions are now laid out over a plane 1/0.88
  wider and taller than the window, and a quarter of the nodes are aimed at a target on (or just inside) an edge
  or corner and placed so the projection lands them there at their own depth. They drift outward through the
  edge as they approach and are replaced on respawn, so every edge and corner stays populated and the rim no
  longer reads as a soft vignette.
- The About panel (click the SLATEWIRE wordmark) shows the version as a chip beside the title instead of a faint
  line underneath it, and the version is now in the window title and the page title too.
- The field now rests. Six seconds after the last pointer, key or scroll movement the links fade out over the
  next twelve seconds, the pointer light goes with them and the camera settles into its own drift, leaving a
  quiet starfield; any input brings the mesh back over about a third of a second. The previous build kept the
  lit blob and the full mesh sitting on screen for as long as the window was open.
- The background now runs at a steady 60 fps whenever the window is focused, idle included -- which is when it
  is actually being watched -- and 30 fps when it is not. It never asks for more than the governor's cap, so a
  machine it has downgraded is not pushed. The canvas is opaque and filled with the theme background, which is
  cheaper to composite than a translucent full-screen layer and keeps the dots at full contrast.
- Resizing keeps the field it already has (nodes are pulled back into the new bounds) instead of re-rolling
  every node, and the pointer light snaps to the cursor on first movement instead of crawling in from off-screen.
- Briefs no longer pick up JavaScript: `script`, `style`, `noscript`, `template`, `svg`, `iframe`, `form` and
  `head` blocks plus HTML comments are stripped before paragraphs are matched, and a paragraph that still looks
  like code (script source, leftover markup, a shell transcript) is dropped. That is what used to put raw page
  JavaScript into a "source excerpt" when no API key was configured. The same filter guards the RSS-description
  fallback, which now falls back to the headline rather than showing code.
- A link in a brief is now a short clickable anchor (host plus trimmed path, full URL in the tooltip) that opens
  in a new tab and does not open the article card underneath it. CVE ids and links are found in one pass, so an
  address that contains a CVE id stays one address instead of being broken by a badge.
- Ten new tests cover the extractor (script/style/noscript/comment blocks, code paragraphs, real prose that
  mentions a document or contains a link). Total suite is 178 tests, all passing.

### Diagnostics removed for release
- Removed the diagnostics tool in full: the Settings -> Diagnostics group (report, note, folder and delete
  buttons plus the animation readout), the `/api/diag/*` backend routes and the `diagnostics.py` module, the
  page-side recorder, the `diagnostics` settings key and `tests/test_diagnostics.py`. The app no longer writes
  a `diagnostics` folder; the background notifier's log moved to `logs/slatewire-notifier.log`.
- Behaviour that mattered elsewhere is kept: the idle detection that lets the globe drop to 30 fps and the
  background hold 60 fps is now part of the frame governor itself (15 s without pointer, key or scroll input),
  and the notifier still records that the window is open and last active.
- Notifications: the "Send test notification" button (and its `POST /api/notify/test` route) is gone -- it was a
  support tool, not a feature.
- The background checker no longer needs `ENABLE-NOTIFICATIONS.bat`. Settings -> Notifications has a **Check news
  in the background** switch that creates or removes the Windows scheduled task directly (`POST /api/notify/task`),
  and its position is read live from Windows (`schtasks /Query`) rather than from a saved setting, so it cannot
  show a stale state. A per-user task needs no administrator rights; where group policy or antivirus blocks it,
  the row shows the real error and the .bat files stay as a manual fallback. The **Notification pop-ups** switch
  (the old master toggle, renamed) still controls the toasts themselves. The task is the same action
  `enable_notifications.ps1` registers: every 30 minutes, only while you are logged in, nothing while the app is
  open. New tests cover the task command, path quoting and the refusal/removal paths. Total suite is 185 tests,
  all passing.
- AI briefs now default to Google's Flash-Lite model (`BRIEF_MODEL = "gemini-flash-lite-latest"`) with an
  automatic fallback model. The full `gemini-flash-latest` model is currently capacity-limited on the free
  tier (HTTP 503 or no answer before the read timeout), which surfaced in the app as "Could not generate a
  brief right now (ReadTimeout)". Lite returns a 6-15 line brief in a few seconds on the same free key; the
  model and fallback are both configurable in `config.py`.
- Items with no article text -- podcasts such as SANS Stormcast, and pages that only serve a copyright line --
  now show one clean line ("No written summary is available for this item...") instead of sending an empty
  source to Gemini. That used to come back as "The provided article text is empty and contains no news
  content..." and was cached as a real brief. Replies that talk about the prompt are now rejected, and any
  such brief already in `briefs_cache.json` is discarded and regenerated.
- Brief text now flows as a normal paragraph. The one-sentence-per-line formatting no longer leaves a ragged
  column with empty space on the right; the full card width is used, in the article card, the Hot News
  teaser and the daily digest narrative.
- New tests cover the prompt-commentary filter, licence-only source text and podcast detection. The suite is
  185 tests, all passing.
- Three feeds that had stopped working were removed from the built-in list: **Fox-IT** (the blog feed now
  returns HTTP 404, so the watchdog had retired it), **AusCERT Bulletins** (the feed grew past the 3 MB fetch
  limit and failed every refresh) and **SK-CERT** (HTTP 522 from Cloudflare on every attempt, ~20 s each).
  The list is 41 sources across 30 countries; Australia's globe marker and legend entry went with its last
  feed. Source history for a feed that no longer exists is also no longer shown in the Sources panel.
- The supplied mint-on-navy "S" logo is now the installed app icon. A nine-frame `slatewire.ico` (16, 20, 24,
  32, 40, 48, 64, 128 and 256 px) ships in the app folder; the window passes it to pywebview for the title bar
  and Alt-Tab, and INSTALL.bat creates the desktop shortcut with `IconLocation` pointing at it. Windows 11
  draws the taskbar button from the AppUserModelID rather than the window icon, so the app registers its
  per-user identity (`SLATEWIRE`, the same one the notifier already uses for toasts) with `IconUri`; that is a
  normal HKCU key, no administrator rights. The icon is used only outside the page -- the dashboard never
  references it and the favicon is unchanged. New tests check the .ico frame table and that the page does not
  embed the icon. The suite is 185 tests, all passing.

### The Gemini key survives updates (Settings -> AI briefs)
- The optional Gemini API key can now be saved in **Settings -> AI briefs** instead of editing `config.py`.
  Save stores it in `%APPDATA%\SLATEWIRE\secrets.json` (`~/.config/SLATEWIRE/secrets.json` on Linux), outside
  the app folder, so replacing the folder during an update no longer erases it. Remove deletes it; the app
  then falls back to `config.py` or the `SLATEWIRE_GEMINI_API_KEY` environment variable.
- The key is never returned to the page. `GET /api/gemini-key` reports only whether one is configured and
  where it comes from (`settings`, `config` or none); `POST /api/gemini-key` saves (`{"key": "..."}`) or
  forgets (`{"clear": true}`) it. Shape validation rejects empty, too short, too long and whitespace-containing
  values before anything is written.
- Briefs and the digest read the effective key on every request, so a saved key works immediately without
  restarting the app. New `tests/test_secrets_store.py` covers save/load/clear, the Settings > config
  precedence, malformed stored values and both endpoints. Total suite is 197 tests, all passing.

### Correct text for non-English sources, and a clearer keyless state
- Source excerpts now decode a page with the charset the page actually uses. The HTTP client labels any
  `text/*` response without a charset as ISO-8859-1, so a UTF-8 page that declares itself only in
  `<meta charset="utf-8">` was decoded as Latin-1: Czech "Členové" arrived as "ÄlženovÃ©". The new
  `_decode_page()` prefers a specific header charset, then the page's `<meta>` declaration, then UTF-8, and
  only then falls back to Latin-1/cp1252, so a genuinely Latin-1 page still reads correctly.
- Without a key, the excerpt stays in the article's original language and the UI now says so: the Settings
  status line and both "source excerpt" tooltips explain that text comes from the source, not from an AI
  summary. Only an AI brief translates a non-English article into English.
- The static "Optional. Create a free key at aistudio.google.com/apikey..." hint is gone from Settings; that
  guidance now lives in `README.md`, `slatewire/README.md` and `README_WINDOWS.txt`. Settings keeps only the
  dynamic status line, so it never nags after a key is saved.
- Six new encoding tests cover a UTF-8 page labelled ISO-8859-1, `<meta charset>` winning over the header
  default, windows-1250 both by header and by meta, a page with no charset at all, and a true Latin-1 page.
  Total suite is 203 tests, all passing.

## v1.12.1 -- Adaptive animation (30-240 fps) and a cleaner source list
- Replaced the fixed 60 fps animation cap with a small shared governor (`SWPace` in `dashboard.html`). Both
  animated layers (the globe and the background network) now pick a cap from `240, 180, 165, 144, 120, 90, 75,
  60, 45, 30` fps, starting from a hardware guess and correcting once a second from two measurements: the median
  raw `requestAnimationFrame` interval (the display/load ceiling) and the summed per-loop draw cost with ~35%
  headroom. It drops a tier immediately and climbs one tier every 1.8 s, so it does not oscillate.
- A high-refresh panel is now used properly (165 fps measured on a 165 Hz screen; 240 is reachable on a 240 Hz
  screen). A 60 Hz panel still tops out at 60 because the browser cannot deliver more frames than the display.
  Unfocused or idle stays near 30 fps, hidden windows pause, and reduced-motion keeps the globe static at 30 fps.
- All motion is time-based, so a higher cap is smoother, not faster: globe rotation, satellites, label hold/fade,
  and the background network's damping, repulsion, impulses and twinkle phase are normalized per frame interval.
- Removed four feeds that were permanently unreachable from this machine: Lab52 (host offline), CCN-CERT, CERT-IL
  and CSIRT Italia / ACN (HTTP 403 on every path, including their homepages). The list is 44 sources across 31
  countries; a live refresh reports 44/44 reachable.
- The globe now retires a country automatically when its last configured source is removed: the marker and the
  legend entry both disappear, while a country whose sources are merely hidden still shows as struck through.
- New `tests/test_sources.py` keeps `fetcher.SOURCES` and the globe's `MAP_SOURCES` in sync. Total suite is 138
  tests, all passing.
- Live check: 165 fps measured on this panel for both loops (cost ~0.7-0.9 ms globe, ~0.05-0.1 ms background),
  stable over seven samples; source list and legend verified before and after a real 44-source refresh; no console
  errors.
- Version: v1.12.0 -> v1.12.1.

## v1.12.0 -- Export: feed, category, archive, search and digest as Markdown, CSV or JSON
- New `exporter.py` (pure module, no Flask, no disk): turns a list of articles or a digest into a file with context
  metadata and a safe, timestamped filename. `article_row` flattens one article, `select_articles` applies the feed's
  category/severity/source/date/CVE/KEV/duplicate rules, `render_articles` and `render_digest` produce the three formats,
  and `build_filename` keeps the name readable and path-free.
- New `GET /api/export`: the same export as a download (`Content-Disposition: attachment`). Parameters are `dataset`
  (`feed`, `category`, `archive`, `search`, `digest`), `format` (`md`/`markdown`, `csv`, `json`), the shared filters
  (`category`, `severity`, `source`, `days`, `has_cve`, `kev`, `include_dupes`), search `q`/`scope`, and digest
  `hours`/`top`. Bad input returns a clear 400; nothing is fetched and no existing file is changed.
- New `POST /api/export/save`: writes the file into `exports/` next to the app (created on demand) and returns its path,
  filename, size and row count. A second export in the same second gets a `-2` suffix instead of overwriting the first.
  New `POST /api/export/open-folder` opens that folder in Explorer. Both POSTs use the existing app-header guard.
- New **Export** button beside the search bar: it detects the current view (feed with the severity filter, one category,
  the archive, the active search with all its filters, or the digest with its window/top/duplicate settings), shows a
  one-line description of what will be saved, and offers Markdown, CSV and JSON. **Export & save** reports the saved
  filename and size; **Open exports folder** reveals the files. The panel supports Escape, the backdrop and the existing
  focus trap like the other popups.
- Format details: Markdown escapes inline markup and keeps headings, links, briefs and archive notes; CSV repeats the
  context columns on every row, starts with a UTF-8 BOM for Excel, and prefixes cells that could be read as formulas
  (`=`, `+`, `-`, `@`, tab, carriage return) with an apostrophe; JSON is plain data (`metadata` plus `articles`, or
  `metadata` plus `digest`). Only `http(s)` links are written. Digest CSV contains the ranked top stories.
- `exports/` is ignored by git; the README documents the feature, the stored folder and the fact that exports make no
  network request.
- Tests: new `tests/test_export.py` (27 tests) covers format aliases, article rows and unsafe links, every selection
  filter (including hidden sources and duplicates), filename safety, Markdown escaping and metadata, CSV headers, BOM
  and formula-injection protection, JSON shape, digest Markdown/CSV/JSON and empty inputs. Total suite is now 135 tests,
  all passing.
- Live check with deterministic data: exported the feed (6 rows, one duplicate hidden), a category, the archive
  (2 saved articles with notes), a search scoped to the archive, and the digest as JSON; verified the context line
  changes with the view and search filter, saved filenames and collisions, the CSV BOM/header, correct content types,
  the header guard, the folder opener and a clean browser console.
- Version: v1.11.0 -> v1.12.0.

## v1.11.0 -- Source health: history, failure streaks, success rate and latency
- New `source_health.py` (pure module, no Flask): each refresh records one attempt per source in a small rolling
  history -- total checks, successes, consecutive failures, the last 20 outcomes (success rate), the last 10 successful
  latencies (average) and the last success/error times. Feeds that answer but return nothing are "empty"; feeds whose newest
  article is older than 21 days are "stale"; two or more failures in a row become "failing".
- New `source_health.json`, updated by every real fetch (not by the read-only page load). The stored shape is a flat
  `{source name: record}` mapping, clamped to 200 sources, 20 outcomes and 10 latencies; damaged or hand-edited values are
  cleaned rather than trusted. A failed history write never breaks a fetch.
- The `/api/news` and `/api/fetch` payloads gain an additive `source_health` list (the same fields the panel uses), so the
  home page can show a "N failing" count without a second request. Existing payload keys are unchanged.
- New read-only `GET /api/sources/health`: merges the saved history with the latest refresh snapshot and returns
  per-source derived fields plus summary counts. New `POST /api/sources/health/clear` forgets the history; the next refresh
  starts a new one. No existing file or schema is changed.
- Sources panel: every row now shows `<success rate>% of N checks`, failures in a row, average response time and the last
  time the source worked, on a second line under the existing article-count/reason line. The header adds a health summary
  (healthy / failing repeatedly / failed last time / empty / stale, and how many refreshes are tracked), the stats line shows
  "N failing", and failing sources sort first. A **Reset health history** button clears the saved file after a confirmation.
- Optional **Source health alerts** (Settings -> Notifications, off by default): one toast when a source reaches three
  failures in a row. It fires only on the refresh that crosses the threshold, not on every later failure.
- Tests: new `tests/test_source_health.py` (20 tests) covers first success, streak transitions and the one-shot alert,
  recovery, empty feeds, stale boundaries, the rolling success window, latency averaging/capping, junk JSON, view ordering
  and overrides, summary counts, and the fetcher's save-across-refreshes path. Total suite is now 108 tests, all passing.
- Live check with deterministic data: two sources healthy, one empty, one failing 3x in a row and one stale 30 days.
  Verified the stats-line alert, every health line, problem-first ordering, reset (cancel keeps history, accept clears to one
  check), the Settings toggle persisting to `settings.json`, the toast helper (one-shot, disabled by default) and a clean
  browser console.
- Version: v1.10.0 -> v1.11.0.

## v1.10.0 -- Daily digest: a local summary of the last N hours of the feed
- New `digest.py` (pure module, no Flask): builds a one-page digest from the live feed -- headline counts (total, critical/high/routine, known-exploited, distinct CVEs), per-category and per-source counts, the top stories, the critical ones, and one row per distinct known-exploited CVE. Windows default to 24 hours and can be 1 hour to 7 days; sources hidden in Settings and duplicate rows are skipped by default, and the digest never includes stories without a usable date.
- New `articles.py`: the small date/text helpers shared by search and digest (`article_ts`, `source_count`, `text`, `norm`), so the two modules cannot drift apart.
- New read-only `GET /api/digest`: reads `news_data.json` and `settings.json`, validates/clamps `hours` and `top`, and never triggers a feed fetch. No schema or stored-file changes.
- Optional `POST /api/digest/ai`: sends only the digest's own counts and top-story titles to the existing Gemini plumbing and returns a short plain-English paragraph. Responses are cached in memory by digest fingerprint, so repeated clicks are free; with no API key (or on any API error) it falls back to the local narrative and says why. The structured digest and its local narrative always work offline.
- UI: new **Digest** tab with a window picker, top-story count, an Include duplicates toggle, headline stat tiles, three columns (Top stories, Critical, Known exploited), category/source coverage chips, and an **AI summary** button. Rows open the normal brief card with KEV/source/archive badges; clicking a CVE opens its NVD page. Typing a search from the Digest tab switches to the results view, and a new fetch invalidates the cached digest.
- Tests: `tests/test_digest.py` covers the shared helpers, window/undated handling, clamping, hidden sources, duplicates, severity/KEV/CVE counts, ranking tie-breaks, exploited-CVE merging, summaries and the narrative. Total suite is now 88 tests, all passing.
- Live check with deterministic data: digest counts and ordering, 24h vs 48h windows, duplicate toggle, top-3 limit, hidden-source exclusion, AI fallback note, opening a KEV story, and searching from the Digest tab. Browser console clean.

## v1.9.0 -- Search v2: full-text search across the feed and archive, with filters
- New `search.py` (pure module, no Flask): searches title, description, AI brief, archive note and source name, so a story is
  findable after it ages out of the five-day feed or is saved in the archive. Live and archived copies of the same id are merged
  into one result, and an archived note or brief is searchable alongside the current feed text.
- Matching: every query word must appear (AND), quoted phrases stay together and have to match inside one field (they cannot span
  the title and description), case-insensitive. Ranking weights where the match is (title > description > brief/note > source),
  adds a whole-query phrase bonus and a small recency boost, then uses publish time and id for deterministic ties. Repeated
  query words are collapsed, so they neither weight a result twice nor crowd out other terms. Each result carries a snippet from
  the best matching body field plus `matched_fields`.
- Filters: category, severity, source, date range (`days`), scope (`all` / `live` / `archive`), `has_cve`, `kev` and
  `include_dupes` (duplicate rows are hidden by default, exactly like the feed). Sources hidden in Settings are skipped too,
  unless the Source filter names one explicitly. Facet counts come back per category, severity and source for the filtered
  result set. `limit` defaults to 50 and is capped at 200; an empty query returns no results.
- New read-only `GET /api/search`: reads `news_data.json`, `archive.json`, `briefs_cache.json` and `settings.json` directly and
  never triggers a feed fetch. Parameters are clamped/validated; no schema or stored-file changes.
- UI: the search bar keeps the instant headline filter as a fast path, then debounces (250 ms) into the backend search. Results
  show highlighted query words in headline and snippet, the matching field label (source excerpt / brief / note), severity,
  source, category, KEV/source/archived badges, and a `shown of total` count. A filter row offers category, severity, source,
  time, scope, Known exploited, Has CVE and Include duplicates; stale responses are dropped by a request token.
- Review pass after the first build: quoted phrases now get the phrase-ranking bonus; snippets highlight escaped text without
  splitting HTML entities (`&#39;`, `&amp;`); searching from the Archived tab switches to the results view instead of staying
  hidden; the inert feed severity chips are hidden while searching.
- Tests: `tests/test_search.py` (32 tests) covers matching (title/description/brief/note/source, case, AND, quoted phrases and
  field boundaries, repeated words), live+archive merge, archived duplicates, ranking/tie-breaks, every filter (including
  hidden sources), scope, limit clamping, snippets and facets. Total suite is now 60 tests, all passing.
- Live check: 119-article feed + a real archived article. Verified title/description/brief/note search, archive-only scope,
  duplicate toggle (2 -> 7 rows for "SonicWall"), KEV and Has CVE filters, date filtering, the empty state, highlighted
  snippets, and opening a result (KEV badge + `CVE-2026-19868 · added · due` line). Browser console clean. Re-checked after the
  review pass with deterministic data: hidden source excluded by default and found when picked explicitly, entity-safe snippet
  highlighting, archive-tab search, and the full endpoint parameter matrix.
- Version: v1.8.0 -> v1.9.0.

## v1.8.0 -- CISA KEV enrichment: "KNOWN EXPLOITED" flag, ransomware and due dates
- New `kev.py`: every article is checked against CISA's Known Exploited Vulnerabilities catalog, so a CVE mention becomes "this
  flaw is already being exploited" instead of just a number. The catalog is public (no API key) and cached in `kev_cache.json`
  for a day; when the fetch shows the PC is offline the cached copy is used and no extra request is made. A failed download
  degrades to the last good copy, and a missing catalog never blocks a fetch.
- Additive article fields: `kev` (bool), `kev_cves` (matched ids), `kev_details` (vendor/product/name/dateAdded/dueDate/
  ransomware per match) and `kev_ransomware`. The payload gains a top-level `kev` stats block (catalog size, matched articles/
  CVEs, ransomware count, ok/stale). `categories` keeps its shape.
- UI: rows and search results show a "KNOWN EXPLOITED" tag (with "· RANSOMWARE" when CISA ties the flaw to ransomware); Hot News
  gets a KEV pill plus Exploited/Due rows in AT A GLANCE; the brief card shows the badge and a line with the CVE ids, catalog
  date added and remediation due date.
- Same-story flag: if any article in a same-story cluster names a KEV CVE, the visible representative is flagged too, because
  the representative often does not spell the CVE out itself (the live SonicWall story is exactly this case).
- Clustering precision fix found during the live check: three unrelated Slovak advisories merged because "kritické
  zraniteľnosti" counted as shared content. Slovak/Czech/Polish/French/Spanish/Italian/Dutch advisory boilerplate is now
  stripped before scoring, and the three advisories stay separate.
- Tests: `tests/test_kev.py` (15 tests) covers title/description matching, case handling, ransomware, multiple/unknown CVEs,
  cluster propagation, category stats and cache behaviour (download once, stale fallback, offline mode, no-cache failure).
  All network calls are stubbed.
- Live check: catalog loaded at 1,769 CVEs; a real 113-article fetch matched 4 articles (SonicWall CVE-2026-19868 ×2,
  Cisco CVE-2023-20198, PHP CVE-2026-109618/109619), 5 articles flagged after cluster propagation. Verified in the page:
  row tag, Hot News pill + Exploited/Due rows, brief badge + "CVE · added · due" line; console clean.
- Version: v1.7.0 -> v1.8.0.

## v1.7.0 -- Same-story clustering (dedupe v1): one row per story, "N sources", "Also reported by"
- New `dedupe.py`: after classification and the per-day cap, articles that describe the same story are grouped so the feed shows one
  representative row instead of three or four near-identical headlines. Nothing is deleted -- every article stays in its category and the
  existing JSON shape is unchanged; the stage only adds fields.
- Signals (no external services): a shared CVE id is the strongest match, then a blended TF-IDF cosine + token-Jaccard score of the
  headline, confirmed by the RSS summary when the headline was reworded; articles more than 72 hours apart are never merged. Feed
  boilerplate ("Security Alert (A26-...)"), newsletter/podcast titles, dates and generic breach words are stripped before scoring, with
  accents folded so German/French/Spanish feeds compare correctly. At least two real headline words must be shared, which keeps
  single-brand advisory templates apart (the measured precision-first trade-off).
- Representative = earliest published, then the most complete description, then source/id, so the original report stays at the top. New
  additive article fields: `cluster_id`, `is_representative`, `dupe_count`, `source_count`, `also_reported_by`. The payload also gains a
  top-level `clusters` map and a `dedupe` stats block; `categories` keeps its shape.
- UI: list and search rows show an "N sources" chip (distinct outlets) and a small "duplicate" tag when a non-representative is shown;
  Hot News picks a representative and lists "Also reported by" in AT A GLANCE; the brief card shows "N sources" and an "Also reported by"
  line. Settings -> Feed & display gains "Show duplicate stories" (default off) to reveal every underlying article.
- Validation: `tests/test_dedupe.py` (13 tests) plus a hand-labelled real fetch (`tests/validation_pairs.json`, 103 articles / 32 pairs).
  On that corpus the tuned gate scores precision 1.00 and recall 0.69; the misses are deliberate single-entity/cross-language cases
  (D-Link DGS-3130, GovCERT OpenSSH alert, freezer hacks, Zyxel, Android rollup) kept to protect precision.
- Version: v1.6.0 -> v1.7.0.

## v1.6.0 -- Polish pass: skeleton + live fetch progress, cleaner fallback briefs, one-row toolbar, grouped Settings, accessibility 100
- First load and Refresh: the page paints a skeleton (category tiles + Hot News placeholder) immediately instead of one "Loading..." line, and a line under
  the tabs shows real progress ("Fetching feeds... 18/48 sources") polled from the new `GET /api/fetch/status` (`fetcher.progress()`).
  Refresh keeps the current articles on screen and disables the button ("Refreshing...", `aria-busy`) until the new data arrives. If it fails, the last
  fetch stays visible and "Couldn't refresh -- showing the last fetch" appears for 9 s instead of blanking the page.
- No-key briefs: the long "[No Gemini API key configured...]" paragraph is gone. `/api/brief` now returns `{"brief", "ai"}`; the card labels a non-AI
  result with a small "source excerpt" pill and the Hot News card shows a "SOURCE EXCERPT" chip. Excerpts are still never cached, so adding a key later
  still produces a real brief immediately.
- Excerpt quality (`article_text.py`): HTML entities are decoded (`&nbsp;` no longer appears literally), whitespace before punctuation is removed, sentences
  are not split after "U.S." / "e.g." / "etc.", and a page that opens with a "latest news" list no longer feeds those headlines into the brief -- the
  longest contiguous run of body-like paragraphs (long, or ending in punctuation) is used instead. Checked against Security Affairs, The Hacker News,
  BleepingComputer, heise and The Register; pages without body-like paragraphs keep the old behaviour.
- Layout: "updated HH:MM" and Refresh moved into the header's right side; search and the gear share one toolbar row with the tabs (search first, gear
  last, wrapping under the tabs below 700 px); less dead space above the tiles, under the stats line and around the fetch-status line.
- Settings: the single long list is now four labelled groups -- Appearance / Feed & display / Notifications / Diagnostics -- with the same controls and
  the same saved values.
- Accessibility (`Lighthouse 91 -> 100` on the light theme; best-practices 100, SEO 100): `<main id="view-root">` landmark; `role="dialog"` +
  `aria-modal` + `aria-labelledby` on the brief, About, Sources and Settings panels; Tab focus is trapped inside the open panel; Esc closes it and returns
  focus to the row/button that opened it; segment buttons expose `aria-pressed`; fetch progress is `aria-live` and the stats are announced once
  through a separate screen-reader summary (not on every animation frame); the skeleton does not animate
  under `prefers-reduced-motion`; CVE badges are at least 24x24 px; a `meta description` was added. All four light themes were darkened to at least
  4.5:1 for faint text, severity colours and CVE badges.
- `static/favicon.svg` (SVG radar mark) + `<link rel="icon">`: the `/favicon.ico` 404 on every load is gone.
- `requirements.txt`: upper-bounded ranges (`flask>=3.1,<4`, `requests>=2.32,<3`, `feedparser>=6.0,<7`, `pywebview>=5,<7`, `psutil>=5.9,<8`), tested with
  flask 3.1.3, requests 2.34.2, feedparser 6.0.14 and psutil 7.2.2 (pywebview is desktop-only).
- Version: v1.5.12 -> v1.6.0.

## v1.5.12 -- News Now (5 days), cleaner sources, better categories, 60 fps cap, background notifications
- Tabs: "Today" and "Yesterday" are replaced by ONE tab, **News Now**, showing the last **5 days** merged (HISTORY_DAYS 2 -> 5; DAYS_KEPT 5 on the page).
  Tiles say "N in 5 days", the stats line says "N articles in the last 5 days". Why only 5 articles were shown: ~500 articles were fetched but only today+yesterday were kept
  (on a Sunday only 18 of 57 sources had published anything in 2 days). With 5 days 30+ sources contribute.
- Sources: 57 -> 48 (31 countries). Removed: SingCERT (404), BGD e-GOV CIRT (404), EG-CERT and DZ-CERT (certificate errors), NCSC Hungary (not a feed), and four feeds that had not
  published for months/years (CNCS Portugal, SI-CERT, Avast Blog, SensePost). Their globe markers/labels (BGD EGY SGP SVN DZA HUN PRT ZAF) are gone too.
  CERT-IL, CSIRT Italia and CCN-CERT (HTTP 403) now present a browser identity (experiment: if they still fail they will be removed). SK-CERT (520) and Lab52 (timeout) kept for now.
- Categories: SHIELD was always empty. New keyword sets (tool releases, detection, guidance, hardening...), whole-word matching for short words, tie-break shield > fallout > radar > strike,
  advisory-type feeds default to RADAR. On your 27 real articles SHIELD went 0 -> 4 and CRITICAL 1 -> 6 (KEV, unauthenticated root, Linux kernel flaw, Drupal, Apache MINA, Ivanti).
- No internet: when every source refuses to connect at once the page says "No internet connection - showing your saved articles" instead of listing all sources; the report says the same.
- Frame rate: globe and network background are capped at 60 fps (30 fps when the window is not focused or you have been idle 15 s). Rotation and dot speed no longer depend on the
  monitor refresh rate (your 165 Hz screen used to turn the globe ~2.7x too fast and draw 165 frames/s = ~65% of a core even when idle).
  Simulated high-refresh test: v1.5.11 saturated the main thread (100%), v1.5.12 about 43%; script time halved.
- Notifications (Windows): new `notifier.py`, run every 30 min by a hidden scheduled task ("SLATEWIRE Notifier"; ENABLE-NOTIFICATIONS.bat / DISABLE-NOTIFICATIONS.bat; INSTALL.bat offers it).
  "IMPORTANT": a new critical article (< 24 h old) while the app is closed (max 4 per day, grouped). "Catch up": app unused 6+ h and new articles waiting (max one per 6 h).
  Does nothing while the window is open, without internet, or when switched off in Settings; first run only remembers what exists. Toast text goes through an environment variable and
  XML escaping (feed text is never part of a command). Settings: toggle, "Send test notification", status line. The app writes app_state.json (running / last used) for the notifier.
- Diagnostics fixes: only msedgewebview2.exe counts as "the window" (a default browser started by a link click was counted as the app: 19-22 "window processes", 3.3 GB, 170% CPU);
  other programs are listed separately; WebView2 process IDs are listed so you can compare with Task Manager; the first 3 minutes are ignored for the memory-growth check
  (a start-up ramp was reported as 680 MB/hour); report section 17 shows the notifier log.
- Tests: backend 20, browser 10, full UI 38, label stability (0.0000 px), diagnostics backend 57, diagnostics page 23, notifier 35 (new), fetcher 22 (new), end-to-end with real backend (see HANDOFF).
- Not testable here (no Windows): the toast/balloon display, the scheduled task, the PowerShell scripts, WebView2 behaviour. Use Settings -> "Send test notification" first.

## v1.5.11 -- Black-box recorder: crash trail, freeze watchdog, backend tracebacks
- Session log `diagnostics/slatewire-session.log` (rotates at 1 MB): start-up line, a page heartbeat every 30 s (state, fps, worst frame, long tasks, memory, last 3 actions),
  a backend line every minute (CPU of backend + window, memory, window processes), page errors (flushed within 10 s), freezes, stalls, backend exceptions.
- Crash detection: a marker file is kept while the app runs; if it is still there at the next start the previous run ended unexpectedly. Settings then says so,
  and the report gets a "PREVIOUS RUN DID NOT END CLEANLY" section with the last log lines. Another running copy is not mistaken for a crash.
- Autosave: a full report is written every 5 minutes (`slatewire-autosave.txt`); after a crash it is kept as `slatewire-crash-<time>.txt`.
  On a normal close the final report is saved as `slatewire-last-session.txt`. The page sends a full snapshot every 2 minutes and when the window closes.
- Freeze watchdog (separate worker thread in the page): if the window does not answer for 5 s it reports the last actions; it also reports when the window recovers
  and the real duration. Ignored while minimised and across machine sleep. The backend also notes stalls of its own sampler and a page that stops sending heartbeats.
- Backend errors: any unexpected exception is logged with its traceback (report section 13) and the request gets a JSON 500; thread crashes and start-up fetch errors
  are logged too; `faulthandler` writes native crashes to `slatewire-fault.log`; without a console (pythonw) stdout/stderr go to `slatewire-stderr.log`.
- Report has two new sections: 13 crash/freeze/exception history, 16 session log tail. Settings: new "Delete diagnostic files" button (two clicks).
- Switching Diagnostics off stops sampling, logging, heartbeats, the watchdog and autosave.
- Tests: diagnostics backend 51/51 (real killed process, clean exit, second instance, rotation, freezes, tracebacks), end-to-end with real backend + real page (see below).
- Not testable here: Windows (WebView2 processes, hidden/minimised state, closing the real window, End task), the real pywebview window.

## v1.5.10 -- Diagnostics: performance log and "Create diagnostic report"
- New Settings section "Diagnostics" (on by default): records how the app behaves and, on request, writes one plain-text report to
  `slatewire\diagnostics\slatewire-diagnostic-<date>-<time>.txt` (last 30 kept). Buttons: Create diagnostic report, Open report folder, Add note.
- What is measured (page): frame rate per state (active / idle after 15 s without input / hidden), frame-time p50/p95/p99/max, dropped-frame counts and
  histogram, long main-thread tasks, JS heap and DOM size, startup timings, time spent per globe frame / network-background frame / render / feed load,
  every request to the backend (count, errors, avg, p95, max, KB), clicks (ids only), setting changes, window resizes, errors and console warnings, your notes.
- What is measured (backend, new diagnostics.py): CPU of the Python backend AND of the window's browser processes (msedgewebview2.exe) every 5 s, split by
  page state so idle CPU is its own number; memory (RSS) and growth trend; threads, Windows handles; whole-machine CPU/memory; per-route handling time;
  per-source feed status and time; data-file sizes; settings; environment (Windows build, Python, package versions, WebView2 version).
- The report starts with AUTOMATIC FINDINGS (low fps, stutter, high idle CPU, memory growth, slow requests, dead sources, "idle was not measured").
- Privacy: no API key, article text or search words are recorded (searches log only their length). Nothing is sent anywhere.
- Cost of the diagnostics (measured): page ~0.22 ms of JS per second; on/off difference in the browser within run-to-run noise (6 alternating runs, -1.7% +-3%);
  backend sampler ~1.7-2.4 ms per 5 s (about 0.03% of a core).
- New requirement: psutil (installed by INSTALL.bat). New setting key: diagnostics (boolean).
- INSTALL.bat now prefers Python 3.12, then 3.13, then the newest installed.
- Bug found and fixed while testing with the real backend: the "time in each state" totals were sent in milliseconds but read as seconds.
- Tests: backend 20/20, browser 10/10, full UI 38/38, diagnostics backend 25/25 (new), diagnostics page 23/23 (new), label stability unchanged
  (offset spread 0.0000 px, 33 fades per turn). A real end-to-end run (real Flask + psutil + the real page, 45 s of use, 75 s idle) produced a complete 22 KB report.
- Not testable here: Windows (WebView2 child-process CPU, the hidden/minimised state, Open report folder), the real pywebview window.

## v1.5.9 -- Light-blue continent outlines; Windows package
- Globe continent outlines are now light blue in every theme (pale blue on the three dark themes, deeper blue on the three light themes, so they stay visible).
  Country labels keep their normal colour (the yellow-label option was dropped).
- Windows package: INSTALL.bat (creates a private Python environment, installs the requirements, adds a Desktop shortcut), SLATEWIRE.bat (starts the app
  without a console window), SLATEWIRE-debug.bat (starts it with a console to show errors), UNINSTALL.bat, README_WINDOWS.txt. The app does not start with Windows.
- Tests run on this version: backend 20/20, browser 10/10, full UI 38/38 (6 themes, tabs, search, severity filter, article and hot-news briefs, archive,
  About and Sources pop-ups, settings toggles, sort, text size, reset, no sideways scroll at 4 window sizes, globe animating, no JS errors),
  label stability unchanged (offset spread 0.0000 px, 33 fade events per turn).
- Not tested (no Windows machine here): the real pywebview window, the .bat files, and the live feeds.

## v1.5.8 -- Steady globe labels (A at 5% smaller; B and the preview switch removed)
- Chosen look: A, label text 5% smaller than v1.5.4 (6.2 px -> 5.89 px). Look B, its solver and the preview-only switch are removed.
- Label stability: the 22 newer country labels each have one fixed spot beside their dot (table `NEW_LABELS`) and never move. Where two labels
  would collide, the lower-priority one fades out and back in (waits ~1/3 s first, so a borderline pair cannot flicker). The old automatic
  placement made these labels hop between slots; that is gone.
- The 17 original labels now also have one fixed spot each (table `FIXED`), chosen once by a search over a full turn of the globe for the fewest
  overlaps with every other label and dot, each label within ~14 px of its own dot. This also fixes USA/CAN crossing each other.
- Measured over one full turn (about 45-54 s, Chromium headless, no JS errors): every label's offset from its dot stayed identical (spread 0.0000 px);
  33 fade events for the 22 newer labels (1-5 per label, median 3), 1 of them under 0.5 s after the previous one, 0 fades that reversed midway.
- Overlaps, before -> after the re-pick: frames with any overlapping pair 35.2% -> 18.8%; frames with a pair overlapping by more than a third of a
  label 18.0% -> 6.5%; original-vs-original overlaps now only GER/RUS, in 4.4% of frames. The rest are newer labels during their short fade.
- Known, not changed: with ~20 European countries in a small area, not every label can show at once; hidden ones fade back as the globe turns.

## v1.5.7 -- Globe label look B redone to match the sketch
- Look B: each label stands just outside the globe in the direction of its dot; the line leaves the dot slanted and ends in a
  short flat tick before the text (previous version ran sideways into two columns). Crowded dots (Europe) get their labels
  spread around the rim by a small solver; labels keep their order and glide, so they do not swap or jump.
  Measured: no overlapping labels in the solved layout at 48 angles of a full turn; while the globe turns on its own,
  0 of 250 samples overlapped with Europe/Americas in view and 4 of 150 (one pair each) with Asia in view.
- Look A unchanged (label text 2% smaller; a 2% change is ~0.15 px, below what a screen can show).

## v1.5.6 -- Globe label options (preview: pick one)
- Two alternatives for the country labels on the globe, selectable only in the preview (switch at bottom-right of the page):
  A = same look, label text 2% smaller; B = labels stand outside the globe in two columns, each on a thin line to its dot
  (crowded Europe is shared between the left and right column; labels glide, nothing jumps). "Current" = v1.5.5 look.
  Globe size, rotation speed and everything else are unchanged. In the app, `window.__GLOBE_LABELS` ('A' / 'B') picks the look;
  the unchosen code will be removed once a look is chosen.

## v1.5.5 -- Sources pop-up, tidier key, small visual edits
- Sources moved out of Settings into their own pop-up: click "N/M sources reachable" on the home page.
- Country key under the globe is now neat alphabetical columns (code + name), not a wrapped line.
- The "Couldn't reach" note is short: just the source names.
- A CVE id wrapped in ( ) or [ ] in a headline loses the brackets (the badge replaces them); the badge look is unchanged.
- "updated <time>" is smaller, hour:minute only, and sits right above the settings gear.
- Tests: the Sources check now expects the pop-up.

## v1.5.4 -- 36 government CERT feeds + per-source status
- Sources: 21 -> 57, countries 17 -> 39. Added: CERT-EU (advisories + threat intel), CERT.be, NCSC UK, JPCERT/CC news,
  AusCERT, CERT.at, Bangladesh CIRT, EG-CERT, NCSC-FI (2), GovCERT.HK, CERT.LV, CERT Polska, SingCERT/CSA, SI-CERT,
  INCIBE-CERT, CCN-CERT, Swiss NCSC, DZ-CERT, CERT.br, CERT.hr, NUKIB, DKCERT, CERT-EE/RIA, CERT-FR, NCSC Hungary,
  CERT-IL, CSIRT Italia/ACN, NCSC-NL, NSM (Norway), CNCS Portugal, DNSC (Romania), SK-CERT, CERT-SE, CERT-UA.
  The feed addresses come from a public CERT registry (checked there in August 2026); they were NOT fetched or
  date-checked from the build sandbox. 15 of them publish in a language other than English (tag shown in Settings -> Sources).
- New per-source status after every refresh (`source_status` in news_data.json): fetched / empty / failed, number of
  articles, newest article time, failure reason, time taken. Shown in Settings -> Sources (problem sources first, with
  a summary line); the home page "N/M sources reachable" now opens it; the "Couldn't reach" note includes the reasons.
- Globe: 22 new country markers (labels of the new ones are placed automatically so they do not overlap; the original
  17 keep their positions). Country key moved under the globe and wraps (built from the marker list).
- Parallel fetching 8 -> 12 (57 sources). Tests: 2 backend checks + 1 browser check added; sources are escaped in the status list.

## v1.5.3 -- Safety and stability fixes (from the v1.5.2 test review)
Page
- Text from feeds, the AI brief, notes and the backend is now escaped before it is shown
  (titles, source names, hot news, briefs, notes, the "couldn't reach" list, ids in attributes).
  A hostile or hacked feed can no longer run script in the app window. CVE badges are added after escaping.
- Article links are only used if they start with http:// or https://. Note box limited to 2000 characters.
- Every change request (POST/DELETE) now carries an `X-Slatewire` header; article ids are URL-encoded.
- Network background: at most 140 dots (link distance grows on big screens so it looks the same),
  squared distances in the pair loop, half-resolution drawing above 2200 px wide, resize debounced (200 ms).
Backend
- New `safe_io.py`: JSON files are written atomically (no half-written files), a damaged file is moved aside
  instead of being read as empty, and the archive keeps a last-good copy (`archive.json.bak`) that is restored automatically.
  All read-modify-write cycles run under one lock.
- Archive: at most 2000 articles, note 2000 characters, every field whitelisted and length-capped; bad input
  (no id, a JSON list, a non-text note) returns 400 instead of 500; a full archive returns a 400 message.
- Briefs cache capped at 500 entries; brief requests need a JSON object with a text title.
- Request body limit 1 MB (413 above). Backend refuses any Host other than 127.0.0.1:5055 / localhost:5055 (403)
  and any non-GET request without the app header (403): blocks cross-site POSTs and DNS rebinding.
- Refresh: sources are fetched 8 at a time (order kept); overlapping refreshes share one fetch.
- Feeds: download limit 3 MB / 20 s (a slow-drip server can no longer stall a fetch), title cut at 300, link at 2000,
  date at 100; articles whose link is not http(s) are dropped. Article pages: 1.5 MB / 15 s, http(s) only.
- A damaged news_data.json no longer breaks /api/news (it is refetched).

## v1.5.2 -- Network background
- New subtle animated background: slowly drifting dots joined by faint lines.
  Lines and dots near the mouse pointer light up and link to it. Sits behind
  everything, cards are not affected. Follows the active theme colors, pauses
  when the window is hidden, and stays still if the system asks for reduced motion.

## v1.5.1 -- New source: Canada
- Added the Canadian Centre for Cyber Security (Cyber Centre) alerts and
  advisories feed (official government source, several posts per weekday).
  Feed checked live: valid Atom, last updated 9 Oct 2026.
  Now 21 sources across 17 countries; CAN added to the globe and its key.

## v1.5.0 -- Decrypt effect
- Every 15 seconds (and once at start-up) the title SLATEWIRE, the subtitle
  "cybersecurity news pulse", the category headings (STRIKE, SHIELD, FALLOUT,
  RADAR, also in the list view), the HOT NEWS heading and the SOURCE MAP
  heading scramble into random symbols and settle back, left to right.
  It never reacts to the mouse. Skipped when the window is hidden or the
  system asks for reduced motion.

## v1.4.0 -- Look and feel
- Dark / Light mode switch. Dark themes: Terminal (neon green), Slate,
  Midnight. Light themes: Parchment, Stone, Sage. The whole app, including
  the globe, follows the theme.
- Springy animation style: staggered pop-in of cards and rows, cards that
  lift on hover, button press feedback, springy switches, counting numbers
  in the stats line, pulsing critical dots, springy popups.
- Hot News card redesigned: headline and brief on the left, an "At a glance"
  column on the right (severity, source, age, number of articles it was
  picked from, category, CVE badges).
- Source map side cards: Top sources, Severity mix, By category (shown on
  wide windows).
- Text size slider (85% to 130%).

## v1.3.0 -- Reading
- Past days: Today and Yesterday tabs. Articles are kept for 2 days
  (`history.json`) and then removed; archived articles are never removed.
  A source that fails keeps its earlier articles.
- Read tracking: an article counts as read after it stays open for 5 seconds;
  read articles are dimmed and carry a small "Read" label (`read.json`).
- Per-source on/off switches in Settings. Hidden sources disappear from the
  lists, counts, hot news and search; a country with all sources off loses
  its globe marker and is struck through in the list.
- Sort fix: "Newest first" now really sorts by time. "Most severe first"
  (severity, then newest) is the default and matches how the app always sorted.
- Search covers both stored days.

## v1.2.0 -- Settings and source map layout
- Settings popup (gear icon above the search bar, `settings.json`):
  auto-refresh (off / 15 / 30 / 60 min), sort order, CVE badges on/off,
  hot news card on/off, source map on/off.
- Source map: country list in two columns beside the globe, bigger globe,
  smaller dots and labels, panel as wide as the hot news card.
- Globe and satellites turn 40% slower.
- Wider popups: article 980 px, About 560 px.
- The version number in the About popup now comes from `config.py`.

## v1.1.0 -- More sources
- 3 new sources: Sekoia Blog (France), Lab52 (Spain), Fox-IT (Netherlands).
  20 sources across 16 countries.
- Fixed, hand-placed label positions for the 9 European globe markers so the
  country codes no longer overlap.

## v1.0.0 -- Baseline
- Flask backend plus pywebview window. 17 sources across 13 countries,
  search, archive with notes, CVE badges linking to NVD, hot news card,
  severity filter and sorting, About popup, rotating quotes, rotating globe
  with real coastlines, Terminal Green theme.
