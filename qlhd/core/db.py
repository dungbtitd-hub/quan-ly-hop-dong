"""Cơ sở dữ liệu SQLite chính (hợp đồng, hóa đơn, tài khoản).

Mọi bảng dữ liệu nghiệp vụ dùng mã định danh toàn cục (UUID) và các cột đồng bộ
(NgayTao, NgaySua, MaMaySua, PhienBan, DaXoa, NgayXoa) để đồng bộ nhiều máy.
Tiền lưu dạng số nguyên (đồng), ngày lưu dạng YYYY-MM-DD.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SYNC_COLS = """
    NgayTao TEXT NOT NULL,
    NgaySua TEXT NOT NULL,
    MaMaySua TEXT NOT NULL,
    PhienBan INTEGER NOT NULL DEFAULT 1,
    DaXoa INTEGER NOT NULL DEFAULT 0,
    NgayXoa TEXT"""

SYNC_FIELDS = ("NgayTao", "NgaySua", "MaMaySua", "PhienBan", "DaXoa", "NgayXoa")

# Bảng nghiệp vụ được đồng bộ giữa các máy, theo thứ tự phụ thuộc khóa ngoại.
SYNCED_TABLES = [
    "LoaiHopDong",
    "DoiTac",
    "NhomDoiTac",
    "NhomDoiTacThanhVien",
    "HopDong",
    "CongViec",
    "LichSuBanGiao",
    "LichSuNghiemThu",
    "HoaDonDauRa",
    "ThanhToanHopDong",
    "HoaDonDauVao",
    "DongHoaDonDauVao",
    "TaiSan",
]

SCHEMA = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS LoaiHopDong (
    Ma TEXT PRIMARY KEY,
    TenLoaiHD TEXT NOT NULL,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS DoiTac (
    Ma TEXT PRIMARY KEY,
    TenDoiTac TEXT NOT NULL,
    MaSoThue TEXT,
    VaiTroDoiTac TEXT NOT NULL DEFAULT 'Chủ đầu tư'
        CHECK(VaiTroDoiTac IN ('Chủ đầu tư', 'Nhà cung cấp', 'Cả hai')),
    DiaChi TEXT, NguoiLienHe TEXT, DienThoai TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS NhomDoiTac (
    Ma TEXT PRIMARY KEY,
    TenNhom TEXT NOT NULL,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS NhomDoiTacThanhVien (
    Ma TEXT PRIMARY KEY,
    MaNhom TEXT NOT NULL REFERENCES NhomDoiTac(Ma),
    MaDoiTac TEXT NOT NULL REFERENCES DoiTac(Ma),{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS HopDong (
    Ma TEXT PRIMARY KEY,
    SoHopDong TEXT NOT NULL,
    TenHopDong TEXT NOT NULL,
    ChieuHopDong TEXT NOT NULL CHECK(ChieuHopDong IN ('Đầu ra', 'Đầu vào')),
    MaLoaiHD TEXT REFERENCES LoaiHopDong(Ma),
    MaDoiTac TEXT REFERENCES DoiTac(Ma),
    TenDoiTac TEXT NOT NULL,
    MaThueDoiTac TEXT,
    GiaTriTruocThue INTEGER NOT NULL DEFAULT 0,
    VAT INTEGER NOT NULL DEFAULT 0,
    GiaTriTongSauThue INTEGER NOT NULL DEFAULT 0,
    NgayKy TEXT NOT NULL,
    NgayBatDauDuKien TEXT,
    NgayKetThucDuKien TEXT,
    ThoiGianThucHien INTEGER,             -- số ngày / tháng thực hiện, dùng tự tính ngày kết thúc
    DonViThoiGian TEXT,                   -- 'ngày' / 'tháng'
    NhacTruocNgay INTEGER,                -- nhắc trước khi hết hạn (để trống = theo cài đặt chung)
    TinhTrangHopDong TEXT NOT NULL DEFAULT 'Chưa bắt đầu'
        CHECK(TinhTrangHopDong IN ('Chưa bắt đầu', 'Đang thực hiện', 'Đã nghiệm thu',
                                   'Hoàn thành', 'Tạm dừng', 'Hủy')),
    GhiChu TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS CongViec (
    Ma TEXT PRIMARY KEY,
    MaHopDong TEXT NOT NULL REFERENCES HopDong(Ma),
    TenNoiDungCV TEXT NOT NULL,
    NgayDuKienHT TEXT,
    TienDoPT INTEGER NOT NULL DEFAULT 0 CHECK(TienDoPT BETWEEN 0 AND 100),
    TinhTrangCV TEXT NOT NULL DEFAULT 'Chưa làm'
        CHECK(TinhTrangCV IN ('Chưa làm', 'Đang làm', 'Đã bàn giao', 'Đã nghiệm thu', 'Hoàn thành')),
    GhiChuCV TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS LichSuBanGiao (
    Ma TEXT PRIMARY KEY,
    MaCongViec TEXT NOT NULL REFERENCES CongViec(Ma),
    NgayBanGiao TEXT NOT NULL,
    NoiDungTL TEXT NOT NULL,
    PhanHoiCDT TEXT,
    DuongDanTep TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS LichSuNghiemThu (
    Ma TEXT PRIMARY KEY,
    MaCongViec TEXT NOT NULL REFERENCES CongViec(Ma),
    NgayNghiemThu TEXT NOT NULL,
    KetQuaNT TEXT NOT NULL CHECK(KetQuaNT IN ('Đạt', 'Chưa đạt')),
    GhiChuNT TEXT,
    DuongDanTep TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS HoaDonDauRa (
    Ma TEXT PRIMARY KEY,
    KyHieuMauSo TEXT,
    KyHieuHD TEXT,
    SoHoaDon TEXT NOT NULL,
    MaHopDong TEXT NOT NULL REFERENCES HopDong(Ma),
    NgayXuatHD TEXT NOT NULL,
    TenNguoiMua TEXT,
    MSTNguoiMua TEXT,
    GiaTriTruocThue INTEGER NOT NULL DEFAULT 0,
    VAT INTEGER NOT NULL DEFAULT 0,
    GiaTriSauThue INTEGER NOT NULL DEFAULT 0,
    TinhChatHD TEXT NOT NULL DEFAULT 'Gốc'
        CHECK(TinhChatHD IN ('Gốc', 'Điều chỉnh', 'Thay thế', 'Đã hủy')),
    MatHang TEXT,
    ThueSuat TEXT,
    DuongDanTep TEXT,
    GhiChuHDDR TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS ThanhToanHopDong (
    Ma TEXT PRIMARY KEY,
    MaHopDong TEXT NOT NULL REFERENCES HopDong(Ma),
    MaHDDauRa TEXT REFERENCES HoaDonDauRa(Ma),
    NgayThanhToan TEXT NOT NULL,
    SoTien INTEGER NOT NULL CHECK(SoTien > 0),
    HinhThuc TEXT NOT NULL DEFAULT 'Chuyển khoản' CHECK(HinhThuc IN ('Chuyển khoản', 'Tiền mặt')),
    SoThamChieuSK TEXT,
    GhiChu TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS HoaDonDauVao (
    Ma TEXT PRIMARY KEY,
    KyHieuMauSo TEXT,
    KyHieuHD TEXT,
    SoHoaDonGoc TEXT NOT NULL,
    TenNhaCungCap TEXT NOT NULL,
    MaThueNCC TEXT NOT NULL,
    NgayNhanHD TEXT NOT NULL,
    GiaTriTruocThue INTEGER NOT NULL DEFAULT 0,
    VAT INTEGER NOT NULL DEFAULT 0,
    GiaTriSauThue INTEGER NOT NULL DEFAULT 0,
    MaHopDong TEXT REFERENCES HopDong(Ma),
    MatHang TEXT,
    ThueSuat TEXT,                        -- '10%', '8%', '0%', 'KCT', …
    NhomThue TEXT,                        -- 'Chịu thuế' / 'Không chịu thuế'
    NguoiMuaHang TEXT,                    -- nhân viên mua hàng
    SoThamChieuSK TEXT,                   -- số tham chiếu trên sao kê ngân hàng
    DuongDanTep TEXT,
    GhiChuHDDV TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS DongHoaDonDauVao (   -- từng dòng hàng hóa, dịch vụ của hóa đơn đầu vào
    Ma TEXT PRIMARY KEY,
    MaHoaDon TEXT NOT NULL,
    STT INTEGER NOT NULL DEFAULT 1,
    TenHang TEXT NOT NULL,
    DVT TEXT,
    SoLuong REAL,
    DonGia INTEGER,
    ThanhTien INTEGER NOT NULL DEFAULT 0, -- chưa thuế
    ThueSuat TEXT,
    PhanLoai TEXT NOT NULL DEFAULT 'Chưa phân loại'
        CHECK(PhanLoai IN ('Chưa phân loại', 'Chi phí trong kỳ', 'TSCĐ', 'CCDC', 'Chờ phân bổ', 'Không tính chi phí')),
    MaNhomDM TEXT,                        -- mã nhóm trong danh mục cài sẵn (asset_catalog)
    TuDong INTEGER NOT NULL DEFAULT 1,    -- 1 = phần mềm tự phân loại, 0 = kế toán đã chọn
    SoThang INTEGER,                      -- thời gian khấu hao / phân bổ
    GhiChu TEXT,{SYNC_COLS}
);
CREATE TABLE IF NOT EXISTS TaiSan (               -- sổ TSCĐ và sổ theo dõi CCDC (gồm chi phí chờ phân bổ)
    Ma TEXT PRIMARY KEY,
    LoaiSo TEXT NOT NULL CHECK(LoaiSo IN ('TSCĐ', 'CCDC')),
    PhanLoai TEXT NOT NULL DEFAULT 'CCDC' CHECK(PhanLoai IN ('TSCĐ', 'CCDC', 'Chờ phân bổ')),
    MaTS TEXT NOT NULL,
    TenTS TEXT NOT NULL,
    LoaiTS TEXT,
    MaNhomDM TEXT,
    DonViSuDung TEXT,
    NgayGhiTang TEXT NOT NULL,
    SoCTGhiTang TEXT,
    NgayBatDauKH TEXT,
    SoKy INTEGER NOT NULL CHECK(SoKy > 0),
    DVT TEXT,
    SoLuong REAL NOT NULL DEFAULT 1,
    NguyenGia INTEGER NOT NULL DEFAULT 0,
    GiaTriTinhKH INTEGER NOT NULL DEFAULT 0,
    MucKy INTEGER,                        -- giá trị khấu hao / phân bổ mỗi kỳ (trống = tự tính)
    TKNguyenGia TEXT,
    TKKhauHao TEXT,                       -- TSCĐ: 2141…; CCDC: TK chờ phân bổ 242
    NgayGhiGiam TEXT,
    LyDoGhiGiam TEXT,
    MocNam INTEGER,                       -- số liệu đã chốt theo sổ cũ đến hết năm này (nhập từ Excel)
    MocTrongKy INTEGER,
    MocLuyKe INTEGER,
    MocKyConLai INTEGER,
    MaDongHD TEXT,                        -- dòng hóa đơn đầu vào sinh ra tài sản
    ThuTu INTEGER NOT NULL DEFAULT 0,
    GhiChu TEXT,{SYNC_COLS}
);
CREATE INDEX IF NOT EXISTS ix_dong_hoa_don ON DongHoaDonDauVao(MaHoaDon);
CREATE INDEX IF NOT EXISTS ix_tai_san_dong ON TaiSan(MaDongHD);

-- Bảng cục bộ của từng máy (không đồng bộ)
CREATE TABLE IF NOT EXISTS ThongTinMay (
    MaMay TEXT PRIMARY KEY,
    TenMay TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS BanQuyen (
    Id INTEGER PRIMARY KEY CHECK(Id = 1),
    LoaiKichHoat TEXT NOT NULL,
    MaCongTy TEXT NOT NULL,
    TenCongTy TEXT NOT NULL,
    KeyGiamDoc TEXT NOT NULL,
    MaKichHoatMay TEXT,
    KhoaBiMatCongTy TEXT,
    SoTaiKhoanToiDa INTEGER NOT NULL,
    NgayKichHoat TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS NguoiDung (
    MaNguoiDung INTEGER PRIMARY KEY AUTOINCREMENT,
    TenDangNhap TEXT NOT NULL UNIQUE,
    HoTen TEXT NOT NULL,
    MatKhauBam TEXT,
    VaiTro TEXT NOT NULL CHECK(VaiTro IN ('Giám đốc', 'Người quản lý', 'Kế toán viên')),
    TrangThai TEXT NOT NULL DEFAULT 'Hoạt động'
        CHECK(TrangThai IN ('Chờ kích hoạt', 'Hoạt động', 'Đã khóa', 'Đã thu hồi')),
    BatBuocDoiMK INTEGER NOT NULL DEFAULT 0,
    SoLanSaiMK INTEGER NOT NULL DEFAULT 0,
    KhoaDen TEXT,
    MaKichHoat TEXT,
    NgayTao TEXT NOT NULL,
    LanDangNhapCuoi TEXT
);
CREATE TABLE IF NOT EXISTS PhanQuyen (
    MaNguoiDung INTEGER NOT NULL REFERENCES NguoiDung(MaNguoiDung) ON DELETE CASCADE,
    ChucNang TEXT NOT NULL,
    Xem INTEGER NOT NULL DEFAULT 0,
    Them INTEGER NOT NULL DEFAULT 0,
    Sua INTEGER NOT NULL DEFAULT 0,
    Xoa INTEGER NOT NULL DEFAULT 0,
    Xuat INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (MaNguoiDung, ChucNang)
);
CREATE TABLE IF NOT EXISTS MaDaCap (
    MaKichHoat TEXT PRIMARY KEY,
    TenDangNhap TEXT NOT NULL,
    HoTen TEXT NOT NULL,
    MaMay TEXT NOT NULL,
    VaiTro TEXT NOT NULL,
    NgayCap TEXT NOT NULL,
    HetHan TEXT,
    DaThuHoi INTEGER NOT NULL DEFAULT 0,
    NgayThuHoi TEXT
);
CREATE TABLE IF NOT EXISTS DanhSachThuHoi (
    MaKichHoat TEXT PRIMARY KEY,
    NgayThuHoi TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS NhatKyHeThong (
    MaNhatKy INTEGER PRIMARY KEY AUTOINCREMENT,
    ThoiGian TEXT NOT NULL,
    TenDangNhap TEXT,
    HanhDong TEXT NOT NULL,
    DoiTuong TEXT
);
CREATE TABLE IF NOT EXISTS KhoaThuChi (
    TenDangNhap TEXT PRIMARY KEY,         -- '__khoiphuc__' = bản bọc bằng Mã khôi phục
    ChiaKhoaDaBoc BLOB NOT NULL,
    Muoi BLOB NOT NULL,
    NgayDat TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS LichSuDongBo (
    MaLan INTEGER PRIMARY KEY AUTOINCREMENT,
    MaMayDoiTac TEXT NOT NULL,
    TenMayDoiTac TEXT NOT NULL,
    Chieu TEXT NOT NULL CHECK(Chieu IN ('Gửi', 'Nhận')),
    ThoiDiem TEXT NOT NULL,
    MocDuLieu TEXT NOT NULL,
    PhamVi TEXT,
    KetQua TEXT,
    TenDangNhap TEXT
);
CREATE TABLE IF NOT EXISTS AnhXaMa (       -- bản ghi máy khác đã được gộp vào bản ghi máy này
    Bang TEXT NOT NULL,
    MaNgoai TEXT NOT NULL,
    MaNoiBo TEXT NOT NULL,
    PRIMARY KEY (Bang, MaNgoai)
);
CREATE TABLE IF NOT EXISTS NhanDongBo (    -- lúc máy này nhận bản ghi từ máy khác, để gửi tiếp khi đồng bộ "thay đổi"
    Bang TEXT NOT NULL,
    Ma TEXT NOT NULL,
    ThoiDiem TEXT NOT NULL,
    PRIMARY KEY (Bang, Ma)
);
CREATE TABLE IF NOT EXISTS CaiDat (
    Khoa TEXT PRIMARY KEY,
    GiaTri TEXT
);
"""


