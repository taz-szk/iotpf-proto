from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy.exc import IntegrityError
from app.services.tenant_users import create_tenant_user_record


def test_create_tenant_user_record_inserts_and_syncs_grafana():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.tenant_users.engine") as mock_engine, \
         patch("app.services.tenant_users.ensure_grafana_user_in_org") as mock_grafana:
        mock_engine.connect.return_value = mock_conn
        create_tenant_user_record(
            tenant_id="t1", schema="tenant_t1", email="a@example.com",
            password_hash="hashed", role="admin", grafana_org_id="5",
        )
    assert mock_conn.execute.called
    mock_grafana.assert_called_once_with(5, "a@example.com", "Viewer", "t1")
    mock_conn.commit.assert_called_once()


def test_create_tenant_user_record_skips_grafana_when_no_org():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.tenant_users.engine") as mock_engine, \
         patch("app.services.tenant_users.ensure_grafana_user_in_org") as mock_grafana:
        mock_engine.connect.return_value = mock_conn
        create_tenant_user_record(
            tenant_id="t1", schema="tenant_t1", email="a@example.com",
            password_hash="hashed", role="viewer", grafana_org_id=None,
        )
    mock_grafana.assert_not_called()


def test_create_tenant_user_record_propagates_integrity_error():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.side_effect = IntegrityError("dup", {}, Exception("dup"))
    with patch("app.services.tenant_users.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        with pytest.raises(IntegrityError):
            create_tenant_user_record(
                tenant_id="t1", schema="tenant_t1", email="a@example.com",
                password_hash="hashed", role="admin", grafana_org_id=None,
            )
