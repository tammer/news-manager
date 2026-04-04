"""CLI: verify subscriber cookies can fetch one article URL (thestar_plan.md)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from news_manager.config import load_dotenv_if_present
from news_manager.cookies_loader import (
    cookies_dir_from_environ,
    load_cookie_jar,
    resolve_cookie_file_for_home_url,
)
from news_manager.fetch import USER_AGENT, fetch_single_raw_article
import httpx


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_present()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Fetch one article URL with optional subscriber cookies; print OK or FAIL.",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Article URL to fetch (e.g. a thestar.com article)",
    )
    parser.add_argument(
        "--cookies-file",
        type=Path,
        default=None,
        help="Explicit cookie JSON path (overrides auto-resolve from --url host)",
    )
    parser.add_argument(
        "--cookies-dir",
        type=Path,
        default=None,
        help="Directory for cookies/<host>.json (default: NEWS_MANAGER_COOKIES_DIR or cookies/)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout seconds (default: 30)",
    )
    args = parser.parse_args(argv)

    cookies_dir = args.cookies_dir if args.cookies_dir is not None else cookies_dir_from_environ()
    path = args.cookies_file
    if path is None:
        path = resolve_cookie_file_for_home_url(args.url, cookies_dir)

    jar = None
    if path is not None:
        try:
            jar = load_cookie_jar(path)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if jar is None:
            print(f"No usable cookies in {path}", file=sys.stderr)
    else:
        print(
            "No cookie file found (use --cookies-file or place cookies/<host>.json).",
            file=sys.stderr,
        )

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    client_kw: dict = {
        "headers": {"User-Agent": USER_AGENT},
        "timeout": args.timeout,
        "limits": limits,
    }
    if jar is not None:
        client_kw["cookies"] = jar

    try:
        with httpx.Client(**client_kw) as client:
            raw = fetch_single_raw_article(client, args.url, None, None)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2

    if raw is None:
        print("FAIL: no HTML or empty extractable body", file=sys.stderr)
        return 2
    if not raw.content.strip():
        print("FAIL: empty article body", file=sys.stderr)
        return 2

    print("OK")
    t = raw.title or ""
    preview = (t[:120] + "…") if len(t) > 120 else t
    print(f"title: {preview!r}")
    print(f"chars: {len(raw.content)}")
    print(f"raw: {raw.content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
