"""Module Thu chi: tệp ThuChi.dat mã hóa riêng, chỉ Giám đốc và Người quản lý mở được.

- Toàn bộ cơ sở dữ liệu thu chi nằm trong bộ nhớ khi đang mở; khi lưu được tuần tự hóa và mã hóa
  AES-256-GCM bằng "chìa khóa dữ liệu" ngẫu nhiên.
- Chìa khóa dữ liệu được bọc riêng bằng Khóa thu chi của từng người (bảng KhoaThuChi ở tệp chính)
  và một bản bọc bằng Mã khôi phục.
- Phần Thu tự lấy từ các lần thanh toán hợp đồng đầu ra (bảng ThanhToanHopDong), không lưu bản sao.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import crypto
from .db import SYNC_COLS, Database, Store, new_id, now_iso

FILE_MAGIC = b"QLHDTC1\n"
RECOVERY_USER = "__khoiphuc__"
MAX_FAILED = 5
LOCK_MINUTES = 15
ALLOWED_ROLES = ("Giám đốc", "Người quản lý")

FIN_TABLES = ["QuyTien", "KhoanMuc", "PhieuThuChi"]

FIN_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS QuyTien (
    Ma TEXT PRIMARY KEY,
    TenQuy TEXT NOT NULL,
    LoaiQuy TEXT NOT NULL CHECK(LoaiQuy IN ('Tiền mặt', 'Ngân hàng')),
    SoDuBanDau INTEGER NOT NULL DEFAULT 0,
    NgaySoDuBanDau TEXT NOT NULL,
    DangSuDung INTEGER NOT NULL DEFAULT 1,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS KhoanMuc (
    Ma TEXT PRIMARY KEY,
    TenKhoanMuc TEXT NOT NULL,
    LoaiThuChi TEXT NOT NULL CHECK(LoaiThuChi IN ('Thu', 'Chi')),
    MaKhoanMucCha TEXT,
    ApDungCho TEXT NOT NULL DEFAULT 'Cả hai' CHECK(ApDungCho IN ('Dự án', 'Văn phòng', 'Cả hai')),
    ThuTu INTEGER NOT NULL DEFAULT 0,
    DangSuDung INTEGER NOT NULL DEFAULT 1,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS PhieuThuChi (
    Ma TEXT PRIMARY KEY,
    SoPhieu TEXT NOT NULL,
    NgayPhatSinh TEXT NOT NULL,
    LoaiThuChi TEXT NOT NULL CHECK(LoaiThuChi IN ('Thu', 'Chi')),
    PhanLoaiChi TEXT CHECK(PhanLoaiChi IN ('Dự án', 'Văn phòng')),
    MaDuAn TEXT,
    TenDuAn TEXT,
    MaKhoanMuc TEXT NOT NULL,
    MaQuy TEXT NOT NULL,
    SoTien INTEGER NOT NULL CHECK(SoTien > 0),
    NoiDung TEXT NOT NULL,
    DoiTuong TEXT,
    ThamChieu TEXT,
    DuongDanTep TEXT,
    NguoiTao TEXT,
    NguoiSua TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS XoaVinhVien (
    Bang TEXT NOT NULL,
    Ma TEXT NOT NULL,
    NgayXoa TEXT NOT NULL,
    PRIMARY KEY (Bang, Ma)
);
CREATE TABLE IF NOT EXISTS CaiDatTC (
    Khoa TEXT PRIMARY KEY,
    GiaTri TEXT
);
"""

DEFAULT_CATEGORIES = {
    ("Chi", "Văn phòng"): {
        "Nhân sự": ["Lương", "BHXH", "Thưởng"],
        "Văn phòng": ["Thuê văn phòng", "Điện nước", "Internet", "Văn phòng phẩm"],
        "Giao dịch": ["Tiếp khách", "Xăng xe", "Công tác phí"],
    },
    ("Chi", "Dự án"): {
        "Chi phí dự án": ["Vật tư, thiết bị", "Nhân công, thầu phụ", "Thuê máy", "Vận chuyển",
                          "Chi phí khác"],
    },
    ("Thu", "Cả hai"): {
        "Thu khác": ["Lãi tiền gửi", "Thanh lý tài sản", "Góp vốn", "Hoàn tiền"],
    },
}


