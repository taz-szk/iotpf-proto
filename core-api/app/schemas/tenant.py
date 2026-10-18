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
    signup_source: str = "platform_created"
    trial_expires_at: Optional[datetime] = None
    trial_extension_requested_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class TenantDetailOut(TenantOut):
    soracom_configured: bool = False
