import pytest
from unittest.mock import patch, MagicMock

from app.services.ollama_client import chat, embed


def test_embed_sends_correct_request_and_returns_vector():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = embed("何か質問文")

    assert result == [0.1, 0.2, 0.3]
    call = mock_post.call_args
    assert "/v1/embeddings" in call.args[0]
    assert call.kwargs["json"]["input"] == "何か質問文"
    assert call.kwargs["json"]["model"] == "qwen2.5:3b" or "model" in call.kwargs["json"]


def test_chat_sends_messages_and_returns_message_dict():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "回答です", "tool_calls": None}}]
    }
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = chat(messages=[{"role": "user", "content": "質問"}])

    assert result == {"role": "assistant", "content": "回答です", "tool_calls": None}
    call = mock_post.call_args
    assert "/v1/chat/completions" in call.args[0]
    assert call.kwargs["json"]["messages"] == [{"role": "user", "content": "質問"}]
    assert "tools" not in call.kwargs["json"]


def test_chat_includes_tools_when_provided():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}}]
    }
    tools = [{"type": "function", "function": {"name": "some_tool"}}]
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = chat(messages=[{"role": "user", "content": "質問"}], tools=tools)

    assert result["tool_calls"] == [{"id": "1"}]
    call = mock_post.call_args
    assert call.kwargs["json"]["tools"] == tools


def test_chat_includes_num_ctx_option():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "回答です", "tool_calls": None}}]
    }
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        chat(messages=[{"role": "user", "content": "質問"}])

    call = mock_post.call_args
    assert call.kwargs["json"]["options"]["num_ctx"] == 8192


def test_chat_raises_on_error_status():
    mock_resp = MagicMock(status_code=500)
    mock_resp.raise_for_status.side_effect = Exception("ollama down")
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp):
        with pytest.raises(Exception):
            chat(messages=[{"role": "user", "content": "質問"}])
