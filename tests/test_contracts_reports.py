import pytest

from qlhd.core.contracts import ValidationError
from qlhd.core.reports import CONTEXTS, ContractReports, ReportFilters, export_xlsx, period_range, split_periods
from tests.helpers import sample_data


def test_contract_summary(company):
    d = sample_data(company["kt"].db)
    s = d["cs"].summary(d["hd"])
    assert s["GiaTriHD"] == 110_000_000
    assert s["DaXuatHD"] == 55_000_000
    assert s["DaThanhToan"] == 35_000_000
    assert s["ConPhaiThu"] == 20_000_000
    assert s["ConPhaiXuat"] == 55_000_000
    assert s["TinhTrangThanhToan"] == "Thu một phần"
    assert s["TinhTrangXuatHD"] == "Xuất một phần"


def test_duplicates_rejected(company):
    d = sample_data(company["kt"].db)
    cs = d["cs"]
    with pytest.raises(ValidationError):
        cs.save_out_invoice({"KyHieuHD": "C26TAA", "SoHoaDon": "12", "MaHopDong": d["hd"], "NgayXuatHD": "2026-05-01"})
    with pytest.raises(ValidationError):
        cs.save_contract({"ThoiGianThucHien": 6, "SoHopDong": "01/2026/HĐTV", "TenHopDong": "x", "ChieuHopDong": "Đầu ra",
                          "MaDoiTac": d["cdt"], "NgayKy": "2026-01-01"})


def test_new_contract_requires_duration(company):
    d = sample_data(company["kt"].db)
    cs = d["cs"]
    v = {"SoHopDong": "07/2026", "TenHopDong": "Giám sát", "ChieuHopDong": "Đầu ra", "MaDoiTac": d["cdt"],
         "NgayKy": "2026-03-01", "GiaTriTruocThue": 10_000_000}
    with pytest.raises(ValidationError, match="Thời gian thực hiện"):
        cs.save_contract(dict(v))
    ma = cs.save_contract(dict(v, ThoiGianThucHien=45, DonViThoiGian="ngày"))
    assert cs.db.get("HopDong", ma)["NgayKetThucDuKien"] == "2026-04-14"
    cs.db.update("HopDong", ma, {"ThoiGianThucHien": None})          # hợp đồng cũ chưa có thời gian: sửa vẫn lưu được
    cs.save_contract(dict(cs.db.get("HopDong", ma), GhiChu="sửa"), ma)


def test_all_contexts_and_excel(company, tmp_path):
    db = company["kt"].db
    sample_data(db)
    cr = ContractReports(db)
    assert len(CONTEXTS) == 12
    f = ReportFilters(tach_nam=False, tu="2026-01-01", den="2026-12-31")
    for key, _ in CONTEXTS:
        reps = cr.build(key, f)
        out = export_xlsx(reps, tmp_path / f"{key}.xlsx", company="Cty", user="Kế toán")
        assert out.stat().st_size > 1000
    by_type = cr.build("loai_hd", f)
    assert [r.sheet for r in by_type] == ["Báo cáo đầu ra", "Báo cáo đầu vào"]     # đầu ra, đầu vào tách bảng
    assert by_type[0].rows[0]["DaThanhToan"] == 35_000_000
    merged = cr.build("loai_hd", ReportFilters(tach_nam=False, tu="2026-01-01", den="2026-12-31", gop_ra_vao=True))
    assert len(merged) == 1 and {r["Chieu"] for r in merged[0].rows} == {"Đầu ra", "Đầu vào"}
    paid = cr.build("da_thanh_toan", ReportFilters(tach_nam=False, tu="2026-04-01", den="2026-04-30"))[0]
    assert paid.total["SoTien"] == 5_000_000
    vin = cr.build("hoa_don_vao", ReportFilters(tach_nam=False, co_hd_lien_ket=False))[0]
    data = [r for r in vin.rows if not r.get("_kieu")]
    assert len(data) == 1 and data[0]["SoHopDong"] == "(Mua trực tiếp)"
    group = cr.build("nhom_cdt", f)[0]
    assert group.total["ConPhaiThu"] == 20_000_000
    assert len(cr.build("thoi_gian", f)) == 2 and len(cr.build("hoa_don_ra_vao", f)) == 2
    assert [r.sheet for r in cr.build("tong_hop", f)] == ["Hợp đồng đầu ra", "Hợp đồng đầu vào", "Công việc",
                                                           "Hóa đơn đầu ra", "Thanh toán", "Hóa đơn đầu vào"]


