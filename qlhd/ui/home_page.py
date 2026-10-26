"""Trang Tổng quan: chọn ngữ cảnh hiển thị (hợp đồng, công việc, hóa đơn ra / vào, công nợ, cảnh báo thiếu dữ liệu,
phân loại hóa đơn, lịch 14 ngày), thẻ số liệu theo kỳ; cài đặt nhắc hạn."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QBoxLayout, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ..core import alerts as al
from ..core.reports import fmt_date, fmt_money, period_range
from . import style
from .widgets import Combo, FlowLayout, button, card, hint, info, page_title, tagged

WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật")
WEEKDAYS_SHORT = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")
REMIND_PRESETS = (1, 2, 3, 7, 15, 30)


def today_text(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.strftime('%d/%m/%Y')}"


def contract_tone(left: int) -> str:
    return "danger" if left <= 0 else "warn" if left <= 7 else "accent"


def _clear(layout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            _clear(it.layout())


class ElidedLabel(QLabel):
    """Nhãn một dòng, chữ dài thì cắt "…" theo bề rộng còn lại (di chuột xem đủ) – không kéo giãn trang."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def minimumSizeHint(self):
        sz = super().minimumSizeHint()
        sz.setWidth(40)
        return sz

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = self.contentsRect()
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(r, int(self.alignment() | Qt.AlignVCenter),
                   self.fontMetrics().elidedText(self.text(), Qt.ElideRight, r.width()))


def _wrap(lbl: QLabel) -> QLabel:
    """Nhãn tự xuống dòng, không đòi bề rộng tối thiểu theo cả câu."""
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    lbl.setMinimumWidth(40)
    return lbl


class _ClickFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def mouseReleaseEvent(self, ev):
        self.clicked.emit()
        super().mouseReleaseEvent(ev)


