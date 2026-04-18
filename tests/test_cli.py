"""CLI behavior for from-db selectors."""

from unittest.mock import MagicMock, patch

import pytest

from news_manager.cli import main


@patch("news_manager.cli.run_pipeline_from_db")
@patch("news_manager.cli.create_supabase_client")
@patch("news_manager.cli.groq_api_key")
@patch("news_manager.cli.supabase_settings")
@patch("news_manager.cli.load_dotenv_if_present")
def test_main_from_db_passes_category_and_source_selectors(
    _mock_dotenv: MagicMock,
    _mock_supabase_settings: MagicMock,
    _mock_groq: MagicMock,
    mock_create_supabase: MagicMock,
    mock_run_from_db: MagicMock,
) -> None:
    mock_create_supabase.return_value = MagicMock()

    code = main(
        [
            "--from-db",
            "--category",
            "News",
            "--source",
            "sid-123",
        ]
    )

    assert code == 0
    mock_run_from_db.assert_called_once()
    assert mock_run_from_db.call_args.kwargs["category_selector"] == "News"
    assert mock_run_from_db.call_args.kwargs["source_selector"] == "sid-123"


def test_main_rejects_removed_v1_flags() -> None:
    with patch("news_manager.cli.load_dotenv_if_present"):
        with patch("news_manager.cli.supabase_settings"), patch(
            "news_manager.cli.groq_api_key"
        ), patch("news_manager.cli.create_supabase_client"), patch(
            "news_manager.cli.run_pipeline_from_db"
        ):
            with pytest.raises(SystemExit) as exc:
                main(["--sources", "sources.json", "--instructions", "instructions.md"])
    assert exc.value.code == 2
