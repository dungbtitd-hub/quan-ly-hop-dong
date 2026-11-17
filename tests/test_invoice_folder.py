"""Quét thư mục hóa đơn, Gemini đọc ảnh / kiểm tra / phân loại (giả lập Gemini, không gọi mạng)."""
import json
from pathlib import Path

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
        assert "an toàn" in str(e) and not e.offline
    assert gm.apply_suggestion({"ma_nhom": "XX99", "do_chac": "cao"}, 1) is None
    assert gm.apply_suggestion({"ma_nhom": "TB02", "do_chac": "cao", "so_thang": 36}, 35_000_000)["PhanLoai"] == "TSCĐ"



def test_gemini_retired_model_switches_to_available_one(company, monkeypatch):
    """Google ngừng cấp mô hình cũ (404): tự lấy danh sách mô hình, chuyển sang mô hình còn dùng được, lưu lại."""
    db = company["kt"].db
    svc = GeminiService(db)
    svc.save_settings({"khoa": "AIza-thu", "mo_hinh": "gemini-2.5-flash"})
    calls = []

    def post_once(self, body):
        calls.append(self.model)
        if self.model == "gemini-2.5-flash":
            e = GeminiError("Mô hình gemini-2.5-flash không còn dùng được", "mo_hinh")
            e.detail = ("This model models/gemini-2.5-flash is no longer available to new users. Please update your "
                        "code to use models/gemini-3.8-flash for the latest features.")
            raise e
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    monkeypatch.setattr(gm.Gemini, "_post_once", post_once)
    monkeypatch.setattr(gm.Gemini, "list_models",
                        lambda self: ["gemini-3.8-flash", "gemini-3.8-pro", "gemini-flash-latest", "gemini-3.9-flash-preview"])
    monkeypatch.setattr(gm, "_RESOLVED", {})
    monkeypatch.setattr(gm, "_RETIRED", {})
    g = svc.client()
    g.ping()
    assert calls[0] == "gemini-2.5-flash" and calls[-1] == "gemini-3.8-flash" and g.model == "gemini-3.8-flash"
    svc.record_ok()
    assert db.setting(gm.K_MODEL) == "gemini-3.8-flash" and svc.client().model == "gemini-3.8-flash"
    assert gm.best_model(["gemini-3.8-flash", "gemini-4.1-flash", "gemini-4.1-flash-lite"], "x-flash") == "gemini-4.1-flash"
    assert gm.best_model(["gemini-flash-latest"], "gemini-2.5-flash") == "gemini-flash-latest"


