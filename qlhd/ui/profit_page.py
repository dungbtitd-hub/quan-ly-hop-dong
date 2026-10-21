"""Thẻ Lãi lỗ trong Thu chi: bảng tổng hợp lãi lỗ theo năm, báo cáo doanh thu và chi phí năm (dữ liệu bảo vệ)."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QLabel, QSpinBox, QVBoxLayout, QWidget

from ..core.profit import GROUPS, REVENUE_MODES, ProfitError, ProfitService
from ..core.reports import export_xlsx, fmt_money
from .widgets import (Combo, DataTable, FormDialog, MoneyEdit, ask_secret, ask_text, button, confirm, error, hint,
                      info, run_safely, toolbar)

COLS = [("STT", "STT", "text"), ("TenDong", "Nội dung", "text"), ("GiaTri", "Số tiền", "money"),
        ("Q1", "Quý 1", "money"), ("Q2", "Quý 2", "money"), ("Q3", "Quý 3", "money"), ("Q4", "Quý 4", "money"),
        ("NguonText", "Nguồn số liệu", "text")]


class ProfitTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year)
        self.year.valueChanged.connect(self.reload)
        self.mode = Combo([(t, k) for k, t in REVENUE_MODES])
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 100)
        self.rate.setDecimals(1)
        self.rate.setSuffix(" %")
        self.tndn_own = QCheckBox("Ghi đè thuế TNDN")
        self.tndn = MoneyEdit()
        self.tncn = MoneyEdit()
        self.tndn_own.toggled.connect(self.tndn.setEnabled)
        can_edit = ctx.can("ThuChi", "Sua")
        b_save = button("Lưu thông số năm", self.save_params, "Primary")
        b_save.setEnabled(can_edit)
        lay.addLayout(toolbar(QLabel("Năm"), self.year, QLabel("Doanh thu tính theo"), self.mode, "stretch"))
        lay.addLayout(toolbar(QLabel("Thuế suất TNDN"), self.rate, self.tndn_own, self.tndn,
                              QLabel("Thuế TNCN còn phải nộp"), self.tncn, b_save, "stretch"))
        btns = [button("Thêm dòng…", self.add_row), button("Sửa dòng…", self.edit_row),
                button("Lên", lambda: self.move(-1)), button("Xuống", lambda: self.move(1)),
                button("Ẩn / hiện dòng", self.toggle_hidden), button("Xóa dòng", self.delete_row, "Danger")]
        for b in btns:
            b.setEnabled(can_edit)
        self.b_close = button("Chốt năm", self.close_year)
        self.b_open = button("Mở chốt", self.reopen_year)
        self.b_close.setEnabled(can_edit)
        self.b_open.setEnabled(ctx.user["VaiTro"] == "Giám đốc")
        b_x1 = button("Xuất bảng lãi lỗ…", self.export_table)
        b_x2 = button("Xuất báo cáo doanh thu, chi phí năm…", self.export_report)
        for b in (b_x1, b_x2):
            b.setEnabled(ctx.can("ThuChi", "Xuat"))
        lay.addLayout(toolbar(*btns, "stretch", self.b_close, self.b_open))
        self.table = DataTable(COLS)
        self.table.activated_row.connect(lambda _: self.edit_row())
        lay.addWidget(self.table, 1)
        self.status = hint("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.pw = QCheckBox("Đặt mật khẩu mở file Excel")
        self.pw.setChecked(True)
        b_dy = button("Xóa vĩnh viễn số liệu năm…", self.delete_year, "Danger")
        b_da = button("Xóa toàn bộ lãi lỗ…", self.delete_all, "Danger")
        b_dy.setEnabled(ctx.can("ThuChi", "Xoa"))
        b_da.setEnabled(ctx.can("ThuChi", "Xoa") and ctx.user["VaiTro"] == "Giám đốc")
        lay.addLayout(toolbar(b_x1, b_x2, self.pw, "stretch", b_dy, b_da))
        self.res = None

    @property
    def svc(self) -> ProfitService:
        return ProfitService(self.ctx.finance, self.ctx.db)

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.ctx.finance is not None:
            self.reload()

    def reload(self):
        if self.ctx.finance is None:
            return
        svc = self.svc
        y = self.year.value()
        infos = svc.year_info(y)
        self.res = res = svc.compute(y)
        self.mode.set_value(infos["CheDoDoanhThu"])
        self.rate.setValue(float(infos["TyLeThueTNDN"]))
        self.tndn_own.setChecked(infos["ThueTNDNNhap"] is not None)
        self.tndn.setEnabled(infos["ThueTNDNNhap"] is not None)
        self.tndn.set_value(infos["ThueTNDNNhap"] or 0)
        self.tncn.set_value(infos["TNCNConPhaiNop"] or 0)
        rows = []

        def add(r, **kw):
            q = r.get("Quy") or [None] * 4
            rows.append(dict(r, Q1=q[0], Q2=q[1], Q3=q[2], Q4=q[3], **kw))

        visible = [r for r in res["rows"]]
        for g in ("Doanh thu", "Điều chỉnh"):
            grp = [r for r in visible if r["Nhom"] == g]
            if g == "Điều chỉnh" and grp:
                rows.append({"TenDong": "Điều chỉnh", "_kieu": "nhom"})
            for r in grp:
                add(r, TenDong=self._label(r))
        q = res["doanh_thu_quy"]
        rows.append({"TenDong": "Tổng doanh thu", "GiaTri": res["tong_doanh_thu"], "Q1": q[0], "Q2": q[1],
                     "Q3": q[2], "Q4": q[3], "_kieu": "cong"})
        q = res["chi_phi_quy"]
        rows.append({"TenDong": "Chi phí", "GiaTri": res["chi_phi"], "Q1": q[0], "Q2": q[1], "Q3": q[2], "Q4": q[3],
                     "_kieu": "cong"})
        for r in (r for r in visible if r["Nhom"] == "Chi phí"):
            add(r, TenDong="- " + self._label(r), STT=r["STT"] or "")
        rows.append({"TenDong": f"Lợi nhuận {y}", "GiaTri": res["loi_nhuan"], "_kieu": "cong"})
        rows.append({"TenDong": f"Thuế TNDN ({res['ty_le']:g}%)" if not res["thue_tndn_nhap"] else "Thuế TNDN (ghi đè)",
                     "GiaTri": res["thue_tndn"], "NguonText": "Lợi nhuận × tỷ lệ" if not res["thue_tndn_nhap"]
                     else "Nhập tay"})
        for r in (r for r in visible if r["Nhom"] == "Thuế"):
            add(r, TenDong=self._label(r))
        rows.append({"TenDong": "Tổng số thuế phải nộp", "GiaTri": res["tong_thue"], "_kieu": "cong"})
        rows.append({"TenDong": "Thuế thu nhập cá nhân còn phải nộp", "GiaTri": res["tncn_con"],
                     "NguonText": "Nhập tay (ô phía trên)"})
        rows.append({"TenDong": "Tổng chi phí phải nộp", "GiaTri": res["tong_phai_nop"], "_kieu": "cong"})
        self.table.set_rows(rows)
        notes = []
        if res.get("da_chot"):
            notes.append(f"Năm {y} đã chốt: đang xem số lúc chốt. Giám đốc bấm Mở chốt để sửa.")
        notes += res["canh_bao"]
        notes.append("Dòng tự động: phần mềm lấy từ hóa đơn, sổ TSCĐ / CCDC; nhập số ở Sửa dòng để ghi đè, để trống "
                     "để về số tự động. Số liệu bảng này được mã hóa cùng thu chi.")
        self.status.setText("\n".join(notes))
        self.b_close.setVisible(not res.get("da_chot"))
        self.b_open.setVisible(bool(res.get("da_chot")))

    @staticmethod
    def _label(r) -> str:
        s = r["TenDong"]
        if not r["Hien"]:
            s += "  (đang ẩn)"
        if r.get("GhiDe"):
            s += "  (ghi đè)"
        return s

    def _current_row(self):
        r = self.table.current()
        if r is None or not r.get("Ma"):
            info(self, "Hãy chọn một dòng của bảng (không phải dòng tổng).")
            return None
        return r

    def _run(self, fn, *args):
        try:
            fn(*args)
        except ProfitError as e:
            error(self, str(e))
        self.reload()

    def save_params(self):
        self._run(self.svc.set_year, self.year.value(), {
            "CheDoDoanhThu": self.mode.value(), "TyLeThueTNDN": self.rate.value(),
            "ThueTNDNNhap": self.tndn.value() if self.tndn_own.isChecked() else None,
            "TNCNConPhaiNop": self.tncn.value()})

    def add_row(self):
        dlg = FormDialog("Thêm dòng bảng lãi lỗ", [
            {"key": "Nhom", "label": "Thuộc nhóm", "kind": "combo", "options": [(g, g) for g in GROUPS]},
            {"key": "TenDong", "label": "Tên dòng", "required": True},
            {"key": "Dau", "label": "Cách tính", "kind": "combo",
             "options": [("Cộng vào nhóm (+)", 1), ("Trừ khỏi nhóm (−)", -1)]},
            {"key": "GiaTri", "label": "Số tiền", "kind": "money"}], {"Nhom": "Chi phí", "Dau": 1}, self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            self._run(self.svc.add_row, self.year.value(), v["Nhom"], v["TenDong"], v["Dau"], v["GiaTri"])

    def edit_row(self):
        r = self._current_row()
        if r is None:
            return
        auto = r["Nguon"] != "nhap"
        fields = [{"key": "TenDong", "label": "Tên dòng", "required": True}]
        if not (r["HeThong"] and auto):
            fields.append({"key": "Dau", "label": "Cách tính", "kind": "combo",
                           "options": [("Cộng vào nhóm (+)", 1), ("Trừ khỏi nhóm (−)", -1)]})
        fields += [{"key": "GhiDe", "label": "", "kind": "check",
                    "text": "Nhập số tay (ghi đè số tự động)" if auto else "Có số tiền"},
                   {"key": "GiaTriNhap", "label": "Số tiền", "kind": "money",
                    "hint": (f"Số tự động: {fmt_money(r['TuDong'] or 0)} đ. Bỏ tích ô trên để về số tự động."
                             if auto else "Nhập số dương; dấu theo Cách tính.")},
                   {"key": "GhiChu", "label": "Ghi chú"}]
        vals = {"TenDong": r["TenDong"].split("  (")[0], "Dau": r["Dau"], "GhiChu": r.get("GhiChu"),
                "GhiDe": r["GiaTriNhap"] is not None or not auto, "GiaTriNhap": r["GiaTriNhap"] or 0}
        dlg = FormDialog("Sửa dòng bảng lãi lỗ", fields, vals, self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            v["GiaTriNhap"] = v["GiaTriNhap"] if v.pop("GhiDe") else None
            self._run(self.svc.update_row, r["Ma"], v)

    def move(self, step):
        r = self._current_row()
        if r is not None:
            self._run(self.svc.move_row, r["Ma"], step)

    def toggle_hidden(self):
        r = self._current_row()
        if r is not None:
            self._run(self.svc.update_row, r["Ma"], {"An": 0 if r["An"] else 1})

    def delete_row(self):
        r = self._current_row()
        if r is not None and confirm(self, f"Xóa vĩnh viễn dòng \"{r['TenDong']}\"?"):
            self._run(self.svc.delete_row, r["Ma"])

    def close_year(self):
        if confirm(self, f"Chốt bảng lãi lỗ năm {self.year.value()}? Số liệu được giữ nguyên như hiện tại; muốn sửa "
                         "phải Giám đốc mở chốt."):
            self._run(self.svc.close_year, self.year.value())

    def reopen_year(self):
        self._run(self.svc.reopen_year, self.ctx.user, self.year.value())

    def _password(self):
        if not self.pw.isChecked():
            return ""
        return ask_secret(self, "Mật khẩu file", "Mật khẩu để mở file Excel (người nhận cần mật khẩu này):")

    def export_table(self):
        y = self.year.value()
        path, _ = QFileDialog.getSaveFileName(self, "Xuất bảng lãi lỗ", f"Bang_lai_lo_{y}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        pw = self._password()
        if pw is None:
            return
        if run_safely(self, self.svc.export_xlsx, path, y, company=self.ctx.company, user=self.ctx.user["HoTen"],
                      password=pw or None) is not None:
            self.ctx.db.log(self.ctx.user["TenDangNhap"], f"Xuất bảng lãi lỗ năm {y}", path)
            info(self, f"Đã xuất:\n{path}")

    def export_report(self):
        y = self.year.value()
        path, _ = QFileDialog.getSaveFileName(self, "Xuất báo cáo doanh thu, chi phí năm",
                                              f"Doanh_thu_chi_phi_{y}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        pw = self._password()
        if pw is None:
            return
        reps = run_safely(self, self.svc.year_report, y)
        if reps and run_safely(self, export_xlsx, reps, path, company=self.ctx.company, user=self.ctx.user["HoTen"],
                               password=pw or None) is not None:
            self.ctx.db.log(self.ctx.user["TenDangNhap"], f"Xuất báo cáo doanh thu, chi phí năm {y}", path)
            info(self, f"Đã xuất:\n{path}")

    def _check_key(self) -> bool:
        key = ask_secret(self, "Xác nhận", "Nhập lại Khóa thu chi để xóa vĩnh viễn:")
        if key is None:
            return False
        try:
            self.ctx.finance.check_key(key)
            return True
        except Exception as e:  # noqa: BLE001
            error(self, str(e))
            return False

    def delete_year(self):
        y = self.year.value()
        if confirm(self, f"Xóa vĩnh viễn số liệu bảng lãi lỗ năm {y} (dòng tự thêm, số nhập tay, số chốt)? Không "
                         "khôi phục được. Nên xuất Excel trước.") and self._check_key():
            n = self.svc.delete_year(y)
            info(self, f"Đã xóa {n} bản ghi lãi lỗ năm {y}.")
            self.reload()

    def delete_all(self):
        typed = ask_text(self, "Xóa toàn bộ lãi lỗ", 'Gõ chữ "XOA TOAN BO" để xác nhận:')
        if not typed or typed.strip().upper() != "XOA TOAN BO":
            return
        if self._check_key():
            n = self.svc.delete_all()
            info(self, f"Đã xóa vĩnh viễn {n} bản ghi lãi lỗ của mọi năm.")
            self.reload()
