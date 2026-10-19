# 準リアルタイムアクセス機能(InfluxDB読み取り専用トークン) 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナント管理者が、自分のテナントのInfluxDBデータに対する読み取り専用トークンを発行し、外部のBIツール・分析基盤から直接クエリできるようにする。テナント分離はInfluxDBのトークンスコープのみで保証する。

**Architecture:** 既存の`create_influxdb_token_for_org`(読み書き両用、内部サービス用)とは別に、読み取り専用の`InfluxDBオーソリゼーション`をInfluxDB管理APIで発行する。管理画面からの発行・失効はBootstrap Token(`provisioning_tokens.py`)と同じ「発行時一度だけ表示」UXパターンに揃える。外部ツールからの実クエリ経路は、nginxの「何も判定しないただの転送」ロケーションを新設してInfluxDBへ到達させる。

**Tech Stack:** FastAPI (core-api), httpx(InfluxDB管理API呼び出し), SQLAlchemy, nginx, Alpine.js (admin-ui/tenant-portal.html)

**Spec:** `docs/superpowers/specs/2026-10-19-data-export-design.md`(②準リアルタイムアクセス機能の節)。本プランは①エクスポート/ダウンロード機能(別プラン`2026-10-19-data-export-csv.md`)を含まない。

## Global Constraints

- テナント分離は**InfluxDBのトークンスコープ機能のみ**で保証する。アプリケーション側でtenant_idを比較するような独自の判定ロジックは一切置かない
- 発行するトークンは読み取り専用(`action: "read"`)。書き込み権限は付与しない
- 外部クエリ用のnginx経路は、テナント識別・認可ロジックを一切持たない「ただの転送」とする
- 発行・運用はBootstrap Tokenと同じパターン: 発行時に一度だけ画面に表示し、以後は再表示しない。再発行で旧トークンは即座に失効する
- 有効期限は無期限を選択可能(強制的な期限切れは設けない)
- 発行・再発行・失効は`audit_logs`に記録する
- 有効化して使っている間(トークンが発行され失効していない間)、月額固定費`realtime_access_fee`を課金する

## Review Focus

- InfluxDB管理API呼び出し(発行・失効)が失敗した場合、DBの行だけが更新されて実際のInfluxDB側オーソリゼーションが存在しない、という不整合状態を作らない(どちらかが失敗したら両方失敗として扱う)
- 発行済みトークンをテナント管理画面の一覧取得(`GET`)で**再表示しない**(値を含めてレスポンスに出さない)
- 失効済み(revoked)のテナントが、古いnginx経路や古いトークン値で引き続きInfluxDBにアクセスできない(InfluxDB側のオーソリゼーションを実際に`DELETE`している)
- admin以外のロール(operator/viewer)がトークンの発行・失効を行えない

---

### Task 1: DBモデル・マイグレーション

**Files:**
- Create: `core-api/app/models/realtime_access.py`
- Modify: `core-api/app/database.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_realtime_access_models.py`

**Interfaces:**
- Produces: `RealtimeAccessToken`— カラム: `tenant_id`(UUID, PK, FK tenants.id), `influx_authorization_id`(String(64)、InfluxDBオーソリゼーションのID。失効時にこれを使って`DELETE`する), `encrypted_token`(String、`app.services.crypto.encrypt_secret`で暗号化した値。既存の`tenant_soracom_credentials`と同じ「復号して使う必要がある秘密情報」の扱い), `created_at`(DateTime tz, server_default now), `revoked_at`(DateTime tz, nullable)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_realtime_access_models.py
from app.models.realtime_access import RealtimeAccessToken

def test_realtime_access_token_table_name_and_columns():
    assert RealtimeAccessToken.__tablename__ == "realtime_access_tokens"
    row = RealtimeAccessToken(
        tenant_id="11111111-1111-1111-1111-111111111111",
        influx_authorization_id="auth-1",
        encrypted_token="enc-value",
    )
    assert row.influx_authorization_id == "auth-1"
    assert row.revoked_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_realtime_access_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.realtime_access'`

- [ ] **Step 3: Implement the model and migration**

