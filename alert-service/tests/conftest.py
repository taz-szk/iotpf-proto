import pytest


@pytest.fixture(autouse=True)
def _no_delivery_status_db(monkeypatch):
    # 通知の配信結果の記録(record_notify_status)は、DBの無いテスト環境では何もしない。
    # 記録そのもののテストはtest_delivery_status.pyで個別に差し替えて行う。
    monkeypatch.setattr("app.scheduler.record_notify_status", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_admin_notice_side_effects(monkeypatch):
    # 管理者への失敗通知(宛先の取得・メール送信・通知済みの記録)は、既定では何もしない。
    # 個別のテストで必要なものだけ差し替える(test_failure_notice.py)。
    monkeypatch.setattr("app.scheduler.get_tenant_admin_contacts", lambda tenant_id: {"tenant_name": "", "emails": []})
    monkeypatch.setattr("app.scheduler.send_delivery_failure_email", lambda *a, **k: None)
    monkeypatch.setattr("app.scheduler.record_admin_notice", lambda *a, **k: None)
