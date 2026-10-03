# SORACOM回線管理機能 設計仕様書

## 目的・背景

テナントが契約するSORACOM回線（SIM）を、本プラットフォームの管理画面から確認・操作できるようにする。回線契約はテナントとSORACOM間の直接契約であり、本プラットフォームはSORACOM APIを中継する立場に留まる。あわせて、SIM（IMSI）と本プラットフォーム上のデバイス（device_id）を対応付けて管理できるようにする。

## 対象ユーザー・成功基準

- **テナント管理者（admin）**: SORACOM認証情報を登録し、回線を確認・操作できる。
- **テナント運用者（operator）**: 認証情報には触れないが、回線の確認・操作・デバイスとの紐づけができる。
- **プラットフォーム管理者**: テナント詳細画面で、そのテナントがSORACOM連携済みかどうかをバッジで確認できる（認証情報や回線内容には触れない）。
- 成功基準: テナント管理者がSORACOM認証情報を登録し、運用者が画面から回線一覧を見て、デバイスに紐づけ、有効化/停止・速度クラス変更ができる。

## 前提・制約

- 回線契約はテナント単位（各テナントが自分のSORACOM契約の回線を管理する）。プラットフォーム全体で1つのSORACOMアカウントを共有する構成ではない。
- SORACOM認証情報（Auth Key ID / Auth Key）は、テナント管理者が自分で入力する（PF管理者は代理入力しない）。
- 認証情報はDBに**暗号化して**保存する（平文保存は不可）。
- カバレッジは既定で日本カバレッジ（`api.soracom.io`）。テナントごとにグローバルカバレッジ（`g.api.soracom.io`）も選択できる。
- 回線一覧・状態は、当面は**都度SORACOM APIに問い合わせる**（キャッシュしない）。将来SIM数が増えた場合に、定期同期+DBキャッシュへ切り替えられるよう、ルーター層とSORACOM呼び出し層の間に中間サービス層を挟む。
- 一覧取得は全件取得をしない。SORACOM APIのカーソル方式ページネーション（`last_evaluated_key`）をそのまま利用し、表示範囲+数ページ分だけを読み出す。
- 以下は**スコープ外**（実装しない）:
  - 回線の解約（terminate） — 取り消し不可の破壊的操作のため対象外。
  - 休止（suspend）・standby切替。
  - 信号強度（RSSI/RSRP等） — SORACOM APIでは提供されない（デバイス側モデムの情報であり、SORACOM Air/Subscriber APIのレスポンスに存在しないことを、SORACOM公式リポジトリ`soracom/soracom-cli`のOpenAPI定義（`generators/assets/soracom-api.en.yaml`、`Subscriber`/`SessionStatus`/`Cell`スキーマ）で確認済み）。
  - SMS送信、データ容量バンドル変更。
  - BootstrapTokenの暗号化（別件、保留継続。ただし本機能の暗号化部品は共用できる設計にする）。

## SORACOM APIの仕様（確認済み、2026-07-30版OpenAPI定義に基づく）

出典: SORACOM公式リポジトリ `github.com/soracom/soracom-cli` の `generators/assets/soracom-api.en.yaml`（OpenAPI 3.0、`info.version: 20260730-014026`）。

### 認証

- `POST /v1/auth`
  - リクエスト: `{"authKeyId": "...", "authKey": "...", "tokenTimeoutSeconds": 86400}`（`tokenTimeoutSeconds`は既定86400秒=24時間、最大172800秒=48時間。本機能では既定値を使う）
  - レスポンス: `{"apiKey": "...", "token": "...", "operatorId": "..."}`
  - 失敗時は401。
- 以降のAPI呼び出しは、ヘッダー `X-Soracom-API-Key: <apiKey>` と `X-Soracom-Token: <token>` を付与する。
- ベースURL: 日本カバレッジ `https://api.soracom.io/v1`、グローバルカバレッジ `https://g.api.soracom.io/v1`（テナントのcoverage設定で切り替え）。
- `POST /v1/auth/logout` でトークンを失効できる（本機能では使わない。プロセス内キャッシュのTTL切れに任せる）。

