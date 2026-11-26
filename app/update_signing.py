"""
Release integrity for the in-app updater (no third-party dependencies).

Every GitHub Release ships `SHA256SUMS` (hash of each portable zip) and, when the
`LOY_UPDATE_SIGNING_KEY` CI secret is configured, `SHA256SUMS.sig`: an Ed25519 signature
over `SHA256SUMS`.

- `UPDATE_PUBLIC_KEY_HEX` set  -> the updater REQUIRES a valid signature (protects against a
  compromised release or CI pipeline).
- `UPDATE_PUBLIC_KEY_HEX` empty -> the updater still REQUIRES the zip to match SHA256SUMS
  (protects against corrupted / truncated downloads), and reports the release as unsigned.

Generate a key pair with:  python tools/sign_release.py keygen
Ed25519 below is the RFC 8032 reference algorithm (section 6), verified by RFC test vectors
in tests/test_server.py.
"""

from __future__ import annotations

import hashlib
from typing import Dict, Optional, Tuple

# Paste the PUBLIC key printed by `python tools/sign_release.py keygen` here (64 hex chars).
UPDATE_PUBLIC_KEY_HEX = "d7855fed5f1c51ec6b6a646fc92abc8a3e952a76fe2ada4b89164d7891d39b3c"

# ---------------- Ed25519 (RFC 8032 reference) ----------------
_p = 2**255 - 19
_q = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _p - 2, _p)


_d = -121665 * _inv(121666) % _p
_SQRT_M1 = pow(2, (_p - 1) // 4, _p)

Point = Tuple[int, int, int, int]


def _add(P: Point, Q: Point) -> Point:
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C = 2 * P[3] * Q[3] * _d % _p
    D = 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _mul(s: int, P: Point) -> Point:
    Q: Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P: Point, Q: Point) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _p:
        return None
    x2 = (y * y - 1) * _inv(_d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _SQRT_M1 % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * _inv(5) % _p
_gx = _recover_x(_gy, 0)
assert _gx is not None
_G: Point = (_gx, _gy, 1, _gx * _gy % _p)


def _compress(P: Point) -> bytes:
    zinv = _inv(P[2])
    x, y = P[0] * zinv % _p, P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes) -> Optional[Point]:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _p)


def _sha512_modq(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _q


def _expand(secret: bytes) -> Tuple[int, bytes]:
    if len(secret) != 32:
        raise ValueError("Ed25519 secret key must be 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    Rs = _compress(_mul(r, _G))
    h = _sha512_modq(Rs + A + msg)
    return Rs + int.to_bytes((r + h * a) % _q, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _q:
        return False
    h = _sha512_modq(signature[:32] + public + msg)
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))


# ---------------- release manifest ----------------

def parse_sha256sums(text: str) -> Dict[str, str]:
    """Parse `sha256sum` output: '<64 hex>  <filename>' per line."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and len(parts[0]) == 64:
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def verify_release(
    sums_text: bytes,
    sig: Optional[bytes],
    asset_name: str,
    asset_sha256: str,
    public_key_hex: str = UPDATE_PUBLIC_KEY_HEX,
) -> Dict[str, object]:
    """
    Raise ValueError unless the downloaded asset is trustworthy. Returns {"signed": bool}.
    """
    if public_key_hex:
        if not sig:
            raise ValueError("Release is missing SHA256SUMS.sig; refusing unsigned update")
        sig_bytes = bytes.fromhex(sig.decode("ascii").strip()) if len(sig) != 64 else sig
        if not verify(bytes.fromhex(public_key_hex), sums_text, sig_bytes):
            raise ValueError("SHA256SUMS signature is INVALID; refusing update")
    expected = parse_sha256sums(sums_text.decode("utf-8", errors="replace")).get(asset_name)
    if not expected:
        raise ValueError(f"SHA256SUMS has no entry for {asset_name}")
    if expected != asset_sha256.lower():
        raise ValueError("Downloaded update does not match SHA256SUMS (corrupted or tampered); refusing update")
    return {"signed": bool(public_key_hex)}
