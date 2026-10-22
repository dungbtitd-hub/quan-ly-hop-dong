"""Tự tính bộ giá trị VAT theo 4 hướng: ở bộ tính, file Excel mẫu, bảng kê đầu vào, biểu mẫu, dữ liệu cũ."""
import pytest
from openpyxl import load_workbook

from qlhd.core import in_invoice_excel, vat
from qlhd.core.contracts import ContractService, ValidationError, backfill_vat
from qlhd.core.excel_import import import_rows, read_workbook, write_template


@pytest.mark.parametrize("args,expect", [
    ((100_000_000, None, None, "10%"), (100_000_000, 10_000_000, 110_000_000)),   # trước thuế + %
    ((None, None, 102_275, "8%"), (94_699, 7_576, 102_275)),                     # sau thuế + %
    ((94_699, 7_576, None, ""), (94_699, 7_576, 102_275)),                       # trước thuế + VAT
    ((None, 7_576, 102_275, ""), (94_699, 7_576, 102_275)),                      # sau thuế + VAT
    ((94_699, None, 102_275, ""), (94_699, 7_576, 102_275)),                     # trước + sau thuế
    ((94_699, None, None, "8%"), (94_699, 7_576, 102_275)),                      # 7.575,92 làm tròn lên
    ((5_000_000, None, None, "KCT"), (5_000_000, 0, 5_000_000)),
    ((-1_000_000, None, None, "10%"), (-1_000_000, -100_000, -1_100_000)),        # hóa đơn điều chỉnh giảm
])
def test_four_directions(args, expect):
    res = vat.complete(*args)
    assert not res.error and (res.before, res.vat, res.after) == expect


def test_rate_parsing_and_checks():
    assert [vat.parse_rate(x) for x in (0.1, 0.08, 10, "10", "8 %", "5%", "kct", "0,1", None)] == \
        ["10%", "8%", "10%", "10%", "8%", "5%", "KCT", "10%", ""]
    with pytest.raises(ValueError):
        vat.parse_rate("mười")
    assert vat.complete(100, 10, None).rate == "10%"                            # suy ra thuế suất
    assert vat.complete(100, 10, 120).error                                     # mâu thuẫn
    assert vat.complete(1_000_000, 50_000, None, "10%").notes                   # VAT lệch thuế suất
    assert vat.complete(None, 5, None).error                                    # chỉ có tiền VAT


def test_excel_template_auto_vat(tmp_path, company):
    db = company["gd"].db
    tpl = write_template(tmp_path / "mau.xlsx")
    wb = load_workbook(tpl)
    ws = wb["Hợp đồng"]
    head = [c.value.rstrip(" *") for c in ws[1]]
    base = [c.value for c in ws[2]]
    ws.delete_rows(2, ws.max_row)
    col = {k: head.index(k) for k in head}
    cases = [  # số HĐ, trước thuế, thuế suất, VAT, sau thuế
        ("V1", 100_000_000, "10%", None, None), ("V2", None, 0.08, None, 108_000_000),
        ("V3", 50_000_000, None, 4_000_000, None), ("V4", None, None, 5_000_000, 55_000_000),
        ("V5", 10, None, 1, 20)]
    for so, b, r, t, a in cases:
        row = list(base)
        row[col["Số hợp đồng"]] = so
        for k, x in (("Giá trị trước thuế (đồng)", b), ("Thuế suất VAT (%)", r), ("Tiền thuế VAT (đồng)", t),
                     ("Giá trị sau thuế (đồng)", a)):
            row[col[k]] = x
        ws.append(row)
    wb.save(tpl)
    rows = [r for r in read_workbook(tpl, db) if r.sheet == "Hợp đồng"]
    got = [(r.values["GiaTriTruocThue"], r.values["ThueSuat"], r.values["VAT"], r.values["GiaTriTongSauThue"])
           for r in rows[:4]]
    assert got == [(100_000_000, "10%", 10_000_000, 110_000_000), (100_000_000, "8%", 8_000_000, 108_000_000),
                   (50_000_000, "8%", 4_000_000, 54_000_000), (50_000_000, "10%", 5_000_000, 55_000_000)]
    assert rows[0].errors[0].startswith("Tự tính") and rows[4].status == "Lỗi"
    import_rows(db, rows)
    saved = {h["SoHopDong"]: h for h in db.active("HopDong")}
    assert saved["V2"]["GiaTriTruocThue"] == 100_000_000 and saved["V2"]["ThueSuat"] == "8%"


