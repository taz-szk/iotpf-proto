"""テレメトリCSVエクスポート用: 行数概算・CSV生成サービス。

InfluxDBへのクエリが失敗した場合(非200応答・例外)は常にInfluxUnavailableErrorを
投げる。サイズ概算は「キャップ超過を見逃さない」ために失敗時に0を返してはならない
(呼び出し側がフェイルセーフに失敗できるようにするため)。
"""
import csv
import io
from datetime import datetime

import httpx

from app.config import settings
from app.models.public import Tenant
from app.routers.stats import _parse_influx_csv_scalar

_FIXED_EMPTY_HEADER = ["device_name", "datetime", "latitude", "longitude"]

# 出力CSVから除外するInfluxDBのシステムカラム(device_idも除外。device_nameのみ使う)
_EXCLUDED_COLUMNS = {"result", "table", "_start", "_stop", "device_id"}


class InfluxUnavailableError(Exception):
    """概算・生成のいずれかでInfluxDBへのクエリが失敗した場合に投げる。"""


def _device_id_filter_line(device_ids: list[str] | None) -> str:
    if not device_ids:
        return ""
    or_expr = " or ".join(f'r.device_id == "{device_id}"' for device_id in device_ids)
    return f'  |> filter(fn: (r) => {or_expr})\n'


def _post_influx_query(tenant: Tenant, query: str) -> str:
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
        f'  |> range(start: {start.isoformat()}, stop: {end.isoformat()})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        f'{_device_id_filter_line(device_ids)}'
        '  |> map(fn: (r) => ({r with _row_key: string(v: r._time) + "|" + r.device_id}))\n'
        '  |> keep(columns: ["_row_key"])\n'
        '  |> group()\n'
        '  |> distinct(column: "_row_key")\n'
        '  |> group()\n'
        '  |> count()\n'
    )
    text = _post_influx_query(tenant, query)
    return _parse_influx_csv_scalar(text)


def _parse_influx_annotated_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """InfluxDBのannotated CSVを解析し、(header, rows)を返す。#始まりの行は注釈なので
    スキップし、最初の非#行をヘッダとする。"""
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(",")
        if header is None:
            header = parts
            continue
        rows.append(parts)
    return header or [], rows


def generate_export_csv(
    tenant: Tenant, start: datetime, end: datetime, device_ids: list[str] | None,
) -> bytes:
    """指定条件のテレメトリをCSV(UTF-8, CRLF改行)として生成する。device_ids=[]の場合は
    InfluxDBに問い合わせず、固定の最小ヘッダのみを返す(対象データが無いのでフィールド
    列を発見する手段が無いため)。"""
    if device_ids is not None and len(device_ids) == 0:
        return (",".join(_FIXED_EMPTY_HEADER) + "\r\n").encode("utf-8")

    query = (
        'from(bucket: "telemetry")\n'
        f'  |> range(start: {start.isoformat()}, stop: {end.isoformat()})\n'
        '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
        f'{_device_id_filter_line(device_ids)}'
        '  |> pivot(rowKey: ["_time", "device_id", "device_name"], columnKey: ["_field"], valueColumn: "_value")\n'
        '  |> sort(columns: ["device_name", "_time"])\n'
    )
    text = _post_influx_query(tenant, query)
    influx_header, influx_rows = _parse_influx_annotated_csv(text)

    # InfluxDBの列順を保ちつつ、システム列を除外し_timeをdatetimeに改名した出力列名リストを作る
    output_columns: list[str] = []
    for col in influx_header:
        col = col.strip()
        if not col or col in _EXCLUDED_COLUMNS:
            continue
        output_columns.append(col)
    # device_nameを先頭、_time(datetime)を2番目に並べる
    ordered = ["device_name", "_time"] + [c for c in output_columns if c not in ("device_name", "_time")]
    out_header = ["datetime" if c == "_time" else c for c in ordered]

    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(out_header)
    for parts in influx_rows:
        row = dict(zip(influx_header, parts))
        writer.writerow([row.get(c, "").strip() for c in ordered])

    return buf.getvalue().encode("utf-8")
