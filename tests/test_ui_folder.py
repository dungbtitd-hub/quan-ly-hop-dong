"""Giao diện: thư mục hóa đơn, tệp gốc, xuất PDF, Gemini, đồng bộ tự động (offscreen, không gọi mạng)."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qlhd.core import licensing as lic  # noqa: E402
from tests.helpers import sample_data  # noqa: E402
from tests.test_invoice_folder import _xml  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def ctx(tmp_path, vendor):
    from PySide6.QtWidgets import QApplication

    from qlhd.context import AppContext
    from qlhd.ui.style import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "sang")
    priv, pub = vendor
    c = AppContext(tmp_path / "data", vendor_pub=pub)
    key = lic.issue_director_key(priv, c.acc.activation_request(), "Công ty ABC", "0101234567", 5)
    c.acc.activate_director(key, "giamdoc", "Nguyễn Văn A", "matkhau123")
    c.user = c.acc.login("giamdoc", "matkhau123")
    sample_data(c.db)
    yield c
    c.close()


def _quiet(monkeypatch, *modules):
    shown = []
    for m in modules:
        for name in ("info", "error"):
            if hasattr(m, name):
                monkeypatch.setattr(m, name, lambda _p, text, *a, **k: shown.append(text))
        if hasattr(m, "confirm"):
            monkeypatch.setattr(m, "confirm", lambda *a, **k: True)
        if hasattr(m, "confirm_changes"):
            monkeypatch.setattr(m, "confirm_changes", lambda *a, **k: True)
        if hasattr(m, "reveal"):
            monkeypatch.setattr(m, "reveal", lambda *a: None)
    return shown


def test_scan_folder_list_columns_and_export_pdf(qtbot, ctx, tmp_path, monkeypatch):
    from pypdf import PdfReader

    from qlhd.core.invoice_folder import F_REVIEW, InvoiceFolders
    from qlhd.ui import invoice_files as ifl
    from qlhd.ui.invoice_files import ExportPdfDialog, InvoiceFolderDialog, Scanner
    from qlhd.ui.invoices_page import InvoicesPage

    shown = _quiet(monkeypatch, ifl)
    root = tmp_path / "vao"
    _xml(root / "2026" / "hd_cu.xml", 501)
    f = InvoiceFolders(ctx.db)
    f.save_settings({"vao": str(root)})
    sc = Scanner(ctx)
    with qtbot.waitSignal(sc.finished, timeout=10_000) as sig:
        assert sc.start()
    assert sig.args[0]["lan_dau"] and sig.args[0]["can_xem"] == 1
    _xml(root / "2026" / "Quy 4" / "hd_moi.xml", 502)
    from PySide6.QtGui import QColor, QImage
    img = QImage(200, 300, QImage.Format_RGB32)
    img.fill(QColor("white"))
    img.save(str(root / "2026" / "Quy 4" / "hd_moi.png"))
    with qtbot.waitSignal(sc.finished, timeout=10_000) as sig:
        assert sc.start()
    assert sig.args[0]["them"] == 1
    inv = ctx.db.one("SELECT * FROM HoaDonDauVao WHERE SoHoaDonGoc = '502'")
    assert inv["TepTuongDoi"] == "2026/Quy 4/hd_moi.xml" and json.loads(inv["TepKem"]) == ["2026/Quy 4/hd_moi.png"]

    page = InvoicesPage(ctx, "vao")
    qtbot.addWidget(page)
    page.refresh()
    row = next(r for r in page.table.rows if r["SoHoaDonGoc"] == "502")
    assert row["TenFileGoc"] == "hd_moi.xml" and row["ThuMuc"] == "2026\\Quy 4"
    page.box.edit.setText("hd_moi")                                     # tìm theo tên file
    page.refresh()
    assert [r["SoHoaDonGoc"] for r in page.table.rows] == ["502"]
    menu = page.files.menu([row])
    assert "Xuất PDF ra thư mục khác…" in [a.text() for a in menu.actions()]

    out = tmp_path / "xuat"
    out.mkdir()
    dlg = ExportPdfDialog(ctx, "vao", [row, dict(ctx.db.one("SELECT * FROM HoaDonDauVao WHERE SoHoaDonGoc = '7'"))])
    qtbot.addWidget(dlg)
    assert len(dlg.files) == 2 and dlg.files[1][1] is None   # chưa gắn tệp gốc: dựng từ số liệu đã lưu
    assert dlg.export(out, False, ask=False) == 2
    assert len(PdfReader(str(out / "hd_moi.pdf")).pages) == 1          # ảnh đi kèm → PDF
    assert len(PdfReader(str(out / "HoaDon__7.pdf")).pages) >= 1
    dlg.files.append((row, root / "2026" / "Quy 4" / "hd_moi.xml"))    # XML → bản thể hiện, gộp một tệp
    assert dlg.export(out, True, "gop.pdf", ask=False) == 1
    assert len(PdfReader(str(out / "gop.pdf")).pages) >= 2

    d = InvoiceFolderDialog(ctx, None, tab=1)
    qtbot.addWidget(d)
    review = d.tables[1].rows
    assert [r["Ten"] for r in review] == ["hd_cu.xml"] and review[0]["TrangThai"] == F_REVIEW
    assert [r["Ten"] for r in d.tables[0].rows] == ["hd_moi.xml"]
    d.tables[1].selectRow(0)
    d.skip()
    assert f.review_count() == 0 and not shown


def test_gemini_result_dialog_and_fix(qtbot, ctx, monkeypatch):
    from qlhd.core import gemini as gm
    from qlhd.core.gemini import GeminiService
    from qlhd.ui import invoice_files as ifl
    from qlhd.ui.invoice_files import GeminiResultDialog, display_fields

    _quiet(monkeypatch, ifl)
    inv = ctx.db.one("SELECT * FROM HoaDonDauVao WHERE SoHoaDonGoc = '88'")
    svc = GeminiService(ctx.db)
    st = svc.save_check("vao", inv["Ma"], {"so_hoa_don": "89", "ngay_lap": "2026-02-10", "mst_nguoi_ban": "0200000002",
                                           "tong_thanh_toan": 11_000_000, "hop_le": True, "luu_y": ["Thiếu mã CQT"]})
    assert st == gm.ST_ISSUE
    assert display_fields(ctx, "vao", ctx.db.get("HoaDonDauVao", inv["Ma"]))["_mau"] == "danger"
    dlg = GeminiResultDialog(ctx, "vao", inv["Ma"])
    qtbot.addWidget(dlg)
    assert any(r["ket_qua"] == "Khác" and r["truong"] == "Số hóa đơn" for r in dlg.table.rows)
    assert "Thiếu mã CQT" in dlg.notes.text()
    dlg.fix()                                                           # cũ → mới đã xác nhận
    new = ctx.db.get("HoaDonDauVao", inv["Ma"])
    assert new["SoHoaDonGoc"] == "89" and new["GeminiTT"] == gm.ST_OK and new["SuaTay"]


def test_folder_settings_and_assets_gemini_banner(qtbot, ctx, tmp_path, monkeypatch):
    from qlhd.core.gemini import GeminiService
    from qlhd.core.invoice_folder import InvoiceFolders
    from qlhd.ui.assets_page import AssetsPage
    from qlhd.ui.invoice_files import FolderSettingsDialog

    (tmp_path / "ra").mkdir()
    dlg = FolderSettingsDialog(ctx)
    qtbot.addWidget(dlg)
    dlg.dirs["ra"].setText(str(tmp_path / "ra"))
    dlg.key.setText("AIza-khoa-thu")
    dlg.auto_class.setChecked(False)
    dlg.save()
    assert InvoiceFolders(ctx.db).settings()["ra"] == str(tmp_path / "ra") and dlg.changed_dirs
    g = GeminiService(ctx.db)
    assert g.key() == "AIza-khoa-thu" and not g.flag("gemini_tu_phan_loai")
    page = AssetsPage(ctx)
    qtbot.addWidget(page)
    page.tabs.setCurrentIndex(1)
    page.refresh()
    assert "chưa được Gemini rà soát" in page.gem_info.text()


def test_auto_sync_tab_and_sync_now(qtbot, ctx, tmp_path, monkeypatch):
    from qlhd.core.sync_folder import SyncFolder
    from qlhd.ui import main_window, sync_page, widgets
    from qlhd.ui.main_window import MainWindow

    shown = _quiet(monkeypatch, main_window, sync_page, widgets)
    shared = tmp_path / "chung"
    shared.mkdir()
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.go_to("Đồng bộ")
    sp = win.sync_page
    assert not win.sync_btn.isVisibleTo(win)
    sp.a_dir.setText(str(shared))
    sp.a_pw1.setText("dongbo-2026")
    sp.a_pw2.setText("dongbo-2026")
    sp.a_clean.setValue(5)
    sp.save_auto()
    auto = SyncFolder(ctx.db, ctx.sync)
    assert auto.ready() and auto.settings()["don_ngay"] == 5 and win.send_timer.isActive()
    assert win.sync_btn.isVisibleTo(win)
    ctx.db.update("HopDong", ctx.cs.contracts()[0]["Ma"], {"GhiChu": "sửa để gửi"})
    win._paint_status_buttons()
    assert "(" in win.sync_btn.text()
    win.sync_now()
    assert list(shared.glob("DongBo_*.qlhd")) and "Đã gửi" in shown[-1]
    sp.refresh()
    assert sp.auto_list.rows and sp.auto_list.rows[0]["TT"].startswith("Máy này gửi")
    win.close()


def test_scan_button_on_invoice_page_and_gemini_read_in_folder_dialog(qtbot, ctx, tmp_path, monkeypatch):
    from qlhd.core import gemini as gm
    from qlhd.core.invoice_folder import InvoiceFolders
    from qlhd.ui import invoice_files as ifl
    from qlhd.ui import invoices_page as ip
    from qlhd.ui.invoice_files import InvoiceFolderDialog
    from qlhd.ui.invoices_page import InvoicesPage
    from tests.test_invoice_folder import FakeGemini

    shown = _quiet(monkeypatch, ifl, ip)
    root = tmp_path / "vao"
    root.mkdir()
    f = InvoiceFolders(ctx.db)
    f.save_settings({"vao": str(root)})
    page = InvoicesPage(ctx, "vao")
    qtbot.addWidget(page)
    opened = []
    monkeypatch.setattr(page, "open_folder_dialog", lambda: opened.append(1))
    assert page.b_scan.isEnabled()
    _xml(root / "hd88.xml", 88, mst="0200000002", ban="Công ty Vật tư X", kh="K26T")   # đã có trong phần mềm
    page.scan_now()
    qtbot.waitUntil(lambda: page.b_scan.isEnabled(), timeout=10_000)
    assert f.files("vao")[0]["TrangThai"] == "lech" and opened      # đã có, số tiền khác → hỏi mở Cần xem
    # ảnh chưa đọc được (chưa cài Gemini lúc quét) → bấm Đọc bằng Gemini
    (root / "anh.jpg").write_bytes(b"\xff\xd8\xff")
    page.scan_now()
    qtbot.waitUntil(lambda: page.b_scan.isEnabled(), timeout=10_000)
    gem = FakeGemini(extract={"la_hoa_don": True, "ky_hieu": "C26TQQ", "so_hoa_don": "55", "ngay_lap": "2026-11-12",
                              "ten_nguoi_ban": "Quán C", "mst_nguoi_ban": "0700000007", "tien_truoc_thue": 100_000,
                              "tien_thue": 8_000, "tong_thanh_toan": 108_000, "thue_suat": "8%"})
    monkeypatch.setattr(gm.GeminiService, "configured", lambda self: True)
    monkeypatch.setattr(gm.GeminiService, "client", lambda self: gem)
    d = InvoiceFolderDialog(ctx, None, tab=1)
    qtbot.addWidget(d)
    names = [r["Ten"] for r in d.tables[1].rows]
    d.tables[1].selectRow(names.index("anh.jpg"))
    d.gemini()
    qtbot.waitUntil(lambda: d.b_read.isEnabled(), timeout=10_000)
    assert [c[0] for c in gem.calls][:1] == ["doc"] and any("Gemini đã đọc xong" in x for x in shown)
    assert ctx.db.one("SELECT * FROM HoaDonDauVao WHERE SoHoaDonGoc = '55'") is not None
