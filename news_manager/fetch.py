"""HTTP fetch, URL normalization, link discovery, article extraction."""

from __future__ import annotations

import logging
import re
from typing import Literal
from urllib.parse import urljoin, urlparse, urlunparse

import feedparser
import httpx
import trafilatura
from bs4 import BeautifulSoup
from trafilatura import extract_metadata

from news_manager.models import RawArticle

logger = logging.getLogger(__name__)

# Many CDNs treat non-browser UAs harshly; curl’s default identity is widely accepted.
USER_AGENT = "curl/8.7.1"

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


def extract_article_urls(html: str, home_url: str) -> list[str]:
    """Parse HTML for same-site links that may be articles."""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[str] = []
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
        out.append(abs_url)
    return out


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
        r = client.get(url, follow_redirects=True)
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
        r = client.get(url, follow_redirects=True)
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


def discover_article_targets(
    client: httpx.Client,
    home_raw: str,
    *,
    kind: Literal["html", "rss"] = "html",
) -> list[tuple[str, str | None, str | None]]:
    """
    List candidate article URLs in crawl order (RSS feed order or HTML link order).
    Does not fetch individual article bodies.
    """
    home_url = normalize_url(home_raw)
    if kind == "rss":
        feed_body = fetch_feed_xml(client, home_url)
        if not feed_body:
            return []
        return parse_feed_entries(feed_body)
    home_html = fetch_html(client, home_url)
    if not home_html:
        return []
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
    HTML: fetch home page, discover same-site links, fetch each article.
    RSS: fetch Atom/RSS feed (e.g. Substack `/feed`), then fetch each entry URL.
    Failures are logged; returns whatever could be fetched.
    """
    home_url = normalize_url(home_raw)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        limits=limits,
    ) as client:
        if kind == "rss":
            feed_body = fetch_feed_xml(client, home_url)
            if not feed_body:
                return []
            entries = parse_feed_entries(feed_body)
            return _fetch_raw_articles_from_urls(client, entries, max_articles=max_articles)

        home_html = fetch_html(client, home_url)
        if not home_html:
            return []
        links = extract_article_urls(home_html, home_url)
        links.sort(key=lambda u: len(urlparse(u).path), reverse=True)
        tuples = [(u, None, None) for u in links]
        return _fetch_raw_articles_from_urls(client, tuples, max_articles=max_articles)
