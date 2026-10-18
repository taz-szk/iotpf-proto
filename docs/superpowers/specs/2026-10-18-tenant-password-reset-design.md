# テナント管理者パスワード再設定機能 設計スペック

## 意図・背景

現在、テナント管理者（`/admin/`配下のテナントログイン利用者、ロールは admin/operator/viewer いずれも含む）がパスワードを忘れた場合、プラットフォーム管理者に連絡して手動でリセットしてもらうしかない。これは手間がかかり、利用者にとって不便。

テナントセルフサインアップ機能（`docs/superpowers/specs/2026-10-17-tenant-self-signup-design.md`）で確立した「メールにトークン付きリンクを送り、クリックで本人確認する」パターンを、パスワード再設定にも適用する。

## 対象範囲

- **対象**: テナントユーザー（`tenant_<uuid>`スキーマの`users`テーブルに登録された、role問わず全員）
- **対象外（今回のスコープ外）**: プラットフォーム管理者（`platform_users`）のパスワード再設定。今回は要望が無いため実装しない。
- **対象外（今回のスコープ外）**: テナントスラッグそのものを忘れたケースの自己解決。これは今回の機能では救えず、引き続きプラットフォーム管理者への問い合わせ対応とする。同一メールアドレスが複数テナントに存在しうる（スキーマごとに独立したテーブルのため、メール一意性はテナント内でしか保証されない）ため、メールアドレスだけでテナントを特定する方式は採用しない。

## ゴール

- テナントログイン画面（`admin-ui/tenant-login.html`）に「パスワードを忘れた方はこちら」リンクを追加する
- 専用フォームで「テナントスラッグ・メールアドレス」を入力すると、該当すればパスワード再設定用のリンクをメールで送る
- リンクをクリックすると新しいパスワードを設定できる画面が開き、設定が完了するとログイン画面に戻る

## 非ゴール・アンチゴール

- テナントスラッグの自己解決（上記「対象外」参照）
- プラットフォーム管理者のパスワード再設定
- TOTP(MFA)設定の解除・バイパス。パスワード再設定はTOTPの有効/無効状態に影響しない（再設定後も次回ログイン時に通常通りTOTPを要求する）
- メールアドレスの存在調査に使われてしまうこと（アンチゴール）。「該当するテナント・メールアドレスの組み合わせが存在するかどうか」をレスポンスの内容や応答速度の差から外部に漏らさない

## 決定した設計

### フロー

1. `admin-ui/tenant-login.html` に「パスワードを忘れた方はこちら」リンクを追加（今回セッションで追加した「試用登録はこちらで」と同様の並び）。リンク先は新規ページ `admin-ui/forgot-password.html`
2. `forgot-password.html`: テナントスラッグ・メールアドレスの2項目を入力するフォーム。送信すると`POST /tenant-auth/forgot-password`を呼ぶ
3. バックエンド処理:
   - レート制限（既存の`is_rate_limited`/`record_failure`、キーは`tenant_password_reset:{ip}`。login/signupと同じ仕組みを再利用）
   - reCAPTCHA v3検証（既存の`verify_recaptcha`をそのまま再利用。サインアップ用に発行済みの`RECAPTCHA_SITE_KEY`/`RECAPTCHA_SECRET_KEY`を使い回すため、鍵の再発行は不要）。スコアが低い場合は429/400ではなく、他の「見つからない」ケースと同じレスポンスを返す（reCAPTCHA失敗の有無でも存在確認に使われないようにする）
   - `Tenant.slug == req.tenant_slug and Tenant.status == "active"`で該当テナントを検索（既存ログイン処理と同じ条件）。見つからなければ何もせず、以降と同じレスポンスを返す
   - 見つかった場合、該当テナントのスキーマの`users`テーブルを`email`で検索（`tenant_auth.py`の既存ログイン処理と同じ`SELECT ... WHERE email = :email AND is_active = TRUE`パターン）
   - **レスポンスは常に即座に`{"status": "requested"}`（HTTPステータス200）を返す。** テナント・ユーザーの存在確認やメール送信の成否は、レスポンスの内容・速度のいずれにも影響させない（下記「メール送信」参照）
   - 見つかった場合のみ、`password_reset_tokens`テーブル（新規、publicスキーマ）にレコードを作成する。既存の未使用トークンが同一ユーザーに残っていれば削除してから新規作成する（サインアップの仮登録と同じ「再送信で上書き」方式）

   **メール送信（非同期化）**: トークン作成後のメール送信は、FastAPIの`BackgroundTasks`でレスポンスを返した後に非同期実行する。テナント・ユーザーが見つからなかった経路（メール送信そのものが発生しない）と、見つかってメール送信する経路の両方で、クライアントへの応答はDB検索のみで即座に返るため、応答速度の差が生まれない。送信失敗はログに残すのみで、クライアントには伝播させない（サインアップ確認メールとは異なり、ここでは失敗を呼び出し元に伝播させると「宛先が存在する前提の500エラー」が存在確認に使われてしまうため、意図的に方針を変える）
