"""resolve_llm_ingest_instructions: application chooses a single LLM instruction block."""

from news_manager.pipeline import resolve_llm_ingest_instructions


def test_global_only() -> None:
    assert resolve_llm_ingest_instructions("  only global  ", None) == "only global"
    assert resolve_llm_ingest_instructions("only global", "") == "only global"
    assert resolve_llm_ingest_instructions("only global", "   ") == "only global"


def test_per_source_none_or_empty_uses_global() -> None:
    assert resolve_llm_ingest_instructions("g", None) == "g"
    assert resolve_llm_ingest_instructions("g", "") == "g"


def test_per_source_only() -> None:
    assert resolve_llm_ingest_instructions("", "per") == "per"
    assert resolve_llm_ingest_instructions("  ", "per") == "per"


def test_both_uses_per_source_only() -> None:
    out = resolve_llm_ingest_instructions("global body", "per body")
    assert out == "per body"
    assert "global body" not in out