def test_existing_invoices_only_link_file_conflict_and_rescan(company, tmp_path):
    """Hóa đơn đã nhập tay (số có số 0 đầu, ký hiệu kèm mẫu số / để trống): quét chỉ ghi nhận nơi chứa tệp, không
    báo nghi trùng; số liệu khác thì để Cần xem; xóa bản sao khỏi thư mục thì quên file; xét lại file Cần xem."""
    from qlhd.core.contracts import ContractService
    from qlhd.core.invoice_folder import F_CONFLICT
    db = company["kt"].db
    d0 = sample_data(db)
    cs = ContractService(db)
    a = cs.save_in_invoice({"SoHoaDonGoc": "0000201", "KyHieuHD": "1C26TMA", "TenNhaCungCap": "Cửa hàng Tin học A",
                            "MaThueNCC": "0400000004", "NgayNhanHD": "2026-11-10", "GiaTriTruocThue": 1_000_000,
                            "VAT": 100_000})
    b = cs.save_in_invoice({"SoHoaDonGoc": "202", "TenNhaCungCap": "Cửa hàng Tin học A", "MaThueNCC": "0400000004",
                            "NgayNhanHD": "2026-11-10", "GiaTriTruocThue": 900_000, "VAT": 90_000})
    db.update("HoaDonDauVao", a, {"MatHang": "kế toán đã sửa"})
    vao, ra = tmp_path / "vao", tmp_path / "ra"
    f = InvoiceFolders(db)
    _xml(vao / "201.xml", 201)
    _xml(vao / "202.xml", 202)                                         # số tiền khác hóa đơn đã nhập
    _xml(vao / "ban sao" / "201.xml", 201)
    _xml(ra / "hd12.xml", 12, mst="0100000001", kh="1C26TAA")           # hóa đơn đầu ra đã có (C26TAA số 12)
    f.save_settings({"vao": str(vao), "ra": str(ra)})
    r = _scan(f, "vao")
    assert r["co_san"] == 2 and r["lech"] == 1 and r["them"] == []
    inv = db.get("HoaDonDauVao", a)
    assert inv["TepTuongDoi"] == "201.xml" and inv["MatHang"] == "kế toán đã sửa"
    st = {x["DuongDan"]: (x["TrangThai"], x["GhiChu"]) for x in f.files("vao")}
    assert st["ban sao/201.xml"][0] == F_EXISTS and "Bản sao" in st["ban sao/201.xml"][1]
    assert st["202.xml"][0] == F_CONFLICT and "Tiền trước thuế 900.000 → 1.000.000" in st["202.xml"][1]
    assert db.get("HoaDonDauVao", b)["GiaTriTruocThue"] == 900_000 and db.get("HoaDonDauVao", b)["TepTuongDoi"]
    assert f.review_count() == 1
    assert f.apply_file_values("vao", "202.xml")[0][0] == "GiaTriTruocThue"
    assert db.get("HoaDonDauVao", b)["GiaTriTruocThue"] == 1_000_000 and f.review_count() == 0
    rr = _scan(f, "ra")
    assert rr["lech"] == 1 and rr["them"] == [] and db.get("HoaDonDauRa", d0["inv"])["TepTuongDoi"] == "hd12.xml"
    assert "Ngày lập 2026-03-01 → 2026-11-10" in f.files("ra", F_CONFLICT)[0]["GhiChu"]
    # xóa bản sao khỏi thư mục: phần mềm quên file đó
    (vao / "ban sao" / "201.xml").unlink()
    assert f.prune("vao") == 1 and "ban sao/201.xml" not in {x["DuongDan"] for x in f.files("vao")}
    # file Cần xem được xét lại mỗi lần quét: nghi trùng do có 2 file cùng hóa đơn, xóa một file thì hết
    _xml(vao / "moi" / "301.xml", 301, tien=3_000_000)
    _xml(vao / "moi" / "301 - Copy.xml", 301, tien=3_000_000)
    r = _scan(f, "vao")
    assert len(r["them"]) == 1 and r["co_san"] == 1                    # bản thứ hai nhận ra là hóa đơn vừa thêm
    # quét lại toàn bộ: không thêm gì, không đổi số liệu
    again = f.recheck_files("vao", everything=True)
    items = InvoiceFolders.read("vao", again, cache=f.cache("vao", [x for x, _ in again]))
    r = f.process("vao", items)
    assert r["them"] == [] and r["can_xem"] == 0 and db.get("HoaDonDauVao", a)["MatHang"] == "kế toán đã sửa"
    assert {x["TrangThai"] for x in f.files("vao")} <= {F_ADDED, F_EXISTS}


