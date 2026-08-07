from pydantic import BaseModel

class TenantUserCreate(BaseModel):
    email: str
    password: str
    role: str = "viewer"  # "admin" | "operator" | "viewer"

class TenantUserOut(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True
