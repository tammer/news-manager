"""Load browser-exported cookie JSON for subscriber-only HTTP fetches (see thestar_plan.md)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

from news_manager.fetch import source_base_label
from news_manager.models import Source

logger = logging.getLogger(__name__)


def cookies_dir_from_environ() -> Path:
    return Path(os.environ.get("NEWS_MANAGER_COOKIES_DIR", "cookies"))


def resolve_cookie_file(source: Source, cookies_dir: Path) -> Path | None:
    """
    Explicit ``Source.cookies`` path wins; else ``cookies/<host>.json`` or
    ``cookies/www.<host>.json`` using ``source_base_label(source.url)``.
    """
    if source.cookies:
        p = Path(source.cookies)
        if not p.is_absolute():
            p = Path.cwd() / p
        return p if p.is_file() else None
    key = source_base_label(source.url)
    if not key:
        return None
    c1 = cookies_dir / f"{key}.json"
    if c1.is_file():
        return c1
    c2 = cookies_dir / f"www.{key}.json"
    if c2.is_file():
        return c2
    return None


def resolve_cookie_file_for_home_url(home_raw: str, cookies_dir: Path) -> Path | None:
    """Same file naming as ``resolve_cookie_file``, keyed from feed/home URL."""
    key = source_base_label(home_raw)
    if not key:
        return None
    c1 = cookies_dir / f"{key}.json"
    if c1.is_file():
        return c1
    c2 = cookies_dir / f"www.{key}.json"
    if c2.is_file():
        return c2
    return None


def load_cookie_jar(path: Path) -> httpx.Cookies | None:
    """
    Parse browser extension JSON (array of cookie objects) into httpx.Cookies.
    Skips expired entries when ``expirationDate`` is present (Unix seconds).
    Returns None if file missing, invalid, or no usable cookies.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Invalid cookie file {path}: {e}") from e
    if not isinstance(raw, list):
        raise ValueError(f"Cookie file {path} must be a JSON array")
    jar = httpx.Cookies()
    now = time.time()
    count = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        value = item.get("value")
        if not isinstance(value, str):
            continue
        exp = item.get("expirationDate")
        if isinstance(exp, (int, float)) and float(exp) < now:
            continue
        domain = item.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            continue
        path_s = item.get("path")
        if not isinstance(path_s, str) or not path_s.strip():
            path_s = "/"
        jar.set(name.strip(), value, domain=domain.strip(), path=path_s)
        count += 1
    if count == 0:
        return None
    return jar


def cookie_jar_for_source(source: Source) -> httpx.Cookies | None:
    """Resolve path from source + env cookies dir and load jar."""
    path = resolve_cookie_file(source, cookies_dir_from_environ())
    if path is None:
        return None
    jar = load_cookie_jar(path)
    if jar is not None:
        logger.info(
            "Loaded subscriber cookies for %s from %s",
            source_base_label(source.url),
            path.name,
        )
    return jar


def cookie_jar_for_home_url(home_raw: str) -> httpx.Cookies | None:
    """For fetch_articles_for_source and tools: key from home/feed URL."""
    path = resolve_cookie_file_for_home_url(home_raw, cookies_dir_from_environ())
    if path is None:
        return None
    jar = load_cookie_jar(path)
    if jar is not None:
        logger.info("Loaded subscriber cookies from %s", path.name)
    return jar
