"""Ô tìm kiếm kèm bộ lọc đa ngữ cảnh (dùng chung cho Hợp đồng, Hóa đơn đầu ra / đầu vào, Tài sản, CCDC):
tìm theo cột, theo thời gian (từ ngày – đến ngày, tháng, quý, năm) và các ô lọc riêng của từng màn hình.
Bộ lọc được nhớ theo từng màn hình, từng tài khoản."""
from __future__ import annotations

import json
from datetime import date

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from ..core.asset_catalog import normalize
from ..core.reports import fmt_date, period_range
from .widgets import Combo, DateEdit, FlowLayout, tagged

PERIOD_MODES = [("all", "Tất cả"), ("ngay", "Từ ngày – đến ngày"), ("thang", "Tháng"), ("quy", "Quý"), ("nam", "Năm")]


def period_choices(mode: str, today: date | None = None) -> list[tuple[str, str]]:
    """(nhãn, ngày đại diện) cho hộp chọn kỳ: 36 tháng, 12 quý hoặc 10 năm gần nhất."""
    t = today or date.today()
    out = []
    if mode == "thang":
        y, m = t.year, t.month
        for _ in range(36):
            out.append((f"Tháng {m}/{y}", date(y, m, 1).isoformat()))
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    elif mode == "quy":
        y, q = t.year, (t.month - 1) // 3 + 1
        for _ in range(12):
            out.append((f"Quý {q}/{y}", date(y, q * 3 - 2, 1).isoformat()))
            y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    elif mode == "nam":
        out = [(f"Năm {y}", date(y, 1, 1).isoformat()) for y in range(t.year, t.year - 10, -1)]
    return out


def _digits(v) -> str:
    return "".join(ch for ch in str(v) if ch.isdigit())


