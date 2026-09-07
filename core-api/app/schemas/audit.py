from datetime import datetime
from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    id:            str
    actor_type:    str
    actor_email:   str
    tenant_id:     str | None
    action:        str
    resource_type: str | None
    resource_id:   str | None
    detail:        dict | None
    ip_address:    str | None
    result:        str
    created_at:    datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListOut(BaseModel):
    total: int
    items: list[AuditLogOut]
