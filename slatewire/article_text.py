"""
Fetches a longer, real excerpt directly from an article's own page, instead
of relying only on the (often one-sentence) RSS summary. Used both as a
richer non-AI fallback brief, and as better input text when an API key is
configured.

Same safety rule as fetcher.py: this only reads back text (paragraph
content), never executes or downloads anything else from the page.
"""

import re
from html import unescape

import safe_io

REQUEST_TIMEOUT = 10
MAX_PAGE_BYTES = 1_500_000   # stop reading a page after ~1.5 MB
TOTAL_SECONDS = 15           # and give up if the whole download takes longer than this
USER_AGENT = "Slatewire/1.0 (personal news reader; +https://github.com/)"

# Cookie-banner / nav-link paragraphs are almost always the first couple of
# <p> tags on a page. Skipping them outright (rather than just filtering by
# length) meaningfully reduces the odds of a brief opening with "We use
# cookies to improve your experience..." instead of the actual article.
SKIP_LEADING_PARAGRAPHS = 2

# Elements whose contents must never become a brief. A <script> can contain a
# literal "<p>" (template strings, document.write, JSON-LD payloads), and the
# paragraph matcher below has no idea it is inside code -- that is how raw
# JavaScript used to end up on screen as a "source excerpt".
_STRIP_BLOCKS = re.compile(
    r"<(script|style|noscript|template|svg|iframe|form|head)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# requests labels any text/* response without a charset as ISO-8859-1. Most
# pages are UTF-8 and say so in <meta charset>, so trusting that header alone
# turns Czech "Členové" into "ÄlženovÃ©". Read the page's own declaration.
_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""",
    re.IGNORECASE,
)
_AMBIGUOUS_LATIN1 = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", "latin_1"}


def _decode_page(body: bytes, declared: str | None) -> str:
    """Decode an HTML page using the best charset signal available.

    A specific HTTP charset wins. The generic ISO-8859-1 default and any
    Latin-1 <meta> declaration are tried only after UTF-8, so a UTF-8 page
    that merely lacks a header charset is not mangled.
    """
    match = _META_CHARSET.search(body[:4096])
    meta = (match.group(1).decode("ascii", "ignore").strip().strip("\"'").lower()
            if match else "")
    declared = (declared or "").strip().strip("\"'").lower()

    candidates = []
    if declared and declared not in _AMBIGUOUS_LATIN1:
        candidates.append(declared)
    if meta and meta not in _AMBIGUOUS_LATIN1:
        candidates.append(meta)
    candidates.append("utf-8")
    if declared and declared in _AMBIGUOUS_LATIN1:
        candidates.append(declared)
    if meta and meta in _AMBIGUOUS_LATIN1:
        candidates.append(meta)
    candidates.append("cp1252")

    seen = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            return body.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")

# Tell-tale signs of code that arrived as text rather than as a stripped block.
_CODE_SIGNS = re.compile(
    r"(=>|\bfunction\s*\(|\b(?:var|let|const)\s+[A-Za-z_$][\w$]*\s*="
    r"|\b(?:document|window|navigator)\.(?:[A-Za-z_$]|\()"
    r"|\\u[0-9a-f]{4}|</?[a-z][a-z0-9]*[\s/>])",
    re.IGNORECASE,
)


def looks_like_code(text: str) -> bool:
    """True for paragraphs that are script/style source or leftover markup."""
    if len(text) < 20:
        return False
    if _CODE_SIGNS.search(text):
        return True
    # Real prose uses few of these; minified or obfuscated code is full of them.
    # (URLs are excluded on purpose: briefs are allowed to contain links.)
    symbols = sum(1 for ch in text if ch in "{}[]();=<>\\|$#@~^")
    if symbols / len(text) > 0.08:
        return True
    # Shell transcripts and command lists ("xe vm-list ... | grep ... /var/log/...")
    # read as junk in a brief even though they are not JavaScript. Prose almost never
    # has four or more path-, flag- or assignment-shaped words in one paragraph, so
    # this leaves advisory text such as "Classification: Critical, Solution: Official
    # Fix, CVSSv3.1: 9.8" untouched.
    tokens = text.split()
    odd = sum(1 for w in tokens if "/" in w or "=" in w or (w.startswith("-") and len(w) > 1))
    if odd >= 4 and odd / max(1, len(tokens)) > 0.12:
        return True
    return False


def extract_excerpt(url: str, max_chars: int = 1500) -> str | None:
    if not url:
        return None
    try:
        body, enc = safe_io.download_limited(url, {"User-Agent": USER_AGENT}, MAX_PAGE_BYTES, TOTAL_SECONDS,
                                             read_timeout=REQUEST_TIMEOUT)
        html = _decode_page(body, enc)
    except Exception:
        return None

    html = _HTML_COMMENT.sub(" ", html)
    html = _STRIP_BLOCKS.sub(" ", html)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = []
    skipped = 0
    for p in paragraphs:
        text = re.sub(r"<[^>]+>", " ", p)
        # Page HTML often keeps entities like &nbsp; / &amp;. Without this they
        # reach the screen literally (the dashboard escapes '&' again).
        text = unescape(text)
        # Replacing inline tags with a space leaves " ," / " ." behind,
        # which looks broken once the excerpt is shown as a brief.
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        if looks_like_code(text):
            continue
        # skip short boilerplate lines (nav links, captions, "share this" etc.)
        if len(text) > 60:
            if skipped < SKIP_LEADING_PARAGRAPHS:
                skipped += 1
                continue
            cleaned.append(text)

    # Many sites put a "latest news" list in <p> tags ahead of the article, so
    # the first dozen paragraphs can be unrelated headlines. Headlines are
    # short and usually do not end with sentence punctuation; real body
    # paragraphs are longer or do. Keep the longest contiguous run of those.
    # (Skipped when nothing qualifies, so unusual pages keep the old behaviour.)
    def _is_body(para: str) -> bool:
        return len(para) > 120 or para.endswith((".", "!", "?", '"', "”", ")", "’"))

    best_start = best_len = 0
    i = 0
    while i < len(cleaned):
        if _is_body(cleaned[i]):
            j = i
            while j < len(cleaned) and _is_body(cleaned[j]):
                j += 1
            if j - i > best_len:
                best_start, best_len = i, j - i
            i = j
        else:
            i += 1
    if best_len:
        cleaned = cleaned[best_start:best_start + best_len]

    if not cleaned:
        return None

    excerpt = " ".join(cleaned)
    return excerpt[:max_chars].rsplit(" ", 1)[0] + "…"


# Dots in common abbreviations are not the end of a sentence. A placeholder
# holds the space so the splitter below cannot break after "U.S." or "e.g.".
_ABBREV = re.compile(r"\b(U\.S\.|U\.K\.|E\.U\.|e\.g\.|i\.e\.|etc\.|vs\.|No\.)\s+")
_ABBREV_KEEP = "\u0001"


def wrap_as_lines(text: str, target_lines: int = 8) -> str:
    """Break a block of text into short, readable lines (roughly one
    sentence per line) for display without needing AI summarization."""
    text = _ABBREV.sub(lambda m: m.group(1) + _ABBREV_KEEP, text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.replace(_ABBREV_KEEP, " ").strip() for s in sentences if s.strip()]
    return "\n".join(sentences[:target_lines])
