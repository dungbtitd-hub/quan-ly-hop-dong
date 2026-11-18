"""Xuất dữ liệu tùy chọn: mọi loại dữ liệu phần mềm đang quản lý đều xuất được ra Excel, người xuất tự chọn cột
(không cố định), lọc theo khoảng ngày. Dùng chung khung Report / export_xlsx của báo cáo."""
from __future__ import annotations

from dataclasses import dataclass, field

from .reports import MONEY, TEXT, Report, fmt_date

DATE, INT_ = "date", "int"


@dataclass
class Dataset:
    key: str
    label: str
    sql: str                                   # trả về các cột trong `columns` (đặt tên cột theo khóa)
    columns: list[tuple[str, str, str]]        # (khóa, tiêu đề, kiểu)
    date_key: str | None = None                # cột ngày để lọc từ – đến
    sum_keys: list[str] = field(default_factory=list)
    finance: bool = False                      # dữ liệu thu chi (chỉ khi đã mở khóa Thu chi)
    extra: object = None                       # hàm(db, rows) bổ sung cột tính toán
    quyen: str = "HopDong"                     # chức năng cần có quyền xem


def _contract_extra(db, rows):
    from .contracts import ContractService
    cs = ContractService(db)
    for r in rows:
        s = cs.summary(r["Ma"])
        r.update(DaXuatHD=s["DaXuatHD"], ConPhaiXuat=s["ConPhaiXuat"], DaThanhToan=s["DaThanhToan"],
                 ConPhaiThu=s["ConPhaiThu"], TamUngConLai=s["TamUngConLai"],
                 TinhTrangThanhToan=s["TinhTrangThanhToan"] if s["DaXuatHD"] else "",
                 TinhTrangXuatHD=s["TinhTrangXuatHD"])


def _payment_extra(db, rows):
    total: dict[str, int] = {}
    for r in sorted(rows, key=lambda x: (x["NgayThanhToan"] or "", x["Ma"])):
        total[r["MaHopDong"]] = total.get(r["MaHopDong"], 0) + int(r["SoTien"] or 0)
        r["LuyKe"] = total[r["MaHopDong"]]


def _out_invoice_extra(db, rows):
    paid = {r["MaHDDauRa"]: r["s"] for r in db.query(
        "SELECT MaHDDauRa, SUM(SoTien) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND MaHDDauRa IS NOT NULL"
        " GROUP BY MaHDDauRa")}
    dates: dict[str, list] = {}
    for p in db.query("SELECT MaHDDauRa, NgayThanhToan, SoThamChieuSK FROM ThanhToanHopDong WHERE DaXoa = 0"
                      " AND MaHDDauRa IS NOT NULL ORDER BY NgayThanhToan"):
        dates.setdefault(p["MaHDDauRa"], []).append(p)
    for r in rows:
        da = int(paid.get(r["Ma"]) or 0)
        r["DaThu"] = da
        r["ConPhaiThu"] = 0 if r["TinhChatHD"] == "Đã hủy" else int(r["GiaTriSauThue"] or 0) - da
        ps = dates.get(r["Ma"], [])
        r["NgayThu"] = ", ".join(fmt_date(p["NgayThanhToan"]) for p in ps)
        r["SoSaoKe"] = ", ".join(p["SoThamChieuSK"] for p in ps if p["SoThamChieuSK"])


