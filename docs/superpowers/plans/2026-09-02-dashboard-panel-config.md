# ダッシュボードパネル設定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナントポータルからセンサーキーごとにGrafanaチャートタイプを選択・変更できるようにする

**Architecture:** `dashboard_panel_configs` テーブルに設定を保存し、PUT API が Grafana API を呼んでダッシュボードパネルを即時再生成する。テナントポータルに「ダッシュボード設定」タブを追加してUIから操作できるようにする。

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL (raw SQL migration), Grafana HTTP API, Alpine.js, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-09-02-dashboard-panel-config-design.md`

## Global Constraints

- Python 3.11+、既存の `core-api/app/` パターンに従う（SQLAlchemy ORM、FastAPI Depends）
- マイグレーションは Alembic **不使用** — `database.py` にインライン関数を追加して `main.py` の `on_startup` で呼ぶ（既存パターン踏襲）
- チャートタイプ allowlist 9種のみ: `timeseries` / `barchart` / `histogram` / `heatmap` / `state-timeline` / `gauge` / `stat` / `bargauge` / `table`
- `sensor_key` は正規表現 `^[a-zA-Z0-9_-]{1,64}$` のみ許可（Fluxインジェクション対策）
- 変更権限は tenant ロール `operator` / `admin` のみ。`viewer` は GET のみ
- Grafanaダッシュボード再生成は PUT 時に同期実行（非同期キューなし）
- 設定なし（空配列）の場合は現行フォールバック（全フィールド timeseries 1枚）を維持
- 既存の stat パネル（登録状態 id=2・接続状態 id=4）とrow パネル（id=1）は変更しない
- テストは `unittest.mock.patch` でDBと Grafana API をモック（実DBなし）

---

## ファイル構成

| ファイル | 操作 | 内容 |
|---|---|---|
| `core-api/app/models/public.py` | 修正 | `DashboardPanelConfig` モデル追加 |
| `core-api/app/database.py` | 修正 | `migrate_dashboard_panel_configs()` 追加 |
| `core-api/app/main.py` | 修正 | `on_startup` に migration 追加 |
| `core-api/app/services/grafana.py` | 修正 | パネルビルダー関数群追加 |
| `core-api/app/routers/tenant_portal.py` | 修正 | GET/PUT エンドポイント追加 |
| `core-api/tests/test_dashboard_panel_config.py` | 新規 | API テスト |
| `core-api/tests/test_grafana.py` | 修正 | パネルビルダーのユニットテスト追加 |
| `admin-ui/js/api.js` | 修正 | `dashboardConfig` メソッド追加 |
| `admin-ui/tenant-portal.html` | 修正 | ダッシュボード設定タブ追加 |

---

### Task 1: DBモデル + マイグレーション

**Files:**
- Modify: `core-api/app/models/public.py`
- Modify: `core-api/app/database.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_dashboard_panel_config.py`（Task 3 で作成、ここでは import 確認のみ）

**Interfaces:**
- Produces: `DashboardPanelConfig` クラス（tenant_id, sensor_key, panel_type カラム）
- Produces: `migrate_dashboard_panel_configs()` 関数

- [ ] **Step 1: `DashboardPanelConfig` モデルを `public.py` に追記する**

`core-api/app/models/public.py` の末尾に追加:

```python
from sqlalchemy import UniqueConstraint

class DashboardPanelConfig(Base):
    __tablename__ = "dashboard_panel_configs"
    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id  = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    sensor_key = Column(String(64), nullable=False)
    panel_type = Column(String(20), nullable=False, default="timeseries")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "sensor_key"),)
