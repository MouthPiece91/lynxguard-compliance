"""API-key authentication. Every key belongs to exactly one organization.

Only SHA-256 hashes of keys are stored. Generate a key with:

    python -m lynxguard.auth new --org acme-fx --name "Acme compliance desk"

and add the printed entry to the keys file (LYNXGUARD_KEYS_FILE).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

KEY_PREFIX = "lg_"


@dataclass(frozen=True)
class Principal:
    org_id: str
    key_name: str


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    def __init__(self, entries: dict[str, dict]):
        # {sha256(key): {"org_id": ..., "name": ...}}
        self._entries = entries

    @classmethod
    def from_file(cls, path: str | Path) -> "KeyStore":
        return cls(json.loads(Path(path).read_text()))

    def authenticate(self, key: str | None) -> Principal | None:
        if not key or not key.startswith(KEY_PREFIX):
            return None
        h = hash_key(key)
        for stored, meta in self._entries.items():
            if hmac.compare_digest(stored, h):
                return Principal(org_id=meta["org_id"], key_name=meta.get("name", ""))
        return None


def _main() -> None:
    ap = argparse.ArgumentParser(prog="python -m lynxguard.auth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="generate a new API key")
    new.add_argument("--org", required=True)
    new.add_argument("--name", default="")
    args = ap.parse_args()
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    print("API key (shown once, give it to the client):")
    print(f"  {key}")
    print("Add this entry to your keys file:")
    print(json.dumps({hash_key(key): {"org_id": args.org, "name": args.name}}, indent=2))


if __name__ == "__main__":
    _main()
