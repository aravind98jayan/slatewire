"""
Same-story clustering (dedupe v1).

A story that several outlets pick up at once can appear in the feed three or
four times with different headlines. This module groups those articles so the
page can show one row ("3 sources") instead of repeating the same event.

It runs after classification and before news_data.json is written, so both the
API and the page see the extra fields. Nothing is removed: every article stays
in its category, the fields only say which article represents the cluster.

Additive article fields:

    cluster_id        stable id for the story (all members share it)
    is_representative True for the one article the page shows by default
    dupe_count        number of other articles in the same cluster
    source_count      number of distinct outlets in the cluster
    also_reported_by  [{source, title, link, pubDate, published_ts}, ...]

A cluster is built with three cheap signals (no external services):

* shared CVE ids are the strongest signal,
* TF-IDF cosine similarity of the headlines (plus token Jaccard),
* publication times more than 72 hours apart are never merged.

The representative is the earliest published article, with the most complete
description breaking a tie. That keeps the "original" report at the top and
still lists the later coverage under "also reported by".
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict

# Blended TF-IDF/Jaccard score. It is deliberately paired with the "at least
# two shared headline words" gate below: two unrelated advisory templates can
# score 0.4+ while sharing only a brand, but they do not share two content
# words. Tuned on tests/validation_pairs.json (precision first).
DEFAULT_TITLE_THRESHOLD = 0.18
DESC_CONFIRM_THRESHOLD = 0.35   # one shared headline word + a very close summary
DEFAULT_WINDOW_SECONDS = 72 * 3600

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9]+")

# Common English words only. "new" is in here because it appears in a large
# share of headlines and says nothing about which story this is.
STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has
have he her here hers him his how i if in into is it its itself just like may
me might more most my new no nor not now of off on once only or other our out
over own said same she should so some such than that the their theirs them then
there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your after
""".split())

# Feed boilerplate. Government advisory feeds and newsletters reuse the same
# headline template for every item ("Security Alert (A26-11-08): Multiple
# Vulnerabilities in ..."), so these words must not count as shared content.
# The distinctive words (vendor, product, campaign) are what is left.
GENERIC = frozenset("""
security cyber cybersecurity alert alerts advisory advisories bulletin bulletins
multiple various product products vulnerability vulnerabilities vuln vulns
update updates updated patch patches patched fix fixes fixed
new latest report reports reported news round rounds roundup newsletter newsletters weekly
podcast podcasts stormcast episode episodes
monday tuesday wednesday thursday friday saturday sunday
mon tue tues wed thu thur thurs fri sat sun
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
ai breach breaches customer customers user users expos expose exposes exposed exposure
leak leaks leaked stolen compromise compromised hack hacking hacker hackers
launch launches launched bug bugs key keys code codes execution execute executes flaw flaws
scan scans scanned scanning
isc sans affair ncsc verholpen
auf an und mit von fur der die das den dem des ein eine einen einem einer
ist sind wird werden hat haben bei aus nach uber fur vor zum zur kann konnen
aber oder nicht noch wie warum jetzt angriff angriffe attacken
kriticke kriticky kriticka kritick zranitelnost zranitelnosti zranitelne
varovanie varovani utocnici utocnik utocnictvi aktivne aktivni zneuzivaju zneuziti
bezpecnost bezpecnosti bezpecnostni doporucuje doporuceni aktualizace aktualizacia
riziko rizika hrozba hrozby napadeni oprava opravy
krytyczne krytyczna krytyczny podatnosc podatnosci ostrzezenie atak ataki aktualizacja
vulnerabilite vulnerabilites vulnerabilidad vulnerabilidade vulnerabilita
critique critiques critica criticas critico
aviso alerta alerte avviso allerta actualizacion aggiornamento
kwetsbaarheid kwetsbaarheden waarschuwing aanval
""".split())


