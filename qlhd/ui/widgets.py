"""Ô nhập tiền, ngày, bảng dữ liệu, hộp thoại biểu mẫu dùng chung."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QStyle, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QHBoxLayout, QLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import duplicates as dupl
from ..core.reports import fmt_date, fmt_money
from . import style
from .icons import icon, icon_for_text

BUTTON_ICON_COLOR = "#7b8494"

EMPTY_DATE = QDate(1900, 1, 1)


# ---------------------------------------------------------------- thông báo
def info(parent, text: str, title: str = "Thông báo") -> None:
    QMessageBox.information(parent, title, text)


def error(parent, text: str, title: str = "Lỗi") -> None:
    QMessageBox.warning(parent, title, text)


def confirm(parent, text: str, title: str = "Xác nhận") -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def confirm_changes(parent, changes: list[tuple[str, str, str]], what: str = "") -> bool:
    """Sửa dữ liệu đã có: liệt kê thay đổi (cũ → mới) và hỏi xác nhận trước khi lưu, tránh sửa nhầm."""
    lines = [f"• {label}: {old or '(trống)'} → {new or '(trống)'}" for label, old, new in changes[:15]]
    more = f"\n… và {len(changes) - 15} thay đổi khác." if len(changes) > 15 else ""
    return confirm(parent, f"Lưu thay đổi{(' ' + what) if what else ''}?\n\n" + "\n".join(lines) + more,
                   "Xác nhận sửa dữ liệu")


class DeleteDialog(QDialog):
    """Hỏi kỹ trước khi xóa: nêu rõ xóa gì, kéo theo gì, có hoàn tác được không; phải tích đồng ý mới bấm Xóa được."""

    def __init__(self, what: str, details=(), *, permanent: bool = True, count: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Xác nhận xóa")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        head = QLabel(f"Bạn sắp xóa {what}.")
        head.setWordWrap(True)
        head.setStyleSheet("font-weight:600;")
        lay.addWidget(head)
        details = [d for d in details if d]
        if details:
            lay.addWidget(hint("\n".join(f"• {d}" for d in details[:15])
                               + (f"\n… và {len(details) - 15} mục khác." if len(details) > 15 else "")))
        warn = QLabel("Xóa vĩnh viễn, không thể hoàn tác. Nên xuất Excel hoặc sao lưu trước nếu cần giữ."
                      if permanent else "Có thể khôi phục lại sau ở mục đã xóa.")
        warn.setWordWrap(True)
        warn.setObjectName("Danger" if permanent else "Hint")
        lay.addWidget(warn)
        self.agree = QCheckBox(f"Tôi đã kiểm tra, đồng ý xóa {count} mục này" if count > 1
                               else "Tôi đã kiểm tra, đồng ý xóa")
        lay.addWidget(self.agree)
        bb = QDialogButtonBox()
        self.ok = bb.addButton("Xóa", QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("Danger")
        cancel = bb.addButton("Không xóa", QDialogButtonBox.RejectRole)
        self.ok.setEnabled(False)
        self.ok.setAutoDefault(False)
        cancel.setDefault(True)
        self.agree.toggled.connect(self.ok.setEnabled)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


def confirm_delete(parent, what: str, details=(), *, permanent: bool = True, count: int = 1) -> bool:
    """Mọi thao tác xóa đều qua đây (hỏi kỹ, mặc định là Không xóa)."""
    return DeleteDialog(what, details, permanent=permanent, count=count, parent=parent).exec() == QDialog.Accepted


class DuplicateDialog(QDialog):
    """Dữ liệu định thêm giống dữ liệu đã có: không tự thêm. Liệt kê bản ghi giống kèm lý do; chỉ thêm khi người nhập
    tích đã rà soát. Mặc định là Không thêm. items: [(nhãn, [lý do…])] (nhập nhiều dòng thì tích từng dòng)."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nghi trùng dữ liệu đã có")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        many = len(items) > 1
        head = QLabel(f"Có {len(items)} mục giống dữ liệu đã có trong phần mềm, chưa được thêm." if many
                      else f"{items[0][0]} giống dữ liệu đã có trong phần mềm, chưa được thêm.")
        head.setWordWrap(True)
        head.setStyleSheet("font-weight:600;")
        lay.addWidget(head)
        self.list = QListWidget()
        for label, reasons in items:
            it = QListWidgetItem(f"{label}\n" + "\n".join(f"   • {x}" for x in reasons[:6]))
            if many:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked)
            self.list.addItem(it)
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 1)
        lay.addWidget(hint("Kiểm tra lại: nếu đúng là đã nhập rồi thì bấm Không thêm. Chỉ thêm khi chắc chắn đây là "
                           "dữ liệu khác (VD hai lần thanh toán riêng biệt cùng số tiền)."
                           + (" Tích những mục muốn thêm." if many else "")))
        self.agree = QCheckBox("Tôi đã rà soát, đây không phải dữ liệu trùng")
        lay.addWidget(self.agree)
        bb = QDialogButtonBox()
        self.ok = bb.addButton("Vẫn thêm", QDialogButtonBox.AcceptRole)
        cancel = bb.addButton("Không thêm", QDialogButtonBox.RejectRole)
        self.ok.setEnabled(False)
        self.ok.setAutoDefault(False)
        cancel.setDefault(True)
        self.agree.toggled.connect(self.ok.setEnabled)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self) -> list[int]:
        if self.list.count() == 1:
            return [0]
        return [i for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]


def confirm_duplicate(parent, e) -> bool:
    """Hỏi một mục nghi trùng (DuplicateWarning): True nếu người nhập rà soát và chọn vẫn thêm."""
    return DuplicateDialog([(e.what, e.matches)], parent).exec() == QDialog.Accepted


def review_duplicates(parent, items) -> int:
    """Nhập nhiều dòng: items [(nhãn, lý do, hàm lưu, dữ liệu)]. Lưu các dòng được tích sau rà soát; trả số đã lưu."""
    if not items:
        return 0
    dlg = DuplicateDialog([(label, reasons) for label, reasons, _, _ in items], parent)
    if dlg.exec() != QDialog.Accepted:
        return 0
    n = 0
    for i in dlg.chosen():
        _, _, fn, v = items[i]
        if run_safely(parent, fn, dict(v, **{dupl.CONFIRM_KEY: True})) is not None:
            n += 1
    return n