def test_pdf_without_xml_goes_to_gemini_even_first_scan(company, tmp_path, monkeypatch):
    from qlhd.core import invoice_folder as fo
    from qlhd.core import pdf_invoice
    db = company["kt"].db
    sample_data(db)
    root = tmp_path / "vao"
    root.mkdir()
    (root / "quet.pdf").write_bytes(b"%PDF-1.4 quet")
    (root / "co_san.pdf").write_bytes(b"%PDF-1.4 chu")
    text = {"SoHoaDon": "88", "KyHieuHD": "K26T", "KyHieuMauSo": "1", "MSTNguoiBan": "0200000002",
            "TenNguoiBan": "Công ty Vật tư X", "NgayLap": "2026-02-10", "GiaTriTruocThue": 10_000_000, "VAT": 1_000_000,
            "GiaTriSauThue": 11_000_000, "MSTNguoiMua": "", "TenNguoiMua": "", "ThueSuatHD": "10%", "MatHang": "x"}

    def fake_pdf(path):
        if path.endswith("co_san.pdf"):
            return dict(text)
        raise pdf_invoice.InvoicePDFError("PDF quét, không có lớp chữ")
    monkeypatch.setattr(pdf_invoice, "parse_invoice", fake_pdf)
    gem = FakeGemini(extract={"la_hoa_don": True, "ky_hieu": "C26TQQ", "so_hoa_don": "55", "ngay_lap": "2026-11-12",
                              "ten_nguoi_ban": "Quán C", "mst_nguoi_ban": "0700000007", "tien_truoc_thue": 100_000,
                              "tien_thue": 8_000, "tong_thanh_toan": 108_000, "thue_suat": "8%"})
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(root)})
    items = InvoiceFolders.read("vao", f.new_files("vao"), gem, known=fo.known_keys(db, "vao"))
    assert [Path(c[1]).name for c in gem.calls] == ["quet.pdf"]     # hóa đơn đã có: không gửi Gemini
    r = f.process("vao", items)
    assert r["lan_dau"] and r["co_san"] == 1 and r["can_xem"] == 1
    rv = f.files("vao", F_REVIEW)[0]
    assert rv["Data"]["raw"]["SoHoaDon"] == "55" and rv["Data"]["src"] == SRC_GEMINI
    # xét lại lần sau dùng số liệu đã đọc, không gửi Gemini nữa
    again = f.recheck_files("vao")
    InvoiceFolders.read("vao", again, gem, cache=f.cache("vao", [x for x, _ in again]))
    assert len(gem.calls) == 1


def test_gemini_full_fields_multi_rate_fees_discount():
    from qlhd.core.invoice_folder import from_gemini, invoice_values
    g = {"la_hoa_don": True, "mau_so": "1", "ky_hieu": "C26TXD", "so_hoa_don": "00001234", "ngay_lap": "2026-11-05",
         "ten_nguoi_ban": "Cây xăng D", "mst_nguoi_ban": "0800000008", "ten_nguoi_mua": "Công ty ABC",
         "mst_nguoi_mua": "0101234567",
         "dong": [{"ten": "Xăng RON95", "dvt": "lít", "so_luong": 50.5, "don_gia": 20000, "thanh_tien": 1_010_000,
                   "thue_suat": "10%", "tinh_chat": "hang_hoa"},
                  {"ten": "Dịch vụ rửa xe", "thanh_tien": 100_000, "thue_suat": "8%", "tinh_chat": "hang_hoa"},
                  {"ten": "Chiết khấu thương mại", "thanh_tien": 10_000, "thue_suat": "10%", "tinh_chat": "chiet_khau"},
                  {"ten": "Hàng khuyến mại", "thanh_tien": 0, "tinh_chat": "khuyen_mai"},
                  {"ten": "Ghi chú: giao tại kho", "tinh_chat": "ghi_chu"}],
         "thue_theo_muc": [{"thue_suat": "10%", "tien_truoc_thue": 1_000_000, "tien_thue": 100_000},
                           {"thue_suat": "8%", "tien_truoc_thue": 100_000, "tien_thue": 8_000}],
         "phi_khac": [{"ten": "Thuế bảo vệ môi trường", "so_tien": 200_000}],
         "tien_truoc_thue": 1_100_000, "tien_thue": 108_000, "tong_thanh_toan": 1_408_000}
    d = from_gemini(g)
    assert [x["TenHang"] for x in d["Dong"]] == ["Xăng RON95", "Dịch vụ rửa xe", "Chiết khấu thương mại"]
    assert d["Dong"][2]["ThanhTien"] == -10_000 and d["Dong"][0]["SoLuong"] == 50.5
    assert d["PhiKhac"] == 200_000 and d["TenPhiKhac"] == "Thuế bảo vệ môi trường"
    assert d["ThueSuatHD"] == "10%; 8%" and len(d["ChiTietThueSuat"]) == 2
    v, notes = invoice_values("vao", d, "x.pdf", "0101234567")
    assert v["ThueSuat"] == "10%; 8%" and v["ChiTietThueSuat"] and v["PhiKhac"] == 200_000
    assert v["GiaTriSauThue"] == 1_408_000 and len(v["_Dong"]) == 3
    assert any("nhiều mức thuế" in n for n in notes)