```

注意: `UniqueConstraint` は `sqlalchemy` から import 済みか確認。既存の `from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, func` に `UniqueConstraint` を追加する。

- [ ] **Step 2: `database.py` に `migrate_dashboard_panel_configs()` を追記する**

`core-api/app/database.py` の末尾に追加:

```python
def migrate_dashboard_panel_configs() -> None:
    """dashboard_panel_configs テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_panel_configs (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                sensor_key  VARCHAR(64) NOT NULL,
                panel_type  VARCHAR(20) NOT NULL DEFAULT 'timeseries',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, sensor_key)
            )
        """))
        conn.commit()
```

- [ ] **Step 3: `main.py` の `on_startup` に migration を追加する**

`core-api/app/main.py` の import 行を修正:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs
```

`on_startup` のループに追加:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs):
```

- [ ] **Step 4: import できることを確認する**

```bash
cd core-api
python -c "from app.models.public import DashboardPanelConfig; from app.database import migrate_dashboard_panel_configs; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: コミットする**

```bash
git add core-api/app/models/public.py core-api/app/database.py core-api/app/main.py
git commit -m "feat: dashboard_panel_configs テーブル追加（モデル・マイグレーション）"
```

---

### Task 2: Grafanaパネルビルダー

**Files:**
- Modify: `core-api/app/services/grafana.py`
- Modify: `core-api/tests/test_grafana.py`

**Interfaces:**
- Consumes: なし（既存 `_FLUX_TELEMETRY`, `_FLUX_STATUS`, `_FLUX_DELETED` を活用）
- Produces:
  - `PANEL_DATA_MODE: dict[str, str]` — パネルタイプ → データモードのマッピング
  - `build_sensor_panel(sensor_key: str, panel_type: str, panel_id: int, x: int, y: int) -> dict`
  - `build_dashboard_panels(configs: list[dict]) -> list[dict]` — configs: `[{"sensor_key": str, "panel_type": str}]`
  - `sync_tenant_dashboard_with_configs(org_id: int, tenant_name: str, configs: list[dict]) -> None`

- [ ] **Step 1: テストを先に書く**

`core-api/tests/test_grafana.py` に追記:

```python
from app.services.grafana import build_sensor_panel, build_dashboard_panels, PANEL_DATA_MODE

def test_panel_data_mode_has_all_types():
    expected = {"timeseries", "barchart", "histogram", "heatmap", "state-timeline",
                "gauge", "stat", "bargauge", "table"}
    assert set(PANEL_DATA_MODE.keys()) == expected

def test_build_sensor_panel_timeseries():
    panel = build_sensor_panel("temperature", "timeseries", 10, 6, 1)
    assert panel["type"] == "timeseries"
    assert panel["id"] == 10
    assert panel["title"] == "temperature"
    assert panel["gridPos"] == {"x": 6, "y": 1, "w": 9, "h": 6}
    assert "aggregateWindow" in panel["targets"][0]["query"]

def test_build_sensor_panel_gauge_uses_last_query():
    panel = build_sensor_panel("humidity", "gauge", 11, 15, 1)
    assert panel["type"] == "gauge"
    assert "last()" in panel["targets"][0]["query"]
    assert "aggregateWindow" not in panel["targets"][0]["query"]

def test_build_sensor_panel_bargauge_uses_last_query():
    panel = build_sensor_panel("pressure", "bargauge", 12, 6, 8)
    assert panel["type"] == "bargauge"
    assert "last()" in panel["targets"][0]["query"]

def test_build_dashboard_panels_empty_configs_returns_fallback():
    panels = build_dashboard_panels([])
    types = [p["type"] for p in panels]
    assert "row" in types
    assert "timeseries" in types
    # should have the all-fields timeseries panel (id=3)
    ts_panel = next(p for p in panels if p["type"] == "timeseries")
    assert ts_panel["id"] == 3

def test_build_dashboard_panels_with_configs():
    configs = [
        {"sensor_key": "temperature", "panel_type": "gauge"},
        {"sensor_key": "humidity", "panel_type": "barchart"},
    ]
    panels = build_dashboard_panels(configs)
    types = [p["type"] for p in panels]
    # fallback timeseries should NOT appear
    assert not any(p.get("id") == 3 for p in panels)
    assert "gauge" in types
    assert "barchart" in types
    # fixed panels still present
    assert any(p.get("id") == 1 for p in panels)  # row
    assert any(p.get("id") == 2 for p in panels)  # stat deleted
    assert any(p.get("id") == 4 for p in panels)  # stat status

def test_build_sensor_panel_sensor_key_escaped():
    panel = build_sensor_panel('temp"test', "timeseries", 10, 6, 1)
    assert '\\"' in panel["targets"][0]["query"]
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
cd core-api
python -m pytest tests/test_grafana.py::test_panel_data_mode_has_all_types -v
```

Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: `grafana.py` に定数と Flux クエリビルダーを追加する**

