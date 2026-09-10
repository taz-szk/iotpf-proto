from typing import Optional
from pydantic import BaseModel

class ProvisionRequest(BaseModel):
    bootstrap_token: str
    device_id: str
    device_name: str = ""
    group_id: Optional[str] = None

class ProvisionOut(BaseModel):
    tenant_id: str
    device_id: str
    certificate: str
    private_key: str
    ca_certificate: str = ""

class ProvisionGroupsRequest(BaseModel):
    bootstrap_token: str
