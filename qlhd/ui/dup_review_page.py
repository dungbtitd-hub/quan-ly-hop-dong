"""Màn hình Rà soát trùng lặp và thu thừa: chọn ngữ cảnh (thanh toán, thu thừa, hóa đơn, hợp đồng, đối tác, thu chi),
lọc theo kỳ / đối tác / hợp đồng, xem từng nhóm (bản gốc + bản nghi trùng), tích chọn rồi sửa, xóa (hỏi kỹ) hoặc đánh
dấu "không trùng"."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QLabel, QTabBar, QVBoxLayout, QWidget

from ..core import dup_review as dr
from ..core.contracts import ValidationError
from ..core.reports import RELATIVE_PERIODS, Report, export_xlsx, fmt_date, fmt_money, relative_range
from .widgets import Combo, DataTable, button, confirm, confirm_delete, error, hint, info, page_title, run_safely, toolbar

FEATURE = {"thanh_toan": "HoaDonDauRa", "thu_thua": "HoaDonDauRa", "hoa_don_ra": "HoaDonDauRa",
           "hoa_don_vao": "HoaDonDauVao", "hop_dong": "HopDong", "doi_tac": "DanhMuc", "thu_chi": "ThuChi"}


def row_label(kind: str, r: dict) -> str:
    """Mô tả ngắn một bản ghi (dùng trong hộp hỏi xóa, nhật ký)."""
    if kind in ("thanh_toan", "thu_thua"):
        return (f"Thanh toán HĐ {r['SoHopDong']} ngày {fmt_date(r['NgayThanhToan'])}: {fmt_money(r['SoTien'])} đ"
                + (f", sao kê {r['SoThamChieuSK']}" if r.get("SoThamChieuSK") else ""))
    if kind == "hoa_don_ra":
        return f"Hóa đơn đầu ra số {r['SoHoaDon']} ngày {fmt_date(r['NgayXuatHD'])}: {fmt_money(r['GiaTriSauThue'])} đ"
    if kind == "hoa_don_vao":
        return (f"Hóa đơn đầu vào số {r['SoHoaDonGoc']} của {r['TenNhaCungCap']} ngày {fmt_date(r['NgayNhanHD'])}: "
                f"{fmt_money(r['GiaTriSauThue'])} đ")
    if kind == "hop_dong":
        return f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']}"
    if kind == "doi_tac":
        return f"Đối tác {r['TenDoiTac']}" + (f" (MST {r['MaSoThue']})" if r.get("MaSoThue") else "")
    return f"Phiếu {r['LoaiThuChi'].lower()} {r['SoPhieu']} ngày {fmt_date(r['NgayPhatSinh'])}: {fmt_money(r['SoTien'])} đ"


class DupReviewPage(QWidget):
    open_contract = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.groups: list[dr.Group] = []
        self._inv_helpers: dict = {}
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Rà soát trùng lặp và thu thừa"))
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.kinds = [k for k, _ in dr.KINDS]
        for _, label in dr.KINDS:
            self.tabs.addTab(label)
        self.tabs.currentChanged.connect(lambda _: self.reload())
        lay.addWidget(self.tabs)
        self.ky = Combo([(label, key) for key, label in RELATIVE_PERIODS if key], "den_nay")
        self.doi_tac = Combo()
        self.hop_dong = Combo()
        self.show_ignored = QCheckBox("Hiện cả nhóm đã đánh dấu \"không trùng\"")
        self.count = QLabel()
        for w in (self.ky, self.doi_tac, self.hop_dong):
            w.currentIndexChanged.connect(lambda _: self.reload())
        self.show_ignored.toggled.connect(lambda _: self.reload())
        lay.addLayout(toolbar(QLabel("Kỳ"), self.ky, QLabel("Đối tác"), self.doi_tac, QLabel("Hợp đồng"),
                              self.hop_dong, self.show_ignored, "stretch", self.count))
        self.table = DataTable([], multi=True)
        self.table.activated_row.connect(self._open)
        lay.addWidget(self.table, 1)
        self.note = hint("")
        lay.addWidget(self.note)
        self.b_edit = button("Sửa dòng đang chọn…", self.edit, icon_name="edit")
        self.b_open = button("Mở hợp đồng", lambda: self._open(self.table.current()), icon_name="open")
        self.b_ignore = button("Đánh dấu \"không trùng\"", self.mark_ignored)
        self.b_export = button("Xuất Excel", self.export, icon_name="excel")
        self.b_del = button("Xóa các dòng đã tích…", self.delete_checked, "Danger", "trash")
        lay.addLayout(toolbar(self.b_edit, self.b_open, self.b_ignore, self.b_export, "stretch", self.b_del))

    # ---------------------------------------------------------------- dữ liệu
    @property
    def kind(self) -> str:
        return self.kinds[max(self.tabs.currentIndex(), 0)]

    def refresh(self):
        keep_p, keep_h = self.doi_tac.value(), self.hop_dong.value()
        for w in (self.doi_tac, self.hop_dong):
            w.blockSignals(True)
        self.doi_tac.set_options([("Tất cả", None)] + [(p["TenDoiTac"], p["Ma"]) for p in self.ctx.cs.partners()],
                                 keep_p)
        self.hop_dong.set_options([("Tất cả", None)] + [(f"{h['SoHopDong']} – {h['TenHopDong']}", h["Ma"])
                                                         for h in self.ctx.cs.contracts()], keep_h)
        for w in (self.doi_tac, self.hop_dong):
            w.blockSignals(False)
        fdb = self.ctx.finance.fdb if self.ctx.finance is not None else None
        n = dr.counts(self.ctx.db, fdb)
        for i, (k, label) in enumerate(dr.KINDS):
            self.tabs.setTabText(i, f"{label} ({n[k]})" if n.get(k) else label)
            self.tabs.setTabEnabled(i, self.ctx.can(FEATURE[k]) and (k != "thu_chi" or fdb is not None))
        self.reload()

    def reload(self):
        kind = self.kind
        fdb = self.ctx.finance.fdb if self.ctx.finance is not None else None
        tu, den = relative_range(self.ky.value(), date.today()) if self.ky.value() else ("", "")
        self.groups = dr.scan(self.ctx.db, kind, fdb=fdb, tu=tu or None, den=den or None,
                              doi_tac=self.doi_tac.value(), ma_hd=self.hop_dong.value(),
                              show_ignored=self.show_ignored.isChecked())
        cols = dr.COLUMNS[kind]
        first = cols[0][0]
        rows = []
        for gi, g in enumerate(self.groups):
            rows.append({"_kieu": "nhom", first: f"Nhóm {gi + 1} · {g.title}"
                         + (" · đã đánh dấu không trùng" if g.ignored else ""), "_g": gi})
            for r in g.rows:
                rows.append(dict(r, _g=gi, _mau=None if r.get("Goc") else "warn"))
        self.table.set_rows(rows, cols)
        for i, r in enumerate(self.table.rows):        # ô tích ở cột đầu: bản nghi trùng tích sẵn
            if r.get("_kieu"):
                continue
            it = self.table.item(i, 0)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if r.get("Goc") or self.groups[r["_g"]].ignored else Qt.Checked)
        n_rows = sum(len(g.suspects()) for g in self.groups)
        self.count.setText(f"{len(self.groups)} nhóm · {n_rows} dòng nghi trùng" if kind != "thu_thua"
                           else f"{len(self.groups)} hóa đơn / hợp đồng thu thừa")
        self.note.setText(
            "Mỗi hóa đơn / hợp đồng thu thừa là một nhóm, dưới là các lần thanh toán của nó; lần nghi ghi trùng tô cam "
            "và tích sẵn. Xóa lần ghi thừa, hoặc sửa số tiền / hóa đơn liên quan cho đúng." if kind == "thu_thua" else
            "Dòng đầu mỗi nhóm (nền trắng) là bản ghi nhập trước; các dòng tô cam là bản nghi trùng và được tích sẵn. "
            "Bấm đúp một dòng để mở hợp đồng. Không phải trùng thì chọn nhóm rồi bấm Đánh dấu \"không trùng\".")
        can = self.ctx.can(FEATURE[kind], "Xoa")
        self.b_del.setEnabled(can and bool(self.groups))
        self.b_edit.setEnabled(self.ctx.can(FEATURE[kind], "Sua") and bool(self.groups))
        self.b_ignore.setEnabled(kind != "thu_thua" and bool(self.groups))
        self.b_open.setEnabled(kind not in ("doi_tac", "thu_chi"))

    def checked(self) -> list[dict]:
        return [r for i, r in enumerate(self.table.rows)
                if not r.get("_kieu") and self.table.item(i, 0).checkState() == Qt.Checked]

    def _open(self, r):
        if r and (r.get("MaHopDong") or self.kind == "hop_dong" and r.get("Ma")):
            self.open_contract.emit(r.get("MaHopDong") or r["Ma"])

    # ---------------------------------------------------------------- thao tác
    def edit(self):
        r = self.table.current()
        if not r or r.get("_kieu"):
            return info(self, "Chọn một dòng (không phải dòng tiêu đề nhóm) để sửa.")
        kind, ma, ok = self.kind, r["Ma"], False
        if kind in ("thanh_toan", "thu_thua"):
            from .contracts_page import edit_payment
            ok = edit_payment(self.ctx, self, ma)
        elif kind in ("hoa_don_ra", "hoa_don_vao"):
            ok = self._invoice_helper(kind)._save(dict(self.ctx.db.get(dr.TABLES[kind], ma)), ma, parent=self)
        elif kind == "hop_dong":
            from .contracts_page import contract_dialog
            v = contract_dialog(self.ctx, self, dict(self.ctx.db.get("HopDong", ma)))
            ok = bool(v and run_safely(self, self.ctx.cs.save_contract, v, ma))
        elif kind == "doi_tac":
            from .contracts_page import partner_dialog
            ok = bool(partner_dialog(self.ctx, self, dict(self.ctx.db.get("DoiTac", ma)), ma))
        elif kind == "thu_chi" and self.ctx.finance is not None:
            from .finance_page import VoucherDialog
            v = dict(self.ctx.finance.fdb.get("PhieuThuChi", ma))
            ok = VoucherDialog(self.ctx.finance, v["LoaiThuChi"], v, self).exec() == QDialog.Accepted
        if ok:
            self.refresh()

    def _invoice_helper(self, kind):
        if kind not in self._inv_helpers:
            from .invoices_page import InvoicesPage
            page = InvoicesPage(self.ctx, "ra" if kind == "hoa_don_ra" else "vao")
            page.hide()
            self._inv_helpers[kind] = page
        return self._inv_helpers[kind]

    def mark_ignored(self):
        """Nhóm của các dòng đang chọn (hoặc đã tích) là dữ liệu đúng, không phải trùng: thôi cảnh báo."""
        gis = sorted({r["_g"] for r in self.table.selected() or self.checked()})
        if not gis:
            return info(self, "Chọn (bấm vào) một dòng của nhóm cần đánh dấu.")
        groups = [self.groups[i] for i in gis]
        undo = all(g.ignored for g in groups)
        names = "\n".join(f"• {g.title}" for g in groups[:10])
        if undo:
            if confirm(self, f"Bỏ đánh dấu \"không trùng\" cho {len(groups)} nhóm?\n{names}", "Bỏ đánh dấu"):
                dr.set_ignored(self.ctx.db, self.kind, [g.key for g in groups], False)
                self.refresh()
            return
        if confirm(self, f"Xác nhận {len(groups)} nhóm sau là dữ liệu đúng, không phải nhập trùng?\n{names}\n\n"
                   "Phần mềm thôi cảnh báo các nhóm này (đồng bộ sang máy khác). Nếu sau này có bản ghi mới trùng thêm "
                   "thì nhóm sẽ hiện lại.", "Đánh dấu không trùng"):
            dr.set_ignored(self.ctx.db, self.kind, [g.key for g in groups if not g.ignored])
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Đánh dấu không trùng",
                            f"{dr.KIND_LABELS[self.kind]}: {len(groups)} nhóm")
            self.refresh()

    def delete_checked(self):
        rows = self.checked()
        kind = self.kind
        if not rows:
            return info(self, "Tích ô ở đầu các dòng cần xóa.")
        if not self.ctx.can(FEATURE[kind], "Xoa"):
            return info(self, "Tài khoản không có quyền xóa loại dữ liệu này.")
        details = [row_label(kind, r) for r in rows[:12]] + ([f"… và {len(rows) - 12} dòng khác"] if len(rows) > 12
                                                              else [])
        whole = [g.title for gi, g in enumerate(self.groups)
                 if g.rows and all(any(x["Ma"] == r["Ma"] for x in rows) for r in g.rows)]
        if whole:
            details.append("CHÚ Ý: xóa hết mọi dòng của nhóm (cả bản gốc): " + "; ".join(whole[:5]))
        if kind == "hoa_don_ra":
            details.append("Các lần thanh toán của hóa đơn bị xóa vẫn giữ trong hợp đồng (thành tạm ứng chưa gắn "
                           "hóa đơn).")
        if kind == "hop_dong":
            details.append("Xóa hợp đồng kéo theo toàn bộ công việc, hóa đơn đầu ra, thanh toán của hợp đồng đó.")
        if kind == "thu_chi":
            details.append("Phiếu thu chi bị xóa vĩnh viễn, không khôi phục được.")
        if not confirm_delete(self, f"{len(rows)} dòng {dr.KIND_LABELS[kind].lower()}", details,
                              permanent=kind == "thu_chi", count=len(rows)):
            return
        done, fails = 0, []
        for r in rows:
            try:
                self._delete_one(kind, r["Ma"])
                done += 1
            except ValidationError as e:
                fails.append(f"{row_label(kind, r)}: {e}")
        if done:
            self.ctx.db.log(self.ctx.user["TenDangNhap"], "Xóa dữ liệu nghi trùng",
                            f"{dr.KIND_LABELS[kind]}: {done} dòng")
        if fails:
            error(self, f"Đã xóa {done} dòng. Không xóa được:\n" + "\n".join(fails[:10]))
        self.refresh()

    def _delete_one(self, kind: str, ma: str) -> None:
        cs = self.ctx.cs
        if kind in ("thanh_toan", "thu_thua"):
            cs.delete_payment(ma)
        elif kind == "hoa_don_ra":
            cs.delete_out_invoice(ma)
        elif kind == "hoa_don_vao":
            cs.delete_in_invoice(ma)
        elif kind == "hop_dong":
            cs.delete_contract(ma)
        elif kind == "doi_tac":
            cs.delete_partner(ma)
        elif kind == "thu_chi":
            self.ctx.finance.delete_vouchers([ma])

    def export(self):
        if not self.groups:
            return info(self, "Không có dòng nào để xuất.")
        path, _ = QFileDialog.getSaveFileName(self, "Xuất Excel", f"Ra soat {dr.KIND_LABELS[self.kind]}.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        cols = dr.COLUMNS[self.kind]
        rows = [{"_kieu": "nhom", "_nhan": f"Nhóm {i + 1} · {g.title}"} if r is None else r
                for i, g in enumerate(self.groups) for r in [None] + g.rows]
        export_xlsx(Report(f"Rà soát {dr.KIND_LABELS[self.kind].lower()}", cols, rows, None, sheet="Rà soát"), path,
                    company=self.ctx.company, user=self.ctx.user["HoTen"])
        info(self, f"Đã xuất file:\n{path}")