Create `core-api/app/models/realtime_access.py` with `RealtimeAccessToken` per the Interfaces block (follow `core-api/app/models/billing.py`'s style). In `core-api/app/database.py`, add `migrate_create_realtime_access_tokens()` using the exact `CREATE TABLE IF NOT EXISTS` raw-SQL style of `migrate_create_password_reset_tokens()` (`tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE`, `influx_authorization_id VARCHAR(64) NOT NULL`, `encrypted_token TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `revoked_at TIMESTAMPTZ`). Register it in `core-api/app/main.py` (import + `on_startup()` tuple).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_realtime_access_models.py -v`
Expected: PASS (1/1)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/models/realtime_access.py core-api/app/database.py core-api/app/main.py core-api/tests/test_realtime_access_models.py
git commit -m "feat: add realtime_access_tokens model and migration"
```

---

### Task 2: InfluxDB発行/失効サービス・APIエンドポイント

**Files:**
- Create: `core-api/app/services/realtime_access.py`
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_realtime_access_service.py`
- Test: `core-api/tests/test_tenant_portal_realtime_access.py`

**Interfaces:**
- Consumes: `app.services.crypto.encrypt_secret`, `decrypt_secret`(既存)
- Consumes: `app.models.realtime_access.RealtimeAccessToken`(Task 1)
- Consumes: `settings.influxdb_url`, `settings.influxdb_admin_token`(既存、`app.services.tenant`の使い方を参照)
- Produces: `issue_readonly_token(org_id: str) -> tuple[str, str]`(戻り値`(influx_authorization_id, plaintext_token)`)
- Produces: `revoke_readonly_token(influx_authorization_id: str) -> None`
- Produces: `class InfluxAuthorizationError(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_realtime_access_service.py
from unittest.mock import patch, MagicMock
import pytest
from app.services.realtime_access import issue_readonly_token, revoke_readonly_token, InfluxAuthorizationError

def test_issue_readonly_token_posts_read_only_permission_and_returns_id_and_token():
    resp = MagicMock(status_code=201)
    resp.json.return_value = {"id": "auth-123", "token": "plain-token-value"}
    resp.raise_for_status = MagicMock()
    with patch("app.services.realtime_access.httpx.post", return_value=resp) as mock_post:
        auth_id, token = issue_readonly_token("org-1")
    assert (auth_id, token) == ("auth-123", "plain-token-value")
    sent_json = mock_post.call_args.kwargs["json"]
    assert sent_json["permissions"] == [{"action": "read", "resource": {"type": "buckets", "orgID": "org-1"}}]

def test_issue_readonly_token_raises_on_influx_failure():
    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = Exception("boom")
    with patch("app.services.realtime_access.httpx.post", return_value=resp):
        with pytest.raises(InfluxAuthorizationError):
            issue_readonly_token("org-1")

def test_revoke_readonly_token_calls_delete():
    resp = MagicMock(status_code=204)
    resp.raise_for_status = MagicMock()
    with patch("app.services.realtime_access.httpx.delete", return_value=resp) as mock_delete:
        revoke_readonly_token("auth-123")
    assert "auth-123" in mock_delete.call_args.args[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_realtime_access_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.realtime_access'`

- [ ] **Step 3: Implement `core-api/app/services/realtime_access.py`**

`issue_readonly_token(org_id)`: `httpx.post(f"{settings.influxdb_url}/api/v2/authorizations", headers={"Authorization": f"Token {settings.influxdb_admin_token}", "Content-Type": "application/json"}, json={"orgID": org_id, "description": f"readonly-realtime-{org_id}", "permissions": [{"action": "read", "resource": {"type": "buckets", "orgID": org_id}}]}, timeout=10.0)` (mirror `create_influxdb_token_for_org` in `app/services/tenant.py`, but with only the read permission and no write). Wrap the call in `try`/`except` and also check `resp.status_code != 201`; on any failure raise `InfluxAuthorizationError(str(e))`. On success return `(resp.json()["id"], resp.json()["token"])`.

`revoke_readonly_token(influx_authorization_id)`: `httpx.delete(f"{settings.influxdb_url}/api/v2/authorizations/{influx_authorization_id}", headers={"Authorization": f"Token {settings.influxdb_admin_token}"}, timeout=10.0)`; raise `InfluxAuthorizationError` on failure (same pattern as above).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_realtime_access_service.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Write the failing API test**

```python
# core-api/tests/test_tenant_portal_realtime_access.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _token(role="admin"):
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": role})

def _session_ctx():
    db = MagicMock(); db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    return db

def test_issue_token_rejects_non_admin():
    resp = client.post("/tenant-portal/me/realtime-access-token",
                        headers={"Authorization": f"Bearer {_token('viewer')}"})
    assert resp.status_code == 403

def test_issue_token_returns_plaintext_once():
    tenant = MagicMock(influxdb_org_id="org-1")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.issue_readonly_token", return_value=("auth-1", "plain-token")):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.side_effect = [tenant, None]
        mock_session.return_value = db
        resp = client.post("/tenant-portal/me/realtime-access-token",
                            headers={"Authorization": f"Bearer {_token('admin')}"})
    assert resp.status_code == 201
    assert resp.json()["token"] == "plain-token"

def test_get_token_status_never_includes_token_value():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        row = MagicMock(created_at=None, revoked_at=None)
        db.query.return_value.filter.return_value.first.return_value = row
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/realtime-access-token",
                           headers={"Authorization": f"Bearer {_token('viewer')}"})
    assert resp.status_code == 200
    assert "token" not in resp.json()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest core-api/tests/test_tenant_portal_realtime_access.py -v`
Expected: FAIL (routes don't exist yet)

- [ ] **Step 7: Implement the endpoints in `core-api/app/routers/tenant_portal.py`**

Add a `# ---- 準リアルタイムアクセス ----` section. Import `issue_readonly_token`, `revoke_readonly_token`, `InfluxAuthorizationError` from `app.services.realtime_access`, `encrypt_secret` from `app.services.crypto`, `RealtimeAccessToken` from `app.models.realtime_access`.

- `POST /me/realtime-access-token` (`_require_admin`): load the `Tenant`; if a `RealtimeAccessToken` row exists for this tenant already (even if `revoked_at` is set), call `revoke_readonly_token(old_row.influx_authorization_id)` first and delete that row (reissue invalidates the old one — propagate `InfluxAuthorizationError` as a `502` if this fails, since leaving the old InfluxDB authorization alive while discarding our record of it would be the inconsistency the Review Focus calls out). Then call `issue_readonly_token(tenant.influxdb_org_id)`; on `InfluxAuthorizationError`, return `502`. On success, `db.add(RealtimeAccessToken(tenant_id=..., influx_authorization_id=auth_id, encrypted_token=encrypt_secret(token)))`, `log_audit(..., "issue_realtime_access_token", ...)`, `db.commit()`, return `201 {"token": token, "created_at": ...}` (the only response that ever carries the plaintext token).
- `GET /me/realtime-access-token` (`_require_tenant`): return `{"created_at": ..., "revoked_at": ...}` or `{"created_at": None, "revoked_at": None}` if no row — **never** include `token` or `encrypted_token` in this response.
- `DELETE /me/realtime-access-token` (`_require_admin`): load the row; 404 if none; call `revoke_readonly_token(...)`; on `InfluxAuthorizationError` return `502` (do not delete the DB row if the InfluxDB side failed — Review Focus: don't let the DB and InfluxDB state diverge); on success delete the row (or set `revoked_at`), `log_audit(..., "revoke_realtime_access_token", ...)`, `db.commit()`, `204`.

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest core-api/tests/test_tenant_portal_realtime_access.py -v`
Expected: PASS (3/3)

- [ ] **Step 9: Commit**

```bash
git add core-api/app/services/realtime_access.py core-api/app/routers/tenant_portal.py core-api/tests/test_realtime_access_service.py core-api/tests/test_tenant_portal_realtime_access.py
git commit -m "feat: add read-only InfluxDB realtime-access token issue/status/revoke"
```

---

### Task 3: 外部クエリ経路(nginx pure pass-through)とレート制限

**Files:**
- Modify: `nginx/conf.d/rate-limit.conf`
- Modify: `nginx/conf.d/api.conf`

**Interfaces:**
- Consumes: 既存の`internal`ネットワーク(nginxは既にこのネットワークに参加しており、`influxdb`コンテナへ到達できる。docker-composeの変更は不要)

- [ ] **Step 1: Add a rate-limit zone**

In `nginx/conf.d/rate-limit.conf`, add a new zone following the exact style of the existing `login`/`signup` zones: `limit_req_zone $binary_remote_addr zone=influx_query:10m rate=60r/m;` (per-IP, consistent with the existing zones' granularity; per-token limiting is left as a future enhancement, matching the design spec's own deferral of this detail to builder discretion).

- [ ] **Step 2: Add the pass-through location**

In `nginx/conf.d/api.conf`, add a new `location /influx/` block (placed near the other `proxy_pass` blocks, before the general `/api/` block since it's a separate path), mirroring the exact structure of the existing `location = /api/signup` block (same `proxy_set_header` lines, same `proxy_connect_timeout`/`proxy_read_timeout`), but:
  - `limit_req zone=influx_query burst=10 nodelay;`
  - `set $influxdb http://influxdb:8086;`
  - `rewrite ^/influx(/.*) $1 break;`
  - `proxy_pass $influxdb;`

This block does **no** authentication, authorization, or tenant-identification of any kind — it is a transparent forward. The `Authorization: Token <...>` header an external client sends passes through unchanged to InfluxDB, which is the only component that evaluates it (this is the Global Constraint "テナント識別・認可ロジックを一切持たない").

- [ ] **Step 3: Verify nginx config syntax**

Run: `docker compose exec nginx nginx -t` (per this repo's existing debugging workflow documented in `CLAUDE.md`)
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`

If a local nginx container isn't running, this step can't be completed in this task's environment — note that in the task report rather than skipping verification silently.

- [ ] **Step 4: Commit**

```bash
git add nginx/conf.d/rate-limit.conf nginx/conf.d/api.conf
git commit -m "feat: add pure pass-through nginx route for external InfluxDB realtime access"
```

---

### Task 4: Billing連携

**Files:**
- Modify: `core-api/app/services/billing.py`(`ITEM_KEYS`)
- Modify: `core-api/app/services/billing_usage.py`(`aggregate_monthly_usage`)
- Test: `core-api/tests/test_billing_usage_realtime_access.py`

**Interfaces:**
- Consumes: `app.models.realtime_access.RealtimeAccessToken`(Task 1)
- Produces: `aggregate_monthly_usage(...)`の返り値に`"realtime_access_fee"`キーを追加

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_billing_usage_realtime_access.py
from unittest.mock import MagicMock
from app.services.billing_usage import _realtime_access_usage_snapshot

def test_realtime_access_fee_on_when_token_active():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(revoked_at=None)
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 1}

def test_realtime_access_fee_off_when_no_token():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 0}

def test_realtime_access_fee_off_when_revoked():
    from datetime import datetime, timezone
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(
        revoked_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_billing_usage_realtime_access.py -v`
Expected: FAIL with `ImportError: cannot import name '_realtime_access_usage_snapshot'`

- [ ] **Step 3: Implement**

In `core-api/app/services/billing_usage.py`, add:

```python
def _realtime_access_usage_snapshot(db, tenant_id: str) -> dict[str, int]:
```
Query `RealtimeAccessToken` by `tenant_id`; return `{"realtime_access_fee": 1}` if a row exists and `revoked_at is None`, else `{"realtime_access_fee": 0}`.

Merge this into `aggregate_monthly_usage(...)`'s returned dict (same pattern as the export plan's `_export_usage_snapshot` merge, if that plan has already landed — if not, add it as its own `**_realtime_access_usage_snapshot(db, tenant_id)` entry independently; the two snapshots are independent dict merges and don't depend on each other's presence).

In `core-api/app/services/billing.py`, add `"realtime_access_fee"` to the `ITEM_KEYS` tuple.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_billing_usage_realtime_access.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run the full test suite**

Run: `pytest core-api/tests -v`
Expected: all tests pass (no regressions from the `ITEM_KEYS`/`aggregate_monthly_usage` changes)

- [ ] **Step 6: Commit**

```bash
git add core-api/app/services/billing.py core-api/app/services/billing_usage.py core-api/tests/test_billing_usage_realtime_access.py
git commit -m "feat: bill the realtime access token feature via realtime_access_fee"
```

---

### Task 5: 管理画面UI

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: Task 2のAPI(`POST`/`GET`/`DELETE /me/realtime-access-token`)
- Consumes: 既存の`tabs`配列、`portalFetch()`、Bootstrap Token UI(`tokens`タブ、「発行時一度だけ表示」のマークアップ)を参考にする

- [ ] **Step 1: Add the UI inside the existing `tokens` tab**

Rather than a new tab, add a second card within the existing `x-show="activeTab === 'tokens'"` section (the realtime-access token is conceptually close enough to the Bootstrap Token UI that it belongs in the same place a tenant admin already looks for "トークン"管理): a card titled 「準リアルタイムアクセス用トークン(読み取り専用)」showing current status (発行日・有効/失効, from `GET /me/realtime-access-token`), a 発行/再発行 button (admin only, calls `issueRealtimeAccessToken()`), and — mirroring the existing `newTokenValue` display block exactly — a one-time-display banner for the freshly issued plaintext token, plus a 失効 button (admin only, confirm dialog, calls `revokeRealtimeAccessToken()`).

- [ ] **Step 2: Add state fields and methods**

Add `realtimeAccessStatus: {created_at: null, revoked_at: null}`, `newRealtimeAccessToken: ''` to `portalApp()`'s state. Add methods `async loadRealtimeAccessStatus()` (GET, called alongside the existing token-tab load), `async issueRealtimeAccessToken()` (POST, store the response's `token` into `newRealtimeAccessToken`, then reload status — mirror `createToken()`'s structure), `async revokeRealtimeAccessToken()` (confirm dialog + DELETE, mirror `revokeToken()`).

- [ ] **Step 3: Manual verification**

Run the app and confirm in a browser that the 「ブートストラップトークン」タブ now also shows the準リアルタイムアクセス card, that issuing displays the token once, and that reloading the tab (or re-switching to it) no longer shows the plaintext value.

- [ ] **Step 4: Commit**

```bash
git add admin-ui/tenant-portal.html
git commit -m "feat: add realtime-access token issue/revoke UI to tenant portal"
```
