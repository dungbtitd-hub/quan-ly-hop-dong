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
        cs.save_contract({"SoHopDong": "01/2026/HĐTV", "TenHopDong": "x", "ChieuHopDong": "Đầu ra",
                          "MaDoiTac": d["cdt"], "NgayKy": "2026-01-01"})


def test_all_nine_contexts_and_excel(company, tmp_path):
    db = company["kt"].db
    sample_data(db)
    cr = ContractReports(db)
    assert len(CONTEXTS) == 9
    f = ReportFilters(tu="2026-01-01", den="2026-12-31")
    for key, _ in CONTEXTS:
        rep = cr.build(key, f)
        out = export_xlsx(rep, tmp_path / f"{key}.xlsx", company="Cty", user="Kế toán")
        assert out.stat().st_size > 1000
    by_type = cr.build("loai_hd", f)
    out_row = next(r for r in by_type.rows if r["Chieu"] == "Đầu ra")
    assert out_row["DaThanhToan"] == 35_000_000
    paid = cr.build("da_thanh_toan", ReportFilters(tu="2026-04-01", den="2026-04-30"))
    assert paid.total["SoTien"] == 5_000_000
    vin = cr.build("hoa_don_vao", ReportFilters(co_hd_lien_ket=False))
    assert len(vin.rows) == 1 and vin.rows[0]["SoHopDong"] == "(Mua trực tiếp)"
    group = cr.build("nhom_cdt", f)
    assert group.total["ConPhaiThu"] == 20_000_000


def test_filters_payment_status(company):
    db = company["kt"].db
    sample_data(db)
    cr = ContractReports(db)
    assert len(cr.build("hop_dong", ReportFilters(tt_thanh_toan="Còn phải thu", chieu="Đầu ra")).rows) == 1
    assert len(cr.build("hop_dong", ReportFilters(tt_thanh_toan="Đã thu đủ", chieu="Đầu ra")).rows) == 0
    trong_ky = cr.build("hop_dong", ReportFilters(tu="2026-04-01", den="2026-04-30", che_do="trong_ky",
                                                   tinh_theo="NgayTienVe", chieu="Đầu ra"))
    assert trong_ky.rows[0]["DaThanhToan"] == 5_000_000


def test_periods():
    assert [p[0] for p in split_periods("2026-01-15", "2026-03-02", "thang")] == [
        "Tháng 01/2026", "Tháng 02/2026", "Tháng 03/2026"]
    assert split_periods("2026-01-01", "2026-12-31", "quy")[3] == ("Quý 4/2026", "2026-10-01", "2026-12-31")
    from datetime import date
    assert period_range("quy", date(2026, 5, 9)) == ("2026-04-01", "2026-06-30")
    assert period_range("thang", date(2026, 12, 9)) == ("2026-12-01", "2026-12-31")
