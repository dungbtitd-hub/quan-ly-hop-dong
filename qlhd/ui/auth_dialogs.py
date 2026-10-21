"""Kích hoạt bản quyền, đăng nhập, đổi mật khẩu."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core.auth import AuthError
from ..core.licensing import LicenseError
from ..developer import DEVELOPER
from .widgets import copy_text, error, hint, info


def _pw() -> QLineEdit:
    w = QLineEdit()
    w.setEchoMode(QLineEdit.Password)
    return w


class ActivationDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Kích hoạt phần mềm")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Phần mềm cần được kích hoạt trước khi sử dụng. Máy Giám đốc kích hoạt bằng Key do "
                           f"{DEVELOPER['ten']} cấp; máy nhân viên kích hoạt bằng Mã do Giám đốc cấp."))
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Mã máy này:"))
        mid = QLineEdit(ctx.machine_id)
        mid.setReadOnly(True)
        mid.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 11pt;")
        mrow.addWidget(mid, 1)
        b = QPushButton("Sao chép")
        b.clicked.connect(lambda: (copy_text(ctx.machine_id), info(self, "Đã sao chép Mã máy.")))
        mrow.addWidget(b)
        lay.addLayout(mrow)
        tabs = QTabWidget()
        tabs.addTab(self._director_tab(), "Máy Giám đốc (Key nhà phát triển)")
        tabs.addTab(self._member_tab(), "Máy nhân viên / máy Giám đốc thứ hai")
        lay.addWidget(tabs)
        row = QHBoxLayout()
        row.addStretch(1)
        quit_btn = QPushButton("Thoát")
        quit_btn.clicked.connect(self.reject)
        row.addWidget(quit_btn)
        lay.addLayout(row)

    def _director_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(hint(f"Bước 1: gửi Mã yêu cầu dưới đây cho {DEVELOPER['ten']} "
                           f"(Zalo {DEVELOPER['dien_thoai']}, email {DEVELOPER['email']})."))
        self.request = QPlainTextEdit(self.ctx.acc.activation_request())
        self.request.setReadOnly(True)
        self.request.setFixedHeight(64)
        lay.addWidget(self.request)
        b = QPushButton("Sao chép Mã yêu cầu")
        b.clicked.connect(lambda: (copy_text(self.request.toPlainText()), info(self, "Đã sao chép Mã yêu cầu.")))
        lay.addWidget(b, 0, Qt.AlignLeft)
        lay.addWidget(hint("Bước 2: dán Key nhận được, tạo tài khoản Giám đốc."))
        form = QFormLayout()
        self.key = QPlainTextEdit()
        self.key.setFixedHeight(64)
        self.key.setPlaceholderText("QLHD-KEY:…")
        self.d_user = QLineEdit("giamdoc")
        self.d_name = QLineEdit()
        self.d_pw, self.d_pw2 = _pw(), _pw()
        form.addRow("Key Giám đốc *", self.key)
        form.addRow("Tên đăng nhập *", self.d_user)
        form.addRow("Họ tên Giám đốc *", self.d_name)
        form.addRow("Mật khẩu (≥ 8 ký tự) *", self.d_pw)
        form.addRow("Nhập lại mật khẩu *", self.d_pw2)
        lay.addLayout(form)
        b = QPushButton("Kích hoạt")
        b.setObjectName("Primary")
        b.clicked.connect(self._activate_director)
        lay.addWidget(b, 0, Qt.AlignRight)
        return w

    def _member_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(hint("Gửi Mã máy ở trên cho Giám đốc. Giám đốc vào Tài khoản → Cấp mã, chọn vai trò và "
                           "quyền rồi gửi lại Mã kích hoạt. Dán mã vào đây và đặt mật khẩu của bạn. "
                           "Máy Giám đốc thứ hai (theo dõi song song) cũng kích hoạt theo cách này với vai trò Giám đốc."))
        form = QFormLayout()
        self.code = QPlainTextEdit()
        self.code.setPlaceholderText("QLHD-NV:…")
        self.code.setFixedHeight(90)
        self.m_pw, self.m_pw2 = _pw(), _pw()
        form.addRow("Mã kích hoạt *", self.code)
        form.addRow("Mật khẩu (≥ 8 ký tự) *", self.m_pw)
        form.addRow("Nhập lại mật khẩu *", self.m_pw2)
        lay.addLayout(form)
        b = QPushButton("Kích hoạt")
        b.setObjectName("Primary")
        b.clicked.connect(self._activate_member)
        lay.addWidget(b, 0, Qt.AlignRight)
        lay.addStretch(1)
        return w

    def _activate_director(self):
        if self.d_pw.text() != self.d_pw2.text():
            return error(self, "Hai lần nhập mật khẩu không khớp.")
        if not self.d_user.text().strip() or not self.d_name.text().strip():
            return error(self, "Nhập tên đăng nhập và họ tên Giám đốc.")
        try:
            self.ctx.acc.activate_director(self.key.toPlainText(), self.d_user.text().strip(),
                                           self.d_name.text().strip(), self.d_pw.text())
        except (AuthError, LicenseError) as e:
            return error(self, str(e))
        info(self, "Kích hoạt thành công. Hãy đăng nhập bằng tài khoản Giám đốc vừa tạo.")
        self.accept()

    def _activate_member(self):
        if self.m_pw.text() != self.m_pw2.text():
            return error(self, "Hai lần nhập mật khẩu không khớp.")
        try:
            p = self.ctx.acc.activate_member(self.code.toPlainText(), self.m_pw.text())
        except (AuthError, LicenseError) as e:
            return error(self, str(e))
        info(self, f"Kích hoạt thành công tài khoản {p['tdn']} ({p['vai_tro']}).")
        self.accept()


class LoginDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(f"Đăng nhập – {ctx.company}")
        self.setFixedWidth(380)
        lay = QVBoxLayout(self)
        title = QLabel(ctx.company)
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        lay.addWidget(title)
        form = QFormLayout()
        self.user = QLineEdit(ctx.db.setting("dang_nhap_cuoi") or "")
        self.pw = _pw()
        form.addRow("Tên đăng nhập", self.user)
        form.addRow("Mật khẩu", self.pw)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Đăng nhập")
        b.setObjectName("Primary")
        b.setDefault(True)
        b.clicked.connect(self._login)
        c = QPushButton("Thoát")
        c.clicked.connect(self.reject)
        row.addWidget(b)
        row.addWidget(c)
        lay.addLayout(row)
        (self.pw if self.user.text() else self.user).setFocus()

    def _login(self):
        try:
            self.ctx.user = self.ctx.acc.login(self.user.text().strip(), self.pw.text())
        except AuthError as e:
            self.pw.clear()
            return error(self, str(e))
        self.ctx.db.set_setting("dang_nhap_cuoi", self.ctx.user["TenDangNhap"])
        self.accept()


class ChangePasswordDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Đổi mật khẩu đăng nhập")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.old, self.new, self.new2 = _pw(), _pw(), _pw()
        form.addRow("Mật khẩu hiện tại", self.old)
        form.addRow("Mật khẩu mới (≥ 8 ký tự)", self.new)
        form.addRow("Nhập lại mật khẩu mới", self.new2)
        lay.addLayout(form)
        b = QPushButton("Đổi mật khẩu")
        b.setObjectName("Primary")
        b.clicked.connect(self._save)
        lay.addWidget(b, 0, Qt.AlignRight)

    def _save(self):
        if self.new.text() != self.new2.text():
            return error(self, "Hai lần nhập mật khẩu mới không khớp.")
        try:
            self.ctx.acc.change_password(self.ctx.user["MaNguoiDung"], self.old.text(), self.new.text())
        except AuthError as e:
            return error(self, str(e))
        info(self, "Đã đổi mật khẩu.")
        self.accept()


class RenewDialog(QDialog):
    """Gia hạn bản quyền. Máy Giám đốc chính: gửi Mã yêu cầu gia hạn cho nhà phát triển, dán Key mới.
    Máy nhân viên / Giám đốc phụ: dán Key Giám đốc hiện hành (Giám đốc sao chép ở mục Tài khoản)."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Gia hạn bản quyền")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        st = ctx.acc.license_status()
        if st["thong_bao"]:
            msg = QLabel(st["thong_bao"])
            msg.setWordWrap(True)
            msg.setStyleSheet("color:#a32020; font-weight:600;" if st["trang_thai"] in ("het_han", "lui_ngay", "loi")
                              else "color:#9c5700; font-weight:600;")
            lay.addWidget(msg)
        primary = ctx.acc.is_primary_director_machine()
        if primary:
            lay.addWidget(hint(f"Bước 1: gửi Mã yêu cầu gia hạn dưới đây cho {DEVELOPER['ten']} "
                               f"(Zalo {DEVELOPER['dien_thoai']}, email {DEVELOPER['email']})."))
            req = QPlainTextEdit(ctx.acc.renewal_request())
            req.setReadOnly(True)
            req.setFixedHeight(64)
            lay.addWidget(req)
            b = QPushButton("Sao chép Mã yêu cầu gia hạn")
            b.clicked.connect(lambda: (copy_text(req.toPlainText()), info(self, "Đã sao chép Mã yêu cầu gia hạn.")))
            lay.addWidget(b, 0, Qt.AlignLeft)
            lay.addWidget(hint("Bước 2: dán Key gia hạn nhận được rồi bấm Gia hạn. Dữ liệu, tài khoản giữ nguyên. "
                               "Sau đó gửi một gói đồng bộ cho các máy nhân viên để các máy đó tự gia hạn theo."))
        else:
            lay.addWidget(hint("Máy này dùng bản quyền của Giám đốc. Sau khi Giám đốc gia hạn: nhận một gói đồng bộ "
                               "từ máy Giám đốc (tự gia hạn), hoặc dán Key Giám đốc mới (Giám đốc vào Tài khoản → "
                               "Sao chép Key cho máy nhân viên) vào ô dưới đây."))
        self.key = QPlainTextEdit()
        self.key.setPlaceholderText("QLHD-KEY:…")
        self.key.setFixedHeight(70)
        lay.addWidget(self.key)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Gia hạn")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._renew)
        close = QPushButton("Đóng")
        close.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(close)
        lay.addLayout(row)

    def _renew(self):
        try:
            new = self.ctx.acc.renew(self.key.toPlainText())
        except (AuthError, LicenseError) as e:
            return error(self, str(e))
        from ..core.licensing import expiry_text
        info(self, f"Đã gia hạn bản quyền: {expiry_text(new)}.")
        self.accept()
