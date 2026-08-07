# テナントデバイス一覧タブ 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin UI のテナント詳細画面に「デバイス」タブを追加し、登録デバイスの一覧と死活状態（connection_status / last_seen_at）を 30 秒ごとに自動リフレッシュして表示する。

**Architecture:** `GET /tenants/{tenant_id}/devices` を新規ルーター `tenant_devices.py` に実装（`tenant_users.py` と同パターン）し、`tenant.html` の Alpine.js コンポーネントに `devices` タブと `setInterval` による自動リフレッシュを追加する。

**Tech Stack:** FastAPI / SQLAlchemy / PostgreSQL（バックエンド）、Alpine.js 3.x + Tailwind CSS CDN（フロントエンド）

## Global Constraints

- `tenant_id` パスパラメータは `UUID` 型（FastAPI が自動検証、不正値は 422）
- スキーマ名: `f"tenant_{str(tenant_id).replace('-', '_')}"`（ダッシュ→アンダースコア）
- `_require_platform` の token_type チェックは `payload.get("token_type") != "access"` を使う（`== "refresh"` は不可）
- Alpine.js 3.x + Tailwind CSS CDN（既存ページと同じスタック）
- `api.js` の `tenantDevices.list` を通じてフロントが呼ぶ

---

### Task 1: バックエンド API — `GET /tenants/{tenant_id}/devices`

**Files:**
- Create: `core-api/app/routers/tenant_devices.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_tenant_devices.py`

**Interfaces:**
- Produces: `GET /tenants/{tenant_id}/devices` → `list[DeviceOut]`
  ```
  DeviceOut {
    id: str
    device_id: str
    connection_status: str   # "online" | "offline" | "unknown"
    last_seen_at: str | None  # ISO 8601 UTC、null 可
    fw_version: str | None
    cert_not_after: str | None  # ISO 8601 UTC、null 可
    created_at: str
  }
  ```

- [ ] **Step 1: テスト用ファイルを作成して失敗させる**

`core-api/tests/test_tenant_devices.py` を作成：

```python
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})

def _make_tenant(tid="33333333-3333-3333-3333-333333333333"):
    t = MagicMock()
    t.id = tid
    t.status = "active"
    return t

def test_list_tenant_devices_empty():
    tenant = _make_tenant()
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        resp = client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == []

def test_list_tenant_devices_returns_rows():
    from datetime import datetime, timezone
    tenant = _make_tenant()
    now = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
    mock_row = MagicMock()
    mock_row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    mock_row.device_id = "device-001"
    mock_row.connection_status = "online"
    mock_row.last_seen_at = now
    mock_row.fw_version = "1.2.3"
    mock_row.cert_not_after = None
    mock_row.created_at = now
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        resp = client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["device_id"] == "device-001"
    assert data[0]["connection_status"] == "online"
    assert data[0]["fw_version"] == "1.2.3"
    assert data[0]["cert_not_after"] is None

def test_list_tenant_devices_unauthorized():
    resp = client.get("/tenants/33333333-3333-3333-3333-333333333333/devices")
    assert resp.status_code == 403

def test_list_tenant_devices_schema_name():
    """スキーマ名変換（UUID のダッシュ→アンダースコア）を検証する"""
    tenant = _make_tenant()
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    sql = str(mock_conn.execute.call_args[0][0])
    assert '"tenant_33333333_3333_3333_3333_333333333333".devices' in sql

def test_list_tenant_devices_not_found():
    with patch("app.routers.tenant_devices.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.get(
            "/tenants/33333333-3333-3333-3333-333333333333/devices",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
cd core-api
python -m pytest tests/test_tenant_devices.py -v
```

Expected: `ImportError: cannot import from 'app.routers.tenant_devices'`（モジュール未作成）

- [ ] **Step 3: `tenant_devices.py` を実装する**

`core-api/app/routers/tenant_devices.py` を作成：