class FinanceError(Exception):
    pass


class FinanceDB(Store):
    synced_tables = FIN_TABLES


# ------------------------------------------------------------------ khóa
def is_initialized(db: Database) -> bool:
    return db.one("SELECT 1 FROM KhoaThuChi LIMIT 1") is not None


def has_key_for(db: Database, username: str) -> bool:
    return db.one("SELECT 1 FROM KhoaThuChi WHERE TenDangNhap = ?", (username,)) is not None


def _wrap(db: Database, username: str, secret: str, data_key: bytes, iterations: int) -> None:
    salt = os.urandom(16)
    wrapped = crypto.encrypt(crypto.derive_key(secret, salt, iterations), data_key, b"qlhd-tc-key")
    db.execute("INSERT OR REPLACE INTO KhoaThuChi (TenDangNhap, ChiaKhoaDaBoc, Muoi, NgayDat)"
               " VALUES (?, ?, ?, ?)", (username, wrapped, salt + iterations.to_bytes(4, "big"), now_iso()))


def _unwrap(db: Database, username: str, secret: str) -> bytes:
    row = db.one("SELECT * FROM KhoaThuChi WHERE TenDangNhap = ?", (username,))
    if not row:
        raise FinanceError("Tài khoản này chưa được đặt Khóa thu chi.")
    salt, iterations = row["Muoi"][:16], int.from_bytes(row["Muoi"][16:], "big")
    try:
        return crypto.decrypt(crypto.derive_key(secret, salt, iterations), row["ChiaKhoaDaBoc"],
                              b"qlhd-tc-key")
    except crypto.CryptoError:
        raise FinanceError("Khóa thu chi không đúng.") from None


def _check_role(user) -> None:
    if user["VaiTro"] not in ALLOWED_ROLES:
        raise FinanceError("Tài khoản này không có quyền truy cập Thu chi.")


def _lock_state(db: Database, username: str) -> tuple[int, str | None]:
    raw = db.setting("tc_sai:" + username)
    if not raw:
        return 0, None
    n, until = raw.split("|")
    return int(n), until or None


def _register_fail(db: Database, username: str) -> None:
    n, _ = _lock_state(db, username)
    n += 1
    if n >= MAX_FAILED:
        until = (datetime.now(timezone.utc) + timedelta(minutes=LOCK_MINUTES)).isoformat()
        db.set_setting("tc_sai:" + username, f"0|{until}")
        db.log(username, "Sai Khóa thu chi 5 lần, tạm khóa")
    else:
        db.set_setting("tc_sai:" + username, f"{n}|")
        db.log(username, "Sai Khóa thu chi")


def _check_locked(db: Database, username: str) -> None:
    _, until = _lock_state(db, username)
    if until and datetime.fromisoformat(until) > datetime.now(timezone.utc):
        raise FinanceError(f"Nhập sai Khóa thu chi {MAX_FAILED} lần, tạm khóa {LOCK_MINUTES} phút.")


def install_wrappers(db: Database, wrappers: list[dict]) -> int:
    """Nhận các bản bọc chìa khóa từ Gói Giám đốc (chỉ thêm tài khoản máy này chưa có)."""
    n = 0
    for w in wrappers:
        if not has_key_for(db, w["TenDangNhap"]):
            db.execute("INSERT INTO KhoaThuChi (TenDangNhap, ChiaKhoaDaBoc, Muoi, NgayDat) VALUES (?, ?, ?, ?)",
                       (w["TenDangNhap"], bytes.fromhex(w["ChiaKhoaDaBoc"]), bytes.fromhex(w["Muoi"]),
                        w["NgayDat"]))
            n += 1
    return n


