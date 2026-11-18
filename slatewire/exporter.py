"""
Export SLATEWIRE data as Markdown, CSV or JSON.

Pure module (standard library only, no Flask, no file access): app.py gathers
the articles or digest from the existing read-only endpoints and this module
turns them into text with context metadata and a safe filename.

Everything exported is inert text. Markdown escapes inline markup, links are
limited to http(s), JSON is plain data, and CSV cells that could be read as a
formula are prefixed with an apostrophe so a spreadsheet cannot execute them.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import datetime, timezone

from articles import article_ts, norm, source_count, text

EXPORT_DIR = "exports"
MAX_ITEMS = 2000                 # the archive's own cap; keeps a save bounded
CSV_BOM = "\ufeff"               # helps Excel open UTF-8 files correctly

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

EXTENSIONS = {"markdown": ".md", "csv": ".csv", "json": ".json"}
MIMETYPES = {
    "markdown": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json; charset=utf-8",
}
DEFAULT_TITLES = {
    "feed": "SLATEWIRE feed export",
    "category": "SLATEWIRE category export",
    "archive": "SLATEWIRE archive export",
    "search": "SLATEWIRE search export",
}
CSV_COLUMNS = (
    "dataset", "exported_utc", "app_version",
    "id", "title", "source", "category", "severity",
    "published_utc", "published_raw", "link",
    "summary", "brief", "note", "cves", "kev", "archived", "source_count",
)
DIGEST_CSV_COLUMNS = (
    "dataset", "exported_utc", "app_version",
    "rank", "id", "title", "source", "category", "severity",
    "published_utc", "link", "cves", "kev", "source_count",
)


def normalize_format(value) -> str | None:
    """'md', 'markdown', '.csv' ... -> internal name, or None when unsupported."""
    value = text(value).strip().lower().lstrip(".")
    if value in ("md", "markdown"):
        return "markdown"
    if value == "csv":
        return "csv"
    if value == "json":
        return "json"
    return None


def mimetype(fmt: str) -> str:
    return MIMETYPES.get(normalize_format(fmt) or "", MIMETYPES["markdown"])


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_link(value) -> str:
    url = text(value).strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return url.replace(" ", "%20").replace("<", "%3C").replace(">", "%3E")


def _cves(article: dict) -> list[str]:
    parts = [text(article.get("title")), text(article.get("desc")),
             text(article.get("brief")), text(article.get("note"))]
    found = {match.group(0).upper()
             for part in parts for match in CVE_RE.finditer(part)}
    known = article.get("kev_cves")
    if isinstance(known, list):
        for cve in known:
            if isinstance(cve, str) and CVE_RE.fullmatch(cve.strip()):
                found.add(cve.strip().upper())
    return sorted(found)


def article_row(article: dict) -> dict:
    """One article as the flat, JSON-ready row every format starts from."""
    ts = article_ts(article)
    return {
        "id": text(article.get("id")),
        "title": text(article.get("title")).strip(),
        "source": text(article.get("source")).strip(),
        "category": norm(article.get("category")),
        "severity": norm(article.get("severity")),
        "published_utc": _iso(ts) if ts > 0 else "",
        "published_raw": text(article.get("pubDate")).strip(),
        "link": _safe_link(article.get("link")),
        "summary": text(article.get("desc")).strip(),
        "brief": text(article.get("brief")).strip(),
        "note": text(article.get("note")).strip(),
        "cves": _cves(article),
        "kev": bool(article.get("kev")),
        "archived": bool(article.get("archived") or article.get("in_archive")),
        "source_count": source_count(article),
    }


def select_articles(articles, *, hidden_sources=None, category="", severity="",
                    source="", days=None, has_cve=None, kev=None,
                    include_duplicates=False, now: float | None = None) -> list[dict]:
    """Apply the feed's filter rules without Flask or the search module.

    Hidden sources stay out unless the caller names one explicitly (the same
    rule as search). Duplicate rows are skipped unless requested, except for
    archived records, which are deliberate keeps.
    """
    now = time.time() if now is None else float(now)
    hidden = {norm(name) for name in (hidden_sources or []) if norm(name)}
    want_category = norm(category)
    want_severity = norm(severity)
    want_source = norm(source)
    try:
        days = int(days) if days is not None and days != "" else None
    except (TypeError, ValueError):
        days = None
    if days is not None and days <= 0:
        days = None
    cutoff = now - days * 86400 if days else None

    out = []
    for article in (articles or []):
        if not isinstance(article, dict):
            continue
        if not text(article.get("id")):
            continue
        if want_category and norm(article.get("category")) != want_category:
            continue
        if want_severity and norm(article.get("severity")) != want_severity:
            continue
        article_source = norm(article.get("source"))
        if want_source:
            if article_source != want_source:
                continue
        elif hidden and article_source in hidden:
            continue
        ts = article_ts(article)
        if cutoff is not None and (ts <= 0 or ts < cutoff):
            continue
        cves = _cves(article) if has_cve is not None else None
        if has_cve is not None and bool(cves) != bool(has_cve):
            continue
        if kev is not None and bool(article.get("kev")) != bool(kev):
            continue
        if (not include_duplicates and article.get("is_representative") is False
                and not article.get("in_archive")):
            continue
        out.append(article)
        if len(out) >= MAX_ITEMS:
            break
    return out


def _slug(value) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text(value).lower()).strip("-")
    return (slug[:40].strip("-") or "export")


def build_filename(dataset, fmt, *, query="", category="", now=None) -> str:
    """A safe, descriptive, timestamped filename (never a path)."""
    fmt = normalize_format(fmt) or "markdown"
    now = time.time() if now is None else float(now)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d-%H%M%S")
    parts = ["slatewire", _slug(dataset)]
    if category:
        parts.append(_slug(category))
    if query:
        parts.append(_slug(query))
    return "-".join(parts) + "-" + stamp + EXTENSIONS[fmt]


def _metadata(dataset, title, count, *, version="", filters=None, matched=None,
              now=None) -> dict:
    now = time.time() if now is None else float(now)
    meta = {
        "app": "SLATEWIRE",
        "version": text(version),
        "dataset": text(dataset),
        "title": text(title),
        "exported_utc": _iso(now),
        "count": int(count),
        "filters": {str(key): value for key, value in (filters or {}).items()
                    if value not in (None, "", False, [], {})},
    }
    if matched is not None and int(matched) != int(count):
        meta["matched"] = int(matched)
    return meta


def _md_inline(value) -> str:
    """Escape inline Markdown and fold whitespace into one line."""
    flat = re.sub(r"\s+", " ", text(value)).strip()
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", flat)


def _filters_text(filters: dict | None) -> str:
    parts = []
    for key, value in (filters or {}).items():
        if value in (None, "", False, [], {}):
            continue
        parts.append(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
    return ", ".join(parts)


def _csv_safe(value):
    """Stop spreadsheet formula injection and turn booleans into words."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return value
    value = "" if value is None else str(value)
    if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv(rows: list[dict], meta: dict) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_csv_safe(value) for value in (
            meta["dataset"], meta["exported_utc"], meta["version"],
            row["id"], row["title"], row["source"], row["category"], row["severity"],
            row["published_utc"], row["published_raw"], row["link"],
            row["summary"], row["brief"], row["note"],
            " ".join(row["cves"]), row["kev"], row["archived"], row["source_count"],
        )])
    return CSV_BOM + buf.getvalue()


