from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import uuid

def _make_token(tenant_id):
    token = MagicMock()
    token.tenant_id = uuid.UUID(tenant_id)
    token.is_active = True
    token.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    token.max_devices = 100
    token.registered_count = 0
    return token

def test_provision_success(client):
    tenant_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)
    mock_cert_pem = "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----"
    mock_key_pem = "-----BEGIN EC PRIVATE KEY-----\nMOCK\n-----END EC PRIVATE KEY-----"

    with patch("app.routers.provisioning.SessionLocal") as mock_session, \
         patch("app.routers.provisioning.issue_device_cert_for_tenant", return_value=(mock_cert_pem, mock_key_pem)):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        mock_db.execute.return_value.first.return_value = None

        resp = client.post("/provision", json={
            "bootstrap_token": "valid-token",
            "device_id": "device-serial-001"
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "certificate" in data
    assert "private_key" in data
    assert data["tenant_id"] == tenant_id

def test_provision_invalid_token(client):
    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = client.post("/provision", json={
            "bootstrap_token": "invalid-token",
            "device_id": "device-001"
        })

    assert resp.status_code == 401

def test_provision_expired_token(client):
    tenant_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)
    mock_token.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token

        resp = client.post("/provision", json={
            "bootstrap_token": "expired-token",
            "device_id": "device-001"
        })

    assert resp.status_code == 401

def test_provision_max_devices_reached(client):
    tenant_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)
    mock_token.max_devices = 5
    mock_token.registered_count = 5

    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token

        resp = client.post("/provision", json={
            "bootstrap_token": "full-token",
            "device_id": "device-001"
        })

    assert resp.status_code == 403

def test_provision_duplicate_device(client):
    tenant_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)

    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        mock_db.execute.return_value.first.return_value = MagicMock()

        resp = client.post("/provision", json={
            "bootstrap_token": "valid-token",
            "device_id": "existing-device"
        })

    assert resp.status_code == 409

def test_provision_with_valid_group_id(client):
    tenant_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)
    mock_cert_pem = "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----"
    mock_key_pem = "-----BEGIN EC PRIVATE KEY-----\nMOCK\n-----END EC PRIVATE KEY-----"

    with patch("app.routers.provisioning.SessionLocal") as mock_session, \
         patch("app.routers.provisioning.issue_device_cert_for_tenant", return_value=(mock_cert_pem, mock_key_pem)):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        # 1回目: グループ存在確認 -> 存在する, 2回目: 既存デバイスチェック -> なし
        mock_db.execute.return_value.first.side_effect = [MagicMock(), None]

        resp = client.post("/provision", json={
            "bootstrap_token": "valid-token",
            "device_id": "device-serial-002",
            "group_id": group_id,
        })

    assert resp.status_code == 200
    insert_call = mock_db.execute.call_args_list[-1]
    assert insert_call.args[1]["group_id"] == group_id

def test_provision_with_invalid_group_id(client):
    tenant_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)

    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        # グループ存在確認 -> 存在しない
        mock_db.execute.return_value.first.side_effect = [None]

        resp = client.post("/provision", json={
            "bootstrap_token": "valid-token",
            "device_id": "device-serial-003",
            "group_id": str(uuid.uuid4()),
        })

    assert resp.status_code == 422

def test_provision_groups_success(client):
    tenant_id = str(uuid.uuid4())
    mock_token = _make_token(tenant_id)
    group_id = str(uuid.uuid4())
    mock_row = MagicMock()
    mock_row.id = group_id
    mock_row.name = "拠点A"
    mock_row.description = "説明"
    mock_row.created_at = None

    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        mock_db.execute.return_value.fetchall.return_value = [mock_row]

        resp = client.post("/provision/groups", json={"bootstrap_token": "valid-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert data == [{"id": group_id, "name": "拠点A", "description": "説明", "created_at": None}]

def test_provision_groups_invalid_token(client):
    with patch("app.routers.provisioning.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = client.post("/provision/groups", json={"bootstrap_token": "invalid-token"})

    assert resp.status_code == 401
