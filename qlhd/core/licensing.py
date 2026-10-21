"""Kích hoạt bản quyền theo chuỗi chữ ký số: Nhà phát triển → Giám đốc → Nhân viên.

- Nhà phát triển giữ khóa bí mật Ed25519 trong Phần mềm cấp mã (tools/license_manager.py)
  và ký Key cố định cho Giám đốc. Phần mềm chỉ chứa khóa công khai.
- Khi yêu cầu kích hoạt, máy Giám đốc sinh cặp khóa công ty; khóa công khai đi trong
  Mã yêu cầu và được ký vào Key, nên mã nhân viên do Giám đốc ký kiểm tra được ở máy nhân viên.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey



class LicenseError(Exception):
    pass


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canon(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


# ---- khóa
def new_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_to_b64(k: Ed25519PrivateKey) -> str:
    return b64e(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption()))


def private_from_b64(s: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b64d(s))


def public_to_b64(k: Ed25519PublicKey) -> str:
    return b64e(k.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def public_from_b64(s: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(b64d(s))


def vendor_public_key() -> Ed25519PublicKey:
    from .vendor_pubkey import VENDOR_PUBKEY

    return public_from_b64(VENDOR_PUBKEY)


def _sign(priv: Ed25519PrivateKey, payload: dict) -> str:
    return b64e(_canon(payload)) + "." + b64e(priv.sign(_canon(payload)))


def _open(token: str, pub: Ed25519PublicKey) -> dict:
    try:
        body, sig = token.strip().split(".")
        payload = json.loads(b64d(body))
        pub.verify(b64d(sig), _canon(payload))
    except (ValueError, InvalidSignature, json.JSONDecodeError) as e:
        raise LicenseError("Mã không hợp lệ hoặc đã bị sửa.") from e
    return payload


def _strip_prefix(s: str, prefix: str) -> str:
    s = "".join(s.split())
    if not s.startswith(prefix):
        raise LicenseError("Mã không đúng định dạng.")
    return s[len(prefix):]


# ---- Bước 1: Giám đốc yêu cầu, Nhà phát triển cấp Key
def make_request(machine_id: str, company_pub_b64: str) -> str:
    return "QLHD-YC:" + b64e(_canon({"may": machine_id, "pub": company_pub_b64}))


def read_request(request: str) -> dict:
    try:
        return json.loads(b64d(_strip_prefix(request, "QLHD-YC:")))
    except (ValueError, json.JSONDecodeError) as e:
        raise LicenseError("Mã yêu cầu không hợp lệ.") from e


def issue_director_key(vendor_priv: Ed25519PrivateKey, request: str, ten_cong_ty: str,
                       mst: str, so_tai_khoan: int) -> str:
    req = read_request(request)
    payload = {
        "loai": "giam_doc", "id": str(uuid.uuid4()), "cty": ten_cong_ty, "mst": mst,
        "may": req["may"], "pub": req["pub"], "toi_da": int(so_tai_khoan),
        "ngay": date.today().isoformat(), "han": None,
    }
    return "QLHD-KEY:" + _sign(vendor_priv, payload)


def verify_director_key(key: str, machine_id: str | None = None,
                        vendor_pub: Ed25519PublicKey | None = None) -> dict:
    payload = _open(_strip_prefix(key, "QLHD-KEY:"), vendor_pub or vendor_public_key())
    if payload.get("loai") != "giam_doc":
        raise LicenseError("Đây không phải Key Giám đốc.")
    if machine_id is not None and payload["may"] != machine_id:
        raise LicenseError("Key này được cấp cho máy khác.")
    return payload


# ---- Bước 2: Giám đốc cấp mã cho nhân viên (hoặc máy Giám đốc thứ hai)
def issue_member_code(company_priv: Ed25519PrivateKey, director_key: str, *, ten_dang_nhap: str,
                      ho_ten: str, vai_tro: str, quyen: dict, machine_id: str,
                      het_han: str | None = None) -> tuple[str, str]:
    """Trả về (mã kích hoạt, id mã)."""
    code_id = str(uuid.uuid4())
    payload = {
        "loai": "thanh_vien", "id": code_id, "tdn": ten_dang_nhap, "ten": ho_ten,
        "vai_tro": vai_tro, "quyen": quyen, "may": machine_id,
        "ngay": date.today().isoformat(), "han": het_han,
    }
    signed = _sign(company_priv, payload)
    bundle = {"ma": signed, "key": director_key}
    return "QLHD-NV:" + b64e(_canon(bundle)), code_id


def verify_member_code(code: str, machine_id: str | None = None,
                       vendor_pub: Ed25519PublicKey | None = None,
                       today: date | None = None) -> tuple[dict, dict]:
    """Trả về (thông tin mã, thông tin Key Giám đốc)."""
    try:
        bundle = json.loads(b64d(_strip_prefix(code, "QLHD-NV:")))
    except (ValueError, json.JSONDecodeError) as e:
        raise LicenseError("Mã nhân viên không đúng định dạng.") from e
    director = verify_director_key(bundle["key"], None, vendor_pub)
    payload = _open(bundle["ma"], public_from_b64(director["pub"]))
    if payload.get("loai") != "thanh_vien":
        raise LicenseError("Mã không phải mã nhân viên.")
    if machine_id is not None and payload["may"] != machine_id:
        raise LicenseError("Mã này được cấp cho máy khác.")
    if payload.get("han") and (today or date.today()).isoformat() > payload["han"]:
        raise LicenseError("Mã đã hết hạn, liên hệ Giám đốc để gia hạn.")
    return payload, director


# ---- Danh sách thu hồi (do Giám đốc ký, đi kèm gói đồng bộ)
def sign_revocations(company_priv: Ed25519PrivateKey, ids: list[str]) -> str:
    return _sign(company_priv, {"loai": "thu_hoi", "ids": sorted(ids)})


def read_revocations(token: str, company_pub_b64: str) -> list[str]:
    payload = _open(token, public_from_b64(company_pub_b64))
    if payload.get("loai") != "thu_hoi":
        raise LicenseError("Danh sách thu hồi không hợp lệ.")
    return list(payload["ids"])
