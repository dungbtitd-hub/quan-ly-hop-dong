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
F_LABELS = {F_ADDED: "Đã tự thêm", F_EXISTS: "Có từ trước", F_REVIEW: "Cần xem", F_SKIP: "Đã bỏ qua",
            F_COMPANION: "Tệp kèm (cùng hóa đơn)"}

SRC_XML, SRC_PDF, SRC_GEMINI, SRC_GEMINI_OK = "XML", "PDF", "Gemini", "Gemini (đã soát)"
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


def from_gemini(g: dict) -> dict:
    """Kết quả Gemini đọc ảnh / PDF quét → cùng dạng với xml_invoice.parse_invoice."""
    def money(x):
        try:
            return int(x or 0)
        except (TypeError, ValueError):
            return 0
    lines = []
    for ln in g.get("dong") or []:
        if not (ln.get("ten") or "").strip():
            continue
        lines.append({"TenHang": ln["ten"].strip(), "DVT": ln.get("dvt") or "", "SoLuong": ln.get("so_luong"),
                      "DonGia": money(ln.get("don_gia")), "ThanhTien": money(ln.get("thanh_tien")),
                      "ThueSuat": ln.get("thue_suat") or ""})
    names = [x["TenHang"] for x in lines]
    d = {"SoHopDong": "", "VanBan": "", "KyHieuMauSo": (g.get("mau_so") or "").strip(),
         "KyHieuHD": (g.get("ky_hieu") or "").strip(), "SoHoaDon": (g.get("so_hoa_don") or "").strip(),
         "NgayLap": (g.get("ngay_lap") or "")[:10], "TenNguoiBan": (g.get("ten_nguoi_ban") or "").strip(),
         "MSTNguoiBan": (g.get("mst_nguoi_ban") or "").replace(" ", ""),
         "TenNguoiMua": (g.get("ten_nguoi_mua") or "").strip(),
         "MSTNguoiMua": (g.get("mst_nguoi_mua") or "").replace(" ", ""),
         "GiaTriTruocThue": money(g.get("tien_truoc_thue")), "VAT": money(g.get("tien_thue")),
         "GiaTriSauThue": money(g.get("tong_thanh_toan")), "PhiKhac": 0, "TenPhiKhac": "",
         "ThueSuat": g.get("thue_suat") or "", "ThueSuatHD": g.get("thue_suat") or "",
         "MatHang": xml_invoice.join_items(names), "ChiTietThueSuat": [], "Dong": lines}
    if d["GiaTriSauThue"] and not d["GiaTriTruocThue"]:
        d["GiaTriTruocThue"] = d["GiaTriSauThue"] - d["VAT"]
    return d


