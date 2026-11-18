"""
Daily digest: a one-page summary of the last N hours of the live feed.

Like search.py this module is Flask-free. The endpoint in app.py hands it the
current feed and gets back a JSON-ready dict, so the counting and ranking rules
can be tested without a server or a network.

The digest is built entirely locally:

* headline counts (total, critical/high/routine, known-exploited, distinct CVEs)
* per-category and per-source counts
* the top stories, the critical ones, and the known-exploited ones
* a short plain-English narrative

An AI-written paragraph is optional and lives in app.py; this module never
talks to the network.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime, timezone

from articles import article_ts, norm, source_count, text

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

WINDOW_HOURS_DEFAULT = 24
WINDOW_HOURS_MAX = 168          # the feed itself keeps about five days
TOP_DEFAULT = 5
TOP_MAX = 20
SEVERITY_RANK = {"critical": 0, "high": 1, "routine": 2}

# Fields a digest row hands back to the page so a result can be opened with
# the same card, badges and archive button as a feed row.
SUMMARY_FIELDS = (
    "id", "title", "desc", "source", "category", "severity",
    "published_ts", "pubDate", "link",
    "kev", "kev_cves", "kev_details", "kev_ransomware",
    "is_representative", "dupe_count", "source_count", "also_reported_by",
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _rank(article: dict) -> tuple:
    """Most severe first, then known-exploited, then most-covered, then newest."""
    return (
        SEVERITY_RANK.get(norm(article.get("severity")), 3),
        0 if article.get("kev") else 1,
        -source_count(article),
        -article_ts(article),
        text(article.get("id")),
    )


def _summary(article: dict) -> dict:
    row = {key: article[key] for key in SUMMARY_FIELDS if key in article}
    row["title"] = text(article.get("title"))
    row["source"] = text(article.get("source"))
    row["source_count"] = source_count(article)
    return row


def _cves(article: dict) -> set[str]:
    """CVE ids mentioned in the article, plus any KEV ids already attached."""
    found = {m.group(0).upper()
             for m in CVE_RE.finditer(text(article.get("title")) + " "
                                      + text(article.get("desc")))}
    known = article.get("kev_cves")
    if not isinstance(known, list):
        known = []
    for cve in known:
        if isinstance(cve, str) and CVE_RE.fullmatch(cve.strip()):
            found.add(cve.strip().upper())
    return found


def _exploited(articles: list[dict]) -> list[dict]:
    """One row per distinct known-exploited CVE, newest addition first."""
    by_cve: dict[str, dict] = {}
    for article in articles:
        details = article.get("kev_details")
        known = article.get("kev_cves")
        if not isinstance(known, list):
            known = []
        if not article.get("kev") or not (isinstance(details, list) or known):
            continue
        detail_by_cve = {}
        if isinstance(details, list):
            for detail in details:
                if isinstance(detail, dict):
                    cve = text(detail.get("cve")).strip().upper()
                    if cve:
                        detail_by_cve[cve] = detail
        for cve in sorted({text(c).strip().upper() for c in known if text(c).strip()}):
            detail = detail_by_cve.get(cve, {})
            entry = by_cve.get(cve)
            # Keep the richest copy of the CVE (some articles carry full
            # vendor/product data, others only the id).
            if entry is None or len(detail) > entry["_detail_size"]:
                by_cve[cve] = {
                    "cve": cve,
                    "vendor": text(detail.get("vendor")),
                    "product": text(detail.get("product")),
                    "name": text(detail.get("name")),
                    "dateAdded": text(detail.get("dateAdded")),
                    "dueDate": text(detail.get("dueDate")),
                    "ransomware": bool(detail.get("ransomware")),
                    "article_id": text(article.get("id")),
                    "title": text(article.get("title")),
                    "source": text(article.get("source")),
                    "published_ts": article_ts(article),
                    "_detail_size": len(detail),
                }
    rows = sorted(by_cve.values(),
                  key=lambda r: (r["dateAdded"], -r["published_ts"], r["cve"]),
                  reverse=True)
    for row in rows:
        row.pop("_detail_size", None)
    return rows


def _narrative(hours: int, total: int, counts: dict, sources: int,
               top: dict | None, exploited: list[dict]) -> str:
    window = "the last hour" if hours == 1 else f"the last {hours} hours"
    if not total:
        return f"No new stories arrived in {window}."

    parts = [f"In {window} SLATEWIRE collected {total} "
             f"{'story' if total == 1 else 'stories'} from {sources} "
             f"{'source' if sources == 1 else 'sources'}."]

    severity = []
    if counts.get("critical"):
        severity.append(f"{counts['critical']} critical")
    if counts.get("high"):
        severity.append(f"{counts['high']} high")
    if severity:
        parts.append("Severity mix: " + ", ".join(severity) + ".")

    if exploited:
        names = ", ".join(row["cve"] for row in exploited[:3])
        extra = "" if len(exploited) <= 3 else f" and {len(exploited) - 3} more"
        parts.append(f"{len(exploited)} known-exploited "
                     f"{'CVE' if len(exploited) == 1 else 'CVEs'} "
                     f"{'is' if len(exploited) == 1 else 'are'} in the feed "
                     f"({names}{extra}).")

    if top:
        parts.append(f"Most covered: \u201c{top['title']}\u201d ({top['source']}).")
    return " ".join(parts)


def build(live: list[dict] | None = None, *,
          hours: int = WINDOW_HOURS_DEFAULT,
          top: int = TOP_DEFAULT,
          hidden_sources: list[str] | None = None,
          include_duplicates: bool = False,
          now: float | None = None) -> dict:
    """Build one digest from the flat list of live-feed articles."""
    now = time.time() if now is None else float(now)
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = WINDOW_HOURS_DEFAULT
    hours = max(1, min(WINDOW_HOURS_MAX, hours))
    try:
        top = int(top)
    except (TypeError, ValueError):
        top = TOP_DEFAULT
    top = max(1, min(TOP_MAX, top))

    cutoff = now - hours * 3600
    hidden = {norm(name) for name in hidden_sources or [] if norm(name)}
    articles: list[dict] = []
    seen: set[str] = set()
    for article in live or []:
        if not isinstance(article, dict):
            continue
        article_id = text(article.get("id"))
        if not article_id or article_id in seen:
            continue
        ts = article_ts(article)
        if ts <= 0 or ts < cutoff:
            continue
        if not include_duplicates and article.get("is_representative") is False:
            continue
        if norm(article.get("source")) in hidden:
            continue
        seen.add(article_id)
        articles.append(article)

    categories = Counter(norm(a.get("category")) for a in articles)
    sources = Counter(text(a.get("source")).strip() for a in articles)
    severities = Counter(norm(a.get("severity")) for a in articles)
    cves: set[str] = set()
    for article in articles:
        cves.update(_cves(article))

    ranked = sorted(articles, key=_rank)
    top_stories = [_summary(a) for a in ranked[:top]]
    critical = [_summary(a) for a in ranked
                if norm(a.get("severity")) == "critical"][:top]
    kev_stories = [_summary(a) for a in ranked if a.get("kev")][:top]
    exploited = _exploited(articles)

    counts = {
        "critical": severities.get("critical", 0),
        "high": severities.get("high", 0),
        "routine": severities.get("routine", 0),
        "other": sum(v for k, v in severities.items() if k not in SEVERITY_RANK),
        "kev": sum(1 for a in articles if a.get("kev")),
        "cves": len(cves),
        "sources": sum(1 for name in sources if name),
    }

    source_rows = [{"name": name, "count": count}
                   for name, count in sources.most_common(top)]
    category_rows = [{"id": name, "count": count}
                     for name, count in categories.most_common() if name]

    return {
        "generated_at": _iso(now),
        "window_hours": hours,
        "since": _iso(cutoff),
        "total": len(articles),
        "counts": counts,
        "categories": category_rows,
        "sources": source_rows,
        "top_stories": top_stories,
        "critical": critical,
        "kev_stories": kev_stories,
        "exploited": exploited,
        "narrative": _narrative(hours, len(articles), counts,
                                counts["sources"],
                                top_stories[0] if top_stories else None,
                                exploited),
    }
