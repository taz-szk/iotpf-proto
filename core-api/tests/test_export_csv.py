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
