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

def test_estimate_export_row_count_parses_multi_device_multi_table_response():
    """estimate_export_row_count自体はInfluxDB側で既に集計された単一のスカラー値を
    受け取るだけだが、その値が2台のデバイス×2時刻=4件のdistinct(_time,device_id)組を
    表す現実的なInfluxDB count()レスポンス(フルアノテーション付き)からも正しく
    パースできることを確認する(単純な最小CSVだけでなく)。"""
    resp = MagicMock(
        status_code=200,
        text=(
            "#group,false,false,true,true,false,true,true,false,false\r\n"
            "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,string,string,double\r\n"
            "#default,_result,,,,,,,\r\n"
            ",result,table,_value\r\n"
            ",_result,0,4\r\n"
        ),
    )
    with patch("app.services.export_csv.httpx.post", return_value=resp) as mock_post:
        n = estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                       datetime(2026,10,2,tzinfo=timezone.utc), device_ids=["dev-1", "dev-2"])
    assert n == 4
    # クエリが両デバイスをOR条件で含み、_timeとdevice_idの組をdistinctカウントしていること
    # (単一列のdistinct()では(_time, device_id)の組を区別できないため、キー結合している)
    query = mock_post.call_args.kwargs["json"]["query"]
    assert 'r.device_id == "dev-1"' in query
    assert 'r.device_id == "dev-2"' in query
    assert "r._time" in query and "r.device_id" in query
    assert "distinct(column:" in query

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

def test_generate_export_csv_parses_multi_device_multi_row_without_repeated_header():
    """InfluxDBの実際のannotated CSVは、複数デバイス分の行が同じスキーマ(同じ列集合・型)
    であれば単一のテーブル群としてヘッダー行を1回だけ出し、_table列の値だけが0,1,...と
    増えて区別される(既存コードのapp.services.grafana.list_archived_devicesが
    `_ARCHIVED_DEVICES_CSV`で検証している挙動と同じ: table=0とtable=1が同一ヘッダー配下に
    並ぶ)。generate_export_csvのpivotは全行で同じ列集合(_time, device_id, device_name,
    + 共通フィールド)を生成するため、通常は複数デバイス・複数時刻でもヘッダーは1回だけ
    しか出ない。本テストはヘッダー非繰り返し・2テーブル(2デバイス)×2時刻=4行が
    全て正しくCSV化されることを検証する。"""
    influx_csv = (
        "#group,false,false,true,true,false,false,true,true,false,false\r\n"
        "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,string,string,string,double,double\r\n"
        "#default,_result,,,,,,,,,\r\n"
        ",result,table,_start,_stop,_time,device_id,device_name,latitude,longitude\r\n"
        ",_result,0,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,2026-10-01T10:00:00Z,dev-1,Sensor-1,35.1,139.1\r\n"
        ",_result,0,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,2026-10-01T11:00:00Z,dev-1,Sensor-1,35.2,139.2\r\n"
        ",_result,1,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,2026-10-01T10:30:00Z,dev-2,Sensor-2,36.1,140.1\r\n"
        ",_result,1,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,2026-10-01T11:30:00Z,dev-2,Sensor-2,36.2,140.2\r\n"
    )
    resp = MagicMock(status_code=200, text=influx_csv)
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        csv_bytes = generate_export_csv(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                         datetime(2026,10,2,tzinfo=timezone.utc), device_ids=["dev-1", "dev-2"])
    lines = csv_bytes.decode("utf-8").split("\r\n")
    lines = [l for l in lines if l]
    assert lines[0] == "device_name,datetime,latitude,longitude"
    # ヘッダーは1回だけ(繰り返されない)で、データ行が4行すべて出力されていること
    assert lines.count("device_name,datetime,latitude,longitude") == 1
    assert len(lines) == 5
    assert "Sensor-1,2026-10-01T10:00:00Z,35.1,139.1" in lines
    assert "Sensor-1,2026-10-01T11:00:00Z,35.2,139.2" in lines
    assert "Sensor-2,2026-10-01T10:30:00Z,36.1,140.1" in lines
    assert "Sensor-2,2026-10-01T11:30:00Z,36.2,140.2" in lines