`core-api/app/services/grafana.py` のファイル先頭（既存の `_FLUX_TELEMETRY` 定義の直前に）追加:

```python
PANEL_DATA_MODE: dict[str, str] = {
    "timeseries":     "timeseries",
    "barchart":       "timeseries",
    "histogram":      "timeseries",
    "heatmap":        "timeseries",
    "state-timeline": "timeseries",
    "gauge":          "last_value",
    "stat":           "last_value",
    "bargauge":       "last_value",
    "table":          "any",
}
```

同ファイルの `mark_device_deleted` 関数の直前に追加（既存クエリ定数の後）:

```python
def _flux_telemetry_field(sensor_key: str) -> str:
    escaped = sensor_key.replace("\\", "\\\\").replace('"', '\\"')
    return (
        'from(bucket: "telemetry")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> filter(fn: (r) => r.device_name =~ /^${device_name:regex}$/)\n'
        f'  |> filter(fn: (r) => r._field == "{escaped}")\n'
        '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
        '  |> yield(name: "mean")'
    )

def _flux_last_field(sensor_key: str) -> str:
    escaped = sensor_key.replace("\\", "\\\\").replace('"', '\\"')
    return (
        'from(bucket: "telemetry")\n'
        '  |> range(start: -1h)\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        '  |> filter(fn: (r) => r.device_name =~ /^${device_name:regex}$/)\n'
        f'  |> filter(fn: (r) => r._field == "{escaped}")\n'
        '  |> last()'
    )

def build_sensor_panel(sensor_key: str, panel_type: str, panel_id: int, x: int, y: int) -> dict:
    """センサーキーとパネルタイプからGrafanaパネル定義を生成する。"""
    use_last = PANEL_DATA_MODE.get(panel_type) == "last_value"
    query = _flux_last_field(sensor_key) if use_last else _flux_telemetry_field(sensor_key)
    ds = {"type": "influxdb"}

    base: dict = {
        "id": panel_id,
        "title": sensor_key,
        "type": panel_type,
        "gridPos": {"x": x, "y": y, "w": 9, "h": 6},
        "targets": [{"refId": "A", "datasource": ds, "query": query}],
        "fieldConfig": {"defaults": {"displayName": "${__field.name}"}},
    }

    if panel_type == "timeseries":
        base["options"] = {
            "tooltip": {"mode": "multi"},
            "legend": {"displayMode": "list", "placement": "bottom"},
        }
        base["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2}
    elif panel_type == "barchart":
        base["options"] = {"xTickLabelRotation": 0, "barWidth": 0.6}
    elif panel_type == "histogram":
        base["options"] = {"fillOpacity": 80, "gradientMode": "none"}
    elif panel_type == "heatmap":
        base["options"] = {"calculate": False, "color": {"scheme": "Oranges"}}
    elif panel_type == "state-timeline":
        base["options"] = {"mergeValues": True, "showValue": "auto"}
    elif panel_type == "gauge":
        base["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "auto",
        }
    elif panel_type == "stat":
        base["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "none",
        }
    elif panel_type == "bargauge":
        base["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "horizontal",
            "displayMode": "gradient",
        }
    # table: Grafana のデフォルト設定で動作するため options 不要

    return base

def build_dashboard_panels(configs: list[dict]) -> list[dict]:
    """パネル設定リストからGrafanaパネル配列を構築する。
    configs: [{"sensor_key": str, "panel_type": str}]
    空の場合は全フィールド timeseries のフォールバックを返す。
    """
    import copy

    # 固定パネル（既存 _DEFAULT_DASHBOARD の定義から抽出）
    row_panel = {
        "id": 1,
        "type": "row",
        "title": "${device_name}",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1},
        "repeat": "device_name",
        "repeatDirection": "v",
        "collapsed": False,
    }
    stat_deleted = copy.deepcopy(_DEFAULT_DASHBOARD["dashboard"]["panels"][1])  # id=2
    stat_status  = copy.deepcopy(_DEFAULT_DASHBOARD["dashboard"]["panels"][2])  # id=4
    fallback_ts  = copy.deepcopy(_DEFAULT_DASHBOARD["dashboard"]["panels"][3])  # id=3

    fixed = [row_panel, stat_deleted, stat_status]

    if not configs:
        return fixed + [fallback_ts]

    sensor_panels = []
    for i, cfg in enumerate(configs):
        panel_id = 10 + i
        x = 6 + (i % 2) * 9   # x=6 または x=15（2列）
        y = 1 + (i // 2) * 7
        sensor_panels.append(
            build_sensor_panel(cfg["sensor_key"], cfg["panel_type"], panel_id, x, y)
        )

    return fixed + sensor_panels
```

