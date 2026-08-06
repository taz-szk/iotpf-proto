import httpx
from app.config import settings
from app.database import create_tenant_schema
from app.services.grafana import provision_tenant_grafana

def create_influxdb_org(name: str, admin_token: str) -> dict:
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/orgs",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={"name": name},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()

def create_influxdb_token_for_org(org_id: str, admin_token: str) -> str:
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/authorizations",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={
            "orgID": org_id,
            "description": f"org-{org_id}-token",
            "permissions": [
                {"action": "read", "resource": {"type": "buckets", "orgID": org_id}},
                {"action": "write", "resource": {"type": "buckets", "orgID": org_id}},
            ],
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["token"]

def setup_tenant(tenant_id: str, tenant_name: str) -> tuple[str, str, int]:
    """
    Returns: (influxdb_org_id, influxdb_token, grafana_org_id)
    """
    org = create_influxdb_org(tenant_name, settings.influxdb_admin_token)
    org_id = org["id"]
    token = create_influxdb_token_for_org(org_id, settings.influxdb_admin_token)
    create_tenant_schema(tenant_id)
    grafana_org_id = provision_tenant_grafana(tenant_name, org_id, token)
    return org_id, token, grafana_org_id
