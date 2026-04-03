"""Write output JSON."""

from __future__ import annotations

import json
from pathlib import Path

from news_manager.models import CategoryResult


def write_output(path: Path, categories: list[CategoryResult]) -> None:
    """Pretty-print JSON array of category blocks."""
    data = [c.to_json_dict() for c in categories]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
