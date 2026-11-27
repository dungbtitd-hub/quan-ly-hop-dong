"""Tài khoản: Giám đốc cấp mã kích hoạt cho nhân viên / máy Giám đốc thứ hai, phân quyền, thu hồi."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from ..core.auth import ACTIONS, FEATURES, ROLES, AuthError, default_permissions
from .widgets import (Combo, DataTable, DateEdit, button, confirm, confirm_delete, copy_text, error, hint, info, page_title,
                      toolbar)

FEATURE_LABELS = {"HopDong": "Hợp đồng", "HoaDonDauRa": "Hóa đơn đầu ra / thanh toán", "HoaDonDauVao": "Hóa đơn đầu vào",
                  "TaiSan": "Tài sản, CCDC (phân loại hóa đơn)",
                  "BaoCao": "Báo cáo", "DanhMuc": "Danh mục", "SaoLuu": "Sao lưu", "DongBo": "Đồng bộ",
                  "ThuChi": "Thu chi (bảo mật)", "TaiKhoan": "Tài khoản"}
ACTION_LABELS = {"Xem": "Xem", "Them": "Thêm", "Sua": "Sửa", "Xoa": "Xóa", "Xuat": "Xuất"}


class IssueCodeDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Cấp mã kích hoạt")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.username = QLineEdit()
        self.fullname = QLineEdit()
        self.role = Combo(list(ROLES), "Kế toán viên")
        self.machine = QLineEdit()
        self.machine.setPlaceholderText("QLHD-XXXX-XXXX-XXXX-XXXX-XXXX (lấy ở màn hình Kích hoạt của máy đó)")
        self.expiry = DateEdit(None, optional=True)
        form.addRow("Tên đăng nhập *", self.username)
        form.addRow("Họ tên *", self.fullname)
        form.addRow("Vai trò *", self.role)
        form.addRow("Mã máy *", self.machine)
        form.addRow("Hết hạn (để trống = vĩnh viễn)", self.expiry)
        lay.addLayout(form)
        self.role_hint = hint("")
        lay.addWidget(self.role_hint)
        box = QGroupBox("Phân quyền")
        grid = QGridLayout(box)
        self.checks: dict[tuple[str, str], QCheckBox] = {}
        for j, a in enumerate(ACTIONS):
            grid.addWidget(QLabel(ACTION_LABELS[a]), 0, j + 1, Qt.AlignCenter)
        for i, f in enumerate(FEATURES, start=1):
            grid.addWidget(QLabel(FEATURE_LABELS[f]), i, 0)
            for j, a in enumerate(ACTIONS):
                c = QCheckBox()
                self.checks[(f, a)] = c
                grid.addWidget(c, i, j + 1, Qt.AlignCenter)
        lay.addWidget(box)
        self.role.currentIndexChanged.connect(self._apply_role)
        self._apply_role()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Cấp mã")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._issue)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.code = None

    def _apply_role(self):
        role = self.role.value()
        perms = default_permissions(role)
        for (f, a), c in self.checks.items():
            c.setChecked(bool(perms[f][a]))
            locked = (f == "ThuChi" and role == "Kế toán viên") or (f == "TaiKhoan" and role != "Giám đốc")
            c.setEnabled(not locked)
        self.role_hint.setText({
            "Kế toán viên": "Kế toán viên chỉ làm phần hợp đồng, hóa đơn, thanh toán; không bao giờ được cấp quyền Thu chi.",
            "Người quản lý": "Người quản lý xem được Thu chi sau khi Giám đốc đặt Khóa thu chi cho họ.",
            "Giám đốc": "Dùng cho máy Giám đốc thứ hai (theo dõi song song): đặt cùng tên đăng nhập Giám đốc, "
                        "sau đó gửi Gói Giám đốc từ mục Đồng bộ để chuyển cả dữ liệu thu chi.",
        }[role])
        if role == "Giám đốc" and not self.username.text():
            self.username.setText(self.ctx.user["TenDangNhap"])
            self.fullname.setText(self.ctx.user["HoTen"])

    def _issue(self):
        if not self.username.text().strip() or not self.fullname.text().strip() or not self.machine.text().strip():
            return error(self, "Nhập đủ tên đăng nhập, họ tên và Mã máy.")
        perms: dict = {}
        for (f, a), c in self.checks.items():
            perms.setdefault(f, {})[a] = int(c.isChecked())
        try:
            self.code = self.ctx.acc.issue_code(ten_dang_nhap=self.username.text().strip(),
                                                ho_ten=self.fullname.text().strip(), vai_tro=self.role.value(),
                                                machine_id=self.machine.text().strip(), quyen=perms,
                                                het_han=self.expiry.value())
        except AuthError as e:
            return error(self, str(e))
        self.accept()


class CodeResultDialog(QDialog):
    def __init__(self, code: str, who: str, parent=None, title: str = "Mã kích hoạt", note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"Gửi mã này cho {who}. Mã chỉ dùng được trên đúng máy đã nhập Mã máy. {note}".strip()))
        box = QPlainTextEdit(code)
        box.setReadOnly(True)
        lay.addWidget(box)
        lay.addLayout(toolbar(button("Sao chép", lambda: (copy_text(code), info(self, "Đã sao chép."))),
                              button("Lưu ra file…", lambda: self._save(code)), "stretch",
                              button("Đóng", self.accept, "Primary")))

    def _save(self, code):
        path, _ = QFileDialog.getSaveFileName(self, "Lưu mã", "Ma_kich_hoat.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(code + "\n")


class WipeOrderDialog(QDialog):
    """Giám đốc cấp lệnh xóa sạch dữ liệu cho một máy nhân viên (VD máy kế toán nghỉ việc)."""

    def __init__(self, ctx, machine: str = "", parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.token = None
        self.setWindowTitle("Lệnh xóa dữ liệu máy khác")
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Lệnh có chữ ký của Giám đốc, chỉ dùng được cho đúng Mã máy đã nhập, trong số ngày hiệu lực, "
                           "và chỉ một lần. Thực hiện bằng một trong hai cách: dán lệnh ở màn hình đăng nhập của máy "
                           "đó (nút \"Giám đốc can thiệp\", không cần mật khẩu của nhân viên), hoặc để lệnh tự đi kèm "
                           "các gói đồng bộ gửi từ máy này: máy đó mở gói là dữ liệu bị xóa ngay."))
        form = QFormLayout()
        opts = [("(Nhập Mã máy bên dưới)", "")] + [
            (f"{r['HoTen']} ({r['TenDangNhap']}, {r['VaiTro']}) – {r['MaMay']}", r["MaMay"])
            for r in ctx.db.query("SELECT * FROM MaDaCap ORDER BY NgayCap DESC") if r["MaMay"]]
        self.pick = Combo(opts, machine if any(m == machine for _, m in opts) else "")
        self.machine = QLineEdit(machine)
        self.machine.setPlaceholderText("QLHD-XXXX-XXXX-XXXX-XXXX-XXXX")
        self.pick.currentIndexChanged.connect(lambda _: self.pick.value() and self.machine.setText(self.pick.value()))
        self.days = QSpinBox()
        self.days.setRange(1, 90)
        self.days.setValue(7)
        self.days.setSuffix(" ngày")
        self.keep = QCheckBox("Giữ bản quyền và tài khoản trên máy đó (chỉ xóa dữ liệu)")
        self.with_sync = QCheckBox("Gửi kèm lệnh trong các gói đồng bộ xuất từ máy này cho đến khi hết hạn")
        self.with_sync.setChecked(True)
        form.addRow("Máy đã cấp mã", self.pick)
        form.addRow("Mã máy cần xóa *", self.machine)
        form.addRow("Hiệu lực", self.days)
        lay.addLayout(form)
        lay.addWidget(self.keep)
        lay.addWidget(hint("Không giữ thì máy đó trở về chưa kích hoạt; nên thu hồi luôn mã của nhân viên đó."))
        lay.addWidget(self.with_sync)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Tạo lệnh xóa")
        bb.button(QDialogButtonBox.Ok).setObjectName("Danger")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._issue)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _issue(self):
        m = self.machine.text().strip()
        if not m:
            return error(self, "Chọn máy hoặc nhập Mã máy cần xóa dữ liệu.")
        if not confirm_delete(self, f"sạch toàn bộ dữ liệu trên máy {m} (tạo lệnh xóa)",
                              ["Máy đó xóa dữ liệu khi nhận lệnh, không khôi phục được.",
                               "Giữ bản quyền trên máy đó." if self.keep.isChecked() else "Xóa cả bản quyền."]):
            return
        try:
            self.token, self.order = self.ctx.acc.issue_wipe_order(
                m, keep_license=self.keep.isChecked(), valid_days=self.days.value(),
                send_with_sync=self.with_sync.isChecked())
        except AuthError as e:
            return error(self, str(e))
        self.accept()


class AccountsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Tài khoản và cấp mã"))
        primary = ctx.acc.is_primary_director_machine()
        b_issue = button("Cấp mã kích hoạt…", self.issue, "Primary")
        b_revoke = button("Thu hồi mã đã chọn", self.revoke, "Danger")
        b_renew = button("Gia hạn bản quyền…", self.renew)
        b_copy_key = button("Sao chép Key cho máy nhân viên", self.copy_key)
        b_wipe = button("Lệnh xóa dữ liệu máy khác…", self.wipe_order, "Danger", "trash")
        for b in (b_issue, b_revoke, b_copy_key, b_wipe):
            b.setEnabled(primary)
        lay.addLayout(toolbar(b_issue, b_revoke, b_wipe, "stretch", b_renew, b_copy_key))
        if not primary:
            lay.addWidget(hint("Máy này là máy Giám đốc thứ hai: chỉ xem danh sách; việc cấp và thu hồi mã làm trên "
                               "máy Giám đốc chính."))
        self.quota = hint("")
        lay.addWidget(self.quota)
        self.b_cancel_wipe = button("Hủy các lệnh xóa chờ gửi", self.cancel_wipe, "Link")
        self.wipe_note = hint("", "Warn")
        self.wipe_note.setWordWrap(False)
        lay.addLayout(toolbar(self.wipe_note, self.b_cancel_wipe, "stretch"))
        lay.addWidget(QLabel("Mã đã cấp"))
        self.codes = DataTable([("TenDangNhap", "Tên đăng nhập", "text"), ("HoTen", "Họ tên", "text"),
                                ("VaiTro", "Vai trò", "text"), ("MaMay", "Mã máy", "text"), ("NgayCap", "Ngày cấp", "text"),
                                ("HetHan", "Hết hạn", "date"), ("TrangThai", "Trạng thái", "text")])
        lay.addWidget(self.codes)
        lay.addWidget(QLabel("Tài khoản trên máy này"))
        self.users = DataTable([("TenDangNhap", "Tên đăng nhập", "text"), ("HoTen", "Họ tên", "text"),
                                ("VaiTro", "Vai trò", "text"), ("TrangThai", "Trạng thái", "text"),
                                ("LanDangNhapCuoi", "Đăng nhập cuối", "text")])
        lay.addWidget(self.users)
        lay.addWidget(hint("Thu hồi mã: tài khoản bị khóa trên máy nhân viên ở lần đồng bộ kế tiếp "
                           "(danh sách thu hồi có chữ ký Giám đốc đi kèm mọi gói đồng bộ)."))

    def refresh(self):
        rows = [dict(r, NgayCap=r["NgayCap"][:10], TrangThai="Đã thu hồi" if r["DaThuHoi"] else "Đang dùng")
                for r in self.ctx.db.query("SELECT * FROM MaDaCap ORDER BY NgayCap DESC")]
        self.codes.set_rows(rows)
        self.users.set_rows(self.ctx.acc.users())
        lic = self.ctx.acc.license()
        used = sum(1 for r in rows if r["TrangThai"] == "Đang dùng")
        st = self.ctx.acc.license_status()
        han = ("vĩnh viễn" if not st["han"] else
               f"đến {st['han'][8:10]}/{st['han'][5:7]}/{st['han'][:4]} (còn {st['con_lai']} ngày)")
        orders = self.ctx.acc.pending_wipe_orders()
        self.wipe_note.setText("Lệnh xóa đang chờ gửi kèm gói đồng bộ: " + ", ".join(
            f"máy {o['may']} (đến {o['han'][8:10]}/{o['han'][5:7]})" for o in orders) if orders else "")
        self.wipe_note.setVisible(bool(orders))
        self.b_cancel_wipe.setVisible(bool(orders))
        self.quota.setText(f"Đã dùng {used}/{lic['SoTaiKhoanToiDa']} tài khoản theo Key của {lic['TenCongTy']}. "
                           f"Thời hạn bản quyền: {han}.")

    def renew(self):
        from .auth_dialogs import RenewDialog

        if RenewDialog(self.ctx, self).exec() == QDialog.Accepted:
            self.refresh()

    def copy_key(self):
        copy_text(self.ctx.acc.license()["KeyGiamDoc"])
        info(self, "Đã sao chép Key Giám đốc hiện hành. Máy nhân viên đã hết hạn dán Key này ở màn hình Gia hạn "
                   "bản quyền. Máy còn dùng được thì chỉ cần nhận một gói đồng bộ từ máy này là tự gia hạn.")

    def issue(self):
        dlg = IssueCodeDialog(self.ctx, self)
        if dlg.exec() == QDialog.Accepted and dlg.code:
            CodeResultDialog(dlg.code, dlg.fullname.text(), self).exec()
            self.refresh()

    def wipe_order(self):
        r = self.codes.current()
        dlg = WipeOrderDialog(self.ctx, r["MaMay"] if r else "", self)
        if dlg.exec() == QDialog.Accepted and dlg.token:
            CodeResultDialog(dlg.token, "người đang ở máy cần xóa (hoặc tự dán ở màn hình đăng nhập máy đó)", self,
                             title="Lệnh xóa dữ liệu",
                             note=f"Lệnh dùng được cho máy {dlg.order['may']} đến hết ngày "
                                  f"{dlg.order['han'][8:10]}/{dlg.order['han'][5:7]}/{dlg.order['han'][:4]}.").exec()
            self.refresh()

    def cancel_wipe(self):
        if confirm(self, "Hủy các lệnh xóa đang chờ gửi kèm gói đồng bộ? Lệnh đã sao chép ra ngoài vẫn dùng được "
                         "đến khi hết hạn."):
            self.ctx.acc.cancel_wipe_orders()
            self.refresh()

    def revoke(self):
        r = self.codes.current()
        if r and r["TrangThai"] == "Đang dùng" and confirm(self, f"Thu hồi mã của {r['HoTen']} ({r['TenDangNhap']})?"):
            self.ctx.acc.revoke(r["MaKichHoat"])
            self.refresh()
