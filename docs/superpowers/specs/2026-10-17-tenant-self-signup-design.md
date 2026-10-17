# テナントセルフサインアップ機能 設計

## 背景・目的

現在、テナント作成はプラットフォーム管理者だけが実行できる認証必須のAPI
(`POST /tenants`)に限られており、新規に試してもらうには都度連絡してテナントと
アカウントを作成する必要がある。この手間を無くし、見込み利用者が自分で
サインアップしてすぐ試せるようにする。

目的は営業的なトライアル管理ではなく、「手軽に試せること」。一方でサインアップは
実リソース(PostgreSQLスキーマ・InfluxDB org・Grafana org)を消費するため、
悪用防止と、デモサーバー上にテナントが溜まり続けない期限管理が必要。

## スコープ外

- 課金・決済との連携(トライアル期間中は無料、延長しても無料のまま)
- テナント企業ドメイン等によるサインアップ制限
- 延長申請の明示的な「却下」操作(無視すれば次の自動削除サイクルで削除される)
- SORACOM Auth Key・テナント別Slack Webhookなど、テナント単位でDB管理している
  シークレットのローテーション運用(別件)

## 全体フロー

```
① 公開サインアップフォーム(platform-ui、reCAPTCHA v3検証)
   → tenant_signup_requests に仮登録 + 確認メール送信
② メールのリンクをクリック(24時間以内)
   → 既存の setup_tenant() 等を呼び出し、本物の Tenant + 最初の admin user を作成
   → trial_expires_at = 今日 + 90日
③ 期限7日前 → cronジョブが警告メールを送信(テナント admin へ)
④ テナント admin が管理画面から延長申請 → Slack通知(既存Webhook再利用)
⑤ プラットフォーム管理者が承認 → trial_expires_at = 今日 + 90日(再延長、何度でも可)
⑥ 期限が過ぎても延長申請中でなければ、cronジョブが既存の削除処理で自動削除
```

## データモデル変更

### `tenants` テーブルへのカラム追加

| カラム | 型 | 説明 |
|---|---|---|
| `signup_source` | `String(20)`, not null, default `"platform_created"` | `"self_signup"` / `"platform_created"` |
| `trial_expires_at` | `DateTime(timezone=True)`, nullable | 自己サインアップのみ設定。管理者作成テナントは常にNULL(無期限) |
| `trial_warning_sent_at` | `DateTime(timezone=True)`, nullable | 期限7日前警告メールの送信済みフラグ。延長承認時にNULLへリセット |
| `trial_extension_requested_at` | `DateTime(timezone=True)`, nullable | 延長申請中かどうか。承認時にNULLへリセット |

Alembic等のマイグレーション管理は使っていないため(既存の`create_tenant_schema`等を
見る限り生SQL/モデル直書き運用)、既存のマイグレーション手順(docs配下のrunbook)に
従って`ALTER TABLE`を追記する。

### 新テーブル `tenant_signup_requests`

メール確認が完了するまでは本物の`Tenant`行を作らず、ここに仮登録を保持する。

| カラム | 型 | 説明 |
|---|---|---|
| `id` | UUID, primary key | |
| `email` | `String(255)`, not null | |
| `tenant_name` | `String(255)`, not null | |
| `slug` | `String(100)`, not null, unique | `tenant_name`から自動生成(下記) |
| `password_hash` | `String`, not null | 既存の`hash_password`で生成 |
| `verification_token` | `String(64)`, not null, unique | `secrets.token_urlsafe(32)` |
| `created_at` | `DateTime(timezone=True)`, server_default now() | |
| `expires_at` | `DateTime(timezone=True)`, not null | `created_at + 24時間`。期限切れの行は検証時に無効とし、定期的に掃除する |

同じメールアドレス・同じslugで複数回サインアップされた場合、古い未確認の行は
新規リクエスト時に削除してから作り直す(確認リンクを使い回されないようにする)。

