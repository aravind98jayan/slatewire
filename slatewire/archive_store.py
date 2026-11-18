"""
Keeps your saved articles in archive.json (so the app can check "is this
archived" and list/remove things), and mirrors the same data into
archive.txt in plain readable form any time it changes -- so you can open
archive.txt directly, outside the app, if you just want to read what you've
saved.

Limits (so a bad request or a runaway client can't bloat the file):
at most MAX_ITEMS saved articles, and every text field is cut to a sane length.
"""
from __future__ import annotations

from datetime import datetime, timezone

import safe_io

JSON_PATH = "archive.json"
TXT_PATH = "archive.txt"

MAX_ITEMS = 2000
MAX_NOTE = 2000
# field -> max length; anything not listed here is not stored
FIELD_LIMITS = {"id": 64, "title": 400, "link": 2000, "pubDate": 100, "desc": 1000, "source": 100,
                "severity": 20, "category": 20, "brief": 6000, "note": MAX_NOTE}
NUMBER_FIELDS = ("published_ts", "first_seen_ts")


class ArchiveFull(Exception):
    pass


def _load() -> dict:
    data = safe_io.read_json(JSON_PATH, {})
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    safe_io.write_json(JSON_PATH, data, backup=True, indent=2)
    _write_txt_mirror(data)


def _write_txt_mirror(data: dict) -> None:
    items = sorted(data.values(), key=lambda a: a.get("archived_at", ""), reverse=True)
    lines = []
    for a in items:
        lines.append(a.get("title", "(untitled)"))
        lines.append(f"{a.get('source','')} · {a.get('pubDate','')} · saved {a.get('archived_at','')}")
        if a.get("note"):
            lines.append("")
            lines.append("Note: " + str(a["note"]))
        if a.get("brief"):
            lines.append("")
            lines.append(str(a["brief"]))
        elif a.get("desc"):
            lines.append("")
            lines.append(str(a["desc"]))
        lines.append("")
        lines.append(a.get("link", ""))
        lines.append("-" * 60)
        lines.append("")
    safe_io.write_text(TXT_PATH, "\n".join(lines) if lines else "No archived articles yet.\n")


def clean(article: dict) -> dict:
    """Keep only known fields, as text of limited length."""
    out = {}
    for k, limit in FIELD_LIMITS.items():
        v = article.get(k)
        if isinstance(v, str):
            out[k] = v[:limit]
    for k in NUMBER_FIELDS:
        v = article.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
    return out


def list_all() -> list[dict]:
    return list(_load().values())


def add(article: dict) -> None:
    article = clean(article)
    if not article.get("id"):
        raise ValueError("article has no id")
    with safe_io.LOCK:
        data = _load()
        if article["id"] not in data and len(data) >= MAX_ITEMS:
            raise ArchiveFull(f"archive is full ({MAX_ITEMS} articles) -- remove some first")
        article["archived_at"] = datetime.now(timezone.utc).isoformat()
        article.setdefault("note", "")
        data[article["id"]] = article
        _save(data)


def remove(article_id: str) -> None:
    with safe_io.LOCK:
        data = _load()
        if article_id in data:
            del data[article_id]
            _save(data)


def clear() -> None:
    with safe_io.LOCK:
        _save({})


def is_archived(article_id: str) -> bool:
    return article_id in _load()


def update_note(article_id: str, note: str) -> bool:
    """Update just the note field on an already-archived article.
    Returns False if the article isn't archived."""
    with safe_io.LOCK:
        data = _load()
        if article_id not in data:
            return False
        data[article_id]["note"] = str(note)[:MAX_NOTE]
        _save(data)
        return True
