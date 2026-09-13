from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from pydantic import BaseModel
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"
PENDING_ACTION_ID = "22222222-2222-2222-2222-222222222222"


def _tenant_cookie(role="admin"):
    token = create_access_token({"sub": "user-1", "email": "admin@tenant.example", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_chat_requires_tenant_auth():
    resp = client.post("/tenant-portal/me/assistant/chat", json={"message": "質問"})
    assert resp.status_code == 401


def test_chat_returns_answer_for_admin():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.answer_question", return_value={"answer": "回答です", "sources": [], "pending_action": None}) as mock_answer:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/chat",
            json={"message": "質問"},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 200
    assert resp.json() == {"answer": "回答です", "sources": [], "pending_action": None}
    call_kwargs = mock_answer.call_args.kwargs
    assert call_kwargs["tenant_id"] == TENANT_ID
    assert call_kwargs["message"] == "質問"
    assert call_kwargs["requested_by"] == "admin@tenant.example"
    from app.services.rag_tools.tenant import TENANT_TOOLS
    assert call_kwargs["tools"] is TENANT_TOOLS
    assert call_kwargs["payload"]["tenant_id"] == TENANT_ID


def test_chat_returns_answer_for_operator():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.answer_question", return_value={"answer": "回答です", "sources": [], "pending_action": None}):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/chat",
            json={"message": "質問"},
            cookies=_tenant_cookie("operator"),
        )
    assert resp.status_code == 200


def test_chat_returns_503_when_not_configured():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=False):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/chat",
            json={"message": "質問"},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 503


def test_confirm_action_requires_tenant_auth():
    resp = client.post("/tenant-portal/me/assistant/confirm-action", json={"pending_action_id": PENDING_ACTION_ID})
    assert resp.status_code == 401


def test_confirm_action_executes_and_returns_result():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", return_value={"id": "rule-1"}) as mock_execute:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 200
    assert resp.json() == {"result": {"id": "rule-1"}}
    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["tenant_id"] == TENANT_ID
    assert call_kwargs["payload"]["tenant_id"] == TENANT_ID


def test_confirm_action_returns_404_when_not_found():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", return_value=None):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 404


def test_confirm_action_returns_409_when_tool_no_longer_registered():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", side_effect=ValueError("Tool 'x' is no longer registered")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 409


def test_confirm_action_returns_422_when_tool_handler_raises_validation_error():
    """ハンドラ内でのpydantic ValidationError（ValueErrorのサブクラス）は409ではなく422にマップされる。"""
    class _Strict(BaseModel):
        threshold: float

    def _raise_validation_error(*args, **kwargs):
        _Strict()  # 必須フィールド無しでValidationErrorを送出させる

    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", side_effect=_raise_validation_error):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 422


def test_confirm_action_returns_422_for_invalid_pending_action_id_format():
    resp = client.post(
        "/tenant-portal/me/assistant/confirm-action",
        json={"pending_action_id": "not-a-uuid"},
        cookies=_tenant_cookie("admin"),
    )
    assert resp.status_code == 422
