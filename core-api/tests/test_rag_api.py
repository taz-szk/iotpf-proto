from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


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
            json={"pending_action_id": "pending-1"},
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
            json={"pending_action_id": "missing"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_confirm_action_returns_409_when_tool_no_longer_registered():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.execute_pending_action", side_effect=ValueError("Tool 'x' is no longer registered")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": "pending-1"},
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
