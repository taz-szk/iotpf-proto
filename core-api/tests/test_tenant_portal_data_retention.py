import uuid
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_token(role: str = "admin"):
    return create_access_token({
        "sub": str(uuid.uuid4()), "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def test_get_data_retention_requires_auth():
    resp = client.get("/tenant-portal/data-retention")
    assert resp.status_code == 401


def test_get_data_retention_rejects_non_admin():
    resp = client.get(
        "/tenant-portal/data-retention",
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403


def test_get_data_retention_allows_admin():
    tenant = MagicMock(data_retention_days=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.get_effective_retention_days", return_value=365):
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get(
            "/tenant-portal/data-retention",
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "365", "is_default": True}


def test_put_data_retention_sets_override_as_admin():
    tenant = MagicMock(data_retention_days=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/data-retention",
            json={"retention_days": "90"},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90"}
    assert tenant.data_retention_days == 90


def test_put_data_retention_rejects_non_admin():
    resp = client.put(
        "/tenant-portal/data-retention",
        json={"retention_days": "90"},
        cookies={"iot_token": _tenant_token("operator")},
    )
    assert resp.status_code == 403


def test_put_data_retention_rejects_below_minimum():
    tenant = MagicMock()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/data-retention",
            json={"retention_days": "10"},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 422