def test_pdf_and_xml_same_invoice_added_at_different_times_are_merged(company, tmp_path, monkeypatch):
    """XML quét trước, PDF (tên khác) chép vào sau: PDF gắn kèm hóa đơn, không thêm trùng; ngược lại PDF trước,
    XML sau: XML làm tệp chính, PDF thành tệp kèm."""
    from qlhd.core import pdf_invoice
    db = company["kt"].db
    sample_data(db)
    vao = tmp_path / "vao"
    vao.mkdir()
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(vao)})
    _scan(f, "vao")                                                    # lần đầu: thư mục trống
    _xml(vao / "hd401.xml", 401, tien=4_000_000)
    r = _scan(f, "vao")
    ma = r["them"][0]
    text = {"SoHoaDon": "0000401", "KyHieuHD": "C26TMA", "KyHieuMauSo": "1", "MSTNguoiBan": "0400000004",
            "TenNguoiBan": "Cửa hàng Tin học A", "NgayLap": "2026-11-10", "GiaTriTruocThue": 4_000_000,
            "VAT": 400_000, "GiaTriSauThue": 4_400_000, "MSTNguoiMua": "", "TenNguoiMua": "", "ThueSuatHD": "10%",
            "MatHang": "Giấy A4"}
    monkeypatch.setattr(pdf_invoice, "parse_invoice", lambda path: dict(text))
    (vao / "ban in hoa don 401.pdf").write_bytes(b"%PDF-1.4")
    r = _scan(f, "vao")
    assert r["them"] == [] and r["can_xem"] == 0 and r["co_san"] == 1
    inv = db.get("HoaDonDauVao", ma)
    assert inv["TepTuongDoi"] == "hd401.xml" and json.loads(inv["TepKem"]) == ["ban in hoa don 401.pdf"]
    assert {x["DuongDan"]: x["TrangThai"] for x in f.files("vao")}["ban in hoa don 401.pdf"] == F_COMPANION
    # PDF trước, XML sau
    text.update(SoHoaDon="402", GiaTriTruocThue=5_000_000, VAT=500_000, GiaTriSauThue=5_500_000)
    (vao / "scan402.pdf").write_bytes(b"%PDF-1.4")
    r = _scan(f, "vao")
    ma2 = r["them"][0]
    assert db.get("HoaDonDauVao", ma2)["TepTuongDoi"] == "scan402.pdf"
    _xml(vao / "402.xml", 402, tien=5_000_000)
    r = _scan(f, "vao")
    inv = db.get("HoaDonDauVao", ma2)
    assert r["them"] == [] and inv["TepTuongDoi"] == "402.xml" and json.loads(inv["TepKem"]) == ["scan402.pdf"]


def test_gemini_extract_retries_when_cut_and_drops_unsupported_thinking(tmp_path, monkeypatch):
    from qlhd.core.gemini import Gemini, parse_json_reply
    img = tmp_path / "hd.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    g = Gemini("AIza-thu", "gemini-thu-flash")
    bodies = []
    cut = {"candidates": [{"content": {"parts": [{"text": '{"la_hoa_don": true, "dong": [{"ten": "A"}, {"ten": "A"'}]},
                           "finishReason": "MAX_TOKENS"}]}
    ok = {"candidates": [{"content": {"parts": [{"thought": True, "text": "suy nghĩ…"},
                                                {"text": '```json\n{"la_hoa_don": true, "so_hoa_don": "9"}\n```'}]}}]}

    def post(body):
        bodies.append(json.loads(json.dumps(body)))
        if len(bodies) == 1:
            raise GeminiError('Gemini từ chối yêu cầu (400): Unknown name "thinkingConfig"')
        return cut if len(bodies) == 2 else ok
    monkeypatch.setattr(g, "_post", post)
    assert g.extract_invoice(img)["so_hoa_don"] == "9"
    assert "thinkingConfig" in bodies[0]["generationConfig"]
    assert all("thinkingConfig" not in b["generationConfig"] for b in bodies[1:])
    assert "15 dòng" in bodies[2]["contents"][0]["parts"][1]["text"]
    try:
        parse_json_reply(cut)
        raise AssertionError("phải báo bị cắt")
    except GeminiError as e:
        assert e.kind == "dai"


