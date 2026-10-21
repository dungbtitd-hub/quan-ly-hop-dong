"""Cửa sổ chính: thanh tiêu đề, thanh điều hướng bên trái, các màn hình, menu, tự khóa thu chi."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .accounts_page import AccountsPage
from .auth_dialogs import ChangePasswordDialog
from .contracts_page import CatalogPage, ContractsPage, HomePage
from .finance_page import FinancePage
from .invoices_page import InvoicesPage
from .reports_page import ReportsPage
from .splash import AboutDialog
from .sync_page import SyncPage
from .widgets import confirm, info


class _ActivityFilter(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self.win.last_activity = time.monotonic()
        return False


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.logout_requested = False
        self.setWindowTitle(f"{APP_NAME} – {ctx.company}")
        self.resize(1280, 800)
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget()
        header.setObjectName("Header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 8, 14, 8)
        t = QLabel(APP_NAME)
        t.setObjectName("HeaderTitle")
        hl.addWidget(t)
        hl.addStretch(1)
        sub = QLabel(f"{ctx.company}  ·  {ctx.user['HoTen']} ({ctx.user['VaiTro']})")
        sub.setObjectName("HeaderSub")
        hl.addWidget(sub)
        root.addWidget(header)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(200)
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.addWidget(self.stack)
        body.addWidget(content, 1)
        root.addLayout(body, 1)
        self.setCentralWidget(central)

        self.pages = []
        self._add("Trang chủ", HomePage(ctx), True)
        self._add("Hợp đồng", ContractsPage(ctx), ctx.can("HopDong"))
        self._add("Hóa đơn đầu ra", InvoicesPage(ctx, "ra"), ctx.can("HoaDonDauRa"))
        self._add("Hóa đơn đầu vào", InvoicesPage(ctx, "vao"), ctx.can("HoaDonDauVao"))
        self.finance_page = FinancePage(ctx) if ctx.can("ThuChi") else None
        if self.finance_page:
            self._add("Thu chi (đã khóa)", self.finance_page, True)
            self.finance_page.unlocked.connect(self._finance_changed)
            self.finance_page.locked.connect(self._finance_changed)
        self._add("Báo cáo", ReportsPage(ctx), ctx.can("BaoCao"))
        self.sync_page = SyncPage(ctx)
        self._add("Đồng bộ", self.sync_page, ctx.can("DongBo"))
        self._add("Danh mục", CatalogPage(ctx), ctx.can("DanhMuc"))
        if ctx.can("TaiKhoan"):
            self._add("Tài khoản", AccountsPage(ctx), True)
        self.nav.currentRowChanged.connect(self._go)
        self.nav.setCurrentRow(0)

        self._menus()
        self.statusBar().showMessage(f"Máy: {ctx.sync.machine_name()}  ·  Mã máy: {ctx.machine_id}  ·  "
                                     f"Dữ liệu: {ctx.data_dir}")
        self.last_activity = time.monotonic()
        QApplication.instance().installEventFilter(_ActivityFilter(self))
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self._check_idle)
        self.idle_timer.start(15_000)

    def _add(self, title, page, visible):
        if not visible:
            return
        self.nav.addItem(QListWidgetItem(title))
        self.stack.addWidget(page)
        self.pages.append(page)

    def _go(self, i):
        if i < 0:
            return
        self.stack.setCurrentIndex(i)
        page = self.pages[i]
        if hasattr(page, "refresh"):
            page.refresh()

    def refresh_all(self):
        self.ctx.refresh_user()
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def _finance_changed(self):
        for i, p in enumerate(self.pages):
            if p is self.finance_page:
                self.nav.item(i).setText("Thu chi (đang mở)" if self.ctx.finance else "Thu chi (đã khóa)")

    def _menus(self):
        m = self.menuBar().addMenu("Hệ thống")
        for text, fn in (("Đổi mật khẩu đăng nhập…", lambda: ChangePasswordDialog(self.ctx, self).exec()),
                         ("Sao lưu ngay", self._backup), ("Khôi phục từ bản sao lưu…", self._restore),
                         ("Mở thư mục dữ liệu", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.data_dir)))),
                         (None, None), ("Đăng xuất", self._logout), ("Thoát", self.close)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, self)
            a.triggered.connect(fn)
            m.addAction(a)
        h = self.menuBar().addMenu("Trợ giúp")
        a = QAction("Giới thiệu / Nhà phát triển", self)
        a.triggered.connect(lambda: AboutDialog(self.ctx.acc, self.ctx.machine_id, self).exec())
        h.addAction(a)
        h.addAction(QAction(f"Phiên bản {__version__}", self))

    def _backup(self):
        p = self.ctx.backup_now("thu-cong")
        info(self, f"Đã sao lưu vào:\n{p}")

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn bản sao lưu", str(self.ctx.backup_dir()), "Sao lưu (*.db)")
        if path and confirm(self, "Thay toàn bộ dữ liệu hợp đồng hiện tại bằng bản sao lưu đã chọn? "
                                  "Phần mềm sẽ sao lưu dữ liệu hiện tại trước khi khôi phục."):
            self.ctx.backup_now("truoc-khoi-phuc")
            self.ctx.db.restore_from(path)
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Khôi phục sao lưu", path)
            info(self, "Đã khôi phục.")
            self.refresh_all()

    def _check_idle(self):
        if self.ctx.finance is None or not self.finance_page:
            return
        if time.monotonic() - self.last_activity > self.finance_page.auto_lock_minutes() * 60:
            self.finance_page.lock()

    def changeEvent(self, ev):
        if ev.type() == QEvent.WindowStateChange and self.isMinimized() and self.finance_page:
            self.finance_page.lock()
        super().changeEvent(ev)

    def _logout(self):
        self.logout_requested = True
        self.close()

    def closeEvent(self, ev):
        if self.finance_page:
            self.finance_page.lock()
        self.ctx.lock_finance()
        ev.accept()


__all__ = ["MainWindow", "Qt"]
