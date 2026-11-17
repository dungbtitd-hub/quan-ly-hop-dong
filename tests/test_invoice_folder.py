"""Quét thư mục hóa đơn, Gemini đọc ảnh / kiểm tra / phân loại (giả lập Gemini, không gọi mạng)."""
import json

from qlhd.core import gemini as gm
from qlhd.core.assets import AssetService
from qlhd.core.gemini import GeminiError, GeminiService
from qlhd.core.invoice_folder import F_ADDED, F_COMPANION, F_EXISTS, F_REVIEW, SRC_GEMINI, InvoiceFolders
from tests.helpers import sample_data

IN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<HDon><DLHDon><TTChung><KHMSHDon>1</KHMSHDon><KHHDon>{kh}</KHHDon><SHDon>{so}</SHDon><NLap>2026-11-10</NLap>
</TTChung><NDHDon><NBan><Ten>{ban}</Ten><MST>{mst}</MST></NBan><NMua><Ten>Công ty Mua</Ten></NMua>
<DSHHDVu><HHDVu><TChat>1</TChat><THHDVu>{hang}</THHDVu><SLuong>1</SLuong><DGia>{tien}</DGia><TSuat>10%</TSuat>
<ThTien>{tien}</ThTien></HHDVu></DSHHDVu>
<TToan><TgTCThue>{tien}</TgTCThue><TgTThue>{vat}</TgTThue><TgTTTBSo>{tong}</TgTTTBSo></TToan></NDHDon>
</DLHDon></HDon>"""


def _xml(path, so, mst="0400000004", ban="Cửa hàng Tin học A", kh="C26TMA", hang="Giấy A4", tien=1_000_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(IN_XML.format(so=so, mst=mst, ban=ban, kh=kh, hang=hang, tien=tien, vat=tien // 10,
                                  tong=tien + tien // 10), encoding="utf-8")


class FakeGemini:
    def __init__(self, extract=None, error=None, classify=None, check=None):
        self.extract, self.error, self.cls, self.chk = extract, error, classify, check
        self.calls = []

    def extract_invoice(self, path):
        self.calls.append(("doc", str(path)))
        if self.error:
            raise self.error
        return self.extract

    def classify_lines(self, lines):
        self.calls.append(("phan_loai", lines))
        if self.error:
            raise self.error
        return self.cls(lines)


def _scan(f, kind, gem=None):
    return f.process(kind, InvoiceFolders.read(kind, f.new_files(kind), gem, use_gemini=not f.first_scan(kind)))


def test_scan_only_new_files_and_pairs_xml_pdf(company, tmp_path):
    db = company["kt"].db
    sample_data(db)
    root = tmp_path / "hd_vao"
    _xml(root / "2026" / "Quy 4" / "hd1.xml", 101)
    (root / "2026" / "Quy 4" / "hd1.pdf").write_bytes(b"%PDF-1.4 gia")
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(root), "ra": ""})
    # lần quét đầu: không tự thêm, để nhân viên chọn
    r = _scan(f, "vao")
    assert r["lan_dau"] and r["them"] == [] and r["can_xem"] == 1
    st = {x["DuongDan"]: x["TrangThai"] for x in f.files("vao")}
    assert st == {"2026/Quy 4/hd1.xml": F_REVIEW, "2026/Quy 4/hd1.pdf": F_COMPANION}
    assert f.new_files("vao") == []                       # đã ghi nhớ: lần sau bỏ qua
    # hóa đơn mới xuất hiện: tự thêm, gắn tệp gốc + tệp PDF kèm, dữ liệu tự động
    _xml(root / "2026" / "Quy 4" / "hd2.xml", 102, hang="Máy tính xách tay Dell", tien=25_000_000)
    (root / "2026" / "Quy 4" / "hd2.pdf").write_bytes(b"%PDF-1.4 gia")
    r = _scan(f, "vao")
    assert not r["lan_dau"] and len(r["them"]) == 1
    inv = db.get("HoaDonDauVao", r["them"][0])
    assert inv["SoHoaDonGoc"] == "102" and inv["TepTuongDoi"] == "2026/Quy 4/hd2.xml" and inv["SuaTay"] is None
    assert json.loads(inv["TepKem"]) == ["2026/Quy 4/hd2.pdf"] and inv["NguonSoLieu"] == "XML"
    assert f.file_name(inv) == "hd2.xml" and f.sub_folder(inv) == "2026\\Quy 4"
    assert f.view_file("vao", inv).name == "hd2.pdf" and f.resolve("vao", inv).name == "hd2.xml"
    assert AssetService(db).lines_of(inv["Ma"])
    # nhân viên sửa hóa đơn rồi file được chép lại vào thư mục khác: không nhập lại, không ghi đè số liệu
    db.update("HoaDonDauVao", inv["Ma"], {"MatHang": "đã soát"})
    _xml(root / "ban sao" / "hd2.xml", 102, hang="Máy tính xách tay Dell", tien=25_000_000)
    r = _scan(f, "vao")
    assert r["co_san"] == 1 and r["them"] == []
    assert db.get("HoaDonDauVao", inv["Ma"])["MatHang"] == "đã soát"
    assert db.get("HoaDonDauVao", inv["Ma"])["TepTuongDoi"] == "2026/Quy 4/hd2.xml"
    assert any(x["TrangThai"] == F_EXISTS for x in f.files("vao"))


def test_suspected_duplicate_and_unknown_contract_go_to_review(company, tmp_path):
    db = company["kt"].db
    d0 = sample_data(db)
    vao, ra = tmp_path / "vao", tmp_path / "ra"
    vao.mkdir()
    ra.mkdir()
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(vao), "ra": str(ra)})
    _scan(f, "vao"), _scan(f, "ra")                       # lần đầu (thư mục trống)
    # cùng người bán + số 88 nhưng khác ký hiệu → nghi trùng, không tự thêm
    _xml(vao / "nghi_trung.xml", 88, mst="0200000002", ban="Công ty Vật tư X", kh="K26X")
    # hóa đơn đầu ra: người mua chưa có hợp đồng → Cần xem; người mua là CĐT (một hợp đồng) → tự thêm
    _xml(ra / "la.xml", 5, mst="0100000001")
    (ra / "la.xml").write_text((ra / "la.xml").read_text(encoding="utf-8").replace(
        "<NMua><Ten>Công ty Mua</Ten></NMua>", "<NMua><Ten>Khách lạ</Ten><MST>0999999999</MST></NMua>"),
        encoding="utf-8")
    _xml(ra / "cdt.xml", 6, mst="0100000001")
    (ra / "cdt.xml").write_text((ra / "cdt.xml").read_text(encoding="utf-8").replace(
        "<NMua><Ten>Công ty Mua</Ten></NMua>", "<NMua><Ten>Ban QLDA Hà Nội</Ten><MST>0100000001</MST></NMua>"),
        encoding="utf-8")
    r = _scan(f, "vao")
    assert r["them"] == [] and r["can_xem"] == 1
    note = f.files("vao", F_REVIEW)[0]["GhiChu"]
    assert note.startswith("Nghi trùng")
    r = _scan(f, "ra")
    assert len(r["them"]) == 1 and r["can_xem"] == 1
    assert db.get("HoaDonDauRa", r["them"][0])["MaHopDong"] == d0["hd"]
    rv = f.files("ra", F_REVIEW)[0]
    assert rv["DuongDan"] == "la.xml" and "hợp đồng" in rv["GhiChu"] and rv["Data"]["values"]["SoHoaDon"] == "5"
    assert f.review_count() == 2


def test_image_read_by_gemini_and_offline_retry(company, tmp_path):
    db = company["kt"].db
    sample_data(db)
    root = tmp_path / "vao"
    root.mkdir()
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(root)})
    _scan(f, "vao")
    (root / "anh_hd.jpg").write_bytes(b"\xff\xd8\xff anh")
    off = FakeGemini(error=GeminiError("mất mạng", "mang"))
    r = _scan(f, "vao", off)
    assert r["cho_gemini"] == 1 and r["can_xem"] == 0 and f.new_files("vao")   # lần sau đọc lại
    ok = FakeGemini(extract={"la_hoa_don": True, "ky_hieu": "C26TZZ", "so_hoa_don": "0000301",
                             "ngay_lap": "2026-11-12", "ten_nguoi_ban": "Quán ăn B", "mst_nguoi_ban": "0500000005",
                             "tien_truoc_thue": 500_000, "tien_thue": 40_000, "tong_thanh_toan": 540_000,
                             "thue_suat": "8%", "dong": [{"ten": "Tiếp khách", "thanh_tien": 500_000}]})
    r = _scan(f, "vao", ok)
    assert len(r["them"]) == 1 and len(ok.calls) == 1
    inv = db.get("HoaDonDauVao", r["them"][0])
    assert inv["NguonSoLieu"] == SRC_GEMINI and inv["SoHoaDonGoc"] == "0000301" and inv["GiaTriSauThue"] == 540_000
    # thiếu trường bắt buộc → Cần xem
    (root / "mo.png").write_bytes(b"\x89PNG mo")
    r = _scan(f, "vao", FakeGemini(extract={"la_hoa_don": True, "so_hoa_don": "9"}))
    assert r["can_xem"] == 1 and "Thiếu" in f.files("vao", F_REVIEW)[0]["GhiChu"]
    # không có Gemini: ảnh để nhân viên xem
    (root / "khac.jpg").write_bytes(b"\xff\xd8\xff")
    assert _scan(f, "vao")["can_xem"] == 1
    assert {x["TrangThai"] for x in f.files("vao")} == {F_ADDED, F_REVIEW}


def test_gemini_classify_auto_writes_keeps_manual_and_reverts(company):
    db = company["kt"].db
    from qlhd.core.contracts import ContractService
    from qlhd.core.xml_invoice import parse_invoice
    cs = ContractService(db)
    for so, hang in ((1, "Dell XPS 9340 i7"), (2, "Hàng hóa XYZ không rõ"), (3, "Bút bi Thiên Long")):
        d = parse_invoice(IN_XML.format(so=so, mst=f"060000000{so}", ban=f"NCC {so}", kh="C26T", hang=hang,
                                        tien=25_000_000, vat=2_500_000, tong=27_500_000).encode())
        cs.save_in_invoice({"SoHoaDonGoc": str(so), "TenNhaCungCap": f"NCC {so}", "MaThueNCC": d["MSTNguoiBan"],
                            "NgayNhanHD": "2026-11-10", "GiaTriTruocThue": 25_000_000, "VAT": 2_500_000,
                            "KyHieuHD": "C26T", "_Dong": d["Dong"]})
    svc = GeminiService(db)
    lines = {ln["TenHang"]: ln for ln in svc.unreviewed_lines()}
    assert len(lines) == 3
    pen = lines["Bút bi Thiên Long"]
    AssetService(db).set_line_class(pen["Ma"], "Chi phí trong kỳ", "CP04", None)   # kế toán tự chọn

    def answer(ask):
        out = []
        for a in ask:
            if a["ten"].startswith("Dell"):
                out.append({"id": a["id"], "ma_nhom": "TB02", "so_thang": 36, "do_chac": "cao", "ly_do": "máy tính"})
            elif a["ten"].startswith("Bút"):
                out.append({"id": a["id"], "ma_nhom": "CP09", "do_chac": "cao", "ly_do": "đồ dùng"})
            else:
                out.append({"id": a["id"], "do_chac": "thap", "ly_do": "không rõ"})
        return out
    gem = FakeGemini(classify=answer)
    before = {k: dict(db.get("DongHoaDonDauVao", v["Ma"])) for k, v in lines.items()}
    res = svc.classify(svc.unreviewed_lines(), gem)
    assert res == {"doi": 1, "dong_y": 0, "khong_chac": 1, "khac": 1}
    lap = db.get("DongHoaDonDauVao", lines["Dell XPS 9340 i7"]["Ma"])
    assert lap["MaNhomDM"] == "TB02" and lap["PhanLoai"] == "CCDC" and lap["SoThang"] == 36 and lap["TuDong"] == 1
    assert lap["SuaTay"] is None                              # Gemini ghi là dữ liệu tự động
    pen2 = db.get("DongHoaDonDauVao", pen["Ma"])
    assert pen2["MaNhomDM"] == "CP04" and pen2["GeminiTT"] == gm.CL_DIFF   # không ghi đè lựa chọn của kế toán
    unk = db.get("DongHoaDonDauVao", lines["Hàng hóa XYZ không rõ"]["Ma"])
    assert unk["GeminiTT"] == gm.CL_UNSURE and unk["PhanLoai"] == before["Hàng hóa XYZ không rõ"]["PhanLoai"]
    assert svc.unreviewed_lines() == [] and svc.state()[0] == "chua_cai"
    assert lap["GeminiTT"] == gm.CL_CHANGED
    assert db.one("SELECT COUNT(*) c FROM TaiSan WHERE DaXoa = 0 AND MaDongHD = ?", (lap["Ma"],))["c"] == 1
    assert svc.revert([lap["Ma"]]) == 1
    back = db.get("DongHoaDonDauVao", lap["Ma"])
    assert back["MaNhomDM"] is None and back["TuDong"] == 0 and back["SuaTay"]
    assert db.one("SELECT COUNT(*) c FROM TaiSan WHERE DaXoa = 0 AND MaDongHD = ?", (lap["Ma"],))["c"] == 0
    # mất kết nối: lỗi được ghi nhận, dòng vẫn chưa rà soát
    db.update("DongHoaDonDauVao", unk["Ma"], {"GeminiTT": None})
    try:
        svc.classify(svc.unreviewed_lines(), FakeGemini(error=GeminiError("mất mạng", "mang")))
    except GeminiError:
        pass
    assert db.setting(gm.K_STATE) == "loi" and len(svc.unreviewed_lines()) == 1


def test_gemini_check_compare_and_api_errors(company, monkeypatch):
    db = company["kt"].db
    d0 = sample_data(db)
    svc = GeminiService(db)
    inv = dict(db.get("HoaDonDauRa", d0["inv"]))
    ok = {"so_hoa_don": "0000012", "ngay_lap": "2026-03-01", "tong_thanh_toan": 55_000_000, "hop_le": True,
          "luu_y": ["Không thấy chữ ký số trên bản thể hiện"]}
    st, cmp, notes = svc.compare("ra", inv, ok)
    assert st == gm.ST_OK and notes
    assert svc.compare("ra", inv, dict(ok, tong_thanh_toan=50_000_000))[0] == gm.ST_ISSUE
    assert svc.compare("ra", inv, dict(ok, hop_le=False))[0] == gm.ST_ISSUE
    assert svc.save_check("ra", d0["inv"], dict(ok, so_hoa_don="13")) == gm.ST_ISSUE
    assert [k for k, _ in svc.issue_invoices()] == ["ra"]
    svc.mark_seen("ra", d0["inv"])
    assert svc.issue_invoices() == []
    # khóa API lưu mã hóa, riêng máy; trả lời không đọc được / lỗi HTTP
    svc.save_settings({"khoa": "AIza-thu", "mo_hinh": "gemini-2.5-flash"})
    assert svc.key() == "AIza-thu" and db.setting(gm.K_KEY) != "AIza-thu"
    g = svc.client()
    monkeypatch.setattr(g, "_post", lambda body: {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
    g.ping()
    monkeypatch.setattr(g, "_post", lambda body: {"promptFeedback": {"blockReason": "SAFETY"}})
    try:
        g.ping()
        raise AssertionError("phải báo lỗi")
    except GeminiError as e:
        assert "SAFETY" in str(e) and not e.offline
    assert gm.apply_suggestion({"ma_nhom": "XX99", "do_chac": "cao"}, 1) is None
    assert gm.apply_suggestion({"ma_nhom": "TB02", "do_chac": "cao", "so_thang": 36}, 35_000_000)["PhanLoai"] == "TSCĐ"

