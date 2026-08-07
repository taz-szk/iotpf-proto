# P7: テナントユーザー認証 + Grafana Auth Proxy 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナントユーザーが IoT Platform にログインするだけで、追加認証なしに自分のテナントの Grafana ダッシュボードにシームレスにアクセスできるようにする。

**Architecture:** テナントユーザーが `/tenant-auth/login` でログインすると、JWT を HttpOnly Cookie (`iot_token`) に保存する。ブラウザが `/grafana/` にアクセスした際、nginx の `auth_request` が `/internal/verify-jwt` にサブリクエストを送り、Cookie を検証する。検証 OK なら nginx が `X-WEBAUTH-USER: {email}` ヘッダーを付与し、Grafana の Auth Proxy (GF_AUTH_PROXY_ENABLED) がそのまま自動ログインする。プラットフォーム管理者のログインでも同じ Cookie を発行することで、管理者も Grafana にシームレスにアクセスできる。

**Tech Stack:** FastAPI, SQLAlchemy/raw SQL (per-tenant schema), python-jose JWT, nginx `auth_request` + `auth_request_set`, Grafana Auth Proxy (GF_AUTH_PROXY_ENABLED), Alpine.js + Tailwind CSS (Admin UI)

## Global Constraints

- ベースディレクトリ: `C:\Users\USER\AI-Work\iot-platform\`
- テナントユーザーは既存の per-tenant PostgreSQL スキーマ `tenant_{uuid_no_dashes}.users` に保存済み（UUID のハイフンはアンダースコアに変換: `tenant_xxxxxxxx_xxxx_xxxx_xxxx_xxxxxxxxxxxx`）
- スキーマ名変換: `f"tenant_{tenant_id.replace('-', '_')}"` （`database.py:22` の実装に合わせる）
- JWT ライブラリ: `python-jose` (`from jose import jwt`)。`verify_token` は `services/auth.py:22` に実装済み
- プラットフォーム管理者の JWT: `{"type": "platform", "sub": user_id, "email": email, "token_type": "access"}`
- テナントユーザーの JWT: `{"type": "tenant", "sub": user_id, "email": email, "tenant_id": tenant_id, "role": role, "token_type": "access"}`
- Cookie 名: `iot_token`、HttpOnly・Secure・SameSite=Lax・Path=/
- Grafana Auth Proxy ヘッダー名: `X-WEBAUTH-USER`（メールアドレスを渡す）
- Grafana ロールマッピング: `admin` → `Admin`、`operator` → `Editor`、`viewer` → `Viewer`
- 管理 UI は Alpine.js 3.x + Tailwind CSS CDN、`/admin/js/api.js` の `api` オブジェクトを経由して API コール
- `_require_platform` 依存関数のパターンは `routers/tenants.py:13-17` に準拠する

---

### Task 1: Tenant Auth API（ログイン・ログアウト・verify-jwt）

テナントユーザーと管理者のログイン時に `iot_token` Cookie を発行し、nginx auth_request が使う `GET /auth/verify-jwt` エンドポイントを実装する。

**Files:**
- Modify: `core-api/app/services/auth.py`（`create_access_token` に `expires_delta` 引数を追加）
- Modify: `core-api/app/config.py`（`grafana_session_expire_hours` 設定追加）
- Create: `core-api/app/schemas/tenant_auth.py`
- Create: `core-api/app/routers/tenant_auth.py`
- Modify: `core-api/app/routers/auth.py`（`/auth/verify-jwt` エンドポイント追加 + プラットフォームログイン時の Cookie 発行）
- Modify: `core-api/app/main.py`（tenant_auth ルーター登録）

**Interfaces:**
- Produces:
  - `POST /tenant-auth/login` → `TenantLoginResponse(user_id, email, role, tenant_id, redirect_url)`。Cookie `iot_token` を Set-Cookie で発行。
  - `POST /tenant-auth/logout` → Cookie 削除
  - `GET /auth/verify-jwt` → 200 + レスポンスヘッダー `X-Auth-User: {email}`、または 401

- [ ] **Step 1: `create_access_token` に `expires_delta` 引数を追加する（failing test から）**

`tests/test_auth_service.py` を作成:
```python
from datetime import timedelta
from app.services.auth import create_access_token, verify_token

