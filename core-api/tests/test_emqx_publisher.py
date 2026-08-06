from unittest.mock import patch, MagicMock
from app.services.emqx_publisher import publish_ota_command

def test_publish_ota_command_posts_to_emqx():
    with patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        publish_ota_command("tenant-abc", "device-001", {"version": "1.0.0"})

    mock_httpx.post.assert_called_once()
    call_kwargs = mock_httpx.post.call_args
    assert "api/v5/publish" in call_kwargs[0][0]
    payload_sent = call_kwargs[1]["json"]
    assert payload_sent["topic"] == "tenant-abc/device-001/ota/command"
    assert payload_sent["qos"] == 1
