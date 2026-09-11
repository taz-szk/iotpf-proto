from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _tenant_token(role: str = "viewer"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _invoice(target_year_month, status_, subtotal=5000, tax_amount=500, total_amount=5500):
    inv = MagicMock()
    inv.target_year_month = target_year_month
    inv.status = status_
    inv.subtotal = subtotal
    inv.tax_amount = tax_amount
    inv.total_amount = total_amount
    inv.id = "invoice-1"
    return inv


def test_list_my_invoices_requires_auth():
    resp = client.get("/tenant-portal/me/billing/invoices")
    assert resp.status_code == 401


def test_list_my_invoices_allows_viewer():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _invoice("2026-09", "draft"),
        ]
        resp = client.get(
            "/tenant-portal/me/billing/invoices",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{
        "target_year_month": "2026-09", "status": "draft",
        "subtotal": 5000, "tax_amount": 500, "total_amount": 5500,
        "correction_count": 0,
    }]
    filter_args = mock_db.query.return_value.filter.call_args[0]
    # str(arg) renders SQLAlchemy binary expressions without bound values, so
    # also check the bound parameter's actual value to pin the real predicate.
    assert any(
        TENANT_ID in str(arg) or getattr(getattr(arg, "right", None), "value", None) == TENANT_ID
        for arg in filter_args
    )


def test_get_my_invoice_detail_includes_line_items():
    from decimal import Decimal
    line_item = MagicMock()
    line_item.item_key = "base_fee"
    line_item.quantity = 1
    line_item.unit_price = Decimal("5000")
    line_item.amount = 5000

    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _invoice("2026-09", "draft"),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = [line_item]
        resp = client.get(
            "/tenant-portal/me/billing/invoices/2026-09",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_year_month"] == "2026-09"
    assert body["line_items"] == [{"item_key": "base_fee", "quantity": 1, "unit_price": "5000", "amount": 5000}]
    invoice_filter_args = mock_db.query.return_value.filter.call_args_list[0][0]
    assert any(
        TENANT_ID in str(arg) or getattr(getattr(arg, "right", None), "value", None) == TENANT_ID
        for arg in invoice_filter_args
    )


def test_get_my_invoice_detail_not_found():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        resp = client.get(
            "/tenant-portal/me/billing/invoices/2026-01",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 404
