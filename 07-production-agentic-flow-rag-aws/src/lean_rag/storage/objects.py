"""Object storage. S3 is the source of truth; everything in OpenSearch can be rebuilt from it."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import boto3
import jwt
from botocore.config import Config
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    method: str  # "POST" (S3 form upload) or "PUT" (local dev endpoint)
    fields: dict[str, str] = field(default_factory=dict)
    expires_in: int = 900


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def size(self, key: str) -> int | None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload: ...


def aws_client(service: str, region: str, endpoint_url: str | None = None, timeout_s: int = 30) -> Any:
    cfg = Config(
        region_name=region,
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=5,
        read_timeout=timeout_s,
    )
    return boto3.client(service, config=cfg, endpoint_url=endpoint_url)


class S3ObjectStore:
    def __init__(self, bucket: str, client: Any, presign_client: Any | None = None) -> None:
        self.bucket = bucket
        self.client = client
        self.presign_client = presign_client or client

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        body: bytes = resp["Body"].read()
        return body

    def size(self, key: str) -> int | None:
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                deleted += len(keys)
        return deleted

    def presign_upload(self, key: str, content_type: str, max_bytes: int, expires_in: int) -> PresignedUpload:
        # A presigned POST (not PUT) lets S3 itself enforce the size limit and content type.
        post = self.presign_client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=expires_in,
        )
        return PresignedUpload(url=post["url"], method="POST", fields=post["fields"], expires_in=expires_in)


class LocalObjectStore:
    """Filesystem-backed store for local development and tests (not used in AWS)."""

    def __init__(self, root: str | Path, upload_secret: str, public_base_url: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._secret = upload_secret
        self._base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("key escapes storage root")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic, so a crashed write never leaves a half object

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise KeyError(key)
        return path.read_bytes()

    def size(self, key: str) -> int | None:
        path = self._path(key)
        return path.stat().st_size if path.exists() else None

    def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)
        if base.is_file():
            base.unlink()
            return 1
        if not base.exists():
            return 0
        files = [p for p in base.rglob("*") if p.is_file()]
        for p in files:
            p.unlink()
        return len(files)

    def presign_upload(self, key: str, content_type: str, max_bytes: int, expires_in: int) -> PresignedUpload:
        token = jwt.encode(
            {"key": key, "ct": content_type, "max": max_bytes, "exp": int(time.time()) + expires_in},
            self._secret,
            algorithm="HS256",
        )
        return PresignedUpload(
            url=f"{self._base_url}/local-upload/{token}",
            method="PUT",
            fields={"Content-Type": content_type},
            expires_in=expires_in,
        )

    def verify_upload_token(self, token: str) -> dict[str, Any]:
        claims: dict[str, Any] = jwt.decode(token, self._secret, algorithms=["HS256"])
        return claims
