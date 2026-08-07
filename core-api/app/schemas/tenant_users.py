from pydantic import BaseModel, EmailStr

class TenantUserCreate(BaseModel):
    email: EmailStr
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
