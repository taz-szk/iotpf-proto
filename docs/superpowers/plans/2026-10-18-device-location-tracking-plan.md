# デバイス位置情報(GPS)表示機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナント内デバイスの最新位置を地図上にピン表示する「全体表示」画面と、1台の移動履歴を表示する「履歴」画面をテナントポータルに追加し、シミュレータから動作確認できるようにする。

**Architecture:** 位置情報は既存のテレメトリ経路(MQTT→ingestion-service→InfluxDB)に`latitude`/`longitude`という名前のセンサー値として乗せる(ingestion側は無変更)。core-apiに新規APIを追加し、フロントエンドはLeaflet.js(ローカル配置、タイル画像のみ外部読み込み)で地図を描画する。シミュレータにはワーカーごとに状態を持つGPSランダムウォーク機能を追加する。

**Tech Stack:** FastAPI, InfluxDB(Flux, httpx経由の生クエリ), SQLAlchemy, Leaflet.js, Alpine.js, Python(Tkinterシミュレータ)。

**Spec:** `docs/superpowers/specs/2026-10-18-device-location-tracking-design.md`

## Global Constraints

- 位置情報の「鮮度」閾値は1時間: 最新の`latitude`/`longitude`の受信時刻が1時間より古いデバイスは「位置不明」として扱う(ピン非表示)
- 履歴クエリの期間上限は7日間: `end - start`が7日を超える場合は422を返す
- 地図タイルURLは設定値: `core-api/app/config.py`に`map_tile_url_template`(既定値`https://tile.openstreetmap.org/{z}/{x}/{y}.png`)・`map_tile_attribution`(既定値`&copy; OpenStreetMap contributors`)を追加する
- 地図描画はLeaflet.js(ローカル静的配置)。タイル画像のみ例外的に外部ホストから読み込む
- 位置情報タブは全ロール(admin/operator/viewer)が閲覧可能。「この表示をデフォルトにする」ボタンのみadmin/operator限定(既存の`canEdit`)

## Review Focus

- 1時間より古い位置情報しか無いデバイスは、全体表示のピンに出してはならず、一覧側では「位置不明」等の表示にすること(配送車両の古い位置を現在地のように見せない)
- 履歴クエリで`end - start`が7日を超える場合、422で拒否すること(黙って間引く・切り詰めるのではない)
- `group_id`に他テナントのグループID、または存在しないグループIDを渡した場合、そのテナントのデバイスが漏れたり500になったりしないこと
- `PUT /tenant-portal/me/map-default-view`に範囲外の緯度(-90〜90外)・経度(-180〜180外)を渡した場合、422で拒否し保存しないこと
- `GET /tenant-portal/me/devices/{device_id}/location-history`に他テナントのdevice_idを渡した場合、404を返し、そのデバイスのデータを返さないこと

---

### Task 1: `Tenant`の地図デフォルト表示カラムとタイル設定

**Files:**
- Modify: `core-api/app/models/public.py`(`Tenant`クラスに`map_default_lat`/`map_default_lng`/`map_default_zoom`を追加)
- Modify: `core-api/app/database.py`(`migrate_add_map_default_view()`を追加)
- Modify: `core-api/app/main.py`(インポート行と起動時マイグレーションタプルに追加)
- Modify: `core-api/app/config.py`(`map_tile_url_template`・`map_tile_attribution`を追加)
- Modify: `.env.example`(リポジトリルート。`MAP_TILE_URL_TEMPLATE`・`MAP_TILE_ATTRIBUTION`を追加)
- Test: `core-api/tests/test_tenant_map_default_view_model.py`

**Interfaces:**
- Produces: `Tenant.map_default_lat: float | None`, `Tenant.map_default_lng: float | None`, `Tenant.map_default_zoom: int | None`
- Produces: `migrate_add_map_default_view() -> None`
- Produces: `settings.map_tile_url_template: str`, `settings.map_tile_attribution: str`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_map_default_view_model.py
import uuid
from app.models.public import Tenant