class FilterBox(QWidget):
    """search_fields: [(nhãn, khóa)] để chọn "Tìm trong"; money_keys: cột số tiền (tìm theo giá trị);
    date_key: cột ngày để lọc thời gian (None = không lọc thời gian); filters: [{key, label, options}]."""
    changed = Signal()

    def __init__(self, ctx, name: str, placeholder: str, search_fields, money_keys=(), date_key: str | None = None,
                 date_label: str = "Thời gian", filters=(), parent=None):
        super().__init__(parent)
        self.ctx, self.name = ctx, name
        self.search_fields = list(search_fields)
        self.money_keys = list(money_keys)
        self.date_key = date_key
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        frame = QFrame()
        frame.setObjectName("SearchBox")
        row = QHBoxLayout(frame)
        row.setContentsMargins(6, 0, 0, 0)
        row.setSpacing(4)
        icon = QLabel("⌕")
        icon.setObjectName("Hint")
        row.addWidget(icon)
        self.edit = QLineEdit()
        self.edit.setObjectName("SearchEdit")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setMinimumWidth(240)
        self.edit.setClearButtonEnabled(True)
        row.addWidget(self.edit, 1)
        self.count = tagged(QLabel(""), "Pill", "accent")
        row.addWidget(self.count)
        self.btn = QToolButton()
        self.btn.setObjectName("FilterButton")
        self.btn.setText("Lọc ▾")
        self.btn.setToolTip("Mở bộ lọc: tìm trong cột, thời gian (ngày, tháng, quý, năm) và các ô lọc khác")
        self.btn.clicked.connect(self.toggle_panel)
        row.addWidget(self.btn)
        lay.addWidget(frame)

        # bảng lọc mở ra dưới ô tìm kiếm
        self.panel = QFrame(self, Qt.Popup)
        self.panel.setObjectName("FilterPanel")
        pl = QVBoxLayout(self.panel)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        r = 0
        self.field = Combo([("Tất cả cột", None)] + self.search_fields)
        g.addWidget(QLabel("Tìm trong"), r, 0)
        g.addWidget(self.field, r, 1)
        r += 1
        self.mode_buttons: dict[str, QPushButton] = {}
        if date_key:
            modes = QHBoxLayout()
            for key, label in PERIOD_MODES:
                b = QPushButton(label)
                b.setObjectName("Chip")
                b.setCheckable(True)
                b.clicked.connect(lambda _=False, key=key: self.set_mode(key))
                modes.addWidget(b)
                self.mode_buttons[key] = b
            modes.addStretch(1)
            g.addWidget(QLabel(date_label), r, 0)
            g.addLayout(modes, r, 1)
            r += 1
            when = QHBoxLayout()
            self.period = Combo()
            self.tu = DateEdit(f"{date.today().year}-01-01")
            self.den = DateEdit(f"{date.today().year}-12-31")
            when.addWidget(self.period)
            when.addWidget(self.tu)
            self.dash = QLabel("–")
            when.addWidget(self.dash)
            when.addWidget(self.den)
            when.addStretch(1)
            g.addLayout(when, r, 1)
            r += 1
            self.period.currentIndexChanged.connect(self._emit)
            self.tu.dateChanged.connect(self._emit)
            self.den.dateChanged.connect(self._emit)
        self.combos: dict[str, Combo] = {}
        self.labels: dict[str, str] = {}
        for f in filters:
            c = Combo(f["options"])
            c.currentIndexChanged.connect(self._emit)
            g.addWidget(QLabel(f["label"]), r, 0)
            g.addWidget(c, r, 1)
            self.combos[f["key"]] = c
            self.labels[f["key"]] = f["label"]
            r += 1
        g.setColumnStretch(1, 1)
        pl.addLayout(g)
        btns = QHBoxLayout()
        btns.addStretch(1)
        clear = QPushButton("Xóa lọc")
        clear.clicked.connect(lambda: self.clear())
        close = QPushButton("Đóng")
        close.setObjectName("Primary")
        close.clicked.connect(self.panel.hide)
        btns.addWidget(clear)
        btns.addWidget(close)
        pl.addLayout(btns)

        # nhãn các bộ lọc đang dùng, bấm × để bỏ
        self.chips_box = QWidget()
        self.chips = FlowLayout(self.chips_box)
        lay.addWidget(self.chips_box)
        self.edit.textChanged.connect(self._emit)
        self.field.currentIndexChanged.connect(self._emit)
        self.mode = "all"
        self._restore()
        self._sync_mode()
        self._update_chips()

    # ---- truy cập
    def combo(self, key: str) -> Combo:
        return self.combos[key]

    def value(self, key: str):
        return self.combos[key].value()

    def set_options(self, key: str, options) -> None:
        c = self.combos[key]
        keep = c.value()
        c.blockSignals(True)
        c.set_options(options, keep)
        c.blockSignals(False)

    def text(self) -> str:
        return self.edit.text().strip()

    def date_range(self) -> tuple[str, str] | None:
        if not self.date_key or self.mode == "all":
            return None
        if self.mode == "ngay":
            return self.tu.value(), self.den.value()
        ref = self.period.value()
        if not ref:
            return None
        return period_range(self.mode, date.fromisoformat(ref))

    # ---- lọc một dòng
    def matches(self, row: dict, keys=None) -> bool:
        q = self.text()
        if q:
            field = self.field.value()
            look = [field] if field else list(keys or [k for _, k in self.search_fields])
            text = normalize(" ".join(str(row.get(k) or "") for k in look if k not in self.money_keys))
            ok = normalize(q).strip() in text
            digits = _digits(q)
            if not ok and digits and len(digits) >= 3 and (not field or field in self.money_keys):
                ok = any(digits in _digits(row.get(k) or "") for k in self.money_keys)
            if not ok:
                return False
        rng = self.date_range()
        if rng:
            d = str(row.get(self.date_key) or "")[:10]
            if not d or not rng[0] <= d <= rng[1]:
                return False
        return True

    # ---- giao diện
    def toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
            return
        self.panel.adjustSize()
        w = max(self.panel.sizeHint().width(), 480)
        pos = self.mapToGlobal(QPoint(self.width() - w, self.edit.height() + 6))
        self.panel.setGeometry(pos.x(), pos.y(), w, self.panel.sizeHint().height())
        self.panel.show()

    def set_mode(self, mode: str):
        self.mode = mode
        self._sync_mode()
        self._emit()

    def _sync_mode(self):
        if not self.date_key:
            return
        for key, b in self.mode_buttons.items():
            b.setChecked(key == self.mode)
        if self.mode in ("thang", "quy", "nam"):
            keep = self.period.value()
            self.period.blockSignals(True)
            opts = period_choices(self.mode)
            self.period.set_options(opts)
            if keep in [v for _, v in opts]:
                self.period.set_value(keep)
            self.period.blockSignals(False)
        self.period.setVisible(self.mode in ("thang", "quy", "nam"))
        for w in (self.tu, self.dash, self.den):
            w.setVisible(self.mode == "ngay")

    def clear(self, emit: bool = True):
        self._loading = True
        self.edit.clear()
        self.field.setCurrentIndex(0)
        for c in self.combos.values():
            c.setCurrentIndex(0)
        self.mode = "all"
        self._sync_mode()
        self._loading = False
        if emit:
            self._emit()
        else:
            self._update_chips()
            self._save()

    def active(self) -> list[tuple[str, callable]]:
        """(nhãn, hàm bỏ lọc) của các bộ lọc đang dùng."""
        out = []
        if self.field.value():
            out.append((f"Tìm trong: {self.field.currentText()}", lambda: self.field.setCurrentIndex(0)))
        rng = self.date_range()
        if rng:
            label = self.period.currentText() if self.mode != "ngay" else f"{fmt_date(rng[0])} – {fmt_date(rng[1])}"
            out.append((label, lambda: self.set_mode("all")))
        for key, c in self.combos.items():
            if c.currentIndex() > 0:
                out.append((f"{self.labels[key]}: {c.currentText()}", lambda c=c: c.setCurrentIndex(0)))
        return out

    def _update_chips(self):
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        act = self.active()
        if act:
            self.chips.addWidget(tagged(QLabel("Đang lọc:"), "Hint"))
            for label, undo in act:
                b = QPushButton(f"{label}  ×")
                b.setObjectName("Chip")
                b.setCheckable(True)
                b.setChecked(True)
                b.setToolTip("Bấm để bỏ lọc này")
                b.clicked.connect(lambda _=False, undo=undo: undo())
                self.chips.addWidget(b)
        self.chips_box.setVisible(bool(act))
        self.count.setText(f"{len(act)} lọc")
        self.count.setVisible(bool(act))

    def _emit(self, *_):
        if self._loading:
            return
        self._update_chips()
        self._save()
        self.changed.emit()

    # ---- nhớ bộ lọc theo màn hình, tài khoản
    def _key(self) -> str | None:
        user = self.ctx.user["TenDangNhap"] if self.ctx.user is not None else None
        return f"bo_loc:{self.name}:{user}" if user else None

    def _save(self):
        key = self._key()
        if not key:
            return
        state = {"field": self.field.value(), "mode": self.mode,
                 "period": self.period.value() if self.date_key else None,
                 "tu": self.tu.value() if self.date_key else None, "den": self.den.value() if self.date_key else None,
                 "combos": {k: c.value() for k, c in self.combos.items()}}
        self.ctx.db.set_setting(key, json.dumps(state, ensure_ascii=False))

    def _restore(self):
        key = self._key()
        raw = self.ctx.db.setting(key) if key else None
        if not raw:
            return
        try:
            st = json.loads(raw)
        except ValueError:
            return
        self._loading = True
        self.field.set_value(st.get("field"))
        for k, v in (st.get("combos") or {}).items():
            if k in self.combos:
                self.combos[k].set_value(v)
        if self.date_key:
            self.mode = st.get("mode") or "all"
            self._sync_mode()
            if st.get("period"):
                self.period.set_value(st["period"])
            if st.get("tu"):
                self.tu.set_value(st["tu"])
            if st.get("den"):
                self.den.set_value(st["den"])
        self._loading = False

    def reset_silently(self):
        """Bỏ mọi lọc mà không phát tín hiệu (VD mở đúng một hợp đồng từ màn khác)."""
        self.clear(emit=False)


__all__ = ["FilterBox", "period_choices"]
