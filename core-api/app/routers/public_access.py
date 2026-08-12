"""パブリックダッシュボードアクセス — ログイン不要で Grafana を閲覧できる公開URL。"""
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from app.database import SessionLocal
from app.models.public import Tenant
from app.services.auth import create_access_token
from app.services.grafana import ensure_grafana_user_in_org

router = APIRouter(prefix="/public", tags=["public"])

_PUBLIC_VIEWER_EMAIL = "viewer-public"
_JWT_TTL_HOURS = 24


@router.get("/{token}")
def public_dashboard(token: str):
    """公開トークンで認証し、Grafana ダッシュボードへリダイレクトする。"""
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.public_token == token,
            Tenant.status == "active",
        ).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public URL not found")
        tenant_id = str(tenant.id)
        grafana_org_id = tenant.grafana_org_id

    if grafana_org_id:
        try:
            ensure_grafana_user_in_org(
                int(grafana_org_id),
                _PUBLIC_VIEWER_EMAIL,
                "Viewer",
                tenant_id,
            )
        except Exception as e:
            print(f"[public_access] Grafana viewer setup failed: {e}")

    jwt = create_access_token(
        {
            "sub": f"public:{tenant_id}",
            "email": _PUBLIC_VIEWER_EMAIL,
            "type": "tenant",
            "role": "viewer",
            "tenant_id": tenant_id,
        },
        expires_delta=timedelta(hours=_JWT_TTL_HOURS),
    )

    redirect_url = f"/grafana/?orgId={grafana_org_id}&kiosk&theme=light" if grafana_org_id else "/grafana/"
    redir = RedirectResponse(url=redirect_url, status_code=302)
    redir.set_cookie(
        "iot_token",
        jwt,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_JWT_TTL_HOURS * 3600,
        path="/",
    )
    return redir