def _article_markdown(row: dict) -> list[str]:
    lines = ["### " + (_md_inline(row["title"]) or "(untitled)"), ""]
    bits = []
    if row["source"]:
        bits.append("**Source:** " + _md_inline(row["source"]))
    if row["category"]:
        bits.append("**Category:** " + _md_inline(row["category"].upper()))
    if row["severity"]:
        bits.append("**Severity:** " + _md_inline(row["severity"]))
    if row["published_utc"]:
        bits.append("**Published:** " + row["published_utc"])
    if row["cves"]:
        bits.append("**CVE:** " + ", ".join(row["cves"]))
    if row["kev"]:
        bits.append("**Known exploited**")
    if row["source_count"] > 1:
        bits.append(f"**{row['source_count']} sources**")
    if row["archived"]:
        bits.append("**Archived**")
    if bits:
        lines.append(" · ".join(bits))
    if row["link"]:
        lines += ["", f"[Open article](<{row['link']}>)"]
    if row["summary"]:
        lines += ["", _md_inline(row["summary"])]
    if row["brief"]:
        lines += ["", "**Brief**", "", _md_inline(row["brief"])]
    if row["note"]:
        lines += ["", "**Your note**", "", _md_inline(row["note"])]
    return lines


def _articles_markdown(rows: list[dict], meta: dict) -> str:
    lines = ["# " + _md_inline(meta["title"]), ""]
    lines.append("- **Exported:** " + meta["exported_utc"])
    if meta["version"]:
        lines.append("- **App:** SLATEWIRE " + _md_inline(meta["version"]))
    lines.append(f"- **Items:** {meta['count']}")
    if meta.get("matched") is not None:
        lines.append(f"- **Matched:** {meta['matched']} (first {meta['count']} exported)")
    filters = _filters_text(meta["filters"])
    if filters:
        lines.append("- **Filters:** " + _md_inline(filters))
    lines += ["", "---", ""]
    for row in rows:
        lines += _article_markdown(row) + ["", "---", ""]
    return "\n".join(lines).rstrip() + "\n"


