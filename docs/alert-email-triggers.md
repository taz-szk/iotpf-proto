# アラートメール 送信トリガー仕様

> 参照: `alert-service/app/scheduler.py` · `evaluator.py` · `notifier.py` · `failure_notice.py` · `db.py`
> 評価間隔: 60秒（`EVAL_INTERVAL_SEC`）

---

## 動作概要

APScheduler が **60秒間隔** で全テナントのアクティブなアラートルールを評価します。
メール送信は「状態変化」にのみ反応する設計で、条件が継続して満たされても送信は繰り返されません。

---

## 連続条件時のメール送信挙動

**1回だけ送信（状態変化検知型）。** 条件が満たされ続けても追加メールは送られません。条件が解消されたときに `[RESOLVED]` メールが1通送信されます。

評価ループの核心ロジック（`scheduler.py:43`）:

```python
if should_alert and not existing:     # 未発火 → 発火
    create_alert_event(...)
    _notify_and_record(rule, ...)     # ← ルールの通知先(メール/Slack)へ送信し、配信結果を記録

elif not should_alert and existing:   # 発火中 → 解消
    resolve_alert_event(...)
    _notify_and_record(rule, ..., resolved=True)  # ← [RESOLVED] 送信
```

時系列での挙動（60秒ごとの評価）:

| 経過時間 | 状態 | メール |
|---|---|---|
| t=0 | 正常 | — |
| t=60s | 条件一致 | 📧 アラート送信 |
| t=120s | 条件継続 | サイレント |
| t=180s | 条件継続 | サイレント |
| t=240s | 条件解消 | 📧 `[RESOLVED]` 送信 |
| t=300s | 正常 | — |

イベント管理は `alert_events` テーブル（`resolved_at IS NULL` = 未解消）で行われます。既存の未解消イベントがある間は新規メールを送りません。

---

## 送信トリガー

### 1. テレメトリアラート発火 `[ALERT]`

InfluxDB のテレメトリデータがルールの閾値条件を満たし、かつ同ルールの未解消イベントが存在しない場合に送信されます。

### 2. アラート解消 `[RESOLVED]`

発火中のアラートが閾値条件を満たさなくなった場合に送信されます。件名に `[RESOLVED]` が付与されます。

### 3. デバイスオフライン `[ALERT]`

デバイスの `last_seen_at` が `DEVICE_OFFLINE_THRESHOLD_SEC`（デフォルト 180秒）を超えており、かつデバイスのステータスがまだ `offline` でない場合に送信されます。`condition = "device_offline"` のアラートルールが対象。送信後にデバイスを `offline` マークするため、再接続まで再送されません。

---

## テレメトリ評価のトリガーモード

アラートルール設定の `trigger_mode` によって「いつ `should_alert=True` になるか」が決まります。

| trigger_mode | 発火条件 | パラメータ |
|---|---|---|
| `consecutive` | 最新 N サンプルが連続して閾値条件を満たす | `consecutive_count` |
| `duration` | 過去 D 分のサンプルがすべて閾値条件を満たす | `duration_sec` |
| `consecutive_and_duration` | 連続カウント AND 継続時間の両方を満たす | `consecutive_count` + `duration_sec` |

InfluxDB クエリ範囲: `max(duration_sec + 60, consecutive_count × 60 + 60)` 秒。データが取得できない場合は `should_alert = False`（誤報なし）。

---

## 閾値条件（condition）

| condition | 判定 |
|---|---|
| `above` | value > threshold |
| `below` | value < threshold |
| `equal` | \|value − threshold\| < 1e-9 |
| `device_offline` | last_seen_at が閾値超過（テレメトリ評価とは別フロー） |

---

## 送信メールの仕様

| 項目 | 内容 |
|---|---|
| 件名（アラート） | `[SEVERITY] IoT Alert: {sensor_key} / {device_id}` |
| 件名（解消） | `[RESOLVED] IoT Alert: {sensor_key} / {device_id}` |
| 本文（アラート） | sensor_key / device_id / condition & threshold / current_value / tenant_id |
| 本文（解消） | sensor_key / device_id / tenant_id のみ（current_value なし） |
| 送信先 | アラートルールの `notify_emails` 配列 |
| From アドレス | `SMTP_FROM` 環境変数（デフォルト: `alerts@iot-platform.local`） |

---

