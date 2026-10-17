import uuid
from datetime import datetime, timedelta, timezone

import secrets
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models.public import Tenant, TenantSignupRequest
from app.services.audit import write_audit_log
from app.services.auth import hash_password
from app.services.mailer import send_signup_verification_email
from app.services.password_policy import validate_password
from app.services.rate_limiter import is_rate_limited, record_failure
from app.services.recaptcha import verify_recaptcha
from app.services.slug import slugify
from app.services.tenant import setup_tenant
from app.services.tenant_users import create_tenant_user_record

router = APIRouter()

_REQUEST_TTL = timedelta(hours=24)
_TRIAL_PERIOD = timedelta(days=90)


class SignupRequest(BaseModel):
    tenant_name: str
    email: EmailStr
    password: str
    recaptcha_token: str


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    rate_key = f"signup:{ip}"
    if is_rate_limited(rate_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    if not verify_recaptcha(body.recaptcha_token, ip):
        record_failure(rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reCAPTCHA verification failed")

    validate_password(body.password, body.email)

    with SessionLocal() as db:
        existing_pending = db.query(TenantSignupRequest).filter(
            TenantSignupRequest.email == body.email
        ).first()
        if existing_pending:
            db.delete(existing_pending)
            db.flush()

        def _slug_taken(candidate: str) -> bool:
            return (
                db.query(Tenant).filter(Tenant.slug == candidate).first() is not None
                or db.query(TenantSignupRequest).filter(TenantSignupRequest.slug == candidate).first() is not None
            )

        slug = slugify(body.tenant_name, _slug_taken)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        signup_req = TenantSignupRequest(
            id=uuid.uuid4(), email=body.email, tenant_name=body.tenant_name, slug=slug,
            password_hash=hash_password(body.password), verification_token=token,
            expires_at=now + _REQUEST_TTL,
        )
        db.add(signup_req)
        db.flush()

        verification_url = f"https://{settings.platform_domain}/signup/verify?token={token}"
        try:
            send_signup_verification_email(body.email, body.tenant_name, verification_url)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Failed to send verification email")

        db.commit()
    return {"status": "verification_email_sent"}


@router.get("/signup/verify")
def verify_signup(token: str):
    with SessionLocal() as db:
        signup_req = db.query(TenantSignupRequest).filter(
            TenantSignupRequest.verification_token == token
        ).first()
        if not signup_req:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or already used token")

        expires_at = signup_req.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            db.delete(signup_req)
            db.commit()
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Verification link expired")

        tenant_id = uuid.uuid4()
        tenant = Tenant(
            id=tenant_id, name=signup_req.tenant_name, slug=signup_req.slug,
            signup_source="self_signup",
            trial_expires_at=datetime.now(timezone.utc) + _TRIAL_PERIOD,
        )
        db.add(tenant)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Slug already taken, please try signing up again")

        tenant_id_str = str(tenant_id)
        org_id, influx_token, grafana_org_id = setup_tenant(tenant_id_str, signup_req.tenant_name)
        tenant.influxdb_org_id = org_id
        tenant.influxdb_token = influx_token
        tenant.grafana_org_id = str(grafana_org_id)

        schema = f"tenant_{tenant_id_str.replace('-', '_')}"
        create_tenant_user_record(
            tenant_id=tenant_id_str, schema=schema, email=signup_req.email,
            password_hash=signup_req.password_hash, role="admin", grafana_org_id=str(grafana_org_id),
        )

        write_audit_log(db, "self_signup", tenant_id_str, signup_req.email,
                        "signup_tenant", tenant_id=tenant_id_str, resource_type="tenant",
                        resource_id=signup_req.tenant_name)

        db.delete(signup_req)
        db.commit()

    return {"status": "tenant_created", "tenant_id": tenant_id_str}
