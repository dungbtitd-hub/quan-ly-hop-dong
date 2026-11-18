"""Thêm / sửa hóa đơn đầu vào trong một biểu mẫu: mọi thông tin của hóa đơn và phân loại từng dòng hàng
(chi phí trong kỳ, CCDC, TSCĐ…) ngay khi nhập, không phải sang bước phân loại riêng."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import asset_catalog as dm
from ..core.assets import FEE_NOTE, FLAGS, AssetService, line_flag
from ..core.reports import fmt_money
from . import style
from .widgets import Combo, FormDialog, button, hint

CLASSES = [dm.CHUA_PHAN_LOAI, dm.CHI_PHI, dm.CCDC, dm.TSCD, dm.CHO_PB, dm.KHONG_TINH]
COLS = ["Tên hàng hóa, dịch vụ", "Thành tiền", "Phân loại", "Nhóm trong danh mục", "Số tháng", "Cảnh báo"]


def _group_box(value: str | None) -> QComboBox:
    box = Combo(dm.group_options(all_groups=True))
    box.insertItem(0, "", None)
    box.setEditable(True)                       # gõ vài chữ để tìm nhóm
    box.setInsertPolicy(QComboBox.NoInsert)
    box.completer().setFilterMode(Qt.MatchContains)
    box.completer().setCompletionMode(QCompleter.PopupCompletion)
    box.set_value(value)
    if value is None:
        box.setCurrentIndex(0)
    box.lineEdit().setCursorPosition(0)        # hiện đầu tên nhóm (mã nhóm), không phải đuôi
    return box


def _group_value(box: QComboBox) -> str | None:
    i = box.findText(box.currentText())         # chữ gõ dở không khớp nhóm nào: coi như chưa chọn
    return box.itemData(i) if i >= 0 else None


class InInvoiceDialog(FormDialog):
    """FormDialog của hóa đơn đầu vào kèm bảng phân loại dòng hàng."""

    def __init__(self, ctx, title, fields, values, parent=None, validate=None, ma: str | None = None):
        super().__init__(title, fields, values, parent, validate=validate, editing=bool(ma))
        self.ctx = ctx
        self.ma = ma
        self.svc = AssetService(ctx.db)
        self.can_class = ctx.can("TaiSan", "Sua")
        self.lines = self._initial_lines(values)
        # bố cục hai cột: thông tin hóa đơn bên trái, dòng hàng và phân loại bên phải
        lay = self.layout()
        form = lay.takeAt(0).layout()
        form.setParent(None)
        inner = QWidget()
        inner.setLayout(form)
        left = QScrollArea()                   # nhiều ô: cuộn được, không bị ép chữ chồng nhau
        left.setWidget(inner)
        left.setWidgetResizable(True)
        left.setFrameShape(QScrollArea.NoFrame)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setMinimumWidth(400)
        right = QGroupBox("Dòng hàng hóa, dịch vụ và phân loại")
        rl = QVBoxLayout(right)
        top = QHBoxLayout()
        self.all_cls = Combo([(c, c) for c in CLASSES[1:]])
        self.all_group = _group_box(None)
        self.all_group.setMinimumWidth(260)
        top.addWidget(QLabel("Cho mọi dòng:"))
        top.addWidget(self.all_cls)
        top.addWidget(self.all_group, 1)
        b_all = button("Áp dụng", self.apply_all)
        top.addWidget(b_all)
        rl.addLayout(top)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rl.addWidget(self.table, 1)
        self.legend = hint("")
        rl.addWidget(self.legend)
        rl.addWidget(hint("Phần mềm chỉ tự chọn khi tên hàng khớp rõ một nhóm; chưa rõ thì để \"Chưa phân loại\" kèm gợi ý. "
                          "Chọn nhóm thì phân loại đổi theo nhóm. TSCĐ, CCDC, chờ phân bổ cần số tháng."))
        for w in (self.all_cls, self.all_group, b_all):
            w.setEnabled(self.can_class)
        if not self.can_class:
            rl.addWidget(hint("Tài khoản không có quyền phân loại: kế toán sẽ phân loại ở màn Tài sản, CCDC.", "Warn"))
        body = QHBoxLayout()
        body.addWidget(left, 4)
        body.addWidget(right, 8)
        lay.insertLayout(0, body)
        self.setMinimumWidth(1180)
        self.resize(1320, 760)
        self._fill()
        if "MatHang" in self.widgets and len(self.lines) == 1 and self.lines[0].get("_gop"):
            self.widgets["MatHang"].textEdited.connect(self._sync_single)
            self.widgets["GiaTriTruocThue"].textEdited.connect(self._sync_single)

    # ---- dòng hàng
    def _initial_lines(self, values: dict) -> list[dict]:
        if self.ma:
            if not self.svc.lines_of(self.ma):
                self.svc.ensure_lines(self.ma)
            rows = self.svc._line_rows("d.MaHoaDon = ?", (self.ma,))
            return [dict(r, _old=(r["PhanLoai"], r["MaNhomDM"], r["SoThang"])) for r in rows]
        src = values.get("_Dong") or []
        has_contract = bool(values.get("MaHopDong"))
        out = []
        for i, ln in enumerate(src, start=1):
            unit = int(ln.get("DonGia") or 0) or (int((ln.get("ThanhTien") or 0) / ln["SoLuong"])
                                                  if ln.get("SoLuong") else int(ln.get("ThanhTien") or 0))
            out.append(self._new_line(ln["TenHang"], int(ln.get("ThanhTien") or 0), unit, has_contract, STT=i))
        if not out:
            v = self.values()
            name = v.get("MatHang") or ""
            out.append(self._new_line(name, int(v.get("GiaTriTruocThue") or 0), int(v.get("GiaTriTruocThue") or 0),
                                      has_contract, _gop=True, STT=1))
        return out

    @staticmethod
    def _new_line(name: str, amount: int, unit: int, has_contract: bool, **extra) -> dict:
        kq = dm.classify(name, unit, co_hop_dong=has_contract) if name.strip() else dm.KetQua(dm.CHUA_PHAN_LOAI,
                                                                                               None, None)
        return {"TenHang": name, "ThanhTien": amount, "DonGiaTinh": unit, "PhanLoai": kq.phan_loai,
                "MaNhomDM": kq.nhom.ma if kq.nhom else None, "SoThang": kq.so_thang, "TuDong": 1,
                "_old": None, "_ly_do": kq.ly_do, **extra}

    def _sync_single(self, *_):
        """Hóa đơn nhập tay một dòng gộp: tên hàng, số tiền theo ô Mặt hàng, Trước thuế; phân loại gợi ý lại nếu
        người dùng chưa tự chọn."""
        ln, v = self.lines[0], self.values()
        ln["TenHang"], ln["ThanhTien"] = v.get("MatHang") or "", int(v.get("GiaTriTruocThue") or 0)
        ln["DonGiaTinh"] = ln["ThanhTien"]
        if not ln.get("_tay"):
            fresh = self._new_line(ln["TenHang"], ln["ThanhTien"], ln["DonGiaTinh"], bool(v.get("MaHopDong")))
            ln.update({k: fresh[k] for k in ("PhanLoai", "MaNhomDM", "SoThang", "_ly_do")})
        self._fill()

    def _fill(self):
        t = self.table
        t.setRowCount(len(self.lines))
        self.rows: list[tuple] = []
        for i, ln in enumerate(self.lines):
            t.setItem(i, 0, QTableWidgetItem(ln["TenHang"]))
            money = QTableWidgetItem(fmt_money(ln["ThanhTien"]))
            money.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, 1, money)
            cls = Combo([(c, c) for c in CLASSES], ln["PhanLoai"])
            grp = _group_box(ln["MaNhomDM"])
            months = QSpinBox()
            months.setRange(0, 600)
            months.setSuffix(" tháng")
            months.setValue(int(ln["SoThang"] or 0))
            fee = ln.get("GhiChu") == FEE_NOTE
            for w in (cls, grp, months):
                w.setEnabled(self.can_class and not fee)
            cls.currentIndexChanged.connect(lambda _=0, i=i: self._changed(i, "cls"))
            grp.currentIndexChanged.connect(lambda _=0, i=i: self._changed(i, "grp"))
            months.valueChanged.connect(lambda _=0, i=i: self._changed(i, "months"))
            t.setCellWidget(i, 2, cls)
            t.setCellWidget(i, 3, grp)
            t.setCellWidget(i, 4, months)
            self.rows.append((cls, grp, months))
            self._paint(i)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        for j, w in enumerate((160, 95, 135, 230, 85)):
            t.setColumnWidth(j, w)
        hdr.setStretchLastSection(True)

    def _changed(self, i: int, what: str):
        cls, grp, months = self.rows[i]
        ln = self.lines[i]
        ln["_tay"] = True
        nhom = dm.BY_CODE.get(_group_value(grp) or "")
        if what == "grp" and nhom:
            allowed = dm.allowed_classes(nhom, ln["DonGiaTinh"] or 0)
            if cls.value() not in allowed:
                cls.blockSignals(True)
                cls.set_value(dm.default_class(nhom, ln["DonGiaTinh"] or 0))
                cls.blockSignals(False)
        pl = cls.value()
        if what in ("grp", "cls") and nhom and pl in dm.TAI_SAN and not months.value():
            months.blockSignals(True)
            months.setValue(int(dm.default_months(nhom, pl, ln["DonGiaTinh"] or 0) or 0))
            months.blockSignals(False)
        ln["PhanLoai"], ln["MaNhomDM"] = pl, _group_value(grp)
        ln["SoThang"] = months.value() or None if pl in dm.TAI_SAN else None
        ln["TuDong"] = 0
        self._paint(i)

    def _paint(self, i: int):
        ln = self.lines[i]
        row = dict(ln, MaHoaDon=self.ma or "", MaHopDong=self.values().get("MaHopDong"))
        if ln.get("_ly_do") and ln["PhanLoai"] == dm.CHUA_PHAN_LOAI:
            code, text = "goi_y", ln["_ly_do"]
        else:
            code, text = line_flag(row)
        if ln["PhanLoai"] != dm.CHUA_PHAN_LOAI and not ln.get("MaNhomDM"):
            code, text = "chua_phan_loai", "Chọn nhóm trong danh mục."
        item = QTableWidgetItem(FLAGS[code][0] if code else "")
        item.setToolTip(text)
        brush = QBrush(QColor(style.color(FLAGS[code][1] + "_soft"))) if code else QBrush()
        for j in (0, 1, 5):
            it = item if j == 5 else self.table.item(i, j)
            if it is not None:
                it.setBackground(brush)
        self.table.setItem(i, 5, item)
        if i < len(self.rows):
            self.rows[i][1].lineEdit().setCursorPosition(0)
        n = sum(1 for k in range(len(self.lines)) if (self.table.item(k, 5) and self.table.item(k, 5).text()))
        self.legend.setText(f"{n} dòng cần xem lại (tô màu, di chuột vào ô Cảnh báo để xem lý do)." if n else
                            "Mọi dòng đã có phân loại.")

    def apply_all(self):
        pl, nhom = self.all_cls.value(), _group_value(self.all_group)
        for i, (cls, grp, months) in enumerate(self.rows):
            if not cls.isEnabled():
                continue
            if nhom:
                grp.set_value(nhom)
            cls.set_value(pl)
            self._changed(i, "grp" if nhom else "cls")

    # ---- kiểm tra, xác nhận, lưu
    def validate_extra(self) -> None:
        for i, ln in enumerate(self.lines, start=1):
            if not ln.get("_tay") or ln["PhanLoai"] == dm.CHUA_PHAN_LOAI:
                continue
            nhom = dm.BY_CODE.get(ln.get("MaNhomDM") or "")
            if nhom is None:
                raise ValueError(f"Dòng {i} ({ln['TenHang']}): chọn nhóm trong danh mục.")
            if ln["PhanLoai"] not in dm.allowed_classes(nhom, ln["DonGiaTinh"] or 0):
                raise ValueError(f"Dòng {i}: nhóm {nhom.ma} không xếp vào \"{ln['PhanLoai']}\" được.")
            if ln["PhanLoai"] in dm.TAI_SAN and not ln.get("SoThang"):
                raise ValueError(f"Dòng {i} ({ln['TenHang']}): nhập số tháng khấu hao / phân bổ.")

    def extra_changes(self) -> list[tuple[str, str, str]]:
        out = []
        for i, ln in enumerate(self.lines, start=1):
            if not ln.get("_old") or not ln.get("_tay"):
                continue
            new = (ln["PhanLoai"], ln.get("MaNhomDM"), ln.get("SoThang"))
            if new != ln["_old"]:
                fmt = lambda t: " ".join(str(x) for x in (t[0], t[1] or "", f"{t[2]} tháng" if t[2] else "") if x)  # noqa: E731
                out.append((f"Dòng {i} – {ln['TenHang'][:40]}", fmt(ln["_old"]), fmt(new)))
        return out

    def save_classes(self, ma: str) -> list[str]:
        """Sau khi lưu hóa đơn: ghi phân loại người dùng đã chọn cho từng dòng. Trả về lỗi (nếu có)."""
        if not self.can_class:
            return []
        saved = [ln for ln in self.svc.lines_of(ma) if ln["GhiChu"] != FEE_NOTE]
        mine = [ln for ln in self.lines if ln.get("GhiChu") != FEE_NOTE]
        errors = []
        for k, ln in enumerate(mine):
            if not ln.get("_tay") or ln["PhanLoai"] == dm.CHUA_PHAN_LOAI:
                continue
            target = next((s for s in saved if s["Ma"] == ln.get("Ma")), None) or (saved[k] if k < len(saved) else None)
            if target is None:
                continue
            try:
                self.svc.set_line_class(target["Ma"], ln["PhanLoai"], ln.get("MaNhomDM"), ln.get("SoThang"))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{ln['TenHang']}: {e}")
        if any(ln.get("_tay") for ln in mine):
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Phân loại dòng hóa đơn đầu vào khi nhập / sửa",
                            str(sum(1 for ln in mine if ln.get("_tay"))))
        return errors


__all__ = ["InInvoiceDialog"]
