"""Hóa đơn đầu ra và đầu vào: nhập tay, đọc từ XML hoặc PDF, ghi nhận thanh toán, mở tệp gốc."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import invoice_match, pdf_invoice, xml_invoice
from ..core.contracts import INVOICE_KINDS, TAX_GROUPS, ValidationError, guess_tax_group, rate_from_amounts
from ..core.reports import fmt_date, fmt_money
from .contracts_page import payment_dialog
from .widgets import (Combo, DataTable, EditCombo, FormDialog, button, confirm, error, hint, info, page_title,
                      run_safely, toolbar)


CHOOSE = "__chon_hop_dong__"
CHOOSE_LABEL = "— Chọn hợp đồng —"
TAX_GROUP_OPTIONS = [("Chịu thuế GTGT (nhóm 1 bảng kê)", "Chịu thuế"),
                     ("Không chịu thuế GTGT (nhóm 5 bảng kê)", "Không chịu thuế")]


def _check_chosen(values: dict) -> None:
    if values.get("MaHopDong") == CHOOSE:
        raise ValidationError("Chưa chọn hợp đồng cho hóa đơn. Hãy chọn đúng hợp đồng"
                              " (hóa đơn đầu vào không theo hợp đồng thì chọn \"Không có – mua hàng trực tiếp\").")


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
    return dict(common, SoHoaDonGoc=d["SoHoaDon"], TenNhaCungCap=d["TenNguoiBan"], MaThueNCC=d["MSTNguoiBan"],
                NgayNhanHD=d["NgayLap"] or None, NhomThue=guess_tax_group(d["VAT"])), notes


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
        self.search.setPlaceholderText("Tìm số hóa đơn, đối tác, MST, mặt hàng…")
        self.search.textChanged.connect(self.refresh)
        add_ok = ctx.can(self.feature, "Them")
        b_add = button("Nhập tay", self.add_manual, "Primary")
        b_file = button("Nhập từ file XML / PDF (chọn nhiều file)…", self.add_from_files)
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
                    ("ConPhaiThu", "Còn phải thu", "money"), ("TinhChatHD", "Tính chất", "text")]
        else:
            cols = [("KyHieuHD", "Ký hiệu", "text"), ("SoHoaDonGoc", "Số", "text"), ("NgayNhanHD", "Ngày", "date"),
                    ("TenNhaCungCap", "Tên người bán", "text"), ("MaThueNCC", "MST người bán", "text"),
                    ("MatHang", "Mặt hàng", "text"), ("GiaTriTruocThue", "Trước thuế", "money"),
                    ("ThueSuat", "Thuế suất", "text"), ("VAT", "VAT", "money"), ("GiaTriSauThue", "Sau thuế", "money"),
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
                r = dict(inv)
                r["SoHopDong"] = contracts.get(inv["MaHopDong"], "")
                r["DaThu"] = self.ctx.cs.invoice_paid(inv["Ma"])
                r["ConPhaiThu"] = inv["GiaTriSauThue"] - r["DaThu"] if inv["TinhChatHD"] != "Đã hủy" else 0
                if self.only.value() == "con" and r["ConPhaiThu"] <= 0 or self.only.value() == "du" and r["ConPhaiThu"] > 0:
                    continue
                if k and k not in " ".join(str(r.get(x) or "") for x in ("SoHoaDon", "KyHieuHD", "TenNguoiMua",
                                                                          "MSTNguoiMua", "SoHopDong", "MatHang")).lower():
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
                    {"key": "MatHang", "label": "Mặt hàng"}, {"key": "ThueSuat", "label": "Thuế suất",
                                                               "hint": "VD 10%, 8%, 0%, KCT."},
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
                {"key": "TenNhaCungCap", "label": "Tên người bán", "required": True},
                {"key": "MaThueNCC", "label": "MST người bán", "required": True},
                {"key": "NgayNhanHD", "label": "Ngày hóa đơn", "kind": "date"},
                {"key": "MatHang", "label": "Mặt hàng"},
                {"key": "GiaTriTruocThue", "label": "Trước thuế", "kind": "money"},
                {"key": "ThueSuat", "label": "Thuế suất", "hint": "VD 10%, 8%, 0%, KCT."},
                {"key": "VAT", "label": "VAT", "kind": "money"},
                {"key": "GiaTriSauThue", "label": "Sau thuế", "kind": "money",
                 "hint": "Để 0 thì phần mềm tự tính = trước thuế + VAT."},
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
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn hóa đơn điện tử (chọn được nhiều file)", "",
                                                "Hóa đơn (*.xml *.pdf)")
        if not paths:
            return
        if len(paths) > 1:
            dlg = BatchInvoiceDialog(self, paths)
            if dlg.exec() == QDialog.Accepted:
                self.refresh()
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


class BatchInvoiceDialog(QDialog):
    """Nhập nhiều hóa đơn XML / PDF một lượt: mỗi file một dòng, chọn hợp đồng ngay trên dòng.
    Dòng chưa nhận ra hợp đồng phải được chọn rồi mới lưu; hóa đơn đã có thì bỏ qua."""

    def __init__(self, page: InvoicesPage, paths: list[str]):
        super().__init__(page)
        self.page = page
        self.kind = page.kind
        self.ctx = page.ctx
        self.setWindowTitle("Nhập nhiều hóa đơn " + ("đầu ra" if self.kind == "ra" else "đầu vào"))
        self.resize(1250, 600)
        self.items: list[dict] = []
        self._read(paths)
        lay = QVBoxLayout(self)
        self.summary = hint("")
        lay.addWidget(self.summary)
        if self.kind == "vao":
            row = QHBoxLayout()
            row.addWidget(QLabel("Người mua hàng cho các hóa đơn đã tích:"))
            self.buyer = EditCombo(page.buyers())
            self.buyer.setMinimumWidth(220)
            row.addWidget(self.buyer)
            row.addWidget(button("Áp dụng", self._apply_buyer))
            row.addStretch(1)
            lay.addLayout(row)
        self.cols = ["Nhập", "File", "Số hóa đơn", "Ngày", "Người mua" if self.kind == "ra" else "Tên người bán",
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
        seen = set()
        for path in paths:
            it = {"path": path, "file": Path(path).name, "values": {}, "notes": [], "status": "moi", "goi_y": []}
            try:
                d = parse_invoice_file(path)
            except (xml_invoice.InvoiceXMLError, pdf_invoice.InvoicePDFError) as e:
                it.update(status="loi", notes=[str(e)])
                self.items.append(it)
                continue
            values, notes = invoice_values(self.kind, d, path, mst)
            key = (values.get("MaThueNCC", ""), (values.get("KyHieuHD") or "").upper(),
                   (values.get("SoHoaDon") or values.get("SoHoaDonGoc") or "").lstrip("0"))
            if self.ctx.cs.invoice_exists(self.kind, values):
                it["status"] = "da_co"
                notes.insert(0, "Đã có trong phần mềm, bỏ qua.")
            elif key in seen:
                it["status"] = "da_co"
                notes.insert(0, "Trùng với file khác trong lượt nhập này, bỏ qua.")
            seen.add(key)
            m = invoice_match.match_contract(self.ctx.db, self.kind, d)
            values["MaHopDong"] = m.ma if m.ma else CHOOSE
            if not m.ma:
                notes.append(m.note or "Chưa nhận ra hợp đồng, hãy chọn.")
            it.update(values=values, notes=notes, goi_y=m.goi_y)
            self.items.append(it)

    def _contract_options(self, suggested):
        if self.kind == "ra":
            base = [(f"{h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"])
                    for h in self.ctx.cs.contracts("Đầu ra")]
        else:
            base = [("(Không có – mua hàng trực tiếp)", None)] + [
                (f"{h['SoHopDong']} – {h['TenHopDong']} ({h['TenDoiTac']})", h["Ma"])
                for h in self.ctx.cs.contracts("Đầu vào")]
        ids = [h["Ma"] for h in suggested]
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
                combo = Combo(self._contract_options(it["goi_y"]), v.get("MaHopDong"))
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
            color = (QColor("#fde3d6") if st == "loi" else QColor("#eceff3") if st == "da_co"
                     else QColor("#fff1c7") if i in missing else QColor("white"))
            for j in range(len(self.cols)):
                item = self.table.item(i, j)
                if item is not None:
                    item.setBackground(color)
        n_old = sum(1 for it in self.items if it["status"] == "da_co")
        n_err = sum(1 for it in self.items if it["status"] == "loi")
        msg = f"Đã đọc {len(self.items)} file: {len(new)} hóa đơn sẽ nhập"
        if n_old:
            msg += f", {n_old} hóa đơn đã có (bỏ qua)"
        if n_err:
            msg += f", {n_err} file không đọc được"
        msg += "."
        if missing:
            msg += f" Còn {len(missing)} hóa đơn (tô vàng) chưa chọn hợp đồng."
        self.summary.setText(msg)
        self.ok.setText(f"Nhập {len(new)} hóa đơn")
        self.ok.setEnabled(bool(new))

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
                         self.page._fields(" ".join(it["notes"]), True, it["goi_y"]), values, self,
                         validate=_check_chosen)
        if dlg.exec() == QDialog.Accepted:
            it["values"].update(dlg.values())
            self._fill()
            self.table.selectRow(i)
            self._update()

    def _save(self):
        rows = [i for i in range(len(self.items)) if self._checked(i)]
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
                        "Nhập nhiều hóa đơn " + ("đầu ra" if self.kind == "ra" else "đầu vào"), f"{saved} hóa đơn")
        if errors:
            error(self, f"Đã nhập {saved} hóa đơn. Không lưu được:\n" + "\n".join(errors[:20]))
        else:
            info(self, f"Đã nhập {saved} hóa đơn.")
        self.accept()
