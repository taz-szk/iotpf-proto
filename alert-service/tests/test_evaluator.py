from unittest.mock import patch
from app.evaluator import check_condition, evaluate_rule

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
        "condition": "above", "threshold": 80.0, "device_id": "dev-001",
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 3, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_recent_values", return_value=[85.0, 82.0, 81.0, 70.0]):
        result, value = evaluate_rule(rule, "org-001")
    assert result is True
    assert value == 85.0

def test_evaluate_rule_consecutive_no_alert():
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": "dev-001",
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 3, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_recent_values", return_value=[85.0, 70.0, 85.0]):
        result, value = evaluate_rule(rule, "org-001")
    assert result is False

def test_evaluate_rule_device_offline_skipped():
    rule = {
        "condition": "device_offline", "threshold": None, "device_id": None,
        "sensor_key": "device", "trigger_mode": "consecutive",
        "consecutive_count": 1, "duration_sec": 60,
    }
    result, value = evaluate_rule(rule, "org-001")
    assert result is False
    assert value is None

def test_evaluate_rule_no_data():
    rule = {
        "condition": "above", "threshold": 80.0, "device_id": "dev-001",
        "sensor_key": "temperature", "trigger_mode": "consecutive",
        "consecutive_count": 3, "duration_sec": 60,
    }
    with patch("app.evaluator._flux_recent_values", return_value=[]):
        result, value = evaluate_rule(rule, "org-001")
    assert result is False
    assert value is None
