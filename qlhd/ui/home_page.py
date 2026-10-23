"""Trang Tổng quan: thẻ số liệu, lịch 14 ngày tới, việc cần xử lý, công nợ theo hợp đồng; cài đặt nhắc hạn."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QBoxLayout, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ..core.reports import fmt_date, fmt_money
from .widgets import button, card, hint, info, page_title, tagged

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


class HomePage(QWidget):
    open_contract = Signal(str)       # Ma hợp đồng
    open_page = Signal(str)           # tên trang trong thanh điều hướng
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

        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(page_title("Tổng quan"))
        self.sub = _wrap(tagged(QLabel(), "PageSub"))          # tự xuống dòng, không đẩy rộng trang
        col.addWidget(self.sub)
        head.addLayout(col, 1)
        self.b_remind = button("Cài đặt nhắc hạn…", self.remind_settings, icon_name="bell")
        head.addWidget(self.b_remind, 0, Qt.AlignBottom)
        if ctx.can("HopDong", "Them"):
            head.addWidget(button("Thêm hợp đồng", self.add_contract.emit, "Primary"), 0, Qt.AlignBottom)
        lay.addLayout(head)

        self.stats = QGridLayout()
        self.stats.setSpacing(14)
        lay.addLayout(self.stats)

        self.days_card, dl = card("14 ngày tới")
        self.days_row = QHBoxLayout()
        self.days_row.setSpacing(6)
        dl.addLayout(self.days_row)
        lay.addWidget(self.days_card)

        bottom = self.bottom = QBoxLayout(QBoxLayout.LeftToRight)
        bottom.setSpacing(14)
        self.todo_count = tagged(QLabel(), "Pill", "danger")
        todo, tl = card("Cần xử lý", self.todo_count)
        self.todo = QVBoxLayout()
        self.todo.setSpacing(6)
        tl.addLayout(self.todo)
        tl.addStretch(1)
        bottom.addWidget(todo, 3)
        debt, gl = card("Công nợ theo hợp đồng", button("Xem báo cáo", lambda: self.open_page.emit("Báo cáo"),
                                                          "Link"))
        self.debts = QVBoxLayout()
        self.debts.setSpacing(10)
        gl.addLayout(self.debts)
        gl.addStretch(1)
        bottom.addWidget(debt, 2)
        lay.addLayout(bottom, 1)
        self.sync_info = hint("")
        lay.addWidget(self.sync_info)

    NARROW = 980      # hẹp hơn thì thẻ số liệu xếp 2 × 2, "Cần xử lý" và "Công nợ" xếp chồng

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._arrange()

    def _arrange(self):
        narrow = self.width() < self.NARROW
        self.bottom.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        frames = [self.stats.itemAt(i).widget() for i in range(self.stats.count())]
        for i, f in enumerate(frames):
            self.stats.addWidget(f, *((i // 2, i % 2) if narrow else (0, i)))
        for c in range(4):
            self.stats.setColumnStretch(c, 1 if c < (2 if narrow else 4) else 0)

    # ---- dựng các phần
    def _stat(self, i: int, label: str, value: str, note: str, tone: str | None) -> None:
        frame, cl = card()
        cl.setSpacing(4)
        cl.addWidget(_wrap(tagged(QLabel(label), "StatLabel")))
        cl.addWidget(_wrap(tagged(QLabel(value), "StatValue", tone)))
        cl.addWidget(_wrap(hint(note)))
        frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.stats.addWidget(frame, 0, i)

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
        num.setStyleSheet("font-size:13pt; font-weight:700;")
        v.addWidget(wd)
        v.addWidget(num)
        mark = tagged(QLabel(f"{len(events)} việc" if events else " "), "Hint" if not events else "Warn")
        mark.setAlignment(Qt.AlignCenter)
        v.addWidget(mark)
        f.setToolTip(fmt_date(iso) + ("\n" + "\n".join(events) if events else "\nKhông có việc đến hạn"))
        return f

    def refresh(self):
        t = date.today()
        d = self.ctx.cs.dashboard(t.isoformat())
        self.data = d
        todo_n = len(d["cv_qua_han"]) + len(d["hd_sap_het_han"])
        self.sub.setText(f"{today_text(t)} · {d['dang_thuc_hien']} hợp đồng đang thực hiện · "
                         f"{todo_n} việc cần xử lý")
        _clear(self.stats)
        late = [r for r in d["hd_sap_het_han"] if r["ConLai"] < 0]
        self._stat(0, "Sắp hết hạn", str(len(d["hd_sap_het_han"]) - len(late)),
                   f"Nhắc trước {self.ctx.cs.remind_days()} ngày" + (f" · {len(late)} đã quá hạn" if late else ""),
                   "warn" if d["hd_sap_het_han"] else None)
        self._stat(1, "Công việc quá hạn", str(len(d["cv_qua_han"])), "Chưa hoàn thành, đã qua hạn",
                   "danger" if d["cv_qua_han"] else None)
        self._stat(2, "Đang thực hiện", str(d["dang_thuc_hien"]),
                   f"{d['hd_dau_ra']} HĐ đầu ra · {d['hd_dau_vao']} HĐ đầu vào", "accent")
        self._stat(3, "Còn phải thu", fmt_money(d["con_phai_thu"]) + " đ",
                   f"{len(d['cong_no'])} hợp đồng còn công nợ", None)

        _clear(self.days_row)
        for iso, events in d["lich"]:
            self.days_row.addWidget(self._day(iso, events, t))

        _clear(self.todo)
        self.todo_count.setText(str(todo_n))
        self.todo_count.setVisible(todo_n > 0)
        items = []
        for r in d["hd_sap_het_han"]:
            items.append((r["ConLai"], f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']}",
                          f"{r['TenDoiTac']} · kết thúc {fmt_date(r['NgayKetThucDuKien'])}",
                          r["ConLaiText"], contract_tone(r["ConLai"]), r["Ma"]))
        for r in d["cv_qua_han"]:
            items.append((-r["QuaHan"], r["TenNoiDungCV"], f"Công việc của HĐ {r['SoHopDong']} · hạn "
                          f"{fmt_date(r['NgayDuKienHT'])}", f"Quá hạn {r['QuaHan']} ngày", "danger", r["MaHopDong"]))
        items.sort(key=lambda x: x[0])
        for _, title, sub, pill, tone, ma in items[:12]:
            row = _AlertRow(title, sub, pill, tone)
            row.clicked.connect(lambda ma=ma: self.open_contract.emit(ma))
            self.todo.addWidget(row)
        if len(items) > 12:
            self.todo.addWidget(hint(f"… và {len(items) - 12} việc khác."))
        if not items:
            self.todo.addWidget(tagged(QLabel("Không có việc quá hạn hay hợp đồng sắp hết hạn."), "Success"))

        _clear(self.debts)
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
            self.debts.addWidget(frame)
        if not d["cong_no"]:
            self.debts.addWidget(hint("Không còn công nợ phải thu."))

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
