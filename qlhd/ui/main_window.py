"""Cửa sổ chính: thanh điều hướng tối bên trái (thông tin nhà phát triển ở góc dưới), thanh trên cùng
(tiêu đề, chuyển sáng/tối, chuông nhắc, tài khoản), các màn hình, tự khóa thu chi."""
from __future__ import annotations

import time
from datetime import date, datetime

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QStackedWidget, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core.reports import fmt_date
from ..developer import DEVELOPER
from . import icons, style
from ..core import alerts as al
from ..core.assets import REVIEW_AFTER_UPGRADE, AssetService
from ..core.contracts import backfill_vat
from .accounts_page import AccountsPage
from .alerts_ui import NotificationsDialog, TelegramDialog, send_digest_async
from .assets_page import AssetsPage
from .auth_dialogs import MyAccountDialog, WipeDataDialog
from .contracts_page import CatalogPage, ContractsPage
from .finance_page import FinancePage
from .home_page import HomePage, reminder_text
from .invoices_page import InvoicesPage
from .reports_page import ReportsPage
from .dup_review_page import DupReviewPage
from .splash import AboutDialog
from .sync_page import SyncPage
from .widgets import confirm, error, info, local_time
from ..core import sync_folder as sfm
from ..core.gemini import GeminiService
from ..core.sync_folder import SyncFolder
from .invoice_files import FolderSettingsDialog, InvoiceFolderDialog, Scanner, scan_text

PAGE_ROLE = Qt.UserRole          # chỉ số trang trong stack; None = dòng tiêu đề nhóm
BADGE_ROLE = Qt.UserRole + 1     # số hiện trên huy hiệu đỏ
PEEK_MS = 2 * 60_000             # cứ 2 phút xem thư mục đồng bộ có gói mới từ máy khác không


class _ActivityFilter(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self.win.last_activity = time.monotonic()
        return False


class _NavDelegate(QStyledItemDelegate):
    """Vẽ dòng tiêu đề nhóm (CÔNG VIỆC, DỮ LIỆU…) và huy hiệu số đỏ bên phải mục."""

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        if index.data(PAGE_ROLE) is None:
            return QSize(s.width(), 34)
        return QSize(s.width(), max(s.height(), 36))

    def paint(self, p, option, index):
        if index.data(PAGE_ROLE) is None:
            p.save()
            f = QFont(option.font)
            f.setPointSizeF(max(f.pointSizeF() - 1.5, 7))
            f.setBold(True)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1)
            p.setFont(f)
            p.setPen(QColor(style.color("sidebar_head")))
            p.drawText(option.rect.adjusted(12, 10, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, index.data())
            p.restore()
            return
        super().paint(p, option, index)
        n = index.data(BADGE_ROLE)
        if n:
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            txt = str(n) if n < 100 else "99+"
            w = max(20, 8 + p.fontMetrics().horizontalAdvance(txt))
            r = QRect(option.rect.right() - w - 8, option.rect.center().y() - 9, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(style.THEMES["sang"]["danger"]))
            p.drawRoundedRect(r, 9, 9)
            f = QFont(option.font)
            f.setBold(True)
            f.setPointSizeF(max(f.pointSizeF() - 1.5, 7))
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignCenter, txt)
            p.restore()


