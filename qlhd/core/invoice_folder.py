"""Thư mục hóa đơn đầu ra / đầu vào trên máy này: quét (cả thư mục con Năm, Quý…), tự thêm hóa đơn mới vào phần
mềm, ghi nhớ file đã xử lý để lần sau bỏ qua, ghép tệp gốc với hóa đơn.

Quy tắc (chủ phần mềm chốt 17/11/2026):
- Chỉ xử lý file MỚI xuất hiện; file đã xử lý (đã thêm, đã có trong phần mềm, đã bỏ qua) không nhập lại, không ghi
  đè số liệu, không gửi lại Gemini.
- Hóa đơn mới đọc được rõ ràng thì tự thêm. Nghi trùng, hóa đơn đầu ra chưa nhận ra hợp đồng, thiếu trường bắt
  buộc, file không đọc được thì KHÔNG tự thêm, để ở mục Cần xem.
- XML và PDF / ảnh cùng một hóa đơn tính là một hóa đơn: số liệu lấy từ XML, file kia để xem / xuất PDF.
- File ảnh, PDF quét (không có XML): nhờ Gemini đọc số liệu; hóa đơn thêm theo cách này ghi nguồn "Gemini" để nhân
  viên soát lại.
- Lần quét đầu tiên một thư mục (thường chứa hóa đơn nhiều năm): không tự thêm, không gửi Gemini; hóa đơn chưa có
  trong phần mềm nằm ở mục Cần xem để nhân viên chọn thêm.
Đường dẫn thư mục là riêng từng máy; mỗi hóa đơn lưu đường dẫn tương đối nên máy khác (đặt cùng cấu trúc thư mục)
vẫn mở được tệp sau khi đồng bộ.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from dataclasses import dataclass, field
from pathlib import Path

from . import duplicates as dupl
from . import invoice_match, pdf_invoice, vat, xml_invoice
from .contracts import ContractService, DuplicateWarning, ValidationError, guess_tax_group, rate_from_amounts
from .db import now_iso
from .reports import fmt_money

KINDS = ("ra", "vao")
KIND_LABEL = {"ra": "Đầu ra", "vao": "Đầu vào"}
TABLE = {"ra": "HoaDonDauRa", "vao": "HoaDonDauVao"}
EXT_XML = {".xml"}
EXT_PDF = {".pdf"}
EXT_IMG = {".jpg", ".jpeg", ".png", ".webp"}

K_DIR = {"ra": "tmhd_thu_muc_ra", "vao": "tmhd_thu_muc_vao"}
K_ON_OPEN = "tmhd_quet_khi_mo"
K_AUTO_ADD = "tmhd_tu_them"
K_XML, K_PDF, K_IMG = "tmhd_xml", "tmhd_pdf", "tmhd_anh"
K_LAST = "tmhd_lan_quet"
K_SCANNED = {"ra": "tmhd_da_quet_ra", "vao": "tmhd_da_quet_vao"}   # thư mục đã quét lần đầu (đổi thư mục = lần đầu)
K_SUMMARY = "tmhd_ket_qua"              # JSON tóm tắt lần quét gần nhất (cho dòng báo trên Tổng quan)

# trạng thái một file trong bảng TepDaQuet
F_ADDED = "da_them"
F_EXISTS = "co_san"
F_REVIEW = "can_xem"
F_SKIP = "bo_qua"
F_COMPANION = "tep_kem"
F_CONFLICT = "lech"                     # đã có trong phần mềm nhưng số liệu trong file khác
F_LABELS = {F_ADDED: "Đã tự thêm", F_EXISTS: "Có từ trước", F_REVIEW: "Cần xem", F_SKIP: "Đã bỏ qua",
            F_COMPANION: "Tệp kèm (cùng hóa đơn)", F_CONFLICT: "Đã có, số liệu khác"}
F_OPEN = (F_REVIEW, F_CONFLICT)         # còn chờ nhân viên (thẻ Cần xem)

SRC_XML, SRC_PDF, SRC_GEMINI, SRC_GEMINI_OK = "XML", "PDF", "Gemini", "Gemini (đã soát)"
GEMINI_RETRY_WAIT = 30       # giây chờ trước khi thử lại khi Gemini báo vượt hạn mức / quá tải
GEMINI_PARALLEL = 3          # số file gửi Gemini cùng lúc (gói miễn phí giới hạn số lần gọi mỗi phút)
REQUIRED = {"vao": (("SoHoaDonGoc", "Số hóa đơn"), ("TenNhaCungCap", "Tên người bán"), ("MaThueNCC", "MST người bán"),
                    ("NgayNhanHD", "Ngày hóa đơn")),
            "ra": (("SoHoaDon", "Số hóa đơn"), ("NgayXuatHD", "Ngày lập"))}


# ------------------------------------------------------------------------ đọc file hóa đơn
def parse_invoice_file(path: str) -> dict:
    if path.lower().endswith(".xml"):
        return xml_invoice.parse_invoice(path)
    return pdf_invoice.parse_invoice(path)


def invoice_values(kind: str, d: dict, path: str, mst_cty: str = "") -> tuple[dict, list[str]]:
    """Dữ liệu đọc từ file → giá trị lưu cho hóa đơn đầu ra / đầu vào, kèm các lưu ý cho người nhập."""
    notes = []
    if d.get("ThieuTruong"):
        notes.append("Chưa đọc được: " + ", ".join(d["ThieuTruong"]) + ". Hãy kiểm tra và nhập bổ sung.")
    rate = d.get("ThueSuatHD") or rate_from_amounts(d["GiaTriTruocThue"], d["VAT"])
    try:
        rate = vat.parse_rate(rate)
    except ValueError:
        notes.append(f"Thuế suất \"{rate}\" chưa nhận ra, hãy chọn lại.")
        rate = ""
    common = {"KyHieuMauSo": d["KyHieuMauSo"], "KyHieuHD": d["KyHieuHD"], "GiaTriTruocThue": d["GiaTriTruocThue"],
              "VAT": d["VAT"], "GiaTriSauThue": d["GiaTriSauThue"], "MatHang": d.get("MatHang") or "",
              "ThueSuat": rate, "DuongDanTep": path, "ChiTietThueSuat": None}
    lines = vat.load_breakdown(d.get("ChiTietThueSuat"))
    if len(lines) > 1:            # nhiều mức thuế: giữ chi tiết từng mức nếu cộng lại khớp tổng hóa đơn
        b, t = sum(x["TruocThue"] for x in lines), sum(x["VAT"] for x in lines)
        if abs(b - d["GiaTriTruocThue"]) <= len(lines) and abs(t - d["VAT"]) <= len(lines):
            common.update(ChiTietThueSuat=vat.dump_breakdown(lines), ThueSuat=vat.breakdown_rate(lines))
            notes.append("Hóa đơn nhiều mức thuế: " + "; ".join(
                f"{x['ThueSuat']}: tiền hàng {fmt_money(x['TruocThue'])}, VAT {fmt_money(x['VAT'])}" for x in lines) + ".")
        else:
            notes.append(f"Hóa đơn nhiều mức thuế ({rate}): chi tiết từng mức không khớp tổng, chỉ lưu tổng tiền.")
    if kind == "ra":
        if mst_cty and d["MSTNguoiBan"] and d["MSTNguoiBan"] != mst_cty:
            notes.append(f"MST người bán {d['MSTNguoiBan']} khác MST công ty {mst_cty}.")
        return dict(common, SoHoaDon=d["SoHoaDon"], NgayXuatHD=d["NgayLap"] or None, TenNguoiMua=d["TenNguoiMua"],
                    MSTNguoiMua=d["MSTNguoiMua"], TinhChatHD="Gốc"), notes
    if mst_cty and d["MSTNguoiMua"] and d["MSTNguoiMua"] != mst_cty:
        notes.append(f"MST người mua {d['MSTNguoiMua']} khác MST công ty {mst_cty}: hóa đơn có thể không phải của công ty.")
    if d.get("PhiKhac"):
        common.update(PhiKhac=d["PhiKhac"], TenPhiKhac=d.get("TenPhiKhac") or "Phí bảo vệ môi trường")
        notes.append(f"Hóa đơn có {common['TenPhiKhac']} {fmt_money(d['PhiKhac'])} đ ngoài thuế GTGT.")
    return dict(common, SoHoaDonGoc=d["SoHoaDon"], TenNhaCungCap=d["TenNguoiBan"], MaThueNCC=d["MSTNguoiBan"],
                NgayNhanHD=d["NgayLap"] or None, NhomThue=guess_tax_group(d["VAT"]), _Dong=d.get("Dong") or None), notes


def _s(x) -> str:
    return "" if x is None else str(x).strip()


def _money(x) -> int:
    """Số tiền Gemini trả: số, hoặc chuỗi kiểu "1.234.567" / "1,234,567" / "1.234.567,50 đ"."""
    if isinstance(x, (int, float)):
        return int(round(x))
    t = re.sub(r"[^\d,.\-]", "", str(x or ""))
    if not t:
        return 0
    if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", t):           # chỉ có dấu phân cách nghìn
        t = re.sub(r"[.,]", "", t)
    elif "," in t and "." in t:                              # 1.234.567,50 hoặc 1,234,567.50
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    else:
        t = t.replace(",", ".")
    try:
        return int(round(float(t)))
    except ValueError:
        return 0


def _date(x) -> str:
    """Ngày lập Gemini trả (YYYY-MM-DD, DD/MM/YYYY, "Ngày 05 tháng 11 năm 2026"…) → YYYY-MM-DD ("" nếu không rõ)."""
    t = str(x or "").strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.search(r"(\d{1,2})\D{1,12}?(\d{1,2})\D{1,12}?(\d{4})", t)
        if not m:
            return ""
        d, mo, y = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return ""


def _mst_text(x) -> str:
    """MST: bỏ khoảng trắng, chấm; giữ dấu gạch của MST chi nhánh (0101234567-001)."""
    t = re.sub(r"[^\d\-]", "", str(x or ""))
    return t.strip("-")


def from_gemini(g: dict) -> dict:
    """Kết quả Gemini đọc ảnh / PDF → cùng dạng với xml_invoice.parse_invoice (đủ dòng hàng, nhiều mức thuế,
    chiết khấu, phí khác)."""
    money = _money

    def rate(x):
        try:
            return vat.parse_rate(_s(x))
        except ValueError:
            return _s(x)
    lines, names = [], []
    for ln in g.get("dong") or []:
        ten = " ".join(_s(ln.get("ten")).split())
        kind = ln.get("tinh_chat") or "hang_hoa"
        if not ten or kind == "ghi_chu":
            continue
        tien = money(ln.get("thanh_tien"))
        if kind == "khuyen_mai" and not tien:
            continue
        lines.append({"TenHang": ten, "DVT": ln.get("dvt") or "", "SoLuong": ln.get("so_luong"),
                      "DonGia": money(ln.get("don_gia")), "ThanhTien": -abs(tien) if kind == "chiet_khau" else tien,
                      "ThueSuat": rate(ln.get("thue_suat"))})
        if ten not in names:
            names.append(ten)
    by_rate = [{"ThueSuat": rate(x.get("thue_suat")), "TruocThue": money(x.get("tien_truoc_thue")),
                "VAT": money(x.get("tien_thue"))} for x in g.get("thue_theo_muc") or [] if _s(x.get("thue_suat"))]
    rates = list(dict.fromkeys([x["ThueSuat"] for x in by_rate if x["ThueSuat"]]
                               + [x["ThueSuat"] for x in lines if x["ThueSuat"]]))
    if len(by_rate) < 2 and len({x["ThueSuat"] for x in lines if x["ThueSuat"]}) > 1:
        acc: dict[str, int] = {}
        for x in lines:
            if x["ThueSuat"]:
                acc[x["ThueSuat"]] = acc.get(x["ThueSuat"], 0) + x["ThanhTien"]
        by_rate = [{"ThueSuat": r, "TruocThue": t, "VAT": None} for r, t in acc.items()]
    fees = [(" ".join(_s(f.get("ten")).split()), money(f.get("so_tien"))) for f in g.get("phi_khac") or []]
    fees = [(n, t) for n, t in fees if t]
    truoc = money(g.get("tien_truoc_thue")) or sum(x["TruocThue"] for x in by_rate) or sum(x["ThanhTien"] for x in lines)
    thue = money(g.get("tien_thue")) or sum(x["VAT"] or 0 for x in by_rate)
    phi = sum(t for _, t in fees)
    tong = money(g.get("tong_thanh_toan")) or (truoc + thue + phi if truoc else 0)
    if not phi and truoc and tong - truoc - thue > 2:
        phi = tong - truoc - thue                     # tổng thanh toán lớn hơn tiền hàng + thuế: phần chênh là phí
    hd_rate = "; ".join(rates) if len(rates) > 1 else rate(g.get("thue_suat")) or (rates[0] if rates else "")
    d = {"SoHopDong": _s(g.get("so_hop_dong")), "VanBan": _s(g.get("so_hop_dong")),
         "KyHieuMauSo": _s(g.get("mau_so")),
         "KyHieuHD": _s(g.get("ky_hieu")), "SoHoaDon": _s(g.get("so_hoa_don")),
         "NgayLap": _date(g.get("ngay_lap")), "TenNguoiBan": _s(g.get("ten_nguoi_ban")),
         "MSTNguoiBan": _mst_text(g.get("mst_nguoi_ban")),
         "TenNguoiMua": _s(g.get("ten_nguoi_mua")),
         "MSTNguoiMua": _mst_text(g.get("mst_nguoi_mua")),
         "DonViTienTe": _s(g.get("don_vi_tien_te")) or "VND", "TyGia": g.get("ty_gia") or "",
         "GiaTriTruocThue": truoc, "VAT": thue, "GiaTriSauThue": tong, "PhiKhac": phi,
         "TenPhiKhac": "; ".join(n for n, _ in fees if n) or ("Phí khác" if phi else ""),
         "ThueSuat": [{"ThueSuat": x["ThueSuat"], "ThanhTien": x["TruocThue"], "TienThue": x["VAT"] or 0}
                      for x in by_rate],
         "ThueSuatHD": hd_rate, "MatHang": xml_invoice.join_items(names),
         "ChiTietThueSuat": by_rate if len(by_rate) > 1 else [], "Dong": lines}
    return d


def _num(x) -> str:
    """Số hóa đơn so sánh được: chỉ chữ số, bỏ số 0 đầu ("0000123" = "123")."""
    return "".join(ch for ch in str(x or "") if ch.isdigit()).lstrip("0")


def _mst(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())


def _sym(x) -> str:
    return "".join(ch for ch in str(x or "").upper() if ch.isalnum())


def same_symbol(a: str, b: str) -> bool:
    """Ký hiệu hóa đơn coi là một: giống nhau, một bên để trống, hoặc một bên có thêm mẫu số ở đầu (1C26TAA)."""
    a, b = _sym(a), _sym(b)
    return not a or not b or a == b or a.endswith(b) or b.endswith(a)


def _merge(gd: dict, text: dict | None, it: "Item") -> dict:
    """Số liệu Gemini đọc, bổ sung trường trống từ lớp chữ PDF; hai bên khác nhau thì ghi lưu ý để nhân viên xem."""
    if not text:
        return gd
    for k, v in text.items():
        if k not in gd or gd[k] in (None, "", 0, []):
            gd[k] = v
    if _complete(text):
        diff = []
        if _num(text.get("SoHoaDon")) != _num(gd.get("SoHoaDon")):
            diff.append(f"số hóa đơn {text.get('SoHoaDon')} / {gd.get('SoHoaDon')}")
        if abs(int(text.get("GiaTriSauThue") or 0) - int(gd.get("GiaTriSauThue") or 0)) > 2:
            diff.append(f"tổng tiền {fmt_money(text.get('GiaTriSauThue'))} / {fmt_money(gd.get('GiaTriSauThue'))}")
        if diff:
            it.warn = "Lớp chữ PDF và Gemini đọc khác nhau: " + "; ".join(diff)
    return gd


def invoice_key(kind: str, d: dict) -> tuple:
    return (_mst(d.get("MSTNguoiBan")) if kind == "vao" else "", _sym(d.get("KyHieuHD")), _num(d.get("SoHoaDon")))


def loose_key(kind: str, d: dict) -> tuple:
    """Khóa để biết hóa đơn đã có trong phần mềm (đầu vào: MST người bán + số; đầu ra: số)."""
    return (_mst(d.get("MSTNguoiBan")) if kind == "vao" else "", _num(d.get("SoHoaDon")))


def known_keys(db, kind: str) -> set[tuple]:
    """Khóa các hóa đơn đã có (đọc CSDL ở luồng giao diện, dùng khi đọc file ở luồng nền để khỏi gửi Gemini)."""
    if kind == "vao":
        return {(_mst(r["MaThueNCC"]), _num(r["SoHoaDonGoc"]))
                for r in db.query("SELECT MaThueNCC, SoHoaDonGoc FROM HoaDonDauVao WHERE DaXoa = 0")}
    return {("", _num(r["SoHoaDon"])) for r in db.query("SELECT SoHoaDon FROM HoaDonDauRa WHERE DaXoa = 0")}


def _complete(d: dict | None) -> bool:
    return bool(d) and not d.get("ThieuTruong") and bool(_num(d.get("SoHoaDon"))) and bool(d.get("GiaTriSauThue"))


@dataclass
class Item:
    """Một hóa đơn tìm thấy trong thư mục (một file chính, có thể kèm file PDF / ảnh cùng hóa đơn)."""
    kind: str
    rel: str                                    # file chính (tương đối, dấu /)
    path: str
    kem: list[str] = field(default_factory=list)
    data: dict | None = None
    source: str = ""
    error: str = ""
    offline: bool = False                       # lỗi do mất kết nối Gemini: lần quét sau thử lại
    warn: str = ""                              # PDF và Gemini đọc ra khác nhau: để nhân viên xem
    not_invoice: bool = False                   # Gemini trả lời file không phải hóa đơn (không gửi lại)
    again: bool = False                         # file đã quét trước đây, nay xét lại
    old: dict | None = None                     # dòng TepDaQuet cũ (khi xét lại)


# ------------------------------------------------------------------------ quét thư mục
class InvoiceFolders:
    def __init__(self, db):
        self.db = db

    # ---- cài đặt (riêng máy này)
    def root(self, kind: str) -> Path | None:
        p = self.db.setting(K_DIR[kind])
        return Path(p) if p else None

    def flag(self, key: str, default: bool = True) -> bool:
        return self.db.setting(key, "1" if default else "0") == "1"

    def settings(self) -> dict:
        return {"ra": self.db.setting(K_DIR["ra"]) or "", "vao": self.db.setting(K_DIR["vao"]) or "",
                "quet_khi_mo": self.flag(K_ON_OPEN), "tu_them": self.flag(K_AUTO_ADD), "xml": self.flag(K_XML),
                "pdf": self.flag(K_PDF), "anh": self.flag(K_IMG)}

    def save_settings(self, v: dict) -> None:
        for kind in KINDS:
            p = (v.get(kind) or "").strip()
            if p and not Path(p).is_dir():
                raise ValidationError(f"Không tìm thấy thư mục hóa đơn {KIND_LABEL[kind].lower()}:\n{p}")
            self.db.set_setting(K_DIR[kind], p or None)
        for key, name in ((K_ON_OPEN, "quet_khi_mo"), (K_AUTO_ADD, "tu_them"), (K_XML, "xml"), (K_PDF, "pdf"),
                          (K_IMG, "anh")):
            if name in v:
                self.db.set_setting(key, "1" if v[name] else "0")

    def configured(self) -> bool:
        return any(self.root(k) for k in KINDS)

    def extensions(self) -> set[str]:
        ext = set()
        if self.flag(K_XML):
            ext |= EXT_XML
        if self.flag(K_PDF):
            ext |= EXT_PDF
        if self.flag(K_IMG):
            ext |= EXT_IMG
        return ext

    # ---- tệp gốc của một hóa đơn
    def resolve(self, kind: str, inv) -> Path | None:
        """Tệp chính của hóa đơn trên máy này: theo thư mục hóa đơn của máy + đường dẫn tương đối, hoặc đường dẫn
        đầy đủ đã lưu (nhập từ file trước đây)."""
        inv = dict(inv)
        root = self.root(kind)
        if inv.get("TepTuongDoi") and root:
            p = root / inv["TepTuongDoi"]
            if p.exists():
                return p
        if inv.get("DuongDanTep") and Path(inv["DuongDanTep"]).exists():
            return Path(inv["DuongDanTep"])
        return None

    def companion(self, kind: str, inv) -> Path | None:
        inv = dict(inv)
        root = self.root(kind)
        for rel in json.loads(inv.get("TepKem") or "[]") if inv.get("TepKem") else []:
            if root and (root / rel).exists():
                return root / rel
        return None

    def view_file(self, kind: str, inv) -> Path | None:
        """Tệp nên mở để xem: PDF / ảnh đi kèm nếu có, không thì tệp chính."""
        return self.companion(kind, inv) or self.resolve(kind, inv)

    @staticmethod
    def file_name(inv) -> str:
        inv = dict(inv)
        p = inv.get("TepTuongDoi") or inv.get("DuongDanTep") or ""
        return Path(p.replace("\\", "/")).name if p else ""

    @staticmethod
    def sub_folder(inv) -> str:
        rel = (dict(inv).get("TepTuongDoi") or "").replace("\\", "/")
        parent = str(Path(rel).parent) if rel else ""
        return "" if parent in (".", "") else parent.replace("/", "\\")

    # ---- tìm file mới
    def first_scan(self, kind: str) -> bool:
        root = self.db.setting(K_DIR[kind]) or ""
        return bool(root) and self.db.setting(K_SCANNED[kind]) != root

    def _walk(self, kind: str) -> list[tuple[str, str]] | None:
        root = self.root(kind)
        if not root or not root.is_dir():
            return None
        ext = self.extensions()
        out = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "~"))]
            for f in files:
                if f.startswith((".", "~$")) or Path(f).suffix.lower() not in ext:
                    continue
                full = Path(dirpath) / f
                out.append((full.relative_to(root).as_posix(), str(full)))
        out.sort()
        return out

    def _rows(self, kind: str) -> dict[str, dict]:
        return {r["DuongDan"]: dict(r) for r in self.db.query("SELECT * FROM TepDaQuet WHERE Chieu = ?", (kind,))}

    def new_files(self, kind: str) -> list[tuple[str, str]]:
        """(đường dẫn tương đối, đường dẫn đầy đủ) các file chưa xử lý (hoặc đã bị thay bằng file khác cùng tên) trong
        thư mục và mọi thư mục con."""
        files = self._walk(kind)
        if not files:
            return []
        seen = self._rows(kind)
        out = []
        for rel, full in files:
            r = seen.get(rel)
            if r is None:
                out.append((rel, full))
                continue
            try:
                st = Path(full).stat()
            except OSError:
                continue
            if r["KichThuoc"] is not None and (r["KichThuoc"] != st.st_size
                                               or abs((r["NgaySuaTep"] or 0) - st.st_mtime) > 2):
                out.append((rel, full))                 # file đã bị chép đè: đọc lại
        return out

    def recheck_files(self, kind: str, everything: bool = False) -> list[tuple[str, str]]:
        """File đã quét nhưng còn chờ nhân viên (Cần xem, số liệu khác) — xét lại mỗi lần quét vì hóa đơn có thể đã
        được nhập tay, đã thêm hợp đồng, đã xóa bản trùng… everything: quét lại toàn bộ (trừ file đã bỏ qua)."""
        files = self._walk(kind)
        if not files:
            return []
        seen = self._rows(kind)
        new = {rel for rel, _ in self.new_files(kind)}
        out = []
        for rel, full in files:
            r = seen.get(rel)
            if r is None or rel in new:
                continue
            if r["TrangThai"] in F_OPEN or (everything and r["TrangThai"] != F_SKIP):
                out.append((rel, full))
        return out

    def prune(self, kind: str) -> int:
        """Quên các file không còn trong thư mục (đã xóa, đã chuyển đi) để không còn báo trùng / cần xem."""
        files = self._walk(kind)
        if not files:                       # thư mục chưa sẵn sàng (ổ mạng, Drive chưa đồng bộ): không xóa gì
            return 0
        have = {rel for rel, _ in files}
        gone = [rel for rel in self._rows(kind) if rel not in have]
        for rel in gone:
            self.db.conn.execute("DELETE FROM TepDaQuet WHERE Chieu = ? AND DuongDan = ?", (kind, rel))
        self.db.conn.commit()
        return len(gone)

    def cache(self, kind: str, rels) -> dict[str, dict]:
        """Số liệu đã đọc lần trước của các file (để xét lại không phải đọc / gửi Gemini lần nữa)."""
        rows = self._rows(kind)
        out = {}
        for rel in rels:
            r = rows.get(rel)
            if r is None:
                continue
            data = json.loads(r["DuLieu"]) if r["DuLieu"] else {}
            out[rel] = {"row": r, "raw": data.get("raw"), "src": data.get("src") or "",
                        "not_invoice": bool(data.get("not_invoice"))}
        return out

    # ---- đọc (chạy được ở luồng nền: không dùng CSDL)
    @staticmethod
    def read(kind: str, files: list[tuple[str, str]], gemini=None, *, use_gemini: bool = True,
             cache: dict | None = None, known: set | None = None, force_gemini: bool = False,
             progress=None) -> list[Item]:
        """Đọc các file, ghép XML với PDF / ảnh cùng hóa đơn.
        - XML: số liệu lấy từ XML (chính xác nhất), không gửi Gemini để đọc.
        - PDF / ảnh không đi kèm XML: gửi Gemini đọc (trừ hóa đơn đã có trong phần mềm — known), lấy thêm lớp chữ
          của PDF để đối chiếu; mất kết nối Gemini thì dùng lớp chữ nếu đọc đủ, không thì để lần quét sau.
        cache: số liệu đã đọc lần trước (xét lại file cũ), known: khóa hóa đơn đã có, force_gemini: luôn gửi Gemini."""
        cache = cache or {}
        known = known or set()
        say = progress or (lambda _t: None)
        xml, other = [], []
        for rel, path in files:
            (xml if rel.lower().endswith(".xml") else other).append((rel, path))
        items: list[Item] = []
        by_stem: dict[tuple[str, str], Item] = {}
        by_key: dict[tuple, Item] = {}

        def new_item(rel, path, **kw):
            c = cache.get(rel)
            return Item(kind, rel, path, again=c is not None, old=(c or {}).get("row"), **kw)

        for rel, path in xml:
            it = new_item(rel, path, source=SRC_XML)
            try:
                it.data = xml_invoice.parse_invoice(path)
                by_key[invoice_key(kind, it.data)] = it
            except Exception as e:  # noqa: BLE001 - XML hỏng / không phải hóa đơn
                it.error = f"Không đọc được XML: {e}"
            items.append(it)
            by_stem[(str(Path(rel).parent), Path(rel).stem.lower())] = it

        def pair(it: Item) -> bool:
            k = invoice_key(kind, it.data)
            for key, x in by_key.items():
                if x is not it and key[0] == k[0] and key[2] == k[2] and k[2] and same_symbol(key[1], k[1]):
                    x.kem.append(it.rel)
                    return True
            return False

        need_gem = [(rel, path) for rel, path in other]
        gem_down = None                       # lỗi mất kết nối / hết hạn mức: không gửi tiếp các file sau
        texts: dict[str, tuple] = {}          # rel → (số liệu lớp chữ PDF | None, lỗi)

        def text_of(rel, path):
            if rel not in texts:
                t, err = None, ""
                if rel.lower().endswith(".pdf"):
                    try:
                        t = pdf_invoice.parse_invoice(path)
                    except pdf_invoice.InvoicePDFError as e:
                        err = str(e)
                    except Exception as e:  # noqa: BLE001 - PDF hỏng
                        err = f"Không đọc được PDF: {e}"
                texts[rel] = (t, err)
            return texts[rel]

        def pairs(d) -> bool:
            k = invoice_key(kind, d)
            return any(x.data is not d and key[0] == k[0] and key[2] == k[2] and k[2] and same_symbol(key[1], k[1])
                       for key, x in by_key.items())

        def wants_gemini(rel, path) -> bool:
            stem = (str(Path(rel).parent), Path(rel).stem.lower())
            if stem in by_stem and by_stem[stem].data:
                return False
            c = cache.get(rel) or {}
            if (c.get("raw") or c.get("not_invoice")) and not force_gemini:
                return False
            text, _ = text_of(rel, path)
            if text is not None and pairs(text):
                return False
            return force_gemini or not (_complete(text) and loose_key(kind, text) in known)

        # Gửi Gemini song song vài file một lúc (mỗi file Gemini đọc mất vài chục giây): quét nhanh hơn nhiều lần.
        gem_results: dict[str, object] = {}
        ask_list = [(r, p) for r, p in need_gem if wants_gemini(r, p)] if gemini is not None and use_gemini else []
        if ask_list:
            import concurrent.futures as cf
            import threading
            from .gemini import GeminiError
            stop = threading.Event()
            n_done = [0]
            lock = threading.Lock()

            def job(rel, path):
                if stop.is_set():
                    return stop.err
                try:
                    try:
                        res = gemini.extract_invoice(path)
                    except GeminiError as e:
                        # vượt số lần gọi mỗi phút / máy chủ bận: chờ một chút rồi thử lại một lần
                        if e.kind != "han_muc" and "quá tải" not in str(e) or stop.wait(GEMINI_RETRY_WAIT):
                            raise
                        res = gemini.extract_invoice(path)
                except GeminiError as e:
                    if e.offline:
                        stop.err = e
                        stop.set()
                    res = e
                except Exception as e:  # noqa: BLE001 - lỗi bất ngờ ở một file không làm hỏng cả lần quét
                    res = GeminiError(f"lỗi khi đọc: {e}")
                with lock:
                    n_done[0] += 1
                    say(f"Gemini đã đọc {n_done[0]}/{len(ask_list)} file…")
                return res
            say(f"Gemini đang đọc {len(ask_list)} file…")
            with cf.ThreadPoolExecutor(max_workers=GEMINI_PARALLEL) as ex:
                futs = {ex.submit(job, r, p): r for r, p in ask_list}
                for f in cf.as_completed(futs):
                    gem_results[futs[f]] = f.result()
        for rel, path in need_gem:
            stem = (str(Path(rel).parent), Path(rel).stem.lower())
            if stem in by_stem and by_stem[stem].data:
                by_stem[stem].kem.append(rel)
                continue
            it = new_item(rel, path)
            c = cache.get(rel) or {}
            if c.get("raw") and not force_gemini:
                it.data, it.source = c["raw"], c.get("src") or SRC_PDF
                if not pair(it):
                    items.append(it)
                continue
            if c.get("not_invoice") and not force_gemini:
                it.error, it.not_invoice = "Gemini: file này không phải hóa đơn.", True
                items.append(it)
                continue
            text, terr = text_of(rel, path)
            if terr:
                it.error = terr
            if text is not None:
                it.data, it.source = text, SRC_PDF
                if pair(it):
                    continue
            if rel in gem_results:
                from .gemini import GeminiError
                try:
                    g = gem_results[rel]
                    if g is None:
                        raise GeminiError("Gemini chưa đọc được (đã dừng vì lỗi kết nối).", "mang")
                    if isinstance(g, GeminiError):
                        raise g
                    if g.get("la_hoa_don") is False:
                        it.error, it.not_invoice, it.data = "Gemini: file này không phải hóa đơn.", True, None
                    else:
                        gd = from_gemini(g)
                        it.data, it.source, it.error = _merge(gd, text, it), SRC_GEMINI, ""
                        if _complete(text) and not it.warn:
                            it.source = SRC_PDF            # lớp chữ PDF và Gemini khớp nhau: tin được
                except GeminiError as e:
                    if e.offline:
                        gem_down = e
                    if e.offline and not _complete(text):
                        it.data, it.error, it.offline = None, f"Gemini chưa đọc được: {e}", True
                    elif text is None:
                        it.error = f"Gemini chưa đọc được: {e}"
            elif it.data is None and not it.error:
                it.error = "File ảnh không có lớp chữ."
            if it.data is None and gem_down is not None and not it.offline and not it.not_invoice:
                it.error, it.offline = f"Gemini chưa đọc được: {gem_down}", True
            if it.data is None and not it.offline and not it.not_invoice and (gemini is None or not use_gemini):
                it.error = (it.error or "Không đọc được.") + " Bấm \"Đọc bằng Gemini\" hoặc nhập tay."
            if it.data is not None and pair(it):
                continue
            items.append(it)
        return items

    # ---- ghi vào CSDL (luồng chính)
    def _mark(self, kind: str, rel: str, path: str, status: str, ma: str | None = None, note: str = "",
              data: dict | None = None) -> None:
        try:
            st = Path(path).stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size = mtime = None
        self.db.conn.execute(
            "INSERT OR REPLACE INTO TepDaQuet (Chieu, DuongDan, KichThuoc, NgaySuaTep, TrangThai, MaHoaDon, GhiChu,"
            " DuLieu, ThoiDiem) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, rel, size, mtime, status, ma, note or None,
             json.dumps(data, ensure_ascii=False, default=str) if data is not None else None, now_iso()))

    def _index(self, kind: str) -> dict[str, list[dict]]:
        col = "SoHoaDon" if kind == "ra" else "SoHoaDonGoc"
        idx: dict[str, list[dict]] = {}
        for r in self.db.query(f"SELECT * FROM {TABLE[kind]} WHERE DaXoa = 0"):
            idx.setdefault(_num(r[col]), []).append(dict(r))
        return idx

    def _existing(self, kind: str, values: dict, idx: dict | None = None):
        """Hóa đơn đã có trong phần mềm ứng với số liệu đọc từ file. So khớp mềm: số hóa đơn bỏ số 0 đầu, MST chỉ
        lấy chữ số, ký hiệu có / không kèm mẫu số hoặc để trống (đầu ra để trống ký hiệu thì cần khớp thêm ngày
        hoặc số tiền). Nhiều hóa đơn cùng khớp thì không chọn (để nhân viên xem)."""
        so = _num(values.get("SoHoaDon" if kind == "ra" else "SoHoaDonGoc"))
        if not so:
            return None
        rows = (idx if idx is not None else self._index(kind)).get(so, [])
        if kind == "vao":
            mst = _mst(values.get("MaThueNCC"))
            if not mst:
                return None
            rows = [r for r in rows if _mst(r["MaThueNCC"]) == mst]
        kh = _sym(values.get("KyHieuHD"))
        exact = [r for r in rows if _sym(r["KyHieuHD"]) == kh]
        if len(exact) == 1:
            return exact[0]
        comp = [r for r in rows if same_symbol(r["KyHieuHD"], kh)]
        if kind == "ra":
            day = (values.get("NgayXuatHD") or "")[:10]
            total = int(values.get("GiaTriSauThue") or 0)
            comp = [r for r in comp if (_sym(r["KyHieuHD"]) and kh) or (day and (r["NgayXuatHD"] or "")[:10] == day)
                    or (total and abs(int(r["GiaTriSauThue"] or 0) - total) <= 2)]
        return comp[0] if len(comp) == 1 else None

    def _link(self, kind: str, inv: dict, it: Item) -> str:
        """Hóa đơn đã có trong phần mềm: chỉ ghi nhận nơi chứa tệp (không đổi số liệu). Hóa đơn đã gắn một tệp
        khác vẫn còn trong thư mục thì giữ tệp cũ, file này coi là bản sao."""
        cur = inv.get("TepTuongDoi")
        root = self.root(kind)
        v = {}
        kem = json.loads(inv.get("TepKem") or "[]") if inv.get("TepKem") else []
        is_xml = it.rel.lower().endswith(".xml")
        if cur and cur != it.rel and root and (root / cur).exists():
            cur_xml = cur.lower().endswith(".xml")
            if cur_xml and not is_xml:                 # đã có XML, nay thêm PDF / ảnh của cùng hóa đơn: gắn kèm
                if it.rel not in kem:
                    v["TepKem"] = json.dumps(kem + [it.rel], ensure_ascii=False)
                    with self.db.auto():
                        self.db.update(TABLE[kind], inv["Ma"], v, commit=False)
                    inv.update(v)
                return "kem"
            if is_xml and not cur_xml:                 # đã gắn PDF / ảnh, nay có XML: XML làm tệp chính, PDF kèm
                v["TepTuongDoi"] = it.rel
                kem = [cur] + [k for k in kem if k != cur]
            else:
                return f"Bản sao: hóa đơn đã gắn tệp {cur}"
        elif cur != it.rel:
            v["TepTuongDoi"] = it.rel
        for k in it.kem:
            if k not in kem:
                kem.append(k)
        if json.dumps(kem, ensure_ascii=False) != (inv.get("TepKem") or "[]") and kem:
            v["TepKem"] = json.dumps(kem, ensure_ascii=False)
        if v:
            with self.db.auto():
                self.db.update(TABLE[kind], inv["Ma"], v, commit=False)
            inv.update(v)
        return ""

    DIFF = {"vao": (("NgayNhanHD", "Ngày hóa đơn", "date"), ("GiaTriTruocThue", "Tiền trước thuế", "money"),
                    ("VAT", "Tiền thuế", "money")),
            "ra": (("NgayXuatHD", "Ngày lập", "date"), ("GiaTriTruocThue", "Tiền trước thuế", "money"),
                   ("VAT", "Tiền thuế", "money"), ("MSTNguoiMua", "MST người mua", "mst"))}

    def diff(self, kind: str, inv: dict, values: dict) -> list[tuple[str, str, str, str]]:
        """Trường số liệu khác nhau giữa phần mềm và file (chỉ so khi cả hai bên đều có): (cột, tên, cũ, mới)."""
        out = []
        for col, label, typ in self.DIFF[kind]:
            a, b = inv.get(col), values.get(col)
            if a in (None, "") or b in (None, "", 0):
                continue
            if typ == "money":
                if abs(int(a or 0) - int(b or 0)) > 2:
                    out.append((col, label, fmt_money(a), fmt_money(b)))
            elif typ == "date":
                if str(a)[:10] != str(b)[:10]:
                    out.append((col, label, str(a)[:10], str(b)[:10]))
            elif _mst(a) != _mst(b):
                out.append((col, label, str(a), str(b)))
        return out

    def values_for(self, kind: str, it: Item, mst_cty: str = "") -> tuple[dict, list[str]]:
        values, notes = invoice_values(kind, it.data, it.path, mst_cty)
        values.update(TepTuongDoi=it.rel, TepKem=json.dumps(it.kem, ensure_ascii=False) if it.kem else None,
                      NguonSoLieu=it.source)
        if it.warn:
            notes.append(it.warn)
        return values, notes

    def review_reason(self, kind: str, it: Item, values: dict, mst_cty: str = "") -> str | None:
        """Lý do phải để nhân viên xem (None = thêm tự động được). Ghi thêm MaHopDong, _GoiY vào values."""
        missing = [label for key, label in REQUIRED[kind] if not values.get(key)]
        if missing:
            return "Thiếu " + ", ".join(missing)
        m = invoice_match.match_contract(self.db, kind, it.data)
        if kind == "ra":
            if not m.ma:
                return "Chưa nhận ra hợp đồng" + (f": {m.note}" if m.note else "")
            values["MaHopDong"] = m.ma
        else:
            values["MaHopDong"] = m.ma or None
        values["_GoiY"] = list(m.goi_y or [])
        check = dupl.out_invoices if kind == "ra" else dupl.in_invoices
        found = check(self.db, values)
        if found:
            return "Nghi trùng: " + found[0]
        if kind == "vao" and mst_cty and it.data.get("MSTNguoiMua") and _mst(it.data["MSTNguoiMua"]) != _mst(mst_cty):
            return f"MST người mua {it.data['MSTNguoiMua']} khác MST công ty"
        return it.warn or None

    def prepare(self, kind: str, it: Item, mst_cty: str = "") -> tuple[dict, list[str], str | None]:
        """Giá trị để lưu + lưu ý + lý do phải để nhân viên xem (None = thêm tự động được)."""
        values, notes = self.values_for(kind, it, mst_cty)
        return values, notes, self.review_reason(kind, it, values, mst_cty)

    def save(self, kind: str, values: dict) -> str:
        """Thêm hóa đơn (đã rà soát trùng). Số liệu đọc từ file nên là dữ liệu tự động."""
        cs = ContractService(self.db)
        v = {k: val for k, val in values.items() if k != "_GoiY"}
        v[dupl.CONFIRM_KEY] = True
        with self.db.auto():
            return cs.save_out_invoice(v) if kind == "ra" else cs.save_in_invoice(v)

    def process(self, kind: str, items: list[Item], *, mst_cty: str = "", auto_add: bool | None = None,
                user_asked: bool = False) -> dict:
        """Ghi kết quả đọc vào CSDL: tự thêm hóa đơn mới rõ ràng; hóa đơn đã có thì chỉ ghi nhận nơi chứa tệp (số
        liệu khác thì để nhân viên xem); còn lại để Cần xem.
        user_asked: nhân viên chọn file ở Cần xem và bấm "Đọc bằng Gemini" — đọc xong, hóa đơn mới rõ ràng (không
        trùng / nghi trùng, đủ trường, đầu ra nhận ra hợp đồng) được thêm luôn, kể cả file từ lần quét đầu.
        Trả {them: [mã], can_xem: n, co_san: n, lech: n, cho_gemini: n}."""
        first = self.first_scan(kind)
        if auto_add is None:
            auto_add = user_asked or (self.flag(K_AUTO_ADD) and not first)
        res = {"them": [], "can_xem": 0, "co_san": 0, "lech": 0, "cho_gemini": 0, "lan_dau": first}
        idx = self._index(kind)
        seen: set[tuple] = set()
        for it in items:
            for rel in it.kem:
                self._mark(kind, rel, str(Path(it.path).parent.joinpath(Path(rel).name)), F_COMPANION,
                           note=f"Cùng hóa đơn với {Path(it.rel).name}")
            if it.data is None:
                if it.offline:                        # mất kết nối Gemini: chưa ghi nhớ, lần quét sau đọc lại
                    res["cho_gemini"] += 1
                    continue
                self._mark(kind, it.rel, it.path, F_REVIEW, note=it.error or "Không đọc được",
                           data={"not_invoice": True} if it.not_invoice else None)
                res["can_xem"] += 1
                continue
            raw = {"raw": it.data, "src": it.source}
            try:
                values, notes = self.values_for(kind, it, mst_cty)
                inv = self._existing(kind, values, idx)
            except Exception as e:  # noqa: BLE001 - dữ liệu đọc được lỗi bất thường: để nhân viên xem
                self._mark(kind, it.rel, it.path, F_REVIEW, note=f"Lỗi khi đọc số liệu: {e}", data=raw)
                res["can_xem"] += 1
                continue
            old = it.old or {}
            if inv is not None:
                note = self._link(kind, inv, it)
                if note == "kem":
                    self._mark(kind, it.rel, it.path, F_COMPANION, inv["Ma"],
                               note=f"Cùng hóa đơn với {Path(inv['TepTuongDoi']).name}", data=raw)
                    res["co_san"] += 1
                    continue
                diff = self.diff(kind, inv, values)
                if diff:
                    self._mark(kind, it.rel, it.path, F_CONFLICT, inv["Ma"],
                               note="Đã có trong phần mềm, số liệu trong file khác: "
                                    + "; ".join(f"{label} {a} → {b}" for _c, label, a, b in diff)
                                    + (" (Gemini đọc, nên mở file xem lại)" if it.source == SRC_GEMINI else ""),
                               data=_dump(values, notes, it))
                    res["lech"] += 1
                    res["can_xem"] += 1
                    continue
                keep = old.get("TrangThai") == F_ADDED and old.get("MaHoaDon") == inv["Ma"]
                self._mark(kind, it.rel, it.path, F_ADDED if keep else F_EXISTS, inv["Ma"],
                           note=note or (old.get("GhiChu") if keep else ""), data=raw)
                res["co_san"] += 1
                continue
            key = invoice_key(kind, it.data)
            if key in seen and key[2]:
                self._mark(kind, it.rel, it.path, F_REVIEW, note="Trùng với file khác trong cùng lượt quét",
                           data=_dump(values, notes, it))
                res["can_xem"] += 1
                continue
            seen.add(key)
            try:
                why = self.review_reason(kind, it, values, mst_cty)
            except Exception as e:  # noqa: BLE001
                why = f"Lỗi khi đọc số liệu: {e}"
            if why is None and not user_asked and (old.get("GhiChu") or "").startswith("Lần quét đầu"):
                why = old["GhiChu"]                  # hóa đơn cũ từ lần quét đầu: vẫn chờ nhân viên chọn
            if why is None and not auto_add:
                why = "Lần quét đầu: chờ nhân viên chọn thêm" if first else "Chờ nhân viên thêm (đang tắt tự thêm)"
            if why is None:
                try:
                    ma = self.save(kind, values)
                except (ValidationError, DuplicateWarning) as e:
                    why = str(e)
                else:
                    self._mark(kind, it.rel, it.path, F_ADDED, ma, data=raw)
                    row = self.db.get(TABLE[kind], ma)
                    if row is not None:
                        idx.setdefault(_num(values.get("SoHoaDon" if kind == "ra" else "SoHoaDonGoc")),
                                       []).append(dict(row))
                    res["them"].append(ma)
                    continue
            self._mark(kind, it.rel, it.path, F_REVIEW, note=why, data=_dump(values, notes, it))
            res["can_xem"] += 1
        if first and not res["cho_gemini"]:
            self.db.set_setting(K_SCANNED[kind], self.db.setting(K_DIR[kind]))
        self.db.conn.commit()
        return res

    def apply_file_values(self, kind: str, rel: str) -> list[tuple[str, str, str, str]]:
        """File có số liệu khác hóa đơn trong phần mềm: nhân viên chọn cập nhật theo file (đã xác nhận cũ → mới)."""
        r = self._rows(kind).get(rel)
        if not r or not r["MaHoaDon"]:
            return []
        inv = self.db.get(TABLE[kind], r["MaHoaDon"])
        values = load_values(json.loads(r["DuLieu"]) if r["DuLieu"] else None)
        if inv is None or not values:
            return []
        diff = self.diff(kind, dict(inv), values)
        if diff:
            self.db.update(TABLE[kind], inv["Ma"], {c: values[c] for c, *_ in diff})
        self.set_status(kind, [rel], F_EXISTS, inv["Ma"], note="Đã cập nhật số liệu theo file")
        return diff

    # ---- danh sách cho cửa sổ "Thư mục hóa đơn"
    def files(self, kind: str | None = None, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM TepDaQuet WHERE 1 = 1"
        params: list = []
        if kind:
            sql += " AND Chieu = ?"
            params.append(kind)
        if status:
            sql += " AND TrangThai = ?"
            params.append(status)
        rows = [dict(r) for r in self.db.query(sql + " ORDER BY ThoiDiem DESC, DuongDan", params)]
        invs = {}
        for k in KINDS:
            for r in self.db.query(f"SELECT * FROM {TABLE[k]} WHERE DaXoa = 0"):
                invs[(k, r["Ma"])] = dict(r)
        for r in rows:
            r["HoaDon"] = invs.get((r["Chieu"], r["MaHoaDon"])) if r["MaHoaDon"] else None
            r["Data"] = json.loads(r["DuLieu"]) if r["DuLieu"] else None
            if r["HoaDon"] is None and r["TrangThai"] in (F_ADDED, F_EXISTS, F_CONFLICT):
                r["TrangThai"] = F_REVIEW if r["TrangThai"] == F_ADDED else r["TrangThai"]
                r["GhiChu"] = r["GhiChu"] or "Hóa đơn đã bị xóa khỏi phần mềm"
        return rows

    def full_path(self, kind: str, rel: str) -> Path | None:
        root = self.root(kind)
        return root / rel if root else None

    def set_status(self, kind: str, rels: list[str], status: str, ma: str | None = None, note: str | None = None) -> None:
        for rel in rels:
            self.db.conn.execute("UPDATE TepDaQuet SET TrangThai = ?, MaHoaDon = COALESCE(?, MaHoaDon),"
                                 " GhiChu = ?, ThoiDiem = ? WHERE Chieu = ? AND DuongDan = ?",
                                 (status, ma, note, now_iso(), kind, rel))
        self.db.conn.commit()

    def forget(self, kind: str, rels: list[str]) -> None:
        """Cho quét lại các file (VD sau khi có kết nối Gemini để đọc ảnh)."""
        for rel in rels:
            self.db.conn.execute("DELETE FROM TepDaQuet WHERE Chieu = ? AND DuongDan = ?", (kind, rel))
        self.db.conn.commit()

    def review_count(self) -> int:
        return self.db.one("SELECT COUNT(*) c FROM TepDaQuet WHERE TrangThai IN (?, ?)", F_OPEN)["c"]

    def save_summary(self, summary: dict) -> None:
        self.db.set_setting(K_LAST, now_iso())
        self.db.set_setting(K_SUMMARY, json.dumps(summary, ensure_ascii=False))

    def summary(self) -> dict:
        try:
            return json.loads(self.db.setting(K_SUMMARY) or "{}")
        except ValueError:
            return {}


def _dump(values: dict, notes: list[str], it: Item | None = None) -> dict:
    v = {k: val for k, val in values.items() if k not in ("_Dong", "_GoiY")}
    out = {"values": v, "dong": values.get("_Dong"), "notes": notes}
    if it is not None:
        out.update(raw=it.data, src=it.source)
    return out


def load_values(data: dict | None) -> dict:
    """Giá trị đã lưu của một file Cần xem → values để mở form / thêm (kèm các dòng hàng)."""
    if not data:
        return {}
    v = dict(data.get("values") or {})
    if data.get("dong"):
        v["_Dong"] = data["dong"]
    return v
