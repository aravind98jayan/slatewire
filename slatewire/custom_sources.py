"""
Custom RSS sources for SLATEWIRE.

Built-in feeds live in ``fetcher.SOURCES``. This module handles the ones the
user adds at runtime (typed in, or imported from an OPML file), so the rules
can be tested without touching the disk or the network. ``fetcher.py`` owns the
JSON file (``custom_sources.json``) and merges these rows with the built-ins.

Pure module: standard library only, no Flask, no file access.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

MAX_SOURCES = 60
MAX_NAME = 120
MAX_URL = 1000
MAX_OPML = 750_000            # an OPML file above ~750 KB is refused (the app caps bodies at 1 MB)
MAX_COUNTRY = 3
MAX_LANG = 5

_COUNTRY_RE = re.compile(r"^[A-Z0-9]{2,3}$")
_LANG_RE = re.compile(r"^[a-z]{2,5}$")


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _url(value) -> str:
    """A clean http(s) URL, or "" when it is not usable."""
    url = str(value or "").strip()
    if not url or len(url) > MAX_URL:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    if any(ch.isspace() for ch in url):
        return ""
    return url


def url_key(url: str) -> str:
    """Two spellings of the same feed compare equal (case, fragment, trailing /)."""
    return _text(url, MAX_URL).split("#", 1)[0].rstrip("/").lower()


def clean(raw) -> tuple[dict | None, str]:
    """One user-supplied source as a safe dict, plus a human-readable error."""
    if not isinstance(raw, dict):
        return None, "expected a source object"
    name = _text(raw.get("name"), MAX_NAME)
    if not name:
        return None, "a name is required"
    url = _url(raw.get("url"))
    if not url:
        return None, "the feed URL must start with http:// or https://"

    country = str(raw.get("country") or "").strip().upper()
    if len(country) > MAX_COUNTRY or (country and not _COUNTRY_RE.match(country)):
        return None, "the country must be a 2-3 letter code (or empty)"

    lat, lon = raw.get("lat"), raw.get("lon")
    has_lat = lat not in (None, "")
    has_lon = lon not in (None, "")
    if has_lat != has_lon:
        return None, "give both latitude and longitude, or neither"
    try:
        lat = float(lat) if has_lat else 0.0
        lon = float(lon) if has_lon else 0.0
    except (TypeError, ValueError):
        return None, "latitude and longitude must be numbers"
    if lat != lat or lon != lon or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, "latitude must be -90..90 and longitude -180..180"

    lang = str(raw.get("lang") or "").strip().lower() or "en"
    if len(lang) > MAX_LANG or not _LANG_RE.match(lang):
        return None, "the language must be a short code such as en"

    return {
        "name": name,
        "url": url,
        "country": country,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "lang": lang,
        "custom": True,
    }, ""


def normalize(data) -> list[dict]:
    """Accept the stored JSON (list, or {"sources": [...]}) and clean it."""
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        data = data["sources"]
    if not isinstance(data, list):
        return []
    out, seen = [], set()
    for raw in data:
        source, _err = clean(raw)
        if not source:
            continue
        key = url_key(source["url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
        if len(out) >= MAX_SOURCES:
            break
    return out


def _outline_attrs(node) -> dict:
    return {str(key).lower(): value for key, value in node.attrib.items()}


def parse_opml(text) -> tuple[list[dict], int, int]:
    """Read an OPML document. Returns (usable sources, invalid rows, duplicates).

    Both plain ``<outline xmlUrl=...>`` files and nested folders are accepted.
    Nothing is fetched or written here.
    """
    if not isinstance(text, str) or not text.strip():
        return [], 0, 0
    if len(text) > MAX_OPML:
        raise ValueError("OPML file is too large (over 750 KB)")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("not a valid OPML/XML file") from None

    found, invalid, duplicates = [], 0, 0
    seen = set()
    for node in root.iter():
        # tolerate namespaces and any capitalisation of the tag
        if not str(node.tag).rsplit("}", 1)[-1].lower() == "outline":
            continue
        attrs = _outline_attrs(node)
        url = attrs.get("xmlurl")
        if not url:
            continue
        name = attrs.get("text") or attrs.get("title") or ""
        if not name:
            try:
                name = urlsplit(url).hostname or url
            except ValueError:
                name = url
        source, _err = clean({"name": name, "url": url})
        if not source:
            invalid += 1
            continue
        key = url_key(source["url"])
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        found.append(source)
    return found, invalid, duplicates


def add(existing, raw, reserved_names=(), reserved_urls=()) -> tuple[list[dict], dict | None, str]:
    """Add one source. Returns (new list, added source or None, error)."""
    rows = normalize(existing)
    source, err = clean(raw)
    if not source:
        return rows, None, err
    if len(rows) >= MAX_SOURCES:
        return rows, None, f"the custom source list is full ({MAX_SOURCES})"

    names = {row["name"].lower() for row in rows} | {str(n).lower() for n in reserved_names}
    urls = {url_key(row["url"]) for row in rows} | {url_key(str(u)) for u in reserved_urls}
    if source["name"].lower() in names:
        return rows, None, "a source with that name already exists"
    if url_key(source["url"]) in urls:
        return rows, None, "that feed URL is already in the list"
    rows.append(source)
    return rows, source, ""


def merge(existing, incoming, reserved_names=(), reserved_urls=()) -> tuple[list[dict], dict]:
    """Add many sources (an OPML import). Duplicates are skipped, not errors."""
    rows = normalize(existing)
    stats = {"added": 0, "duplicates": 0, "invalid": 0, "full": 0}
    names = {row["name"].lower() for row in rows} | {str(n).lower() for n in reserved_names}
    urls = {url_key(row["url"]) for row in rows} | {url_key(str(u)) for u in reserved_urls}
    for raw in incoming or []:
        if len(rows) >= MAX_SOURCES:
            stats["full"] += 1
            continue
        source, _err = clean(raw)
        if not source:
            stats["invalid"] += 1
            continue
        key = url_key(source["url"])
        if key in urls or source["name"].lower() in names:
            stats["duplicates"] += 1
            continue
        rows.append(source)
        urls.add(key)
        names.add(source["name"].lower())
        stats["added"] += 1
    return rows, stats


def remove(existing, name: str) -> tuple[list[dict], bool]:
    """Remove one custom source by name. Returns (new list, removed)."""
    rows = normalize(existing)
    keep = [row for row in rows if row["name"].lower() != str(name).strip().lower()]
    return keep, len(keep) != len(rows)
