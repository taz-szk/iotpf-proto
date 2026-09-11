from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.billing import InvalidEffectiveDateError
from app.models.billing import BillingInvoice

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token():
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": "admin",
    })


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_list_current_prices_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/prices")
    assert resp.status_code == 401


def test_list_current_prices_rejects_tenant_token():
    resp = client.get(
        f"/tenants/{TENANT_ID}/billing/prices",
        headers={"Authorization": f"Bearer {_tenant_token()}"},
    )
    assert resp.status_code == 401


def test_list_current_prices_returns_effective_prices():
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.get_effective_unit_prices",
               return_value={"base_fee": Decimal("5000"), "data_points": Decimal("0.01")}):
        mock_session.return_value = _session_ctx()
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/prices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_key = {item["item_key"]: item["unit_price"] for item in body}
    assert by_key == {"base_fee": "5000", "data_points": "0.01"}


def test_create_price_success():
    created = MagicMock()
    created.item_key = "base_fee"
    created.unit_price = Decimal("6000")
    created.effective_from = date(2099, 1, 1)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.set_unit_price", return_value=created) as mock_set:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body == {"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"}
    assert mock_set.call_args[0][3] == Decimal("6000")


def test_create_price_rejects_invalid_effective_date():
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.set_unit_price",
               side_effect=InvalidEffectiveDateError("must be a future month")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2020-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_rejects_malformed_unit_price_string():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "not-a-number", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_rejects_nan_unit_price():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "NaN", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_rejects_infinity_unit_price():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "Infinity", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_rejects_unit_price_exceeding_maximum():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "100000000", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_create_price_rejects_unit_price_with_too_many_decimal_places():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "0.00005", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_list_current_prices_returns_404_for_nonexistent_tenant():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/prices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_create_price_returns_404_for_nonexistent_tenant():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/prices",
            json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_create_price_requires_platform_auth():
    resp = client.post(
        f"/tenants/{TENANT_ID}/billing/prices",
        json={"item_key": "base_fee", "unit_price": "6000", "effective_from": "2099-01-01"},
    )
    assert resp.status_code == 401


def _invoice_row(target_year_month, status_, subtotal, tax_amount, total_amount):
    row = MagicMock(spec=BillingInvoice)
    row.target_year_month = target_year_month
    row.status = status_
    row.subtotal = subtotal
    row.tax_amount = tax_amount
    row.total_amount = total_amount
    return row


def test_list_tenant_invoices_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/invoices")
    assert resp.status_code == 401


def test_list_tenant_invoices_returns_invoices_newest_first():
    rows = [
        _invoice_row("2026-09", "draft", 5000, 500, 5500),
        _invoice_row("2026-08", "finalized", 4000, 400, 4400),
    ]
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()  # tenant exists
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["target_year_month"] == "2026-09"
    assert body[0]["total_amount"] == 5500
    filter_calls = mock_db.query.return_value.filter.call_args_list
    # str(call.args) renders SQLAlchemy binary expressions without bound values,
    # so also check each bound parameter's actual value to pin the real predicate.
    assert any(
        TENANT_ID in str(call.args)
        or any(getattr(getattr(arg, "right", None), "value", None) == TENANT_ID for arg in call.args)
        for call in filter_calls
    )


def test_get_tenant_invoice_detail_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/invoices/2026-09")
    assert resp.status_code == 401


def test_get_tenant_invoice_detail_includes_line_items():
    line_item = MagicMock()
    line_item.item_key = "base_fee"
    line_item.quantity = 1
    line_item.unit_price = Decimal("5000")
    line_item.amount = 5000
    invoice = _invoice_row("2026-09", "draft", 5000, 500, 5500)
    invoice.id = "invoice-1"

    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()  # tenant exists
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [invoice]
        mock_db.query.return_value.filter.return_value.all.return_value = [line_item]
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-09",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_year_month"] == "2026-09"
    assert body["total_amount"] == 5500
    assert body["line_items"] == [{"item_key": "base_fee", "quantity": 1, "unit_price": "5000", "amount": 5000}]


def test_get_tenant_invoice_detail_tenant_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-09",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_get_tenant_invoice_detail_invoice_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()  # tenant exists
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-01",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_correct_tenant_invoice_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/billing/invoices/2026-09/correct")
    assert resp.status_code == 401


def test_correct_tenant_invoice_tenant_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-09/correct",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_correct_tenant_invoice_returns_409_when_not_finalized():
    from app.services.billing_batch import InvoiceNotFinalizedError
    tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org-1", influxdb_token="tok")
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.correct_invoice", side_effect=InvoiceNotFinalizedError("no finalized invoice")):
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-09/correct",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 409


def test_correct_tenant_invoice_returns_no_change_when_nothing_to_correct():
    tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org-1", influxdb_token="tok")
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.correct_invoice", return_value=None):
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-09/correct",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"corrected": False}


def test_correct_tenant_invoice_returns_delta_when_corrected():
    tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org-1", influxdb_token="tok")
    corrected = MagicMock(subtotal=20, tax_amount=2, total_amount=22)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.correct_invoice", return_value=corrected) as mock_correct:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.post(
            f"/tenants/{TENANT_ID}/billing/invoices/2026-09/correct",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {
        "corrected": True, "delta_subtotal": 20, "delta_tax_amount": 2, "delta_total_amount": 22,
    }
    mock_correct.assert_called_once_with(mock_db, TENANT_ID, f"tenant_{TENANT_ID.replace('-', '_')}", "org-1", "tok", "2026-09")


def test_list_tenant_invoices_tenant_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/invoices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_get_tenant_bill_shock_threshold_requires_platform_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/billing/bill-shock-threshold")
    assert resp.status_code == 401


def test_get_tenant_bill_shock_threshold_returns_override_when_set():
    tenant = MagicMock(bill_shock_threshold_amount=70000)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.get_effective_bill_shock_threshold", return_value=70000):
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": "70000", "is_default": False}


def test_get_tenant_bill_shock_threshold_returns_default_when_no_override():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    with patch("app.routers.billing.SessionLocal") as mock_session, \
         patch("app.routers.billing.get_effective_bill_shock_threshold", return_value=50000):
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": "50000", "is_default": True}


def test_get_tenant_bill_shock_threshold_tenant_not_found():
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = mock_db
        resp = client.get(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_put_tenant_bill_shock_threshold_sets_override():
    tenant = MagicMock(bill_shock_threshold_amount=None)
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            json={"threshold_amount": "80000"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": "80000"}
    assert tenant.bill_shock_threshold_amount == 80000
    assert mock_db.commit.called


def test_put_tenant_bill_shock_threshold_clears_override_with_null():
    tenant = MagicMock(bill_shock_threshold_amount=80000)
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            json={"threshold_amount": None},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"threshold_amount": None}
    assert tenant.bill_shock_threshold_amount is None


def test_put_tenant_bill_shock_threshold_rejects_invalid_value():
    tenant = MagicMock()
    with patch("app.routers.billing.SessionLocal") as mock_session:
        mock_db = _session_ctx()
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        mock_session.return_value = mock_db
        resp = client.put(
            f"/tenants/{TENANT_ID}/billing/bill-shock-threshold",
            json={"threshold_amount": "-5"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422
