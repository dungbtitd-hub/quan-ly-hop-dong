"""Ngữ cảnh chạy phần mềm trên một máy: thư mục dữ liệu, CSDL, tài khoản đăng nhập, kho thu chi."""
from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime
from pathlib import Path

from .core.auth import AccountService
from .core.contracts import ContractService
from .core.db import Database
from .core.machine import machine_id
from .core.sync import SyncService

KEEP_BACKUPS = 30


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

    def close(self) -> None:
        self.lock_finance()
        try:
            if self.acc.is_activated():
                self.backup_now()
        finally:
            self.db.close()
