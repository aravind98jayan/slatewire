"""
SLATEWIRE -- desktop entry point.

Starts a small local Flask backend (fetching, briefs, archive) and opens the
dashboard in its own native window via pywebview -- no browser tab, no URL
bar. Close the window to quit; nothing keeps running in the background
afterwards.
"""

import hashlib
import os
import re
import threading
import time

import requests
import webview
from flask import Flask, Response, jsonify, request, send_from_directory

import archive_store
import article_text
import atexit
import config
import custom_sources
import digest
import exporter
import fetcher
import notifier
import safe_io
import search
import secrets_store
import source_health

# Resolve every data file relative to this script's own directory, not the
# process's current working directory -- otherwise a packaged build (or
# just launching from a different folder) silently reads/writes the wrong
# place.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# Window and taskbar icon. It lives on disk only (desktop shortcut and the
# Windows window use it); nothing in the page ever shows it.
ICON_PATH = os.path.join(BASE_DIR, "slatewire.ico")
APP_ID = "SLATEWIRE"          # taskbar identity; the notifier uses the same one

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 1_000_000   # no request body bigger than 1 MB

# The backend only answers the app's own window. A web page open in another
# tab/browser could otherwise talk to http://127.0.0.1:5055 (cross-site POST,
# or DNS rebinding with a foreign Host name) and e.g. wipe the archive.
ALLOWED_HOSTS = {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
MAX_ID = 64
BRIEF_CACHE_MAX = 500
READ_MAX = 5000


APP_STATE_PATH = "app_state.json"
_state_touch = {"t": 0.0}


def _write_app_state(**kw):
    """app_state.json tells the background notifier whether the window is open and when it was last used."""
    try:
        cur = safe_io.read_json(APP_STATE_PATH, {}) or {}
        cur.update(kw)
        safe_io.write_json(APP_STATE_PATH, cur)
    except Exception:
        pass


def _touch_app_state():
    now = time.time()
    if now - _state_touch["t"] >= 120:
        _state_touch["t"] = now
        _write_app_state(running=True, pid=os.getpid(), last_active=now)


@app.after_request
def _touch_state(resp):
    """Mark the app as active so the background notifier knows the window is open."""
    try:
        if request.path.startswith("/api/"):
            _touch_app_state()
    except Exception:
        pass
    return resp


@app.before_request
def _guard():
    if request.host.lower() not in ALLOWED_HOSTS:
        return jsonify({"ok": False, "error": "forbidden host"}), 403
    # Every state-changing request must carry a header the page adds itself.
    # Another website cannot add it without a CORS pre-flight, which we never allow.
    if request.method not in SAFE_METHODS and request.headers.get("X-Slatewire") != "1":
        return jsonify({"ok": False, "error": "missing app header"}), 403


@app.errorhandler(Exception)
def _unhandled(e):
    """HTTP errors keep their normal response; anything else becomes a plain 500."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    return jsonify({"ok": False, "error": "internal error"}), 500


@app.errorhandler(413)
def _too_big(_e):
    return jsonify({"ok": False, "error": "request too large"}), 413


def _body_dict():
    """The JSON body if it is an object, else None."""
    body = request.get_json(force=True, silent=True)
    return body if isinstance(body, dict) else None


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


NEWS_PATH = "news_data.json"
BRIEFS_CACHE_PATH = "briefs_cache.json"
SETTINGS_PATH = "settings.json"
SETTINGS_DEFAULTS = {"refresh": "off", "sort": "severity", "cveBadges": True, "hotNews": True, "sourceMap": True, "showDuplicates": False, "notifications": True, "sourceAlerts": False, "textScale": 100, "hiddenSources": [], "mode": "dark", "darkTheme": "terminal", "lightTone": "parchment"}
SETTINGS_CHOICES = {"refresh": ("off", "15", "30", "60"), "sort": ("severity", "newest"), "mode": ("dark", "light"), "darkTheme": ("terminal", "slate", "midnight"), "lightTone": ("parchment", "stone", "sage")}

_last_news_data = None  # in-memory cache of the last fetch, used to validate links


def _flag_arg(name):
    """None when a boolean query parameter is absent or empty, else its value."""
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_arg(name, default, lo, hi):
    """An integer query parameter clamped to [lo, hi]; the default when bad."""
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _live_articles() -> list[dict]:
    """Every article in the saved feed, flattened out of its categories."""
    news = safe_io.read_json(NEWS_PATH, {})
    articles: list[dict] = []
    if isinstance(news, dict) and isinstance(news.get("categories"), dict):
        for items in news["categories"].values():
            if isinstance(items, list):
                articles.extend(a for a in items if isinstance(a, dict))
    return articles


def _hidden_sources() -> list[str]:
    """Source names muted in Settings (the feed hides them; other views follow)."""
    saved = safe_io.read_json(SETTINGS_PATH, {})
    hidden = saved.get("hiddenSources") if isinstance(saved, dict) else None
    return hidden if isinstance(hidden, list) else []


# ---------- brief cache (avoids re-calling the API for the same article) ----------


def _load_briefs_cache() -> dict:
    data = safe_io.read_json(BRIEFS_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_briefs_cache(cache: dict) -> None:
    # keep only the newest BRIEF_CACHE_MAX briefs (dicts remember insertion order)
    while len(cache) > BRIEF_CACHE_MAX:
        cache.pop(next(iter(cache)))
    safe_io.write_json(BRIEFS_CACHE_PATH, cache, indent=2)


def _known_article_links() -> set:
    """Links from the last successful fetch, used to sanity-check that a
    client isn't asking the server to fetch an arbitrary URL. This is a
    lightweight mitigation, not a full auth system -- fine for a
    single-user local app, worth revisiting before any public deployment."""
    global _last_news_data
    if _last_news_data is None:
        loaded = safe_io.read_json(NEWS_PATH, None)
        _last_news_data = loaded if isinstance(loaded, dict) else {"categories": {}}
    links = set()
    cats = _last_news_data.get("categories", {})
    for cat_articles in (cats.values() if isinstance(cats, dict) else []):
        for a in cat_articles:
            if isinstance(a, dict) and a.get("link"):
                links.add(a["link"])
    return links


def _ask_gemini(prompt: str, timeout: int = 25) -> str:
    """One Gemini call, with a fallback model; returns the plain text of the reply.

    Google's full Flash model is frequently capacity-limited on the free tier (HTTP
    503) or stalls past the read timeout. Rather than fail the brief, a busy or
    timed-out model falls through to config.BRIEF_MODEL_FALLBACK."""
    key = secrets_store.effective_key()
    if not key:
        return ""
    models = [config.BRIEF_MODEL]
    fallback = getattr(config, "BRIEF_MODEL_FALLBACK", "")
    if fallback and fallback not in models:
        models.append(fallback)
    last = None
    for model in models:
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={
                    "x-goog-api-key": key,
                    "Content-Type": "application/json",
                },
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last = requests.HTTPError(f"{resp.status_code} from {model}", response=resp)
                continue
            resp.raise_for_status()
            data = resp.json()
            try:
                parts = data["candidates"][0]["content"]["parts"]
                text = "\n".join(p.get("text", "") for p in parts).strip()
            except (KeyError, IndexError, TypeError):
                text = ""
            if text:
                return text
            last = ValueError("empty reply from " + model)
        except requests.exceptions.RequestException as e:
            last = e
    if last:
        raise last
    return ""


# The model sometimes answers the prompt instead of writing a brief when the source
# page has no article text ("The provided article text is empty..."). Those replies
# must never reach the card; cached ones are discarded and regenerated.
_META_BRIEF = re.compile(
    r"(article text is empty|no (?:article|news) (?:text|content)|"
    r"contains no (?:article|news) content|(?:can(?:not|'t)?|unable to) (?:be )?summar|"
    r"please provide (?:the )?(?:full )?(?:article|text)|"
    r"not enough (?:article|source) (?:text|content)|"
    r"as an ai (?:language )?model|i(?:'m| am) (?:sorry|unable)|i (?:don't|do not) have access)",
    re.IGNORECASE,
)
_NO_SUMMARY = "No written summary is available for this item. Open the original link to read or listen."


def _brief_is_unusable(text: str) -> bool:
    """True when the model answered about the prompt instead of writing a brief."""
    return bool(_META_BRIEF.search((text or "")[:600]))


def _looks_like_podcast(article: dict) -> bool:
    """Podcast pages (for example SANS Stormcast) have no article text to summarize."""
    hay = (str(article.get("title") or "") + " " + str(article.get("link") or "")).lower()
    return "podcast" in hay


def _has_article_text(text: str) -> bool:
    """False for a page that only yielded a licence/copyright line and a link."""
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"\(c\).*?(?:licen[cs]e|copyright)[^.]*\.?", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned) >= 100 and len(cleaned.split()) >= 15


def _generate_brief(article: dict, target_lines_min: int, target_lines_max: int, style: str) -> tuple[str, bool]:
    """Returns (text, ai_written). ai_written is False when no API key is
    configured (the text is then a real excerpt from the source page) or when
    the API returned nothing usable."""
    # Podcast pages carry no article text at all -- do not spend a page fetch
    # and a model call on them (the model would only talk about the prompt).
    if _looks_like_podcast(article):
        return _NO_SUMMARY, False
    # Pull a real excerpt from the article's own page -- the RSS snippet
    # alone is often just one sentence. Falls back to the RSS snippet if
    # the page can't be fetched (paywalled, blocked, slow, etc.).
    excerpt = article_text.extract_excerpt(article.get("link"))
    source_text = excerpt or article.get("desc") or article["title"]
    # A feed description can carry the same script/markup junk the page extractor
    # now filters out. Never show that as a brief -- fall back to the headline.
    if article_text.looks_like_code(source_text):
        source_text = article["title"]

    # Pages that only carry a copyright line have nothing to summarize either.
    has_text = _has_article_text(source_text)
    if not has_text:
        return _NO_SUMMARY, False

    if not secrets_store.has_key():
        # No AI key yet: show a real excerpt from the source. The page labels
        # this as an excerpt, so no long warning is needed inside the card.
        return article_text.wrap_as_lines(source_text, target_lines=target_lines_max), False

    prompt = (
        f"Summarize this cybersecurity news item for a SOC-analyst job seeker's "
        f"daily briefing app. This is the '{style}' version.\n"
        f"Write {target_lines_min} to {target_lines_max} short lines (a little more or less "
        f"depending on how much the source actually says), in clear plain English that doesn't "
        f"need re-reading to understand. Stay close to the source's own facts and "
        "terminology -- do not speculate or add anything not implied by the "
        "text below. No markdown, no headers, no bullet symbols, just plain "
        "sentences on their own lines.\n"
        "If the article text below is not in English, translate it as you "
        "summarize -- the output must always be in English regardless of the "
        "source language.\n\n"
        f"Title: {article['title']}\n"
        f"Source: {article['source']}\n"
        f"Article text: {source_text}"
    )

    text = _ask_gemini(prompt)
    if not text or _brief_is_unusable(text):
        # The model talked about the prompt (or returned nothing). Show the
        # source instead of commentary, and let the page label it as an excerpt.
        return article_text.wrap_as_lines(source_text, target_lines=target_lines_max), False
    return text, True


def _source_alerts(data):
    """Toast when a source is retired, plus the optional failure-streak alert."""
    try:
        saved = safe_io.read_json(SETTINGS_PATH, {})
        if not isinstance(saved, dict):
            saved = {}
        rows = data.get("source_health") if isinstance(data, dict) else None
        retired = [r["name"] for r in (rows or [])
                   if isinstance(r, dict) and r.get("just_retired") and isinstance(r.get("name"), str)]
        # Retirement is permanent until the user restores the source, so it is
        # worth a toast even though streak alerts are opt-in. It still honours
        # the master notifications switch.
        if retired and saved.get("notifications") is not False:
            count = len(retired)
            shown = ", ".join(retired[:4]) + (f" +{count - 4} more" if count > 4 else "")
            notifier.send_toast(
                "SLATEWIRE source retired",
                f"{count} source{'s' if count != 1 else ''} failed {source_health.RETIRE_AFTER} "
                f"hard checks in a row and was retired: {shown}. Restore it in Settings -> Sources.",
            )
            return
        if saved.get("sourceAlerts") is not True:
            return
        names = [r["name"] for r in (rows or [])
                 if isinstance(r, dict) and r.get("just_alerted") and isinstance(r.get("name"), str)]
        if not names:
            return
        shown = ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")
        count = len(names)
        notifier.send_toast(
            "SLATEWIRE source health",
            f"{count} source{'s' if count != 1 else ''} failed {source_health.ALERT_STREAK} "
            f"refreshes in a row: {shown}",
        )
    except Exception:   # a notification must never break a refresh
        pass


# ---------------------------------- export ---------------------------------

EXPORT_DATASETS = ("feed", "category", "archive", "search", "digest")


class _ExportError(Exception):
    """A bad export request (message is safe to show in the page)."""


def _export_format() -> str:
    fmt = exporter.normalize_format(request.args.get("format"))
    if fmt is None:
        raise _ExportError("format must be md, markdown, csv or json")
    return fmt


def _export_dataset() -> str:
    dataset = (request.args.get("dataset") or "").strip().lower()
    if dataset not in EXPORT_DATASETS:
        raise _ExportError("dataset must be one of: " + ", ".join(EXPORT_DATASETS))
    return dataset


def _export_filters(**extra) -> dict:
    """The filter set shared by the metadata and the UI, without empty values."""
    filters = {
        "category": request.args.get("category", "")[:40].strip(),
        "severity": request.args.get("severity", "")[:20].strip(),
        "source": request.args.get("source", "")[:100].strip(),
        "days": _int_arg("days", None, 1, 3650),
        "has_cve": _flag_arg("has_cve"),
        "kev": _flag_arg("kev"),
        "include_duplicates": bool(_flag_arg("include_dupes")),
    }
    filters.update(extra)
    return filters


def _export_payload() -> dict:
    """Build one export (body, filename, content type) from the request args."""
    fmt = _export_format()
    dataset = _export_dataset()
    now = time.time()
    category = request.args.get("category", "")[:40].strip()
    severity = request.args.get("severity", "")[:20].strip()
    source = request.args.get("source", "")[:100].strip()
    days = _int_arg("days", None, 1, 3650)
    has_cve = _flag_arg("has_cve")
    kev = _flag_arg("kev")
    include_dupes = bool(_flag_arg("include_dupes"))

    if dataset == "digest":
        result = digest.build(
            _live_articles(),
            hours=_int_arg("hours", digest.WINDOW_HOURS_DEFAULT, 1,
                           digest.WINDOW_HOURS_MAX),
            top=_int_arg("top", digest.TOP_DEFAULT, 1, digest.TOP_MAX),
            hidden_sources=_hidden_sources(),
            include_duplicates=include_dupes,
        )
        body, filename, mime = exporter.render_digest(
            result, fmt, version=config.VERSION, now=now)
        return {"body": body, "filename": filename, "mimetype": mime,
                "count": int(result.get("total") or 0),
                "dataset": dataset, "format": fmt}

    filters = _export_filters()

    if dataset == "search":
        query = request.args.get("q", "").strip()[:search.MAX_QUERY]
        if not query:
            raise _ExportError("a search export needs a query")
        scope = request.args.get("scope", "all")
        result = search.search(
            query,
            live=_live_articles(),
            archive=archive_store.list_all(),
            briefs=_load_briefs_cache(),
            hidden_sources=_hidden_sources(),
            category=category,
            severity=severity,
            source=source,
            days=days,
            has_cve=has_cve,
            kev=kev,
            scope=scope,
            include_duplicates=include_dupes,
            limit=search.MAX_LIMIT,
        )
        filters["q"] = query
        filters["scope"] = scope if scope in ("live", "archive") else ""
        body, filename, mime = exporter.render_articles(
            result["results"], fmt, dataset="search",
            title=f'SLATEWIRE search: "{query}"',
            version=config.VERSION, filters=filters, query=query,
            matched=result["total"], now=now)
        count = len(result["results"])

    elif dataset == "archive":
        articles = []
        for article in archive_store.list_all():
            if isinstance(article, dict):
                item = dict(article)
                item["archived"] = True
                articles.append(item)
        selected = exporter.select_articles(
            articles, category=category, severity=severity, source=source,
            days=days, has_cve=has_cve, kev=kev, include_duplicates=True, now=now)
        body, filename, mime = exporter.render_articles(
            selected, fmt, dataset="archive", version=config.VERSION,
            filters=filters, now=now)
        count = len(selected)

    else:
        if dataset == "category" and not category:
            raise _ExportError("a category export needs a category")
        articles = _live_articles()
        archived_ids = {a.get("id") for a in archive_store.list_all()
                        if isinstance(a, dict)}
        for article in articles:
            article["archived"] = article.get("id") in archived_ids
        selected = exporter.select_articles(
            articles, hidden_sources=_hidden_sources(), category=category,
            severity=severity, source=source, days=days, has_cve=has_cve,
            kev=kev, include_duplicates=include_dupes, now=now)
        title = (f"SLATEWIRE category: {category}" if dataset == "category"
                 else "SLATEWIRE feed export")
        body, filename, mime = exporter.render_articles(
            selected, fmt, dataset=dataset, title=title,
            version=config.VERSION, filters=filters, now=now)
        count = len(selected)

    return {"body": body, "filename": filename, "mimetype": mime,
            "count": count, "dataset": dataset, "format": fmt}


def _export_folder() -> str:
    folder = os.path.abspath(exporter.EXPORT_DIR)
    os.makedirs(folder, exist_ok=True)
    return folder


def _export_path(filename: str) -> tuple[str, str]:
    """A path inside exports/ only -- the filename comes from our own builder."""
    name = os.path.basename(filename)
    if not name or name != filename or name.startswith("."):
        raise _ExportError("invalid export filename")
    folder = _export_folder()
    path = os.path.join(folder, name)
    if os.path.exists(path):   # never overwrite: two exports can land in one second
        stem, ext = os.path.splitext(name)
        number = 2
        while os.path.exists(os.path.join(folder, f"{stem}-{number}{ext}")):
            number += 1
        path = os.path.join(folder, f"{stem}-{number}{ext}")
    return path, folder


# ---------------------------------- routes ----------------------------------

@app.route("/")
def home():
    return send_from_directory("static", "dashboard.html")


@app.route("/api/news")
def get_news():
    global _last_news_data
    data = safe_io.read_json(NEWS_PATH, None)
    fetched = False
    if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
        data = fetcher.fetch_and_save(NEWS_PATH)
        fetched = True
    data["version"] = config.VERSION
    _last_news_data = data
    archived_ids = {a.get("id") for a in archive_store.list_all()}
    for cat_articles in data["categories"].values():
        for a in cat_articles:
            a["archived"] = a.get("id") in archived_ids
    if fetched:
        _source_alerts(data)
    return jsonify(data)


@app.route("/api/search")
def search_articles():
    """Full-text search across the loaded feed and the archive (read-only)."""
    query = request.args.get("q", "")
    if not isinstance(query, str):
        query = ""
    query = query.strip()[:search.MAX_QUERY]

    # Read only: unlike /api/news this never starts a fetch, so a search frame
    # can't accidentally block on the network.
    result = search.search(
        query,
        live=_live_articles(),
        archive=archive_store.list_all(),
        briefs=_load_briefs_cache(),
        # The feed hides muted sources; search follows the same setting so a
        # muted outlet cannot reappear in the results. An explicit ?source=
        # still wins (see search.search).
        hidden_sources=_hidden_sources(),
        category=request.args.get("category", "")[:40],
        severity=request.args.get("severity", "")[:20],
        source=request.args.get("source", "")[:100],
        days=_int_arg("days", None, 1, 3650),
        has_cve=_flag_arg("has_cve"),
        kev=_flag_arg("kev"),
        scope=request.args.get("scope", "all"),
        include_duplicates=bool(_flag_arg("include_dupes")),
        limit=_int_arg("limit", search.DEFAULT_LIMIT, 1, search.MAX_LIMIT),
    )
    return jsonify(result)


@app.route("/api/digest")
def daily_digest():
    """A read-only summary of the last N hours of the feed."""
    result = digest.build(
        _live_articles(),
        hours=_int_arg("hours", digest.WINDOW_HOURS_DEFAULT, 1,
                       digest.WINDOW_HOURS_MAX),
        top=_int_arg("top", digest.TOP_DEFAULT, 1, digest.TOP_MAX),
        hidden_sources=_hidden_sources(),
        include_duplicates=bool(_flag_arg("include_dupes")),
    )
    return jsonify(result)


DIGEST_AI_CACHE_MAX = 20
_digest_ai_cache: dict[str, str] = {}


def _clamp_int(value, default, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _digest_prompt(result: dict) -> str:
    counts = result["counts"]
    lines = [
        "Write a short cybersecurity news digest for a SOC analyst.",
        f"Time window: the last {result['window_hours']} hours.",
        f"Stories: {result['total']} (critical {counts['critical']}, "
        f"high {counts['high']}, routine {counts['routine']}, "
        f"known-exploited {counts['kev']}, distinct CVEs {counts['cves']}).",
        "",
        "Top stories:",
    ]
    for number, story in enumerate(result["top_stories"], 1):
        flags = []
        if story.get("kev"):
            flags.append("known exploited")
        if story.get("source_count", 1) > 1:
            flags.append(f"{story['source_count']} sources")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"{number}. [{story.get('severity') or 'unknown'}] "
                     f"{story.get('title') or ''} -- "
                     f"{story.get('source') or ''}{suffix}")
    if result["exploited"]:
        lines += ["", "Known-exploited CVEs:"]
        for row in result["exploited"][:10]:
            bits = [row["cve"]]
            if row["vendor"] or row["product"]:
                bits.append(" ".join(x for x in (row["vendor"], row["product"]) if x))
            if row["name"]:
                bits.append(row["name"])
            if row["dateAdded"]:
                bits.append("added " + row["dateAdded"])
            if row["ransomware"]:
                bits.append("ransomware use known")
            lines.append("- " + " | ".join(bits))
    lines += [
        "",
        "Write 3 to 6 short plain sentences as one flowing paragraph. No line "
        "breaks, no markdown, no bullet symbols, no headers.",
        "Lead with the most important development. Stay strictly within the "
        "facts above; do not speculate or add anything. Always write in English.",
    ]
    return "\n".join(lines)


@app.route("/api/digest/ai", methods=["POST"])
def daily_digest_ai():
    """Optional AI-written paragraph for the digest (in-memory cached)."""
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    result = digest.build(
        _live_articles(),
        hours=_clamp_int(body.get("hours"), digest.WINDOW_HOURS_DEFAULT, 1,
                         digest.WINDOW_HOURS_MAX),
        top=_clamp_int(body.get("top"), digest.TOP_DEFAULT, 1, digest.TOP_MAX),
        hidden_sources=_hidden_sources(),
        include_duplicates=bool(body.get("include_dupes")),
    )
    local = result["narrative"]
    if not result["total"]:
        return jsonify({"digest": local, "ai": False, "reason": "no stories"})
    if not secrets_store.has_key():
        return jsonify({"digest": local, "ai": False, "reason": "no API key"})

    key = hashlib.sha1("|".join(
        [str(result["window_hours"]), str(result["total"]),
         str(result["counts"]["kev"]),
         ",".join(row["cve"] for row in result["exploited"])]
        + [f"{s.get('id')}:{s.get('title')}" for s in result["top_stories"]]
    ).encode("utf-8")).hexdigest()
    cached = _digest_ai_cache.get(key)
    if cached:
        return jsonify({"digest": cached, "ai": True, "cached": True})
    try:
        text = _ask_gemini(_digest_prompt(result), timeout=45)
    except Exception as e:
        return jsonify({"digest": local, "ai": False,
                        "reason": type(e).__name__})
    if not text:
        return jsonify({"digest": local, "ai": False, "reason": "empty reply"})
    while len(_digest_ai_cache) >= DIGEST_AI_CACHE_MAX:
        _digest_ai_cache.pop(next(iter(_digest_ai_cache)))
    _digest_ai_cache[key] = text
    return jsonify({"digest": text, "ai": True, "cached": False})


@app.route("/api/settings", methods=["GET", "POST"])
def settings_route():
    with safe_io.LOCK:
        cur = dict(SETTINGS_DEFAULTS)
        saved = safe_io.read_json(SETTINGS_PATH, {})
        if isinstance(saved, dict):
            cur.update({k: v for k, v in saved.items() if k in SETTINGS_DEFAULTS})
        if request.method == "POST":
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                body = {}
            for k in SETTINGS_DEFAULTS:
                if k not in body:
                    continue
                if k in SETTINGS_CHOICES:
                    if body[k] in SETTINGS_CHOICES[k]:
                        cur[k] = body[k]
                elif k == "textScale":
                    v = body[k]
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        cur[k] = int(max(85, min(130, round(v))))
                elif k == "hiddenSources":
                    if isinstance(body[k], list):
                        cur[k] = [x for x in body[k] if isinstance(x, str) and 0 < len(x) <= 80][:200]
                elif isinstance(body[k], bool):
                    cur[k] = body[k]
            safe_io.write_json(SETTINGS_PATH, cur)
    return jsonify(cur)


@app.route("/api/gemini-key", methods=["GET", "POST"])
def gemini_key_route():
    """Save or forget the optional Gemini API key from Settings.

    The key is written to %APPDATA%\\SLATEWIRE\\secrets.json, outside the app
    folder, so an update that replaces the folder cannot erase it. GET never
    returns the key -- only whether one is configured and where it comes from.
    """
    if request.method == "GET":
        return jsonify({"ok": True, **secrets_store.status()})
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    with safe_io.LOCK:
        if body.get("clear") is True:
            removed = secrets_store.clear_key()
            return jsonify({"ok": True, "removed": removed, **secrets_store.status()})
        problem = secrets_store.save_key(body.get("key"))
        if problem:
            return _bad(problem)
        return jsonify({"ok": True, **secrets_store.status()})


READ_PATH = "read.json"
READ_KEEP_DAYS = 14


@app.route("/api/read", methods=["GET", "POST"])
def read_route():
    """Which article ids have been opened for 5+ seconds ({id: timestamp})."""
    with safe_io.LOCK:
        data = safe_io.read_json(READ_PATH, {})
        if not isinstance(data, dict):
            data = {}
        if request.method == "POST":
            body = _body_dict()
            rid = (body or {}).get("id")
            if isinstance(rid, str) and 0 < len(rid) <= MAX_ID:
                data[rid] = int(time.time())
            cutoff = time.time() - READ_KEEP_DAYS * 86400
            data = {k: v for k, v in data.items() if isinstance(v, (int, float)) and v >= cutoff}
            if len(data) > READ_MAX:   # keep the newest ones
                data = dict(sorted(data.items(), key=lambda kv: kv[1])[-READ_MAX:])
            safe_io.write_json(READ_PATH, data)
    return jsonify({"ids": list(data.keys())})


@app.route("/api/fetch", methods=["POST"])
def refetch_news():
    global _last_news_data
    data = fetcher.fetch_and_save(NEWS_PATH)
    data["version"] = config.VERSION
    _last_news_data = data
    _source_alerts(data)
    return jsonify(data)


@app.route("/api/fetch/status")
def fetch_status():
    """Live progress of the fetch that is running right now (or the last one)."""
    return jsonify(fetcher.progress())


def _source_health_payload() -> dict:
    """Stored source history, merged with the latest refresh snapshot.

    Rows are limited to sources that still exist: a feed removed from
    ``fetcher.SOURCES`` (or a custom feed that was removed) can leave its
    history behind in ``source_health.json``, and it should not keep showing
    up in the Sources panel."""
    records = safe_io.read_json(fetcher.HEALTH_PATH, {})
    news = safe_io.read_json(NEWS_PATH, {})
    rows = news.get("source_status") if isinstance(news, dict) else None
    if not isinstance(rows, list):
        rows = []
    rows = [r for r in rows if isinstance(r, dict)]
    known = {source["name"] for source in fetcher.all_sources()}
    sources = [row for row in source_health.view(records, rows) if row.get("name") in known]
    return {"sources": sources, "summary": source_health.summarize(sources),
            "now": int(time.time())}


@app.route("/api/sources/health")
def sources_health():
    """Read-only: per-source success rate, failure streaks, latency and age."""
    return jsonify(_source_health_payload())


@app.route("/api/sources/health/clear", methods=["POST"])
def clear_sources_health():
    """Forget the saved history (the next refresh starts a fresh one)."""
    with safe_io.LOCK:
        safe_io.write_json(fetcher.HEALTH_PATH, {})
    return jsonify(_source_health_payload())


@app.route("/api/sources/revive", methods=["POST"])
def revive_source():
    """Put a retired source back into the active set (its history is kept)."""
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > source_health.MAX_NAME:
        return _bad("name must be a source name")
    name = name.strip()
    if name not in {source["name"] for source in fetcher.all_sources()}:
        return _bad("unknown source")
    with safe_io.LOCK:
        records = source_health.normalize(safe_io.read_json(fetcher.HEALTH_PATH, {}))
        if name not in records:
            return _bad("source has no saved history")
        safe_io.write_json(fetcher.HEALTH_PATH, source_health.revive(records, name), indent=2)
    return jsonify(_source_health_payload())


def _custom_payload() -> dict:
    """The saved custom feeds, plus the cap the page shows."""
    rows = custom_sources.normalize(safe_io.read_json(fetcher.CUSTOM_PATH, []))
    return {"sources": rows, "max": custom_sources.MAX_SOURCES}


def _reserved_source_names() -> tuple[list[str], list[str]]:
    return ([source["name"] for source in fetcher.SOURCES],
            [source["url"] for source in fetcher.SOURCES])


def _save_custom(rows: list[dict]) -> None:
    with safe_io.LOCK:
        safe_io.write_json(fetcher.CUSTOM_PATH, rows, indent=2)


@app.route("/api/sources/custom")
def custom_sources_list():
    """Read-only: the feeds the user added."""
    return jsonify(_custom_payload())


@app.route("/api/sources/custom", methods=["POST"])
def add_custom_source():
    """Add one custom RSS/Atom feed."""
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    names, urls = _reserved_source_names()
    with safe_io.LOCK:
        existing = custom_sources.normalize(safe_io.read_json(fetcher.CUSTOM_PATH, []))
        rows, added, error = custom_sources.add(existing, body, names, urls)
        if not added:
            return _bad(error)
        safe_io.write_json(fetcher.CUSTOM_PATH, rows, indent=2)
    payload = _custom_payload()
    payload.update(ok=True, added=added)
    return jsonify(payload)


@app.route("/api/sources/custom/import", methods=["POST"])
def import_custom_sources():
    """Bulk-add feeds from an OPML document (posted as text)."""
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    opml = body.get("opml")
    if not isinstance(opml, str) or not opml.strip():
        return _bad("opml must be the text of an OPML file")
    try:
        incoming, invalid, duplicates = custom_sources.parse_opml(opml)
    except ValueError as exc:
        return _bad(str(exc))
    names, urls = _reserved_source_names()
    with safe_io.LOCK:
        existing = custom_sources.normalize(safe_io.read_json(fetcher.CUSTOM_PATH, []))
        rows, stats = custom_sources.merge(existing, incoming, names, urls)
        safe_io.write_json(fetcher.CUSTOM_PATH, rows, indent=2)
    stats["invalid"] += invalid
    stats["duplicates"] += duplicates
    payload = _custom_payload()
    payload.update(ok=True, stats=stats)
    return jsonify(payload)


@app.route("/api/sources/custom/remove", methods=["POST"])
def remove_custom_source():
    """Remove one custom feed, its health history and any hidden flag."""
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return _bad("name must be a source name")
    name = name.strip()
    with safe_io.LOCK:
        existing = custom_sources.normalize(safe_io.read_json(fetcher.CUSTOM_PATH, []))
        rows, removed = custom_sources.remove(existing, name)
        if not removed:
            return _bad("not a custom source")
        safe_io.write_json(fetcher.CUSTOM_PATH, rows, indent=2)

        records = source_health.normalize(safe_io.read_json(fetcher.HEALTH_PATH, {}))
        if name in records:
            records.pop(name)
            safe_io.write_json(fetcher.HEALTH_PATH, records, indent=2)

        saved = safe_io.read_json(SETTINGS_PATH, {})
        hidden = saved.get("hiddenSources") if isinstance(saved, dict) else None
        if isinstance(hidden, list) and name in hidden:
            saved["hiddenSources"] = [n for n in hidden if n != name]
            safe_io.write_json(SETTINGS_PATH, saved, indent=2)
    payload = _custom_payload()
    payload.update(ok=True, removed=name)
    return jsonify(payload)


@app.route("/api/brief/<article_id>", methods=["POST"])
def get_brief(article_id):
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    if not (0 < len(article_id) <= MAX_ID):
        return _bad("bad article id")
    if not isinstance(body.get("title"), str) or not body["title"].strip():
        return _bad("title must be text")
    article = archive_store.clean(body)   # known fields only, as limited-length text
    article["id"] = article_id
    article.setdefault("source", "")

    # Basic validation: the link must be one we actually fetched ourselves,
    # not an arbitrary URL the client is asking us to go fetch server-side.
    link = body.get("link")
    if link and (not isinstance(link, str) or link not in _known_article_links()):
        return jsonify({"brief": "Could not generate a brief: article not recognized."}), 400

    is_hotnews = request.args.get("context") == "hotnews"
    target_min, target_max = (6, 8) if is_hotnews else (10, 15)
    style = "Hot News teaser" if is_hotnews else "full article brief"
    cache_key = f"{article_id}:hotnews" if is_hotnews else f"{article_id}:full"

    with safe_io.LOCK:
        cache = _load_briefs_cache()
    cached = cache.get(cache_key)
    if cached and not _brief_is_unusable(cached):
        return jsonify({"brief": cached, "ai": True})
    try:
        brief, ai_written = _generate_brief(article, target_min, target_max, style)
    except Exception as e:
        return jsonify({"brief": f"Could not generate a brief right now ({type(e).__name__}). "
                                  f"You can still open the original link.", "ai": False}), 200

    # Only real AI-written briefs are cached. An excerpt or a placeholder must
    # be retried once a key is configured.
    if ai_written:
        with safe_io.LOCK:
            cache = _load_briefs_cache()
            cache[cache_key] = brief
            _save_briefs_cache(cache)
    return jsonify({"brief": brief, "ai": ai_written})


@app.route("/api/archive", methods=["GET"])
def list_archive():
    return jsonify(archive_store.list_all())


@app.route("/api/archive", methods=["POST"])
def add_to_archive():
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    article = archive_store.clean(body)
    if not article.get("id"):
        return _bad("article needs a text id of 1-64 characters")
    link = body.get("link")
    if link and (not isinstance(link, str) or link not in _known_article_links()):
        return _bad("article not recognized")
    with safe_io.LOCK:
        cache = _load_briefs_cache()
    article["brief"] = cache.get(f"{article['id']}:full") or article.get("desc", "")
    try:
        archive_store.add(article)
    except archive_store.ArchiveFull as e:
        return _bad(str(e))
    except ValueError as e:
        return _bad(str(e))
    return jsonify({"ok": True})


@app.route("/api/archive/<article_id>", methods=["DELETE"])
def remove_from_archive(article_id):
    archive_store.remove(article_id)
    return jsonify({"ok": True})


@app.route("/api/archive/<article_id>/note", methods=["POST"])
def set_archive_note(article_id):
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    note = body.get("note", "")
    if not isinstance(note, str):
        return _bad("note must be text")
    if not archive_store.update_note(article_id, note):
        return _bad("not archived", 404)
    return jsonify({"ok": True})


@app.route("/api/archive/clear", methods=["POST"])
def clear_archive():
    archive_store.clear()
    return jsonify({"ok": True})


@app.route("/api/archive/path")
def archive_path():
    return jsonify({"path": os.path.abspath(archive_store.TXT_PATH)})


# ---------------------------------- export ---------------------------------

@app.route("/api/export")
def export_download():
    """The same export as a download (Content-Disposition: attachment)."""
    try:
        payload = _export_payload()
    except _ExportError as e:
        return _bad(str(e))
    response = Response(payload["body"].encode("utf-8"),
                        content_type=payload["mimetype"])
    response.headers["Content-Disposition"] = (
        'attachment; filename="' + payload["filename"] + '"')
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/export/save", methods=["POST"])
def export_save():
    """Write an export into the exports/ folder next to the app."""
    try:
        payload = _export_payload()
        path, folder = _export_path(payload["filename"])
        safe_io.write_text(path, payload["body"])
    except _ExportError as e:
        return _bad(str(e))
    except OSError as e:
        return _bad("could not save the export: " + str(e)[:120], 500)
    return jsonify({
        "ok": True,
        "path": path,
        "folder": folder,
        "filename": os.path.basename(path),
        "bytes": len(payload["body"].encode("utf-8")),
        "count": payload["count"],
        "dataset": payload["dataset"],
        "format": payload["format"],
    })


@app.route("/api/export/open-folder", methods=["POST"])
def export_open_folder():
    try:
        folder = _export_folder()
        if os.name == "nt":
            os.startfile(folder)   # noqa: S606  (fixed folder inside the app)
        return jsonify({"ok": True, "path": folder, "opened": os.name == "nt"})
    except OSError as e:
        return _bad("could not open the exports folder: " + str(e)[:120], 500)



@app.route("/api/ping")
def ping():
    """The launch readiness probe: tiny, so it never looks like a second page load."""
    return jsonify({"ok": True})


@app.route("/api/notify/status", methods=["GET"])
def notify_status():
    s = safe_io.read_json(SETTINGS_PATH, {}) or {}
    return jsonify({"enabled": s.get("notifications", True) is not False, "task": notifier.task_status(), "windows": os.name == "nt"})


@app.route("/api/notify/task", methods=["POST"])
def notify_task():
    """Turn the Windows background checker on or off from Settings.

    Creating the task needs no elevation for a per-user task; on machines where
    policy forbids it the error text points at ENABLE-NOTIFICATIONS.bat."""
    body = _body_dict()
    if body is None:
        return _bad("expected a JSON object")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return _bad("enabled must be true or false")
    ok, message = notifier.task_install() if enabled else notifier.task_remove()
    return jsonify({"ok": ok, "message": message, "task": notifier.task_status(), "windows": os.name == "nt"})


# ---------------------------------- launcher ----------------------------------

def run_flask():
    app.run(port=config.PORT, debug=False, use_reloader=False)


def _wait_for_flask(port: int, timeout: float = 5.0) -> None:
    """Poll the local port until Flask actually responds, so the desktop
    window doesn't briefly show a connection-refused error on launch."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"http://127.0.0.1:{port}/api/ping", timeout=0.5)
            return
        except requests.exceptions.RequestException:
            time.sleep(0.1)


