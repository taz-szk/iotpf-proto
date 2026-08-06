from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional

class TenantCreate(BaseModel):
    name: str
    slug: str

class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    grafana_org_id: Optional[str] = None

    class Config:
        from_attributes = True