- [ ] **Step 4: `sync_tenant_dashboard_with_configs` を追加する**

`sync_tenant_dashboard` 関数の直後に追加:

```python
def sync_tenant_dashboard_with_configs(org_id: int, tenant_name: str, configs: list[dict]) -> None:
    """パネル設定付きでテナントダッシュボードを再生成する。PUT API から呼び出す。"""
    import copy
    auth = _admin_auth()

    prefs = httpx.get(
        f"{settings.grafana_url}/api/org/preferences",
        auth=auth,
        headers={"X-Grafana-Org-Id": str(org_id)},
        timeout=10.0,
    )
    prefs.raise_for_status()
    uid = prefs.json().get("homeDashboardUID")

    if not uid:
        # ダッシュボード未作成の場合は新規作成
        create_default_dashboard(org_id, tenant_name)
        return

    dash_resp = httpx.get(
        f"{settings.grafana_url}/api/dashboards/uid/{uid}",
        auth=auth,
        headers={"X-Grafana-Org-Id": str(org_id)},
        timeout=10.0,
    )
    if dash_resp.status_code != 200:
        return
    current_version = dash_resp.json()["dashboard"].get("version", 0)

    dashboard = copy.deepcopy(_DEFAULT_DASHBOARD)
    dashboard["dashboard"]["title"] = f"テレメトリ監視 - {tenant_name}"
    dashboard["dashboard"]["uid"] = uid
    dashboard["dashboard"]["version"] = current_version
    dashboard["dashboard"]["panels"] = build_dashboard_panels(configs)

    httpx.post(
        f"{settings.grafana_url}/api/dashboards/db",
        auth=auth,
        headers={"X-Grafana-Org-Id": str(org_id)},
        json=dashboard,
        timeout=10.0,
    ).raise_for_status()
```

- [ ] **Step 5: テストを実行して全て通ることを確認する**

```bash
cd core-api
python -m pytest tests/test_grafana.py -v
```

Expected: 全テスト PASS（既存3件 + 新規8件）

- [ ] **Step 6: コミットする**

```bash
git add core-api/app/services/grafana.py core-api/tests/test_grafana.py
git commit -m "feat: Grafanaパネルビルダー追加（9種チャートタイプ対応）"
```

---

### Task 3: APIエンドポイント（GET/PUT /tenant-portal/dashboard/panel-configs）

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Create: `core-api/tests/test_dashboard_panel_config.py`