## エンドポイント

### `POST /signup`(認証不要、新規ルーター `core-api/app/routers/signup.py`)

リクエスト: `{tenant_name: str, email: str, password: str, recaptcha_token: str}`

処理順序:
1. **reCAPTCHA v3検証**: Google reCAPTCHA siteverify APIに`recaptcha_token`を送り、
   `success`かつ`score`が閾値(環境変数`RECAPTCHA_MIN_SCORE`、既定`0.5`)以上であることを確認。
   失敗時は`400`。
2. **レート制限**: 既存の`services/rate_limiter.py`をIPアドレスキーで適用
   (`is_rate_limited` / `record_failure`と同じ関数を流用し、キー接頭辞を`"signup:"`等にして
   ログイン試行と名前空間を分ける)
3. **入力検証**: `validate_password(password, email)`(既存の`password_policy.py`をそのまま使用)、
   emailの簡易形式チェック
4. **重複チェック**: `tenant_signup_requests.email`、`platform_users`相当のテナント内`users`は
   まだ存在しないため、既存`Tenant.slug`との重複のみ確認すればよい
5. **slug自動生成**: `tenant_name`を小文字化・英数字とハイフンのみに変換・前後のハイフン除去。
   既存`Tenant.slug`または`tenant_signup_requests.slug`と衝突する場合は`-2`, `-3`...を付与
   (新規ユーティリティ`services/slug.py`に`slugify(name: str, is_taken: Callable[[str], bool]) -> str`
   を追加)
6. 同一emailの既存未確認リクエストがあれば削除し、新しい`tenant_signup_requests`行を作成
7. `mailer.py`に追加する`send_signup_verification_email(to_email, tenant_name, verification_url)`で
   確認メール送信。送信失敗時は`500`を返し、行はロールバックする(既存のビルショックメールとは異なり、
   ここでは送信成否がユーザー体験に直結するため例外を握り潰さない)
8. `201`を返す(本文なし。「確認メールを送信しました」の案内はフロントで表示)

### `GET /signup/verify?token=...`(認証不要)

1. `verification_token`で`tenant_signup_requests`を検索。無ければ`404`
2. `expires_at`を過ぎていれば該当行を削除し`410 Gone`
3. 既存の`create_tenant`(`routers/tenants.py`)とほぼ同じ処理を実行:
   - `Tenant(signup_source="self_signup", trial_expires_at=now+90日)`を作成
   - `setup_tenant(tenant_id, tenant_name)`でInfluxDB org/Grafana org/スキーマ作成
   - `seed_tenant_default_prices` は呼ばない(課金対象外のため)
   - `services/billing_batch.py`の`run_monthly_billing_batch`は現状`Tenant.status == "active"`の
     全テナントを対象にしており、`signup_source`による除外が無い。トライアルは無料という方針
     ([[Global Constraints]]参照)に合わせ、このクエリに`Tenant.signup_source != "self_signup"`
     の条件を追加する(実装タスクに含める)
   - テナントスキーマの`users`テーブルへ、`tenant_signup_requests`の`password_hash`で
     role=`admin`の最初のユーザーを作成(`tenant_users.py`の`create_tenant_user`のDB操作部分を
     再利用できるよう、ロジックを`services/tenant_users.py`等に切り出すことを検討)
   - `write_audit_log(db, "self_signup", str(tenant.id), email, "signup_tenant", ...)`
     (actor_typeに`"self_signup"`という新しい区分を使う。既存は`"platform"`/`"tenant"`のみの想定なので、
     `AuditLog`モデル側にactor_type列の制約が無いか確認すること)
   - `tenant_signup_requests`の行を削除
4. 成功時はログインページへ誘導するHTML(または`platform-ui`の静的ページへのリダイレクト)を返す

## トライアル期限管理(`core-api/app/scripts/trial_expiry_check.py`)