def test_gemini_missing_header_asked_again_and_formats_normalized(tmp_path, monkeypatch):
    from qlhd.core.gemini import EXTRACT_SCHEMA, HEADER_SCHEMA, Gemini
    from qlhd.core.invoice_folder import from_gemini
    # các trường chính phải "required" (không thì Gemini bỏ qua), dòng hàng xếp cuối
    for k in ("so_hoa_don", "ngay_lap", "ten_nguoi_ban", "mst_nguoi_ban", "tong_thanh_toan"):
        assert k in EXTRACT_SCHEMA["required"]
    assert EXTRACT_SCHEMA["propertyOrdering"][-1] == "dong"
    pdf = tmp_path / "hd.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    g = Gemini("AIza-thu", "gemini-thu-flash")
    bodies = []
    first = {"la_hoa_don": True, "so_hoa_don": 1234, "ngay_lap": "", "ten_nguoi_ban": "",
             "dong": [{"ten": "Dịch vụ", "thanh_tien": "1.000.000"}], "tong_thanh_toan": "1.080.000"}
    head = {"la_hoa_don": True, "so_hoa_don": "1234", "ngay_lap": "Ngày 05 tháng 11 năm 2026",
            "ten_nguoi_ban": "Công ty Dịch vụ E", "mst_nguoi_ban": "0900 000 009", "tong_thanh_toan": 1_080_000}

    def post(body):
        bodies.append(body)
        reply = first if len(bodies) == 1 else head
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(reply, ensure_ascii=False)}]}}]}
    monkeypatch.setattr(g, "_post", post)
    r = g.extract_invoice(pdf)
    assert len(bodies) == 2
    assert bodies[1]["generationConfig"]["responseSchema"] == HEADER_SCHEMA
    d = from_gemini(r)
    assert d["SoHoaDon"] == "1234" and d["NgayLap"] == "2026-11-05"
    assert d["TenNguoiBan"] == "Công ty Dịch vụ E" and d["MSTNguoiBan"] == "0900000009"
    assert d["GiaTriSauThue"] == 1_080_000 and d["Dong"][0]["ThanhTien"] == 1_000_000
    assert from_gemini({"ngay_lap": "17/11/2026"})["NgayLap"] == "2026-11-17"
    assert from_gemini({"tong_thanh_toan": "1,234,567.50"})["GiaTriSauThue"] == 1_234_568


def test_gemini_reads_files_in_parallel_and_retries_quota(company, tmp_path, monkeypatch):
    import threading
    import time as _t
    from qlhd.core import invoice_folder as fo
    from qlhd.core import pdf_invoice
    db = company["kt"].db
    sample_data(db)
    root = tmp_path / "vao"
    root.mkdir()
    for i in range(6):
        (root / f"anh{i}.pdf").write_bytes(b"%PDF-1.4 quet")
    monkeypatch.setattr(pdf_invoice, "parse_invoice",
                        lambda p: (_ for _ in ()).throw(pdf_invoice.InvoicePDFError("PDF quét")))
    monkeypatch.setattr(fo, "GEMINI_RETRY_WAIT", 0)
    state = {"now": 0, "max": 0, "quota": 1}
    lock = threading.Lock()

    class Gem:
        def extract_invoice(self, path):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
                hit = state["quota"] > 0 and path.endswith("anh3.pdf")
                if hit:
                    state["quota"] -= 1
            try:
                _t.sleep(0.05)
                if hit:
                    raise GeminiError("Gemini báo hết hạn mức sử dụng, thử lại sau.", "han_muc")
                n = Path(path).stem[-1]
                return {"la_hoa_don": True, "ky_hieu": "C26T", "so_hoa_don": f"7{n}", "ngay_lap": "2026-11-01",
                        "ten_nguoi_ban": "NCC", "mst_nguoi_ban": "0700000007", "tien_truoc_thue": 100_000,
                        "tien_thue": 10_000, "tong_thanh_toan": 110_000, "thue_suat": "10%"}
            finally:
                with lock:
                    state["now"] -= 1
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(root)})
    items = InvoiceFolders.read("vao", f.new_files("vao"), Gem(), force_gemini=True)
    assert state["max"] > 1                                 # gửi song song
    assert len(items) == 6 and all(it.data and not it.error for it in items)   # file bị hết hạn mức đã thử lại
    assert sorted(it.data["SoHoaDon"] for it in items) == [f"7{i}" for i in range(6)]


