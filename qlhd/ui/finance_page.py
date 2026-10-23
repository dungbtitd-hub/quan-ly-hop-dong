"""Module Thu chi trên giao diện: mở khóa, sổ thu chi, khoản mục, quỹ, báo cáo, xóa dữ liệu, bảo mật."""
from __future__ import annotations

import os
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox,
                               QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import finance as fin
from ..core import finance_import
from ..core.finance import FinanceError, FinanceStore
from ..core.reports import FinanceReports, export_xlsx, fmt_date, fmt_money, period_range
from .profit_page import ProfitTab
from .widgets import (Combo, DataTable, DateEdit, FormDialog, MoneyEdit, ask_secret, ask_text, button, confirm,
                      confirm_changes,
                      copy_text, error, hint, info, page_title, run_safely, toolbar)

ENTRY_COLS = [("Ngay", "Ngày", "date"), ("SoPhieu", "Số phiếu / Số HĐ", "text"), ("PhanLoai", "Phân loại", "text"),
              ("NoiDung", "Nội dung", "text"), ("TenDuAn", "Dự án", "text"), ("KhoanMuc", "Khoản mục", "text"),
              ("Quy", "Quỹ", "text"), ("Thu", "Thu", "money"), ("Chi", "Chi", "money")]


def _pw() -> QLineEdit:
    w = QLineEdit()
    w.setEchoMode(QLineEdit.Password)
    return w