**Interfaces:**
- Consumes:
  - `DashboardPanelConfig` モデル（Task 1）
  - `sync_tenant_dashboard_with_configs(org_id, tenant_name, configs)` (Task 2)
  - `_require_tenant()`, `_require_admin_or_operator()` Depends（既存）
- Produces:
  - `GET /tenant-portal/dashboard/panel-configs` → `list[dict]`
  - `PUT /tenant-portal/dashboard/panel-configs` → 204

- [ ] **Step 1: テストを先に書く**

新規ファイル `core-api/tests/test_dashboard_panel_config.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _tenant_token(role: str = "admin"):
    return create_access_token({
        "sub": "user-id",
        "email": "user@test.com",
        "type": "tenant",
        "tenant_id": TENANT_ID,
        "role": role,
    })

def _make_tenant():
    t = MagicMock()
    t.id = TENANT_ID
    t.name = "test-tenant"
    t.grafana_org_id = "5"
    return t

def _make_config(sensor_key: str, panel_type: str):
    c = MagicMock()
    c.sensor_key = sensor_key
    c.panel_type = panel_type
    return c

# ─── GET ────────────────────────────────────────────────────────────────────

def test_get_panel_configs_returns_empty_list():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.all.return_value = []
        resp = client.get(
            "/tenant-portal/dashboard/panel-configs",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    assert resp.json() == []

def test_get_panel_configs_returns_existing_configs():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.all.return_value = [
            _make_config("temperature", "gauge"),
            _make_config("humidity", "timeseries"),
        ]
        resp = client.get(
            "/tenant-portal/dashboard/panel-configs",
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["sensor_key"] == "temperature"
    assert data[0]["panel_type"] == "gauge"

def test_get_panel_configs_requires_auth():
    resp = client.get("/tenant-portal/dashboard/panel-configs")
    assert resp.status_code == 401

# ─── PUT ────────────────────────────────────────────────────────────────────

def test_put_panel_configs_success_operator():
    tenant = _make_tenant()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.services.grafana.sync_tenant_dashboard_with_configs") as mock_sync:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/dashboard/panel-configs",
            json=[{"sensor_key": "temperature", "panel_type": "gauge"}],
            cookies={"iot_token": _tenant_token("operator")},
        )
    assert resp.status_code == 204
    mock_sync.assert_called_once_with(5, "test-tenant", [{"sensor_key": "temperature", "panel_type": "gauge"}])

def test_put_panel_configs_viewer_is_forbidden():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[{"sensor_key": "temperature", "panel_type": "gauge"}],
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403

def test_put_panel_configs_invalid_sensor_key():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[{"sensor_key": "temp/erature", "panel_type": "gauge"}],
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422

def test_put_panel_configs_invalid_panel_type():
    resp = client.put(
        "/tenant-portal/dashboard/panel-configs",
        json=[{"sensor_key": "temperature", "panel_type": "piechart"}],
        cookies={"iot_token": _tenant_token("admin")},
    )
    assert resp.status_code == 422

def test_put_panel_configs_empty_list_clears_configs():
    tenant = _make_tenant()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.services.grafana.sync_tenant_dashboard_with_configs") as mock_sync:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/dashboard/panel-configs",
            json=[],
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 204
    mock_sync.assert_called_once_with(5, "test-tenant", [])

def test_put_panel_configs_requires_auth():
    resp = client.put("/tenant-portal/dashboard/panel-configs", json=[])
    assert resp.status_code == 401
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
cd core-api
python -m pytest tests/test_dashboard_panel_config.py -v
```

Expected: FAIL (endpoint not found, 404)

- [ ] **Step 3: `tenant_portal.py` に Pydantic スキーマとエンドポイントを追加する**

`tenant_portal.py` の import ブロックに追加:

```python
import re
from enum import Enum
from pydantic import field_validator
```

