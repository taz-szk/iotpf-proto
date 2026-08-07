from pydantic import BaseModel


class TenantLoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


class TenantLoginResponse(BaseModel):
    user_id: str
    email: str
    role: str
    tenant_id: str
    redirect_url: str
