"""
Full-text search over the live feed and the archive.

This module is deliberately Flask-free: the endpoint in app.py hands it plain
lists of articles and gets back a plain dict, which makes the scoring and
filter rules easy to test without a server or a network.

What is searched
----------------
* title, description, AI brief and archive note (all case-insensitive)
* the source name (so "SANS" finds the SANS ISC stories)
* CVE ids are matched as ordinary text, and also drive the has_cve filter

How matching works
------------------
Every word of the query must appear somewhere in the article (AND), which
keeps "SonicWall MFA" from returning every SonicWall story ever written.
Articles are then ranked by where the words appear -- a title hit is worth
much more than a brief hit -- with a small recency boost so equally relevant
stories put the newest first. Quoted phrases are matched as one term.

Live and archived copies of the same id are merged into one record, so a story
that is in both places appears once and is searchable through both its old
note and its current brief. Only representatives are shown by default (the
same rule as the feed); `include_duplicates=True` opts back in. Sources hidden
in Settings are skipped too, unless the search names that source explicitly.

A quoted phrase has to appear inside one field -- it will not match half the
phrase in the title and the other half in the description.
"""
from __future__ import annotations

import re
import time
from collections import Counter

from articles import article_ts as _article_ts
from articles import norm as _norm
from articles import text as _text

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_QUERY = 200

# Where a query word is found, and how much that is worth. A headline hit is
# the strongest signal; a brief/note hit is useful but weaker.
FIELD_WEIGHTS = {
    "title": 10.0,
    "description": 4.0,
    "brief": 3.0,
    "note": 3.0,
    "source": 2.0,
}
SNIPPET_FIELDS = ("description", "brief", "note")
SNIPPET_BEFORE = 70
SNIPPET_AFTER = 150


def parse_terms(query: str) -> list[str]:
    """Split a query into unique lower-case terms; quoted text stays one phrase."""
    query = _text(query).strip()
    if not query:
        return []
    terms: list[str] = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query):
        term = (phrase or word).strip().strip(".,;:!?()[]{}<>\"'").lower()
        # Repeating a word must not weight it twice, and must not crowd other
        # real terms out of the query.
        if term and term not in terms:
            terms.append(term)
    return terms


def _article_cves(article: dict) -> set[str]:
    parts = [_text(article.get("title")), _text(article.get("desc")),
             _text(article.get("brief")), _text(article.get("note"))]
    found = {m.group(0).upper() for part in parts for m in CVE_RE.finditer(part)}
    for cve in article.get("kev_cves") or []:
        if isinstance(cve, str) and CVE_RE.fullmatch(cve.strip()):
            found.add(cve.strip().upper())
    return found


def _merge(live: list[dict] | None, archive: list[dict] | None,
           briefs: dict | None) -> dict[str, dict]:
    """Merge live and archived articles by id without mutating the inputs."""
    records: dict[str, dict] = {}

    for article in live or []:
        if not isinstance(article, dict):
            continue
        article_id = _text(article.get("id"))
        if not article_id:
            continue
        record = dict(article)
        record["_in_live"] = True
        record["_in_archive"] = False
        records[article_id] = record

    for article in archive or []:
        if not isinstance(article, dict):
            continue
        article_id = _text(article.get("id"))
        if not article_id:
            continue
        record = records.get(article_id)
        if record is None:
            record = dict(article)
            record["_in_live"] = False
            records[article_id] = record
        record["_in_archive"] = True
        # The saved note and the saved brief are the user's own copy, so they
        # win over whatever the live feed has now.
        for key in ("brief", "note"):
            if _text(article.get(key)):
                record[key] = article[key]
        # Fill anything the live copy lacks (an archive-only story).
        for key in ("title", "desc", "source", "link", "pubDate", "severity", "category"):
            if not _text(record.get(key)) and _text(article.get(key)):
                record[key] = article[key]
        if not record.get("published_ts") and article.get("published_ts"):
            record["published_ts"] = article["published_ts"]

    # A brief can be cached before the user archives the story.
    for article_id, record in records.items():
        if _text(record.get("brief")):
            continue
        for suffix in (":full", ":hotnews"):
            brief = (briefs or {}).get(article_id + suffix)
            if isinstance(brief, str) and brief.strip():
                record["brief"] = brief
                break
    return records


def _fields(record: dict) -> dict[str, str]:
    return {
        "title": _text(record.get("title")),
        "description": _text(record.get("desc")),
        "brief": _text(record.get("brief")),
        "note": _text(record.get("note")),
        "source": _text(record.get("source")),
    }


def _score_fields(fields: dict[str, str], terms: list[str]) -> tuple[float, set[str]]:
    score = 0.0
    matched: set[str] = set()
    for name, text in fields.items():
        low = text.lower()
        hits = 0
        occurrences = 0
        for term in terms:
            count = low.count(term)
            if count:
                hits += 1
                occurrences += count
        if not hits:
            continue
        matched.add(name)
        # Repeated mentions help, but only a little: a brief that says
        # "SonicWall" five times should not beat a headline that names it.
        score += FIELD_WEIGHTS[name] * (hits + min(occurrences - hits, 4) * 0.25)
    return score, matched


