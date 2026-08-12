from pydantic import BaseModel

class ProvisionRequest(BaseModel):
    bootstrap_token: str
    device_id: str
    device_name: str = ""

class ProvisionOut(BaseModel):
    tenant_id: str
    device_id: str
    certificate: str
    private_key: str
    ca_certificate: str = ""
