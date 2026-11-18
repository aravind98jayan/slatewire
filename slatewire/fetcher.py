"""
Fetches cybersecurity news directly from RSS feeds (server-side -- no
browser CORS restriction applies here, so no proxy is needed), classifies
each article into one of four categories, flags a severity level, and
writes the result to news_data.json.

This module only ever reads text (titles / summaries / links / dates) from
each feed. It never downloads, opens, or executes anything from the source
sites -- parsing is done with feedparser's XML parser, and every field is
treated as inert display text.
"""
from __future__ import annotations

import calendar
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import feedparser
import requests

import config
import custom_sources
import dedupe
import kev
import safe_io
import source_health

# Each source carries a country tag + rough lat/lon so the frontend globe
# can place a marker for it. Coordinates are approximate (capital city or
# HQ city), not precise addresses.
SOURCES = [
    {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews",
     "country": "IN", "lat": 20.59, "lon": 78.96},
    {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/",
     "country": "USA", "lat": 37.09, "lon": -95.71},
    {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/",
     "country": "USA", "lat": 37.09, "lon": -95.71},
    {"name": "Dark Reading", "url": "https://www.darkreading.com/rss.xml",
     "country": "USA", "lat": 37.09, "lon": -95.71},
    {"name": "PortSwigger Blog", "url": "https://portswigger.net/blog/rss",
     "country": "UK", "lat": 55.38, "lon": -3.44},
    {"name": "SANS ISC", "url": "https://isc.sans.edu/rssfeed_full.xml",
     "country": "USA", "lat": 37.09, "lon": -95.71},
    {"name": "The Register", "url": "https://www.theregister.com/security/headlines.rss",
     "country": "UK", "lat": 55.38, "lon": -3.44},
    {"name": "heise Security", "url": "https://www.heise.de/security/rss/news-atom.xml",
     "country": "GER", "lat": 52.37, "lon": 9.73},
    {"name": "JPCERT/CC", "url": "https://blogs.jpcert.or.jp/en/atom.xml",
     "country": "JPN", "lat": 35.68, "lon": 139.69},
    {"name": "WeLiveSecurity", "url": "https://www.welivesecurity.com/en/rss/feed/",
     "country": "SVK", "lat": 48.15, "lon": 17.11},
    {"name": "Securelist", "url": "https://securelist.com/feed/",
     "country": "RUS", "lat": 55.75, "lon": 37.62},
    {"name": "Security Affairs", "url": "https://securityaffairs.com/feed",
     "country": "ITA", "lat": 41.90, "lon": 12.50},
    {"name": "Check Point Research", "url": "https://research.checkpoint.com/feed/",
     "country": "ISR", "lat": 32.08, "lon": 34.78},
    {"name": "ASEC (AhnLab)", "url": "https://asec.ahnlab.com/en/feed/",
     "country": "KOR", "lat": 37.57, "lon": 126.98},
    {"name": "Bitdefender Labs", "url": "https://www.bitdefender.com/blog/api/rss/labs/",
     "country": "ROU", "lat": 44.43, "lon": 26.10},
    {"name": "Sekoia Blog", "url": "https://www.sekoia.com/blog/rss.xml",
     "country": "FRA", "lat": 48.86, "lon": 2.35},
    {"name": "Canadian Cyber Centre", "url": "https://www.cyber.gc.ca/api/cccs/atom/v1/get?feed=alerts_advisories&lang=en",
     "country": "CAN", "lat": 45.42, "lon": -75.70},
    {"name": "CERT-EU Advisories", "url": "https://cert.europa.eu/publications/security-advisories-rss",
     "country": "BEL", "lat": 50.85, "lon": 4.35, "lang": "en"},
    {"name": "CERT-EU Threat Intelligence", "url": "https://cert.europa.eu/publications/threat-intelligence-rss",
     "country": "BEL", "lat": 50.85, "lon": 4.35, "lang": "en"},
    {"name": "CERT.be Advisories", "url": "https://ccb.belgium.be/advisories.xml",
     "country": "BEL", "lat": 50.85, "lon": 4.35, "lang": "en"},
    {"name": "NCSC UK (all)", "url": "https://www.ncsc.gov.uk/api/1/services/v1/all-rss-feed.xml",
     "country": "UK", "lat": 55.38, "lon": -3.44, "lang": "en"},
    {"name": "JPCERT/CC (news)", "url": "https://www.jpcert.or.jp/english/rss/jpcert-en.rdf",
     "country": "JPN", "lat": 35.68, "lon": 139.69, "lang": "en"},
    {"name": "CERT.at", "url": "https://cert.at/cert-at.en.blog.rss_2.0.xml",
     "country": "AUT", "lat": 48.21, "lon": 16.37, "lang": "en"},
    {"name": "NCSC-FI (news)", "url": "https://www.kyberturvallisuuskeskus.fi/feed/rss/en",
     "country": "FIN", "lat": 60.17, "lon": 24.94, "lang": "en"},
    {"name": "NCSC-FI (vulnerabilities)", "url": "https://www.kyberturvallisuuskeskus.fi/sites/default/files/rss/vulns.xml",
     "country": "FIN", "lat": 60.17, "lon": 24.94, "lang": "en"},
    {"name": "GovCERT.HK", "url": "https://www.govcert.gov.hk/en/rss_security_alerts.xml",
     "country": "HKG", "lat": 22.32, "lon": 114.17, "lang": "en"},
    {"name": "CERT.LV", "url": "https://cert.lv/en/feed/rss/all",
     "country": "LVA", "lat": 56.95, "lon": 24.11, "lang": "en"},
    {"name": "CERT Polska", "url": "https://cert.pl/en/rss.xml",
     "country": "POL", "lat": 52.23, "lon": 21.01, "lang": "en"},
    {"name": "INCIBE-CERT", "url": "https://www.incibe.es/en/incibe-cert/alerta-temprana/avisos/feed",
     "country": "ESP", "lat": 40.42, "lon": -3.7, "lang": "en"},
    {"name": "Swiss NCSC news", "url": "https://www.newsd.admin.ch/newsd/feeds/rss?lang=en&org-nr=1101",
     "country": "CHE", "lat": 46.95, "lon": 7.45, "lang": "en"},
    {"name": "CERT.br", "url": "https://www.cert.br/rss/certbr-rss.xml",
     "country": "BRA", "lat": -23.55, "lon": -46.63, "lang": "pt"},
    {"name": "CERT.hr", "url": "https://www.cert.hr/feed/",
     "country": "HRV", "lat": 45.81, "lon": 15.98, "lang": "hr"},
    {"name": "NUKIB", "url": "https://nukib.gov.cz/rss.xml",
     "country": "CZE", "lat": 50.08, "lon": 14.44, "lang": "cs"},
    {"name": "DKCERT", "url": "https://www.cert.dk/news/rss",
     "country": "DNK", "lat": 55.68, "lon": 12.57, "lang": "da"},
    {"name": "CERT-EE / RIA", "url": "https://www.ria.ee/et/news-feed/all/feed",
     "country": "EST", "lat": 59.44, "lon": 24.75, "lang": "et"},
    {"name": "CERT-FR", "url": "https://www.cert.ssi.gouv.fr/feed/",
     "country": "FRA", "lat": 48.86, "lon": 2.35, "lang": "fr"},
    {"name": "NCSC-NL advisories", "url": "https://advisories.ncsc.nl/rss/advisories",
     "country": "NLD", "lat": 52.08, "lon": 4.31, "lang": "nl"},
    {"name": "NSM NCSC alerts", "url": "https://nsm.no/fagomrader/digital-sikkerhet/nasjonalt-cybersikkerhetssenter/varsler-fra-ncsc/rss/",
     "country": "NOR", "lat": 59.91, "lon": 10.75, "lang": "no"},
    {"name": "DNSC (CERT-RO)", "url": "https://dnsc.ro/feed",
     "country": "ROU", "lat": 44.43, "lon": 26.1, "lang": "ro"},
    {"name": "CERT-SE", "url": "https://www.cert.se/feed.rss",
     "country": "SWE", "lat": 59.33, "lon": 18.07, "lang": "sv"},
    {"name": "CERT-UA", "url": "https://cert.gov.ua/api/articles/rss",
     "country": "UKR", "lat": 50.45, "lon": 30.52, "lang": "uk"},
]