# ---------------------------------------------------------------------------
# 最終レビュー指摘への回帰テスト
# ---------------------------------------------------------------------------

# InfluxDB 2.x(influxdb:2-alpine)でバケット未作成のorgへクエリした際の実際の応答(実機で採取)
_BUCKET_NOT_FOUND_BODY = '{"code":"not found","message":"failed to initialize execute state: could not find bucket \\"telemetry\\""}'


def test_estimate_returns_zero_when_bucket_not_found():
    """セルフサインアップのテナントは最初のデータ点がingestされるまでバケットが無い。
    その場合はエラーではなく0件として扱う。"""
    resp = MagicMock(status_code=404, text=_BUCKET_NOT_FOUND_BODY)
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        n = estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                       datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)
    assert n == 0


def test_generate_returns_header_only_when_bucket_not_found():
    resp = MagicMock(status_code=404, text=_BUCKET_NOT_FOUND_BODY)
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        csv_bytes = generate_export_csv(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                         datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)
    assert csv_bytes == b"device_name,datetime,latitude,longitude\r\n"


def test_estimate_returns_zero_when_influx_org_not_provisioned():
    t = Tenant(); t.influxdb_org_id = None; t.influxdb_token = None
    with patch("app.services.export_csv.httpx.post") as mock_post:
        n = estimate_export_row_count(t, datetime(2026,10,1,tzinfo=timezone.utc),
                                       datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)
    assert n == 0
    mock_post.assert_not_called()


def test_estimate_still_raises_on_other_404_or_400():
    for resp in (MagicMock(status_code=404, text='{"code":"not found","message":"organization not found"}'),
                 MagicMock(status_code=400, text='{"code":"invalid","message":"compilation failed"}')):
        with patch("app.services.export_csv.httpx.post", return_value=resp):
            with pytest.raises(InfluxUnavailableError):
                estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                           datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None)


def test_query_uses_utc_offset_datetime_literals():
    """Fluxのdatetimeリテラルはオフセット必須。aware(JST)はUTCへ変換し、naiveはUTCとみなして
    いずれもZ付きで埋め込む(オフセット無しだとInfluxDBは400 compilation failedを返す)。"""
    from datetime import timedelta
    jst = timezone(timedelta(hours=9))
    resp = MagicMock(status_code=200, text=",result,table,_value\r\n,_result,0,1\r\n")
    with patch("app.services.export_csv.httpx.post", return_value=resp) as mock_post:
        estimate_export_row_count(_tenant(), datetime(2026,10,1,9,0,tzinfo=jst),
                                   datetime(2026,10,1,10,0), device_ids=None)
    query = mock_post.call_args.kwargs["json"]["query"]
    assert "range(start: 2026-10-01T00:00:00Z, stop: 2026-10-01T10:00:00Z)" in query


def test_device_id_values_are_escaped_in_flux():
    resp = MagicMock(status_code=200, text=",result,table,_value\r\n,_result,0,1\r\n")
    with patch("app.services.export_csv.httpx.post", return_value=resp) as mock_post:
        estimate_export_row_count(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                   datetime(2026,10,2,tzinfo=timezone.utc),
                                   device_ids=['x") |> to(bucket: "telemetry") //'])
    query = mock_post.call_args.kwargs["json"]["query"]
    assert 'r.device_id == "x\\") |> to(bucket: \\"telemetry\\") //"' in query