def render_articles(articles, fmt, *, dataset="feed", title=None, version="",
                    filters=None, query="", category="", matched=None,
                    now=None) -> tuple[str, str, str]:
    """Return (body, filename, content type) for a list of feed/archive rows."""
    fmt = normalize_format(fmt) or "markdown"
    now = time.time() if now is None else float(now)
    rows = [article_row(article) for article in (articles or [])
            if isinstance(article, dict)][:MAX_ITEMS]
    title = title or DEFAULT_TITLES.get(dataset, "SLATEWIRE export")
    meta = _metadata(dataset, title, len(rows), version=version, filters=filters,
                     matched=matched, now=now)

    if fmt == "json":
        body = json.dumps({"metadata": meta, "articles": rows},
                          indent=2, ensure_ascii=False) + "\n"
    elif fmt == "csv":
        body = _csv(rows, meta)
    else:
        body = _articles_markdown(rows, meta)

    filename = build_filename(dataset, fmt, query=query, category=category, now=now)
    return body, filename, MIMETYPES[fmt]


def _digest_markdown(result: dict, meta: dict) -> str:
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    lines = ["# SLATEWIRE digest", ""]
    lines.append("- **Exported:** " + meta["exported_utc"])
    if meta["version"]:
        lines.append("- **App:** SLATEWIRE " + _md_inline(meta["version"]))
    lines.append(f"- **Window:** last {result.get('window_hours', '?')} hours "
                 f"(since {result.get('since') or '?'})")
    lines.append(f"- **Stories:** {meta['count']}")
    lines.append(f"- **Severity:** critical {counts.get('critical', 0)} · "
                 f"high {counts.get('high', 0)} · routine {counts.get('routine', 0)}")
    lines.append(f"- **Known exploited:** {counts.get('kev', 0)} stories · "
                 f"{counts.get('cves', 0)} distinct CVEs · "
                 f"{counts.get('sources', 0)} sources")

    narrative = _md_inline(result.get("narrative"))
    if narrative:
        lines += ["", "## Summary", "", narrative]

    top = result.get("top_stories") or []
    if top:
        lines += ["", "## Top stories", ""]
        for number, story in enumerate(top, 1):
            row = article_row(story) if isinstance(story, dict) else article_row({})
            flags = []
            if row["severity"]:
                flags.append(row["severity"])
            if row["kev"]:
                flags.append("known exploited")
            if row["source_count"] > 1:
                flags.append(f"{row['source_count']} sources")
            suffix = f" ({', '.join(flags)})" if flags else ""
            title = _md_inline(row["title"]) or "(untitled)"
            if row["link"]:
                title = f"[{title}](<{row['link']}>)"
            lines.append(f"{number}. **{title}** — {_md_inline(row['source'])}{suffix}")

    critical = result.get("critical") or []
    if critical:
        lines += ["", "## Critical", ""]
        for story in critical:
            row = article_row(story) if isinstance(story, dict) else article_row({})
            title = _md_inline(row["title"]) or "(untitled)"
            if row["link"]:
                title = f"[{title}](<{row['link']}>)"
            lines.append(f"- **{title}** — {_md_inline(row['source'])}")

    exploited = result.get("exploited") or []
    if exploited:
        lines += ["", "## Known exploited CVEs", "",
                  "| CVE | Vendor | Product | Added | Due | Ransomware |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for item in exploited:
            if not isinstance(item, dict):
                continue
            lines.append("| " + " | ".join(
                _md_inline(item.get(key)) or "—"
                for key in ("cve", "vendor", "product", "dateAdded", "dueDate")
            ) + " | " + ("yes" if item.get("ransomware") else "no") + " |")
    return "\n".join(lines).rstrip() + "\n"


def _digest_csv(result: dict, meta: dict) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(DIGEST_CSV_COLUMNS)
    for rank, story in enumerate(result.get("top_stories") or [], 1):
        if not isinstance(story, dict):
            continue
        row = article_row(story)
        writer.writerow([_csv_safe(value) for value in (
            "digest", meta["exported_utc"], meta["version"], rank,
            row["id"], row["title"], row["source"], row["category"], row["severity"],
            row["published_utc"], row["link"], " ".join(row["cves"]),
            row["kev"], row["source_count"],
        )])
    return CSV_BOM + buf.getvalue()


def render_digest(result, fmt, *, version="", now=None) -> tuple[str, str, str]:
    """Return (body, filename, content type) for a digest result."""
    fmt = normalize_format(fmt) or "markdown"
    now = time.time() if now is None else float(now)
    result = result if isinstance(result, dict) else {}
    meta = {
        "app": "SLATEWIRE",
        "version": text(version),
        "dataset": "digest",
        "title": "SLATEWIRE digest",
        "exported_utc": _iso(now),
        "count": int(result.get("total") or 0),
        "window_hours": result.get("window_hours"),
        "since": result.get("since") or "",
        "counts": result.get("counts") if isinstance(result.get("counts"), dict) else {},
    }

    if fmt == "json":
        body = json.dumps({"metadata": meta, "digest": result},
                          indent=2, ensure_ascii=False) + "\n"
    elif fmt == "csv":
        body = _digest_csv(result, meta)
    else:
        body = _digest_markdown(result, meta)

    filename = build_filename("digest", fmt, now=now)
    return body, filename, MIMETYPES[fmt]