CATEGORIES = ["strike", "shield", "fallout", "radar"]

KEYWORDS = {
    "strike": ["exploit", "attacker", "threat actor", "threat group", "apt", "campaign", "malware", "phishing", "zero-day", "zero day",
               "backdoor", "trojan", "botnet", "red team", "hackers", "espionage", "spyware", "infostealer", "stealer", "wiper", "ddos",
               "supply chain", "cyberattack", "cyber attack", "hacktivist", "loader", "rat",
               # German equivalents (heise Security publishes in German)
               "angreifer", "ausnutzen", "hintertür", "schadsoftware"],
    "radar": ["cve-", "cvss", "vulnerab", "patch", "advisory", "advisories", "security advisory", "flaw", "bug bounty", "disclosed",
              "security update", "security bulletin", "known exploited", "hotfix", "firmware update",
              "sicherheitslücke", "schwachstelle", "sicherheitswarnung"],
    "fallout": ["breach", "ransomware", "leaked", "leak", "hacked", "data leak", "outage", "compromised", "stolen", "attack on", "hit by",
                "extortion", "victims", "fined", "arrested", "sentenced",
                "datenleck", "datenpanne", "geleakt", "gehackt", "kompromittiert", "erpressersoftware"],
    "shield": ["detection", "mitigation", "mitigate", "defender", "defence", "defense", "blue team", "siem", "patch tuesday",
               "best practice", "framework", "research finds", "guidance", "guide", "how to", "hardening", "harden", "recommendation",
               "playbook", "threat hunting", "incident response", "forensic", "yara", "sigma", "wireshark", "suricata", "zeek", "snort",
               "osquery", "velociraptor", "open-source tool", "open source tool", "release", "released", "edr", "xdr", "soc", "zero trust",
               "awareness", "checklist",
               "erkennung", "abwehr", "gegenmaßnahme"],
}
# Words of 4 letters or fewer must match as whole words ("rat" must not match inside "operation"); longer ones match from the start of a word.
def _compile(words):
    return [re.compile((r"\b" + re.escape(w) + r"\b") if (len(w) <= 4 and w.isalpha()) else (r"(?<![a-z0-9])" + re.escape(w))) for w in words]
