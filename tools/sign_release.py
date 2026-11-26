#!/usr/bin/env python3
"""
Release signing for the LOY Studio in-app updater.

  python tools/sign_release.py keygen
      Prints a new Ed25519 key pair. Put the SECRET (hex) in the GitHub Actions secret
      `LOY_UPDATE_SIGNING_KEY`, and the PUBLIC key in app/update_signing.py
      (UPDATE_PUBLIC_KEY_HEX). Never commit the secret.

  python tools/sign_release.py sums <dir>
      Writes <dir>/SHA256SUMS for every .zip in <dir>.

  LOY_UPDATE_SIGNING_KEY=<hex> python tools/sign_release.py sign <dir>/SHA256SUMS
      Writes <dir>/SHA256SUMS.sig (hex Ed25519 signature).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.update_signing import public_key, sign  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "keygen":
        sk = secrets.token_bytes(32)
        print("SECRET (GitHub secret LOY_UPDATE_SIGNING_KEY):", sk.hex())
        print("PUBLIC (app/update_signing.py UPDATE_PUBLIC_KEY_HEX):", public_key(sk).hex())
        return 0
    if cmd == "sums" and len(argv) == 2:
        d = Path(argv[1])
        zips = sorted(d.glob("*.zip"))
        if not zips:
            print(f"No .zip files in {d}", file=sys.stderr)
            return 1
        (d / "SHA256SUMS").write_text("".join(f"{_sha256(z)}  {z.name}\n" for z in zips), encoding="utf-8")
        print((d / "SHA256SUMS").read_text(encoding="utf-8"), end="")
        return 0
    if cmd == "sign" and len(argv) == 2:
        key_hex = os.environ.get("LOY_UPDATE_SIGNING_KEY", "").strip()
        if not key_hex:
            print("LOY_UPDATE_SIGNING_KEY is not set; skipping signature", file=sys.stderr)
            return 0
        sums = Path(argv[1])
        sig = sign(bytes.fromhex(key_hex), sums.read_bytes())
        sums.with_name(sums.name + ".sig").write_text(sig.hex() + "\n", encoding="utf-8")
        print(f"Signed {sums.name} with public key {public_key(bytes.fromhex(key_hex)).hex()}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