def ask_secret(parent, title: str, label: str) -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.Password)
    return text if ok else None


def ask_text(parent, title: str, label: str, value: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.Normal, value)
    return text.strip() if ok and text.strip() else None


def require(cond, message: str) -> None:
    if not cond:
        raise ValueError(message)


def local_time(iso: str | None) -> str:
    """Thời điểm lưu (UTC) → giờ máy này, dạng HH:MM dd/mm/yyyy."""
    from datetime import datetime
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M %d/%m/%Y")
    except ValueError:
        return iso[:16].replace("T", " ")


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def run_safely(parent, fn, *args, **kwargs):
    """Chạy một thao tác, hiện lỗi nghiệp vụ thành hộp thoại thay vì làm dừng phần mềm."""
    while True:
        try:
            return fn(*args, **kwargs)
        except dupl.DuplicateWarning as e:     # giống dữ liệu đã có: rà soát xác nhận mới thêm
            values = next((a for a in args if isinstance(a, dict)), None)
            if values is None or not confirm_duplicate(parent, e):
                return None
            args = tuple(dict(a, **{dupl.CONFIRM_KEY: True}) if a is values else a for a in args)
        except Exception as e:  # noqa: BLE001
            error(parent, str(e))
            return None


# ---------------------------------------------------------------- ô nhập
class MoneyEdit(QLineEdit):
    valueChanged = Signal(int)

    def __init__(self, value: int = 0, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignRight)
        self.setPlaceholderText("0")
        self.textEdited.connect(self._reformat)
        self.set_value(value)

    def _reformat(self, text: str) -> None:
        digits = "".join(ch for ch in text if ch.isdigit())
        neg = text.strip().startswith("-")
        new = ("-" if neg else "") + (fmt_money(int(digits)) if digits else "")
        if new != text:
            pos = self.cursorPosition() + (len(new) - len(text))
            self.setText(new)
            self.setCursorPosition(max(pos, 0))
        self.valueChanged.emit(self.value())

    def value(self) -> int:
        digits = "".join(ch for ch in self.text() if ch.isdigit())
        v = int(digits) if digits else 0
        return -v if self.text().strip().startswith("-") else v

    def set_value(self, v) -> None:
        self.setText(fmt_money(v) if v else "")


class DateEdit(QDateEdit):
    """Ô ngày dd/MM/yyyy có lịch; optional=True cho phép để trống."""

    def __init__(self, value: str | None = None, optional: bool = False, parent=None):
        super().__init__(parent)
        self.optional = optional
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd/MM/yyyy")
        if optional:
            self.setMinimumDate(EMPTY_DATE)
            self.setSpecialValueText(" ")
        self.set_value(value if value or optional else date.today().isoformat())

    def value(self) -> str | None:
        if self.optional and self.date() == EMPTY_DATE:
            return None
        return self.date().toString("yyyy-MM-dd")

    def set_value(self, iso: str | None) -> None:
        if not iso:
            self.setDate(EMPTY_DATE if self.optional else QDate.currentDate())
        else:
            self.setDate(QDate.fromString(iso[:10], "yyyy-MM-dd"))


class Combo(QComboBox):
    def __init__(self, options=(), value=None, parent=None):
        super().__init__(parent)
        self.set_options(options, value)

    def set_options(self, options, value=None) -> None:
        # Giữ nguyên trạng thái chặn tín hiệu của nơi gọi: nếu nơi gọi đã chặn
        # (VD FilterBox.set_options trong refresh) thì không được mở chặn ở đây,
        # nếu không set_value sẽ phát tín hiệu -> refresh lại -> lặp vô hạn.
        was_blocked = self.blockSignals(True)
        self.clear()
        for opt in options:
            label, data = opt if isinstance(opt, tuple) else (opt, opt)
            self.addItem(label, data)
        self.blockSignals(was_blocked)
        if value is not None:
            self.set_value(value)

    def value(self):
        return self.currentData()

    def set_value(self, value) -> None:
        i = self.findData(value)
        if i >= 0:
            self.setCurrentIndex(i)


class EditCombo(QComboBox):
    """Ô gõ tự do kèm danh sách gợi ý (VD tên nhân viên mua hàng)."""

    def __init__(self, suggestions=(), value=None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.addItems([""] + [x for x in suggestions if x])
        self.set_value(value)

    def value(self) -> str:
        return self.currentText().strip()

    def set_value(self, value) -> None:
        self.setEditText(value or "")


class CheckList(QListWidget):
    """Danh sách có ô tích để chọn nhiều."""

    def __init__(self, options=(), checked=(), parent=None):
        super().__init__(parent)
        self.set_options(options, checked)

    def set_options(self, options, checked=()) -> None:
        self.clear()
        for opt in options:
            label, data = opt if isinstance(opt, tuple) else (opt, opt)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, data)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if data in checked else Qt.Unchecked)
            self.addItem(it)

    def values(self) -> list:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())
                if self.item(i).checkState() == Qt.Checked]


