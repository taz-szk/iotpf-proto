from unittest.mock import patch, MagicMock

import pytest

from app.services.ollama_client import chat, embed


def test_embed_sends_correct_request_and_returns_vector():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = embed("http://172.31.19.73:11434", "nomic-embed-text", "何か質問文")

    assert result == [0.1, 0.2, 0.3]
    call = mock_post.call_args
    assert call.args[0] == "http://172.31.19.73:11434/v1/embeddings"
    assert call.kwargs["json"] == {"model": "nomic-embed-text", "input": "何か質問文"}


def test_chat_sends_messages_and_returns_message_dict():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "回答です", "tool_calls": None}}]
    }
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = chat(ollama_url="http://172.31.19.73:11434", model="qwen2.5:3b", messages=[{"role": "user", "content": "質問"}])

    assert result == {"role": "assistant", "content": "回答です", "tool_calls": None}
    call = mock_post.call_args
    assert call.args[0] == "http://172.31.19.73:11434/v1/chat/completions"
    assert call.kwargs["json"]["model"] == "qwen2.5:3b"
    assert call.kwargs["json"]["messages"] == [{"role": "user", "content": "質問"}]
    assert "tools" not in call.kwargs["json"]


def test_chat_includes_tools_when_provided():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}}]
    }
    tools = [{"type": "function", "function": {"name": "some_tool"}}]
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = chat(ollama_url="http://172.31.19.73:11434", model="qwen2.5:3b", messages=[{"role": "user", "content": "質問"}], tools=tools)

    assert result["tool_calls"] == [{"id": "1"}]
    call = mock_post.call_args
    assert call.kwargs["json"]["tools"] == tools


def test_chat_includes_num_ctx_option():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "回答です", "tool_calls": None}}]
    }
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        chat(ollama_url="http://172.31.19.73:11434", model="qwen2.5:3b", messages=[{"role": "user", "content": "質問"}])

    call = mock_post.call_args
    assert call.kwargs["json"]["options"]["num_ctx"] == 8192


def test_chat_raises_on_error_status():
    mock_resp = MagicMock(status_code=500)
    mock_resp.raise_for_status.side_effect = Exception("ollama down")
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp):
        with pytest.raises(Exception):
            chat(ollama_url="http://172.31.19.73:11434", model="qwen2.5:3b", messages=[{"role": "user", "content": "質問"}])