DATASETS = [
    Dataset("hop_dong", "Hợp đồng", """
        SELECT h.*, l.TenLoaiHD FROM HopDong h LEFT JOIN LoaiHopDong l ON l.Ma = h.MaLoaiHD
        WHERE h.DaXoa = 0 ORDER BY h.NgayKy, h.SoHopDong""", [
        ("SoHopDong", "Số hợp đồng", TEXT), ("TenHopDong", "Tên hợp đồng", TEXT), ("ChieuHopDong", "Chiều", TEXT),
        ("TenLoaiHD", "Loại hợp đồng", TEXT), ("TenDoiTac", "Đối tác", TEXT), ("MaThueDoiTac", "MST đối tác", TEXT),
        ("NgayKy", "Ngày ký", DATE), ("NgayBatDauDuKien", "Ngày bắt đầu", DATE),
        ("ThoiGianThucHien", "Thời gian thực hiện", INT_), ("DonViThoiGian", "Đơn vị thời gian", TEXT),
        ("NgayKetThucDuKien", "Ngày kết thúc dự kiến", DATE), ("TinhTrangHopDong", "Tình trạng", TEXT),
        ("GiaTriTruocThue", "Giá trị trước thuế", MONEY), ("ThueSuat", "Thuế suất", TEXT), ("VAT", "VAT", MONEY),
        ("GiaTriTongSauThue", "Giá trị sau thuế", MONEY), ("DaXuatHD", "Đã xuất hóa đơn", MONEY),
        ("ConPhaiXuat", "Còn phải xuất", MONEY), ("TinhTrangXuatHD", "Tình trạng xuất hóa đơn", TEXT),
        ("DaThanhToan", "Đã thanh toán", MONEY), ("ConPhaiThu", "Còn phải thu", MONEY),
        ("TinhTrangThanhToan", "Tình trạng thanh toán", TEXT), ("TamUngConLai", "Tạm ứng chưa khấu trừ", MONEY),
        ("NhacTruocNgay", "Nhắc trước (ngày)", INT_), ("GhiChu", "Ghi chú", TEXT),
        ("HoSoDaLay", "Hồ sơ đã lấy về", TEXT)], "NgayKy",
        ["GiaTriTruocThue", "VAT", "GiaTriTongSauThue", "DaXuatHD", "ConPhaiXuat", "DaThanhToan", "ConPhaiThu",
         "TamUngConLai"], extra=_contract_extra),
    Dataset("thanh_toan", "Thanh toán (tiền về)", """
        SELECT t.*, h.SoHopDong, h.TenHopDong, h.TenDoiTac, d.KyHieuHD, d.SoHoaDon
        FROM ThanhToanHopDong t JOIN HopDong h ON h.Ma = t.MaHopDong LEFT JOIN HoaDonDauRa d ON d.Ma = t.MaHDDauRa
        WHERE t.DaXoa = 0 AND h.DaXoa = 0 ORDER BY t.NgayThanhToan, h.SoHopDong""", [
        ("SoHopDong", "Số hợp đồng", TEXT), ("TenHopDong", "Tên hợp đồng", TEXT), ("TenDoiTac", "Đối tác", TEXT),
        ("NgayThanhToan", "Ngày tiền về", DATE), ("SoTien", "Số tiền", MONEY), ("HinhThuc", "Hình thức", TEXT),
        ("SoThamChieuSK", "Số tham chiếu sao kê", TEXT), ("KyHieuHD", "Ký hiệu hóa đơn", TEXT),
        ("SoHoaDon", "Số hóa đơn", TEXT), ("LuyKe", "Lũy kế đã thu của HĐ", MONEY), ("GhiChu", "Ghi chú", TEXT)],
        "NgayThanhToan", ["SoTien"], extra=_payment_extra, quyen="HoaDonDauRa"),
    Dataset("hoa_don_ra", "Hóa đơn đầu ra", """
        SELECT i.*, h.SoHopDong, h.TenHopDong FROM HoaDonDauRa i JOIN HopDong h ON h.Ma = i.MaHopDong
        WHERE i.DaXoa = 0 AND h.DaXoa = 0 ORDER BY i.NgayXuatHD, i.SoHoaDon""", [
        ("KyHieuMauSo", "Ký hiệu mẫu số", TEXT), ("KyHieuHD", "Ký hiệu", TEXT), ("SoHoaDon", "Số hóa đơn", TEXT),
        ("NgayXuatHD", "Ngày lập", DATE), ("SoHopDong", "Số hợp đồng", TEXT), ("TenHopDong", "Tên hợp đồng", TEXT),
        ("TenNguoiMua", "Người mua", TEXT), ("MSTNguoiMua", "MST người mua", TEXT), ("MatHang", "Mặt hàng", TEXT),
        ("GiaTriTruocThue", "Trước thuế", MONEY), ("ThueSuat", "Thuế suất", TEXT), ("VAT", "VAT", MONEY),
        ("GiaTriSauThue", "Sau thuế", MONEY), ("DaThu", "Đã thu", MONEY), ("ConPhaiThu", "Còn phải thu", MONEY),
        ("NgayThu", "Ngày thanh toán", TEXT), ("SoSaoKe", "Số tham chiếu sao kê", TEXT),
        ("TinhChatHD", "Tính chất", TEXT), ("DotThanhToan", "Đợt thanh toán", TEXT), ("GhiChuHDDR", "Ghi chú", TEXT)],
        "NgayXuatHD", ["GiaTriTruocThue", "VAT", "GiaTriSauThue", "DaThu", "ConPhaiThu"], extra=_out_invoice_extra, quyen="HoaDonDauRa"),
    Dataset("hoa_don_vao", "Hóa đơn đầu vào", """
        SELECT i.*, h.SoHopDong FROM HoaDonDauVao i LEFT JOIN HopDong h ON h.Ma = i.MaHopDong
        WHERE i.DaXoa = 0 ORDER BY i.NgayNhanHD, i.SoHoaDonGoc""", [
        ("KyHieuMauSo", "Ký hiệu mẫu số", TEXT), ("KyHieuHD", "Ký hiệu", TEXT), ("SoHoaDonGoc", "Số hóa đơn", TEXT),
        ("NgayNhanHD", "Ngày hóa đơn", DATE), ("TenNhaCungCap", "Người bán", TEXT), ("MaThueNCC", "MST người bán", TEXT),
        ("MatHang", "Mặt hàng", TEXT), ("GiaTriTruocThue", "Trước thuế", MONEY), ("ThueSuat", "Thuế suất", TEXT),
        ("VAT", "VAT", MONEY), ("PhiKhac", "Phí khác", MONEY), ("GiaTriSauThue", "Sau thuế", MONEY),
        ("NhomThue", "Nhóm thuế", TEXT), ("SoHopDong", "Số hợp đồng", TEXT), ("NguoiMuaHang", "Người mua hàng", TEXT),
        ("SoThamChieuSK", "Số tham chiếu sao kê", TEXT), ("GhiChuHDDV", "Ghi chú", TEXT)],
        "NgayNhanHD", ["GiaTriTruocThue", "VAT", "PhiKhac", "GiaTriSauThue"], quyen="HoaDonDauVao"),
    Dataset("dong_hddv", "Dòng hàng hóa đơn đầu vào", """
        SELECT d.*, i.SoHoaDonGoc, i.NgayNhanHD, i.TenNhaCungCap FROM DongHoaDonDauVao d
        JOIN HoaDonDauVao i ON i.Ma = d.MaHoaDon WHERE d.DaXoa = 0 AND i.DaXoa = 0
        ORDER BY i.NgayNhanHD, i.SoHoaDonGoc, d.STT""", [
        ("NgayNhanHD", "Ngày hóa đơn", DATE), ("SoHoaDonGoc", "Số hóa đơn", TEXT), ("TenNhaCungCap", "Người bán", TEXT),
        ("STT", "STT dòng", INT_), ("TenHang", "Tên hàng", TEXT), ("DVT", "ĐVT", TEXT), ("SoLuong", "Số lượng", TEXT),
        ("DonGia", "Đơn giá", MONEY), ("ThanhTien", "Thành tiền", MONEY), ("ThueSuat", "Thuế suất", TEXT),
        ("PhanLoai", "Phân loại", TEXT), ("MaNhomDM", "Nhóm danh mục", TEXT), ("SoThang", "Số tháng", INT_),
        ("GhiChu", "Ghi chú", TEXT)], "NgayNhanHD", ["ThanhTien"], quyen="HoaDonDauVao"),
    Dataset("cong_viec", "Công việc", """
        SELECT c.*, h.SoHopDong, h.TenHopDong FROM CongViec c JOIN HopDong h ON h.Ma = c.MaHopDong
        WHERE c.DaXoa = 0 AND h.DaXoa = 0 ORDER BY h.SoHopDong, c.NgayDuKienHT""", [
        ("SoHopDong", "Số hợp đồng", TEXT), ("TenHopDong", "Tên hợp đồng", TEXT), ("TenNoiDungCV", "Công việc", TEXT),
        ("NgayDuKienHT", "Hạn hoàn thành", DATE), ("TienDoPT", "Tiến độ %", INT_), ("TinhTrangCV", "Tình trạng", TEXT),
        ("GhiChuCV", "Ghi chú", TEXT)], "NgayDuKienHT"),
    Dataset("ban_giao", "Lịch sử bàn giao", """
        SELECT b.*, c.TenNoiDungCV, h.SoHopDong FROM LichSuBanGiao b JOIN CongViec c ON c.Ma = b.MaCongViec
        JOIN HopDong h ON h.Ma = c.MaHopDong WHERE b.DaXoa = 0 ORDER BY b.NgayBanGiao""", [
        ("SoHopDong", "Số hợp đồng", TEXT), ("TenNoiDungCV", "Công việc", TEXT), ("NgayBanGiao", "Ngày bàn giao", DATE),
        ("NoiDungTL", "Nội dung tài liệu", TEXT), ("PhanHoiCDT", "Phản hồi CĐT", TEXT)], "NgayBanGiao"),
    Dataset("nghiem_thu", "Lịch sử nghiệm thu", """
        SELECT n.*, c.TenNoiDungCV, h.SoHopDong FROM LichSuNghiemThu n JOIN CongViec c ON c.Ma = n.MaCongViec
        JOIN HopDong h ON h.Ma = c.MaHopDong WHERE n.DaXoa = 0 ORDER BY n.NgayNghiemThu""", [
        ("SoHopDong", "Số hợp đồng", TEXT), ("TenNoiDungCV", "Công việc", TEXT),
        ("NgayNghiemThu", "Ngày nghiệm thu", DATE), ("KetQuaNT", "Kết quả", TEXT), ("GhiChuNT", "Ghi chú", TEXT)],
        "NgayNghiemThu"),
    Dataset("tai_san", "Tài sản cố định, CCDC", """
        SELECT * FROM TaiSan WHERE DaXoa = 0 ORDER BY LoaiSo, NgayGhiTang, ThuTu""", [
        ("LoaiSo", "Sổ", TEXT), ("MaTS", "Mã", TEXT), ("TenTS", "Tên", TEXT), ("PhanLoai", "Phân loại", TEXT),
        ("LoaiTS", "Loại TSCĐ", TEXT), ("DonViSuDung", "Đơn vị sử dụng", TEXT), ("NgayGhiTang", "Ngày ghi tăng", DATE),
        ("SoCTGhiTang", "Số CT ghi tăng", TEXT), ("NgayBatDauKH", "Ngày bắt đầu KH", DATE), ("SoKy", "Số kỳ", INT_),
        ("DVT", "ĐVT", TEXT), ("SoLuong", "Số lượng", TEXT), ("NguyenGia", "Nguyên giá", MONEY),
        ("GiaTriTinhKH", "Giá trị tính KH", MONEY), ("TKNguyenGia", "TK nguyên giá", TEXT),
        ("TKKhauHao", "TK khấu hao / phân bổ", TEXT), ("NgayGhiGiam", "Ngày ghi giảm", DATE),
        ("LyDoGhiGiam", "Lý do ghi giảm", TEXT), ("GhiChu", "Ghi chú", TEXT)], "NgayGhiTang",
        ["NguyenGia", "GiaTriTinhKH"], quyen="TaiSan"),
    Dataset("doi_tac", "Đối tác", "SELECT * FROM DoiTac WHERE DaXoa = 0 ORDER BY TenDoiTac", [
        ("TenDoiTac", "Tên đối tác", TEXT), ("MaSoThue", "Mã số thuế", TEXT), ("VaiTroDoiTac", "Vai trò", TEXT),
        ("DiaChi", "Địa chỉ", TEXT), ("NguoiLienHe", "Người liên hệ", TEXT), ("DienThoai", "Điện thoại", TEXT)]),
    Dataset("loai_hd", "Loại hợp đồng", "SELECT * FROM LoaiHopDong WHERE DaXoa = 0 ORDER BY TenLoaiHD",
            [("TenLoaiHD", "Loại hợp đồng", TEXT)]),
    Dataset("thu_chi", "Phiếu thu chi", """
        SELECT p.*, k.TenKhoanMuc, q.TenQuy FROM PhieuThuChi p LEFT JOIN KhoanMuc k ON k.Ma = p.MaKhoanMuc
        LEFT JOIN QuyTien q ON q.Ma = p.MaQuy WHERE p.DaXoa = 0 ORDER BY p.NgayPhatSinh, p.SoPhieu""", [
        ("SoPhieu", "Số phiếu", TEXT), ("NgayPhatSinh", "Ngày", DATE), ("LoaiThuChi", "Thu / Chi", TEXT),
        ("PhanLoaiChi", "Phân loại chi", TEXT), ("TenDuAn", "Dự án", TEXT), ("TenKhoanMuc", "Khoản mục", TEXT),
        ("TenQuy", "Quỹ", TEXT), ("SoTien", "Số tiền", MONEY), ("NoiDung", "Nội dung", TEXT),
        ("DoiTuong", "Đối tượng", TEXT), ("ThamChieu", "Tham chiếu", TEXT), ("NguoiTao", "Người tạo", TEXT)],
        "NgayPhatSinh", ["SoTien"], finance=True),
]
BY_KEY = {d.key: d for d in DATASETS}


