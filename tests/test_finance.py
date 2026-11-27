import pytest

from qlhd.core.finance import FinanceError, FinanceStore
from qlhd.core.reports import FinanceReports, export_xlsx
from tests.helpers import sample_data

KEY = "khoa-thu-chi-1"


def _open(m):
    store, code = FinanceStore.initialize(m.db, m.finance_path, m.user, KEY, iterations=1000)
    return store, code


def test_accountant_blocked(company):
    kt = company["kt"]
    with pytest.raises(FinanceError):
        FinanceStore.initialize(kt.db, kt.finance_path, kt.user, KEY)


def test_file_is_encrypted_and_lockout(company):
    gd = company["gd"]
    store, code = _open(gd)
    cat = next(c for c, _ in store.category_tree("Chi") if c["TenKhoanMuc"] == "Lương")
    store.add_voucher({"NgayPhatSinh": "2026-03-31", "LoaiThuChi": "Chi", "PhanLoaiChi": "Văn phòng",
                       "MaKhoanMuc": cat["Ma"], "MaQuy": store.default_fund("Chuyển khoản"),
                       "SoTien": 15_000_000, "NoiDung": "Lương tháng 3 bí mật"})
    store.lock()
    raw = gd.finance_path.read_bytes()
    assert "bí mật".encode() not in raw and b"SQLite format" not in raw
    for _ in range(5):
        with pytest.raises(FinanceError):
            FinanceStore.unlock(gd.db, gd.finance_path, gd.user, "sai-khoa-123")
    with pytest.raises(FinanceError, match="tạm khóa"):
        FinanceStore.unlock(gd.db, gd.finance_path, gd.user, KEY)
    gd.db.set_setting("tc_sai:giamdoc", None)
    store = FinanceStore.unlock(gd.db, gd.finance_path, gd.user, KEY)
    assert store.vouchers()[0]["NoiDung"] == "Lương tháng 3 bí mật"
    store.lock()
    store = FinanceStore.unlock_with_recovery(gd.db, gd.finance_path, gd.user, code.lower(), "khoa-moi-123",
                                              iterations=1000)
    assert len(store.vouchers()) == 1
    store.lock()
    FinanceStore.unlock(gd.db, gd.finance_path, gd.user, "khoa-moi-123").lock()


def test_receipts_balance_reports_delete(company, tmp_path):
    gd = company["gd"]
    sample_data(gd.db)
    store, _ = _open(gd)
    projects = store.projects()
    vt = next(c for c, _ in store.category_tree("Chi") if c["TenKhoanMuc"] == "Vật tư, thiết bị")
    luong = next(c for c, _ in store.category_tree("Chi") if c["TenKhoanMuc"] == "Lương")
    lai = next(c for c, _ in store.category_tree("Thu") if c["TenKhoanMuc"] == "Lãi tiền gửi")
    bank = store.default_fund("Chuyển khoản")
    store.add_voucher({"NgayPhatSinh": "2026-03-15", "LoaiThuChi": "Chi", "PhanLoaiChi": "Dự án",
                       "MaDuAn": projects[0]["Ma"], "TenDuAn": projects[0]["TenHopDong"], "MaKhoanMuc": vt["Ma"],
                       "MaQuy": bank, "SoTien": 8_000_000, "NoiDung": "Mua vật tư"})
    store.add_voucher({"NgayPhatSinh": "2026-03-31", "LoaiThuChi": "Chi", "PhanLoaiChi": "Văn phòng",
                       "MaKhoanMuc": luong["Ma"], "MaQuy": bank, "SoTien": 10_000_000, "NoiDung": "Lương"})
    store.add_voucher({"NgayPhatSinh": "2026-03-31", "LoaiThuChi": "Thu", "MaKhoanMuc": lai["Ma"], "MaQuy": bank,
                       "SoTien": 100_000, "NoiDung": "Lãi"})
    t = store.totals("2026-03-01", "2026-03-31")
    assert t["thu_hop_dong"] == 30_000_000 and t["thu_khac"] == 100_000
    assert t["chi_du_an"] == 8_000_000 and t["chi_van_phong"] == 10_000_000
    assert store.balance("2026-12-31") == 35_000_000 + 100_000 - 18_000_000
    cash = store.default_fund("Tiền mặt")
    assert store.balance("2026-12-31", cash) == 5_000_000
    assert store.vouchers()[0]["SoPhieu"] == "PC-2026-0001"
    fr = FinanceReports(store, gd.db)
    for kind in ("ngay", "thang", "quy", "nam", "chi", "du_an"):
        reps = fr.package(kind, "2026-01-01", "2026-12-31")
        export_xlsx(reps, tmp_path / f"tc_{kind}.xlsx", company="Cty", user="GĐ")
    q = fr.summary("2026-01-01", "2026-03-31", "quy")
    assert q.rows[0]["Tong"] == 30_100_000
    proj = fr.by_project("2026-01-01", "2026-12-31")
    assert proj.rows[0]["ChiDuAn"] == 8_000_000 and proj.rows[0]["DaThu"] == 35_000_000
    enc = export_xlsx(reps, tmp_path / "bao_mat.xlsx", password="mo-file-123")
    assert enc.read_bytes()[:4] == bytes.fromhex("D0CF11E0")
    assert store.range_preview("2026-03-31", "2026-03-31")["so_phieu"] == 2
    with pytest.raises(FinanceError):
        store.delete_range("2026-03-31", "2026-03-31", "sai")
    assert store.delete_range("2026-03-31", "2026-03-31", KEY) == 2
    with pytest.raises(FinanceError):
        store.delete_all(gd.user, KEY, "xoa")
    store.delete_all(gd.user, KEY, "XOA TOAN BO")
    assert store.vouchers() == []
    assert store.contract_receipts()  # thu hợp đồng nằm ở phần hợp đồng, không bị xóa


def test_category_tree_ops(company):
    gd = company["gd"]
    store, _ = _open(gd)
    root = store.add_category("Marketing", "Chi", None, "Văn phòng")
    child = store.add_category("Quảng cáo", "Chi", root, "Văn phòng")
    assert store.category_path(child) == "Marketing › Quảng cáo"
    with pytest.raises(FinanceError):
        store.move_category(root, child)
    store.add_voucher({"NgayPhatSinh": "2026-05-01", "LoaiThuChi": "Chi", "PhanLoaiChi": "Văn phòng",
                       "MaKhoanMuc": child, "MaQuy": store.default_fund("Tiền mặt"), "SoTien": 1000, "NoiDung": "x"})
    with pytest.raises(FinanceError):
        store.delete_category(child)
    other = store.add_category("Facebook", "Chi", root, "Văn phòng")
    assert store.merge_categories(child, other) == 1
    assert store.vouchers()[0]["MaKhoanMuc"] == other


def test_manager_gets_own_key(company, tmp_path):
    gd = company["gd"]
    store, _ = _open(gd)
    code = gd.acc.issue_code(ten_dang_nhap="quanly", ho_ten="QL", vai_tro="Người quản lý", machine_id=gd.id)
    gd.acc._create_user("quanly2", "QL2", "Người quản lý", "matkhau123", {}, None)
    store.set_user_key("quanly2", "khoa-quan-ly-1", iterations=1000)
    store.lock()
    ql = gd.db.one("SELECT * FROM NguoiDung WHERE TenDangNhap = 'quanly2'")
    assert FinanceStore.unlock(gd.db, gd.finance_path, ql, "khoa-quan-ly-1").funds()
    assert code
