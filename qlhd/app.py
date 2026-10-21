"""Điểm khởi chạy phần mềm: màn hình nhà phát triển → kích hoạt (lần đầu) → đăng nhập → cửa sổ chính."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QDialog

from . import APP_NAME
from .context import AppContext
from .ui.auth_dialogs import ActivationDialog, LoginDialog, RenewDialog
from .ui.main_window import MainWindow
from .ui.splash import SplashDialog
from .ui.style import apply_theme


def create_app(argv=None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ITD Viet Nam")
    app.setStyle("Fusion")
    apply_theme(app, "sang")
    f = QFont("Segoe UI", 9)
    f.setStyleHint(QFont.SansSerif)
    app.setFont(f)
    from .ui.icon_data import ICON_PNG_B64

    pm = QPixmap()
    pm.loadFromData(QByteArray.fromBase64(ICON_PNG_B64.encode()))
    app.setWindowIcon(QIcon(pm))
    return app


def self_test() -> int:
    """Kiểm tra nhanh bản đóng gói: nạp đủ thư viện, dựng được giao diện, rồi thoát."""
    import os
    import tempfile

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import msoffcrypto  # noqa: F401
    import openpyxl  # noqa: F401
    import pypdf  # noqa: F401

    from .core import licensing
    from .ui import (accounts_page, assets_page, contracts_page, finance_page, invoices_page,  # noqa: F401
                     profit_page, reports_page, sync_page)

    create_app([])
    licensing.vendor_public_key()
    with tempfile.TemporaryDirectory() as d:
        ctx = AppContext(d)
        SplashDialog(ctx.acc, seconds=0).grab()
        ActivationDialog(ctx).grab()
        ctx.acc.license_status()
        ctx.db.close()
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--self-test" in argv:
        return self_test()
    app = create_app(argv)
    ctx = AppContext()
    apply_theme(app, ctx.db.setting("giao_dien") or "sang")
    try:
        SplashDialog(ctx.acc).exec()
        if not ctx.acc.is_activated():
            if ActivationDialog(ctx).exec() != QDialog.Accepted:
                return 0
        while not ctx.acc.license_usable():
            if RenewDialog(ctx).exec() != QDialog.Accepted:
                return 0
        while True:
            ctx.user = None
            if LoginDialog(ctx).exec() != QDialog.Accepted:
                return 0
            win = MainWindow(ctx)
            win.setAttribute(Qt.WA_DeleteOnClose, False)
            win.show()
            app.exec()
            if not win.logout_requested:
                return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
