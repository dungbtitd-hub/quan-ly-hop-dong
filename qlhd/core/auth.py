"""Tài khoản, mật khẩu, phân quyền và các bước kích hoạt trên một máy."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
from datetime import date, datetime, timedelta, timezone

from . import licensing as lic
from .db import Database, now_iso

ROLES = ("Giám đốc", "Người quản lý", "Kế toán viên")
FEATURES = ("HopDong", "HoaDonDauRa", "HoaDonDauVao", "BaoCao", "DanhMuc", "SaoLuu",
            "DongBo", "ThuChi", "TaiKhoan")
ACTIONS = ("Xem", "Them", "Sua", "Xoa", "Xuat")
MAX_FAILED = 5
LOCK_MINUTES = 15
WARN_DAYS = 30          # báo trước khi bản quyền hết hạn
CLOCK_TOLERANCE_DAYS = 1
PBKDF2_ITER = 200_000
USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{3,32}")


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

    # ---- thời hạn bản quyền
    def license_status(self, today: date | None = None) -> dict:
        """trang_thai: 'vinh_vien' | 'con_han' | 'sap_het' | 'het_han' | 'lui_ngay' | 'loi'."""
        today = today or date.today()
        try:
            payload = self.director_payload()
        except lic.LicenseError:
            return {"trang_thai": "loi", "han": None, "con_lai": None,
                    "thong_bao": "Key bản quyền trên máy này không hợp lệ với phiên bản phần mềm này."}
        if payload is None:
            return {"trang_thai": "chua_kich_hoat", "han": None, "con_lai": None, "thong_bao": ""}
        han = lic.key_expiry(payload)
        if not han:
            return {"trang_thai": "vinh_vien", "han": None, "con_lai": None, "thong_bao": ""}
        seen = self.db.setting("ngay_lon_nhat")
        if seen and today < date.fromisoformat(seen) - timedelta(days=CLOCK_TOLERANCE_DAYS):
            return {"trang_thai": "lui_ngay", "han": han, "con_lai": None,
                    "thong_bao": f"Ngày giờ máy tính ({today:%d/%m/%Y}) đang lùi về trước ngày phần mềm đã chạy "
                                 f"({date.fromisoformat(seen):%d/%m/%Y}). Hãy chỉnh lại ngày giờ cho đúng."}
        if not seen or today.isoformat() > seen:
            self.db.set_setting("ngay_lon_nhat", today.isoformat())
        con_lai = (date.fromisoformat(han) - today).days
        han_vn = date.fromisoformat(han).strftime("%d/%m/%Y")
        if con_lai < 0:
            return {"trang_thai": "het_han", "han": han, "con_lai": con_lai,
                    "thong_bao": f"Bản quyền đã hết hạn ngày {han_vn}. Dữ liệu vẫn được giữ nguyên; "
                                 "gia hạn để tiếp tục sử dụng."}
        if con_lai <= WARN_DAYS:
            return {"trang_thai": "sap_het", "han": han, "con_lai": con_lai,
                    "thong_bao": f"Bản quyền còn {con_lai} ngày (hết hạn {han_vn}). Liên hệ nhà phát triển để gia hạn."}
        return {"trang_thai": "con_han", "han": han, "con_lai": con_lai, "thong_bao": ""}

    def license_usable(self, today: date | None = None) -> bool:
        return self.license_status(today)["trang_thai"] in ("vinh_vien", "con_han", "sap_het")

    def renewal_request(self) -> str:
        """Mã yêu cầu gia hạn: cùng Mã máy và khóa công ty đã kích hoạt, nên Key mới khớp dữ liệu cũ."""
        row = self.license()
        if not row or not row["KhoaBiMatCongTy"]:
            raise AuthError("Chỉ máy Giám đốc chính mới gửi yêu cầu gia hạn cho nhà phát triển.")
        pub = lic.public_to_b64(lic.private_from_b64(row["KhoaBiMatCongTy"]).public_key())
        return lic.make_request(self.machine_id, pub)

    def renew(self, key: str, today: date | None = None) -> dict:
        """Nhận Key gia hạn. Máy Giám đốc chính: Key mới của nhà phát triển cho đúng máy này.
        Máy nhân viên / máy Giám đốc phụ: Key Giám đốc hiện hành (Giám đốc sao chép gửi sang)."""
        row = self.license()
        if not row:
            raise AuthError("Máy chưa kích hoạt.")
        key = "".join(key.split())
        if key.startswith("QLHD-NV:"):
            try:
                key = json.loads(lic.b64d(key.split(":", 1)[1]))["key"]
            except (ValueError, KeyError):
                raise AuthError("Mã không đúng định dạng.") from None
        primary = bool(row["KhoaBiMatCongTy"])
        new = lic.verify_director_key(key, self.machine_id if primary else None, self.vendor_pub)
        try:
            old_pub = lic.verify_director_key(row["KeyGiamDoc"], None, self.vendor_pub)["pub"]
        except lic.LicenseError:
            old_pub = None
        if primary:
            own = lic.public_to_b64(lic.private_from_b64(row["KhoaBiMatCongTy"]).public_key())
            if new["pub"] != own:
                raise AuthError("Key không khớp với Mã yêu cầu gia hạn của máy này.")
        elif new["pub"] != old_pub:
            raise AuthError("Key này thuộc công ty / máy Giám đốc khác.")
        if lic.is_expired(new, today):
            raise AuthError("Key này cũng đã hết hạn.")
        self.db.execute("UPDATE BanQuyen SET KeyGiamDoc = ?, TenCongTy = ?, MaCongTy = ?, SoTaiKhoanToiDa = ?"
                        " WHERE Id = 1", (key, new["cty"], new["mst"], new["toi_da"]))
        self.db.log(None, "Gia hạn bản quyền", lic.expiry_text(new))
        return new

    def maybe_update_key(self, key: str) -> bool:
        """Nhận Key Giám đốc đi kèm gói đồng bộ: chỉ thay khi cùng công ty và hạn dài hơn."""
        row = self.license()
        if not row or row["KhoaBiMatCongTy"] or "".join(key.split()) == row["KeyGiamDoc"]:
            return False
        try:
            new = lic.verify_director_key(key, None, self.vendor_pub)
            cur = lic.verify_director_key(row["KeyGiamDoc"], None, self.vendor_pub)
        except lic.LicenseError:
            return False
        if new["pub"] != cur["pub"]:
            return False
        longer = lambda p: lic.key_expiry(p) or "9999-12-31"  # noqa: E731
        if longer(new) <= longer(cur):
            return False
        self.renew(key)
        return True

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
        if lic.is_expired(payload):
            raise AuthError("Key đã hết hạn.")
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
        if lic.is_expired(director):
            raise AuthError("Bản quyền của công ty đã hết hạn, Giám đốc cần gia hạn trước.")
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
        st = self.license_status()
        if st["trang_thai"] in ("het_han", "lui_ngay", "loi"):
            raise AuthError(st["thong_bao"])
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

    def change_username(self, user_id: int, password: str, new_name: str) -> None:
        """Đổi tên đăng nhập; chuyển theo Khóa thu chi và trạng thái khóa của tài khoản."""
        u = self.db.one("SELECT * FROM NguoiDung WHERE MaNguoiDung = ?", (user_id,))
        if not verify_password(password, u["MatKhauBam"]):
            raise AuthError("Mật khẩu hiện tại không đúng.")
        new_name = new_name.strip()
        if not USERNAME_RE.fullmatch(new_name):
            raise AuthError("Tên đăng nhập dài 3–32 ký tự, chỉ gồm chữ không dấu, số và dấu . _ -")
        if new_name == u["TenDangNhap"]:
            return
        if new_name.startswith("__") or self.db.one(
                "SELECT 1 FROM NguoiDung WHERE lower(TenDangNhap) = lower(?) AND MaNguoiDung != ?", (new_name, user_id)):
            raise AuthError(f"Tên đăng nhập \"{new_name}\" đã có người dùng.")
        old = u["TenDangNhap"]
        c = self.db.conn
        c.execute("UPDATE NguoiDung SET TenDangNhap = ? WHERE MaNguoiDung = ?", (new_name, user_id))
        c.execute("UPDATE KhoaThuChi SET TenDangNhap = ? WHERE TenDangNhap = ?", (new_name, old))
        c.execute("UPDATE CaiDat SET Khoa = ? WHERE Khoa = ?", ("tc_sai:" + new_name, "tc_sai:" + old))
        c.execute("UPDATE CaiDat SET GiaTri = ? WHERE Khoa = 'dang_nhap_cuoi' AND GiaTri = ?", (new_name, old))
        c.commit()
        self.db.log(new_name, "Đổi tên đăng nhập", f"{old} → {new_name}")

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
