from pydantic import BaseModel, EmailStr


class TenantLoginRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    password: str


class TenantLoginResponse(BaseModel):
    status: str = "ok"              # "ok" | "totp_required" | "totp_setup_required"
    user_id: str | None = None
    email: str | None = None
    role: str | None = None
    tenant_id: str | None = None
    redirect_url: str | None = None
    partial_token: str | None = None