4. 確認メール本文には、再設定用リンク（`https://{platform_domain}/admin/reset-password.html?token=...`）を記載。**有効期限は1時間**（サインアップ確認の24時間より短い。パスワード再設定はより機密性が高い操作のため）
5. `forgot-password.html`送信後の画面には、「該当するアカウントがあれば数分以内にメールが届きます。届かない場合はメールアドレスが間違っている可能性があります」という注記を表示する（ユーザー要望により追加。アカウントの存在を明言しないまま、よくある原因を案内する）
6. `admin-ui/reset-password.html`: URLの`?token=`をJavaScriptで読み取り、新しいパスワード（確認用含む、既存の`validate_password`ポリシーと同じ12文字以上・72バイト以下）の入力フォームを表示。送信すると`POST /tenant-auth/reset-password`（`token`・`new_password`をボディに含む）を呼ぶ
7. バックエンド処理（`POST /tenant-auth/reset-password`）:
   - トークンを`password_reset_tokens`から検索。見つからない・期限切れなら404/410相当のエラー
   - トークンに紐づくテナント・ユーザーを再チェックする（トークン発行後にテナントが停止、またはユーザーが無効化・削除されていないか）。いずれかに該当する場合は404相当のエラーとし、トークンを削除する
   - 新しいパスワードのポリシー検証（既存の`validate_password`）
   - 該当テナントスキーマの`users`テーブルの`password_hash`を更新（既存の`tenant_users.py`のパスワード変更処理と同じ`UPDATE ... SET password_hash`パターン）
   - 使用済みトークンを削除
   - 既存のログイン中セッションは、JWTに埋め込まれた`pwv`（`password_fingerprint(password_hash)`）が変わることで自動的に無効になる（既存の仕組みをそのまま利用、追加実装不要）
8. 完了後、`reset-password.html`は「パスワードを更新しました」と表示し、`tenant-login.html`（テナントスラッグを引き継ぐ）へのリンクを出す

### データモデル（新規）

`core-api/app/models/public.py`に追加:

```python
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)  # テナントスキーマ側のusers.id。public側に外部キー制約は張れない
    email = Column(String(255), nullable=False)  # メール本文の宛先確認・ログ用
    token = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
```

`core-api/app/database.py`に`migrate_create_password_reset_tokens()`を追加し、既存の`migrate_*`と同じ`CREATE TABLE IF NOT EXISTS`パターンで`app/main.py`の起動時マイグレーションタプルに登録する。

### 既存コードとの関係

- `send_password_reset_email(to_email, tenant_name, reset_url)`を`core-api/app/services/mailer.py`に追加（既存の`send_signup_verification_email`と同じ英語本文方針。送信失敗は呼び出し元に伝播させる）
- `POST /tenant-auth/forgot-password`・`POST /tenant-auth/reset-password`は`core-api/app/routers/tenant_auth.py`に追加（既存のログインエンドポイントと同じファイル、同じ`_equalize_timing`パターンを再利用）
- nginxの`/admin/`ロケーション（既存、静的配信）に新規ページ2枚が自然に乗る。追加のロケーション定義は不要だが、`forgot-password.html`がreCAPTCHA v3を読み込むため、`nginx/conf.d/api.conf`の`/admin/`ロケーションにも`/signup/`と同様のCSP緩和（`add_header Content-Security-Policy`で`https://www.google.com/recaptcha/`・`https://www.gstatic.com/recaptcha/`を許可）を追加する。既存の`/admin/`配下の他ページ（ログイン等）はAlpine.js依存のため、緩和後のCSPも`'unsafe-eval'`を維持すること
- `forgot-password.html`は`public-ui/signup.html`と同じ構成（reCAPTCHA v3スクリプトタグ、`__RECAPTCHA_SITE_KEY__`プレースホルダをデプロイ時に`sed`置換）を踏襲する
- `nginx/conf.d/rate-limit.conf`の既存`zone=login`を再利用するか、専用ゾーンを切るかは実装プランで判断（**ビルダーの判断に委ねる**）

## ビルダーに委ねる部分

- APIのレート制限キー名・1分あたりの許容回数の具体値（既存のlogin/signupの値を参考に実装時に決定）
- `password_reset_tokens`テーブルのインデックス設計（token列へのunique制約で十分か、tenant_id+user_idの複合インデックスが要るか）
- フロントエンドのエラーメッセージの正確な文言（既存の`signup.html`/`verify.html`のパターンに合わせる）
- テスト設計（既存の`test_signup_api.py`・`test_tenant_auth.py`相当のモックパターンに合わせる）
