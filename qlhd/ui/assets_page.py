"""Màn hình Tài sản, CCDC: dòng hóa đơn chờ kế toán phân loại, bảng kê dòng hóa đơn đầu vào, sổ TSCĐ, sổ CCDC."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core import asset_catalog as dm
from ..core.assets import CASES, CCDC_COLS, CCDC_KEYS, EXTRA_COLS, TSCD_COLS, TSCD_KEYS, AssetError, AssetService
from ..core.reports import fmt_money
from .widgets import (Combo, DataTable, FormDialog, button, confirm, error, hint, info, page_title,
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
        b_imp = button("Nhập sổ cũ từ Excel…", self.import_book)
        b_exp = button("Xuất Excel…", self.export, "Primary")
        b_add.setEnabled(can_add)
        b_imp.setEnabled(can_add)
        for b in (b_edit, b_off, b_undo):
            b.setEnabled(can_edit)
        b_del.setEnabled(self.ctx.can("TaiSan", "Xoa"))
        b_exp.setEnabled(self.ctx.can("TaiSan", "Xuat"))
        lay.addLayout(toolbar(QLabel("Năm"), self.year, QLabel("Trường hợp"), self.case, self.extra, "stretch"))
        lay.addLayout(toolbar(b_add, b_edit, b_off, b_undo, b_del, "stretch", b_imp, b_exp))
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
        tot = {k: sum(r[k] or 0 for r in rows) for k in ("NguyenGia", "TrongKy", "LuyKe", "ConLai")}
        shown = rows + ([{"MaTS": f"Số dòng = {len(rows)}", "_kieu": "tong", **tot}] if rows else [])
        self.table.set_rows(shown, self._columns())
        word = "khấu hao" if self.loai_so == "TSCĐ" else "phân bổ"
        self.total.setText(f"{len(rows)} tài sản · Nguyên giá {fmt_money(tot['NguyenGia'])} đ · {word} trong năm "
                           f"{fmt_money(tot['TrongKy'])} đ · lũy kế {fmt_money(tot['LuyKe'])} đ · còn lại "
                           f"{fmt_money(tot['ConLai'])} đ")

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
        f = [{"key": "MaTS", "label": "Mã", "hint": "Để trống thì phần mềm tự đánh mã."},
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

    def import_book(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn sổ TSCĐ / sổ CCDC (Excel xuất từ phần mềm kế toán)", "",
                                              "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        res = run_safely(self, self.svc.import_register, path)
        if res is None:
            return
        self.ctx.db.log(self.ctx.user["TenDangNhap"], f"Nhập sổ {res['loai']} năm {res['nam']}", path)
        msg = (f"Đã nhập sổ {res['loai']} năm {res['nam']}: thêm {res['them']}, cập nhật {res['cap_nhat']} tài sản. "
               "Số liệu đến hết năm giữ đúng theo sổ, các năm sau phần mềm tính tiếp.")
        if res["lech"]:
            msg += "\n\nKhác cách tính chuẩn (đã giữ theo sổ):\n" + "\n".join(res["lech"][:10])
        if res["bo_qua"]:
            msg += "\n\nBỏ qua:\n" + "\n".join(res["bo_qua"][:10])
        info(self, msg)
        self.year.setValue(res["nam"])
        self.page.select_book(res["loai"])
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
        if not self.ctx.can("TaiSan", "Sua"):
            return info(self, "Tài khoản không có quyền phân loại.")
        dlg = ClassifyDialog(rows, self)
        if dlg.exec() != QDialog.Accepted:
            return
        pl, nhom, months = dlg.values()
        errors = []
        for r in rows:
            try:
                self.svc.set_line_class(r["Ma"], pl, nhom, months)
            except AssetError as e:
                errors.append(f"{r['TenHang']}: {e}")
        self.ctx.db.log(self.ctx.user["TenDangNhap"], "Phân loại dòng hóa đơn đầu vào",
                        f"{len(rows) - len(errors)} dòng: {pl}")
        if errors:
            error(self, "Không lưu được:\n" + "\n".join(errors[:15]))
        self.refresh()
        win = self.window()
        if hasattr(win, "update_badges"):
            win.update_badges()

    def export_lines(self):
        y = self.l_year.value()
        path, _ = QFileDialog.getSaveFileName(self, "Xuất bảng kê dòng hóa đơn", f"Dong_hoa_don_dau_vao_{y}.xlsx",
                                              "Excel (*.xlsx)")
        if path and run_safely(self, self.svc.export_lines, path, y, company=self.ctx.company,
                               user=self.ctx.user["HoTen"]) is not None:
            info(self, f"Đã xuất:\n{path}")