def test_tenant_map_default_view_fields():
    t = Tenant(id=uuid.uuid4(), name="Acme", slug="acme",
               map_default_lat=35.68, map_default_lng=139.76, map_default_zoom=10)
    assert t.map_default_lat == 35.68
    assert t.map_default_lng == 139.76
    assert t.map_default_zoom == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_map_default_view_model.py -v`
Expected: FAIL(`TypeError: 'map_default_lat' is an invalid keyword argument`)

- [ ] **Step 3: `Tenant`クラスに3カラムを追加**

`core-api/app/models/public.py`の`Tenant`クラス末尾(既存の`trial_extension_requested_at`の直後)に追加:
```python
map_default_lat = Column(Float, nullable=True)
map_default_lng = Column(Float, nullable=True)
map_default_zoom = Column(Integer, nullable=True)
```
`Float`をファイル先頭の`from sqlalchemy import ...`に追加すること(既存は`Column, String, Boolean, Integer, DateTime, ForeignKey, func, UniqueConstraint`)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_map_default_view_model.py -v`
Expected: PASS

- [ ] **Step 5: マイグレーション・設定値を追加**

`core-api/app/database.py`に、既存の`migrate_add_trial_columns()`と同じ`ADD COLUMN IF NOT EXISTS`パターンで追加:
```python
def migrate_add_map_default_view() -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS map_default_lat DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS map_default_lng DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS map_default_zoom INTEGER
        """))
        conn.commit()
```
`core-api/app/main.py`のインポート行・起動時マイグレーションタプルの両方に`migrate_add_map_default_view`を追加する。

`core-api/app/config.py`の`Settings`クラスに追加:
```python
map_tile_url_template: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
map_tile_attribution: str = "&copy; OpenStreetMap contributors"
```

リポジトリルートの`.env.example`に以下を追加(既存の`RECAPTCHA_SITE_KEY`等と同じブロックの近くでよい):
```
MAP_TILE_URL_TEMPLATE=https://tile.openstreetmap.org/{z}/{x}/{y}.png
MAP_TILE_ATTRIBUTION=&copy; OpenStreetMap contributors
```
`docker-compose.yml`のcore-apiサービスの`environment:`に以下を追加(既存の`RECAPTCHA_SITE_KEY`等と同じブロック):
```yaml
      MAP_TILE_URL_TEMPLATE: "${MAP_TILE_URL_TEMPLATE:-https://tile.openstreetmap.org/{z}/{x}/{y}.png}"
      MAP_TILE_ATTRIBUTION: "${MAP_TILE_ATTRIBUTION:-&copy; OpenStreetMap contributors}"
```
(前回のテナントセルフサインアップ機能で、新しい設定値をdocker-compose.ymlに配線し忘れてreCAPTCHAが無効化されたままになっていた事故があるため、このステップを他のタスクに埋め込まず独立したチェック項目として明示する。)

- [ ] **Step 6: Run full backend test suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 7: Commit**

```bash
git add core-api/app/models/public.py core-api/app/database.py core-api/app/main.py core-api/app/config.py .env.example docker-compose.yml core-api/tests/test_tenant_map_default_view_model.py
git commit -m "feat(location): Tenantの地図デフォルト表示カラムとタイル設定を追加"
```

---

