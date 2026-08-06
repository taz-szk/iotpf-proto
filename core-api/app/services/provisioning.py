from app.services.step_ca import issue_device_cert

def issue_device_cert_for_tenant(tenant_id: str, device_id: str) -> tuple[str, str]:
    cn = f"{tenant_id}:{device_id}"
    return issue_device_cert(cn)
