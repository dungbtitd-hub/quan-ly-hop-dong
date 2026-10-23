"""Màn hình Đồng bộ: xuất gói, nhận gói & so sánh, Gói Giám đốc, lịch sử, hoàn tác."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QRadioButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import finance as fin
from ..core.finance import FinanceError, FinanceStore
from ..core.reports import fmt_date
from ..core.sync import (A_BOTH, A_KEEP, A_MERGE, A_SKIP, A_TAKE, ACTION_LABELS, PKG_EXT, ST_CONFLICT, ST_DUP,
                         ST_SAME, SyncError, field_label)
from . import style
from .widgets import (CheckList, Combo, DataTable, DateEdit, ask_secret, button, confirm, error, hint, info,
                      page_title, toolbar)

STATUS_COLORS = {ST_CONFLICT: "danger", ST_DUP: "warn", ST_SAME: "muted"}


def _pw() -> QLineEdit:
    w = QLineEdit()
    w.setEchoMode(QLineEdit.Password)
    return w


class SyncPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.pkg = None
        self.items = []
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Đồng bộ dữ liệu giữa các máy"))
        tabs = QTabWidget()
        tabs.addTab(self._tab_export(), "Xuất gói (máy gửi)")
        tabs.addTab(self._tab_import(), "Nhận gói (máy nhận)")
        tabs.addTab(self._tab_history(), "Lịch sử && hoàn tác")
        lay.addWidget(tabs)
        can_export = ctx.can("DongBo", "Xuat")
        self.b_export.setEnabled(can_export)
        self.b_open.setEnabled(ctx.can("DongBo", "Them"))

    # ------------------------------------------------------------ xuất
    def _tab_export(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Máy nhận")
        gl = QFormLayout(g)
        self.partner = Combo()
        self.partner_name = QLineEdit()
        self.partner_name.setPlaceholderText("VD: Máy Giám đốc, Máy kế toán Lan")
        gl.addRow("Gửi cho máy", self.partner)
        gl.addRow("Tên máy nhận (nếu là máy mới)", self.partner_name)
        self.my_name = QLineEdit()
        self.my_name.editingFinished.connect(lambda: self.ctx.db.set_setting("ten_may", self.my_name.text().strip()))
        gl.addRow("Tên máy này", self.my_name)
        lay.addWidget(g)
        g2 = QGroupBox("Phạm vi")
        l2 = QVBoxLayout(g2)
        self.sc_all = QRadioButton("Toàn bộ dữ liệu (dùng khi cài máy mới)")
        self.sc_changes = QRadioButton("Các thay đổi từ lần gửi trước cho máy này (khuyên dùng hằng ngày)")
        self.sc_contracts = QRadioButton("Chỉ các hợp đồng được tích chọn")
        self.sc_all.setChecked(True)
        for rb in (self.sc_all, self.sc_changes, self.sc_contracts):
            l2.addWidget(rb)
        self.contract_list = CheckList()
        self.contract_list.setMaximumHeight(150)
        l2.addWidget(self.contract_list)
        self.with_direct = QCheckBox("Kèm hóa đơn đầu vào không có hợp đồng trong khoảng")
        self.d_tu, self.d_den = DateEdit(f"{date.today().year}-01-01"), DateEdit()
        l2.addLayout(toolbar(self.with_direct, self.d_tu, QLabel("–"), self.d_den, "stretch"))
        lay.addWidget(g2)
        g3 = QGroupBox("Bảo mật")
        l3 = QFormLayout(g3)
        self.pw1, self.pw2 = _pw(), _pw()
        l3.addRow("Mật khẩu đồng bộ (≥ 6 ký tự)", self.pw1)
        l3.addRow("Nhập lại", self.pw2)
        self.director_pkg = QCheckBox("Gói Giám đốc: kèm toàn bộ dữ liệu thu chi (vẫn mã hóa) để máy Giám đốc thứ hai "
                                      "theo dõi song song")
        l3.addRow(self.director_pkg)
        self.director_hint = hint("", "Hint")
        l3.addRow(self.director_hint)
        lay.addWidget(g3)
        self.b_export = button("Xuất gói…", self.export, "Primary")
        lay.addLayout(toolbar(self.b_export, "stretch"))
        lay.addWidget(hint("Gói được nén và mã hóa bằng mật khẩu đồng bộ của công ty. Gói thường không bao giờ chứa "
                           "dữ liệu thu chi. Chuyển file .qlhd sang máy kia bằng USB, email, Zalo…"))
        lay.addStretch(1)
        return w

    def refresh(self):
        self.partner.set_options([("(Máy mới / chưa có trong danh sách)", None)] +
                                 [(p["TenMay"], p["MaMay"]) for p in self.ctx.sync.partners() if p["MaMay"]])
        self.my_name.setText(self.ctx.sync.machine_name())
        self.contract_list.set_options([(f"{h['SoHopDong']} – {h['TenHopDong']} ({h['ChieuHopDong']})", h["Ma"])
                                        for h in self.ctx.cs.contracts()], self.contract_list.values())
        ok = self.ctx.user["VaiTro"] == "Giám đốc" and self.ctx.finance is not None
        self.director_pkg.setEnabled(ok)
        if not ok:
            self.director_pkg.setChecked(False)
        self.director_hint.setText("" if ok else "Gói Giám đốc chỉ xuất được khi đăng nhập Giám đốc và đã mở khóa Thu chi.")
        self.history.set_rows(self.ctx.sync.history())
        self.b_undo.setEnabled(self.ctx.sync.can_undo())

    def export(self):
        if self.pw1.text() != self.pw2.text():
            return error(self, "Hai lần nhập mật khẩu đồng bộ không khớp.")
        scope = "all" if self.sc_all.isChecked() else "changes" if self.sc_changes.isChecked() else "contracts"
        ids = self.contract_list.values()
        if scope == "contracts" and not ids:
            return error(self, "Tích chọn ít nhất một hợp đồng.")
        partner = self.partner.value()
        partner_name = self.partner.currentText() if partner else (self.partner_name.text().strip() or None)
        is_director = self.director_pkg.isChecked()
        default = f"{'GoiGiamDoc' if is_director else 'DongBo'}_{self.ctx.sync.machine_name()}_{date.today():%Y%m%d}{PKG_EXT}"
        path, _ = QFileDialog.getSaveFileName(self, "Lưu gói đồng bộ", default.replace(" ", "_"),
                                              f"Gói đồng bộ (*{PKG_EXT})")
        if not path:
            return
        try:
            h = self.ctx.sync.export_package(
                path, self.pw1.text(), user=self.ctx.user, scope=scope, partner=partner, partner_name=partner_name,
                contract_ids=ids, in_invoice_from=self.d_tu.value() if self.with_direct.isChecked() else None,
                in_invoice_to=self.d_den.value() if self.with_direct.isChecked() else None,
                finance=self.ctx.finance if is_director else None)
        except SyncError as e:
            return error(self, str(e))
        counts = ", ".join(f"{k}: {v}" for k, v in h["so_luong"].items()) or "không có bản ghi"
        info(self, f"Đã xuất gói:\n{path}\n\n{h['pham_vi']} – {counts}" +
             ("\n\nGói có kèm dữ liệu thu chi (đã mã hóa)." if is_director else ""))
        self.refresh()

    # ------------------------------------------------------------ nhận
    def _tab_import(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.b_open = button("Chọn file gói…", self.open_package, "Primary")
        self.pkg_info = hint("Chưa chọn gói.")
        lay.addLayout(toolbar(self.b_open, "stretch"))
        lay.addWidget(self.pkg_info)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Nội dung", "Trạng thái", "Chi tiết (trường khác nhau, lý do nghi trùng)", "Xử lý"])
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 360)
        lay.addWidget(self.tree, 1)
        self.hide_same = QCheckBox("Ẩn các dòng giống nhau")
        self.hide_same.setChecked(True)
        self.hide_same.toggled.connect(self._fill_tree)
        self.b_all = button("Đồng bộ tất cả", self.apply_all, "Primary")
        self.b_sel = button("Đồng bộ hợp đồng đã tích", self.apply_selected)
        self.b_all.setEnabled(False)
        self.b_sel.setEnabled(False)
        lay.addLayout(toolbar(self.hide_same, "stretch", self.b_sel, self.b_all))
        self.counts = hint("")
        lay.addWidget(self.counts)
        return w

    def open_package(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn gói đồng bộ", "", f"Gói đồng bộ (*{PKG_EXT})")
        if not path:
            return
        try:
            h = self.ctx.sync.read_header(path)
        except SyncError as e:
            return error(self, str(e))
        pw = ask_secret(self, "Mật khẩu đồng bộ", f"Gói từ {h['ten_may']} ({h['nguoi_xuat']}, "
                        f"{fmt_date(h['thoi_diem'][:10])}).\nNhập mật khẩu đồng bộ:")
        if pw is None:
            return
        try:
            self.pkg = self.ctx.sync.read_package(path, pw)
        except SyncError as e:
            return error(self, str(e))
        order = self.ctx.sync.wipe_order_for_me(self.pkg)
        if order:
            from .auth_dialogs import run_wipe_order

            self.pkg = None
            if run_wipe_order(self, self.ctx, order):
                self.window().close()
            return
        if self.pkg.is_director and self.ctx.user["VaiTro"] not in fin.ALLOWED_ROLES:
            self.pkg.body.pop("finance", None)
        self.items = self.ctx.sync.compare(self.pkg)
        h = self.pkg.header
        txt = (f"Gói từ <b>{h['ten_may']}</b> – người xuất {h['nguoi_xuat']} – lúc {h['thoi_diem'][:16].replace('T', ' ')} UTC"
               f" – phạm vi: {h['pham_vi']}.")
        if self.pkg.is_director:
            txt += " <b>Gói Giám đốc (kèm thu chi đã mã hóa).</b>"
        if self.pkg.already_imported:
            txt += " <span style='color:" + style.color("warn") + "'>Gói này đã được nhận trước đây.</span>"
        self.pkg_info.setText(txt)
        self._fill_tree()
        self.b_all.setEnabled(True)
        self.b_sel.setEnabled(True)

    def _fill_tree(self):
        self.tree.clear()
        if not self.pkg:
            return
        groups: dict = {}
        names = {r["Ma"]: f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']}" for r in self.pkg.body["tables"]["HopDong"]}
        names.update({r["Ma"]: f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']}" for r in self.ctx.cs.contracts()})
        stats: dict[str, int] = {}
        for it in self.items:
            stats[it.status] = stats.get(it.status, 0) + 1
            if self.hide_same.isChecked() and it.status == ST_SAME:
                continue
            key = it.group or "__dm__"
            if key not in groups:
                label = names.get(it.group, "Danh mục và hóa đơn không theo hợp đồng") if it.group else \
                    "Danh mục và hóa đơn không theo hợp đồng"
                g = QTreeWidgetItem([label])
                g.setFlags(g.flags() | Qt.ItemIsUserCheckable)
                g.setCheckState(0, Qt.Checked)
                g.setData(0, Qt.UserRole, it.group)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[key] = g
            row = QTreeWidgetItem([it.label, it.status, it.note or ", ".join(field_label(k) for k in it.diff[:6])])
            if it.note:
                row.setToolTip(2, it.note.replace(" · ", "\n"))
            if it.status in STATUS_COLORS:
                from PySide6.QtGui import QColor
                row.setForeground(1, QColor(style.color(STATUS_COLORS[it.status])))
            groups[key].addChild(row)
            opts = {ST_DUP: [A_MERGE, A_BOTH, A_SKIP]}.get(it.status, [A_TAKE, A_KEEP] if it.local else [A_TAKE, A_SKIP])
            combo = Combo([("(chọn)", None)] + [(ACTION_LABELS[a], a) for a in opts] if it.action is None
                          else [(ACTION_LABELS[a], a) for a in opts], it.action)
            combo.currentIndexChanged.connect(lambda _=0, it=it, c=combo: setattr(it, "action", c.value()))
            self.tree.setItemWidget(row, 3, combo)
        self.tree.expandAll()
        self.counts.setText(" · ".join(f"{k}: {v}" for k, v in stats.items()))

    def _decisions(self) -> dict:
        return {(it.table, it.their_ma): it.action for it in self.items if it.action}

    def apply_all(self):
        self._apply(None)

    def apply_selected(self):
        groups = set()
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            if g.checkState(0) == Qt.Checked and g.data(0, Qt.UserRole):
                groups.add(g.data(0, Qt.UserRole))
        if not groups:
            return info(self, "Tích chọn ít nhất một hợp đồng.")
        self._apply(groups)

    def _apply(self, groups):
        if not self.pkg:
            return
        prefer = None
        pending = [it for it in self.items if it.status == ST_CONFLICT and not it.action
                   and (groups is None or it.group in groups)]
        if pending:
            dlg = QDialog(self)
            dlg.setWindowTitle("Xử lý xung đột")
            dl = QVBoxLayout(dlg)
            dl.addWidget(QLabel(f"Có {len(pending)} dòng xung đột chưa chọn cách xử lý (cả hai máy cùng sửa)."))
            r1, r2 = QRadioButton("Ưu tiên bên gửi"), QRadioButton("Ưu tiên máy này")
            r2.setChecked(True)
            dl.addWidget(r1)
            dl.addWidget(r2)
            row = QHBoxLayout()
            ok = button("Tiếp tục", dlg.accept, "Primary")
            row.addStretch(1)
            row.addWidget(ok)
            row.addWidget(button("Hủy", dlg.reject))
            dl.addLayout(row)
            if dlg.exec() != QDialog.Accepted:
                return
            prefer = "gui" if r1.isChecked() else "may_nay"
        unsure = [it for it in self.items if it.status == ST_DUP and not it.action
                  and (groups is None or it.group is None or it.group in groups)]
        if unsure:
            dlg = QDialog(self)
            dlg.setWindowTitle("Hóa đơn nghi trùng")
            dl = QVBoxLayout(dlg)
            dl.addWidget(QLabel(f"Có {len(unsure)} dòng nghi trùng (khớp một phần với dữ liệu máy này) chưa chọn cách "
                                "xử lý:\n\n" + "\n".join(f"• {it.label}: {it.note}" for it in unsure[:8])
                                + (f"\n… và {len(unsure) - 8} dòng khác." if len(unsure) > 8 else "")))
            r1 = QRadioButton("Giữ cả hai (thêm thành bản ghi riêng)")
            r2 = QRadioButton("Gộp vào bản ghi ở máy này (chỉ bổ sung ô còn trống)")
            r1.setChecked(True)
            dl.addWidget(r1)
            dl.addWidget(r2)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(button("Tiếp tục", dlg.accept, "Primary"))
            row.addWidget(button("Quay lại chọn từng dòng", dlg.reject))
            dl.addLayout(row)
            if dlg.exec() != QDialog.Accepted:
                return
            for it in unsure:
                it.action = A_BOTH if r1.isChecked() else A_MERGE
        try:
            res = self.ctx.sync.apply(self.pkg, user=self.ctx.user, decisions=self._decisions(), prefer=prefer,
                                      only_groups=groups)
        except SyncError as e:
            return error(self, str(e))
        msg = res["tom_tat"] + f".\nĐã sao lưu trước khi đồng bộ: {Path(res['sao_luu']).name}"
        if res.get("thu_hoi"):
            msg += f"\nĐã áp dụng thu hồi {res['thu_hoi']} tài khoản."
        if res["loi_chi_tiet"]:
            msg += "\n\nLỗi:\n" + "\n".join(res["loi_chi_tiet"][:10])
        if self.pkg.is_director and self.pkg.body.get("finance"):
            msg += "\n\n" + self._apply_finance()
        info(self, msg, "Kết quả đồng bộ")
        self.pkg = None
        self.items = []
        self.tree.clear()
        self.pkg_info.setText("Đã đồng bộ xong.")
        self.b_all.setEnabled(False)
        self.b_sel.setEnabled(False)
        self.refresh()
        self.window().refresh_all()

    def _apply_finance(self) -> str:
        user = self.ctx.user
        if user["VaiTro"] not in fin.ALLOWED_ROLES:
            return "Bỏ qua phần thu chi (tài khoản này không có quyền thu chi)."
        n = fin.install_wrappers(self.ctx.db, self.pkg.body["finance"]["wrappers"])
        store = self.ctx.finance
        if store is None:
            if not fin.has_key_for(self.ctx.db, user["TenDangNhap"]):
                return "Gói không có Khóa thu chi cho tài khoản này, chưa nhận được phần thu chi."
            key = ask_secret(self, "Khóa thu chi", "Nhập Khóa thu chi (của tài khoản này trên máy Giám đốc chính) "
                                                   "để nhận phần thu chi:")
            if key is None:
                return "Chưa nhận phần thu chi (chưa nhập Khóa thu chi). Có thể nhận lại gói sau."
            try:
                store = FinanceStore.unlock(self.ctx.db, self.ctx.finance_path, user, key)
            except FinanceError as e:
                return f"Chưa nhận phần thu chi: {e}"
            self.ctx.finance = store
        try:
            r = self.ctx.sync.apply_finance(self.pkg, store)
        except SyncError as e:
            return f"Phần thu chi: {e}"
        return (f"Thu chi: thêm {r['them']}, cập nhật {r['cap_nhat']}, xóa {r['xoa']}"
                + (f"; nhận {n} khóa tài khoản." if n else "."))

    # ------------------------------------------------------------ lịch sử
    def _tab_history(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.b_undo = button("Hoàn tác lần nhận gói gần nhất", self.undo, "Danger")
        lay.addLayout(toolbar(self.b_undo, "stretch"))
        self.history = DataTable([("ThoiDiem", "Thời điểm", "text"), ("Chieu", "Chiều", "text"),
                                  ("TenMayDoiTac", "Máy", "text"), ("PhamVi", "Phạm vi", "text"),
                                  ("KetQua", "Kết quả", "text"), ("TenDangNhap", "Người thực hiện", "text")])
        lay.addWidget(self.history)
        return w

    def undo(self):
        if confirm(self, "Khôi phục dữ liệu về trạng thái ngay trước lần nhận gói gần nhất?"):
            self.ctx.lock_finance()
            try:
                self.ctx.sync.undo_last(self.ctx.user, self.ctx.finance_path)
            except SyncError as e:
                return error(self, str(e))
            info(self, "Đã hoàn tác. Nếu đang mở Thu chi, hãy mở khóa lại.")
            self.window().refresh_all()
