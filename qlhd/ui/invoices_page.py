"""Hóa đơn đầu ra và đầu vào: nhập tay, đọc từ XML hoặc PDF, ghi nhận thanh toán, mở tệp gốc."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import in_invoice_excel, invoice_match, pdf_invoice, xml_invoice
from ..core.assets import AssetService
from ..core.contracts import (INVOICE_KINDS, TAX_GROUPS, ValidationError, guess_tax_group, payment_status,
                              rate_from_amounts)
from ..core.finance_import import FileNeedsPassword
from ..core.reports import fmt_date, fmt_money
from .contracts_page import edit_payment, payment_dialog
from . import style
from .widgets import (VAT_HINT, Combo, DataTable, EditCombo, FormDialog, ask_secret, button, confirm, confirm_changes,
                      error, hint, info, rate_field,
                      page_title, run_safely, toolbar)


CHOOSE = "__chon_hop_dong__"
CHOOSE_LABEL = "— Chọn hợp đồng —"
TAX_GROUP_OPTIONS = [("Chịu thuế GTGT (nhóm 1 bảng kê)", "Chịu thuế"),
                     ("Không chịu thuế GTGT (nhóm 5 bảng kê)", "Không chịu thuế")]


def _check_chosen(values: dict) -> None:
    if values.get("MaHopDong") == CHOOSE:
        raise ValidationError("Chưa chọn hợp đồng cho hóa đơn. Hãy chọn đúng hợp đồng"
                              " (hóa đơn đầu vào không theo hợp đồng thì chọn \"Không có – mua hàng trực tiếp\").")


def contract_label(h) -> str:
    return f"{h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})"


def open_contracts(ctx, kind: str, keep=()) -> list:
    """Hợp đồng cùng chiều với hóa đơn. Hóa đơn đầu ra: bỏ các hợp đồng đã xuất đủ hóa đơn theo giá trị,
    luôn giữ các hợp đồng trong `keep` (hợp đồng đang gắn với hóa đơn, hợp đồng gợi ý)."""
    keep = {k for k in keep if k}
    out = []
    for h in ctx.cs.contracts("Đầu ra" if kind == "ra" else "Đầu vào"):
        s = ctx.cs.summary(h["Ma"]) if kind == "ra" else None
        if kind != "ra" or h["Ma"] in keep or not s["GiaTriHD"] or s["ConPhaiXuat"] > 0:
            out.append(h)
    return out


def type_filter_field(ctx, contracts) -> dict:
    """Ô lọc danh sách hợp đồng theo loại hợp đồng (chỉ để tìm nhanh, không lưu)."""
    used = {h["MaLoaiHD"] for h in contracts}
    types = [(t["TenLoaiHD"], t["Ma"]) for t in ctx.cs.contract_types() if t["Ma"] in used]
    return {"key": "_LoaiHD", "label": "Loại hợp đồng", "kind": "combo", "filter": True,
            "options": [("(Tất cả loại)", None)] + types + ([("(Chưa phân loại)", "")] if None in used else []),
            "hint": "Chọn loại để thu gọn danh sách hợp đồng bên dưới. Hợp đồng đã xuất đủ hóa đơn không hiện."}


def bind_type_filter(dlg, contracts, head=()) -> None:
    """Đổi loại hợp đồng thì lọc lại ô Hợp đồng; head: các lựa chọn luôn đứng đầu (Chọn hợp đồng, Không có…)."""
    combo, kinds = dlg.widget("MaHopDong"), dlg.widget("_LoaiHD")
    by_ma = {h["Ma"]: h for h in contracts}
    full = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]

    def apply(*_):
        loai, cur = kinds.value(), combo.value()
        opts = [o for o in full if o[1] not in by_ma or loai is None or (by_ma[o[1]]["MaLoaiHD"] or "") == loai]
        combo.set_options(opts, cur if any(o[1] == cur for o in opts) else (opts[0][1] if opts else None))
    kinds.currentIndexChanged.connect(apply)


def out_invoice_row(ctx, inv) -> dict:
    """Hóa đơn đầu ra kèm tình trạng thanh toán: đã thu, còn phải thu, ngày thanh toán, số tham chiếu sao kê."""
    r = dict(inv)
    pays = ctx.cs.invoice_payments(inv["Ma"])
    r["_pays"] = pays
    r["DaThu"] = sum(p["SoTien"] for p in pays)
    r["ConPhaiThu"] = inv["GiaTriSauThue"] - r["DaThu"] if inv["TinhChatHD"] != "Đã hủy" else 0
    r["TTThanhToan"] = "Đã hủy" if inv["TinhChatHD"] == "Đã hủy" else payment_status(inv["GiaTriSauThue"], r["DaThu"])
    r["NgayTT"] = ", ".join(fmt_date(p["NgayThanhToan"]) for p in pays)
    r["SoThamChieuSK"] = ", ".join(p["SoThamChieuSK"] for p in pays if p["SoThamChieuSK"])
    return r


def invoice_payment(ctx, parent, r: dict) -> bool:
    """Ghi nhận thanh toán cho một hóa đơn đầu ra; đã có lần thanh toán thì mở chi tiết (sửa / xóa / thu thêm)."""
    if ctx.cs.invoice_payments(r["Ma"]):
        dlg = InvoicePaymentsDialog(ctx, r, parent)
        dlg.exec()
        return dlg.changed
    v = payment_dialog(ctx, parent, r["MaHopDong"], {"MaHDDauRa": r["Ma"], "SoTien": max(r["ConPhaiThu"], 0),
                                                     "HinhThuc": "Chuyển khoản"})
    return bool(v and run_safely(parent, ctx.cs.save_payment, v))


def parse_invoice_file(path: str) -> dict:
    if path.lower().endswith(".xml"):
        return xml_invoice.parse_invoice(path)
    return pdf_invoice.parse_invoice(path)


def read_invoice_file(parent, path: str) -> dict | None:
    try:
        return parse_invoice_file(path)
    except (xml_invoice.InvoiceXMLError, pdf_invoice.InvoicePDFError) as e:
        error(parent, f"{Path(path).name}: {e}")
        return None


def invoice_values(kind: str, d: dict, path: str, mst_cty: str = "") -> tuple[dict, list[str]]:
    """Dữ liệu đọc từ file → giá trị lưu cho hóa đơn đầu ra / đầu vào, kèm các lưu ý cho người nhập."""
    notes = []
    if d.get("ThieuTruong"):
        notes.append("Chưa đọc được: " + ", ".join(d["ThieuTruong"]) + ". Hãy kiểm tra và nhập bổ sung.")
    rate = d.get("ThueSuatHD") or rate_from_amounts(d["GiaTriTruocThue"], d["VAT"])
    common = {"KyHieuMauSo": d["KyHieuMauSo"], "KyHieuHD": d["KyHieuHD"], "GiaTriTruocThue": d["GiaTriTruocThue"],
              "VAT": d["VAT"], "GiaTriSauThue": d["GiaTriSauThue"], "MatHang": d.get("MatHang") or "",
              "ThueSuat": rate, "DuongDanTep": path}
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


class InvoicesPage(QWidget):
    """kind = 'ra' (hóa đơn đầu ra) hoặc 'vao' (hóa đơn đầu vào)."""

    open_contract = Signal(str)       # Ma hợp đồng – mở trang Hợp đồng tại hợp đồng đó

    def __init__(self, ctx, kind: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.kind = kind
        self.feature = "HoaDonDauRa" if kind == "ra" else "HoaDonDauVao"
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Hóa đơn đầu ra" if kind == "ra" else "Hóa đơn đầu vào"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm số hóa đơn, đối tác, MST, mặt hàng…")
        self.search.setMinimumWidth(280)
        self.search.textChanged.connect(self.refresh)
        add_ok = ctx.can(self.feature, "Them")
        b_add = button("Nhập tay", self.add_manual, "Primary")
        b_file = button("Nhập từ file XML / PDF (chọn nhiều file)…", self.add_from_files)
        b_edit = button("Sửa", self.edit)
        b_del = button("Xóa", self.delete, "Danger")
        b_open = button("Mở tệp gốc", self.open_file)
        b_hd = button("Mở hợp đồng", self.go_contract)
        b_hd.setToolTip("Mở hợp đồng của hóa đơn đang chọn để xem, sửa hợp đồng, công việc, thanh toán")
        b_hd.setEnabled(ctx.can("HopDong"))
        btns = [b_add, b_file, b_edit, b_del, b_open, b_hd]
        if kind == "vao":
            b_cls = button("Phân loại dòng hàng…", self.classify_lines)
            b_cls.setToolTip("Xếp các dòng hàng của hóa đơn vào chi phí / CCDC / TSCĐ (sửa nếu phần mềm tự xếp nhầm)")
            b_cls.setEnabled(ctx.can("TaiSan", "Sua"))
            btns.append(b_cls)
        b_add.setEnabled(add_ok)
        b_file.setEnabled(add_ok)
        if kind == "vao":
            b_xlsx = button("Nhập từ Excel…", self.add_from_excel)
            b_xlsx.setEnabled(add_ok)
            btns[2:2] = [b_xlsx, button("Tải file Excel mẫu", self.save_excel_template)]
        b_edit.setEnabled(ctx.can(self.feature, "Sua"))
        b_del.setEnabled(ctx.can(self.feature, "Xoa"))
        if kind == "ra":
            b_pay = button("Ghi nhận thanh toán", self.add_payment)
            b_pays = button("Thanh toán đã ghi…", self.show_payments)
            b_pay.setEnabled(ctx.can("HoaDonDauRa", "Sua"))
            btns[4:4] = [b_pay, b_pays]
            self.only = Combo([("Tất cả", None), ("Còn phải thu", "con"), ("Đã thu đủ", "du"),
                               ("Đã thu, chưa có số tham chiếu sao kê", "chua_sk")])
            b_ref = button("Cập nhật số sao kê…", self.update_refs)
            b_ref.setEnabled(ctx.can("HoaDonDauRa", "Sua"))
            btns.insert(6, b_ref)
        else:
            self.only = Combo([("Tất cả", None), ("Có hợp đồng", "co"), ("Mua trực tiếp (không HĐ)", "khong"),
                               ("Chịu thuế GTGT", "Chịu thuế"), ("Không chịu thuế GTGT", "Không chịu thuế")])
        self.only.currentIndexChanged.connect(self.refresh)
        lay.addLayout(toolbar(*btns, "stretch", self.only, self.search))
        if kind == "ra":
            cols = [("KyHieuHD", "Ký hiệu", "text"), ("SoHoaDon", "Số", "text"), ("NgayXuatHD", "Ngày lập", "date"),
                    ("SoHopDong", "Số HĐ", "text"), ("TenNguoiMua", "Người mua", "text"),
                    ("MatHang", "Mặt hàng", "text"), ("ThueSuat", "Thuế suất", "text"),
                    ("GiaTriTruocThue", "Trước thuế", "money"), ("VAT", "VAT", "money"),
                    ("GiaTriSauThue", "Sau thuế", "money"), ("DaThu", "Đã thu", "money"),
                    ("ConPhaiThu", "Còn phải thu", "money"), ("TTThanhToan", "Tình trạng thanh toán", "text"),
                    ("NgayTT", "Ngày thanh toán", "text"), ("SoThamChieuSK", "Số tham chiếu sao kê", "text"),
                    ("TinhChatHD", "Tính chất", "text")]
        else:
            cols = [("KyHieuHD", "Ký hiệu", "text"), ("SoHoaDonGoc", "Số", "text"), ("NgayNhanHD", "Ngày", "date"),
                    ("TenNhaCungCap", "Tên người bán", "text"), ("MaThueNCC", "MST người bán", "text"),
                    ("MatHang", "Mặt hàng", "text"), ("GiaTriTruocThue", "Trước thuế", "money"),
                    ("ThueSuat", "Thuế suất", "text"), ("VAT", "VAT", "money"), ("PhiKhac", "Phí BVMT", "money"),
                    ("GiaTriSauThue", "Sau thuế", "money"),
                    ("NhomThue", "Nhóm thuế", "text"), ("NguoiMuaHang", "Người mua hàng", "text"),
                    ("SoThamChieuSK", "Số sao kê", "text"), ("SoHopDong", "Số HĐ đầu vào", "text")]
        self.table = DataTable(cols)
        self.table.activated_row.connect(lambda _: self.edit())
        lay.addWidget(self.table, 1)
        self.total = QLabel()
        lay.addWidget(self.total)

    def refresh(self):
        contracts = {r["Ma"]: r["SoHopDong"] for r in self.ctx.db.query("SELECT Ma, SoHopDong FROM HopDong")}
        k = self.search.text().strip().lower()
        rows = []
        if self.kind == "ra":
            for inv in self.ctx.cs.out_invoices():
                r = out_invoice_row(self.ctx, inv)
                r["SoHopDong"] = contracts.get(inv["MaHopDong"], "")
                pays = r.pop("_pays")
                if self.only.value() == "con" and r["ConPhaiThu"] <= 0 or self.only.value() == "du" and r["ConPhaiThu"] > 0:
                    continue
                if self.only.value() == "chua_sk" and not any(
                        p["HinhThuc"] == "Chuyển khoản" and not (p["SoThamChieuSK"] or "").strip() for p in pays):
                    continue
                if k and k not in " ".join(str(r.get(x) or "") for x in ("SoHoaDon", "KyHieuHD", "TenNguoiMua",
                                                                          "MSTNguoiMua", "SoHopDong", "MatHang",
                                                                          "SoThamChieuSK")).lower():
                    continue
                rows.append(r)
        else:
            for inv in self.ctx.cs.in_invoices():
                r = dict(inv)
                r["SoHopDong"] = contracts.get(inv["MaHopDong"], "") if inv["MaHopDong"] else "(Mua trực tiếp)"
                only = self.only.value()
                if only == "co" and not inv["MaHopDong"] or only == "khong" and inv["MaHopDong"] \
                        or only in TAX_GROUPS and inv["NhomThue"] != only:
                    continue
                if k and k not in " ".join(str(r.get(x) or "") for x in (
                        "SoHoaDonGoc", "KyHieuHD", "TenNhaCungCap", "MaThueNCC", "SoHopDong", "MatHang",
                        "NguoiMuaHang", "SoThamChieuSK")).lower():
                    continue
                rows.append(r)
        self.table.set_rows(rows)
        self.total.setText(f"{len(rows)} hóa đơn · Tổng sau thuế {fmt_money(sum(r['GiaTriSauThue'] for r in rows))} đ")

    # ---- biểu mẫu
    def _fields(self, contract_hint: str = "", must_choose: bool = False, suggested=(), keep=None):
        """must_choose: chưa xác định được hợp đồng, buộc người nhập tự chọn (không điền sẵn)."""
        fields = self._base_fields(keep=[keep] + [h["Ma"] for h in suggested])
        f = next(f for f in fields if f["key"] == "MaHopDong")
        if must_choose or self.kind == "ra":
            ids = [h["Ma"] for h in suggested]
            top = [(f"★ Gợi ý: {h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"]) for h in suggested]
            rest = [o for o in f["options"] if o[1] not in ids]
            f["options"] = [(CHOOSE_LABEL, CHOOSE)] + top + rest
        if contract_hint:
            f["hint"] = contract_hint
        return fields

    def _base_fields(self, keep=()):
        self._contracts = open_contracts(self.ctx, self.kind, keep)
        contracts = [(contract_label(h), h["Ma"]) for h in self._contracts]
        if self.kind == "ra":
            return [type_filter_field(self.ctx, self._contracts),
                    {"key": "MaHopDong", "label": "Hợp đồng đầu ra", "kind": "combo", "options": contracts},
                    {"key": "KyHieuMauSo", "label": "Ký hiệu mẫu số"}, {"key": "KyHieuHD", "label": "Ký hiệu hóa đơn"},
                    {"key": "SoHoaDon", "label": "Số hóa đơn", "required": True},
                    {"key": "NgayXuatHD", "label": "Ngày xuất", "kind": "date"},
                    {"key": "TenNguoiMua", "label": "Tên người mua"}, {"key": "MSTNguoiMua", "label": "MST người mua"},
                    {"key": "MatHang", "label": "Mặt hàng"}, rate_field(),
                    {"key": "GiaTriTruocThue", "label": "Trước thuế", "kind": "money"},
                    {"key": "VAT", "label": "VAT", "kind": "money"},
                    {"key": "GiaTriSauThue", "label": "Sau thuế", "kind": "money",
                     "hint": VAT_HINT},
                    {"key": "TinhChatHD", "label": "Tính chất", "kind": "combo", "options": list(INVOICE_KINDS)},
                    {"key": "DuongDanTep", "label": "Tệp gốc (XML/PDF)"},
                    {"key": "GhiChuHDDR", "label": "Ghi chú"}]
        contracts = [("(Không có – mua hàng trực tiếp)", None)] + contracts
        return [{"key": "KyHieuMauSo", "label": "Ký hiệu mẫu số"}, {"key": "KyHieuHD", "label": "Ký hiệu hóa đơn"},
                {"key": "SoHoaDonGoc", "label": "Số hóa đơn", "required": True},
                {"key": "TenNhaCungCap", "label": "Tên người bán", "required": True},
                {"key": "MaThueNCC", "label": "MST người bán", "required": True},
                {"key": "NgayNhanHD", "label": "Ngày hóa đơn", "kind": "date"},
                {"key": "MatHang", "label": "Mặt hàng"},
                {"key": "GiaTriTruocThue", "label": "Trước thuế", "kind": "money"},
                rate_field(),
                {"key": "VAT", "label": "VAT", "kind": "money"},
                {"key": "PhiKhac", "label": "Phí BVMT / phí khác", "kind": "money",
                 "hint": "Chỉ hóa đơn có phí ngoài VAT (VD nước sạch: phí bảo vệ môi trường). Không có thì để 0."},
                {"key": "TyLePhiKhac", "label": "Tỷ lệ phí", "kind": "combo_edit", "options": ["10%"],
                 "hint": "Nhập tỷ lệ thì phí = tỷ lệ × trước thuế."},
                {"key": "GiaTriSauThue", "label": "Sau thuế", "kind": "money",
                 "hint": VAT_HINT},
                {"key": "NhomThue", "label": "Nhóm thuế GTGT", "kind": "combo", "options": TAX_GROUP_OPTIONS,
                 "hint": "Dùng để tách nhóm khi xuất bảng kê hóa đơn mua vào."},
                {"key": "NguoiMuaHang", "label": "Người mua hàng", "kind": "combo_edit", "options": self.buyers(),
                 "hint": "Nhân viên đã mua hàng."},
                {"key": "SoThamChieuSK", "label": "Số tham chiếu sao kê", "hint": "Số giao dịch trên sao kê ngân hàng."},
                {"key": "MaHopDong", "label": "Hợp đồng đầu vào", "kind": "combo", "options": contracts},
                {"key": "DuongDanTep", "label": "Tệp gốc (XML/PDF)"},
                {"key": "GhiChuHDDV", "label": "Ghi chú"}]

    def buyers(self) -> list[str]:
        """Gợi ý người mua hàng: họ tên các tài khoản và những tên đã nhập trước đây."""
        names = [u["HoTen"] for u in self.ctx.acc.users()]
        names += [r["NguoiMuaHang"] for r in self.ctx.db.query(
            "SELECT DISTINCT NguoiMuaHang FROM HoaDonDauVao WHERE DaXoa = 0 AND COALESCE(NguoiMuaHang, '') != ''")]
        return sorted(set(names), key=str.lower)

    def _save(self, values: dict, ma: str | None = None, contract_hint: str = "", must_choose: bool = False,
              suggested=(), parent=None) -> bool:
        fn = self.ctx.cs.save_out_invoice if self.kind == "ra" else self.ctx.cs.save_in_invoice
        values = dict(values)
        if must_choose or (self.kind == "ra" and not values.get("MaHopDong")):
            values["MaHopDong"] = CHOOSE
        dlg = FormDialog("Hóa đơn đầu ra" if self.kind == "ra" else "Hóa đơn đầu vào",
                         self._fields(contract_hint, must_choose, suggested, keep=values.get("MaHopDong")), values,
                         parent or self, validate=_check_chosen)
        if self.kind == "ra":
            bind_type_filter(dlg, self._contracts)
        while dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            v.pop("_LoaiHD", None)
            try:
                fn(dict(v, _Dong=values.get("_Dong")) if self.kind == "vao" else v, ma)
                return True
            except ValidationError as e:
                error(parent or self, str(e))
        return False

    def add_manual(self):
        if self.kind == "ra" and not self.ctx.cs.contracts("Đầu ra"):
            return info(self, "Chưa có hợp đồng đầu ra nào. Hãy thêm hợp đồng trước.")
        if self._save({"TinhChatHD": "Gốc"}):
            self.refresh()
            self.notify_pending()

    def add_from_files(self):
        if self.kind == "ra" and not self.ctx.cs.contracts("Đầu ra"):
            return info(self, "Chưa có hợp đồng đầu ra nào. Hãy thêm hợp đồng trước.")
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn hóa đơn điện tử (chọn được nhiều file)", "",
                                                "Hóa đơn (*.xml *.pdf)")
        if not paths:
            return
        if len(paths) > 1:
            dlg = BatchInvoiceDialog(self, paths)
            if dlg.exec() == QDialog.Accepted:
                self.refresh()
                self.notify_pending()
            return
        path = paths[0]
        d = read_invoice_file(self, path)
        if not d:
            return
        values, notes = invoice_values(self.kind, d, path, self.company_mst())
        if self.ctx.cs.invoice_exists(self.kind, values):
            notes.insert(0, "Hóa đơn này đã có trong phần mềm.")
        m = invoice_match.match_contract(self.ctx.db, self.kind, d)
        if m.ma:
            values["MaHopDong"] = m.ma
        if notes:
            info(self, f"{Path(path).name}\n\n" + "\n".join(notes), "Lưu ý")
        if self._save(values, contract_hint=m.note, must_choose=not m.ma, suggested=m.goi_y):
            self.refresh()
            self.notify_pending()

    def save_excel_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Lưu file mẫu", "Mau_nhap_hoa_don_dau_vao.xlsx", "Excel (*.xlsx)")
        if path and run_safely(self, in_invoice_excel.write_template, path):
            info(self, f"Đã lưu file mẫu:\n{path}\n\nCó thể nhập thẳng bảng kê hóa đơn mua vào đã có, "
                       "không bắt buộc dùng file mẫu.")

    def add_from_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file Excel hóa đơn đầu vào / bảng kê mua vào", "",
                                              "Excel (*.xlsx *.xlsm *.xls)")
        if not path:
            return
        password = None
        while True:
            try:
                rows = in_invoice_excel.read_workbook(path, password)
                break
            except FileNeedsPassword as e:
                password = ask_secret(self, "File Excel có mật khẩu", str(e))
                if password is None:
                    return
            except ValidationError as e:
                return error(self, str(e))
            except Exception as e:  # noqa: BLE001 - file hỏng / không phải Excel
                return error(self, f"Không đọc được file Excel: {e}")
        dlg = BatchInvoiceDialog(self, items=self.excel_items(rows), source=Path(path).name)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.notify_pending()

    def excel_items(self, rows) -> list[dict]:
        """Dòng đọc từ Excel → dòng của màn hình nhập nhiều hóa đơn (kèm gắn hợp đồng)."""
        items = []
        for r in rows:
            v, notes, goi_y = dict(r.values), [], []
            if r.so_hop_dong:
                h = in_invoice_excel.find_contract(self.ctx.db, r.so_hop_dong)
                v["MaHopDong"] = h["Ma"] if h else CHOOSE
                if not h:
                    notes.append(f"Chưa có hợp đồng đầu vào số {r.so_hop_dong} trong phần mềm, hãy chọn.")
            else:
                m = invoice_match.match_contract(self.ctx.db, "vao", {"MSTNguoiBan": v.get("MaThueNCC"),
                                                                    "VanBan": v.get("MatHang")})
                v["MaHopDong"] = m.ma or CHOOSE
                goi_y = m.goi_y
                notes.append(m.note)
            items.append({"path": None, "file": f"{r.sheet} – dòng {r.line}", "values": v, "status": "moi",
                          "errors": list(r.errors), "notes": r.errors + r.notes + notes, "goi_y": goi_y})
        mark_duplicates(self.ctx, "vao", items)
        return items

    def notify_pending(self) -> None:
        """Sau khi nhập hóa đơn đầu vào: báo ngay nếu có dòng hàng phần mềm chưa tự phân loại được."""
        if self.kind != "vao":
            return
        n = AssetService(self.ctx.db).pending_count()
        win = self.window()
        if hasattr(win, "update_badges"):
            win.update_badges()
        if not n:
            return
        text = (f"Có {n} dòng hàng hóa, dịch vụ phần mềm chưa tự phân loại được (chi phí trong kỳ, CCDC hay TSCĐ).")
        if self.ctx.can("TaiSan", "Sua") and hasattr(win, "go_to"):
            if confirm(self, text + " Mở màn Tài sản, CCDC để phân loại ngay?", "Cần kế toán phân loại"):
                win.go_to("Tài sản, CCDC")
        else:
            info(self, text + " Kế toán sẽ phân loại ở màn Tài sản, CCDC.", "Cần kế toán phân loại")

    def company_mst(self) -> str:
        row = self.ctx.acc.license()
        return row["MaCongTy"] if row else ""

    def edit(self):
        r = self.table.current()
        if not r or not self.ctx.can(self.feature, "Sua"):
            return
        table = "HoaDonDauRa" if self.kind == "ra" else "HoaDonDauVao"
        if self._save(dict(self.ctx.db.get(table, r["Ma"])), r["Ma"]):
            self.refresh()

    def go_contract(self):
        r = self.table.current()
        if not r:
            return info(self, "Chọn một hóa đơn trước.")
        if not r.get("MaHopDong"):
            return info(self, "Hóa đơn này không gắn với hợp đồng (mua hàng trực tiếp). Bấm Sửa để chọn hợp đồng.")
        self.open_contract.emit(r["MaHopDong"])

    def classify_lines(self):
        r = self.table.current()
        if not r:
            return info(self, "Chọn một hóa đơn trước.")
        from .assets_page import classify_lines
        svc = AssetService(self.ctx.db)
        if not svc.lines_of(r["Ma"]):
            svc.ensure_lines(r["Ma"])
        lines = svc._line_rows("d.MaHoaDon = ?", (r["Ma"],))
        if not lines:
            return info(self, "Hóa đơn chưa có dòng hàng để phân loại.")
        if classify_lines(self.ctx, self, lines):
            self.refresh()

    def delete(self):
        r = self.table.current()
        if not r:
            return
        so = r.get("SoHoaDon") or r.get("SoHoaDonGoc")
        if confirm(self, f"Xóa hóa đơn số {so}?"):
            (self.ctx.cs.delete_out_invoice if self.kind == "ra" else self.ctx.cs.delete_in_invoice)(r["Ma"])
            self.refresh()

    def add_payment(self):
        r = self.table.current()
        if not r:
            return info(self, "Chọn một hóa đơn trước.")
        if invoice_payment(self.ctx, self, r):           # đã có thanh toán: xem chi tiết, sửa / xóa / thu thêm
            self.refresh()

    def update_refs(self):
        rows = self.ctx.cs.payments_missing_ref()
        if not rows:
            return info(self, "Mọi lần thu chuyển khoản đều đã có số tham chiếu sao kê.")
        dlg = BankRefDialog(self.ctx, rows, self)
        dlg.exec()
        if dlg.saved:
            self.refresh()

    def show_payments(self):
        r = self.table.current()
        if not r:
            return info(self, "Chọn một hóa đơn trước.")
        if not self.ctx.cs.invoice_payments(r["Ma"]):
            return info(self, f"Hóa đơn số {r['SoHoaDon']} chưa ghi nhận lần thanh toán nào.")
        dlg = InvoicePaymentsDialog(self.ctx, r, self)
        dlg.exec()
        if dlg.changed:
            self.refresh()

    def open_file(self):
        r = self.table.current()
        if not r or not r.get("DuongDanTep"):
            return info(self, "Hóa đơn này chưa gắn tệp gốc.")
        if not Path(r["DuongDanTep"]).exists():
            return error(self, f"Không còn tìm thấy tệp:\n{r['DuongDanTep']}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(r["DuongDanTep"]))


def mark_duplicates(ctx, kind: str, items: list[dict]) -> None:
    """Hóa đơn đã có trong phần mềm hoặc lặp lại trong lượt nhập: đánh dấu bỏ qua."""
    seen = set()
    for it in items:
        if it["status"] != "moi":
            continue
        v = it["values"]
        key = (v.get("MaThueNCC", ""), (v.get("KyHieuHD") or "").upper(),
               (v.get("SoHoaDon") or v.get("SoHoaDonGoc") or "").lstrip("0"))
        if ctx.cs.invoice_exists(kind, v):
            it["status"] = "da_co"
            it["notes"].insert(0, "Đã có trong phần mềm, bỏ qua.")
        elif key in seen:
            it["status"] = "da_co"
            it["notes"].insert(0, "Trùng với hóa đơn khác trong lượt nhập này, bỏ qua.")
        seen.add(key)


REQUIRED_IN = (("SoHoaDonGoc", "Số hóa đơn"), ("TenNhaCungCap", "Tên người bán"), ("MaThueNCC", "MST người bán"),
               ("NgayNhanHD", "Ngày hóa đơn"))


class BatchInvoiceDialog(QDialog):
    """Nhập nhiều hóa đơn XML / PDF một lượt: mỗi file một dòng, chọn hợp đồng ngay trên dòng.
    Dòng chưa nhận ra hợp đồng phải được chọn rồi mới lưu; hóa đơn đã có thì bỏ qua."""

    def __init__(self, page: InvoicesPage, paths: list[str] | None = None, items: list[dict] | None = None,
                 source: str = ""):
        super().__init__(page)
        self.page = page
        self.kind = page.kind
        self.ctx = page.ctx
        self.source = source
        self.setWindowTitle("Nhập nhiều hóa đơn " + ("đầu ra" if self.kind == "ra" else "đầu vào")
                            + (f" từ {source}" if source else ""))
        self.resize(1250, 640)
        self.items: list[dict] = []
        if items is not None:
            self.items = items
        else:
            self._read(paths or [])
        lay = QVBoxLayout(self)
        self.summary = hint("")
        lay.addWidget(self.summary)
        self.type_filter = None
        if self.kind == "ra":
            f = type_filter_field(self.ctx, open_contracts(self.ctx, "ra"))
            row = QHBoxLayout()
            row.addWidget(QLabel("Loại hợp đồng"))
            self.type_filter = Combo(f["options"])
            self.type_filter.setMinimumWidth(220)
            self.type_filter.setToolTip(f["hint"])
            self.type_filter.currentIndexChanged.connect(self._type_changed)
            row.addWidget(self.type_filter)
            row.addWidget(hint("Chọn loại để thu gọn danh sách hợp đồng ở cột Hợp đồng; hợp đồng đã xuất đủ hóa đơn "
                               "không hiện."), 1)
            lay.addLayout(row)
        if self.kind == "vao":
            row = QHBoxLayout()
            row.addWidget(QLabel("Cho các dòng đã tích:  Hợp đồng"))
            self.bulk_contract = Combo([("(Không có – mua hàng trực tiếp)", None)] + [
                (f"{h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"])
                for h in self.ctx.cs.contracts("Đầu vào")])
            self.bulk_contract.setMinimumWidth(260)
            row.addWidget(self.bulk_contract)
            row.addWidget(button("Áp dụng", self._apply_contract))
            row.addSpacing(16)
            row.addWidget(QLabel("Người mua hàng"))
            self.buyer = EditCombo(page.buyers())
            self.buyer.setMinimumWidth(200)
            row.addWidget(self.buyer)
            row.addWidget(button("Áp dụng", self._apply_buyer))
            row.addStretch(1)
            lay.addLayout(row)
        self.cols = ["Nhập", "File" if items is None else "Dòng trong file", "Số hóa đơn", "Ngày", "Người mua" if self.kind == "ra" else "Tên người bán",
                     "MST", "Mặt hàng", "Sau thuế", "Hợp đồng"] + (["Nhóm thuế", "Người mua hàng"]
                                                                  if self.kind == "vao" else []) + ["Ghi chú"]
        self.table = QTableWidget(len(self.items), len(self.cols))
        self.table.setHorizontalHeaderLabels(self.cols)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _: self._edit())
        self._fill()
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        edit = button("Sửa chi tiết dòng đang chọn…", self._edit)
        bb.addButton(edit, QDialogButtonBox.ActionRole)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.table.itemChanged.connect(lambda _: self._update())
        self._update()

    # ---- đọc file
    def _read(self, paths):
        mst = self.page.company_mst()
        for path in paths:
            it = {"path": path, "file": Path(path).name, "values": {}, "notes": [], "status": "moi", "goi_y": []}
            try:
                d = parse_invoice_file(path)
            except (xml_invoice.InvoiceXMLError, pdf_invoice.InvoicePDFError) as e:
                it.update(status="loi", notes=[str(e)])
                self.items.append(it)
                continue
            values, notes = invoice_values(self.kind, d, path, mst)
            m = invoice_match.match_contract(self.ctx.db, self.kind, d)
            values["MaHopDong"] = m.ma if m.ma else CHOOSE
            if not m.ma:
                notes.append(m.note or "Chưa nhận ra hợp đồng, hãy chọn.")
            it.update(values=values, notes=notes, goi_y=m.goi_y)
            self.items.append(it)
        mark_duplicates(self.ctx, self.kind, self.items)

    def _type_changed(self, *_):
        for i, it in enumerate(self.items):
            combo = self.table.cellWidget(i, 8)
            if combo is not None:
                it["values"]["MaHopDong"] = combo.value()
        self._fill()
        self._update()

    def _contract_options(self, suggested, current=None):
        ids = [h["Ma"] for h in suggested]
        loai = self.type_filter.value() if self.type_filter is not None else None
        rows = open_contracts(self.ctx, self.kind, ids + [current])
        if loai is not None:
            rows = [h for h in rows if h["Ma"] in ids or h["Ma"] == current or (h["MaLoaiHD"] or "") == loai]
        base = [(contract_label(h), h["Ma"]) for h in rows]
        if self.kind != "ra":
            base = [("(Không có – mua hàng trực tiếp)", None)] + base
        top = [(f"★ Gợi ý: {h['SoHopDong']} – {h['TenHopDong']}", h["Ma"]) for h in suggested]
        return [(CHOOSE_LABEL, CHOOSE)] + top + [o for o in base if o[1] not in ids]

    def _fill(self):
        self.table.blockSignals(True)
        for i, it in enumerate(self.items):
            v = it["values"]
            chk = QTableWidgetItem()
            if it["status"] == "moi":
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Checked)
            else:
                chk.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(i, 0, chk)
            cells = [it["file"], v.get("SoHoaDon") or v.get("SoHoaDonGoc") or "",
                     fmt_date(v.get("NgayXuatHD") or v.get("NgayNhanHD")),
                     v.get("TenNguoiMua") if self.kind == "ra" else v.get("TenNhaCungCap"),
                     v.get("MSTNguoiMua") if self.kind == "ra" else v.get("MaThueNCC"),
                     v.get("MatHang") or "", fmt_money(v.get("GiaTriSauThue")) if v else ""]
            for j, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text or "")
                if j == 7:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, item)
            if it["status"] != "loi":
                combo = Combo(self._contract_options(it["goi_y"], v.get("MaHopDong")), v.get("MaHopDong"))
                combo.currentIndexChanged.connect(lambda _=0, i=i: self._contract_changed(i))
                self.table.setCellWidget(i, 8, combo)
                if self.kind == "vao":
                    self.table.setCellWidget(i, 9, Combo(TAX_GROUP_OPTIONS, v.get("NhomThue")))
                    self.table.setItem(i, 10, QTableWidgetItem(v.get("NguoiMuaHang") or ""))
            self.table.setItem(i, len(self.cols) - 1, QTableWidgetItem(" ".join(it["notes"])))
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        hdr = self.table.horizontalHeader()
        for j in range(len(self.cols)):
            self.table.setColumnWidth(j, min(self.table.columnWidth(j), 320 if j == 8 else 220))
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)

    def _checked(self, i) -> bool:
        return self.items[i]["status"] == "moi" and self.table.item(i, 0).checkState() == Qt.Checked

    def _contract_changed(self, i):
        self.items[i]["values"]["MaHopDong"] = self.table.cellWidget(i, 8).value()
        self._update()

    def _update(self):
        new = [i for i in range(len(self.items)) if self._checked(i)]
        missing = [i for i in new if self.items[i]["values"].get("MaHopDong") == CHOOSE]
        for i in range(len(self.items)):
            st = self.items[i]["status"]
            bad = st == "loi" or (st == "moi" and self.items[i].get("errors"))
            color = QColor(style.color("danger_soft" if bad else "total_bg" if st == "da_co"
                                       else "warn_soft" if i in missing else "card"))
            for j in range(len(self.cols)):
                item = self.table.item(i, j)
                if item is not None:
                    item.setBackground(color)
        n_old = sum(1 for it in self.items if it["status"] == "da_co")
        n_err = sum(1 for it in self.items if it["status"] == "loi")
        n_bad = sum(1 for i in new if self.items[i].get("errors"))
        msg = (f"Đã đọc {len(self.items)} " + ("dòng" if self.source else "file") + f": {len(new)} hóa đơn sẽ nhập")
        if n_old:
            msg += f", {n_old} hóa đơn đã có (bỏ qua)"
        if n_err:
            msg += f", {n_err} file không đọc được"
        msg += "."
        if missing:
            msg += f" Còn {len(missing)} hóa đơn (tô vàng) chưa chọn hợp đồng."
        if n_bad:
            msg += f" {n_bad} dòng (tô đỏ) thiếu thông tin bắt buộc: bấm Sửa chi tiết để bổ sung hoặc bỏ tích."
        self.summary.setText(msg)
        self.ok.setText(f"Nhập {len(new)} hóa đơn")
        self.ok.setEnabled(bool(new))

    def _apply_contract(self):
        ma = self.bulk_contract.value()
        for i in range(len(self.items)):
            if self._checked(i):
                self.table.cellWidget(i, 8).set_value(ma)
        self._update()

    def _apply_buyer(self):
        name = self.buyer.value()
        for i in range(len(self.items)):
            if self._checked(i):
                self.items[i]["values"]["NguoiMuaHang"] = name
                self.table.item(i, 10).setText(name)

    def _collect(self, i) -> dict:
        v = dict(self.items[i]["values"])
        v["MaHopDong"] = self.table.cellWidget(i, 8).value()
        if self.kind == "vao":
            v["NhomThue"] = self.table.cellWidget(i, 9).value()
            v["NguoiMuaHang"] = self.table.item(i, 10).text().strip()
        return v

    def _edit(self):
        i = self.table.currentRow()
        if i < 0 or self.items[i]["status"] == "loi":
            return
        it = self.items[i]
        values = self._collect(i)
        dlg = FormDialog("Hóa đơn " + ("đầu ra" if self.kind == "ra" else "đầu vào"),
                         self.page._fields(" ".join(it["notes"]), True, it["goi_y"], keep=values.get("MaHopDong")),
                         values, self, validate=_check_chosen)
        if self.kind == "ra":
            bind_type_filter(dlg, self.page._contracts)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            v.pop("_LoaiHD", None)
            it["values"].update(v)
            if self.kind == "vao" and it.get("errors"):
                it["errors"] = [f"Thiếu {label}" for key, label in REQUIRED_IN if not it["values"].get(key)]
                it["notes"] = list(it["errors"])
            self._fill()
            self.table.selectRow(i)
            self._update()

    def _save(self):
        rows = [i for i in range(len(self.items)) if self._checked(i)]
        bad = [self.items[i]["file"] for i in rows if self.items[i].get("errors")]
        if bad:
            return error(self, "Các dòng sau thiếu thông tin bắt buộc:\n" + "\n".join(bad[:15]) +
                         "\n\nBấm Sửa chi tiết để bổ sung (dòng tô đỏ), hoặc bỏ tích dòng đó.")
        missing = [self.items[i]["file"] for i in rows if self._collect(i).get("MaHopDong") == CHOOSE]
        if missing:
            return error(self, "Chưa chọn hợp đồng cho các hóa đơn:\n" + "\n".join(missing[:15]) +
                         "\n\nChọn đúng hợp đồng ở cột Hợp đồng (dòng tô vàng), hoặc bỏ tích dòng đó.")
        fn = self.ctx.cs.save_out_invoice if self.kind == "ra" else self.ctx.cs.save_in_invoice
        saved, errors = 0, []
        for i in rows:
            try:
                fn(self._collect(i))
                saved += 1
                self.items[i]["status"] = "da_co"
            except ValidationError as e:
                errors.append(f"{self.items[i]['file']}: {e}")
        self.ctx.db.log(self.ctx.user["TenDangNhap"] if self.ctx.user else None,
                        "Nhập nhiều hóa đơn " + ("đầu ra" if self.kind == "ra" else "đầu vào"),
                        f"{saved} hóa đơn" + (f" từ {self.source}" if self.source else ""))
        if errors:
            error(self, f"Đã nhập {saved} hóa đơn. Không lưu được:\n" + "\n".join(errors[:20]))
        else:
            info(self, f"Đã nhập {saved} hóa đơn.")
        self.accept()


class InvoicePaymentsDialog(QDialog):
    """Các lần thanh toán đã ghi cho một hóa đơn đầu ra: sửa hoặc xóa lần ghi nhầm."""

    def __init__(self, ctx, inv: dict, parent=None):
        super().__init__(parent)
        self.ctx, self.inv, self.changed = ctx, inv, False
        self.setWindowTitle(f"Thanh toán của hóa đơn số {inv['SoHoaDon']}")
        self.resize(760, 360)
        lay = QVBoxLayout(self)
        self.head = hint("")
        lay.addWidget(self.head)
        self.table = DataTable([("NgayThanhToan", "Ngày tiền về", "date"), ("SoTien", "Số tiền", "money"),
                                ("HinhThuc", "Hình thức", "text"), ("SoThamChieuSK", "Số sao kê", "text"),
                                ("GhiChu", "Ghi chú", "text")])
        self.table.activated_row.connect(lambda _: self.edit())
        can = ctx.can("HoaDonDauRa", "Sua")
        b_add = button("Ghi nhận thêm lần thu…", self.add, "Primary")
        b_edit, b_del = button("Sửa…", self.edit), button("Xóa thông tin thanh toán", self.delete, "Danger")
        for b in (b_add, b_edit, b_del):
            b.setEnabled(can)
        lay.addLayout(toolbar(b_add, b_edit, b_del, "stretch"))
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Đóng")
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.reload()

    def reload(self):
        rows = self.ctx.cs.invoice_payments(self.inv["Ma"])
        self.table.set_rows(rows)
        paid = sum(r["SoTien"] for r in rows)
        self.head.setText(f"Hóa đơn {fmt_money(self.inv['GiaTriSauThue'])} đ · đã thu {fmt_money(paid)} đ · "
                          f"còn phải thu {fmt_money(self.inv['GiaTriSauThue'] - paid)} đ. "
                          "Ghi nhầm thì chọn dòng rồi bấm Sửa (đổi số tiền, ngày, hóa đơn liên quan…) hoặc Xóa.")

    def add(self):
        paid = sum(r["SoTien"] for r in self.ctx.cs.invoice_payments(self.inv["Ma"]))
        v = payment_dialog(self.ctx, self, self.inv["MaHopDong"],
                           {"MaHDDauRa": self.inv["Ma"], "SoTien": max(self.inv["GiaTriSauThue"] - paid, 0),
                            "HinhThuc": "Chuyển khoản"})
        if v and run_safely(self, self.ctx.cs.save_payment, v):
            self.changed = True
            self.reload()

    def edit(self):
        p = self.table.current()
        if not p:
            return info(self, "Chọn một lần thanh toán.")
        if self.ctx.can("HoaDonDauRa", "Sua") and edit_payment(self.ctx, self, p["Ma"]):
            self.changed = True
            self.reload()

    def delete(self):
        p = self.table.current()
        if not p:
            return info(self, "Chọn một lần thanh toán.")
        if confirm(self, f"Xóa thông tin thanh toán {fmt_money(p['SoTien'])} đ ngày {fmt_date(p['NgayThanhToan'])}"
                         + (f", số tham chiếu {p['SoThamChieuSK']}" if p["SoThamChieuSK"] else "")
                         + " của hóa đơn này?\n\nSau khi xóa, hóa đơn được tính lại số còn phải thu."):
            self.ctx.cs.delete_payment(p["Ma"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xóa lần thanh toán", fmt_money(p["SoTien"]))
            self.changed = True
            self.reload()


class BankRefDialog(QDialog):
    """Đối chiếu sao kê: các lần thu chuyển khoản chưa có số tham chiếu, gõ số tham chiếu ngay trên bảng rồi lưu."""

    COLS = [("NgayThanhToan", "Ngày tiền về"), ("SoTien", "Số tiền"), ("SoHopDong", "Số HĐ"), ("TenDoiTac", "Đối tác"),
            ("HoaDon", "Hóa đơn"), ("GhiChu", "Ghi chú"), ("SoThamChieuSK", "Số tham chiếu sao kê")]

    def __init__(self, ctx, rows: list[dict], parent=None):
        super().__init__(parent)
        self.ctx, self.rows, self.saved = ctx, rows, 0
        self.setWindowTitle("Cập nhật số tham chiếu sao kê")
        self.resize(980, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"{len(rows)} lần thu chuyển khoản chưa có số tham chiếu. Mở sao kê ngân hàng, tìm giao dịch"
                           " theo ngày và số tiền rồi gõ số tham chiếu vào cột cuối; dòng để trống thì giữ nguyên."))
        self.table = QTableWidget(len(rows), len(self.COLS))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        for i, r in enumerate(rows):
            r["HoaDon"] = f"{r['KyHieuHD'] or ''} {r['SoHoaDon']}".strip() if r["SoHoaDon"] else "(tạm ứng)"
            for j, (key, _) in enumerate(self.COLS):
                val = r.get(key)
                text = fmt_date(val) if key == "NgayThanhToan" else fmt_money(val) if key == "SoTien" else (val or "")
                item = QTableWidgetItem(str(text))
                if key != "SoThamChieuSK":
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if key == "SoTien":
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(len(self.COLS) - 1, 220)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu số tham chiếu")
        bb.button(QDialogButtonBox.Cancel).setText("Đóng")
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def save(self):
        col = len(self.COLS) - 1
        filled = [(i, self.table.item(i, col).text().strip()) for i in range(len(self.rows))]
        filled = [(i, ref) for i, ref in filled if ref]
        if not filled:
            return self.reject()
        if not confirm_changes(self, [(f"{fmt_date(self.rows[i]['NgayThanhToan'])} – "
                                       f"{fmt_money(self.rows[i]['SoTien'])} đ", "", ref) for i, ref in filled],
                               "số tham chiếu sao kê"):
            return
        errors = []
        for i, r in enumerate(self.rows):
            ref = self.table.item(i, col).text().strip()
            if not ref:
                continue
            try:
                self.ctx.cs.set_payment_ref(r["Ma"], ref)
                self.saved += 1
            except ValidationError as e:
                errors.append(f"Dòng {i + 1}: {e}")
        if self.saved:
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Cập nhật số tham chiếu sao kê", f"{self.saved} lần thu")
        if errors:
            error(self, "Chưa lưu được:\n" + "\n".join(errors[:15]))
            return
        self.accept()
