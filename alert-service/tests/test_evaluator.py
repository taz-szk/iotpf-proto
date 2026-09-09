from unittest.mock import patch
from app.evaluator import check_condition, evaluate_rule, _flux_values_by_device

def test_check_above_true():
    assert check_condition(85.0, "above", 80.0) is True

def test_check_above_false():
    assert check_condition(75.0, "above", 80.0) is False

def test_check_below_true():
    assert check_condition(10.0, "below", 20.0) is True

def test_check_equal_true():
    assert check_condition(25.0, "equal", 25.0) is True

def test_check_no_threshold():
    assert check_condition(85.0, "above", None) is False

def test_evaluate_rule_consecutive_alert():
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": "dev-001", "group_id": None,
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 3, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device", return_value={"dev-001": [85.0, 82.0, 81.0, 70.0]}):
        result, value = evaluate_rule(rule, "org-001", "token-001")
    assert result is True
    assert value == 85.0

def test_evaluate_rule_consecutive_no_alert():
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": "dev-001", "group_id": None,
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 3, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device", return_value={"dev-001": [85.0, 70.0, 85.0]}):
        result, value = evaluate_rule(rule, "org-001", "token-001")
    assert result is False

def test_evaluate_rule_device_offline_skipped():
    rule = {
        "condition": "device_offline", "threshold": None, "device_id": None, "group_id": None,
        "sensor_key": "device", "trigger_mode": "consecutive",
        "consecutive_count": 1, "duration_sec": 60,
    }
    result, value = evaluate_rule(rule, "org-001", "token-001")
    assert result is False
    assert value is None

def test_evaluate_rule_no_data():
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": "dev-001", "group_id": None,
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 3, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device", return_value={}):
        result, value = evaluate_rule(rule, "org-001", "token-001")
    assert result is False
    assert value is None

def test_evaluate_rule_all_devices_when_no_device_and_no_group():
    """device_id も group_id も無いルールは全デバイス対象（後方互換性）。"""
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": None, "group_id": None,
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 1, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device", return_value={"dev-001": [85.0]}) as mock_flux:
        result, value = evaluate_rule(rule, "org-001", "token-001")
    assert result is True
    assert value == 85.0
    # device_ids に None（全デバイス対象）が渡っていること
    assert mock_flux.call_args[0][2] is None

def test_evaluate_rule_group_uses_group_device_ids():
    """group_id ありのルールは group_device_ids で渡されたデバイスのみを評価対象にする。"""
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": None, "group_id": "group-001",
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 1, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device", return_value={"dev-002": [85.0]}) as mock_flux:
        result, value = evaluate_rule(
            rule, "org-001", "token-001",
            group_device_ids=["dev-002", "dev-003"],
        )
    assert result is True
    assert value == 85.0
    # group_device_ids で渡されたデバイス一覧が device_ids として渡っていること
    assert mock_flux.call_args[0][2] == ["dev-002", "dev-003"]

def test_evaluate_rule_group_no_devices_resolved_no_alert():
    """group_id ありのルールで group_device_ids が空/未解決の場合はアラートを発火しない
    （グループ外デバイスへの誤発火を防ぐ）。"""
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": None, "group_id": "group-001",
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 1, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device") as mock_flux:
        result, value = evaluate_rule(rule, "org-001", "token-001", group_device_ids=[])
    assert result is False
    assert value is None
    mock_flux.assert_not_called()

def test_evaluate_rule_device_id_takes_precedence_over_group_id():
    """device_id と group_id が両方入っていた場合は device_id が優先される。"""
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": "dev-001", "group_id": "group-001",
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 1, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_values_by_device", return_value={"dev-001": [85.0]}) as mock_flux:
        result, value = evaluate_rule(
            rule, "org-001", "token-001",
            group_device_ids=["dev-002", "dev-003"],
        )
    assert result is True
    assert mock_flux.call_args[0][2] == ["dev-001"]


def test_flux_values_by_device_single_device_uses_equality_filter():
    with patch("app.evaluator.InfluxDBClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.query_api.return_value.query.return_value = []
        _flux_values_by_device("org-001", "token-001", ["dev-001"], "temperature", 120)
        flux_query = mock_client.query_api.return_value.query.call_args[0][0]
    assert 'r.device_id == "dev-001"' in flux_query

def test_flux_values_by_device_multiple_devices_uses_regex_filter():
    with patch("app.evaluator.InfluxDBClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.query_api.return_value.query.return_value = []
        _flux_values_by_device("org-001", "token-001", ["dev-001", "dev-002"], "temperature", 120)
        flux_query = mock_client.query_api.return_value.query.call_args[0][0]
    assert r'r.device_id =~ /^(dev\-001|dev\-002)$/' in flux_query

def test_flux_values_by_device_none_means_all_devices():
    with patch("app.evaluator.InfluxDBClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.query_api.return_value.query.return_value = []
        _flux_values_by_device("org-001", "token-001", None, "temperature", 120)
        flux_query = mock_client.query_api.return_value.query.call_args[0][0]
    assert "device_id" not in flux_query
