"""format_ingest_instructions (v2 combined prompts)."""

from news_manager.summarize import format_ingest_instructions


def test_global_only() -> None:
    assert format_ingest_instructions("  only global  ", "") == "only global"
    assert format_ingest_instructions("only global", "   ") == "only global"


def test_per_source_only() -> None:
    assert format_ingest_instructions("", "per") == "per"
    assert format_ingest_instructions("  ", "per") == "per"


def test_both_includes_precedence_wording() -> None:
    out = format_ingest_instructions("global body", "per body")
    assert "GLOBAL_INSTRUCTIONS" in out
    assert "PER_SOURCE_INSTRUCTIONS" in out
    assert "conflict" in out.lower()
    assert "global body" in out
    assert "per body" in out
