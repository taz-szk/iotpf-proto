"""SORACOM APIの呼び出し本体。認証トークンはテナントごとにプロセス内メモリでキャッシュし、
期限切れ(401)を受けたら1回だけ再認証してリトライする。エラーは利用者に見せてよい日本語メッセージに
変換し、認証情報・トークンはメッセージにも例外にも含めない。"""
import threading
import time

import httpx

_BASE_URLS = {
    "jp": "https://api.soracom.io/v1",
    "g": "https://g.api.soracom.io/v1",
}
# SORACOMのトークンは既定24時間(86400秒)有効。それより手前でキャッシュを切り、
# 実際の期限切れより先にこちらから再認証しておく。
_TOKEN_TTL_SEC = 23 * 3600
_TIMEOUT = 10.0

_lock = threading.Lock()
# tenant_id -> (api_key, token, expires_at_monotonic)
_token_cache: dict[str, tuple[str, str, float]] = {}


class SoracomAuthError(Exception):
    """認証情報(Auth Key)が無効、または認証に失敗した。"""


class SoracomApiError(Exception):
    """認証以外のAPI呼び出しの失敗。str(e)はそのまま利用者に見せてよい。"""


def forget_token(tenant_id: str) -> None:
    with _lock:
        _token_cache.pop(tenant_id, None)


def _base_url(coverage: str) -> str:
    return _BASE_URLS.get(coverage, _BASE_URLS["jp"])


def _authenticate(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str) -> tuple[str, str]:
    try:
        resp = httpx.post(
            f"{_base_url(coverage)}/auth",
            json={"authKeyId": auth_key_id, "authKey": auth_key},
            timeout=_TIMEOUT,
        )
    except Exception as e:
        raise SoracomAuthError(f"SORACOMへの接続に失敗しました（{type(e).__name__}）") from e
    if resp.status_code == 401:
        raise SoracomAuthError("SORACOMの認証情報が正しくありません")
    if resp.status_code != 200:
        raise SoracomAuthError(f"SORACOM認証に失敗しました（HTTP {resp.status_code}）")
    body = resp.json()
    api_key, token = body["apiKey"], body["token"]
    with _lock:
        _token_cache[tenant_id] = (api_key, token, time.monotonic() + _TOKEN_TTL_SEC)
    return api_key, token


def _get_token(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str) -> tuple[str, str]:
    with _lock:
        cached = _token_cache.get(tenant_id)
        if cached and cached[2] > time.monotonic():
            return cached[0], cached[1]
    return _authenticate(tenant_id, coverage, auth_key_id, auth_key)


def _describe_exception(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return f"SORACOMへの接続がタイムアウトしました（{_TIMEOUT:.0f}秒）"
    if isinstance(e, httpx.NetworkError):
        return f"SORACOMに接続できません（{type(e).__name__}）"
    return f"SORACOM APIの呼び出しに失敗しました（{type(e).__name__}）"


def _describe_http_failure(status_code: int) -> str:
    if status_code == 429:
        return "SORACOM APIの呼び出し回数制限に達しました。しばらくしてから再度お試しください"
    if status_code >= 500:
        return "SORACOM側で一時的な問題が発生しています"
    return f"SORACOM APIがエラーを返しました（HTTP {status_code}）"


def _request(method: str, tenant_id: str, coverage: str, auth_key_id: str, auth_key: str,
            path: str, **kwargs) -> httpx.Response:
    """認証済みでSORACOM APIを呼ぶ。401を受けたら1回だけ再認証してリトライする。
    404はそのまま呼び出し元に返す(呼び出し元が「対象が無い」として扱う)。"""
    api_key, token = _get_token(tenant_id, coverage, auth_key_id, auth_key)
    url = f"{_base_url(coverage)}{path}"
    headers = {"X-Soracom-API-Key": api_key, "X-Soracom-Token": token}
    call = httpx.get if method == "GET" else httpx.post

    try:
        resp = call(url, headers=headers, timeout=_TIMEOUT, **kwargs)
    except Exception as e:
        raise SoracomApiError(_describe_exception(e)) from e

    if resp.status_code == 401:
        forget_token(tenant_id)
        api_key, token = _authenticate(tenant_id, coverage, auth_key_id, auth_key)
        headers = {"X-Soracom-API-Key": api_key, "X-Soracom-Token": token}
        try:
            resp = call(url, headers=headers, timeout=_TIMEOUT, **kwargs)
        except Exception as e:
            raise SoracomApiError(_describe_exception(e)) from e

    if resp.status_code == 404:
        return resp
    if resp.status_code >= 400:
        raise SoracomApiError(_describe_http_failure(resp.status_code))
    return resp


def list_subscribers(
    tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, *,
    status_filter: str | None = None, speed_class_filter: str | None = None,
    tag_name: str | None = None, tag_value: str | None = None,
    limit: int = 20, last_evaluated_key: str | None = None,
) -> tuple[list[dict], str | None]:
    params: dict = {"limit": limit}
    if status_filter:
        params["status_filter"] = status_filter
    if speed_class_filter:
        params["speed_class_filter"] = speed_class_filter
    if tag_name:
        params["tag_name"] = tag_name
    if tag_value:
        params["tag_value"] = tag_value
    if last_evaluated_key:
        params["last_evaluated_key"] = last_evaluated_key

    resp = _request("GET", tenant_id, coverage, auth_key_id, auth_key, "/subscribers", params=params)
    return resp.json(), resp.headers.get("x-soracom-next-key")


def get_subscriber(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, imsi: str) -> dict | None:
    resp = _request("GET", tenant_id, coverage, auth_key_id, auth_key, f"/subscribers/{imsi}")
    return None if resp.status_code == 404 else resp.json()


def verify_credentials(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str) -> bool:
    try:
        _authenticate(tenant_id, coverage, auth_key_id, auth_key)
        return True
    except SoracomAuthError:
        return False