# Cột thêm ở các phiên bản sau: tệp dữ liệu cũ được bổ sung khi mở (bảng, cột, kiểu).
ADDED_COLUMNS = [
    ("HopDong", "ThoiGianThucHien", "INTEGER"),
    ("HopDong", "DonViThoiGian", "TEXT"),
    ("HopDong", "NhacTruocNgay", "INTEGER"),
    ("HopDong", "ThueSuat", "TEXT"),
    ("HopDong", "HoSoDaLay", "TEXT"),                  # hồ sơ, tài liệu đã lấy về từ đối tác
    ("HoaDonDauRa", "MatHang", "TEXT"),
    ("HoaDonDauRa", "ThueSuat", "TEXT"),
    ("HoaDonDauVao", "MatHang", "TEXT"),
    ("HoaDonDauVao", "ThueSuat", "TEXT"),
    ("HoaDonDauVao", "NhomThue", "TEXT"),
    ("HoaDonDauVao", "NguoiMuaHang", "TEXT"),
    ("HoaDonDauVao", "SoThamChieuSK", "TEXT"),
    ("HoaDonDauVao", "PhiKhac", "INTEGER"),            # phí ngoài VAT, VD phí bảo vệ môi trường (nước sạch)
    ("HoaDonDauVao", "TyLePhiKhac", "TEXT"),           # tỷ lệ phí trên giá trị trước thuế, VD '10%'
    ("HoaDonDauVao", "TenPhiKhac", "TEXT"),
    ("TaiSan", "XoaKhoiSoNam", "INTEGER"),             # đã hết khấu hao, xóa khỏi sổ từ năm này (năm trước vẫn hiện)
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id() -> str:
    return str(uuid.uuid4())


class Store:
    """Thao tác chung trên một kết nối SQLite có các bảng đồng bộ."""

    synced_tables: list[str] = []

    def __init__(self, conn: sqlite3.Connection, machine_id: str):
        self.conn = conn
        self.machine_id = machine_id
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    # ---- truy vấn chung
    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params=()) -> None:
        self.conn.execute(sql, params)
        self.conn.commit()

    def columns(self, table: str) -> list[str]:
        return [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]

    # ---- bảng đồng bộ
    def insert(self, table: str, values: dict, *, commit: bool = True) -> str:
        assert table in self.synced_tables, table
        row = dict(values)
        row.setdefault("Ma", new_id())
        ts = now_iso()
        row.setdefault("NgayTao", ts)
        row.setdefault("NgaySua", ts)
        row.setdefault("MaMaySua", self.machine_id)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        if commit:
            self.conn.commit()
        return row["Ma"]

    def update(self, table: str, ma: str, values: dict, *, commit: bool = True) -> None:
        assert table in self.synced_tables, table
        row = {k: v for k, v in values.items() if k not in SYNC_FIELDS and k != "Ma"}
        row["NgaySua"] = now_iso()
        row["MaMaySua"] = self.machine_id
        sets = ", ".join(f"{c} = ?" for c in row)
        self.conn.execute(
            f"UPDATE {table} SET {sets}, PhienBan = PhienBan + 1 WHERE Ma = ?",
            [*row.values(), ma],
        )
        if commit:
            self.conn.commit()

    def upsert_raw(self, table: str, row: dict, *, commit: bool = False) -> None:
        """Ghi nguyên bản ghi nhận từ máy khác (giữ nguyên NgaySua, MaMaySua, PhienBan)."""
        assert table in self.synced_tables, table
        cols = [c for c in self.columns(table) if c in row]
        self.conn.execute(
            f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols])
        if commit:
            self.conn.commit()

    def soft_delete(self, table: str, ma: str, *, commit: bool = True) -> None:
        ts = now_iso()
        self.conn.execute(
            f"UPDATE {table} SET DaXoa = 1, NgayXoa = ?, NgaySua = ?, MaMaySua = ?, "
            f"PhienBan = PhienBan + 1 WHERE Ma = ?",
            (ts, ts, self.machine_id, ma),
        )
        if commit:
            self.conn.commit()

    def get(self, table: str, ma: str):
        return self.one(f"SELECT * FROM {table} WHERE Ma = ?", (ma,))

    def active(self, table: str, where: str = "", params=(), order: str = "") -> list[sqlite3.Row]:
        sql = f"SELECT * FROM {table} WHERE DaXoa = 0"
        if where:
            sql += f" AND ({where})"
        if order:
            sql += f" ORDER BY {order}"
        return self.query(sql, params)