既存の `from pydantic import BaseModel, Field` はそのまま流用。`Enum` と `field_validator` を追加。

ルーターの最後（ファイル末尾）に追加:

```python
# ---------------------------------------------------------------------------
# ダッシュボードパネル設定
# ---------------------------------------------------------------------------

class PanelType(str, Enum):
    timeseries    = "timeseries"
    barchart      = "barchart"
    histogram     = "histogram"
    heatmap       = "heatmap"
    state_timeline = "state-timeline"
    gauge         = "gauge"
    stat          = "stat"
    bargauge      = "bargauge"
    table         = "table"


class PanelConfigItem(BaseModel):
    sensor_key: str
    panel_type: PanelType

    @field_validator("sensor_key")
    @classmethod
    def validate_sensor_key(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', v):
            raise ValueError("sensor_key must be alphanumeric, underscore, or hyphen (1-64 chars)")
        return v


@router.get("/dashboard/panel-configs")
def get_panel_configs(payload: dict = Depends(_require_tenant)):
    from app.models.public import DashboardPanelConfig
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        rows = db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id
        ).all()
    return [{"sensor_key": r.sensor_key, "panel_type": r.panel_type} for r in rows]


@router.put("/dashboard/panel-configs", status_code=204)
def put_panel_configs(
    items: list[PanelConfigItem],
    payload: dict = Depends(_require_admin_or_operator),
):
    from app.models.public import DashboardPanelConfig, Tenant
    from app.services.grafana import sync_tenant_dashboard_with_configs
    tenant_id = payload["tenant_id"]

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        # 全件置き換え
        db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id
        ).delete()
        for item in items:
            db.add(DashboardPanelConfig(
                tenant_id=tenant_id,
                sensor_key=item.sensor_key,
                panel_type=item.panel_type.value,
            ))
        db.commit()

    if tenant.grafana_org_id:
        configs = [{"sensor_key": i.sensor_key, "panel_type": i.panel_type.value} for i in items]
        try:
            sync_tenant_dashboard_with_configs(int(tenant.grafana_org_id), tenant.name, configs)
        except Exception as e:
            print(f"[panel_configs] Grafana sync failed: {e}")
```

- [ ] **Step 4: テストを実行して全て通ることを確認する**

```bash
cd core-api
python -m pytest tests/test_dashboard_panel_config.py -v
```

Expected: 全8件 PASS

- [ ] **Step 5: 既存テスト全件が通ることを確認する**

```bash
cd core-api
python -m pytest --tb=short -q
```

Expected: 全件 PASS（既存テストのリグレッションなし）

- [ ] **Step 6: コミットする**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_dashboard_panel_config.py
git commit -m "feat: ダッシュボードパネル設定 GET/PUT API 追加"
```

---

### Task 4: テナントポータルUI

**Files:**
- Modify: `admin-ui/js/api.js`
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes:
  - `GET /tenant-portal/dashboard/panel-configs`
  - `PUT /tenant-portal/dashboard/panel-configs`
- Produces: `ダッシュボード設定` タブ（ダッシュボードタイプ選択UI）

- [ ] **Step 1: `api.js` に `dashboardConfig` を追加する**

`admin-ui/js/api.js` の既存 API オブジェクト末尾に追加（platform プロパティの後など）:

```javascript
dashboardConfig: {
    list: () => request('GET', '/tenant-portal/dashboard/panel-configs'),
    update: (body) => request('PUT', '/tenant-portal/dashboard/panel-configs', body),
},
```

- [ ] **Step 2: `tenant-portal.html` のサイドバー tabs 配列に新タブを追加する**

既存の `tabs` 配列（Alpine.js の `portalApp()` 内）を探して末尾に追加。
`{ id: 'alerts', icon: '🔔', label: 'アラート' }` などの後に:

```javascript
{ id: 'dashboard-config', icon: '⚙', label: 'ダッシュボード設定' },
```

- [ ] **Step 3: `portalApp()` の data に `dashboardConfig` 関連ステートを追加する**

`portalApp()` 関数内の `return { ... }` に追加:

```javascript
// ダッシュボード設定
panelConfigs: [],          // [{sensor_key, panel_type}]
newSensorKey: '',
newPanelType: 'timeseries',
panelConfigSaving: false,
panelConfigError: '',
```

`init()` 関数内に追加（他のデータ取得と同様）:

```javascript
await this.loadPanelConfigs();
```

- [ ] **Step 4: `portalApp()` に `loadPanelConfigs`, `addPanelConfig`, `removePanelConfig`, `savePanelConfigs` メソッドを追加する**

```javascript
async loadPanelConfigs() {
    try {
        this.panelConfigs = await api.dashboardConfig.list();
    } catch(e) {
        // 設定なしは空配列なので無視
    }
},