```python
from uuid import UUID
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import text
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.services.auth import verify_token

router = APIRouter(prefix="/tenants/{tenant_id}/devices", tags=["tenant-devices"])
_bearer = HTTPBearer()


class DeviceOut(BaseModel):
    id: str
    device_id: str
    connection_status: str
    last_seen_at: Optional[str] = None
    fw_version: Optional[str] = None
    cert_not_after: Optional[str] = None
    created_at: str


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _get_active_tenant(tenant_id_str: str):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.id == tenant_id_str, Tenant.status == "active"
        ).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.get("", response_model=list[DeviceOut])
def list_tenant_devices(tenant_id: UUID, _: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'''
                SELECT id, device_id, connection_status, last_seen_at,
                       fw_version, cert_not_after, created_at
                FROM "{schema}".devices
                ORDER BY created_at DESC
            ''')
        ).fetchall()
    return [
        DeviceOut(
            id=str(r.id),
            device_id=r.device_id,
            connection_status=r.connection_status,
            last_seen_at=r.last_seen_at.isoformat() if r.last_seen_at else None,
            fw_version=r.fw_version,
            cert_not_after=r.cert_not_after.isoformat() if r.cert_not_after else None,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
```

- [ ] **Step 4: `main.py` にルーターを登録する**

`core-api/app/main.py` の import 行と include_router 行を変更：

```python
# import 行（末尾に tenant_devices を追加）
from app.routers import health, auth, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth, tenant_users, tenant_devices

# ファイル末尾に追加（tenant_users.router の後）
app.include_router(tenant_devices.router)
```

- [ ] **Step 5: テストが通ることを確認する**

```bash
python -m pytest tests/test_tenant_devices.py -v
```

Expected: 5 tests PASS

- [ ] **Step 6: コミットする**

```bash
git add core-api/app/routers/tenant_devices.py core-api/app/main.py core-api/tests/test_tenant_devices.py
git commit -m "feat: add GET /tenants/{id}/devices endpoint"
```

---

### Task 2: フロントエンド — デバイスタブ + 自動リフレッシュ

**Files:**
- Modify: `admin-ui/js/api.js`
- Modify: `admin-ui/tenant.html`

**Interfaces:**
- Consumes: `api.tenantDevices.list(tenantId)` → Task 1 の `GET /tenants/{id}/devices` を呼ぶ
- Task 1 の `DeviceOut` 型と一致するフィールドを参照: `device_id`, `connection_status`, `last_seen_at`, `fw_version`, `cert_not_after`, `created_at`

- [ ] **Step 1: `api.js` に `tenantDevices.list` を追加する**

`admin-ui/js/api.js` の `api.devices` スタブを置き換える：

現在のコード（45–48行目付近）:
```javascript
    devices: {
        // accessed via raw SQL from tenant schema — future endpoint
    },
```

置き換え後:
```javascript
    tenantDevices: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/devices`),
    },
```

- [ ] **Step 2: `tenant.html` のタブボタン行にデバイスタブを追加する**

既存のタブボタン群（`class="flex border-b border-gray-200 mb-6"` の `<div>` 内）の末尾、`ユーザー管理` ボタンの直後に追加：

```html
        <button @click="activeTab = 'devices'; startDevicesRefresh()"
                :class="activeTab === 'devices' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'"
                class="px-4 py-2 text-sm font-medium">デバイス</button>