_KW_RE = {cat: _compile(words) for cat, words in KEYWORDS.items()}
# when two categories score the same, the first one in this list wins
TIE_ORDER = ["shield", "fallout", "radar", "strike"]
# Government / national CERT feeds are advisories: with no keyword hit they belong to RADAR, news sites default to FALLOUT.
GOV_HINTS = ("CERT", "CSIRT", "NCSC", "NUKIB", "DNSC", "INCIBE", "NSM", "ACN", "GovCERT")

CRITICAL_WORDS = ["actively exploited", "exploited in the wild", "known exploited vulnerabilit", "zero-day", "zero day", "0-day",
                  "critical vulnerability", "critical flaw", "critical security", "critical patch", "critical remote",
                  "unauthenticated", "remote code execution", "rce", "ransomware", "nation-state", "emergency patch", "emergency directive",
                  "supply chain attack", "worm", "mass exploitation",
                  "aktiv ausgenutzt", "kritische sicherheitslücke", "notfall-patch"]
HIGH_WORDS = ["exploit", "breach", "leaked", "compromised", "advisory", "malware", "backdoor", "phishing campaign", "attack",
              "angreifer", "datenleck", "schadsoftware", "sicherheitswarnung"]
_CRIT_RE, _HIGH_RE = _compile(CRITICAL_WORDS), _compile(HIGH_WORDS)

REQUEST_TIMEOUT = 12          # seconds to wait for the server to connect / send the next piece
FEED_MAX_BYTES = 3_000_000    # a feed bigger than this (after decompression) is refused
FEED_TOTAL_SECONDS = 20       # hard ceiling for one whole feed download
MAX_PARALLEL = 12             # sources fetched at the same time
MAX_TITLE = 300
MAX_LINK = 2000
MAX_PUBDATE = 100

# History: articles are kept for the last 5 local days (today + 4 earlier),
# then dropped. Archived articles live in archive.json and are never touched.
HISTORY_PATH = "history.json"
HISTORY_DAYS = 5
HEALTH_PATH = "source_health.json"   # rolling per-source history (streaks, latency, success rate)
CUSTOM_PATH = "custom_sources.json"  # feeds added by the user (typed in or imported from OPML)
USER_AGENT = "Slatewire/1.0 (personal news reader; +https://github.com/)"
# A few government sites answer HTTP 403 to unknown programs; sources flagged "ua": "browser" are fetched with a normal browser identity instead.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 SLATEWIRE")


def article_id(source: str, title: str) -> str:
    """Short, stable, filesystem/key-safe id for an article."""
    return hashlib.sha1(f"{source}::{title}".encode("utf-8")).hexdigest()[:16]


