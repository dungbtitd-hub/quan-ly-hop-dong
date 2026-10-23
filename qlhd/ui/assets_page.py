"""Màn hình Tài sản, CCDC: dòng hóa đơn chờ kế toán phân loại, bảng kê dòng hóa đơn đầu vào, sổ TSCĐ, sổ CCDC."""
from __future__ import annotations

from datetime import date

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHeaderView, QLabel, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from ..core import asset_catalog as dm
from ..core import ccdc_excel
from ..core.assets import (A_ADD, A_SKIP, A_UPDATE, ACTION_LABELS, CASES, CCDC_COLS, CCDC_KEYS, EXTRA_COLS, ST_IN_FILE,
                           ST_NEW, ST_SAME_CODE, TSCD_COLS, TSCD_KEYS, AssetError, AssetService)
from ..core.reports import fmt_date, fmt_money
from . import style
from .widgets import (Combo, DataTable, FormDialog, button, confirm, confirm_changes, error, hint, info, page_title,
                      run_safely, toolbar)

LINE_COLS = [("NgayNhanHD", "Ngày HĐ", "date"), ("SoHoaDonGoc", "Số HĐ", "text"),
             ("TenNhaCungCap", "Người bán", "text"), ("TenHang", "Tên hàng hóa, dịch vụ", "text"),
             ("SoLuong", "SL", "text"), ("DonGiaTinh", "Đơn giá", "money"), ("ThanhTien", "Thành tiền chưa thuế", "money"),
             ("PhanLoai", "Phân loại", "text"), ("NhomDM", "Nhóm danh mục", "text"), ("SoThang", "Số tháng", "int")]
MONEY_KEYS = {"NguyenGia", "GiaTriTinhKH", "TrongKy", "LuyKe", "ConLai", "MucKy"}
INT_KEYS = {"SoKy", "KyConLai", "SoLuong", "SLDaGiam", "SLConLai", "ThangTrongNam"}
CLASS_CHOICES = [dm.TSCD, dm.CCDC, dm.CHO_PB, dm.CHI_PHI, dm.KHONG_TINH]


def _quantity(v):
    if v is None:
        return ""
    return int(v) if float(v).is_integer() else v


