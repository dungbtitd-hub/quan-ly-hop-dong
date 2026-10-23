"""Nghiệp vụ hợp đồng, hóa đơn đầu ra / đầu vào, thanh toán và danh mục."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import vat
from .assets import AssetService
from .db import Database

CONTRACT_STATUSES = ("Chưa bắt đầu", "Đang thực hiện", "Đã nghiệm thu", "Hoàn thành", "Tạm dừng", "Hủy")
TASK_STATUSES = ("Chưa làm", "Đang làm", "Đã bàn giao", "Đã nghiệm thu", "Hoàn thành")
INVOICE_KINDS = ("Gốc", "Điều chỉnh", "Thay thế", "Đã hủy")
PARTNER_ROLES = ("Chủ đầu tư", "Nhà cung cấp", "Cả hai")
PAY_METHODS = ("Chuyển khoản", "Tiền mặt")
DURATION_UNITS = ("ngày", "tháng")
TAX_GROUPS = ("Chịu thuế", "Không chịu thuế")
# Tên nhóm theo bảng kê hóa đơn mua vào (mẫu quản trị).
TAX_GROUP_TITLES = {
    "Chịu thuế": "1. Hàng hoá, dịch vụ dùng riêng cho SXKD chịu thuế GTGT và sử dụng cho các hoạt động cung cấp "
                 "hàng hoá, dịch vụ không kê khai, nộp thuế GTGT đủ điều kiện khấu trừ thuế",
    "Không chịu thuế": "5. Hàng hóa, dịch vụ không phải tổng hợp trên tờ khai 01/GTGT",
}
OPEN_STATUSES = ("Chưa bắt đầu", "Đang thực hiện", "Tạm dừng")
DEFAULT_REMIND_DAYS = 30


class ValidationError(Exception):
    pass


def payment_status(invoiced: int, paid: int) -> str:
    if paid <= 0:
        return "Chưa thu"
    if paid >= invoiced:
        return "Đã thu đủ"
    return "Thu một phần"


PAY_FILTERS = (("Đã thanh toán đủ", "du"), ("Thanh toán một phần", "mot_phan"), ("Chưa thanh toán", "chua"),
               ("Còn phải thu (đã xuất hóa đơn)", "con_thu"))
PAY_STATE_LABELS = {"du": "Đã thanh toán đủ", "mot_phan": "Thanh toán một phần", "chua": "Chưa thanh toán"}


def contract_pay_state(s: dict) -> str:
    """Tình trạng thanh toán của hợp đồng đầu ra so với giá trị hợp đồng (summary): du / mot_phan / chua."""
    paid, value = s["DaThanhToan"], s["GiaTriHD"]
    if paid <= 0:
        return "chua"
    return "du" if paid >= value else "mot_phan"


def contract_matches(hd, s: dict, tinh_trang: str | None = None, thanh_toan: str | None = None) -> bool:
    """Lọc hợp đồng theo tình trạng thực hiện và tình trạng thanh toán (thanh toán chỉ xét hợp đồng đầu ra,
    tức tiền thu về)."""
    if tinh_trang and hd["TinhTrangHopDong"] != tinh_trang:
        return False
    if thanh_toan:
        if hd["ChieuHopDong"] != "Đầu ra":
            return False
        if thanh_toan == "con_thu":
            return s["ConPhaiThu"] > 0
        return contract_pay_state(s) == thanh_toan
    return True


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def end_date(start: str | None, amount, unit: str | None) -> str | None:
    """Ngày kết thúc khi thực hiện `amount` ngày / tháng, tính cả ngày bắt đầu (30 ngày từ 01/03 → 30/03)."""
    if not start or not amount or int(amount) <= 0:
        return None
    d = date.fromisoformat(start[:10])
    if unit == "tháng":
        return (add_months(d, int(amount)) - timedelta(days=1)).isoformat()
    return (d + timedelta(days=int(amount) - 1)).isoformat()


def duration_text(amount, unit) -> str:
    return f"{amount} {unit or 'ngày'}" if amount else ""


def guess_tax_group(vat) -> str:
    """Hóa đơn có tiền thuế GTGT thì chịu thuế; 0%, KCT, KKKNT, không có thuế thì xếp nhóm không chịu thuế."""
    if int(vat or 0) > 0:
        return "Chịu thuế"
    return "Không chịu thuế"


def rate_from_amounts(before_tax, vat) -> str:
    """Thuế suất suy ra từ tiền hàng và tiền thuế khi hóa đơn không ghi rõ (chỉ nhận 5%, 8%, 10%)."""
    before_tax, vat = int(before_tax or 0), int(vat or 0)
    if before_tax <= 0 or vat <= 0:
        return ""
    r = vat * 100 / before_tax
    return next((f"{std}%" for std in (10, 8, 5) if abs(r - std) < 0.2), "")


def _fill_vat(v: dict, after_key: str) -> None:
    """Đủ hai dữ kiện (trước thuế, thuế suất, VAT, sau thuế) thì tự tính phần còn lại; số mâu thuẫn thì báo lỗi."""
    try:
        vat.fill(v, after_key)
    except ValueError as e:
        raise ValidationError(str(e)) from None


VAT_TABLES = (("HopDong", "GiaTriTongSauThue"), ("HoaDonDauRa", "GiaTriSauThue"), ("HoaDonDauVao", "GiaTriSauThue"))


def backfill_vat(db) -> int:
    """Dữ liệu nhập ở bản cũ đã đủ dữ kiện mà chưa tính (VD có thuế suất nhưng VAT = 0, sau thuế = trước thuế,
    hoặc thiếu sau thuế): tính bổ sung. Trả về số bản ghi đã cập nhật."""
    n = 0
    for table, after_key in VAT_TABLES:
        cols = db.columns(table)
        for r in db.active(table):
            old = {k: r[k] for k in ("GiaTriTruocThue", "VAT", after_key, "ThueSuat") if k in cols}
            v = dict(old)
            b, t, a = v["GiaTriTruocThue"] or 0, v["VAT"] or 0, v[after_key] or 0
            try:
                rate = vat.parse_rate(v.get("ThueSuat"))
            except ValueError:
                continue
            r_val = vat.rate_value(rate)
            if t == 0 and r_val and (b == 0 or a in (0, b)):
                # bản cũ chỉ cộng trước thuế + VAT: có thuế suất mà VAT = 0 nghĩa là chưa tính
                res = vat.complete(b or None, None, None if b else (a or None), rate)
            elif b and not a:
                res = vat.complete(b, t, None, rate)
            elif a and t and not b:
                res = vat.complete(None, t, a, rate)
            elif a and b and not t and a != b:
                res = vat.complete(b, None, a, rate)
            else:
                continue
            if res.error:
                continue
            v.update(GiaTriTruocThue=res.before, VAT=res.vat, **{after_key: res.after})
            if "ThueSuat" in cols:
                v["ThueSuat"] = res.rate
            if v != old:
                db.update(table, r["Ma"], {k: v[k] for k in v if v[k] != old[k]}, commit=False)
                n += 1
    if n:
        db.conn.commit()
    return n


def invoicing_status(value: int, invoiced: int) -> str:
    if invoiced <= 0:
        return "Chưa xuất"
    if invoiced >= value:
        return "Đã xuất đủ"
    return "Xuất một phần"


class ContractService:
    def __init__(self, db: Database):
        self.db = db

    # ---- danh mục
    def contract_types(self) -> list[sqlite3.Row]:
        return self.db.active("LoaiHopDong", order="TenLoaiHD")

    def save_contract_type(self, ten: str, ma: str | None = None) -> str:
        ten = ten.strip()
        if not ten:
            raise ValidationError("Chưa nhập tên loại hợp đồng.")
        dup = self.db.one("SELECT Ma FROM LoaiHopDong WHERE DaXoa = 0 AND lower(TenLoaiHD) = lower(?)"
                          " AND Ma != ?", (ten, ma or ""))
        if dup:
            raise ValidationError("Loại hợp đồng này đã có.")
        if ma:
            self.db.update("LoaiHopDong", ma, {"TenLoaiHD": ten})
            return ma
        return self.db.insert("LoaiHopDong", {"TenLoaiHD": ten})

    def delete_contract_type(self, ma: str) -> None:
        if self.db.one("SELECT 1 FROM HopDong WHERE DaXoa = 0 AND MaLoaiHD = ?", (ma,)):
            raise ValidationError("Loại hợp đồng đang được dùng, không xóa được.")
        self.db.soft_delete("LoaiHopDong", ma)

    def partners(self, vai_tro: str | None = None) -> list[sqlite3.Row]:
        if vai_tro:
            return self.db.active("DoiTac", "VaiTroDoiTac IN (?, 'Cả hai')", (vai_tro,), "TenDoiTac")
        return self.db.active("DoiTac", order="TenDoiTac")

    def save_partner(self, values: dict, ma: str | None = None) -> str:
        v = {k: (val.strip() if isinstance(val, str) else val) for k, val in values.items()}
        if not v.get("TenDoiTac"):
            raise ValidationError("Chưa nhập tên đối tác.")
        if v.get("MaSoThue"):
            dup = self.db.one("SELECT TenDoiTac FROM DoiTac WHERE DaXoa = 0 AND MaSoThue = ? AND Ma != ?",
                              (v["MaSoThue"], ma or ""))
            if dup:
                raise ValidationError(f"Mã số thuế đã có ở đối tác \"{dup['TenDoiTac']}\".")
        if ma:
            self.db.update("DoiTac", ma, v, commit=False)
            p = self.db.get("DoiTac", ma)          # hợp đồng lưu kèm tên, MST đối tác: cập nhật theo
            for hd in self.db.active("HopDong", "MaDoiTac = ?", (ma,)):
                if (hd["TenDoiTac"], hd["MaThueDoiTac"]) != (p["TenDoiTac"], p["MaSoThue"]):
                    self.db.update("HopDong", hd["Ma"], {"TenDoiTac": p["TenDoiTac"], "MaThueDoiTac": p["MaSoThue"]},
                                   commit=False)
            self.db.conn.commit()
            return ma
        return self.db.insert("DoiTac", v)

    def find_or_create_partner(self, ten: str, mst: str | None, vai_tro: str) -> str:
        if mst:
            r = self.db.one("SELECT * FROM DoiTac WHERE DaXoa = 0 AND MaSoThue = ?", (mst,))
            if r:
                if r["VaiTroDoiTac"] not in (vai_tro, "Cả hai"):
                    self.db.update("DoiTac", r["Ma"], {"VaiTroDoiTac": "Cả hai"})
                return r["Ma"]
        r = self.db.one("SELECT Ma FROM DoiTac WHERE DaXoa = 0 AND lower(TenDoiTac) = lower(?)", (ten,))
        if r:
            return r["Ma"]
        return self.db.insert("DoiTac", {"TenDoiTac": ten, "MaSoThue": mst, "VaiTroDoiTac": vai_tro})

    def delete_partner(self, ma: str) -> None:
        if self.db.one("SELECT 1 FROM HopDong WHERE DaXoa = 0 AND MaDoiTac = ?", (ma,)):
            raise ValidationError("Đối tác đang có hợp đồng, không xóa được.")
        self.db.soft_delete("DoiTac", ma)

    def groups(self) -> list[sqlite3.Row]:
        return self.db.active("NhomDoiTac", order="TenNhom")

    def group_members(self, ma_nhom: str) -> list[str]:
        return [r["MaDoiTac"] for r in self.db.active("NhomDoiTacThanhVien", "MaNhom = ?", (ma_nhom,))]

    def save_group(self, ten: str, members: list[str], ma: str | None = None) -> str:
        ten = ten.strip()
        if not ten:
            raise ValidationError("Chưa nhập tên nhóm.")
        if ma:
            self.db.update("NhomDoiTac", ma, {"TenNhom": ten}, commit=False)
        else:
            ma = self.db.insert("NhomDoiTac", {"TenNhom": ten}, commit=False)
        current = {r["MaDoiTac"]: r["Ma"] for r in self.db.active("NhomDoiTacThanhVien", "MaNhom = ?", (ma,))}
        for dt, row_ma in current.items():
            if dt not in members:
                self.db.soft_delete("NhomDoiTacThanhVien", row_ma, commit=False)
        for dt in members:
            if dt not in current:
                self.db.insert("NhomDoiTacThanhVien", {"MaNhom": ma, "MaDoiTac": dt}, commit=False)
        self.db.conn.commit()
        return ma

    def delete_group(self, ma: str) -> None:
        for r in self.db.active("NhomDoiTacThanhVien", "MaNhom = ?", (ma,)):
            self.db.soft_delete("NhomDoiTacThanhVien", r["Ma"], commit=False)
        self.db.soft_delete("NhomDoiTac", ma)

    # ---- hợp đồng
    def contracts(self, chieu: str | None = None, tu_khoa: str = "") -> list[sqlite3.Row]:
        where, params = [], []
        if chieu:
            where.append("ChieuHopDong = ?")
            params.append(chieu)
        if tu_khoa:
            where.append("(SoHopDong LIKE ? OR TenHopDong LIKE ? OR TenDoiTac LIKE ?)")
            params += [f"%{tu_khoa}%"] * 3
        return self.db.active("HopDong", " AND ".join(where), params, "NgayKy DESC, SoHopDong")

    def save_contract(self, values: dict, ma: str | None = None) -> str:
        v = {k: (val.strip() if isinstance(val, str) else val) for k, val in values.items()}
        for f, label in (("SoHopDong", "Số hợp đồng"), ("TenHopDong", "Tên hợp đồng"), ("NgayKy", "Ngày ký")):
            if not v.get(f):
                raise ValidationError(f"Chưa nhập {label}.")
        if v.get("ChieuHopDong") not in ("Đầu ra", "Đầu vào"):
            raise ValidationError("Chọn hợp đồng Đầu ra hoặc Đầu vào.")
        if v.get("MaDoiTac"):
            p = self.db.get("DoiTac", v["MaDoiTac"])
            v["TenDoiTac"] = p["TenDoiTac"]
            v["MaThueDoiTac"] = p["MaSoThue"]
        if not v.get("TenDoiTac"):
            raise ValidationError("Chưa chọn đối tác (chủ đầu tư / nhà cung cấp).")
        _fill_vat(v, "GiaTriTongSauThue")
        if ma is None and not int(v.get("ThoiGianThucHien") or 0):
            raise ValidationError("Chưa nhập Thời gian thực hiện hợp đồng (số ngày hoặc số tháng).")
        if "ThoiGianThucHien" in v:
            v["ThoiGianThucHien"] = int(v.get("ThoiGianThucHien") or 0) or None
            if v.get("DonViThoiGian") not in DURATION_UNITS:
                v["DonViThoiGian"] = "ngày"
            if v["ThoiGianThucHien"]:
                v["NgayKetThucDuKien"] = end_date(v.get("NgayBatDauDuKien") or v["NgayKy"], v["ThoiGianThucHien"],
                                                  v["DonViThoiGian"])
        if "NhacTruocNgay" in v:
            v["NhacTruocNgay"] = int(v.get("NhacTruocNgay") or 0) or None
        dup = self.db.one(
            "SELECT Ma FROM HopDong WHERE DaXoa = 0 AND SoHopDong = ? AND COALESCE(MaThueDoiTac, TenDoiTac) = ?"
            " AND Ma != ?", (v["SoHopDong"], v.get("MaThueDoiTac") or v["TenDoiTac"], ma or ""))
        if dup:
            raise ValidationError("Đã có hợp đồng cùng Số hợp đồng với đối tác này.")
        if ma:
            self.db.update("HopDong", ma, v)
            return ma
        return self.db.insert("HopDong", v)

    def delete_contract(self, ma: str) -> None:
        """Xóa mềm hợp đồng cùng toàn bộ dữ liệu con (để việc xóa được đồng bộ sang máy khác)."""
        for cv in self.db.active("CongViec", "MaHopDong = ?", (ma,)):
            for t in ("LichSuBanGiao", "LichSuNghiemThu"):
                for r in self.db.active(t, "MaCongViec = ?", (cv["Ma"],)):
                    self.db.soft_delete(t, r["Ma"], commit=False)
            self.db.soft_delete("CongViec", cv["Ma"], commit=False)
        for t in ("ThanhToanHopDong", "HoaDonDauRa"):
            for r in self.db.active(t, "MaHopDong = ?", (ma,)):
                self.db.soft_delete(t, r["Ma"], commit=False)
        for r in self.db.active("HoaDonDauVao", "MaHopDong = ?", (ma,)):
            self.db.update("HoaDonDauVao", r["Ma"], {"MaHopDong": None}, commit=False)
        self.db.soft_delete("HopDong", ma)

    def delete_task(self, ma: str) -> None:
        """Xóa mềm một công việc cùng các lần bàn giao, nghiệm thu của nó."""
        for t in ("LichSuBanGiao", "LichSuNghiemThu"):
            for r in self.db.active(t, "MaCongViec = ?", (ma,)):
                self.db.soft_delete(t, r["Ma"], commit=False)
        self.db.soft_delete("CongViec", ma)

    def summary(self, ma: str, tu: str | None = None, den: str | None = None) -> dict:
        """Giá trị HĐ, đã xuất hóa đơn, đã thanh toán, còn phải xuất, còn phải thu."""
        hd = self.db.get("HopDong", ma)
        cond_i, cond_p, pi, pp = "", "", [ma], [ma]
        if tu:
            cond_i += " AND NgayXuatHD >= ?"
            cond_p += " AND NgayThanhToan >= ?"
            pi.append(tu)
            pp.append(tu)
        if den:
            cond_i += " AND NgayXuatHD <= ?"
            cond_p += " AND NgayThanhToan <= ?"
            pi.append(den)
            pp.append(den)
        if hd["ChieuHopDong"] == "Đầu ra":
            invoiced = self.db.one(
                "SELECT COALESCE(SUM(GiaTriSauThue), 0) s FROM HoaDonDauRa WHERE DaXoa = 0 AND MaHopDong = ?"
                " AND TinhChatHD != 'Đã hủy'" + cond_i, pi)["s"]
        else:
            invoiced = self.db.one(
                "SELECT COALESCE(SUM(GiaTriSauThue), 0) s FROM HoaDonDauVao WHERE DaXoa = 0 AND MaHopDong = ?"
                + cond_i.replace("NgayXuatHD", "NgayNhanHD"), pi)["s"]
        paid = self.db.one("SELECT COALESCE(SUM(SoTien), 0) s, MAX(NgayThanhToan) d FROM ThanhToanHopDong"
                           " WHERE DaXoa = 0 AND MaHopDong = ?" + cond_p, pp)
        value = hd["GiaTriTongSauThue"]
        return {
            "GiaTriHD": value, "DaXuatHD": invoiced, "ConPhaiXuat": max(value - invoiced, 0),
            "DaThanhToan": paid["s"], "ConPhaiThu": invoiced - paid["s"],
            "PhanTramTT": round(paid["s"] * 100 / value, 1) if value else 0.0,
            "LanThuGanNhat": paid["d"] or "",
            "TinhTrangThanhToan": payment_status(invoiced, paid["s"]),
            "TinhTrangXuatHD": invoicing_status(value, invoiced),
        }

    # ---- công việc
    def tasks(self, ma_hd: str) -> list[sqlite3.Row]:
        return self.db.active("CongViec", "MaHopDong = ?", (ma_hd,), "NgayDuKienHT")

    def save_task(self, values: dict, ma: str | None = None) -> str:
        if not (values.get("TenNoiDungCV") or "").strip():
            raise ValidationError("Chưa nhập nội dung công việc.")
        if ma:
            self.db.update("CongViec", ma, values)
            return ma
        return self.db.insert("CongViec", values)

    def add_handover(self, values: dict) -> str:
        return self.db.insert("LichSuBanGiao", values)

    def add_acceptance(self, values: dict) -> str:
        return self.db.insert("LichSuNghiemThu", values)

    # ---- hóa đơn đầu ra
    def out_invoices(self, ma_hd: str | None = None) -> list[sqlite3.Row]:
        if ma_hd:
            return self.db.active("HoaDonDauRa", "MaHopDong = ?", (ma_hd,), "NgayXuatHD")
        return self.db.active("HoaDonDauRa", order="NgayXuatHD DESC")

    def save_out_invoice(self, values: dict, ma: str | None = None) -> str:
        v = dict(values)
        if not v.get("SoHoaDon") or not v.get("NgayXuatHD") or not v.get("MaHopDong"):
            raise ValidationError("Cần Số hóa đơn, Ngày xuất và Hợp đồng.")
        hd = self.db.get("HopDong", v["MaHopDong"])
        if hd["ChieuHopDong"] != "Đầu ra":
            raise ValidationError("Hóa đơn đầu ra phải gắn với hợp đồng đầu ra.")
        _fill_vat(v, "GiaTriSauThue")
        dup = self.db.one("SELECT 1 FROM HoaDonDauRa WHERE DaXoa = 0 AND COALESCE(KyHieuHD,'') = ? AND SoHoaDon = ?"
                          " AND Ma != ?", (v.get("KyHieuHD") or "", v["SoHoaDon"], ma or ""))
        if dup:
            raise ValidationError("Hóa đơn (ký hiệu + số) này đã có.")
        if ma:
            self.db.update("HoaDonDauRa", ma, v)
            return ma
        return self.db.insert("HoaDonDauRa", v)

    def payments_missing_ref(self) -> list[dict]:
        """Lần thu chuyển khoản đã ghi nhưng chưa có số tham chiếu sao kê (chưa đối chiếu sao kê ngân hàng)."""
        rows = self.db.query(
            "SELECT t.*, h.SoHopDong, h.TenDoiTac, d.KyHieuHD, d.SoHoaDon FROM ThanhToanHopDong t"
            " JOIN HopDong h ON h.Ma = t.MaHopDong LEFT JOIN HoaDonDauRa d ON d.Ma = t.MaHDDauRa"
            " WHERE t.DaXoa = 0 AND t.HinhThuc = 'Chuyển khoản' AND TRIM(COALESCE(t.SoThamChieuSK, '')) = ''"
            " ORDER BY t.NgayThanhToan, h.SoHopDong")
        return [dict(r) for r in rows]

    def set_payment_ref(self, ma_tt: str, so_tham_chieu: str) -> None:
        so = " ".join((so_tham_chieu or "").split())
        if so:
            dup = self.db.one("SELECT 1 FROM ThanhToanHopDong WHERE DaXoa = 0 AND SoThamChieuSK = ? AND Ma != ?",
                              (so, ma_tt))
            if dup:
                raise ValidationError(f"Số tham chiếu {so} đã ghi cho một lần thanh toán khác.")
        self.db.update("ThanhToanHopDong", ma_tt, {"SoThamChieuSK": so or None})

    def invoice_payments(self, ma_hddr: str) -> list[sqlite3.Row]:
        return self.db.active("ThanhToanHopDong", "MaHDDauRa = ?", (ma_hddr,), "NgayThanhToan")

    def invoice_paid(self, ma_hddr: str) -> int:
        return self.db.one("SELECT COALESCE(SUM(SoTien),0) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND MaHDDauRa = ?",
                           (ma_hddr,))["s"]

    def delete_out_invoice(self, ma: str) -> None:
        for r in self.db.active("ThanhToanHopDong", "MaHDDauRa = ?", (ma,)):
            self.db.update("ThanhToanHopDong", r["Ma"], {"MaHDDauRa": None}, commit=False)
        self.db.soft_delete("HoaDonDauRa", ma)

    # ---- thanh toán
    def payments(self, ma_hd: str | None = None) -> list[sqlite3.Row]:
        if ma_hd:
            return self.db.active("ThanhToanHopDong", "MaHopDong = ?", (ma_hd,), "NgayThanhToan")
        return self.db.active("ThanhToanHopDong", order="NgayThanhToan DESC")

    def save_payment(self, values: dict, ma: str | None = None) -> str:
        v = dict(values)
        if not v.get("MaHopDong") or not v.get("NgayThanhToan"):
            raise ValidationError("Cần Hợp đồng và Ngày thanh toán.")
        v["SoTien"] = int(v.get("SoTien") or 0)
        if v["SoTien"] <= 0:
            raise ValidationError("Số tiền phải lớn hơn 0.")
        if v.get("HinhThuc") not in PAY_METHODS:
            v["HinhThuc"] = "Chuyển khoản"
        if ma:
            self.db.update("ThanhToanHopDong", ma, v)
            return ma
        return self.db.insert("ThanhToanHopDong", v)

    def delete_payment(self, ma: str) -> None:
        self.db.soft_delete("ThanhToanHopDong", ma)

    # ---- hóa đơn đầu vào
    def in_invoices(self) -> list[sqlite3.Row]:
        return self.db.active("HoaDonDauVao", order="NgayNhanHD DESC")

    def save_in_invoice(self, values: dict, ma: str | None = None) -> str:
        """values["_Dong"]: các dòng hàng đọc từ hóa đơn điện tử (nếu có), để phân loại chi phí / CCDC / TSCĐ."""
        v = dict(values)
        lines = v.pop("_Dong", None)
        for f, label in (("SoHoaDonGoc", "Số hóa đơn"), ("TenNhaCungCap", "Tên người bán"),
                         ("MaThueNCC", "MST người bán"), ("NgayNhanHD", "Ngày hóa đơn")):
            if not v.get(f):
                raise ValidationError(f"Chưa nhập {label}.")
        _fill_vat(v, "GiaTriSauThue")
        v["MaHopDong"] = v.get("MaHopDong") or None
        if v.get("NhomThue") not in TAX_GROUPS:
            v["NhomThue"] = guess_tax_group(v["VAT"])
        dup = self.db.one("SELECT 1 FROM HoaDonDauVao WHERE DaXoa = 0 AND MaThueNCC = ? AND COALESCE(KyHieuHD,'') = ?"
                          " AND SoHoaDonGoc = ? AND Ma != ?",
                          (v["MaThueNCC"], v.get("KyHieuHD") or "", v["SoHoaDonGoc"], ma or ""))
        if dup:
            raise ValidationError("Hóa đơn đầu vào (MST + ký hiệu + số) này đã có.")
        if ma:
            self.db.update("HoaDonDauVao", ma, v)
        else:
            ma = self.db.insert("HoaDonDauVao", v)
        AssetService(self.db).ensure_lines(ma, lines)
        return ma

    def delete_in_invoice(self, ma: str) -> None:
        AssetService(self.db).delete_invoice_lines(ma)
        self.db.soft_delete("HoaDonDauVao", ma)

    def invoice_exists(self, kind: str, values: dict) -> bool:
        """Hóa đơn đã có trong dữ liệu chưa (kind 'ra' / 'vao'), để bỏ qua khi nhập nhiều file."""
        if kind == "ra":
            return self.db.one("SELECT 1 FROM HoaDonDauRa WHERE DaXoa = 0 AND COALESCE(KyHieuHD,'') = ?"
                               " AND SoHoaDon = ?", (values.get("KyHieuHD") or "", values.get("SoHoaDon") or "")) \
                is not None
        return self.db.one("SELECT 1 FROM HoaDonDauVao WHERE DaXoa = 0 AND MaThueNCC = ? AND COALESCE(KyHieuHD,'') = ?"
                           " AND SoHoaDonGoc = ?", (values.get("MaThueNCC") or "", values.get("KyHieuHD") or "",
                                                    values.get("SoHoaDonGoc") or "")) is not None

    # ---- nhắc hợp đồng sắp hết hạn
    def remind_days(self) -> int:
        try:
            return max(int(self.db.setting("nhac_het_han_truoc") or DEFAULT_REMIND_DAYS), 0)
        except ValueError:
            return DEFAULT_REMIND_DAYS

    def set_remind_days(self, days: int) -> None:
        self.db.set_setting("nhac_het_han_truoc", str(max(int(days), 0)))

    def expiring(self, today: str) -> list[dict]:
        """Hợp đồng chưa xong sắp đến (trong số ngày nhắc) hoặc đã quá ngày kết thúc dự kiến."""
        default = self.remind_days()
        t = date.fromisoformat(today)
        out = []
        for hd in self.db.active("HopDong", f"TinhTrangHopDong IN ({','.join('?' * len(OPEN_STATUSES))})"
                                 " AND NgayKetThucDuKien IS NOT NULL", OPEN_STATUSES, "NgayKetThucDuKien"):
            left = (date.fromisoformat(hd["NgayKetThucDuKien"][:10]) - t).days
            before = hd["NhacTruocNgay"] if hd["NhacTruocNgay"] else default
            if left <= before:
                r = dict(hd)
                r["ConLai"] = left
                r["ConLaiText"] = ("Hết hạn hôm nay" if left == 0 else f"Còn {left} ngày" if left > 0
                                   else f"Quá hạn {-left} ngày")
                out.append(r)
        return out

    def overdue_tasks(self, today: str) -> list[dict]:
        t = date.fromisoformat(today)
        out = []
        for r in self.db.query(
                "SELECT c.TenNoiDungCV, h.SoHopDong, h.Ma MaHopDong, c.NgayDuKienHT FROM CongViec c"
                " JOIN HopDong h ON h.Ma = c.MaHopDong"
                " WHERE c.DaXoa = 0 AND h.DaXoa = 0 AND c.TinhTrangCV NOT IN ('Hoàn thành', 'Đã nghiệm thu')"
                " AND c.NgayDuKienHT IS NOT NULL AND c.NgayDuKienHT < ? ORDER BY c.NgayDuKienHT", (today,)):
            r = dict(r)
            r["QuaHan"] = (t - date.fromisoformat(r["NgayDuKienHT"][:10])).days
            out.append(r)
        return out

    # ---- trang chủ
    def dashboard(self, today: str, days: int = 14) -> dict:
        n_out = self.db.one("SELECT COUNT(*) n FROM HopDong WHERE DaXoa = 0 AND ChieuHopDong = 'Đầu ra'")["n"]
        n_in = self.db.one("SELECT COUNT(*) n FROM HopDong WHERE DaXoa = 0 AND ChieuHopDong = 'Đầu vào'")["n"]
        running = self.db.one("SELECT COUNT(*) n FROM HopDong WHERE DaXoa = 0 AND TinhTrangHopDong = 'Đang thực hiện'")["n"]
        receivable, debts = 0, []
        for hd in self.contracts("Đầu ra"):
            s = self.summary(hd["Ma"])
            if s["ConPhaiThu"] > 0:
                receivable += s["ConPhaiThu"]
                debts.append({"Ma": hd["Ma"], "SoHopDong": hd["SoHopDong"], "TenHopDong": hd["TenHopDong"],
                              "TenDoiTac": hd["TenDoiTac"], **s})
        debts.sort(key=lambda r: -r["ConPhaiThu"])
        t = date.fromisoformat(today)
        overdue = self.overdue_tasks(today)
        ending = self.expiring(today)
        # Lịch các ngày tới: hạn công việc và ngày kết thúc hợp đồng.
        last = (t + timedelta(days=days - 1)).isoformat()
        events: dict[str, list[str]] = {}
        for r in self.db.query(
                "SELECT c.TenNoiDungCV, h.SoHopDong, c.NgayDuKienHT d FROM CongViec c JOIN HopDong h ON h.Ma = c.MaHopDong"
                " WHERE c.DaXoa = 0 AND h.DaXoa = 0 AND c.TinhTrangCV NOT IN ('Hoàn thành', 'Đã nghiệm thu')"
                " AND substr(c.NgayDuKienHT, 1, 10) BETWEEN ? AND ?", (today, last)):
            events.setdefault(r["d"][:10], []).append(f"Hạn công việc: {r['TenNoiDungCV']} ({r['SoHopDong']})")
        for r in self.db.active("HopDong", f"TinhTrangHopDong IN ({','.join('?' * len(OPEN_STATUSES))})"
                                " AND substr(NgayKetThucDuKien, 1, 10) BETWEEN ? AND ?",
                                (*OPEN_STATUSES, today, last)):
            events.setdefault(r["NgayKetThucDuKien"][:10], []).append(f"Hết hạn hợp đồng {r['SoHopDong']}")
        calendar = [((t + timedelta(days=i)).isoformat(), events.get((t + timedelta(days=i)).isoformat(), []))
                    for i in range(days)]
        return {"hd_dau_ra": n_out, "hd_dau_vao": n_in, "dang_thuc_hien": running, "con_phai_thu": receivable,
                "cong_no": debts, "cv_qua_han": overdue, "hd_sap_het_han": ending, "lich": calendar}
