"""Tài khoản, mật khẩu, phân quyền và các bước kích hoạt trên một máy."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
from datetime import datetime, timedelta, timezone

from . import licensing as lic
from .db import Database, now_iso

ROLES = ("Giám đốc", "Người quản lý", "Kế toán viên")
FEATURES = ("HopDong", "HoaDonDauRa", "HoaDonDauVao", "BaoCao", "DanhMuc", "SaoLuu",
            "DongBo", "ThuChi", "TaiKhoan")
ACTIONS = ("Xem", "Them", "Sua", "Xoa", "Xuat")
MAX_FAILED = 5
LOCK_MINUTES = 15
PBKDF2_ITER = 200_000


class AuthError(Exception):
    pass


def hash_password(password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITER) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    _, it, salt, dk = stored.split("$")
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
    return hmac.compare_digest(test.hex(), dk)


def _all(v: int) -> dict:
    return {a: v for a in ACTIONS}


def default_permissions(role: str) -> dict:
    if role == "Giám đốc":
        return {f: _all(1) for f in FEATURES}
    if role == "Người quản lý":
        p = {f: _all(1) for f in FEATURES}
        p["TaiKhoan"] = _all(0)
        p["SaoLuu"] = {"Xem": 1, "Them": 1, "Sua": 0, "Xoa": 0, "Xuat": 1}
        return p
    # Kế toán viên: chỉ phần hợp đồng
    p = {f: _all(0) for f in FEATURES}
    p["HopDong"] = {"Xem": 1, "Them": 0, "Sua": 0, "Xoa": 0, "Xuat": 1}
    p["HoaDonDauRa"] = {"Xem": 1, "Them": 0, "Sua": 1, "Xoa": 0, "Xuat": 1}  # ghi nhận thanh toán
    p["HoaDonDauVao"] = {"Xem": 1, "Them": 1, "Sua": 1, "Xoa": 0, "Xuat": 1}
    p["BaoCao"] = {"Xem": 1, "Them": 0, "Sua": 0, "Xoa": 0, "Xuat": 1}
    p["DanhMuc"] = {"Xem": 1, "Them": 0, "Sua": 0, "Xoa": 0, "Xuat": 0}
    p["DongBo"] = {"Xem": 1, "Them": 1, "Sua": 0, "Xoa": 0, "Xuat": 1}
    return p


class AccountService:
    def __init__(self, db: Database, machine_id: str, vendor_pub=None):
        self.db = db
        self.machine_id = machine_id
        self.vendor_pub = vendor_pub
        if not db.one("SELECT 1 FROM ThongTinMay WHERE MaMay = ?", (machine_id,)):
            db.execute("INSERT OR REPLACE INTO ThongTinMay (MaMay, TenMay) VALUES (?, ?)",
                       (machine_id, platform.node() or machine_id))

    # ---- trạng thái bản quyền
    def license(self):
        return self.db.one("SELECT * FROM BanQuyen WHERE Id = 1")

    def is_activated(self) -> bool:
        return self.license() is not None

    def director_payload(self) -> dict | None:
        lic_row = self.license()
        if not lic_row:
            return None
        return lic.verify_director_key(lic_row["KeyGiamDoc"], None, self.vendor_pub)

    def is_primary_director_machine(self) -> bool:
        row = self.license()
        return bool(row and row["LoaiKichHoat"] == "Giám đốc" and row["KhoaBiMatCongTy"])

    # ---- Bước 1: kích hoạt Giám đốc
    def activation_request(self) -> str:
        priv = self.db.setting("khoa_cong_ty_tam")
        if not priv:
            priv = lic.private_to_b64(lic.new_private_key())
            self.db.set_setting("khoa_cong_ty_tam", priv)
        pub = lic.public_to_b64(lic.private_from_b64(priv).public_key())
        return lic.make_request(self.machine_id, pub)

    def activate_director(self, key: str, username: str, fullname: str, password: str) -> None:
        if self.is_activated():
            raise AuthError("Máy này đã được kích hoạt.")
        payload = lic.verify_director_key(key, self.machine_id, self.vendor_pub)
        priv = self.db.setting("khoa_cong_ty_tam")
        if not priv or lic.public_to_b64(lic.private_from_b64(priv).public_key()) != payload["pub"]:
            raise AuthError("Key không khớp với Mã yêu cầu của máy này. Hãy gửi lại Mã yêu cầu hiện tại.")
        _check_password_rule(password)
        self.db.execute(
            "INSERT INTO BanQuyen (Id, LoaiKichHoat, MaCongTy, TenCongTy, KeyGiamDoc, KhoaBiMatCongTy,"
            " SoTaiKhoanToiDa, NgayKichHoat) VALUES (1, 'Giám đốc', ?, ?, ?, ?, ?, ?)",
            (payload["mst"], payload["cty"], "".join(key.split()), priv, payload["toi_da"], now_iso()))
        self._create_user(username, fullname, "Giám đốc", password, default_permissions("Giám đốc"), None)
        self.db.log(username, "Kích hoạt Giám đốc", payload["cty"])

    # ---- Bước 2: kích hoạt nhân viên / máy Giám đốc thứ hai
    def activate_member(self, code: str, password: str) -> dict:
        if self.is_activated():
            raise AuthError("Máy này đã được kích hoạt.")
        payload, director = lic.verify_member_code(code, self.machine_id, self.vendor_pub)
        _check_password_rule(password)
        loai = "Giám đốc (máy phụ)" if payload["vai_tro"] == "Giám đốc" else "Nhân viên"
        self.db.execute(
            "INSERT INTO BanQuyen (Id, LoaiKichHoat, MaCongTy, TenCongTy, KeyGiamDoc, MaKichHoatMay,"
            " SoTaiKhoanToiDa, NgayKichHoat) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (loai, director["mst"], director["cty"], json.loads(lic.b64d(code.split(":", 1)[1]))["key"],
             payload["id"], director["toi_da"], now_iso()))
        self._create_user(payload["tdn"], payload["ten"], payload["vai_tro"], password,
                          payload["quyen"], payload["id"])
        self.db.log(payload["tdn"], "Kích hoạt " + loai, director["cty"])
        return payload

    def issue_code(self, *, ten_dang_nhap: str, ho_ten: str, vai_tro: str, machine_id: str,
                   quyen: dict | None = None, het_han: str | None = None) -> str:
        if not self.is_primary_director_machine():
            raise AuthError("Chỉ máy Giám đốc chính mới cấp được mã.")
        if vai_tro not in ROLES:
            raise AuthError("Vai trò không hợp lệ.")
        row = self.license()
        used = self.db.one("SELECT COUNT(*) n FROM MaDaCap WHERE DaThuHoi = 0")["n"]
        if used >= row["SoTaiKhoanToiDa"]:
            raise AuthError(f"Đã dùng hết {row['SoTaiKhoanToiDa']} tài khoản được cấp theo Key.")
        quyen = quyen or default_permissions(vai_tro)
        if vai_tro == "Kế toán viên":
            quyen = dict(quyen)
            quyen["ThuChi"] = _all(0)  # nguyên tắc cứng: không thể cấp thu chi cho kế toán viên
            quyen["TaiKhoan"] = _all(0)
        code, code_id = lic.issue_member_code(
            lic.private_from_b64(row["KhoaBiMatCongTy"]), row["KeyGiamDoc"],
            ten_dang_nhap=ten_dang_nhap, ho_ten=ho_ten, vai_tro=vai_tro, quyen=quyen,
            machine_id=machine_id.strip(), het_han=het_han)
        self.db.execute(
            "INSERT INTO MaDaCap (MaKichHoat, TenDangNhap, HoTen, MaMay, VaiTro, NgayCap, HetHan)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code_id, ten_dang_nhap, ho_ten, machine_id.strip(), vai_tro, now_iso(), het_han))
        self.db.log(None, "Cấp mã " + vai_tro, ten_dang_nhap)
        return code

    def revoke(self, code_id: str) -> None:
        self.db.execute("UPDATE MaDaCap SET DaThuHoi = 1, NgayThuHoi = ? WHERE MaKichHoat = ?",
                        (now_iso(), code_id))
        self.db.execute("INSERT OR IGNORE INTO DanhSachThuHoi (MaKichHoat, NgayThuHoi) VALUES (?, ?)",
                        (code_id, now_iso()))
        self.db.log(None, "Thu hồi mã", code_id)

    def signed_revocations(self) -> str | None:
        if not self.is_primary_director_machine():
            return None
        ids = [r["MaKichHoat"] for r in self.db.query("SELECT MaKichHoat FROM DanhSachThuHoi")]
        return lic.sign_revocations(lic.private_from_b64(self.license()["KhoaBiMatCongTy"]), ids)

    def apply_revocations(self, token: str) -> int:
        director = self.director_payload()
        ids = lic.read_revocations(token, director["pub"])
        n = 0
        for code_id in ids:
            self.db.execute("INSERT OR IGNORE INTO DanhSachThuHoi (MaKichHoat, NgayThuHoi) VALUES (?, ?)",
                            (code_id, now_iso()))
            cur = self.db.conn.execute(
                "UPDATE NguoiDung SET TrangThai = 'Đã thu hồi' WHERE MaKichHoat = ? AND TrangThai != 'Đã thu hồi'",
                (code_id,))
            n += cur.rowcount
        self.db.conn.commit()
        return n

    # ---- người dùng
    def _create_user(self, username, fullname, role, password, perms, code_id) -> int:
        cur = self.db.conn.execute(
            "INSERT INTO NguoiDung (TenDangNhap, HoTen, MatKhauBam, VaiTro, MaKichHoat, NgayTao)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (username, fullname, hash_password(password), role, code_id, now_iso()))
        uid = cur.lastrowid
        for f, acts in perms.items():
            self.db.conn.execute(
                "INSERT INTO PhanQuyen (MaNguoiDung, ChucNang, Xem, Them, Sua, Xoa, Xuat) VALUES (?,?,?,?,?,?,?)",
                (uid, f, *(int(acts.get(a, 0)) for a in ACTIONS)))
        self.db.conn.commit()
        return uid

    def users(self):
        return self.db.query("SELECT * FROM NguoiDung ORDER BY MaNguoiDung")

    def login(self, username: str, password: str):
        u = self.db.one("SELECT * FROM NguoiDung WHERE TenDangNhap = ?", (username,))
        if not u:
            raise AuthError("Sai tên đăng nhập hoặc mật khẩu.")
        if u["TrangThai"] in ("Đã khóa", "Đã thu hồi"):
            raise AuthError(f"Tài khoản {u['TrangThai'].lower()}.")
        if u["MaKichHoat"] and self.db.one("SELECT 1 FROM DanhSachThuHoi WHERE MaKichHoat = ?",
                                          (u["MaKichHoat"],)):
            raise AuthError("Tài khoản đã bị Giám đốc thu hồi.")
        now = datetime.now(timezone.utc)
        if u["KhoaDen"] and datetime.fromisoformat(u["KhoaDen"]) > now:
            raise AuthError(f"Nhập sai quá {MAX_FAILED} lần, tài khoản tạm khóa {LOCK_MINUTES} phút.")
        if not verify_password(password, u["MatKhauBam"]):
            fails = u["SoLanSaiMK"] + 1
            lock = (now + timedelta(minutes=LOCK_MINUTES)).isoformat() if fails >= MAX_FAILED else None
            self.db.execute("UPDATE NguoiDung SET SoLanSaiMK = ?, KhoaDen = ? WHERE MaNguoiDung = ?",
                            (0 if lock else fails, lock, u["MaNguoiDung"]))
            self.db.log(username, "Đăng nhập sai")
            raise AuthError("Sai tên đăng nhập hoặc mật khẩu.")
        self.db.execute("UPDATE NguoiDung SET SoLanSaiMK = 0, KhoaDen = NULL, LanDangNhapCuoi = ? "
                        "WHERE MaNguoiDung = ?", (now_iso(), u["MaNguoiDung"]))
        self.db.log(username, "Đăng nhập")
        return self.db.one("SELECT * FROM NguoiDung WHERE MaNguoiDung = ?", (u["MaNguoiDung"],))

    def change_password(self, user_id: int, old: str, new: str) -> None:
        u = self.db.one("SELECT * FROM NguoiDung WHERE MaNguoiDung = ?", (user_id,))
        if not verify_password(old, u["MatKhauBam"]):
            raise AuthError("Mật khẩu cũ không đúng.")
        _check_password_rule(new)
        self.db.execute("UPDATE NguoiDung SET MatKhauBam = ?, BatBuocDoiMK = 0 WHERE MaNguoiDung = ?",
                        (hash_password(new), user_id))

    def permissions(self, user) -> dict:
        rows = self.db.query("SELECT * FROM PhanQuyen WHERE MaNguoiDung = ?", (user["MaNguoiDung"],))
        return {r["ChucNang"]: {a: r[a] for a in ACTIONS} for r in rows}

    def can(self, user, feature: str, action: str = "Xem") -> bool:
        if feature == "ThuChi" and user["VaiTro"] == "Kế toán viên":
            return False
        if feature == "TaiKhoan" and user["VaiTro"] != "Giám đốc":
            return False
        return bool(self.permissions(user).get(feature, {}).get(action, 0))


def _check_password_rule(password: str) -> None:
    if len(password) < 8:
        raise AuthError("Mật khẩu phải có ít nhất 8 ký tự.")
