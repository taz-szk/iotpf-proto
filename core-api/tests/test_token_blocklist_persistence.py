import time
from unittest.mock import MagicMock, patch

import pytest

import app.services.token_blocklist as bl


@pytest.fixture(autouse=True)
def clear_blocklist():
    bl._revoked.clear()
    yield
    bl._revoked.clear()


def test_revoke_persists_with_absolute_expiry(monkeypatch):
    stored = []
    monkeypatch.setattr(bl, "_store", lambda jti, exp_epoch: stored.append((jti, exp_epoch)))
    before = time.time()
    bl.revoke_jti("jti-1", 600.0)
    assert len(stored) == 1
    jti, exp_epoch = stored[0]
    assert jti == "jti-1"
    assert before + 600 <= exp_epoch <= time.time() + 600


def test_revocation_survives_process_restart(monkeypatch):
    # 再起動 = メモリ上のブロックリストが空になる。DBに残っていれば失効扱いのまま。
    monkeypatch.setattr(bl, "_lookup", lambda jti: jti == "jti-persisted")
    assert bl.is_revoked("jti-persisted") is True
    assert bl.is_revoked("jti-other") is False


def test_db_hit_is_cached_in_memory(monkeypatch):
    calls = []

    def lookup(jti):
        calls.append(jti)
        return True

    monkeypatch.setattr(bl, "_lookup", lookup)
    assert bl.is_revoked("jti-2")
    assert bl.is_revoked("jti-2")
    assert calls == ["jti-2"]


def test_lookup_failure_fails_closed(monkeypatch):
    def boom(jti):
        raise RuntimeError("db down")

    monkeypatch.setattr(bl, "_lookup", boom)
    assert bl.is_revoked("jti-3") is True


def test_store_failure_still_revokes_in_memory(monkeypatch):
    def boom(jti, exp_epoch):
        raise RuntimeError("db down")

    monkeypatch.setattr(bl, "_store", boom)
    bl.revoke_jti("jti-4", 60.0)
    assert bl.is_revoked("jti-4") is True


def test_store_and_lookup_sql():
    conn = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = conn
    engine = MagicMock()
    engine.begin.return_value = cm
    engine.connect.return_value = cm
    conn.execute.return_value.fetchone.return_value = (1,)

    # conftestで差し替えられる前の実物を取り出す(engineも同じモジュールのものを差し替える)
    real = _original_module()

    with patch.object(real, "engine", engine):
        real._store("jti-5", 1234567890.0)
        sqls = [str(c.args[0]) for c in conn.execute.call_args_list]
        assert any("INSERT INTO revoked_tokens" in s and "ON CONFLICT" in s for s in sqls)
        assert any("DELETE FROM revoked_tokens" in s for s in sqls)

        conn.execute.reset_mock()
        assert real._lookup("jti-5") is True
        assert "revoked_tokens" in str(conn.execute.call_args.args[0])


def _original_module():
    import importlib.util
    # conftestのmonkeypatchはテスト関数の実行中も有効なので、モジュールを読み直して実物を得る
    spec = importlib.util.find_spec("app.services.token_blocklist")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh


def test_migration_creates_table():
    from app import database

    conn = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = conn
    engine = MagicMock()
    engine.connect.return_value = cm
    with patch.object(database, "engine", engine):
        database.migrate_create_revoked_tokens()
    sqls = " ".join(str(c.args[0]) for c in conn.execute.call_args_list)
    assert "CREATE TABLE IF NOT EXISTS revoked_tokens" in sqls
    assert "jti" in sqls and "expires_at" in sqls
    conn.commit.assert_called()
