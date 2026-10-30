"""Where confirmation screenshots and PDFs live: a private MinIO bucket per environment, reached only through the API
(no public name). Tests use memory."""
from __future__ import annotations

import io
import threading
from typing import Protocol

from ghostline.config import Settings


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> tuple[bytes, str]: ...
    def delete(self, key: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        with self._lock:
            self._objects[key] = (data, content_type)

    def get(self, key: str) -> tuple[bytes, str]:
        with self._lock:
            return self._objects[key]

    def delete(self, key: str) -> None:
        with self._lock:
            self._objects.pop(key, None)

    def __len__(self) -> int:
        return len(self._objects)


class MinioStore:
    def __init__(self, settings: Settings) -> None:
        from urllib.parse import urlsplit

        from minio import Minio

        u = urlsplit(settings.minio_endpoint)
        self._client = Minio(u.netloc, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key,
                             secure=u.scheme == "https")
        self._bucket = settings.minio_bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(self._bucket, key, io.BytesIO(data), len(data), content_type=content_type)

    def get(self, key: str) -> tuple[bytes, str]:
        r = self._client.get_object(self._bucket, key)
        try:
            return r.read(), r.headers.get("Content-Type", "application/octet-stream")
        finally:
            r.close()
            r.release_conn()

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)


def for_settings(settings: Settings) -> ObjectStore:
    if settings.minio_endpoint and settings.minio_bucket:
        return MinioStore(settings)
    return MemoryStore()
