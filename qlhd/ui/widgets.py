"""Ô nhập tiền, ngày, bảng dữ liệu, hộp thoại biểu mẫu dùng chung."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QStyle, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

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


def ask_secret(parent, title: str, label: str) -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.Password)
    return text if ok else None


def ask_text(parent, title: str, label: str, value: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.Normal, value)
    return text.strip() if ok and text.strip() else None


def require(cond, message: str) -> None:
    if not cond:
        raise ValueError(message)


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def run_safely(parent, fn, *args, **kwargs):
    """Chạy một thao tác, hiện lỗi nghiệp vụ thành hộp thoại thay vì làm dừng phần mềm."""
    try:
        return fn(*args, **kwargs)
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
        self.blockSignals(True)
        self.clear()
        for opt in options:
            label, data = opt if isinstance(opt, tuple) else (opt, opt)
            self.addItem(label, data)
        self.blockSignals(False)
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


class DataTable(QTableWidget):
    """Bảng hiển thị danh sách dict theo cột (khóa, tiêu đề, kiểu)."""

    activated_row = Signal(dict)

    def __init__(self, columns=None, parent=None, multi: bool = False):
        super().__init__(parent)
        self.columns = columns or []
        self.rows: list[dict] = []
        self.setShowGrid(False)
        self._pct = PercentDelegate(self)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        self.horizontalHeader().setHighlightSections(False)
        self.doubleClicked.connect(lambda _: self.current() and self.activated_row.emit(self.current()))

    def set_rows(self, rows, columns=None) -> None:
        if columns is not None:
            self.columns = columns
        self.rows = [dict(r) for r in rows]
        self.setSortingEnabled(False)
        self.clear()
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
            bold = bool(kieu)
            fill = (QBrush(QColor(style.color("group_bg"))) if kieu == "nhom"
                    else QBrush(QColor(style.color("total_bg"))) if kieu
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
                if fill is not None:
                    item.setBackground(fill)
                if bold:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.setItem(i, j, item)
        self.setSortingEnabled(not any(r.get("_kieu") for r in self.rows))
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        self.resizeColumnsToContents()
        for j, (_, _, kind) in enumerate(self.columns):
            self.setColumnWidth(j, 150 if kind == "pct" else min(max(self.columnWidth(j) + 12, 70), 360))
        hdr.setStretchLastSection(True)

    def _row_of(self, item) -> dict | None:
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        return self.rows[idx] if idx is not None and idx < len(self.rows) else None

    def current(self) -> dict | None:
        r = self.currentRow()
        return self._row_of(self.item(r, 0)) if r >= 0 else None

    def selected(self) -> list[dict]:
        seen, out = set(), []
        for it in self.selectedItems():
            idx = it.data(Qt.UserRole)
            if idx not in seen:
                seen.add(idx)
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
                 validate=None, extra_buttons=None):
        super().__init__(parent)
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
        for key in ("ThueSuat", "TyLePhiKhac"):
            if key in w:
                box = w[key]
                sig = box.currentTextChanged if isinstance(box, QComboBox) else box.textEdited
                sig.connect(lambda _t, k=key: update(k))

    def set_value(self, key, v) -> None:
        w = self.widgets[key]
        if isinstance(w, (MoneyEdit, DateEdit, Combo, EditCombo)):
            w.set_value(v)
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(v or "")
        elif isinstance(w, QSpinBox):
            w.setValue(int(v or 0))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(v))
        else:
            w.setText("" if v is None else str(v))

    def values(self) -> dict:
        out = {}
        for key, w in self.widgets.items():
            if isinstance(w, (MoneyEdit, DateEdit, Combo, EditCombo)):
                out[key] = w.value()
            elif isinstance(w, QPlainTextEdit):
                out[key] = w.toPlainText().strip()
            elif isinstance(w, QSpinBox):
                out[key] = w.value()
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _accept(self) -> None:
        vals = self.values()
        for f in self.fields:
            if f.get("required") and vals.get(f["key"]) in (None, ""):
                error(self, f"Chưa nhập {f['label']}.")
                return
        if self.validate:
            try:
                self.validate(vals)
            except Exception as e:  # noqa: BLE001
                error(self, str(e))
                return
        self.accept()


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
