"""Resolve a loose query into a homepage or RSS URL (lookup only; no DB writes)."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from news_manager.config import DEFAULT_HTTP_TIMEOUT, groq_model
from news_manager.fetch import _looks_like_feed_xml
from news_manager.llm import get_client
from news_manager.summarize import _parse_json_response

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = DEFAULT_HTTP_TIMEOUT
_MAX_HTML_BYTES = 512_000
_MAX_HTML_FOR_LLM = 24_000
_USER_AGENT = "news-manager-source-resolve/1.0"

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _strip_json_fence(content: str) -> str:
    content = content.strip()
    m = _JSON_FENCE.search(content)
    if m:
        return m.group(1).strip()
    return content


def _chat_json(system: str, user: str) -> dict[str, Any] | None:
    client = get_client()
    resp = client.chat.completions.create(
        model=groq_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    raw = resp.choices[0].message.content
    if not raw:
        return None
    return _parse_json_response(raw) or _parse_json_response(_strip_json_fence(raw))


def _host_is_forbidden(hostname: str) -> bool:
    h = hostname.lower().rstrip(".")
    if h in ("localhost", "0.0.0.0", "127.0.0.1", "::1"):
        return True
    if h.endswith(".local") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        )
    except ValueError:
        return False


def _dns_resolves_to_forbidden_ip(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for item in infos:
        addr = item[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            return True
    return False


def url_fetch_allowed(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    host = p.hostname
    if not host:
        return False
    if _host_is_forbidden(host):
        return False
    if _dns_resolves_to_forbidden_ip(host):
        return False
    return True


def _scrub_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https"):
        return url.strip()
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    new_query = urlencode(q, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, ""))


def ddg_text_search(query: str, *, max_results: int, region: str | None) -> list[dict[str, str]]:
    from duckduckgo_search import DDGS

    kwargs: dict[str, Any] = {"max_results": max_results}
    if region:
        kwargs["region"] = region
    with DDGS() as ddgs:
        rows = list(ddgs.text(query, **kwargs))
    out: list[dict[str, str]] = []
    for r in rows:
        href = (r.get("href") or r.get("url") or "").strip()
        if not href:
            continue
        out.append(
            {
                "title": (r.get("title") or "").strip(),
                "href": href,
                "body": (r.get("body") or "").strip(),
            }
        )
    return out


def _collect_candidates_from_query(user_query: str, *, max_results: int, region: str | None) -> list[dict[str, str]]:
    q = user_query.strip()
    if not q:
        return []
    parsed = urlparse(q if "://" in q else f"https://{q}")
    if parsed.scheme in ("http", "https") and parsed.netloc:
        u = _scrub_url(q if "://" in q else f"https://{q}")
        if url_fetch_allowed(u):
            return [{"title": "", "href": u, "body": "direct"}]
    return ddg_text_search(q, max_results=max_results, region=region)


def _filter_search_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        href = r.get("href", "")
        if not href or not url_fetch_allowed(href):
            continue
        out.append(r)
    return out


def _llm_pick_homepage(user_query: str, rows: list[dict[str, str]]) -> dict[str, Any] | None:
    if not rows:
        return None
    lines = []
    for i, r in enumerate(rows[:15]):
        lines.append(f"{i + 1}. url={r['href']!r} title={r['title']!r} snippet={r['body']!r}")
    system = (
        "You pick the single best canonical news/site homepage for the user's intent. "
        "Always choose exactly one URL from the list (or its obvious root homepage on the same registrable domain). "
        "Respond with JSON only: "
        '{"homepage_url":"https://...","website_title":"short name","confidence":"high|medium|low","notes":""}'
    )
    user = f"User query: {user_query!r}\n\nCandidates:\n" + "\n".join(lines)
    return _chat_json(system, user)


def _resolve_redirects_once(url: str) -> str:
    if not url_fetch_allowed(url):
        return url
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            r = client.get(url)
            return _scrub_url(str(r.url))
    except Exception as e:
        logger.debug("redirect resolve failed for %s: %s", url, e)
        return _scrub_url(url)


def fetch_html_limited(url: str) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Return (html, final_url, error_details)."""
    if not url_fetch_allowed(url):
        return None, None, {"stage": "url_validation", "reason": "url_not_allowed"}
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            with client.stream("GET", url) as resp:
                final_url = str(resp.url)
                status_code = resp.status_code
                content_type = (resp.headers.get("content-type") or "").strip()
                enc = resp.encoding or "utf-8"
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _MAX_HTML_BYTES:
                        break
                raw = b"".join(chunks)
                response_headers = {
                    "content_type": content_type,
                    "content_length": (resp.headers.get("content-length") or "").strip(),
                    "server": (resp.headers.get("server") or "").strip(),
                }
        text = raw.decode(enc, errors="replace")
        if status_code >= 400:
            detail: dict[str, Any] = {
                "stage": "homepage_fetch",
                "reason": f"http_{status_code}",
                "url": _scrub_url(url),
                "final_url": _scrub_url(final_url),
                "status_code": status_code,
                "bytes_read": len(raw),
                "response_headers": response_headers,
            }
            preview = text[:600].strip()
            if preview:
                detail["body_preview"] = preview
            return None, None, detail
        if not text.strip():
            return (
                None,
                None,
                {
                    "stage": "homepage_fetch",
                    "reason": "empty_body",
                    "url": _scrub_url(url),
                    "final_url": _scrub_url(final_url),
                    "status_code": status_code,
                    "content_type": content_type,
                    "bytes_read": len(raw),
                },
            )
        return text, final_url, None
    except Exception as e:
        logger.info("fetch_html_limited failed for %s: %s", url, e)
        detail: dict[str, Any] = {
            "stage": "homepage_fetch",
            "reason": e.__class__.__name__,
            "url": _scrub_url(url),
        }
        if isinstance(e, httpx.RequestError) and e.request is not None:
            detail["final_url"] = _scrub_url(str(e.request.url))
        return None, None, detail


