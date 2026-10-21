"""Màn hình Báo cáo hợp đồng: 9 ngữ cảnh, 10 bộ lọc, lưu mẫu báo cáo, xuất Excel."""
from __future__ import annotations

import json
from datetime import date

from PySide6.QtWidgets import (QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QRadioButton, QSplitter,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from ..core.contracts import CONTRACT_STATUSES
from ..core.reports import CONTEXTS, ContractReports, ReportFilters, export_xlsx
from .widgets import CheckList, Combo, DataTable, DateEdit, ask_text, button, confirm, info, page_title, toolbar


class ReportsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.cr = ContractReports(ctx.db)
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Báo cáo hợp đồng"))
        self.context = Combo([(label, key) for key, label in CONTEXTS], "hop_dong")
        self.templates = Combo()
        self.templates.activated.connect(self._load_template)
        lay.addLayout(toolbar(QLabel("Ngữ cảnh:"), self.context, "stretch", QLabel("Mẫu đã lưu:"), self.templates,
                              button("Lưu mẫu…", self._save_template), button("Xóa mẫu", self._del_template)))
        split = QSplitter(Qt.Horizontal)
        filt = QGroupBox("Bộ lọc")
        g = QGridLayout(filt)
        year = date.today().year
        self.tu, self.den = DateEdit(f"{year}-01-01", optional=True), DateEdit(f"{year}-12-31", optional=True)
        self.tinh_theo = Combo([("Ngày ký hợp đồng", "NgayKy"), ("Ngày lập hóa đơn", "NgayHoaDon"),
                                ("Ngày tiền về", "NgayTienVe")])
        self.chieu = Combo([("Tất cả", None), ("Đầu ra", "Đầu ra"), ("Đầu vào", "Đầu vào")])
        self.loai = CheckList()
        self.doi_tac = CheckList()
        self.nhom = CheckList()
        self.tinh_trang = CheckList(list(CONTRACT_STATUSES))
        self.tt_tt = Combo([("Tất cả", None), ("Chưa thu", "Chưa thu"), ("Thu một phần", "Thu một phần"),
                            ("Đã thu đủ", "Đã thu đủ"), ("Chỉ còn phải thu", "Còn phải thu")])
        self.tt_xuat = Combo([("Tất cả", None), ("Chưa xuất", "Chưa xuất"), ("Xuất một phần", "Xuất một phần"),
                              ("Đã xuất đủ", "Đã xuất đủ")])
        self.lien_ket = Combo([("Tất cả", None), ("Có hợp đồng", True), ("Không có hợp đồng", False)])
        self.trong_ky = QRadioButton("Trong kỳ")
        self.luy_ke = QRadioButton("Lũy kế")
        self.luy_ke.setChecked(True)
        self.ky = Combo([("Tháng", "thang"), ("Quý", "quy"), ("Năm", "nam")])
        for lst in (self.loai, self.doi_tac, self.nhom, self.tinh_trang):
            lst.setMaximumHeight(90)
        r = 0
        for label, w in (("1. Từ ngày", self.tu), ("    Đến ngày", self.den), ("2. Tính theo", self.tinh_theo),
                         ("3. Chiều hợp đồng", self.chieu), ("4. Loại hợp đồng", self.loai),
                         ("5. Chủ đầu tư / NCC", self.doi_tac), ("6. Nhóm chủ đầu tư", self.nhom),
                         ("7. Tình trạng HĐ", self.tinh_trang), ("8. Thanh toán", self.tt_tt),
                         ("9. Xuất hóa đơn", self.tt_xuat), ("10. HĐơn đầu vào", self.lien_ket),
                         ("Kỳ (ngữ cảnh thời gian)", self.ky)):
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
        split.addWidget(filt)
        right = QWidget()
        rl = QVBoxLayout(right)
        b_export = button("Xuất Excel…", self.export, "Primary")
        b_export.setEnabled(ctx.can("BaoCao", "Xuat"))
        rl.addLayout(toolbar(button("Xem trước", self.preview), b_export, "stretch"))
        self.table = DataTable([])
        rl.addWidget(self.table)
        split.addWidget(right)
        split.setSizes([360, 820])
        lay.addWidget(split, 1)

    def refresh(self):
        self.loai.set_options([(t["TenLoaiHD"], t["Ma"]) for t in self.ctx.cs.contract_types()], self.loai.values())
        self.doi_tac.set_options([(p["TenDoiTac"], p["Ma"]) for p in self.ctx.cs.partners()], self.doi_tac.values())
        self.nhom.set_options([(g["TenNhom"], g["Ma"]) for g in self.ctx.cs.groups()], self.nhom.values())
        self.templates.set_options([("", None)] + [(r["Khoa"].split(":", 1)[1], r["Khoa"]) for r in self.ctx.db.query(
            "SELECT Khoa FROM CaiDat WHERE Khoa LIKE 'mau_bao_cao:%' ORDER BY Khoa")])

    def filters(self) -> ReportFilters:
        return ReportFilters(
            tu=self.tu.value(), den=self.den.value(), tinh_theo=self.tinh_theo.value(), chieu=self.chieu.value(),
            loai_hd=self.loai.values(), doi_tac=self.doi_tac.values(), nhom=self.nhom.values(),
            tinh_trang=self.tinh_trang.values(), tt_thanh_toan=self.tt_tt.value(), tt_xuat_hd=self.tt_xuat.value(),
            co_hd_lien_ket=self.lien_ket.value(), che_do="trong_ky" if self.trong_ky.isChecked() else "luy_ke",
            ky=self.ky.value())

    def build(self):
        return self.cr.build(self.context.value(), self.filters())

    def preview(self):
        rep = self.build()
        rows = list(rep.rows) + ([dict(rep.total, _kieu="tong")] if rep.total else [])
        self.table.set_rows(rows, rep.columns)

    def export(self):
        rep = self.build()
        path, _ = QFileDialog.getSaveFileName(self, "Xuất Excel", f"{rep.title}.xlsx", "Excel (*.xlsx)")
        if path:
            export_xlsx(rep, path, company=self.ctx.company, user=self.ctx.user["HoTen"])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xuất báo cáo", rep.title)
            info(self, f"Đã xuất file:\n{path}")

    # ---- mẫu báo cáo
    def _save_template(self):
        name = ask_text(self, "Lưu mẫu báo cáo", "Tên mẫu (VD: Công nợ nhóm CĐT nhà nước – quý):")
        if name:
            data = dict(self.filters().__dict__, context=self.context.value())
            self.ctx.db.set_setting("mau_bao_cao:" + name, json.dumps(data, ensure_ascii=False))
            self.refresh()

    def _load_template(self):
        key = self.templates.value()
        if not key:
            return
        d = json.loads(self.ctx.db.setting(key))
        self.context.set_value(d.pop("context"))
        self.tu.set_value(d["tu"])
        self.den.set_value(d["den"])
        self.tinh_theo.set_value(d["tinh_theo"])
        self.chieu.set_value(d["chieu"])
        self.loai.set_options([(t["TenLoaiHD"], t["Ma"]) for t in self.ctx.cs.contract_types()], d["loai_hd"])
        self.doi_tac.set_options([(p["TenDoiTac"], p["Ma"]) for p in self.ctx.cs.partners()], d["doi_tac"])
        self.nhom.set_options([(g["TenNhom"], g["Ma"]) for g in self.ctx.cs.groups()], d["nhom"])
        self.tinh_trang.set_options(list(CONTRACT_STATUSES), d["tinh_trang"])
        self.tt_tt.set_value(d["tt_thanh_toan"])
        self.tt_xuat.set_value(d["tt_xuat_hd"])
        self.lien_ket.set_value(d["co_hd_lien_ket"])
        (self.trong_ky if d["che_do"] == "trong_ky" else self.luy_ke).setChecked(True)
        self.ky.set_value(d["ky"])
        self.preview()

    def _del_template(self):
        key = self.templates.value()
        if key and confirm(self, "Xóa mẫu báo cáo đã chọn?"):
            self.ctx.db.set_setting(key, None)
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