addPanelConfig() {
    const key = this.newSensorKey.trim();
    if (!key || !/^[a-zA-Z0-9_-]{1,64}$/.test(key)) {
        this.panelConfigError = 'センサーキーは英数字・アンダースコア・ハイフンのみ使用できます';
        return;
    }
    if (this.panelConfigs.find(c => c.sensor_key === key)) {
        this.panelConfigError = `"${key}" は既に追加されています`;
        return;
    }
    this.panelConfigs.push({ sensor_key: key, panel_type: this.newPanelType });
    this.newSensorKey = '';
    this.newPanelType = 'timeseries';
    this.panelConfigError = '';
},

removePanelConfig(sensor_key) {
    this.panelConfigs = this.panelConfigs.filter(c => c.sensor_key !== sensor_key);
},

async savePanelConfigs() {
    this.panelConfigSaving = true;
    this.panelConfigError = '';
    try {
        await api.dashboardConfig.update(this.panelConfigs);
        this.switchTab('dashboard');
        // iframe を強制リロード
        this.$nextTick(() => {
            const iframe = document.querySelector('iframe');
            if (iframe) iframe.src = iframe.src;
        });
    } catch(e) {
        this.panelConfigError = 'ダッシュボードの更新に失敗しました: ' + (e.message || e);
    } finally {
        this.panelConfigSaving = false;
    }
},