### 回線（Subscriber）一覧・詳細

- `GET /v1/subscribers`（operationId: `listSubscribers`）
  - クエリ: `status_filter`（`active|inactive|ready|instock|shipped|suspended|terminated`、`|`区切りで複数可）、`speed_class_filter`、`tag_name`+`tag_value`+`tag_value_match_mode`、`serial_number_filter`、`limit`（1〜100）、`last_evaluated_key`。
  - レスポンス: `Subscriber`の配列。次ページの有無は、レスポンスヘッダー `x-soracom-next-key` で判定する（値があれば次の`last_evaluated_key`として使う）。
- `GET /v1/subscribers/{imsi}`（operationId: `getSubscriber`）→ `Subscriber`1件。404は「回線が見つからない」。

**`Subscriber`の主なフィールド**（今回利用するもの）: `imsi`、`iccid`、`msisdn`、`simId`、`status`（`ready|active|inactive|standby|suspended|terminated`）、`speedClass`、`groupId`、`moduleType`、`tags`、`expiredAt`／`expiryAction`、`terminationEnabled`、`sessionStatus`（`online`・`lastUpdatedAt`・`ueIpAddress`・`cell`〈基地局情報、CI/eci/LAC。信号強度ではない〉）。信号強度に相当するフィールドは存在しない。

### 操作

- `POST /v1/subscribers/{imsi}/activate`（operationId: `activateSubscriber`）— 本文なし。
- `POST /v1/subscribers/{imsi}/deactivate`（operationId: `deactivateSubscriber`）— 本文なし。
- `POST /v1/subscribers/{imsi}/update_speed_class`（operationId: `updateSpeedClass`）— 本文 `{"speedClass": "s1.standard"}` 等。有効な値はSIMのプラン（サブスクリプション）により異なる（`s1.minimum`〜`s1.8xfast`、`u1.*`、`t1.standard`、`arc.standard`等）。本機能では、対象回線の現在の`speedClass`の値域をそのままUIの選択肢として使う（SORACOM側が返す値をそのまま尊重し、こちらでハードコードしない）。
- いずれも成功時は更新後の`Subscriber`を返す。404は「回線が見つからない」。

## アーキテクチャ

```
[テナントポータルUI]
  tenant-sim.html (新規ページ)
       │  fetch
       ▼
[core-api] routers/tenant_sim.py (新規)
       │  使う
       ▼
services/sim_service.py (新規・中間層)
       │  使う
       ▼
services/soracom_client.py (新規・SORACOM API呼び出し本体)
       │  復号した認証情報を使う
       ▼
services/soracom_credentials.py (新規・認証情報CRUD)
       │  暗号化/復号に使う
       ▼
services/crypto.py (新規・汎用Fernet暗号化ヘルパー)
```

`sim_service.py`を挟む理由: 将来、一覧取得を「都度SORACOM API」から「定期同期+DBキャッシュ」に切り替える際、`routers/tenant_sim.py`のコードを変更せずに済むようにするため。`sim_service.list_lines(tenant_id, filters, cursor)`のような関数シグネチャを、キャッシュ方式に変わっても維持する。

## データモデル

### `public.tenant_soracom_credentials`（新規テーブル）

