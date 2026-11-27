"""Cơ sở dữ liệu SQLite chính (hợp đồng, hóa đơn, tài khoản).

Mọi bảng dữ liệu nghiệp vụ dùng mã định danh toàn cục (UUID) và các cột đồng bộ
(NgayTao, NgaySua, MaMaySua, PhienBan, DaXoa, NgayXoa) để đồng bộ nhiều máy.
Tiền lưu dạng số nguyên (đồng), ngày lưu dạng YYYY-MM-DD.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SYNC_COLS = """
    NgayTao TEXT NOT NULL,
    NgaySua TEXT NOT NULL,
    MaMaySua TEXT NOT NULL,
    PhienBan INTEGER NOT NULL DEFAULT 1,
    DaXoa INTEGER NOT NULL DEFAULT 0,
    NgayXoa TEXT"""

SYNC_FIELDS = ("NgayTao", "NgaySua", "MaMaySua", "PhienBan", "DaXoa", "NgayXoa", "SuaTay", "NgaySuaTruong")
# NgaySuaTruong: JSON {tên ô: [giờ sửa UTC, 1 = người sửa / 0 = phần mềm tự cập nhật]} – giờ sửa riêng từng ô, để khi
# đồng bộ hai máy cùng sửa một bản ghi thì gộp tự động theo từng ô: ô nào máy nào sửa sau cùng thì lấy của máy đó.
# SuaTay: giờ (UTC) người dùng sửa bản ghi lần gần nhất; trống = bản ghi chỉ do phần mềm tự tạo / tự cập nhật
# (quét thư mục, đọc XML / PDF, Gemini, tự tính lại). Khi đồng bộ, dữ liệu người dùng đã sửa thắng dữ liệu tự động.

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
CREATE TABLE IF NOT EXISTS TepDaQuet (      -- file hóa đơn trong thư mục đã xử lý (máy này): quét lại thì bỏ qua
    Chieu TEXT NOT NULL,                    -- 'ra' / 'vao'
    DuongDan TEXT NOT NULL,                 -- đường dẫn tương đối trong thư mục hóa đơn (dấu /)
    KichThuoc INTEGER,
    NgaySuaTep REAL,
    TrangThai TEXT NOT NULL,                -- da_them / co_san / can_xem / bo_qua / tep_kem
    MaHoaDon TEXT,
    GhiChu TEXT,
    DuLieu TEXT,                            -- JSON số liệu đọc được (dòng cần xem)
    ThoiDiem TEXT NOT NULL,
    PRIMARY KEY (Chieu, DuongDan)
);
"""


