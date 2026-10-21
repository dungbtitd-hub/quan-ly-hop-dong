"""Nghiệp vụ hợp đồng, hóa đơn đầu ra / đầu vào, thanh toán và danh mục."""
from __future__ import annotations

import sqlite3

from .db import Database

CONTRACT_STATUSES = ("Chưa bắt đầu", "Đang thực hiện", "Đã nghiệm thu", "Hoàn thành", "Tạm dừng", "Hủy")
TASK_STATUSES = ("Chưa làm", "Đang làm", "Đã bàn giao", "Đã nghiệm thu", "Hoàn thành")
INVOICE_KINDS = ("Gốc", "Điều chỉnh", "Thay thế", "Đã hủy")
PARTNER_ROLES = ("Chủ đầu tư", "Nhà cung cấp", "Cả hai")
PAY_METHODS = ("Chuyển khoản", "Tiền mặt")


class ValidationError(Exception):
    pass


def payment_status(invoiced: int, paid: int) -> str:
    if paid <= 0:
        return "Chưa thu"
    if paid >= invoiced:
        return "Đã thu đủ"
    return "Thu một phần"


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
            self.db.update("DoiTac", ma, v)
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
        v["GiaTriTruocThue"] = int(v.get("GiaTriTruocThue") or 0)
        v["VAT"] = int(v.get("VAT") or 0)
        v["GiaTriTongSauThue"] = v["GiaTriTruocThue"] + v["VAT"]
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
        v["GiaTriTruocThue"] = int(v.get("GiaTriTruocThue") or 0)
        v["VAT"] = int(v.get("VAT") or 0)
        v["GiaTriSauThue"] = int(v.get("GiaTriSauThue") or (v["GiaTriTruocThue"] + v["VAT"]))
        dup = self.db.one("SELECT 1 FROM HoaDonDauRa WHERE DaXoa = 0 AND COALESCE(KyHieuHD,'') = ? AND SoHoaDon = ?"
                          " AND Ma != ?", (v.get("KyHieuHD") or "", v["SoHoaDon"], ma or ""))
        if dup:
            raise ValidationError("Hóa đơn (ký hiệu + số) này đã có.")
        if ma:
            self.db.update("HoaDonDauRa", ma, v)
            return ma
        return self.db.insert("HoaDonDauRa", v)

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
        v = dict(values)
        for f, label in (("SoHoaDonGoc", "Số hóa đơn"), ("TenNhaCungCap", "Nhà cung cấp"),
                         ("MaThueNCC", "MST nhà cung cấp"), ("NgayNhanHD", "Ngày hóa đơn")):
            if not v.get(f):
                raise ValidationError(f"Chưa nhập {label}.")
        v["GiaTriTruocThue"] = int(v.get("GiaTriTruocThue") or 0)
        v["VAT"] = int(v.get("VAT") or 0)
        v["GiaTriSauThue"] = int(v.get("GiaTriSauThue") or (v["GiaTriTruocThue"] + v["VAT"]))
        v["MaHopDong"] = v.get("MaHopDong") or None
        dup = self.db.one("SELECT 1 FROM HoaDonDauVao WHERE DaXoa = 0 AND MaThueNCC = ? AND COALESCE(KyHieuHD,'') = ?"
                          " AND SoHoaDonGoc = ? AND Ma != ?",
                          (v["MaThueNCC"], v.get("KyHieuHD") or "", v["SoHoaDonGoc"], ma or ""))
        if dup:
            raise ValidationError("Hóa đơn đầu vào (MST + ký hiệu + số) này đã có.")
        if ma:
            self.db.update("HoaDonDauVao", ma, v)
            return ma
        return self.db.insert("HoaDonDauVao", v)

    def delete_in_invoice(self, ma: str) -> None:
        self.db.soft_delete("HoaDonDauVao", ma)

    # ---- trang chủ
    def dashboard(self, today: str) -> dict:
        n_out = self.db.one("SELECT COUNT(*) n FROM HopDong WHERE DaXoa = 0 AND ChieuHopDong = 'Đầu ra'")["n"]
        n_in = self.db.one("SELECT COUNT(*) n FROM HopDong WHERE DaXoa = 0 AND ChieuHopDong = 'Đầu vào'")["n"]
        receivable = 0
        for hd in self.contracts("Đầu ra"):
            receivable += max(self.summary(hd["Ma"])["ConPhaiThu"], 0)
        overdue = self.db.query(
            "SELECT c.TenNoiDungCV, h.SoHopDong, c.NgayDuKienHT FROM CongViec c JOIN HopDong h ON h.Ma = c.MaHopDong"
            " WHERE c.DaXoa = 0 AND h.DaXoa = 0 AND c.TinhTrangCV NOT IN ('Hoàn thành', 'Đã nghiệm thu')"
            " AND c.NgayDuKienHT IS NOT NULL AND c.NgayDuKienHT < ? ORDER BY c.NgayDuKienHT", (today,))
        ending = self.db.query(
            "SELECT SoHopDong, TenHopDong, NgayKetThucDuKien FROM HopDong WHERE DaXoa = 0"
            " AND TinhTrangHopDong IN ('Chưa bắt đầu', 'Đang thực hiện') AND NgayKetThucDuKien IS NOT NULL"
            " AND NgayKetThucDuKien BETWEEN ? AND date(?, '+30 day') ORDER BY NgayKetThucDuKien", (today, today))
        return {"hd_dau_ra": n_out, "hd_dau_vao": n_in, "con_phai_thu": receivable,
                "cv_qua_han": overdue, "hd_sap_het_han": ending}
