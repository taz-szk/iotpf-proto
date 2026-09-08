# デバイスグループ管理機能 設計仕様書

## 概要

テナント配下のデバイスを「グループ」にまとめて管理できるようにする。アラートルール・ファームウェアOTA配信・ダッシュボードパネル設定を、デバイス単位／グループ単位／テナント全体の3段階で扱えるようにする。

**背景・目的:** 拠点ごと・機種ごとなど、複数デバイスをまとめて運用したいという要望。現状はアラートルールが「特定デバイス」か「全デバイス」の2択しかなく、ファームウェア配信も1台ずつ手動、ダッシュボードのパネル設定もテナント全体で共通のため、デバイス群によって取得センサーが異なるようなケースに対応できない。

## スコープ

**含む:**
- グループの作成・編集・削除(PF管理者・テナント管理者の両方が操作可能)
- デバイスのグループ割当(1デバイス=1グループ、未所属可)
- アラートルールのグループ対象化
- ファームウェアOTAのグループ一括配信
- ダッシュボードパネル設定のグループ別上書き
- 上記操作の監査ログ記録

**含まない(将来検討):**
- グループの階層化(親子関係)
- デバイスの複数グループ所属(タグ的な使い方)
- 公開ダッシュボード(`public_access.py`)側のグループ対応
- 実DBを使った統合テストの整備(既存の監査ログ機能で積み残した課題と合わせて次スプリント検討)

## データモデル

### 新規テーブル: `tenant_{id}.device_groups`

```sql
CREATE TABLE device_groups (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100) NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
)
```

既存の `devices` / `alert_rules` / `firmware_releases` と同じくテナントスキーマ内に配置する(テナントごとに動的生成されるスキーマの一部)。

### `devices` テーブルへの列追加

```sql
ALTER TABLE devices ADD COLUMN group_id UUID REFERENCES device_groups(id) ON DELETE SET NULL;
```

同一スキーマ内の参照なのでDB外部キーを張れる。グループを削除してもデバイス自体は消えず「未所属」に戻る。

### `alert_rules` テーブルへの列追加

```sql
ALTER TABLE alert_rules ADD COLUMN group_id UUID;
```

既存の `device_id`(VARCHAR、DB外部キーなし・アプリ層で検証)と同じ緩い結合方針に合わせ、`group_id` にもDB外部キーは張らない。

**排他ルール:** `device_id` と `group_id` は同時に設定不可(アプリ層でバリデーション)。両方NULLなら従来通りテナント全体が対象。

### `public.dashboard_panel_configs` テーブルへの列追加

```sql
ALTER TABLE dashboard_panel_configs ADD COLUMN group_id UUID;
ALTER TABLE dashboard_panel_configs DROP CONSTRAINT dashboard_panel_configs_tenant_id_sensor_key_key;
ALTER TABLE dashboard_panel_configs ADD CONSTRAINT dashboard_panel_configs_tenant_group_sensor_key_key
    UNIQUE (tenant_id, group_id, sensor_key);
```

`group_id` はテナントスキーマ内のテーブルを指すためDB外部キーは張らない(クロススキーマ参照の制約上)。PostgresのUNIQUE制約はNULL同士を別行として扱うため、`group_id IS NULL` の行は「テナント全体のデフォルト設定」として1テナント1センサーキーにつき1行のみ持たせる想定(アプリ層で担保)。

## API設計

### グループCRUD(新規)

- PF管理者: `GET/POST/PATCH/DELETE /tenants/{tenant_id}/groups`
- テナント管理者: `GET/POST/PATCH/DELETE /tenant-portal/groups`

`DELETE` は対象グループを参照しているアラートルールが1件でもあれば `409 Conflict` を返し、レスポンスボディに該当ルールの一覧(`id`, `sensor_key`, `condition`, `severity` など識別に必要な情報)を含める。UIはこれをそのまま「先に削除・変更してください」の一覧表示に使う。

### デバイスのグループ割当(新規)

デバイスの編集APIが現状存在しないため新設する。

- `PATCH /tenants/{tenant_id}/devices/{device_id}` body `{group_id: uuid | null}`
- `PATCH /tenant-portal/devices/{device_id}` 同様(自テナントのみ)

### アラートルール(既存拡張)

`AlertRuleCreate` / `AlertRuleUpdate` に `group_id: str | None` を追加。`device_id` と `group_id` の排他バリデーションを追加。

### OTA一括送信(新規、既存の1台ずつのエンドポイントは維持)

- `POST /tenants/{tenant_id}/groups/{group_id}/ota` body `{firmware_id}`
- `POST /tenant-portal/groups/{group_id}/ota` 同様

グループ内の全デバイスに対し、既存の単体OTA送信ロジックをループ実行する。一部のデバイスへの送信が失敗しても他デバイスへの送信は継続し、レスポンスで成功/失敗の内訳(デバイスIDごとの結果)を返す。

### ダッシュボードパネル設定(既存拡張)

`GET/PUT /tenant-portal/dashboard/panel-configs` に `group_id` クエリパラメータを追加(省略時はテナント共通のデフォルト設定 `group_id IS NULL` を参照)。

## 監査ログ連携

既存の監査ログ機能([[project_audit_log_feature]])にそのまま統合する。

- `create_device_group` / `update_device_group` / `delete_device_group` — `resource_type="device_group"`
- `assign_device_group` — デバイスのグループ変更。`resource_id`はdevice_id、`detail`に旧グループID→新グループIDを記録
- OTA一括送信は新アクションを作らず、既存の `ota_send` を対象デバイスの数だけ個別に記録する(1台ずつの手動送信と同じ粒度にすることで、監査ログ画面のフィルタ・表示が既存の作り込みをそのまま使える)

## UI変更

**テナントポータル(`admin-ui/tenant-portal.html`):**
- 新タブ「グループ管理」— 一覧・作成・編集・削除
- デバイス一覧タブに「グループ」列を追加、行から所属グループを変更可能
- アラートルール作成フォームに対象選択(デバイス / グループ / 全体)を追加
- ファームウェアタブに、グループを選んで一括OTA送信するUIを追加
- ダッシュボード設定タブにグループセレクタを追加(選択したグループのパネル設定を編集)

**PF管理者画面(`platform-ui/tenant.html`):**
- テナント詳細画面のデバイス一覧に同様のグループ管理UIを追加

## エッジケース

- **グループ削除:** 対象アラートルールが存在する場合は409でブロックし、該当ルールの一覧を返す(上記API設計を参照)。デバイスは自動的に「未所属」に戻る(`ON DELETE SET NULL`)。
- **デバイスをグループから外す:** そのデバイスに紐づいていたグループ対象アラートルールは、単に評価対象から外れるだけ(ルール自体は削除されない)。
- **OTA一括送信の部分失敗:** 1台でも失敗があっても処理を止めない。成功/失敗をデバイス単位でレスポンスに含める。

## テスト方針

- 単体テスト(モックベース、既存の監査ログ機能のテストスタイルを踏襲):
  - グループCRUD(作成・一覧・編集・削除)
  - グループ削除ブロック(依存ルールがある場合に409+一覧が返ること)
  - デバイスのグループ割当
  - アラートルールの `device_id`/`group_id` 排他バリデーション
  - OTA一括送信の部分失敗ハンドリング
  - 監査ログ記録(各アクションが正しいtenant_id/resource_type/resource_idで記録されること)
- 実DBを使った統合テストは今回のスコープ外(既存の監査ログ機能で積み残した課題と合わせて別途検討)
