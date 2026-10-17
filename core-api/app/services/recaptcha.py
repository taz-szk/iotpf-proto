import httpx
from app.config import settings

_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def verify_recaptcha(token: str, remote_ip: str | None = None) -> bool:
    """reCAPTCHA v3のトークンを検証する。successがTrueかつscoreが
    settings.recaptcha_min_score以上の場合のみTrue。通信失敗時もFalse
    (サインアップは拒否する側に倒す)。"""
    data = {"secret": settings.recaptcha_secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        resp = httpx.post(_VERIFY_URL, data=data, timeout=10)
        body = resp.json()
    except Exception:
        return False
    return bool(body.get("success")) and body.get("score", 0) >= settings.recaptcha_min_score