class PickDialog(QDialog):
    """Bảng chọn nhiều bật lên: rộng, tìm theo tên / MST, chọn tất cả, bỏ chọn, chỉ hiện mục đã chọn."""

    def __init__(self, title: str, options: list[tuple], checked, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 560)
        self.options = options
        self.on = set(checked)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Gõ tên hoặc MST để tìm…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        top.addWidget(self.search, 1)
        b_all = QPushButton("Chọn tất cả (đang hiện)")
        b_all.clicked.connect(lambda: self._set_shown(True))
        b_none = QPushButton("Bỏ chọn hết")
        b_none.clicked.connect(self._clear)
        self.only = QCheckBox("Chỉ hiện mục đã chọn")
        self.only.toggled.connect(self._fill)
        for w in (b_all, b_none, self.only):
            top.addWidget(w)
        lay.addLayout(top)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "Tên", "MST / ghi chú"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.count = QLabel()
        bottom.addWidget(self.count, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Đồng ý")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bottom.addWidget(bb)
        lay.addLayout(bottom)
        self._fill()

    @staticmethod
    def split(label: str) -> tuple[str, str]:
        name, sep, rest = str(label).rpartition(" – ")
        return (name, rest) if sep else (str(label), "")

    def shown(self) -> list[tuple]:
        q = self.search.text().strip().lower()
        return [(label, data) for label, data in self.options
                if (not q or q in str(label).lower()) and (not self.only.isChecked() or data in self.on)]

    def _fill(self, *_):
        self.table.blockSignals(True)
        rows = self.shown()
        self.table.setRowCount(len(rows))
        for i, (label, data) in enumerate(rows):
            name, extra = self.split(label)
            c = QTableWidgetItem()
            c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            c.setCheckState(Qt.Checked if data in self.on else Qt.Unchecked)
            c.setData(Qt.UserRole, data)
            self.table.setItem(i, 0, c)
            for col, text in ((1, name), (2, extra)):
                it = QTableWidgetItem(text)
                it.setToolTip(str(label))
                self.table.setItem(i, col, it)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        self._count()

    def _count(self):
        self.count.setText(f"{len(self.on)} / {len(self.options)} mục đã chọn · không chọn mục nào = lấy tất cả")

    def _item_changed(self, it):
        if it.column() != 0:
            return
        data = it.data(Qt.UserRole)
        if it.checkState() == Qt.Checked:
            self.on.add(data)
        else:
            self.on.discard(data)
        self._count()

    def _cell_clicked(self, row, col):
        if col:                                   # bấm vào tên cũng tích / bỏ tích
            c = self.table.item(row, 0)
            c.setCheckState(Qt.Unchecked if c.checkState() == Qt.Checked else Qt.Checked)

    def _set_shown(self, on: bool):
        for _, data in self.shown():
            (self.on.add if on else self.on.discard)(data)
        self._fill()

    def _clear(self):
        self.on.clear()
        self._fill()

    def values(self) -> list:
        return [d for _, d in self.options if d in self.on]


class PickButton(QPushButton):
    """Ô chọn nhiều dạng nút: hiện "Tất cả" hoặc số mục đã chọn; bấm vào bật bảng chọn (PickDialog).
    Cùng cách dùng với CheckList (set_options / values) để thay thế trực tiếp."""

    changed = Signal()
    OFF_TIP = "Không dùng cho ngữ cảnh đang chọn"

    def __init__(self, options=(), checked=(), title: str = "Chọn", parent=None):
        super().__init__(parent)
        self.title = title
        self._options: list[tuple] = []
        self._on: list = []
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QPushButton{text-align:left;padding:6px 10px}"
                           "QPushButton[chon=\"true\"]{background:#eef4ff;border:1px solid #1d4ed8;color:#1e3a8a;"
                           "font-weight:600}"
                           "QPushButton:disabled{color:#9ca3af;border:1px dashed #d1d5db}")
        self.clicked.connect(self.pick)
        self.set_options(options, checked)

    def set_options(self, options, checked=()) -> None:
        self._options = [opt if isinstance(opt, tuple) else (opt, opt) for opt in options]
        keys = {d for _, d in self._options}
        self._on = [d for _, d in self._options if d in set(checked)] if keys else []
        self._show()

    def values(self) -> list:
        return list(self._on)

    def set_values(self, values) -> None:
        self.set_options(self._options, values)
        self.changed.emit()

    def labels(self) -> list[str]:
        on = set(self._on)
        return [str(label) for label, d in self._options if d in on]

    def _show(self) -> None:
        if not self.isEnabled():
            self.setText("Không dùng cho ngữ cảnh này")
            self.setToolTip(self.OFF_TIP)
            return
        names = self.labels()
        if not names:
            self.setText("Tất cả (bấm để chọn)…")
            self.setToolTip("Chưa lọc: lấy tất cả. Bấm để mở bảng chọn.")
        else:
            short = ", ".join(PickDialog.split(n)[0] for n in names[:2]) + ("…" if len(names) > 2 else "")
            self.setText(f"✔ {len(names)} đã chọn: {short}")
            self.setToolTip("\n".join(names))
        self.setProperty("chon", bool(names))
        self.style().unpolish(self)
        self.style().polish(self)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self._show()

    def pick(self) -> None:
        dlg = PickDialog(self.title, self._options, self._on, self)
        if dlg.exec() == QDialog.Accepted:
            self._on = dlg.values()
            self._show()
            self.changed.emit()


# ---------------------------------------------------------------- bảng
class NumItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other):
        if isinstance(other, NumItem):
            a, b = self.sort_value, other.sort_value
            try:
                return (a if a is not None else 0) < (b if b is not None else 0)
            except TypeError:
                return str(a or "") < str(b or "")
        return super().__lt__(other)


