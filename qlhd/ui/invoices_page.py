"""Hóa đơn đầu ra và đầu vào: nhập tay, đọc từ XML hoặc PDF, ghi nhận thanh toán, mở tệp gốc."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QLineEdit, QVBoxLayout, QWidget

from ..core import invoice_match, pdf_invoice, xml_invoice
from ..core.contracts import INVOICE_KINDS, ValidationError
from ..core.reports import fmt_money
from .contracts_page import payment_dialog
from .widgets import Combo, DataTable, FormDialog, button, confirm, error, info, page_title, run_safely, toolbar


CHOOSE = "__chon_hop_dong__"
CHOOSE_LABEL = "— Chọn hợp đồng —"


def _check_chosen(values: dict) -> None:
    if values.get("MaHopDong") == CHOOSE:
        raise ValidationError("Chưa chọn hợp đồng cho hóa đơn. Hãy chọn đúng hợp đồng"
                              " (hóa đơn đầu vào không theo hợp đồng thì chọn \"Không có – mua hàng trực tiếp\").")


def read_invoice_file(parent, path: str) -> dict | None:
    try:
        if path.lower().endswith(".xml"):
            return xml_invoice.parse_invoice(path)
        return pdf_invoice.parse_invoice(path)
    except (xml_invoice.InvoiceXMLError, pdf_invoice.InvoicePDFError) as e:
        error(parent, f"{Path(path).name}: {e}")
        return None


class InvoicesPage(QWidget):
    """kind = 'ra' (hóa đơn đầu ra) hoặc 'vao' (hóa đơn đầu vào)."""

    def __init__(self, ctx, kind: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.kind = kind
        self.feature = "HoaDonDauRa" if kind == "ra" else "HoaDonDauVao"
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Hóa đơn đầu ra" if kind == "ra" else "Hóa đơn đầu vào"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm số hóa đơn, đối tác, MST…")
        self.search.textChanged.connect(self.refresh)
        add_ok = ctx.can(self.feature, "Them")
        b_add = button("Nhập tay", self.add_manual, "Primary")
        b_file = button("Đọc từ file XML / PDF…", self.add_from_files)
        b_edit = button("Sửa", self.edit)
        b_del = button("Xóa", self.delete, "Danger")
        b_open = button("Mở tệp gốc", self.open_file)
        btns = [b_add, b_file, b_edit, b_del, b_open]
        b_add.setEnabled(add_ok)
        b_file.setEnabled(add_ok)
        b_edit.setEnabled(ctx.can(self.feature, "Sua"))
        b_del.setEnabled(ctx.can(self.feature, "Xoa"))
        if kind == "ra":
            b_pay = button("Ghi nhận thanh toán", self.add_payment)
            b_pay.setEnabled(ctx.can("HoaDonDauRa", "Sua"))
            btns.insert(4, b_pay)
            self.only = Combo([("Tất cả", None), ("Còn phải thu", "con"), ("Đã thu đủ", "du")])
        else:
            self.only = Combo([("Tất cả", None), ("Có hợp đồng", "co"), ("Mua trực tiếp (không HĐ)", "khong")])
        self.only.currentIndexChanged.connect(self.refresh)
        lay.addLayout(toolbar(*btns, "stretch", self.only, self.search))
        if kind == "ra":
            cols = [("KyHieuHD", "Ký hiệu", "text"), ("SoHoaDon", "Số", "text"), ("NgayXuatHD", "Ngày lập", "date"),
                    ("SoHopDong", "Số HĐ", "text"), ("TenNguoiMua", "Người mua", "text"),
                    ("GiaTriTruocThue", "Trước thuế", "money"), ("VAT", "VAT", "money"),
                    ("GiaTriSauThue", "Sau thuế", "money"), ("DaThu", "Đã thu", "money"),
                    ("ConPhaiThu", "Còn phải thu", "money"), ("TinhChatHD", "Tính chất", "text")]
        else:
            cols = [("KyHieuHD", "Ký hiệu", "text"), ("SoHoaDonGoc", "Số", "text"), ("NgayNhanHD", "Ngày", "date"),
                    ("TenNhaCungCap", "Nhà cung cấp", "text"), ("MaThueNCC", "MST", "text"),
                    ("SoHopDong", "Số HĐ đầu vào", "text"), ("GiaTriTruocThue", "Trước thuế", "money"),
                    ("VAT", "VAT", "money"), ("GiaTriSauThue", "Sau thuế", "money")]
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
                r = dict(inv)
                r["SoHopDong"] = contracts.get(inv["MaHopDong"], "")
                r["DaThu"] = self.ctx.cs.invoice_paid(inv["Ma"])
                r["ConPhaiThu"] = inv["GiaTriSauThue"] - r["DaThu"] if inv["TinhChatHD"] != "Đã hủy" else 0
                if self.only.value() == "con" and r["ConPhaiThu"] <= 0 or self.only.value() == "du" and r["ConPhaiThu"] > 0:
                    continue
                if k and k not in " ".join(str(r.get(x) or "") for x in ("SoHoaDon", "KyHieuHD", "TenNguoiMua",
                                                                          "MSTNguoiMua", "SoHopDong")).lower():
                    continue
                rows.append(r)
        else:
            for inv in self.ctx.cs.in_invoices():
                r = dict(inv)
                r["SoHopDong"] = contracts.get(inv["MaHopDong"], "") if inv["MaHopDong"] else "(Mua trực tiếp)"
                if self.only.value() == "co" and not inv["MaHopDong"] or self.only.value() == "khong" and inv["MaHopDong"]:
                    continue
                if k and k not in " ".join(str(r.get(x) or "") for x in ("SoHoaDonGoc", "KyHieuHD", "TenNhaCungCap",
                                                                          "MaThueNCC", "SoHopDong")).lower():
                    continue
                rows.append(r)
        self.table.set_rows(rows)
        self.total.setText(f"{len(rows)} hóa đơn · Tổng sau thuế {fmt_money(sum(r['GiaTriSauThue'] for r in rows))} đ")

    # ---- biểu mẫu
    def _fields(self, contract_hint: str = "", must_choose: bool = False, suggested=()):
        """must_choose: chưa xác định được hợp đồng, buộc người nhập tự chọn (không điền sẵn)."""
        fields = self._base_fields()
        f = next(f for f in fields if f["key"] == "MaHopDong")
        if must_choose or self.kind == "ra":
            ids = [h["Ma"] for h in suggested]
            top = [(f"★ Gợi ý: {h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"]) for h in suggested]
            rest = [o for o in f["options"] if o[1] not in ids]
            f["options"] = [(CHOOSE_LABEL, CHOOSE)] + top + rest
        if contract_hint:
            f["hint"] = contract_hint
        return fields

    def _base_fields(self):
        if self.kind == "ra":
            contracts = [(f"{h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"])
                         for h in self.ctx.cs.contracts("Đầu ra")]
            return [{"key": "MaHopDong", "label": "Hợp đồng đầu ra", "kind": "combo", "options": contracts},
                    {"key": "KyHieuMauSo", "label": "Ký hiệu mẫu số"}, {"key": "KyHieuHD", "label": "Ký hiệu hóa đơn"},
                    {"key": "SoHoaDon", "label": "Số hóa đơn", "required": True},
                    {"key": "NgayXuatHD", "label": "Ngày xuất", "kind": "date"},
                    {"key": "TenNguoiMua", "label": "Tên người mua"}, {"key": "MSTNguoiMua", "label": "MST người mua"},
                    {"key": "GiaTriTruocThue", "label": "Trước thuế", "kind": "money"},
                    {"key": "VAT", "label": "VAT", "kind": "money"},
                    {"key": "GiaTriSauThue", "label": "Sau thuế", "kind": "money",
                     "hint": "Để 0 thì phần mềm tự tính = trước thuế + VAT."},
                    {"key": "TinhChatHD", "label": "Tính chất", "kind": "combo", "options": list(INVOICE_KINDS)},
                    {"key": "DuongDanTep", "label": "Tệp gốc (XML/PDF)"},
                    {"key": "GhiChuHDDR", "label": "Ghi chú"}]
        contracts = [("(Không có – mua hàng trực tiếp)", None)] + [
            (f"{h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"]) for h in self.ctx.cs.contracts("Đầu vào")]
        return [{"key": "KyHieuMauSo", "label": "Ký hiệu mẫu số"}, {"key": "KyHieuHD", "label": "Ký hiệu hóa đơn"},
                {"key": "SoHoaDonGoc", "label": "Số hóa đơn", "required": True},
                {"key": "TenNhaCungCap", "label": "Nhà cung cấp", "required": True},
                {"key": "MaThueNCC", "label": "MST nhà cung cấp", "required": True},
                {"key": "NgayNhanHD", "label": "Ngày hóa đơn", "kind": "date"},
                {"key": "GiaTriTruocThue", "label": "Trước thuế", "kind": "money"},
                {"key": "VAT", "label": "VAT", "kind": "money"},
                {"key": "GiaTriSauThue", "label": "Sau thuế", "kind": "money",
                 "hint": "Để 0 thì phần mềm tự tính = trước thuế + VAT."},
                {"key": "MaHopDong", "label": "Hợp đồng đầu vào", "kind": "combo", "options": contracts},
                {"key": "DuongDanTep", "label": "Tệp gốc (XML/PDF)"},
                {"key": "GhiChuHDDV", "label": "Ghi chú"}]

    def _save(self, values: dict, ma: str | None = None, contract_hint: str = "", must_choose: bool = False,
              suggested=()) -> bool:
        fn = self.ctx.cs.save_out_invoice if self.kind == "ra" else self.ctx.cs.save_in_invoice
        values = dict(values)
        if must_choose or (self.kind == "ra" and not values.get("MaHopDong")):
            values["MaHopDong"] = CHOOSE
        dlg = FormDialog("Hóa đơn đầu ra" if self.kind == "ra" else "Hóa đơn đầu vào",
                         self._fields(contract_hint, must_choose, suggested), values, self, validate=_check_chosen)
        while dlg.exec() == QDialog.Accepted:
            try:
                fn(dlg.values(), ma)
                return True
            except ValidationError as e:
                error(self, str(e))
        return False

    def add_manual(self):
        if self.kind == "ra" and not self.ctx.cs.contracts("Đầu ra"):
            return info(self, "Chưa có hợp đồng đầu ra nào. Hãy thêm hợp đồng trước.")
        if self._save({"TinhChatHD": "Gốc"}):
            self.refresh()

    def add_from_files(self):
        if self.kind == "ra" and not self.ctx.cs.contracts("Đầu ra"):
            return info(self, "Chưa có hợp đồng đầu ra nào. Hãy thêm hợp đồng trước.")
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn hóa đơn điện tử", "", "Hóa đơn (*.xml *.pdf)")
        mst_cty = (self.ctx.acc.license() or {})["MaCongTy"] if self.ctx.acc.license() else ""
        saved = 0
        for path in paths:
            d = read_invoice_file(self, path)
            if not d:
                continue
            notes = []
            if d.get("ThieuTruong"):
                notes.append("Chưa đọc được: " + ", ".join(d["ThieuTruong"]) + ". Hãy kiểm tra và nhập bổ sung.")
            if self.kind == "ra":
                if mst_cty and d["MSTNguoiBan"] and d["MSTNguoiBan"] != mst_cty:
                    notes.append(f"MST người bán {d['MSTNguoiBan']} khác MST công ty {mst_cty}.")
                values = {"KyHieuMauSo": d["KyHieuMauSo"], "KyHieuHD": d["KyHieuHD"], "SoHoaDon": d["SoHoaDon"],
                          "NgayXuatHD": d["NgayLap"] or None, "TenNguoiMua": d["TenNguoiMua"],
                          "MSTNguoiMua": d["MSTNguoiMua"], "GiaTriTruocThue": d["GiaTriTruocThue"], "VAT": d["VAT"],
                          "GiaTriSauThue": d["GiaTriSauThue"], "TinhChatHD": "Gốc", "DuongDanTep": path}
            else:
                if mst_cty and d["MSTNguoiMua"] and d["MSTNguoiMua"] != mst_cty:
                    notes.append(f"MST người mua {d['MSTNguoiMua']} khác MST công ty {mst_cty}: hóa đơn có thể không phải của công ty.")
                values = {"KyHieuMauSo": d["KyHieuMauSo"], "KyHieuHD": d["KyHieuHD"], "SoHoaDonGoc": d["SoHoaDon"],
                          "TenNhaCungCap": d["TenNguoiBan"], "MaThueNCC": d["MSTNguoiBan"],
                          "NgayNhanHD": d["NgayLap"] or None, "GiaTriTruocThue": d["GiaTriTruocThue"], "VAT": d["VAT"],
                          "GiaTriSauThue": d["GiaTriSauThue"], "DuongDanTep": path}
            m = invoice_match.match_contract(self.ctx.db, self.kind, d)
            if m.ma:
                values["MaHopDong"] = m.ma
            if notes:
                info(self, f"{Path(path).name}\n\n" + "\n".join(notes), "Lưu ý")
            if self._save(values, contract_hint=m.note, must_choose=not m.ma, suggested=m.goi_y):
                saved += 1
        if saved:
            self.refresh()

    def edit(self):
        r = self.table.current()
        if not r or not self.ctx.can(self.feature, "Sua"):
            return
        table = "HoaDonDauRa" if self.kind == "ra" else "HoaDonDauVao"
        if self._save(dict(self.ctx.db.get(table, r["Ma"])), r["Ma"]):
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
        v = payment_dialog(self.ctx, self, r["MaHopDong"], {"MaHDDauRa": r["Ma"], "SoTien": max(r["ConPhaiThu"], 0),
                                                            "HinhThuc": "Chuyển khoản"})
        if v and run_safely(self, self.ctx.cs.save_payment, v):
            self.refresh()

    def open_file(self):
        r = self.table.current()
        if not r or not r.get("DuongDanTep"):
            return info(self, "Hóa đơn này chưa gắn tệp gốc.")
        if not Path(r["DuongDanTep"]).exists():
            return error(self, f"Không còn tìm thấy tệp:\n{r['DuongDanTep']}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(r["DuongDanTep"]))
