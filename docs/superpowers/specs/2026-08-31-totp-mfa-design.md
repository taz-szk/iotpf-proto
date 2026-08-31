# TOTP MFA 設計書

## 概要

プラットフォーム管理者アカウントおよびテナントユーザーに対して、TOTP（Time-based One-Time Password）ベースの多要素認証を追加する。Google Authenticator などの標準 TOTP アプリに対応。

**目標：**
- パスワードログイン後に 6 桁コードを要求する 2 段階認証
- プラットフォーム管理者が「プラットフォーム全体で必須」「テナント全体で必須」を ON/OFF できる
- TOTP 未設定ユーザーがログインすると強制セットアップ画面へリダイレクト

## 技術スタック

- **バックエンド:** pyotp>=2.9.0（TOTP 生成・検証）
- **フロントエンド:** qrcode.js（CDN）で QR コードをブラウザ描画
- **認証トークン:** 既存 JWT インフラを拡張した partial_token（type: partial_platform / partial_tenant）

---

## アーキテクチャ

### ログインフロー（MFA 有効時）

```
POST /auth/login（パスワード検証）
  ├─ MFA 不要              → {access_token, refresh_token}  ← 既存と同じ
  ├─ MFA 必須 & TOTP 設定済み → {status: "totp_required",       partial_token: "..."}
  └─ MFA 必須 & TOTP 未設定   → {status: "totp_setup_required", partial_token: "..."}

POST /auth/totp/verify       partial_token → {access_token, refresh_token}
GET  /auth/totp/setup        partial_token → {otpauth_uri, secret}
POST /auth/totp/activate     partial_token → {access_token, refresh_token}
DELETE /auth/totp            通常 token + パスワード確認 → TOTP 無効化
```

テナント側は `/tenant-auth/totp/*` として完全対称に実装する。

### partial_token

既存の JWT 生成関数を使い、`type` フィールドに `"partial_platform"` または `"partial_tenant"` をセット。有効期限 10 分。TOTP エンドポイント専用の Dependency（`_require_partial_platform` / `_require_partial_tenant`）で検証し、通常の API エンドポイントでは弾く。

---

## DB 変更

### platform_users テーブル（列追加）

```sql
ALTER TABLE platform_users ADD COLUMN totp_secret  VARCHAR(64);
ALTER TABLE platform_users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT FALSE;
```

### テナントスキーマ users テーブル（列追加）

テナント作成時の init SQL と、既存テナントへの migration スクリプト両方に適用。

```sql
ALTER TABLE "{schema}".users ADD COLUMN totp_secret  VARCHAR(64);
ALTER TABLE "{schema}".users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT FALSE;
```

### mfa_settings テーブル（新規 / public スキーマ）

```sql
CREATE TABLE mfa_settings (
    id                INTEGER PRIMARY KEY DEFAULT 1,
    platform_required BOOLEAN NOT NULL DEFAULT FALSE,
    tenant_required   BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (id = 1)   -- 常に 1 行のみ
);
INSERT INTO mfa_settings VALUES (1, false, false);
```

---

## API エンドポイント

### プラットフォーム管理者（`/auth/*`）

| メソッド | パス | 認証 | 説明 |
|---|---|---|---|
| POST | `/auth/totp/verify` | partial_platform | コード検証 → 通常 JWT 発行 |
| GET | `/auth/totp/setup` | partial_platform | secret 生成・otpauth URI 返却 |
| POST | `/auth/totp/activate` | partial_platform | 初回コード検証 → TOTP 有効化 → JWT 発行 |
| DELETE | `/auth/totp` | 通常 token | TOTP 無効化（パスワード確認必須） |

### テナントユーザー（`/tenant-auth/*`）

| メソッド | パス | 認証 | 説明 |
|---|---|---|---|
| POST | `/tenant-auth/totp/verify` | partial_tenant (Cookie) | コード検証 → Session Cookie 発行 |
| GET | `/tenant-auth/totp/setup` | partial_tenant (Cookie) | secret 生成・otpauth URI 返却 |
| POST | `/tenant-auth/totp/activate` | partial_tenant (Cookie) | 初回検証 → TOTP 有効化 → Cookie 発行 |
| DELETE | `/tenant-auth/totp` | 通常 Cookie | TOTP 無効化（パスワード確認必須） |

