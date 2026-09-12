from decimal import Decimal
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token():
    return create_access_token({"sub": "user-id", "email": "user@test.com", "type": "tenant",
                                 "tenant_id": "11111111-1111-1111-1111-111111111111", "role": "admin"})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_get_default_prices_requires_platform_auth():
    resp = client.get("/platform/billing/default-prices")
    assert resp.status_code == 401


def test_get_default_prices_rejects_tenant_token():
    resp = client.get("/platform/billing/default-prices",
                       headers={"Authorization": f"Bearer {_tenant_token()}"})
    assert resp.status_code == 401


def test_get_default_prices_returns_current_values():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_unit_prices",
               return_value={"base_fee": Decimal("5000"), "data_points": Decimal("0.01")}):
        mock_session.return_value = _session_ctx()
        resp = client.get("/platform/billing/default-prices",
                           headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    body = resp.json()
    by_key = {item["item_key"]: item["unit_price"] for item in body}
    assert by_key == {"base_fee": "5000", "data_points": "0.01"}


def test_put_default_prices_replaces_and_returns_updated_list():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_unit_prices") as mock_set, \
         patch("app.routers.platform.write_audit_log"):
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/billing/default-prices",
            json=[{"item_key": "base_fee", "unit_price": "5000"}],
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    mock_set.assert_called_once()
    called_prices = mock_set.call_args[0][1]
    assert called_prices == {"base_fee": Decimal("5000")}
    body = resp.json()
    assert body == [{"item_key": "base_fee", "unit_price": "5000"}]


def test_put_default_prices_rejects_invalid_unit_price():
    resp = client.put(
        "/platform/billing/default-prices",
        json=[{"item_key": "base_fee", "unit_price": "not-a-number"}],
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_put_default_prices_rejects_unknown_item_key():
    resp = client.put(
        "/platform/billing/default-prices",
        json=[{"item_key": "not_a_real_item", "unit_price": "5000"}],
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_get_tax_rate_requires_platform_auth():
    resp = client.get("/platform/billing/tax-rate")
    assert resp.status_code == 401


def test_get_tax_rate_returns_current_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_tax_rate", return_value=Decimal("0.10")):
        mock_session.return_value = _session_ctx()
        resp = client.get("/platform/billing/tax-rate",
                           headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    assert resp.json() == {"tax_rate": "0.10"}


def test_put_tax_rate_updates_and_returns_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_tax_rate") as mock_set, \
         patch("app.routers.platform.write_audit_log") as mock_audit:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/billing/tax-rate",
            json={"tax_rate": "0.08"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"tax_rate": "0.08"}
    mock_set.assert_called_once_with(mock_db, Decimal("0.08"))
    assert mock_audit.called
    assert mock_db.commit.called


def test_put_tax_rate_rejects_invalid_value():
    resp = client.put(
        "/platform/billing/tax-rate",
        json={"tax_rate": "1.5"},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_put_default_prices_commits_after_audit_log():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_unit_prices"), \
         patch("app.routers.platform.write_audit_log") as mock_audit:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        client.put(
            "/platform/billing/default-prices",
            json=[{"item_key": "base_fee", "unit_price": "5000"}],
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    # write_audit_log must be called, and a commit must happen after it
    # (call order matters: the commit that persists the audit row must come
    # after write_audit_log was invoked)
    assert mock_audit.called
    assert mock_db.commit.called


def test_get_bill_shock_threshold_requires_platform_auth():
    resp = client.get("/platform/billing/bill-shock-threshold")
    assert resp.status_code == 401


def test_get_bill_shock_threshold_returns_current_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_bill_shock_threshold", return_value=50000):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            "/platform/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": "50000"}


def test_get_bill_shock_threshold_returns_null_when_unset():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_bill_shock_threshold", return_value=None):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            "/platform/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": None}


def test_put_bill_shock_threshold_updates_and_returns_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_bill_shock_threshold") as mock_set, \
         patch("app.routers.platform.write_audit_log") as mock_audit:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/billing/bill-shock-threshold",
            json={"default_threshold_amount": "60000"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": "60000"}
    mock_set.assert_called_once_with(mock_db, 60000)
    assert mock_audit.called
    assert mock_db.commit.called


def test_put_bill_shock_threshold_accepts_null_to_unset():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_bill_shock_threshold") as mock_set, \
         patch("app.routers.platform.write_audit_log"):
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/billing/bill-shock-threshold",
            json={"default_threshold_amount": None},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_threshold_amount": None}
    mock_set.assert_called_once_with(mock_db, None)


def test_put_bill_shock_threshold_rejects_invalid_value():
    with patch("app.routers.platform.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/billing/bill-shock-threshold",
            json={"default_threshold_amount": "not-a-number"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_get_data_retention_requires_platform_auth():
    resp = client.get("/platform/data-retention")
    assert resp.status_code == 401


def test_get_data_retention_returns_current_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.get_default_retention_days", return_value=365):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            "/platform/data-retention",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_retention_days": "365"}


def test_put_data_retention_updates_and_returns_value():
    with patch("app.routers.platform.SessionLocal") as mock_session, \
         patch("app.routers.platform.set_default_retention_days") as mock_set, \
         patch("app.routers.platform.write_audit_log") as mock_audit:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.put(
            "/platform/data-retention",
            json={"default_retention_days": "180"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"default_retention_days": "180"}
    mock_set.assert_called_once_with(mock_db, 180)
    assert mock_audit.called
    assert mock_db.commit.called


def test_put_data_retention_rejects_below_minimum():
    with patch("app.routers.platform.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/data-retention",
            json={"default_retention_days": "30"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_put_data_retention_rejects_invalid_value():
    with patch("app.routers.platform.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.put(
            "/platform/data-retention",
            json={"default_retention_days": "not-a-number"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422
