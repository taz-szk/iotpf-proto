"""テレメトリCSVエクスポート用: 行数概算・CSV生成サービス。

InfluxDBへのクエリが失敗した場合(非200応答・例外)は原則InfluxUnavailableErrorを
投げる。サイズ概算は「キャップ超過を見逃さない」ために失敗時に0を返してはならない
(呼び出し側がフェイルセーフに失敗できるようにするため)。

例外: テレメトリ用バケットがまだ存在しない場合(セルフサインアップのテナントは
最初のデータ点がingestされるまでバケットが作られない)は「データ0件」として扱い、
概算は0、生成はヘッダのみのCSVを返す。
"""
import csv
import io
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.models.public import Tenant
from app.routers.stats import _parse_influx_csv_scalar

_FIXED_EMPTY_HEADER = ["device_name", "datetime", "latitude", "longitude"]

# 出力CSVから除外するInfluxDBのシステムカラム(device_idも除外。device_nameのみ使う)。
# pivot()後も_measurementは列として残るため除外する。
_EXCLUDED_COLUMNS = {"", "result", "table", "_start", "_stop", "_measurement", "device_id"}


class InfluxUnavailableError(Exception):
    """概算・生成のいずれかでInfluxDBへのクエリが失敗した場合に投げる。"""


class _BucketNotFound(Exception):
    """クエリ対象のバケットが存在しない(=まだデータが1件も無い)ことを表す内部例外。"""


def _flux_string_literal(value: str) -> str:
    """Flux文字列リテラル用にエスケープする(多層防御。呼び出し側でdevice_idは
    テナントの実在デバイスに検証済みだが、念のため\\と"と${をエスケープする)。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


def _flux_time(dt: datetime) -> str:
    """Fluxのdatetimeリテラル(RFC3339, オフセット必須)を返す。naiveな値はUTCとみなす
    (API層でnaiveな値は拒否しているが、内部呼び出しへの多層防御)。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _device_id_filter_line(device_ids: list[str] | None) -> str:
    if not device_ids:
        return ""
    or_expr = " or ".join(f"r.device_id == {_flux_string_literal(device_id)}" for device_id in device_ids)
    return f'  |> filter(fn: (r) => {or_expr})\n'


def _is_bucket_not_found(status_code: int, body: str) -> bool:
    """InfluxDB 2.xはバケット未作成時に404と
    {"code":"not found","message":"failed to initialize execute state: could not find bucket \\"telemetry\\""}
    を返す。"""
    if status_code != 404:
        return False
    lowered = body.lower()
    return "could not find bucket" in lowered or ("bucket" in lowered and "not found" in lowered)


def _post_influx_query(tenant: Tenant, query: str) -> str:
    if not tenant.influxdb_org_id or not tenant.influxdb_token:
        # InfluxDBのorg自体が未プロビジョニング = データが存在し得ない
        raise _BucketNotFound()
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={tenant.influxdb_org_id}",
            headers={"Authorization": f"Token {tenant.influxdb_token}", "Content-Type": "application/json"},
            json={"query": query, "type": "flux"},
            timeout=15.0,
        )
    except Exception as e:
        raise InfluxUnavailableError(str(e)) from e
    if resp.status_code != 200:
        if _is_bucket_not_found(resp.status_code, resp.text):
            raise _BucketNotFound()
        raise InfluxUnavailableError(f"InfluxDB query failed with status {resp.status_code}: {resp.text}")
    return resp.text


def estimate_export_row_count(
    tenant: Tenant, start: datetime, end: datetime, device_ids: list[str] | None,
) -> int:
    """指定条件でエクスポートした場合に生成されるCSV行数(=テレメトリの(_time, device_id)の
    distinctな組み合わせ数)を概算する。device_ids=[]の場合はInfluxDBに問い合わせず0を返す。"""
    if device_ids is not None and len(device_ids) == 0:
        return 0

    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {_flux_time(start)}, stop: {_flux_time(end)})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        f'{_device_id_filter_line(device_ids)}'
        '  |> map(fn: (r) => ({r with _row_key: string(v: r._time) + "|" + r.device_id}))\n'
        '  |> keep(columns: ["_row_key"])\n'
        '  |> group()\n'
        '  |> distinct(column: "_row_key")\n'
        '  |> group()\n'
        '  |> count()\n'
    )
    try:
        text = _post_influx_query(tenant, query)
    except _BucketNotFound:
        return 0
    return _parse_influx_csv_scalar(text)


def _parse_influx_annotated_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """InfluxDBのannotated CSVを解析し、(全テーブルの列名の和集合(出現順), 行dictのリスト)を返す。

    - 値はRFC 4180に従いクォートされ得る(カンマを含むdevice_name等)ため、csvモジュールで解析する。
    - pivot()後、デバイスごとにフィールド集合が異なるとテーブルブロックが分かれ、ブロックごとに
      #group/#datatype/#default注釈とヘッダ行が出力される。注釈行(#始まり)を見たら次の
      非注釈行を新しいヘッダとして読み直し、各データ行はその時点のヘッダに対応付ける。
    """
    all_columns: list[str] = []
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    expect_header = True

    for record in csv.reader(io.StringIO(text, newline="")):
        if not record or all(not cell.strip() for cell in record):
            # 空行はテーブルブロック(またはresult)の区切り。次の非注釈行はヘッダ。
            expect_header = True
            continue
        if record[0].startswith("#"):
            expect_header = True
            continue
        if expect_header:
            header = [c.strip() for c in record]
            expect_header = False
            for col in header:
                if col not in seen:
                    seen.add(col)
                    all_columns.append(col)
            continue
        rows.append(dict(zip(header, record)))
    return all_columns, rows


def generate_export_csv(
    tenant: Tenant, start: datetime, end: datetime, device_ids: list[str] | None,
) -> bytes:
    """指定条件のテレメトリをCSV(UTF-8, CRLF改行)として生成する。device_ids=[]の場合や
    バケット未作成の場合はInfluxDBのデータが無いので、固定の最小ヘッダのみを返す
    (対象データが無いのでフィールド列を発見する手段が無いため)。"""
    empty_csv = (",".join(_FIXED_EMPTY_HEADER) + "\r\n").encode("utf-8")
    if device_ids is not None and len(device_ids) == 0:
        return empty_csv

    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {_flux_time(start)}, stop: {_flux_time(end)})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        f'{_device_id_filter_line(device_ids)}'
        '  |> pivot(rowKey: ["_time", "device_id", "device_name"], columnKey: ["_field"], valueColumn: "_value")\n'
        '  |> group()\n'
        '  |> sort(columns: ["device_name", "_time"])\n'
    )
    try:
        text = _post_influx_query(tenant, query)
    except _BucketNotFound:
        return empty_csv
    influx_columns, influx_rows = _parse_influx_annotated_csv(text)
    if not influx_columns:
        return empty_csv

    # 全テーブルの列の和集合からシステム列を除外し、device_nameを先頭、_time(datetime)を2番目に並べる
    output_columns = [c for c in influx_columns if c not in _EXCLUDED_COLUMNS]
    ordered = ["device_name", "_time"] + [c for c in output_columns if c not in ("device_name", "_time")]
    out_header = ["datetime" if c == "_time" else c for c in ordered]

    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(out_header)
    for row in influx_rows:
        writer.writerow([(row.get(c) or "").strip() for c in ordered])

    return buf.getvalue().encode("utf-8")
