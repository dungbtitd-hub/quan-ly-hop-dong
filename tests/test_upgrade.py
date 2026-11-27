"""Cài bản mới đè bản cũ: dữ liệu tạo bằng phiên bản 1.0.2 mở được và giữ nguyên."""
import shutil
from pathlib import Path

from qlhd.core.contracts import ContractService
from qlhd.core.db import ADDED_COLUMNS, Database
from qlhd.core.reports import CONTEXTS, ContractReports, ReportFilters, export_xlsx

OLD_DB = Path(__file__).parent / "data" / "du_lieu_v1.0.2.db"


def test_open_v102_data(tmp_path):
    path = tmp_path / "QuanLyHopDong.db"
    shutil.copy(OLD_DB, path)
    db = Database(path, "QLHD-CU")
    for table, col, _ in ADDED_COLUMNS:
        assert col in db.columns(table)
    counts = {t: db.one(f"SELECT COUNT(*) n FROM {t}")["n"] for t in ("HopDong", "HoaDonDauRa", "HoaDonDauVao",
                                                                       "ThanhToanHopDong", "DoiTac")}
    assert counts == {"HopDong": 2, "HoaDonDauRa": 1, "HoaDonDauVao": 2, "ThanhToanHopDong": 2, "DoiTac": 2}
    cs = ContractService(db)
    hd = db.one("SELECT * FROM HopDong WHERE SoHopDong = '01/2026/HĐTV'")
    assert hd["TenHopDong"] == "Thiết kế trường học" and hd["GiaTriTongSauThue"] == 110_000_000
    cs.save_contract(dict(hd, ThoiGianThucHien=3, DonViThoiGian="tháng"), hd["Ma"])
    assert db.get("HopDong", hd["Ma"])["NgayKetThucDuKien"] == "2026-04-14"
    cr = ContractReports(db)
    for key, _ in CONTEXTS:
        export_xlsx(cr.build(key, ReportFilters()), tmp_path / f"{key}.xlsx")
    db.close()
    again = Database(path, "QLHD-CU")      # mở lần hai: không nâng cấp lại, không lỗi
    assert again.one("SELECT COUNT(*) n FROM HopDong")["n"] == 2