def test_old_template_without_new_columns_still_reads(tmp_path, company):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Hợp đồng"
    ws.append(["Số hợp đồng *", "Tên hợp đồng *", "Chiều hợp đồng *", "Loại hợp đồng",
               "Đối tác (Chủ đầu tư / Nhà cung cấp) *", "MST đối tác", "Giá trị trước thuế (đồng) *",
               "Tiền thuế VAT (đồng)", "Ngày ký *"])
    ws.append(["C1", "Cũ", "Đầu ra", None, "Ban A", None, 200_000_000, 20_000_000, "01/02/2026"])
    wb.save(tmp_path / "cu.xlsx")
    r = read_workbook(tmp_path / "cu.xlsx", company["gd"].db)[0]
    assert r.status == "Hợp lệ" and r.values["GiaTriTongSauThue"] == 220_000_000 and r.values["NgayKy"] == "2026-02-01"


def test_in_invoice_bang_ke_auto_vat():
    head = ("Số hóa đơn", "Ngày hóa đơn", "Mã số thuế người bán", "Tên người bán", "Giá trị chưa có thuế",
            "Thuế suất", "Thuế GTGT", "Tổng thanh toán")
    data = [("1", "30/04/2026", "0109266456", "A", 94_699, "8%", None, None),
            ("2", "30/04/2026", "0109266456", "A", None, "10%", None, 110_000),
            ("3", "30/04/2026", "0109266456", "A", None, None, 800, 10_800),
            ("4", "30/04/2026", "0109266456", "A", 100, None, 10, 200)]
    items = in_invoice_excel.read_rows([head] + data, "S")
    got = [(i.values["GiaTriTruocThue"], i.values["VAT"], i.values["GiaTriSauThue"], i.values["ThueSuat"])
           for i in items]
    assert got[:3] == [(94_699, 7_576, 102_275, "8%"), (100_000, 10_000, 110_000, "10%"),
                       (10_000, 800, 10_800, "8%")]
    assert items[3].errors and not items[0].errors


def test_save_and_backfill_old_data(company):
    db = company["gd"].db
    cs = ContractService(db)
    p = cs.find_or_create_partner("Nhà cung cấp X", "0101010101", "Nhà cung cấp")
    base = {"SoHoaDonGoc": "9", "TenNhaCungCap": "X", "MaThueNCC": "0101010101", "NgayNhanHD": "2026-03-01"}
    ma = cs.save_in_invoice(dict(base, GiaTriSauThue=108_000, ThueSuat="8%"))
    assert db.get("HoaDonDauVao", ma)["VAT"] == 8_000
    with pytest.raises(ValidationError):
        cs.save_in_invoice(dict(base, SoHoaDonGoc="10", GiaTriTruocThue=100, VAT=10, GiaTriSauThue=120))
    # bản cũ: có thuế suất nhưng VAT = 0, sau thuế = trước thuế
    old = db.insert("HoaDonDauVao", dict(base, SoHoaDonGoc="11", GiaTriTruocThue=50_000, VAT=0,
                                         GiaTriSauThue=50_000, ThueSuat="10%"))
    ok = db.insert("HoaDonDauVao", dict(base, SoHoaDonGoc="12", GiaTriTruocThue=50_000, VAT=0,
                                        GiaTriSauThue=50_000, ThueSuat="KCT"))
    hd = cs.save_contract({"SoHopDong": "B1", "TenHopDong": "t", "ChieuHopDong": "Đầu vào", "MaDoiTac": p,
                           "NgayKy": "2026-01-01", "GiaTriTruocThue": 1_000})
    db.update("HopDong", hd, {"GiaTriTongSauThue": 0})
    assert backfill_vat(db) == 2
    assert (db.get("HoaDonDauVao", old)["VAT"], db.get("HoaDonDauVao", old)["GiaTriSauThue"]) == (5_000, 55_000)
    assert db.get("HoaDonDauVao", ok)["VAT"] == 0
    assert db.get("HopDong", hd)["GiaTriTongSauThue"] == 1_000
    assert backfill_vat(db) == 0


def test_form_live_calculation(qtbot):
    from qlhd.ui.widgets import FormDialog, rate_field

    dlg = FormDialog("HĐ", [{"key": "GiaTriTruocThue", "label": "Trước", "kind": "money"}, rate_field(),
                            {"key": "VAT", "label": "VAT", "kind": "money"},
                            {"key": "GiaTriSauThue", "label": "Sau", "kind": "money"}])
    qtbot.addWidget(dlg)
    w = dlg.widgets
    w["ThueSuat"].setEditText("10%")
    qtbot.keyClicks(w["GiaTriTruocThue"], "2000000")
    assert (dlg.values()["VAT"], dlg.values()["GiaTriSauThue"]) == (200_000, 2_200_000)
    w["GiaTriSauThue"].clear()
    qtbot.keyClicks(w["GiaTriSauThue"], "1080000")
    w["ThueSuat"].setEditText("8%")
    v = dlg.values()
    assert (v["GiaTriTruocThue"], v["VAT"], v["GiaTriSauThue"]) == (1_000_000, 80_000, 1_080_000)