isPanelTypeLastValue(panel_type) {
    return ['gauge', 'stat', 'bargauge'].includes(panel_type);
},
```

- [ ] **Step 5: ダッシュボード設定タブのHTMLを追加する**

`tenant-portal.html` の他のタブセクションと同じ `<div x-show="activeTab === '...'">` パターンで追加:

```html
<!-- ===== ダッシュボード設定 ===== -->
<div x-show="activeTab === 'dashboard-config'">
  <div class="flex items-center justify-between mb-4">
    <h3 class="font-medium text-gray-700">センサー別チャートタイプ設定</h3>
  </div>

  <div x-show="panelConfigError" class="mb-4 bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-2 rounded" x-text="panelConfigError"></div>

  <!-- 設定一覧 -->
  <div class="bg-white rounded-xl shadow-sm border border-gray-200 mb-4">
    <div x-show="panelConfigs.length === 0" class="py-8 text-center text-gray-400 text-sm">
      設定なし — フォールバック（全フィールド折れ線グラフ）で表示されます
    </div>
    <table x-show="panelConfigs.length > 0" class="w-full">
      <thead class="bg-gray-50 border-b border-gray-100">
        <tr>
          <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">センサーキー</th>
          <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">チャートタイプ</th>
          <th class="px-4 py-3"></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        <template x-for="(cfg, idx) in panelConfigs" :key="cfg.sensor_key">
          <tr>
            <td class="px-4 py-3 text-sm font-mono font-medium" x-text="cfg.sensor_key"></td>
            <td class="px-4 py-3">
              <div>
                <template x-if="canEdit">
                  <select x-model="panelConfigs[idx].panel_type"
                          class="text-sm border border-gray-300 rounded px-2 py-1">
                    <optgroup label="時系列（推移グラフ）">
                      <option value="timeseries">折れ線グラフ</option>
                      <option value="barchart">棒グラフ</option>
                      <option value="histogram">ヒストグラム</option>
                      <option value="heatmap">ヒートマップ</option>
                      <option value="state-timeline">状態タイムライン</option>
                    </optgroup>
                    <optgroup label="現在値（最新値のみ）">
                      <option value="gauge">ゲージ</option>
                      <option value="stat">シグナル</option>
                      <option value="bargauge">バーゲージ</option>
                    </optgroup>
                    <optgroup label="汎用">
                      <option value="table">テーブル</option>
                    </optgroup>
                  </select>
                </template>
                <template x-if="!canEdit">
                  <span class="text-sm" x-text="cfg.panel_type"></span>
                </template>
                <p x-show="isPanelTypeLastValue(cfg.panel_type)"
                   class="text-xs text-amber-600 mt-0.5">
                  ⚠ 最新値のみ表示されます（時系列データは集計されません）
                </p>
              </div>
            </td>
            <td class="px-4 py-3 text-right">
              <button x-show="canEdit" @click="removePanelConfig(cfg.sensor_key)"
                      class="text-xs text-red-500 hover:text-red-700">削除</button>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>

  <!-- 新規追加フォーム -->
  <div x-show="canEdit" class="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mb-4">
    <p class="text-xs font-medium text-gray-600 mb-2">センサーキーを追加</p>
    <div class="flex gap-2 items-start">
      <div class="flex-1">
        <input type="text" x-model="newSensorKey" placeholder="例: temperature"
               class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm font-mono"
               @keydown.enter="addPanelConfig()">
        <p class="text-xs text-gray-400 mt-0.5">英数字・アンダースコア・ハイフンのみ</p>
      </div>
      <select x-model="newPanelType" class="border border-gray-300 rounded px-2 py-1.5 text-sm">
        <optgroup label="時系列">
          <option value="timeseries">折れ線グラフ</option>
          <option value="barchart">棒グラフ</option>
          <option value="histogram">ヒストグラム</option>
          <option value="heatmap">ヒートマップ</option>
          <option value="state-timeline">状態タイムライン</option>
        </optgroup>
        <optgroup label="現在値">
          <option value="gauge">ゲージ</option>
          <option value="stat">シグナル</option>
          <option value="bargauge">バーゲージ</option>
        </optgroup>
        <optgroup label="汎用">
          <option value="table">テーブル</option>
        </optgroup>
      </select>
      <button @click="addPanelConfig()"
              class="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm whitespace-nowrap">
        + 追加
      </button>
    </div>
  </div>

  <!-- 反映ボタン -->
  <div x-show="canEdit" class="flex justify-end">
    <button @click="savePanelConfigs()"
            :disabled="panelConfigSaving"
            class="bg-green-600 hover:bg-green-700 disabled:bg-green-300 text-white px-4 py-2 rounded text-sm font-medium">
      <span x-text="panelConfigSaving ? '反映中...' : 'ダッシュボードに反映'"></span>
    </button>
  </div>
</div>
```

- [ ] **Step 6: ブラウザで動作確認する**

1. テナントポータルにログインして「ダッシュボード設定」タブが表示されることを確認
2. センサーキー `temperature` / チャートタイプ `ゲージ` を追加
3. 「ダッシュボードに反映」を押してエラーが出ないことを確認
4. ゲージを選択したときに「⚠ 最新値のみ表示されます」が表示されることを確認
5. `viewer` ロールでログインして追加・削除・反映ボタンが非表示になることを確認

- [ ] **Step 7: コミットする**

```bash
git add admin-ui/js/api.js admin-ui/tenant-portal.html
git commit -m "feat: テナントポータルにダッシュボード設定タブを追加"
```
