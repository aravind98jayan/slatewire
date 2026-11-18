"""
CISA Known Exploited Vulnerabilities (KEV) enrichment.

The KEV catalog is the authoritative list of CVEs that are being exploited
in the wild. Checking every article against it turns a plain vulnerability
mention into "this one is already being used by attackers", which is the
single most useful severity signal for a news reader.

The catalog is downloaded from CISA (public, no API key, no account) and kept
in a small local cache file (`kev_cache.json`) so the app refreshes it at most
once a day and still works offline with the last copy it had.

Article fields added (additive only):

    kev              True when any CVE in the article is in the catalog
    kev_cves         sorted list of the matching CVE ids
    kev_details      [{cve, vendor, product, name, dateAdded, dueDate,
                       ransomware}, ...] for each match
    kev_ransomware   True when at least one match is tied to ransomware

Nothing here raises: a missing network, a damaged cache or a changed feed
format degrades to "no KEV data" and the rest of the app keeps working.
"""
from __future__ import annotations

import json
import re
import time

import safe_io

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
KEV_CACHE_PATH = "kev_cache.json"

# CISA updates the catalog a few times a week; once a day is plenty and keeps
# the app usable offline. A failed download is retried sooner (one hour).
KEV_MAX_AGE = 24 * 3600
KEV_RETRY_AFTER_FAILURE = 3600
KEV_MAX_BYTES = 16 * 1024 * 1024
KEV_TOTAL_TIMEOUT = 20
KEV_USER_AGENT = "SLATEWIRE/1.8 (local news reader; +https://www.cisa.gov/known-exploited-vulnerabilities-catalog)"

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)


