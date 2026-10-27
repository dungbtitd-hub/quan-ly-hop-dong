"""Khung chọn cột dùng chung cho các hộp xuất: ô tích chia nhóm, xếp nhiều cột cho dễ nhìn, kèm bảng xem trước."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHeaderView, QLabel, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.invoice_match import fold
from ..core.reports import fmt_date, fmt_money
from . import style


class CheckGrid(QScrollArea):
    """Các ô tích chia theo nhóm (tiêu đề nhóm tô xanh), mỗi nhóm xếp `cols` cột.

    options: [(nhóm, nhãn, khóa)]; nhóm rỗng thì không có tiêu đề."""

    changed = Signal()

    def __init__(self, options=(), checked=(), cols: int = 2, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.cols = cols
        self.boxes: list[tuple[str, QCheckBox]] = []
        self.headers: dict[str, QLabel] = {}
        self.set_options(options, checked)

    def set_options(self, options, checked=()) -> None:
        inner = QWidget()
        inner.setObjectName("CheckGrid")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        self.boxes, self.headers = [], {}
        groups: dict[str, list] = {}
        for group, label, key in options:
            groups.setdefault(group, []).append((label, key))
        for group, items in groups.items():
            if group:
                h = QLabel(group)
                h.setStyleSheet(f"color: {style.color('accent')}; font-weight: 600; padding-top: 4px;")
                self.headers[group] = h
                lay.addWidget(h)
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(2)
            rows = (len(items) + self.cols - 1) // self.cols
            for i, (label, key) in enumerate(items):
                cb = QCheckBox(label)
                cb.setProperty("key", key)
                cb.setProperty("group", group)
                cb.setChecked(key in checked)
                cb.toggled.connect(self.changed)
                grid.addWidget(cb, i % rows if rows else 0, i // rows if rows else 0)   # đọc theo cột như bảng in
                self.boxes.append((key, cb))
            for c in range(self.cols):
                grid.setColumnStretch(c, 1)
            lay.addLayout(grid)
        lay.addStretch(1)
        self.setWidget(inner)

    def values(self) -> list:
        return [k for k, cb in self.boxes if cb.isChecked()]

    def set_values(self, keys) -> None:
        for k, cb in self.boxes:
            cb.setChecked(k in keys)

    def set_all(self, on: bool) -> None:
        """Tích / bỏ tích các ô đang hiện (ô bị ẩn do tìm kiếm giữ nguyên)."""
        for _, cb in self.boxes:
            if not cb.isHidden():
                cb.setChecked(on)

    def filter(self, text: str) -> None:
        t = fold(text)
        shown: dict[str, bool] = {}
        for _, cb in self.boxes:
            g = cb.property("group")
            hit = not t or t in fold(cb.text()) or t in fold(g or "")
            cb.setHidden(not hit)
            shown[g] = shown.get(g, False) or hit
        for g, h in self.headers.items():
            h.setHidden(not shown.get(g, False))

    def visible(self) -> list:
        return [k for k, cb in self.boxes if not cb.isHidden()]


class PreviewTable(QTableWidget):
    """Vài dòng đầu của file sẽ xuất; cột trong `highlight` (trường vừa thêm) tô xanh."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.setMaximumHeight(150)
        self.setMinimumHeight(110)

    def show_rows(self, columns, rows, highlight=()) -> None:
        self.clear()
        self.setColumnCount(len(columns))
        self.setRowCount(len(rows))
        soft = QBrush(QColor(style.color("accent_soft")))
        for j, (key, title, *rest) in enumerate(columns):
            head = QTableWidgetItem(title)
            if key in highlight:
                head.setBackground(soft)
                head.setForeground(QBrush(QColor(style.color("accent"))))
            self.setHorizontalHeaderItem(j, head)
            kind = rest[0] if rest else "text"
            for i, r in enumerate(rows):
                v = r.get(key)
                if kind in ("money", "int") and isinstance(v, (int, float)):
                    text = fmt_money(v)
                elif kind == "date" and isinstance(v, str):
                    text = fmt_date(v)
                else:
                    text = "" if v is None else str(v)
                it = QTableWidgetItem(text)
                if kind in ("money", "int"):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if key in highlight:
                    it.setBackground(soft)
                self.setItem(i, j, it)
        self.resizeColumnsToContents()
        for j in range(self.columnCount()):
            self.setColumnWidth(j, min(max(self.columnWidth(j) + 10, 70), 260))
        marked = [j for j, c in enumerate(columns) if c[0] in highlight]
        if marked and rows:                                  # cột vừa thêm nằm cuối: cuộn tới để thấy ngay
            self.scrollToItem(self.item(0, marked[-1]))
            self.scrollToItem(self.item(0, marked[0]))
