import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database import SessionLocal
from app.models.public import Tenant
from app.models.billing import BillingInvoice, BillingLineItem
from app.schemas.billing import InvoiceOut, UnitPriceOut, UnitPriceSet
from app.services.auth import verify_token
from app.services.audit import log_audit
from app.services.billing import (
    ITEM_KEYS,
    InvalidEffectiveDateError,
    InvalidUnitPriceError,
    get_effective_unit_prices,
    set_unit_price,
    validate_unit_price,
)

router = APIRouter(prefix="/tenants/{tenant_id}/billing", tags=["billing"])
_bearer = HTTPBearer(auto_error=False)


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _validate_uuid(value: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid tenant_id")
    return value.lower()


@router.get("/prices", response_model=list[UnitPriceOut])
def list_current_prices(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        prices = get_effective_unit_prices(db, tenant_id, now.year, now.month)
    month_start = date(now.year, now.month, 1)
    return [
        {"item_key": item_key, "unit_price": str(unit_price), "effective_from": month_start}
        for item_key, unit_price in prices.items()
    ]


@router.post("/prices", response_model=UnitPriceOut, status_code=status.HTTP_201_CREATED)
def create_price(tenant_id: str, body: UnitPriceSet, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    try:
        unit_price = validate_unit_price(body.unit_price)
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        try:
            row = set_unit_price(db, tenant_id, body.item_key, unit_price, body.effective_from)
        except InvalidEffectiveDateError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        result = {
            "item_key": row.item_key,
            "unit_price": str(row.unit_price),
            "effective_from": row.effective_from,
        }

    log_audit("platform", payload["sub"], payload["email"], "set_billing_unit_price",
              tenant_id=tenant_id, resource_type="billing_unit_price",
              detail={"item_key": body.item_key, "unit_price": body.unit_price,
                      "effective_from": body.effective_from.isoformat()})
    return result


@router.get("/invoices", response_model=list[InvoiceOut])
def list_tenant_invoices(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        rows = (
            db.query(BillingInvoice)
            .filter(BillingInvoice.tenant_id == tenant_id)
            .order_by(BillingInvoice.target_year_month.desc())
            .all()
        )
        return [
            InvoiceOut(
                target_year_month=r.target_year_month, status=r.status,
                subtotal=r.subtotal, tax_amount=r.tax_amount, total_amount=r.total_amount,
            )
            for r in rows
        ]


@router.get("/invoices/{target_year_month}")
def get_tenant_invoice(tenant_id: str, target_year_month: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        invoice = db.query(BillingInvoice).filter(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.target_year_month == target_year_month,
        ).first()
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        line_items = db.query(BillingLineItem).filter(BillingLineItem.invoice_id == invoice.id).all()
        line_items.sort(key=lambda li: ITEM_KEYS.index(li.item_key) if li.item_key in ITEM_KEYS else len(ITEM_KEYS))
        return {
            "target_year_month": invoice.target_year_month,
            "status": invoice.status,
            "subtotal": invoice.subtotal,
            "tax_amount": invoice.tax_amount,
            "total_amount": invoice.total_amount,
            "line_items": [
                {
                    "item_key": li.item_key, "quantity": li.quantity,
                    "unit_price": str(li.unit_price), "amount": li.amount,
                }
                for li in line_items
            ],
        }