class ClassifyDialog(QDialog):
    """Kế toán chọn phân loại và nhóm (chỉ trong danh mục cài sẵn), nhập thời gian khấu hao / phân bổ."""

    def __init__(self, lines: list[dict], parent=None):
        super().__init__(parent)
        self.lines = lines
        self.setWindowTitle("Phân loại dòng hóa đơn đầu vào")
        self.setMinimumWidth(560)
        first = lines[0]
        lay = QVBoxLayout(self)
        names = "\n".join(f"• {x['TenHang']} – {fmt_money(x['ThanhTien'])} đ" for x in lines[:6])
        more = f"\n… và {len(lines) - 6} dòng khác" if len(lines) > 6 else ""
        lay.addWidget(QLabel(f"{len(lines)} dòng đang chọn:\n{names}{more}"))
        form = QFormLayout()
        kq = dm.classify(first["TenHang"], first["DonGiaTinh"])
        pl = first["PhanLoai"] if first["PhanLoai"] != dm.CHUA_PHAN_LOAI else kq.phan_loai
        self.cls = Combo([(x, x) for x in CLASS_CHOICES], pl if pl in CLASS_CHOICES else dm.CHI_PHI)
        self.group = Combo()
        self.cls.setMinimumWidth(380)
        self.group.setMinimumWidth(380)
        self.months = QSpinBox()
        self.months.setRange(0, 600)
        self.months.setSuffix(" tháng")
        self.quick = QWidget()
        self.quick_lay = toolbar()
        self.quick.setLayout(self.quick_lay)
        self.warn = hint("", "Warn")
        form.addRow("Phân loại *", self.cls)
        form.addRow("Nhóm trong danh mục *", self.group)
        form.addRow("Thời gian khấu hao / phân bổ", self.months)
        form.addRow("", self.quick)
        lay.addLayout(form)
        lay.addWidget(self.warn)
        lay.addWidget(hint("Danh mục nhóm cài sẵn theo quy định, chỉ chọn được trong danh sách. Thời gian nhập tự do: "
                           "TSCĐ chọn nhanh 2, 3, 5 năm; CCDC, chờ phân bổ 12, 24, 36 tháng hoặc theo thời hạn trên "
                           "chứng từ (ví dụ phí đường bộ 18 tháng)."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu phân loại")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._start_group = first["MaNhomDM"] or (kq.nhom.ma if kq.nhom else None)
        self._start_months = first["SoThang"] or kq.so_thang
        self.cls.currentIndexChanged.connect(self._class_changed)
        self.group.currentIndexChanged.connect(self._group_changed)
        self.months.valueChanged.connect(self._warn)
        self._class_changed()

    def _class_changed(self):
        pl = self.cls.value()
        keep = self.group.value() or self._start_group
        self.group.set_options(dm.group_options(pl), keep)
        is_asset = pl in dm.TAI_SAN
        self.months.setEnabled(is_asset)
        while self.quick_lay.count():
            it = self.quick_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if is_asset:
            picks = [(f"{n} năm", n * 12) for n in (2, 3, 5)] if pl == dm.TSCD else [(f"{n} tháng", n)
                                                                                    for n in (12, 24, 36)]
            for text, n in picks:
                self.quick_lay.addWidget(button(text, lambda _=False, n=n: self.months.setValue(n)))
            self.quick_lay.addStretch(1)
        self._group_changed()

    def _group_changed(self):
        pl = self.cls.value()
        if pl in dm.TAI_SAN:
            nhom = dm.BY_CODE.get(self.group.value() or "")
            months = self._start_months
            if nhom and not months:
                months = dm.default_months(nhom, pl, self.lines[0]["DonGiaTinh"])
            self.months.setValue(int(months or 0))
        else:
            self.months.setValue(0)
        self._warn()

    def _warn(self):
        msg = AssetService.month_warning(self.cls.value(), self.group.value(), self.months.value())
        nhom = dm.BY_CODE.get(self.group.value() or "")
        if nhom and nhom.ghi_chu:
            msg = (msg + "\n" if msg else "") + nhom.ghi_chu
        self.warn.setText(msg)

    def values(self) -> tuple[str, str | None, int | None]:
        return self.cls.value(), self.group.value(), (self.months.value() or None)


def classify_lines(ctx, parent, rows: list[dict]) -> bool:
    """Phân loại / phân loại lại các dòng hóa đơn đầu vào (dùng ở thẻ phân loại, sổ tài sản, trang hóa đơn đầu vào).
    Dòng đã có phân loại thì hỏi xác nhận thay đổi."""
    if not ctx.can("TaiSan", "Sua"):
        info(parent, "Tài khoản không có quyền phân loại.")
        return False
    dlg = ClassifyDialog(rows, parent)
    if dlg.exec() != QDialog.Accepted:
        return False
    pl, nhom, months = dlg.values()
    done = [r for r in rows if r.get("PhanLoai") not in (None, "", dm.CHUA_PHAN_LOAI)]
    if done and not confirm_changes(parent, [(r["TenHang"], f"{r['PhanLoai']} {r.get('NhomDM') or ''}".strip(),
                                              f"{pl} {nhom or ''}".strip()) for r in done], "phân loại"):
        return False
    svc, errors = AssetService(ctx.db), []
    for r in rows:
        try:
            svc.set_line_class(r["Ma"], pl, nhom, months)
        except AssetError as e:
            errors.append(f"{r['TenHang']}: {e}")
    ctx.db.log(ctx.user["TenDangNhap"], "Phân loại dòng hóa đơn đầu vào", f"{len(rows) - len(errors)} dòng: {pl}")
    if errors:
        error(parent, "Không lưu được:\n" + "\n".join(errors[:15]))
    win = parent.window()
    if hasattr(win, "update_badges"):
        win.update_badges()
    return True


class ImportPreviewDialog(QDialog):
    """Xem trước sổ nhập từ Excel: từng dòng là mới, trùng mã, có thể trùng hay trùng trong file; chọn cách xử lý."""

    COLS = [("Dòng", "dong"), ("Mã", "MaTS"), ("Tên", "TenTS"), ("Ngày ghi tăng", "NgayGhiTang"),
            ("Giá trị", "NguyenGia"), ("Tình trạng", "status"), ("Trùng với", "trung_voi"), ("Xử lý", None)]

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.data = data
        self.setWindowTitle("Xem trước CCDC kê khai nhập thêm" if data.get("ke_khai")
                            else f"Xem trước sổ {data['loai']} năm {data['nam']}")
        self.resize(1100, 560)
        lay = QVBoxLayout(self)
        rows = data["rows"]
        warn = [it for it in rows if it["status"] != ST_NEW]
        text = f"{len(rows)} dòng trong file, {len(rows) - len(warn)} dòng mới."
        if warn:
            text += (f" Có {len(warn)} dòng trùng hoặc nghi trùng với tài sản đã theo dõi (tô màu): mặc định trùng mã "
                     "và ngày thì cập nhật, nghi trùng (cùng ngày ghi tăng, cùng tên hoặc cùng giá trị) thì bỏ qua. "
                     "Kiểm tra cột \"Trùng với\" rồi đổi cách xử lý nếu cần.")
        lay.addWidget(hint(text, "Warn" if warn else "Hint"))
        if data["loi"]:
            lay.addWidget(hint("Không đọc được: " + "; ".join(data["loi"][:5]), "Danger"))
        self.table = QTableWidget(len(rows), len(self.COLS))
        self.table.setHorizontalHeaderLabels([c[0] for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.combos: dict[int, Combo] = {}
        fill = QBrush(QColor(style.color("warn_soft")))
        for i, it in enumerate(rows):
            v = it["values"]
            for j, (_, key) in enumerate(self.COLS[:-1]):
                val = it.get(key) if key in it else v.get(key)
                if key == "NguyenGia":
                    val = fmt_money(val)
                elif key == "NgayGhiTang":
                    val = fmt_date(val)
                item = QTableWidgetItem("" if val is None else str(val))
                item.setToolTip(item.text())
                if it["status"] != ST_NEW:
                    item.setBackground(fill)
                self.table.setItem(i, j, item)
            choices = [A_ADD, A_SKIP] if it["status"] in (ST_NEW, ST_IN_FILE) else \
                [A_UPDATE, A_ADD, A_SKIP] if it["status"] == ST_SAME_CODE else [A_SKIP, A_UPDATE, A_ADD]
            combo = Combo([(ACTION_LABELS[a], a) for a in choices], it["action"])
            self.combos[it["dong"]] = combo
            self.table.setCellWidget(i, len(self.COLS) - 1, combo)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(2, min(self.table.columnWidth(2), 280))
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setColumnWidth(7, 170)
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Nhập vào sổ")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def actions(self) -> dict[int, str]:
        return {dong: c.value() for dong, c in self.combos.items()}


class RemoveDepreciatedDialog(QDialog):
    """Chọn các tài sản đã hết khấu hao / phân bổ để xóa khỏi sổ (mặc định chọn hết)."""

    def __init__(self, rows: list[dict], loai_so: str, year: int, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle(f"Xóa tài sản hết khấu hao khỏi sổ {loai_so} năm {year}")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"{len(rows)} tài sản đã hết khấu hao / phân bổ từ trước năm {year} (còn lại 0 đ, cả năm "
                           f"không phát sinh). Bỏ chọn tài sản muốn giữ. Tài sản xóa không hiện trong sổ từ năm {year}; "
                           "sổ các năm trước và bảng lãi lỗ giữ nguyên. Xem lại hoặc khôi phục ở trường hợp "
                           "\"Đã xóa khỏi sổ\"."))
        self.table = QTableWidget(len(rows), 5)
        self.table.setHorizontalHeaderLabels(["Xóa", "Mã", "Tên", "Ngày ghi tăng", "Nguyên giá"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.checks: list[QCheckBox] = []
        for i, r in enumerate(rows):
            cb = QCheckBox()
            cb.setChecked(True)
            self.checks.append(cb)
            self.table.setCellWidget(i, 0, cb)
            for j, val in enumerate((r["MaTS"], r["TenTS"], fmt_date(r["NgayGhiTang"]), fmt_money(r["NguyenGia"])),
                                    start=1):
                self.table.setItem(i, j, QTableWidgetItem(str(val or "")))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Xóa khỏi sổ")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self) -> list[dict]:
        return [r for r, cb in zip(self.rows, self.checks) if cb.isChecked()]


class RegisterTab(QWidget):
    def __init__(self, page, loai_so: str):
        super().__init__()
        self.page = page
        self.ctx = page.ctx
        self.svc = page.svc
        self.loai_so = loai_so
        lay = QVBoxLayout(self)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year)
        self.case = Combo([(label, key) for key, label in CASES], "toan_bo")
        self.extra = QCheckBox("Thêm cột của phần mềm (nhóm, hóa đơn, ghi chú…)")
        self.year.valueChanged.connect(self.reload)
        self.case.currentIndexChanged.connect(self.reload)
        self.extra.toggled.connect(self.reload)
        can_add, can_edit = self.ctx.can("TaiSan", "Them"), self.ctx.can("TaiSan", "Sua")
        b_add = button("Thêm tay…", self.add)
        b_edit = button("Sửa…", self.edit)
        b_off = button("Ghi giảm…", self.write_off)
        b_undo = button("Bỏ ghi giảm", self.undo_write_off)
        b_del = button("Xóa", self.delete, "Danger")
        b_done = button("Xóa TS hết khấu hao…" if loai_so == "TSCĐ" else "Xóa CCDC hết phân bổ…",
                        self.remove_depreciated, "Danger")
        b_back = button("Khôi phục vào sổ", self.restore)
        b_cls = button("Phân loại lại (từ hóa đơn)…", self.reclassify)
        b_cls.setToolTip("Tài sản sinh ra từ hóa đơn đầu vào bị xếp nhầm: đổi phân loại, nhóm, số tháng ngay tại đây")
        b_cls.setEnabled(can_edit)
        b_back.setToolTip("Đưa tài sản đã xóa khỏi sổ (trường hợp \"Đã xóa khỏi sổ\") trở lại sổ")
        b_imp = button("Nhập sổ cũ từ Excel…", self.import_book)
        b_exp = button("Xuất Excel…", self.export, "Primary")
        b_add.setEnabled(can_add)
        b_imp.setEnabled(can_add)
        for b in (b_edit, b_off, b_undo):
            b.setEnabled(can_edit)
        for b in (b_del, b_done, b_back):
            b.setEnabled(self.ctx.can("TaiSan", "Xoa"))
        b_exp.setEnabled(self.ctx.can("TaiSan", "Xuat"))
        lay.addLayout(toolbar(QLabel("Năm"), self.year, QLabel("Trường hợp"), self.case, self.extra, "stretch"))
        extra = []
        if loai_so == "CCDC":
            b_tpl = button("Tải mẫu kê khai CCDC…", self.save_extra_template)
            b_ext = button("Nhập CCDC kê khai từ Excel…", self.import_extra)
            b_tpl.setToolTip("File Excel mẫu để kê khai CCDC nhập thêm ngoài hóa đơn đầu vào "
                             "(mua lẻ không hóa đơn, được tặng, điều chuyển, kiểm kê thừa…)")
            b_ext.setEnabled(can_add)
            extra = [b_tpl, b_ext]
        lay.addLayout(toolbar(b_add, b_edit, b_off, b_undo, b_del, b_done, b_back, b_cls, "stretch", *extra, b_imp,
                              b_exp))
        self.table = DataTable([])
        self.table.activated_row.connect(lambda _: self.edit())
        lay.addWidget(self.table, 1)
        self.total = hint("")
        lay.addWidget(self.total)

    def _columns(self):
        cols, keys = (TSCD_COLS, TSCD_KEYS) if self.loai_so == "TSCĐ" else (CCDC_COLS, CCDC_KEYS)
        out = []
        for k, title in zip(keys, cols):
            kind = "money" if k in MONEY_KEYS else "int" if k in INT_KEYS - {"SoLuong"} else \
                "date" if k.startswith("Ngay") else "text"
            out.append((k, title, kind))
        if self.extra.isChecked():
            out += [(k, t, "date" if k.startswith("Ngay") else "int" if k in INT_KEYS else "text")
                    for k, t in EXTRA_COLS]
        return out

    def reload(self):
        rows = self.svc.register(self.loai_so, self.year.value(), self.case.value())
        for r in rows:
            r["SoLuong"] = _quantity(r["SoLuong"])
        warn = self.svc.duplicate_warnings(self.loai_so)
        for r in rows:
            if r.get("Ma") in warn:
                r["CanhBao"], r["_canh_bao"] = warn[r["Ma"]], True
        tot = {k: sum(r[k] or 0 for r in rows) for k in ("NguyenGia", "TrongKy", "LuyKe", "ConLai")}
        shown = rows + ([{"MaTS": f"Số dòng = {len(rows)}", "_kieu": "tong", **tot}] if rows else [])
        cols = self._columns()
        n_warn = sum(1 for r in rows if r.get("_canh_bao"))
        if n_warn:
            cols.append(("CanhBao", "Cảnh báo trùng", "text"))
        self.table.set_rows(shown, cols)
        word = "khấu hao" if self.loai_so == "TSCĐ" else "phân bổ"
        self.total.setText(f"{len(rows)} tài sản · Nguyên giá {fmt_money(tot['NguyenGia'])} đ · {word} trong năm "
                           f"{fmt_money(tot['TrongKy'])} đ · lũy kế {fmt_money(tot['LuyKe'])} đ · còn lại "
                           f"{fmt_money(tot['ConLai'])} đ"
                           + (f" · ⚠ {n_warn} tài sản nghi trùng (tô màu), kiểm tra rồi xóa bản thừa" if n_warn else ""))

    def _current(self):
        r = self.table.current()
        if r is None or r.get("_kieu"):
            info(self, "Hãy chọn một tài sản.")
            return None
        return r

    def _fields(self):
        tscd = self.loai_so == "TSCĐ"
        groups = [("(chưa chọn)", None)] + [(f"{n.ma} – {n.ten}", n.ma) for n in dm.CATALOG
                                           if n.kieu in ("GIA_TRI", "CCDC", "CHO_PB")]
        f = [{"key": "MaTS", "label": "Mã", "hint": "Để trống thì phần mềm tự đánh mã. Mã CCDC tự cấp (CC001/2026…) "
                                                       "tự đánh lại theo ngày ghi tăng khi thêm, sửa, xóa."},
             {"key": "TenTS", "label": "Tên", "required": True},
             {"key": "MaNhomDM", "label": "Nhóm trong danh mục", "kind": "combo", "options": groups}]
        if tscd:
            f += [{"key": "LoaiTS", "label": "Loại TSCĐ", "kind": "combo_edit",
                   "options": ["Máy móc, thiết bị", "Phương tiện vận tải, truyền dẫn", "Nhà cửa, vật kiến trúc",
                               "Thiết bị, dụng cụ quản lý", "TSCĐ vô hình"]},
                  {"key": "DonViSuDung", "label": "Đơn vị sử dụng", "kind": "combo_edit",
                   "options": ["Ban giám đốc", "Chuyên viên", "Kế toán"]}]
        else:
            f += [{"key": "PhanLoai", "label": "Loại", "kind": "combo",
                   "options": [("Công cụ dụng cụ", dm.CCDC), ("Chi phí chờ phân bổ", dm.CHO_PB)]}]
        f += [{"key": "NgayGhiTang", "label": "Ngày ghi tăng", "kind": "date", "required": True},
              {"key": "SoCTGhiTang", "label": "Số CT ghi tăng"}]
        if tscd:
            f.append({"key": "NgayBatDauKH", "label": "Ngày bắt đầu tính KH", "kind": "date", "optional": True})
        f += [{"key": "SoKy", "label": "Thời gian SD (tháng)" if tscd else "Số kỳ phân bổ (tháng)", "kind": "int",
               "min": 1, "max": 600, "required": True},
              {"key": "DVT", "label": "ĐVT"}, {"key": "SoLuong", "label": "Số lượng", "kind": "int", "min": 1},
              {"key": "NguyenGia", "label": "Nguyên giá" if tscd else "Giá trị CCDC", "kind": "money",
               "required": True}]
        if tscd:
            f.append({"key": "GiaTriTinhKH", "label": "Giá trị tính KH", "kind": "money",
                      "hint": "Để 0 thì bằng nguyên giá. Tài sản đưa vào sổ giữa chừng: ghi giá trị còn lại."})
        f += [{"key": "MucKy", "label": "Giá trị KH / PB mỗi kỳ", "kind": "money",
               "hint": "Để 0 thì phần mềm tự tính = giá trị / số kỳ, kỳ cuối nhận phần còn lại."}]
        if tscd:
            f += [{"key": "TKNguyenGia", "label": "TK nguyên giá"}, {"key": "TKKhauHao", "label": "TK khấu hao"}]
        else:
            f.append({"key": "TKKhauHao", "label": "TK chờ phân bổ"})
        f.append({"key": "GhiChu", "label": "Ghi chú", "kind": "multiline"})
        return f

    def _save(self, values, ma=None):
        dlg = FormDialog("Sổ TSCĐ" if self.loai_so == "TSCĐ" else "Sổ CCDC", self._fields(), values, self)
        while dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            v["LoaiSo"] = self.loai_so
            for k in ("MucKy", "GiaTriTinhKH"):
                if k in v and not v[k]:
                    v[k] = None
            dups = self.svc.find_duplicates({**(dict(self.ctx.db.get("TaiSan", ma)) if ma else {}), **v}, ma) \
                if v.get("NgayGhiTang") else []
            if dups and not confirm(self, "Có thể trùng với tài sản đã có trong sổ (cùng ngày ghi tăng, cùng tên "
                                          "hoặc cùng giá trị):\n" + "\n".join(
                                              f"{d['MaTS']} – {d['TenTS']}, {fmt_money(d['NguyenGia'])} đ"
                                              for d in dups[:5]) + "\n\nVẫn lưu?"):
                continue
            try:
                self.svc.save_asset(v, ma)
                self.ctx.db.log(self.ctx.user["TenDangNhap"], "Sửa sổ tài sản" if ma else "Thêm tài sản", v["TenTS"])
                self.reload()
                return
            except AssetError as e:
                error(self, str(e))

    def add(self):
        self._save({"NgayGhiTang": date.today().isoformat(), "SoKy": 36 if self.loai_so == "TSCĐ" else 12,
                    "SoLuong": 1, "DVT": "Cái", "PhanLoai": dm.CCDC,
                    "TKNguyenGia": "21112", "TKKhauHao": "2141" if self.loai_so == "TSCĐ" else "242"})

    def edit(self):
        r = self._current()
        if r is None:
            return
        a = dict(self.ctx.db.get("TaiSan", r["Ma"]))
        if a["MaDongHD"]:
            info(self, "Tài sản sinh ra từ hóa đơn đầu vào: thời gian và giá trị theo dòng hóa đơn; đổi phân loại, "
                       "số tháng thì làm ở thẻ Dòng hóa đơn đầu vào. Ở đây sửa được tên, mã, đơn vị sử dụng…")
        self._save(a, r["Ma"])

    def reclassify(self):
        r = self._current()
        if r is None:
            return
        a = self.ctx.db.get("TaiSan", r["Ma"])
        if not a["MaDongHD"]:
            return info(self, "Tài sản này nhập tay / từ sổ cũ, không sinh từ hóa đơn: sửa bằng nút \"Sửa…\" "
                              "hoặc xóa nếu không phải tài sản.")
        lines = self.svc._line_rows("d.Ma = ?", (a["MaDongHD"],))
        if lines and classify_lines(self.ctx, self, lines):
            self.page.refresh()
            self.reload()

    def write_off(self):
        r = self._current()
        if r is None:
            return
        dlg = FormDialog("Ghi giảm tài sản", [{"key": "Ngay", "label": "Ngày ghi giảm", "kind": "date"},
                                               {"key": "LyDo", "label": "Lý do (thanh lý, hỏng, mất…)"}],
                         {"Ngay": date.today().isoformat()}, self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            if run_safely(self, self.svc.write_off, r["Ma"], v["Ngay"], v["LyDo"]) is None:
                self.reload()

    def undo_write_off(self):
        r = self._current()
        if r is not None and r.get("NgayGhiGiam"):
            self.svc.undo_write_off(r["Ma"])
            self.reload()

    def delete(self):
        r = self._current()
        if r is not None and confirm(self, f"Xóa {r['MaTS']} – {r['TenTS']} khỏi sổ?"):
            if run_safely(self, self.svc.delete_asset, r["Ma"]) is None:
                self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xóa tài sản", r["TenTS"])
                self.reload()

    def remove_depreciated(self):
        y = self.year.value()
        rows = self.svc.fully_depreciated(self.loai_so, y)
        if not rows:
            info(self, f"Sổ {self.loai_so} năm {y} không có tài sản nào đã hết khấu hao / phân bổ từ trước năm {y}.\n"
                       "Tài sản hết khấu hao trong năm thì sang năm sau mới xóa được, để sổ năm đó vẫn đủ dòng.")
            return
        dlg = RemoveDepreciatedDialog(rows, self.loai_so, y, self)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = dlg.chosen()
        if not chosen:
            return
        if not confirm(self, f"Xóa {len(chosen)} tài sản đã hết khấu hao khỏi sổ {self.loai_so} từ năm {y}?\n"
                             + "\n".join(f"{r['MaTS']} – {r['TenTS']}" for r in chosen[:10])
                             + ("\n…" if len(chosen) > 10 else "")):
            return
        n = run_safely(self, self.svc.remove_depreciated, [r["Ma"] for r in chosen], y)
        if n is not None:
            self.ctx.db.log(self.ctx.user["TenDangNhap"], f"Xóa {n} tài sản hết khấu hao khỏi sổ {self.loai_so}",
                            str(y))
            self.reload()
            info(self, f"Đã xóa {n} tài sản khỏi sổ từ năm {y}. Xem lại ở trường hợp \"Đã xóa khỏi sổ\".")

    def restore(self):
        r = self._current()
        if r is None:
            return
        if not r.get("XoaKhoiSoNam"):
            info(self, "Chọn tài sản ở trường hợp \"Đã xóa khỏi sổ\" để khôi phục.")
            return
        if confirm(self, f"Đưa {r['MaTS']} – {r['TenTS']} trở lại sổ?"):
            self.svc.restore_to_register(r["Ma"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Khôi phục tài sản vào sổ", r["TenTS"])
            self.reload()

    def import_book(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn sổ TSCĐ / sổ CCDC (Excel xuất từ phần mềm kế toán)", "",
                                              "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        data = run_safely(self, self.svc.read_register, path)
        if data is None:
            return
        if not data["rows"]:
            error(self, "Không có dòng tài sản nào đọc được trong file.\n" + "\n".join(data["loi"][:10]))
            return
        dlg = ImportPreviewDialog(data, self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = run_safely(self, self.svc.apply_register, data, dlg.actions())
        if res is None:
            return
        self.ctx.db.log(self.ctx.user["TenDangNhap"], f"Nhập sổ {res['loai']} năm {res['nam']}", path)
        msg = (f"Đã nhập sổ {res['loai']} năm {res['nam']}: thêm {res['them']}, cập nhật {res['cap_nhat']}, "
               f"bỏ qua {res['bo_qua']} tài sản. Số liệu đến hết năm giữ đúng theo sổ, các năm sau phần mềm tính tiếp.")
        if res["lech"]:
            msg += "\n\nKhác cách tính chuẩn (đã giữ theo sổ):\n" + "\n".join(res["lech"][:10])
        if res["loi"]:
            msg += "\n\nKhông đọc được:\n" + "\n".join(res["loi"][:10])
        info(self, msg)
        self.year.setValue(res["nam"])
        self.page.select_book(res["loai"])
        self.reload()

    def save_extra_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Lưu file mẫu kê khai CCDC", "Mau_ke_khai_CCDC.xlsx",
                                              "Excel (*.xlsx)")
        if path and run_safely(self, ccdc_excel.write_template, path) is not None:
            info(self, f"Đã lưu file mẫu:\n{path}\n\nĐiền mỗi dòng một CCDC (đọc sheet Hướng dẫn), rồi bấm "
                       "\"Nhập CCDC kê khai từ Excel…\". Mã CCDC để trống thì phần mềm tự đánh.")

    def import_extra(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file kê khai CCDC", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        data = run_safely(self, self.svc.read_extra_ccdc, path)
        if data is None:
            return
        if not data["rows"]:
            error(self, "Không có dòng CCDC nào đọc được trong file.\n" + "\n".join(data["loi"][:10]))
            return
        dlg = ImportPreviewDialog(data, self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = run_safely(self, self.svc.apply_extra_ccdc, data, dlg.actions())
        if res is None:
            return
        self.ctx.db.log(self.ctx.user["TenDangNhap"], "Nhập CCDC kê khai", path)
        msg = (f"Đã nhập CCDC kê khai: thêm {res['them']}, cập nhật {res['cap_nhat']}, bỏ qua {res['bo_qua']}. "
               "Mã CCDC đã đánh lại theo ngày ghi tăng.")
        if res["loi"]:
            msg += "\n\nKhông nhập được:\n" + "\n".join(res["loi"][:10])
        info(self, msg)
        self.year.setValue(res["nam"])
        self.reload()

    def export(self):
        y, case = self.year.value(), self.case.value()
        base = "So_TSCD" if self.loai_so == "TSCĐ" else "So_CCDC"
        path, _ = QFileDialog.getSaveFileName(self, "Xuất sổ ra Excel", f"{base}_{y}_{case}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        n = run_safely(self, self.svc.export_register, path, self.loai_so, y, case, self.extra.isChecked())
        if n is not None:
            self.ctx.db.log(self.ctx.user["TenDangNhap"], f"Xuất sổ {self.loai_so} năm {y}", path)
            info(self, f"Đã xuất {n} tài sản:\n{path}")


class AssetsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.svc = AssetService(ctx.db)
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Tài sản, công cụ dụng cụ"))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_pending(), "Cần phân loại")
        self.tabs.addTab(self._tab_lines(), "Dòng hóa đơn đầu vào")
        self.tscd = RegisterTab(self, "TSCĐ")
        self.ccdc = RegisterTab(self, "CCDC")
        self.tabs.addTab(self.tscd, "Sổ TSCĐ")
        self.tabs.addTab(self.ccdc, "Sổ CCDC")
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        lay.addWidget(self.tabs)

    def select_book(self, loai: str):
        self.tabs.setCurrentWidget(self.tscd if loai == "TSCĐ" else self.ccdc)

    # ---- chờ phân loại
    def _tab_pending(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(hint("Các dòng hóa đơn đầu vào phần mềm chưa tự phân loại được (không khớp danh mục, hoặc chi phí "
                           "trả trước cần số tháng theo chứng từ). Kế toán chọn phân loại, nhóm và thời gian; chưa "
                           "phân loại xong thì chưa chốt bảng lãi lỗ của năm được."))
        b = button("Phân loại các dòng đã chọn…", lambda: self.classify(self.pending), "Primary")
        b.setEnabled(self.ctx.can("TaiSan", "Sua"))
        lay.addLayout(toolbar(b, "stretch"))
        self.pending = DataTable(LINE_COLS, multi=True)
        self.pending.activated_row.connect(lambda _: self.classify(self.pending))
        lay.addWidget(self.pending, 1)
        self.pending_info = hint("")
        lay.addWidget(self.pending_info)
        return w

    # ---- tất cả dòng
    def _tab_lines(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.l_year = QSpinBox()
        self.l_year.setRange(2000, 2100)
        self.l_year.setValue(date.today().year)
        self.l_cls = Combo([("Tất cả phân loại", None)] + [(x, x) for x in dm.PHAN_LOAI])
        self.l_year.valueChanged.connect(self.refresh)
        self.l_cls.currentIndexChanged.connect(self.refresh)
        b = button("Phân loại lại các dòng đã chọn…", lambda: self.classify(self.lines), "Primary")
        b.setEnabled(self.ctx.can("TaiSan", "Sua"))
        b_x = button("Xuất Excel bảng kê…", self.export_lines)
        b_x.setEnabled(self.ctx.can("TaiSan", "Xuat"))
        lay.addLayout(toolbar(QLabel("Năm"), self.l_year, self.l_cls, "stretch", b, b_x))
        self.lines = DataTable(LINE_COLS, multi=True)
        self.lines.activated_row.connect(lambda _: self.classify(self.lines))
        lay.addWidget(self.lines, 1)
        self.lines_info = hint("")
        lay.addWidget(self.lines_info)
        return w

    def refresh(self):
        self.svc.backfill()
        i = self.tabs.currentIndex()
        if i == 0:
            rows = self.svc.pending_lines()
            for r in rows:
                r["SoLuong"] = _quantity(r["SoLuong"])
            self.pending.set_rows(rows)
            self.pending_info.setText(f"{len(rows)} dòng chờ phân loại." if rows else "Không còn dòng nào chờ phân loại.")
        elif i == 1:
            y = self.l_year.value()
            rows = self.svc.lines(f"{y}-01-01", f"{y}-12-31", self.l_cls.value())
            for r in rows:
                r["SoLuong"] = _quantity(r["SoLuong"])
            self.lines.set_rows(rows)
            by = {}
            for r in rows:
                by[r["PhanLoai"]] = by.get(r["PhanLoai"], 0) + int(r["ThanhTien"] or 0)
            self.lines_info.setText(f"{len(rows)} dòng · " + " · ".join(f"{k}: {fmt_money(v)} đ" for k, v in by.items()))
        else:
            self.tabs.currentWidget().reload()
        self.tabs.setTabText(0, f"Cần phân loại ({self.svc.pending_count()})" if self.svc.pending_count()
                             else "Cần phân loại")

    def classify(self, table: DataTable):
        rows = [r for r in table.selected() if not r.get("_kieu")]
        if not rows:
            return info(self, "Hãy chọn dòng cần phân loại (giữ Ctrl / Shift để chọn nhiều dòng).")
        if classify_lines(self.ctx, self, rows):
            self.refresh()

    def export_lines(self):
        y = self.l_year.value()
        path, _ = QFileDialog.getSaveFileName(self, "Xuất bảng kê dòng hóa đơn", f"Dong_hoa_don_dau_vao_{y}.xlsx",
                                              "Excel (*.xlsx)")
        if path and run_safely(self, self.svc.export_lines, path, y, company=self.ctx.company,
                               user=self.ctx.user["HoTen"]) is not None:
            info(self, f"Đã xuất:\n{path}")
