"""Tệp gốc hóa đơn, thư mục hóa đơn và Gemini: cài đặt, quét khi mở phần mềm, mục Cần xem, mở / xuất PDF, kiểm tra
hóa đơn bằng Gemini và xem kết quả."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPageSize, QPainter, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QTabWidget, QVBoxLayout, QWidget)

from ..core import gemini as gm
from ..core import invoice_folder as fo
from ..core import invoice_match, xml_invoice
from ..core.gemini import GeminiError, GeminiService
from ..core.invoice_folder import KIND_LABEL, TABLE, InvoiceFolders
from ..core.reports import fmt_date, fmt_money
from .widgets import (Combo, DataTable, button, confirm, confirm_changes, copy_text, error, hint, info, local_time, tagged,
                      toolbar)

LOOKUP_URL = "https://hoadondientu.gdt.gov.vn"
KEY_URL = "https://aistudio.google.com/app/apikey"


# ------------------------------------------------------------------ chạy việc lâu ở luồng nền
class _Task(QObject):
    done = Signal(object, object)          # (kết quả, lỗi)


_ALIVE: set = set()


def run_bg(fn, on_done) -> None:
    """Chạy fn() ở luồng nền (không làm đơ phần mềm); on_done(kết quả, lỗi) chạy lại ở luồng giao diện.
    fn không được dùng CSDL (SQLite gắn với luồng giao diện)."""
    task = _Task()
    _ALIVE.add(task)

    def finish(res, err):
        _ALIVE.discard(task)
        on_done(res, err)
    task.done.connect(finish)

    def run():
        try:
            res, err = fn(), None
        except Exception as e:  # noqa: BLE001 - trả lỗi về luồng giao diện
            res, err = None, e
        task.done.emit(res, err)
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ mở tệp / thư mục
def open_path(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal(path) -> None:
    """Mở thư mục chứa tệp, chọn sẵn tệp đó (Windows, macOS)."""
    p = Path(path)
    try:
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", str(p)])
            return
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(p)])
            return
    except OSError:
        pass
    open_path(p.parent)


def inv_kind(table_or_kind: str) -> str:
    return "ra" if table_or_kind in ("ra", "HoaDonDauRa") else "vao"


def invoice_no(kind: str, inv) -> str:
    inv = dict(inv)
    so = inv.get("SoHoaDon") if kind == "ra" else inv.get("SoHoaDonGoc")
    return f"{inv.get('KyHieuHD') or ''} số {so or ''}".strip()


def display_fields(ctx, kind: str, inv) -> dict:
    """Cột Tên file gốc / Thư mục / Gemini cho danh sách hóa đơn."""
    f = InvoiceFolders(ctx.db)
    inv = dict(inv)
    folder = f.sub_folder(inv)
    if not folder and inv.get("DuongDanTep") and not inv.get("TepTuongDoi"):
        folder = str(Path(inv["DuongDanTep"]).parent)
    st = inv.get("GeminiTT") or ""
    label = {gm.ST_OK: "✓ OK", gm.ST_ISSUE: "⚠ Có vấn đề", gm.ST_SEEN: "Đã xem"}.get(st, "")
    if inv.get("NguonSoLieu") == fo.SRC_GEMINI:
        label = (label + " · " if label else "") + "Gemini đọc, chờ soát"
    out = {"TenFileGoc": f.file_name(inv), "ThuMuc": folder, "GeminiHT": label}
    if st == gm.ST_ISSUE:
        out["_mau"] = "danger"
    elif inv.get("NguonSoLieu") == fo.SRC_GEMINI:
        out["_mau"] = "warn"
    return out


class FileActions:
    """Các thao tác với tệp gốc của hóa đơn (dùng ở danh sách hóa đơn và cửa sổ Thư mục hóa đơn)."""

    def __init__(self, ctx, kind: str, parent: QWidget, after=None):
        self.ctx, self.kind, self.parent = ctx, kind, parent
        self.folders = InvoiceFolders(ctx.db)
        self.after = after or (lambda: None)

    def menu(self, rows: list[dict], gemini: bool = True) -> QMenu:
        m = QMenu(self.parent)
        one = rows[0] if len(rows) == 1 else None
        m.addAction("Mở tệp gốc", lambda: self.open(one)).setEnabled(one is not None)
        m.addAction("Mở thư mục chứa", lambda: self.open_folder(one)).setEnabled(one is not None)
        m.addAction("Sao chép tên file", lambda: self.copy(rows, False)).setEnabled(bool(rows))
        m.addAction("Sao chép đường dẫn", lambda: self.copy(rows, True)).setEnabled(bool(rows))
        m.addSeparator()
        m.addAction("Xuất PDF ra thư mục khác…", lambda: self.export_pdf(rows)).setEnabled(bool(rows))
        if self.ctx.can(TABLE[self.kind], "Sua"):
            m.addAction("Gắn / đổi tệp gốc…", lambda: self.attach(one)).setEnabled(one is not None)
        if gemini:
            m.addSeparator()
            m.addAction("Kiểm tra bằng Gemini", lambda: self.check(rows)).setEnabled(bool(rows))
            m.addAction("Xem kết quả Gemini…", lambda: self.show_result(one)).setEnabled(
                one is not None and bool(one.get("GeminiKQ")))
            if self.ctx.can(TABLE[self.kind], "Sua"):
                m.addAction("Xác nhận số liệu Gemini đọc", lambda: self.confirm_gemini(rows)).setEnabled(
                    any(r.get("NguonSoLieu") == fo.SRC_GEMINI for r in rows))
        return m

    def _file(self, inv, view: bool = True) -> Path | None:
        if inv is None:
            return None
        p = self.folders.view_file(self.kind, inv) if view else self.folders.resolve(self.kind, inv)
        if p is None:
            name = self.folders.file_name(inv)
            if name:
                error(self.parent, f"Không tìm thấy tệp gốc \"{name}\" trên máy này.\n\nKiểm tra Cài đặt › Thư mục "
                                   "hóa đơn (máy này phải đặt cùng cấu trúc thư mục với máy đã nhập hóa đơn).")
            else:
                info(self.parent, "Hóa đơn này chưa gắn tệp gốc. Chọn \"Gắn / đổi tệp gốc…\" để gắn.")
        return p

    def open(self, inv) -> None:
        p = self._file(inv)
        if p:
            open_path(p)

    def open_folder(self, inv) -> None:
        p = self._file(inv, view=False)
        if p:
            reveal(p)

    def copy(self, rows: list[dict], full: bool) -> None:
        out = []
        for r in rows:
            if full:
                p = self.folders.resolve(self.kind, r)
                if p:
                    out.append(str(p))
            elif self.folders.file_name(r):
                out.append(self.folders.file_name(r))
        if not out:
            return info(self.parent, "Các hóa đơn đã chọn chưa gắn tệp gốc.")
        copy_text("\n".join(out))
        self.parent.window().statusBar().showMessage(f"Đã sao chép {len(out)} dòng.", 4000) \
            if hasattr(self.parent.window(), "statusBar") else None

    def attach(self, inv) -> None:
        if inv is None:
            return
        root = self.folders.root(self.kind)
        path, _ = QFileDialog.getOpenFileName(self.parent, "Chọn tệp gốc của hóa đơn", str(root or ""),
                                              "Hóa đơn (*.xml *.pdf *.jpg *.jpeg *.png *.webp)")
        if not path:
            return
        old = self.folders.file_name(inv) or "(chưa gắn)"
        if not confirm_changes(self.parent, [("Tệp gốc", old, Path(path).name)], "Gắn tệp gốc"):
            return
        v = {"DuongDanTep": path, "TepTuongDoi": None, "TepKem": None}
        try:
            v["TepTuongDoi"] = Path(path).resolve().relative_to(root.resolve()).as_posix() if root else None
        except ValueError:
            pass
        self.ctx.db.update(TABLE[self.kind], inv["Ma"], v)
        self.after()

    def confirm_gemini(self, rows: list[dict]) -> None:
        rows = [r for r in rows if r.get("NguonSoLieu") == fo.SRC_GEMINI]
        if not rows or not confirm(self.parent, f"Xác nhận đã soát số liệu {len(rows)} hóa đơn Gemini đọc từ ảnh / "
                                                "PDF quét (đúng với hóa đơn gốc)?"):
            return
        for r in rows:
            self.ctx.db.update(TABLE[self.kind], r["Ma"], {"NguonSoLieu": fo.SRC_GEMINI_OK})
        self.after()

    # ---- Gemini
    def check(self, rows: list[dict]) -> None:
        check_invoices(self.ctx, self.kind, rows, self.parent, self.after, show=len(rows) == 1)

    def show_result(self, inv) -> None:
        if inv is not None:
            GeminiResultDialog(self.ctx, self.kind, inv["Ma"], self.parent).exec()
            self.after()

    # ---- xuất PDF
    def export_pdf(self, rows: list[dict]) -> None:
        ExportPdfDialog(self.ctx, self.kind, rows, self.parent).exec()


# ------------------------------------------------------------------ Gemini kiểm tra hóa đơn
def check_invoices(ctx, kind: str, rows: list[dict], parent, after=None, show: bool = False, quiet: bool = False):
    """Gửi tệp gốc các hóa đơn cho Gemini kiểm tra (luồng nền), ghi kết quả. quiet: tự kiểm tra hóa đơn mới."""
    svc = GeminiService(ctx.db)
    if not svc.configured():
        if not quiet:
            info(parent, "Chưa cài khóa API Gemini. Vào Cài đặt › Thư mục hóa đơn và Gemini để nhập khóa.")
        return
    folders = InvoiceFolders(ctx.db)
    jobs, missing = [], []
    for r in rows:
        p = folders.resolve(kind, r)
        if p is None or p.suffix.lower() not in (".xml", ".pdf", ".jpg", ".jpeg", ".png", ".webp"):
            missing.append(invoice_no(kind, r))
        else:
            jobs.append((r["Ma"], str(p)))
    if not jobs:
        if not quiet:
            info(parent, "Các hóa đơn đã chọn chưa gắn tệp gốc (XML / PDF / ảnh) trên máy này.")
        return
    if not quiet and len(jobs) > 1 and not confirm(
            parent, f"Gửi tệp gốc {len(jobs)} hóa đơn cho Gemini kiểm tra?" +
            (f"\n\n{len(missing)} hóa đơn chưa có tệp gốc sẽ bỏ qua." if missing else "")):
        return
    gem = svc.client()
    win = parent.window() if parent is not None else None
    if win is not None and hasattr(win, "statusBar"):
        win.statusBar().showMessage(f"Gemini đang kiểm tra {len(jobs)} hóa đơn…")

    def work():
        out = []
        for ma, path in jobs:
            try:
                out.append((ma, gem.check_invoice(path), None))
            except GeminiError as e:
                out.append((ma, None, e))
                if e.offline:
                    break
        return out

    def done(res, err):
        res = res or []
        ok = issue = 0
        errs = []
        for ma, kq, e in res:
            if e is not None:
                svc.record_error(e)
                errs.append(str(e))
                continue
            svc.record_ok()
            if ctx.db.get(TABLE[kind], ma) is None:
                continue
            st = svc.save_check(kind, ma, kq)
            ok += st == gm.ST_OK
            issue += st == gm.ST_ISSUE
        if err is not None:
            errs.append(str(err))
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(f"Gemini đã kiểm tra: {ok} OK, {issue} có vấn đề"
                                        + (f", lỗi: {errs[0]}" if errs else ""), 15_000)
        if after:
            after()
        if hasattr(win, "update_badges"):
            win.update_badges()
        if quiet:
            return
        if show and len(res) == 1 and res[0][1] is not None:
            GeminiResultDialog(ctx, kind, res[0][0], parent).exec()
            if after:
                after()
        elif errs:
            error(parent, f"Gemini đã kiểm tra {ok + issue} hóa đơn ({ok} OK, {issue} có vấn đề).\n\n" + errs[0])
        else:
            info(parent, f"Gemini đã kiểm tra {ok + issue} hóa đơn: {ok} OK, {issue} có vấn đề.")
    run_bg(work, done)


class GeminiResultDialog(QDialog):
    """Kết quả Gemini kiểm tra một hóa đơn: bảng so sánh với số liệu trong phần mềm, các lưu ý."""

    FIX = {"vao": {"Tên người bán": ("TenNhaCungCap", "ten_nguoi_ban"), "MST người bán": ("MaThueNCC", "mst_nguoi_ban"),
                   "Số hóa đơn": ("SoHoaDonGoc", "so_hoa_don"), "Ngày lập": ("NgayNhanHD", "ngay_lap")},
           "ra": {"Người mua": ("TenNguoiMua", "ten_nguoi_mua"), "MST người mua": ("MSTNguoiMua", "mst_nguoi_mua"),
                  "Số hóa đơn": ("SoHoaDon", "so_hoa_don"), "Ngày lập": ("NgayXuatHD", "ngay_lap")}}

    def __init__(self, ctx, kind: str, ma: str, parent=None):
        super().__init__(parent)
        self.ctx, self.kind, self.ma = ctx, kind, ma
        self.svc = GeminiService(ctx.db)
        self.setWindowTitle("Kết quả kiểm tra hóa đơn bằng Gemini")
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.table = DataTable([("truong", "Trường", "text"), ("phan_mem", "Trong phần mềm", "text"),
                                ("gemini", "Gemini đọc từ tệp", "text"), ("ket_qua", "Kết quả", "text")])
        lay.addWidget(self.table, 1)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.notes)
        lay.addWidget(hint("Gemini chỉ đọc và soát nội dung tệp hóa đơn, không tra cứu được trên trang của cơ quan "
                           "thuế. Muốn biết hóa đơn còn hiệu lực, bấm \"Tra cứu trên trang thuế\" và nhập thông tin "
                           "hóa đơn."))
        bb = QDialogButtonBox()
        can_edit = ctx.can(TABLE[kind], "Sua")
        self.b_fix = bb.addButton("Sửa theo Gemini…", QDialogButtonBox.ActionRole)
        self.b_fix.clicked.connect(self.fix)
        self.b_fix.setEnabled(can_edit)
        self.b_seen = bb.addButton("Đánh dấu đã xem", QDialogButtonBox.ActionRole)
        self.b_seen.clicked.connect(self.seen)
        self.b_seen.setEnabled(can_edit)
        b = bb.addButton("Kiểm tra lại", QDialogButtonBox.ActionRole)
        b.clicked.connect(self.recheck)
        b = bb.addButton("Tra cứu trên trang thuế", QDialogButtonBox.ActionRole)
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(LOOKUP_URL)))
        bb.addButton("Đóng", QDialogButtonBox.RejectRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.reload()

    def _data(self) -> tuple[dict, dict]:
        inv = dict(self.ctx.db.get(TABLE[self.kind], self.ma))
        try:
            data = json.loads(inv.get("GeminiKQ") or "{}")
        except ValueError:
            data = {}
        return inv, data

    def reload(self):
        inv, data = self._data()
        st = inv.get("GeminiTT") or "Chưa kiểm tra"
        when = local_time(inv.get("GeminiLuc"))
        tone = {gm.ST_OK: "success", gm.ST_ISSUE: "danger"}.get(st, "muted")
        self.head.setText(f"<b>Hóa đơn {KIND_LABEL[self.kind].lower()} {invoice_no(self.kind, inv)}</b> – "
                          f"<span style='font-weight:600' class='{tone}'>{st}</span>"
                          + (f" · kiểm tra lúc {when}" if when else "")
                          + (f" · mô hình {data.get('mo_hinh')}" if data.get("mo_hinh") else ""))
        rows = []
        for r in data.get("so_sanh") or []:
            k = r.get("khop")
            rows.append({"truong": r["truong"], "phan_mem": _show(r.get("phan_mem")), "gemini": _show(r.get("gemini")),
                         "ket_qua": "Khớp" if k else "Khác" if k is False else "",
                         "_mau": "danger" if k is False else None})
        self.table.set_rows(rows)
        notes = data.get("luu_y") or []
        kq = data.get("ket_qua") or {}
        txt = []
        if kq.get("hop_le") is False:
            txt.append("Gemini đánh giá cấu trúc hóa đơn KHÔNG hợp lệ.")
        txt += [f"• {n}" for n in notes]
        self.notes.setText("\n".join(txt) if txt else "Không có lưu ý.")
        fixable = [r for r in data.get("so_sanh") or [] if r.get("khop") is False and r["truong"] in self.FIX[self.kind]]
        self.b_fix.setVisible(bool(fixable))
        self.b_seen.setVisible(st == gm.ST_ISSUE)

    def fix(self):
        inv, data = self._data()
        kq = data.get("ket_qua") or {}
        changes, v = [], {}
        for r in data.get("so_sanh") or []:
            if r.get("khop") is False and r["truong"] in self.FIX[self.kind]:
                col, src = self.FIX[self.kind][r["truong"]]
                new = (kq.get(src) or "").strip()
                if new:
                    v[col] = new[:10] if col.startswith("Ngay") else new
                    changes.append((r["truong"], _show(inv.get(col)), new))
        if not changes or not confirm_changes(self, changes, "Sửa theo kết quả Gemini đọc"):
            return
        try:
            self.ctx.db.update(TABLE[self.kind], self.ma, v)
        except Exception as e:  # noqa: BLE001
            return error(self, str(e))
        inv = dict(self.ctx.db.get(TABLE[self.kind], self.ma))
        self.svc.save_check(self.kind, self.ma, kq)
        self.reload()

    def seen(self):
        self.svc.mark_seen(self.kind, self.ma)
        self.reload()

    def recheck(self):
        inv, _ = self._data()
        check_invoices(self.ctx, self.kind, [inv], self, self.reload)


def _show(v) -> str:
    if v is None:
        return ""
    if isinstance(v, int) and not isinstance(v, bool):
        return fmt_money(v)
    s = str(v)
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return fmt_date(s[:10])
    return s


# ------------------------------------------------------------------ xuất PDF
def _invoice_html(d: dict, note: str) -> str:
    rows = "".join(f"<tr><td>{i}</td><td>{ln.get('TenHang') or ''}</td><td>{ln.get('DVT') or ''}</td>"
                   f"<td align=right>{ln.get('SoLuong') or ''}</td><td align=right>{fmt_money(ln.get('DonGia'))}</td>"
                   f"<td align=right>{fmt_money(ln.get('ThanhTien'))}</td></tr>"
                   for i, ln in enumerate(d.get("Dong") or [], start=1))
    return f"""<h2 align=center>HÓA ĐƠN GIÁ TRỊ GIA TĂNG</h2>
