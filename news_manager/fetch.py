"""HTTP fetch, URL normalization, link discovery, article extraction."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
import trafilatura
from bs4 import BeautifulSoup
from trafilatura import extract_metadata

from news_manager.models import RawArticle

logger = logging.getLogger(__name__)

USER_AGENT = "news-manager/0.1 (+https://github.com/)"

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


def fetch_html(client: httpx.Client, url: str) -> str | None:
    try:
        r = client.get(url, follow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype.lower() and "text" not in ctype.lower():
            logger.warning("Skipping non-HTML response for %s: %s", url, ctype)
            return None
        return r.text
    except httpx.HTTPError as e:
        logger.warning("HTTP error fetching %s: %s", url, e)
        return None


def fetch_articles_for_source(
    home_raw: str,
    *,
    max_articles: int,
    timeout: float,
) -> list[RawArticle]:
    """
    Fetch home page, collect article links, fetch each article up to max_articles.
    Failures are logged; returns whatever could be fetched.
    """
    home_url = normalize_url(home_raw)
    results: list[RawArticle] = []
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        limits=limits,
    ) as client:
        home_html = fetch_html(client, home_url)
        if not home_html:
            return results
        links = extract_article_urls(home_html, home_url)
        # Prefer longer paths (often deeper articles); stable order
        links.sort(key=lambda u: len(urlparse(u).path), reverse=True)
        for article_url in links:
            if len(results) >= max_articles:
                break
            html = fetch_html(client, article_url)
            if not html:
                continue
            title, date, content = _extract_body_title_date(html, article_url)
            if not content:
                logger.warning("No extractable text for %s", article_url)
                continue
            if not title:
                # Fallback: page <title> via regex or BeautifulSoup
                m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.S)
                title = m.group(1).strip() if m else article_url
            results.append(
                RawArticle(
                    title=title,
                    date=date,
                    content=content,
                    url=article_url,
                )
            )
    return results
