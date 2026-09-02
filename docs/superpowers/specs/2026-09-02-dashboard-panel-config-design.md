# ダッシュボードパネル設定 実装設計書

**Goal:** テナントポータルからセンサーキーごとにGrafanaのチャートタイプを選択・変更できるようにする

**Architecture:** テナントポータルUIで設定を変更 → core-API がDBに保存 → Grafana API でダッシュボードを即時再生成。Grafana の repeat 機能を維持しつつ、センサーキーごとに別パネルを動的生成する。

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, Grafana HTTP API (dashboard/db endpoint), Alpine.js, Tailwind CSS

**Spec:** docs/superpowers/specs/2026-09-02-dashboard-panel-config-design.md（本ファイル）

## Global Constraints

- Python 3.11+, FastAPI, SQLAlchemy (既存パターンに従う)
- チャートタイプ allowlist: `timeseries` / `barchart` / `gauge` / `stat` の4種のみ
- sensor_key は英数字・アンダースコア・ハイフンのみ許可（最大64文字）、Fluxインジェクション対策
- 変更権限は tenant ロール `operator` 以上。`viewer` は GET のみ
- Grafanaダッシュボード再生成は PUT 時に同期実行（非同期キューなし）
- 設定なしのセンサーキーは現行フォールバック（全フィールド timeseries 1枚）を維持
- 既存の stat パネル（登録状態・接続状態）は変更しない

---

## 1. データモデル

### 1.1 新テーブル: `dashboard_panel_configs`

publicスキーマに追加。

```sql
CREATE TABLE dashboard_panel_configs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    sensor_key  VARCHAR(64) NOT NULL,
    panel_type  VARCHAR(20) NOT NULL DEFAULT 'timeseries',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, sensor_key)
);
```

### 1.2 panel_type の値と data_mode

`data_mode` はバックエンドのFluxクエリ選択とUIの注記表示を整合させるための内部属性。
- `timeseries`: `aggregateWindow()` でグラフ全体を描画
- `last_value`: `last()` で最新値のみ取得
- `any`: どちらでも動作

このプラットフォームの全テレメトリデータは InfluxDB に時系列で保存される。
そのため、時系列データに対して意味を持つパネルタイプのみを対象とする。
`piechart`（割合表示）は時系列データと構造的に合わないため除外。

| 値 | 表示名 | data_mode | 用途 |
|---|---|---|---|
| `timeseries` | 折れ線グラフ | timeseries | 時系列推移（デフォルト） |
| `barchart` | 棒グラフ | timeseries | 時間軸集計値の比較 |
| `histogram` | ヒストグラム | timeseries | 値の分布 |
| `heatmap` | ヒートマップ | timeseries | 時間×値の密度 |
| `state-timeline` | 状態タイムライン | timeseries | ON/OFF等の状態変化 |
| `gauge` | ゲージ | last_value | 現在値（メーター型） |
| `stat` | シグナル | last_value | 現在値（数値＋色） |
| `bargauge` | バーゲージ | last_value | 現在値（バー型） |
| `table` | テーブル | any | 生データ一覧 |

**バックエンド定数** (`grafana.py` に定義):
```python
PANEL_DATA_MODE = {
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

`build_sensor_panel()` はこの辞書を参照してFluxクエリ（`_flux_telemetry_field` / `_flux_last_field`）を選択する。`PanelType` Enum の allowlist もこの10種に拡張する。

### 1.3 マイグレーション

`core-api/migrations/` に Alembic マイグレーションファイルを追加。既存テーブルへの影響なし。

### 1.4 SQLAlchemy モデル

```python
class DashboardPanelConfig(Base):
    __tablename__ = "dashboard_panel_configs"
    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id  = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    sensor_key = Column(String(64), nullable=False)
    panel_type = Column(String(20), nullable=False, default="timeseries")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "sensor_key"),)
```

---

## 2. API

### 2.1 エンドポイント

ファイル: `core-api/app/routers/tenant_portal.py`（既存ルーターに追記）

```
GET  /tenant-portal/dashboard/panel-configs
PUT  /tenant-portal/dashboard/panel-configs
```

### 2.2 GET /tenant-portal/dashboard/panel-configs

認証: tenant JWT クッキー（operator / viewer / admin）

レスポンス:
```json
[
  {"sensor_key": "temperature", "panel_type": "gauge"},
  {"sensor_key": "humidity",    "panel_type": "timeseries"}
]
```

空配列 = 設定なし（フォールバック動作）。

### 2.3 PUT /tenant-portal/dashboard/panel-configs

認証: tenant JWT クッキー（operator / admin のみ）

リクエストボディ:
```json
[
  {"sensor_key": "temperature", "panel_type": "gauge"},
  {"sensor_key": "humidity",    "panel_type": "barchart"}
]
```

処理順:
1. sensor_key バリデーション（正規表現 `^[a-zA-Z0-9_-]{1,64}$`）
2. panel_type バリデーション（allowlist 4種）
3. 当該 tenant_id の既存レコードを全削除
4. 新レコードを一括 INSERT
5. `sync_tenant_dashboard_with_configs(org_id, tenant_name, configs)` を呼び出してGrafana再生成
6. 204 No Content を返す

バリデーションエラー: 422 Unprocessable Entity

### 2.4 Pydantic スキーマ

```python
from enum import Enum
from pydantic import BaseModel, field_validator
import re