def _score(record: dict, fields: dict[str, str], terms: list[str],
           now: float) -> tuple[float, set[str]]:
    score, matched = _score_fields(fields, terms)
    title_low = fields["title"].lower()
    description_low = fields["description"].lower()
    # Joined terms (not the raw query) so quotes/punctuation cannot stop the
    # phrase bonus from firing, and repeating a word does not reweight it.
    phrase = " ".join(terms)

    # The whole query as a phrase is a strong signal, especially in a headline.
    if phrase:
        if phrase in title_low:
            score += 12.0
        elif phrase in description_low:
            score += 4.0
        if phrase in fields["brief"].lower():
            score += 2.0

    # Newer stories win ties; the boost fades out over about ten days.
    ts = _article_ts(record)
    if ts > 0:
        age_days = max(0.0, (now - ts) / 86400.0)
        score += max(0.0, 2.0 - age_days / 5.0)

    # A known-exploited story is more actionable than a theoretical one, but
    # relevance still decides the order.
    if record.get("kev"):
        score += 0.5
    return score, matched


def _snippet(fields: dict[str, str], terms: list[str]) -> tuple[str, str]:
    """Return (snippet, field) from the most useful matching body field."""
    for name in SNIPPET_FIELDS:
        text = fields[name].strip()
        if not text:
            continue
        low = text.lower()
        positions = [low.find(term) for term in terms if low.find(term) >= 0]
        if not positions:
            continue
        pos = min(positions)
        start = max(0, pos - SNIPPET_BEFORE)
        end = min(len(text), pos + SNIPPET_AFTER)
        piece = text[start:end]
        piece = re.sub(r"\s+", " ", piece).strip()
        if start > 0:
            piece = "…" + piece
        if end < len(text):
            piece = piece + "…"
        return piece, name
    return "", ""


def search(query: str,
           live: list[dict] | None = None,
           archive: list[dict] | None = None,
           briefs: dict | None = None,
           *,
           hidden_sources: list[str] | None = None,
           category: str | None = None,
           severity: str | None = None,
           source: str | None = None,
           days: int | None = None,
           has_cve: bool | None = None,
           kev: bool | None = None,
           scope: str = "all",
           include_duplicates: bool = False,
           limit: int = DEFAULT_LIMIT,
           now: float | None = None) -> dict:
    """Run one search and return a JSON-ready result dict.

    All filter arguments are optional. An empty query returns no results (the
    page keeps its instant title filter for that case), so a stray request
    cannot dump the whole corpus.
    """
    now = time.time() if now is None else float(now)
    query = _text(query).strip()[:MAX_QUERY]
    terms = parse_terms(query)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(MAX_LIMIT, limit))

    out = {
        "query": query,
        "terms": terms,
        "total": 0,
        "count": 0,
        "limit": limit,
        "results": [],
        "facets": {"categories": {}, "severities": {}, "sources": {}},
    }
    if not terms:
        return out

    category = _norm(category) or None
    severity = _norm(severity) or None
    source = _norm(source) or None
    hidden = {_norm(name) for name in hidden_sources or [] if _norm(name)}
    scope = _norm(scope)
    if scope not in ("live", "archive", "all"):
        scope = "all"
    days = (int(days) if isinstance(days, int) and not isinstance(days, bool)
            and days > 0 else None)
    cutoff = now - days * 86400 if days else None

    records = _merge(live, archive, briefs)
    matched_records: list[dict] = []

    for record in records.values():
        fields = _fields(record)
        # Every term must appear in at least one field. Checking field by field
        # (instead of one joined string) stops a quoted phrase from matching
        # across a boundary, e.g. "SonicWall MFA" split over title + desc.
        texts = [value.lower() for value in fields.values()]
        if any(not any(term in text for text in texts) for term in terms):
            continue

        if category and _norm(record.get("category")) != category:
            continue
        if severity and _norm(record.get("severity")) != severity:
            continue
        record_source = _norm(record.get("source"))
        if source and record_source != source:
            continue
        # Match the feed: a muted source stays out of the results, unless the
        # caller asked for that exact source in the filter.
        if hidden and not source and record_source in hidden:
            continue
        ts = _article_ts(record)
        if cutoff is not None and (ts <= 0 or ts < cutoff):
            continue

        cves = _article_cves(record)
        if has_cve is not None and bool(cves) != bool(has_cve):
            continue
        if kev is not None and bool(record.get("kev")) != bool(kev):
            continue
        if scope == "live" and not record["_in_live"]:
            continue
        if scope == "archive" and not record["_in_archive"]:
            continue
        # Archived duplicates are deliberate keeps, so they stay visible even
        # when the feed's duplicate rows are hidden.
        if (not include_duplicates and record.get("is_representative") is False
                and not record["_in_archive"]):
            continue

        score, matched = _score(record, fields, terms, now)
        snippet, snippet_field = _snippet(fields, terms)
        matched_records.append({
            "record": record,
            "score": score,
            "matched": matched,
            "snippet": snippet,
            "snippet_field": snippet_field,
            "cves": cves,
            "ts": ts,
        })

    matched_records.sort(key=lambda r: (-r["score"], -r["ts"],
                                        _text(r["record"].get("id"))))

    facets = {
        "categories": Counter(_norm(r["record"].get("category")) for r in matched_records),
        "severities": Counter(_norm(r["record"].get("severity")) for r in matched_records),
        "sources": Counter(_text(r["record"].get("source")).strip() for r in matched_records),
    }

    results = []
    for entry in matched_records[:limit]:
        record = entry["record"]
        item = {key: value for key, value in record.items() if not key.startswith("_")}
        item.update({
            "score": round(entry["score"], 3),
            "snippet": entry["snippet"],
            "snippet_field": entry["snippet_field"],
            "matched_fields": sorted(entry["matched"]),
            "in_live": record["_in_live"],
            "in_archive": record["_in_archive"],
            "archived": record["_in_archive"],
        })
        results.append(item)

    out["total"] = len(matched_records)
    out["count"] = len(results)
    out["results"] = results
    out["facets"] = {
        key: {name: count for name, count in values.items() if name}
        for key, values in facets.items()
    }
    return out
