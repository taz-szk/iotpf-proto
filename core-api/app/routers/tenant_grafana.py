from uuid import UUID
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from app.models.public import Tenant
from app.database import SessionLocal
from app.services.auth import verify_token
from app.services.grafana import add_user_to_grafana_org, get_org_home_dashboard_url

router = APIRouter(prefix="/tenants/{tenant_id}/grafana", tags=["tenant-grafana"])


@router.get("")
def grafana_redirect(tenant_id: UUID, request: Request):
    """プラットフォーム管理者を テナント Grafana org に追加してダッシュボードへリダイレクト。
    iot_token cookie で認証（nginx /grafana/ proxy と同じ仕組み）。"""
    token = request.cookies.get("iot_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token")
    payload = verify_token(token)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    tenant_id_str = str(tenant_id)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.id == tenant_id_str, Tenant.status == "active"
        ).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if not tenant.grafana_org_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Grafana not provisioned")

    org_id = int(tenant.grafana_org_id)
    email = payload["email"]

    try:
        # プラットフォーム管理者をテナント org に追加（既存なら 409 → 無視）
        add_user_to_grafana_org(org_id, email, "Admin")
        # ホームダッシュボード URL を取得
        dashboard_url = get_org_home_dashboard_url(org_id)
    except Exception:
        dashboard_url = None

    if dashboard_url:
        redirect_url = f"{dashboard_url}?orgId={org_id}"
    else:
        redirect_url = f"/grafana/?orgId={org_id}"

    return RedirectResponse(url=redirect_url, status_code=302)