class PanelType(str, Enum):
    timeseries = "timeseries"
    barchart   = "barchart"
    gauge      = "gauge"
    stat       = "stat"

class PanelConfigItem(BaseModel):
    sensor_key: str
    panel_type: PanelType

    @field_validator("sensor_key")
    @classmethod
    def validate_sensor_key(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', v):
            raise ValueError("sensor_key must be alphanumeric, underscore, or hyphen (max 64 chars)")
        return v
```

---

## 3. Grafana パネル生成

### 3.1 変更ファイル

`core-api/app/services/grafana.py`

### 3.2 Flux クエリ（センサーキー別フィルタ付き）

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
```

### 3.3 パネルビルダー

```python
def build_sensor_panel(sensor_key: str, panel_type: str, panel_id: int, x: int, y: int) -> dict:
    """センサーキー + パネルタイプからGrafanaパネル定義を生成する。"""
    use_last = panel_type in ("gauge", "stat")
    query = _flux_last_field(sensor_key) if use_last else _flux_telemetry_field(sensor_key)

    base = {
        "id": panel_id,
        "title": sensor_key,
        "type": panel_type,
        "gridPos": {"x": x, "y": y, "w": 9, "h": 6},
        "targets": [{"refId": "A", "datasource": {"type": "influxdb"}, "query": query}],
        "fieldConfig": {"defaults": {"displayName": "${__field.name}"}},
    }

    if panel_type == "timeseries":
        base["options"] = {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}}
        base["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2}
    elif panel_type == "barchart":
        base["options"] = {"xTickLabelRotation": 0, "barWidth": 0.6}
    elif panel_type == "gauge":
        base["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "auto"}
    elif panel_type == "stat":
        base["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "auto", "textMode": "auto",
            "colorMode": "background", "graphMode": "none",
        }

    return base
```

### 3.4 ダッシュボードビルド関数の変更

既存の `create_default_dashboard` / `sync_tenant_dashboard` を拡張し、configs を受け取るオーバーロードを追加。

```python
def build_dashboard_panels(configs: list[dict]) -> list[dict]:
    """configs: [{"sensor_key": str, "panel_type": str}]"""
    fixed = [_stat_deleted_panel(), _stat_status_panel()]  # id=2, id=4（既存）

    if not configs:
        return fixed + [_timeseries_all_panel()]  # フォールバック（既存の id=3）

    sensor_panels = []
    for i, cfg in enumerate(configs):
        panel_id = 10 + i  # 固定パネル(1,2,4)と重複しないよう10以降
        x = (i % 2) * 12   # 2列レイアウト
        y = 1 + (i // 2) * 7
        sensor_panels.append(
            build_sensor_panel(cfg["sensor_key"], cfg["panel_type"], panel_id, x, y)
        )

    return fixed + sensor_panels

def sync_tenant_dashboard_with_configs(org_id: int, tenant_name: str, configs: list[dict]) -> None:
    """設定付きでダッシュボードを再生成する。PUT API から呼び出す。"""
    # 既存の sync_tenant_dashboard を configs 対応版に拡張
    ...
```

---

## 4. UI (tenant-portal.html)

### 4.1 新タブ: ダッシュボード設定

サイドバーに追加:
```javascript
{ id: 'dashboard-config', icon: '⚙', label: 'ダッシュボード設定' }
```

### 4.2 タブ内レイアウト

```
設定中のパネル一覧（テーブル）
  | センサーキー | チャートタイプ | 操作 |
  | temperature | [ゲージ    ▼] | [削除] |
  | humidity    | [折れ線    ▼] | [削除] |

新規追加フォーム
  [センサーキー入力 ___________] [折れ線 ▼] [+ 追加]

[ダッシュボードに反映]ボタン
  → PUT → 成功時に「ダッシュボード」タブに切り替えてiframeをリロード
```

### 4.3 UI ロジック

- `configs` 配列をローカルで編集（追加・変更・削除）し、「反映」ボタンで一括 PUT
- viewer ロールは追加・削除・変更ボタンを非表示（読み取り専用表示）
- PUT 中はボタンを disabled + "反映中..." 表示
- エラー時はエラーバーに表示（既存パターン踏襲）

**チャートタイプ選択 UIの整合性表示:**

ドロップダウンは `data_mode` でグループ分けして表示する:
```html
<select>
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
```

`last_value` グループを選択すると行に注記を表示:
`⚠ このチャートは最新値のみ表示します（時系列データは集計されません）`

### 4.4 api.js への追加

```javascript
dashboardConfig: {
    list: () => request('GET', '/tenant-portal/dashboard/panel-configs'),
    update: (body) => request('PUT', '/tenant-portal/dashboard/panel-configs', body),
}
```

---

## 5. テスト方針

- `test_dashboard_panel_config.py`:
  - GET: 認証必須、空配列レスポンス確認
  - PUT (operator): バリデーション通過・DB保存確認
  - PUT (viewer): 403 返却確認
  - PUT: 不正 sensor_key (スラッシュ含む) → 422
  - PUT: 不正 panel_type → 422
- Grafana API 呼び出しは `unittest.mock.patch` でモック
- `build_dashboard_panels()` のユニットテスト（configs あり・なし両方）