def _prefetch_news_in_background():
    """Kick off the first news fetch on a background thread so the window
    doesn't sit on a blank page while the initial RSS fetch runs."""
    if not os.path.exists(NEWS_PATH):
        try:
            fetcher.fetch_and_save(NEWS_PATH)
        except Exception:   # the frontend shows the empty state and the user can hit Refresh
            pass


def _set_windows_identity():
    """Give the window its own taskbar identity and icon.

    On Windows 11 the taskbar button takes its picture from the AppUserModelID,
    not from the window icon, so ``webview.start(icon=...)`` alone only changes
    the title bar and Alt-Tab. Registering the same per-user identity the
    notifier uses for its toasts (no administrator needed) fixes the button;
    the window icon remains the fallback on every Windows version.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:                                        # noqa: BLE001
        pass
    if not os.path.isfile(ICON_PATH):
        return
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Classes\AppUserModelId" + "\\" + APP_ID)
        with key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "SLATEWIRE")
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, ICON_PATH)
            winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def main():
    _set_windows_identity()
    now = time.time()
    _write_app_state(running=True, pid=os.getpid(), last_start=now, last_active=now)
    atexit.register(lambda: _write_app_state(running=False, last_close=time.time()))
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    threading.Thread(target=_prefetch_news_in_background, daemon=True).start()
    _wait_for_flask(config.PORT)
    webview.create_window(
        "SLATEWIRE " + config.VERSION,
        f"http://127.0.0.1:{config.PORT}/?app=1",
        width=1040,
        height=760,
        min_size=(720, 560),
    )
    webview.start(icon=ICON_PATH if os.path.isfile(ICON_PATH) else None)


if __name__ == "__main__":
    main()
