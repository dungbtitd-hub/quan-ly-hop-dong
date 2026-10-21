"""Tài khoản: Giám đốc cấp mã kích hoạt cho nhân viên / máy Giám đốc thứ hai, phân quyền, thu hồi."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget)

from ..core.auth import ACTIONS, FEATURES, ROLES, AuthError, default_permissions
from .widgets import (Combo, DataTable, DateEdit, button, confirm, copy_text, error, hint, info, page_title,
                      toolbar)

FEATURE_LABELS = {"HopDong": "Hợp đồng", "HoaDonDauRa": "Hóa đơn đầu ra / thanh toán", "HoaDonDauVao": "Hóa đơn đầu vào",
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
    def __init__(self, code: str, who: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mã kích hoạt")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"Gửi mã này cho {who}. Mã chỉ dùng được trên đúng máy đã nhập Mã máy."))
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


class AccountsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Tài khoản và cấp mã"))
        primary = ctx.acc.is_primary_director_machine()
        b_issue = button("Cấp mã kích hoạt…", self.issue, "Primary")
        b_revoke = button("Thu hồi mã đã chọn", self.revoke, "Danger")
        b_issue.setEnabled(primary)
        b_revoke.setEnabled(primary)
        lay.addLayout(toolbar(b_issue, b_revoke, "stretch"))
        if not primary:
            lay.addWidget(hint("Máy này là máy Giám đốc thứ hai: chỉ xem danh sách; việc cấp và thu hồi mã làm trên "
                               "máy Giám đốc chính."))
        self.quota = hint("")
        lay.addWidget(self.quota)
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
        self.quota.setText(f"Đã dùng {used}/{lic['SoTaiKhoanToiDa']} tài khoản theo Key của {lic['TenCongTy']}.")

    def issue(self):
        dlg = IssueCodeDialog(self.ctx, self)
        if dlg.exec() == QDialog.Accepted and dlg.code:
            CodeResultDialog(dlg.code, dlg.fullname.text(), self).exec()
            self.refresh()

    def revoke(self):
        r = self.codes.current()
        if r and r["TrangThai"] == "Đang dùng" and confirm(self, f"Thu hồi mã của {r['HoTen']} ({r['TenDangNhap']})?"):
            self.ctx.acc.revoke(r["MaKichHoat"])
            self.refresh()