def test_create_access_token_custom_expiry():
    token = create_access_token({"sub": "u1", "email": "a@b.com"}, expires_delta=timedelta(hours=24))
    payload = verify_token(token)
    assert payload["sub"] == "u1"
    assert payload["token_type"] == "access"

def test_create_access_token_default_expiry():
    token = create_access_token({"sub": "u2", "email": "b@b.com"})
    payload = verify_token(token)
    assert payload is not None
```

- [ ] **Step 2: テストが失敗することを確認**

```
cd C:\Users\USER\AI-Work\iot-platform\core-api
docker exec core-api python -m pytest app/../tests/test_auth_service.py -v 2>&1 | head -30
```

テストファイルが存在しない場合は `docker exec core-api python -c "from app.services.auth import create_access_token; create_access_token({}, expires_delta=None)"` が失敗することを確認。

- [ ] **Step 3: `services/auth.py` を修正**

`core-api/app/services/auth.py` の `create_access_token` を:
```python
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    return jwt.encode({**data, "exp": expire, "token_type": "access"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
```
`from datetime import datetime, timedelta, timezone` はすでにインポート済み。

- [ ] **Step 4: `config.py` に設定追加**

`core-api/app/config.py` の `Settings` クラスに追加:
```python
grafana_session_expire_hours: int = 24
```

- [ ] **Step 5: `schemas/tenant_auth.py` を作成**

```python
from pydantic import BaseModel

class TenantLoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str

class TenantLoginResponse(BaseModel):
    user_id: str
    email: str
    role: str
    tenant_id: str
    redirect_url: str
```

- [ ] **Step 6: テナントログインのテストを書く**

`tests/test_tenant_auth.py` を作成:
```python
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

def _make_tenant(slug="acme", grafana_org_id="2"):
    t = MagicMock()
    t.id = "11111111-1111-1111-1111-111111111111"
    t.slug = slug
    t.status = "active"
    t.grafana_org_id = grafana_org_id
    return t

def test_tenant_login_success():
    tenant = _make_tenant()
    row = MagicMock()
    row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    row.email = "user@acme.com"
    row.password_hash = "$2b$12$..."
    row.role = "viewer"

    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.verify_password", return_value=True):
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = row
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.post("/tenant-auth/login", json={"tenant_slug": "acme", "email": "user@acme.com", "password": "pass"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "user@acme.com"
    assert data["redirect_url"] == "/grafana/?orgId=2"
    assert "iot_token" in resp.cookies

def test_tenant_login_invalid_credentials():
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/tenant-auth/login", json={"tenant_slug": "x", "email": "x@x.com", "password": "x"})
    assert resp.status_code == 401

def test_verify_jwt_no_cookie():
    resp = client.get("/auth/verify-jwt")
    assert resp.status_code == 401
```

- [ ] **Step 7: `routers/tenant_auth.py` を実装**

```python
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import text
from app.schemas.tenant_auth import TenantLoginRequest, TenantLoginResponse
from app.services.auth import verify_password, create_access_token
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.config import settings

router = APIRouter(prefix="/tenant-auth", tags=["tenant-auth"])

@router.post("/login", response_model=TenantLoginResponse)
def tenant_login(req: TenantLoginRequest, response: Response):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(
            Tenant.slug == req.tenant_slug,
            Tenant.status == "active"
        ).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    schema = f"tenant_{str(tenant.id).replace('-', '_')}"
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT id, email, password_hash, role FROM "{schema}".users WHERE email = :email AND is_active = TRUE'),
            {"email": req.email}
        ).fetchone()

    if not row or not verify_password(req.password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    payload = {
        "sub": str(row.id),
        "email": row.email,
        "type": "tenant",
        "tenant_id": str(tenant.id),
        "role": row.role,
    }
    token = create_access_token(
        payload,
        expires_delta=timedelta(hours=settings.grafana_session_expire_hours),
    )
    expire_seconds = settings.grafana_session_expire_hours * 3600
    response.set_cookie(
        key="iot_token", value=token,
        httponly=True, secure=True, samesite="lax",
        max_age=expire_seconds, path="/",
    )
    return TenantLoginResponse(
        user_id=str(row.id),
        email=row.email,
        role=row.role,
        tenant_id=str(tenant.id),
        redirect_url=f"/grafana/?orgId={tenant.grafana_org_id}",
    )

@router.post("/logout")
def tenant_logout(response: Response):
    response.delete_cookie(key="iot_token", path="/")
    return {"ok": True}
```

- [ ] **Step 8: `routers/auth.py` に `/auth/verify-jwt` エンドポイントを追加し、プラットフォームログインにも Cookie 発行を追加**

`auth.py` のインポートに追加: `from fastapi import Request, Response` および `from datetime import timedelta`。

**ログインエンドポイントを修正** (`/auth/login`): `response: Response` パラメータを追加し、Cookie 発行を追加:
```python
@router.post("/login", response_model=TokenOut)
def login(req: LoginRequest, response: Response):
    with SessionLocal() as db:
        user = db.query(PlatformUser).filter(PlatformUser.email == req.email).first()
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    payload = {"sub": str(user.id), "email": user.email, "type": "platform"}
    access = create_access_token(payload)
    refresh = create_refresh_token(payload)
    # Grafana SSO 用 Cookie（24h）
    grafana_token = create_access_token(
        payload,
        expires_delta=timedelta(hours=settings.grafana_session_expire_hours),
    )
    response.set_cookie(
        key="iot_token", value=grafana_token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.grafana_session_expire_hours * 3600, path="/",
    )
    return TokenOut(access_token=access, refresh_token=refresh)
```

**verify-jwt エンドポイントを追加**（同じ `router` に追記）:
```python
@router.get("/verify-jwt")
def verify_jwt(request: Request, response: Response):
    token = request.cookies.get("iot_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token")
    payload = verify_token(token)
    if not payload or payload.get("type") not in ("tenant", "platform") or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    response.headers["X-Auth-User"] = payload["email"]
    return {"email": payload["email"], "type": payload["type"]}
```

- [ ] **Step 9: `main.py` に tenant_auth ルーターを登録**

```python
from app.routers import health, auth, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth

# include_router 一覧に追加:
app.include_router(tenant_auth.router)
```

- [ ] **Step 10: テストを実行して全パスを確認**

```
docker compose build core-api
docker compose up -d core-api
docker exec core-api python -m pytest tests/ -v 2>&1 | tail -20
```

ビルドエラーや ImportError がないことを確認。`/auth/verify-jwt` に Cookie なしでアクセスして 401 を確認:
```
curl -k https://localhost/api/auth/verify-jwt
# → {"detail":"No token"}
```

- [ ] **Step 11: コミット**

```bash
git add core-api/app/services/auth.py core-api/app/config.py core-api/app/schemas/tenant_auth.py core-api/app/routers/tenant_auth.py core-api/app/routers/auth.py core-api/app/main.py
git commit -m "feat(p7): add tenant auth login endpoint and verify-jwt for nginx auth_request"
```

---

### Task 2: テナントユーザー管理 API + Grafana ユーザー同期

プラットフォーム管理者が per-tenant schema にユーザーを作成・一覧取得し、同時に Grafana org にユーザーを追加する API を実装する。

**Files:**
- Modify: `core-api/app/services/grafana.py`（`ensure_grafana_user_in_org` 関数追加）
- Create: `core-api/app/schemas/tenant_users.py`
- Create: `core-api/app/routers/tenant_users.py`
- Modify: `core-api/app/main.py`（tenant_users ルーター登録）

**Interfaces:**
- Consumes: `_require_platform` パターン（`routers/tenants.py:13-17` と同じ実装）
- Consumes: `hash_password` from `services/auth.py`
- Consumes: `ensure_grafana_user_in_org(org_id: int, email: str, grafana_role: str) -> None` （本タスクで作成）
- Produces:
  - `POST /tenants/{tenant_id}/users` → `201 TenantUserOut(id, email, role, is_active, created_at)`
  - `GET /tenants/{tenant_id}/users` → `list[TenantUserOut]`

- [ ] **Step 1: テストを書く**

`tests/test_tenant_users.py` を作成:
```python
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})

def _make_tenant(tid="22222222-2222-2222-2222-222222222222"):
    t = MagicMock()
    t.id = tid
    t.slug = "acme"
    t.status = "active"
    t.grafana_org_id = "3"
    return t

def test_create_tenant_user_success():
    tenant = _make_tenant()
    with patch("app.routers.tenant_users.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_users.engine") as mock_engine, \
         patch("app.routers.tenant_users.ensure_grafana_user_in_org") as mock_grafana:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.post(
            "/tenants/22222222-2222-2222-2222-222222222222/users",
            json={"email": "user@acme.com", "password": "secure1234", "role": "viewer"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "user@acme.com"
    assert data["role"] == "viewer"
    mock_grafana.assert_called_once_with(3, "user@acme.com", "Viewer")

def test_create_tenant_user_unauthorized():
    resp = client.post(
        "/tenants/22222222-2222-2222-2222-222222222222/users",
        json={"email": "x@x.com", "password": "x", "role": "viewer"},
    )
    assert resp.status_code == 403

def test_list_tenant_users_success():
    tenant = _make_tenant()
    with patch("app.routers.tenant_users.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_users.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_row = MagicMock()
        mock_row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        mock_row.email = "user@acme.com"
        mock_row.role = "viewer"
        mock_row.is_active = True
        mock_row.created_at = "2026-01-01T00:00:00"
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        resp = client.get(
            "/tenants/22222222-2222-2222-2222-222222222222/users",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

- [ ] **Step 2: テストが失敗することを確認**

```
docker exec core-api python -m pytest tests/test_tenant_users.py -v 2>&1 | head -20
```

- [ ] **Step 3: `services/grafana.py` に `ensure_grafana_user_in_org` を追加**

```python
import secrets

def ensure_grafana_user_in_org(org_id: int, email: str, grafana_role: str) -> None:
    """Grafana org_id にユーザーを追加する。存在しなければ作成する。"""
    # 1. ユーザー存在確認
    lookup = httpx.get(
        f"{settings.grafana_url}/api/users/lookup?loginOrEmail={email}",
        auth=_admin_auth(), timeout=10.0,
    )
    if lookup.status_code == 404:
        # 2. ユーザー作成（パスワードは使わない — Auth Proxy が認証するため）
        create = httpx.post(
            f"{settings.grafana_url}/api/admin/users",
            auth=_admin_auth(),
            json={"name": email, "email": email, "login": email,
                  "password": secrets.token_hex(16)},
            timeout=10.0,
        )
        create.raise_for_status()
    else:
        lookup.raise_for_status()

    # 3. org に追加（409 = すでにメンバー → 無視）
    add = httpx.post(
        f"{settings.grafana_url}/api/orgs/{org_id}/users",
        auth=_admin_auth(),
        json={"loginOrEmail": email, "role": grafana_role},
        timeout=10.0,
    )
    if add.status_code not in (200, 409):
        add.raise_for_status()
```

- [ ] **Step 4: `schemas/tenant_users.py` を作成**

```python
from pydantic import BaseModel, EmailStr
from datetime import datetime

class TenantUserCreate(BaseModel):
    email: str
    password: str
    role: str = "viewer"  # "admin" | "operator" | "viewer"

class TenantUserOut(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True
```

- [ ] **Step 5: `routers/tenant_users.py` を実装**

```python
import uuid
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from app.schemas.tenant_users import TenantUserCreate, TenantUserOut
from app.models.public import Tenant
from app.database import SessionLocal, engine
from app.services.auth import verify_token, hash_password
from app.services.grafana import ensure_grafana_user_in_org

router = APIRouter(prefix="/tenants/{tenant_id}/users", tags=["tenant-users"])
_bearer = HTTPBearer()

_ROLE_GRAFANA = {"admin": "Admin", "operator": "Editor", "viewer": "Viewer"}

def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload

def _get_active_tenant(tenant_id: str):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id, Tenant.status == "active").first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant

@router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
def create_tenant_user(tenant_id: str, body: TenantUserCreate, _: dict = Depends(_require_platform)):
    if body.role not in _ROLE_GRAFANA:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(_ROLE_GRAFANA)}")
    tenant = _get_active_tenant(tenant_id)
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    user_id = str(uuid.uuid4())
    password_hash = hash_password(body.password)

    with engine.connect() as conn:
        conn.execute(
            text(f'''
                INSERT INTO "{schema}".users (id, email, password_hash, role)
                VALUES (:id, :email, :hash, :role)
            '''),
            {"id": user_id, "email": body.email, "hash": password_hash, "role": body.role}
        )
        conn.commit()

    if tenant.grafana_org_id:
        ensure_grafana_user_in_org(int(tenant.grafana_org_id), body.email, _ROLE_GRAFANA[body.role])

    return TenantUserOut(
        id=user_id, email=body.email, role=body.role,
        is_active=True, created_at="",
    )

@router.get("", response_model=list[TenantUserOut])
def list_tenant_users(tenant_id: str, _: dict = Depends(_require_platform)):
    tenant = _get_active_tenant(tenant_id)
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT id, email, role, is_active, created_at FROM "{schema}".users ORDER BY created_at DESC')
        ).fetchall()
    return [
        TenantUserOut(
            id=str(r.id), email=r.email, role=r.role,
            is_active=r.is_active, created_at=str(r.created_at),
        )
        for r in rows
    ]
```

- [ ] **Step 6: `main.py` に tenant_users ルーターを登録**

```python
from app.routers import ..., tenant_users
app.include_router(tenant_users.router)
```

- [ ] **Step 7: テストを実行してパスを確認**

```
docker compose build core-api && docker compose up -d core-api
docker exec core-api python -m pytest tests/ -v 2>&1 | tail -20
```

- [ ] **Step 8: コミット**

```bash
git add core-api/app/services/grafana.py core-api/app/schemas/tenant_users.py core-api/app/routers/tenant_users.py core-api/app/main.py
git commit -m "feat(p7): add tenant user management API with Grafana org sync"
```

---

### Task 3: nginx auth_request 設定 + Grafana Auth Proxy 有効化

nginx の `/grafana/` location に `auth_request` を追加し、Grafana の Auth Proxy 設定を `docker-compose.yml` に追加する。

**Files:**
- Modify: `nginx/conf.d/api.conf`（auth_request + 内部サブリクエスト location 追加）
- Modify: `docker-compose.yml`（grafana サービスの environment に Auth Proxy 設定追加）

**Interfaces:**
- Consumes: `GET /auth/verify-jwt`（Task 1 で実装済み）。nginx からは `http://core-api:8000/auth/verify-jwt` に接続。
- Produces: nginx が Grafana リクエストに `X-WEBAUTH-USER` ヘッダーを付与して転送。Grafana は Auth Proxy でそのユーザーとして自動ログイン。

- [ ] **Step 1: 現在の動作を確認（変更前ベースライン）**

```
curl -k https://localhost/grafana/ -o /dev/null -w "%{http_code}\n"
# → 302 (Grafanaログインページへリダイレクト) が期待値
```

- [ ] **Step 2: `nginx/conf.d/api.conf` を修正**

`/grafana/` location に `auth_request` を追加し、内部サブリクエスト location と unauthorized 時のリダイレクトを追加する。

**変更後の `api.conf` 全体:**
```nginx
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/certs/server.crt;
    ssl_certificate_key /etc/nginx/certs/server.key;
    ssl_trusted_certificate /etc/nginx/certs/root_ca.crt;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    resolver 127.0.0.11 valid=10s;

    location /api/ {
        set $core_api http://core-api:8000;
        rewrite ^/api(/.*) $1 break;
        proxy_pass $core_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
    }

    location /provision {
        set $core_api http://core-api:8000;
        proxy_pass $core_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Grafana — auth_request で iot_token Cookie を検証してから転送
    location /grafana/ {
        auth_request /internal/verify-jwt;
        auth_request_set $auth_user $upstream_http_x_auth_user;

        proxy_pass http://grafana:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-WEBAUTH-USER $auth_user;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        error_page 401 = @grafana_unauthorized;
    }

    # auth_request 用内部サブリクエスト（外部からアクセス不可）
    location = /internal/verify-jwt {
        internal;
        set $core_api http://core-api:8000;
        proxy_pass $core_api/auth/verify-jwt;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header X-Original-URI $request_uri;
    }

    # Cookie なし → テナントログインページへ
    location @grafana_unauthorized {
        return 302 /admin/tenant-login.html;
    }

    location /admin/ {
        alias /usr/share/nginx/admin-ui/;
        index index.html;
        try_files $uri $uri/ /admin/index.html;
    }
}
```

- [ ] **Step 3: `docker-compose.yml` の grafana サービスに Auth Proxy 設定を追加**

`grafana:` サービスの `environment:` ブロックに追加:
```yaml
      GF_AUTH_PROXY_ENABLED: "true"
      GF_AUTH_PROXY_HEADER_NAME: X-WEBAUTH-USER
      GF_AUTH_PROXY_HEADER_PROPERTY: email
      GF_AUTH_PROXY_AUTO_SIGN_UP: "true"
      GF_AUTH_PROXY_SYNC_TTL: "60"
      GF_AUTH_PROXY_WHITELIST: ""
```

`GF_AUTH_ANONYMOUS_ENABLED: "false"` はすでに設定済みなので変更不要。

- [ ] **Step 4: サービスを再起動して動作確認**

```
docker compose up -d grafana nginx
```

再起動後、Cookie なしのアクセスがテナントログインページにリダイレクトされることを確認:
```
curl -k -s -o /dev/null -w "%{redirect_url}\n" https://localhost/grafana/
# → https://localhost/admin/tenant-login.html が期待値
```

`/internal/verify-jwt` が外部から直接アクセスできないことを確認:
```
curl -k -s -o /dev/null -w "%{http_code}\n" https://localhost/internal/verify-jwt
# → 404 が期待値（internal location は外部から見えない）
```

- [ ] **Step 5: コミット**

```bash
git add nginx/conf.d/api.conf docker-compose.yml
git commit -m "feat(p7): enable nginx auth_request and Grafana Auth Proxy for seamless SSO"
```

---

### Task 4: Admin UI — テナントユーザー管理タブ + テナントログインページ

`tenant.html` に「ユーザー管理」タブを追加し、テナントユーザー用のスタンドアロンログインページ `tenant-login.html` を作成する。

**Files:**
- Modify: `admin-ui/js/api.js`（`api.tenantUsers.list` / `api.tenantUsers.create` を追加）
- Modify: `admin-ui/tenant.html`（「ユーザー管理」タブ追加）
- Create: `admin-ui/tenant-login.html`（テナントユーザー向けログインページ）

**Interfaces:**
- Consumes: `GET /api/tenants/{id}/users` → `list[{id, email, role, is_active, created_at}]`
- Consumes: `POST /api/tenants/{id}/users` body: `{email, password, role}` → 201
- Consumes: `POST /api/tenant-auth/login` body: `{tenant_slug, email, password}` → `{user_id, email, role, tenant_id, redirect_url}`

- [ ] **Step 1: `api.js` に tenantUsers API を追加**

既存の `api.js` を読み込み、`api.alertRules` などと同じパターンで追記する。
`api.tenantUsers` を `api` オブジェクトに追加:
```javascript
tenantUsers: {
  list: (tenantId) => api.request('GET', `/tenants/${tenantId}/users`),
  create: (tenantId, email, password, role) =>
    api.request('POST', `/tenants/${tenantId}/users`, { email, password, role }),
},
```

- [ ] **Step 2: `tenant.html` のタブバーに「ユーザー管理」ボタンを追加**

既存のタブバー（`<div class="flex border-b border-gray-200 mb-6">`）の末尾（ファームウェアタブの後）に追記:
```html
<button @click="activeTab = 'users'; loadUsers()"
        :class="activeTab === 'users' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'"
        class="px-4 py-2 text-sm font-medium">ユーザー管理</button>
```

- [ ] **Step 3: `tenant.html` にユーザー管理タブパネルを追加**

ファームウェアタブの終了 `</div>` の直後に追記:
```html
<!-- ユーザー管理タブ -->
<div x-show="activeTab === 'users'">
  <div class="flex items-center justify-between mb-4">
    <h3 class="font-medium text-gray-700">テナントユーザー</h3>
    <button @click="showUserForm = !showUserForm"
            class="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm">
      + ユーザー追加
    </button>
  </div>

  <div x-show="showUserForm" class="bg-white rounded-xl border border-gray-200 p-4 mb-4">
    <form @submit.prevent="createUser" class="grid grid-cols-2 gap-3">
      <div>
        <label class="block text-xs font-medium text-gray-600 mb-1">メールアドレス</label>
        <input type="email" x-model="newUser.email" required
               class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-600 mb-1">パスワード</label>
        <input type="password" x-model="newUser.password" required minlength="8"
               class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-600 mb-1">ロール</label>
        <select x-model="newUser.role"
                class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
          <option value="viewer">Viewer（閲覧のみ）</option>
          <option value="operator">Operator（編集可）</option>
          <option value="admin">Admin（管理者）</option>
        </select>
      </div>
      <div class="col-span-2 flex justify-end gap-2">
        <button type="button" @click="showUserForm = false"
                class="text-sm text-gray-500 px-3 py-1.5">キャンセル</button>
        <button type="submit"
                class="bg-blue-600 text-white text-sm px-3 py-1.5 rounded">作成</button>
      </div>
    </form>
  </div>

  <div class="bg-white rounded-xl shadow-sm border border-gray-200">
    <div x-show="users.length === 0" class="py-8 text-center text-gray-400 text-sm">ユーザーがいません</div>
    <table x-show="users.length > 0" class="w-full">
      <thead class="bg-gray-50 border-b border-gray-100">
        <tr>
          <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">メール</th>
          <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">ロール</th>
          <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">状態</th>
          <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">作成日</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        <template x-for="u in users" :key="u.id">
          <tr>
            <td class="px-4 py-3 text-sm font-medium" x-text="u.email"></td>
            <td class="px-4 py-3">
              <span class="text-xs px-2 py-0.5 rounded font-medium"
                    :class="{
                      'bg-purple-100 text-purple-700': u.role === 'admin',
                      'bg-blue-100 text-blue-700': u.role === 'operator',
                      'bg-gray-100 text-gray-600': u.role === 'viewer',
                    }"
                    x-text="u.role"></span>
            </td>
            <td class="px-4 py-3 text-sm" x-text="u.is_active ? '有効' : '無効'"></td>
            <td class="px-4 py-3 text-sm text-gray-500"
                x-text="u.created_at ? new Date(u.created_at).toLocaleDateString('ja-JP') : '-'"></td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 4: `tenant.html` の Alpine.js データオブジェクトにユーザー管理の state と関数を追加**

`tenantDetailApp()` の `return { ... }` オブジェクトに追記（`otaDeviceId: '',` の後）:
```javascript
// ユーザー管理
users: [],
showUserForm: false,
newUser: { email: '', password: '', role: 'viewer' },

async loadUsers() {
  try {
    this.users = await api.tenantUsers.list(tenantId);
  } catch(e) {
    this.error = e.message;
  }
},

async createUser() {
  try {
    await api.tenantUsers.create(tenantId, this.newUser.email, this.newUser.password, this.newUser.role);
    this.users = await api.tenantUsers.list(tenantId);
    this.showUserForm = false;
    this.newUser = { email: '', password: '', role: 'viewer' };
  } catch(e) {
    this.error = e.message;
  }
},
```

- [ ] **Step 5: `tenant-login.html` を作成**

テナントユーザー向けのスタンドアロンログインページ。ログイン成功後は `redirect_url` にリダイレクト。

```html
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ダッシュボードログイン - IoT Platform</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
  <div x-data="tenantLoginApp()" class="w-full max-w-sm">
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-8">
      <h1 class="text-xl font-bold text-gray-800 mb-1">ダッシュボードにアクセス</h1>
      <p class="text-sm text-gray-500 mb-6">テナントのメールとパスワードを入力してください</p>

      <div x-show="error" class="bg-red-50 border border-red-200 text-red-600 rounded px-4 py-2 mb-4 text-sm" x-text="error"></div>

      <form @submit.prevent="login" class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">テナントスラッグ</label>
          <input type="text" x-model="form.tenant_slug" required placeholder="your-company"
                 class="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">メールアドレス</label>
          <input type="email" x-model="form.email" required
                 class="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">パスワード</label>
          <input type="password" x-model="form.password" required
                 class="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
        </div>
        <button type="submit" :disabled="loading"
                class="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium py-2 rounded text-sm">
          <span x-show="!loading">ダッシュボードを開く</span>
          <span x-show="loading">ログイン中...</span>
        </button>
      </form>
    </div>
  </div>

  <script>
    function tenantLoginApp() {
      return {
        form: { tenant_slug: '', email: '', password: '' },
        loading: false,
        error: '',

        async login() {
          this.loading = true;
          this.error = '';
          try {
            const resp = await fetch('/api/tenant-auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(this.form),
              credentials: 'include',
            });
            if (!resp.ok) {
              const err = await resp.json().catch(() => ({ detail: 'ログインに失敗しました' }));
              throw new Error(err.detail || 'ログインに失敗しました');
            }
            const data = await resp.json();
            window.location.href = data.redirect_url;
          } catch(e) {
            this.error = e.message;
          } finally {
            this.loading = false;
          }
        },
      };
    }
  </script>
</body>
</html>
```

- [ ] **Step 6: 動作確認**

nginx を reload して Admin UI を確認:
```
docker compose exec nginx nginx -s reload
```

ブラウザで `https://localhost/admin/tenant-login.html` を開き、ログインフォームが表示されることを確認。

`tenant.html?id={tenant_id}` を開き、「ユーザー管理」タブが表示されることを確認。

テナントユーザーが存在しない状態では「ユーザーがいません」と表示されることを確認。

- [ ] **Step 7: エンドツーエンド動作確認**

前提: テスト用テナント `acme` が存在し、Grafana org_id が設定済みであること。

1. Admin UI でテナントユーザーを作成: `user@acme.com` / `testpass1234` / viewer
2. `https://localhost/admin/tenant-login.html` でログイン（slug: acme, email: user@acme.com, password: testpass1234）
3. `/grafana/?orgId=N` にリダイレクトされ、Grafana に自動ログインされることを確認
4. Cookie なしで `https://localhost/grafana/` にアクセスすると `/admin/tenant-login.html` にリダイレクトされることを確認

- [ ] **Step 8: コミット**

```bash
git add admin-ui/js/api.js admin-ui/tenant.html admin-ui/tenant-login.html
git commit -m "feat(p7): add tenant user management tab and tenant login page to admin UI"
```
