# データエクスポート(CSVダウンロード)機能 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナント管理者が、期間・デバイス・グループで絞り込んだテレメトリ(位置情報含む)を、定期(日次/週次/月次)またはアドホックにCSVでダウンロードできるようにする。

**Architecture:** 既存の`billing_usage.py`の集計パターン(InfluxDBへのFluxクエリ)でCSVを生成し、既存のMinIO(ファームウェア格納と同じ「DBにキーだけ持たせてMinIOに実体を置く」パターン)に格納する。非同期生成は既存の`billing_batch.py`/`data_retention.py`と同じ「`threading.Thread`デーモン」方式。テナントごとに「定期用1行・手動用1行」の計2行だけを持つ`export_jobs`テーブルで、生成ごとに上書きする。

**Tech Stack:** FastAPI (core-api), SQLAlchemy, InfluxDB Flux クエリ, MinIO, Alpine.js (admin-ui/tenant-portal.html)

**Spec:** `docs/superpowers/specs/2026-10-19-data-export-design.md`(①エクスポート/ダウンロード機能の節)。本プランは②準リアルタイムアクセス機能(別プラン`2026-10-19-realtime-access-token.md`)を含まない。

## Global Constraints

- CSVの列: デバイス名、日時、テレメトリ各項目(位置情報の緯度・経度を含む)
- 絞り込み条件: 期間、対象デバイス、対象グループ(いずれか、または組み合わせ)
- 1テナントにつき「定期用1枠」「手動用1枠」の計2枠のみ保持。新しい生成は同じ枠を問答無用で上書きする
- 削除は「ユーザーの明示的な削除操作」と「同じ枠への上書き」の2パターンのみ。生成済みファイルに自動期限切れ(TTL)は設けない
- アドホックエクスポートが処理中の間に新規リクエストが来たら、`409`で拒否する(「現在処理中です」)。ただし処理中状態がタイムアウト(`_LOCK_TIMEOUT_SECONDS`、デフォルト600秒)を超えて残留している場合は異常終了とみなし自動的に解除する
- サイズ上限は設定値化する(デフォルト20MB、`settings.export_max_size_bytes`)。生成前の概算が上限を超える場合はジョブを作成せず`422`で拒否する
- 定期エクスポートの頻度は日次/週次/月次から選択。対象期間は頻度から自動算出(直前の1日/1週間/1ヶ月)
- 管理画面の操作(リクエスト・削除・スケジュール変更)はテナントadmin権限限定(既存のアーカイブ即時完全削除機能と同じ`_require_admin`)
- Billing: `export_base_fee`(定期スケジュールが有効な間は常に`quantity:1`)、`export_retained_size_mb`(月末スナップショット時点で残存しているファイルサイズのMB、切り上げ)の2つの`item_key`を追加

## Review Focus

- 対象デバイスを1件も指定せず、かつテナントにデバイスが1台も居ない状態でアドホックエクスポートをリクエストした場合、0行のCSV(ヘッダのみ)が生成され、エラーにはならずダウンロードできる(空データを異常として扱わない)
- サイズ概算クエリ自体がInfluxDBへの到達不可・タイムアウトで失敗した場合、ジョブは「概算できなかった」ことを理由に安全側(拒否)に倒れる。無制限に生成を許可しない
- `group_id`と`device_ids`を同時に指定した場合の絞り込みの意味(和集合か積集合か)が定義されている
- 他テナントの`export_jobs`行・MinIOキーに、tenant_idの不一致や推測可能なキーからアクセスできない(MinIOキーにtenant_idを含めた上で、ダウンロード時にリクエスト元のtenant_idとDB上のジョブのtenant_idを突き合わせる)
- 定期エクスポートのスケジューラが、同じ実行対象を2回処理してしまう(多重実行)ことがない

---

### Task 1: DBモデル・マイグレーション

**Files:**
- Create: `core-api/app/models/export.py`
- Modify: `core-api/app/database.py`(末尾、`migrate_add_map_default_view`の後に追加)
- Modify: `core-api/app/main.py`(import文と`on_startup`のマイグレーションタプルに追加)
- Test: `core-api/tests/test_export_models.py`

