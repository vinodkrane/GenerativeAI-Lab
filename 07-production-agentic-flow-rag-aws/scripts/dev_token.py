"""Mint an HS256 token for local development (RAG_AUTH_MODE=local only).

python scripts/dev_token.py --sub alice --tenant acme --groups staff,engineering
"""

from __future__ import annotations

import argparse
import sys

from lean_rag.config import get_settings
from lean_rag.security.auth import mint_local_token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sub", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--groups", default="", help="comma-separated")
    parser.add_argument("--ttl", type=int, default=8 * 3600)
    args = parser.parse_args()
    settings = get_settings()
    if settings.auth_mode != "local" or not settings.local_jwt_secret:
        sys.exit("dev tokens only work with RAG_AUTH_MODE=local and RAG_LOCAL_JWT_SECRET set")
    groups = [g for g in args.groups.split(",") if g]
    print(
        mint_local_token(
            settings.local_jwt_secret,
            args.sub,
            args.tenant,
            groups,
            tenant_claim=settings.tenant_claim,
            groups_claim=settings.groups_claim,
            ttl_s=args.ttl,
        )
    )


if __name__ == "__main__":
    main()