class PercentDelegate(QStyledItemDelegate):
    """Cột % thanh toán vẽ thành thanh tiến độ kèm số."""

    def paint(self, painter, option, index):
        value = index.data(Qt.UserRole + 1)
        if value in (None, ""):
            return super().paint(painter, option, index)
        self.initStyleOption(option, index)
        option.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)
        r = option.rect.adjusted(8, 0, -8, 0)
        bar = QRectF(r.left(), r.center().y() - 3, max(r.width() - 46, 20), 6)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(style.color("border")))
        painter.drawRoundedRect(bar, 3, 3)
        done = QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(float(value), 100.0)) / 100, bar.height())
        painter.setBrush(QColor(style.color("success")))
        painter.drawRoundedRect(done, 3, 3)
        painter.setPen(QColor(style.color("text")))
        painter.drawText(QRectF(bar.right() + 6, r.top(), 44, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                         f"{float(value):.0f}%")
        painter.restore()


class _TotalsBar(QTableWidget):
    """Dòng TỔNG ghim dưới đáy bảng: cùng độ rộng cột với bảng, cuộn ngang theo bảng."""

    def __init__(self, table: "DataTable"):
        super().__init__(table)
        self.setObjectName("TotalsBar")
        self.horizontalHeader().setVisible(False)
        self.verticalHeader().setVisible(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFrameShape(QFrame.NoFrame)
        self.setShowGrid(True)


class DataTable(QTableWidget):
    """Bảng hiển thị danh sách dict theo cột (khóa, tiêu đề, kiểu)."""

    activated_row = Signal(dict)
    group_activated = Signal(tuple)      # bấm đúp một dòng nhóm (xem nhanh theo ngữ cảnh)

    def __init__(self, columns=None, parent=None, multi: bool = False):
        super().__init__(parent)
        self.columns = columns or []
        self.rows: list[dict] = []
        self.setShowGrid(True)                  # kẻ ô như Excel, đường kẻ mờ (màu grid của giao diện)
        self._pct = PercentDelegate(self)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setResizeContentsPrecision(300)   # đo độ rộng cột theo 300 dòng đầu: bảng lớn không bị khựng
        self.doubleClicked.connect(self._double)
        self._totals: _TotalsBar | None = None
        self._foot_h = 0
        self._unit = "dòng"

    def _double(self, idx):
        row = self._row_of(self.item(idx.row(), 0))
        if row and row.get("_gom"):
            self.group_activated.emit(tuple(row["_gom"]))
        elif self.current():
            self.activated_row.emit(self.current())

    # ---- dòng TỔNG dưới đáy bảng
    def show_totals(self, unit: str = "dòng") -> None:
        """Ghim dòng TỔNG các cột tiền (theo các dòng đang hiện) dưới đáy bảng; chọn từ 2 dòng thì thêm dòng
        cộng riêng các dòng đang chọn."""
        if self._totals is not None:
            return
        self._unit = unit
        self._totals = _TotalsBar(self)
        self.horizontalHeader().sectionResized.connect(lambda i, _o, n: self._totals.setColumnWidth(i, n))
        self.horizontalScrollBar().valueChanged.connect(self._totals.horizontalScrollBar().setValue)
        self.itemSelectionChanged.connect(self._update_totals)

    def _sum_rows(self, rows) -> dict:
        out = {}
        for k, _, kind in self.columns:
            if kind == "money":
                total = 0
                for r in rows:
                    try:
                        total += int(r.get(k) or 0)
                    except (TypeError, ValueError):
                        pass
                out[k] = total
        return out

    def _update_totals(self) -> None:
        bar = self._totals
        if bar is None:
            return
        grouped = any(r.get("_gom") for r in self.rows)
        base = [r for r in self.rows if r.get("_gom")] if grouped else [r for r in self.rows if not r.get("_kieu")]
        n = sum(r.get("_so_dong", 1) for r in base)
        lines = [(f"TỔNG {n} {self._unit}" + (f" · {len(base)} nhóm" if grouped else ""), self._sum_rows(base),
                  "warn_soft")]
        seen, sel = set(), []
        for it in self.selectedItems():
            idx = it.data(Qt.UserRole)
            if idx is not None and idx not in seen and idx < len(self.rows):
                seen.add(idx)
                if not self.rows[idx].get("_kieu") or self.rows[idx].get("_gom"):
                    sel.append(self.rows[idx])
        if len(sel) >= 2:
            lines.insert(0, (f"Đang chọn {sum(r.get('_so_dong', 1) for r in sel)} {self._unit}", self._sum_rows(sel),
                             "accent_soft"))
        bar.clearSpans()
        bar.setColumnCount(self.columnCount())
        bar.setRowCount(len(lines))
        first_money = next((j for j, c in enumerate(self.columns) if c[2] == "money"), len(self.columns))
        for i, (label, sums, color) in enumerate(lines):
            for j, (k, _, kind) in enumerate(self.columns):
                text = fmt_money(sums[k]) if kind == "money" else ""
                item = QTableWidgetItem(text)
                if kind == "money":
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                try:
                    item.setBackground(QBrush(QColor(style.color(color))))
                except Exception:   # noqa: BLE001 – thiếu màu trong giao diện thì để mặc định
                    pass
                bar.setItem(i, j, item)
            lead = QTableWidgetItem(label)
            f = lead.font()
            f.setBold(True)
            lead.setFont(f)
            try:
                lead.setBackground(QBrush(QColor(style.color(color))))
            except Exception:   # noqa: BLE001
                pass
            bar.setItem(i, 0, lead)
            if first_money > 1:
                bar.setSpan(i, 0, 1, first_money)
        h = self.verticalHeader().defaultSectionSize()
        for i in range(len(lines)):
            bar.setRowHeight(i, h)
        for j in range(self.columnCount()):
            bar.setColumnWidth(j, self.columnWidth(j))
        self._foot_h = h * len(lines) + 2
        self.updateGeometries()

    def _place_totals(self) -> None:
        bar = self._totals
        if bar is None:
            return
        vp = self.viewport().geometry()
        h = self._foot_h
        bar.setGeometry(vp.left(), vp.bottom() + 1, vp.width(), h)
        bar.horizontalScrollBar().setValue(self.horizontalScrollBar().value())
        bar.show()

    def updateGeometries(self):
        super().updateGeometries()        # QTableView đặt lề trên cho tiêu đề cột; thêm lề dưới cho dòng TỔNG
        if self._totals is not None and getattr(self, "_foot_h", 0):
            m = self.viewportMargins()
            if m.bottom() != self._foot_h:
                self.setViewportMargins(m.left(), m.top(), m.right(), self._foot_h)
            self._place_totals()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_totals()

    def set_rows(self, rows, columns=None) -> None:
        if columns is not None:
            self.columns = columns
        self.rows = [dict(r) for r in rows]
        if any(c[0] == "STT" for c in self.columns):    # cột số thứ tự: đánh liền mạch, bỏ qua dòng nhóm / tổng
            n = 0
            for r in self.rows:
                if not r.get("_kieu") and "STT" not in r:
                    n += 1
                    r["STT"] = n
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        self.clear()
        self.verticalHeader().setDefaultSectionSize(int(30 * style.font_scale()))   # theo cỡ chữ đang chọn
        self.setColumnCount(len(self.columns))
        self.setHorizontalHeaderLabels([c[1] for c in self.columns])
        self.setRowCount(len(self.rows))
        for j in range(self.columnCount()):
            self.setItemDelegateForColumn(j, None)
        for j, (_, _, kind) in enumerate(self.columns):
            if kind == "pct":
                self.setItemDelegateForColumn(j, self._pct)
        for i, row in enumerate(self.rows):
            kieu = row.get("_kieu")
            bold = bool(kieu) and kieu != "con"          # "con": dòng chi tiết dưới dòng tổng hợp
            fill = (None if kieu == "con" else QBrush(QColor(style.color("group_bg"))) if kieu == "nhom"
                    else QBrush(QColor(style.color("total_bg"))) if kieu
                    else QBrush(QColor(style.color(f"{row['_mau']}_soft"))) if row.get("_mau")
                    else QBrush(QColor(style.color("warn_soft"))) if row.get("_canh_bao") else None)
            for j, (key, _, kind) in enumerate(self.columns):
                v = row.get(key)
                if kind in ("money", "int"):
                    item = NumItem(fmt_money(v) if v not in (None, "") else "", v)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif kind == "pct":
                    item = NumItem(f"{v:.1f}%" if v not in (None, "") else "", v)
                    item.setData(Qt.UserRole + 1, v if not kieu else None)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif kind == "date":
                    item = NumItem(fmt_date(v), v or "")
                else:
                    item = QTableWidgetItem("" if v is None else str(v))
                item.setData(Qt.UserRole, i)
                if row.get("CanhBao"):
                    item.setToolTip(row["CanhBao"])
                if fill is not None:
                    item.setBackground(fill)
                if bold:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                elif kieu == "con":
                    item.setForeground(QBrush(QColor(style.color("muted"))))
                self.setItem(i, j, item)
        self.setSortingEnabled(not any(r.get("_kieu") for r in self.rows))
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        self.resizeColumnsToContents()
        for j, (_, _, kind) in enumerate(self.columns):
            self.setColumnWidth(j, 150 if kind == "pct" else min(max(self.columnWidth(j) + 12, 70), 360))
        hdr.setStretchLastSection(True)
        self.setUpdatesEnabled(True)
        self._update_totals()

    def _row_of(self, item) -> dict | None:
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        return self.rows[idx] if idx is not None and idx < len(self.rows) else None

    def current(self) -> dict | None:
        r = self.currentRow()
        row = self._row_of(self.item(r, 0)) if r >= 0 else None
        return None if row and row.get("_gom") else row     # dòng nhóm (xem theo ngữ cảnh) không phải bản ghi

    def selected(self) -> list[dict]:
        seen, out = set(), []
        for it in self.selectedItems():
            idx = it.data(Qt.UserRole)
            if idx not in seen:
                seen.add(idx)
                if not self.rows[idx].get("_gom"):
                    out.append(self.rows[idx])
        return out


# ---------------------------------------------------------------- biểu mẫu
VAT_HINT = ("Nhập 2 trong 4 ô Trước thuế / Thuế suất / VAT / Sau thuế, phần mềm tự tính các ô còn lại.")


def rate_field() -> dict:
    return {"key": "ThueSuat", "label": "Thuế suất VAT", "kind": "combo_edit",
            "options": ["10%", "8%", "5%", "0%", "KCT", "KKKNT"], "hint": "Chọn hoặc gõ, VD 10%, 8%, KCT."}

class FormDialog(QDialog):
    """fields: list of dict(key, label, kind, options, required, hint, optional)."""

    def __init__(self, title: str, fields: list[dict], values: dict | None = None, parent=None,
                 validate=None, extra_buttons=None, editing: bool | None = None):
        super().__init__(parent)
        # sửa bản ghi đã có (values có Ma, hoặc editing=True): lưu thì phải xác nhận các thay đổi
        self.editing = bool((values or {}).get("Ma")) if editing is None else editing
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.fields = fields
        self.validate = validate
        self.widgets: dict[str, QWidget] = {}
        values = values or {}
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        for f in fields:
            w = self._make(f, values.get(f["key"]))
            self.widgets[f["key"]] = w
            label = f["label"] + (" *" if f.get("required") else "")
            if f.get("hint"):
                box = QWidget()
                bl = QVBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.addWidget(w)
                h = QLabel(f["hint"])
                h.setObjectName("Hint")
                h.setWordWrap(True)
                bl.addWidget(h)
                form.addRow(label, box)
            else:
                form.addRow(label, w)
        lay.addLayout(form)
        self._bind_vat()
        self._initial = self._display()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        for text, cb in (extra_buttons or []):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, cb=cb: cb(self))
            bb.addButton(b, QDialogButtonBox.ActionRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _make(self, f: dict, v):
        kind = f.get("kind", "text")
        if kind == "money":
            return MoneyEdit(v or 0)
        if kind == "date":
            return DateEdit(v, optional=f.get("optional", False))
        if kind == "combo":
            return Combo(f.get("options", []), v)
        if kind == "combo_edit":
            return EditCombo(f.get("options", []), v)
        if kind == "rates":
            return RatesEdit(v)
        if kind == "multiline":
            w = QPlainTextEdit(v or "")
            w.setFixedHeight(70)
            return w
        if kind == "int":
            w = QSpinBox()
            w.setRange(f.get("min", 0), f.get("max", 1_000_000))
            w.setValue(int(v or 0))
            return w
        if kind == "check":
            w = QCheckBox(f.get("text", ""))
            w.setChecked(bool(v))
            return w
        w = QLineEdit("" if v is None else str(v))
        if kind == "password":
            w.setEchoMode(QLineEdit.Password)
        if f.get("readonly"):
            w.setReadOnly(True)
        return w

    def widget(self, key):
        return self.widgets[key]

    def _bind_vat(self) -> None:
        """Biểu mẫu có Trước thuế, VAT, Sau thuế (và Thuế suất): sửa ô nào thì tự tính các ô còn lại."""
        w = self.widgets
        after = next((k for k in ("GiaTriSauThue", "GiaTriTongSauThue") if k in w), None)
        if not after or "GiaTriTruocThue" not in w or "VAT" not in w:
            return
        from ..core.vat import complete, parse_rate

        def rate(key: str = "ThueSuat") -> str:
            if key not in w:
                return ""
            try:
                return parse_rate(self.values()[key])
            except ValueError:
                return ""

        has_fee = "PhiKhac" in w
        anchor = ["GiaTriTruocThue" if self.values()["GiaTriTruocThue"] or not self.values()[after] else after]

        def update(changed: str) -> None:
            v = self.values()
            b, t, a, r = v["GiaTriTruocThue"] or None, v["VAT"] or None, v[after] or None, rate()
            fa = {}
            if has_fee:      # phí ngoài VAT: gõ số tiền thì giữ số đó, có tỷ lệ thì tính theo tỷ lệ
                fr = rate("TyLePhiKhac")
                fa = {"fee": v["PhiKhac"]} if changed == "PhiKhac" or not fr else {"fee": None, "fee_rate": fr}
            if changed in ("GiaTriTruocThue", after):
                anchor[0] = changed          # số tiền người dùng gõ sau cùng làm gốc khi đổi thuế suất
            # giữ ô vừa sửa và dữ kiện đi cùng, tính lại các ô phụ thuộc
            if changed == "GiaTriTruocThue":
                res = complete(b, None, None, r, **fa) if r else complete(b, t, None, **fa)
            elif changed in ("ThueSuat", "TyLePhiKhac", "PhiKhac"):
                if changed == "ThueSuat" and not r:
                    return
                tt = None if r else t
                res = complete(None, tt, a, r, **fa) if anchor[0] == after and a else \
                    complete(b, tt, None, r, **fa) if b else complete(None, tt, a, r, **fa)
            elif changed == "VAT":
                res = complete(b, t, None, **fa) if b else complete(None, t, a, **fa)
            else:
                res = complete(None, None, a, r, **fa) if r else complete(b, None, a, **fa) if b else \
                    complete(None, t, a, **fa)
            if res.error:
                return
            out = [("GiaTriTruocThue", res.before), ("VAT", res.vat), (after, res.after)]
            if has_fee:
                out.append(("PhiKhac", res.fee))
            for key, val in out:
                if key != changed:
                    w[key].set_value(val)

        for key in ("GiaTriTruocThue", "VAT", after) + (("PhiKhac",) if has_fee else ()):
            w[key].textEdited.connect(lambda _t, k=key: update(k))
        if isinstance(w.get("ChiTietThueSuat"), RatesEdit):
            def from_rates():                # nhập chi tiết nhiều mức thuế: điền thuế suất, tổng tiền hàng, VAT
                lines = w["ChiTietThueSuat"].lines()
                if len(lines) < 2:
                    return
                from ..core.vat import breakdown_rate
                if "ThueSuat" in w:
                    w["ThueSuat"].blockSignals(True)
                    w["ThueSuat"].set_value(breakdown_rate(lines))
                    w["ThueSuat"].blockSignals(False)
                b, t = sum(x["TruocThue"] for x in lines), sum(x["VAT"] for x in lines)
                fee = self.values().get("PhiKhac") or 0 if has_fee else 0
                w["GiaTriTruocThue"].set_value(b)
                w["VAT"].set_value(t)
                w[after].set_value(b + t + fee)
            w["ChiTietThueSuat"].changed.connect(from_rates)
        for key in ("ThueSuat", "TyLePhiKhac"):
            if key in w:
                box = w[key]
                sig = box.currentTextChanged if isinstance(box, QComboBox) else box.textEdited
                sig.connect(lambda _t, k=key: update(k))

    def set_value(self, key, v) -> None:
        widget_set(self.widgets[key], v)

    def values(self) -> dict:
        return {key: widget_value(w) for key, w in self.widgets.items()}

    def _accept(self) -> None:
        vals = self.values()
        for f in self.fields:
            if f.get("required") and vals.get(f["key"]) in (None, ""):
                error(self, f"Chưa nhập {f['label']}.")
                return
        try:
            if self.validate:
                self.validate(vals)
            self.validate_extra()
        except Exception as e:  # noqa: BLE001
            error(self, str(e))
            return
        if self.editing:
            now = self._display()
            changes = [(f["label"], self._initial[f["key"]], now[f["key"]]) for f in self.fields
                       if now[f["key"]] != self._initial[f["key"]] and f.get("kind") != "password"
                       and not f.get("filter")]            # ô chỉ để lọc danh sách, không phải dữ liệu
            changes += self.extra_changes()
            if changes and not confirm_changes(self, changes):
                return
        self.accept()

    def validate_extra(self) -> None:
        """Kiểm tra thêm của biểu mẫu mở rộng (VD bảng phân loại dòng hàng); lỗi thì raise."""

    def extra_changes(self) -> list[tuple[str, str, str]]:
        """Thay đổi ngoài các ô của biểu mẫu, để liệt kê khi xác nhận sửa."""
        return []

    def _display(self) -> dict:
        """Giá trị từng ô dưới dạng chữ như người dùng thấy (để so sánh và liệt kê thay đổi)."""
        return {key: widget_text(w) for key, w in self.widgets.items()}


class RatesDialog(QDialog):
    """Hóa đơn nhiều mức thuế: mỗi dòng một mức thuế suất với tiền hàng (trước thuế) và tiền VAT."""

    RATES = ["10%", "8%", "5%", "0%", "KCT", "KKKNT"]

    def __init__(self, lines: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nhiều mức thuế trên một hóa đơn")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Mỗi dòng một mức thuế: tiền hàng chưa thuế và tiền VAT của các mặt hàng chịu mức đó "
                           "(xem bảng tổng hợp theo thuế suất trên hóa đơn). Gõ tiền hàng, VAT tự tính, sửa được."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Thuế suất", "Tiền hàng (trước thuế)", "Tiền VAT"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        self.total = QLabel()
        lay.addWidget(self.total)
        lay.addLayout(toolbar(button("Thêm mức thuế", lambda: self.add_line()),
                              button("Xóa dòng đang chọn", self.remove_line), "stretch"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Đồng ý")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for x in lines or [{"ThueSuat": "10%"}, {"ThueSuat": "8%"}]:
            self.add_line(x)
        self._sum()

    def add_line(self, x: dict | None = None):
        x = x or {}
        i = self.table.rowCount()
        self.table.insertRow(i)
        rate = EditCombo(self.RATES, x.get("ThueSuat") or "")
        before, tax = MoneyEdit(x.get("TruocThue") or 0), MoneyEdit(x.get("VAT") or 0)
        self.table.setCellWidget(i, 0, rate)
        self.table.setCellWidget(i, 1, before)
        self.table.setCellWidget(i, 2, tax)
        rate.currentTextChanged.connect(lambda _t: self._auto(rate, before, tax))
        before.textEdited.connect(lambda _t: self._auto(rate, before, tax))
        tax.textEdited.connect(lambda _t: self._sum())

    def _auto(self, rate, before, tax):
        from ..core.vat import _round, parse_rate, rate_value
        try:
            r = rate_value(parse_rate(rate.value()))
        except ValueError:
            r = None
        if r is not None and not isinstance(r, str):
            tax.set_value(_round(before.value() * r / 100))
        self._sum()

    def remove_line(self):
        i = self.table.currentRow()
        if i >= 0:
            self.table.removeRow(i)
            self._sum()

    def lines(self) -> list[dict]:
        out = []
        for i in range(self.table.rowCount()):
            rate = self.table.cellWidget(i, 0).value()
            before, tax = self.table.cellWidget(i, 1).value(), self.table.cellWidget(i, 2).value()
            if rate or before or tax:
                out.append({"ThueSuat": rate, "TruocThue": before, "VAT": tax})
        return out

    def _sum(self):
        ls = self.lines()
        self.total.setText(f"Cộng: tiền hàng {fmt_money(sum(x['TruocThue'] for x in ls))} · VAT "
                           f"{fmt_money(sum(x['VAT'] for x in ls))} · {len(ls)} mức thuế")

    def _ok(self):
        from ..core.vat import parse_rate
        ls = self.lines()
        try:
            rates = [parse_rate(x["ThueSuat"]) for x in ls]
        except ValueError as e:
            return error(self, str(e))
        if any(not r for r in rates):
            return error(self, "Chọn thuế suất cho mọi dòng.")
        if len(set(rates)) != len(rates):
            return error(self, "Mỗi mức thuế chỉ một dòng: cộng các mặt hàng cùng mức vào một dòng.")
        self.accept()


class RatesEdit(QWidget):
    """Ô "Nhiều mức thuế" của biểu mẫu hóa đơn: giữ chi tiết từng mức (JSON), bấm nút để nhập / sửa."""

    changed = Signal()

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        lay.addWidget(button("Nhập / sửa…", self.edit))
        self.clear_btn = button("Bỏ", lambda: self.set_value(None, True))
        lay.addWidget(self.clear_btn)
        self.set_value(value)

    def edit(self):
        from ..core.vat import load_breakdown
        dlg = RatesDialog(load_breakdown(self._value), self)
        if dlg.exec() == QDialog.Accepted:
            self.set_value(dlg.lines(), True)

    def set_value(self, v, emit: bool = False) -> None:
        from ..core.vat import dump_breakdown, load_breakdown
        lines = load_breakdown(v)
        self._value = dump_breakdown(lines)
        self.label.setText(self.text() or "Một mức thuế (không cần nhập)")
        self.clear_btn.setVisible(bool(self._value))
        if emit:
            self.changed.emit()

    def value(self):
        return self._value

    def lines(self) -> list[dict]:
        from ..core.vat import load_breakdown
        return load_breakdown(self._value)

    def text(self) -> str:
        return "; ".join(f"{x['ThueSuat']}: {fmt_money(x['TruocThue'])} + VAT {fmt_money(x['VAT'])}"
                         for x in self.lines()) if self._value else ""


def widget_set(w, v) -> None:
    if isinstance(w, (MoneyEdit, DateEdit, Combo, EditCombo, RatesEdit)):
        w.set_value(v)
    elif isinstance(w, QPlainTextEdit):
        w.setPlainText(v or "")
    elif isinstance(w, QSpinBox):
        w.setValue(int(v or 0))
    elif isinstance(w, QCheckBox):
        w.setChecked(bool(v))
    else:
        w.setText("" if v is None else str(v))


def widget_value(w):
    if isinstance(w, (MoneyEdit, DateEdit, Combo, EditCombo, RatesEdit)):
        return w.value()
    if isinstance(w, QPlainTextEdit):
        return w.toPlainText().strip()
    if isinstance(w, QSpinBox):
        return w.value()
    if isinstance(w, QCheckBox):
        return w.isChecked()
    return w.text().strip()


def widget_text(w) -> str:
    """Giá trị của ô dưới dạng chữ như người dùng thấy."""
    if isinstance(w, RatesEdit):
        return w.text()
    if isinstance(w, MoneyEdit):
        return fmt_money(w.value()) if w.value() else ""
    if isinstance(w, DateEdit):
        return fmt_date(w.value()) if w.value() else ""
    if isinstance(w, QComboBox):
        return w.currentText().strip()
    if isinstance(w, QPlainTextEdit):
        return w.toPlainText().strip()
    if isinstance(w, QSpinBox):
        return str(w.value())
    if isinstance(w, QCheckBox):
        return "Có" if w.isChecked() else "Không"
    return w.text().strip()


def field_text(f: dict, v) -> str:
    """Giá trị v của ô f dưới dạng chữ như trên biểu mẫu."""
    kind = f.get("kind", "text")
    if kind == "combo":
        for opt in f.get("options", []):
            label, data = opt if isinstance(opt, tuple) else (opt, opt)
            if data == v:
                return str(label).strip()
        return "" if v is None else str(v)
    if kind == "money":
        return fmt_money(v) if v else ""
    if kind == "date":
        return fmt_date(v) if v else ""
    if kind == "int":
        return str(int(v or 0))
    if kind == "check":
        return "Có" if v else "Không"
    return "" if v is None else str(v).strip()


class BulkEditDialog(QDialog):
    """Sửa cùng lúc nhiều dòng đã chọn: tích "Đổi" ở ô muốn đổi, ô không tích giữ nguyên giá trị từng dòng.
    Lưu thì liệt kê cũ → mới (gộp theo giá trị) và hỏi xác nhận."""

    def __init__(self, title: str, fields: list[dict], rows: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.fields, self.rows = fields, rows
        self.widgets: dict[str, QWidget] = {}
        self.boxes: dict[str, QCheckBox] = {}
        self._old: dict[str, list[str]] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"Đang chọn {len(rows)} dòng. Tích \"Đổi\" ở ô muốn đổi cho mọi dòng đã chọn; ô không "
                           "tích giữ nguyên giá trị riêng của từng dòng."))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        for f in fields:
            vals = [r.get(f["key"]) for r in rows]
            same = all(v == vals[0] for v in vals)
            w = FormDialog._make(self, f, vals[0] if same else None)
            self._old[f["key"]] = [field_text(f, v) for v in vals]   # để liệt kê cũ → mới
            w.setEnabled(False)
            if not same and isinstance(w, QLineEdit):
                w.setPlaceholderText("(khác nhau)")
            box = QCheckBox("Đổi")
            box.setToolTip("Tích để đổi ô này cho mọi dòng đã chọn")
            box.toggled.connect(w.setEnabled)
            row = QHBoxLayout()
            row.addWidget(box)
            row.addWidget(w, 1)
            if not same:
                row.addWidget(hint("(khác nhau)"))
            form.addRow(f["label"], row)
            self.widgets[f["key"]], self.boxes[f["key"]] = w, box
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(f"Lưu cho {len(rows)} dòng")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def changes(self) -> dict:
        """{khóa: giá trị mới} của các ô được tích Đổi."""
        return {k: widget_value(w) for k, w in self.widgets.items() if self.boxes[k].isChecked()}

    def change_list(self) -> list[tuple[str, str, str]]:
        out = []
        for f in self.fields:
            k = f["key"]
            if not self.boxes[k].isChecked():
                continue
            new = widget_text(self.widgets[k])
            olds = [o for o in self._old[k] if o != new]
            if not olds:
                continue
            uniq = list(dict.fromkeys(o or "(trống)" for o in olds))
            old = ", ".join(uniq[:3]) + (f"… ({len(uniq)} giá trị)" if len(uniq) > 3 else "")
            out.append((f"{f['label']} ({len(olds)} dòng)", old, new))
        return out

    def _accept(self) -> None:
        if not self.changes():
            return error(self, "Chưa tích \"Đổi\" ô nào.")
        for f in self.fields:
            if f.get("required") and self.boxes[f["key"]].isChecked() and \
                    widget_value(self.widgets[f["key"]]) in (None, ""):
                return error(self, f"Chưa nhập {f['label']}.")
        ch = self.change_list()
        if not ch:
            return info(self, "Các dòng đã chọn đã có đúng giá trị này, không có gì thay đổi.")
        if confirm_changes(self, ch, f"cho {len(self.rows)} dòng đã chọn"):
            self.accept()


def bulk_edit(parent, title: str, fields: list[dict], rows: list[dict], save_one) -> int:
    """Mở hộp sửa nhiều dòng; save_one(row, changes) lưu từng dòng (raise để báo lỗi dòng đó).
    Trả số dòng đã lưu."""
    dlg = BulkEditDialog(title, fields, rows, parent)
    if dlg.exec() != QDialog.Accepted:
        return 0
    ch, ok, errors = dlg.changes(), 0, []
    for r in rows:
        try:
            save_one(r, ch)
            ok += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{r.get('_nhan') or r.get('Ma')}: {e}")
    if errors:
        error(parent, f"Đã lưu {ok} dòng. Không lưu được {len(errors)} dòng:\n" + "\n".join(errors[:12]))
    return ok


def toolbar(*buttons) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    for b in buttons:
        if b == "stretch":
            lay.addStretch(1)
        elif isinstance(b, QWidget):
            lay.addWidget(b)
    return lay


def button(text: str, cb=None, name: str | None = None, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if cb:
        b.clicked.connect(lambda _=False: cb())
    if name:
        b.setObjectName(name)
    icon_name = icon_name or icon_for_text(text)
    if icon_name:
        b.setIcon(icon(icon_name, "#ffffff" if name == "Primary" else BUTTON_ICON_COLOR, 16))
        b.setIconSize(QSize(16, 16))
    return b


def card(title: str | None = None, *header_widgets) -> tuple[QFrame, QVBoxLayout]:
    """Khung thẻ bo góc; trả về (khung, layout nội dung). header_widgets đặt bên phải tiêu đề."""
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.setSpacing(10)
    if title is not None:
        row = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("CardTitle")
        row.addWidget(t)
        row.addStretch(1)
        for w in header_widgets:
            row.addWidget(w)
        lay.addLayout(row)
    return frame, lay


def tagged(label: QLabel, name: str, tone: str | None = None) -> QLabel:
    """Đặt objectName + thuộc tính tone cho QSS (VD Pill / danger)."""
    label.setObjectName(name)
    if tone:
        label.setProperty("tone", tone)
    return label


def page_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("PageTitle")
    return lbl


def hint(text: str, name: str = "Hint") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(name)
    lbl.setWordWrap(True)
    return lbl


class FlowLayout(QLayout):
    """Xếp các nút liền nhau, hết chỗ thì xuống dòng (VD hàng nút chọn ngữ cảnh, nhãn bộ lọc)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do(self, rect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            w = it.sizeHint()
            if x + w.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), w))
            x += w.width() + sp
            line = max(line, w.height())
        return y + line - rect.y()