**Interfaces:**
- Produces: `ExportJob`(`core-api/app/models/export.py`)— カラム: `id`(UUID, PK), `tenant_id`(UUID, FK tenants.id), `kind`(String(10), "periodic"|"adhoc"), `status`(String(20), "queued"|"processing"|"done"|"failed"), `period_start`(DateTime tz), `period_end`(DateTime tz), `device_ids`(JSONB, nullable), `group_id`(UUID, nullable), `minio_key`(String(255), nullable), `file_size_bytes`(Integer, nullable), `error_message`(String(500), nullable), `requested_at`(DateTime tz, server_default now), `started_at`(DateTime tz, nullable), `completed_at`(DateTime tz, nullable)。`UniqueConstraint("tenant_id", "kind", name="uq_export_jobs_tenant_kind")`
- Produces: `ExportSchedule`(同ファイル)— カラム: `tenant_id`(UUID, PK, FK tenants.id), `frequency`(String(10), "daily"|"weekly"|"monthly"), `device_ids`(JSONB, nullable), `group_id`(UUID, nullable), `enabled`(Boolean, default True), `last_run_at`(DateTime tz, nullable)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_export_models.py
from app.models.export import ExportJob, ExportSchedule

def test_export_job_table_name_and_unique_constraint_columns():
    assert ExportJob.__tablename__ == "export_jobs"
    row = ExportJob(
        tenant_id="11111111-1111-1111-1111-111111111111",
        kind="adhoc", status="queued",
        period_start="2026-10-01T00:00:00+00:00",
        period_end="2026-10-19T00:00:00+00:00",
    )
    assert row.kind == "adhoc"
    assert row.status == "queued"
    constraint_cols = {c.name for c in ExportJob.__table__.constraints
                       if c.name == "uq_export_jobs_tenant_kind"
                       for c in [next(iter(ExportJob.__table__.constraints))]}
    names = {uc.name for uc in ExportJob.__table__.constraints}
    assert "uq_export_jobs_tenant_kind" in names

def test_export_schedule_table_name_and_defaults():
    assert ExportSchedule.__tablename__ == "export_schedules"
    row = ExportSchedule(tenant_id="11111111-1111-1111-1111-111111111111", frequency="weekly")
    assert row.frequency == "weekly"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_export_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.export'`

- [ ] **Step 3: Implement `ExportJob`・`ExportSchedule` in `core-api/app/models/export.py`**

Use the exact columns from the Interfaces block above. Follow `core-api/app/models/billing.py`'s style (`Column`, `UUID(as_uuid=True)`, `JSONB` from `sqlalchemy.dialects.postgresql`, `func.now()` for server defaults).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_export_models.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Add migration functions**

