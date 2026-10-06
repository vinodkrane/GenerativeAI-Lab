"""Exercise a running API end to end: create -> upload -> wait for INDEXED -> query.

Works against `make run` (local upload endpoint) and `make docker-up` (S3 presigned POST).
Uses only the standard library plus the app's own token helper.

    python scripts/demo.py --file evals/corpus/travel-expense-policy.md \
        --question "What is the nightly hotel limit in London?"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from lean_rag.config import get_settings
from lean_rag.security.auth import mint_local_token


def call(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - URL comes from CLI args
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read() or b"null")


def upload(instructions: dict[str, Any], data: bytes) -> None:
    if instructions["method"] == "PUT":
        req = urllib.request.Request(instructions["url"], data=data, method="PUT")  # noqa: S310
        for k, v in instructions["fields"].items():
            req.add_header(k, v)
        urllib.request.urlopen(req, timeout=60).close()  # noqa: S310
        return
    # S3 presigned POST: multipart/form-data with the policy fields first and the file last.
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in instructions["fields"].items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="upload"\r\n\r\n'.encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(instructions["url"], data=b"".join(parts), method="POST")  # noqa: S310
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    urllib.request.urlopen(req, timeout=60).close()  # noqa: S310


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--file", default="evals/corpus/travel-expense-policy.md")
    parser.add_argument("--question", default="What is the nightly hotel limit in London?")
    parser.add_argument("--sub", default="alice")
    parser.add_argument("--tenant", default="acme")
    parser.add_argument("--groups", default="staff")
    args = parser.parse_args()

    settings = get_settings()
    assert settings.local_jwt_secret, "set RAG_LOCAL_JWT_SECRET (make install creates .env)"
    groups = [g for g in args.groups.split(",") if g]
    token = mint_local_token(settings.local_jwt_secret, args.sub, args.tenant, groups)

    path = Path(args.file)
    created = call("POST", f"{args.api}/documents", token, {"filename": path.name, "allowed_groups": groups})
    doc_id = created["document"]["document_id"]
    print(f"created document {doc_id}; uploading {path.name}")
    upload(created["upload"], path.read_bytes())

    status = "PENDING_UPLOAD"
    for _ in range(60):
        status = call("GET", f"{args.api}/documents/{doc_id}", token)["status"]
        if status in {"INDEXED", "NEEDS_REVIEW", "FAILED", "DUPLICATE"}:
            break
        time.sleep(1)
    print(f"ingestion status: {status}")
    if status != "INDEXED":
        sys.exit(1)

    result = call("POST", f"{args.api}/query", token, {"question": args.question})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
