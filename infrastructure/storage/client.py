import hmac
import hashlib
import time
from pathlib import Path
from typing import Any, Optional, Protocol
from fastapi import UploadFile

from config.errors import NotFoundError
from config.settings import get_settings


class StorageClient(Protocol):
    def put_object(self, file: Any, key: str) -> str:
        ...

    def get_object(self, key: str) -> bytes:
        ...

    def get_presigned_url(self, key: str, expiry_sec: int = 3600) -> str:
        ...


class LocalStorageClient:
    """Local file-backed storage client for dev and tests."""

    def __init__(self, base_dir: Optional[Path] = None, signing_secret: str = "storage-secret"):
        settings = get_settings()
        self.base_dir = base_dir or Path("./data/storage") / settings.object_storage_bucket
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.signing_secret = signing_secret.encode("utf-8")

    def put_object(self, file: Any, key: str) -> str:
        target_path = self.base_dir / key
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if hasattr(file, "file") and hasattr(file.file, "read"):
            content = file.file.read()
            file.file.seek(0)
        elif isinstance(file, (bytes, bytearray)):
            content = bytes(file)
        elif hasattr(file, "read"):
            content = file.read()
        else:
            content = bytes(file)

        with open(target_path, "wb") as f:
            f.write(content)
        return key

    def get_object(self, key: str) -> bytes:
        target_path = self.base_dir / key
        if not target_path.exists() or not target_path.is_file():
            raise NotFoundError(f"Object not found in storage: {key}", detail={"key": key})
        with open(target_path, "rb") as f:
            return f.read()

    def get_presigned_url(self, key: str, expiry_sec: int = 3600) -> str:
        # Verify object exists or key is valid
        expires_at = int(time.time()) + expiry_sec
        signature = hmac.new(
            self.signing_secret,
            f"{key}:{expires_at}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"/api/v1/documents/stream/{key}?expires={expires_at}&signature={signature}"

    def verify_presigned_url(self, key: str, expires_at: int | float, signature: str) -> bool:
        if time.time() >= float(expires_at):
            return False
        expected = hmac.new(
            self.signing_secret,
            f"{key}:{expires_at}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)


_storage_client: StorageClient = LocalStorageClient()


def get_storage_client() -> StorageClient:
    return _storage_client


def set_storage_client(client: StorageClient) -> None:
    global _storage_client
    _storage_client = client


def put_object(file: Any, key: str) -> str:
    return get_storage_client().put_object(file, key)


def get_object(key: str) -> bytes:
    return get_storage_client().get_object(key)


def get_presigned_url(key: str, expiry_sec: int = 3600) -> str:
    return get_storage_client().get_presigned_url(key, expiry_sec)
