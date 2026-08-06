from unittest.mock import patch, MagicMock
import uuid

def test_create_tenant_success(client):
    mock_influx_org = {"id": "influx-org-id-001", "name": "test-tenant"}
    mock_influx_token = "influx-token-001"
    tenant_id = str(uuid.uuid4())

    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID(tenant_id)
    mock_tenant.name = "Test Tenant"
    mock_tenant.slug = "test-tenant"
    mock_tenant.status = "active"
    from datetime import datetime, timezone
    mock_tenant.created_at = datetime.now(timezone.utc)

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.setup_tenant", return_value=("influx-org-id-001", "influx-token-001")), \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "type": "platform"}):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.refresh.side_effect = lambda t: setattr(t, 'id', uuid.UUID(tenant_id)) or setattr(t, 'created_at', datetime.now(timezone.utc)) or setattr(t, 'status', 'active')

        resp = client.post(
            "/tenants",
            json={"name": "Test Tenant", "slug": "test-tenant"},
            headers={"Authorization": "Bearer dummy"}
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Tenant"
    assert data["slug"] == "test-tenant"

def test_create_tenant_duplicate(client):
    existing = MagicMock()

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "type": "platform"}):

        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        resp = client.post(
            "/tenants",
            json={"name": "Test Tenant", "slug": "test-tenant"},
            headers={"Authorization": "Bearer dummy"}
        )

    assert resp.status_code == 409

def test_create_tenant_unauthorized(client):
    with patch("app.routers.tenants.verify_token", return_value=None):
        resp = client.post(
            "/tenants",
            json={"name": "Test Tenant", "slug": "test-tenant"},
            headers={"Authorization": "Bearer invalid"}
        )
    assert resp.status_code == 401
