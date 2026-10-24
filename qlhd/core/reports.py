"""Báo cáo hợp đồng (9 ngữ cảnh, 10 bộ lọc), báo cáo thu chi và xuất Excel."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .contracts import (TAX_GROUP_TITLES, TAX_GROUPS, ContractService, duration_text, guess_tax_group,
                        payment_status)
from .db import Database
from .invoice_match import fold

CONTEXTS = [
    ("tong_hop", "Tổng hợp (toàn bộ thông tin hợp đồng)"),
    ("loai_hd", "Theo loại hợp đồng"),
    ("chu_dau_tu", "Theo chủ đầu tư"),
    ("nhom_cdt", "Theo nhóm chủ đầu tư"),
    ("hop_dong", "Theo hợp đồng"),
    ("hoa_don_ra", "Theo hóa đơn đầu ra đã xuất"),
    ("hoa_don_ra_nguoi_mua", "Hóa đơn đầu ra theo người mua (mỗi người một sheet)"),
    ("da_thanh_toan", "Theo hợp đồng đã được thanh toán"),
    ("nha_cung_cap", "Theo nhà cung cấp"),
    ("hoa_don_vao", "Bảng kê hóa đơn đầu vào (mua vào)"),
    ("hoa_don_ra_vao", "Hóa đơn đầu ra và đầu vào"),
    ("thoi_gian", "Theo thời gian (tháng / quý / năm)"),
]
CONTEXT_LABELS = dict(CONTEXTS)

TAX_MODES = [
    ("tach", "Cả hai nhóm, tách riêng chịu thuế / không chịu thuế (như bảng kê)"),
    ("lan", "Cả hai nhóm, xếp lẫn theo ngày hóa đơn"),
    ("Chịu thuế", "Chỉ hóa đơn chịu thuế GTGT"),
    ("Không chịu thuế", "Chỉ hóa đơn không chịu thuế GTGT"),
]
RELATIVE_PERIODS = [
    (None, "Tự chọn (từ ngày – đến ngày)"),
    ("thang_nay", "Tháng này"), ("quy_nay", "Quý này"), ("nam_nay", "Năm nay"),
    ("thang_truoc", "Tháng trước"), ("quy_truoc", "Quý trước"), ("nam_truoc", "Năm trước"),
]

DETAIL_LEVELS = [("gop", "Gộp (chỉ dòng tổng hợp)"), ("hop_dong", "Chi tiết từng hợp đồng"),
                 ("hoa_don", "Chi tiết đến từng hóa đơn")]
INVOICE_PERIODS = [(None, "Không chia kỳ"), ("thang", "Tháng"), ("quy", "Quý"), ("nam", "Năm")]

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
    nhom_thue: str = "tach"               # bảng kê đầu vào: tach / lan / Chịu thuế / Không chịu thuế
    nguoi_mua: list[str] = field(default_factory=list)     # hóa đơn đầu ra theo người mua (khóa người mua)
    gop_ra_vao: bool = False              # False: đầu ra và đầu vào mỗi loại một bảng
    ky_tuong_doi: str | None = None       # tháng này / quý trước…: tự tính từ – đến lúc xuất (mẫu xuất nhanh)
    tach_nam: bool = True                 # chia báo cáo theo năm ký hợp đồng (mỗi năm một nhóm, có dòng cộng năm)
    nam: int | None = None                # chỉ lấy hợp đồng ký trong năm này (dùng khi tách theo năm)
    muc_chi_tiet: str = "gop"             # gop / hop_dong (liệt kê từng hợp đồng) / hoa_don (đến từng hóa đơn)
    nhom_ky_hd: str | None = None         # hóa đơn chia nhóm theo thang / quy / nam (có dòng cộng từng kỳ)
    chi_cong_ky: bool = False             # hóa đơn: chỉ một dòng cộng cho mỗi kỳ (gộp), không liệt kê từng hóa đơn

    @classmethod
    def from_dict(cls, d: dict) -> "ReportFilters":
        """Đọc mẫu báo cáo đã lưu (bỏ qua khóa lạ, khóa thiếu lấy mặc định)."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def resolved(self, today: date | None = None) -> "ReportFilters":
        if not self.ky_tuong_doi:
            return self
        tu, den = relative_range(self.ky_tuong_doi, today or date.today())
        return ReportFilters(**{**self.__dict__, "tu": tu, "den": den})

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
        if self.muc_chi_tiet != "gop":
            parts.append(dict(DETAIL_LEVELS).get(self.muc_chi_tiet, ""))
        if self.nhom_ky_hd:
            parts.append(f"Hóa đơn theo {dict(INVOICE_PERIODS).get(self.nhom_ky_hd, '').lower()}"
                         + (" (chỉ dòng cộng)" if self.chi_cong_ky else ""))
        return " · ".join(parts)


@dataclass
class Report:
    title: str
    columns: list[tuple[str, str, str]]   # (khóa, tiêu đề, kiểu)
    rows: list[dict]                      # dòng có "_kieu": "nhom" / "cong" để in đậm; "_nhan": nhãn trải ngang
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


def _with(f: "ReportFilters", **kw) -> "ReportFilters":
    return ReportFilters(**{**f.__dict__, **kw})