```

- [ ] **Step 3: デバイスタブのパネル HTML を追加する**

ファームウェアタブ（`x-show="activeTab === 'firmware'"` の `<div>`）の直前に挿入：

```html
      <!-- デバイスタブ -->
      <div x-show="activeTab === 'devices'">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-medium text-gray-700">登録デバイス</h3>
          <button @click="loadDevices()" :disabled="devicesLoading"
                  class="text-sm text-blue-600 hover:text-blue-800 disabled:text-blue-300">
            <span x-show="!devicesLoading">↻ 更新</span>
            <span x-show="devicesLoading">更新中...</span>
          </button>
        </div>
        <div class="bg-white rounded-xl shadow-sm border border-gray-200">
          <div x-show="!devicesLoading && devices.length === 0"
               class="py-8 text-center text-gray-400 text-sm">デバイスが登録されていません</div>
          <table x-show="devices.length > 0" class="w-full text-sm">
            <thead class="bg-gray-50 border-b border-gray-100">
              <tr>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">デバイスID</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">状態</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">最終通信</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">FWバージョン</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">証明書期限</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">登録日時</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <template x-for="d in devices" :key="d.id">
                <tr>
                  <td class="px-4 py-3 font-mono text-xs" x-text="d.device_id"></td>
                  <td class="px-4 py-3">
                    <span class="text-xs px-2 py-0.5 rounded font-medium"
                          :class="{
                            'bg-green-100 text-green-700': d.connection_status === 'online',
                            'bg-red-100 text-red-700':   d.connection_status === 'offline',
                            'bg-gray-100 text-gray-500': d.connection_status === 'unknown',
                          }"
                          x-text="d.connection_status"></span>
                  </td>
                  <td class="px-4 py-3 text-gray-500"
                      :title="d.last_seen_at ?? ''"
                      x-text="timeAgo(d.last_seen_at)"></td>
                  <td class="px-4 py-3 text-gray-500" x-text="d.fw_version ?? '-'"></td>
                  <td class="px-4 py-3"
                      :class="certClass(d.cert_not_after)"
                      x-text="d.cert_not_after ? new Date(d.cert_not_after).toLocaleDateString('ja-JP') : '-'"></td>
                  <td class="px-4 py-3 text-gray-400"
                      x-text="new Date(d.created_at).toLocaleDateString('ja-JP')"></td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <p class="text-xs text-gray-400 mt-2 text-right">30秒ごとに自動更新</p>
      </div>
```

- [ ] **Step 4: Alpine.js の state と関数を追加する**

`<script>` 内の `tenantDetailApp()` が返すオブジェクトに追加する。

**state（既存の `users: [], showUserForm: false, ...` の直後に追加）:**

```javascript
        // デバイス
        devices: [],
        devicesLoading: false,
        _devicesTimer: null,
```

**ヘルパー関数（`createUser()` の後、`loadFirmware()` の前に追加）:**

```javascript
        timeAgo(isoStr) {
          if (!isoStr) return '-';
          const diff = Date.now() - new Date(isoStr).getTime();
          const mins = Math.floor(diff / 60000);
          if (mins < 1) return '今';
          if (mins < 60) return `${mins}分前`;
          const hrs = Math.floor(mins / 60);
          if (hrs < 24) return `${hrs}時間前`;
          return `${Math.floor(hrs / 24)}日前`;
        },

        certClass(certNotAfter) {
          if (!certNotAfter) return 'text-gray-400';
          const days = (new Date(certNotAfter) - Date.now()) / 86400000;
          if (days < 0)  return 'text-red-600 font-medium';
          if (days < 7)  return 'text-yellow-600 font-medium';
          return 'text-gray-500';
        },

        async loadDevices() {
          this.devicesLoading = true;
          try {
            this.devices = await api.tenantDevices.list(tenantId);
          } catch(e) {
            this.error = e.message;
          } finally {
            this.devicesLoading = false;
          }
        },

        startDevicesRefresh() {
          if (this._devicesTimer) clearInterval(this._devicesTimer);
          this.loadDevices();
          this._devicesTimer = setInterval(() => {
            if (this.activeTab === 'devices') this.loadDevices();
          }, 30000);
        },
```

- [ ] **Step 5: ブラウザで動作確認する**

1. `docker compose build core-api && docker compose up -d core-api` で API を更新する
2. ブラウザで `https://{PLATFORM_DOMAIN}/admin/tenant.html?id={テナントID}` を開く
3. 「デバイス」タブをクリックして一覧が表示されることを確認する
4. `connection_status` が `online` のデバイスが緑バッジで表示されることを確認する
5. 30 秒待って自動リフレッシュされることを確認する（または `↻ 更新` ボタンで即時確認）

- [ ] **Step 6: コミットする**

```bash
git add admin-ui/js/api.js admin-ui/tenant.html
git commit -m "feat: add devices tab with auto-refresh to tenant detail page"
```
