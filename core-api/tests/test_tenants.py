from unittest.mock import patch, MagicMock
import uuid

from app.models.public import Tenant

TENANT_ID = "11111111-1111-1111-1111-111111111111"

def test_create_tenant_success(client):
    mock_influx_org = {"id": "influx-org-id-001", "name": "test-tenant"}
    mock_influx_token = "influx-token-001"
    tenant_id = str(uuid.uuid4())

    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID(tenant_id)
    mock_tenant.name = "Test Tenant"
    mock_tenant.slug = "test-tenant"
    mock_tenant.status = "active"
    mock_tenant.grafana_org_id = "42"
    from datetime import datetime, timezone
    mock_tenant.created_at = datetime.now(timezone.utc)

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.setup_tenant", return_value=("influx-org-id-001", "influx-token-001", 42)), \
         patch("app.routers.tenants.create_influxdb_bucket"), \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "admin@example.com", "type": "platform"}):

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

def test_create_tenant_seeds_default_prices(client):
    tenant_id = str(uuid.uuid4())

    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID(tenant_id)
    mock_tenant.name = "Test Tenant"
    mock_tenant.slug = "test-tenant"
    mock_tenant.status = "active"
    mock_tenant.grafana_org_id = "42"
    from datetime import datetime, timezone
    mock_tenant.created_at = datetime.now(timezone.utc)

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.setup_tenant", return_value=("influx-org-id-001", "influx-token-001", 42)), \
         patch("app.routers.tenants.seed_tenant_default_prices") as mock_seed, \
         patch("app.routers.tenants.create_influxdb_bucket"), \
         patch("app.routers.tenants.write_audit_log"), \
         patch("app.routers.tenants.uuid.uuid4", return_value=uuid.UUID(tenant_id)), \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "admin@example.com", "type": "platform"}):

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
    mock_seed.assert_called_once_with(mock_db, tenant_id)

def test_create_tenant_creates_influxdb_bucket_with_default_retention(client):
    tenant_id = str(uuid.uuid4())

    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID(tenant_id)
    mock_tenant.name = "Test Tenant"
    mock_tenant.slug = "test-tenant"
    mock_tenant.status = "active"
    mock_tenant.grafana_org_id = "42"
    from datetime import datetime, timezone
    mock_tenant.created_at = datetime.now(timezone.utc)

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.setup_tenant", return_value=("influx-org-id-001", "influx-token-001", 42)), \
         patch("app.routers.tenants.seed_tenant_default_prices"), \
         patch("app.routers.tenants.create_influxdb_bucket") as mock_create_bucket, \
         patch("app.routers.tenants.get_default_retention_days", return_value=365), \
         patch("app.routers.tenants.write_audit_log"), \
         patch("app.routers.tenants.uuid.uuid4", return_value=uuid.UUID(tenant_id)), \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "admin@example.com", "type": "platform"}):

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
    mock_create_bucket.assert_called_once()
    call_args = mock_create_bucket.call_args[0]
    assert call_args[0] == "influx-org-id-001"  # org_id
    assert call_args[2] == 365  # retention_days（get_default_retention_daysの戻り値）


def test_create_tenant_succeeds_even_if_influxdb_bucket_creation_fails(client):
    tenant_id = str(uuid.uuid4())

    mock_tenant = MagicMock()
    mock_tenant.id = uuid.UUID(tenant_id)
    mock_tenant.name = "Test Tenant"
    mock_tenant.slug = "test-tenant"
    mock_tenant.status = "active"
    mock_tenant.grafana_org_id = "42"
    from datetime import datetime, timezone
    mock_tenant.created_at = datetime.now(timezone.utc)

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.setup_tenant", return_value=("influx-org-id-001", "influx-token-001", 42)), \
         patch("app.routers.tenants.seed_tenant_default_prices"), \
         patch("app.routers.tenants.create_influxdb_bucket", side_effect=Exception("influxdb down")), \
         patch("app.routers.tenants.get_default_retention_days", return_value=365), \
         patch("app.routers.tenants.write_audit_log"), \
         patch("app.routers.tenants.uuid.uuid4", return_value=uuid.UUID(tenant_id)), \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "admin@example.com", "type": "platform"}):

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


def test_get_tenant_data_retention_returns_override_when_set(client):
    tenant = MagicMock(data_retention_days=90)
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.get_effective_retention_days", return_value=90), \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/data-retention",
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90", "is_default": False}


def test_get_tenant_data_retention_tenant_not_found(client):
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/data-retention",
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 404


def test_put_tenant_data_retention_sets_override(client):
    tenant = MagicMock(data_retention_days=None)
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": "90"},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90"}
    assert tenant.data_retention_days == 90


def test_put_tenant_data_retention_clears_override_with_null(client):
    tenant = MagicMock(data_retention_days=90)
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": None},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": None}
    assert tenant.data_retention_days is None


def test_put_tenant_data_retention_does_not_read_tenant_attribute_after_commit(client):
    tenant = MagicMock(spec=Tenant)
    tenant.data_retention_days = None

    def _commit_side_effect():
        # Simulate SQLAlchemy's expire-on-commit: any further attribute
        # access on `tenant` after this point should be treated as a bug.
        type(tenant).data_retention_days = property(
            lambda self: (_ for _ in ()).throw(AssertionError("read tenant attribute after commit"))
        )

    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": str(uuid.uuid4()), "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_db.commit.side_effect = _commit_side_effect
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": "90"},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"retention_days": "90"}


def test_put_tenant_data_retention_rejects_below_minimum(client):
    tenant = MagicMock()
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.verify_token", return_value={"sub": "admin-id", "email": "a@b.com", "type": "platform"}):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/data-retention",
            json={"retention_days": "10"},
            headers={"Authorization": "Bearer dummy"},
        )
    assert resp.status_code == 422
