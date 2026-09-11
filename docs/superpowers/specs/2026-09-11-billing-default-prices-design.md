# デフォルト料金テーブル管理機能 設計仕様書

**Goal:** PF管理者が「テナント開通時の共通デフォルト単価」を一括で管理できるようにし、新規テナント作成時にその値が自動的にそのテナントの単価として設定されるようにする。既存のテナント個別の単価設定・上書き機能はそのまま有効に保つ。

**背景:** [[project_tenant_billing_future]] — 課金機能Phase 1/2完了後、テナントごとに毎回手動で単価を設定する運用負荷を減らすため、開通時に妥当な初期値が自動で入る仕組みが欲しいという要望（2026-09-11）。

**スコープ外（今回やらない）:**
- デフォルト自体の変更履歴管理（`effective_from`のような時系列管理） — デフォルトは常に「現在値」のみを保持する
- 既存テナントへのデフォルト遡及適用・「デフォルトにリセット」機能
- テナントごとの単価設定・上書き機能自体の変更（既存の`/tenants/{tenant_id}/billing/prices`はそのまま）

---

## 1. データモデル

新規テーブル `billing_default_unit_prices`（`public`スキーマ、テナント非依存のグローバル設定）。

```sql
CREATE TABLE IF NOT EXISTS billing_default_unit_prices (
    item_key    VARCHAR(50) PRIMARY KEY,
    unit_price  NUMERIC(12,4) NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`item_key`は既存の`app.services.billing.ITEM_KEYS`（`base_fee`/`data_points`/`device_count`/`provisionable_devices`/`alert_events`）のいずれか。`unit_price`のみを保持し、履歴（`effective_from`）は持たない — PF管理者が値を変更すると、その時点で以後の新規テナント開通に即座に反映される（既存テナントには影響しない。§2参照）。

SQLAlchemyモデルは `core-api/app/models/billing.py` に追加:

```python
class BillingDefaultUnitPrice(Base):
    __tablename__ = "billing_default_unit_prices"
    item_key = Column(String(50), primary_key=True)
    unit_price = Column(Numeric(12, 4), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

マイグレーションは既存の`migrate_create_billing_tables()`と同様のべき等パターンで`core-api/app/database.py`に追加する（新規マイグレーション関数として分離するか既存に追記するかは実装計画で判断）。

---

## 2. テナント開通時の反映ロジック

`core-api/app/routers/tenants.py`の`create_tenant()`（`POST /tenants`）内、テナント行のcommit後に、`billing_default_unit_prices`の全行を新規テナントの`billing_unit_prices`へコピーする新規サービス関数を呼ぶ。

```python
def seed_tenant_default_prices(db: Session, tenant_id: str) -> None:
    """テナント開通時にデフォルト単価をそのテナントの単価として一括設定する。
    既存のset_unit_price()が課す「変更は翌月からのみ適用」ルールはここでは適用しない
    （これは初期値の設定であり「変更」ではないため）。デフォルトが1件も無ければ何もしない。"""
```

- `effective_from`は「テナント作成日を含む月の1日」（`date.today().replace(day=1)`）とし、開通直後の当月から利用量計算に反映される。
- `billing_default_unit_prices`に存在しない`item_key`はコピーされない（その項目は既存の挙動と同じく単価未設定＝0円として明細行に出力される）。
- `set_unit_price()`の直接呼び出しではなく、`BillingUnitPrice`行を直接構築してINSERTする（`set_unit_price()`は`effective_from`が当月以前だと`InvalidEffectiveDateError`を出すため、そのまま使うと開通時に必ず失敗する）。
- テナント作成が失敗（ロールバック）した場合、単価コピーもロールバックされる必要がある。既存の`create_tenant()`は1つの`with SessionLocal() as db:`ブロック内で`tenant`のINSERTと`db.commit()`を行っているため、単価コピーも同じ`db`セッション・同じcommit前に行い、同一トランザクションに含める。

---

## 3. API

`core-api/app/routers/platform.py`（既存の`/platform/mfa-settings`と同じルーター、`prefix="/platform"`）に追記。

```
GET /platform/billing/default-prices
PUT /platform/billing/default-prices
```

認証は既存の`/platform/mfa-settings`と同じPF管理者JWT（該当routerの既存dependencyを再利用）。

### GET レスポンス
```json
[
  {"item_key": "base_fee", "unit_price": "5000.0000"},
  {"item_key": "data_points", "unit_price": "0.0100"}
]
```
設定されていない`item_key`は配列に含まれない（空配列 = 全項目未設定）。

### PUT リクエスト・レスポンス
既存のダッシュボードパネル設定PUT（`/tenant-portal/dashboard/panel-configs`）と同じ「全件置き換え」パターン。

リクエスト:
```json
[
  {"item_key": "base_fee", "unit_price": "5000"},
  {"item_key": "data_points", "unit_price": "0.01"}
]
```
処理: 既存の`billing_default_unit_prices`を全削除→リクエストの内容で再挿入。レスポンスはGETと同じ形式で更新後の一覧を返す（200）。

バリデーション: `item_key`は`ITEM_KEYS`のいずれかであること、`unit_price`は既存の`/tenants/{tenant_id}/billing/prices`の`POST`と同じ検証（10進数として解釈可能・有限・非負・最大値99999999.9999・小数点以下4桁まで）を再利用する。

---

## 4. PF管理者UI

`platform-ui/platform-settings.html`に新しいセクションを追加（既存の「多要素認証（TOTP）設定」セクションと並ぶ2つ目のセクション）。

- 見出し「デフォルト料金テーブル（新規テナント開通時の初期単価）」
- `ITEM_KEYS`5項目それぞれについて、日本語ラベル＋単価入力欄（テキスト入力、既存の`/tenants/{tenant_id}/billing/prices`タブの入力パターンを踏襲）
- 「保存」ボタン → `PUT /platform/billing/default-prices`
- ページロード時に`GET /platform/billing/default-prices`で現在値を取得し、未設定の項目は空欄表示
- 保存成功時に既存のMFA設定セクションと同様の成功メッセージ表示パターンを踏襲

---

## 5. 未確定・次回検討事項

| 項目 | 内容 |
|---|---|
| 既存テナントへのデフォルト適用 | 「今持っている値をデフォルトにリセット」的な機能は今回スコープ外。将来必要になれば別Planで検討 |
| デフォルトの履歴管理 | 現在値のみ保持する方針で確定（2026-09-11のユーザー回答）。将来「〇月からデフォルトを変える」要望が出た場合は、テナント別単価と同じ`effective_from`方式への拡張を検討 |
