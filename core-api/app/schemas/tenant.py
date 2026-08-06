from pydantic import BaseModel
from uuid import UUID
from datetime import datetime

class TenantCreate(BaseModel):
    name: str
    slug: str

class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