### Task 2: 地図設定の取得・更新API

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`(`GET /me`を拡張、`PUT /me/map-default-view`を追加)
- Test: `core-api/tests/test_tenant_portal_map_default_view.py`

**Interfaces:**
- Consumes: `Tenant.map_default_lat/lng/zoom`(Task 1)、`settings.map_tile_url_template/attribution`(Task 1)
- Produces: `GET /tenant-portal/me`の応答に`map_default_lat`・`map_default_lng`・`map_default_zoom`・`map_tile_url_template`・`map_tile_attribution`を追加
- Produces: `PUT /tenant-portal/me/map-default-view`(admin/operatorのみ)。ボディ`{lat: float, lng: float, zoom: int}`。応答は更新後の3フィールド

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_portal_map_default_view.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _cookie(role="admin"):
    token = create_access_token({"sub": "u1", "email": "a@acme.com", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}

def _session_ctx(tenant):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant
    return mock_db

def test_me_includes_map_settings():
    tenant = MagicMock(map_default_lat=35.0, map_default_lng=139.0, map_default_zoom=8, grafana_org_id=None, public_token=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=False):
        mock_sl.return_value = _session_ctx(tenant)
        resp = client.get("/tenant-portal/me", cookies=_cookie())
    assert resp.status_code == 200
    body = resp.json()
    assert body["map_default_lat"] == 35.0
    assert "map_tile_url_template" in body

def test_update_map_default_view_rejects_invalid_latitude():
    with patch("app.routers.tenant_portal.SessionLocal"):
        resp = client.put("/tenant-portal/me/map-default-view", json={"lat": 999, "lng": 139.0, "zoom": 8}, cookies=_cookie())
    assert resp.status_code == 422

def test_update_map_default_view_saves_values():
    tenant = MagicMock(id=TENANT_ID)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        db = _session_ctx(tenant)
        mock_sl.return_value = db
        resp = client.put("/tenant-portal/me/map-default-view", json={"lat": 35.5, "lng": 139.5, "zoom": 10}, cookies=_cookie())
    assert resp.status_code == 200
    assert tenant.map_default_lat == 35.5
    assert db.commit.called

def test_update_map_default_view_requires_admin_or_operator():
    resp = client.put("/tenant-portal/me/map-default-view", json={"lat": 35.5, "lng": 139.5, "zoom": 10}, cookies=_cookie(role="viewer"))
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_map_default_view.py -v`
Expected: FAIL(`map_default_lat`が`get_me`の応答に無い、`/me/map-default-view`が404)

- [ ] **Step 3: `get_me`を拡張し、`map-default-view`エンドポイントを追加**

`core-api/app/routers/tenant_portal.py`の`get_me`が返すdictに追加:
```python
"map_default_lat": tenant.map_default_lat,
"map_default_lng": tenant.map_default_lng,
"map_default_zoom": tenant.map_default_zoom,
"map_tile_url_template": settings.map_tile_url_template,
"map_tile_attribution": settings.map_tile_attribution,
```

新規エンドポイント(既存の`PublicAccessBody`/`toggle_public_access`の直後に追加):
```python
class MapDefaultViewBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    zoom: int = Field(ge=0, le=22)

@router.put("/me/map-default-view")
def update_map_default_view(body: MapDefaultViewBody, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant.map_default_lat = body.lat
        tenant.map_default_lng = body.lng
        tenant.map_default_zoom = body.zoom
        db.commit()
        return {"map_default_lat": tenant.map_default_lat, "map_default_lng": tenant.map_default_lng, "map_default_zoom": tenant.map_default_zoom}
```
`Field`を`from pydantic import BaseModel, Field, ...`に追加すること(既存のimportに`Field`が無ければ追加)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_map_default_view.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend test suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_map_default_view.py
git commit -m "feat(location): 地図デフォルト表示の取得・更新APIを追加"
```

---

### Task 3: 全デバイスの最新位置取得API

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_tenant_portal_device_locations.py`

