from unittest.mock import MagicMock, patch
import uuid
from app.services.audit import write_audit_log, log_audit


def test_write_audit_log_adds_to_session():
    mock_db = MagicMock()
    write_audit_log(
        mock_db,
        actor_type="platform",
        actor_id=str(uuid.uuid4()),
        actor_email="admin@example.com",
        action="login_success",
        ip_address="127.0.0.1",
    )
    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.actor_type == "platform"
    assert added.actor_email == "admin@example.com"
    assert added.action == "login_success"
    assert added.result == "success"


def test_write_audit_log_failure_result():
    mock_db = MagicMock()
    write_audit_log(
        mock_db,
        actor_type="tenant",
        actor_id=str(uuid.uuid4()),
        actor_email="user@tenant.com",
        action="login_failure",
        result="failure",
    )
    added = mock_db.add.call_args[0][0]
    assert added.result == "failure"


def test_write_audit_log_with_tenant_id():
    mock_db = MagicMock()
    tid = str(uuid.uuid4())
    write_audit_log(
        mock_db,
        actor_type="tenant",
        actor_id=str(uuid.uuid4()),
        actor_email="user@tenant.com",
        action="create_device",
        tenant_id=tid,
        resource_type="device",
        resource_id="device-001",
    )
    added = mock_db.add.call_args[0][0]
    assert str(added.tenant_id) == tid
    assert added.resource_type == "device"
    assert added.resource_id == "device-001"


def test_log_audit_swallows_exceptions():
    """DB 接続が失敗しても例外を外に出さないこと。"""
    with patch("app.services.audit.SessionLocal", side_effect=Exception("DB down")):
        log_audit("platform", str(uuid.uuid4()), "a@b.com", "login_success")
        # 例外が発生しないことを確認（ここに到達すれば OK）
