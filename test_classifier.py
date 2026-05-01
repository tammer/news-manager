"""Quick manual runner for the discovery URL classifier."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pprint import pprint

from news_manager.source_discovery import _classify_url

TEST_URL = "https://mrbookreview.com/best-book-blogs-to-read-updated/"
TEST_INTENT = "book reviews"


def main() -> None:
    result = _classify_url(TEST_URL, TEST_INTENT)
    print("Raw classifier return value:")
    pprint(result)

    if result is not None and is_dataclass(result):
        payload = asdict(result)
        payload.pop("content", None)
        print("\nAs dict (without content):")
        pprint(payload)


if __name__ == "__main__":
    main()
