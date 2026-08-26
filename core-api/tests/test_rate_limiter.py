import time
from unittest.mock import patch, MagicMock
import pytest
import app.services.rate_limiter as rl


@pytest.fixture(autouse=True)
def clear_state():
    """各テスト前後にレート制限の状態をリセットする。"""
    rl._failures.clear()
    yield
    rl._failures.clear()


# ---------- unit tests for rate_limiter module ----------

def test_not_limited_initially():
    assert not rl.is_rate_limited("test:1.2.3.4")


def test_record_failure_increments():
    key = "test:1.2.3.4"
    for _ in range(rl.MAX_FAILURES - 1):
        rl.record_failure(key)
    assert not rl.is_rate_limited(key)

    rl.record_failure(key)
    assert rl.is_rate_limited(key)


def test_clear_failures_unlocks():
    key = "test:1.2.3.4"
    for _ in range(rl.MAX_FAILURES):
        rl.record_failure(key)
    assert rl.is_rate_limited(key)

    rl.clear_failures(key)
    assert not rl.is_rate_limited(key)


def test_old_failures_evicted():
    key = "test:1.2.3.4"
    past = time.monotonic() - rl.WINDOW_SEC - 1
    with rl._lock:
        rl._failures[key] = [past] * rl.MAX_FAILURES

    assert not rl.is_rate_limited(key)


def test_different_keys_independent():
    key_a = "test:1.1.1.1"
    key_b = "test:2.2.2.2"
    for _ in range(rl.MAX_FAILURES):
        rl.record_failure(key_a)

    assert rl.is_rate_limited(key_a)
    assert not rl.is_rate_limited(key_b)


# ---------- endpoint integration tests ----------

def test_platform_login_rate_limited(client):
    """5回失敗後は 429 を返す。"""
    with patch("app.routers.auth.SessionLocal") as mock_session, \
         patch("app.routers.auth.verify_password", return_value=False):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_user = MagicMock()
        mock_user.is_active = True
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        for _ in range(rl.MAX_FAILURES):
            resp = client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
            assert resp.status_code == 401

        resp = client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
        assert resp.status_code == 429


def test_platform_login_success_clears_counter(client):
    """失敗後に成功するとカウンターがリセットされ、次の失敗は 429 にならない。"""
    with patch("app.routers.auth.SessionLocal") as mock_session, \
         patch("app.routers.auth.verify_password") as mock_vp, \
         patch("app.routers.auth.ensure_platform_admin_in_grafana"):
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_db
        mock_user = MagicMock()
        mock_user.id = "11111111-1111-1111-1111-111111111111"
        mock_user.email = "a@b.com"
        mock_user.is_active = True
        mock_user.token_version = 1
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        mock_vp.return_value = False
        for _ in range(rl.MAX_FAILURES - 1):
            client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})

        mock_vp.return_value = True
        resp = client.post("/auth/login", json={"email": "a@b.com", "password": "correct"})
        assert resp.status_code == 200

        mock_vp.return_value = False
        resp = client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
        assert resp.status_code == 401  # 429 ではなく 401（カウンターリセット済み）