### MFA グローバル設定（`/platform/*`）

| メソッド | パス | 認証 | 説明 |
|---|---|---|---|
| GET | `/platform/mfa-settings` | 通常 token | 現在の設定取得 |
| PATCH | `/platform/mfa-settings` | 通常 token | ON/OFF 切り替え |

---

## UI フロー

### 管理者ログイン（`/admin/index.html`）

```
ログイン成功 → status 確認
  "ok"                  → tenants.html（変わらず）
  "totp_required"       → partial_token を sessionStorage 保存 → totp-verify.html
  "totp_setup_required" → partial_token を sessionStorage 保存 → totp-setup.html
```

### `/admin/totp-setup.html`（TOTP 初期設定）

1. partial_token で `GET /auth/totp/setup` → `{otpauth_uri, secret}` 取得
2. qrcode.js で QR コードをブラウザ描画
3. 手動入力用の secret も表示
4. ユーザーが Google Authenticator でスキャン後、6 桁コードを入力
5. `POST /auth/totp/activate` → 成功 → `{access_token, refresh_token}` → tenants.html

### `/admin/totp-verify.html`（通常ログイン 2 段階目）

1. 6 桁コード入力フォーム
2. `POST /auth/totp/verify` → `{access_token, refresh_token}` → tenants.html

### `/admin/platform-settings.html`（MFA グローバル設定）

- 「プラットフォーム管理者に TOTP を必須にする」トグル
- 「テナントユーザーに TOTP を必須にする」トグル
- ナビバーに「設定」リンクを追加

### テナントログイン（`/admin/tenant-login.html`）

管理者側と同構造。partial_token は sessionStorage 経由で `/admin/tenant-totp-verify.html` / `/admin/tenant-totp-setup.html` へ渡す。

---

## ファイル構成

### 新規ファイル

```
core-api/app/routers/mfa.py              プラットフォーム TOTP エンドポイント
core-api/app/routers/tenant_mfa.py       テナント TOTP エンドポイント
core-api/app/routers/platform.py         MFA 設定 CRUD
core-api/app/services/totp.py            pyotp ラッパー（生成・検証・URI）
postgres/init/02_mfa.sql                 mfa_settings テーブル作成
admin-ui/totp-verify.html                TOTP 入力ページ（管理者）
admin-ui/totp-setup.html                 TOTP 初期設定ページ（管理者）
admin-ui/platform-settings.html          MFA ON/OFF 設定ページ
admin-ui/tenant-totp-verify.html         TOTP 入力ページ（テナント）
admin-ui/tenant-totp-setup.html          TOTP 初期設定ページ（テナント）
```

### 変更ファイル

```
core-api/app/routers/auth.py             login レスポンス拡張、partial_token 発行
core-api/app/routers/tenant_auth.py      同上（テナント側）
core-api/app/models/public.py            PlatformUser に列追加、MfaSettings モデル追加
core-api/app/main.py                     新ルーター登録
core-api/requirements.txt               pyotp 追加
postgres/init/01_init.sql               platform_users・users 列追加
admin-ui/js/api.js                       totp / platform エンドポイント追加
admin-ui/index.html                      partial_token 受取り後のリダイレクト処理
admin-ui/tenant-login.html               同上（テナント側）
nginx/conf.d/                            /api/platform/* ルーティング追加（必要に応じ）
```

---

## セキュリティ考慮

- `totp_secret` は DB に平文保存（既存の `password_hash` と同等の扱い）。将来的な暗号化は v2 で検討。
- `activate` 後は `GET /auth/totp/setup` で再度 secret を取得不可（`totp_enabled=true` の場合は 404 を返す）。
- partial_token は `type` フィールドで通常 token と区別し、通常 API エンドポイントの Dependency で弾く。
- コード検証は pyotp の `valid_window=1`（前後 30 秒）を使用し、時刻ズレに対応。

## 互換性

- 既存ユーザーは `totp_enabled = false`。MFA 設定が OFF のうちはログインフロー変化なし。
- `mfa_settings` の初期値は両方 `false` なので、デプロイ直後は既存動作を維持。