<p align=center>Ký hiệu {d.get('KyHieuMauSo') or ''}{d.get('KyHieuHD') or ''} – Số {d.get('SoHoaDon') or ''} – Ngày
{fmt_date(d.get('NgayLap'))}</p>
<p><b>Đơn vị bán:</b> {d.get('TenNguoiBan') or ''}<br>MST: {d.get('MSTNguoiBan') or ''}</p>
<p><b>Người mua:</b> {d.get('TenNguoiMua') or ''}<br>MST: {d.get('MSTNguoiMua') or ''}</p>
<table border=1 cellspacing=0 cellpadding=4 width=100%>
<tr><th>STT</th><th>Tên hàng hóa, dịch vụ</th><th>ĐVT</th><th>Số lượng</th><th>Đơn giá</th><th>Thành tiền</th></tr>
{rows}</table>
<p align=right>Cộng tiền hàng: <b>{fmt_money(d.get('GiaTriTruocThue'))}</b><br>Thuế suất: {d.get('ThueSuatHD') or ''} –
Tiền thuế GTGT: <b>{fmt_money(d.get('VAT'))}</b><br>Tổng cộng thanh toán: <b>{fmt_money(d.get('GiaTriSauThue'))}</b></p>
<p style='color:#666'>{note}</p>"""


def xml_html(path: str) -> str:
    return _invoice_html(xml_invoice.parse_invoice(path),
                         f"Bản thể hiện do phần mềm tạo từ tệp XML {Path(path).name} (không thay thế hóa đơn gốc).")


def db_html(ctx, kind: str, inv) -> str:
    """Bản thể hiện dựng từ số liệu hóa đơn đã lưu trong phần mềm (khi không còn tệp gốc trên máy này)."""
    inv = dict(inv)
    lic = ctx.db.one("SELECT MaCongTy, TenCongTy FROM BanQuyen WHERE Id = 1")
    cty, mst = (lic["TenCongTy"], lic["MaCongTy"]) if lic else ("", "")
    if kind == "vao":
        lines = [dict(r) for r in ctx.db.query("SELECT * FROM DongHoaDonDauVao WHERE MaHoaDon = ? ORDER BY STT",
                                                (inv["Ma"],))]
        d = {"SoHoaDon": inv.get("SoHoaDonGoc"), "NgayLap": inv.get("NgayNhanHD"),
             "TenNguoiBan": inv.get("TenNhaCungCap"), "MSTNguoiBan": inv.get("MaThueNCC"),
             "TenNguoiMua": cty, "MSTNguoiMua": mst}
    else:
        lines = []
        d = {"SoHoaDon": inv.get("SoHoaDon"), "NgayLap": inv.get("NgayXuatHD"), "TenNguoiBan": cty,
             "MSTNguoiBan": mst, "TenNguoiMua": inv.get("TenNguoiMua"), "MSTNguoiMua": inv.get("MSTNguoiMua")}
    if not lines and inv.get("MatHang"):
        lines = [{"TenHang": inv["MatHang"], "ThanhTien": inv.get("GiaTriTruocThue")}]
    d.update(KyHieuMauSo=inv.get("KyHieuMauSo"), KyHieuHD=inv.get("KyHieuHD"), Dong=lines,
             GiaTriTruocThue=inv.get("GiaTriTruocThue"), VAT=inv.get("VAT"), GiaTriSauThue=inv.get("GiaTriSauThue"),
             ThueSuatHD=inv.get("ThueSuat"))
    return _invoice_html(d, "Bản thể hiện do phần mềm tạo từ số liệu hóa đơn đã lưu (không tìm thấy tệp gốc trên "
                            "máy này; không thay thế hóa đơn gốc).")


def _html_pdf(html: str, target: Path) -> None:
    w = QPdfWriter(str(target))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(150)
    doc = QTextDocument()
    doc.setHtml(html)
    doc.print_(w)


def to_pdf(src: Path, target: Path) -> None:
    """Một tệp hóa đơn → PDF: PDF thì chép, ảnh thì đặt vào trang A4, XML thì dựng bản thể hiện."""
    ext = src.suffix.lower()
    if ext == ".pdf":
        shutil.copyfile(src, target)
        return
    if ext == ".xml":
        return _html_pdf(xml_html(str(src)), target)
    w = QPdfWriter(str(target))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(150)
    img = QImage(str(src))
    if img.isNull():
        raise ValueError(f"Không đọc được ảnh {src.name}")
    p = QPainter(w)
    page = QRectF(0, 0, w.width(), w.height())
    s = min(page.width() / img.width(), page.height() / img.height())
    p.drawImage(QRectF(0, 0, img.width() * s, img.height() * s), img)
    p.end()


def merge_pdfs(files: list[Path], target: Path) -> None:
    from pypdf import PdfWriter
    out = PdfWriter()
    for f in files:
        out.append(str(f))
    with open(target, "wb") as fh:
        out.write(fh)


class ExportPdfDialog(QDialog):
    """Xuất tệp gốc các hóa đơn đã chọn ra thư mục khác dưới dạng PDF (tùy chọn gộp thành một tệp)."""

    def __init__(self, ctx, kind: str, rows: list[dict], parent=None):
        super().__init__(parent)
        self.ctx, self.kind = ctx, kind
        folders = InvoiceFolders(ctx.db)
        self.files = []                 # (hóa đơn, tệp gốc hoặc None = dựng từ số liệu đã lưu)
        for r in rows:
            self.files.append((r, folders.view_file(kind, r)))
        self.setWindowTitle("Xuất PDF ra thư mục khác")
        self.resize(560, 260)
        lay = QVBoxLayout(self)
        no_file = sum(1 for _, p in self.files if p is None)
        lab = QLabel(f"{len(self.files)} hóa đơn sẽ được xuất PDF (XML được dựng thành bản thể hiện, ảnh đặt vào "
                     "trang A4)." + (f" {no_file} hóa đơn không tìm thấy tệp gốc trên máy này: dựng bản thể hiện từ "
                                     "số liệu đã lưu trong phần mềm." if no_file else ""))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        form = QFormLayout()
        self.target = QLineEdit(ctx.db.setting("tmhd_xuat_pdf") or str(Path.home()))
        pick = button("Chọn…", self._pick)
        row = QHBoxLayout()
        row.addWidget(self.target, 1)
        row.addWidget(pick)
        form.addRow("Thư mục đích", row)
        self.merge = QCheckBox("Gộp thành một tệp PDF")
        self.merge_name = QLineEdit(f"HoaDon_{KIND_LABEL[kind].replace(' ', '')}.pdf")
        form.addRow(self.merge)
        form.addRow("Tên tệp gộp", self.merge_name)
        self.merge.toggled.connect(self.merge_name.setEnabled)
        self.merge_name.setEnabled(False)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Xuất PDF")
        bb.button(QDialogButtonBox.Ok).setEnabled(bool(self.files))
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.run)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục đích", self.target.text())
        if d:
            self.target.setText(d)

    def run(self):
        target = Path(self.target.text().strip())
        if not target.is_dir():
            return error(self, "Thư mục đích không tồn tại.")
        self.ctx.db.set_setting("tmhd_xuat_pdf", str(target))
        try:
            n = self.export(target, self.merge.isChecked(), self.merge_name.text().strip())
        except Exception as e:  # noqa: BLE001
            return error(self, f"Không xuất được PDF: {e}")
        info(self, f"Đã xuất {n} tệp PDF vào:\n{target}")
        reveal(target)
        self.accept()

    def _name(self, r, p) -> str:
        if p is not None:
            return p.with_suffix(".pdf").name
        r = dict(r)
        so = r.get("SoHoaDon") if self.kind == "ra" else r.get("SoHoaDonGoc")
        raw = f"HoaDon_{r.get('KyHieuHD') or ''}_{so or r['Ma']}"
        return "".join(c if c.isalnum() or c in "_-" else "_" for c in raw) + ".pdf"

    def _one(self, r, p, dest: Path) -> None:
        if p is None:
            _html_pdf(db_html(self.ctx, self.kind, r), dest)
        else:
            to_pdf(p, dest)

    def export(self, target: Path, merge: bool, merge_name: str = "", ask=True) -> int:
        names = [self._name(r, p) for r, p in self.files]
        if merge:
            name = merge_name or "HoaDon.pdf"
            dest = target / (name if name.lower().endswith(".pdf") else name + ".pdf")
            if dest.exists() and ask and not confirm(self, f"Tệp {dest.name} đã có trong thư mục đích. Ghi đè?"):
                return 0
            tmpdir = target / ".qlhd_tam"
            tmpdir.mkdir(exist_ok=True)
            try:
                parts = []
                for i, (r, p) in enumerate(self.files):
                    t = tmpdir / f"{i:04d}.pdf"
                    self._one(r, p, t)
                    parts.append(t)
                merge_pdfs(parts, dest)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return 1
        exist = [n for n in names if (target / n).exists()]
        if exist and ask and not confirm(self, f"{len(exist)} tệp đã có trong thư mục đích (VD {exist[0]}). Ghi đè?"):
            return 0
        for (r, p), n in zip(self.files, names):
            self._one(r, p, target / n)
        return len(self.files)


# ------------------------------------------------------------------ cài đặt
class FolderSettingsDialog(QDialog):
    """Cài đặt (riêng máy này): thư mục hóa đơn đầu ra / đầu vào, quét khi mở, khóa API Gemini."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.folders = InvoiceFolders(ctx.db)
        self.gem = GeminiService(ctx.db)
        s, g = self.folders.settings(), self.gem.settings()
        self.setWindowTitle("Thư mục hóa đơn và Gemini")
        self.resize(720, 600)
        lay = QVBoxLayout(self)
        box = QGroupBox("Thư mục hóa đơn trên máy này")
        f = QFormLayout(box)
        self.dirs = {}
        for kind in fo.KINDS:
            e = QLineEdit(s[kind])
            e.setPlaceholderText("Chưa đặt – bấm Chọn…")
            row = QHBoxLayout()
            row.addWidget(e, 1)
            row.addWidget(button("Chọn…", lambda _=False, e=e: self._pick(e)))
            f.addRow(f"Hóa đơn {KIND_LABEL[kind].lower()}", row)
            self.dirs[kind] = e
        f.addRow(hint("Phần mềm quét cả các thư mục con (Năm, Quý…). Chỉ xử lý file mới; file đã xử lý không nhập "
                      "lại, không ghi đè số liệu. Lần quét đầu tiên một thư mục không tự thêm: hóa đơn chưa có trong "
                      "phần mềm nằm ở mục Cần xem để chọn."))
        self.on_open = QCheckBox("Tự quét khi mở phần mềm")
        self.on_open.setChecked(s["quet_khi_mo"])
        self.auto_add = QCheckBox("Tự thêm hóa đơn mới đọc được rõ ràng (nghi trùng, chưa nhận ra hợp đồng… vẫn để "
                                  "Cần xem)")
        self.auto_add.setChecked(s["tu_them"])
        types = QHBoxLayout()
        self.t_xml, self.t_pdf, self.t_img = QCheckBox("XML"), QCheckBox("PDF"), QCheckBox("Ảnh (JPG, PNG)")
        for b, k in ((self.t_xml, "xml"), (self.t_pdf, "pdf"), (self.t_img, "anh")):
            b.setChecked(s[k])
            types.addWidget(b)
        types.addStretch(1)
        f.addRow(self.on_open)
        f.addRow(self.auto_add)
        f.addRow("Loại file", types)
        lay.addWidget(box)
        box = QGroupBox("Gemini (Google) hỗ trợ hóa đơn")
        f = QFormLayout(box)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Đã cài – để trống là giữ nguyên" if g["co_khoa"] else "Dán khóa API Gemini")
        krow = QHBoxLayout()
        krow.addWidget(self.key, 1)
        krow.addWidget(button("Lấy khóa…", lambda: QDesktopServices.openUrl(QUrl(KEY_URL))))
        if g["co_khoa"]:
            krow.addWidget(button("Xóa khóa", self._clear_key))
        f.addRow("Khóa API", krow)
        self.model = Combo()
        self._set_models([], g["mo_hinh"])
        f.addRow("Mô hình", self.model)
        self.state = QLabel()
        self.state.setWordWrap(True)
        test = button("Kiểm tra kết nối", self.test)
        srow = QHBoxLayout()
        srow.addWidget(self.state, 1)
        srow.addWidget(test)
        f.addRow("Trạng thái", srow)
        self.auto_check = QCheckBox("Tự kiểm tra hóa đơn mới quét được (OK / Có vấn đề)")
        self.auto_check.setChecked(g["tu_kiem"])
        self.auto_class = QCheckBox("Tự phân loại dòng hóa đơn đầu vào theo danh mục (CCDC / TSCĐ / chi phí); dòng kế "
                                    "toán đã chọn không bị ghi đè")
        self.auto_class.setChecked(g["tu_phan_loai"])
        self.auto_read = QCheckBox("Đọc số liệu file ảnh / PDF quét (không có XML)")
        self.auto_read.setChecked(g["doc_anh"])
        for b in (self.auto_check, self.auto_class, self.auto_read):
            f.addRow(b)
        f.addRow(hint("Khóa API lưu mã hóa, chỉ dùng trên máy này, không đi theo gói đồng bộ. Gemini chỉ đọc nội dung "
                      "tệp hóa đơn, không tra cứu được trên trang của cơ quan thuế. Mất kết nối thì phần mềm vẫn làm "
                      "việc như bình thường và đánh dấu \"Chưa được Gemini rà soát\"."))
        lay.addWidget(box)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Lưu")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._show_state()

    def _pick(self, e: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục hóa đơn", e.text())
        if d:
            e.setText(d)

    def _show_state(self):
        st, text = self.gem.state()
        self.state.setText(text)
        tagged(self.state, "Hint" if st in ("chua_cai", "chua_ro") else "Success" if st == "ok" else "Warn")

    def _clear_key(self):
        if confirm(self, "Xóa khóa API Gemini trên máy này? Các việc Gemini sẽ dừng cho tới khi nhập khóa mới."):
            self.gem.save_settings({"khoa": "", "mo_hinh": self.model.value()})
            self.key.setPlaceholderText("Dán khóa API Gemini")
            self._show_state()

    def _set_models(self, names: list[str], current: str) -> None:
        opts = list(dict.fromkeys([current] + gm.MODELS + sorted(n for n in names if n.startswith("gemini"))))
        self.model.set_options([(m, m) for m in opts], current)

    def test(self):
        key = self.key.text().strip() or self.gem.key()
        if not key:
            return info(self, "Hãy nhập khóa API Gemini.")
        g = gm.Gemini(key, self.model.value(), timeout=30)
        self.state.setText("Đang kiểm tra…")

        def work():
            names = g.choose_model()   # chọn mô hình còn được cấp, đang trả lời nhanh nhất
            g.ping()
            return names

        def done(names, err):
            if err is None:
                moved = g.model != g.requested and g.requested not in gm.MODELS
                self._set_models(names or [], g.model)
                self.gem.save_settings({"khoa": self.key.text().strip() or None, "mo_hinh": g.model})
                if self.key.text().strip():
                    self.key.clear()
                    self.key.setPlaceholderText("Đã cài – để trống là giữ nguyên")
                self.gem.record_ok()
                self._show_state()
                if moved:
                    self.state.setText(self.state.text() + f" · mô hình {g.requested} đã ngừng cấp, "
                                                           f"đã chuyển sang {g.model}")
                return
            if isinstance(err, GeminiError) and not self.key.text().strip():
                self.gem.record_error(err)
            self.state.setText(str(err))
            tagged(self.state, "Warn")
        run_bg(work, done)

    def save(self):
        v = {k: e.text().strip() for k, e in self.dirs.items()}
        v.update(quet_khi_mo=self.on_open.isChecked(), tu_them=self.auto_add.isChecked(), xml=self.t_xml.isChecked(),
                 pdf=self.t_pdf.isChecked(), anh=self.t_img.isChecked())
        old = self.folders.settings()
        try:
            self.folders.save_settings(v)
        except Exception as e:  # noqa: BLE001
            return error(self, str(e))
        self.gem.save_settings({"khoa": self.key.text().strip() or None, "mo_hinh": self.model.value(),
                                "tu_kiem": self.auto_check.isChecked(), "tu_phan_loai": self.auto_class.isChecked(),
                                "doc_anh": self.auto_read.isChecked()})
        self.changed_dirs = any(old[k] != v[k] for k in fo.KINDS)
        self.accept()


def scan_text(s: dict) -> str:
    """Tóm tắt một lượt quét / đọc cho thanh trạng thái."""
    parts = []
    if s.get("them"):
        parts.append(f"tự thêm {s['them']} hóa đơn mới")
    if s.get("co_san"):
        parts.append(f"{s['co_san']} hóa đơn đã có (đã ghi nhận nơi chứa tệp)")
    if s.get("lech"):
        parts.append(f"{s['lech']} hóa đơn đã có nhưng số liệu khác")
    rest = (s.get("can_xem") or 0) - (s.get("lech") or 0)
    if rest > 0:
        parts.append(f"{rest} file cần xem")
    if s.get("cho_gemini"):
        parts.append(f"{s['cho_gemini']} file chờ Gemini (mất kết nối / hết hạn mức, lần quét sau đọc tiếp)")
    if s.get("loi"):
        parts.append("lỗi: " + s["loi"])
    return ", ".join(parts) + "." if parts else "không có gì mới."


# ------------------------------------------------------------------ quét thư mục (khi mở phần mềm / bấm Quét ngay)
class Scanner(QObject):
    """Quét các thư mục hóa đơn: tìm file mới và xét lại file còn chờ (luồng giao diện), đọc / nhờ Gemini đọc
    (luồng nền), ghi vào CSDL (luồng giao diện), rồi tự nhờ Gemini kiểm tra hóa đơn mới và phân loại dòng hóa đơn
    đầu vào mới."""
    finished = Signal(dict)
    progress = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.folders = InvoiceFolders(ctx.db)
        self.gem = GeminiService(ctx.db)
        self.busy = False

    def start(self, everything: bool = False) -> bool:
        """everything: quét lại toàn bộ file trong thư mục (hóa đơn đã có chỉ ghi nhận lại nơi chứa tệp)."""
        if self.busy or not self.folders.configured() or self.ctx.user is None:
            return False
        jobs = []
        for kind in fo.KINDS:
            if not self.ctx.can(TABLE[kind], "Them") or not self.folders.root(kind):
                continue
            self.folders.prune(kind)
            first = self.folders.first_scan(kind)
            again = self.folders.recheck_files(kind, everything)
            files = sorted(self.folders.new_files(kind) + again)
            if files or first:
                jobs.append((kind, files, self.folders.cache(kind, [rel for rel, _ in again]),
                             fo.known_keys(self.ctx.db, kind)))
        if not jobs:
            self.finished.emit({})
            return False
        self.busy = True
        client = None
        if self.gem.configured() and self.gem.flag(gm.K_AUTO_READ):
            client = self.gem.client()
        say = self.progress.emit
        say("Đang quét thư mục hóa đơn…")

        def work():
            return [(kind, InvoiceFolders.read(kind, files, client, cache=cache, known=known, progress=say))
                    for kind, files, cache, known in jobs]
        run_bg(work, self._done)
        return True

    def _done(self, res, err):
        self.busy = False
        summary = {"them": 0, "can_xem": 0, "co_san": 0, "lech": 0, "cho_gemini": 0, "loi": str(err) if err else "",
                   "lan_dau": False, "moi": {}}
        if err is None:
            mst = self.ctx.acc.license()["MaCongTy"] if self.ctx.acc.license() else ""
            for kind, items in res:
                if any(it.offline for it in items):
                    msg = next(it.error for it in items if it.offline)
                    self.gem.record_error(GeminiError(msg.replace("Gemini chưa đọc được: ", ""), "mang"))
                elif any(it.source == fo.SRC_GEMINI for it in items):
                    self.gem.record_ok()
                r = self.folders.process(kind, items, mst_cty=mst or "")
                for k in ("can_xem", "co_san", "lech", "cho_gemini"):
                    summary[k] += r[k]
                summary["them"] += len(r["them"])
                summary["lan_dau"] |= r["lan_dau"]
                summary["moi"][kind] = r["them"]
        summary["cho_xem_tong"] = self.folders.review_count()
        self.folders.save_summary({k: v for k, v in summary.items() if k != "moi"})
        self.after_scan(summary["moi"])
        self.finished.emit(summary)

    def after_scan(self, new: dict) -> None:
        """Hóa đơn vừa tự thêm: Gemini kiểm tra (nếu bật) và phân loại dòng hóa đơn đầu vào (nếu bật)."""
        if not self.gem.configured():
            return
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        if self.gem.flag(gm.K_AUTO_CHECK):
            for kind, mas in new.items():
                rows = [dict(r) for r in (self.ctx.db.get(TABLE[kind], m) for m in mas) if r]
                if rows:
                    check_invoices(self.ctx, kind, rows, parent, quiet=True)
        if self.gem.flag(gm.K_AUTO_CLASS) and new.get("vao"):
            classify_async(self.ctx, parent, [m for m in new["vao"]])


def classify_async(ctx, parent, invoice_mas: list[str] | None = None, on_done=None, quiet: bool = True) -> bool:
    """Nhờ Gemini phân loại các dòng hóa đơn đầu vào chưa rà soát (của các hóa đơn chỉ định, hoặc tất cả)."""
    svc = GeminiService(ctx.db)
    if not svc.configured():
        if not quiet:
            info(parent, "Chưa cài khóa API Gemini. Vào Cài đặt › Thư mục hóa đơn và Gemini để nhập khóa.")
        return False
    lines = [dict(x) for x in svc.unreviewed_lines()]
    if invoice_mas is not None:
        lines = [x for x in lines if x["MaHoaDon"] in set(invoice_mas)]
    if not lines:
        if not quiet:
            info(parent, "Không còn dòng hóa đơn nào chờ Gemini rà soát.")
        return False
    gem = svc.client()
    ask = [{"id": ln["Ma"], "ten": ln["TenHang"], "so_luong": ln["SoLuong"], "don_gia": _unit(ln),
            "thanh_tien": ln["ThanhTien"], "nguoi_ban": ln.get("TenNhaCungCap") or ""} for ln in lines]

    def work():
        out = []
        for i in range(0, len(ask), 40):
            out += gem.classify_lines(ask[i:i + 40])
        return out

    def done(res, err):
        if err is not None:
            if isinstance(err, GeminiError):
                svc.record_error(err)
            if not quiet:
                error(parent, f"Gemini chưa rà soát được: {err}")
            if on_done:
                on_done(None)
            return
        cur = {ln["Ma"]: dict(r) for ln in lines if (r := ctx.db.get("DongHoaDonDauVao", ln["Ma"])) is not None}
        cur_lines = [dict(cur[m], TenNhaCungCap=ln.get("TenNhaCungCap")) for ln in lines if (m := ln["Ma"]) in cur]

        class _Fixed:
            def classify_lines(self, _ask):
                return res
        r = svc.classify(cur_lines, _Fixed(), batch=max(len(cur_lines), 1))
        win = parent.window() if parent is not None else None
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(f"Gemini đã rà soát phân loại: đổi {r['doi']}, đồng ý {r['dong_y']}, "
                                        f"không chắc {r['khong_chac']}, gợi ý khác {r['khac']}.", 15_000)
        if hasattr(win, "update_badges"):
            win.update_badges()
        if on_done:
            on_done(r)
    run_bg(work, done)
    return True


def _unit(ln: dict) -> int:
    if ln.get("DonGia"):
        return int(ln["DonGia"])
    return int(ln["ThanhTien"] / ln["SoLuong"]) if ln.get("SoLuong") else int(ln.get("ThanhTien") or 0)


# ------------------------------------------------------------------ cửa sổ Thư mục hóa đơn
class InvoiceFolderDialog(QDialog):
    """Vừa tự thêm / Cần xem / Tất cả file trong thư mục hóa đơn."""
    TABS = (("Vừa tự thêm", (fo.F_ADDED,)), ("Cần xem", fo.F_OPEN), ("Tất cả file trong thư mục", None))

    def __init__(self, ctx, parent=None, tab: int = 1, scanner: Scanner | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.folders = InvoiceFolders(ctx.db)
        self.scanner = scanner
        self.setWindowTitle("Thư mục hóa đơn")
        self.resize(1200, 640)
        lay = QVBoxLayout(self)
        self.head = hint("")
        lay.addWidget(self.head)
        self.tabs = QTabWidget()
        self.tables = []
        for title, _ in self.TABS:
            t = DataTable([("Chieu", "Loại", "text"), ("Ten", "Tên file", "text"), ("ThuMuc", "Thư mục", "text"),
                           ("TT", "Trạng thái", "text"), ("HoaDon", "Hóa đơn", "text"),
                           ("SauThue", "Sau thuế", "money"), ("Gemini", "Gemini", "text"),
                           ("GhiChu", "Lý do / ghi chú", "text")], multi=True)
            t.setContextMenuPolicy(Qt.CustomContextMenu)
            t.customContextMenuRequested.connect(lambda _p, t=t: self._menu(t))
            t.activated_row.connect(lambda r: self.open_file(r))
            self.tables.append(t)
            self.tabs.addTab(t, title)
        lay.addWidget(self.tabs, 1)
        self.b_add = button("Thêm vào phần mềm…", self.add, "Primary")
        self.b_skip = button("Bỏ qua (không nhập)", self.skip)
        self.b_apply = button("Cập nhật theo file…", self.apply_file)
        self.b_apply.setToolTip("Hóa đơn đã có nhưng số liệu trong file khác: xem cũ → mới rồi cập nhật theo file")
        self.b_read = button("Đọc bằng Gemini", self.gemini)
        self.b_read.setToolTip("File chưa có trong phần mềm (PDF, ảnh): Gemini đọc số liệu. File đã có hóa đơn: "
                               "Gemini kiểm tra lại.")
        self.b_check = button("Kiểm tra bằng Gemini", self.gemini)
        self.b_check.setToolTip(self.b_read.toolTip())
        self.b_scan = button("Quét ngay", self.scan)
        self.b_rescan = button("Quét lại toàn bộ", self.rescan)
        self.b_rescan.setToolTip("Xét lại mọi file trong thư mục: hóa đơn đã có chỉ ghi nhận lại nơi chứa tệp, "
                                 "không đổi số liệu; file đã bỏ qua giữ nguyên")
        b_open = button("Mở tệp", lambda: self.open_file(self._table().current()))
        b_folder = button("Mở thư mục chứa", self.open_folder)
        b_copy = button("Sao chép tên file", self.copy_names)
        b_set = button("Cài đặt…", self.settings)
        lay.addLayout(toolbar(self.b_add, self.b_skip, self.b_apply, self.b_read, self.b_check, b_open, b_folder,
                              b_copy, "stretch", self.b_scan, self.b_rescan, b_set))
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Đóng")
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.tabs.currentChanged.connect(lambda _: self._buttons())
        self.tabs.setCurrentIndex(tab)
        self.reload()

    def _table(self) -> DataTable:
        return self.tables[self.tabs.currentIndex()]

    def reload(self):
        all_rows = self.folders.files()
        gem = GeminiService(self.ctx.db)
        for (title, status), t in zip(self.TABS, self.tables):
            rows = []
            for r in all_rows:
                if status and r["TrangThai"] not in status:
                    continue
                inv = r["HoaDon"]
                d = (r["Data"] or {}).get("values") or {}
                so = invoice_no(r["Chieu"], inv) if inv else (
                    invoice_no(r["Chieu"], d) if d else "")
                rel = r["DuongDan"]
                parent = str(Path(rel).parent)
                rows.append({**r, "Ten": Path(rel).name, "ThuMuc": "" if parent == "." else parent,
                             "Chieu": KIND_LABEL[r["Chieu"]], "_kind": r["Chieu"], "TT": fo.F_LABELS.get(r["TrangThai"],
                                                                                                     r["TrangThai"]),
                             "HoaDon": so, "SauThue": (inv or d).get("GiaTriSauThue") if (inv or d) else None,
                             "Gemini": display_fields(self.ctx, r["Chieu"], inv)["GeminiHT"] if inv else "",
                             "_inv": inv, "_mau": "warn" if r["TrangThai"] in fo.F_OPEN else None})
            t.set_rows(rows)
            self.tabs.setTabText(self.tables.index(t), f"{title} ({len(rows)})")
        s = self.folders.summary()
        st = self.folders.settings()
        dirs = " · ".join(f"{KIND_LABEL[k]}: {st[k] or 'chưa đặt'}" for k in fo.KINDS)
        last = local_time(self.ctx.db.setting(fo.K_LAST))
        self.head.setText(f"{dirs}" + (f" · Quét lần cuối {last}" if last else "")
                          + (f" · Gemini: {gem.state()[1].split(': ', 1)[-1]}" if gem.configured() else ""))
        if s.get("loi"):
            self.head.setText(self.head.text() + f" · Lỗi: {s['loi']}")
        self._buttons()

    def _buttons(self):
        review = self.TABS[self.tabs.currentIndex()][1] == fo.F_OPEN
        add_ok = self.ctx.can("HoaDonDauRa", "Them") or self.ctx.can("HoaDonDauVao", "Them")
        self.b_add.setEnabled(review and add_ok)
        self.b_skip.setEnabled(review and add_ok)
        self.b_apply.setEnabled(review and (self.ctx.can("HoaDonDauRa", "Sua") or self.ctx.can("HoaDonDauVao", "Sua")))

    def _sel(self) -> list[dict]:
        return self._table().selected()

    def _menu(self, t: DataTable):
        rows = t.selected()
        if not rows:
            return
        m = QMenu(self)
        m.addAction("Mở tệp", lambda: self.open_file(rows[0]))
        m.addAction("Mở thư mục chứa", self.open_folder)
        m.addAction("Sao chép tên file", self.copy_names)
        m.addAction("Sao chép đường dẫn", lambda: copy_text("\n".join(
            str(self.folders.full_path(r["_kind"], r["DuongDan"])) for r in rows)))
        m.addSeparator()
        if self.b_add.isEnabled():
            m.addAction("Thêm vào phần mềm…", self.add)
            m.addAction("Bỏ qua (không nhập)", self.skip)
            m.addAction("Cập nhật theo file…", self.apply_file)
        m.addAction("Đọc / kiểm tra bằng Gemini", self.gemini)
        m.exec(self.cursor().pos())

    def open_file(self, r):
        if not r:
            return
        p = self.folders.full_path(r["_kind"], r["DuongDan"])
        if p is None or not p.exists():
            return error(self, "Không còn tìm thấy tệp này trong thư mục hóa đơn.")
        open_path(p)

    def open_folder(self):
        r = self._table().current()
        if r:
            p = self.folders.full_path(r["_kind"], r["DuongDan"])
            if p is not None:
                reveal(p)

    def copy_names(self):
        rows = self._sel()
        if rows:
            copy_text("\n".join(r["Ten"] for r in rows))

    def settings(self):
        dlg = FolderSettingsDialog(self.ctx, self)
        if dlg.exec() == QDialog.Accepted:
            self.reload()
            if getattr(dlg, "changed_dirs", False):
                self.scan()

    def scan(self, everything: bool = False):
        sc = self.scanner or Scanner(self.ctx, self)
        self.scanner = sc
        if getattr(self, "_hooked", None) is not sc:
            sc.finished.connect(self._scanned)
            sc.progress.connect(self._progress)
            self._hooked = sc
        self._waiting = True
        if sc.start(everything):
            self.b_scan.setEnabled(False)
            self.b_rescan.setEnabled(False)
            self.b_scan.setText("Đang quét…")
        else:
            self._waiting = False
            self.reload()
            if sc.busy:
                info(self, "Phần mềm đang quét thư mục hóa đơn, chờ xong rồi bấm lại.")
            elif not self.folders.configured():
                info(self, "Chưa đặt thư mục hóa đơn. Bấm Cài đặt… để chọn thư mục.")
            else:
                self.head.setText(self.head.text() + " · Không có file mới.")

    def rescan(self):
        if confirm(self, "Quét lại toàn bộ file trong thư mục hóa đơn?\n\n- Hóa đơn đã có trong phần mềm: chỉ ghi nhận "
                         "lại nơi chứa tệp, không đổi số liệu (số liệu khác thì đưa vào Cần xem).\n- File PDF / ảnh "
                         "chưa có trong phần mềm sẽ gửi Gemini đọc (nếu đã cài khóa).\n- File đã bỏ qua giữ nguyên."):
            self.scan(True)

    def _progress(self, text: str):
        if getattr(self, "_waiting", False):
            self.b_scan.setText(text if len(text) < 60 else text[:57] + "…")

    def _scanned(self, s: dict):
        if not getattr(self, "_waiting", False):
            return
        self._waiting = False
        self.b_scan.setEnabled(True)
        self.b_rescan.setEnabled(True)
        self.b_scan.setText("Quét ngay")
        self.reload()
        if s:
            self.head.setText(self.head.text() + " · " + scan_text(s))

    def skip(self):
        rows = [r for r in self._sel() if r["TrangThai"] == fo.F_REVIEW]
        keep = [r for r in self._sel() if r["TrangThai"] == fo.F_CONFLICT]
        if not rows and not keep:
            return info(self, "Chọn file ở thẻ Cần xem.")
        text = []
        if rows:
            text.append(f"Bỏ qua {len(rows)} file (không nhập vào phần mềm)? Lần quét sau phần mềm không hỏi lại các "
                        "file này; vẫn xem được ở thẻ Tất cả file.")
        if keep:
            text.append(f"{len(keep)} file có số liệu khác hóa đơn đã có: giữ nguyên số liệu trong phần mềm.")
        if not confirm(self, "\n\n".join(text)):
            return
        for r in rows:
            self.folders.set_status(r["_kind"], [r["DuongDan"]], fo.F_SKIP, note="Nhân viên bỏ qua")
        for r in keep:
            self.folders.set_status(r["_kind"], [r["DuongDan"]], fo.F_EXISTS, note="Giữ số liệu trong phần mềm")
        self.reload()

    def apply_file(self):
        rows = [r for r in self._sel() if r["TrangThai"] == fo.F_CONFLICT and r["_inv"]]
        if not rows:
            return info(self, "Chọn file trạng thái \"Đã có, số liệu khác\" ở thẻ Cần xem.")
        done = 0
        for r in rows:
            kind = r["_kind"]
            if not self.ctx.can(TABLE[kind], "Sua"):
                continue
            diff = self.folders.diff(kind, r["_inv"], fo.load_values(r["Data"]))
            if not diff:
                self.folders.set_status(kind, [r["DuongDan"]], fo.F_EXISTS, note="Số liệu đã khớp")
                continue
            if confirm_changes(self, [(label, a, b) for _c, label, a, b in diff],
                               f"Cập nhật hóa đơn {invoice_no(kind, r['_inv'])} theo file {r['Ten']}"):
                self.folders.apply_file_values(kind, r["DuongDan"])
                done += 1
        self.reload()
        if done:
            info(self, f"Đã cập nhật số liệu {done} hóa đơn theo file.")

    def gemini(self):
        """File chưa có trong phần mềm (PDF, ảnh): Gemini đọc số liệu rồi xét như lúc quét. File đã gắn với hóa đơn
        trong phần mềm: Gemini kiểm tra hóa đơn."""
        rows = self._sel()
        if not rows:
            return info(self, "Chọn file (giữ Ctrl / Shift để chọn nhiều) để Gemini đọc hoặc kiểm tra.")
        svc = GeminiService(self.ctx.db)
        if not svc.configured():
            return info(self, "Chưa cài khóa API Gemini. Bấm Cài đặt… để nhập khóa.")
        check: dict[str, list] = {}
        read, xml = [], 0
        for r in rows:
            if r["_inv"]:
                check.setdefault(r["_kind"], []).append(r["_inv"])
            elif r["DuongDan"].lower().endswith(".xml"):
                xml += 1
            elif r["TrangThai"] != fo.F_COMPANION:
                read.append(r)
        if not check and not read:
            return info(self, "File XML đã đọc đủ số liệu (không cần Gemini đọc). Thêm vào phần mềm rồi bấm Kiểm tra "
                              "bằng Gemini nếu muốn Gemini soát.")
        for kind, invs in check.items():
            check_invoices(self.ctx, kind, invs, self, self.reload, show=len(rows) == 1)
        if read:
            self._read(svc, read)

    def _read(self, svc, rows):
        gem = svc.client()
        jobs: dict[str, list] = {}
        for r in rows:
            p = self.folders.full_path(r["_kind"], r["DuongDan"])
            if p is not None and p.exists():
                jobs.setdefault(r["_kind"], []).append((r["DuongDan"], str(p)))
        if not jobs:
            return error(self, "Không còn tìm thấy các file này trong thư mục hóa đơn.")
        caches = {k: self.folders.cache(k, [rel for rel, _ in v]) for k, v in jobs.items()}
        n = sum(len(v) for v in jobs.values())
        self.b_read.setEnabled(False)
        self.b_check.setEnabled(False)
        self.b_read.setText(f"Gemini đang đọc {n} file…")

        def work():
            return {kind: InvoiceFolders.read(kind, files, gem, cache=caches[kind], force_gemini=True)
                    for kind, files in jobs.items()}

        def done(res, err):
            self.b_read.setEnabled(True)
            self.b_check.setEnabled(True)
            self.b_read.setText("Đọc bằng Gemini")
            if err is not None:
                return error(self, f"Gemini chưa đọc được: {err}")
            mst = self.ctx.acc.license()["MaCongTy"] if self.ctx.acc.license() else ""
            total = {"them": 0, "can_xem": 0, "co_san": 0, "lech": 0, "cho_gemini": 0}
            offline = ""
            for kind, items in res.items():
                for it in items:
                    if it.offline:
                        offline = it.error
                r = self.folders.process(kind, items, mst_cty=mst or "")
                total["them"] += len(r["them"])
                for k in ("can_xem", "co_san", "lech", "cho_gemini"):
                    total[k] += r[k]
                if r["them"] and svc.flag(gm.K_AUTO_CLASS) and kind == "vao":
                    classify_async(self.ctx, self, r["them"])
            if offline:
                svc.record_error(GeminiError(offline.replace("Gemini chưa đọc được: ", ""), "mang"))
            else:
                svc.record_ok()
            self.reload()
            msg = "Gemini đã đọc xong: " + scan_text(total)
            (error if offline else info)(self, msg + (f"\n\n{offline}" if offline else ""))
        run_bg(work, done)

    def add(self):
        from .invoices_page import CHOOSE, BatchInvoiceDialog, InvoicesPage, mark_duplicates
        rows = [r for r in self._sel() if r["TrangThai"] == fo.F_REVIEW]
        if not rows:
            return info(self, "Chọn file ở thẻ Cần xem.")
        kinds = {r["_kind"] for r in rows}
        if len(kinds) > 1:
            return info(self, "Chọn file cùng loại (cùng đầu ra hoặc cùng đầu vào) để thêm một lượt.")
        kind = kinds.pop()
        if not self.ctx.can(TABLE[kind], "Them"):
            return error(self, "Tài khoản này không có quyền thêm hóa đơn loại này.")
        items = []
        for r in rows:
            v = fo.load_values(r["Data"])
            if not v:
                items.append({"path": None, "file": r["Ten"], "values": {}, "status": "loi",
                              "notes": [r["GhiChu"] or "Chưa đọc được số liệu: nhập tay hoặc Đọc bằng Gemini"],
                              "goi_y": [], "_rel": r["DuongDan"]})
                continue
            notes = list((r["Data"] or {}).get("notes") or [])
            goi_y = []
            if not v.get("MaHopDong"):
                m = invoice_match.match_contract(self.ctx.db, kind, {
                    "MSTNguoiMua": v.get("MSTNguoiMua"), "MSTNguoiBan": v.get("MaThueNCC"), "VanBan": v.get("MatHang")})
                goi_y = m.goi_y
                v["MaHopDong"] = m.ma or (CHOOSE if kind == "ra" else None)
            if r["GhiChu"]:
                notes.insert(0, r["GhiChu"])
            errs = [f"Thiếu {label}" for key, label in fo.REQUIRED[kind] if not v.get(key)]
            items.append({"path": None, "file": r["Ten"], "values": v, "status": "moi", "notes": notes + errs,
                          "errors": errs, "goi_y": goi_y, "_rel": r["DuongDan"]})
        mark_duplicates(self.ctx, kind, items)
        page = InvoicesPage(self.ctx, kind, self)
        page.hide()
        dlg = BatchInvoiceDialog(page, items=items, source="thư mục hóa đơn")
        dlg.exec()
        for it in items:
            if not it["values"]:
                continue
            inv = self.folders._existing(kind, it["values"])
            if inv is not None:
                self.folders.set_status(kind, [it["_rel"]], fo.F_ADDED, inv["Ma"], note="Nhân viên thêm")
                if not inv.get("TepTuongDoi"):
                    with self.ctx.db.auto():
                        self.ctx.db.update(TABLE[kind], inv["Ma"], {"TepTuongDoi": it["_rel"]})
        page.deleteLater()
        self.reload()
        win = self.parent().window() if self.parent() else None
        if hasattr(win, "refresh_all"):
            win.refresh_all()


__all__ = ["scan_text", "ExportPdfDialog", "FileActions", "FolderSettingsDialog", "GeminiResultDialog", "InvoiceFolderDialog",
           "Scanner", "check_invoices", "classify_async", "display_fields", "reveal", "run_bg"]