def classify(text: str, source_name: str = "") -> str:
    t = text.lower()
    scores = {cat: sum(1 for r in rx if r.search(t)) for cat, rx in _KW_RE.items()}
    best = max(scores.values())
    if best <= 0:   # no keyword at all: advisories feeds -> RADAR, news sites -> FALLOUT (most headlines are incident-driven)
        return "radar" if any(h in (source_name or "") for h in GOV_HINTS) else "fallout"
    return next(c for c in TIE_ORDER if scores[c] == best)


def severity(text: str) -> str:
    t = text.lower()
    if any(r.search(t) for r in _CRIT_RE):
        return "critical"
    if any(r.search(t) for r in _HIGH_RE):
        return "high"
    return "routine"


SEVERITY_RANK = {"critical": 2, "high": 1, "routine": 0}


def _sort_key(article: dict) -> tuple:
    # Sort by severity first (critical > high > routine), then by real
    # publish time within each severity band. published_ts is a proper
    # numeric timestamp (see fetch_source) -- sorting the raw RFC-822
    # string instead would order by weekday name, not time, which was a
    # real bug in an earlier version of this file.
    return (SEVERITY_RANK.get(article.get("severity", "routine"), 0),
            article.get("published_ts", 0))


# Why the last fetch of each source failed (shown in Settings -> Sources).
_reasons: dict[str, str] = {}
# True when the failure is permanent (HTTP 404/410, DNS failure, not a feed)
# rather than a timeout, 403 or 5xx that may clear up on its own.
_hard: dict[str, bool] = {}


def _reason_for(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.HTTPError):
        code = getattr(exc.response, "status_code", None)
        return f"HTTP {code}" if code else "HTTP error"
    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        return "timed out / too slow"
    if isinstance(exc, requests.exceptions.SSLError):
        return "certificate (SSL) error"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "cannot connect"
    if isinstance(exc, ValueError) and "too large" in str(exc):
        return "feed too large (over 3 MB)"
    return type(exc).__name__


def _hard_for(exc: Exception) -> bool:
    """Whether this failure means the URL itself is wrong, not just busy."""
    if isinstance(exc, requests.exceptions.HTTPError):
        return getattr(exc.response, "status_code", None) in (404, 410)
    if isinstance(exc, requests.exceptions.ConnectionError):
        text = str(exc).lower()
        return any(hint in text for hint in (
            "name or service not known", "nodename nor servname",
            "getaddrinfo failed", "name resolution", "no address associated",
        ))
    return False


def fetch_source(source: dict) -> tuple[list[dict], str | None]:
    """Returns (articles, error). error is None on success."""
    _reasons.pop(source["name"], None)
    _hard.pop(source["name"], None)
    try:
        body, _enc = safe_io.download_limited(
            source["url"],
            ({"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8"}
             if source.get("ua") == "browser" else {"User-Agent": USER_AGENT}),
            FEED_MAX_BYTES,
            min(FEED_TOTAL_SECONDS, REQUEST_TIMEOUT * 2),
            connect_timeout=min(5, REQUEST_TIMEOUT),
            read_timeout=REQUEST_TIMEOUT,
        )
        parsed = feedparser.parse(body)
        if parsed.bozo and not parsed.entries:
            _reasons[source["name"]] = "not valid RSS/Atom"
            _hard[source["name"]] = True
            return [], source["name"]

        articles = []
        for entry in parsed.entries[:12]:
            link = str(getattr(entry, "link", "") or "").strip()
            # only real web links are kept (no javascript:, file:, data: ...)
            if not link.lower().startswith(("http://", "https://")) or len(link) > MAX_LINK:
                continue
            title = str(getattr(entry, "title", "") or "").strip()[:MAX_TITLE] or "(untitled)"
            pub = str(getattr(entry, "published", "") or getattr(entry, "updated", "") or "")[:MAX_PUBDATE]
            pub_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            published_ts = calendar.timegm(pub_parsed) if pub_parsed else 0
            summary = str(getattr(entry, "summary", "") or "")
            # feedparser leaves HTML in summary; strip crudely, keep it plain text
            summary = re.sub("<[^<]+?>", "", summary[:5000]).strip()[:600]
            articles.append({
                "title": title,
                "link": link,
                "pubDate": pub,
                "published_ts": published_ts,
                "desc": summary,
                "source": source["name"],
            })
        return articles, None
    except Exception as exc:  # noqa: BLE001
        _reasons[source["name"]] = _reason_for(exc)
        _hard[source["name"]] = _hard_for(exc)
        return [], source["name"]


