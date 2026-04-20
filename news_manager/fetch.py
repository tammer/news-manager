"""HTTP fetch, URL normalization, link discovery, article extraction."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urljoin, urlparse, urlunparse

import feedparser
import httpx
import trafilatura
from bs4 import BeautifulSoup
from trafilatura import extract_metadata

from news_manager.config import groq_model_html_discovery
from news_manager.models import RawArticle

logger = logging.getLogger(__name__)

# Many CDNs treat non-browser UAs harshly; curl’s default identity is widely accepted.
USER_AGENT = "curl/8.7.1"

# 429 Too Many Requests: retry with Retry-After when present (RFC 9110).
_HTTP_429_MAX_ATTEMPTS = 4
_HTTP_429_RETRY_AFTER_CAP_SEC = 120.0
_HTTP_429_FALLBACK_BASE_SEC = 20.0


def _retry_delay_after_429(response: httpx.Response, attempt_index: int) -> float:
    """Seconds to sleep before the next attempt (capped)."""
    cap = _HTTP_429_RETRY_AFTER_CAP_SEC
    raw = response.headers.get("Retry-After")
    if raw:
        s = raw.strip()
        try:
            sec = int(s)
            return min(max(0.0, float(sec)), cap)
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(s)
            if when is not None:
                now = datetime.now(when.tzinfo or timezone.utc)
                return min(max(0.0, (when - now).total_seconds()), cap)
        except (TypeError, ValueError, OSError):
            pass
    return min(_HTTP_429_FALLBACK_BASE_SEC * (2**attempt_index), cap)


def _get_with_429_retry(client: httpx.Client, url: str) -> httpx.Response:
    """GET with retries on 429 only; other status codes returned as-is."""
    for attempt in range(_HTTP_429_MAX_ATTEMPTS):
        r = client.get(url, follow_redirects=True)
        if r.status_code != 429:
            return r
        if attempt >= _HTTP_429_MAX_ATTEMPTS - 1:
            return r
        delay = _retry_delay_after_429(r, attempt)
        logger.warning(
            "HTTP 429 for %s, sleeping %.1fs then retry (%s/%s)",
            url,
            delay,
            attempt + 1,
            _HTTP_429_MAX_ATTEMPTS,
        )
        time.sleep(delay)

# Paths that are unlikely to be article pages (heuristic; document in code).
PATH_DENY_SUBSTRINGS = (
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/author/",
    "/authors/",
    "/search",
    "/video/",
    "/videos/",
    "/live/",
    "/login",
    "/newsletter",
    "/subscribe",
    "/account",
    "/profile",
    "/static/",
    "/assets/",
    "/wp-content/",
    "/cookie",
    "/privacy",
    "/terms",
)

BAD_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
    ".pdf",
    ".zip",
    ".mp4",
    ".mp3",
)


def normalize_url(raw: str) -> str:
    """
    Normalize a source entry to a fetchable URL: default https, strip fragment.
    Host/path are preserved as parsed (including www) for consistent behavior.
    """
    s = raw.strip()
    if not s:
        raise ValueError("empty URL")
    parsed = urlparse(s)
    if not parsed.scheme:
        s = "https://" + s
        parsed = urlparse(s)
    if not parsed.netloc:
        raise ValueError(f"invalid URL: {raw!r}")
    parsed = parsed._replace(fragment="")
    return urlunparse(parsed)


def source_base_label(feed_url: str) -> str:
    """
    Hostname for JSON/HTML display (e.g. nextbigthing.substack.com).
    Uses the configured source URL; strips leading www.
    """
    s = feed_url.strip()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).hostname
    if not host:
        return ""
    return _strip_www(host)


def _strip_www(netloc: str) -> str:
    n = netloc.lower()
    return n[4:] if n.startswith("www.") else n


def same_site(home_url: str, link_url: str) -> bool:
    """Same registrable host: compare netloc with www stripped."""
    h = urlparse(home_url).netloc
    l = urlparse(link_url).netloc
    return _strip_www(h) == _strip_www(l)


def _path_looks_like_article(path: str) -> bool:
    p = path.lower().rstrip("/")
    if not p or p == "/":
        return False
    for sub in PATH_DENY_SUBSTRINGS:
        if sub in p:
            return False
    for ext in BAD_EXTENSIONS:
        if p.endswith(ext):
            return False
    return True


_ANCHOR_TEXT_MAX_CHARS = 120


def _compact_anchor_text(tag) -> str:
    """Visible anchor text for LLM context (single line, bounded length)."""
    try:
        raw = tag.get_text(separator=" ", strip=True)
    except (AttributeError, TypeError):
        return ""
    one = " ".join(str(raw).split()).strip()
    if len(one) <= _ANCHOR_TEXT_MAX_CHARS:
        return one
    return one[: _ANCHOR_TEXT_MAX_CHARS - 3] + "..."


def extract_article_link_candidates(html: str, home_url: str) -> list[tuple[str, str]]:
    """
    Same-site article-like links in document order: ``(absolute_url, anchor_text)``.
    First occurrence wins per URL (anchor text from the first ``<a>``).
    """
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not href or not isinstance(href, str):
            continue
        href = href.strip()
        if not href or href.startswith("#") or href.lower().startswith(
            ("javascript:", "mailto:", "tel:")
        ):
            continue
        abs_url = urljoin(home_url, href)
        p = urlparse(abs_url)
        if p.scheme not in ("http", "https"):
            continue
        abs_url = urlunparse(p._replace(fragment=""))
        if abs_url in seen:
            continue
        if not same_site(home_url, abs_url):
            continue
        if not _path_looks_like_article(p.path):
            continue
        seen.add(abs_url)
        out.append((abs_url, _compact_anchor_text(a)))
    return out


def extract_article_urls(html: str, home_url: str) -> list[str]:
    """Parse HTML for same-site links that may be articles."""
    return [u for u, _ in extract_article_link_candidates(html, home_url)]


def _extract_body_title_date(html: str, url: str) -> tuple[str, str | None, str]:
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
    )
    meta = extract_metadata(html)
    title = ""
    date: str | None = None
    if meta is not None:
        if meta.title:
            title = meta.title.strip()
        if meta.date:
            date = meta.date
    if not text or not str(text).strip():
        return "", date, ""
    return title, date, str(text).strip()


def _response_ok_for_article_html(ctype: str, body_prefix: str) -> bool:
    c = ctype.lower()
    if "html" in c or "text" in c:
        return True
    # Some sites mislabel; sniff
    s = body_prefix.lstrip()[:800].lower()
    return "<html" in s or "<!doctype html" in s


def fetch_html(client: httpx.Client, url: str) -> str | None:
    try:
        r = _get_with_429_retry(client, url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        prefix = r.text[:2000] if r.text else ""
        if not _response_ok_for_article_html(ctype, prefix):
            logger.warning("Skipping non-HTML response for %s: %s", url, ctype)
            return None
        return r.text
    except httpx.HTTPError as e:
        logger.warning("HTTP error fetching %s: %s", url, e)
        return None


def _looks_like_feed_xml(text: str) -> bool:
    s = text.lstrip()[:800]
    return s.startswith("<?xml") or s.startswith("<rss") or s.startswith("<feed")


def fetch_feed_xml(client: httpx.Client, url: str) -> str | None:
    """GET feed URL; accept RSS/Atom XML (Substack, blogs, etc.)."""
    try:
        r = _get_with_429_retry(client, url)
        r.raise_for_status()
        text = r.text
        if not text or not text.strip():
            return None
        ctype = r.headers.get("content-type", "").lower()
        if any(x in ctype for x in ("xml", "rss", "atom", "rdf")):
            return text
        if _looks_like_feed_xml(text):
            return text
        logger.warning(
            "Feed URL %s did not look like RSS/Atom (Content-Type: %s)",
            url,
            ctype or "(none)",
        )
        return None
    except httpx.HTTPError as e:
        logger.warning("HTTP error fetching feed %s: %s", url, e)
        return None


def parse_feed_entries(body: str) -> list[tuple[str, str | None, str | None]]:
    """
    Return (article_url, published_str_or_none, feed_title_or_none) per entry, feed order.
    """
    parsed = feedparser.parse(body)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        logger.warning(
            "Feed parse issue: %s",
            getattr(parsed, "bozo_exception", "unknown"),
        )
    out: list[tuple[str, str | None, str | None]] = []
    for entry in parsed.entries:
        link: str | None = entry.get("link")
        if not link:
            for L in entry.get("links", []) or []:
                if isinstance(L, dict) and L.get("href"):
                    rel = (L.get("rel") or "").lower()
                    if rel in ("", "alternate", "self"):
                        link = L.get("href")
                        break
        if not link:
            lid = entry.get("id")
            if isinstance(lid, str) and lid.startswith("http"):
                link = lid
        if not link or not str(link).strip().startswith(("http://", "https://")):
            continue
        pub = entry.get("published") or entry.get("updated")
        pub_s = pub if isinstance(pub, str) else None
        tit = entry.get("title")
        title_s = tit.strip() if isinstance(tit, str) else None
        out.append((str(link).strip(), pub_s, title_s))
    return out


def _xml_local_name(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _looks_like_sitemap_index(text: str) -> bool:
    s = text.lstrip()[:24_000]
    return bool(re.search(r"<\s*sitemapindex\b", s, re.I))


def _looks_like_sitemap_urlset(text: str) -> bool:
    """True for a URL sitemap (not a sitemap index document)."""
    s = text.lstrip()[:24_000]
    if _looks_like_sitemap_index(text):
        return False
    return bool(re.search(r"<\s*urlset\b", s, re.I))


def extract_sitemap_http_urls(body: str, home_url: str) -> list[str]:
    """
    Collect ``http(s)`` URLs from ``<loc>`` under ``<urlset>`` (Sitemap 0.9).
    Same-site and path heuristics as HTML link discovery. Document order, deduped.
    """
    if not body.strip():
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        logger.warning("discovery: sitemap XML parse error for %s: %s", home_url, e)
        return []
    out: list[str] = []
    seen: set[str] = set()
    for el in root.iter():
        if _xml_local_name(el.tag) != "loc":
            continue
        raw = (el.text or "").strip()
        if not raw.startswith(("http://", "https://")):
            continue
        p = urlparse(raw)
        if p.scheme not in ("http", "https"):
            continue
        nu = normalize_url(urlunparse(p._replace(fragment="")))
        if nu in seen:
            continue
        if not same_site(home_url, nu):
            continue
        if not _path_looks_like_article(p.path):
            continue
        seen.add(nu)
        out.append(nu)
    return out


def fetch_listing_body(client: httpx.Client, url: str) -> str | None:
    """
    GET ``url`` once for discovery sniffing (RSS/Atom, URL sitemap, or HTML).

    Accepts typical feed XML, sitemap XML, and HTML listing pages; rejects ambiguous
    non-listing bodies with a warning.
    """
    try:
        r = _get_with_429_retry(client, url)
        r.raise_for_status()
        text = r.text
        if not text or not text.strip():
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        if any(x in ctype for x in ("xml", "rss", "atom", "rdf")):
            return text
        if _looks_like_feed_xml(text):
            return text
        if _looks_like_sitemap_index(text) or _looks_like_sitemap_urlset(text):
            return text
        if _response_ok_for_article_html(ctype, text[:2000]):
            return text
        logger.warning(
            "discovery: could not classify listing response url=%s content-type=%s",
            url,
            ctype or "(none)",
        )
        return None
    except httpx.HTTPError as e:
        logger.warning("discovery: HTTP error fetching listing %s: %s", url, e)
        return None


def discover_article_targets(
    client: httpx.Client,
    home_raw: str,
    *,
    force_feed_xml: bool = False,
    use_llm_for_html: bool = False,
) -> list[tuple[str, str | None, str | None]]:
    """
    List candidate article URLs in crawl order.

    **Auto** (``force_feed_xml=False``): one GET, then RSS/Atom entries if present,
    else URL sitemap ``<loc>`` URLs, else same-site HTML links (optional LLM ordering).

    **Force feed/XML** (``force_feed_xml=True``): same GET, then RSS/Atom or sitemap
    only (no HTML link crawl). Matches legacy ``use_rss=true`` sources.
    """
    disc_log = logging.getLogger("news_manager.html_discovery")
    home_url = normalize_url(home_raw)
    host = urlparse(home_url).hostname or ""

    body = fetch_listing_body(client, home_url)
    if not body:
        logger.info("discovery: no listing body host=%s force_feed_xml=%s", host, force_feed_xml)
        return []

    if force_feed_xml:
        feed_rows = parse_feed_entries(body)
        if feed_rows:
            logger.info(
                "discovery: strategy=rss_atom host=%s force_feed_xml=True count=%s",
                host,
                len(feed_rows),
            )
            return feed_rows
        if _looks_like_sitemap_index(body):
            logger.info(
                "discovery: strategy=sitemap_index_unsupported host=%s force_feed_xml=True",
                host,
            )
            return []
        if _looks_like_sitemap_urlset(body):
            locs = extract_sitemap_http_urls(body, home_url)
            if locs:
                logger.info(
                    "discovery: strategy=sitemap host=%s force_feed_xml=True count=%s",
                    host,
                    len(locs),
                )
                return [(u, None, None) for u in locs]
        logger.warning(
            "discovery: force_feed_xml but no RSS entries or urlset locs host=%s",
            host,
        )
        return []

    if _looks_like_sitemap_index(body):
        logger.info(
            "discovery: strategy=sitemap_index_unsupported host=%s force_feed_xml=False",
            host,
        )
        return []
    if _looks_like_sitemap_urlset(body):
        locs = extract_sitemap_http_urls(body, home_url)
        if locs:
            logger.info(
                "discovery: strategy=sitemap host=%s force_feed_xml=False count=%s",
                host,
                len(locs),
            )
            return [(u, None, None) for u in locs]
    feed_rows = parse_feed_entries(body)
    if feed_rows:
        logger.info(
            "discovery: strategy=rss_atom host=%s force_feed_xml=False count=%s",
            host,
            len(feed_rows),
        )
        return feed_rows

    home_html = body

    if use_llm_for_html:
        candidates = extract_article_link_candidates(home_html, home_url)
        disc_log.info(
            "discover_article_targets: html_llm host=%s candidate_count=%s model=%s",
            host,
            len(candidates),
            groq_model_html_discovery(),
        )
        sample = [u for u, _ in candidates[:5]]
        disc_log.info(
            "discover_article_targets: sample_candidate_urls host=%s first_up_to_5=%s",
            host,
            sample,
        )
        if disc_log.isEnabledFor(logging.DEBUG):
            cap_dbg = min(len(candidates), 500)
            lines = [f"{u}\t{t}" for u, t in candidates[:cap_dbg]]
            disc_log.debug(
                "discover_article_targets: candidate_tsv host=%s lines=%s\n%s",
                host,
                cap_dbg,
                "\n".join(lines),
            )

        if not candidates:
            disc_log.info(
                "discover_article_targets: no candidates after parse host=%s",
                host,
            )
            return []

        from news_manager.html_discovery_llm import select_article_urls_with_llm

        picked = select_article_urls_with_llm(
            home_url, candidates, home_host=host or None
        )
        if picked is not None and len(picked) > 0:
            disc_log.info(
                "discover_article_targets: using_llm_order host=%s target_count=%s",
                host,
                len(picked),
            )
            return [(u, None, None) for u in picked]

        disc_log.warning(
            "discover_article_targets: llm_empty_or_failed heuristic_fallback host=%s "
            "candidate_count=%s",
            host,
            len(candidates),
        )
        urls = [u for u, _ in candidates]
        urls.sort(key=lambda u: len(urlparse(u).path), reverse=True)
        return [(u, None, None) for u in urls]

    links = extract_article_urls(home_html, home_url)
    links.sort(key=lambda u: len(urlparse(u).path), reverse=True)
    return [(u, None, None) for u in links]


def fetch_single_raw_article(
    client: httpx.Client,
    article_url: str,
    feed_date: str | None,
    feed_title: str | None,
) -> RawArticle | None:
    """Fetch one article page and extract body (same logic as batch fetch)."""
    nu = normalize_url(article_url)
    html = fetch_html(client, nu)
    if not html:
        return None
    title, date, content = _extract_body_title_date(html, nu)
    if not content:
        logger.warning("No extractable text for %s", nu)
        return None
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.S)
        title = (m.group(1).strip() if m else None) or feed_title or nu
    elif feed_title and len(title) < 3:
        title = feed_title
    if not date and feed_date:
        date = feed_date
    return RawArticle(
        title=title,
        date=date,
        content=content,
        url=nu,
    )


def _fetch_raw_articles_from_urls(
    client: httpx.Client,
    urls_with_meta: list[tuple[str, str | None, str | None]],
    *,
    max_articles: int,
) -> list[RawArticle]:
    """Fetch HTML for each URL and extract article text; use feed metadata as fallback."""
    results: list[RawArticle] = []
    for article_url, feed_date, feed_title in urls_with_meta:
        if len(results) >= max_articles:
            break
        raw = fetch_single_raw_article(client, article_url, feed_date, feed_title)
        if raw is not None:
            results.append(raw)
    return results


def fetch_articles_for_source(
    home_raw: str,
    *,
    kind: Literal["html", "rss"] = "html",
    max_articles: int,
    timeout: float,
) -> list[RawArticle]:
    """
    Discover article URLs then fetch each page.

    ``kind="rss"`` forces feed/XML discovery (RSS/Atom or URL sitemap). ``kind="html"``
    uses auto-detect (RSS, sitemap, or HTML link crawl). See ``discover_article_targets``.

    Failures are logged; returns whatever could be fetched.
    Uses ``cookies/<host>.json`` when present (see thestar_plan.md).
    """
    from news_manager.cookies_loader import cookie_jar_for_home_url

    home_url = normalize_url(home_raw)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    jar = cookie_jar_for_home_url(home_raw)
    client_kw: dict = {
        "headers": {"User-Agent": USER_AGENT},
        "timeout": timeout,
        "limits": limits,
    }
    if jar is not None:
        client_kw["cookies"] = jar
    with httpx.Client(**client_kw) as client:
        targets = discover_article_targets(
            client,
            home_url,
            force_feed_xml=(kind == "rss"),
            use_llm_for_html=False,
        )
        return _fetch_raw_articles_from_urls(client, targets, max_articles=max_articles)
