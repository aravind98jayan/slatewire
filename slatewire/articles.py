"""
Small helpers shared by the article-based feature modules.

Standard library only, so search.py, digest.py and any future module can use
the same date/type handling without importing Flask.
"""
from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime


def text(value) -> str:
    return value if isinstance(value, str) else ""


def norm(value) -> str:
    return text(value).strip().lower()


def article_ts(article: dict) -> float:
    """Best-effort publish time as a Unix timestamp (0 when unknown)."""
    ts = article.get("published_ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return float(ts)
    pub = text(article.get("pubDate")).strip()
    if pub:
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    return 0.0


def source_count(article: dict) -> int:
    """How many outlets reported this story (never less than 1)."""
    try:
        count = int(article.get("source_count") or 0)
        if count > 0:
            return count
        return max(1, int(article.get("dupe_count") or 0) + 1)
    except (TypeError, ValueError):
        return 1