def _load_history(path: str = HISTORY_PATH) -> dict:
    data = safe_io.read_json(path, {})
    return data if isinstance(data, dict) else {}


def _load_health() -> dict:
    """The stored per-source health records (empty on a first run or a bad file)."""
    try:
        return source_health.normalize(safe_io.read_json(HEALTH_PATH, {}))
    except (OSError, ValueError):
        return {}


def _custom_sources() -> list[dict]:
    """Feeds the user added, cleaned and marked so the UI can label them."""
    try:
        return custom_sources.normalize(safe_io.read_json(CUSTOM_PATH, []))
    except (OSError, ValueError):
        return []


def all_sources() -> list[dict]:
    """Built-in feeds first, then the user's custom feeds."""
    return SOURCES + _custom_sources()


def merge_history(fresh: list[dict], history: dict, now: float | None = None) -> dict:
    """Add freshly fetched articles to the history and drop anything older
    than HISTORY_DAYS local calendar days. An article with no usable publish
    time gets the time it was first seen; a publish time in the future is
    clamped to now."""
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(now)
    cutoff = (datetime(dt.year, dt.month, dt.day) - timedelta(days=HISTORY_DAYS - 1)).timestamp()
    for a in fresh:
        old = history.get(a["id"])
        first = old.get("first_seen_ts", now) if old else now
        item = dict(a)
        item["first_seen_ts"] = first
        ts = item.get("published_ts") or int(first)
        item["published_ts"] = int(min(ts, now))
        history[item["id"]] = item
    return {k: v for k, v in history.items() if v.get("published_ts", 0) >= cutoff}


# Live progress of the current fetch. The page polls /api/fetch/status while a
# refresh runs, so "fetching 18/48 sources" is real data rather than a guess.
_progress_lock = threading.Lock()
_progress = {
    "running": False, "done": 0, "total": len(SOURCES),
    "ok": 0, "failed": 0, "started_ts": 0.0, "finished_ts": 0.0,
}


def _progress_reset(total: int | None = None) -> None:
    with _progress_lock:
        _progress.update(running=True, done=0, total=len(SOURCES) if total is None else total,
                         ok=0, failed=0, started_ts=time.time(), finished_ts=0.0)


def _progress_tick(reachable: bool) -> None:
    with _progress_lock:
        _progress["done"] += 1
        _progress["ok" if reachable else "failed"] += 1


def _progress_finish() -> None:
    with _progress_lock:
        _progress["running"] = False
        _progress["finished_ts"] = time.time()


def progress() -> dict:
    """A copy of the current fetch progress (safe to serialise)."""
    with _progress_lock:
        return dict(_progress)


def collect_fresh() -> tuple[list, list, list]:
    """Download every source and classify its articles. Saves NOTHING (the background notifier uses this).
    Returns (fresh_articles, failed_source_names, per_source_status)."""
    errors = []
    fresh = []
    # Retired sources are skipped entirely until the user restores them.
    retired = source_health.retired_names(_load_health())
    active = [source for source in all_sources() if source["name"] not in retired]
    _progress_reset(len(active))

    def _one(source):
        # fetch_source is looked up here (at call time) on purpose: tests replace it
        t0 = time.monotonic()
        try:
            res = fetch_source(source)
        except Exception:
            res = ([], source["name"])
        _progress_tick(res[1] is None)   # reachable, even if the feed was empty
        return res, int((time.monotonic() - t0) * 1000)

    # several sources at once; map() keeps the active-source order in the results
    try:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            results = list(pool.map(_one, active))
    finally:
        _progress_finish()

    status = []
    for source, ((articles, err), ms) in zip(active, results):
        name = source["name"]
        if err:
            errors.append(err)
            state, reason = "failed", _reasons.get(name, "no response")
        elif not articles:
            state, reason = "empty", "reachable, but no usable articles in the feed"
        else:
            state, reason = "ok", ""
        status.append({
            "name": name, "country": source["country"], "lang": source.get("lang", "en"),
            "state": state, "items": 0 if err else len(articles),
            "newest_ts": max((a.get("published_ts") or 0) for a in articles) if (articles and not err) else 0,
            "reason": reason, "ms": ms, "hard": _hard.get(name) is True,
            "custom": source.get("custom") is True,
        })
        if err:
            continue
        for a in articles:
            text = f"{a['title']} {a['desc']}"
            cat = classify(text, name)
            sev = severity(text)
            a["id"] = article_id(a["source"], a["title"])
            a["category"] = cat
            a["severity"] = sev
            fresh.append(a)
    return fresh, errors, status


