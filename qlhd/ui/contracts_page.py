"""Màn hình Trang chủ, Hợp đồng (kèm công việc, hóa đơn, thanh toán) và nhập từ Excel."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QLineEdit,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..core import excel_import
from ..core.contracts import (CONTRACT_STATUSES, DURATION_UNITS, PAY_FILTERS, PAY_METHODS, PAY_STATE_LABELS,
                              TASK_STATUSES, ValidationError, contract_matches, contract_pay_state, duration_text,
                              end_date)
from ..core.reports import fmt_date, fmt_money
from .widgets import (VAT_HINT, Combo, DataTable, FormDialog, button, confirm, confirm_changes, error, hint, info,
                      page_title,
                      rate_field, require, run_safely, toolbar)


CONTRACT_COLS = [("STT", "STT", "int"), ("SoHopDong", "Số HĐ", "text"), ("TenHopDong", "Tên hợp đồng", "text"),
                 ("TenDoiTac", "Đối tác", "text"), ("ChieuHopDong", "Chiều", "text"), ("LoaiHD", "Loại", "text"),
                 ("NgayKy", "Ngày ký", "date"), ("ThoiGianTH", "Thời gian TH", "text"),
                 ("NgayKetThucDuKien", "Ngày kết thúc", "date"), ("TinhTrangHopDong", "Tình trạng", "text"),
                 ("GiaTriHD", "Giá trị HĐ", "money"), ("PhanTramTT", "% thanh toán", "pct"), ("DaXuatHD", "Đã xuất HĐơn", "money"),
                 ("DaThanhToan", "Đã thanh toán", "money"), ("ConPhaiThu", "Còn phải thu", "money"),
                 ("TTThanhToan", "Thanh toán", "text")]


def contract_dialog(ctx, parent, values: dict | None = None) -> dict | None:
    values = dict(values or {"ChieuHopDong": "Đầu ra", "TinhTrangHopDong": "Chưa bắt đầu"})
    new = not values.get("Ma")
    types = [("(Chưa phân loại)", None)] + [(t["TenLoaiHD"], t["Ma"]) for t in ctx.cs.contract_types()]
    partners = [(f"{p['TenDoiTac']}" + (f" – {p['MaSoThue']}" if p["MaSoThue"] else ""), p["Ma"])
                for p in ctx.cs.partners()]
    fields = [
        {"key": "ChieuHopDong", "label": "Chiều hợp đồng", "kind": "combo", "options": ["Đầu ra", "Đầu vào"]},
        {"key": "SoHopDong", "label": "Số hợp đồng", "required": True},
        {"key": "TenHopDong", "label": "Tên hợp đồng", "required": True},
        {"key": "MaLoaiHD", "label": "Loại hợp đồng", "kind": "combo", "options": types},
        {"key": "MaDoiTac", "label": "Đối tác", "kind": "combo", "options": partners,
         "hint": "Chủ đầu tư (đầu ra) hoặc Nhà cung cấp (đầu vào). Chưa có thì bấm \"Thêm đối tác\"."},
        {"key": "GiaTriTruocThue", "label": "Giá trị trước thuế", "kind": "money"},
        rate_field(),
        {"key": "VAT", "label": "Tiền thuế VAT", "kind": "money"},
        {"key": "GiaTriTongSauThue", "label": "Giá trị sau thuế", "kind": "money", "hint": VAT_HINT},
        {"key": "NgayKy", "label": "Ngày ký", "kind": "date"},
        {"key": "NgayBatDauDuKien", "label": "Ngày bắt đầu thực hiện", "kind": "date", "optional": True,
         "hint": "Để trống thì tính từ ngày ký."},
        {"key": "ThoiGianThucHien", "label": "Thời gian thực hiện", "kind": "int", "max": 100_000, "required": new,
         "hint": "Số ngày hoặc số tháng (chọn đơn vị bên dưới), bắt buộc với hợp đồng mới. Ngày kết thúc được "
                 "tự tính."},
        {"key": "DonViThoiGian", "label": "Đơn vị", "kind": "combo", "options": list(DURATION_UNITS)},
        {"key": "NgayKetThucDuKien", "label": "Ngày kết thúc dự kiến", "kind": "date", "optional": True},
        {"key": "NhacTruocNgay", "label": "Nhắc trước khi hết hạn (ngày)", "kind": "int", "max": 3650,
         "hint": f"Để 0 thì dùng cài đặt chung (hiện là {ctx.cs.remind_days()} ngày)."},
        {"key": "TinhTrangHopDong", "label": "Tình trạng", "kind": "combo", "options": list(CONTRACT_STATUSES)},
        {"key": "GhiChu", "label": "Ghi chú", "kind": "multiline"},
    ]

    def add_partner(dlg):
        role = "Chủ đầu tư" if dlg.values()["ChieuHopDong"] == "Đầu ra" else "Nhà cung cấp"
        ma = partner_dialog(ctx, dlg, {"VaiTroDoiTac": role})
        if ma:
            combo = dlg.widget("MaDoiTac")
            p = ctx.db.get("DoiTac", ma)
            combo.addItem(p["TenDoiTac"], ma)
            combo.set_value(ma)

    values.setdefault("DonViThoiGian", "ngày")
    dlg = FormDialog("Hợp đồng", fields, values, parent, extra_buttons=[("Thêm đối tác", add_partner)],
                     validate=lambda v: (require(v.get("MaDoiTac"), "Chọn đối tác."),
                                         require(not new or v.get("ThoiGianThucHien"),
                                                 "Nhập thời gian thực hiện hợp đồng (số ngày hoặc số tháng).")))

    def recalc(*_):
        v = dlg.values()
        end = end_date(v["NgayBatDauDuKien"] or v["NgayKy"], v["ThoiGianThucHien"], v["DonViThoiGian"])
        dlg.widget("NgayKetThucDuKien").setEnabled(not end)
        if end:
            dlg.set_value("NgayKetThucDuKien", end)

    for key in ("NgayKy", "NgayBatDauDuKien"):
        dlg.widget(key).dateChanged.connect(recalc)
    dlg.widget("ThoiGianThucHien").valueChanged.connect(recalc)
    dlg.widget("DonViThoiGian").currentIndexChanged.connect(recalc)
    recalc()
    return dlg.values() if dlg.exec() == QDialog.Accepted else None


def partner_dialog(ctx, parent, values: dict | None = None, ma: str | None = None) -> str | None:
    fields = [{"key": "TenDoiTac", "label": "Tên đối tác", "required": True},
              {"key": "MaSoThue", "label": "Mã số thuế"},
              {"key": "VaiTroDoiTac", "label": "Vai trò", "kind": "combo",
               "options": ["Chủ đầu tư", "Nhà cung cấp", "Cả hai"]},
              {"key": "DiaChi", "label": "Địa chỉ"}, {"key": "NguoiLienHe", "label": "Người liên hệ"},
              {"key": "DienThoai", "label": "Điện thoại"}]
    dlg = FormDialog("Đối tác", fields, values, parent)
    while dlg.exec() == QDialog.Accepted:
        try:
            return ctx.cs.save_partner(dlg.values(), ma)
        except ValidationError as e:
            error(parent, str(e))
    return None


def payment_dialog(ctx, parent, ma_hd: str, values: dict | None = None,
                   title: str = "Ghi nhận thanh toán") -> dict | None:
    invoices = [("(Tạm ứng / chưa có hóa đơn)", None)] + [
        (f"{i['KyHieuHD'] or ''} số {i['SoHoaDon']} – {fmt_money(i['GiaTriSauThue'])}", i["Ma"])
        for i in ctx.cs.out_invoices(ma_hd)]
    fields = [{"key": "NgayThanhToan", "label": "Ngày tiền về", "kind": "date"},
              {"key": "SoTien", "label": "Số tiền", "kind": "money", "required": True},
              {"key": "HinhThuc", "label": "Hình thức", "kind": "combo", "options": list(PAY_METHODS)},
              {"key": "MaHDDauRa", "label": "Hóa đơn liên quan", "kind": "combo", "options": invoices},
              {"key": "SoThamChieuSK", "label": "Số tham chiếu sao kê"},
              {"key": "GhiChu", "label": "Ghi chú"}]
    dlg = FormDialog(title, fields, values, parent,
                     validate=lambda v: require(v["SoTien"] > 0, "Số tiền phải lớn hơn 0."))
    if dlg.exec() != QDialog.Accepted:
        return None
    v = dlg.values()
    v["MaHopDong"] = ma_hd
    return v


def edit_payment(ctx, parent, ma_tt: str) -> bool:
    """Sửa một lần thanh toán đã ghi (ghi nhầm số tiền, ngày, hóa đơn liên quan…)."""
    p = dict(ctx.db.get("ThanhToanHopDong", ma_tt))
    v = payment_dialog(ctx, parent, p["MaHopDong"], p, "Sửa lần thanh toán")
    if v and run_safely(parent, ctx.cs.save_payment, v, ma_tt):
        ctx.db.log(ctx.user["TenDangNhap"], "Sửa lần thanh toán",
                   f"{fmt_money(p['SoTien'])} → {fmt_money(v['SoTien'])} đ")
        return True
    return False


class ContractsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Hợp đồng"))
        self.filter = Combo([("Tất cả", None), ("Đầu ra", "Đầu ra"), ("Đầu vào", "Đầu vào")])
        self.filter.currentIndexChanged.connect(self.refresh)
        self.status = Combo([("Mọi tình trạng", None)] + [(x, x) for x in CONTRACT_STATUSES])
        self.status.currentIndexChanged.connect(self.refresh)
        self.paid = Combo([("Mọi tình trạng thanh toán", None)] + list(PAY_FILTERS))
        self.paid.setToolTip("Lọc theo tiền thu về của hợp đồng đầu ra.")
        self.paid.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm số HĐ, tên HĐ, đối tác…")
        self.search.setMinimumWidth(260)
        self.search.textChanged.connect(self.refresh)
        can_add = ctx.can("HopDong", "Them")
        self.b_add = button("Thêm hợp đồng", self.add, "Primary")
        self.b_edit = button("Sửa", self.edit)
        self.b_del = button("Xóa", self.delete, "Danger")
        self.b_imp = button("Nhập từ Excel…", self.import_excel)
        self.b_tpl = button("Tải file Excel mẫu", self.save_template)
        for b, ok in ((self.b_add, can_add), (self.b_edit, ctx.can("HopDong", "Sua")),
                      (self.b_del, ctx.can("HopDong", "Xoa")), (self.b_imp, can_add)):
            b.setEnabled(ok)
        self.b_partner = button("Sửa đối tác…", self.edit_partner)
        self.b_partner.setToolTip("Sửa tên, MST, vai trò của đối tác (chủ đầu tư / nhà cung cấp) của hợp đồng đang chọn")
        self.b_partner.setEnabled(ctx.can("DanhMuc", "Sua") or ctx.can("HopDong", "Sua"))
        lay.addLayout(toolbar(self.b_add, self.b_edit, self.b_del, self.b_partner, self.b_imp, self.b_tpl, "stretch"))
        lay.addLayout(toolbar(QLabel("Lọc:"), self.filter, self.status, self.paid, "stretch", self.search))
        split = QSplitter(Qt.Vertical)
        self.table = DataTable(CONTRACT_COLS)
        self.table.activated_row.connect(lambda _: self.edit())
        self.table.itemSelectionChanged.connect(self.refresh_detail)
        split.addWidget(self.table)
        self.detail = QTabWidget()
        self.tasks = DataTable([("TenNoiDungCV", "Công việc", "text"), ("NgayDuKienHT", "Hạn", "date"),
                                ("TienDoPT", "Tiến độ %", "int"), ("TinhTrangCV", "Tình trạng", "text"),
                                ("GhiChuCV", "Ghi chú", "text")])
        self.tasks.activated_row.connect(lambda _: self.edit_task())
        self.invoices = DataTable([("KyHieuHD", "Ký hiệu", "text"), ("SoHoaDon", "Số", "text"),
                                   ("NgayXuatHD", "Ngày", "date"), ("GiaTriSauThue", "Sau thuế", "money"),
                                   ("TinhChatHD", "Tính chất", "text"), ("ConPhaiThu", "Còn phải thu", "money"),
                                   ("TTThanhToan", "Tình trạng thanh toán", "text"),
                                   ("NgayTT", "Ngày thanh toán", "text"), ("SoThamChieuSK", "Số sao kê", "text")])
        self.invoices.activated_row.connect(lambda _: self.edit_invoice())
        self._inv_helper = None
        self.payments = DataTable([("NgayThanhToan", "Ngày tiền về", "date"), ("SoTien", "Số tiền", "money"),
                                   ("HinhThuc", "Hình thức", "text"), ("SoThamChieuSK", "Số sao kê", "text"),
                                   ("GhiChu", "Ghi chú", "text")])
        self.detail.addTab(self._wrap(self.tasks, [button("Thêm công việc", self.add_task),
                                                   button("Sửa công việc", self.edit_task),
                                                   button("Xóa công việc", self.delete_task, "Danger"),
                                                   button("Thêm lần bàn giao", self.add_handover),
                                                   button("Thêm lần nghiệm thu", self.add_acceptance)]), "Công việc")
        inv_btns = [button("Thêm hóa đơn đầu ra", self.add_invoice), button("Sửa hóa đơn", self.edit_invoice),
                    button("Xóa hóa đơn", self.delete_invoice, "Danger"),
                    button("Ghi nhận / xem thanh toán", self.invoice_payment)]
        for b, right in zip(inv_btns, ("Them", "Sua", "Xoa", "Sua")):
            b.setEnabled(ctx.can("HoaDonDauRa", right))
        self.detail.addTab(self._wrap(self.invoices, inv_btns), "Hóa đơn")
        pay_btns = [button("Ghi nhận thanh toán", self.add_payment), button("Sửa lần thanh toán", self.edit_payment),
                    button("Xóa lần thanh toán", self.del_payment)]
        self.payments.activated_row.connect(lambda _: self.edit_payment())
        for b in pay_btns:
            b.setEnabled(ctx.can("HoaDonDauRa", "Sua"))
        self.detail.addTab(self._wrap(self.payments, pay_btns), "Thanh toán")
        split.addWidget(self.detail)
        split.setSizes([420, 220])
        lay.addWidget(split, 1)
        self.summary = hint("")
        lay.addWidget(self.summary)

    def _wrap(self, table, buttons) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(toolbar(*buttons, "stretch"))
        lay.addWidget(table)
        return w

    def refresh(self):
        types = {t["Ma"]: t["TenLoaiHD"] for t in self.ctx.cs.contract_types()}
        rows = []
        for hd in self.ctx.cs.contracts(self.filter.value(), self.search.text().strip()):
            r = dict(hd)
            r.update(self.ctx.cs.summary(hd["Ma"]))
            if not contract_matches(hd, r, self.status.value(), self.paid.value()):
                continue
            r["TTThanhToan"] = PAY_STATE_LABELS[contract_pay_state(r)] if hd["ChieuHopDong"] == "Đầu ra" else ""
            r["LoaiHD"] = types.get(hd["MaLoaiHD"], "")
            r["ThoiGianTH"] = duration_text(hd["ThoiGianThucHien"], hd["DonViThoiGian"])
            r["STT"] = len(rows) + 1
            rows.append(r)
        self.table.set_rows(rows)
        tot = sum(r["GiaTriHD"] for r in rows)
        self.summary.setText(f"{len(rows)} hợp đồng · Tổng giá trị {fmt_money(tot)} đ · Còn phải thu "
                             f"{fmt_money(sum(max(r['ConPhaiThu'], 0) for r in rows if r['ChieuHopDong'] == 'Đầu ra'))} đ")
        self.refresh_detail()

    def select_contract(self, ma: str) -> None:
        """Mở trang ở hợp đồng được chọn (từ Tổng quan)."""
        for w in (self.filter, self.status, self.paid, self.search):
            w.blockSignals(True)
        for w in (self.filter, self.status, self.paid):
            w.setCurrentIndex(0)
        self.search.clear()
        for w in (self.filter, self.status, self.paid, self.search):
            w.blockSignals(False)
        self.refresh()
        for i in range(self.table.rowCount()):
            r = self.table._row_of(self.table.item(i, 0))
            if r and r["Ma"] == ma:
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 0))
                break

    def _reload_keep(self) -> None:
        """Làm mới mà vẫn đứng ở hợp đồng đang chọn (để nhập tiếp hóa đơn, thanh toán)."""
        ma, tab = self.current_id(), self.detail.currentIndex()
        self.refresh()
        for i in range(self.table.rowCount()):
            r = self.table._row_of(self.table.item(i, 0))
            if r and r["Ma"] == ma:
                self.table.selectRow(i)
                break
        self.detail.setCurrentIndex(tab)

    def current_id(self) -> str | None:
        r = self.table.current()
        return r["Ma"] if r else None

    def refresh_detail(self):
        ma = self.current_id()
        if not ma:
            for t in (self.tasks, self.invoices, self.payments):
                t.set_rows([])
            return
        hd = self.ctx.db.get("HopDong", ma)
        self.tasks.set_rows(self.ctx.cs.tasks(ma))
        if hd["ChieuHopDong"] == "Đầu ra":
            from .invoices_page import out_invoice_row
            self.invoices.set_rows([out_invoice_row(self.ctx, i) for i in self.ctx.cs.out_invoices(ma)])
        else:
            rows = [dict(r, SoHoaDon=r["SoHoaDonGoc"], NgayXuatHD=r["NgayNhanHD"], TinhChatHD="Đầu vào")
                    for r in self.ctx.db.active("HoaDonDauVao", "MaHopDong = ?", (ma,))]
            self.invoices.set_rows(rows)
        self.payments.set_rows(self.ctx.cs.payments(ma))

    def add(self):
        v = contract_dialog(self.ctx, self)
        if v and run_safely(self, self.ctx.cs.save_contract, v):
            self.refresh()

    def edit(self):
        r = self.table.current()
        if not r or not self.ctx.can("HopDong", "Sua"):
            return
        v = contract_dialog(self.ctx, self, dict(self.ctx.db.get("HopDong", r["Ma"])))
        if v and run_safely(self, self.ctx.cs.save_contract, v, r["Ma"]):
            self.refresh()

    def delete(self):
        r = self.table.current()
        if r and confirm(self, f"Xóa hợp đồng {r['SoHopDong']} cùng công việc, hóa đơn đầu ra, thanh toán của nó?"):
            self.ctx.cs.delete_contract(r["Ma"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xóa hợp đồng", r["SoHopDong"])
            self.refresh()

    # ---- công việc
    def _task_fields(self):
        return [{"key": "TenNoiDungCV", "label": "Nội dung công việc", "required": True},
                {"key": "NgayDuKienHT", "label": "Ngày dự kiến hoàn thành", "kind": "date", "optional": True},
                {"key": "TienDoPT", "label": "Tiến độ (%)", "kind": "int", "max": 100},
                {"key": "TinhTrangCV", "label": "Tình trạng", "kind": "combo", "options": list(TASK_STATUSES)},
                {"key": "GhiChuCV", "label": "Ghi chú"}]

    def add_task(self):
        ma = self.current_id()
        if not ma:
            return info(self, "Chọn một hợp đồng trước.")
        dlg = FormDialog("Công việc", self._task_fields(), {"TinhTrangCV": "Chưa làm"}, self)
        if dlg.exec() == QDialog.Accepted:
            run_safely(self, self.ctx.cs.save_task, dict(dlg.values(), MaHopDong=ma))
            self._reload_keep()

    def edit_task(self):
        t = self.tasks.current()
        if not t:
            return
        dlg = FormDialog("Công việc", self._task_fields(), t, self)
        if dlg.exec() == QDialog.Accepted:
            run_safely(self, self.ctx.cs.save_task, dlg.values(), t["Ma"])
            self._reload_keep()

    def delete_task(self):
        t = self.tasks.current()
        if not t:
            return info(self, "Chọn một công việc.")
        if confirm(self, f"Xóa công việc \"{t['TenNoiDungCV']}\" cùng các lần bàn giao, nghiệm thu của nó?"):
            self.ctx.cs.delete_task(t["Ma"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xóa công việc", t["TenNoiDungCV"])
            self._reload_keep()

    def add_handover(self):
        t = self.tasks.current()
        if not t:
            return info(self, "Chọn một công việc trước.")
        dlg = FormDialog("Lần bàn giao", [
            {"key": "NgayBanGiao", "label": "Ngày bàn giao", "kind": "date"},
            {"key": "NoiDungTL", "label": "Nội dung tài liệu", "required": True},
            {"key": "PhanHoiCDT", "label": "Phản hồi chủ đầu tư"},
            {"key": "DuongDanTep", "label": "Đường dẫn biên bản"}], None, self)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.cs.add_handover(dict(dlg.values(), MaCongViec=t["Ma"]))
            self.ctx.cs.save_task({"TinhTrangCV": "Đã bàn giao"}, t["Ma"])
            self.refresh_detail()

    def add_acceptance(self):
        t = self.tasks.current()
        if not t:
            return info(self, "Chọn một công việc trước.")
        dlg = FormDialog("Lần nghiệm thu", [
            {"key": "NgayNghiemThu", "label": "Ngày nghiệm thu", "kind": "date"},
            {"key": "KetQuaNT", "label": "Kết quả", "kind": "combo", "options": ["Đạt", "Chưa đạt"]},
            {"key": "GhiChuNT", "label": "Ghi chú"},
            {"key": "DuongDanTep", "label": "Đường dẫn biên bản"}], None, self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            self.ctx.cs.add_acceptance(dict(v, MaCongViec=t["Ma"]))
            if v["KetQuaNT"] == "Đạt":
                self.ctx.cs.save_task({"TinhTrangCV": "Đã nghiệm thu", "TienDoPT": 100}, t["Ma"])
            self.refresh_detail()

    def edit_partner(self):
        r = self.table.current()
        if not r:
            return info(self, "Chọn một hợp đồng trước.")
        if not r.get("MaDoiTac"):
            return info(self, "Hợp đồng chưa gắn đối tác trong danh mục: bấm Sửa hợp đồng để chọn đối tác.")
        p = dict(self.ctx.db.get("DoiTac", r["MaDoiTac"]))
        if partner_dialog(self.ctx, self, p, p["Ma"]):
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Sửa đối tác", p["TenDoiTac"])
            self._reload_keep()

    # ---- hóa đơn đầu ra của hợp đồng đang chọn
    def _out_contract(self) -> str | None:
        ma = self.current_id()
        if not ma:
            info(self, "Chọn một hợp đồng trước.")
            return None
        if self.ctx.db.get("HopDong", ma)["ChieuHopDong"] != "Đầu ra":
            info(self, "Hóa đơn đầu vào nhập ở trang Hóa đơn đầu vào.")
            return None
        return ma

    def _helper(self):
        if self._inv_helper is None:
            from .invoices_page import InvoicesPage
            self._inv_helper = InvoicesPage(self.ctx, "ra")
            self._inv_helper.hide()
        return self._inv_helper

    def add_invoice(self):
        ma = self._out_contract()
        if ma and self._helper()._save({"MaHopDong": ma}, parent=self):
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Thêm hóa đơn đầu ra", self.table.current()["SoHopDong"])
            self._reload_keep()

    def edit_invoice(self):
        r = self.invoices.current()
        if not r or self._out_contract() is None:
            return info(self, "Chọn một hóa đơn.") if not r else None
        if self._helper()._save(dict(self.ctx.db.get("HoaDonDauRa", r["Ma"])), r["Ma"], parent=self):
            self._reload_keep()

    def delete_invoice(self):
        r = self.invoices.current()
        if not r or self._out_contract() is None:
            return info(self, "Chọn một hóa đơn.") if not r else None
        pays = self.ctx.cs.invoice_payments(r["Ma"])
        if confirm(self, f"Xóa hóa đơn số {r['SoHoaDon']} ({fmt_money(r['GiaTriSauThue'])} đ)?"
                         + (f"\n\nHóa đơn đã có {len(pays)} lần thanh toán: các lần này vẫn giữ trong thẻ Thanh toán "
                            "của hợp đồng (không còn gắn với hóa đơn)." if pays else "")):
            self.ctx.cs.delete_out_invoice(r["Ma"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xóa hóa đơn đầu ra", r["SoHoaDon"])
            self._reload_keep()

    def invoice_payment(self):
        r = self.invoices.current()
        if not r or self._out_contract() is None:
            return info(self, "Chọn một hóa đơn.") if not r else None
        from .invoices_page import invoice_payment
        if invoice_payment(self.ctx, self, r):
            self._reload_keep()

    # ---- thanh toán
    def add_payment(self):
        ma = self.current_id()
        if not ma:
            return info(self, "Chọn một hợp đồng trước.")
        if self.ctx.db.get("HopDong", ma)["ChieuHopDong"] != "Đầu ra":
            return info(self, "Thanh toán được ghi cho hợp đồng đầu ra (tiền thu về).")
        v = payment_dialog(self.ctx, self, ma, {"HinhThuc": "Chuyển khoản"})
        if v and run_safely(self, self.ctx.cs.save_payment, v):
            self._reload_keep()

    def edit_payment(self):
        p = self.payments.current()
        if not p:
            return info(self, "Chọn một lần thanh toán.")
        if not self.ctx.can("HoaDonDauRa", "Sua"):
            return
        if edit_payment(self.ctx, self, p["Ma"]):
            self._reload_keep()

    def del_payment(self):
        p = self.payments.current()
        if p and confirm(self, f"Xóa lần thanh toán {fmt_money(p['SoTien'])} đ ngày {fmt_date(p['NgayThanhToan'])}?"):
            self.ctx.cs.delete_payment(p["Ma"])
            self._reload_keep()

    # ---- Excel
    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Lưu file Excel mẫu", "Mau_nhap_du_lieu_hop_dong.xlsx",
                                              "Excel (*.xlsx)")
        if path:
            excel_import.write_template(path)
            info(self, f"Đã lưu file mẫu:\n{path}")

    def import_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file Excel", "", "Excel (*.xlsx)")
        if not path:
            return
        rows = run_safely(self, excel_import.read_workbook, path, self.ctx.db)
        if not rows:
            return
        dups = excel_import.duplicate_invoice_rows(rows)
        if dups:
            lines = [f"• Dòng {r.line}: số {r.values['SoHoaDon']} – {r.status.lower()}: {r.errors[0].split(': ', 1)[-1]}"
                     for r in dups[:15]]
            more = f"\n… và {len(dups) - 15} dòng khác." if len(dups) > 15 else ""
            error(self, f"Có {len(dups)} hóa đơn đầu ra bị trùng số hóa đơn:\n\n" + "\n".join(lines) + more +
                  "\n\nCác dòng trùng sẽ không được lưu. Dòng nghi trùng (thiếu ký hiệu) chỉ được lưu"
                  " nếu bạn tích \"Vẫn lưu\" ở màn hình xem trước.", "Trùng số hóa đơn")
        dlg = ImportPreviewDialog(rows, self)
        if dlg.exec() == QDialog.Accepted:
            res = excel_import.import_rows(self.ctx.db, rows)
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Nhập hợp đồng từ Excel", Path(path).name)
            msg = (f"Đã lưu {res['Hợp đồng']} hợp đồng, {res['Hóa đơn đầu ra']} hóa đơn đầu ra, "
                   f"{res['Thanh toán']} lần thanh toán.")
            if res["loi"]:
                msg += "\n\nKhông lưu được:\n" + "\n".join(res["loi"][:20])
            info(self, msg)
            self.refresh()


class ImportPreviewDialog(QDialog):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Xem trước dữ liệu nhập từ Excel")
        self.resize(1000, 560)
        lay = QVBoxLayout(self)
        self.rows = rows
        self.summary = hint("")
        lay.addWidget(self.summary)
        table = DataTable([("sheet", "Sheet", "text"), ("line", "Dòng", "int"), ("status", "Kết quả", "text"),
                           ("SoHopDong", "Số HĐ", "text"), ("noi_dung", "Nội dung", "text"),
                           ("GiaTriTruocThue", "Trước thuế", "money"), ("ThueSuat", "Thuế suất", "text"),
                           ("VAT", "VAT", "money"), ("SauThue", "Sau thuế", "money"),
                           ("errors", "Ghi chú", "text")])
        data = []
        for r in rows:
            v = r.values
            noi_dung = v.get("TenHopDong") or (f"Hóa đơn {v.get('SoHoaDon')}" if r.sheet == "Hóa đơn đầu ra"
                                                else f"{fmt_money(v.get('SoTien'))} đ ngày {fmt_date(v.get('NgayThanhToan'))}")
            data.append({"sheet": r.sheet, "line": r.line, "status": r.status, "SoHopDong": v.get("SoHopDong"),
                         "noi_dung": noi_dung, "errors": "; ".join(r.errors),
                         "GiaTriTruocThue": v.get("GiaTriTruocThue"), "ThueSuat": v.get("ThueSuat"),
                         "VAT": v.get("VAT"), "SauThue": v.get("GiaTriTongSauThue", v.get("GiaTriSauThue"))})
        table.set_rows(data)
        lay.addWidget(table)
        self.unsure = [r for r in rows if r.status == "Nghi trùng"]
        if self.unsure:
            self.force = QCheckBox(f"Vẫn lưu {len(self.unsure)} hóa đơn nghi trùng (đã kiểm tra, không phải trùng)")
            self.force.toggled.connect(self._update)
            lay.addWidget(self.force)
        self.bb = bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        self._update()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _update(self, force: bool = False):
        for r in self.unsure:
            r.force = force
        ok = sum(1 for r in self.rows if r.ok)
        dup = sum(1 for r in self.rows if r.status in ("Trùng", "Nghi trùng") and not r.ok)
        self.summary.setText(f"{ok} dòng hợp lệ sẽ được lưu; {len(self.rows) - ok} dòng bỏ qua"
                             + (f" (trong đó {dup} dòng trùng)." if dup else "."))
        self.bb.button(QDialogButtonBox.Ok).setText(f"Lưu {ok} dòng hợp lệ")
        self.bb.button(QDialogButtonBox.Ok).setEnabled(ok > 0)


class CatalogPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Danh mục"))
        tabs = QTabWidget()
        lay.addWidget(tabs)
        editable = ctx.can("DanhMuc", "Them")
        # loại hợp đồng
        self.types = DataTable([("TenLoaiHD", "Loại hợp đồng", "text")])
        w = QWidget()
        wl = QVBoxLayout(w)
        bs = [button("Thêm", self.add_type), button("Đổi tên", self.rename_type), button("Xóa", self.del_type)]
        for b in bs:
            b.setEnabled(editable)
        wl.addLayout(toolbar(*bs, "stretch"))
        wl.addWidget(self.types)
        tabs.addTab(w, "Loại hợp đồng")
        # đối tác
        self.partners = DataTable([("TenDoiTac", "Tên đối tác", "text"), ("MaSoThue", "MST", "text"),
                                   ("VaiTroDoiTac", "Vai trò", "text"), ("DiaChi", "Địa chỉ", "text"),
                                   ("NguoiLienHe", "Người liên hệ", "text"), ("DienThoai", "Điện thoại", "text")])
        self.partners.activated_row.connect(lambda _: self.edit_partner())
        w = QWidget()
        wl = QVBoxLayout(w)
        bs = [button("Thêm", self.add_partner), button("Sửa", self.edit_partner), button("Xóa", self.del_partner)]
        for b in bs:
            b.setEnabled(editable)
        wl.addLayout(toolbar(*bs, "stretch"))
        wl.addWidget(self.partners)
        tabs.addTab(w, "Đối tác")
        # nhóm chủ đầu tư
        self.groups = DataTable([("TenNhom", "Nhóm chủ đầu tư", "text"), ("SoThanhVien", "Số chủ đầu tư", "int")])
        self.groups.activated_row.connect(lambda _: self.edit_group())
        w = QWidget()
        wl = QVBoxLayout(w)
        bs = [button("Thêm nhóm", self.add_group), button("Sửa nhóm", self.edit_group), button("Xóa nhóm", self.del_group)]
        for b in bs:
            b.setEnabled(editable)
        wl.addLayout(toolbar(*bs, "stretch"))
        wl.addWidget(self.groups)
        tabs.addTab(w, "Nhóm chủ đầu tư")

    def refresh(self):
        self.types.set_rows(self.ctx.cs.contract_types())
        self.partners.set_rows(self.ctx.cs.partners())
        self.groups.set_rows([dict(g, SoThanhVien=len(self.ctx.cs.group_members(g["Ma"]))) for g in self.ctx.cs.groups()])

    def add_type(self):
        from .widgets import ask_text
        name = ask_text(self, "Loại hợp đồng", "Tên loại hợp đồng:")
        if name and run_safely(self, self.ctx.cs.save_contract_type, name):
            self.refresh()

    def rename_type(self):
        from .widgets import ask_text
        r = self.types.current()
        if r:
            name = ask_text(self, "Đổi tên", "Tên mới:", r["TenLoaiHD"])
            if name and (name == r["TenLoaiHD"] or not confirm_changes(self, [("Loại hợp đồng", r["TenLoaiHD"], name)])):
                return
            if name and run_safely(self, self.ctx.cs.save_contract_type, name, r["Ma"]):
                self.refresh()

    def del_type(self):
        r = self.types.current()
        if r and confirm(self, f"Xóa loại hợp đồng \"{r['TenLoaiHD']}\"?"):
            run_safely(self, self.ctx.cs.delete_contract_type, r["Ma"])
            self.refresh()

    def add_partner(self):
        if partner_dialog(self.ctx, self, {"VaiTroDoiTac": "Chủ đầu tư"}):
            self.refresh()

    def edit_partner(self):
        r = self.partners.current()
        if r and partner_dialog(self.ctx, self, r, r["Ma"]):
            self.refresh()

    def del_partner(self):
        r = self.partners.current()
        if r and confirm(self, f"Xóa đối tác \"{r['TenDoiTac']}\"?"):
            run_safely(self, self.ctx.cs.delete_partner, r["Ma"])
            self.refresh()

    def _group_dialog(self, name="", members=()):
        from .widgets import CheckList
        dlg = QDialog(self)
        dlg.setWindowTitle("Nhóm chủ đầu tư")
        dlg.resize(460, 520)
        lay = QVBoxLayout(dlg)
        edit = QLineEdit(name)
        edit.setPlaceholderText("Tên nhóm, VD: CĐT nhà nước")
        lay.addWidget(edit)
        lst = CheckList([(p["TenDoiTac"], p["Ma"]) for p in self.ctx.cs.partners("Chủ đầu tư")], members)
        lay.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() == QDialog.Accepted:
            return edit.text(), lst.values()
        return None

    def add_group(self):
        res = self._group_dialog()
        if res and run_safely(self, self.ctx.cs.save_group, *res):
            self.refresh()

    def edit_group(self):
        r = self.groups.current()
        if not r:
            return
        old_members = set(self.ctx.cs.group_members(r["Ma"]))
        res = self._group_dialog(r["TenNhom"], old_members)
        if res and (res[0] != r["TenNhom"] or set(res[1]) != old_members) and not confirm_changes(
                self, [("Tên nhóm", r["TenNhom"], res[0]),
                       ("Số chủ đầu tư trong nhóm", str(len(old_members)), str(len(res[1])))], "nhóm"):
            return
        if res and run_safely(self, self.ctx.cs.save_group, res[0], res[1], r["Ma"]):
            self.refresh()

    def del_group(self):
        r = self.groups.current()
        if r and confirm(self, f"Xóa nhóm \"{r['TenNhom']}\"?"):
            self.ctx.cs.delete_group(r["Ma"])
            self.refresh()


__all__ = ["ContractsPage", "CatalogPage", "contract_dialog", "partner_dialog", "payment_dialog"]
