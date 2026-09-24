import pytest


@pytest.fixture(autouse=True)
def _no_delivery_status_db(monkeypatch):
    # 通知の配信結果の記録(record_notify_status)は、DBの無いテスト環境では何もしない。
    # 記録そのもののテストはtest_delivery_status.pyで個別に差し替えて行う。
    monkeypatch.setattr("app.scheduler.record_notify_status", lambda *a, **k: None)