def _save_source_health(status: list[dict]) -> dict:
    """Add this refresh to the rolling per-source history (source_health.json).
    A failed history write must never break the fetch itself."""
    if not status:
        return {}
    try:
        with safe_io.LOCK:
            records = source_health.normalize(safe_io.read_json(HEALTH_PATH, {}))
            records = source_health.apply(records, status)
            safe_io.write_json(HEALTH_PATH, records, indent=2)
        return records
    except (OSError, ValueError):
        return {}


def fetch_all() -> dict:
    buckets = {cat: [] for cat in CATEGORIES}
    fresh, errors, status = collect_fresh()
    health_records = _save_source_health(status)

    # A source retired by this refresh (or an earlier one) is no longer part of
    # the active set: its rows stay in the Sources panel, but it does not count
    # toward the totals and it gets no globe marker.
    retired = source_health.retired_names(health_records) if health_records else set()
    active = [s for s in all_sources() if s["name"] not in retired]
    active_errors = [name for name in errors if name not in retired]

    # Merge into the rolling history (a source that failed this time keeps
    # its earlier articles), then build the categories with a per-day cap.
    with safe_io.LOCK:
        history = merge_history(fresh, _load_history())
        try:
            safe_io.write_json(HISTORY_PATH, history)
        except OSError:
            pass
    per_day = {}
    for a in sorted(history.values(), key=_sort_key, reverse=True):
        if a.get("category") in buckets:
            per_day.setdefault((a["category"], date.fromtimestamp(a["published_ts"])), []).append(a)
    for (cat, _day), items in per_day.items():
        buckets[cat].extend(items[: config.MAX_PER_CATEGORY])
    for cat in buckets:
        buckets[cat].sort(key=_sort_key, reverse=True)

    # Same-story grouping runs on the final set (after the per-day cap), so the
    # page, the API and the archive all see one representative per story.
    clusters, dedupe_stats = dedupe.apply_to_categories(buckets)

    # every source refused to connect within moments = the PC has no internet
    # right now (the page says so instead of listing every source)
    offline = bool(status and all(x.get("reason") == "cannot connect" for x in status))

    # CISA KEV: mark the CVEs that are already exploited in the wild. The
    # catalog is cached daily; when the fetch just showed we are offline we
    # read that cache instead of waiting on another request.
    kev_stats = kev.enrich_categories(buckets, online=not offline)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "categories": buckets,
        "clusters": clusters,
        "dedupe": dedupe_stats,
        "kev": kev_stats,
        "errors": active_errors,
        "sources_total": len(active),
        "sources_ok": len(active) - len(active_errors),
        "source_map": [{"name": s["name"], "country": s["country"], "lat": s["lat"], "lon": s["lon"],
                        "custom": s.get("custom") is True}
                        for s in active],
        "source_status": status,
        "source_health": source_health.view(health_records, status),
        "offline": offline,
    }
    return data


_cond = threading.Condition()
_running = False
_done_count = 0
_last_data = None
_last_error = None


def fetch_and_save(path: str = "news_data.json") -> dict:
    """Fetch every source and save the result. If a fetch is already running,
    this waits for it and returns ITS result (which finished after this call
    arrived) instead of starting another full round of downloads."""
    global _running, _done_count, _last_data, _last_error
    with _cond:
        if _running:
            seen = _done_count
            while _done_count == seen:
                _cond.wait()
            if _last_error is not None:
                raise _last_error
            return dict(_last_data)
        _running = True
    data, err = None, None
    try:
        data = fetch_all()
        with safe_io.LOCK:
            safe_io.write_json(path, data, indent=2)
    except BaseException as e:   # noqa: BLE001 - passed on to waiting callers too
        err = e
    finally:
        with _cond:
            _running = False
            _done_count += 1
            _last_data, _last_error = data, err
            _cond.notify_all()
    if err is not None:
        raise err
    return data


if __name__ == "__main__":
    start = time.time()
    result = fetch_and_save()
    total = sum(len(v) for v in result["categories"].values())
    print(f"Fetched {total} articles from {result['sources_ok']}/{result['sources_total']} sources "
          f"in {time.time() - start:.1f}s")
    if result["errors"]:
        print("Could not reach:", ", ".join(result["errors"]))
