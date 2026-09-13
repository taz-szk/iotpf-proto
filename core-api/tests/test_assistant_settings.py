from unittest.mock import MagicMock, patch

from app.models.public import AssistantSettings
from app.services.assistant_settings import (
    get_assistant_settings,
    is_assistant_configured,
    test_ollama_connection as check_ollama_connection,
    update_assistant_settings,
)


def test_get_assistant_settings_queries_id_1():
    mock_db = MagicMock()
    row = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row

    result = get_assistant_settings(mock_db)

    assert result is row
    mock_db.query.assert_called_once_with(AssistantSettings)


def test_is_assistant_configured_true_when_ollama_url_set():
    mock_db = MagicMock()
    row = MagicMock(ollama_url="http://172.31.19.73:11434")
    mock_db.query.return_value.filter.return_value.first.return_value = row

    assert is_assistant_configured(mock_db) is True


def test_is_assistant_configured_false_when_ollama_url_none():
    mock_db = MagicMock()
    row = MagicMock(ollama_url=None)
    mock_db.query.return_value.filter.return_value.first.return_value = row

    assert is_assistant_configured(mock_db) is False


def test_is_assistant_configured_false_when_no_row():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    assert is_assistant_configured(mock_db) is False


def test_update_assistant_settings_sets_fields_and_commits():
    mock_db = MagicMock()
    row = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row

    result = update_assistant_settings(
        mock_db, ollama_url="http://172.31.19.73:11434", ollama_chat_model="qwen2.5:3b", ollama_embed_model="nomic-embed-text"
    )

    assert row.ollama_url == "http://172.31.19.73:11434"
    assert row.ollama_chat_model == "qwen2.5:3b"
    assert row.ollama_embed_model == "nomic-embed-text"
    assert result == {
        "ollama_url": "http://172.31.19.73:11434",
        "ollama_chat_model": "qwen2.5:3b",
        "ollama_embed_model": "nomic-embed-text",
    }
    mock_db.commit.assert_called_once()


def test_update_assistant_settings_empty_url_disables_feature():
    mock_db = MagicMock()
    row = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = row

    result = update_assistant_settings(mock_db, ollama_url="", ollama_chat_model="", ollama_embed_model="")

    assert row.ollama_url is None
    assert result["ollama_url"] is None
    assert result["ollama_chat_model"] == "qwen2.5:3b"
    assert result["ollama_embed_model"] == "nomic-embed-text"


def test_check_ollama_connection_returns_models_on_success():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"models": [{"name": "qwen2.5:3b"}, {"name": "nomic-embed-text"}]}
    with patch("app.services.assistant_settings.httpx.get", return_value=mock_resp) as mock_get:
        result = check_ollama_connection("http://172.31.19.73:11434")

    assert result == {"ok": True, "models": ["qwen2.5:3b", "nomic-embed-text"]}
    mock_get.assert_called_once_with("http://172.31.19.73:11434/api/tags", timeout=10.0)


def test_check_ollama_connection_returns_error_on_failure():
    with patch("app.services.assistant_settings.httpx.get", side_effect=Exception("connection refused")):
        result = check_ollama_connection("http://unreachable:11434")

    assert result["ok"] is False
    assert "connection refused" in result["error"]
