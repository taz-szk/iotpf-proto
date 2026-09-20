from app.database import SessionLocal
from app.models.public import DashboardPanelConfig


def get_default_panel_configs(tenant_id: str) -> list[dict]:
    """センサー別パネル設定のうち、Grafanaダッシュボードに反映されるテナント全体のデフォルト
    (group_id未指定)分を返す。グループ別の設定はダッシュボード同期の対象外。"""
    with SessionLocal() as db:
        rows = db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id,
            DashboardPanelConfig.group_id.is_(None),
        ).all()
        return [{"sensor_key": r.sensor_key, "panel_type": r.panel_type} for r in rows]