def sheet_name(text: str) -> str:
    """Tên sheet Excel hợp lệ: bỏ ký tự cấm, tối đa 31 ký tự."""
    for ch in '[]:*?/\\':
        text = text.replace(ch, " ")
    return " ".join(text.split())[:31] or "Báo cáo"


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
        if f.nam:
            where.append("substr(NgayKy, 1, 4) = ?")
            params.append(str(f.nam))
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

    NO_YEAR_SPLIT = ("thoi_gian",)        # ngữ cảnh đã chia theo kỳ thời gian

    def build(self, context: str, f: ReportFilters) -> list[Report]:
        """Một ngữ cảnh có thể ra nhiều bảng (mỗi bảng một sheet khi xuất Excel)."""
        f = f.resolved()
        if f.tach_nam and f.nam is None and context not in self.NO_YEAR_SPLIT:
            return self._by_year(context, f)
        out = getattr(self, "_ctx_" + context)(f)
        reps = out if isinstance(out, list) else [out]
        for rep in reps:
            rep.subtitle = rep.subtitle or f.describe()
        return reps

    def years(self, f: ReportFilters) -> list[int]:
        """Các năm có hợp đồng (theo ngày ký) trong phạm vi lọc, và năm của hóa đơn đầu vào mua trực tiếp."""
        ys = {int(c["NgayKy"][:4]) for c in self.contracts(_with(f, nam=None)) if c["NgayKy"]}
        ys |= {int(i["NgayNhanHD"][:4]) for i in self._in_invoices(_with(f, nam=None))
               if not i["MaHopDong"] and i["NgayNhanHD"]}
        return sorted(ys)

    def _by_year(self, context: str, f: ReportFilters) -> list[Report]:
        """Tách báo cáo theo năm ký hợp đồng: trong mỗi bảng, từng năm một nhóm (tiêu đề NĂM …, các dòng,
        dòng Cộng năm), cuối bảng là tổng cộng các năm."""
        merged: dict[str, Report] = {}
        totals: dict[str, list[dict]] = {}
        for y in self.years(f):
            for rep in self.build(context, _with(f, nam=y)):
                if not any(not r.get("_kieu") for r in rep.rows):
                    continue
                first = rep.columns[0][0]
                m = merged.setdefault(rep.sheet, Report(rep.title, rep.columns, [], None, rep.subtitle, rep.sheet))
                m.rows.append({"_kieu": "nhom", "_nhan": f"NĂM {y}"})
                m.rows += rep.rows
                if rep.total:
                    m.rows.append(dict(rep.total, _kieu="cong", **{first: f"Cộng năm {y}"}))
                    totals.setdefault(rep.sheet, []).append(rep.total)
        out = []
        for sheet, m in merged.items():
            year_totals = totals.get(sheet)
            if year_totals:
                first = m.columns[0][0]
                kinds = {k: kind for k, _, kind in m.columns}
                keys = {k for t in year_totals for k, v in t.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool) and kinds.get(k) != PCT}
                m.total = {k: sum(t.get(k) or 0 for t in year_totals) for k in keys}
                m.total[first] = "TỔNG CỘNG"
            m.subtitle = (m.subtitle + " · " if m.subtitle else "") + "Tách theo năm ký hợp đồng"
            out.append(m)
        if not out:                      # không có dữ liệu: vẫn trả bảng rỗng như khi không tách năm
            return self.build(context, _with(f, tach_nam=False))
        return out

    def _by_direction(self, f, make) -> Report | list[Report]:
        """Đầu ra và đầu vào tách hai bảng, trừ khi người dùng chọn gộp chung hoặc đã lọc một chiều."""
        if f.chieu or f.gop_ra_vao:
            return make(f)
        out = []
        for chieu in ("Đầu ra", "Đầu vào"):
            rep = make(_with(f, chieu=chieu))
            rep.title += " – hợp đồng " + chieu.lower()
            rep.sheet = sheet_name(f"{rep.sheet} {chieu.lower()}")
            out.append(rep)
        return out

    # ---- đa ngữ cảnh: gộp hoặc chi tiết
    def _invoice_children(self, c: dict, f: ReportFilters, first: str, second: str | None = None) -> list[dict]:
        """Dòng chi tiết từng hóa đơn của một hợp đồng (đặt dưới dòng hợp đồng, không cộng vào tổng)."""
        out = []
        if c["ChieuHopDong"] == "Đầu ra":
            for inv in self.db.active("HoaDonDauRa", "MaHopDong = ? AND TinhChatHD != 'Đã hủy'", (c["Ma"],),
                                      "NgayXuatHD, SoHoaDon"):
                if f.tu and inv["NgayXuatHD"] < f.tu and f.che_do == "trong_ky" or f.den and inv["NgayXuatHD"] > f.den:
                    continue
                paid = self.cs.invoice_paid(inv["Ma"])
                status = payment_status(inv["GiaTriSauThue"], paid)
                if f.tt_thanh_toan and (status != f.tt_thanh_toan if f.tt_thanh_toan != "Còn phải thu"
                                        else inv["GiaTriSauThue"] - paid <= 0):
                    continue
                row = {"_kieu": "con", "MaHopDong": c["Ma"], first: f"      • Hóa đơn {inv['SoHoaDon']}",
                       "DaXuatHD": inv["GiaTriSauThue"], "DaThanhToan": paid, "ConPhaiThu": inv["GiaTriSauThue"] - paid,
                       "TinhTrangHopDong": status, "TinhTrangThanhToan": status, "NgayKy": inv["NgayXuatHD"],
                       "LanThuGanNhat": ""}
                if second:
                    row[second] = f"ngày {fmt_date(inv['NgayXuatHD'])} · {status.lower()}"
                out.append(row)
        else:
            for inv in self.db.active("HoaDonDauVao", "MaHopDong = ?", (c["Ma"],), "NgayNhanHD, SoHoaDonGoc"):
                if f.tu and inv["NgayNhanHD"] < f.tu and f.che_do == "trong_ky" or f.den and inv["NgayNhanHD"] > f.den:
                    continue
                status = "Đã thanh toán" if inv["SoThamChieuSK"] else "Chưa có sao kê"
                row = {"_kieu": "con", "MaHopDong": c["Ma"], first: f"      • Hóa đơn {inv['SoHoaDonGoc']}",
                       "DaXuatHD": inv["GiaTriSauThue"], "NgayKy": inv["NgayNhanHD"], "TinhTrangHopDong": status}
                if second:
                    row[second] = f"ngày {fmt_date(inv['NgayNhanHD'])} · {status.lower()}"
                out.append(row)
        return out

    def _contract_children(self, contracts: list[dict], f: ReportFilters, first: str,
                           second: str | None = None) -> list[dict]:
        """Dòng chi tiết từng hợp đồng (và hóa đơn của nó nếu chọn chi tiết đến hóa đơn) dưới một dòng tổng hợp."""
        out = []
        for c in contracts:
            row = {k: c.get(k) for k in ("GiaTriHD", "DaXuatHD", "ConPhaiXuat", "DaThanhToan", "ConPhaiThu",
                                         "PhanTramTT", "LanThuGanNhat", "TinhTrangThanhToan")}
            row.update({"_kieu": "con", "MaHopDong": c["Ma"], first: f"   ↳ {c['SoHopDong']} – {c['TenHopDong']}"})
            if second:
                row[second] = f"{c['TenDoiTac']} · ký {fmt_date(c['NgayKy'])}"
            out.append(row)
            if f.muc_chi_tiet == "hoa_don":
                out += self._invoice_children(c, f, first, second)
        return out

    def _expand(self, rep: Report, f: ReportFilters, contracts: list[dict], key_of, first: str,
                second: str | None = None) -> Report:
        """Gộp: giữ dòng tổng hợp. Chi tiết: dưới mỗi dòng tổng hợp liệt kê hợp đồng (và hóa đơn) của nó."""
        if f.muc_chi_tiet == "gop":
            return rep
        rows = []
        for r in rep.rows:
            rows.append(r)
            if r.get("_kieu"):
                continue
            rows += self._contract_children([c for c in contracts if key_of(c) == key_of(r)], f, first, second)
        rep.rows = rows
        return rep

    def _by_invoice_period(self, rep: Report, f: ReportFilters, date_key: str, sum_keys: list[str],
                           number_key: str) -> Report:
        """Hóa đơn chia theo tháng / quý / năm: mỗi kỳ một nhóm có dòng cộng; hoặc chỉ một dòng cộng mỗi kỳ (gộp)."""
        if not f.nhom_ky_hd:
            return rep
        first = rep.columns[0][0]
        items = [r for r in rep.rows if not r.get("_kieu")]
        groups: dict[str, list[dict]] = {}
        for r in items:
            groups.setdefault(period_label(r[date_key], f.nhom_ky_hd), []).append(r)
        rows = []
        for label, part in sorted(groups.items(), key=lambda g: g[1][0][date_key]):
            sub = _sum(part, sum_keys)
            if f.chi_cong_ky:
                rows.append(dict(sub, **{first: label, number_key: f"{len(part)} hóa đơn", date_key: part[0][date_key],
                                         "_dem": len(part)}))
                continue
            rows.append({"_kieu": "nhom", "_nhan": f"{label} ({len(part)} hóa đơn)"})
            rows += part
            rows.append(dict(sub, _kieu="cong", **{first: f"Cộng {label.lower()}"}))
        rep.rows = rows
        return rep

    # 1
    def _ctx_loai_hd(self, f):
        return self._by_direction(f, self._loai_hd)

    def _loai_hd(self, f):
        groups: dict[tuple, dict] = {}
        contracts = self.contracts(f)
        for c in contracts:
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
        return self._expand(Report("Báo cáo tổng hợp theo loại hợp đồng", [
            ("LoaiHD", "Loại hợp đồng", TEXT), ("Chieu", "Chiều", TEXT), ("SoHD", "Số HĐ", INT),
            ("GiaTriHD", "Tổng giá trị HĐ", MONEY), ("DaXuatHD", "Đã xuất hóa đơn", MONEY),
            ("ConPhaiXuat", "Còn phải xuất", MONEY), ("DaThanhToan", "Đã thanh toán", MONEY),
            ("ConPhaiThu", "Còn phải thu", MONEY), ("PhanTramTT", "% thanh toán", PCT)], rows, total),
            f, contracts, lambda r: (r["LoaiHD"], r.get("Chieu") or r.get("ChieuHopDong")), "LoaiHD")

    def _partner_rows(self, contracts):
        groups: dict[str, dict] = {}
        for c in contracts:
            key = c["MaDoiTac"] or c["TenDoiTac"]
            g = groups.setdefault(key, {"_khoa": key, "TenDoiTac": c["TenDoiTac"], "MST": c["MaThueDoiTac"] or "", "SoHD": 0,
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
        contracts = self.contracts(f, "Đầu ra")
        rows = self._partner_rows(contracts)
        total = _sum(rows, ["SoHD", "GiaTriHD", "DaXuatHD", "DaThanhToan", "ConPhaiThu"])
        total["TenDoiTac"] = "TỔNG CỘNG"
        return self._expand(Report("Báo cáo theo chủ đầu tư", self.PARTNER_COLS, rows, total), f, contracts,
                            lambda r: r.get("_khoa") or r.get("MaDoiTac") or r["TenDoiTac"], "TenDoiTac", "MST")

    # 3
    def _ctx_nhom_cdt(self, f):
        groups = [g for g in self.cs.groups() if not f.nhom or g["Ma"] in f.nhom]
        rows, keys = [], ["SoHD", "GiaTriHD", "DaXuatHD", "DaThanhToan", "ConPhaiThu"]
        all_rows = []
        for g in groups:
            sub = ReportFilters(**{**f.__dict__, "nhom": [g["Ma"]]})
            sub_contracts = self.contracts(sub, "Đầu ra")
            members = self._partner_rows(sub_contracts)
            rows.append({"_kieu": "nhom", "Nhom": g["TenNhom"]})
            for m in members:
                m["Nhom"] = ""
                rows.append(m)
                if f.muc_chi_tiet != "gop":
                    rows += self._contract_children(
                        [c for c in sub_contracts if (c["MaDoiTac"] or c["TenDoiTac"]) == m["_khoa"]], f, "TenDoiTac",
                        "MST")
            st = _sum(members, keys)
            st.update({"_kieu": "cong", "Nhom": f"Cộng nhóm {g['TenNhom']}"})
            rows.append(st)
            all_rows += members
        total = _sum(all_rows, keys)
        total["Nhom"] = "TỔNG CỘNG"
        return Report("Báo cáo theo nhóm chủ đầu tư", [("Nhom", "Nhóm", TEXT)] + self.PARTNER_COLS[:-1], rows, total)

    # 4
    def _ctx_hop_dong(self, f):
        return self._by_direction(f, self._hop_dong)

    def _hop_dong(self, f):
        contracts = self.contracts(f)
        rows = []
        for r in contracts:
            r["NgayKyF"] = r["NgayKy"]
            rows.append(r)
            if f.muc_chi_tiet == "hoa_don":
                rows += self._invoice_children(r, f, "SoHopDong", "TenHopDong")
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
    OUT_COLS = [("KyHieuHD", "Ký hiệu", TEXT), ("SoHoaDon", "Số hóa đơn", TEXT), ("NgayXuatHD", "Ngày lập", "date"),
                ("SoHopDong", "Số HĐ", TEXT), ("TenNguoiMua", "Tên người mua", TEXT), ("MSTNguoiMua", "MST người mua", TEXT),
                ("MatHang", "Mặt hàng", TEXT), ("GiaTriTruocThue", "Trước thuế", MONEY), ("ThueSuat", "Thuế suất", TEXT),
                ("VAT", "VAT", MONEY), ("GiaTriSauThue", "Sau thuế", MONEY), ("DaThu", "Đã thu", MONEY),
                ("ConPhaiThu", "Còn phải thu", MONEY), ("TinhTrang", "Tình trạng thanh toán", TEXT)]
    OUT_SUM = ["GiaTriTruocThue", "VAT", "GiaTriSauThue", "DaThu", "ConPhaiThu"]

    def _out_invoice_rows(self, f) -> list[dict]:
        ids = {c["Ma"]: c for c in self.contracts(_with(f, tt_thanh_toan=None, tt_xuat_hd=None), "Đầu ra")}
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
            name = inv["TenNguoiMua"] or c["TenDoiTac"]
            mst = inv["MSTNguoiMua"] or (c["MaThueDoiTac"] if not inv["TenNguoiMua"] else "") or ""
            rows.append({"KyHieuHD": inv["KyHieuHD"] or "", "SoHoaDon": inv["SoHoaDon"],
                         "NgayXuatHD": inv["NgayXuatHD"], "SoHopDong": c["SoHopDong"], "TenNguoiMua": name,
                         "MSTNguoiMua": mst, "MatHang": inv["MatHang"] or "", "ThueSuat": inv["ThueSuat"] or "",
                         "GiaTriTruocThue": inv["GiaTriTruocThue"], "VAT": inv["VAT"],
                         "GiaTriSauThue": inv["GiaTriSauThue"], "DaThu": paid,
                         "ConPhaiThu": inv["GiaTriSauThue"] - paid, "TinhTrang": status,
                         "_nguoi_mua": buyer_key(name, mst)})
        rows.sort(key=lambda r: (r["NgayXuatHD"], r["SoHoaDon"]))
        return rows

    def buyers(self) -> list[tuple[str, str]]:
        """Người mua trên các hóa đơn đầu ra: (tên hiển thị, khóa) để chọn trong bộ lọc."""
        seen: dict[str, str] = {}
        for r in self._out_invoice_rows(ReportFilters()):
            seen.setdefault(r["_nguoi_mua"], r["TenNguoiMua"] + (f" – {r['MSTNguoiMua']}" if r["MSTNguoiMua"] else ""))
        return sorted(((label, key) for key, label in seen.items()), key=lambda x: x[0].lower())

    def _out_report(self, title, rows, sheet="Hóa đơn đầu ra", f: ReportFilters | None = None) -> Report:
        total = _sum(rows, self.OUT_SUM)
        total["KyHieuHD"] = "TỔNG CỘNG"
        rep = Report(title, self.OUT_COLS, rows, total, sheet=sheet)
        return self._by_invoice_period(rep, f, "NgayXuatHD", self.OUT_SUM, "SoHoaDon") if f else rep

    def _ctx_hoa_don_ra(self, f):
        return self._out_report("Báo cáo hóa đơn đầu ra đã xuất", self._out_invoice_rows(f), f=f)

    def _ctx_hoa_don_ra_nguoi_mua(self, f):
        groups: dict[str, list[dict]] = {}
        for r in self._out_invoice_rows(f):
            if not f.nguoi_mua or r["_nguoi_mua"] in f.nguoi_mua:
                groups.setdefault(r["_nguoi_mua"], []).append(r)
        if not groups:
            return self._out_report("Hóa đơn đầu ra theo người mua", [], f=f)
        out = []
        for rows in sorted(groups.values(), key=lambda g: g[0]["TenNguoiMua"].lower()):
            name = rows[0]["TenNguoiMua"]
            out.append(self._out_report(f"Hóa đơn đầu ra – {name}", rows, sheet_name(name), f))
        return out

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
        years = {r["Ma"]: (r["NgayKy"] or "")[:4] for r in self.db.query("SELECT Ma, NgayKy FROM HopDong")} \
            if f.nam else {}
        for inv in self.cs.in_invoices():
            if f.nam and (years.get(inv["MaHopDong"]) if inv["MaHopDong"] else inv["NgayNhanHD"][:4]) != str(f.nam):
                continue
            if f.tu and inv["NgayNhanHD"] < f.tu or f.den and inv["NgayNhanHD"] > f.den:
                continue
            if f.co_hd_lien_ket is True and not inv["MaHopDong"]:
                continue
            if f.co_hd_lien_ket is False and inv["MaHopDong"]:
                continue
            if f.tt_thanh_toan and bool(inv["SoThamChieuSK"]) != (f.tt_thanh_toan == "Đã thu đủ"):
                continue          # đầu vào: có số sao kê = đã thanh toán
            out.append(inv)
        return out

    # 7
    def _ctx_nha_cung_cap(self, f):
        groups: dict[str, dict] = {}

        def g_for(mst, name):
            key = mst or name.lower()
            return groups.setdefault(key, {"_khoa": key, "TenNCC": name, "MST": mst or "", "SoHD": 0, "GiaTriHD": 0,
                                           "DaNhanHD": 0, "MuaTrucTiep": 0, "TruocThue": 0, "VAT": 0, "SauThue": 0})
        contracts = self.contracts(f, "Đầu vào")
        direct: dict[str, list] = {}
        for c in contracts:
            g = g_for(c["MaThueDoiTac"], c["TenDoiTac"])
            g["SoHD"] += 1
            g["GiaTriHD"] += c["GiaTriHD"]
        for inv in self._in_invoices(f):
            g = g_for(inv["MaThueNCC"], inv["TenNhaCungCap"])
            if inv["MaHopDong"]:
                g["DaNhanHD"] += inv["GiaTriSauThue"]
            else:
                g["MuaTrucTiep"] += inv["GiaTriSauThue"]
                direct.setdefault(g["_khoa"], []).append(inv)
            g["TruocThue"] += inv["GiaTriTruocThue"]
            g["VAT"] += inv["VAT"]
            g["SauThue"] += inv["GiaTriSauThue"]
        rows = sorted(groups.values(), key=lambda r: r["TenNCC"].lower())
        keys = ["SoHD", "GiaTriHD", "DaNhanHD", "MuaTrucTiep", "TruocThue", "VAT", "SauThue"]
        total = _sum(rows, keys)
        total["TenNCC"] = "TỔNG CỘNG"
        if f.muc_chi_tiet != "gop":
            out = []
            for r in rows:
                out.append(r)
                for c in contracts:
                    if (c["MaThueDoiTac"] or c["TenDoiTac"].lower()) != r["_khoa"]:
                        continue
                    out.append({"_kieu": "con", "MaHopDong": c["Ma"], "TenNCC": f"   ↳ {c['SoHopDong']} – {c['TenHopDong']}",
                                "GiaTriHD": c["GiaTriHD"], "DaNhanHD": c["DaXuatHD"]})
                    if f.muc_chi_tiet == "hoa_don":
                        for ch in self._invoice_children(c, f, "TenNCC", "MST"):
                            ch["DaNhanHD"] = ch.pop("DaXuatHD")
                            out.append(ch)
                if f.muc_chi_tiet == "hoa_don":
                    for inv in direct.get(r["_khoa"], []):
                        out.append({"_kieu": "con", "TenNCC": f"      • Hóa đơn {inv['SoHoaDonGoc']} (mua trực tiếp)",
                                    "MST": f"ngày {fmt_date(inv['NgayNhanHD'])}", "MuaTrucTiep": inv["GiaTriSauThue"],
                                    "TruocThue": inv["GiaTriTruocThue"], "VAT": inv["VAT"],
                                    "SauThue": inv["GiaTriSauThue"]})
            rows = out
        return Report("Báo cáo theo nhà cung cấp", [
            ("TenNCC", "Tên người bán", TEXT), ("MST", "MST", TEXT), ("SoHD", "Số HĐ đầu vào", INT),
            ("GiaTriHD", "Giá trị HĐ", MONEY), ("DaNhanHD", "Đã nhận hóa đơn (theo HĐ)", MONEY),
            ("MuaTrucTiep", "Hóa đơn mua trực tiếp", MONEY), ("TruocThue", "Tổng trước thuế", MONEY),
            ("VAT", "VAT", MONEY), ("SauThue", "Sau thuế", MONEY)], rows, total)

    # 8
    IN_COLS = [("SoHoaDonGoc", "Số hóa đơn", TEXT), ("NgayNhanHD", "Ngày hóa đơn", "date"),
               ("TenNhaCungCap", "Tên người bán", TEXT), ("MaThueNCC", "Mã số thuế người bán", TEXT),
               ("MatHang", "Mặt hàng", TEXT), ("GiaTriTruocThue", "Giá trị HHDV mua vào chưa có thuế", MONEY),
               ("ThueSuat", "Thuế suất", TEXT), ("VAT", "Thuế GTGT", MONEY), ("GiaTriSauThue", "Tổng thanh toán", MONEY),
               ("KyHieuHD", "Ký hiệu", TEXT), ("NguoiMuaHang", "Người mua hàng", TEXT),
               ("SoThamChieuSK", "Số tham chiếu sao kê", TEXT), ("SoHopDong", "Số HĐ đầu vào", TEXT)]
    IN_SUM = ["GiaTriTruocThue", "VAT", "GiaTriSauThue"]

    def _in_rows(self, f) -> list[dict]:
        contracts = {r["Ma"]: r["SoHopDong"] for r in self.db.query("SELECT Ma, SoHopDong FROM HopDong")}
        rows = []
        for i in self._in_invoices(f):
            nhom = i["NhomThue"] or guess_tax_group(i["VAT"])
            if f.nhom_thue in TAX_GROUPS and nhom != f.nhom_thue:
                continue
            rows.append({"SoHoaDonGoc": i["SoHoaDonGoc"], "NgayNhanHD": i["NgayNhanHD"],
                         "TenNhaCungCap": i["TenNhaCungCap"], "MaThueNCC": i["MaThueNCC"], "MatHang": i["MatHang"] or "",
                         "GiaTriTruocThue": i["GiaTriTruocThue"], "ThueSuat": i["ThueSuat"] or "", "VAT": i["VAT"],
                         "GiaTriSauThue": i["GiaTriSauThue"], "KyHieuHD": i["KyHieuHD"] or "",
                         "NguoiMuaHang": i["NguoiMuaHang"] or "", "SoThamChieuSK": i["SoThamChieuSK"] or "",
                         "SoHopDong": contracts.get(i["MaHopDong"], "") if i["MaHopDong"] else "(Mua trực tiếp)",
                         "_nhom": nhom})
        rows.sort(key=lambda r: (r["NgayNhanHD"], r["SoHoaDonGoc"]))
        return rows

    def _ctx_hoa_don_vao(self, f):
        """Bảng kê hóa đơn mua vào theo mẫu quản trị: nhóm 1 chịu thuế, nhóm 5 không chịu thuế, dòng 'Số dòng = N'."""
        rows = self._in_rows(f)
        out = rows
        if f.nhom_ky_hd:            # chia theo kỳ thì xếp lẫn hai nhóm thuế theo ngày
            total = _sum(rows, self.IN_SUM)
            total["SoHoaDonGoc"] = f"Số dòng = {len(rows)}"
            rep = Report("Bảng kê hóa đơn, chứng từ hàng hóa, dịch vụ mua vào", self.IN_COLS, rows, total,
                         sheet="Bảng kê mua vào")
            return self._by_invoice_period(rep, f, "NgayNhanHD", self.IN_SUM, "SoHoaDonGoc")
        if f.nhom_thue != "lan":
            out = []
            for g in TAX_GROUPS:
                part = [r for r in rows if r["_nhom"] == g]
                if not part:
                    continue
                head = {"_kieu": "nhom", "_nhan": f"Nhóm HHDV mua vào : {TAX_GROUP_TITLES[g]} ({len(part)})"}
                head.update(_sum(part, self.IN_SUM))
                out += [head] + part
        total = _sum(rows, self.IN_SUM)
        total["SoHoaDonGoc"] = f"Số dòng = {len(rows)}"
        return Report("Bảng kê hóa đơn, chứng từ hàng hóa, dịch vụ mua vào", self.IN_COLS, out, total,
                      sheet="Bảng kê mua vào")

    def _ctx_hoa_don_ra_vao(self, f):
        if not f.gop_ra_vao:
            return [self._ctx_hoa_don_ra(f), self._ctx_hoa_don_vao(f)]
        rows = []
        for r in self._out_invoice_rows(f):
            rows.append({"Loai": "Đầu ra", "KyHieuHD": r["KyHieuHD"], "So": r["SoHoaDon"], "Ngay": r["NgayXuatHD"],
                         "DoiTac": r["TenNguoiMua"], "MST": r["MSTNguoiMua"], "MatHang": r["MatHang"],
                         "SoHopDong": r["SoHopDong"], "TruocThue": r["GiaTriTruocThue"], "ThueSuat": r["ThueSuat"],
                         "VAT": r["VAT"], "SauThue": r["GiaTriSauThue"]})
        for r in self._in_rows(f):
            rows.append({"Loai": "Đầu vào", "KyHieuHD": r["KyHieuHD"], "So": r["SoHoaDonGoc"], "Ngay": r["NgayNhanHD"],
                         "DoiTac": r["TenNhaCungCap"], "MST": r["MaThueNCC"], "MatHang": r["MatHang"],
                         "SoHopDong": r["SoHopDong"], "TruocThue": r["GiaTriTruocThue"], "ThueSuat": r["ThueSuat"],
                         "VAT": r["VAT"], "SauThue": r["GiaTriSauThue"]})
        rows.sort(key=lambda r: (r["Ngay"], r["Loai"], r["So"]))
        keys = ["TruocThue", "VAT", "SauThue"]
        for loai in ("Đầu ra", "Đầu vào"):
            sub = _sum([r for r in rows if r.get("Loai") == loai and not r.get("_kieu")], keys)
            rows.append(dict(sub, _kieu="cong", Loai=f"Cộng hóa đơn {loai.lower()}"))
        return Report("Hóa đơn đầu ra và đầu vào (gộp chung)", [
            ("Loai", "Loại", TEXT), ("KyHieuHD", "Ký hiệu", TEXT), ("So", "Số hóa đơn", TEXT), ("Ngay", "Ngày", "date"),
            ("DoiTac", "Người mua / Người bán", TEXT), ("MST", "MST", TEXT), ("MatHang", "Mặt hàng", TEXT),
            ("SoHopDong", "Số HĐ", TEXT), ("TruocThue", "Trước thuế", MONEY), ("ThueSuat", "Thuế suất", TEXT),
            ("VAT", "VAT", MONEY), ("SauThue", "Sau thuế", MONEY)], rows, None, sheet="Hóa đơn ra vào")

    # 0
    def _ctx_tong_hop(self, f):
        """Toàn bộ thông tin của các hợp đồng được lọc: hợp đồng, công việc, hóa đơn, thanh toán."""
        out = self._by_direction(f, self._tong_hop_contracts)
        out = out if isinstance(out, list) else [out]
        contracts = self.contracts(f)
        no = {c["Ma"]: c["SoHopDong"] for c in contracts}
        ids = list(no)
        marks = ",".join("?" * len(ids)) or "NULL"
        tasks = []
        for t in self.db.query(f"SELECT * FROM CongViec WHERE DaXoa = 0 AND MaHopDong IN ({marks})"
                               " ORDER BY NgayDuKienHT", ids):
            n_bg = self.db.one("SELECT COUNT(*) n FROM LichSuBanGiao WHERE DaXoa = 0 AND MaCongViec = ?", (t["Ma"],))["n"]
            n_nt = self.db.one("SELECT COUNT(*) n FROM LichSuNghiemThu WHERE DaXoa = 0 AND MaCongViec = ?", (t["Ma"],))["n"]
            tasks.append(dict(t, SoHopDong=no[t["MaHopDong"]], SoLanBanGiao=n_bg, SoLanNghiemThu=n_nt))
        out.append(Report("Công việc của hợp đồng", [
            ("SoHopDong", "Số HĐ", TEXT), ("TenNoiDungCV", "Công việc", TEXT), ("NgayDuKienHT", "Hạn hoàn thành", "date"),
            ("TienDoPT", "Tiến độ %", INT), ("TinhTrangCV", "Tình trạng", TEXT), ("SoLanBanGiao", "Số lần bàn giao", INT),
            ("SoLanNghiemThu", "Số lần nghiệm thu", INT), ("GhiChuCV", "Ghi chú", TEXT)], tasks, sheet="Công việc"))
        inv = [dict(r, SoHopDong=no[r["MaHopDong"]]) for r in self.db.query(
            f"SELECT * FROM HoaDonDauRa WHERE DaXoa = 0 AND MaHopDong IN ({marks}) ORDER BY NgayXuatHD", ids)]
        out.append(Report("Hóa đơn đầu ra của hợp đồng", [
            ("SoHopDong", "Số HĐ", TEXT), ("KyHieuMauSo", "Mẫu số", TEXT), ("KyHieuHD", "Ký hiệu", TEXT),
            ("SoHoaDon", "Số hóa đơn", TEXT), ("NgayXuatHD", "Ngày lập", "date"), ("TenNguoiMua", "Tên người mua", TEXT),
            ("MSTNguoiMua", "MST người mua", TEXT), ("MatHang", "Mặt hàng", TEXT), ("GiaTriTruocThue", "Trước thuế", MONEY),
            ("ThueSuat", "Thuế suất", TEXT), ("VAT", "VAT", MONEY), ("GiaTriSauThue", "Sau thuế", MONEY),
            ("TinhChatHD", "Tính chất", TEXT), ("GhiChuHDDR", "Ghi chú", TEXT)], inv,
            dict(_sum(inv, ["GiaTriTruocThue", "VAT", "GiaTriSauThue"]), SoHopDong="TỔNG CỘNG"), sheet="Hóa đơn đầu ra"))
        invoices = {r["Ma"]: r["SoHoaDon"] for r in inv}
        pays = [dict(r, SoHopDong=no[r["MaHopDong"]], SoHoaDon=invoices.get(r["MaHDDauRa"], "")) for r in self.db.query(
            f"SELECT * FROM ThanhToanHopDong WHERE DaXoa = 0 AND MaHopDong IN ({marks}) ORDER BY NgayThanhToan", ids)]
        out.append(Report("Thanh toán của hợp đồng", [
            ("SoHopDong", "Số HĐ", TEXT), ("NgayThanhToan", "Ngày tiền về", "date"), ("SoTien", "Số tiền", MONEY),
            ("HinhThuc", "Hình thức", TEXT), ("SoThamChieuSK", "Số sao kê", TEXT), ("SoHoaDon", "Số hóa đơn", TEXT),
            ("GhiChu", "Ghi chú", TEXT)], pays, dict(_sum(pays, ["SoTien"]), SoHopDong="TỔNG CỘNG"), sheet="Thanh toán"))
        vin = [dict(r, SoHopDong=no[r["MaHopDong"]]) for r in self.db.query(
            f"SELECT * FROM HoaDonDauVao WHERE DaXoa = 0 AND MaHopDong IN ({marks}) ORDER BY NgayNhanHD", ids)]
        out.append(Report("Hóa đơn đầu vào của hợp đồng", [
            ("SoHopDong", "Số HĐ", TEXT)] + [c for c in self.IN_COLS if c[0] != "SoHopDong"] + [
            ("NhomThue", "Nhóm thuế", TEXT), ("GhiChuHDDV", "Ghi chú", TEXT)], vin,
            dict(_sum(vin, self.IN_SUM), SoHopDong="TỔNG CỘNG"), sheet="Hóa đơn đầu vào"))
        return out

    def _tong_hop_contracts(self, f):
        rows = self.contracts(f)
        for r in rows:
            r["ThoiGianTH"] = duration_text(r["ThoiGianThucHien"], r["DonViThoiGian"])
            r["SoCongViec"] = self.db.one("SELECT COUNT(*) n FROM CongViec WHERE DaXoa = 0 AND MaHopDong = ?",
                                          (r["Ma"],))["n"]
            r["SoHoaDon"] = self.db.one(
                "SELECT COUNT(*) n FROM " + ("HoaDonDauRa" if r["ChieuHopDong"] == "Đầu ra" else "HoaDonDauVao")
                + " WHERE DaXoa = 0 AND MaHopDong = ?", (r["Ma"],))["n"]
        keys = ["GiaTriTruocThue", "VAT", "GiaTriTongSauThue", "DaXuatHD", "ConPhaiXuat", "DaThanhToan", "ConPhaiThu"]
        total = _sum(rows, keys)
        total["SoHopDong"] = "TỔNG CỘNG"
        return Report("Báo cáo tổng hợp hợp đồng", [
            ("SoHopDong", "Số HĐ", TEXT), ("TenHopDong", "Tên hợp đồng", TEXT), ("ChieuHopDong", "Chiều", TEXT),
            ("LoaiHD", "Loại HĐ", TEXT), ("TenDoiTac", "Đối tác", TEXT), ("MaThueDoiTac", "MST đối tác", TEXT),
            ("GiaTriTruocThue", "Giá trị trước thuế", MONEY), ("VAT", "VAT", MONEY),
            ("GiaTriTongSauThue", "Giá trị sau thuế", MONEY), ("NgayKy", "Ngày ký", "date"),
            ("NgayBatDauDuKien", "Ngày bắt đầu", "date"), ("ThoiGianTH", "Thời gian thực hiện", TEXT),
            ("NgayKetThucDuKien", "Ngày kết thúc", "date"), ("TinhTrangHopDong", "Tình trạng", TEXT),
            ("DaXuatHD", "Đã xuất / nhận hóa đơn", MONEY), ("ConPhaiXuat", "Còn phải xuất", MONEY),
            ("TinhTrangXuatHD", "Tình trạng hóa đơn", TEXT), ("DaThanhToan", "Đã thanh toán", MONEY),
            ("ConPhaiThu", "Còn phải thu", MONEY), ("PhanTramTT", "% thanh toán", PCT),
            ("TinhTrangThanhToan", "Tình trạng thanh toán", TEXT), ("LanThuGanNhat", "Lần thu gần nhất", "date"),
            ("SoCongViec", "Số công việc", INT), ("SoHoaDon", "Số hóa đơn", INT), ("GhiChu", "Ghi chú", TEXT)],
            rows, total, sheet="Hợp đồng")

    # 9
    def _ctx_thoi_gian(self, f):
        den = f.den or date.today().isoformat()
        tu = f.tu or den[:4] + "-01-01"
        periods = split_periods(tu, den, f.ky)
        contracts = self.contracts(_with(f, tu=None, den=None, che_do="luy_ke"))
        ids = [c["Ma"] for c in contracts if c["ChieuHopDong"] == "Đầu ra"]
        marks = ",".join("?" * len(ids)) or "NULL"
        rows = []
        for label, a, b in periods:
            new_out = [c for c in contracts if a <= c["NgayKy"] <= b and c["ChieuHopDong"] == "Đầu ra"]
            new_in = [c for c in contracts if a <= c["NgayKy"] <= b and c["ChieuHopDong"] == "Đầu vào"]
            inv = self.db.one(
                f"SELECT COALESCE(SUM(GiaTriSauThue),0) s FROM HoaDonDauRa WHERE DaXoa = 0 AND TinhChatHD != 'Đã hủy'"
                f" AND NgayXuatHD BETWEEN ? AND ? AND MaHopDong IN ({marks})", (a, b, *ids))["s"]
            paid = self.db.one(
                f"SELECT COALESCE(SUM(SoTien),0) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND NgayThanhToan BETWEEN ? AND ?"
                f" AND MaHopDong IN ({marks})", (a, b, *ids))["s"]
            vin = self.db.one("SELECT COALESCE(SUM(GiaTriSauThue),0) s FROM HoaDonDauVao WHERE DaXoa = 0"
                              " AND NgayNhanHD BETWEEN ? AND ?", (a, b))["s"]
            rows.append({"Ky": label, "SoHDRa": len(new_out), "GiaTriRa": sum(c["GiaTriTongSauThue"] for c in new_out),
                         "HoaDonRa": inv, "DaThu": paid, "SoHDVao": len(new_in),
                         "GiaTriVao": sum(c["GiaTriTongSauThue"] for c in new_in), "HoaDonVao": vin,
                         "SoHDMoi": len(new_out) + len(new_in),
                         "GiaTriMoi": sum(c["GiaTriTongSauThue"] for c in new_out + new_in)})

        def report(title, cols, sheet):
            total = _sum(rows, [k for k, _, kind in cols if kind in (MONEY, INT)])
            total["Ky"] = "TỔNG CỘNG"
            return Report(title, [("Ky", "Kỳ", TEXT)] + cols, rows, total, sheet=sheet)

        out_cols = [("SoHDRa", "Số HĐ đầu ra ký mới", INT), ("GiaTriRa", "Giá trị ký mới", MONEY),
                    ("HoaDonRa", "Hóa đơn đã xuất", MONEY), ("DaThu", "Tiền đã thu", MONEY)]
        in_cols = [("SoHDVao", "Số HĐ đầu vào ký mới", INT), ("GiaTriVao", "Giá trị ký mới", MONEY),
                   ("HoaDonVao", "Hóa đơn đầu vào", MONEY)]
        if f.gop_ra_vao:
            return report("Báo cáo theo thời gian", [
                ("SoHDMoi", "Số HĐ ký mới", INT), ("GiaTriMoi", "Giá trị ký mới", MONEY),
                ("HoaDonRa", "Hóa đơn đã xuất", MONEY), ("DaThu", "Tiền đã thu", MONEY),
                ("HoaDonVao", "Hóa đơn đầu vào", MONEY)], "Theo thời gian")
        out = []
        if f.chieu in (None, "Đầu ra"):
            out.append(report("Báo cáo theo thời gian – đầu ra", out_cols, "Đầu ra"))
        if f.chieu in (None, "Đầu vào"):
            out.append(report("Báo cáo theo thời gian – đầu vào", in_cols, "Đầu vào"))
        return out


def period_label(d: str, ky: str) -> str:
    y, m = int(d[:4]), int(d[5:7])
    if ky == "nam":
        return f"Năm {y}"
    if ky == "quy":
        return f"Quý {(m - 1) // 3 + 1}/{y}"
    return f"Tháng {m:02d}/{y}"


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


def relative_range(key: str, today: date) -> tuple[str, str]:
    """Khoảng ngày của kỳ tương đối: thang_nay / quy_nay / nam_nay / thang_truoc / quy_truoc / nam_truoc."""
    ky, when = key.split("_")
    if when == "truoc":
        if ky == "thang":
            today = date(today.year, today.month, 1) - timedelta(days=1)
        elif ky == "quy":
            today = date(today.year, (today.month - 1) // 3 * 3 + 1, 1) - timedelta(days=1)
        else:
            today = date(today.year - 1, 6, 1)
    return period_range(ky, today)


def buyer_key(name: str, mst: str) -> str:
    return "mst:" + mst.strip() if mst and mst.strip() else "ten:" + fold(name).strip()


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
        base = sheet_name(rep.sheet)
        name = base
        n = 2
        while name in used_names:
            name = f"{base[:28]} {n}"
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
                if kieu == "con":
                    cell.font = Font(italic=True, color="555555")
                elif kieu:
                    cell.font = Font(bold=True)
                    if kieu in ("nhom", "tong"):
                        cell.fill = group_fill
            if row.get("_nhan"):
                # nhãn nhóm đặt ở cột đầu, tràn sang các ô trống bên phải như bảng kê mẫu
                ws.cell(r, 1, row["_nhan"]).font = Font(bold=True)
                ws.cell(r, 1).fill = group_fill
            r += 1
        ws.freeze_panes = f"B{hr + 1}"
        ws.column_dimensions["A"].width = 6
        for c, (key, label, kind) in enumerate(rep.columns, start=2):
            longest = max([len(str(label))] + [len(str(x.get(key) or "")) for x in rep.rows[:500]
                                               if not x.get("_nhan")])
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
