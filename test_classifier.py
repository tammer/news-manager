"""Deprecated: discovery no longer exposes a single-page classifier here.

Use ``inspect_page_meta.py`` for one-off title/meta + LLM classification, or call
``POST /api/sources/discover`` for multi-query discovery.
"""

from __future__ import annotations

import sys


def main() -> None:
    print(
        "test_classifier.py is deprecated: source discovery was rebuilt without "
        "``_classify_url``. Use inspect_page_meta.py or the discover API.",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