class _DisjointSet:
    """Small union-find used to merge pairwise matches into clusters."""

    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, i: int) -> int:
        parent = self.parent
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def _stem(token: str) -> str:
    """Very light plural stemming -- enough for headline / headlines, patch / patches."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes", "ses")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    """Lower-case headline words without URLs, CVE ids, stopwords, feed
    boilerplate, dates or bare numbers. Accents are folded so German/French/
    Spanish words still compare with the ASCII stoplists."""
    clean = unicodedata.normalize("NFKD", str(text or ""))
    clean = "".join(ch for ch in clean if not unicodedata.combining(ch)).lower()
    clean = URL_RE.sub(" ", clean)
    clean = CVE_RE.sub(" ", clean)
    out = []
    for word in WORD_RE.findall(clean):
        if word.isdigit() or len(word) < 2:
            continue
        if re.fullmatch(r"\d+(?:st|nd|rd|th)", word):   # 5th, 6th
            continue
        if re.fullmatch(r"av?\d+", word):               # advisory ids A26, AV26
            continue
        word = _stem(word)
        if len(word) < 2 or word in STOPWORDS or word in GENERIC:
            continue
        out.append(word)
    return out


def _cves(text: str) -> set[str]:
    return {m.group(0).upper() for m in CVE_RE.finditer(str(text or ""))}


def _prepare(article: dict) -> dict:
    title = article.get("title") or ""
    desc = article.get("desc") or ""
    title_counts = Counter(_tokens(title))
    desc_counts = Counter(_tokens(desc[:800]))
    return {
        "article": article,
        "title_tokens": set(title_counts),
        "desc_tokens": set(desc_counts),
        "title_counts": title_counts,
        "desc_counts": desc_counts,
        "title_cves": _cves(title),
        "cves": _cves(title) | _cves(desc),
        "ts": article.get("published_ts") or 0,
        "title_weights": {},
        "title_norm": 1.0,
        "desc_weights": {},
        "desc_norm": 1.0,
    }


def _add_idf_weights(prepared: list[dict]) -> None:
    """Corpus-wide IDF, then TF-IDF vectors for headline and description."""
    df = Counter()
    for item in prepared:
        df.update(item["title_tokens"])
        df.update(item["desc_tokens"])
    n = len(prepared)
    idf = {token: math.log((n + 1) / (count + 1)) + 1.0 for token, count in df.items()}
    for item in prepared:
        for prefix in ("title", "desc"):
            weights = {token: (1.0 + math.log(count)) * idf.get(token, 1.0)
                       for token, count in item[prefix + "_counts"].items()}
            item[prefix + "_weights"] = weights
            item[prefix + "_norm"] = math.sqrt(sum(w * w for w in weights.values())) or 1.0


def _similarity(tokens_a: set, weights_a: dict, norm_a: float,
                tokens_b: set, weights_b: dict, norm_b: float) -> float:
    """0..1 blend of TF-IDF cosine and token-set Jaccard."""
    if not weights_a or not weights_b:
        return 0.0
    # iterate the smaller vector
    if len(weights_a) > len(weights_b):
        tokens_a, weights_a, norm_a, tokens_b, weights_b, norm_b = (
            tokens_b, weights_b, norm_b, tokens_a, weights_a, norm_a)
    dot = sum(w * weights_b.get(token, 0.0) for token, w in weights_a.items())
    cosine = dot / (norm_a * norm_b)
    union = tokens_a | tokens_b
    jaccard = (len(tokens_a & tokens_b) / len(union)) if union else 0.0
    return 0.75 * cosine + 0.25 * jaccard


def _title_similarity(a: dict, b: dict) -> float:
    return _similarity(a["title_tokens"], a["title_weights"], a["title_norm"],
                       b["title_tokens"], b["title_weights"], b["title_norm"])


def _desc_similarity(a: dict, b: dict) -> float:
    return _similarity(a["desc_tokens"], a["desc_weights"], a["desc_norm"],
                       b["desc_tokens"], b["desc_weights"], b["desc_norm"])


def _match_score(a: dict, b: dict, threshold: float, window: int) -> float | None:
    """How strongly two articles look like the same story; None means no merge."""
    if a["ts"] and b["ts"] and abs(a["ts"] - b["ts"]) > window:
        return None
    title_score = _title_similarity(a, b)
    desc_score = _desc_similarity(a, b)
    shared = a["title_tokens"] & b["title_tokens"]
    title_cves = a["title_cves"] & b["title_cves"]
    any_cves = a["cves"] & b["cves"]
    if title_cves:
        # A CVE in both headlines is the strongest signal. It still has to be
        # paired with some headline overlap, unless one headline is only the id.
        if not shared or title_score >= 0.30 or desc_score >= 0.45:
            return max(title_score, 0.50)
        return None
    if any_cves and (title_score >= 0.35 or (shared and desc_score >= 0.45)):
        return max(title_score, 0.90 * desc_score)
    # At least two real headline words must be shared. One shared word is
    # usually the brand of a newsletter or advisory template, not the event.
    if len(shared) >= 2:
        if title_score >= threshold or desc_score >= DESC_CONFIRM_THRESHOLD:
            return max(title_score, 0.90 * desc_score)
    return None


def cluster_articles(articles: list[dict], threshold: float = DEFAULT_TITLE_THRESHOLD,
                     window_seconds: int = DEFAULT_WINDOW_SECONDS) -> list[list[dict]]:
    """Group articles that describe the same story. Order inside each group is
    the input order; the groups themselves keep first-appearance order."""
    items = [a for a in articles if isinstance(a, dict)]
    if len(items) < 2:
        return [[a] for a in items]

    prepared = [_prepare(a) for a in items]
    _add_idf_weights(prepared)
    dsu = _DisjointSet(len(prepared))
    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            if _match_score(prepared[i], prepared[j], threshold, window_seconds) is not None:
                dsu.union(i, j)

    groups: dict[int, list[dict]] = {}
    for index, item in enumerate(prepared):
        groups.setdefault(dsu.find(index), []).append(item["article"])
    return list(groups.values())


def _cluster_id(group: list[dict]) -> str:
    seed = "|".join(sorted(
        str(a.get("id") or a.get("link") or a.get("title") or "") for a in group
    ))
    return "cl_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _representative_key(article: dict):
    """Earliest published wins; a missing time counts as late. Then the longest
    description, then source/id so the result is deterministic."""
    ts = article.get("published_ts") or 0
    return (ts if ts > 0 else float("inf"),
            -len(str(article.get("desc") or "")),
            str(article.get("source") or ""),
            str(article.get("id") or ""))


def _also_member_key(article: dict):
    ts = article.get("published_ts") or 0
    return (ts if ts > 0 else float("inf"),
            -len(str(article.get("desc") or "")),
            str(article.get("source") or ""))


def _annotate_group(group: list[dict], summary: dict) -> None:
    rep = min(group, key=_representative_key)
    cid = _cluster_id(group)
    by_source: dict[str, dict] = {}
    for article in group:
        source = str(article.get("source") or "").strip()
        if not source:
            continue
        current = by_source.get(source)
        if current is None or _also_member_key(article) < _also_member_key(current):
            by_source[source] = article

    source_count = len(by_source) or 1
    dupe_count = len(group) - 1
    rep_source = str(rep.get("source") or "")
    ordered_sources = sorted(by_source, key=lambda s: (_also_member_key(by_source[s]), s))

    for article in group:
        own_source = str(article.get("source") or "")
        also = []
        for source in ordered_sources:
            if source == own_source:
                continue
            other = by_source[source]
            also.append({
                "source": source,
                "title": other.get("title") or "",
                "link": other.get("link") or "",
                "pubDate": other.get("pubDate") or "",
                "published_ts": other.get("published_ts") or 0,
            })
        article["cluster_id"] = cid
        article["is_representative"] = article is rep
        article["dupe_count"] = dupe_count
        article["source_count"] = source_count
        article["also_reported_by"] = also

    if len(group) > 1:
        summary[cid] = {
            "size": len(group),
            "source_count": source_count,
            "representative_id": rep.get("id") or "",
            "sources": [s for s in ordered_sources if s != rep_source],
        }


def annotate(articles: list[dict], threshold: float = DEFAULT_TITLE_THRESHOLD,
             window_seconds: int = DEFAULT_WINDOW_SECONDS) -> tuple[dict, dict]:
    """Cluster a flat article list and add the cluster fields in place.

    Returns (clusters, stats). `clusters` contains multi-article clusters only;
    `stats` is a small summary suitable for the JSON payload."""
    groups = cluster_articles(articles, threshold, window_seconds)
    clusters: dict = {}
    for group in groups:
        _annotate_group(group, clusters)
    duplicates = sum(len(g) - 1 for g in groups)
    stats = {
        "articles": len(articles),
        "clusters": len(clusters),
        "grouped_articles": sum(c["size"] for c in clusters.values()),
        "duplicates": duplicates,
    }
    return clusters, stats


def apply_to_categories(buckets: dict[str, list[dict]],
                        threshold: float = DEFAULT_TITLE_THRESHOLD,
                        window_seconds: int = DEFAULT_WINDOW_SECONDS) -> tuple[dict, dict]:
    """Cluster every article in a categories dict, across category boundaries
    (the same story can be classified as STRIKE by one feed and RADAR by
    another). The categories themselves are not changed -- only the extra
    fields are added to each article."""
    all_articles: list[dict] = []
    seen: set[int] = set()
    for items in buckets.values():
        if not isinstance(items, list):
            continue
        for article in items:
            if isinstance(article, dict) and id(article) not in seen:
                seen.add(id(article))
                all_articles.append(article)
    return annotate(all_articles, threshold, window_seconds)
