"""Cất bí mật riêng của máy này (khóa API Gemini, mật khẩu đồng bộ tự động) trong CaiDat, mã hóa gắn với Mã máy:
chép tệp dữ liệu sang máy khác thì không đọc được. Các cài đặt này thuộc nhóm riêng từng máy, không đi theo gói."""
from __future__ import annotations

import base64
import hashlib

from . import crypto

_AAD = b"qlhd-bi-mat-may"


def _key(machine_id: str) -> bytes:
    return hashlib.sha256(f"qlhd|{machine_id}|bi-mat-rieng-may".encode()).digest()


def save(db, key: str, value: str | None) -> None:
    if not value:
        db.set_setting(key, None)
        return
    blob = crypto.encrypt(_key(db.machine_id), value.encode(), _AAD)
    db.set_setting(key, base64.b64encode(blob).decode())


def load(db, key: str) -> str:
    raw = db.setting(key)
    if not raw:
        return ""
    try:
        return crypto.decrypt(_key(db.machine_id), base64.b64decode(raw), _AAD).decode()
    except Exception:  # noqa: BLE001 - tệp chép từ máy khác / hỏng: coi như chưa cài
        return ""