class Database(Store):
    """Tệp dữ liệu chính trên máy (QuanLyHopDong.db)."""

    synced_tables = SYNCED_TABLES

    def __init__(self, path: str | Path, machine_id: str = "local"):
        self.path = str(path)
        conn = sqlite3.connect(self.path)
        super().__init__(conn, machine_id)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._upgrade()
        self.conn.commit()

    def _upgrade(self) -> None:
        self.conn.executescript(SCHEMA)       # bảng mới của bản sau (kể cả khi khôi phục bản sao lưu cũ)
        for table, col, kind in ADDED_COLUMNS:
            if col not in self.columns(table):
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")

    def backup_to(self, dest: str | Path) -> None:
        target = sqlite3.connect(str(dest))
        with target:
            self.conn.backup(target)
        target.close()

    def restore_from(self, src: str | Path) -> None:
        source = sqlite3.connect(str(src))
        source.backup(self.conn)
        source.close()
        self._upgrade()
        self.conn.commit()

    # ---- cài đặt & nhật ký
    def setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT GiaTri FROM CaiDat WHERE Khoa = ?", (key,))
        return r["GiaTri"] if r else default

    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self.execute("DELETE FROM CaiDat WHERE Khoa = ?", (key,))
        else:
            self.execute("INSERT OR REPLACE INTO CaiDat (Khoa, GiaTri) VALUES (?, ?)", (key, value))

    def log(self, user: str | None, action: str, target: str | None = None) -> None:
        self.execute(
            "INSERT INTO NhatKyHeThong (ThoiGian, TenDangNhap, HanhDong, DoiTuong) VALUES (?, ?, ?, ?)",
            (now_iso(), user, action, target),
        )