## 通知先（メール / Slack）

通知先は**アラートルールごと**に設定します。メールとSlackは独立で、両方・片方・どちらもなし（画面に記録のみ）を選べます。片方が失敗してももう片方は送ります。

| 項目 | 内容 |
|---|---|
| メール | ルールの `notify_emails`（上記「送信メールの仕様」） |
| Slack | ルールの `slack_webhook_url`（Incoming Webhook）。本文はメールと同じ内容を `{"text": …}` のJSONで POST（10秒タイムアウト、リダイレクト追従なし） |
| URLの検証 | `https://hooks.slack.com/services/T…/B…/…` の形式のみ許可（送信先を自由にできるとSSRFになるため）。APIでの保存時と、送信の直前の両方で検証 |
| URLの扱い | 秘密情報。APIのレスポンスには載せず、`slack_configured` と末尾4文字（`slack_webhook_hint`）だけ返す。更新時に値を送らなければ維持、`null` で解除。ログにも出さない |
| エスケープ | センサー名・デバイスIDの `& < >` はエスケープする（`<!channel>` 等のメンション注入を防ぐ） |

## 配信結果の記録・表示

`notify()` はチャンネルごとの結果 `{"ok", "error"}` を返し、スケジューラが `alert_rules.notify_status`（JSONB）に**直近の結果**として記録します（送信を試みたチャンネルだけを上書き）。

- `error` は利用者に見せてよい説明（例: `HTTP 404（Webhook URLが正しくありません）`、`SMTPサーバーのホスト名を解決できません（gaierror）`）。Webhook URL・宛先は含めない
- ルール一覧（プラットフォーム管理画面・テナント管理画面）の通知先列に、「送信済み」または「送信失敗＋理由・時刻」を表示（失敗は赤）
- 過去の配信の履歴は保存しない（直近の結果のみ）

## 通知失敗時の管理者への通知

**Slack通知が「失敗に変わったとき」だけ**、テナント管理者とプラットフォーム管理者へメールで知らせます（`failure_notice.py`）。

| 項目 | 内容 |
|---|---|
| 送るタイミング | 直前が「成功または未実行」で、今回Slackが失敗したとき。失敗が続く間は送らない。成功と失敗を繰り返しても、同じルールに**1時間**の間隔（`notify_status.admin_notice.at`） |
| 送り先 | 有効なテナント管理者（`admin`）とプラットフォーム管理者。重複を除き、**1回10件まで** |
| バウンス対策 | 形式が不正なアドレスと、予約ドメイン（`example.com/.net/.org`、`.test` `.invalid` `.local` 等）は除外する |
| メールの失敗 | メールチャンネル自体の失敗では送らない（メールが不調なときにメールを重ねない） |
| 件名 | `[IoT Alert] Slack notification failed: {sensor_key}` |
| 本文 | 失敗の理由と、届かなかったアラート（または復旧通知）の内容、対処（Webhook URLの確認・テスト送信） |

## テスト送信

ルール編集画面の「テスト送信」ボタン（Slack・メール）から、通知先にテストメッセージを1通送って確認できます。

- API: `POST /tenants/{tenant_id}/alert-rules/test-notification`（プラットフォーム）、`POST /tenant-portal/me/alert-rules/test-notification`（テナント）
- Slackは、フォームに入力した未保存のURL（`slack_webhook_url`）か、保存済みのルール（`rule_id`。URLは画面に返さないためサーバー側で探す）のどちらかを指定
- 配信に失敗しても HTTP 200 で `{"ok": false, "error": "…"}` を返す。1ユーザー1分5回まで（超過は429）。監査ログにはチャンネルと結果だけを残す

---

## SMTP 設定（.env）

| 環境変数 | 説明 | デフォルト |
|---|---|---|
| `SMTP_FROM` | 差出人アドレス | `alerts@iot-platform.local` |
| `SMTP_HOST` | SMTPサーバーホスト | `localhost` |
| `SMTP_PORT` | ポート | `587` |
| `SMTP_USER` | 認証ユーザー（空の場合は STARTTLS/認証なし） | — |
| `SMTP_PASSWORD` | 認証パスワード | — |
| `EVAL_INTERVAL_SEC` | 評価間隔 | `60` |
| `DEVICE_OFFLINE_THRESHOLD_SEC` | オフライン判定閾値 | `180` |
