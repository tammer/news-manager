"""Bundled default user catalog JSON and loader normalization."""

import json
from pathlib import Path

import pytest

from news_manager.config import load_default_user_catalog_dict


def test_bundled_default_user_catalog_json_is_valid() -> None:
    """Strict JSON parse of the file shipped with the package."""
    path = Path(__file__).resolve().parent.parent / "news_manager" / "default_user_catalog.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert isinstance(data, dict)
    assert data.get("schema_version") == 1
    assert isinstance(data.get("categories"), list)
    assert len(data["categories"]) >= 1


def test_load_default_user_catalog_dict_bundled_file_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loader uses packaged default and returns only schema_version + categories."""
    monkeypatch.delenv("DEFAULT_USER_CATALOG_PATH", raising=False)
    out = load_default_user_catalog_dict()
    assert set(out.keys()) == {"schema_version", "categories"}
    assert out["schema_version"] == 1
    assert len(out["categories"]) >= 1


def test_load_default_user_catalog_dict_strips_export_only_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = {
        "schema_version": 1,
        "user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "email": "ghost@example.com",
        "categories": [
            {
                "category": "X",
                "instruction": "",
                "sources": [{"url": "https://example.com/", "use_rss": False}],
            }
        ],
    }
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("DEFAULT_USER_CATALOG_PATH", str(p))

    out = load_default_user_catalog_dict()
    assert set(out.keys()) == {"schema_version", "categories"}
    assert out["schema_version"] == 1
    assert out["categories"] == raw["categories"]
    assert "user_id" not in out
    assert "email" not in out


def test_load_default_user_catalog_dict_rejects_wrong_schema_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "cat.json"
    p.write_text(
        json.dumps({"schema_version": 2, "categories": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEFAULT_USER_CATALOG_PATH", str(p))
    with pytest.raises(ValueError, match="schema_version"):
        load_default_user_catalog_dict()
