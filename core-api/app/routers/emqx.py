import re
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/emqx")

_CN_PATTERN = re.compile(r"^([^:]+):([^:]+)$")

class AuthRequest(BaseModel):
    username: str = ""
    clientid: str = ""
    peerhost: str = ""
    cert_common_name: str = ""

class AclRequest(BaseModel):
    username: str = ""
    clientid: str = ""
    topic: str = ""
    action: str = ""
    peerhost: str = ""

def _parse_cn(cn: str) -> tuple[str, str] | None:
    m = _CN_PATTERN.match(cn)
    if not m:
        return None
    return m.group(1), m.group(2)

@router.post("/auth")
def emqx_auth(req: AuthRequest):
    cn = req.cert_common_name or req.username
    if not _parse_cn(cn):
        return {"result": "deny"}
    return {"result": "allow"}

@router.post("/acl")
def emqx_acl(req: AclRequest):
    cn = req.username
    parsed = _parse_cn(cn)
    if not parsed:
        return {"result": "deny"}

    tenant_id, device_id = parsed
    topic = req.topic
    action = req.action

    allowed_patterns = [
        (re.compile(rf"^/{re.escape(tenant_id)}/devices/{re.escape(device_id)}/telemetry$"), "publish"),
        (re.compile(rf"^/{re.escape(tenant_id)}/devices/{re.escape(device_id)}/status$"), "publish"),
        (re.compile(rf"^/{re.escape(tenant_id)}/devices/{re.escape(device_id)}/commands$"), "subscribe"),
    ]

    for pattern, allowed_action in allowed_patterns:
        if pattern.match(topic) and action == allowed_action:
            return {"result": "allow"}

    return {"result": "deny"}