class _Tile(QFrame):
    """Ô số liệu trên Tổng quan; bấm để mở màn hình liên quan."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, ev):
        self.clicked.emit()
        super().mouseReleaseEvent(ev)


class _AlertRow(QFrame):
    clicked = Signal()

    def __init__(self, title: str, sub: str, pill: str, tone: str, parent=None):
        super().__init__(parent)
        tagged(self, "AlertRow", tone)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 4, 6)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = _wrap(QLabel(title))
        t.setStyleSheet("font-weight:600;")
        t.setToolTip(title)
        col.addWidget(t)
        col.addWidget(_wrap(hint(sub)))
        lay.addLayout(col, 1)
        lay.addWidget(tagged(QLabel(pill), "Pill", tone), 0, Qt.AlignTop)

    def mouseReleaseEvent(self, ev):
        self.clicked.emit()
        super().mouseReleaseEvent(ev)


class RemindSettingsDialog(QDialog):
    """Số ngày nhắc trước khi hợp đồng hết hạn (áp dụng cho hợp đồng không đặt riêng)."""

    def __init__(self, days: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt nhắc hạn hợp đồng")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Nhắc trước khi hợp đồng hết hạn:"))
        row = QHBoxLayout()
        self.presets = []
        for n in REMIND_PRESETS:
            b = QPushButton(f"{n} ngày")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, n=n: self.spin.setValue(n))
            row.addWidget(b)
            self.presets.append((n, b))
        lay.addLayout(row)
        other = QHBoxLayout()
        other.addWidget(QLabel("Hoặc nhập số ngày:"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 3650)
        self.spin.setSuffix(" ngày")
        self.spin.valueChanged.connect(self._sync)
        other.addWidget(self.spin)
        other.addStretch(1)
        lay.addLayout(other)
        lay.addWidget(hint("Hợp đồng nào cần mốc nhắc khác thì đặt riêng ở ô \"Nhắc trước khi hết hạn\" khi sửa "
                           "hợp đồng. Hợp đồng đã quá hạn mà chưa hoàn thành luôn được nhắc."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.spin.setValue(days)
        self._sync(days)

    def _sync(self, v):
        for n, b in self.presets:
            b.setChecked(n == v)
            b.setObjectName("Primary" if n == v else "")
            b.style().unpolish(b)
            b.style().polish(b)

    def value(self) -> int:
        return self.spin.value()


# khung trên Tổng quan: mã → tên trên nút chọn ngữ cảnh
PANELS = [("hd", "Hợp đồng sắp hết hạn"), ("cv", "Công việc đến hạn, quá hạn"), ("ra", "Hóa đơn đầu ra"),
          ("vao", "Hóa đơn đầu vào"), ("congno", "Đã / chưa thanh toán"), ("canhbao", "Cảnh báo thiếu dữ liệu"),
          ("phanloai", "Phân loại hóa đơn"), ("lich", "Lịch 14 ngày")]
PERIODS = [("Tháng này", "thang"), ("Quý này", "quy"), ("Năm nay", "nam"), ("Tất cả", "tat_ca")]


class HomePage(QWidget):
    open_contract = Signal(str)       # Ma hợp đồng
    open_page = Signal(str)           # tên trang trong thanh điều hướng
    open_review = Signal()            # danh sách hóa đơn đầu vào cần xem lại
    add_contract = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)      # bố cục co theo bề rộng; chỉ dự phòng
        body = QWidget()
        body.setObjectName("Page")
        scroll.setWidget(body)
        outer.addWidget(scroll)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(14)

        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(page_title("Tổng quan"))
        self.sub = _wrap(tagged(QLabel(), "PageSub"))          # tự xuống dòng, không đẩy rộng trang
        head.addWidget(self.sub)
        lay.addLayout(head)
        # kỳ số liệu và các nút: hết chỗ thì xuống dòng (cửa sổ hẹp, chữ to) để không phải kéo ngang
        tools = FlowLayout(spacing=8)
        tools.addWidget(QLabel("Kỳ"))
        self.period = Combo(PERIODS, self._setting("tong_quan_ky") or "thang")
        self.period.setToolTip("Kỳ tính số liệu hóa đơn đầu ra, đầu vào")
        self.period.currentIndexChanged.connect(self._period_changed)
        tools.addWidget(self.period)
        self.b_remind = button("Cài đặt nhắc hạn…", self.remind_settings, icon_name="bell")
        tools.addWidget(self.b_remind)
        if ctx.can("HopDong", "Them"):
            tools.addWidget(button("Thêm hợp đồng", self.add_contract.emit, "Primary"))
        tools_box = QWidget()
        tools_box.setLayout(tools)
        tools_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(tools_box)

        chips = FlowLayout()
        chips.addWidget(tagged(QLabel("Hiển thị:"), "CardTitle"))
        shown = self._shown()
        self.chips: dict[str, QPushButton] = {}
        for key, label in PANELS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(key in shown)
            b.setObjectName("Chip")
            b.toggled.connect(lambda _=False: self._chips_changed())
            chips.addWidget(b)
            self.chips[key] = b
        self.chip_box = QWidget()
        self.chip_box.setLayout(chips)
        self.chip_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.chip_box)
        lay.addWidget(hint("Bấm để bật / tắt từng khung; lựa chọn được nhớ theo tài khoản. Bấm vào từng dòng để mở "
                           "đúng hợp đồng hoặc danh sách liên quan."))

        self._narrow = False
        self._arrange_timer = QTimer(self)
        self._arrange_timer.setSingleShot(True)
        self._arrange_timer.setInterval(0)
        self._arrange_timer.timeout.connect(self._arrange)
        self.stats = QGridLayout()
        self.stats.setSpacing(14)
        lay.addLayout(self.stats)

        self.days_card, dl = card("14 ngày tới")
        self.days_row = QHBoxLayout()
        self.days_row.setSpacing(6)
        dl.addLayout(self.days_row)
        lay.addWidget(self.days_card)

        # hai cột khung; cửa sổ hẹp thì xếp chồng
        bottom = self.bottom = QBoxLayout(QBoxLayout.LeftToRight)
        bottom.setSpacing(14)
        self.cols = [QVBoxLayout(), QVBoxLayout()]
        for c in self.cols:
            c.setSpacing(14)
            bottom.addLayout(c, 1)
        lay.addLayout(bottom, 1)
        lay.addStretch(1)
        self.sync_info = hint("")
        lay.addWidget(self.sync_info)

    NARROW = 980      # hẹp hơn thì thẻ số liệu xếp 2 cột, các khung xếp chồng

    # ---- cài đặt theo tài khoản
    def _setting(self, name: str) -> str | None:
        return self.ctx.db.setting(f"{name}:{self.ctx.user['TenDangNhap']}")

    def _shown(self) -> set[str]:
        raw = self._setting("tong_quan_khung")
        return set(raw.split(",")) if raw is not None else {k for k, _ in PANELS}

    def _chips_changed(self):
        keys = [k for k, b in self.chips.items() if b.isChecked()]
        self.ctx.db.set_setting(f"tong_quan_khung:{self.ctx.user['TenDangNhap']}", ",".join(keys))
        self.refresh()

    def _period_changed(self):
        self.ctx.db.set_setting(f"tong_quan_ky:{self.ctx.user['TenDangNhap']}", self.period.value())
        self.refresh()

    def period_range(self) -> tuple[str, str, str]:
        ky, t = self.period.value(), date.today()
        if ky == "tat_ca":
            return "0000-01-01", "9999-12-31", "mọi thời gian"
        tu, den = period_range(ky, t)
        name = {"thang": f"tháng {t.month}/{t.year}", "quy": f"quý {(t.month - 1) // 3 + 1}/{t.year}",
                "nam": f"năm {t.year}"}[ky]
        return tu, den, name

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # không sắp lại bố cục ngay trong lúc Qt đang co giãn (sửa layout giữa chừng có thể làm phần mềm thoát đột ngột)
        if (self.width() < self.NARROW) != self._narrow:
            self._arrange_timer.start()

    def _per(self) -> int:
        return 2 if self._narrow else 4

    def _arrange(self, force: bool = False):
        narrow = self.width() < self.NARROW
        if narrow == self._narrow and not force:
            return
        self._narrow = narrow
        self.bottom.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        frames = [self.stats.itemAt(i).widget() for i in range(self.stats.count())]
        for f in frames:
            self.stats.removeWidget(f)
        per = self._per()
        for i, f in enumerate(frames):
            self.stats.addWidget(f, i // per, i % per)
        for c in range(4):
            self.stats.setColumnStretch(c, 1 if c < per else 0)

    # ---- dựng các phần
    def _stat(self, i: int, label: str, value: str, note: str, tone: str | None, on_click=None) -> None:
        frame = _Tile()
        frame.setObjectName("Card")
        cl = QVBoxLayout(frame)
        cl.setContentsMargins(16, 12, 16, 14)
        cl.setSpacing(4)
        cl.addWidget(_wrap(tagged(QLabel(label), "StatLabel")))
        cl.addWidget(_wrap(tagged(QLabel(value), "StatValue", tone)))
        cl.addWidget(_wrap(hint(note)))
        frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if on_click:
            frame.clicked.connect(on_click)
        self.stats.addWidget(frame, i // self._per(), i % self._per())

    def _day(self, iso: str, events: list[str], today: date) -> QFrame:
        d = date.fromisoformat(iso)
        f = QFrame()
        f.setObjectName("Day")
        f.setProperty("today", d == today)
        f.setProperty("busy", bool(events))
        f.setMinimumWidth(30)
        f.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v = QVBoxLayout(f)
        v.setContentsMargins(4, 6, 4, 6)
        v.setSpacing(0)
        wd = tagged(QLabel(WEEKDAYS_SHORT[d.weekday()]), "Hint")
        wd.setMinimumWidth(0)
        wd.setAlignment(Qt.AlignCenter)
        num = QLabel(str(d.day))
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"font-size:{style.pt(13)}; font-weight:700;")
        v.addWidget(wd)
        v.addWidget(num)
        mark = tagged(QLabel(f"{len(events)} việc" if events else " "), "Hint" if not events else "Warn")
        mark.setAlignment(Qt.AlignCenter)
        v.addWidget(mark)
        f.setToolTip(fmt_date(iso) + ("\n" + "\n".join(events) if events else "\nKhông có việc đến hạn"))
        return f

    def _panel(self, title: str, *header) -> QVBoxLayout:
        frame, fl = card(title, *header)
        frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col = self.cols[self._next % 2]
        self._next += 1
        col.addWidget(frame)
        return fl

    def _alert_rows(self, fl: QVBoxLayout, items: list, empty: str, limit: int = 8) -> None:
        for a in items[:limit]:
            row = _AlertRow(a.tieu_de, a.chi_tiet, a.nhan, a.muc)
            if a.ma_hd:
                row.clicked.connect(lambda ma=a.ma_hd: self.open_contract.emit(ma))
            elif a.loai == "hddv_xem_lai":
                row.clicked.connect(self.open_review.emit)
            fl.addWidget(row)
        if len(items) > limit:
            fl.addWidget(hint(f"… và {len(items) - limit} mục khác (xem ở chuông Thông báo)."))
        if not items:
            fl.addWidget(tagged(QLabel(empty), "Success"))

    def refresh(self):
        t = date.today()
        today = t.isoformat()
        d = self.ctx.cs.dashboard(today)
        self.data = d
        shown = {k for k, b in self.chips.items() if b.isChecked()}
        can_hd = self.ctx.can("HopDong")
        can_ts = self.ctx.can("TaiSan")
        types = [k for k in al.ALERT_TYPES if (k == "hddv_xem_lai" and can_ts) or (k != "hddv_xem_lai" and can_hd)]
        items = al.collect(self.ctx.db, today, types)
        by = {k: [a for a in items if a.loai == k] for k in al.ALERT_TYPES}
        tu, den, ky_name = self.period_range()
        ov = al.period_overview(self.ctx.db, tu, den)
        n_review = sum(a.so_tien for a in by["hddv_xem_lai"])
        todo_n = len(by["cv_qua_han"]) + len(by["hd_het_han"])
        self.sub.setText(f"{today_text(t)} · {d['dang_thuc_hien']} hợp đồng đang thực hiện · {todo_n} việc cần xử lý"
                         + (f" · {len(by['thieu_du_lieu'])} hợp đồng thiếu dữ liệu" if by["thieu_du_lieu"] else ""))

        _clear(self.stats)
        late = [r for r in d["hd_sap_het_han"] if r["ConLai"] < 0]
        open_hd = lambda: self.open_page.emit("Hợp đồng")      # noqa: E731
        tiles = [
            ("Đang thực hiện", str(d["dang_thuc_hien"]), f"{d['hd_dau_ra']} HĐ đầu ra · {d['hd_dau_vao']} HĐ đầu vào",
             "accent", open_hd),
            ("Hợp đồng sắp hết hạn", str(len(d["hd_sap_het_han"]) - len(late)),
             f"Nhắc trước {self.ctx.cs.remind_days()} ngày" + (f" · {len(late)} đã quá hạn" if late else ""),
             "warn" if d["hd_sap_het_han"] else None, open_hd),
            ("Công việc sắp đến hạn", str(len(by["cv_sap_han"])), f"Trong {al.DUE_SOON_DAYS} ngày tới",
             "warn" if by["cv_sap_han"] else None, open_hd),
            ("Công việc quá hạn", str(len(by["cv_qua_han"])), "Chưa hoàn thành, đã qua hạn",
             "danger" if by["cv_qua_han"] else None, open_hd),
            ("Còn phải thu", fmt_money(d["con_phai_thu"]) + " đ",
             f"{len(by['hd_chua_thu'])} hóa đơn đầu ra chưa thu đủ", None, lambda: self.open_page.emit("Hóa đơn đầu ra")),
            ("Hóa đơn đầu vào " + ky_name, str(ov["vao"]["n"]), f"{ov['vao']['chua_n']} chưa có sao kê thanh toán",
             None, lambda: self.open_page.emit("Hóa đơn đầu vào")),
            ("Hóa đơn cần xem lại phân loại", str(n_review), "Chưa phân loại, chưa rõ nhóm, nghi sai, nghi trùng",
             "warn" if n_review else None, self.open_review.emit),
            ("Hợp đồng thiếu dữ liệu", str(len(by["thieu_du_lieu"])), "Thiếu thời gian thực hiện hoặc giá trị",
             "warn" if by["thieu_du_lieu"] else None, open_hd),
        ]
        if not can_ts:
            tiles = [x for x in tiles if not x[0].startswith("Hóa đơn cần xem lại")]
        for i, (label, value, note, tone, cb) in enumerate(tiles):
            self._stat(i, label, value, note, tone, cb)

        self.days_card.setVisible("lich" in shown)
        _clear(self.days_row)
        for iso, events in d["lich"]:
            self.days_row.addWidget(self._day(iso, events, t))

        for c in self.cols:
            _clear(c)
        self._next = 0
        if "canhbao" in shown and can_hd:
            fl = self._panel("Hợp đồng thiếu dữ liệu", tagged(QLabel(str(len(by["thieu_du_lieu"]))), "Pill", "warn"))
            self._alert_rows(fl, by["thieu_du_lieu"], "Hợp đồng đang thực hiện đã đủ dữ liệu chính.")
            n_free = ov["vao"]["khong_hd"]
            if n_free:
                fl.addWidget(hint(f"{n_free} hóa đơn đầu vào {ky_name} chưa gắn hợp đồng (chi phí chung hoặc cần chọn "
                                  "hợp đồng)."))
            if by["hd_lech_gia_tri"]:
                fl = self._panel("Giá trị hợp đồng lệch hóa đơn",
                                 tagged(QLabel(str(len(by["hd_lech_gia_tri"]))), "Pill", "danger"))
                self._alert_rows(fl, by["hd_lech_gia_tri"], "")
                fl.addWidget(hint("Có thể nhập sai giá trị hợp đồng. Lọc ở Hợp đồng → \"Giá trị HĐ so với hóa đơn\"."))
            if by["tt_trung"] or by["thu_thua"]:
                n = len(by["tt_trung"]) + len(by["thu_thua"])
                fl = self._panel("Thu thừa, thanh toán nghi ghi trùng", tagged(QLabel(str(n)), "Pill", "danger"))
                self._alert_rows(fl, by["thu_thua"] + by["tt_trung"], "")
                fl.addWidget(hint("Bấm từng dòng để mở hợp đồng; lần thanh toán nghi trùng tô đỏ ở thẻ Thanh toán."))
        if "phanloai" in shown and can_ts:
            fl = self._panel("Hóa đơn đầu vào cần kế toán xem lại",
                             button("Mở danh sách", self.open_review.emit, "Link"))
            self._alert_rows(fl, by["hddv_xem_lai"], "Không có dòng nào cần xem lại.")
            fl.addWidget(hint("Phần mềm chỉ tự chọn khi tên hàng khớp rõ một nhóm; chưa rõ thì để trống, tô màu, kèm "
                              "gợi ý cho kế toán xác nhận."))
        if "cv" in shown and can_hd:
            n = len(by["cv_qua_han"]) + len(by["cv_sap_han"])
            fl = self._panel("Công việc sắp đến hạn và quá hạn", tagged(QLabel(str(n)), "Pill", "danger"))
            self._alert_rows(fl, by["cv_qua_han"] + by["cv_sap_han"], "Không có công việc quá hạn hay sắp đến hạn.")
        if "hd" in shown and can_hd:
            fl = self._panel("Hợp đồng sắp hết hạn, quá hạn", tagged(QLabel(str(len(by["hd_het_han"]))), "Pill", "warn"))
            self._alert_rows(fl, by["hd_het_han"], "Không có hợp đồng sắp hết hạn.")
        if "ra" in shown and can_hd:
            ra = ov["ra"]
            fl = self._panel(f"Hóa đơn đầu ra {ky_name}")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(ra["da"] * 100 / ra["tong"]) if ra["tong"] else 0)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            fl.addWidget(bar)
            fl.addWidget(_wrap(QLabel(f"Đã thanh toán: {ra['da_n']} HĐ · {fmt_money(ra['da'])} đ   ·   Chưa thanh toán: "
                                      f"{ra['chua_n']} HĐ · {fmt_money(ra['chua'])} đ")))
            for r in ra["ds_chua"][:6]:
                row = _AlertRow(f"Hóa đơn {r['SoHoaDon']} · HĐ {r['SoHopDong']}", f"Xuất {fmt_date(r['NgayXuatHD'])}",
                                fmt_money(r["ConLai"]) + " đ", "warn")
                row.clicked.connect(lambda ma=r["MaHopDong"]: self.open_contract.emit(ma))
                fl.addWidget(row)
            if not ra["n"]:
                fl.addWidget(hint("Chưa có hóa đơn đầu ra trong kỳ."))
        if "vao" in shown and self.ctx.can("HoaDonDauVao"):
            v = ov["vao"]
            fl = self._panel(f"Hóa đơn đầu vào {ky_name}")
            fl.addWidget(_wrap(QLabel(f"Tổng số: {v['n']} HĐ · {fmt_money(v['tong'])} đ   ·   Đã có sao kê thanh toán: "
                                      f"{v['da_n']}   ·   Chưa thanh toán: {v['chua_n']}")))
            if v["nhom"]:
                fl.addWidget(_wrap(hint("Theo nhóm: " + " · ".join(f"{g[0]} {g[1]} HĐ ({fmt_money(g[2])} đ)"
                                                                   for g in v["nhom"][:8]))))
        if "congno" in shown and can_hd:
            fl = self._panel("Công nợ theo hợp đồng", button("Xem báo cáo", lambda: self.open_page.emit("Báo cáo"),
                                                              "Link"))
            self.debts = fl
            for r in d["cong_no"][:6]:
                frame = _ClickFrame()
                frame.setToolTip("Bấm để mở hợp đồng")
                frame.clicked.connect(lambda ma=r["Ma"]: self.open_contract.emit(ma))
                box = QVBoxLayout(frame)
                box.setContentsMargins(0, 0, 0, 0)
                box.setSpacing(3)
                top = QHBoxLayout()
                name = ElidedLabel(f"{r['SoHopDong']} – {r['TenHopDong']}")
                name.setToolTip(f"{r['SoHopDong']} – {r['TenHopDong']}\n{r['TenDoiTac']}")
                top.addWidget(name, 1)
                top.addWidget(tagged(QLabel(fmt_money(r["ConPhaiThu"]) + " đ"), "Warn"))
                box.addLayout(top)
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(int(min(r["PhanTramTT"], 100)))
                bar.setTextVisible(False)
                bar.setFixedHeight(8)
                box.addWidget(bar)
                box.addWidget(_wrap(hint(f"Đã thu {r['PhanTramTT']:.0f}% · {fmt_money(r['DaThanhToan'])} / "
                                         f"{fmt_money(r['GiaTriHD'])} đ")))
                fl.addWidget(frame)
            if not d["cong_no"]:
                fl.addWidget(hint("Không còn công nợ phải thu."))
            adv = by["tam_ung"]
            if adv:
                fl.addWidget(hint(f"{len(adv)} hợp đồng còn tạm ứng chưa khấu trừ, tổng "
                                  f"{fmt_money(sum(a.so_tien for a in adv))} đ."))
        for c in self.cols:
            c.addStretch(1)

        self._arrange()
        partners = self.ctx.sync.partners()
        if partners:
            p = partners[0]
            self.sync_info.setText(f"Lần đồng bộ gần nhất: {p['TenMay']} lúc {fmt_date(p['LanCuoi'][:10])}.")
        else:
            self.sync_info.setText("Chưa đồng bộ với máy nào.")

    def remind_settings(self):
        dlg = RemindSettingsDialog(self.ctx.cs.remind_days(), self)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.cs.set_remind_days(dlg.value())
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Cài đặt nhắc hạn", f"{dlg.value()} ngày")
            self.refresh()
            info(self, f"Đã lưu: nhắc trước {dlg.value()} ngày khi hợp đồng sắp hết hạn.")


def reminder_text(ctx, today: str) -> str | None:
    """Nội dung nhắc khi đăng nhập (mỗi người một lần mỗi ngày); None nếu không có gì cần nhắc."""
    rows = ctx.cs.expiring(today)
    if not rows:
        return None
    lines = [f"• {r['SoHopDong']} – {r['TenHopDong']}: {r['ConLaiText'].lower()} "
             f"(kết thúc {fmt_date(r['NgayKetThucDuKien'])})" for r in rows[:10]]
    if len(rows) > 10:
        lines.append(f"… và {len(rows) - 10} hợp đồng khác.")
    return f"Có {len(rows)} hợp đồng sắp hết hạn hoặc đã quá hạn:\n\n" + "\n".join(lines)


__all__ = ["HomePage", "RemindSettingsDialog", "reminder_text"]
