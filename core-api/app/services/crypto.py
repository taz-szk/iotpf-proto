"""汎用の対称鍵暗号ヘルパー。SORACOM認証情報など、復号して使う必要がある秘密情報の保存に使う
(パスワードのような一方向ハッシュでは、送信時にAPIへ渡す平文を復元できないため使えない)。"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    # secrets_encryption_keyは任意長・任意文字列でよい運用にするため、SHA-256で32byteに固定してから
    # urlsafe base64化する(Fernetの鍵はこの形式でなければならない)。インストーラはopenssl rand -hex 32
    # のような既存の秘密生成と同じ方法でこの値を生成でき、Fernet鍵の生成手順を別途覚える必要がない。
    digest = hashlib.sha256(settings.secrets_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
