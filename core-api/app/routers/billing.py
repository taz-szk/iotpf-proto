import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database import SessionLocal
from app.models.public import Tenant
from app.schemas.billing import BillShockThresholdOut, BillShockThresholdSet, InvoiceOut, UnitPriceOut, UnitPriceSet
from app.services.auth import verify_token
from app.services.audit import log_audit
from app.services.billing import (
    InvalidEffectiveDateError,
    InvalidUnitPriceError,
    get_effective_bill_shock_threshold,
    get_effective_unit_prices,
    get_invoice_detail_aggregated,
    list_invoices_aggregated,
    set_unit_price,
    validate_bill_shock_threshold,
    validate_unit_price,
)
from app.services.billing_batch import InvoiceNotFinalizedError, correct_invoice

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
        return list_invoices_aggregated(db, tenant_id)


@router.get("/invoices/{target_year_month}")
def get_tenant_invoice(tenant_id: str, target_year_month: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        detail = get_invoice_detail_aggregated(db, tenant_id, target_year_month)
        if not detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        return detail


@router.post("/invoices/{target_year_month}/correct")
def correct_tenant_invoice(tenant_id: str, target_year_month: str, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        schema = f"tenant_{str(tenant.id).lower().replace('-', '_')}"
        influxdb_org_id = tenant.influxdb_org_id or ""
        influxdb_token = tenant.influxdb_token or ""
        try:
            invoice = correct_invoice(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)
        except InvoiceNotFinalizedError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        if invoice is None:
            result = {"corrected": False}
        else:
            result = {
                "corrected": True,
                "delta_subtotal": invoice.subtotal,
                "delta_tax_amount": invoice.tax_amount,
                "delta_total_amount": invoice.total_amount,
            }

    log_audit("platform", payload["sub"], payload["email"], "correct_billing_invoice",
              tenant_id=tenant_id, resource_type="billing_invoice",
              detail={"target_year_month": target_year_month, **result})
    return result


@router.get("/bill-shock-threshold", response_model=BillShockThresholdOut)
def get_tenant_bill_shock_threshold(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        effective = get_effective_bill_shock_threshold(db, tenant)
        is_default = tenant.bill_shock_threshold_amount is None
    return {
        "threshold_amount": str(effective) if effective is not None else None,
        "is_default": is_default,
    }


@router.put("/bill-shock-threshold", response_model=BillShockThresholdSet)
def update_tenant_bill_shock_threshold(tenant_id: str, body: BillShockThresholdSet, payload: dict = Depends(_require_platform)):
    tenant_id = _validate_uuid(tenant_id)
    try:
        amount = validate_bill_shock_threshold(body.threshold_amount or "")
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant.bill_shock_threshold_amount = amount
        db.commit()

    log_audit("platform", payload["sub"], payload["email"], "update_tenant_bill_shock_threshold",
              tenant_id=tenant_id, resource_type="tenant",
              detail={"threshold_amount": amount})
    return {"threshold_amount": str(amount) if amount is not None else None}
