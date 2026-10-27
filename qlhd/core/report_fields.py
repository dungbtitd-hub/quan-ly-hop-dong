"""Thêm trường dữ liệu khi xuất báo cáo: ngoài các cột có sẵn của từng ngữ cảnh, người xuất chọn thêm trường lấy từ
hợp đồng, hóa đơn đầu ra / đầu vào, thanh toán của từng dòng (ngày thanh toán, số tham chiếu sao kê, ngày ký…).

Dòng báo cáo mang khóa ẩn cho biết nó là gì: `_tt` thanh toán, `_hdr` hóa đơn đầu ra, `_hdv` hóa đơn đầu vào,
`_cv` công việc, `_hd` hợp đồng (dòng hóa đơn, thanh toán cũng mang `_hd` của hợp đồng). Dòng tổng hợp (nhóm, cộng,
gộp theo loại / chủ đầu tư) không có khóa nên để trống."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import duration_text
from .reports import INT, MONEY, TEXT, Report, fmt_date, fmt_money

DATE = "date"
SEP = "; "


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str


TT, HD, HDR, HDV = "Thanh toán", "Hợp đồng", "Hóa đơn đầu ra", "Hóa đơn đầu vào"
GROUPS = (TT, HD, HDR, HDV)

FIELDS = [
    Field("x_tt_ngay", "Ngày thanh toán", TT, TEXT),
    Field("x_tt_ref", "Số tham chiếu sao kê", TT, TEXT),
    Field("x_tt_hinh_thuc", "Hình thức thanh toán", TT, TEXT),
    Field("x_tt_tien", "Số tiền từng lần", TT, TEXT),
    Field("x_tt_so_lan", "Số lần thanh toán", TT, INT),
    Field("x_tt_tong", "Tổng đã thanh toán", TT, MONEY),
    Field("x_tt_hoa_don", "Hóa đơn của lần thanh toán", TT, TEXT),
    Field("x_tt_ghi_chu", "Ghi chú thanh toán", TT, TEXT),
    Field("x_hd_so", "Số hợp đồng", HD, TEXT),
    Field("x_hd_ten", "Tên hợp đồng", HD, TEXT),
    Field("x_hd_chieu", "Chiều hợp đồng", HD, TEXT),
    Field("x_hd_loai", "Loại hợp đồng", HD, TEXT),
    Field("x_hd_doi_tac", "Đối tác", HD, TEXT),
    Field("x_hd_mst", "MST đối tác", HD, TEXT),
    Field("x_hd_dia_chi", "Địa chỉ đối tác", HD, TEXT),
    Field("x_hd_lien_he", "Người liên hệ", HD, TEXT),
    Field("x_hd_dien_thoai", "Điện thoại", HD, TEXT),
    Field("x_hd_ngay_ky", "Ngày ký", HD, DATE),
    Field("x_hd_bat_dau", "Ngày bắt đầu", HD, DATE),
    Field("x_hd_ket_thuc", "Ngày kết thúc dự kiến", HD, DATE),
    Field("x_hd_thoi_gian", "Thời gian thực hiện", HD, TEXT),
    Field("x_hd_tinh_trang", "Tình trạng hợp đồng", HD, TEXT),
    Field("x_hd_truoc_thue", "Giá trị HĐ trước thuế", HD, MONEY),
    Field("x_hd_vat", "VAT hợp đồng", HD, MONEY),
    Field("x_hd_gia_tri", "Giá trị HĐ sau thuế", HD, MONEY),
    Field("x_hd_ho_so", "Hồ sơ đã lấy", HD, TEXT),
    Field("x_hd_ghi_chu", "Ghi chú hợp đồng", HD, TEXT),
    Field("x_hdr_mau_so", "Mẫu số", HDR, TEXT),
    Field("x_hdr_ky_hieu", "Ký hiệu", HDR, TEXT),
    Field("x_hdr_so", "Số hóa đơn", HDR, TEXT),
    Field("x_hdr_ngay", "Ngày lập hóa đơn", HDR, DATE),
    Field("x_hdr_nguoi_mua", "Tên người mua", HDR, TEXT),
    Field("x_hdr_mst", "MST người mua", HDR, TEXT),
    Field("x_hdr_mat_hang", "Mặt hàng", HDR, TEXT),
    Field("x_hdr_thue_suat", "Thuế suất", HDR, TEXT),
    Field("x_hdr_truoc_thue", "Trước thuế", HDR, MONEY),
    Field("x_hdr_vat", "VAT", HDR, MONEY),
    Field("x_hdr_sau_thue", "Sau thuế", HDR, MONEY),
    Field("x_hdr_tinh_chat", "Tính chất hóa đơn", HDR, TEXT),
    Field("x_hdr_dot", "Đợt thanh toán", HDR, TEXT),
    Field("x_hdr_da_thu", "Đã thu của hóa đơn", HDR, MONEY),
    Field("x_hdr_con_thu", "Còn phải thu của hóa đơn", HDR, MONEY),
    Field("x_hdr_ghi_chu", "Ghi chú hóa đơn", HDR, TEXT),
    Field("x_hdv_ky_hieu", "Ký hiệu", HDV, TEXT),
    Field("x_hdv_so", "Số hóa đơn", HDV, TEXT),
    Field("x_hdv_ngay", "Ngày hóa đơn", HDV, DATE),
    Field("x_hdv_ncc", "Tên người bán", HDV, TEXT),
    Field("x_hdv_mst", "MST người bán", HDV, TEXT),
    Field("x_hdv_mat_hang", "Mặt hàng", HDV, TEXT),
    Field("x_hdv_thue_suat", "Thuế suất", HDV, TEXT),
    Field("x_hdv_truoc_thue", "Trước thuế", HDV, MONEY),
    Field("x_hdv_vat", "VAT", HDV, MONEY),
    Field("x_hdv_sau_thue", "Sau thuế", HDV, MONEY),
    Field("x_hdv_nhom_thue", "Nhóm thuế", HDV, TEXT),
    Field("x_hdv_nguoi_mua", "Người mua hàng", HDV, TEXT),
    Field("x_hdv_ref", "Số tham chiếu sao kê", HDV, TEXT),
    Field("x_hdv_ghi_chu", "Ghi chú hóa đơn", HDV, TEXT),
]
BY_KEY = {f.key: f for f in FIELDS}

_HD_COLS = {"x_hd_so": "SoHopDong", "x_hd_ten": "TenHopDong", "x_hd_chieu": "ChieuHopDong",
            "x_hd_doi_tac": "TenDoiTac", "x_hd_mst": "MaThueDoiTac", "x_hd_ngay_ky": "NgayKy",
            "x_hd_bat_dau": "NgayBatDauDuKien", "x_hd_ket_thuc": "NgayKetThucDuKien",
            "x_hd_tinh_trang": "TinhTrangHopDong", "x_hd_truoc_thue": "GiaTriTruocThue", "x_hd_vat": "VAT",
            "x_hd_gia_tri": "GiaTriTongSauThue", "x_hd_ho_so": "HoSoDaLay", "x_hd_ghi_chu": "GhiChu"}
_DT_COLS = {"x_hd_dia_chi": "DiaChi", "x_hd_lien_he": "NguoiLienHe", "x_hd_dien_thoai": "DienThoai"}
_HDR_COLS = {"x_hdr_mau_so": "KyHieuMauSo", "x_hdr_ky_hieu": "KyHieuHD", "x_hdr_so": "SoHoaDon",
             "x_hdr_ngay": "NgayXuatHD", "x_hdr_nguoi_mua": "TenNguoiMua", "x_hdr_mst": "MSTNguoiMua",
             "x_hdr_mat_hang": "MatHang", "x_hdr_thue_suat": "ThueSuat", "x_hdr_truoc_thue": "GiaTriTruocThue",
             "x_hdr_vat": "VAT", "x_hdr_sau_thue": "GiaTriSauThue", "x_hdr_tinh_chat": "TinhChatHD",
             "x_hdr_dot": "DotThanhToan", "x_hdr_ghi_chu": "GhiChuHDDR"}
_HDV_COLS = {"x_hdv_ky_hieu": "KyHieuHD", "x_hdv_so": "SoHoaDonGoc", "x_hdv_ngay": "NgayNhanHD",
             "x_hdv_ncc": "TenNhaCungCap", "x_hdv_mst": "MaThueNCC", "x_hdv_mat_hang": "MatHang",
             "x_hdv_thue_suat": "ThueSuat", "x_hdv_truoc_thue": "GiaTriTruocThue", "x_hdv_vat": "VAT",
             "x_hdv_sau_thue": "GiaTriSauThue", "x_hdv_nhom_thue": "NhomThue", "x_hdv_nguoi_mua": "NguoiMuaHang",
             "x_hdv_ref": "SoThamChieuSK", "x_hdv_ghi_chu": "GhiChuHDDV"}


def _rows(reports):
    for rep in reports:
        for r in rep.rows:
            yield r


def available(reports: list[Report]) -> list[Field]:
    """Các trường thêm được cho các bảng này (theo loại dòng có trong bảng)."""
    has = {k for r in _rows(reports) for k in ("_hd", "_hdr", "_hdv", "_tt") if r.get(k)}
    groups = set()
    if has & {"_hd", "_hdr", "_tt"}:
        groups |= {TT, HD}
    if has & {"_hdr", "_tt"}:
        groups.add(HDR)
    if "_hdv" in has:
        groups |= {HDV, HD}
    return [f for f in FIELDS if f.group in groups]


class _Lookup:
    """Đọc dữ liệu theo lô, mỗi bản ghi một lần cho cả lần xuất."""

    def __init__(self, db, den: str | None):
        self.db, self.den = db, den
        self._c: dict[tuple, dict | None] = {}
        self._pays: dict[str, list] | None = None

    def get(self, table: str, ma: str | None) -> dict | None:
        if not ma:
            return None
        k = (table, ma)
        if k not in self._c:
            r = self.db.one(f"SELECT * FROM {table} WHERE Ma = ?", (ma,))
            self._c[k] = dict(r) if r else None
        return self._c[k]

    def type_name(self, ma: str | None) -> str:
        r = self.get("LoaiHopDong", ma)
        return r["TenLoaiHD"] if r else ""

    def payments(self) -> dict[str, list]:
        """Thanh toán theo hợp đồng (đến ngày cuối kỳ báo cáo nếu có), xếp theo ngày."""
        if self._pays is None:
            self._pays = {}
            cond, params = "", []
            if self.den:
                cond, params = " AND NgayThanhToan <= ?", [self.den]
            for p in self.db.query("SELECT * FROM ThanhToanHopDong WHERE DaXoa = 0" + cond
                                   + " ORDER BY NgayThanhToan, NgayTao", params):
                self._pays.setdefault(p["MaHopDong"], []).append(dict(p))
        return self._pays


def _pay_values(lk: _Lookup, pays: list[dict]) -> dict:
    def inv_no(p):
        inv = lk.get("HoaDonDauRa", p["MaHDDauRa"])
        return inv["SoHoaDon"] if inv else "tạm ứng"
    return {"x_tt_ngay": SEP.join(fmt_date(p["NgayThanhToan"]) for p in pays),
            "x_tt_ref": SEP.join(p["SoThamChieuSK"] or "(không có)" for p in pays) if any(
                p["SoThamChieuSK"] for p in pays) else "",
            "x_tt_hinh_thuc": SEP.join(dict.fromkeys(p["HinhThuc"] for p in pays)),
            "x_tt_tien": SEP.join(fmt_money(p["SoTien"]) for p in pays),
            "x_tt_so_lan": len(pays), "x_tt_tong": sum(p["SoTien"] for p in pays),
            "x_tt_hoa_don": SEP.join(inv_no(p) for p in pays),
            "x_tt_ghi_chu": SEP.join(p["GhiChu"] for p in pays if p["GhiChu"])}


def _pays_of(lk: _Lookup, r: dict) -> list[dict] | None:
    """Các lần thanh toán ứng với một dòng: dòng thanh toán là chính nó, dòng hóa đơn đầu ra là các lần trả hóa
    đơn đó, dòng hợp đồng là mọi lần của hợp đồng. Dòng khác (công việc, hóa đơn đầu vào) thì không có."""
    if r.get("_tt"):
        p = lk.get("ThanhToanHopDong", r["_tt"])
        return [p] if p else []
    if r.get("_hdr"):
        return [p for p in lk.payments().get(r.get("_hd") or "", []) if p["MaHDDauRa"] == r["_hdr"]]
    if r.get("_hdv") or r.get("_cv"):
        return None
    if r.get("_hd"):
        return lk.payments().get(r["_hd"], [])
    return None


def _values(lk: _Lookup, r: dict, keys: list[str]) -> dict:
    out: dict = {}
    hd = lk.get("HopDong", r.get("_hd"))
    if hd:
        dt = lk.get("DoiTac", hd["MaDoiTac"]) or {}
        for k, col in _HD_COLS.items():
            out[k] = hd.get(col)
        for k, col in _DT_COLS.items():
            out[k] = dt.get(col)
        out["x_hd_loai"] = lk.type_name(hd["MaLoaiHD"])
        out["x_hd_thoi_gian"] = duration_text(hd.get("ThoiGianThucHien"), hd.get("DonViThoiGian"))
    ma_hdr = r.get("_hdr")
    inv = lk.get("HoaDonDauRa", ma_hdr)
    if inv:
        for k, col in _HDR_COLS.items():
            out[k] = inv.get(col)
        paid = sum(p["SoTien"] for p in lk.payments().get(inv["MaHopDong"], []) if p["MaHDDauRa"] == inv["Ma"])
        out["x_hdr_da_thu"], out["x_hdr_con_thu"] = paid, inv["GiaTriSauThue"] - paid
    vin = lk.get("HoaDonDauVao", r.get("_hdv"))
    if vin:
        for k, col in _HDV_COLS.items():
            out[k] = vin.get(col)
    if any(k.startswith("x_tt_") for k in keys):
        pays = _pays_of(lk, r)
        if pays is not None:
            out.update(_pay_values(lk, pays))
    return {k: out.get(k) for k in keys if out.get(k) not in (None, "")}


def enrich(db, reports: list[Report], keys: list[str], tach_tt: bool = False, den: str | None = None) -> list[Report]:
    """Thêm các trường `keys` vào cuối mỗi bảng. `tach_tt`: mỗi lần thanh toán một dòng chi tiết dưới dòng hợp đồng /
    hóa đơn (dòng cha chỉ ghi số lần). `den`: chỉ tính thanh toán đến ngày này (ngày cuối kỳ báo cáo)."""
    keys = [k for k in keys if k in BY_KEY]
    if not keys:
        return reports
    lk = _Lookup(db, den)
    out = []
    for rep in reports:
        have = {c[0] for c in rep.columns}
        cols = list(rep.columns) + [(k, BY_KEY[k].label, BY_KEY[k].kind) for k in keys if k not in have]
        first = rep.columns[0][0]
        inv_rows = {r["_hdr"] for r in rep.rows if r.get("_hdr") and not r.get("_tt")}
        rows = []
        for r in rep.rows:
            if r.get("_kieu") in ("nhom", "cong") or not any(r.get(k) for k in ("_hd", "_hdr", "_hdv", "_tt")):
                rows.append(r)
                continue
            row = dict(r, **_values(lk, r, keys))
            pays = _pays_of(lk, r) if tach_tt and not r.get("_tt") else None
            if pays and not r.get("_hdr"):           # dòng hợp đồng: lần trả cho hóa đơn đã có dòng riêng thì để ở đó
                pays = [p for p in pays if p["MaHDDauRa"] not in inv_rows]
            if pays:
                for k in keys:
                    if k.startswith("x_tt_") and k != "x_tt_so_lan":
                        row.pop(k, None)
            rows.append(row)
            for p in pays or []:
                child = {"_kieu": "con", "MaHopDong": p["MaHopDong"], "_hd": p["MaHopDong"], "_tt": p["Ma"],
                         "_hdr": p["MaHDDauRa"],
                         first: f"         ◦ Thanh toán {fmt_date(p['NgayThanhToan'])} · {fmt_money(p['SoTien'])}"}
                child.update({k: v for k, v in _values(lk, child, keys).items() if k.startswith("x_tt_")})
                rows.append(child)
        out.append(Report(rep.title, cols, rows, rep.total, rep.subtitle, rep.sheet))
    return out


def parse_choice(raw) -> tuple[list[str] | None, list[str], bool]:
    """Lựa chọn đã lưu: danh sách cột (kiểu cũ) hoặc {"cot": [...], "them": [...], "tach_tt": bool}."""
    if isinstance(raw, dict):
        return raw.get("cot"), list(raw.get("them") or []), bool(raw.get("tach_tt"))
    return raw, [], False
