import hashlib
from typing import BinaryIO

from fastapi import HTTPException, status

MAX_FIRMWARE_BYTES = 100 * 1024 * 1024  # 100 MB
CHUNK_SIZE = 1024 * 1024


def measure_upload(f: BinaryIO) -> tuple[int, str]:
    """アップロードされたファイルを、全量をメモリに載せずにチャンクで読み、(サイズ, "sha256:...")を返す。
    上限を超えた時点で読むのをやめて413にする。読み終えたら先頭に戻すので、そのままMinIOへ流せる。"""
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = f.read(CHUNK_SIZE)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_FIRMWARE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Firmware file too large (max {MAX_FIRMWARE_BYTES // (1024 * 1024)} MB)",
            )
        digest.update(chunk)
    f.seek(0)
    return size, "sha256:" + digest.hexdigest()