def _download() -> dict:
    """Fetch the raw catalog JSON from CISA. Split out so tests can stub it."""
    body, _encoding = safe_io.download_limited(
        KEV_URL,
        headers={"User-Agent": KEV_USER_AGENT, "Accept": "application/json"},
        max_bytes=KEV_MAX_BYTES,
        total_seconds=KEV_TOTAL_TIMEOUT,
        connect_timeout=4,
        read_timeout=10,
    )
    data = json.loads(body.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("KEV catalog is not a JSON object")
    return data


def _parse(raw: dict) -> dict:
    """Turn CISA's schema into {CVE: compact entry}. Unknown shapes are skipped."""
    items = raw.get("vulnerabilities") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        cve = str(item.get("cveID") or "").strip().upper()
        if not CVE_RE.fullmatch(cve):
            continue
        out[cve] = {
            "cve": cve,
            "vendor": str(item.get("vendorProject") or ""),
            "product": str(item.get("product") or ""),
            "name": str(item.get("vulnerabilityName") or ""),
            "dateAdded": str(item.get("dateAdded") or ""),
            "dueDate": str(item.get("dueDate") or ""),
            "ransomware": str(item.get("knownRansomwareCampaignUse") or "").strip().lower() == "known",
        }
    return out


def _read_cache(path: str) -> tuple[dict, dict] | None:
    """Return (catalog, meta) from a valid cache file, else None."""
    cached = safe_io.read_json(path, None)
    if not isinstance(cached, dict):
        return None
    catalog = cached.get("vulnerabilities")
    fetched_at = cached.get("fetched_at")
    if not isinstance(catalog, dict) or not isinstance(fetched_at, (int, float)):
        return None
    ok = bool(cached.get("ok", True))
    return catalog, {
        "ok": ok,
        "stale": False,
        "fetched_at": float(fetched_at),
        "source": KEV_URL,
    }


def load_catalog(path: str | None = None, max_age: int | None = None,
                 allow_download: bool = True) -> tuple[dict, dict]:
    """Return (catalog, meta). Uses the cache while it is fresh; otherwise tries
    CISA and falls back to the last good copy. Never raises."""
    path = path or KEV_CACHE_PATH
    max_age = KEV_MAX_AGE if max_age is None else max_age
    now = time.time()
    cached = _read_cache(path)

    if cached is not None:
        catalog, meta = cached
        age = now - meta["fetched_at"]
        limit = max_age if meta["ok"] else KEV_RETRY_AFTER_FAILURE
        if meta["ok"] and age < limit:
            return catalog, meta
        if not meta["ok"] and age < limit:
            return catalog, meta
        if not allow_download:
            meta["stale"] = True
            return catalog, meta

    if not allow_download:
        return (cached[0], {**cached[1], "stale": True}) if cached else (
            {}, {"ok": False, "stale": True, "fetched_at": 0, "source": KEV_URL,
                 "error": "offline"})

    try:
        raw = _download()
        catalog = _parse(raw)
        if not catalog:
            raise ValueError("KEV catalog contained no usable CVEs")
        meta = {"ok": True, "stale": False, "fetched_at": now, "source": KEV_URL}
        try:
            safe_io.write_json(path, {
                "fetched_at": now,
                "ok": True,
                "source": KEV_URL,
                "catalog_size": len(catalog),
                "vulnerabilities": catalog,
            })
        except OSError:
            pass
        return catalog, meta
    except Exception as exc:   # noqa: BLE001 - degrade to the last good copy
        if cached is not None and cached[0]:
            meta = {**cached[1], "ok": False, "stale": True, "error": str(exc)}
            return cached[0], meta
        meta = {"ok": False, "stale": True, "fetched_at": now, "source": KEV_URL,
                "error": str(exc)}
        try:
            safe_io.write_json(path, {
                "fetched_at": now,
                "ok": False,
                "source": KEV_URL,
                "catalog_size": 0,
                "vulnerabilities": {},
            })
        except OSError:
            pass
        return {}, meta


def article_cves(article: dict) -> set[str]:
    """Every CVE id in an article's title or summary, upper-cased."""
    text = str(article.get("title") or "") + " " + str(article.get("desc") or "")
    return {m.group(0).upper() for m in CVE_RE.finditer(text)}


def annotate_article(article: dict, catalog: dict) -> list[str]:
    """Add the KEV fields to one article; return the CVE ids that matched."""
    matches = sorted(article_cves(article) & catalog.keys())
    details = [catalog[cve] for cve in matches]
    article["kev"] = bool(matches)
    article["kev_cves"] = matches
    article["kev_details"] = details
    article["kev_ransomware"] = any(d.get("ransomware") for d in details)
    return matches


def enrich_categories(buckets: dict[str, list[dict]], catalog: dict | None = None,
                      *, online: bool = True) -> dict:
    """Annotate every article in a categories dict and return summary stats.

    `online=False` (used when the fetch already showed the PC is offline) only
    reads the local cache and never waits on a download."""
    if catalog is None:
        catalog, meta = load_catalog(allow_download=online)
    else:
        meta = {"ok": True, "stale": False, "fetched_at": time.time(), "source": KEV_URL}

    matched_articles = 0
    matched_cves: set[str] = set()
    ransomware_articles = 0
    seen: set[int] = set()
    articles: list[dict] = []
    for items in buckets.values():
        if not isinstance(items, list):
            continue
        for article in items:
            if not isinstance(article, dict) or id(article) in seen:
                continue
            seen.add(id(article))
            articles.append(article)
            matches = annotate_article(article, catalog)
            if matches:
                matched_articles += 1
                matched_cves.update(matches)
                if article.get("kev_ransomware"):
                    ransomware_articles += 1

    # The page shows only one representative per same-story cluster, and the
    # KEV match can sit on a non-representative member (the representative may
    # not spell out the CVE). Share the flag across the cluster so the visible
    # row says the story is known-exploited too.
    by_cluster: dict[str, list[dict]] = {}
    for article in articles:
        cid = article.get("cluster_id")
        if cid:
            by_cluster.setdefault(str(cid), []).append(article)
    propagated_articles = 0
    matched_clusters = 0
    for members in by_cluster.values():
        cves: set[str] = set()
        details: dict[str, dict] = {}
        ransom = False
        for article in members:
            cves.update(article.get("kev_cves") or [])
            for detail in article.get("kev_details") or []:
                if isinstance(detail, dict) and detail.get("cve"):
                    details[detail["cve"]] = detail
            ransom = ransom or bool(article.get("kev_ransomware"))
        if not cves:
            continue
        matched_clusters += 1
        ordered = [details[cve] for cve in sorted(details)]
        for article in members:
            if not article.get("kev"):
                propagated_articles += 1
            article["kev"] = True
            article["kev_cves"] = sorted(cves)
            article["kev_details"] = ordered
            article["kev_ransomware"] = ransom

    return {
        "catalog_size": len(catalog),
        "checked_articles": len(seen),
        "matched_articles": matched_articles,
        "flagged_articles": matched_articles + propagated_articles,
        "matched_clusters": matched_clusters,
        "matched_cves": len(matched_cves),
        "ransomware_articles": ransomware_articles,
        "source": KEV_URL,
        "ok": bool(meta.get("ok")),
        "stale": bool(meta.get("stale")),
        "fetched_at": meta.get("fetched_at") or 0,
        "error": meta.get("error", ""),
    }