bashのcronスクリプト(`vuln-scan.sh`等)と違い、SQLAlchemyモデルやInfluxDB/Grafana連携が
必要なため、**Pythonスクリプトとしてcore-api内に実装し、cronからは`docker compose exec`で叩く**。

```bash
# crontab例(毎日6:00)
0 6 * * * cd /home/ubuntu/iotpf-proto && docker compose exec -T core-api python -m app.scripts.trial_expiry_check >> trial-expiry-check.log 2>&1
```

処理内容:
1. `signup_source="self_signup"` かつ `status="active"` のテナントを全件取得
2. **7日前警告**: `trial_expires_at`が「今日+7日」以前かつ`trial_warning_sent_at`がNULLの
   テナントに対し、`mailer.py`の`send_trial_expiry_warning_email`で警告メール送信、
   `trial_warning_sent_at=now()`を記録
3. **自動削除**: `trial_expires_at`を過ぎている かつ `trial_extension_requested_at`がNULLの
   テナントに対し、既存`routers/tenants.py`の`delete_tenant`と同じ処理
   (`status="deleted"`+slugリネーム+コミット → `services/tenant.teardown_tenant`呼び出し)を実行。
   HTTPを経由せず、サービス関数を直接呼ぶ
4. 処理結果(警告送信件数・削除件数)を標準出力に出す(vuln-scan.shと同じく、cronのリダイレクトで
   ログファイルに残す運用)

## 延長申請・承認

### `POST /tenant-portal/me/trial-extension-request`(既存`tenant_portal.py`に追加、テナントadmin認証)

- 対象テナントが`signup_source="self_signup"`でなければ`400`(管理者作成テナントには無意味)
- `trial_extension_requested_at`がすでに設定されていれば何もせず`200`(多重申請を許容、副作用なし)
- `trial_extension_requested_at=now()`をセット
- Slack通知: `vuln-scan.sh`/`secrets-rotation-check.sh`と同じ`VULN_SCAN_SLACK_WEBHOOK_URL`を使い、
  Python側(`httpx`)からWebhook URLへ直接POST(新規`services/slack_notifier.py`に
  `notify_trial_extension_requested(tenant_name, tenant_id)`を追加)

### `POST /tenants/{tenant_id}/trial-extension/approve`(既存`tenants.py`に追加、`_require_platform`)

- `trial_expires_at = now() + 90日`
- `trial_extension_requested_at = NULL`
- `trial_warning_sent_at = NULL`(次の期限でまた7日前警告が出るようにリセット)
- `write_audit_log(..., "approve_trial_extension", ...)`

## 設定追加(`.env` / `config.py`)

```
RECAPTCHA_SITE_KEY=（公開鍵、platform-uiのフォームに埋め込む）
RECAPTCHA_SECRET_KEY=（秘密鍵、core-apiからGoogle APIを呼ぶ用）
RECAPTCHA_MIN_SCORE=0.5
```

確認メールのリンクURLは、既存の`settings.platform_domain`を使って
`https://{platform_domain}/signup/verify?token=...`を組み立てる(新規設定は増やさない)。

## 画面

**訂正**: `platform-ui`はプラットフォーム管理者専用の非公開コンソール(`/iotairx-console/`、
CLAUDE.mdで「URLを公開文書に載せない」と明記されている)であり、公開ページを置く場所ではない。
`admin-ui`も既存はテナントログイン後の画面群(`/admin/`)。公開サインアップ用に
**新規の静的ディレクトリ`public-ui/`**を追加する。

- **`public-ui/signup.html`**(新規): テナント名・email・パスワード入力 + reCAPTCHA v3
  (スコア型なので画面上にチェックボックス等は出ない)。既存の`docs/promo.html`と同じトーンの
  デザインに合わせる。JSから`POST /api/signup`を呼ぶ(既存ページが`/api/...`を呼ぶのと同じ規約)
