from qlhd.core.invoice_match import fold, match_contract, referenced_numbers
from qlhd.core.pdf_invoice import parse_text
from qlhd.core.xml_invoice import parse_invoice
from tests.helpers import sample_data


def _xml(mua_mst="0100000001", ban_mst="0109999999", ttkhac="", item="Tư vấn thiết kế"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<HDon><DLHDon><TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TAA</KHHDon><SHDon>77</SHDon><NLap>2026-04-01</NLap>
{ttkhac}</TTChung>
<NDHDon><NBan><Ten>Công ty Chúng tôi</Ten><MST>{ban_mst}</MST></NBan><NMua><Ten>Khách</Ten><MST>{mua_mst}</MST></NMua>
<DSHHDVu><HHDVu><THHDVu>{item}</THHDVu><ThTien>1000000</ThTien></HHDVu></DSHHDVu>
<TToan><TgTCThue>1000000</TgTCThue><TgTThue>100000</TgTThue><TgTTTBSo>1100000</TgTTTBSo></TToan></NDHDon>
</DLHDon></HDon>""".encode()


def test_fold_and_references():
    assert fold("01/2026/HĐTV") == "01/2026/hdtv"
    assert referenced_numbers("Thanh toán đợt 1 theo Hợp đồng kinh tế số 01/2026/HĐTV ngày 15/01") == ["01/2026/hdtv"]
    assert referenced_numbers("Theo HĐ 05/2026/MB") == ["05/2026/mb"]
    assert referenced_numbers("Hóa đơn bán hàng tháng 3") == []


def test_match_by_contract_number_in_xml_other_info(company):
    db = company["gd"].db
    d = sample_data(db)
    # đối tác có hai hợp đồng đầu ra: MST thôi chưa đủ, phải nhờ số hợp đồng
    d["cs"].save_contract({"SoHopDong": "02/2026/HĐTV", "TenHopDong": "Thiết kế nhà thi đấu", "ChieuHopDong": "Đầu ra",
                           "MaDoiTac": d["cdt"], "GiaTriTruocThue": 1, "NgayKy": "2026-03-01"})
    inv = parse_invoice(_xml(ttkhac="<TTKhac><TTin><TTruong>Số hợp đồng</TTruong><KDLieu>string</KDLieu>"
                                    "<DLieu>02/2026/HDTV</DLieu></TTin></TTKhac>"))
    assert inv["SoHopDong"] == "02/2026/HDTV"
    m = match_contract(db, "ra", inv)
    assert m.so == "02/2026/HĐTV" and "số hợp đồng" in m.ly_do
    # số hợp đồng nằm trong tên hàng hóa
    m = match_contract(db, "ra", parse_invoice(_xml(item="Thanh toán đợt 2 theo HĐ số 01/2026/HĐTV")))
    assert m.so == "01/2026/HĐTV"
    # không ghi số hợp đồng, đối tác có hai hợp đồng đang thực hiện: không tự gắn, gợi ý cả hai
    m = match_contract(db, "ra", parse_invoice(_xml()))
    assert m.ma is None and {h["SoHopDong"] for h in m.goi_y} == {"01/2026/HĐTV", "02/2026/HĐTV"}
    assert "chọn" in m.note


def test_match_rules_mst_and_unknown_number(company):
    db = company["gd"].db
    sample_data(db)
    # chỉ một hợp đồng với đối tác → gắn theo MST
    m = match_contract(db, "ra", parse_invoice(_xml()))
    assert m.so == "01/2026/HĐTV" and "MST" in m.ly_do
    # hóa đơn ghi số hợp đồng không có trong phần mềm → không đoán
    m = match_contract(db, "ra", parse_invoice(_xml(item="Theo hợp đồng số 99/2026/XYZ")))
    assert m.ma is None and m.so_tren_hoa_don == ["99/2026/xyz"] and "99/2026/XYZ" in m.note
    # số hợp đồng đúng nhưng MST người mua khác đối tác của hợp đồng → không gắn
    m = match_contract(db, "ra", parse_invoice(_xml(mua_mst="0300000003", item="Theo HĐ số 01/2026/HĐTV")))
    assert m.ma is None
    # đầu vào: đối tác là người bán
    m = match_contract(db, "vao", parse_invoice(_xml(ban_mst="0200000002", mua_mst="0101234567",
                                                     item="Vật tư theo hợp đồng 05/2026/MB")))
    assert m.so == "05/2026/MB"
    # không ai khớp → để người dùng chọn
    m = match_contract(db, "vao", parse_invoice(_xml(ban_mst="0400000004", mua_mst="0101234567")))
    assert m.ma is None and not m.goi_y


def test_pdf_text_contract_field(company):
    db = company["gd"].db
    sample_data(db)
    text = ("HÓA ĐƠN GIÁ TRỊ GIA TĂNG\nKý hiệu: 1C26TAA\nSố: 0000088\nNgày 05 tháng 04 năm 2026\n"
            "Đơn vị bán hàng: Công ty Chúng tôi\nMã số thuế: 0109999999\n"
            "Tên đơn vị: Ban QLDA Hà Nội\nMã số thuế: 0100000001\nSố hợp đồng: 01/2026/HĐTV\n"
            "Cộng tiền hàng: 1.000.000\nTiền thuế GTGT: 100.000\nTổng tiền thanh toán: 1.100.000\n")
    inv = parse_text(text)
    assert inv["SoHopDong"] == "01/2026/HĐTV"
    assert match_contract(db, "ra", inv).so == "01/2026/HĐTV"