# Cột thêm ở các phiên bản sau: tệp dữ liệu cũ được bổ sung khi mở (bảng, cột, kiểu).
ADDED_COLUMNS = [
    ("DoiTac", "TenCu", "TEXT"),                       # các tên cũ (đối tác đổi tên, giữ MST), cách nhau "; "
    ("HopDong", "ThoiGianThucHien", "INTEGER"),
    ("HopDong", "DonViThoiGian", "TEXT"),
    ("HopDong", "NhacTruocNgay", "INTEGER"),
    ("HopDong", "ThueSuat", "TEXT"),
    ("HopDong", "HoSoDaLay", "TEXT"),                  # hồ sơ, tài liệu đã lấy về từ đối tác
    ("HoaDonDauRa", "MatHang", "TEXT"),
    ("HoaDonDauRa", "ThueSuat", "TEXT"),
    ("HoaDonDauRa", "DotThanhToan", "TEXT"),           # đợt: Toàn bộ / Lần 1, 2… / Lần cuối (trống = chưa ghi)
    ("HoaDonDauRa", "ChiTietThueSuat", "TEXT"),        # hóa đơn nhiều mức thuế: JSON [{ThueSuat, TruocThue, VAT}]
    ("HoaDonDauVao", "ChiTietThueSuat", "TEXT"),
    ("HoaDonDauVao", "MatHang", "TEXT"),
    ("HoaDonDauVao", "ThueSuat", "TEXT"),
    ("HoaDonDauVao", "NhomThue", "TEXT"),
    ("HoaDonDauVao", "NguoiMuaHang", "TEXT"),
    ("HoaDonDauVao", "SoThamChieuSK", "TEXT"),
    ("HoaDonDauVao", "PhiKhac", "INTEGER"),            # phí ngoài VAT, VD phí bảo vệ môi trường (nước sạch)
    ("HoaDonDauVao", "TyLePhiKhac", "TEXT"),           # tỷ lệ phí trên giá trị trước thuế, VD '10%'
    ("HoaDonDauVao", "TenPhiKhac", "TEXT"),
    ("TaiSan", "XoaKhoiSoNam", "INTEGER"),
    ("CaiDat", "NgaySua", "TEXT"),
    # tệp gốc trong thư mục hóa đơn (đường dẫn tương đối, mỗi máy tự ghép với thư mục của máy mình)
    ("HoaDonDauRa", "TepTuongDoi", "TEXT"),
    ("HoaDonDauRa", "TepKem", "TEXT"),                 # file PDF / ảnh đi kèm file XML cùng hóa đơn (tương đối)
    ("HoaDonDauVao", "TepTuongDoi", "TEXT"),
    ("HoaDonDauVao", "TepKem", "TEXT"),
    ("HoaDonDauRa", "NguonSoLieu", "TEXT"),            # XML / PDF / Gemini (đọc ảnh) / trống = nhập tay
    ("HoaDonDauVao", "NguonSoLieu", "TEXT"),
    ("HoaDonDauRa", "GeminiTT", "TEXT"),               # OK / Có vấn đề / Đã xem (trống = chưa kiểm)
    ("HoaDonDauVao", "GeminiTT", "TEXT"),
    ("HoaDonDauRa", "GeminiKQ", "TEXT"),               # JSON kết quả kiểm tra
    ("HoaDonDauVao", "GeminiKQ", "TEXT"),
    ("HoaDonDauRa", "GeminiLuc", "TEXT"),
    ("HoaDonDauVao", "GeminiLuc", "TEXT"),
    ("DongHoaDonDauVao", "GeminiTT", "TEXT"),          # dong_y / da_doi / khong_chac / khac / (trống = chưa rà soát)
    ("DongHoaDonDauVao", "GeminiGoiY", "TEXT"),        # JSON {ma, phan_loai, so_thang, ly_do, do_chac}
    ("DongHoaDonDauVao", "PLTruocGemini", "TEXT"),     # JSON phân loại trước khi Gemini đổi (để trả về)
] + [(t, "SuaTay", "TEXT") for t in SYNCED_TABLES] + [(t, "NgaySuaTruong", "TEXT") for t in SYNCED_TABLES]


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
        self._auto = 0
        self._has_manual: dict[str, bool] = {}
        self._has_ft: dict[str, bool] = {}

    @contextmanager
    def auto(self):
        """Ghi trong khối này là phần mềm tự cập nhật (không đánh dấu người dùng đã sửa)."""
        self._auto += 1
        try:
            yield self
        finally:
            self._auto -= 1

    @property
    def is_auto(self) -> bool:
        return self._auto > 0

    def _manual_col(self, table: str) -> bool:
        if table not in self._has_manual:
            self._has_manual[table] = "SuaTay" in self.columns(table)
        return self._has_manual[table]

    def _ft_col(self, table: str) -> bool:
        if table not in self._has_ft:
            self._has_ft[table] = "NgaySuaTruong" in self.columns(table)
        return self._has_ft[table]

    def _stamp_fields(self, table: str, ma: str | None, row: dict, ts: str) -> None:
        """Ghi giờ sửa cho các ô thực sự đổi giá trị (ô giữ nguyên thì giữ giờ sửa cũ)."""
        if not self._ft_col(table):
            return
        cur = self.get(table, ma) if ma else None
        try:
            ft = json.loads((cur["NgaySuaTruong"] if cur is not None else None) or "{}")
        except ValueError:
            ft = {}
        flag = 0 if self._auto else 1
        for k, v in row.items():
            if k in SYNC_FIELDS or k == "Ma":
                continue
            if cur is None:
                if v not in (None, ""):
                    ft[k] = [ts, flag]
            elif k in cur.keys() and cur[k] != v:
                ft[k] = [ts, flag]
        row["NgaySuaTruong"] = json.dumps(ft, ensure_ascii=False, sort_keys=True)

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
        if self._manual_col(table) and not self._auto:
            row.setdefault("SuaTay", ts)
        if "NgaySuaTruong" not in row:
            self._stamp_fields(table, None, row, ts)
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
        if self._manual_col(table) and not self._auto:
            row["SuaTay"] = row["NgaySua"]
        self._stamp_fields(table, ma, row, row["NgaySua"])
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

    def merge_fields(self, table: str, ma: str, values: dict, times: dict, *, commit: bool = False) -> None:
        """Đồng bộ: ghi các ô lấy từ máy khác, giữ nguyên giờ sửa từng ô của máy đó (không coi là máy này sửa)."""
        if not values:
            return
        cur = self.get(table, ma)
        row = dict(values)
        row["NgaySua"] = max(now_iso(), cur["NgaySua"] or "")
        row["MaMaySua"] = self.machine_id
        if self._ft_col(table):
            try:
                ft = json.loads(cur["NgaySuaTruong"] or "{}")
            except ValueError:
                ft = {}
            ft.update({k: t for k, t in times.items() if k in values and t})
            row["NgaySuaTruong"] = json.dumps(ft, ensure_ascii=False, sort_keys=True)
        sets = ", ".join(f"{c} = ?" for c in row)
        self.conn.execute(f"UPDATE {table} SET {sets}, PhienBan = PhienBan + 1 WHERE Ma = ?", [*row.values(), ma])
        if commit:
            self.conn.commit()

    def soft_delete(self, table: str, ma: str, *, commit: bool = True) -> None:
        ts = now_iso()
        ft_sql, params = "", [ts, ts, self.machine_id]
        if self._ft_col(table):
            cur = self.get(table, ma)
            try:
                ft = json.loads((cur["NgaySuaTruong"] if cur is not None else None) or "{}")
            except ValueError:
                ft = {}
            ft["DaXoa"] = [ts, 0 if self._auto else 1]
            ft_sql, params = ", NgaySuaTruong = ?", params + [json.dumps(ft, ensure_ascii=False, sort_keys=True)]
        self.conn.execute(
            f"UPDATE {table} SET DaXoa = 1, NgayXoa = ?, NgaySua = ?, MaMaySua = ?{ft_sql}, "
            f"PhienBan = PhienBan + 1 WHERE Ma = ?",
            (*params, ma),
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
        self._restores = 0
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
                if col == "SuaTay":
                    self._mark_old_manual(table)

    def _mark_old_manual(self, table: str) -> None:
        """Dữ liệu có từ trước khi phân biệt người sửa / tự động: coi là người dùng đã nhập, trừ dòng hóa đơn phần
        mềm tự phân loại và tài sản tự sinh từ hóa đơn (vẫn là tự động). Giờ sửa lấy theo NgaySua nên hai máy cùng
        nâng cấp cho cùng kết quả."""
        where = {"DongHoaDonDauVao": " AND TuDong = 0", "TaiSan": " AND MaDongHD IS NULL"}.get(table, "")
        self.conn.execute(f"UPDATE {table} SET SuaTay = NgaySua WHERE SuaTay IS NULL{where}")

    def backup_to(self, dest: str | Path) -> None:
        target = sqlite3.connect(str(dest))
        with target:
            self.conn.backup(target)
        target.close()

    def restore_from(self, src: str | Path) -> None:
        source = sqlite3.connect(str(src))
        source.backup(self.conn)
        source.close()
        self._restores += 1
        self._has_manual.clear()
        self._has_ft.clear()
        self._upgrade()
        self.conn.commit()

    def cache_key(self) -> tuple:
        """Đổi mỗi khi dữ liệu đổi: dùng làm khóa bộ nhớ đệm cho các phép tính nặng."""
        return (id(self.conn), self.path, self._restores, self.conn.total_changes)

    # ---- cài đặt & nhật ký
    def setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT GiaTri FROM CaiDat WHERE Khoa = ?", (key,))
        return r["GiaTri"] if r and r["GiaTri"] is not None else default

    def set_setting(self, key: str, value: str | None) -> None:
        """value None = xóa cài đặt: giữ dòng trống kèm giờ sửa để lần đồng bộ sau máy khác cũng xóa theo."""
        self.execute("INSERT OR REPLACE INTO CaiDat (Khoa, GiaTri, NgaySua) VALUES (?, ?, ?)", (key, value, now_iso()))

    def log(self, user: str | None, action: str, target: str | None = None) -> None:
        self.execute(
            "INSERT INTO NhatKyHeThong (ThoiGian, TenDangNhap, HanhDong, DoiTuong) VALUES (?, ?, ?, ?)",
            (now_iso(), user, action, target),
        )
