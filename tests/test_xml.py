import pytest

from qlhd.core.xml_invoice import InvoiceXMLError, parse_invoice

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<HDon xmlns="http://example.gdt/hddt"><DLHDon Id="data">
<TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TAA</KHHDon><SHDon>0000123</SHDon><NLap>2026-03-01</NLap>
<DVTTe>VND</DVTTe></TTChung>
<NDHDon><NBan><Ten>Công ty Bán</Ten><MST>0101111111</MST></NBan>
<NMua><Ten>Công ty Mua</Ten><MST>0102222222</MST></NMua>
<TToan><THTTLTSuat><LTSuat><TSuat>10%</TSuat><ThTien>1000000</ThTien><TThue>100000</TThue></LTSuat></THTTLTSuat>
<TgTCThue>1000000</TgTCThue><TgTThue>100000</TgTThue><TgTTTBSo>1100000</TgTTTBSo></TToan></NDHDon>
</DLHDon></HDon>"""


def test_parse_namespaced_invoice():
    d = parse_invoice(SAMPLE.encode())
    assert d["SoHoaDon"] == "0000123" and d["KyHieuHD"] == "C26TAA" and d["KyHieuMauSo"] == "1"
    assert d["MSTNguoiBan"] == "0101111111" and d["TenNguoiMua"] == "Công ty Mua"
    assert (d["GiaTriTruocThue"], d["VAT"], d["GiaTriSauThue"]) == (1_000_000, 100_000, 1_100_000)
    assert d["ThueSuat"][0]["ThueSuat"] == "10%"


def test_bad_xml():
    with pytest.raises(InvoiceXMLError):
        parse_invoice(b"<a><b></a>")
    with pytest.raises(InvoiceXMLError):
        parse_invoice(b"<HDon><x/></HDon>")


MULTI = """<?xml version="1.0" encoding="UTF-8"?>
<HDon><DLHDon><TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TBB</KHHDon><SHDon>77</SHDon><NLap>2026-03-02</NLap>
</TTChung><NDHDon><NBan><Ten>Cửa hàng Z</Ten><MST>0303333333</MST></NBan><NMua><Ten>Công ty Mua</Ten></NMua>
<DSHHDVu>
<HHDVu><TChat>1</TChat><THHDVu>Giấy A4</THHDVu><SLuong>10</SLuong><DGia>100000</DGia><TSuat>10%</TSuat><ThTien>1000000</ThTien></HHDVu>
<HHDVu><TChat>1</TChat><THHDVu>Nước uống</THHDVu><SLuong>5</SLuong><DGia>100000</DGia><TSuat>8%</TSuat><ThTien>500000</ThTien></HHDVu>
</DSHHDVu>
<TToan>{ts}<TgTCThue>1500000</TgTCThue><TgTThue>140000</TgTThue><TgTTTBSo>1640000</TgTTTBSo></TToan></NDHDon>
</DLHDon></HDon>"""
TS = ("<THTTLTSuat><LTSuat><TSuat>10%</TSuat><ThTien>1000000</ThTien><TThue>100000</TThue></LTSuat>"
      "<LTSuat><TSuat>8%</TSuat><ThTien>500000</ThTien><TThue>40000</TThue></LTSuat></THTTLTSuat>")


@pytest.mark.parametrize("ts", [TS, ""])          # có / không có bảng tổng hợp theo thuế suất
def test_multi_rate_invoice_saves_and_splits_in_report(company, ts):
    from qlhd.core.contracts import ContractService
    from qlhd.core.reports import ContractReports, ReportFilters
    from qlhd.ui.invoices_page import invoice_values
    d = parse_invoice(MULTI.format(ts=ts).encode())
    assert d["ThueSuatHD"] == "10%; 8%"
    assert [(x["ThueSuat"], x["TruocThue"]) for x in d["ChiTietThueSuat"]] == [("10%", 1_000_000), ("8%", 500_000)]
    v, notes = invoice_values("vao", d, "x.xml")
    assert v["ThueSuat"] == "10%; 8%" and v["ChiTietThueSuat"] and any("nhiều mức thuế" in n for n in notes)
    db = company["kt"].db
    cs = ContractService(db)
    ma = cs.save_in_invoice(v)                                # trước đây: "thuế suất không hợp lệ"
    r = db.get("HoaDonDauVao", ma)
    assert (r["GiaTriTruocThue"], r["VAT"], r["GiaTriSauThue"], r["ThueSuat"]) == (1_500_000, 140_000, 1_640_000,
                                                                                    "10%; 8%")
    rows = [x for x in ContractReports(db).build("hoa_don_vao", ReportFilters(tach_nam=False))[0].rows
            if x.get("SoHoaDonGoc") == "77"]
    assert [(x["ThueSuat"], x["GiaTriTruocThue"], x["VAT"]) for x in rows] == [("10%", 1_000_000, 100_000),
                                                                             ("8%", 500_000, 40_000)]


def test_multi_rate_manual_entry_rules():
    from qlhd.core import vat
    v = {"GiaTriTruocThue": 1_500_000, "VAT": 140_000, "GiaTriSauThue": 0, "ThueSuat": "10%; 8%"}
    vat.fill(v)                                               # nhập tay: nhiều mức + tổng tiền, tổng VAT
    assert v["GiaTriSauThue"] == 1_640_000 and v["ThueSuat"] == "10%; 8%"
    with pytest.raises(ValueError):                           # nhiều mức mà thiếu VAT: không đoán
        vat.fill({"GiaTriTruocThue": 1_500_000, "ThueSuat": "10%;8%"})
    lines = [{"ThueSuat": "10%", "TruocThue": 1_000_000}, {"ThueSuat": "8%", "TruocThue": 500_000}]
    v = {"ChiTietThueSuat": lines, "GiaTriTruocThue": 0, "VAT": 0, "GiaTriSauThue": 0, "ThueSuat": ""}
    vat.fill(v)
    assert (v["GiaTriTruocThue"], v["VAT"], v["GiaTriSauThue"]) == (1_500_000, 140_000, 1_640_000)
    with pytest.raises(ValueError):                           # tổng không khớp chi tiết
        vat.fill({"ChiTietThueSuat": lines, "GiaTriTruocThue": 2_000_000, "VAT": 0, "GiaTriSauThue": 0})
