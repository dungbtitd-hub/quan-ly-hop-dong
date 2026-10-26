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
from .widgets import confirm, info

PAGE_ROLE = Qt.UserRole          # chỉ số trang trong stack; None = dòng tiêu đề nhóm
BADGE_ROLE = Qt.UserRole + 1     # số hiện trên huy hiệu đỏ


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
        self.bell_badge.setText(str(n) if n < 100 else "99")
        self.bell_badge.setVisible(n > 0)
        self.bell.setToolTip(f"{n} thông báo" if n else "Không có thông báo")

    def _bell_menu(self):
        dlg = NotificationsDialog(self.ctx, self)
        dlg.open_contract.connect(self.show_contract)
        dlg.open_review.connect(self.show_review)
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
        QApplication.instance().removeEventFilter(self.activity_filter)   # đăng xuất / đăng nhập lại không cộng dồn
        if self.finance_page and not self.ctx.wiped:
            self.finance_page.lock()
        self.ctx.lock_finance()
        ev.accept()


__all__ = ["MainWindow", "Qt"]
