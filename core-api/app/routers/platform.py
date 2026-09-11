from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.models.public import MfaSettings
from app.services.audit import write_audit_log
from app.services.auth import verify_token
from app.database import SessionLocal
from app.services.billing import (
    InvalidUnitPriceError,
    get_default_unit_prices,
    get_tax_rate,
    set_default_unit_prices,
    set_tax_rate,
    validate_tax_rate,
    validate_unit_price,
    ITEM_KEYS,
)

router = APIRouter(prefix="/platform", tags=["platform"])
_bearer = HTTPBearer(auto_error=False)


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


class MfaSettingsUpdate(BaseModel):
    platform_required: bool | None = None
    tenant_required: bool | None = None


class DefaultPriceItem(BaseModel):
    item_key: str
    unit_price: str


class TaxRateItem(BaseModel):
    tax_rate: str


@router.get("/mfa-settings")
def get_mfa_settings(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        s = db.query(MfaSettings).filter(MfaSettings.id == 1).first()
        if not s:
            return {"platform_required": False, "tenant_required": False}
        return {"platform_required": s.platform_required, "tenant_required": s.tenant_required}


@router.patch("/mfa-settings")
def update_mfa_settings(body: MfaSettingsUpdate, payload: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        s = db.query(MfaSettings).filter(MfaSettings.id == 1).first()
        if not s:
            raise HTTPException(status_code=500, detail="MFA settings not initialized")
        if body.platform_required is not None:
            s.platform_required = body.platform_required
        if body.tenant_required is not None:
            s.tenant_required = body.tenant_required
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "mfa_settings_updated",
                        resource_type="mfa_settings",
                        detail={"platform_required": s.platform_required,
                                "tenant_required": s.tenant_required})
        db.commit()
        db.refresh(s)
        return {"platform_required": s.platform_required, "tenant_required": s.tenant_required}


@router.get("/billing/default-prices", response_model=list[DefaultPriceItem])
def get_billing_default_prices(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        prices = get_default_unit_prices(db)
    return [{"item_key": k, "unit_price": str(v)} for k, v in prices.items()]


@router.put("/billing/default-prices", response_model=list[DefaultPriceItem])
def update_billing_default_prices(body: list[DefaultPriceItem], payload: dict = Depends(_require_platform)):
    validated: dict[str, Decimal] = {}
    for item in body:
        if item.item_key not in ITEM_KEYS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                 detail=f"Unknown item_key: {item.item_key}")
        try:
            validated[item.item_key] = validate_unit_price(item.unit_price)
        except InvalidUnitPriceError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        set_default_unit_prices(db, validated)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "update_billing_default_prices",
                        resource_type="billing_default_unit_prices",
                        detail={k: str(v) for k, v in validated.items()})
        db.commit()

    return [{"item_key": k, "unit_price": str(v)} for k, v in validated.items()]


@router.get("/billing/tax-rate", response_model=TaxRateItem)
def get_billing_tax_rate(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        rate = get_tax_rate(db)
    return {"tax_rate": str(rate)}


@router.put("/billing/tax-rate", response_model=TaxRateItem)
def update_billing_tax_rate(body: TaxRateItem, payload: dict = Depends(_require_platform)):
    try:
        rate = validate_tax_rate(body.tax_rate)
    except InvalidUnitPriceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    with SessionLocal() as db:
        set_tax_rate(db, rate)
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "update_billing_tax_rate",
                        resource_type="billing_settings",
                        detail={"tax_rate": str(rate)})
        db.commit()

    return {"tax_rate": str(rate)}
