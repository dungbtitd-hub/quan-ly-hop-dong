"""Màn hình Báo cáo hợp đồng: các ngữ cảnh, bộ lọc, mẫu báo cáo lưu sẵn để xuất nhanh, xuất Excel."""
from __future__ import annotations

import json
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QRadioButton,
                               QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..core.contracts import CONTRACT_STATUSES
from ..core.reports import (CONTEXT_LABELS, CONTEXTS, RELATIVE_PERIODS, TAX_MODES, ContractReports, ReportFilters,
                            export_xlsx, relative_range)
from .widgets import (CheckList, Combo, DataTable, DateEdit, FormDialog, ask_text, button, confirm, hint, info,
                      page_title, toolbar)

TEMPLATE_PREFIX = "mau_bao_cao:"


def preview_rows(rep) -> list[dict]:
    """Dòng để xem trước trên màn hình: nhãn nhóm hiện ở cột đầu, thêm dòng tổng."""
    first = rep.columns[0][0]
    rows = [dict(r, **{first: r["_nhan"]}) if r.get("_nhan") else r for r in rep.rows]
    return rows + ([dict(rep.total, _kieu="tong")] if rep.total else [])


class ReportsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.cr = ContractReports(ctx.db)
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Báo cáo hợp đồng"))
        self.context = Combo([(label, key) for key, label in CONTEXTS], "hop_dong")
        self.context.currentIndexChanged.connect(self._context_changed)
        self.templates = Combo()
        self.templates.activated.connect(self._load_template)
        lay.addLayout(toolbar(QLabel("Ngữ cảnh:"), self.context, "stretch", QLabel("Mẫu đã lưu:"), self.templates,
                              button("Lưu mẫu…", self._save_template), button("Xóa mẫu", self._del_template)))
        self.quick_box = QWidget()
        self.quick = QHBoxLayout(self.quick_box)
        self.quick.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.quick_box)

        split = QSplitter(Qt.Horizontal)
        filt = QGroupBox("Bộ lọc")
        g = QGridLayout(filt)
        year = date.today().year
        self.ky_td = Combo([(label, key) for key, label in RELATIVE_PERIODS])
        self.ky_td.currentIndexChanged.connect(self._relative_changed)
        self.tu, self.den = DateEdit(f"{year}-01-01", optional=True), DateEdit(f"{year}-12-31", optional=True)
        self.tinh_theo = Combo([("Ngày ký hợp đồng", "NgayKy"), ("Ngày lập hóa đơn", "NgayHoaDon"),
                                ("Ngày tiền về", "NgayTienVe")])
        self.chieu = Combo([("Tất cả", None), ("Đầu ra", "Đầu ra"), ("Đầu vào", "Đầu vào")])
        self.gop = QCheckBox("Gộp đầu ra và đầu vào chung một bảng")
        self.gop.setToolTip("Mặc định hợp đồng / hóa đơn đầu ra và đầu vào được tách thành bảng (sheet) riêng.")
        self.loai = CheckList()
        self.doi_tac = CheckList()
        self.nhom = CheckList()
        self.tinh_trang = CheckList(list(CONTRACT_STATUSES))
        self.tt_tt = Combo([("Tất cả", None), ("Chưa thu", "Chưa thu"), ("Thu một phần", "Thu một phần"),
                            ("Đã thu đủ", "Đã thu đủ"), ("Chỉ còn phải thu", "Còn phải thu")])
        self.tt_xuat = Combo([("Tất cả", None), ("Chưa xuất", "Chưa xuất"), ("Xuất một phần", "Xuất một phần"),
                              ("Đã xuất đủ", "Đã xuất đủ")])
        self.lien_ket = Combo([("Tất cả", None), ("Có hợp đồng", True), ("Không có hợp đồng", False)])
        self.nhom_thue = Combo([(label, key) for key, label in TAX_MODES])
        self.nguoi_mua = CheckList()
        self.trong_ky = QRadioButton("Trong kỳ")
        self.luy_ke = QRadioButton("Lũy kế")
        self.luy_ke.setChecked(True)
        self.ky = Combo([("Tháng", "thang"), ("Quý", "quy"), ("Năm", "nam")])
        for lst in (self.loai, self.doi_tac, self.nhom, self.tinh_trang, self.nguoi_mua):
            lst.setMaximumHeight(90)
        r = 0
        for label, w in (("Kỳ báo cáo", self.ky_td), ("1. Từ ngày", self.tu), ("    Đến ngày", self.den),
                         ("2. Tính theo", self.tinh_theo), ("3. Chiều hợp đồng", self.chieu), ("", self.gop),
                         ("4. Loại hợp đồng", self.loai), ("5. Chủ đầu tư / NCC", self.doi_tac),
                         ("6. Nhóm chủ đầu tư", self.nhom), ("7. Tình trạng HĐ", self.tinh_trang),
                         ("8. Thanh toán", self.tt_tt), ("9. Xuất hóa đơn", self.tt_xuat),
                         ("10. HĐơn đầu vào", self.lien_ket), ("11. Thuế GTGT đầu vào", self.nhom_thue),
                         ("12. Người mua (HĐơn ra)", self.nguoi_mua), ("Kỳ (ngữ cảnh thời gian)", self.ky)):
            g.addWidget(QLabel(label), r, 0, Qt.AlignTop)
            g.addWidget(w, r, 1)
            r += 1
        mode = QHBoxLayout()
        mode.addWidget(self.trong_ky)
        mode.addWidget(self.luy_ke)
        g.addWidget(QLabel("Cách tính giá trị"), r, 0)
        g.addLayout(mode, r, 1)
        r += 1
        g.addWidget(button("Lưu các CĐT đã tích thành nhóm…", self._save_group), r, 0, 1, 2)
        scroll = QScrollArea()
        scroll.setWidget(filt)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(380)
        split.addWidget(scroll)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        b_export = button("Xuất Excel…", self.export, "Primary")
        b_export.setEnabled(ctx.can("BaoCao", "Xuat"))
        self.note = hint("")
        rl.addLayout(toolbar(button("Xem trước", self.preview), b_export, "stretch"))
        rl.addWidget(self.note)
        self.tabs = QTabWidget()
        rl.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setSizes([400, 820])
        lay.addWidget(split, 1)
        self._context_changed()

    def refresh(self):
        self.loai.set_options([(t["TenLoaiHD"], t["Ma"]) for t in self.ctx.cs.contract_types()], self.loai.values())
        self.doi_tac.set_options([(p["TenDoiTac"], p["Ma"]) for p in self.ctx.cs.partners()], self.doi_tac.values())
        self.nhom.set_options([(g["TenNhom"], g["Ma"]) for g in self.ctx.cs.groups()], self.nhom.values())
        self.nguoi_mua.set_options(self.cr.buyers(), self.nguoi_mua.values())
        names = self._template_names()
        self.templates.set_options([("", None)] + [(n, n) for n in names])
        while self.quick.count():
            w = self.quick.takeAt(0).widget()
            if w:
                w.deleteLater()
        quick = [n for n in names if self._template(n).get("xuat_nhanh")]
        if quick:
            self.quick.addWidget(QLabel("Xuất nhanh:"))
            for n in quick:
                b = button(n, lambda n=n: self.quick_export(n))
                b.setToolTip(self._template_tip(n))
                b.setEnabled(self.ctx.can("BaoCao", "Xuat"))
                self.quick.addWidget(b)
        self.quick.addStretch(1)
        self.quick_box.setVisible(bool(quick))

    def _context_changed(self):
        notes = {
            "tong_hop": "Xuất toàn bộ thông tin hợp đồng: danh sách hợp đồng (mọi trường), công việc, hóa đơn, "
                        "thanh toán, mỗi phần một sheet.",
            "hoa_don_vao": "Bảng kê theo mẫu quản trị. Chọn ở bộ lọc 11: tách nhóm chịu thuế / không chịu thuế, "
                           "xếp lẫn theo ngày, hoặc chỉ một nhóm.",
            "hoa_don_ra_nguoi_mua": "Tích người mua ở bộ lọc 12 (không tích = tất cả). Mỗi người mua một sheet.",
            "hoa_don_ra_vao": "Mặc định hóa đơn đầu ra và đầu vào ở hai sheet riêng; tích \"Gộp\" để chung một bảng.",
        }
        self.note.setText(notes.get(self.context.value(), "Đầu ra và đầu vào được tách bảng riêng, trừ khi tích "
                                                          "\"Gộp đầu ra và đầu vào\"."))

    def _relative_changed(self):
        key = self.ky_td.value()
        self.tu.setEnabled(not key)
        self.den.setEnabled(not key)
        if key:
            tu, den = relative_range(key, date.today())
            self.tu.set_value(tu)
            self.den.set_value(den)

    def filters(self) -> ReportFilters:
        return ReportFilters(
            tu=self.tu.value(), den=self.den.value(), tinh_theo=self.tinh_theo.value(), chieu=self.chieu.value(),
            loai_hd=self.loai.values(), doi_tac=self.doi_tac.values(), nhom=self.nhom.values(),
            tinh_trang=self.tinh_trang.values(), tt_thanh_toan=self.tt_tt.value(), tt_xuat_hd=self.tt_xuat.value(),
            co_hd_lien_ket=self.lien_ket.value(), che_do="trong_ky" if self.trong_ky.isChecked() else "luy_ke",
            ky=self.ky.value(), nhom_thue=self.nhom_thue.value(), nguoi_mua=self.nguoi_mua.values(),
            gop_ra_vao=self.gop.isChecked(), ky_tuong_doi=self.ky_td.value())

    def build(self):
        return self.cr.build(self.context.value(), self.filters())

    def preview(self, reports=None):
        reports = reports or self.build()
        self.tabs.clear()
        for rep in reports:
            table = DataTable([])
            table.set_rows(preview_rows(rep), rep.columns)
            self.tabs.addTab(table, rep.sheet)

    def _export(self, reports, default_name: str):
        path, _ = QFileDialog.getSaveFileName(self, "Xuất Excel", f"{default_name}.xlsx", "Excel (*.xlsx)")
        if path:
            export_xlsx(reports, path, company=self.ctx.company, user=self.ctx.user["HoTen"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xuất báo cáo", default_name)
            info(self, f"Đã xuất file ({len(reports)} sheet):\n{path}")

    def export(self):
        reps = self.build()
        self.preview(reps)
        self._export(reps, reps[0].title if len(reps) == 1 else CONTEXT_LABELS[self.context.value()])

    def quick_export(self, name: str):
        d = self._template(name)
        reps = self.cr.build(d["context"], ReportFilters.from_dict(d))
        self.preview(reps)
        f = ReportFilters.from_dict(d).resolved()
        stamp = f" {f.tu or ''}_{f.den or ''}".rstrip("_ ") if f.tu or f.den else ""
        self._export(reps, name + stamp)

    # ---- mẫu báo cáo
    def _template_names(self) -> list[str]:
        return [r["Khoa"][len(TEMPLATE_PREFIX):] for r in self.ctx.db.query(
            "SELECT Khoa FROM CaiDat WHERE Khoa LIKE ? ORDER BY Khoa", (TEMPLATE_PREFIX + "%",))]

    def _template(self, name: str) -> dict:
        try:
            return json.loads(self.ctx.db.setting(TEMPLATE_PREFIX + name) or "{}")
        except ValueError:
            return {}

    def _template_tip(self, name: str) -> str:
        d = self._template(name)
        ky = dict(RELATIVE_PERIODS).get(d.get("ky_tuong_doi")) if d.get("ky_tuong_doi") else None
        return f"{CONTEXT_LABELS.get(d.get('context'), '')}" + (f" · {ky}" if ky else "")

    def _save_template(self):
        dlg = FormDialog("Lưu mẫu báo cáo", [
            {"key": "ten", "label": "Tên mẫu", "required": True,
             "hint": "VD: Bảng kê mua vào quý trước, Công nợ nhóm CĐT nhà nước."},
            {"key": "xuat_nhanh", "label": "", "kind": "check", "text": "Hiện thành nút Xuất nhanh"}],
            {"ten": self.templates.value() or "", "xuat_nhanh": True}, self)
        if not dlg.exec():
            return
        v = dlg.values()
        data = dict(self.filters().__dict__, context=self.context.value(), xuat_nhanh=v["xuat_nhanh"])
        if data["ky_tuong_doi"]:
            data["tu"] = data["den"] = None
        self.ctx.db.set_setting(TEMPLATE_PREFIX + v["ten"], json.dumps(data, ensure_ascii=False))
        self.refresh()
        self.templates.set_value(v["ten"])
        if not data["ky_tuong_doi"] and data["tu"]:
            info(self, "Đã lưu mẫu. Mẫu này dùng khoảng ngày cố định; muốn mỗi lần xuất tự lấy kỳ mới nhất thì chọn "
                       "\"Kỳ báo cáo\" là Tháng này, Quý trước… trước khi lưu.")

    def _load_template(self):
        name = self.templates.value()
        if not name:
            return
        d = self._template(name)
        f = ReportFilters.from_dict(d)
        self.context.set_value(d.get("context", "hop_dong"))
        self.ky_td.set_value(f.ky_tuong_doi)
        if not f.ky_tuong_doi:
            self.tu.set_value(f.tu)
            self.den.set_value(f.den)
        self._relative_changed()
        self.tinh_theo.set_value(f.tinh_theo)
        self.chieu.set_value(f.chieu)
        self.gop.setChecked(f.gop_ra_vao)
        self.loai.set_options([(t["TenLoaiHD"], t["Ma"]) for t in self.ctx.cs.contract_types()], f.loai_hd)
        self.doi_tac.set_options([(p["TenDoiTac"], p["Ma"]) for p in self.ctx.cs.partners()], f.doi_tac)
        self.nhom.set_options([(g["TenNhom"], g["Ma"]) for g in self.ctx.cs.groups()], f.nhom)
        self.tinh_trang.set_options(list(CONTRACT_STATUSES), f.tinh_trang)
        self.nguoi_mua.set_options(self.cr.buyers(), f.nguoi_mua)
        self.tt_tt.set_value(f.tt_thanh_toan)
        self.tt_xuat.set_value(f.tt_xuat_hd)
        self.lien_ket.set_value(f.co_hd_lien_ket)
        self.nhom_thue.set_value(f.nhom_thue)
        (self.trong_ky if f.che_do == "trong_ky" else self.luy_ke).setChecked(True)
        self.ky.set_value(f.ky)
        self.preview()

    def _del_template(self):
        name = self.templates.value()
        if name and confirm(self, f"Xóa mẫu báo cáo \"{name}\"?"):
            self.ctx.db.set_setting(TEMPLATE_PREFIX + name, None)
            self.refresh()

    def _save_group(self):
        members = self.doi_tac.values()
        if not members:
            return info(self, "Tích chọn các chủ đầu tư ở bộ lọc 5 trước.")
        name = ask_text(self, "Lưu nhóm", "Tên nhóm chủ đầu tư:")
        if name:
            self.ctx.cs.save_group(name, members)
            self.refresh()
            info(self, f"Đã lưu nhóm \"{name}\" ({len(members)} chủ đầu tư).")