**Interfaces:**
- Consumes: `list_group_device_ids(schema, group_id)`(既存 `app.services.device_groups`、見つからない場合`GroupNotFoundError`を投げる)
- Produces: `GET /tenant-portal/me/device-locations?group_id=...`(`group_id`省略可)。応答は`[{device_id, device_name, latitude, longitude, last_seen_at, stale}]`のリスト。`stale`は最新受信から1時間以上経過していたら`true`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_portal_device_locations.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.device_groups import GroupNotFoundError

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _cookie(role="viewer"):
    token = create_access_token({"sub": "u1", "email": "a@acme.com", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}

def test_device_locations_marks_old_position_as_stale():
    fresh = {"device_id": "d1", "device_name": "D1", "latitude": 35.0, "longitude": 139.0,
              "last_seen_at": datetime.now(timezone.utc) - timedelta(minutes=10)}
    stale = {"device_id": "d2", "device_name": "D2", "latitude": 34.0, "longitude": 138.0,
              "last_seen_at": datetime.now(timezone.utc) - timedelta(hours=2)}
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal._query_latest_device_locations", return_value=[fresh, stale]):
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get("/tenant-portal/me/device-locations", cookies=_cookie())
    assert resp.status_code == 200
    body = {d["device_id"]: d for d in resp.json()}
    assert body["d1"]["stale"] is False
    assert body["d2"]["stale"] is True

def test_device_locations_filters_by_group():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal._query_latest_device_locations", return_value=[]) as mock_query, \
         patch("app.routers.tenant_portal.list_group_device_ids", return_value=["d1", "d2"]) as mock_group:
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get("/tenant-portal/me/device-locations?group_id=g1", cookies=_cookie())
    assert resp.status_code == 200
    mock_group.assert_called_once()
    assert mock_query.call_args[0][1] == ["d1", "d2"]

def test_device_locations_returns_404_for_unknown_group():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.list_group_device_ids", side_effect=GroupNotFoundError("g-missing")):
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get("/tenant-portal/me/device-locations?group_id=g-missing", cookies=_cookie())
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_device_locations.py -v`
Expected: FAIL(エンドポイント・`_query_latest_device_locations`が未定義)

- [ ] **Step 3: `_query_latest_device_locations`と`GET /me/device-locations`を追加**

`core-api/app/routers/tenant_portal.py`に追加(`from app.services.device_groups import ...`の既存importに`GroupNotFoundError`, `list_group_device_ids`が無ければ追加):

```python
_STALE_AFTER = timedelta(hours=1)

def _query_latest_device_locations(influxdb_org_id: str, device_ids: list[str] | None) -> list[dict]:
    """InfluxDBから各device_idの最新latitude/longitude/last_seen_atを取得する。
    device_idsがNoneなら全デバイス、空リストなら何も返さない。"""
    # 既存のget_tenant_sensor_keys (app/services/grafana.py) と同じhttpx経由のFlux実行パターンを使う。
    # device_id タグでフィルタし、_field in ("latitude","longitude") を group(columns:["device_id","_field"]) |> last() で取得、
    # device_idごとにlatitude/longitude/時刻をまとめて返す。
    ...

@router.get("/me/device-locations")
def get_device_locations(group_id: Optional[str] = None, payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    device_ids = None
    if group_id:
        schema = _schema(tenant_id)
        try:
            device_ids = list_group_device_ids(schema, group_id)
        except GroupNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    rows = _query_latest_device_locations(tenant.influxdb_org_id, device_ids)
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        last_seen = r["last_seen_at"]
        result.append({
            "device_id": r["device_id"], "device_name": r["device_name"],
            "latitude": r["latitude"], "longitude": r["longitude"],
            "last_seen_at": last_seen.isoformat(),
            "stale": (now - last_seen) > _STALE_AFTER,
        })
    return result
```
`_query_latest_device_locations`の内部実装(Flux文の組み立て・httpx呼び出し・CSV解析)は、`app/services/grafana.py`の`get_tenant_sensor_keys`と同じ構造を書く。device_name・device_idが同時に必要なので、Fluxは`_field`でgroupしたあと`pivot(rowKey:["_time","device_id"], columnKey: ["_field"], valueColumn: "_value")`等でlatitude/longitude列を1行にまとめる形にする(具体的な組み方は**ビルダーの判断に委ねる**。テストは`_query_latest_device_locations`自体をモックしているため、この関数の内部実装は本タスクの外部仕様[device_id/device_name/latitude/longitude/last_seen_atを持つdictのリストを返す]を満たせば良い)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_device_locations.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend test suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_device_locations.py
git commit -m "feat(location): 全デバイスの最新位置取得APIを追加"
```

---

### Task 4: 1台の位置履歴取得API

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_tenant_portal_location_history.py`

**Interfaces:**
- Consumes: Task 1〜3のパターン(`_schema`, `Tenant`検索)
- Produces: `GET /tenant-portal/me/devices/{device_id}/location-history?start=...&end=...`(ISO8601文字列)。応答は`[{time, latitude, longitude}]`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_portal_location_history.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _cookie():
    token = create_access_token({"sub": "u1", "email": "a@acme.com", "tenant_id": TENANT_ID, "role": "viewer", "type": "tenant"})
    return {"iot_token": token}

def _iso(dt):
    return dt.isoformat()

def test_location_history_rejects_range_over_7_days():
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(days=8))
    end = _iso(now)
    resp = client.get(f"/tenant-portal/me/devices/d1/location-history?start={start}&end={end}", cookies=_cookie())
    assert resp.status_code == 422

def test_location_history_returns_404_for_device_in_another_tenant():
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(hours=1))
    end = _iso(now)
    with patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        resp = client.get(f"/tenant-portal/me/devices/d1/location-history?start={start}&end={end}", cookies=_cookie())
    assert resp.status_code == 404

def test_location_history_returns_points_for_valid_device():
    now = datetime.now(timezone.utc)
    start = _iso(now - timedelta(hours=1))
    end = _iso(now)
    point = {"time": now.isoformat(), "latitude": 35.0, "longitude": 139.0}
    with patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal._query_location_history", return_value=[point]):
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = MagicMock()
        tenant = MagicMock(id=TENANT_ID, influxdb_org_id="org1")
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.get(f"/tenant-portal/me/devices/d1/location-history?start={start}&end={end}", cookies=_cookie())
    assert resp.status_code == 200
    assert resp.json() == [point]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_location_history.py -v`
Expected: FAIL(エンドポイント未定義)

- [ ] **Step 3: `_query_location_history`と`GET /me/devices/{device_id}/location-history`を追加**

```python
_MAX_HISTORY_RANGE = timedelta(days=7)

def _query_location_history(influxdb_org_id: str, device_id: str, start: datetime, end: datetime) -> list[dict]:
    # Task 3の_query_latest_device_locationsと同様のFlux/httpxパターン。
    # range(start: start, stop: end) |> filter device_id == device_id |> filter _field in (latitude,longitude)
    # |> pivot で1行にまとめ、[{"time":..., "latitude":..., "longitude":...}] を時刻昇順で返す。
    ...

@router.get("/me/devices/{device_id}/location-history")
def get_device_location_history(device_id: str, start: datetime, end: datetime, payload: dict = Depends(_require_tenant)):
    if end - start > _MAX_HISTORY_RANGE:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Range too large (max 7 days)")

    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT device_id FROM "{schema}".devices WHERE device_id = :did'),
            {"did": device_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return _query_location_history(tenant.influxdb_org_id, device_id, start, end)
```
FastAPIは`datetime`型のクエリパラメータをISO8601文字列から自動変換する(既存のbilling系エンドポイントで同様の型を使っていなければ、Pydanticの標準動作に任せて良い)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_location_history.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend test suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_location_history.py
git commit -m "feat(location): デバイス位置履歴取得APIを追加"
```

---

### Task 5: Leaflet.jsのローカル配置とnginx CSP

**Files:**
- Create: `admin-ui/static/leaflet.js`, `admin-ui/static/leaflet.css`, `admin-ui/static/leaflet-images/`(マーカー画像一式)
- Modify: `nginx/conf.d/api.conf`(`/admin/`ロケーションのCSP)

**Interfaces:**
- Produces: `admin-ui/static/leaflet.js`・`leaflet.css`(Task 6で`<script>`/`<link>`から読み込む)

- [ ] **Step 1: Leaflet.js(v1.9系の安定版)を取得し配置する**

公式(https://leafletjs.com/download.html)からLeafletの配布物を取得し、`leaflet.js`・`leaflet.css`を`admin-ui/static/`に、マーカー画像(`marker-icon.png`等一式)を`admin-ui/static/leaflet-images/`に配置する。既存の`alpine.min.js`等と同じく、ビルドツールを介さずファイルをそのまま置く。

- [ ] **Step 2: `nginx/conf.d/api.conf`の`/admin/`ロケーションのCSPにタイルホストを追加**

既存の`/admin/`ロケーション(前回のパスワード再設定機能でreCAPTCHA用に緩めたCSPが既にある)の`Content-Security-Policy`の`img-src`に、タイルサーバーのホストを追加する:
`img-src 'self' data: https://tile.openstreetmap.org;`
(他の既存ディレクティブ`script-src`/`frame-src`/`connect-src`等はreCAPTCHA用のまま変更しない。)

- [ ] **Step 3: Verify**

Run: `docker compose config --quiet`
Expected: 終了コード0、出力なし

- [ ] **Step 4: Commit**

```bash
git add admin-ui/static/leaflet.js admin-ui/static/leaflet.css admin-ui/static/leaflet-images nginx/conf.d/api.conf
git commit -m "feat(location): Leaflet.jsをローカル配置しCSPにタイルホストを追加"
```

---

### Task 6: テナントポータルの「位置情報」タブ(全体表示・履歴)

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: `GET /tenant-portal/me`(Task 2で拡張済み)、`PUT /tenant-portal/me/map-default-view`(Task 2)、`GET /tenant-portal/me/device-locations`(Task 3)、`GET /tenant-portal/me/devices/{device_id}/location-history`(Task 4)、`admin-ui/static/leaflet.js`/`leaflet.css`(Task 5)、既存の`this.groups`配列(`portalApp()`既存)

- [ ] **Step 1: `<head>`にLeaflet.jsを読み込む**

```html
<link rel="stylesheet" href="/admin/static/leaflet.css">
<script src="/admin/static/leaflet.js"></script>
```

- [ ] **Step 2: `tabs`配列に「位置情報」を追加**

既存の`get tabs()`の`base`配列(`devices`の直後あたり)に追加: `{ id: 'location', label: '位置情報', icon: '📍' }`

- [ ] **Step 3: 全体表示画面のHTMLを追加**

既存の`<div x-show="activeTab === 'devices'">`ブロックの直後に追加。グループ選択ドロップダウン(`this.groups`から生成、選択で`loadDeviceLocations()`を再実行)、地図を表示する`<div id="location-map" style="height:500px;">`、admin/operator向けの「この表示をデフォルトにする」ボタン(`@click="saveMapDefaultView()"`)。

- [ ] **Step 4: 履歴画面のHTMLを追加**

同じく`activeTab === 'location'`配下に、`locationSubView === 'history'`のときだけ表示する領域を追加。期間選択(プリセット: 過去1時間・24時間・7日間。7日間が上限)、`<div id="location-history-map" style="height:500px;">`、全体表示に戻るリンク。

- [ ] **Step 5: `portalApp()`にメソッド・状態を追加**

```javascript
locationMap: null, locationHistoryMap: null,
locationSubView: 'overview', // 'overview' | 'history'
selectedLocationGroupId: '', selectedHistoryDeviceId: null, selectedHistoryRangeHours: 24,
savingMapDefaultView: false,

async loadDeviceLocations() {
  const qs = this.selectedLocationGroupId ? `?group_id=${this.selectedLocationGroupId}` : '';
  const locations = await portalFetch('GET', `/me/device-locations${qs}`);
  this._renderLocationPins(locations);
},
_renderLocationPins(locations) { /* Leafletマーカーの描画。staleなデバイスはピンを出さない */ },
async saveMapDefaultView() {
  this.savingMapDefaultView = true;
  try {
    const center = this.locationMap.getCenter();
    const zoom = this.locationMap.getZoom();
    await portalFetch('PUT', '/me/map-default-view', { lat: center.lat, lng: center.lng, zoom });
  } catch (e) { this.error = e.message; }
  finally { this.savingMapDefaultView = false; }
},
openLocationHistory(deviceId) {
  this.selectedHistoryDeviceId = deviceId;
  this.locationSubView = 'history';
  this.$nextTick(() => this.loadLocationHistory());
},
async loadLocationHistory() {
  const end = new Date();
  const start = new Date(end.getTime() - this.selectedHistoryRangeHours * 3600 * 1000);
  const points = await portalFetch('GET',
    `/me/devices/${this.selectedHistoryDeviceId}/location-history?start=${start.toISOString()}&end=${end.toISOString()}`);
  this._renderLocationHistoryLine(points);
},
_renderLocationHistoryLine(points) { /* Leafletのポリラインで軌跡を描画 */ },
```
地図の初期化(`L.map(...)`)・タイルレイヤーの追加(`L.tileLayer(me.map_tile_url_template, { attribution: me.map_tile_attribution })`)・マーカー/ポリラインの描画の具体的なLeaflet API呼び出しは**ビルダーの判断に委ねる**(Leaflet公式ドキュメントの標準的な使い方に従う)。地図の初期中心・ズームは`me.map_default_lat`/`lng`/`zoom`(未設定なら東京付近・ズーム5程度を既定値とする)。

- [ ] **Step 6: HTML構文確認**

Run: 既存のPython `html.parser`によるタグ対応チェックスクリプトを`admin-ui/tenant-portal.html`に対して実行する。
Expected: タグの対応に問題なし

- [ ] **Step 7: Commit**

```bash
git add admin-ui/tenant-portal.html
git commit -m "feat(location): テナントポータルに位置情報タブ(全体表示・履歴)を追加"
```

---

### Task 7: シミュレータのGPSランダムウォーク(純粋関数)

**Files:**
- Create: `simulator/gps_simulation.py`
- Test: `simulator/tests/test_gps_simulation.py`

**Interfaces:**
- Produces: `random_offset(base_lat: float, base_lon: float, max_offset_deg: float = 0.01) -> tuple[float, float]`
- Produces: `make_gps_payload_fn(start_lat: float, start_lon: float, step_deg: float = 0.0005) -> Callable[[], dict]`(呼ぶたびに前回位置から小さくランダムに移動した`{"latitude": float, "longitude": float}`を返す、状態を持つクロージャ)

- [ ] **Step 1: Write the failing test**

```python
# simulator/tests/test_gps_simulation.py
from simulator.gps_simulation import random_offset, make_gps_payload_fn

def test_random_offset_stays_within_max_offset():
    lat, lon = random_offset(35.0, 139.0, max_offset_deg=0.01)
    assert abs(lat - 35.0) <= 0.01
    assert abs(lon - 139.0) <= 0.01

def test_make_gps_payload_fn_returns_latitude_and_longitude():
    fn = make_gps_payload_fn(35.0, 139.0, step_deg=0.0005)
    payload = fn()
    assert set(payload.keys()) == {"latitude", "longitude"}

def test_make_gps_payload_fn_moves_gradually_not_randomly_each_call():
    fn = make_gps_payload_fn(35.0, 139.0, step_deg=0.0005)
    first = fn()
    second = fn()
    # 前回位置からstep_degを超えて飛ばないこと(ランダムウォークであり、毎回ゼロから引き直すのではない)
    assert abs(second["latitude"] - first["latitude"]) <= 0.0005
    assert abs(second["longitude"] - first["longitude"]) <= 0.0005
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd simulator && python -m pytest tests/test_gps_simulation.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'simulator.gps_simulation'`)

- [ ] **Step 3: `simulator/gps_simulation.py`を実装**

```python
import random

def random_offset(base_lat: float, base_lon: float, max_offset_deg: float = 0.01) -> tuple[float, float]:
    return (
        base_lat + random.uniform(-max_offset_deg, max_offset_deg),
        base_lon + random.uniform(-max_offset_deg, max_offset_deg),
    )

def make_gps_payload_fn(start_lat: float, start_lon: float, step_deg: float = 0.0005):
    state = {"lat": start_lat, "lon": start_lon}
    def fn():
        state["lat"] += random.uniform(-step_deg, step_deg)
        state["lon"] += random.uniform(-step_deg, step_deg)
        return {"latitude": round(state["lat"], 6), "longitude": round(state["lon"], 6)}
    return fn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd simulator && python -m pytest tests/test_gps_simulation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add simulator/gps_simulation.py simulator/tests/test_gps_simulation.py
git commit -m "feat(location): シミュレータ用GPSランダムウォークの純粋関数を追加"
```

---

### Task 8: シミュレータGUIへのGPS設定追加

**Files:**
- Modify: `simulator/simulator.py`

**Interfaces:**
- Consumes: `random_offset`・`make_gps_payload_fn`(Task 7)

- [ ] **Step 1: GUIに「GPS移動シミュレーション」セクションを追加**

既存のランダムフィールド設定UIの近くに、チェックボックス`self._gps_enabled`(`tk.BooleanVar`)、緯度`self._gps_base_lat`・経度`self._gps_base_lon`(`tk.StringVar`、既定値は空文字)の入力欄を追加する。

- [ ] **Step 2: `_save_config`/`_load_config`にGPS設定を追加**

`_save_config`の`cfg`辞書に追加: `"gps_enabled": self._gps_enabled.get(), "gps_base_lat": self._gps_base_lat.get(), "gps_base_lon": self._gps_base_lon.get()`。
`_load_config`に対応する読み込みを追加(キーが無い場合はFalse/空文字のデフォルト)。

- [ ] **Step 3: `_start_sending`をワーカーごとのpayload関数に対応させる**

現在の`_start_sending`は全ワーカーに同じ`fn`を渡している。GPSが有効なときは、ワーカーごとに`random_offset`で基準点からの出発点をずらし、`make_gps_payload_fn`で作ったクロージャを個別に`fn`と合成する:

```python
def _start_sending(self) -> None:
    base_fn = self._get_payload_fn()
    if base_fn is None:
        return
    interval = self._interval.get()
    gps_enabled = self._gps_enabled.get()
    try:
        base_lat = float(self._gps_base_lat.get()) if gps_enabled else None
        base_lon = float(self._gps_base_lon.get()) if gps_enabled else None
    except ValueError:
        base_lat = base_lon = None
    for w in self._workers.values():
        w.stop_sending()
        if gps_enabled and base_lat is not None and base_lon is not None:
            start_lat, start_lon = random_offset(base_lat, base_lon)
            gps_fn = make_gps_payload_fn(start_lat, start_lon)
            combined_fn = lambda bf=base_fn, gf=gps_fn: {**bf(), **gf()}
            w.start_sending(interval, combined_fn)
        else:
            w.start_sending(interval, base_fn)
```
(`from gps_simulation import random_offset, make_gps_payload_fn`をファイル先頭に追加。既存の`import`群と同じ相対インポート方式に合わせる。)

- [ ] **Step 4: 手動確認**

Run: `cd simulator && python simulator.py`を起動し、GPSセクションにチェックを入れ基準緯度経度を入力、複数デバイスで送信開始したときにエラーが出ないことを目視確認する(GUIなのでpytestでは検証できない)。

- [ ] **Step 5: Windows向けexeの再ビルドとドキュメント注記**

`simulator/build.ps1`を実行してexeを再ビルドし、次回リリース時に`simulator/dist/IoT_Simulator.exe`を忘れずリリースへ添付すること([[feedback_release_attach_simulator_exe]]参照、本タスクでは再ビルドのみ行い、リリース添付は実際にリリースを作る時点で行う)。

- [ ] **Step 6: Commit**

```bash
git add simulator/simulator.py
git commit -m "feat(location): シミュレータGUIにGPS移動シミュレーション設定を追加"
```

## 完了条件

- Task 1〜4, 7: `cd core-api && python -m pytest -q` および `cd simulator && python -m pytest tests/test_gps_simulation.py -v`が全件PASS
- Task 5, 6: HTML構文チェック・`docker compose config --quiet`でエラーが無いこと
- Task 8: GUI起動確認(目視)でエラーが無いこと