def build(db, key: str, columns: list[str] | None = None, tu: str | None = None, den: str | None = None,
          fdb=None) -> Report:
    """Bảng dữ liệu `key` với các cột đã chọn (giữ thứ tự gốc), lọc theo ngày nếu có. Thu chi cần `fdb`."""
    ds = BY_KEY[key]
    src = fdb if ds.finance else db
    if src is None:
        raise ValueError("Cần mở khóa Thu chi để xuất phiếu thu chi.")
    rows = [dict(r) for r in src.query(ds.sql)]
    if ds.date_key and (tu or den):
        rows = [r for r in rows if r.get(ds.date_key)
                and (not tu or r[ds.date_key][:10] >= tu) and (not den or r[ds.date_key][:10] <= den)]
    if ds.extra:
        ds.extra(db, rows)
    chosen = set(columns or [c[0] for c in ds.columns])
    cols = [c for c in ds.columns if c[0] in chosen]
    if not cols:
        raise ValueError("Chọn ít nhất một cột để xuất.")
    total = None
    sums = [k for k in ds.sum_keys if k in chosen]
    if sums and rows:
        total = {k: sum(int(r.get(k) or 0) for r in rows) for k in sums}
        total[cols[0][0]] = total.get(cols[0][0]) or f"TỔNG CỘNG ({len(rows)} dòng)"
    period = ""
    if tu or den:
        period = f"Từ {fmt_date(tu) if tu else 'đầu'} đến {fmt_date(den) if den else 'nay'}"
    return Report(f"Danh sách {ds.label.lower()}", cols, rows, total,
                  subtitle=period or "Toàn bộ dữ liệu", sheet=ds.label[:31])


def select_columns(reports: list[Report], keys: set[str]) -> list[Report]:
    """Báo cáo chỉ giữ các cột đã chọn (cột đầu luôn giữ vì mang nhãn nhóm / dòng tổng)."""
    out = []
    for rep in reports:
        cols = [c for i, c in enumerate(rep.columns) if i == 0 or c[0] in keys]
        out.append(Report(rep.title, cols, rep.rows, rep.total, rep.subtitle, rep.sheet))
    return out
