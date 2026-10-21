"""Báo cáo hợp đồng (9 ngữ cảnh, 10 bộ lọc), báo cáo thu chi và xuất Excel."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .contracts import ContractService, payment_status
from .db import Database

CONTEXTS = [
    ("loai_hd", "Theo loại hợp đồng"),
    ("chu_dau_tu", "Theo chủ đầu tư"),
    ("nhom_cdt", "Theo nhóm chủ đầu tư"),
    ("hop_dong", "Theo hợp đồng"),
    ("hoa_don_ra", "Theo hóa đơn đã xuất"),
    ("da_thanh_toan", "Theo hợp đồng đã được thanh toán"),
    ("nha_cung_cap", "Theo nhà cung cấp"),
    ("hoa_don_vao", "Theo hóa đơn đầu vào"),
    ("thoi_gian", "Theo thời gian (tháng / quý / năm)"),
]
CONTEXT_LABELS = dict(CONTEXTS)

MONEY, TEXT, PCT, INT = "money", "text", "pct", "int"


@dataclass
class ReportFilters:
    tu: str | None = None                 # 1. khoảng thời gian
    den: str | None = None
    tinh_theo: str = "NgayKy"             # 2. NgayKy / NgayHoaDon / NgayTienVe
    chieu: str | None = None              # 3. Đầu ra / Đầu vào / None = tất cả
    loai_hd: list[str] = field(default_factory=list)       # 4
    doi_tac: list[str] = field(default_factory=list)       # 5
    nhom: list[str] = field(default_factory=list)          # 6
    tinh_trang: list[str] = field(default_factory=list)    # 7
    tt_thanh_toan: str | None = None      # 8. Chưa thu / Thu một phần / Đã thu đủ / Còn phải thu
    tt_xuat_hd: str | None = None         # 9. Chưa xuất / Xuất một phần / Đã xuất đủ
    co_hd_lien_ket: bool | None = None    # 10. hóa đơn đầu vào có / không có hợp đồng
    che_do: str = "luy_ke"                # trong_ky / luy_ke
    ky: str = "thang"                     # cho ngữ cảnh thời gian: thang / quy / nam

    def describe(self) -> str:
        parts = []
        if self.tu or self.den:
            parts.append(f"Từ {fmt_date(self.tu) or '…'} đến {fmt_date(self.den) or '…'}")
        parts.append("Giá trị trong kỳ" if self.che_do == "trong_ky" else "Giá trị lũy kế")
        if self.chieu:
            parts.append("Hợp đồng " + self.chieu.lower())
        if self.tinh_trang:
            parts.append("Tình trạng: " + ", ".join(self.tinh_trang))
        if self.tt_thanh_toan:
            parts.append("Thanh toán: " + self.tt_thanh_toan)
        if self.tt_xuat_hd:
            parts.append("Xuất hóa đơn: " + self.tt_xuat_hd)
        return " · ".join(parts)


@dataclass
class Report:
    title: str
    columns: list[tuple[str, str, str]]   # (khóa, tiêu đề, kiểu)
    rows: list[dict]                      # dòng có "_kieu": "nhom" / "cong" để in đậm
    total: dict | None = None
    subtitle: str = ""
    sheet: str = "Báo cáo"


def fmt_date(s: str | None) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return s


def fmt_money(v) -> str:
    return f"{int(v or 0):,}".replace(",", ".")


def _sum(rows, keys) -> dict:
    return {k: sum(int(r.get(k) or 0) for r in rows if not r.get("_kieu")) for k in keys}


# ====================================================================== hợp đồng
class ContractReports:
    def __init__(self, db: Database):
        self.db = db
        self.cs = ContractService(db)

    def _window(self, f: ReportFilters) -> tuple[str | None, str | None]:
        return (f.tu, f.den) if f.che_do == "trong_ky" else (None, f.den)

    def _group_partners(self, groups: list[str]) -> set[str]:
        out: set[str] = set()
        for g in groups:
            out.update(self.cs.group_members(g))
        return out

    def contracts(self, f: ReportFilters, chieu: str | None = None) -> list[dict]:
        where, params = [], []
        chieu = chieu or f.chieu
        if chieu:
            where.append("ChieuHopDong = ?")
            params.append(chieu)
        if f.loai_hd:
            where.append(f"MaLoaiHD IN ({','.join('?' * len(f.loai_hd))})")
            params += f.loai_hd
        partners = set(f.doi_tac)
        if f.nhom:
            members = self._group_partners(f.nhom)
            partners = partners & members if partners else members
            if not partners:
                return []
        if partners:
            where.append(f"MaDoiTac IN ({','.join('?' * len(partners))})")
            params += list(partners)
        if f.tinh_trang:
            where.append(f"TinhTrangHopDong IN ({','.join('?' * len(f.tinh_trang))})")
            params += f.tinh_trang
        if f.tinh_theo == "NgayKy" and f.che_do == "trong_ky" and f.tu:
            where.append("NgayKy >= ?")
            params.append(f.tu)
        if f.den:
            where.append("NgayKy <= ?")
            params.append(f.den)
        types = {t["Ma"]: t["TenLoaiHD"] for t in self.db.query("SELECT Ma, TenLoaiHD FROM LoaiHopDong")}
        tu, den = self._window(f)
        out = []
        for hd in self.db.active("HopDong", " AND ".join(where), params, "NgayKy, SoHopDong"):
            s = self.cs.summary(hd["Ma"], tu, den)
            if f.che_do == "trong_ky":
                s["GiaTriHD"] = hd["GiaTriTongSauThue"]
            row = dict(hd)
            row.update(s)
            row["LoaiHD"] = types.get(hd["MaLoaiHD"], "(Chưa phân loại)")
            if f.tt_thanh_toan:
                if f.tt_thanh_toan == "Còn phải thu":
                    if s["ConPhaiThu"] <= 0:
                        continue
                elif s["TinhTrangThanhToan"] != f.tt_thanh_toan:
                    continue
            if f.tt_xuat_hd and s["TinhTrangXuatHD"] != f.tt_xuat_hd:
                continue
            out.append(row)
        return out

    def build(self, context: str, f: ReportFilters) -> Report:
        rep = getattr(self, "_ctx_" + context)(f)
        rep.subtitle = f.describe()
        return rep

    # 1
    def _ctx_loai_hd(self, f):
        groups: dict[tuple, dict] = {}
        for c in self.contracts(f):
            g = groups.setdefault((c["LoaiHD"], c["ChieuHopDong"]), {
                "LoaiHD": c["LoaiHD"], "Chieu": c["ChieuHopDong"], "SoHD": 0, "GiaTriHD": 0, "DaXuatHD": 0,
                "ConPhaiXuat": 0, "DaThanhToan": 0, "ConPhaiThu": 0})
            g["SoHD"] += 1
            for k in ("GiaTriHD", "DaXuatHD", "ConPhaiXuat", "DaThanhToan", "ConPhaiThu"):
                g[k] += c[k]
        rows = sorted(groups.values(), key=lambda r: (r["Chieu"], r["LoaiHD"]))
        for r in rows:
            r["PhanTramTT"] = round(r["DaThanhToan"] * 100 / r["GiaTriHD"], 1) if r["GiaTriHD"] else 0.0
        keys = ["SoHD", "GiaTriHD", "DaXuatHD", "ConPhaiXuat", "DaThanhToan", "ConPhaiThu"]
        total = _sum(rows, keys)
        total["LoaiHD"] = "TỔNG CỘNG"
        return Report("Báo cáo tổng hợp theo loại hợp đồng", [
            ("LoaiHD", "Loại hợp đồng", TEXT), ("Chieu", "Chiều", TEXT), ("SoHD", "Số HĐ", INT),
            ("GiaTriHD", "Tổng giá trị HĐ", MONEY), ("DaXuatHD", "Đã xuất hóa đơn", MONEY),
            ("ConPhaiXuat", "Còn phải xuất", MONEY), ("DaThanhToan", "Đã thanh toán", MONEY),
            ("ConPhaiThu", "Còn phải thu", MONEY), ("PhanTramTT", "% thanh toán", PCT)], rows, total)

    def _partner_rows(self, contracts):
        groups: dict[str, dict] = {}
        for c in contracts:
            key = c["MaDoiTac"] or c["TenDoiTac"]
            g = groups.setdefault(key, {"TenDoiTac": c["TenDoiTac"], "MST": c["MaThueDoiTac"] or "", "SoHD": 0,
                                        "GiaTriHD": 0, "DaXuatHD": 0, "DaThanhToan": 0, "ConPhaiThu": 0,
                                        "LanThuGanNhat": ""})
            g["SoHD"] += 1
            for k in ("GiaTriHD", "DaXuatHD", "DaThanhToan", "ConPhaiThu"):
                g[k] += c[k]
            g["LanThuGanNhat"] = max(g["LanThuGanNhat"], c["LanThuGanNhat"])
        return sorted(groups.values(), key=lambda r: r["TenDoiTac"].lower())

    PARTNER_COLS = [("TenDoiTac", "Chủ đầu tư", TEXT), ("MST", "MST", TEXT), ("SoHD", "Số HĐ", INT),
                    ("GiaTriHD", "Tổng giá trị HĐ", MONEY), ("DaXuatHD", "Đã xuất hóa đơn", MONEY),
                    ("DaThanhToan", "Đã thanh toán", MONEY), ("ConPhaiThu", "Còn phải thu", MONEY),
                    ("LanThuGanNhat", "Lần thu gần nhất", "date")]

    # 2
    def _ctx_chu_dau_tu(self, f):
        rows = self._partner_rows(self.contracts(f, "Đầu ra"))
        total = _sum(rows, ["SoHD", "GiaTriHD", "DaXuatHD", "DaThanhToan", "ConPhaiThu"])
        total["TenDoiTac"] = "TỔNG CỘNG"
        return Report("Báo cáo theo chủ đầu tư", self.PARTNER_COLS, rows, total)

    # 3
    def _ctx_nhom_cdt(self, f):
        groups = [g for g in self.cs.groups() if not f.nhom or g["Ma"] in f.nhom]
        rows, keys = [], ["SoHD", "GiaTriHD", "DaXuatHD", "DaThanhToan", "ConPhaiThu"]
        all_rows = []
        for g in groups:
            sub = ReportFilters(**{**f.__dict__, "nhom": [g["Ma"]]})
            members = self._partner_rows(self.contracts(sub, "Đầu ra"))
            rows.append({"_kieu": "nhom", "Nhom": g["TenNhom"]})
            for m in members:
                m["Nhom"] = ""
                rows.append(m)
            st = _sum(members, keys)
            st.update({"_kieu": "cong", "Nhom": f"Cộng nhóm {g['TenNhom']}"})
            rows.append(st)
            all_rows += members
        total = _sum(all_rows, keys)
        total["Nhom"] = "TỔNG CỘNG"
        return Report("Báo cáo theo nhóm chủ đầu tư", [("Nhom", "Nhóm", TEXT)] + self.PARTNER_COLS[:-1], rows, total)

    # 4
    def _ctx_hop_dong(self, f):
        rows = self.contracts(f)
        for r in rows:
            r["NgayKyF"] = r["NgayKy"]
        keys = ["GiaTriHD", "DaXuatHD", "ConPhaiXuat", "DaThanhToan", "ConPhaiThu"]
        total = _sum(rows, keys)
        total["SoHopDong"] = "TỔNG CỘNG"
        return Report("Báo cáo theo hợp đồng", [
            ("SoHopDong", "Số HĐ", TEXT), ("TenHopDong", "Tên hợp đồng", TEXT), ("TenDoiTac", "Đối tác", TEXT),
            ("LoaiHD", "Loại HĐ", TEXT), ("ChieuHopDong", "Chiều", TEXT), ("NgayKy", "Ngày ký", "date"),
            ("TinhTrangHopDong", "Tình trạng", TEXT), ("GiaTriHD", "Giá trị HĐ", MONEY),
            ("DaXuatHD", "Đã xuất hóa đơn", MONEY), ("ConPhaiXuat", "Còn phải xuất", MONEY),
            ("DaThanhToan", "Đã thanh toán", MONEY), ("ConPhaiThu", "Còn phải thu", MONEY),
            ("PhanTramTT", "% thanh toán", PCT)], rows, total)

    # 5
    def _ctx_hoa_don_ra(self, f):
        ids = {c["Ma"]: c for c in self.contracts(ReportFilters(**{**f.__dict__, "tt_thanh_toan": None,
                                                                    "tt_xuat_hd": None}), "Đầu ra")}
        rows = []
        for inv in self.cs.out_invoices():
            c = ids.get(inv["MaHopDong"])
            if not c or inv["TinhChatHD"] == "Đã hủy":
                continue
            if f.tu and inv["NgayXuatHD"] < f.tu or f.den and inv["NgayXuatHD"] > f.den:
                continue
            paid = self.cs.invoice_paid(inv["Ma"])
            status = payment_status(inv["GiaTriSauThue"], paid)
            if f.tt_thanh_toan and (status != f.tt_thanh_toan if f.tt_thanh_toan != "Còn phải thu"
                                    else inv["GiaTriSauThue"] - paid <= 0):
                continue
            rows.append({"KyHieuHD": inv["KyHieuHD"] or "", "SoHoaDon": inv["SoHoaDon"],
                         "NgayXuatHD": inv["NgayXuatHD"], "SoHopDong": c["SoHopDong"], "TenDoiTac": c["TenDoiTac"],
                         "GiaTriTruocThue": inv["GiaTriTruocThue"], "VAT": inv["VAT"],
                         "GiaTriSauThue": inv["GiaTriSauThue"], "DaThu": paid,
                         "ConPhaiThu": inv["GiaTriSauThue"] - paid, "TinhTrang": status})
        rows.sort(key=lambda r: (r["NgayXuatHD"], r["SoHoaDon"]))
        total = _sum(rows, ["GiaTriTruocThue", "VAT", "GiaTriSauThue", "DaThu", "ConPhaiThu"])
        total["KyHieuHD"] = "TỔNG CỘNG"
        return Report("Báo cáo hóa đơn đầu ra đã xuất", [
            ("KyHieuHD", "Ký hiệu", TEXT), ("SoHoaDon", "Số hóa đơn", TEXT), ("NgayXuatHD", "Ngày lập", "date"),
            ("SoHopDong", "Số HĐ", TEXT), ("TenDoiTac", "Chủ đầu tư", TEXT),
            ("GiaTriTruocThue", "Trước thuế", MONEY), ("VAT", "VAT", MONEY), ("GiaTriSauThue", "Sau thuế", MONEY),
            ("DaThu", "Đã thu", MONEY), ("ConPhaiThu", "Còn phải thu", MONEY),
            ("TinhTrang", "Tình trạng thanh toán", TEXT)], rows, total)

    # 6
    def _ctx_da_thanh_toan(self, f):
        contracts = self.contracts(ReportFilters(**{**f.__dict__, "tt_thanh_toan": None}), "Đầu ra")
        rows, detail = [], []
        invoices = {i["Ma"]: i["SoHoaDon"] for i in self.db.query("SELECT Ma, SoHoaDon FROM HoaDonDauRa")}
        for c in contracts:
            pays = [p for p in self.cs.payments(c["Ma"])
                    if (not f.tu or p["NgayThanhToan"] >= f.tu) and (not f.den or p["NgayThanhToan"] <= f.den)]
            if not pays:
                continue
            rows.append({"_kieu": "nhom", "SoHopDong": f"{c['SoHopDong']} – {c['TenHopDong']}"})
            luy_ke = self.db.one("SELECT COALESCE(SUM(SoTien),0) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND MaHopDong = ?"
                                 " AND NgayThanhToan < ?", (c["Ma"], pays[0]["NgayThanhToan"]))["s"]
            for p in pays:
                luy_ke += p["SoTien"]
                r = {"SoHopDong": c["SoHopDong"], "TenDoiTac": c["TenDoiTac"], "NgayThanhToan": p["NgayThanhToan"],
                     "SoTien": p["SoTien"], "HinhThuc": p["HinhThuc"], "SoThamChieuSK": p["SoThamChieuSK"] or "",
                     "SoHoaDon": invoices.get(p["MaHDDauRa"], ""), "LuyKe": luy_ke}
                rows.append(r)
                detail.append(r)
            rows.append({"_kieu": "cong", "SoHopDong": "Cộng hợp đồng", "SoTien": sum(p["SoTien"] for p in pays)})
        total = _sum(detail, ["SoTien"])
        total["SoHopDong"] = "TỔNG CỘNG"
        return Report("Báo cáo hợp đồng đã được thanh toán", [
            ("SoHopDong", "Số HĐ", TEXT), ("TenDoiTac", "Chủ đầu tư", TEXT), ("NgayThanhToan", "Ngày tiền về", "date"),
            ("SoTien", "Số tiền", MONEY), ("HinhThuc", "Hình thức", TEXT), ("SoThamChieuSK", "Số sao kê", TEXT),
            ("SoHoaDon", "Số hóa đơn", TEXT), ("LuyKe", "Lũy kế đã thu của HĐ", MONEY)], rows, total)

    def _in_invoices(self, f) -> list:
        out = []
        for inv in self.cs.in_invoices():
            if f.tu and inv["NgayNhanHD"] < f.tu or f.den and inv["NgayNhanHD"] > f.den:
                continue
            if f.co_hd_lien_ket is True and not inv["MaHopDong"]:
                continue
            if f.co_hd_lien_ket is False and inv["MaHopDong"]:
                continue
            out.append(inv)
        return out

    # 7
    def _ctx_nha_cung_cap(self, f):
        groups: dict[str, dict] = {}

        def g_for(mst, name):
            key = mst or name.lower()
            return groups.setdefault(key, {"TenNCC": name, "MST": mst or "", "SoHD": 0, "GiaTriHD": 0,
                                           "DaNhanHD": 0, "MuaTrucTiep": 0, "TruocThue": 0, "VAT": 0, "SauThue": 0})
        for c in self.contracts(f, "Đầu vào"):
            g = g_for(c["MaThueDoiTac"], c["TenDoiTac"])
            g["SoHD"] += 1
            g["GiaTriHD"] += c["GiaTriHD"]
        for inv in self._in_invoices(f):
            g = g_for(inv["MaThueNCC"], inv["TenNhaCungCap"])
            if inv["MaHopDong"]:
                g["DaNhanHD"] += inv["GiaTriSauThue"]
            else:
                g["MuaTrucTiep"] += inv["GiaTriSauThue"]
            g["TruocThue"] += inv["GiaTriTruocThue"]
            g["VAT"] += inv["VAT"]
            g["SauThue"] += inv["GiaTriSauThue"]
        rows = sorted(groups.values(), key=lambda r: r["TenNCC"].lower())
        keys = ["SoHD", "GiaTriHD", "DaNhanHD", "MuaTrucTiep", "TruocThue", "VAT", "SauThue"]
        total = _sum(rows, keys)
        total["TenNCC"] = "TỔNG CỘNG"
        return Report("Báo cáo theo nhà cung cấp", [
            ("TenNCC", "Nhà cung cấp", TEXT), ("MST", "MST", TEXT), ("SoHD", "Số HĐ đầu vào", INT),
            ("GiaTriHD", "Giá trị HĐ", MONEY), ("DaNhanHD", "Đã nhận hóa đơn (theo HĐ)", MONEY),
            ("MuaTrucTiep", "Hóa đơn mua trực tiếp", MONEY), ("TruocThue", "Tổng trước thuế", MONEY),
            ("VAT", "VAT", MONEY), ("SauThue", "Sau thuế", MONEY)], rows, total)

    # 8
    def _ctx_hoa_don_vao(self, f):
        contracts = {r["Ma"]: r["SoHopDong"] for r in self.db.query("SELECT Ma, SoHopDong FROM HopDong")}
        rows = [{"KyHieuHD": i["KyHieuHD"] or "", "SoHoaDonGoc": i["SoHoaDonGoc"], "NgayNhanHD": i["NgayNhanHD"],
                 "TenNhaCungCap": i["TenNhaCungCap"], "MaThueNCC": i["MaThueNCC"],
                 "SoHopDong": contracts.get(i["MaHopDong"], "") if i["MaHopDong"] else "(Mua trực tiếp)",
                 "GiaTriTruocThue": i["GiaTriTruocThue"], "VAT": i["VAT"], "GiaTriSauThue": i["GiaTriSauThue"]}
                for i in self._in_invoices(f)]
        rows.sort(key=lambda r: (r["NgayNhanHD"], r["SoHoaDonGoc"]))
        total = _sum(rows, ["GiaTriTruocThue", "VAT", "GiaTriSauThue"])
        total["KyHieuHD"] = "TỔNG CỘNG"
        return Report("Báo cáo hóa đơn đầu vào", [
            ("KyHieuHD", "Ký hiệu", TEXT), ("SoHoaDonGoc", "Số hóa đơn", TEXT), ("NgayNhanHD", "Ngày", "date"),
            ("TenNhaCungCap", "Nhà cung cấp", TEXT), ("MaThueNCC", "MST", TEXT), ("SoHopDong", "Số HĐ đầu vào", TEXT),
            ("GiaTriTruocThue", "Trước thuế", MONEY), ("VAT", "VAT", MONEY), ("GiaTriSauThue", "Sau thuế", MONEY)],
            rows, total)

    # 9
    def _ctx_thoi_gian(self, f):
        den = f.den or date.today().isoformat()
        tu = f.tu or den[:4] + "-01-01"
        periods = split_periods(tu, den, f.ky)
        rows = []
        base = ReportFilters(**{**f.__dict__, "tu": None, "den": None, "che_do": "luy_ke"})
        contracts = self.contracts(base)
        ids = [c["Ma"] for c in contracts if c["ChieuHopDong"] == "Đầu ra"]
        for label, a, b in periods:
            ky_moi = [c for c in contracts if a <= c["NgayKy"] <= b]
            inv = self.db.one(
                f"SELECT COALESCE(SUM(GiaTriSauThue),0) s FROM HoaDonDauRa WHERE DaXoa = 0 AND TinhChatHD != 'Đã hủy'"
                f" AND NgayXuatHD BETWEEN ? AND ? AND MaHopDong IN ({','.join('?' * len(ids)) or 'NULL'})",
                (a, b, *ids))["s"]
            paid = self.db.one(
                f"SELECT COALESCE(SUM(SoTien),0) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND NgayThanhToan BETWEEN ? AND ?"
                f" AND MaHopDong IN ({','.join('?' * len(ids)) or 'NULL'})", (a, b, *ids))["s"]
            vin = self.db.one("SELECT COALESCE(SUM(GiaTriSauThue),0) s FROM HoaDonDauVao WHERE DaXoa = 0"
                              " AND NgayNhanHD BETWEEN ? AND ?", (a, b))["s"]
            rows.append({"Ky": label, "SoHDMoi": len(ky_moi), "GiaTriMoi": sum(c["GiaTriTongSauThue"] for c in ky_moi),
                         "HoaDonRa": inv, "DaThu": paid, "HoaDonVao": vin})
        total = _sum(rows, ["SoHDMoi", "GiaTriMoi", "HoaDonRa", "DaThu", "HoaDonVao"])
        total["Ky"] = "TỔNG CỘNG"
        return Report("Báo cáo theo thời gian", [
            ("Ky", "Kỳ", TEXT), ("SoHDMoi", "Số HĐ ký mới", INT), ("GiaTriMoi", "Giá trị ký mới", MONEY),
            ("HoaDonRa", "Hóa đơn đã xuất", MONEY), ("DaThu", "Tiền đã thu", MONEY),
            ("HoaDonVao", "Hóa đơn đầu vào", MONEY)], rows, total)


def split_periods(tu: str, den: str, ky: str) -> list[tuple[str, str, str]]:
    """Chia [tu, den] thành các tháng / quý / năm: (nhãn, ngày đầu, ngày cuối)."""
    start, end = date.fromisoformat(tu), date.fromisoformat(den)
    out = []
    cur = start
    while cur <= end:
        if ky == "nam":
            p_start, p_end, label = date(cur.year, 1, 1), date(cur.year, 12, 31), f"Năm {cur.year}"
        elif ky == "quy":
            q = (cur.month - 1) // 3
            p_start = date(cur.year, q * 3 + 1, 1)
            p_end = _month_end(cur.year, q * 3 + 3)
            label = f"Quý {q + 1}/{cur.year}"
        else:
            p_start = date(cur.year, cur.month, 1)
            p_end = _month_end(cur.year, cur.month)
            label = f"Tháng {cur.month:02d}/{cur.year}"
        a, b = max(p_start, start), min(p_end, end)
        out.append((label, a.isoformat(), b.isoformat()))
        cur = p_end + timedelta(days=1)
    return out


def _month_end(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def period_range(ky: str, ref: date) -> tuple[str, str]:
    """Khoảng ngày của kỳ (ngay / thang / quy / nam) chứa ngày ref."""
    if ky == "ngay":
        return ref.isoformat(), ref.isoformat()
    if ky == "nam":
        return date(ref.year, 1, 1).isoformat(), date(ref.year, 12, 31).isoformat()
    if ky == "quy":
        q = (ref.month - 1) // 3
        return date(ref.year, q * 3 + 1, 1).isoformat(), _month_end(ref.year, q * 3 + 3).isoformat()
    return date(ref.year, ref.month, 1).isoformat(), _month_end(ref.year, ref.month).isoformat()


# ====================================================================== thu chi
class FinanceReports:
    def __init__(self, store, db: Database):
        self.store = store
        self.db = db
        self.cs = ContractService(db)

    def ledger(self, tu: str, den: str) -> Report:
        opening = self.store.balance((date.fromisoformat(tu) - timedelta(days=1)).isoformat())
        rows = [{"_kieu": "nhom", "Ngay": "", "NoiDung": "Tồn đầu kỳ", "Ton": opening}]
        ton = opening
        items = self.store.entries(tu, den)
        for e in items:
            ton += e["Thu"] - e["Chi"]
            rows.append({"Ngay": e["Ngay"], "SoPhieu": e["SoPhieu"], "NoiDung": e["NoiDung"], "DuAn": e["TenDuAn"],
                         "KhoanMuc": e["KhoanMuc"], "Quy": e["Quy"], "Thu": e["Thu"], "Chi": e["Chi"], "Ton": ton})
        total = {"NoiDung": "CỘNG PHÁT SINH / TỒN CUỐI KỲ", "Thu": sum(e["Thu"] for e in items),
                 "Chi": sum(e["Chi"] for e in items), "Ton": ton}
        return Report("Sổ thu chi", [
            ("Ngay", "Ngày", "date"), ("SoPhieu", "Số phiếu / Số HĐ", TEXT), ("NoiDung", "Nội dung", TEXT),
            ("DuAn", "Dự án", TEXT), ("KhoanMuc", "Khoản mục", TEXT), ("Quy", "Quỹ", TEXT),
            ("Thu", "Thu", MONEY), ("Chi", "Chi", MONEY), ("Ton", "Tồn", MONEY)], rows, total,
            subtitle=f"Từ {fmt_date(tu)} đến {fmt_date(den)}", sheet="Sổ thu chi")

    def summary(self, tu: str, den: str, ky: str = "thang") -> Report:
        """Bảng tổng hợp thu chi: chỉ tiêu theo hàng × kỳ theo cột + Tổng."""
        cols_ky = "thang" if ky in ("thang", "quy", "nam") else ky
        periods = split_periods(tu, den, cols_ky)
        if ky == "thang" and len(periods) == 1:
            periods = []
        per_items = [self.store.entries(a, b) for _, a, b in periods]
        all_items = self.store.entries(tu, den)
        keys = [f"k{i}" for i in range(len(periods))] + ["Tong"]

        def line(label, pred, kind="Thu", style=None):
            r = {"ChiTieu": label}
            for k, items in zip(keys, per_items + [all_items]):
                r[k] = sum(i[kind] for i in items if pred(i))
            if style:
                r["_kieu"] = style
            return r

        rows = [line("A. TỔNG THU (1 + 2)", lambda i: True, "Thu", "nhom"),
                line("   1. Thu từ hợp đồng", lambda i: i["Nguon"] == "Hợp đồng", "Thu", "cong")]
        for name in sorted({i["TenDuAn"] for i in all_items if i["Nguon"] == "Hợp đồng"}):
            rows.append(line(f"         {name}", lambda i, n=name: i["Nguon"] == "Hợp đồng" and i["TenDuAn"] == n))
        rows.append(line("   2. Thu khác", lambda i: i["Nguon"] == "Phiếu" and i["LoaiThuChi"] == "Thu", "Thu", "cong"))
        for name in sorted({i["KhoanMuc"] for i in all_items if i["Nguon"] == "Phiếu" and i["LoaiThuChi"] == "Thu"}):
            rows.append(line(f"         {name}", lambda i, n=name: i["Nguon"] == "Phiếu" and i["LoaiThuChi"] == "Thu"
                             and i["KhoanMuc"] == n))
        rows.append(line("B. TỔNG CHI (1 + 2)", lambda i: True, "Chi", "nhom"))
        rows.append(line("   1. Chi dự án", lambda i: i["PhanLoai"] == "Chi dự án", "Chi", "cong"))
        for name in sorted({i["TenDuAn"] for i in all_items if i["PhanLoai"] == "Chi dự án"}):
            rows.append(line(f"         {name}", lambda i, n=name: i["PhanLoai"] == "Chi dự án" and i["TenDuAn"] == n,
                             "Chi"))
        rows.append(line("   2. Chi hoạt động văn phòng", lambda i: i["PhanLoai"] == "Chi văn phòng", "Chi", "cong"))
        roots = {}
        for i in all_items:
            if i["PhanLoai"] == "Chi văn phòng":
                root = self.store.category_root(i["MaKhoanMuc"])
                roots[root] = self.store.category_path(root)
        for root, name in sorted(roots.items(), key=lambda x: x[1]):
            rows.append(line(f"         {name}", lambda i, r=root: i["PhanLoai"] == "Chi văn phòng"
                             and self.store.category_root(i["MaKhoanMuc"]) == r, "Chi"))
        thu, chi = rows[0], next(r for r in rows if r["ChiTieu"].startswith("B."))
        rows.append({"ChiTieu": "C. CHÊNH LỆCH THU - CHI (A − B)", "_kieu": "nhom",
                     **{k: thu[k] - chi[k] for k in keys}})
        ton = {"ChiTieu": "D. TỒN QUỸ CUỐI KỲ", "_kieu": "nhom"}
        for k, (_, _, b) in zip(keys, periods):
            ton[k] = self.store.balance(b)
        ton["Tong"] = self.store.balance(den)
        rows.append(ton)
        cols = [("ChiTieu", "Chỉ tiêu", TEXT)] + [(k, p[0], MONEY) for k, p in zip(keys, periods)] + \
               [("Tong", "Tổng kỳ", MONEY)]
        title = {"thang": "Báo cáo thu chi tháng", "quy": "Báo cáo thu chi quý", "nam": "Báo cáo thu chi năm"}.get(
            ky, "Báo cáo thu chi")
        return Report(title, cols, rows, None, subtitle=f"Từ {fmt_date(tu)} đến {fmt_date(den)}", sheet="Tổng hợp")

    def expense_summary(self, tu: str, den: str) -> Report:
        items = [i for i in self.store.entries(tu, den, loai="Chi")]
        tree = self.store.category_tree("Chi", include_hidden=True)
        rows = []
        for cat, depth in tree:
            sub = self.store._descendants(cat["Ma"])
            da = sum(i["Chi"] for i in items if i["MaKhoanMuc"] in sub and i["PhanLoai"] == "Chi dự án")
            vp = sum(i["Chi"] for i in items if i["MaKhoanMuc"] in sub and i["PhanLoai"] == "Chi văn phòng")
            if da or vp:
                rows.append({"KhoanMuc": "      " * depth + cat["TenKhoanMuc"], "ChiDuAn": da, "ChiVanPhong": vp,
                             "Tong": da + vp, **({"_kieu": "cong"} if depth == 0 else {})})
        total = {"KhoanMuc": "TỔNG CHI", "ChiDuAn": sum(i["Chi"] for i in items if i["PhanLoai"] == "Chi dự án"),
                 "ChiVanPhong": sum(i["Chi"] for i in items if i["PhanLoai"] == "Chi văn phòng")}
        total["Tong"] = total["ChiDuAn"] + total["ChiVanPhong"]
        return Report("Tổng hợp chi theo khoản mục", [
            ("KhoanMuc", "Khoản mục", TEXT), ("ChiDuAn", "Chi dự án", MONEY), ("ChiVanPhong", "Chi văn phòng", MONEY),
            ("Tong", "Tổng", MONEY)], rows, total, subtitle=f"Từ {fmt_date(tu)} đến {fmt_date(den)}",
            sheet="Tổng hợp chi")

    def by_project(self, tu: str, den: str) -> Report:
        items = self.store.entries(tu, den)
        projects: dict[str, dict] = {}
        for hd in self.store.projects():
            s = self.cs.summary(hd["Ma"], None, den)
            projects[hd["Ma"]] = {"DuAn": f"{hd['SoHopDong']} – {hd['TenHopDong']}", "GiaTriHD": hd["GiaTriTongSauThue"],
                                  "DaThu": 0, "ConPhaiThu": s["GiaTriHD"] - s["DaThanhToan"], "ChiDuAn": 0}
        for i in items:
            key = i["MaDuAn"] or ("khac:" + (i["TenDuAn"] or ""))
            if i["Nguon"] == "Hợp đồng" or i["PhanLoai"] == "Chi dự án":
                p = projects.setdefault(key, {"DuAn": i["TenDuAn"] or "(Dự án khác)", "GiaTriHD": 0, "DaThu": 0,
                                              "ConPhaiThu": 0, "ChiDuAn": 0})
                p["DaThu"] += i["Thu"] if i["Nguon"] == "Hợp đồng" else 0
                p["ChiDuAn"] += i["Chi"]
        rows = [p for p in projects.values() if p["DaThu"] or p["ChiDuAn"]]
        for p in rows:
            p["ChenhLech"] = p["DaThu"] - p["ChiDuAn"]
        rows.sort(key=lambda p: p["DuAn"])
        total = _sum(rows, ["GiaTriHD", "DaThu", "ConPhaiThu", "ChiDuAn", "ChenhLech"])
        total["DuAn"] = "TỔNG CỘNG"
        return Report("Thu - chi theo dự án", [
            ("DuAn", "Dự án", TEXT), ("GiaTriHD", "Giá trị HĐ", MONEY), ("DaThu", "Đã thu trong kỳ", MONEY),
            ("ConPhaiThu", "Còn phải thu", MONEY), ("ChiDuAn", "Chi dự án trong kỳ", MONEY),
            ("ChenhLech", "Chênh lệch thu - chi", MONEY)], rows, total,
            subtitle=f"Từ {fmt_date(tu)} đến {fmt_date(den)}", sheet="Theo dự án")

    def package(self, kind: str, tu: str, den: str) -> list[Report]:
        """Bộ sheet cho từng loại báo cáo thu chi (ngay / thang / quy / nam / chi / du_an)."""
        if kind == "ngay":
            return [self.ledger(tu, den)]
        if kind in ("thang", "quy", "nam"):
            detail = self.ledger(tu, den)
            detail.sheet = "Chi tiết"
            return [self.summary(tu, den, kind), self.expense_summary(tu, den), detail]
        if kind == "chi":
            return [self.expense_summary(tu, den)]
        if kind == "du_an":
            return [self.by_project(tu, den)]
        raise ValueError(kind)


# ====================================================================== Excel
def export_xlsx(reports: Report | list[Report], path: str | Path, *, company: str = "", user: str = "",
                password: str | None = None) -> Path:
    """Xuất Excel. Có `password` thì file được mã hóa, phải nhập mật khẩu mới mở được (dùng cho thu chi)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if isinstance(reports, Report):
        reports = [reports]
    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="9DB3CC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="DCE8F5")
    group_fill = PatternFill("solid", fgColor="F2F6FB")
    used_names: set[str] = set()
    for rep in reports:
        name = rep.sheet[:31]
        n = 2
        while name in used_names:
            name = f"{rep.sheet[:28]} {n}"
            n += 1
        used_names.add(name)
        ws = wb.create_sheet(name)
        ncol = len(rep.columns)
        ws.cell(1, 1, company).font = Font(bold=True)
        ws.cell(2, 1, rep.title.upper()).font = Font(bold=True, size=14)
        ws.cell(3, 1, rep.subtitle)
        ws.cell(4, 1, f"Người xuất: {user} · Thời điểm xuất: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        ws.cell(4, 1).font = Font(italic=True, color="555555")
        hr = 6
        for c, (_, label, _) in enumerate([("STT", "STT", INT)] + rep.columns, start=1):
            cell = ws.cell(hr, c, label)
            cell.font = Font(bold=True)
            cell.fill = head_fill
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        r = hr + 1
        stt = 0
        for row in rep.rows + ([dict(rep.total, _kieu="tong")] if rep.total else []):
            kieu = row.get("_kieu")
            if not kieu:
                stt += 1
            ws.cell(r, 1, stt if not kieu else "").border = border
            for c, (key, _, kind) in enumerate(rep.columns, start=2):
                val = row.get(key)
                if kind == "date" and val:
                    try:
                        val = datetime.strptime(val[:10], "%Y-%m-%d")
                    except ValueError:
                        pass
                cell = ws.cell(r, c, val if val not in (None, "") else None)
                cell.border = border
                if kind == MONEY or kind == INT:
                    cell.number_format = "#,##0"
                elif kind == PCT:
                    cell.number_format = '0.0"%"'
                elif kind == "date":
                    cell.number_format = "DD/MM/YYYY"
                if kieu:
                    cell.font = Font(bold=True)
                    if kieu in ("nhom", "tong"):
                        cell.fill = group_fill
            r += 1
        ws.freeze_panes = ws.cell(hr + 1, 2)
        ws.column_dimensions["A"].width = 6
        for c, (key, label, kind) in enumerate(rep.columns, start=2):
            longest = max([len(str(label))] + [len(str(x.get(key) or "")) for x in rep.rows[:500]])
            width = 16 if kind in (MONEY, "date") else min(max(longest + 2, 10), 50)
            ws.column_dimensions[get_column_letter(c)].width = max(width, 12 if kind == INT else width)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(ncol + 1, 2))
    path = Path(path)
    if not password:
        wb.save(path)
        return path
    import io

    from msoffcrypto.format.ooxml import OOXMLFile

    plain = io.BytesIO()
    wb.save(plain)
    plain.seek(0)
    with open(path, "wb") as out:
        OOXMLFile(plain).encrypt(password, out)
    return path
