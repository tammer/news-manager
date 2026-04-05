"""CLI entry point for news-manager."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from news_manager.config import (
    DEFAULT_CONTENT_MAX_CHARS,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_MAX_ARTICLES,
    groq_api_key,
    load_dotenv_if_present,
    read_instructions,
    read_sources_json,
    supabase_settings,
)
from news_manager.pipeline import run_pipeline
from news_manager.supabase_sync import create_supabase_client


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_present()

    parser = argparse.ArgumentParser(
        prog="news-manager",
        description="Fetch sources, filter and summarize articles using Groq; sync to Supabase.",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        required=True,
        help="Path to sources.json",
    )
    parser.add_argument(
        "--instructions",
        type=Path,
        required=True,
        help="Path to instructions.md",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=DEFAULT_MAX_ARTICLES,
        metavar="N",
        help=f"Max articles to fetch per source (default: {DEFAULT_MAX_ARTICLES})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT,
        metavar="SEC",
        help=f"HTTP timeout in seconds (default: {DEFAULT_HTTP_TIMEOUT})",
    )
    parser.add_argument(
        "--content-max-chars",
        type=int,
        default=DEFAULT_CONTENT_MAX_CHARS,
        metavar="N",
        help=f"Max article body chars sent to the LLM (default: {DEFAULT_CONTENT_MAX_CHARS})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log INFO to stderr (e.g. cookie debug lines)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    try:
        categories = read_sources_json(args.sources)
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        instructions = read_instructions(args.instructions)
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        supabase_settings()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        groq_api_key()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        sb = create_supabase_client()
        run_pipeline(
            categories,
            instructions,
            supabase_client=sb,
            max_articles=args.max_articles,
            http_timeout=args.timeout,
            content_max_chars=args.content_max_chars,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