In `core-api/app/database.py`, add `migrate_create_export_tables()` following the exact `CREATE TABLE IF NOT EXISTS` style of `migrate_create_password_reset_tokens()` (raw SQL via `conn.execute(text("..."))`, `conn.commit()`), creating both `export_jobs` and `export_schedules` with the same columns/types/constraint as Step 3 (SQL types: `UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `VARCHAR(10)`, `VARCHAR(20)`, `TIMESTAMPTZ`, `JSONB`, `INTEGER`, `VARCHAR(500)`, `BOOLEAN NOT NULL DEFAULT TRUE`, and `CONSTRAINT uq_export_jobs_tenant_kind UNIQUE (tenant_id, kind)`).

In `core-api/app/main.py`: add `migrate_create_export_tables` to the `from app.database import ...` line and to the `for migrate in (...)` tuple in `on_startup()`.

- [ ] **Step 6: Commit**

```bash
git add core-api/app/models/export.py core-api/app/database.py core-api/app/main.py core-api/tests/test_export_models.py
git commit -m "feat: add export_jobs/export_schedules models and migrations"
```

---

### Task 2: サイズ概算・CSV生成サービス

**Files:**
- Create: `core-api/app/services/export_csv.py`
- Test: `core-api/tests/test_export_csv.py`

**Interfaces:**
- Consumes: `app.models.public.Tenant`(既存、`influxdb_org_id`・`influxdb_token`属性)
- Produces: `estimate_export_row_count(tenant: Tenant, start: datetime, end: datetime, device_ids: list[str] | None) -> int`
- Produces: `generate_export_csv(tenant: Tenant, start: datetime, end: datetime, device_ids: list[str] | None) -> bytes`
- Produces: `class InfluxUnavailableError(Exception)` — 概算・生成のいずれかでInfluxDBへのクエリが失敗(非200応答・例外)した場合に投げる

`device_ids`が`None`の場合は全デバイス対象、空リスト`[]`は「対象デバイス0件」として扱い、両関数ともInfluxDBへ問い合わせずに即座に`0`・ヘッダのみのCSVを返す(グループ絞り込みでメンバーが0台だった場合に相当)。

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_export_csv.py
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest
from app.models.public import Tenant
from app.services.export_csv import estimate_export_row_count, generate_export_csv, InfluxUnavailableError

def _tenant():
    t = Tenant()
    t.influxdb_org_id = "org-1"
    t.influxdb_token = "token-1"
    return t

def test_estimate_export_row_count_empty_device_list_short_circuits():
    assert estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                      datetime(2026,10,2,tzinfo=timezone.utc), device_ids=[]) == 0

def test_estimate_export_row_count_parses_influx_response():
    resp = MagicMock(status_code=200, text="#group\r\n#datatype\r\n,result,table,_value\r\n,_result,0,42\r\n")
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        n = estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                       datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)
    assert n == 42

def test_estimate_export_row_count_raises_on_influx_failure():
    resp = MagicMock(status_code=500, text="error")
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        with pytest.raises(InfluxUnavailableError):
            estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                       datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)

def test_generate_export_csv_empty_device_list_returns_header_only():
    csv_bytes = generate_export_csv(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                     datetime(2026,10,2,tzinfo=timezone.utc), device_ids=[])
    assert csv_bytes == b"device_name,datetime,latitude,longitude\r\n"

def test_generate_export_csv_parses_pivoted_influx_csv():
    influx_csv = (
        "#group,false,false,true,true,false,false,true,true,false,false\r\n"
        "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,string,string,string,double,double\r\n"
        "#default,_result,,,,,,,,,\r\n"
        ",result,table,_start,_stop,_time,device_id,device_name,latitude,longitude\r\n"
        ",_result,0,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,2026-10-01T12:00:00Z,dev-1,Sensor-1,35.1,139.1\r\n"
    )
    resp = MagicMock(status_code=200, text=influx_csv)
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        csv_bytes = generate_export_csv(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                         datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)
    text = csv_bytes.decode("utf-8")
    assert text.splitlines()[0] == "device_name,datetime,latitude,longitude"
    assert "Sensor-1,2026-10-01T12:00:00Z,35.1,139.1" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_export_csv.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.export_csv'`

- [ ] **Step 3: Implement `core-api/app/services/export_csv.py`**

- `estimate_export_row_count`: build a Flux query counting distinct `(_time, device_id)` pairs within `range(start, stop)` for `_measurement == "telemetry"`, with an optional `device_id == "a" or device_id == "b" or ...` filter line when `device_ids` is a non-empty list (mirror the OR-filter style already used in `tenant_portal.py`'s `_query_location_history`, and the `distinct(column: ...) |> group() |> count()` pattern already used in `billing_usage.py`'s `_count_retired_devices`). POST it the same way `billing_usage.py` does (`httpx.post(f"{settings.influxdb_url}/api/v2/query?orgID={tenant.influxdb_org_id}", headers={"Authorization": f"Token {tenant.influxdb_token}", ...}, json={"query": query, "type": "flux"}, timeout=15.0)`). On non-200 or exception, raise `InfluxUnavailableError` (do not silently return `0` — the caller must fail safe, not permit an unbounded export). On 200, parse the scalar result the same way `_parse_influx_csv_scalar` does (import and reuse it from `app.routers.stats`).
- `generate_export_csv`: build a Flux query with `pivot(rowKey: ["_time", "device_id", "device_name"], columnKey: ["_field"], valueColumn: "_value") |> sort(columns: ["device_name", "_time"])`, same filters as above. POST the same way. On non-200/exception, raise `InfluxUnavailableError`. Parse the InfluxDB annotated-CSV response: skip lines starting with `#`, take the first non-`#` line as the header, map its columns by name (do not assume column order), and emit one output CSV line per data row with columns `device_name,datetime,<every other non-system column except result/table/_start/_stop/device_id, in the order they appear in the Influx response>` — i.e. drop `result`, `table`, `_start`, `_stop`, `device_id`, rename `_time` to `datetime`, keep the rest (such as `latitude`, `longitude`, and any other telemetry field) as-is. Output encoding UTF-8, line terminator `\r\n`, using Python's `csv` module. When `device_ids == []`, return `b"device_name,datetime,latitude,longitude\r\n"` without querying InfluxDB (fixed minimal header — there is no field list to discover when there is no data).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_export_csv.py -v`
Expected: PASS (5/5)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/export_csv.py core-api/tests/test_export_csv.py
git commit -m "feat: add InfluxDB export size-estimate and CSV generation service"
```

---

### Task 3: ジョブ管理サービス(アドホック・上書き・ロック復旧)

**Files:**
- Create: `core-api/app/services/export_jobs.py`
- Modify: `core-api/app/config.py`(設定値`export_max_size_bytes`を追加)
- Modify: `core-api/app/services/minio_client.py`(エクスポート用のバケット操作を追加)
- Test: `core-api/tests/test_export_jobs.py`

**Interfaces:**
- Consumes: `estimate_export_row_count`, `generate_export_csv`, `InfluxUnavailableError`(Task 2)
- Consumes: `app.models.export.ExportJob`(Task 1)
- Produces: `class JobAlreadyRunningError(Exception)`
- Produces: `class ExportTooLargeError(Exception)` — `__init__(self, estimated_bytes: int, limit_bytes: int)`
- Produces: `request_adhoc_export(db, tenant: Tenant, start: datetime, end: datetime, device_ids: list[str] | None, group_id: str | None) -> ExportJob`
- Produces: `run_export_job(job_id: str) -> None`(スレッドから呼ばれる。自分で`SessionLocal()`を開く)
- Produces (minio_client.py): `upload_export_file(tenant_id: str, job_id: str, data: bytes) -> str`, `stream_export_file(minio_key: str) -> Iterator[bytes]`, `delete_export_file(minio_key: str) -> None`

**BYTES_PER_ROW_ESTIMATE = 180**(概算の係数。設計書「1行あたり150〜200バイト程度」の中央値)。`settings.export_max_size_bytes`のデフォルトは`20 * 1024 * 1024`(env var `EXPORT_MAX_SIZE_BYTES`)。`_LOCK_TIMEOUT_SECONDS = 600`。

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_export_jobs.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import pytest
from app.models.public import Tenant
from app.models.export import ExportJob
from app.services.export_jobs import request_adhoc_export, JobAlreadyRunningError, ExportTooLargeError

TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _tenant():
    t = Tenant(); t.id = TENANT_ID; t.influxdb_org_id = "org-1"; t.influxdb_token = "tok"
    return t

def _db_with_existing_job(status, started_at):
    db = MagicMock()
    existing = ExportJob(tenant_id=TENANT_ID, kind="adhoc", status=status,
                          period_start=datetime(2026,10,1,tzinfo=timezone.utc),
                          period_end=datetime(2026,10,2,tzinfo=timezone.utc))
    existing.started_at = started_at
    db.query.return_value.filter.return_value.first.return_value = existing
    return db, existing

def test_request_adhoc_export_rejects_when_already_processing():
    db, _ = _db_with_existing_job("processing", datetime.now(timezone.utc))
    with pytest.raises(JobAlreadyRunningError):
        request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                              datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)

def test_request_adhoc_export_recovers_stale_lock_past_timeout():
    stale_start = datetime.now(timezone.utc) - timedelta(seconds=700)
    db, existing = _db_with_existing_job("processing", stale_start)
    with patch("app.services.export_jobs.estimate_export_row_count", return_value=10), \
         patch("app.services.export_jobs.threading.Thread") as mock_thread:
        job = request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                    datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)
    assert job.status == "queued"
    mock_thread.return_value.start.assert_called_once()

def test_request_adhoc_export_rejects_when_estimate_exceeds_limit():
    db, existing = _db_with_existing_job("done", datetime.now(timezone.utc))
    with patch("app.services.export_jobs.estimate_export_row_count",
               return_value=1_000_000) as mock_estimate:
        with pytest.raises(ExportTooLargeError):
            request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                  datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)
    assert existing.status == "done"  # 既存行は変更されない
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_export_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.export_jobs'`

- [ ] **Step 3a: Add `export_max_size_bytes` setting**

In `core-api/app/config.py`, add `export_max_size_bytes: int = int(os.environ.get("EXPORT_MAX_SIZE_BYTES", 20 * 1024 * 1024))` following the existing settings' style in that file (read the file first to match the exact pattern used for other int env-var settings there).

- [ ] **Step 3b: Add MinIO export-file functions**

In `core-api/app/services/minio_client.py`, add a new bucket constant usage `settings.minio_export_bucket`(add this setting to `config.py` too, default `"exports"`) and three functions mirroring `upload_firmware`/`stream_firmware`/`delete_firmware`:
- `upload_export_file(tenant_id: str, job_id: str, data: bytes) -> str`: key `f"{tenant_id}/{job_id}"`, same `_ensure_bucket`-style helper against the export bucket, `put_object` with `content_type="text/csv"`.
- `stream_export_file(minio_key: str) -> Iterator[bytes]`: same streaming pattern as `stream_firmware`.
- `delete_export_file(minio_key: str) -> None`: same as `delete_firmware`.

- [ ] **Step 3c: Implement `core-api/app/services/export_jobs.py`**

```python
def request_adhoc_export(db, tenant, start, end, device_ids, group_id) -> ExportJob:
```
Logic: look up the existing `ExportJob` row for `(tenant.id, kind="adhoc")`. If it exists and `status in ("queued", "processing")`:
  - if `started_at` is set and `datetime.now(timezone.utc) - started_at > timedelta(seconds=_LOCK_TIMEOUT_SECONDS)`: treat as a crashed job — set its `status = "failed"`, `error_message = "timed out"`, fall through to create the new job below.
  - otherwise: raise `JobAlreadyRunningError()`.
Then call `estimate_export_row_count(tenant, start, end, device_ids)` (propagates `InfluxUnavailableError` uncaught — fail safe, per Review Focus). Compute `estimated_bytes = estimated_rows * BYTES_PER_ROW_ESTIMATE`; if `estimated_bytes > settings.export_max_size_bytes`, raise `ExportTooLargeError(estimated_bytes, settings.export_max_size_bytes)` **without** creating or modifying the job row. Otherwise, upsert the `(tenant_id, kind="adhoc")` row (reuse the existing row object if found, else create one) with `status="queued"`, the given `period_start`/`period_end`/`device_ids`/`group_id`, `requested_at=now`, clearing `minio_key`/`file_size_bytes`/`error_message`/`started_at`/`completed_at`. `db.add`, `db.commit`. Start `threading.Thread(target=run_export_job, args=(str(job.id),), daemon=True).start()`. Return the job.

```python
def run_export_job(job_id: str) -> None:
```
Opens its own `SessionLocal()`, loads the `ExportJob` by id, sets `status="processing"`, `started_at=now`, commits. Loads the owning `Tenant`. Resolves `device_ids` (if `group_id` is set and `device_ids` is `None`, resolve via `list_group_device_ids` from `app.services.device_groups`, same as the existing `/me/device-locations` endpoint does). Calls `generate_export_csv(tenant, job.period_start, job.period_end, resolved_device_ids)`. On success: if an old `minio_key` existed on this same row before this run, delete it via `delete_export_file` first (this row's slot is being overwritten); upload the new CSV via `upload_export_file`, set `status="done"`, `minio_key`, `file_size_bytes=len(csv_bytes)`, `completed_at=now`, commit. On any exception: set `status="failed"`, `error_message=str(exc)[:500]`, `completed_at=now`, commit (never raise out of this function — it runs in a background thread with no caller to catch it).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_export_jobs.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/export_jobs.py core-api/app/config.py core-api/app/services/minio_client.py core-api/tests/test_export_jobs.py
git commit -m "feat: add adhoc export job orchestration with stale-lock recovery and size gate"
```

---

### Task 4: APIエンドポイント

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`(末尾に新規セクション追加)
- Test: `core-api/tests/test_tenant_portal_exports.py`

**Interfaces:**
- Consumes: `request_adhoc_export`, `JobAlreadyRunningError`, `ExportTooLargeError`(Task 3)
- Consumes: `stream_export_file`, `delete_export_file`(Task 3 / minio_client.py)
- Consumes: `_require_tenant`, `_require_admin`, `SessionLocal`, `Tenant`, `log_audit`(既存、`tenant_portal.py`冒頭を参照)
- Produces (new endpoints under `/tenant-portal`, prefix already `/tenant-portal` from `router = APIRouter(prefix="/tenant-portal", ...)`):
  - `POST /me/exports` body `{start: datetime, end: datetime, device_ids: list[str] | None, group_id: str | None}` → 201 `{id, kind, status, requested_at}` / 409 `{"detail": "現在処理中です"}` / 422 `{"detail": "最大データサイズを超えています。期間やデバイスの絞り込みを実施してください"}`
  - `GET /me/exports` → `list[{id, kind, status, requested_at, completed_at, file_size_bytes, error_message}]`(定期・手動の最大2件、無ければ空リスト)
  - `GET /me/exports/{kind}/download` → `StreamingResponse`(`text/csv`、`Content-Disposition: attachment; filename="export_{kind}.csv"`)。該当行が無いか`status != "done"`なら404
  - `DELETE /me/exports/{kind}` → 204。MinIOファイル削除+行のリセット(`status`等をクリア、または行削除)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_portal_exports.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.export_jobs import JobAlreadyRunningError, ExportTooLargeError

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _admin_token():
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": "admin"})

def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db

def test_request_export_rejects_when_already_running():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export", side_effect=JobAlreadyRunningError()):
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code == 409

def test_request_export_rejects_when_too_large():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export",
               side_effect=ExportTooLargeError(99_999_999, 20_000_000)):
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code == 422

def test_download_export_returns_404_when_not_done():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_tenant_portal_exports.py -v`
Expected: FAIL (404s for all — no matching route yet, or 405)

- [ ] **Step 3: Implement the endpoints in `core-api/app/routers/tenant_portal.py`**

Add a new `# ---- エクスポート ----` section near the end of the file. Add Pydantic request models (`ExportRequest` with `start: datetime`, `end: datetime`, `device_ids: Optional[list[str]]`, `group_id: Optional[str]`). Implement the four endpoints per the Interfaces block, each using `payload["tenant_id"]` to load the `Tenant` via `SessionLocal()` (same pattern as `purge_archived_devices_portal`), `_require_admin` dependency, and `log_audit(...)` calls on request/delete (action names `"request_export"`, `"delete_export"`, resource_type `"export_job"`). The download endpoint uses `_require_tenant` (any role may download, per existing convention that read-only actions are open to all roles while admin-only applies to the actions that change state) and must check `job.tenant_id == payload["tenant_id"]` before streaming (Review Focus: cross-tenant key guessing). Import `request_adhoc_export`, `JobAlreadyRunningError`, `ExportTooLargeError` from `app.services.export_jobs`, and `stream_export_file`, `delete_export_file` from `app.services.minio_client`, and `ExportJob` from `app.models.export`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_tenant_portal_exports.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_exports.py
git commit -m "feat: add tenant-portal export request/list/download/delete endpoints"
```

---

### Task 5: 定期エクスポートのスケジュール管理API・スケジューラ

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`(スケジュール設定の GET/PUT)
- Create: `core-api/app/services/export_scheduler.py`
- Modify: `core-api/app/main.py`(ワーカー起動を追加)
- Test: `core-api/tests/test_tenant_portal_export_schedule.py`
- Test: `core-api/tests/test_export_scheduler.py`

**Interfaces:**
- Consumes: `request_adhoc_export`は使わない。定期実行は専用ロジックで`kind="periodic"`の`ExportJob`行を直接upsertし、Task3の`run_export_job(job_id)`を呼ぶ(ロック・サイズ上限のチェックはアドホックのみの要件であり定期には適用しない設計のため、`request_adhoc_export`を再利用しない)
- Consumes: `app.models.export.ExportSchedule`, `ExportJob`(Task 1)
- Produces: `GET/PUT /tenant-portal/me/export-schedule`(admin限定、body `{frequency: "daily"|"weekly"|"monthly", device_ids, group_id, enabled}`)
- Produces: `run_due_periodic_exports() -> None`(スケジューラのスレッドループ本体から呼ばれる1回分の処理)
- Produces: `start_export_scheduler_worker() -> None`(daemon threadを起動。`billing_batch.py`の`start_billing_batch_worker`と同じ形)

**頻度→期間算出:** `daily`なら直前24時間、`weekly`なら直前7日間、`monthly`なら直前30日間(カレンダー月ではなく固定長。閏月等の精密さは求めない)。**多重実行防止:** `last_run_at`を見て、`frequency`に対応する間隔が経過していないスケジュールはスキップする。

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_export_scheduler.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from app.services.export_scheduler import run_due_periodic_exports

def _schedule(frequency, last_run_at):
    s = MagicMock(tenant_id="11111111-1111-1111-1111-111111111111", frequency=frequency,
                  device_ids=None, group_id=None, enabled=True, last_run_at=last_run_at)
    return s

def test_run_due_periodic_exports_skips_not_yet_due():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [_schedule("daily", recent)]
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
        run_due_periodic_exports()
    mock_run.assert_not_called()

def test_run_due_periodic_exports_runs_when_due():
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = MagicMock()
    db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    db.query.return_value.filter.return_value.all.return_value = [_schedule("daily", old)]
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        run_due_periodic_exports()
    mock_run.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_export_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.export_scheduler'`

- [ ] **Step 3: Implement `core-api/app/services/export_scheduler.py`**

```python
_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "monthly": timedelta(days=30)}

def run_due_periodic_exports() -> None:
```
Opens `SessionLocal()`, queries all `ExportSchedule` rows with `enabled == True`. For each, skip if `last_run_at` is set and `datetime.now(timezone.utc) - last_run_at < _INTERVALS[frequency]`. Otherwise: compute `period_end = now`, `period_start = now - _INTERVALS[frequency]`; upsert the `(tenant_id, kind="periodic")` `ExportJob` row (same upsert approach as `request_adhoc_export` in Task 3, but unconditional — no lock check, no size check) with `status="queued"`, the computed period and the schedule's `device_ids`/`group_id`; commit; set the schedule's `last_run_at = now`; commit; call `run_export_job(str(job.id))` **synchronously** (the scheduler loop already runs off the request path, so no extra thread is needed here — unlike the adhoc path which must respond to the HTTP request immediately).

Also add `start_export_scheduler_worker() -> None` mirroring `start_billing_batch_worker`'s daemon-thread-with-sleep-loop pattern (read that function in `core-api/app/services/billing_batch.py` first to match its exact structure): loop forever, call `run_due_periodic_exports()`, sleep for a fixed interval (reuse whatever polling interval `billing_batch.py`'s worker uses, or `3600` seconds if that file polls more coarsely — daily/weekly/monthly schedules don't need finer granularity than hourly).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_export_scheduler.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Add the schedule management endpoints**

Write the failing test first:

```python
# core-api/tests/test_tenant_portal_export_schedule.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _admin_token():
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": "admin"})

def test_put_export_schedule_rejects_invalid_frequency():
    resp = client.put("/tenant-portal/me/export-schedule",
                       json={"frequency": "hourly", "device_ids": None, "group_id": None, "enabled": True},
                       headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code == 422
```

Run: `pytest core-api/tests/test_tenant_portal_export_schedule.py -v` → expect FAIL (404/405, route doesn't exist).

Implement `GET`/`PUT /tenant-portal/me/export-schedule` in `core-api/app/routers/tenant_portal.py`: a Pydantic body model with `frequency: Literal["daily", "weekly", "monthly"]` (Pydantic's `Literal` validation naturally produces the `422` the test expects for `"hourly"`), `device_ids: Optional[list[str]]`, `group_id: Optional[str]`, `enabled: bool`. `PUT` upserts the tenant's single `ExportSchedule` row (admin only, `log_audit` action `"update_export_schedule"`). `GET` returns the row or a default `{"frequency": None, "device_ids": None, "group_id": None, "enabled": False}` when none exists yet.

Run: `pytest core-api/tests/test_tenant_portal_export_schedule.py -v` → expect PASS (1/1).

- [ ] **Step 6: Wire the worker into startup**

In `core-api/app/main.py`: import `start_export_scheduler_worker` from `app.services.export_scheduler` and call it in `on_startup()` alongside `start_billing_batch_worker()`.

- [ ] **Step 7: Commit**

```bash
git add core-api/app/services/export_scheduler.py core-api/app/routers/tenant_portal.py core-api/app/main.py core-api/tests/test_export_scheduler.py core-api/tests/test_tenant_portal_export_schedule.py
git commit -m "feat: add periodic export scheduler and schedule management endpoint"
```

---

### Task 6: 管理画面UI

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: Task 4/5のAPI(`/me/exports`, `/me/exports/{kind}/download`, `/me/exports/{kind}` DELETE, `/me/export-schedule` GET/PUT)
- Consumes: 既存の`tabs`配列、`switchTab()`、`portalFetch()`、`canEdit`(既存、`tokens`タブの実装を参照)

- [ ] **Step 1: Add the tab entry**

In the `tabs` array inside `portalApp()` (same array the `tokens`/`stats-billing` entries are in), add `{ id: 'exports', icon: '📤', label: 'データエクスポート' }`.

- [ ] **Step 2: Add the tab's state fields**

In `portalApp()`'s returned data object, add: `exportJobs: []` (list from `GET /me/exports`), `exportSchedule: {frequency: null, device_ids: null, group_id: null, enabled: false}`, `exportRequestForm: {start: '', end: '', device_ids: null, group_id: null}`, `exportRequestError: ''`, `requestingExport: false`.

- [ ] **Step 3: Add the tab's view section**

Add `<div x-show="activeTab === 'exports'">...</div>` (placed next to the `tokens` section, following the same Tailwind classes/structure as that section): a card showing the periodic schedule (frequency `<select>` with `daily`/`weekly`/`monthly` options + enabled checkbox + save button calling a new `saveExportSchedule()` method, visible only when `canEdit`), and a table listing up to 2 rows (`periodic`, `adhoc`) from `exportJobs` with columns `種別`/`状態`/`サイズ`/`完了日時` and, per row, a ダウンロード button (visible when `status === 'done'`, link to `/api/tenant-portal/me/exports/{kind}/download` — confirm the exact base path by reading `portalFetch`'s URL-building in Step 4 below) and a 削除 button (visible when `canEdit` and the row exists, calling `deleteExport(kind)`). Below the table, a form for アドホック実行: date/time inputs for `start`/`end`, a submit button calling `requestExport()`, disabled while `requestingExport`, and an error banner showing `exportRequestError` (populated from the `409`/`422` responses' `detail` field).

- [ ] **Step 4: Add the tab's methods**

Add to `portalApp()`'s methods: `async loadExports()` (GET `/me/exports` and `/me/export-schedule`, called from `switchTab` when `tab === 'exports'`, mirroring how other tabs lazy-load on first visit — read `switchTab()`'s existing branches first to match that pattern), `async requestExport()` (POST `/me/exports` with `exportRequestForm`; on error, read the response body's `detail` into `exportRequestError` instead of throwing, since `portalFetch`'s error path needs inspecting first — follow the same error-handling shape `createToken()` uses, but surface the server's exact message rather than a generic one), `async saveExportSchedule()` (PUT `/me/export-schedule`), `async deleteExport(kind)` (confirm dialog, same `confirm('...')` pattern as `revokeToken`, then DELETE `/me/exports/{kind}`, then reload `exportJobs`).

- [ ] **Step 5: Manual verification**

Run the app (`docker compose up` or equivalent per this repo's existing dev workflow) and in a browser: open the テナント管理画面, switch to the new 「データエクスポート」タブ, confirm the schedule form and the (empty) job table render without console errors. Automated UI tests are not part of this stack (no existing JS test harness in `admin-ui/`); this step is a manual check, not a `pytest` run.

- [ ] **Step 6: Commit**

```bash
git add admin-ui/tenant-portal.html
git commit -m "feat: add data export tab to tenant portal UI"
```

---

### Task 7: Billing連携

**Files:**
- Modify: `core-api/app/services/billing.py`(`ITEM_KEYS`)
- Modify: `core-api/app/services/billing_usage.py`(`aggregate_monthly_usage`)
- Test: `core-api/tests/test_billing_usage_exports.py`

**Interfaces:**
- Consumes: `app.models.export.ExportJob`, `ExportSchedule`(Task 1)
- Produces: `aggregate_monthly_usage(...)`の返り値に`"export_base_fee"`・`"export_retained_size_mb"`キーを追加(既存の呼び出し元・シグネチャは変更しない)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_billing_usage_exports.py
import math
from unittest.mock import MagicMock
from app.services.billing_usage import _export_usage_snapshot

def test_export_usage_snapshot_base_fee_on_when_schedule_enabled():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(enabled=True)
    db.query.return_value.filter.return_value.all.return_value = []
    result = _export_usage_snapshot(db, "tenant-1")
    assert result["export_base_fee"] == 1

def test_export_usage_snapshot_base_fee_off_without_schedule():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    result = _export_usage_snapshot(db, "tenant-1")
    assert result["export_base_fee"] == 0

def test_export_usage_snapshot_sums_retained_size_rounded_up():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    rows = [MagicMock(file_size_bytes=1_500_000), MagicMock(file_size_bytes=600_000)]
    db.query.return_value.filter.return_value.all.return_value = rows
    result = _export_usage_snapshot(db, "tenant-1")
    assert result["export_retained_size_mb"] == math.ceil(2_100_000 / (1024 * 1024))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest core-api/tests/test_billing_usage_exports.py -v`
Expected: FAIL with `ImportError: cannot import name '_export_usage_snapshot'`

- [ ] **Step 3: Implement `_export_usage_snapshot` and wire it in**

In `core-api/app/services/billing_usage.py`, add:

```python
def _export_usage_snapshot(db, tenant_id: str) -> dict[str, int]:
```
Query `ExportSchedule` by `tenant_id`; `export_base_fee = 1` if the row exists and `enabled` is `True`, else `0`. Query all `ExportJob` rows for `tenant_id` with `status == "done"`; sum `file_size_bytes` (treat `None` as `0`); `export_retained_size_mb = math.ceil(total_bytes / (1024 * 1024))` (use `math.ceil`, import `math`). Return `{"export_base_fee": export_base_fee, "export_retained_size_mb": export_retained_size_mb}`.

In `aggregate_monthly_usage(...)`, merge `_export_usage_snapshot(db, tenant_id)`'s two keys into the returned dict (add `**_export_usage_snapshot(db, tenant_id)` to the dict literal, or equivalent).

In `core-api/app/services/billing.py`, add `"export_base_fee"` and `"export_retained_size_mb"` to the `ITEM_KEYS` tuple.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest core-api/tests/test_billing_usage_exports.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run the full test suite**

Run: `pytest core-api/tests -v`
Expected: all tests pass (no regressions in `test_billing_batch.py`, `test_billing_api.py`, etc. from the `ITEM_KEYS`/`aggregate_monthly_usage` changes)

- [ ] **Step 6: Commit**

```bash
git add core-api/app/services/billing.py core-api/app/services/billing_usage.py core-api/tests/test_billing_usage_exports.py
git commit -m "feat: bill the data export feature via export_base_fee and export_retained_size_mb"
```