def invoice_key(kind: str, d: dict) -> tuple:
    so = "".join(ch for ch in str(d.get("SoHoaDon") or "") if ch.isdigit()).lstrip("0")
    mst = "".join(ch for ch in str(d.get("MSTNguoiBan") or "") if ch.isdigit())
    return (mst if kind == "vao" else "", (d.get("KyHieuHD") or "").strip().upper(), so)


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

    def new_files(self, kind: str) -> list[tuple[str, str]]:
        """(đường dẫn tương đối, đường dẫn đầy đủ) các file chưa xử lý trong thư mục và mọi thư mục con."""
        root = self.root(kind)
        if not root or not root.is_dir():
            return []
        seen = {r["DuongDan"] for r in self.db.query("SELECT DuongDan FROM TepDaQuet WHERE Chieu = ?", (kind,))}
        ext = self.extensions()
        out = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "~"))]
            for f in files:
                if f.startswith((".", "~$")) or Path(f).suffix.lower() not in ext:
                    continue
                full = Path(dirpath) / f
                rel = full.relative_to(root).as_posix()
                if rel not in seen:
                    out.append((rel, str(full)))
        out.sort()
        return out

    # ---- đọc (chạy được ở luồng nền: không dùng CSDL)
    @staticmethod
    def read(kind: str, files: list[tuple[str, str]], gemini=None, *, use_gemini: bool = True) -> list[Item]:
        """Đọc các file mới, ghép XML với PDF / ảnh cùng hóa đơn. File không có lớp chữ (ảnh, PDF quét) thì nhờ
        Gemini đọc nếu có; không có Gemini (hoặc mất kết nối) thì để nhân viên xem."""
        xml, other = [], []
        for rel, path in files:
            (xml if rel.lower().endswith(".xml") else other).append((rel, path))
        items: list[Item] = []
        by_stem: dict[tuple[str, str], Item] = {}
        by_key: dict[tuple, Item] = {}
        for rel, path in xml:
            it = Item(kind, rel, path, source=SRC_XML)
            try:
                it.data = xml_invoice.parse_invoice(path)
                by_key[invoice_key(kind, it.data)] = it
            except Exception as e:  # noqa: BLE001 - XML hỏng / không phải hóa đơn
                it.error = f"Không đọc được XML: {e}"
            items.append(it)
            by_stem[(str(Path(rel).parent), Path(rel).stem.lower())] = it
        for rel, path in other:
            stem = (str(Path(rel).parent), Path(rel).stem.lower())
            if stem in by_stem and by_stem[stem].data:
                by_stem[stem].kem.append(rel)
                continue
            it = Item(kind, rel, path)
            if rel.lower().endswith(".pdf"):
                try:
                    it.data, it.source = pdf_invoice.parse_invoice(path), SRC_PDF
                except pdf_invoice.InvoicePDFError as e:
                    it.error = str(e)
                if it.data and invoice_key(kind, it.data) in by_key:
                    by_key[invoice_key(kind, it.data)].kem.append(rel)
                    continue
            if it.data is None:
                if gemini is not None and use_gemini:
                    from .gemini import GeminiError
                    try:
                        g = gemini.extract_invoice(path)
                        if g.get("la_hoa_don") is False:
                            it.error = "Gemini: file này không phải hóa đơn."
                        else:
                            it.data, it.source, it.error = from_gemini(g), SRC_GEMINI, ""
                    except GeminiError as e:
                        it.error = f"Gemini chưa đọc được: {e}"
                        it.offline = e.offline
                else:
                    it.error = (it.error or "File ảnh không có lớp chữ.") + (
                        " Bấm \"Đọc bằng Gemini\" hoặc nhập tay." if gemini is None or not use_gemini else "")
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

    def _existing(self, kind: str, values: dict):
        if kind == "ra":
            return self.db.one("SELECT * FROM HoaDonDauRa WHERE DaXoa = 0 AND COALESCE(KyHieuHD,'') = ? AND SoHoaDon = ?",
                               (values.get("KyHieuHD") or "", values.get("SoHoaDon") or ""))
        return self.db.one("SELECT * FROM HoaDonDauVao WHERE DaXoa = 0 AND MaThueNCC = ? AND COALESCE(KyHieuHD,'') = ?"
                           " AND SoHoaDonGoc = ?", (values.get("MaThueNCC") or "", values.get("KyHieuHD") or "",
                                                    values.get("SoHoaDonGoc") or ""))

    def _link(self, kind: str, inv, it: Item) -> None:
        """Hóa đơn đã có trong phần mềm: chỉ gắn tệp gốc nếu hóa đơn chưa gắn (không đổi số liệu)."""
        if inv["TepTuongDoi"] if "TepTuongDoi" in inv.keys() else False:
            return
        v = {"TepTuongDoi": it.rel}
        if it.kem:
            v["TepKem"] = json.dumps(it.kem, ensure_ascii=False)
        with self.db.auto():
            self.db.update(TABLE[kind], inv["Ma"], v, commit=False)

    def prepare(self, kind: str, it: Item, mst_cty: str = "") -> tuple[dict, list[str], str | None]:
        """Giá trị để lưu + lưu ý + lý do phải để nhân viên xem (None = thêm tự động được)."""
        values, notes = invoice_values(kind, it.data, it.path, mst_cty)
        values.update(TepTuongDoi=it.rel, TepKem=json.dumps(it.kem, ensure_ascii=False) if it.kem else None,
                      NguonSoLieu=it.source)
        missing = [label for key, label in REQUIRED[kind] if not values.get(key)]
        if missing:
            return values, notes, "Thiếu " + ", ".join(missing)
        m = invoice_match.match_contract(self.db, kind, it.data)
        if kind == "ra":
            if not m.ma:
                return values, notes, "Chưa nhận ra hợp đồng" + (f": {m.note}" if m.note else "")
            values["MaHopDong"] = m.ma
        else:
            values["MaHopDong"] = m.ma or None
        values["_GoiY"] = list(m.goi_y or [])
        check = dupl.out_invoices if kind == "ra" else dupl.in_invoices
        found = check(self.db, values)
        if found:
            return values, notes, "Nghi trùng: " + found[0]
        if kind == "vao" and mst_cty and it.data.get("MSTNguoiMua") and \
                "".join(c for c in it.data["MSTNguoiMua"] if c.isdigit()) != "".join(c for c in mst_cty if c.isdigit()):
            return values, notes, f"MST người mua {it.data['MSTNguoiMua']} khác MST công ty"
        return values, notes, None

    def save(self, kind: str, values: dict) -> str:
        """Thêm hóa đơn (đã rà soát trùng). Số liệu đọc từ file nên là dữ liệu tự động."""
        cs = ContractService(self.db)
        v = {k: val for k, val in values.items() if k != "_GoiY"}
        v[dupl.CONFIRM_KEY] = True
        with self.db.auto():
            return cs.save_out_invoice(v) if kind == "ra" else cs.save_in_invoice(v)

    def process(self, kind: str, items: list[Item], *, mst_cty: str = "", auto_add: bool | None = None) -> dict:
        """Ghi kết quả đọc vào CSDL: tự thêm hóa đơn rõ ràng, hóa đơn đã có thì gắn tệp, còn lại để Cần xem.
        Trả {them: [mã], can_xem: n, co_san: n, cho_gemini: n}."""
        first = self.first_scan(kind)
        if auto_add is None:
            auto_add = self.flag(K_AUTO_ADD) and not first
        res = {"them": [], "can_xem": 0, "co_san": 0, "cho_gemini": 0, "lan_dau": first}
        seen: set[tuple] = set()
        for it in items:
            for rel in it.kem:
                self._mark(kind, rel, str(Path(it.path).parent.joinpath(Path(rel).name)), F_COMPANION,
                           note=f"Cùng hóa đơn với {Path(it.rel).name}")
            if it.data is None:
                if it.offline:                        # mất kết nối Gemini: chưa ghi nhớ, lần quét sau đọc lại
                    res["cho_gemini"] += 1
                    continue
                self._mark(kind, it.rel, it.path, F_REVIEW, note=it.error or "Không đọc được")
                res["can_xem"] += 1
                continue
            try:
                values, notes, why = self.prepare(kind, it, mst_cty)
            except Exception as e:  # noqa: BLE001 - dữ liệu đọc được lỗi bất thường: để nhân viên xem
                self._mark(kind, it.rel, it.path, F_REVIEW, note=f"Lỗi khi đọc số liệu: {e}")
                res["can_xem"] += 1
                continue
            inv = self._existing(kind, values)
            if inv is not None:
                self._link(kind, inv, it)
                self._mark(kind, it.rel, it.path, F_EXISTS, inv["Ma"])
                res["co_san"] += 1
                continue
            key = invoice_key(kind, it.data)
            if key in seen and key[2]:
                self._mark(kind, it.rel, it.path, F_REVIEW, note="Trùng với file khác trong cùng lượt quét",
                           data=_dump(values, notes))
                res["can_xem"] += 1
                continue
            seen.add(key)
            if why is None and not auto_add:
                why = "Lần quét đầu: chờ nhân viên chọn thêm" if first else "Chờ nhân viên thêm (đang tắt tự thêm)"
            if why is None:
                try:
                    ma = self.save(kind, values)
                except (ValidationError, DuplicateWarning) as e:
                    why = str(e)
                else:
                    self._mark(kind, it.rel, it.path, F_ADDED, ma)
                    res["them"].append(ma)
                    continue
            self._mark(kind, it.rel, it.path, F_REVIEW, note=why, data=_dump(values, notes))
            res["can_xem"] += 1
        if first and not res["cho_gemini"]:
            self.db.set_setting(K_SCANNED[kind], self.db.setting(K_DIR[kind]))
        self.db.conn.commit()
        return res

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
            if r["HoaDon"] is None and r["TrangThai"] in (F_ADDED, F_EXISTS):
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
        return self.db.one("SELECT COUNT(*) c FROM TepDaQuet WHERE TrangThai = ?", (F_REVIEW,))["c"]

    def save_summary(self, summary: dict) -> None:
        self.db.set_setting(K_LAST, now_iso())
        self.db.set_setting(K_SUMMARY, json.dumps(summary, ensure_ascii=False))

    def summary(self) -> dict:
        try:
            return json.loads(self.db.setting(K_SUMMARY) or "{}")
        except ValueError:
            return {}


def _dump(values: dict, notes: list[str]) -> dict:
    v = {k: val for k, val in values.items() if k not in ("_Dong", "_GoiY")}
    return {"values": v, "dong": values.get("_Dong"), "notes": notes}


def load_values(data: dict | None) -> dict:
    """Giá trị đã lưu của một file Cần xem → values để mở form / thêm (kèm các dòng hàng)."""
    if not data:
        return {}
    v = dict(data.get("values") or {})
    if data.get("dong"):
        v["_Dong"] = data["dong"]
    return v