def test_input_invoice_listing_tax_groups(company, tmp_path):
    from openpyxl import load_workbook

    db = company["kt"].db
    d = sample_data(db)
    d["cs"].save_in_invoice({"SoHoaDonGoc": "00086289", "TenNhaCungCap": "Cây xăng 133", "MaThueNCC": "0100598136",
                             "NgayNhanHD": "2026-02-20", "GiaTriTruocThue": 600_000, "VAT": 0, "ThueSuat": "KCT",
                             "MatHang": "Xăng E10 RON 95", "NguoiMuaHang": "Bùi Tiến Dũng"})
    cr = ContractReports(db)
    rep = cr.build("hoa_don_vao", ReportFilters(tach_nam=False))[0]
    heads = [r["_nhan"] for r in rep.rows if r.get("_kieu") == "nhom"]
    assert heads[0].startswith("Nhóm HHDV mua vào : 1.") and heads[0].endswith("(2)")
    assert heads[1].startswith("Nhóm HHDV mua vào : 5.") and heads[1].endswith("(1)")
    assert rep.total["SoHoaDonGoc"] == "Số dòng = 3" and rep.total["VAT"] == 1_080_000
    only = cr.build("hoa_don_vao", ReportFilters(tach_nam=False, nhom_thue="Không chịu thuế"))[0]
    assert [r["MatHang"] for r in only.rows if not r.get("_kieu")] == ["Xăng E10 RON 95"]
    mixed = cr.build("hoa_don_vao", ReportFilters(tach_nam=False, nhom_thue="lan"))[0]
    assert not any(r.get("_kieu") for r in mixed.rows)
    assert [r["NgayNhanHD"] for r in mixed.rows] == sorted(r["NgayNhanHD"] for r in mixed.rows)
    path = export_xlsx(rep, tmp_path / "bang_ke.xlsx")
    ws = load_workbook(path).active
    assert ws.cell(7, 1).value.startswith("Nhóm HHDV mua vào : 1.")
    assert ws.cell(7, 7).value == 11_000_000      # cộng nhóm, cột "chưa có thuế"


def test_out_invoices_one_sheet_per_buyer(company, tmp_path):
    db = company["kt"].db
    d = sample_data(db)
    d["cs"].save_out_invoice({"KyHieuHD": "C26TAA", "SoHoaDon": "13", "MaHopDong": d["hd"], "NgayXuatHD": "2026-04-01",
                              "TenNguoiMua": "Trường THCS Lê Lợi", "MSTNguoiMua": "0109999999",
                              "GiaTriTruocThue": 10_000_000, "VAT": 1_000_000})
    cr = ContractReports(db)
    buyers = cr.buyers()
    assert len(buyers) == 2
    reps = cr.build("hoa_don_ra_nguoi_mua", ReportFilters(tach_nam=False))
    assert len(reps) == 2 and {len(r.rows) for r in reps} == {1}
    one = cr.build("hoa_don_ra_nguoi_mua", ReportFilters(tach_nam=False, nguoi_mua=["mst:0109999999"]))
    assert len(one) == 1 and one[0].sheet == "Trường THCS Lê Lợi"
    export_xlsx(reps, tmp_path / "nguoi_mua.xlsx")


def test_relative_period_for_quick_export():
    from datetime import date

    from qlhd.core.reports import relative_range
    today = date(2026, 1, 20)
    assert relative_range("thang_truoc", today) == ("2025-12-01", "2025-12-31")
    assert relative_range("quy_truoc", today) == ("2025-10-01", "2025-12-31")
    assert relative_range("nam_nay", today) == ("2026-01-01", "2026-12-31")
    f = ReportFilters(ky_tuong_doi="quy_nay").resolved(today)
    assert (f.tu, f.den) == ("2026-01-01", "2026-03-31")


def test_periods():
    assert [p[0] for p in split_periods("2026-01-15", "2026-03-02", "thang")] == [
        "Tháng 01/2026", "Tháng 02/2026", "Tháng 03/2026"]
    assert split_periods("2026-01-01", "2026-12-31", "quy")[3] == ("Quý 4/2026", "2026-10-01", "2026-12-31")
    from datetime import date
    assert period_range("quy", date(2026, 5, 9)) == ("2026-04-01", "2026-06-30")
    assert period_range("thang", date(2026, 12, 9)) == ("2026-12-01", "2026-12-31")


def test_reports_split_by_contract_year(company):
    db = company["kt"].db
    d = sample_data(db)
    cs = d["cs"]
    old = cs.save_contract({"ThoiGianThucHien": 3, "DonViThoiGian": "tháng", "SoHopDong": "09/2025/HĐTV",
                            "TenHopDong": "Thiết kế 2025", "ChieuHopDong": "Đầu ra", "MaDoiTac": d["cdt"],
                            "GiaTriTongSauThue": 50_000_000, "NgayKy": "2025-06-01"})
    cs.save_out_invoice({"SoHoaDon": "5", "MaHopDong": old, "NgayXuatHD": "2026-01-10", "GiaTriSauThue": 20_000_000})
    cr = ContractReports(db)
    assert cr.years(ReportFilters()) == [2025, 2026]
    rep = next(r for r in cr.build("hop_dong", ReportFilters()) if "đầu ra" in r.title)
    labels = [r.get("_nhan") or r.get("SoHopDong") for r in rep.rows]
    assert labels == ["NĂM 2025", "09/2025/HĐTV", "Cộng năm 2025", "NĂM 2026", "01/2026/HĐTV", "Cộng năm 2026"]
    assert rep.total["GiaTriHD"] == 160_000_000 and rep.total["SoHopDong"] == "TỔNG CỘNG"
    # hóa đơn xuất năm 2026 của hợp đồng ký 2025 nằm trong nhóm năm 2025
    inv = cr.build("hoa_don_ra", ReportFilters())[0]
    y25 = inv.rows[:inv.rows.index(next(r for r in inv.rows if r.get("_nhan") == "NĂM 2026"))]
    assert any(r.get("SoHoaDon") == "5" for r in y25)
    # mọi ngữ cảnh xuất được khi tách năm
    for key, _ in CONTEXTS:
        assert cr.build(key, ReportFilters())
    assert cr.build("hop_dong", ReportFilters(tach_nam=False))[0].rows[0].get("_nhan") is None
