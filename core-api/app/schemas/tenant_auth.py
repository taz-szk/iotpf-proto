from pydantic import BaseModel, EmailStr


class TenantLoginRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    password: str


class TenantLoginResponse(BaseModel):
    user_id: str
    email: str
    role: str
    tenant_id: str
    redirect_url: str