class _DevCard(QFrame):
    """Thông tin nhà phát triển ở góc dưới bên trái; bấm để mở Giới thiệu."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DevCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Giới thiệu / Nhà phát triển")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        for text, name in (("NHÀ PHÁT TRIỂN", "DevCaption"), (DEVELOPER["ten"], "DevName"),
                           (f"Zalo {DEVELOPER['dien_thoai']}", "DevLine"), (DEVELOPER["email"], "DevLine"),
                           (DEVELOPER["website"], "DevLine"), (f"Phiên bản {__version__}", "DevCaption")):
            lbl = QLabel(text)
            lbl.setObjectName(name)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

    def mouseReleaseEvent(self, ev):
        self.clicked.emit()
        super().mouseReleaseEvent(ev)


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.logout_requested = False
        self.setWindowTitle(f"{APP_NAME} – {ctx.company}")
        self.resize(1360, 840)
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._topbar())
        content = QWidget()
        content.setObjectName("Content")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 14, 20, 12)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        right.addWidget(content, 1)
        root.addLayout(right, 1)
        self.setCentralWidget(central)

        self.pages = []
        self.items: dict[str, QListWidgetItem] = {}
        self.home = HomePage(ctx)
        self.contracts_page = ContractsPage(ctx) if ctx.can("HopDong") else None
        self.finance_page = FinancePage(ctx) if ctx.can("ThuChi") else None
        self.sync_page = SyncPage(ctx)
        sections = [
            ("CÔNG VIỆC", [("Tổng quan", "home", self.home, True),
                           ("Hợp đồng", "contract", self.contracts_page, self.contracts_page is not None),
                           ("Hóa đơn đầu ra", "invoice_out", lambda: InvoicesPage(ctx, "ra"), ctx.can("HoaDonDauRa")),
                           ("Hóa đơn đầu vào", "invoice_in", lambda: InvoicesPage(ctx, "vao"),
                            ctx.can("HoaDonDauVao")),
                           ("Tài sản, CCDC", "box", lambda: AssetsPage(ctx), ctx.can("TaiSan")),
                           ("Báo cáo", "report", lambda: ReportsPage(ctx), ctx.can("BaoCao")),
                           ("Rà soát trùng lặp", "search", lambda: DupReviewPage(ctx),
                            any(ctx.can(f) for f in ("HopDong", "HoaDonDauRa", "HoaDonDauVao")))]),
            ("TÀI CHÍNH", [("Thu chi (đã khóa)", "lock", self.finance_page, self.finance_page is not None)]),
            ("DỮ LIỆU", [("Đồng bộ", "sync", self.sync_page, ctx.can("DongBo")),
                         ("Danh mục", "catalog", lambda: CatalogPage(ctx), ctx.can("DanhMuc")),
                         ("Tài khoản", "users", lambda: AccountsPage(ctx), ctx.can("TaiKhoan"))]),
        ]
        for head, entries in sections:
            entries = [e for e in entries if e[3]]
            if not entries:
                continue
            h = QListWidgetItem(head)
            h.setFlags(Qt.NoItemFlags)
            self.nav.addItem(h)
            for title, icon_name, page, _ in entries:
                self._add(title, icon_name, page() if callable(page) else page)
        if self.finance_page:
            self.finance_page.unlocked.connect(self._finance_changed)
            self.finance_page.locked.connect(self._finance_changed)
        for page in self.pages:                         # trang nào có liên kết sang hợp đồng thì mở được ngay
            if page is not self.home and hasattr(page, "open_contract"):
                page.open_contract.connect(self.show_contract)
        self.home.open_page.connect(self.go_to)
        self.home.open_contract.connect(self.show_contract)
        self.home.open_review.connect(lambda: self.show_review())
        self.home.add_contract.connect(self._add_contract)
        self.home.open_action.connect(self._alert_action)
        self.nav.currentRowChanged.connect(self._go)
        self.nav.setCurrentItem(self.items["Tổng quan"])

        self._statusbar()
        st = ctx.acc.license_status()
        self.update_badges()
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self._startup_notices)
        self.startup_timer.start(500)
        self.last_activity = time.monotonic()
        self.activity_filter = _ActivityFilter(self)
        QApplication.instance().installEventFilter(self.activity_filter)
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self._check_idle)
        self.idle_timer.start(15_000)
        self.tg_timer = QTimer(self)               # bản tin Telegram hằng ngày đúng giờ đã đặt
        self.tg_timer.timeout.connect(lambda: self._telegram_tick(False))
        self.tg_timer.start(60_000)
        # đồng bộ tự động qua thư mục chung, quét thư mục hóa đơn
        self.auto_sync = SyncFolder(ctx.db, ctx.sync)
        self.scanner = Scanner(ctx, self)
        self.scanner.finished.connect(self._scanned)
        self.scanner.progress.connect(lambda t: self.statusBar().showMessage(t, 60_000))
        self.scan_timer = QTimer(self)             # tự quét thư mục hóa đơn định kỳ khi phần mềm đang mở
        self.scan_timer.timeout.connect(self._auto_scan)
        self._restart_scan_timer()
        self.send_timer = QTimer(self)
        self.send_timer.timeout.connect(self._auto_send)
        self.recv_timer = QTimer(self)
        self.recv_timer.timeout.connect(lambda: self._auto_receive(False))
        self.peek_timer = QTimer(self)          # kiểm tra nhanh gói mới từ máy khác (chỉ đọc phần đầu tệp)
        self.peek_timer.timeout.connect(self._peek_receive)
        self.restart_auto_sync()
        self._paint_status_buttons()

    # ---------------------------------------------------------------- dựng khung
    def _sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(240)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(10, 14, 10, 12)
        lay.setSpacing(8)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 6)
        logo = QLabel("Q")
        logo.setObjectName("Logo")
        logo.setFixedSize(36, 36)
        logo.setAlignment(Qt.AlignCenter)
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        app = QLabel(APP_NAME)
        app.setObjectName("AppName")
        names.addWidget(app)
        comp = QLabel(self.ctx.company)
        comp.setObjectName("AppCompany")
        comp.setWordWrap(True)
        names.addWidget(comp)
        brand.addLayout(names, 1)
        lay.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setItemDelegate(_NavDelegate(self.nav))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.nav, 1)
        dev = _DevCard()
        dev.clicked.connect(self._about)
        lay.addWidget(dev)
        return side

    def _topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 8, 16, 8)
        lay.setSpacing(10)
        self.top_title = QLabel()
        self.top_title.setObjectName("TopTitle")
        lay.addWidget(self.top_title)
        lay.addStretch(1)
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("IconButton")
        self.theme_btn.setFixedSize(38, 38)
        self.theme_btn.setIconSize(QSize(18, 18))
        self.theme_btn.clicked.connect(self.toggle_theme)
        lay.addWidget(self.theme_btn)
        self.font_btn = QToolButton()
        self.font_btn.setObjectName("IconButton")
        self.font_btn.setFixedSize(38, 38)
        self.font_btn.setText("Aa")
        self.font_btn.setToolTip("Cỡ chữ")
        self.font_btn.setPopupMode(QToolButton.InstantPopup)
        self.font_btn.setMenu(self._font_menu())
        lay.addWidget(self.font_btn)
        self.sync_btn = QToolButton()
        self.sync_btn.setObjectName("IconButton")
        self.sync_btn.setFixedHeight(38)
        self.sync_btn.setIconSize(QSize(18, 18))
        self.sync_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.sync_btn.clicked.connect(self.sync_now)
        lay.addWidget(self.sync_btn)
        self.gem_btn = QToolButton()
        self.gem_btn.setObjectName("IconButton")
        self.gem_btn.setFixedHeight(38)
        self.gem_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.gem_btn.setText("Gemini")
        self.gem_btn.clicked.connect(self.folder_settings)
        lay.addWidget(self.gem_btn)
        self.bell = QToolButton()
        self.bell.setObjectName("IconButton")
        self.bell.setFixedSize(38, 38)
        self.bell.setIconSize(QSize(18, 18))
        self.bell.setToolTip("Nhắc việc")
        self.bell.clicked.connect(self._bell_menu)
        self.bell_badge = QLabel(self.bell)
        self.bell_badge.setObjectName("Badge")
        self.bell_badge.setAlignment(Qt.AlignCenter)
        self.bell_badge.setGeometry(20, 2, 17, 15)
        lay.addWidget(self.bell)
        self.user_btn = QToolButton()
        self.user_btn.setObjectName("UserCard")
        self.user_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.user_btn.setIconSize(QSize(26, 26))
        self.user_btn.setPopupMode(QToolButton.InstantPopup)
        self.user_btn.setMenu(self._user_menu())
        lay.addWidget(self.user_btn)
        self._paint_topbar()
        return bar

    def _user_menu(self) -> QMenu:
        m = QMenu(self)
        entries = [("Tài khoản của tôi…", "user", lambda: self._my_account(0)),
                   ("Đổi mật khẩu…", "key", lambda: self._my_account(1)), None,
                   ("Bản quyền / Gia hạn…", "key", self._renew),
                   ("Giới thiệu / Nhà phát triển", "info", self._about), None,
                   ("Chuyển giao diện sáng / tối", "moon", self.toggle_theme), "font",
                   ("Thông báo…", "bell", self._bell_menu),
                   ("Thư mục hóa đơn và Gemini…", "folder", self.folder_settings),
                   ("Thư mục hóa đơn (vừa tự thêm, cần xem)…", "invoice_in", lambda: self.invoice_folder(1)),
                   ("Đồng bộ ngay", "sync", self.sync_now), None,
                   ("Sao lưu ngay", "save", self._backup), ("Khôi phục từ bản sao lưu…", "import", self._restore),
                   ("Mở thư mục dữ liệu", "folder",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.data_dir))))]
        if self.ctx.user["VaiTro"] == "Giám đốc":
            entries += [("Cảnh báo qua Telegram…", "bell", self.telegram_settings),
                        None, ("Xóa sạch dữ liệu…", "trash", self._wipe)]
        entries += [None, ("Đăng xuất", "logout", self._logout), ("Thoát", None, self.close)]
        for e in entries:
            if e is None:
                m.addSeparator()
                continue
            if e == "font":
                m.addMenu(self._font_menu())
                continue
            text, icon_name, fn = e
            a = QAction(text, self)
            if icon_name:
                a.setIcon(icons.icon(icon_name, "#7b8494", 16))
            a.triggered.connect(fn)
            m.addAction(a)
        return m

    def _font_menu(self) -> QMenu:
        m = QMenu("Cỡ chữ", self)
        self.font_actions = {}
        for key, (label, k) in style.FONT_SIZES.items():
            a = QAction(f"{label} ({round(k * 100)}%)", self)
            a.setCheckable(True)
            a.setChecked(style.font_size() == key)
            a.triggered.connect(lambda _=False, key=key: self.set_font_size(key))
            m.addAction(a)
            self.font_actions[key] = a
        return m

    def set_font_size(self, key: str) -> None:
        """Đổi cỡ chữ toàn phần mềm, nhớ cho lần mở sau trên máy này."""
        style.apply_theme(QApplication.instance(), style.current(), key)
        self.ctx.db.set_setting("co_chu", key)
        for k, a in self.font_actions.items():
            a.setChecked(k == key)
        self.refresh_all()

    def _paint_topbar(self):
        dark = style.current() == "toi"
        self.theme_btn.setIcon(icons.icon("sun" if dark else "moon", style.color("muted"), 18))
        self.theme_btn.setToolTip("Chuyển sang giao diện sáng" if dark else "Chuyển sang giao diện tối")
        self.bell.setIcon(icons.icon("bell", style.color("muted"), 18))
        u = self.ctx.user
        st = self.ctx.acc.license_status()
        han = "vĩnh viễn" if not st["han"] else "hạn " + fmt_date(st["han"])
        self.user_btn.setText(f"{u['HoTen']}\n{u['VaiTro']} · {han}")
        self.user_btn.setIcon(icons.icon("user", style.color("accent"), 26))

    def _statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        st = self.ctx.acc.license_status()
        if st["trang_thai"] == "sap_het":
            warn = QLabel(st["thong_bao"])
            warn.setObjectName("Warn")
            sb.addWidget(warn)
        for text in (f"Máy: {self.ctx.sync.machine_name()}", f"Mã máy: {self.ctx.machine_id}",
                     f"Dữ liệu: {self.ctx.data_dir}", f"Phiên bản {__version__}"):
            sb.addPermanentWidget(QLabel(text))

    # ---------------------------------------------------------------- điều hướng
    def _add(self, title, icon_name, page):
        item = QListWidgetItem(icons.icon(icon_name, style.THEMES["sang"]["sidebar_text"], 18), title)
        item.setData(PAGE_ROLE, len(self.pages))
        self.nav.addItem(item)
        self.stack.addWidget(page)
        self.pages.append(page)
        self.items[title] = item

    def _go(self, row):
        item = self.nav.item(row)
        if item is None or item.data(PAGE_ROLE) is None:
            return
        i = item.data(PAGE_ROLE)
        self.stack.setCurrentIndex(i)
        self.top_title.setText(item.text())
        page = self.pages[i]
        if hasattr(page, "refresh"):
            page.refresh()
        self.update_badges()

    def go_to(self, title: str) -> None:
        item = self.items.get(title)
        if item is not None:
            self.nav.setCurrentItem(item)

    def show_contract(self, ma: str) -> None:
        if self.contracts_page is None:
            return
        self.go_to("Hợp đồng")
        self.contracts_page.select_contract(ma)

    def _add_contract(self):
        if self.contracts_page is not None:
            self.go_to("Hợp đồng")
            self.contracts_page.add()

    def refresh_all(self):
        self.ctx.refresh_user()
        self._paint_topbar()
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()
        self.update_badges()

    def _alert_types(self) -> list[str]:
        """Ngữ cảnh thông báo tài khoản này theo dõi, trong phạm vi quyền được xem."""
        types = al.AlertSettings(self.ctx.db).app_types(self.ctx.user["TenDangNhap"])
        if not self.ctx.can("HopDong"):
            types = [t for t in types if t == "hddv_xem_lai"]
        if "Tài sản, CCDC" not in self.items:
            types = [t for t in types if t != "hddv_xem_lai"]
        return types

    def update_badges(self):
        backfill_vat(self.ctx.db)          # dữ liệu cũ / nhận từ máy bản cũ đủ dữ kiện mà chưa tính VAT
        today = date.today().isoformat()
        if "Tài sản, CCDC" in self.items:
            svc = AssetService(self.ctx.db)
            svc.backfill()
            pending = svc.review_count()
            self.items["Tài sản, CCDC"].setData(BADGE_ROLE, pending or None)
        if "Hợp đồng" in self.items:
            self.items["Hợp đồng"].setData(BADGE_ROLE, len(self.ctx.cs.expiring(today)) or None)
        self._alerts = al.collect(self.ctx.db, today, self._alert_types())
        n = len([a for a in self._alerts if a.loai != "hddv_xem_lai"]) + \
            (1 if any(a.loai == "hddv_xem_lai" for a in self._alerts) else 0)
        self._paint_status_buttons()
        self.bell_badge.setText(str(n) if n < 100 else "99")
        self.bell_badge.setVisible(n > 0)
        self.bell.setToolTip(f"{n} thông báo" if n else "Không có thông báo")

    def _bell_menu(self):
        dlg = NotificationsDialog(self.ctx, self)
        dlg.open_contract.connect(self.show_contract)
        dlg.open_review.connect(self.show_review)
        dlg.open_action.connect(self._alert_action)
        dlg.exec()
        self.update_badges()

    def show_review(self, code: str | None = None) -> None:
        page = next((p for p in self.pages if type(p).__name__ == "AssetsPage"), None)
        if page is not None:
            self.go_to("Tài sản, CCDC")
            page.show_review(code)

    def telegram_settings(self):
        TelegramDialog(self.ctx, self).exec()

    def _telegram_tick(self, opening: bool) -> None:
        st = al.AlertSettings(self.ctx.db)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not st.due(now, opening):
            return
        st.mark_sent(now[:10])            # đánh dấu trước để không gửi lặp khi mạng chậm

        def done(err: str):
            if err:
                st.mark_sent("")           # lỗi: lần sau thử lại
                self.statusBar().showMessage(err, 15_000)
            else:
                self.statusBar().showMessage("Đã gửi bản tin hằng ngày qua Telegram.", 10_000)
        send_digest_async(self.ctx, done)

    def _startup_notices(self):
        st = self.ctx.acc.license_status()
        if st["trang_thai"] == "sap_het":
            info(self, st["thong_bao"], "Sắp hết hạn bản quyền")
        self._daily_reminder()
        self._pending_reminder()
        self._upgrade_review()
        self._telegram_tick(True)
        self._auto_receive(True)
        from ..core.invoice_folder import K_ON_OPEN, InvoiceFolders
        if InvoiceFolders(self.ctx.db).flag(K_ON_OPEN):
            self.scanner.start()

    def _upgrade_review(self):
        """Sau khi nâng cấp phiên bản: rà soát các dòng hóa đơn đầu vào phần mềm đã tự phân loại, báo dòng nghi ngờ
        để kế toán xem lại. Chỉ cảnh báo, không đổi dữ liệu. Mỗi phiên bản một lần."""
        if not self.ctx.can("TaiSan") or "Tài sản, CCDC" not in self.items:
            return
        key = "ra_soat_phan_loai_phien_ban"
        if self.ctx.db.setting(key) == __version__:
            return
        self.ctx.db.set_setting(key, __version__)
        rows = AssetService(self.ctx.db).review_lines(REVIEW_AFTER_UPGRADE)
        if not rows:
            return
        by: dict[str, int] = {}
        for r in rows:
            by[r["MaCanhBao"]] = by.get(r["MaCanhBao"], 0) + 1
        from ..core.assets import FLAGS
        detail = "\n".join(f"• {FLAGS[c][0]}: {n} dòng" for c, n in by.items())
        box = QMessageBox(QMessageBox.Information, "Rà soát sau nâng cấp",
                          f"Phiên bản {__version__} đã rà soát lại hóa đơn đầu vào phần mềm tự phân loại trước đây. "
                          f"Có {len(rows)} dòng cần kế toán xem lại:\n\n{detail}\n\nPhần mềm không tự đổi phân "
                          "loại. Mở danh sách để xem lại ngay?", QMessageBox.Yes | QMessageBox.No, self)
        box.button(QMessageBox.Yes).setText("Mở danh sách")
        box.button(QMessageBox.No).setText("Để sau")
        box.setModal(False)
        yes = box.button(QMessageBox.Yes)
        box.buttonClicked.connect(lambda b: b is yes and self.show_review())
        box.open()
        self.review_box = box

    def _pending_reminder(self):
        """Báo kế toán (mỗi ngày một lần) khi còn dòng hóa đơn đầu vào phần mềm chưa tự phân loại được."""
        if not self.ctx.can("TaiSan", "Sua") or "Tài sản, CCDC" not in self.items:
            return
        n = AssetService(self.ctx.db).pending_count()
        key = f"nhac_phan_loai_ngay:{self.ctx.user['TenDangNhap']}"
        if self.ctx.db.setting("ra_soat_phan_loai_phien_ban") != __version__:
            return                          # lần đầu chạy bản mới: thông báo rà soát sau nâng cấp thay cho nhắc này
        today = date.today().isoformat()
        if not n or self.ctx.db.setting(key) == today:
            return
        self.ctx.db.set_setting(key, today)
        if confirm(self, f"Có {n} dòng hóa đơn đầu vào phần mềm chưa tự phân loại được (chi phí trong kỳ, CCDC hay "
                         "TSCĐ). Mở màn Tài sản, CCDC để phân loại ngay?", "Cần kế toán phân loại"):
            self.show_review()

    def _daily_reminder(self):
        """Nhắc hợp đồng sắp hết hạn một lần mỗi ngày cho mỗi tài khoản, khi đăng nhập."""
        if not self.ctx.can("HopDong") or self.ctx.user is None:
            return
        today = date.today().isoformat()
        key = f"nhac_hd_ngay:{self.ctx.user['TenDangNhap']}"
        if self.ctx.db.setting(key) == today:
            return
        self.ctx.db.set_setting(key, today)
        items = al.collect(self.ctx.db, today, [t for t in self._alert_types() if t != "hddv_xem_lai"])
        text = al.digest_text(items, self.ctx.company, today) if items else reminder_text(self.ctx, today)
        if text:
            box = QMessageBox(QMessageBox.Information, "Bản tin hôm nay", text, QMessageBox.Ok, self)
            box.setModal(False)
            box.open()
            self.reminder_box = box

    def _finance_changed(self):
        item = next((it for t, it in self.items.items() if t.startswith("Thu chi")), None)
        if item is not None:
            opened = self.ctx.finance is not None
            item.setText("Thu chi (đang mở)" if opened else "Thu chi (đã khóa)")
            item.setIcon(icons.icon("wallet" if opened else "lock", style.THEMES["sang"]["sidebar_text"], 18))
            if self.nav.currentItem() is item:
                self.top_title.setText(item.text())

    # ---------------------------------------------------------------- đồng bộ tự động, thư mục hóa đơn, Gemini
    def restart_auto_sync(self) -> None:
        st = self.auto_sync.settings()
        self.send_timer.stop()
        self.recv_timer.stop()
        self.peek_timer.stop()
        if st["thu_muc"]:
            if st["tu_gui"]:
                self.send_timer.start(max(1, st["gui_phut"]) * 60_000)
            if st["tu_nhan"]:
                self.recv_timer.start(max(5, st["nhan_phut"]) * 60_000)
                self.peek_timer.start(PEEK_MS)
        self._paint_status_buttons()

    def _paint_status_buttons(self) -> None:
        if not hasattr(self, "auto_sync"):
            return
        st = self.auto_sync.settings()
        on = bool(st["thu_muc"]) and self.ctx.can("DongBo")
        self.sync_btn.setVisible(on)
        if on:
            n = self.auto_sync.pending_changes()
            pend = self.auto_sync.pending_choices()
            err = self.auto_sync.last_error() or ("Chưa nhập mật khẩu đồng bộ" if not self.auto_sync.ready() else "")
            color = style.color("danger") if err or pend else style.color("warn") if n else style.color("success")
            self.sync_btn.setIcon(icons.icon("sync", color, 18))
            self.sync_btn.setText("Đồng bộ ngay" + (f" ({n})" if n else ""))
            tip = [f"Đồng bộ tự động qua thư mục chung: {st['thu_muc']}",
                   f"{n} thay đổi chưa gửi" if n else "Không có thay đổi chưa gửi"]
            last = local_time(self.ctx.db.setting(sfm.K_LAST_SCAN))
            if last:
                tip.append(f"Quét nhận lần cuối {last}")
            if pend:
                tip.append(f"{sum(pend.values())} bản ghi cần chọn (mở màn Đồng bộ)")
            if err:
                tip.append("Lỗi: " + err)
            self.sync_btn.setToolTip("\n".join(tip))
        gem = GeminiService(self.ctx.db)
        state, text = gem.state()
        color = {"ok": "success", "loi": "danger"}.get(state, "muted")
        self.gem_btn.setIcon(icons.dot(style.color(color)))
        self.gem_btn.setToolTip(text + "\nBấm để cài đặt Thư mục hóa đơn và Gemini.")
        self.gem_btn.setVisible(self.ctx.can("HoaDonDauRa") or self.ctx.can("HoaDonDauVao") or self.ctx.can("TaiSan"))

    def _busy_ui(self) -> bool:
        return QApplication.activeModalWidget() is not None

    def _auto_send(self) -> None:
        try:
            if self.auto_sync.send_if_changed(self.ctx.user):
                self.statusBar().showMessage("Đã tự gửi các thay đổi vào thư mục đồng bộ chung.", 8000)
        except Exception as e:  # noqa: BLE001 - tự động: không làm gián đoạn người dùng
            self.statusBar().showMessage(f"Chưa gửi được gói đồng bộ: {e}", 15_000)
        self._paint_status_buttons()

    def _auto_receive(self, opening: bool) -> None:
        if not self.auto_sync.ready() or (not opening and self.auto_sync.settings()["tu_nhan"] is False):
            return
        if self._busy_ui():                   # đang mở hộp thoại nhập liệu: để lát nữa nhận
            QTimer.singleShot(60_000, lambda: self._auto_receive(False))
            return
        self._receive_now(show=False)

    def _peek_receive(self) -> None:
        """Máy khác vừa gửi gói vào thư mục chung thì nhận luôn, không đợi đến lượt quét định kỳ."""
        try:
            new = self.auto_sync.has_new()
        except Exception:  # noqa: BLE001 - tự động: không làm gián đoạn người dùng
            return
        if new:
            self._auto_receive(False)

    def _receive_now(self, show: bool, again: bool = False):
        try:
            r = self.auto_sync.receive(self.ctx.user, again=again)
        except Exception as e:  # noqa: BLE001
            if show:
                error(self, f"Không nhận được dữ liệu đồng bộ: {e}")
            return None
        if r.nhan:
            msg = f"Đã nhận {r.nhan} gói đồng bộ ({r.ban_ghi} bản ghi)" + (
                f", {r.cho_chon} bản ghi cần chọn ở màn Đồng bộ" if r.cho_chon else "") + "."
            self.statusBar().showMessage(msg, 20_000)
            self.refresh_all()
        elif r.loi:
            self.statusBar().showMessage("Đồng bộ tự động: " + r.loi[0], 20_000)
        self._paint_status_buttons()
        return r

    def sync_now(self) -> None:
        if not self.auto_sync.settings()["thu_muc"]:
            if confirm(self, "Chưa cài thư mục đồng bộ chung. Mở màn Đồng bộ để cài đặt?"):
                self.go_to("Đồng bộ")
            return
        if not self.auto_sync.ready():
            return error(self, "Chưa nhập mật khẩu đồng bộ trên máy này (màn Đồng bộ › Tự động qua thư mục chung).")
        sent = None
        try:
            sent = self.auto_sync.send_if_changed(self.ctx.user)
        except Exception as e:  # noqa: BLE001
            return error(self, f"Không gửi được: {e}")
        if self.auto_sync.last_error() and sent is None:
            return error(self, self.auto_sync.last_error())
        r = self._receive_now(show=True)
        if r is None:
            return
        lines = ["Đã gửi các thay đổi của máy này." if sent else "Máy này không có thay đổi mới để gửi."]
        lines.append(f"Nhận {r.nhan} gói mới ({r.ban_ghi} bản ghi)." if r.nhan else
                     "Không có gói mới từ máy khác. (Muốn nhận lại các gói đã nhận: màn Đồng bộ › "
                     "\"Quét lại thư mục đồng bộ\".)")
        if r.cho_chon:
            lines.append(f"{r.cho_chon} bản ghi cả hai máy cùng có người sửa / nghi trùng: mở màn Đồng bộ › Tự động "
                         "› \"Mở gói có bản ghi cần chọn\".")
        if r.loi:
            lines.append("Lỗi: " + "; ".join(r.loi[:3]))
        info(self, "\n".join(lines), "Đồng bộ ngay")
        if self.stack.currentWidget() is self.sync_page:
            self.sync_page.refresh()

    def receive_again(self) -> None:
        """Quét lại toàn bộ thư mục đồng bộ: nhận lại cả các gói đã nhận (bản cũ không đè bản mới hơn)."""
        if not self.auto_sync.settings()["thu_muc"]:
            return error(self, "Chưa cài thư mục đồng bộ chung.")
        if not self.auto_sync.ready():
            return error(self, "Chưa nhập mật khẩu đồng bộ trên máy này (màn Đồng bộ › Tự động qua thư mục chung).")
        r = self._receive_now(show=True, again=True)
        if r is None:
            return
        if r.loi and not r.nhan:
            return error(self, "\n".join(r.loi[:3]))
        lines = [f"Đã quét lại {r.nhan} gói của các máy khác trong thư mục đồng bộ, cập nhật {r.ban_ghi} bản ghi."
                 if r.nhan else "Thư mục đồng bộ không có gói nào của máy khác."]
        if r.cho_chon:
            lines.append(f"{r.cho_chon} bản ghi cần chọn: màn Đồng bộ › \"Mở gói có bản ghi cần chọn\".")
        if r.loi:
            lines.append("Lỗi: " + "; ".join(r.loi[:3]))
        info(self, "\n".join(lines), "Quét lại thư mục đồng bộ")
        if self.stack.currentWidget() is self.sync_page:
            self.sync_page.refresh()

    def folder_settings(self) -> None:
        dlg = FolderSettingsDialog(self.ctx, self)
        if dlg.exec() == FolderSettingsDialog.Accepted:
            self._paint_status_buttons()
            self._restart_scan_timer()
            if getattr(dlg, "changed_dirs", False):
                self.scanner.start()

    def invoice_folder(self, tab: int = 1) -> None:
        InvoiceFolderDialog(self.ctx, self, tab=tab, scanner=self.scanner).exec()
        self.refresh_all()

    def _restart_scan_timer(self) -> None:
        from ..core.invoice_folder import InvoiceFolders
        self.scan_timer.start(InvoiceFolders(self.ctx.db).every_minutes() * 60_000)

    def _auto_scan(self) -> None:
        """Quét thư mục hóa đơn (đầu ra, đầu vào) định kỳ: chỉ xử lý file mới, không có file mới thì không làm gì."""
        from ..core.invoice_folder import K_ON_OPEN, InvoiceFolders
        if self.ctx.user is not None and not self.scanner.busy and InvoiceFolders(self.ctx.db).flag(K_ON_OPEN):
            self.scanner.start()

    def _scanned(self, s: dict) -> None:
        if not s:
            return                                  # không có file mới: không làm mới màn hình
        self.statusBar().showMessage("Quét thư mục hóa đơn: " + scan_text(s), 30_000)
        if not any(s.get(k) for k in ("them", "can_xem", "co_san", "lech", "loi")):
            return
        self.refresh_all()
        if hasattr(self.home, "refresh"):
            self.home.refresh()

    def _alert_action(self, code: str) -> None:
        if code == "dong_bo":
            self.go_to("Đồng bộ")
        elif code == "thu_muc_hd":
            self.invoice_folder(1)
        elif code == "gemini":
            self.folder_settings()

    # ---------------------------------------------------------------- giao diện sáng / tối
    def toggle_theme(self):
        new = "sang" if style.current() == "toi" else "toi"
        style.apply_theme(QApplication.instance(), new)
        self.ctx.db.set_setting("giao_dien", new)
        self.refresh_all()

    # ---------------------------------------------------------------- menu tài khoản
    def _my_account(self, tab: int):
        MyAccountDialog(self.ctx, self, tab).exec()
        self.refresh_all()

    def _about(self):
        AboutDialog(self.ctx.acc, self.ctx.machine_id, self).exec()

    def _renew(self):
        from .auth_dialogs import RenewDialog

        RenewDialog(self.ctx, self).exec()
        self._paint_topbar()

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

    def _wipe(self):
        if self.finance_page:
            self.finance_page.lock()
        dlg = WipeDataDialog(self.ctx, self)
        if dlg.exec() == WipeDataDialog.Accepted:
            info(self, f"Đã xóa sạch dữ liệu ({dlg.files} tệp được ghi đè và hủy). Phần mềm sẽ đóng lại; "
                       "mở lại để bắt đầu với dữ liệu trống.", "Đã xóa sạch dữ liệu")
            self.close()

    # ---------------------------------------------------------------- tự khóa thu chi
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
        self.startup_timer.stop()
        self.idle_timer.stop()
        self.tg_timer.stop()
        self.send_timer.stop()
        self.recv_timer.stop()
        self.peek_timer.stop()
        if not self.ctx.wiped:
            try:                                    # đóng phần mềm: gửi nốt thay đổi vào thư mục chung
                self.auto_sync.send_if_changed(self.ctx.user)
            except Exception:  # noqa: BLE001 - không chặn việc đóng phần mềm
                pass
        QApplication.instance().removeEventFilter(self.activity_filter)   # đăng xuất / đăng nhập lại không cộng dồn
        if self.finance_page and not self.ctx.wiped:
            self.finance_page.lock()
        self.ctx.lock_finance()
        ev.accept()


__all__ = ["MainWindow", "Qt"]