def test_user_gemini_read_of_review_files_adds_new_and_links_existing(company, tmp_path, monkeypatch):
    from qlhd.core import pdf_invoice
    db = company["kt"].db
    sample_data(db)
    root = tmp_path / "vao"
    root.mkdir()
    (root / "moi.pdf").write_bytes(b"%PDF-1.4 moi")
    (root / "da_co.pdf").write_bytes(b"%PDF-1.4 cu")
    monkeypatch.setattr(pdf_invoice, "parse_invoice",
                        lambda p: (_ for _ in ()).throw(pdf_invoice.InvoicePDFError("PDF quét")))
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(root)})
    # lần quét đầu, Gemini chưa cài: cả hai file vào Cần xem
    f.process("vao", InvoiceFolders.read("vao", f.new_files("vao"), None))
    assert len(f.files("vao", F_REVIEW)) == 2
    old = db.one("SELECT * FROM HoaDonDauVao WHERE DaXoa = 0 AND MaThueNCC != '' LIMIT 1")
    reads = {"moi.pdf": {"la_hoa_don": True, "ky_hieu": "C26TMM", "so_hoa_don": "4321", "ngay_lap": "2026-11-12",
                         "ten_nguoi_ban": "Nhà cung cấp mới", "mst_nguoi_ban": "0500000005", "tien_truoc_thue": 200_000,
                         "tien_thue": 20_000, "tong_thanh_toan": 220_000, "thue_suat": "10%"},
             "da_co.pdf": {"la_hoa_don": True, "ky_hieu": old["KyHieuHD"], "so_hoa_don": old["SoHoaDonGoc"],
                           "ngay_lap": old["NgayNhanHD"], "ten_nguoi_ban": old["TenNhaCungCap"],
                           "mst_nguoi_ban": old["MaThueNCC"], "tien_truoc_thue": old["GiaTriTruocThue"],
                           "tien_thue": old["VAT"], "tong_thanh_toan": old["GiaTriSauThue"]}}

    class Gem:
        def extract_invoice(self, path):
            return dict(reads[Path(path).name])
    rows = f.files("vao", F_REVIEW)
    files = [(r["DuongDan"], str(f.full_path("vao", r["DuongDan"]))) for r in rows]
    items = InvoiceFolders.read("vao", files, Gem(), cache=f.cache("vao", [x for x, _ in files]), force_gemini=True)
    r = f.process("vao", items, user_asked=True)
    assert len(r["them"]) == 1 and r["co_san"] == 1 and r["can_xem"] == 0
    new = db.get("HoaDonDauVao", r["them"][0])
    assert new["SoHoaDonGoc"] == "4321" and new["TepTuongDoi"] == "moi.pdf"
    linked = db.get("HoaDonDauVao", old["Ma"])
    assert linked["TepTuongDoi"] == "da_co.pdf"                     # hóa đơn cũ chỉ ghi nhận nơi chứa file
    assert linked["GiaTriSauThue"] == old["GiaTriSauThue"]
    assert not f.files("vao", F_REVIEW)


def test_gemini_discount_amounts_checked_fixed_or_sent_to_review(tmp_path, monkeypatch):
    from qlhd.core.gemini import Gemini, amounts_mismatch
    from qlhd.core.invoice_folder import from_gemini
    base = {"la_hoa_don": True, "so_hoa_don": "12", "ngay_lap": "2026-11-01", "ten_nguoi_ban": "Siêu thị F",
            "mst_nguoi_ban": "0300000003"}
    # Gemini lấy tiền hàng trước giảm giá (1.000.000), giảm 100.000: tự trừ chiết khấu cho khớp tổng
    d = from_gemini(dict(base, tien_truoc_thue=1_000_000, tien_chiet_khau=100_000, tien_thue=72_000,
                         tong_thanh_toan=972_000))
    assert d["GiaTriTruocThue"] == 900_000 and d["GiaTriSauThue"] == 972_000 and not d.get("LechTien")
    # vẫn không khớp: ghi lý do để nhân viên xem
    d = from_gemini(dict(base, tien_truoc_thue=1_000_000, tien_thue=80_000, tong_thanh_toan=950_000))
    assert "chưa khớp" in d["LechTien"]
    assert amounts_mismatch({"tien_truoc_thue": 900_000, "tien_thue": 72_000, "tong_thanh_toan": 972_000}) is None
    # lần đọc đầu không khớp → hỏi lại phần tổng tiền, lần sau khớp thì lấy
    pdf = tmp_path / "hd.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    g = Gemini("AIza-thu", "gemini-thu-flash")
    replies = [dict(base, tien_truoc_thue=1_000_000, tien_thue=72_000, tong_thanh_toan=972_000),
               dict(base, tien_chiet_khau=100_000, tien_truoc_thue=900_000, tien_thue=72_000, tong_thanh_toan=972_000)]
    monkeypatch.setattr(g, "_post", lambda body: {"candidates": [{"content": {"parts": [
        {"text": json.dumps(replies.pop(0), ensure_ascii=False)}]}}]})
    r = g.extract_invoice(pdf)
    assert r["tien_truoc_thue"] == 900_000 and r["tien_chiet_khau"] == 100_000 and not replies