class RecoveryCodeDialog(QDialog):
    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mã khôi phục thu chi")
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Hãy in hoặc chép tay Mã khôi phục và cất ở nơi an toàn. Đây là cách duy nhất để mở lại "
                           "dữ liệu thu chi nếu tất cả người có quyền đều quên Khóa thu chi. Mã chỉ hiện một lần.",
                           "Warn"))
        edit = QLineEdit(code)
        edit.setReadOnly(True)
        edit.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 14pt;")
        lay.addWidget(edit)
        row = QHBoxLayout()
        row.addWidget(button("Sao chép", lambda: copy_text(code)))
        row.addWidget(button("Lưu ra file…", lambda: self._save(code)))
        row.addStretch(1)
        ok = button("Tôi đã cất giữ mã", self.accept, "Primary")
        row.addWidget(ok)
        lay.addLayout(row)

    def _save(self, code):
        path, _ = QFileDialog.getSaveFileName(self, "Lưu Mã khôi phục", "Ma_khoi_phuc_thu_chi.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("MÃ KHÔI PHỤC THU CHI – Phần mềm Quản lý Hợp đồng\n\n" + code + "\n")


VOUCHER_LABELS = {"NgayPhatSinh": "Ngày", "SoTien": "Số tiền", "NoiDung": "Nội dung", "DoiTuong": "Đối tượng",
                  "MaKhoanMuc": "Khoản mục", "MaQuy": "Quỹ", "MaHopDong": "Dự án", "PhanLoaiChi": "Phân loại chi",
                  "SoChungTu": "Số chứng từ", "GhiChu": "Ghi chú", "LoaiThuChi": "Loại"}


class VoucherDialog(QDialog):
    def __init__(self, store: FinanceStore, loai: str = "Chi", values: dict | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.values_in = dict(values or {})
        self.saved_ids: list[str] = []
        self.editing = values.get("Ma") if values else None
        self.setWindowTitle(("Sửa phiếu" if self.editing else "Phiếu chi" if loai == "Chi" else "Phiếu thu khác"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.loai = Combo([("Chi", "Chi"), ("Thu khác", "Thu")], self.values_in.get("LoaiThuChi", loai))
        self.ngay = DateEdit(self.values_in.get("NgayPhatSinh"))
        self.phan_loai = Combo([("Chi hoạt động văn phòng", "Văn phòng"), ("Chi dự án", "Dự án")],
                               self.values_in.get("PhanLoaiChi") or store.setting("nho_phan_loai") or "Văn phòng")
        projects = [(f"{p['SoHopDong']} – {p['TenHopDong']}", p["Ma"]) for p in store.projects()]
        self.du_an = Combo(projects + [("Dự án khác (chưa có hợp đồng)…", "__khac__")])
        self.du_an_khac = QLineEdit()
        self.du_an_khac.setPlaceholderText("Tên dự án")
        if self.values_in.get("MaDuAn"):
            self.du_an.set_value(self.values_in["MaDuAn"])
        elif self.values_in.get("TenDuAn"):
            self.du_an.set_value("__khac__")
            self.du_an_khac.setText(self.values_in["TenDuAn"])
        self.muc = Combo()
        add_cat = QPushButton("+")
        add_cat.setToolTip("Thêm mục con mới")
        add_cat.setFixedWidth(28)
        add_cat.clicked.connect(self._add_category)
        mrow = QHBoxLayout()
        mrow.addWidget(self.muc, 1)
        mrow.addWidget(add_cat)
        self.so_tien = MoneyEdit(self.values_in.get("SoTien", 0))
        self.quy = Combo([(f["TenQuy"], f["Ma"]) for f in store.funds()],
                         self.values_in.get("MaQuy") or store.setting("nho_quy"))
        self.noi_dung = QLineEdit(self.values_in.get("NoiDung", ""))
        self.doi_tuong = QLineEdit(self.values_in.get("DoiTuong") or "")
        self.tham_chieu = QLineEdit(self.values_in.get("ThamChieu") or "")
        self.so_phieu = QLineEdit(self.values_in.get("SoPhieu") or "")
        self.so_phieu.setPlaceholderText("Tự sinh (PC-2026-0001 / PT-2026-0001)")
        form.addRow("Loại phiếu", self.loai)
        form.addRow("Ngày", self.ngay)
        self.lbl_pl = QLabel("Phân loại chi")
        form.addRow(self.lbl_pl, self.phan_loai)
        self.lbl_da = QLabel("Dự án")
        da_box = QWidget()
        dl = QVBoxLayout(da_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.du_an)
        dl.addWidget(self.du_an_khac)
        form.addRow(self.lbl_da, da_box)
        self.da_box = da_box
        form.addRow("Khoản mục *", mrow)
        form.addRow("Số tiền *", self.so_tien)
        form.addRow("Quỹ *", self.quy)
        form.addRow("Nội dung *", self.noi_dung)
        form.addRow("Người nhận / nộp", self.doi_tuong)
        form.addRow("Số chứng từ / tham chiếu", self.tham_chieu)
        form.addRow("Số phiếu", self.so_phieu)
        lay.addLayout(form)
        bb = QDialogButtonBox()
        save = bb.addButton("Lưu", QDialogButtonBox.AcceptRole)
        save.clicked.connect(lambda: self._save(close=True))
        if not self.editing:
            more = bb.addButton("Lưu && thêm tiếp", QDialogButtonBox.ActionRole)
            more.clicked.connect(lambda: self._save(close=False))
        cancel = bb.addButton("Đóng", QDialogButtonBox.RejectRole)
        cancel.clicked.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.loai, self.phan_loai, self.du_an):
            w.currentIndexChanged.connect(self._sync_ui)
        self._sync_ui()
        if self.values_in.get("MaKhoanMuc"):
            self.muc.set_value(self.values_in["MaKhoanMuc"])

    def _sync_ui(self):
        is_chi = self.loai.value() == "Chi"
        for w in (self.lbl_pl, self.phan_loai):
            w.setVisible(is_chi)
        is_da = is_chi and self.phan_loai.value() == "Dự án"
        self.lbl_da.setVisible(is_da)
        self.da_box.setVisible(is_da)
        self.du_an_khac.setVisible(self.du_an.value() == "__khac__")
        current = self.muc.value()
        scope = self.phan_loai.value() if is_chi else None
        opts = []
        for cat, depth in self.store.category_tree(self.loai.value()):
            if scope and cat["ApDungCho"] not in (scope, "Cả hai"):
                continue
            opts.append(("    " * depth + cat["TenKhoanMuc"], cat["Ma"]))
        self.muc.set_options(opts, current)

    def _add_category(self):
        parent = self.muc.value()
        name = ask_text(self, "Thêm mục", "Tên mục con mới (thuộc mục đang chọn):" if parent else "Tên mục mới:")
        if not name:
            return
        loai = self.loai.value()
        ap = self.phan_loai.value() if loai == "Chi" else "Cả hai"
        ma = run_safely(self, self.store.add_category, name, loai, parent, ap)
        if ma:
            self.store.save()
            self._sync_ui()
            self.muc.set_value(ma)

    def values(self) -> dict:
        v = {"LoaiThuChi": self.loai.value(), "NgayPhatSinh": self.ngay.value(), "MaKhoanMuc": self.muc.value(),
             "SoTien": self.so_tien.value(), "MaQuy": self.quy.value(), "NoiDung": self.noi_dung.text().strip(),
             "DoiTuong": self.doi_tuong.text().strip(), "ThamChieu": self.tham_chieu.text().strip(),
             "SoPhieu": self.so_phieu.text().strip() or None}
        if v["LoaiThuChi"] == "Chi":
            v["PhanLoaiChi"] = self.phan_loai.value()
            if v["PhanLoaiChi"] == "Dự án":
                if self.du_an.value() == "__khac__":
                    v["MaDuAn"], v["TenDuAn"] = None, self.du_an_khac.text().strip()
                else:
                    p = self.store.db.get("HopDong", self.du_an.value()) if self.du_an.value() else None
                    v["MaDuAn"], v["TenDuAn"] = (p["Ma"], p["TenHopDong"]) if p else (None, "")
        return v

    def _save(self, close: bool):
        v = self.values()
        if self.editing:
            changes = [(k, str(self.values_in.get(k) or ""), str(v.get(k) or "")) for k in v
                       if k in self.values_in and str(self.values_in.get(k) or "") != str(v.get(k) or "")]
            if changes and not confirm_changes(self, [(VOUCHER_LABELS.get(k, k), fmt_money(o) if k == "SoTien" and o
                                                       else o, fmt_money(n) if k == "SoTien" and n else n)
                                                      for k, o, n in changes], "phiếu"):
                return
        try:
            if self.editing:
                self.store.update_voucher(self.editing, v)
            else:
                self.saved_ids.append(self.store.add_voucher(v))
        except FinanceError as e:
            return error(self, str(e))
        if v.get("PhanLoaiChi"):
            self.store.set_setting("nho_phan_loai", v["PhanLoaiChi"])
        self.store.set_setting("nho_quy", v["MaQuy"])
        self.store.save()
        if close:
            self.accept()
        else:
            self.so_tien.set_value(0)
            self.noi_dung.clear()
            self.so_phieu.clear()
            self.noi_dung.setFocus()


class FinanceImportDialog(QDialog):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Xem trước phiếu thu chi nhập từ Excel")
        self.resize(1100, 580)
        lay = QVBoxLayout(self)
        ok = [r for r in rows if r.ok]
        chi = sum(r.values["SoTien"] for r in ok if r.values["LoaiThuChi"] == "Chi")
        thu = sum(r.values["SoTien"] for r in ok if r.values["LoaiThuChi"] == "Thu")
        lay.addWidget(hint(f"{len(ok)} dòng hợp lệ sẽ được lưu (chi {fmt_money(chi)} đ, thu khác {fmt_money(thu)} đ); "
                           f"{len(rows) - len(ok)} dòng lỗi hoặc trùng sẽ bỏ qua."))
        table = DataTable([("line", "Dòng", "int"), ("status", "Kết quả", "text"), ("Ngay", "Ngày", "date"),
                           ("Loai", "Loại", "text"), ("TenDuAn", "Dự án", "text"), ("KhoanMuc", "Khoản mục", "text"),
                           ("NoiDung", "Nội dung", "text"), ("SoTien", "Số tiền", "money"),
                           ("errors", "Ghi chú", "text")])
        data = []
        for r in rows:
            v = r.values
            loai = v.get("LoaiThuChi") or ""
            if loai == "Chi" and v.get("PhanLoaiChi"):
                loai = f"Chi {v['PhanLoaiChi'].lower()}"
            data.append({"line": r.line, "status": r.status, "Ngay": v.get("NgayPhatSinh"), "Loai": loai,
                         "TenDuAn": v.get("TenDuAn") or "", "KhoanMuc": v.get("KhoanMuc"),
                         "NoiDung": v.get("NoiDung"), "SoTien": v.get("SoTien") or 0,
                         "errors": "; ".join(r.errors)})
        table.set_rows(data)
        lay.addWidget(table)
        self.delete_source = QCheckBox("Xóa file Excel nguồn sau khi lưu (để số liệu thu chi không còn nằm ngoài "
                                       "tệp mã hóa)")
        lay.addWidget(self.delete_source)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(f"Lưu {len(ok)} dòng hợp lệ")
        bb.button(QDialogButtonBox.Ok).setEnabled(bool(ok))
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class FinancePage(QWidget):
    unlocked = Signal()
    locked = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.lock_view = QWidget()
        self.stack.addWidget(self.lock_view)
        self.main_view = None

    # ------------------------------------------------------------ khóa
    def refresh(self):
        if self.ctx.finance is None:
            self._build_lock_view()
            self.stack.setCurrentWidget(self.lock_view)
        else:
            if self.main_view is None:
                self.main_view = self._build_main_view()
                self.stack.addWidget(self.main_view)
            self.stack.setCurrentWidget(self.main_view)
            self.reload()

    def _build_lock_view(self):
        old = self.lock_view.layout()
        if old is not None:
            QWidget().setLayout(old)
        lay = QVBoxLayout(self.lock_view)
        lay.addWidget(page_title("Thu chi (bảo mật riêng)"))
        db, user = self.ctx.db, self.ctx.user
        box = QGroupBox("Mở khóa module Thu chi")
        bl = QVBoxLayout(box)
        if not fin.is_initialized(db):
            if user["VaiTro"] == "Giám đốc" and self.ctx.acc.is_primary_director_machine():
                bl.addWidget(hint("Thu chi chưa được khởi tạo. Đặt Khóa thu chi (khác mật khẩu đăng nhập, ≥ 8 ký tự). "
                                  "Dữ liệu thu chi được mã hóa riêng; chỉ người có Khóa thu chi mới mở được."))
                form = QFormLayout()
                self.k1, self.k2 = _pw(), _pw()
                form.addRow("Khóa thu chi", self.k1)
                form.addRow("Nhập lại", self.k2)
                bl.addLayout(form)
                bl.addWidget(button("Khởi tạo Thu chi", self._initialize, "Primary"), 0, Qt.AlignLeft)
            else:
                bl.addWidget(hint("Máy này chưa có dữ liệu thu chi. Để theo dõi song song với máy Giám đốc chính: "
                                  "trên máy Giám đốc chính vào Đồng bộ → Xuất gói, chọn \"Gói Giám đốc (kèm thu chi)\"; "
                                  "sau đó nhận gói ở mục Đồng bộ trên máy này."))
        elif not fin.has_key_for(db, user["TenDangNhap"]):
            bl.addWidget(hint("Tài khoản này chưa được đặt Khóa thu chi. Giám đốc mở Thu chi → Bảo mật → "
                              "\"Đặt Khóa thu chi cho Người quản lý\"."))
        else:
            form = QFormLayout()
            self.key = _pw()
            self.key.returnPressed.connect(self._unlock)
            form.addRow("Khóa thu chi", self.key)
            bl.addLayout(form)
            row = QHBoxLayout()
            row.addWidget(button("Mở khóa", self._unlock, "Primary"))
            row.addWidget(button("Quên khóa – dùng Mã khôi phục…", self._recover))
            row.addStretch(1)
            bl.addLayout(row)
        lay.addWidget(box)
        lay.addStretch(1)

    def _initialize(self):
        if self.k1.text() != self.k2.text():
            return error(self, "Hai lần nhập khóa không khớp.")
        try:
            self.ctx.finance, code = FinanceStore.initialize(self.ctx.db, self.ctx.finance_path, self.ctx.user,
                                                             self.k1.text())
        except FinanceError as e:
            return error(self, str(e))
        RecoveryCodeDialog(code, self).exec()
        self.unlocked.emit()
        self.refresh()

    def _unlock(self):
        try:
            self.ctx.finance = FinanceStore.unlock(self.ctx.db, self.ctx.finance_path, self.ctx.user, self.key.text())
        except FinanceError as e:
            self.key.clear()
            return error(self, str(e))
        self.unlocked.emit()
        self.refresh()

    def _recover(self):
        code = ask_text(self, "Mã khôi phục", "Nhập Mã khôi phục 24 ký tự:")
        if not code:
            return
        new = ask_secret(self, "Khóa mới", "Đặt Khóa thu chi mới (≥ 8 ký tự):")
        if not new:
            return
        try:
            self.ctx.finance = FinanceStore.unlock_with_recovery(self.ctx.db, self.ctx.finance_path, self.ctx.user,
                                                                 code, new)
        except FinanceError as e:
            return error(self, str(e))
        info(self, "Đã đặt Khóa thu chi mới.")
        self.unlocked.emit()
        self.refresh()

    def lock(self):
        if self.ctx.finance is not None:
            self.ctx.lock_finance()
            self.locked.emit()
        if self.main_view is not None:
            self.stack.removeWidget(self.main_view)
            self.main_view.deleteLater()
            self.main_view = None
        self.refresh()

    def auto_lock_minutes(self) -> int:
        store = self.ctx.finance
        return int(store.setting("tu_khoa_phut", "5")) if store else 5

    # ------------------------------------------------------------ giao diện chính
    def _build_main_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        head = QHBoxLayout()
        head.addWidget(page_title("Thu chi (đã mở khóa)"))
        head.addStretch(1)
        self.balance_lbl = QLabel()
        head.addWidget(self.balance_lbl)
        head.addWidget(button("Khóa ngay", self.lock, "Danger"))
        lay.addLayout(head)
        tabs = QTabWidget()
        tabs.addTab(self._tab_ledger(), "Sổ thu chi")
        tabs.addTab(self._tab_categories(), "Khoản mục")
        tabs.addTab(self._tab_funds(), "Quỹ tiền")
        tabs.addTab(self._tab_reports(), "Báo cáo && xuất Excel")
        tabs.addTab(ProfitTab(self.ctx), "Lãi lỗ")
        tabs.addTab(self._tab_delete(), "Xóa dữ liệu")
        tabs.addTab(self._tab_security(), "Bảo mật")
        lay.addWidget(tabs)
        return w

    def reload(self):
        store = self.ctx.finance
        if store is None or self.main_view is None:
            return
        today = date.today().isoformat()
        self.balance_lbl.setText(f"Tồn quỹ hôm nay: <b>{fmt_money(store.balance(today))} đ</b>  ")
        self.reload_ledger()
        self.reload_categories()
        self.funds.set_rows(store.fund_balances(today))

    # ---- sổ thu chi
    def _tab_ledger(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        a, b = period_range("thang", date.today())
        self.f_tu, self.f_den = DateEdit(a), DateEdit(b)
        self.f_loai = Combo([("Thu và chi", None), ("Chỉ thu", "Thu"), ("Chỉ chi", "Chi")])
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText("Tìm nội dung, người nhận, số phiếu…")
        for x in (self.f_tu, self.f_den):
            x.dateChanged.connect(self.reload_ledger)
        self.f_loai.currentIndexChanged.connect(self.reload_ledger)
        self.f_text.textChanged.connect(self.reload_ledger)
        can_add = self.ctx.can("ThuChi", "Them")
        b1 = button("Thêm phiếu chi", lambda: self._new("Chi"), "Primary")
        b2 = button("Thêm phiếu thu khác", lambda: self._new("Thu"))
        b3 = button("Sửa", self._edit)
        b4 = button("Xóa phiếu đã chọn", self._delete, "Danger")
        b5 = button("Nhập từ Excel…", self._import_excel)
        b6 = button("Tải file Excel mẫu", self._save_template)
        b1.setEnabled(can_add)
        b2.setEnabled(can_add)
        b3.setEnabled(self.ctx.can("ThuChi", "Sua"))
        b4.setEnabled(self.ctx.can("ThuChi", "Xoa"))
        b5.setEnabled(can_add)
        lay.addLayout(toolbar(b1, b2, b3, b4, "stretch", b5, b6))
        lay.addLayout(toolbar(QLabel("Từ"), self.f_tu, QLabel("đến"), self.f_den, self.f_loai, self.f_text, "stretch"))
        self.ledger = DataTable(ENTRY_COLS, multi=True)
        self.ledger.activated_row.connect(lambda _: self._edit())
        lay.addWidget(self.ledger, 1)
        self.ledger_total = hint("")
        lay.addWidget(self.ledger_total)
        return w

    def reload_ledger(self):
        store = self.ctx.finance
        if store is None:
            return
        rows = store.entries(self.f_tu.value(), self.f_den.value(), loai=self.f_loai.value(),
                             tu_khoa=self.f_text.text().strip())
        self.ledger.set_rows(rows)
        t = store.totals(self.f_tu.value(), self.f_den.value())
        self.ledger_total.setText(
            f"Tồn đầu kỳ {fmt_money(t['ton_dau'])} · Thu {fmt_money(t['tong_thu'])} (hợp đồng {fmt_money(t['thu_hop_dong'])}, "
            f"khác {fmt_money(t['thu_khac'])}) · Chi {fmt_money(t['tong_chi'])} (dự án {fmt_money(t['chi_du_an'])}, "
            f"văn phòng {fmt_money(t['chi_van_phong'])}) · Tồn cuối kỳ {fmt_money(t['ton_cuoi'])}. "
            f"Khoản thu hợp đồng lấy tự động từ phần Thanh toán hợp đồng.")

    def _new(self, loai):
        dlg = VoucherDialog(self.ctx.finance, loai, None, self)
        dlg.exec()
        if dlg.saved_ids:
            self.reload()

    def _edit(self):
        r = self.ledger.current()
        if not r:
            return
        if r["Nguon"] == "Hợp đồng":
            return info(self, "Khoản thu từ hợp đồng được sửa ở phần Hợp đồng / Hóa đơn đầu ra (nơi gốc).")
        v = dict(self.ctx.finance.fdb.get("PhieuThuChi", r["Ma"]))
        if VoucherDialog(self.ctx.finance, v["LoaiThuChi"], v, self).exec() == QDialog.Accepted:
            self.reload()

    def _delete(self):
        rows = [r for r in self.ledger.selected() if r["Nguon"] == "Phiếu"]
        if not rows:
            return info(self, "Chọn phiếu chi / phiếu thu khác cần xóa (khoản thu hợp đồng xóa ở phần Hợp đồng).")
        if confirm(self, f"Xóa vĩnh viễn {len(rows)} phiếu, tổng {fmt_money(sum(r['Thu'] + r['Chi'] for r in rows))} đ?"):
            self.ctx.finance.delete_vouchers([r["Ma"] for r in rows])
            self.reload()

    def _save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Lưu file Excel mẫu nhập thu chi", "Mau_nhap_thu_chi.xlsx",
                                              "Excel (*.xlsx)")
        if path:
            finance_import.write_template(path, self.ctx.finance)
            info(self, f"Đã lưu file mẫu:\n{path}\n\nSheet Danh mục liệt kê khoản mục, quỹ và dự án hiện có "
                       f"(không có số tiền). Nên đặt mật khẩu mở file khi đã điền số liệu.")

    def _import_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file Excel thu chi", "", "Excel (*.xlsx)")
        if not path:
            return
        password = None
        if finance_import.is_encrypted(path):
            password = ask_secret(self, "Mật khẩu file", "File Excel có mật khẩu mở file. Nhập mật khẩu:")
            if password is None:
                return
        rows = run_safely(self, finance_import.read_workbook, path, self.ctx.finance, password)
        if not rows:
            return
        dlg = FinanceImportDialog(rows, self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = finance_import.import_rows(self.ctx.finance, rows)
        msg = (f"Đã lưu {res['Chi']} phiếu chi ({fmt_money(res['tong_chi'])} đ) và {res['Thu']} phiếu thu khác "
               f"({fmt_money(res['tong_thu'])} đ).")
        if res["khoan_muc_moi"]:
            msg += f"\nĐã thêm {res['khoan_muc_moi']} khoản mục mới."
        if res["loi"]:
            msg += "\n\nKhông lưu được:\n" + "\n".join(res["loi"][:20])
        if dlg.delete_source.isChecked():
            try:
                os.remove(path)
                msg += "\n\nĐã xóa file Excel nguồn."
            except OSError as e:
                msg += f"\n\nKhông xóa được file Excel nguồn: {e}"
        info(self, msg)
        self.reload()

    # ---- khoản mục
    def _tab_categories(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.show_hidden = QCheckBox("Hiện cả mục đã ẩn")
        self.show_hidden.toggled.connect(self.reload_categories)
        lay.addLayout(toolbar(button("Thêm nhóm (cấp 1)", lambda: self._cat_add(False)),
                              button("Thêm mục con", lambda: self._cat_add(True)),
                              button("Đổi tên", self._cat_rename), button("Chuyển sang nhóm…", self._cat_move),
                              button("Ẩn / hiện", self._cat_toggle), button("Gộp vào mục khác…", self._cat_merge),
                              button("Xóa", self._cat_delete), "stretch", self.show_hidden))
        self.cat_tree = QTreeWidget()
        self.cat_tree.setHeaderLabels(["Khoản mục", "Áp dụng cho", "Trạng thái"])
        self.cat_tree.setColumnWidth(0, 320)
        lay.addWidget(self.cat_tree)
        lay.addWidget(hint("Mục đã có phiếu chỉ được ẩn hoặc gộp, không xóa, để không mất số liệu cũ."))
        return w

    def reload_categories(self):
        store = self.ctx.finance
        if store is None:
            return
        self.cat_tree.clear()
        for loai, title in (("Chi", "KHOẢN CHI"), ("Thu", "THU KHÁC (ngoài hợp đồng)")):
            root = QTreeWidgetItem([title])
            root.setData(0, Qt.UserRole, ("root", loai))
            f = root.font(0)
            f.setBold(True)
            root.setFont(0, f)
            self.cat_tree.addTopLevelItem(root)
            items = {}
            for cat, depth in store.category_tree(loai, self.show_hidden.isChecked()):
                parent = items.get(cat["MaKhoanMucCha"], root)
                it = QTreeWidgetItem([cat["TenKhoanMuc"], cat["ApDungCho"], "" if cat["DangSuDung"] else "Đã ẩn"])
                it.setData(0, Qt.UserRole, ("cat", cat["Ma"], loai))
                parent.addChild(it)
                items[cat["Ma"]] = it
            root.setExpanded(True)
        self.cat_tree.expandAll()

    def _cat_selected(self):
        it = self.cat_tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _cat_add(self, child: bool):
        sel = self._cat_selected()
        if not sel:
            return info(self, "Chọn KHOẢN CHI / THU KHÁC hoặc một mục trước.")
        loai = sel[1] if sel[0] == "root" else sel[2]
        parent = sel[1] if child and sel[0] == "cat" else None
        if child and not parent:
            return info(self, "Chọn mục cha trước khi thêm mục con.")
        dlg = FormDialog("Khoản mục", [{"key": "ten", "label": "Tên mục", "required": True},
                                       {"key": "ap", "label": "Áp dụng cho", "kind": "combo",
                                        "options": ["Cả hai", "Văn phòng", "Dự án"]}], {"ap": "Cả hai"}, self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            if run_safely(self, self.ctx.finance.add_category, v["ten"], loai, parent, v["ap"]):
                self.ctx.finance.save()
                self.reload_categories()

    def _cat_rename(self):
        sel = self._cat_selected()
        if sel and sel[0] == "cat":
            old = self.cat_tree.currentItem().text(0)
            name = ask_text(self, "Đổi tên", "Tên mới:", old)
            if name and name != old and confirm_changes(self, [("Tên mục", old, name)]):
                self.ctx.finance.rename_category(sel[1], name)
                self.reload()

    def _pick_category(self, loai: str, exclude: str, title: str, allow_root: bool) -> tuple[bool, str | None]:
        opts = [("(Cấp 1 – không thuộc nhóm nào)", "__root__")] if allow_root else []
        opts += [("    " * d + c["TenKhoanMuc"], c["Ma"]) for c, d in self.ctx.finance.category_tree(loai)
                 if c["Ma"] != exclude]
        dlg = FormDialog(title, [{"key": "dst", "label": "Chọn mục", "kind": "combo", "options": opts}], None, self)
        if dlg.exec() != QDialog.Accepted:
            return False, None
        dst = dlg.values()["dst"]
        return True, (None if dst == "__root__" else dst)

    def _cat_move(self):
        sel = self._cat_selected()
        if sel and sel[0] == "cat":
            ok, dst = self._pick_category(sel[2], sel[1], "Chuyển sang nhóm", True)
            if ok and run_safely(self, self.ctx.finance.move_category, sel[1], dst) is None:
                self.reload_categories()

    def _cat_toggle(self):
        sel = self._cat_selected()
        if sel and sel[0] == "cat":
            row = self.ctx.finance.fdb.get("KhoanMuc", sel[1])
            self.ctx.finance.set_category_visible(sel[1], not row["DangSuDung"])
            self.reload_categories()

    def _cat_merge(self):
        sel = self._cat_selected()
        if sel and sel[0] == "cat":
            ok, dst = self._pick_category(sel[2], sel[1], "Gộp vào mục", False)
            if ok and dst and confirm(self, "Chuyển toàn bộ phiếu sang mục đã chọn và ẩn mục hiện tại?"):
                n = self.ctx.finance.merge_categories(sel[1], dst)
                info(self, f"Đã chuyển {n} phiếu.")
                self.reload()

    def _cat_delete(self):
        sel = self._cat_selected()
        if sel and sel[0] == "cat" and confirm(self, "Xóa mục này?"):
            run_safely(self, self.ctx.finance.delete_category, sel[1])
            self.reload_categories()

    # ---- quỹ
    def _tab_funds(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(toolbar(button("Thêm quỹ", self._fund_add), button("Sửa", self._fund_edit),
                              button("Đặt làm quỹ nhận tiền chuyển khoản", lambda: self._fund_default("quy_ngan_hang")),
                              button("Đặt làm quỹ nhận tiền mặt", lambda: self._fund_default("quy_tien_mat")),
                              "stretch"))
        self.funds = DataTable([("TenQuy", "Quỹ", "text"), ("LoaiQuy", "Loại", "text"),
                                ("TonQuy", "Tồn hôm nay", "money")])
        lay.addWidget(self.funds)
        lay.addWidget(hint("Khoản thu hợp đồng cộng vào quỹ theo hình thức nhận: chuyển khoản → quỹ ngân hàng mặc định, "
                           "tiền mặt → quỹ tiền mặt mặc định."))
        return w

    def _fund_fields(self):
        return [{"key": "TenQuy", "label": "Tên quỹ", "required": True},
                {"key": "LoaiQuy", "label": "Loại", "kind": "combo", "options": ["Tiền mặt", "Ngân hàng"]},
                {"key": "SoDuBanDau", "label": "Số dư ban đầu", "kind": "money"},
                {"key": "NgaySoDuBanDau", "label": "Ngày chốt số dư", "kind": "date"}]

    def _fund_add(self):
        dlg = FormDialog("Quỹ tiền", self._fund_fields(), {"LoaiQuy": "Ngân hàng"}, self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            run_safely(self, self.ctx.finance.add_fund, v["TenQuy"], v["LoaiQuy"], v["SoDuBanDau"], v["NgaySoDuBanDau"])
            self.reload()

    def _fund_edit(self):
        r = self.funds.current()
        if not r:
            return
        row = dict(self.ctx.finance.fdb.get("QuyTien", r["Ma"]))
        dlg = FormDialog("Quỹ tiền", self._fund_fields(), row, self)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.finance.update_fund(r["Ma"], dlg.values())
            self.reload()

    def _fund_default(self, key):
        r = self.funds.current()
        if r:
            self.ctx.finance.set_setting(key, r["Ma"])
            self.ctx.finance.save()
            info(self, f"Đã đặt {r['TenQuy']} làm quỹ mặc định.")
            self.reload()

    # ---- báo cáo
    def _tab_reports(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.r_kind = Combo([("Sổ thu chi theo ngày / khoảng ngày", "ngay"), ("Báo cáo tháng", "thang"),
                             ("Báo cáo quý", "quy"), ("Báo cáo năm", "nam"), ("Tổng hợp chi theo khoản mục", "chi"),
                             ("Thu - chi theo dự án", "du_an")], "thang")
        self.r_ref = DateEdit()
        self.r_tu, self.r_den = DateEdit(), DateEdit()
        self.r_kind.currentIndexChanged.connect(self._r_period)
        self.r_ref.dateChanged.connect(self._r_period)
        self.r_pw = QCheckBox("Đặt mật khẩu mở file Excel")
        self.r_pw.setChecked(True)
        lay.addLayout(toolbar(QLabel("Loại báo cáo"), self.r_kind, QLabel("Kỳ chứa ngày"), self.r_ref, QLabel("Từ"),
                              self.r_tu, QLabel("đến"), self.r_den, "stretch"))
        b_export = button("Xuất Excel…", self._r_export, "Primary")
        b_export.setEnabled(self.ctx.can("ThuChi", "Xuat"))
        lay.addLayout(toolbar(button("Xem trước", self._r_preview), b_export, self.r_pw, "stretch"))
        self.r_table = DataTable([])
        lay.addWidget(self.r_table, 1)
        self._r_period()
        return w

    def _r_period(self):
        kind = self.r_kind.value()
        ref = date.fromisoformat(self.r_ref.value())
        if kind in ("ngay", "thang", "quy", "nam"):
            a, b = period_range(kind, ref)
        else:
            a, b = period_range("nam", ref)
        self.r_tu.set_value(a)
        self.r_den.set_value(b)

    def _r_reports(self):
        fr = FinanceReports(self.ctx.finance, self.ctx.db)
        return fr.package(self.r_kind.value(), self.r_tu.value(), self.r_den.value())

    def _r_preview(self):
        reps = self._r_reports()
        rep = reps[0]
        rows = list(rep.rows) + ([dict(rep.total, _kieu="tong")] if rep.total else [])
        self.r_table.set_rows(rows, rep.columns)

    def _r_export(self):
        reps = self._r_reports()
        name = f"ThuChi_{self.r_kind.value()}_{self.r_tu.value()}_{self.r_den.value()}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Xuất Excel thu chi", name, "Excel (*.xlsx)")
        if not path:
            return
        password = None
        if self.r_pw.isChecked():
            password = ask_secret(self, "Mật khẩu file", "Mật khẩu để mở file Excel (người nhận cần mật khẩu này):")
            if not password:
                return
        export_xlsx(reps, path, company=self.ctx.company, user=self.ctx.user["HoTen"], password=password)
        self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xuất Excel thu chi", path)
        info(self, f"Đã xuất file:\n{path}")

    # ---- xóa dữ liệu
    def _tab_delete(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g1 = QGroupBox("Xóa theo khoảng thời gian")
        l1 = QVBoxLayout(g1)
        self.d_tu, self.d_den = DateEdit(), DateEdit()
        l1.addLayout(toolbar(QLabel("Từ"), self.d_tu, QLabel("đến"), self.d_den,
                             button("Xem số phiếu sẽ xóa", self._d_preview), button("Xóa…", self._d_range, "Danger"),
                             "stretch"))
        self.d_info = hint("")
        l1.addWidget(self.d_info)
        lay.addWidget(g1)
        g2 = QGroupBox("Xóa toàn bộ dữ liệu thu chi (chỉ Giám đốc)")
        l2 = QVBoxLayout(g2)
        self.keep_cat = QCheckBox("Giữ lại danh mục khoản mục và quỹ")
        self.keep_cat.setChecked(True)
        b = button("Xóa toàn bộ…", self._d_all, "Danger")
        b.setEnabled(self.ctx.user["VaiTro"] == "Giám đốc")
        l2.addLayout(toolbar(self.keep_cat, b, "stretch"))
        l2.addWidget(hint("Dữ liệu bị xóa được ghi đè và thu gọn tệp, không khôi phục được bằng công cụ cứu dữ liệu. "
                          "Bản sao lưu cũ trong thư mục SaoLuu vẫn còn (vẫn được mã hóa). Nên xuất Excel trước khi xóa.",
                          "Warn"))
        lay.addWidget(g2)
        lay.addStretch(1)
        for b in g1.findChildren(QPushButton) + g2.findChildren(QPushButton):
            if b.text().startswith("Xóa"):
                b.setEnabled(b.isEnabled() and self.ctx.can("ThuChi", "Xoa"))
        return w

    def _d_preview(self):
        p = self.ctx.finance.range_preview(self.d_tu.value(), self.d_den.value())
        self.d_info.setText(f"{p['so_phieu']} phiếu (thu khác {fmt_money(p['thu'])} đ, chi {fmt_money(p['chi'])} đ) "
                            f"từ {fmt_date(self.d_tu.value())} đến {fmt_date(self.d_den.value())}.")
        return p

    def _d_range(self):
        p = self._d_preview()
        if not p["so_phieu"]:
            return info(self, "Không có phiếu nào trong khoảng này.")
        if not confirm(self, f"Xóa vĩnh viễn {p['so_phieu']} phiếu? Nên xuất Excel trước."):
            return
        key = ask_secret(self, "Xác nhận", "Nhập lại Khóa thu chi để xóa:")
        if key is None:
            return
        n = run_safely(self, self.ctx.finance.delete_range, self.d_tu.value(), self.d_den.value(), key)
        if n is not None:
            info(self, f"Đã xóa {n} phiếu.")
            self.reload()

    def _d_all(self):
        typed = ask_text(self, "Xóa toàn bộ", 'Gõ chữ "XOA TOAN BO" để xác nhận:')
        if not typed:
            return
        key = ask_secret(self, "Xác nhận", "Nhập lại Khóa thu chi:")
        if key is None:
            return
        if run_safely(self, self.ctx.finance.delete_all, self.ctx.user, key, typed, self.keep_cat.isChecked()) is None \
                and not self.ctx.finance.vouchers():
            info(self, "Đã xóa toàn bộ dữ liệu thu chi.")
        self.reload()

    # ---- bảo mật
    def _tab_security(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Khóa thu chi của tôi")
        gl = QHBoxLayout(g)
        gl.addWidget(button("Đổi Khóa thu chi…", self._change_key))
        gl.addStretch(1)
        lay.addWidget(g)
        if self.ctx.user["VaiTro"] == "Giám đốc":
            g2 = QGroupBox("Đặt Khóa thu chi cho Người quản lý")
            l2 = QHBoxLayout(g2)
            self.mgr = Combo([(u["HoTen"] + f" ({u['TenDangNhap']})", u["TenDangNhap"]) for u in self.ctx.acc.users()
                              if u["VaiTro"] == "Người quản lý"])
            self.mgr_name = QLineEdit()
            self.mgr_name.setPlaceholderText("hoặc gõ tên đăng nhập trên máy của Người quản lý")
            l2.addWidget(self.mgr)
            l2.addWidget(self.mgr_name)
            l2.addWidget(button("Đặt khóa…", self._set_mgr_key))
            lay.addWidget(g2)
            lay.addWidget(hint("Khóa đặt cho Người quản lý đi kèm Gói Giám đốc khi đồng bộ sang máy của họ."))
        g3 = QGroupBox("Tự khóa")
        l3 = QHBoxLayout(g3)
        self.auto_min = QSpinBox()
        self.auto_min.setRange(1, 120)
        self.auto_min.setValue(self.auto_lock_minutes())
        self.auto_min.valueChanged.connect(lambda v: (self.ctx.finance.set_setting("tu_khoa_phut", str(v)),
                                                      self.ctx.finance.save()))
        l3.addWidget(QLabel("Tự khóa module sau"))
        l3.addWidget(self.auto_min)
        l3.addWidget(QLabel("phút không thao tác (và khi thu nhỏ cửa sổ, khi đăng xuất)."))
        l3.addStretch(1)
        lay.addWidget(g3)
        lay.addStretch(1)
        return w

    def _change_key(self):
        old = ask_secret(self, "Đổi khóa", "Khóa thu chi hiện tại:")
        if old is None:
            return
        try:
            fin._unwrap(self.ctx.db, self.ctx.user["TenDangNhap"], old)
        except FinanceError as e:
            return error(self, str(e))
        new = ask_secret(self, "Đổi khóa", "Khóa mới (≥ 8 ký tự):")
        new2 = ask_secret(self, "Đổi khóa", "Nhập lại khóa mới:") if new else None
        if not new or new != new2:
            return error(self, "Hai lần nhập khóa mới không khớp.")
        if run_safely(self, self.ctx.finance.set_user_key, self.ctx.user["TenDangNhap"], new) is None:
            info(self, "Đã đổi Khóa thu chi.")

    def _set_mgr_key(self):
        name = self.mgr_name.text().strip() or self.mgr.value()
        if not name:
            return info(self, "Chọn hoặc nhập tên đăng nhập của Người quản lý.")
        key = ask_secret(self, "Khóa thu chi", f"Khóa thu chi cho {name} (≥ 8 ký tự):")
        if key and run_safely(self, self.ctx.finance.set_user_key, name, key) is None:
            info(self, f"Đã đặt Khóa thu chi cho {name}. Báo khóa này trực tiếp cho người đó.")


__all__ = ["FinancePage", "VoucherDialog", "RecoveryCodeDialog", "QPlainTextEdit"]
