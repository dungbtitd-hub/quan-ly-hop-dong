"""Mã hóa AES-256-GCM và sinh khóa từ mật khẩu (dùng cho thu chi và gói đồng bộ)."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KDF_ITER = 300_000


class CryptoError(Exception):
    pass


def derive_key(secret: str, salt: bytes, iterations: int = KDF_ITER) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, iterations, dklen=32)


def encrypt(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, aad or None)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], aad or None)
    except (InvalidTag, ValueError) as e:
        raise CryptoError("Sai khóa hoặc dữ liệu đã bị sửa / hỏng.") from e


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def recovery_code() -> str:
    """Mã khôi phục 24 ký tự, chia nhóm 4 cho dễ chép tay."""
    raw = base64.b32encode(secrets.token_bytes(15)).decode()
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def normalize_recovery(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))
