"""Generate static HTML from news-manager output.json."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

_CSS = """
body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; line-height: 1.5; margin: 0 auto; max-width: 52rem; padding: 1.5rem; color: #1a1a1a; }
h1 { font-size: 1.75rem; margin-top: 0; }
h2 { font-size: 1.15rem; margin: 1.75rem 0 0.5rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }
.meta { color: #555; font-size: 0.9rem; margin-bottom: 0.5rem; }
.short { margin: 0.5rem 0; }
.full { margin: 0.75rem 0; white-space: pre-wrap; }
article { margin-bottom: 2rem; }
nav { margin-bottom: 1.5rem; }
nav a { color: #0b57d0; }
"""


def _slug_filename(category: str, taken: set[str]) -> str:
    """Safe .html basename from category label; ensures uniqueness."""
    s = category.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s:
        s = "category"
    base = s[:80].rstrip("-") or "category"
    candidate = f"{base}.html"
    n = 0
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}.html"
    taken.add(candidate)
    return candidate


def _read_output_json(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array")
    return data


def _article_html(
    title: str,
    article_url: str,
    date_val: Any,
    source_val: str,
    short_summary: str,
    full_summary: str,
) -> str:
    date_str = "—"
    if date_val is not None and str(date_val).strip():
        date_str = html.escape(str(date_val))
    source_str = "—"
    if source_val.strip():
        source_str = html.escape(source_val.strip())
    title_esc = html.escape(title)
    url_esc = html.escape(article_url, quote=True)
    return f"""<article>
<h2><a href="{url_esc}" rel="noopener noreferrer">{title_esc}</a></h2>
<p class="meta"><strong>Date:</strong> {date_str} · <strong>Source:</strong> {source_str}</p>
<p class="short"><strong>Short summary:</strong> {html.escape(short_summary)}</p>
<p class="full"><strong>Full summary:</strong> {html.escape(full_summary)}</p>
</article>"""


def _category_page(category: str, articles: list[dict[str, Any]]) -> str:
    cat_esc = html.escape(category)
    blocks: list[str] = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        title = str(a.get("title", ""))
        url = str(a.get("url", ""))
        short_s = str(a.get("short_summary", ""))
        full_s = str(a.get("full_summary", ""))
        src = str(a.get("source", ""))
        blocks.append(
            _article_html(
                title,
                url,
                a.get("date"),
                src,
                short_s,
                full_s,
            )
        )
    body = "\n".join(blocks) if blocks else "<p>No articles.</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cat_esc}</title>
<style>{_CSS}</style>
</head>
<body>
<nav><a href="index.html">← All categories</a></nav>
<h1>{cat_esc}</h1>
{body}
</body>
</html>
"""


def _index_page(links: list[tuple[str, str]]) -> str:
    items = []
    for label, href in links:
        items.append(
            f'<li><a href="{html.escape(href, quote=True)}">{html.escape(label)}</a></li>'
        )
    lis = "\n".join(items)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>News categories</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Categories</h1>
<p>Browse summarized articles by category.</p>
<ul>
{lis}
</ul>
</body>
</html>
"""


def generate_html_files(input_path: Path, output_dir: Path) -> list[Path]:
    """
    Read output.json, write index.html and one HTML file per category.
    Returns paths written (excluding index first in list for tests convenience).
    """
    rows = _read_output_json(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    taken_names: set[str] = set()
    index_links: list[tuple[str, str]] = []
    written: list[Path] = []

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Item {idx} must be an object")
        cat = row.get("category")
        if not isinstance(cat, str) or not cat.strip():
            raise ValueError(f"Item {idx} needs non-empty string 'category'")
        arts = row.get("articles")
        if not isinstance(arts, list):
            raise ValueError(f"Item {idx} needs array 'articles'")
        filename = _slug_filename(cat, taken_names)
        path = output_dir / filename
        path.write_text(_category_page(cat, arts), encoding="utf-8")
        written.append(path)
        index_links.append((cat.strip(), filename))

    index_path = output_dir / "index.html"
    index_path.write_text(_index_page(index_links), encoding="utf-8")
    return [index_path, *written]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="to-html",
        description="Generate static HTML from news-manager output.json.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=Path("output.json"),
        help="Path to output.json (default: output.json)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("html"),
        help="Directory for generated HTML (default: html)",
    )
    args = parser.parse_args(argv)

    try:
        out = generate_html_files(args.input, args.output_dir)
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Wrote {len(out)} file(s) under {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
