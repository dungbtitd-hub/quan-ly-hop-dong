"""Ngữ cảnh chạy phần mềm trên một máy: thư mục dữ liệu, CSDL, tài khoản đăng nhập, kho thu chi."""
from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime
from pathlib import Path

from .core.auth import AccountService, AuthError, verify_password
from .core.contracts import ContractService
from .core.db import Database
from .core.machine import machine_id
from .core.sync import SyncService

KEEP_BACKUPS = 30
# Giữ lại khi xóa sạch dữ liệu nhưng vẫn dùng tiếp bản quyền, tài khoản trên máy này.
LICENSE_TABLES = ("ThongTinMay", "BanQuyen", "NguoiDung", "PhanQuyen", "MaDaCap", "DanhSachThuHoi", "KhoaThuChi")
LICENSE_SETTINGS = ("khoa_cong_ty_tam", "ngay_lon_nhat", "giao_dien")
KEEP_FILES = ("machine.seed",)     # tệp sinh Mã máy dự phòng: giữ để Mã máy không đổi


def shred(path: Path) -> None:
    """Ghi đè nội dung tệp bằng dữ liệu ngẫu nhiên rồi xóa, để không khôi phục được bằng công cụ cứu tệp."""
    try:
        size = path.stat().st_size
        with open(path, "r+b", buffering=0) as f:
            left = size
            while left > 0:
                n = min(left, 1 << 20)
                f.write(os.urandom(n))
                left -= n
            os.fsync(f.fileno())
        hidden = path.with_name(os.urandom(8).hex())
        path.rename(hidden)
        hidden.unlink()
    except OSError:
        path.unlink(missing_ok=True)


def default_data_dir() -> Path:
    env = os.environ.get("QLHD_DATA_DIR")
    if env:
        p = Path(env)
    elif platform.system() == "Windows":
        p = Path(os.environ.get("APPDATA", Path.home())) / "QuanLyHopDong"
    elif platform.system() == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "QuanLyHopDong"
    else:
        p = Path.home() / ".local" / "share" / "QuanLyHopDong"
    p.mkdir(parents=True, exist_ok=True)
    return p


class AppContext:
    def __init__(self, data_dir: Path | None = None, vendor_pub=None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.machine_id = machine_id(self.data_dir)
        self.db = Database(self.data_dir / "QuanLyHopDong.db", self.machine_id)
        self.acc = AccountService(self.db, self.machine_id, vendor_pub)
        self.cs = ContractService(self.db)
        self.sync = SyncService(self.db, self.data_dir, self.acc)
        self.finance_path = self.data_dir / "ThuChi.dat"
        self.user = None
        self.finance = None
        self.wiped = False

    @property
    def company(self) -> str:
        row = self.acc.license()
        return row["TenCongTy"] if row else ""

    def refresh_user(self) -> None:
        if self.user is not None:
            self.user = self.db.one("SELECT * FROM NguoiDung WHERE MaNguoiDung = ?", (self.user["MaNguoiDung"],))

    def can(self, feature: str, action: str = "Xem") -> bool:
        return self.user is not None and self.acc.can(self.user, feature, action)

    def lock_finance(self) -> None:
        if self.finance is not None:
            try:
                self.finance.lock()
            finally:
                self.finance = None

    # ---- sao lưu
    def backup_dir(self) -> Path:
        d = self.data_dir / "SaoLuu"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def backup_now(self, label: str = "tu-dong") -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir() / f"QuanLyHopDong-{label}-{stamp}.db"
        self.db.backup_to(target)
        if self.finance_path.exists():
            shutil.copy2(self.finance_path, self.backup_dir() / f"ThuChi-{label}-{stamp}.dat")
        self._prune()
        return target

    def _prune(self) -> None:
        for pattern in ("QuanLyHopDong-tu-dong-*.db", "ThuChi-tu-dong-*.dat"):
            files = sorted(self.backup_dir().glob(pattern))
            for f in files[:-KEEP_BACKUPS]:
                f.unlink(missing_ok=True)

    # ---- xóa sạch dữ liệu
    def wipe_all(self, password: str, *, keep_license: bool) -> int:
        """Xóa sạch toàn bộ dữ liệu trên máy (hợp đồng, hóa đơn, thu chi, sao lưu, nhật ký, lịch sử đồng bộ).
        keep_license: giữ bản quyền và tài khoản để dùng tiếp; không giữ thì máy về trạng thái chưa kích hoạt.
        Trả về số tệp đã hủy. Sau khi gọi, phần mềm phải đóng lại."""
        u = self.user
        if u is None or u["VaiTro"] != "Giám đốc":
            raise AuthError("Chỉ Giám đốc mới xóa được toàn bộ dữ liệu.")
        if not verify_password(password, u["MatKhauBam"]):
            raise AuthError("Mật khẩu không đúng.")
        kept: dict[str, list[dict]] = {}
        if keep_license:
            for t in LICENSE_TABLES:
                kept[t] = [dict(r) for r in self.db.query(f"SELECT * FROM {t}")]
            marks = ",".join("?" * len(LICENSE_SETTINGS))
            kept["CaiDat"] = [dict(r) for r in self.db.query(f"SELECT * FROM CaiDat WHERE Khoa IN ({marks})",
                                                             LICENSE_SETTINGS)]
        self.lock_finance()
        self.db.close()
        files = [f for f in sorted(self.data_dir.rglob("*"), reverse=True)
                 if f.is_file() and f.relative_to(self.data_dir).as_posix() not in KEEP_FILES]
        for f in files:
            shred(f)
        for d in sorted((d for d in self.data_dir.rglob("*") if d.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        self.wiped = True
        self.db = Database(self.data_dir / "QuanLyHopDong.db", self.machine_id)
        for table, rows in kept.items():
            for r in rows:
                self.db.conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(r)}) VALUES "
                                     f"({', '.join('?' * len(r))})", list(r.values()))
        self.db.conn.commit()
        self.acc = AccountService(self.db, self.machine_id, self.acc.vendor_pub)
        self.cs = ContractService(self.db)
        self.sync = SyncService(self.db, self.data_dir, self.acc)
        self.user = None
        return len(files)

    def close(self) -> None:
        self.lock_finance()
        try:
            if self.acc.is_activated() and not self.wiped:
                self.backup_now()
        finally:
            self.db.close()
