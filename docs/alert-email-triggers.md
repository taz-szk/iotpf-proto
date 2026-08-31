# アラートメール 送信トリガー仕様

> 参照: `alert-service/app/scheduler.py` · `evaluator.py` · `notifier.py` · `db.py`
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
    send_alert_email(...)             # ← 送信

elif not should_alert and existing:   # 発火中 → 解消
    resolve_alert_event(...)
    send_alert_email(..., resolved=True)  # ← [RESOLVED] 送信
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