def test_skipped_and_kept_files_not_reported_again_after_rescan(company, tmp_path):
    """Nhân viên đã rà soát: bỏ qua file đọc nhầm, giữ số liệu phần mềm với file số liệu khác → quét lại toàn bộ,
    hay file bị ổ đám mây đổi giờ sửa, cũng không báo lại."""
    import os
    from qlhd.core import invoice_folder as fo
    from qlhd.core.contracts import ContractService
    db = company["kt"].db
    sample_data(db)
    ContractService(db).save_in_invoice({"SoHoaDonGoc": "202", "TenNhaCungCap": "Cửa hàng Tin học A",
                                         "MaThueNCC": "0400000004", "NgayNhanHD": "2026-11-10",
                                         "GiaTriTruocThue": 900_000, "VAT": 90_000})
    vao = tmp_path / "vao"
    _xml(vao / "202.xml", 202)                                        # số liệu khác hóa đơn đã có
    _xml(vao / "doc_nham.xml", 555, mst="")                           # thiếu MST người bán → Cần xem
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(vao)})
    _scan(f, "vao")
    st = {x["DuongDan"]: x["TrangThai"] for x in f.files("vao")}
    assert st["202.xml"] == fo.F_CONFLICT and st["doc_nham.xml"] == F_REVIEW
    f.set_status("vao", ["202.xml"], F_EXISTS, note=fo.NOTE_KEEP)       # như bấm "Bỏ qua" ở giao diện
    f.set_status("vao", ["doc_nham.xml"], fo.F_SKIP, note=fo.NOTE_SKIP)
    assert f.review_count() == 0
    for _ in range(2):
        again = f.recheck_files("vao", everything=True)
        changed = f.new_files("vao")
        items = InvoiceFolders.read("vao", sorted(again + changed),
                                    cache=f.cache("vao", [x for x, _ in again], fresh=[x for x, _ in changed]))
        f.process("vao", items)
        assert f.review_count() == 0
        # ổ đám mây đổi giờ sửa file: phần mềm đọc lại nhưng vẫn nhớ quyết định
        for name in ("202.xml", "doc_nham.xml"):
            os.utime(vao / name, (1_700_000_000, 1_700_000_000))
    st = {x["DuongDan"]: (x["TrangThai"], x["GhiChu"]) for x in f.files("vao")}
    assert st["202.xml"] == (F_EXISTS, fo.NOTE_KEEP) and st["doc_nham.xml"] == (fo.F_SKIP, fo.NOTE_SKIP)


def test_pdf_text_amounts_skip_column_numbers_and_rates():
    from qlhd.core.pdf_invoice import parse_text
    text = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Ký hiệu: 1C26TAB Số: 0000240
