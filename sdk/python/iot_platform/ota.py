import hashlib
import requests


class OtaHandler:
    @staticmethod
    def download_and_verify(url: str, output_path: str, expected_sha256: str) -> bool:
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()

        sha256 = hashlib.sha256()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                sha256.update(chunk)

        actual = sha256.hexdigest()
        expected = expected_sha256.removeprefix("sha256:")
        return actual == expected

    @staticmethod
    def handle(payload: dict, output_path: str) -> bool:
        return OtaHandler.download_and_verify(
            payload["download_url"],
            output_path,
            payload["checksum"],
        )