```sql
CREATE TABLE IF NOT EXISTS tenant_soracom_credentials (
    tenant_id     UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    coverage      VARCHAR(10) NOT NULL DEFAULT 'jp' CHECK (coverage IN ('jp', 'g')),
    auth_key_id   VARCHAR(255) NOT NULL,
    auth_key_enc  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- 行が存在しない＝未連携。
- `auth_key_enc`は`services/crypto.py`で暗号化したAuth Key本体。APIレスポンスには`auth_key_id`の末尾4文字と`coverage`のみ返す（Slack Webhook URLの扱いと同じ方針）。

### テナントスキーマ `sim_bindings`（新規テーブル）

```sql
CREATE TABLE IF NOT EXISTS "{schema}".sim_bindings (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    imsi       VARCHAR(32) NOT NULL UNIQUE,
    iccid      VARCHAR(32),
    device_id  VARCHAR(255) NOT NULL UNIQUE,
    bound_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- 1 SIM : 1 デバイス（`imsi`・`device_id`ともにUNIQUE）。付け替えは「外す→別デバイスに紐づける」の明示2操作。
- `device_id`はほかの既存テーブル（`alert_rules.device_id`等）と同様、`devices.device_id`と同じ文字列を使うが、DB上のFK制約は張らない（アーカイブ時に`device_id`がリネームされる既存の挙動と整合させるため）。
- デバイスのアーカイブ/削除時は、既存の関連データ削除処理に`sim_bindings`からの削除を追加する（SORACOM側の回線には触れない）。
- `iccid`は紐づけ時点の値をキャッシュ表示用に保持する（正のキーは`imsi`）。

## 暗号化（`services/crypto.py`）

- `cryptography`パッケージの`Fernet`（対称鍵暗号、タイムスタンプ付きトークン）を使う。
- 新しい環境変数 `SECRETS_ENCRYPTION_KEY`（Fernet鍵、`Fernet.generate_key()`で生成する32byteのURL-safe base64文字列）を必須設定として`config.py`に追加する（未設定時は起動を拒否。既存の`jwt_secret`等required項目と同じ扱い）。
- 提供関数: `encrypt_secret(plaintext: str) -> str` / `decrypt_secret(ciphertext: str) -> str`。
- インストーラ（`install-aws.sh`・`install-mac.sh`・`install-ubuntu.sh`・`scripts/setup.sh`）の`.env`自動生成箇所に、`SECRETS_ENCRYPTION_KEY`のランダム生成を追加する。
- この暗号化ヘルパーは汎用部品とし、将来BootstrapTokenの暗号化を実施する際にも再利用できるようにする（今回BootstrapToken側の変更は行わない）。

## API（core-api, `/tenant-portal/me/sim/...`）

| メソッド | パス | 権限 | 内容 |
|---|---|---|---|
| GET | `/me/sim/credentials` | admin, operator | `{"configured": bool, "coverage": "jp"\|"g", "auth_key_id_hint": "…1234"}` |
| PUT | `/me/sim/credentials` | **admin** | body: `{"coverage", "auth_key_id", "auth_key"}`。保存前に`POST /auth`を試し、失敗時は422で保存しない。`auth_key_id`と`auth_key`は対（ペア）であり片方だけの更新はできないため、両方省略した場合（`coverage`のみの変更）は認証情報を維持し、片方だけが指定された場合は422にする |
| DELETE | `/me/sim/credentials` | **admin** | 連携解除。`sim_bindings`は削除しない |
| GET | `/me/sim/lines` | admin, operator | query: `status`, `speed_class`, `tag_name`, `tag_value`, `bound`(true/false/未指定), `cursor`, `limit`（既定20・最大100）。応答に`items`（各行に`bound_device_id`/`bound_device_name`を付加）と`next_cursor` |
| GET | `/me/sim/lines/{imsi}` | admin, operator | 回線詳細＋紐づけ情報 |
| POST | `/me/sim/lines/{imsi}/activate` | admin, operator | 本文なし |
| POST | `/me/sim/lines/{imsi}/deactivate` | admin, operator | 本文なし |
| POST | `/me/sim/lines/{imsi}/speed-class` | admin, operator | body: `{"speed_class": "s1.standard"}` |
| POST | `/me/sim/lines/{imsi}/bind` | admin, operator | body: `{"device_id": "..."}`。デバイス不存在→404、デバイスが別SIMと紐づき済み、またはIMSIが別デバイスと紐づき済み→409 |
| DELETE | `/me/sim/lines/{imsi}/bind` | admin, operator | 紐づけ解除 |

- `bound`フィルタと`bound_device_id`/`bound_device_name`の付加は、取得した1ページ分の`imsi`集合に対して`sim_bindings`を1回のクエリでJOINして行う（IMSIごとの追加SORACOM呼び出しはしない）。
- 認証情報未設定時、一覧・操作系のAPIは400「SORACOM連携が設定されていません」を返す。
- SORACOM呼び出しが401（認証情報が無効）・404（回線なし）・5xx（SORACOM側障害）の場合、それぞれ利用者に分かる日本語メッセージに変換する（Webhook URL等と同様、認証情報そのものはエラーメッセージに含めない）。
- 操作（credentials保存/削除、activate/deactivate/speed-class変更、bind/unbind）は`audit_logs`に記録する（IMSI・操作種別・結果。Auth Key本体は記録しない）。

## プラットフォーム管理者側の変更

- `GET /tenants/{id}`（既存API）のレスポンスに `soracom_configured: bool` を1項目追加する。
- `platform-ui/tenant.html`（テナント詳細）に、「SORACOM連携: 連携済み/未連携」のバッジを1つ追加する。

## UI（テナントポータル）

- 新規ページ `admin-ui/tenant-sim.html`。既存の`tenant-portal.html`からナビゲーションリンクを追加する（既存ファイルへの機能追記は行わない。既に61KBと大きいため）。
- 画面構成:
  - 連携設定カード（admin: 認証情報の入力/更新/削除フォーム。operator: 連携状態の表示のみ、未連携なら案内文）。
  - 回線一覧（フィルタ: ステータス・速度クラス・紐づけ有無・タグ。カーソルベースの「次へ」ページング）。
  - 各行に、紐づけデバイス名（未紐づけなら「紐づける」ボタン）、現在のステータス、有効化/停止ボタン、速度クラス変更（セレクトボックス、選択肢はSORACOM応答に基づく）。
- 既存の`/me/devices`タブの各行に、紐づいたSIMがあれば簡易バッジを追加し、クリックでSIM管理ページ（該当IMSIにフィルタした状態）へ遷移する。

## エラーハンドリング方針（まとめ）

- SORACOM側のエラー・例外は、利用者向けには種類ごとの日本語メッセージに変換し、認証情報・トークンなどの秘密情報は含めない（ログにも出さない。既存のSlack/SMTP通知の方針を踏襲）。
- SORACOM APIがタイムアウト・5xxを返した場合は、そのまま502相当として利用者に「SORACOM側で一時的な問題が発生しています」と表示する（自動リトライはしない。利用者が再実行する）。

## テスト方針（概要）

- `services/crypto.py`: 暗号化→復号の往復、鍵未設定時に起動を拒否すること。
- `services/soracom_client.py`: 認証のキャッシュ・期限切れ・401時の再認証、各APIのリクエスト/レスポンスの整形、エラーの日本語化（実際のSORACOM APIはモックする）。
- `services/sim_service.py`: ページネーションの中継、`sim_bindings`とのJOIN、フィルタの組み合わせ。
- `routers/tenant_sim.py`: 権限（admin限定operation の403化）、bind/unbindの重複エラー（409）、認証情報未設定時の400。
- 実サービスでの確認は、本物のSORACOM認証情報を使わず、`httpx`呼び出しをモックしたうえでローカルDocker上のcore-apiに対して行う（既存のSlack/メール機能の確認方法と同様）。実際のSORACOM契約を使った動作確認は、ユーザー側で別途行う。

## 既存コードとの関連

- 権限チェックは既存の`_require_admin`/`_require_admin_or_operator`（`tenant_portal.py`内のデコレータ関数）と同じパターンを使う。
- 監査ログは既存の`services/audit.py`の`write_audit_log`/`log_audit`をそのまま使う。
- デバイスのアーカイブ/削除処理（`tenant_portal.py`内、既存の`alert_rules`等の関連データ削除箇所）に、`sim_bindings`削除の1行を追加する。