Ngày 05 tháng 01 năm 2026
Đơn vị bán hàng: Công ty G
Mã số thuế: 0101234567
STT Tên hàng hóa Đơn vị tính Số lượng Đơn giá Thành tiền Thuế suất Tiền thuế GTGT 5 = 4 x 3
1 Dịch vụ A lần 1 405.000 405.000 8% 32.400
Thuế suất GTGT: 8%
Cộng tiền hàng: 405.000
Tiền thuế GTGT: 32.400
Tổng tiền thanh toán: 437.400
"""
    d = parse_text(text)
    assert (d["GiaTriTruocThue"], d["VAT"], d["GiaTriSauThue"]) == (405_000, 32_400, 437_400)
    assert not d["ThieuTruong"]
    # chỉ có số cột sau nhãn tiền thuế: suy ra thuế = tổng - tiền hàng
    d = parse_text(text.replace("Tiền thuế GTGT: 32.400\n", ""))
    assert d["VAT"] == 32_400 and not d["ThieuTruong"]


def test_misread_amounts_not_reported_as_conflict(company):
    db = company["kt"].db
    sample_data(db)
    f = InvoiceFolders(db)
    inv = {"NgayNhanHD": "2026-01-05", "GiaTriTruocThue": 3_268_250, "VAT": 261_460, "GiaTriSauThue": 3_529_710}
    # Gemini / lớp chữ đọc nhầm tiền trước thuế (cộng không khớp tổng), phần mềm khớp tổng: không báo lệch
    assert f.diff("vao", inv, {"GiaTriTruocThue": 3_711_750, "VAT": 261_460, "GiaTriSauThue": 3_529_710}) == []
    assert f.diff("vao", dict(inv, VAT=261_460), {"GiaTriTruocThue": 3_268_250, "VAT": 5,
                                                  "GiaTriSauThue": 3_529_710}) == []
    # file cộng khớp mà khác phần mềm: vẫn báo
    assert f.diff("vao", inv, {"GiaTriTruocThue": 3_000_000, "VAT": 240_000, "GiaTriSauThue": 3_240_000})


def test_gemini_reread_of_conflict_pdf_clears_misread(company, tmp_path, monkeypatch):
    from qlhd.core import pdf_invoice
    from qlhd.core.contracts import ContractService
    from qlhd.core.invoice_folder import F_CONFLICT
    db = company["kt"].db
    sample_data(db)
    ma = ContractService(db).save_in_invoice({"SoHoaDonGoc": "487863", "KyHieuHD": "C26MGG", "TenNhaCungCap": "NCC H",
                                              "MaThueNCC": "0600000006", "NgayNhanHD": "2026-01-10",
                                              "GiaTriTruocThue": 3_268_250, "VAT": 261_460})
    root = tmp_path / "vao"
    root.mkdir()
    (root / "487863.pdf").write_bytes(b"%PDF-1.4")
    wrong = {"SoHoaDon": "487863", "KyHieuHD": "C26MGG", "KyHieuMauSo": "1", "MSTNguoiBan": "0600000006",
             "TenNguoiBan": "NCC H", "NgayLap": "2026-01-10", "GiaTriTruocThue": 3_000_000, "VAT": 240_000,
             "GiaTriSauThue": 3_240_000, "MSTNguoiMua": "", "TenNguoiMua": "", "ThueSuatHD": "8%", "MatHang": "",
             "ThieuTruong": []}
    monkeypatch.setattr(pdf_invoice, "parse_invoice", lambda p: dict(wrong))
    f = InvoiceFolders(db)
    f.save_settings({"vao": str(root)})
    f.process("vao", InvoiceFolders.read("vao", f.new_files("vao"), None))
    assert f.files("vao")[0]["TrangThai"] == F_CONFLICT
    gem = FakeGemini(extract={"la_hoa_don": True, "ky_hieu": "C26MGG", "so_hoa_don": "487863", "ngay_lap": "2026-01-10",
                              "ten_nguoi_ban": "NCC H", "mst_nguoi_ban": "0600000006", "tien_truoc_thue": 3_268_250,
                              "tien_thue": 261_460, "tong_thanh_toan": 3_529_710, "thue_suat": "8%"})
    files = [(r["DuongDan"], str(f.full_path("vao", r["DuongDan"]))) for r in f.files("vao")]
    items = InvoiceFolders.read("vao", files, gem, cache=f.cache("vao", [x for x, _ in files]), force_gemini=True)
    f.process("vao", items, user_asked=True)
    assert gem.calls and f.files("vao")[0]["TrangThai"] == F_EXISTS
    assert db.get("HoaDonDauVao", ma)["GiaTriTruocThue"] == 3_268_250