# 実機(influxdb:2-alpine)で、フィールド集合が異なる2デバイス(片方は名前にカンマ)をpivotした
# 際の実際の応答。テーブルごとにヘッダ行が出て空行で区切られ、_measurement列が残る。
_REAL_MULTI_SCHEMA_CSV = (
    ",result,table,_start,_stop,_time,_measurement,device_id,device_name,latitude,longitude,temperature\r\n"
    ',_result,0,2026-10-01T00:00:00Z,2026-10-03T00:00:00Z,2026-10-01T10:00:00Z,telemetry,dev-1,"Tokyo, Bldg A",35.1,139.1,21.5\r\n'
    ',_result,0,2026-10-01T00:00:00Z,2026-10-03T00:00:00Z,2026-10-01T11:00:00Z,telemetry,dev-1,"Tokyo, Bldg A",35.2,139.2,22\r\n'
    "\r\n"
    ",result,table,_start,_stop,_time,_measurement,device_id,device_name,humidity,latitude,longitude\r\n"
    ",_result,1,2026-10-01T00:00:00Z,2026-10-03T00:00:00Z,2026-10-01T10:30:00Z,telemetry,dev-2,Osaka,60,34.6,135.5\r\n"
    "\r\n"
)

# 同じ内容をannotation付き(#group/#datatype/#default)で返した場合
_REAL_MULTI_SCHEMA_ANNOTATED_CSV = (
    "#group,false,false,true,true,false,true,true,true,false,false,false\r\n"
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,string,string,string,double,double,double\r\n"
    "#default,_result,,,,,,,,,,\r\n"
    ",result,table,_start,_stop,_time,_measurement,device_id,device_name,latitude,longitude,temperature\r\n"
    ',,0,2026-10-01T00:00:00Z,2026-10-03T00:00:00Z,2026-10-01T10:00:00Z,telemetry,dev-1,"Tokyo, Bldg A",35.1,139.1,21.5\r\n'
    ',,0,2026-10-01T00:00:00Z,2026-10-03T00:00:00Z,2026-10-01T11:00:00Z,telemetry,dev-1,"Tokyo, Bldg A",35.2,139.2,22\r\n'
    "\r\n"
    "#group,false,false,true,true,false,true,true,true,false,false,false\r\n"
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,string,string,string,long,double,double\r\n"
    "#default,_result,,,,,,,,,,\r\n"
    ",result,table,_start,_stop,_time,_measurement,device_id,device_name,humidity,latitude,longitude\r\n"
    ",,1,2026-10-01T00:00:00Z,2026-10-03T00:00:00Z,2026-10-01T10:30:00Z,telemetry,dev-2,Osaka,60,34.6,135.5\r\n"
    "\r\n"
)


@pytest.mark.parametrize("influx_csv", [_REAL_MULTI_SCHEMA_CSV, _REAL_MULTI_SCHEMA_ANNOTATED_CSV],
                         ids=["plain", "annotated"])
def test_generate_export_csv_handles_multi_schema_tables_quoted_commas_and_measurement(influx_csv):
    import csv as _csv, io as _io
    resp = MagicMock(status_code=200, text=influx_csv)
    with patch("app.services.export_csv.httpx.post", return_value=resp):
        csv_bytes = generate_export_csv(_tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                         datetime(2026,10,3,tzinfo=timezone.utc), device_ids=None)
    rows = list(_csv.reader(_io.StringIO(csv_bytes.decode("utf-8"), newline="")))
    header, data = rows[0], rows[1:]
    # 列は全テーブルの和集合。_measurement等のシステム列は含まない
    assert header == ["device_name", "datetime", "latitude", "longitude", "temperature", "humidity"]
    # 2つ目のヘッダ行がデータ行として出力されていないこと
    assert len(data) == 3
    by_key = {(r[0], r[1]): dict(zip(header, r)) for r in data}
    tokyo = by_key[("Tokyo, Bldg A", "2026-10-01T10:00:00Z")]
    assert tokyo["latitude"] == "35.1" and tokyo["temperature"] == "21.5" and tokyo["humidity"] == ""
    osaka = by_key[("Osaka", "2026-10-01T10:30:00Z")]
    # 2つ目のテーブルの値は2つ目のヘッダに従って対応付けられる(位置ずれしない)
    assert osaka["humidity"] == "60" and osaka["latitude"] == "34.6" and osaka["longitude"] == "135.5"
    assert osaka["temperature"] == ""
    # カンマを含む名前はRFC 4180に従いクォートされて出力される
    assert b'"Tokyo, Bldg A",2026-10-01T10:00:00Z' in csv_bytes