def _page_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    t = soup.find("title")
    if t and t.string:
        return " ".join(t.string.split()).strip()
    return ""


def _extract_feed_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    feeds: list[str] = []
    for link in soup.find_all("link"):
        rel = (link.get("rel") or [])
        if isinstance(rel, str):
            rel = [rel]
        rel_l = [x.lower() for x in rel if isinstance(x, str)]
        if not any("alternate" in x for x in rel_l):
            continue
        t = (link.get("type") or "").lower()
        if not any(x in t for x in ("rss", "atom", "xml")):
            continue
        href = link.get("href")
        if not href or not isinstance(href, str):
            continue
        abs_u = urljoin(base_url, href.strip())
        if url_fetch_allowed(abs_u):
            feeds.append(_scrub_url(abs_u))
    seen: set[str] = set()
    uniq: list[str] = []
    for u in feeds:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def _probe_feed_paths(homepage_url: str) -> list[str]:
    p = urlparse(homepage_url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return []
    base = f"{p.scheme}://{p.netloc}"
    paths = [
        "/feed",
        "/feed/",
        "/rss",
        "/rss.xml",
        "/atom.xml",
        "/feeds/posts/default",
    ]
    found: list[str] = []
    for path in paths:
        u = _scrub_url(urljoin(base, path))
        if not url_fetch_allowed(u):
            continue
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
                r = client.head(u, follow_redirects=True)
                if r.status_code < 400 and "xml" in (r.headers.get("content-type") or "").lower():
                    found.append(str(r.url))
                    continue
                g = client.get(u, follow_redirects=True)
                if g.status_code < 400 and (
                    "xml" in (g.headers.get("content-type") or "").lower()
                    or g.text.lstrip().startswith("<?xml")
                    or "<rss" in g.text[:2000].lower()
                    or "<feed" in g.text[:2000].lower()
                ):
                    found.append(str(g.url))
        except Exception:
            continue
    return found


def _llm_is_article_listing(homepage_url: str, html_excerpt: str) -> dict[str, Any] | None:
    system = (
        "Decide if the page is primarily a news/story listing index (many links to distinct articles/stories), "
        "not a single article, login wall only, or generic corporate landing with no article list. "
        "Respond with JSON only: "
        '{"is_article_listing":true|false,"reason":"short"}'
    )
    user = f"URL: {homepage_url}\n\nHTML excerpt (truncated):\n{html_excerpt}"
    return _chat_json(system, user)


def _looks_like_feed_url(url: str) -> bool:
    u = url.lower()
    return any(x in u for x in ("/feed", "rss", "atom", ".xml"))


def _site_host_key(hostname: str | None) -> str:
    if not hostname:
        return ""
    h = hostname.lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def _listing_path_prefix(path: str) -> str:
    """Directory-style prefix for scope checks (no trailing slash, except root)."""
    p = path or ""
    if p in ("", "/"):
        return ""
    return p.rstrip("/")


def _feed_matches_listing_scope(listing_url: str, feed_url: str) -> bool:
    """
    If the listing is not the site root, only accept feeds whose path is the same
    section or below it. Avoids using a site-wide /index.rss when the user picked
    a topic hub like /hub/book-reviews.
    """
    a, b = urlparse(listing_url), urlparse(feed_url)
    if a.scheme not in ("http", "https") or b.scheme not in ("http", "https"):
        return False
    if _site_host_key(a.hostname) != _site_host_key(b.hostname):
        return False
    prefix = _listing_path_prefix(a.path)
    if not prefix:
        return True
    fp = _listing_path_prefix(b.path)
    if fp == prefix:
        return True
    return fp.startswith(prefix + "/")


def resolve_source(
    user_query: str,
    *,
    locale: str | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """
    Run the full resolve pipeline. Returns a dict suitable for JSON (ok true/false).
    """
    q = user_query.strip()
    if not q:
        return {"ok": False, "error": "no_results", "message": "Empty query."}

    try:
        raw_rows = _collect_candidates_from_query(q, max_results=max(5, min(max_results, 25)), region=locale)
    except Exception as e:
        logger.exception("search failed: %s", e)
        return {"ok": False, "error": "upstream_timeout", "message": "Search failed."}

    rows = _filter_search_rows(raw_rows)
    if not rows:
        return {"ok": False, "error": "no_results", "message": "No usable search results for that query."}

    # Single pasted URL: do not ask the LLM for a "canonical homepage" — it often
    # returns the domain root, which breaks section hubs and feed path scoping.
    if len(rows) == 1 and (rows[0].get("body") or "").strip() == "direct":
        href0 = (rows[0].get("href") or "").strip()
        picked: dict[str, Any] | None = (
            {
                "homepage_url": href0,
                "website_title": "",
                "confidence": "high",
                "notes": "",
            }
            if href0
            else None
        )
    else:
        picked = _llm_pick_homepage(q, rows)
    homepage = None
    website_title = ""
    confidence = "medium"
    notes_parts: list[str] = []

    if isinstance(picked, dict) and isinstance(picked.get("homepage_url"), str):
        homepage = _scrub_url(picked["homepage_url"])
        website_title = str(picked.get("website_title") or "").strip()
        conf = str(picked.get("confidence") or "medium").lower()
        if conf in ("high", "medium", "low"):
            confidence = conf
        n = str(picked.get("notes") or "").strip()
        if n:
            notes_parts.append(n)

    if not homepage or not url_fetch_allowed(homepage):
        # Fallback: first candidate
        homepage = _scrub_url(rows[0]["href"])
        confidence = "low"
        notes_parts.append("Fell back to top search result.")

    homepage = _resolve_redirects_once(homepage)
    if not url_fetch_allowed(homepage):
        return {"ok": False, "error": "no_results", "message": "Resolved URL is not allowed to fetch."}

    fetch_result = fetch_html_limited(homepage)
    if len(fetch_result) == 3:
        html, final_url, fetch_err = fetch_result
    else:
        html, final_url = fetch_result
        fetch_err = None
    if not html or not final_url:
        out: dict[str, Any] = {
            "ok": False,
            "error": "upstream_timeout",
            "message": "Could not fetch the homepage.",
        }
        if fetch_err:
            out["details"] = fetch_err
        return out

    homepage_final = _scrub_url(final_url)

    if _looks_like_feed_xml(html):
        ft = (feedparser.parse(html).feed.get("title") or "").strip()
        wt = (website_title or "").strip() or ft or urlparse(homepage_final).netloc
        notes_parts.append("Input URL is an RSS/Atom feed; using it for ingest.")
        notes = " ".join(notes_parts).strip()
        return {
            "ok": True,
            "website_title": wt,
            "homepage_url": homepage_final,
            "resolved_url": homepage_final,
            "use_rss": True,
            "rss_found": True,
            "confidence": confidence,
            "notes": notes,
        }

    if not website_title:
        website_title = _page_title(html) or urlparse(homepage_final).netloc

    feed_from_html = _extract_feed_links(html, homepage_final)
    feed_probed = _probe_feed_paths(homepage_final)
    all_feeds: list[str] = []
    for u in feed_from_html + feed_probed:
        if u not in all_feeds:
            all_feeds.append(u)

    rss_found = len(all_feeds) > 0
    scoped_feeds = [u for u in all_feeds if _feed_matches_listing_scope(homepage_final, u)]

    if scoped_feeds:
        best_feed = scoped_feeds[0]
        if not _looks_like_feed_url(best_feed) and len(scoped_feeds) > 1:
            for u in scoped_feeds:
                if _looks_like_feed_url(u):
                    best_feed = u
                    break
        resolved_url = _scrub_url(best_feed)
        use_rss = True
        notes_parts.append("RSS/Atom feed found; using feed URL for ingest (skips subscribe/JS-only homepages).")
    else:
        if rss_found:
            notes_parts.append(
                "RSS/Atom on page is site-wide, not this URL path; using HTML listing URL."
            )
        excerpt = html[:_MAX_HTML_FOR_LLM]
        listing = _llm_is_article_listing(homepage_final, excerpt)
        is_listing = True
        if isinstance(listing, dict) and "is_article_listing" in listing:
            is_listing = bool(listing.get("is_article_listing"))
            reason = str(listing.get("reason") or "").strip()
            if reason:
                notes_parts.append(reason)

        if not is_listing:
            return {
                "ok": False,
                "error": "not_a_listing",
                "message": "Could not find a homepage that looks like a story listing.",
            }

        resolved_url = homepage_final
        use_rss = False
        if not rss_found:
            notes_parts.append("No feed found; use HTML listing URL.")

    notes = " ".join(notes_parts).strip()

    return {
        "ok": True,
        "website_title": website_title,
        "homepage_url": homepage_final,
        "resolved_url": resolved_url,
        "use_rss": use_rss,
        "rss_found": rss_found,
        "confidence": confidence,
        "notes": notes,
    }


def resolve_source_json_body(body: bytes) -> tuple[dict[str, Any], int]:
    """Parse POST body; return (response_dict, http_status)."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "no_results", "message": "Invalid JSON body."}, 400
    if not isinstance(data, dict):
        return {"ok": False, "error": "no_results", "message": "JSON body must be an object."}, 400
    query = data.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "no_results", "message": "Missing non-empty string field \"query\"."}, 400
    locale = data.get("locale")
    if locale is not None and not isinstance(locale, str):
        return {"ok": False, "error": "no_results", "message": "Field \"locale\" must be a string if present."}, 400
    max_results = data.get("max_results", 10)
    if max_results is not None and not isinstance(max_results, int):
        return {"ok": False, "error": "no_results", "message": "Field \"max_results\" must be an integer if present."}, 400
    mr = 10 if max_results is None else max(1, min(int(max_results), 25))
    try:
        out = resolve_source(query.strip(), locale=locale, max_results=mr)
    except Exception as e:
        logger.exception("resolve_source failed: %s", e)
        return {
            "ok": False,
            "error": "upstream_timeout",
            "message": "Resolution failed unexpectedly.",
        }, 500
    status = 200
    return out, status
