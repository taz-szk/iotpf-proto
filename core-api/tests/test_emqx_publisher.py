import json
from unittest.mock import patch, MagicMock
from app.services.emqx_publisher import publish_ota_command


def test_publish_ota_command_posts_to_emqx():
    # トークンはモジュール内にキャッシュされるため、他のテストの影響を受けないよう未取得の状態から始める
    with patch("app.services.emqx_publisher._token", None), \
         patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        publish_ota_command("tenant-abc", "device-001", {"version": "1.0.0"})

    # 1回目: EMQX APIへのログイン(トークン取得)、2回目: 実際のpublish
    assert mock_httpx.post.call_count == 2
    login_call, publish_call = mock_httpx.post.call_args_list
    assert "api/v5/login" in login_call[0][0]
    assert "api/v5/publish" in publish_call[0][0]
    payload_sent = publish_call[1]["json"]
    assert payload_sent["topic"] == "/tenant-abc/devices/device-001/commands"
    assert payload_sent["qos"] == 1


def test_kick_client_deletes_the_mqtt_session():
    """削除したデバイスの接続中セッションを、EMQXのAPIで切断する。"""
    from app.services.emqx_publisher import kick_client
    with patch("app.services.emqx_publisher._token", "tok"), \
         patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_httpx.delete.return_value = MagicMock(status_code=204)
        kick_client("tenant-abc:device-001")
    url = mock_httpx.delete.call_args[0][0]
    assert url.endswith("/api/v5/clients/tenant-abc%3Adevice-001")
    assert mock_httpx.delete.call_args[1]["headers"]["Authorization"] == "Bearer tok"


def test_kick_client_swallows_errors():
    """切断はベストエフォート。EMQXに繋がらなくてもデバイス削除自体は失敗させない。"""
    from app.services.emqx_publisher import kick_client
    with patch("app.services.emqx_publisher._token", "tok"), \
         patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_httpx.delete.side_effect = Exception("emqx down")
        kick_client("tenant-abc:device-001")


def test_ota_topic_matches_acl():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    # httpx.post をグローバルに差し替えると、他のテストが起動したバックグラウンドスレッド(EMQX初期設定など)の
    # 呼び出しが call_args に混ざって不安定になる。このモジュール内のhttpxだけを差し替える。
    with patch("app.services.emqx_publisher._token", "cached-token"),          patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_resp
        publish_ota_command("tenant-123", "dev-001", {"type": "ota", "version": "1.0"})

    body = mock_httpx.post.call_args.kwargs["json"]
    assert body["topic"] == "/tenant-123/devices/dev-001/commands"