# ------------------------------------------------------------------ kho thu chi
class FinanceStore:
    def __init__(self, db: Database, path: Path, data_key: bytes, username: str):
        self.db = db
        self.path = Path(path)
        self.data_key = data_key
        self.username = username
        conn = sqlite3.connect(":memory:")
        if self.path.exists():
            blob = self.path.read_bytes()
            if not blob.startswith(FILE_MAGIC):
                raise FinanceError("Tệp thu chi không đúng định dạng.")
            try:
                conn.deserialize(crypto.decrypt(data_key, blob[len(FILE_MAGIC):], FILE_MAGIC))
            except crypto.CryptoError:
                raise FinanceError("Không giải mã được tệp thu chi (khóa không khớp với tệp).") from None
        self.fdb = FinanceDB(conn, db.machine_id)
        self.fdb.conn.executescript(FIN_SCHEMA)
        self.fdb.conn.commit()

    # ---- mở / tạo
    @classmethod
    def initialize(cls, db: Database, path: Path, user, passphrase: str, *,
                   iterations: int = crypto.KDF_ITER) -> tuple["FinanceStore", str]:
        _check_role(user)
        if is_initialized(db):
            raise FinanceError("Thu chi đã được khởi tạo trên máy này.")
        if len(passphrase) < 8:
            raise FinanceError("Khóa thu chi phải có ít nhất 8 ký tự.")
        data_key = crypto.new_key()
        code = crypto.recovery_code()
        _wrap(db, user["TenDangNhap"], passphrase, data_key, iterations)
        _wrap(db, RECOVERY_USER, crypto.normalize_recovery(code), data_key, iterations)
        store = cls(db, path, data_key, user["TenDangNhap"])
        store._seed()
        store.save()
        db.log(user["TenDangNhap"], "Khởi tạo Thu chi")
        return store, code

    @classmethod
    def unlock(cls, db: Database, path: Path, user, passphrase: str) -> "FinanceStore":
        _check_role(user)
        name = user["TenDangNhap"]
        _check_locked(db, name)
        try:
            data_key = _unwrap(db, name, passphrase)
        except FinanceError as e:
            if has_key_for(db, name):
                _register_fail(db, name)
            raise e
        db.set_setting("tc_sai:" + name, None)
        store = cls(db, path, data_key, name)
        if not Path(path).exists():
            store.save()
        db.log(name, "Mở khóa Thu chi")
        return store

    @classmethod
    def unlock_with_recovery(cls, db: Database, path: Path, user, code: str, new_passphrase: str, *,
                             iterations: int = crypto.KDF_ITER) -> "FinanceStore":
        _check_role(user)
        try:
            data_key = _unwrap(db, RECOVERY_USER, crypto.normalize_recovery(code))
        except FinanceError:
            raise FinanceError("Mã khôi phục không đúng.") from None
        if len(new_passphrase) < 8:
            raise FinanceError("Khóa thu chi phải có ít nhất 8 ký tự.")
        _wrap(db, user["TenDangNhap"], new_passphrase, data_key, iterations)
        db.set_setting("tc_sai:" + user["TenDangNhap"], None)
        db.log(user["TenDangNhap"], "Đặt lại Khóa thu chi bằng Mã khôi phục")
        return cls(db, path, data_key, user["TenDangNhap"])

    def set_user_key(self, username: str, passphrase: str, *, iterations: int = crypto.KDF_ITER) -> None:
        """Đặt / đổi Khóa thu chi cho một người có quyền (Giám đốc, Người quản lý)."""
        u = self.db.one("SELECT * FROM NguoiDung WHERE TenDangNhap = ?", (username,))
        if u is not None:
            _check_role(u)
        if len(passphrase) < 8:
            raise FinanceError("Khóa thu chi phải có ít nhất 8 ký tự.")
        _wrap(self.db, username, passphrase, self.data_key, iterations)
        self.db.log(self.username, "Đặt Khóa thu chi", username)

    def wrappers(self) -> list[dict]:
        return [{"TenDangNhap": r["TenDangNhap"], "ChiaKhoaDaBoc": r["ChiaKhoaDaBoc"].hex(),
                 "Muoi": r["Muoi"].hex(), "NgayDat": r["NgayDat"]}
                for r in self.db.query("SELECT * FROM KhoaThuChi")]

    def save(self) -> None:
        self.fdb.conn.commit()
        blob = FILE_MAGIC + crypto.encrypt(self.data_key, self.fdb.conn.serialize(), FILE_MAGIC)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self.path)

    def lock(self) -> None:
        self.save()
        self.fdb.close()
        self.data_key = b""

    # ---- cài đặt riêng của thu chi
    def setting(self, key: str, default: str | None = None) -> str | None:
        r = self.fdb.one("SELECT GiaTri FROM CaiDatTC WHERE Khoa = ?", (key,))
        return r["GiaTri"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.fdb.execute("INSERT OR REPLACE INTO CaiDatTC (Khoa, GiaTri) VALUES (?, ?)", (key, value))

    def _seed(self) -> None:
        today = date.today().replace(month=1, day=1).isoformat()
        tm = self.fdb.insert("QuyTien", {"TenQuy": "Tiền mặt", "LoaiQuy": "Tiền mặt", "NgaySoDuBanDau": today})
        nh = self.fdb.insert("QuyTien", {"TenQuy": "Tài khoản ngân hàng", "LoaiQuy": "Ngân hàng",
                                         "NgaySoDuBanDau": today})
        self.set_setting("quy_tien_mat", tm)
        self.set_setting("quy_ngan_hang", nh)
        order = 0
        for (loai, ap_dung), groups in DEFAULT_CATEGORIES.items():
            for group, children in groups.items():
                order += 1
                g = self.add_category(group, loai, None, ap_dung, order=order)
                for child in children:
                    order += 1
                    self.add_category(child, loai, g, ap_dung, order=order)

    # ---- quỹ
    def funds(self, include_hidden: bool = False) -> list[sqlite3.Row]:
        where = "" if include_hidden else "DangSuDung = 1"
        return self.fdb.active("QuyTien", where, order="LoaiQuy, TenQuy")

    def add_fund(self, ten: str, loai: str, so_du: int = 0, ngay: str | None = None) -> str:
        if not ten.strip():
            raise FinanceError("Chưa nhập tên quỹ.")
        ma = self.fdb.insert("QuyTien", {"TenQuy": ten.strip(), "LoaiQuy": loai, "SoDuBanDau": int(so_du),
                                         "NgaySoDuBanDau": ngay or date.today().isoformat()})
        self.save()
        return ma

    def update_fund(self, ma: str, values: dict) -> None:
        self.fdb.update("QuyTien", ma, values)
        self.save()

    def default_fund(self, hinh_thuc: str) -> str | None:
        return self.setting("quy_tien_mat" if hinh_thuc == "Tiền mặt" else "quy_ngan_hang")

    # ---- khoản mục
    def add_category(self, ten: str, loai: str = "Chi", cha: str | None = None, ap_dung: str = "Cả hai",
                     *, order: int | None = None) -> str:
        if not ten.strip():
            raise FinanceError("Chưa nhập tên mục.")
        if cha:
            parent = self.fdb.get("KhoanMuc", cha)
            if parent is None or parent["LoaiThuChi"] != loai:
                raise FinanceError("Mục cha không hợp lệ.")
        if order is None:
            order = (self.fdb.one("SELECT MAX(ThuTu) m FROM KhoanMuc")["m"] or 0) + 1
        return self.fdb.insert("KhoanMuc", {"TenKhoanMuc": ten.strip(), "LoaiThuChi": loai,
                                            "MaKhoanMucCha": cha, "ApDungCho": ap_dung, "ThuTu": order})

    def rename_category(self, ma: str, ten: str) -> None:
        self.fdb.update("KhoanMuc", ma, {"TenKhoanMuc": ten.strip()})
        self.save()

    def move_category(self, ma: str, new_parent: str | None) -> None:
        p = new_parent
        while p:
            if p == ma:
                raise FinanceError("Không thể chuyển một mục vào chính mục con của nó.")
            p = self.fdb.get("KhoanMuc", p)["MaKhoanMucCha"]
        self.fdb.update("KhoanMuc", ma, {"MaKhoanMucCha": new_parent})
        self.save()

    def set_category_visible(self, ma: str, visible: bool) -> None:
        self.fdb.update("KhoanMuc", ma, {"DangSuDung": int(visible)})
        self.save()

    def delete_category(self, ma: str) -> None:
        used = self.fdb.one("SELECT 1 FROM PhieuThuChi WHERE MaKhoanMuc = ? AND DaXoa = 0", (ma,))
        child = self.fdb.one("SELECT 1 FROM KhoanMuc WHERE MaKhoanMucCha = ? AND DaXoa = 0", (ma,))
        if used or child:
            raise FinanceError("Mục đã có phiếu hoặc có mục con, chỉ được ẩn, không xóa.")
        self.fdb.soft_delete("KhoanMuc", ma)
        self.save()

    def merge_categories(self, src: str, dst: str) -> int:
        """Chuyển toàn bộ phiếu của mục src sang mục dst rồi ẩn src."""
        rows = self.fdb.active("PhieuThuChi", "MaKhoanMuc = ?", (src,))
        for r in rows:
            self.fdb.update("PhieuThuChi", r["Ma"], {"MaKhoanMuc": dst, "NguoiSua": self.username}, commit=False)
        self.fdb.update("KhoanMuc", src, {"DangSuDung": 0}, commit=False)
        self.save()
        return len(rows)

    def categories(self, loai: str | None = None, include_hidden: bool = False) -> list[sqlite3.Row]:
        where, params = [], []
        if loai:
            where.append("LoaiThuChi = ?")
            params.append(loai)
        if not include_hidden:
            where.append("DangSuDung = 1")
        return self.fdb.active("KhoanMuc", " AND ".join(where), params, order="ThuTu, TenKhoanMuc")

    def category_tree(self, loai: str | None = None, include_hidden: bool = False) -> list[tuple[sqlite3.Row, int]]:
        rows = self.categories(loai, include_hidden)
        ids = {r["Ma"] for r in rows}
        children: dict[str | None, list] = {}
        for r in rows:
            parent = r["MaKhoanMucCha"] if r["MaKhoanMucCha"] in ids else None
            children.setdefault(parent, []).append(r)
        out: list[tuple[sqlite3.Row, int]] = []

        def walk(parent, depth):
            for r in children.get(parent, []):
                out.append((r, depth))
                walk(r["Ma"], depth + 1)
        walk(None, 0)
        return out

    def category_path(self, ma: str | None) -> str:
        names = []
        seen = set()
        while ma and ma not in seen:
            seen.add(ma)
            r = self.fdb.get("KhoanMuc", ma)
            if r is None:
                break
            names.append(r["TenKhoanMuc"])
            ma = r["MaKhoanMucCha"]
        return " › ".join(reversed(names))

    def category_root(self, ma: str | None) -> str | None:
        last = None
        seen = set()
        while ma and ma not in seen:
            seen.add(ma)
            r = self.fdb.get("KhoanMuc", ma)
            if r is None:
                break
            last = r["Ma"]
            ma = r["MaKhoanMucCha"]
        return last

    # ---- phiếu thu khác / phiếu chi
    def next_voucher_no(self, loai: str, ngay: str) -> str:
        prefix = f"{'PT' if loai == 'Thu' else 'PC'}-{ngay[:4]}-"
        rows = self.fdb.query("SELECT SoPhieu FROM PhieuThuChi WHERE SoPhieu LIKE ?", (prefix + "%",))
        nums = [int(r["SoPhieu"][len(prefix):]) for r in rows if r["SoPhieu"][len(prefix):].isdigit()]
        return f"{prefix}{(max(nums) if nums else 0) + 1:04d}"

    def _validate(self, v: dict) -> dict:
        v = dict(v)
        if v.get("LoaiThuChi") not in ("Thu", "Chi"):
            raise FinanceError("Chọn Thu hoặc Chi.")
        if not v.get("NgayPhatSinh"):
            raise FinanceError("Chưa nhập ngày.")
        if int(v.get("SoTien") or 0) <= 0:
            raise FinanceError("Số tiền phải lớn hơn 0.")
        if not v.get("MaKhoanMuc"):
            raise FinanceError("Chưa chọn khoản mục.")
        if not v.get("MaQuy"):
            raise FinanceError("Chưa chọn quỹ.")
        if not (v.get("NoiDung") or "").strip():
            raise FinanceError("Chưa nhập nội dung.")
        if v["LoaiThuChi"] == "Chi":
            if v.get("PhanLoaiChi") not in ("Dự án", "Văn phòng"):
                raise FinanceError("Phiếu chi phải chọn Chi dự án hoặc Chi hoạt động văn phòng.")
            if v["PhanLoaiChi"] == "Dự án" and not (v.get("TenDuAn") or "").strip():
                raise FinanceError("Chi dự án phải chọn hoặc nhập tên dự án.")
            if v["PhanLoaiChi"] == "Văn phòng":
                v["MaDuAn"] = None
                v["TenDuAn"] = None
        else:
            v["PhanLoaiChi"] = None
        v["SoTien"] = int(v["SoTien"])
        return v

    def add_voucher(self, values: dict) -> str:
        v = self._validate(values)
        v.setdefault("SoPhieu", None)
        if not v["SoPhieu"]:
            v["SoPhieu"] = self.next_voucher_no(v["LoaiThuChi"], v["NgayPhatSinh"])
        v["NguoiTao"] = self.username
        v["NguoiSua"] = self.username
        ma = self.fdb.insert("PhieuThuChi", v)
        self.save()
        return ma

    def update_voucher(self, ma: str, values: dict) -> None:
        current = dict(self.fdb.get("PhieuThuChi", ma))
        current.update(values)
        v = self._validate(current)
        v["NguoiSua"] = self.username
        self.fdb.update("PhieuThuChi", ma, v)
        self.save()

    def _hard_delete(self, ma: str) -> None:
        self.fdb.conn.execute("DELETE FROM PhieuThuChi WHERE Ma = ?", (ma,))
        self.fdb.conn.execute("INSERT OR REPLACE INTO XoaVinhVien (Bang, Ma, NgayXoa) VALUES ('PhieuThuChi', ?, ?)",
                              (ma, now_iso()))

    def _compact(self) -> None:
        self.fdb.conn.commit()
        self.fdb.conn.execute("VACUUM")
        self.save()

    def delete_vouchers(self, ids: list[str]) -> int:
        for ma in ids:
            self._hard_delete(ma)
        self._compact()
        self.db.log(self.username, f"Xóa {len(ids)} phiếu thu chi")
        return len(ids)

    def range_preview(self, tu: str, den: str) -> dict:
        r = self.fdb.one(
            "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN LoaiThuChi='Thu' THEN SoTien END),0) thu,"
            " COALESCE(SUM(CASE WHEN LoaiThuChi='Chi' THEN SoTien END),0) chi FROM PhieuThuChi"
            " WHERE DaXoa = 0 AND NgayPhatSinh BETWEEN ? AND ?", (tu, den))
        return {"so_phieu": r["n"], "thu": r["thu"], "chi": r["chi"]}

    def delete_range(self, tu: str, den: str, passphrase: str) -> int:
        _unwrap(self.db, self.username, passphrase)
        ids = [r["Ma"] for r in self.fdb.query(
            "SELECT Ma FROM PhieuThuChi WHERE NgayPhatSinh BETWEEN ? AND ?", (tu, den))]
        for ma in ids:
            self._hard_delete(ma)
        self._compact()
        self.db.log(self.username, f"Xóa thu chi từ {tu} đến {den} ({len(ids)} phiếu)")
        return len(ids)

    def delete_all(self, user, passphrase: str, confirm: str, keep_catalog: bool = True) -> None:
        if user["VaiTro"] != "Giám đốc":
            raise FinanceError("Chỉ Giám đốc được xóa toàn bộ dữ liệu thu chi.")
        if confirm.strip().upper() != "XOA TOAN BO":
            raise FinanceError('Gõ đúng chữ "XOA TOAN BO" để xác nhận.')
        _unwrap(self.db, self.username, passphrase)
        for r in self.fdb.query("SELECT Ma FROM PhieuThuChi"):
            self._hard_delete(r["Ma"])
        if not keep_catalog:
            ts = now_iso()
            for t in ("KhoanMuc", "QuyTien"):
                for r in self.fdb.query(f"SELECT Ma FROM {t}"):
                    self.fdb.conn.execute("INSERT OR REPLACE INTO XoaVinhVien VALUES (?, ?, ?)", (t, r["Ma"], ts))
                self.fdb.conn.execute(f"DELETE FROM {t}")
        self._compact()
        self.db.log(self.username, "Xóa toàn bộ dữ liệu thu chi")

    # ---- số liệu
    def projects(self) -> list[sqlite3.Row]:
        return self.db.active("HopDong", "ChieuHopDong = 'Đầu ra'", order="NgayKy DESC")

    def contract_receipts(self, tu: str | None = None, den: str | None = None) -> list[dict]:
        sql = ("SELECT t.Ma, t.NgayThanhToan, t.SoTien, t.HinhThuc, t.SoThamChieuSK, t.GhiChu,"
               " h.Ma MaHopDong, h.SoHopDong, h.TenHopDong, h.TenDoiTac, r.SoHoaDon"
               " FROM ThanhToanHopDong t JOIN HopDong h ON h.Ma = t.MaHopDong"
               " LEFT JOIN HoaDonDauRa r ON r.Ma = t.MaHDDauRa"
               " WHERE t.DaXoa = 0 AND h.DaXoa = 0 AND h.ChieuHopDong = 'Đầu ra'")
        params: list = []
        if tu:
            sql += " AND t.NgayThanhToan >= ?"
            params.append(tu)
        if den:
            sql += " AND t.NgayThanhToan <= ?"
            params.append(den)
        out = []
        for r in self.db.query(sql + " ORDER BY t.NgayThanhToan", params):
            noi_dung = f"Thu tiền HĐ {r['SoHopDong']} – {r['TenDoiTac']}"
            if r["SoHoaDon"]:
                noi_dung += f" (HĐơn {r['SoHoaDon']})"
            out.append({
                "Nguon": "Hợp đồng", "Ma": r["Ma"], "Ngay": r["NgayThanhToan"], "SoPhieu": r["SoHopDong"],
                "NoiDung": noi_dung, "LoaiThuChi": "Thu", "PhanLoai": "Thu từ hợp đồng",
                "MaDuAn": r["MaHopDong"], "TenDuAn": r["TenHopDong"], "MaKhoanMuc": None,
                "KhoanMuc": "Thu từ hợp đồng", "MaQuy": self.default_fund(r["HinhThuc"]),
                "Thu": r["SoTien"], "Chi": 0, "ThamChieu": r["SoThamChieuSK"], "DoiTuong": r["TenDoiTac"],
            })
        return out

    def vouchers(self, tu: str | None = None, den: str | None = None) -> list[sqlite3.Row]:
        where, params = [], []
        if tu:
            where.append("NgayPhatSinh >= ?")
            params.append(tu)
        if den:
            where.append("NgayPhatSinh <= ?")
            params.append(den)
        return self.fdb.active("PhieuThuChi", " AND ".join(where), params, order="NgayPhatSinh, SoPhieu")

    def entries(self, tu: str | None = None, den: str | None = None, *, loai: str | None = None,
                ma_quy: str | None = None, ma_khoan_muc: str | None = None, tu_khoa: str = "") -> list[dict]:
        """Sổ thu chi hợp nhất: khoản thu hợp đồng + phiếu thu khác + phiếu chi."""
        items = self.contract_receipts(tu, den)
        for r in self.vouchers(tu, den):
            items.append({
                "Nguon": "Phiếu", "Ma": r["Ma"], "Ngay": r["NgayPhatSinh"], "SoPhieu": r["SoPhieu"],
                "NoiDung": r["NoiDung"], "LoaiThuChi": r["LoaiThuChi"],
                "PhanLoai": ("Thu khác" if r["LoaiThuChi"] == "Thu" else
                             "Chi dự án" if r["PhanLoaiChi"] == "Dự án" else "Chi văn phòng"),
                "MaDuAn": r["MaDuAn"], "TenDuAn": r["TenDuAn"] or "", "MaKhoanMuc": r["MaKhoanMuc"],
                "KhoanMuc": self.category_path(r["MaKhoanMuc"]), "MaQuy": r["MaQuy"],
                "Thu": r["SoTien"] if r["LoaiThuChi"] == "Thu" else 0,
                "Chi": r["SoTien"] if r["LoaiThuChi"] == "Chi" else 0,
                "ThamChieu": r["ThamChieu"], "DoiTuong": r["DoiTuong"],
            })
        if loai:
            items = [i for i in items if i["LoaiThuChi"] == loai]
        if ma_quy:
            items = [i for i in items if i["MaQuy"] == ma_quy]
        if ma_khoan_muc:
            sub = self._descendants(ma_khoan_muc)
            items = [i for i in items if i["MaKhoanMuc"] in sub]
        if tu_khoa:
            k = tu_khoa.lower()
            items = [i for i in items if k in (i["NoiDung"] or "").lower() or k in (i["DoiTuong"] or "").lower()
                     or k in (i["SoPhieu"] or "").lower()]
        fund_names = {f["Ma"]: f["TenQuy"] for f in self.funds(True)}
        for i in items:
            i["Quy"] = fund_names.get(i["MaQuy"], "")
        items.sort(key=lambda i: (i["Ngay"], i["Nguon"] != "Hợp đồng", i["SoPhieu"] or ""))
        return items

    def _descendants(self, ma: str) -> set[str]:
        out = {ma}
        frontier = [ma]
        while frontier:
            cur = frontier.pop()
            for r in self.fdb.query("SELECT Ma FROM KhoanMuc WHERE MaKhoanMucCha = ?", (cur,)):
                if r["Ma"] not in out:
                    out.add(r["Ma"])
                    frontier.append(r["Ma"])
        return out

    def balance(self, den: str, ma_quy: str | None = None) -> int:
        """Tồn quỹ tính đến hết ngày `den` (một quỹ hoặc tổng các quỹ)."""
        total = 0
        for f in self.funds(True):
            if ma_quy and f["Ma"] != ma_quy:
                continue
            if f["NgaySoDuBanDau"] <= den:
                total += f["SoDuBanDau"]
        for e in self.entries(None, den):
            if ma_quy and e["MaQuy"] != ma_quy:
                continue
            total += e["Thu"] - e["Chi"]
        return total

    def fund_balances(self, den: str) -> list[dict]:
        return [{"Ma": f["Ma"], "TenQuy": f["TenQuy"], "LoaiQuy": f["LoaiQuy"],
                 "TonQuy": self.balance(den, f["Ma"])} for f in self.funds(True)]

    def totals(self, tu: str, den: str) -> dict:
        items = self.entries(tu, den)
        t = {"thu_hop_dong": 0, "thu_khac": 0, "chi_du_an": 0, "chi_van_phong": 0}
        for i in items:
            if i["Nguon"] == "Hợp đồng":
                t["thu_hop_dong"] += i["Thu"]
            elif i["LoaiThuChi"] == "Thu":
                t["thu_khac"] += i["Thu"]
            elif i["PhanLoai"] == "Chi dự án":
                t["chi_du_an"] += i["Chi"]
            else:
                t["chi_van_phong"] += i["Chi"]
        t["tong_thu"] = t["thu_hop_dong"] + t["thu_khac"]
        t["tong_chi"] = t["chi_du_an"] + t["chi_van_phong"]
        t["chenh_lech"] = t["tong_thu"] - t["tong_chi"]
        t["ton_dau"] = self.balance((date.fromisoformat(tu) - timedelta(days=1)).isoformat())
        t["ton_cuoi"] = self.balance(den)
        return t

    # ---- dữ liệu cho Gói Giám đốc
    def export_rows(self) -> dict:
        return {
            "tables": {t: [dict(r) for r in self.fdb.query(f"SELECT * FROM {t}")] for t in FIN_TABLES},
            "tombstones": [dict(r) for r in self.fdb.query("SELECT * FROM XoaVinhVien")],
            "settings": {r["Khoa"]: r["GiaTri"] for r in self.fdb.query("SELECT * FROM CaiDatTC")},
        }


__all__ = ["FinanceStore", "FinanceError", "FIN_TABLES", "is_initialized", "has_key_for",
           "install_wrappers", "new_id"]
