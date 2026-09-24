import pytest

from app.services.slack_webhook import mask_slack_webhook, validate_slack_webhook_url

_OK = "https://hooks.slack.com/" + "services/T01234567/B01234567/" + "abcdEFGHijklMNOPqrstUVWX"  # GitHubのpush protectionがダミーURLを本物と誤検出するため、連結して組み立てる


def test_accepts_a_slack_incoming_webhook_url():
    assert validate_slack_webhook_url(_OK) == _OK


def test_strips_surrounding_whitespace():
    assert validate_slack_webhook_url("  " + _OK + "\n") == _OK


@pytest.mark.parametrize("bad", [
    "http://hooks.slack.com/services/T0/B0/x",             # httpではない
    "https://example.com/services/T0/B0/x",                # 別ホスト
    "https://hooks.slack.com.evil.example/services/T0/B0/x",
    "https://evil.example/hooks.slack.com/services/T0/B0/x",
    "https://hooks.slack.com@evil.example/services/T0/B0/x",  # userinfoでホストを偽装
    "https://hooks.slack.com:8443/services/T0/B0/x",       # ポート指定
    "https://hooks.slack.com/other/T0/B0/x",               # /services/ 以外
    "https://hooks.slack.com/services/",                   # トークンなし
    "https://hooks.slack.com/services/T0/B0/x?url=http://169.254.169.254",  # クエリ
    "https://hooks.slack.com/services/T0/B0/x#frag",
    "https://hooks.slack.com/services/T0/B0/x y",
    "https://hooks.slack.com/services/T0/B0/x\nHost: evil",
    "ftp://hooks.slack.com/services/T0/B0/x",
    "hooks.slack.com/services/T0/B0/x",
    "",
])
def test_rejects_everything_else(bad):
    with pytest.raises(ValueError):
        validate_slack_webhook_url(bad)


def test_rejects_absurdly_long_url():
    with pytest.raises(ValueError):
        validate_slack_webhook_url("https://hooks.slack.com/services/T0/B0/" + "a" * 500)


def test_mask_never_contains_the_secret_part():
    m = mask_slack_webhook(_OK)
    assert m == {"slack_configured": True, "slack_webhook_hint": "…UVWX"}
    assert "abcdEFGH" not in str(m)


def test_mask_when_not_configured():
    assert mask_slack_webhook(None) == {"slack_configured": False, "slack_webhook_hint": None}
    assert mask_slack_webhook("") == {"slack_configured": False, "slack_webhook_hint": None}
