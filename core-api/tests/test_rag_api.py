from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"
PENDING_ACTION_ID = "22222222-2222-2222-2222-222222222222"
MISSING_PENDING_ACTION_ID = "33333333-3333-3333-3333-333333333333"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_chat_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/assistant/chat", json={"message": "質問"})
    assert resp.status_code == 401


def test_chat_returns_answer():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.answer_question", return_value={"answer": "回答です", "sources": [], "pending_action": None}) as mock_answer:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/chat",
            json={"message": "質問"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"answer": "回答です", "sources": [], "pending_action": None}
    mock_answer.assert_called_once()
    assert mock_answer.call_args.kwargs["tenant_id"] == TENANT_ID
    assert mock_answer.call_args.kwargs["message"] == "質問"
    assert mock_answer.call_args.kwargs["requested_by"] == "admin@iot.local"


def test_confirm_action_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/assistant/confirm-action", json={"pending_action_id": "x"})
    assert resp.status_code == 401


def test_confirm_action_executes_and_returns_result():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.execute_pending_action", return_value={"id": "rule-1"}) as mock_execute:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"result": {"id": "rule-1"}}
    mock_execute.assert_called_once()


def test_confirm_action_returns_404_when_not_found():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.execute_pending_action", return_value=None):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": MISSING_PENDING_ACTION_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_confirm_action_returns_409_when_tool_no_longer_registered():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.execute_pending_action", side_effect=ValueError("Tool 'x' is no longer registered")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 409


def test_reindex_documents_requires_platform_auth():
    resp = client.post("/platform/assistant/reindex")
    assert resp.status_code == 401


def test_reindex_documents_returns_chunk_count():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.reindex_all_documents", return_value=42) as mock_reindex:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.post(
            "/platform/assistant/reindex",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"chunk_count": 42}
    mock_reindex.assert_called_once_with(mock_db)


def test_reindex_documents_returns_500_when_no_documents_found():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.reindex_all_documents", side_effect=RuntimeError("No target documents found")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/platform/assistant/reindex",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 500


def test_confirm_action_returns_400_for_invalid_pending_action_id_format():
    resp = client.post(
        f"/tenants/{TENANT_ID}/assistant/confirm-action",
        json={"pending_action_id": "not-a-uuid"},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 400


def test_chat_returns_503_when_not_configured():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.is_assistant_configured", return_value=False):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/chat",
            json={"message": "質問"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 503


def test_confirm_action_returns_503_when_not_configured():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.is_assistant_configured", return_value=False):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 503


def test_reindex_returns_503_when_not_configured():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.is_assistant_configured", return_value=False):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/platform/assistant/reindex",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 503


def test_get_assistant_settings_requires_platform_auth():
    resp = client.get("/platform/assistant/settings")
    assert resp.status_code == 401


def test_get_assistant_settings_returns_current_values():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.get_assistant_settings") as mock_get:
        mock_get.return_value = MagicMock(ollama_url="http://172.31.19.73:11434", ollama_chat_model="qwen2.5:3b", ollama_embed_model="nomic-embed-text")
        mock_session.return_value = _session_ctx()
        resp = client.get("/platform/assistant/settings", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ollama_url"] == "http://172.31.19.73:11434"
    assert body["configured"] is True


def test_get_assistant_settings_configured_false_when_url_none():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.get_assistant_settings") as mock_get:
        mock_get.return_value = MagicMock(ollama_url=None, ollama_chat_model="qwen2.5:3b", ollama_embed_model="nomic-embed-text")
        mock_session.return_value = _session_ctx()
        resp = client.get("/platform/assistant/settings", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.json()["configured"] is False


def test_update_assistant_settings_requires_platform_auth():
    resp = client.put("/platform/assistant/settings", json={"ollama_url": "http://x:11434"})
    assert resp.status_code == 401


def test_update_assistant_settings_saves_and_returns_result():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.update_assistant_settings", return_value={
             "ollama_url": "http://172.31.19.73:11434", "ollama_chat_model": "qwen2.5:3b", "ollama_embed_model": "nomic-embed-text",
         }) as mock_update:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/assistant/settings",
            json={"ollama_url": "http://172.31.19.73:11434"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    mock_update.assert_called_once()


def test_test_connection_requires_platform_auth():
    resp = client.post("/platform/assistant/settings/test-connection", json={"ollama_url": "http://x:11434"})
    assert resp.status_code == 401


def test_test_connection_returns_ok_result():
    with patch("app.routers.rag.test_ollama_connection", return_value={"ok": True, "models": ["qwen2.5:3b"]}) as mock_test:
        resp = client.post(
            "/platform/assistant/settings/test-connection",
            json={"ollama_url": "http://172.31.19.73:11434"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "models": ["qwen2.5:3b"]}
    mock_test.assert_called_once_with("http://172.31.19.73:11434")