- **`public-ui/signup-pending.html`**(新規): 「確認メールを送信しました」の静的案内ページ
- **nginx**: `nginx/conf.d/api.conf`に`location /signup/ { alias /usr/share/nginx/public-ui/; index signup.html; try_files $uri $uri/ /signup/signup.html; }`を追加。
  `docker-compose.yml`のnginxサービスに`./public-ui:/usr/share/nginx/public-ui:ro`のvolumeマウントを追加。
  `/api/signup`(POST)は既存の`/api/auth/login`と同じ理由(ブルートフォース/スパム対策)で
  `nginx/conf.d/rate-limit.conf`に新しいゾーン(例: `limit_req_zone $binary_remote_addr zone=signup:10m rate=10r/m;`)を追加し、
  `location = /api/signup { limit_req zone=signup burst=5 nodelay; ... }`を他のレート制限済みエンドポイントと同じ形で追加する
- **プラットフォーム管理者のテナント一覧**(`platform-ui/tenants.html`、既存、`/iotairx-console/`配下):
  「トライアル期限」「延長申請中」列を追加し、申請中テナントに承認ボタンを表示
  (`POST /tenants/{tenant_id}/trial-extension/approve`を呼ぶ)。この画面はプラットフォーム管理者
  専用のため、承認操作をここに置くのは自然(admin-uiではなく既存のplatform-ui/tenants.htmlを使う)

## Global Constraints

- 既存の`validate_password`(12文字以上・72バイト以下)をそのまま適用する
- 既存のテナント分離(スキーマ分離・mTLS・EMQX ACL)は変更しない。セルフサインアップ
  テナントも通常のテナントと全く同じ分離の仕組みで動く
- トライアル延長は何度でも可、却下操作は作らない(無視すれば次サイクルで削除)
- Slack通知は既存の`VULN_SCAN_SLACK_WEBHOOK_URL`を再利用し、新規Webhookは作らない
- `signup_source="self_signup"`のテナントは月次請求バッチ(`run_monthly_billing_batch`)の対象から除外する(トライアルは無料)

## Review Focus

実装時に特に注意すべき入力/失敗パターン:

1. **確認メール未達・スパム判定**: SMTP送信が失敗した場合(`mailer.py`の既存パターンは
   失敗を握り潰すものと例外を伝播するものが混在している)、サインアップ自体は`500`で
   失敗として扱い、仮登録行をロールバックする。ユーザーが「送信した気になっているが
   実際は失敗している」状態を作らない
2. **確認リンクの二重クリック・期限切れ後クリック**: 既にテナントが作成済みの
   `verification_token`で再度`GET /signup/verify`を呼んだ場合(行が削除済み)に`404`を
   返し、エラーにはならないが二重にテナントは作られないことを確認する
3. **同名・同slug希望の同時サインアップ**(競合): `tenant_signup_requests.slug`の
   unique制約、および確認時の`Tenant.slug`挿入時のunique制約違反を両方試験し、
   後者が発生した場合は仮登録行を残したまま適切なエラーを返す(ユーザーが
   リトライできるようにする)
4. **reCAPTCHAスコアが閾値未満だが`success: true`**: ボットらしい低スコアのトークンを
   拒否できているかを確認する(モックしたスコア値でテスト)
5. **延長申請がないまま期限切れ寸前に管理者が手動で`status="suspended"`にしたテナント**:
   `trial_expiry_check.py`の自動削除条件が`status="active"`限定であることを確認し、
   suspended中のテナントを誤って削除しないことをテストする

## 確認済みの前提

- `AuditLog.actor_type`は`String(20)`でCHECK制約等は無い。`"self_signup"`(11文字)を
  新しいactor_typeとして使って問題ない
- `run_monthly_billing_batch`は`Tenant.status == "active"`の全テナントを対象にしている
  (`signup_source`での除外なし)ことを確認済み。上記Global Constraintsの通り除外条件を追加する
