"""CLI entry point for news-manager."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from news_manager.config import (
    DEFAULT_CONTENT_MAX_CHARS,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_MAX_ARTICLES,
    groq_api_key,
    load_dotenv_if_present,
    supabase_settings,
)
from news_manager.pipeline import run_pipeline_from_db
from news_manager.supabase_sync import create_supabase_client
from news_manager.user_sources_catalog import (
    export_user_sources_catalog,
    fetch_user_id_by_email,
    import_user_sources_catalog,
)


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    """
    If the first token is not a known subcommand or global help flag,
    treat the whole argv as ``ingest`` arguments (backward compatible).
    """
    if not argv:
        return ["ingest"]
    head = argv[0]
    if head in ("ingest", "user-sources", "--help", "-h"):
        return argv
    return ["ingest", *argv]


def _cmd_ingest(args: argparse.Namespace) -> int:
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
        _ = run_pipeline_from_db(
            supabase_client=sb,
            max_articles=args.max_articles,
            http_timeout=args.timeout,
            content_max_chars=args.content_max_chars,
            user_id_selector=args.user_id,
            category_selector=args.category,
            source_selector=args.source,
            reprocess=args.reprocess,
            html_discovery_llm=args.html_discovery_llm,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


def _cmd_user_sources_export(args: argparse.Namespace) -> int:
    try:
        url, key = supabase_settings()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    email = args.email.strip()
    try:
        user_id = fetch_user_id_by_email(
            supabase_url=url, service_role_key=key, email=email
        )
        sb = create_supabase_client()
        payload = export_user_sources_catalog(sb, user_id, email=email)
    except (RuntimeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    indent = None if getattr(args, "compact", False) else 2
    json.dump(payload, sys.stdout, indent=indent, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _cmd_user_sources_import(args: argparse.Namespace) -> int:
    try:
        url, key = supabase_settings()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    email = args.email.strip()
    if args.file is not None:
        try:
            raw = args.file.read_text(encoding="utf-8")
        except OSError as e:
            print(str(e), file=sys.stderr)
            return 1
    else:
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    try:
        user_id = fetch_user_id_by_email(
            supabase_url=url, service_role_key=key, email=email
        )
        sb = create_supabase_client()
        summary = import_user_sources_catalog(sb, user_id, payload)
    except (RuntimeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(
        f"import ok: categories_created={summary.categories_created} "
        f"categories_reused={summary.categories_reused} "
        f"sources_inserted={summary.sources_inserted} "
        f"sources_skipped={summary.sources_skipped}",
        file=sys.stderr,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-manager",
        description="Fetch sources, filter and summarize articles using Groq; sync to Supabase.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest",
        help="Run fetch → summarize → Supabase sync (default behavior).",
    )
    ingest.add_argument(
        "--from-db",
        action="store_true",
        help="Deprecated no-op. DB-backed ingest is the only mode.",
    )
    ingest.add_argument(
        "--category",
        type=str,
        default=None,
        help="Limit ingest to one category (match by category id or name).",
    )
    ingest.add_argument(
        "--source",
        type=str,
        default=None,
        help="Limit ingest to one source (match by source id or name).",
    )
    ingest.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Limit ingest to one user (exact user_id match).",
    )
    ingest.add_argument(
        "--reprocess",
        action="store_true",
        help=(
            "Delete cached news_articles / news_article_exclusions rows and "
            "re-fetch + LLM for those URLs."
        ),
    )
    ingest.add_argument(
        "--html-discovery-llm",
        action="store_true",
        help=(
            "For HTML (non-RSS) sources, use Groq to pick article URLs from homepage links "
            "(extra API calls; requires GROQ_API_KEY). See GROQ_MODEL_HTML_DISCOVERY / "
            "HTML_DISCOVERY_MAX_CANDIDATES in docs."
        ),
    )
    ingest.add_argument(
        "--max-articles",
        type=int,
        default=DEFAULT_MAX_ARTICLES,
        metavar="N",
        help=f"Max articles to fetch per source (default: {DEFAULT_MAX_ARTICLES})",
    )
    ingest.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT,
        metavar="SEC",
        help=f"HTTP timeout in seconds (default: {DEFAULT_HTTP_TIMEOUT})",
    )
    ingest.add_argument(
        "--content-max-chars",
        type=int,
        default=DEFAULT_CONTENT_MAX_CHARS,
        metavar="N",
        help=f"Max article body chars sent to the LLM (default: {DEFAULT_CONTENT_MAX_CHARS})",
    )
    ingest.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log INFO to stderr (e.g. cookie debug lines)",
    )
    ingest.set_defaults(_handler=_cmd_ingest)

    us = sub.add_parser(
        "user-sources",
        help="Export or import per-user categories + sources JSON (service role).",
    )
    us_sub = us.add_subparsers(dest="user_sources_cmd", required=True)

    us_ex = us_sub.add_parser(
        "export",
        help="Print categories + sources for an auth user email as JSON to stdout.",
    )
    us_ex.add_argument(
        "--email",
        required=True,
        help="Auth user email address.",
    )
    us_ex.add_argument(
        "--compact",
        action="store_true",
        help="Single-line JSON on stdout.",
    )
    us_ex.set_defaults(_handler=_cmd_user_sources_export)

    us_im = us_sub.add_parser(
        "import",
        help="Import categories + sources JSON for an auth user (stdin or --file).",
    )
    us_im.add_argument(
        "--email",
        required=True,
        help="Auth user email address.",
    )
    us_im.add_argument(
        "--file",
        type=Path,
        default=None,
        help="JSON file path (default: read stdin).",
    )
    us_im.set_defaults(_handler=_cmd_user_sources_import)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_present()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    normalized = _normalize_cli_argv(raw_argv)
    parser = _build_parser()
    args = parser.parse_args(normalized)

    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
