# テナントデバイス一覧タブ 設計ドキュメント

**日付:** 2026-08-07  
**対象:** Admin UI — テナント詳細画面にデバイス一覧 + 死活状態タブを追加する

---

## Goal

`tenant.html` の既存タブ群（概要・アラートルール・ファームウェア・ユーザー管理）に「デバイス」タブを追加し、テナントごとの登録デバイス一覧と死活状態を 30 秒ごとに自動リフレッシュして表示する。

---

## Architecture

### データソース

デバイス情報は各テナントの PostgreSQL スキーマ（`tenant_{uuid_no_dashes}.devices`）にすでに存在する。`ingestion-service` が受信のたびに `connection_status` と `last_seen_at` を更新しており、追加の集計処理は不要。

### Backend

`GET /tenants/{tenant_id}/devices` を新規ルーター `core-api/app/routers/tenant_devices.py` に実装する。

- 認証: `platform` JWT 必須（`tenant_users.py` の `_require_platform` と同パターン）
- パスパラメータ: `tenant_id: UUID`（FastAPI が自動検証、不正値は 422）
- レスポンス: デバイス一覧の JSON 配列

```
GET /tenants/{tenant_id}/devices
Authorization: Bearer <platform_token>

200 OK
[
  {
    "id": "uuid",
    "device_id": "device-001",
    "connection_status": "online",   // "online" | "offline" | "unknown"
    "last_seen_at": "2026-08-07T12:00:00Z",  // null 可
    "fw_version": "1.2.3",           // null 可
    "cert_not_after": "2027-01-01T00:00:00Z", // null 可
    "created_at": "2026-01-01T00:00:00Z"
  },
  ...
]
```

- 並び順: `created_at DESC`
- テナントが存在しない場合: 404
- `grafana_org_id` のような外部サービス依存なし — DB のみ参照

### Frontend

`admin-ui/tenant.html` に Alpine.js コンポーネントとして追加。

- タブボタン「デバイス」をクリック時に即時ロード
- `setInterval` で 30 秒ごと自動リフレッシュ（タブ表示中のみ — `activeTab === 'devices'` の場合のみ更新）
- 手動「更新」ボタン + ローディングスピナー
- `api.tenantDevices.list(tenantId)` を `admin-ui/js/api.js` に追加

---

## 表示仕様

| カラム | 表示内容 |
|---|---|
| デバイス ID | `device_id` テキスト |
| 状態 | バッジ（🟢 online / 🔴 offline / ⚪ unknown）|
| 最終通信 | 相対時間（「2分前」など）、`title` 属性に ISO 絶対時刻 |
| FW バージョン | テキスト、未設定なら `-` |
| 証明書期限 | 日付（残り 7 日以内は黄色警告、期限切れは赤）|
| 登録日時 | 日付 |

空状態（デバイス 0 件）: 「デバイスが登録されていません」メッセージを表示。

---

## ファイル一覧

| 操作 | パス |
|---|---|
| 新規作成 | `core-api/app/routers/tenant_devices.py` |
| 変更 | `core-api/app/main.py` |
| 変更 | `admin-ui/js/api.js` |
| 変更 | `admin-ui/tenant.html` |

テスト:
| 新規作成 | `core-api/tests/test_tenant_devices.py` |

---

## Global Constraints

- Alpine.js 3.x + Tailwind CSS CDN（既存ページと同じスタック）
- `platform` JWT のみ認証（tenant JWT は不可）
- スキーマ名: `f"tenant_{str(tenant_id).replace('-', '_')}"`
- パスパラメータ `tenant_id: UUID` で FastAPI 自動検証（422 on bad input）
- 既存の `_require_platform` パターンに従う（`token_type != "access"` を拒否）
