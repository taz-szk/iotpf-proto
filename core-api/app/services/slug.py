import re

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$")


class InvalidSlugError(ValueError):
    pass


def validate_tenant_slug(value: str) -> str:
    """利用者が入力したテナントIDを検証する。半角英数字小文字とハイフンのみ、
    3〜30文字、先頭と末尾はハイフン不可とすることで、記号のエスケープを
    考慮する必要がないようにする(URL・ログ・外部連携への流用も安全にするため)。"""
    if not _SLUG_PATTERN.fullmatch(value):
        raise InvalidSlugError(
            "テナントIDは半角英数字(小文字)とハイフンのみ、3〜30文字で指定してください"
            "(先頭・末尾にハイフンは使えません)"
        )
    return value
