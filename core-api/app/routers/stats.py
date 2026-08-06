import re
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
import httpx

from app.database import SessionLocal
from app.models.public import Tenant
from app.services.auth import verify_token
from app.config import settings

router = APIRouter(prefix="/tenants")
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _validate_uuid(value: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID")
    return value.lower()


def _parse_influx_csv_scalar(csv_text: str) -> int:
    for line in csv_text.strip().splitlines():
        if line.startswith("#") or not line.strip() or ",result," in line or line.startswith(",result"):
            continue
        parts = line.split(",")
        for part in reversed(parts):
            part = part.strip()
            if part.lstrip("-").isdigit():
                return int(part)
    return 0


def _count_influxdb_points(influxdb_org_id: str) -> int:
    query = (
        'from(bucket: "telemetry")\n'
        '  |> range(start: -30d)\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> group()\n'
        '  |> count()\n'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "type": "flux", "orgID": influxdb_org_id},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return 0
        return _parse_influx_csv_scalar(resp.text)
    except Exception:
        return 0


@router.get("/{tenant_id}/stats")
def get_tenant_stats(tenant_id: str, _: dict = Depends(_require_platform)):
    _validate_uuid(tenant_id)
    schema = f"tenant_{tenant_id.lower().replace('-', '_')}"

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        total_devices = db.execute(
            text(f'SELECT COUNT(*) FROM "{schema}".devices')
        ).scalar() or 0

        online_devices = db.execute(
            text(f"SELECT COUNT(*) FROM \"{schema}\".devices WHERE connection_status = 'online'")
        ).scalar() or 0

        alert_events_30d = db.execute(
            text(f"SELECT COUNT(*) FROM \"{schema}\".alert_events WHERE triggered_at >= NOW() - INTERVAL '30 days'")
        ).scalar() or 0

        try:
            firmware_releases = db.execute(
                text(f'SELECT COUNT(*) FROM "{schema}".firmware_releases WHERE is_active = TRUE')
            ).scalar() or 0
        except Exception:
            firmware_releases = 0

    data_points_30d = _count_influxdb_points(tenant.influxdb_org_id)

    return {
        "tenant_id": tenant_id,
        "total_devices": total_devices,
        "online_devices": online_devices,
        "data_points_30d": data_points_30d,
        "alert_events_30d": alert_events_30d,
        "firmware_releases": firmware_releases,
    }
