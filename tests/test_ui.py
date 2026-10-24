"""Kiểm thử giao diện ở chế độ không màn hình (QT_QPA_PLATFORM=offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qlhd.core import licensing as lic  # noqa: E402
from qlhd.core.finance import FinanceStore  # noqa: E402
from qlhd.core.reports import CONTEXTS  # noqa: E402
from tests.helpers import sample_data  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def ctx(tmp_path, vendor):
    from qlhd.context import AppContext
    from qlhd.ui.style import apply_theme
    from PySide6.QtWidgets import QApplication

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


def test_splash_and_about_show_developer(qtbot, ctx):
    from PySide6.QtWidgets import QLabel

    from qlhd.ui.splash import AboutDialog, SplashDialog

    s = SplashDialog(ctx.acc, seconds=0)
    qtbot.addWidget(s)
    texts = " ".join(lbl.text() for lbl in s.findChildren(QLabel))
    for needle in ("Công ty cổ phần Công nghệ ITD Việt Nam", "0988095867", "vanthu.itdvn@gmail.com",
                   "itdvietnam.com.vn", "Số 1 Ngõ 30 đường Lý Sơn, Hà Nội", "Đã kích hoạt", "© 2026"):
        assert needle in texts
    a = AboutDialog(ctx.acc, ctx.machine_id)
    qtbot.addWidget(a)
    assert ctx.machine_id in " ".join(lbl.text() for lbl in a.findChildren(QLabel))


def test_main_window_pages(qtbot, ctx, tmp_path):
    from PySide6.QtWidgets import QLabel

    from qlhd.ui.main_window import MainWindow

    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    for i in range(win.nav.count()):
        win.nav.setCurrentRow(i)
    titles = [win.nav.item(i).text() for i in range(win.nav.count())]
    assert "Tài khoản" in titles and any(t.startswith("Thu chi") for t in titles)
    # mở thu chi
    ctx.finance, _ = FinanceStore.initialize(ctx.db, ctx.finance_path, ctx.user, "khoa-thu-chi-1", iterations=1000)
    win.finance_page.refresh()
    assert win.finance_page.main_view is not None
    win.finance_page._r_preview()
    from qlhd.core import finance_import
    from qlhd.ui.finance_page import FinanceImportDialog

    tpl = finance_import.write_template(tmp_path / "mau_tc.xlsx", ctx.finance)
    dlg = FinanceImportDialog(finance_import.read_workbook(tpl, ctx.finance))
    qtbot.addWidget(dlg)
    assert "3 dòng hợp lệ" in dlg.findChildren(QLabel)[0].text()
    win.finance_page.lock()
    assert ctx.finance is None
    # báo cáo
    rp = next(p for p in win.pages if type(p).__name__ == "ReportsPage")
    for key, _ in CONTEXTS:
        rp.context.set_value(key)
        rp.preview()
        assert rp.tabs.count() >= 1 and rp.tabs.widget(0).rowCount() > 0, key
    win.close()


def test_accountant_has_no_finance_menu(qtbot, tmp_path, vendor, ctx):
    from qlhd.context import AppContext
    from qlhd.ui.main_window import MainWindow

    priv, pub = vendor
    kt = AppContext(tmp_path / "kt", vendor_pub=pub)
    code = ctx.acc.issue_code(ten_dang_nhap="ketoan", ho_ten="Kế toán", vai_tro="Kế toán viên", machine_id=kt.machine_id)
    kt.acc.activate_member(code, "matkhau123")
    kt.user = kt.acc.login("ketoan", "matkhau123")
    win = MainWindow(kt)
    qtbot.addWidget(win)
    titles = [win.nav.item(i).text() for i in range(win.nav.count())]
    assert not any(t.startswith("Thu chi") for t in titles) and "Tài khoản" not in titles
    win.close()
    kt.close()


def test_invoice_unmatched_requires_choice(qtbot, ctx):
    from qlhd.core.contracts import ValidationError
    from qlhd.ui.invoices_page import CHOOSE, InvoicesPage, _check_chosen
    from qlhd.ui.widgets import FormDialog

    page = InvoicesPage(ctx, "vao")
    qtbot.addWidget(page)
    hd = ctx.cs.contracts("Đầu vào")[0]
    fields = page._fields("Chưa xác định được hợp đồng, hãy chọn.", True, [hd])
    dlg = FormDialog("Hóa đơn đầu vào", fields, {"MaHopDong": CHOOSE}, validate=_check_chosen)
    qtbot.addWidget(dlg)
    combo = dlg.widgets["MaHopDong"]
    assert combo.value() == CHOOSE and combo.itemText(1).startswith("★ Gợi ý: 05/2026/MB")
    with pytest.raises(ValidationError):
        _check_chosen(dlg.values())
    combo.set_value(hd["Ma"])
    _check_chosen(dlg.values())


def test_renew_dialog_and_splash_term(qtbot, tmp_path, vendor):
    from datetime import date, timedelta

    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    from qlhd.context import AppContext
    from qlhd.ui.auth_dialogs import RenewDialog
    from qlhd.ui.splash import license_text

    priv, pub = vendor
    c = AppContext(tmp_path / "d2", vendor_pub=pub)
    han = (date.today() + timedelta(days=12)).isoformat()
    c.acc.activate_director(lic.issue_director_key(priv, c.acc.activation_request(), "Cty X", "0101", 3, han),
                            "giamdoc", "GD", "matkhau123")
    assert "còn 12 ngày" in license_text(c.acc)
    dlg = RenewDialog(c)
    qtbot.addWidget(dlg)
    assert "còn 12 ngày" in " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    req = dlg.findChildren(QPlainTextEdit)[0].toPlainText()
    new_han = (date.today() + timedelta(days=400)).isoformat()
    dlg.key.setPlainText(lic.issue_director_key(priv, req, "Cty X", "0101", 3, new_han))
    c.acc.renew(dlg.key.toPlainText())
    assert c.acc.license_status()["han"] == new_han
    c.close()


def test_wipe_all_keep_license(tmp_path, vendor):
    from qlhd.context import AppContext
    from qlhd.core.auth import AuthError

    priv, pub = vendor
    c = AppContext(tmp_path / "gd", vendor_pub=pub)
    key = lic.issue_director_key(priv, c.acc.activation_request(), "Công ty ABC", "0101234567", 5)
    c.acc.activate_director(key, "giamdoc", "Nguyễn Văn A", "matkhau123")
    c.user = c.acc.login("giamdoc", "matkhau123")
    sample_data(c.db)
    FinanceStore.initialize(c.db, c.finance_path, c.user, "khoa-thu-chi-1", iterations=1000)[0].lock()
    c.backup_now("thu-cong")
    secret = (c.data_dir / "QuanLyHopDong.db").read_bytes()
    with pytest.raises(AuthError):
        c.wipe_all("sai-mat-khau", keep_license=True)
    n = c.wipe_all("matkhau123", keep_license=True)
    assert n >= 4 and not c.finance_path.exists() and not (c.data_dir / "SaoLuu").exists()
    assert c.db.one("SELECT COUNT(*) n FROM HopDong")["n"] == 0
    assert c.db.one("SELECT COUNT(*) n FROM NhatKyHeThong")["n"] == 0
    assert c.acc.is_activated() and c.acc.login("giamdoc", "matkhau123")
    assert b"Thi\xe1\xba\xbft k\xe1\xba\xbf tr\xc6\xb0\xe1\xbb\x9dng h\xe1\xbb\x8dc" in secret      # có trước khi xóa
    assert b"Thi\xe1\xba\xbft k\xe1\xba\xbf tr\xc6\xb0\xe1\xbb\x9dng h\xe1\xbb\x8dc" not in (
        c.data_dir / "QuanLyHopDong.db").read_bytes()
    c.close()


def test_wipe_all_including_license(tmp_path, vendor):
    from qlhd.context import AppContext

    priv, pub = vendor
    c = AppContext(tmp_path / "gd", vendor_pub=pub)
    mid = c.machine_id
    key = lic.issue_director_key(priv, c.acc.activation_request(), "Công ty ABC", "0101234567", 5)
    c.acc.activate_director(key, "giamdoc", "Nguyễn Văn A", "matkhau123")
    c.user = c.acc.login("giamdoc", "matkhau123")
    c.wipe_all("matkhau123", keep_license=False)
    assert not c.acc.is_activated() and c.acc.users() == []
    c.close()
    assert AppContext(tmp_path / "gd", vendor_pub=pub).machine_id == mid


def test_change_username(ctx):
    from qlhd.core.auth import AuthError

    uid = ctx.user["MaNguoiDung"]
    FinanceStore.initialize(ctx.db, ctx.finance_path, ctx.user, "khoa-thu-chi-1", iterations=1000)[0].lock()
    with pytest.raises(AuthError):
        ctx.acc.change_username(uid, "sai-mat-khau", "dung.bt")
    with pytest.raises(AuthError):
        ctx.acc.change_username(uid, "matkhau123", "có dấu")
    ctx.acc.change_username(uid, "matkhau123", "dung.bt")
    ctx.refresh_user()
    assert ctx.acc.login("dung.bt", "matkhau123")["MaNguoiDung"] == uid
    with pytest.raises(AuthError):
        ctx.acc.login("giamdoc", "matkhau123")
    assert FinanceStore.unlock(ctx.db, ctx.finance_path, ctx.user, "khoa-thu-chi-1") is not None


def test_new_layout_home_theme_and_account(qtbot, ctx):
    from datetime import date, timedelta

    from PySide6.QtWidgets import QApplication, QLabel

    from qlhd.ui import style
    from qlhd.ui.auth_dialogs import MyAccountDialog, WipeDataDialog
    from qlhd.ui.home_page import reminder_text
    from qlhd.ui.main_window import PAGE_ROLE, MainWindow

    hd = ctx.cs.contracts("Đầu ra")[0]
    end = (date.today() + timedelta(days=5)).isoformat()
    ctx.db.execute("UPDATE HopDong SET NgayKetThucDuKien = ?, TinhTrangHopDong = 'Đang thực hiện' WHERE Ma = ?",
                   (end, hd["Ma"]))
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    heads = [win.nav.item(i).text() for i in range(win.nav.count()) if win.nav.item(i).data(PAGE_ROLE) is None]
    assert heads == ["CÔNG VIỆC", "TÀI CHÍNH", "DỮ LIỆU"]
    assert win.top_title.text() == "Tổng quan"
    texts = " ".join(lbl.text() for lbl in win.findChildren(QLabel))
    assert "NHÀ PHÁT TRIỂN" in texts and "0988095867" in texts and "Hợp đồng sắp hết hạn" in texts
    assert win.bell_badge.isVisibleTo(win) and int(win.bell_badge.text()) >= 1
    assert "còn 5 ngày" in reminder_text(ctx, date.today().isoformat())
    win._daily_reminder()
    assert ctx.db.setting("nhac_hd_ngay:giamdoc") == date.today().isoformat()
    # mở hợp đồng từ Tổng quan
    win.show_contract(hd["Ma"])
    assert not win.grab().isNull()          # vẽ thật cả bảng (cột % thanh toán, huy hiệu, thẻ)
    assert win.top_title.text() == "Hợp đồng" and win.contracts_page.current_id() == hd["Ma"]
    # sáng / tối, nhớ lựa chọn
    win.toggle_theme()
    win.go_to("Tổng quan")
    win.grab()
    assert style.current() == "toi" and ctx.db.setting("giao_dien") == "toi"
    assert "#0f141c" in QApplication.instance().styleSheet()
    win.toggle_theme()
    assert style.current() == "sang"
    # tài khoản của tôi
    dlg = MyAccountDialog(ctx, win)
    qtbot.addWidget(dlg)
    dlg.new_name.setText("dung.bt")
    dlg.name_pw.setText("matkhau123")
    from unittest import mock

    with mock.patch("qlhd.ui.auth_dialogs.info"):
        dlg._rename()
    assert ctx.user["TenDangNhap"] == "dung.bt" and ctx.db.setting("dang_nhap_cuoi") == "dung.bt"
    w = WipeDataDialog(ctx, win)
    qtbot.addWidget(w)
    assert not w.ok.isEnabled()
    w.confirm.setText("xoa sach")
    assert w.ok.isEnabled()
    win.close()


def test_director_wipes_accountant_machine(qtbot, tmp_path, vendor, ctx, monkeypatch):
    from datetime import date, timedelta

    from qlhd.context import AppContext
    from qlhd.core import licensing
    from qlhd.core.auth import AuthError
    from qlhd.ui.auth_dialogs import DirectorWipeDialog, LoginDialog

    priv, pub = vendor
    monkeypatch.setattr("qlhd.context.machine_id", lambda d: "QLHD-" + d.name.upper())

    def member(name):
        m = AppContext(tmp_path / name, vendor_pub=pub)
        code = ctx.acc.issue_code(ten_dang_nhap=name, ho_ten="Kế toán", vai_tro="Kế toán viên",
                                  machine_id=m.machine_id)
        m.acc.activate_member(code, "matkhau123")
        sample_data(m.db)
        return m

    kt = member("ketoan")
    with pytest.raises(AuthError):
        ctx.acc.issue_wipe_order(ctx.machine_id)                    # không tự xóa máy Giám đốc bằng lệnh
    tok, order = ctx.acc.issue_wipe_order(kt.machine_id, valid_days=3)
    with pytest.raises(AuthError):
        ctx.acc.check_wipe_order(tok)                               # lệnh dành cho máy khác
    with pytest.raises(licensing.LicenseError):
        licensing.read_wipe_order(tok, ctx.acc.director_payload()["pub"], kt.machine_id,
                                  today=date.today() + timedelta(days=4))       # quá hạn
    # nút Giám đốc can thiệp ở màn hình đăng nhập máy kế toán
    login = LoginDialog(kt)
    qtbot.addWidget(login)
    from PySide6.QtWidgets import QPushButton

    assert any(b.text().startswith("Giám đốc can thiệp") for b in login.findChildren(QPushButton))
    dlg = DirectorWipeDialog(kt)
    qtbot.addWidget(dlg)
    # gói đồng bộ từ máy Giám đốc mang theo lệnh
    p = tmp_path / "goi.qlhd"
    ctx.sync.export_package(p, "dongbo123", user=ctx.user)
    pkg = kt.sync.read_package(p, "dongbo123")
    assert kt.sync.wipe_order_for_me(pkg) == tok
    assert kt.db.one("SELECT COUNT(*) n FROM HopDong")["n"] > 0
    kt.wipe_by_order(tok)
    assert kt.db.one("SELECT COUNT(*) n FROM HopDong")["n"] == 0 and not kt.acc.is_activated()
    kt.close()
    # giữ bản quyền: dùng lại được máy, nhưng gói cũ gửi lại không xóa lần nữa
    kt2 = member("ketoan2")
    tok2, _ = ctx.acc.issue_wipe_order(kt2.machine_id, keep_license=True)
    kt2.wipe_by_order(tok2)
    assert kt2.acc.is_activated() and kt2.acc.login("ketoan2", "matkhau123")
    with pytest.raises(AuthError):
        kt2.wipe_by_order(tok2)
    assert len(ctx.acc.pending_wipe_orders()) == 2
    ctx.acc.cancel_wipe_orders()
    assert ctx.acc.pending_wipe_orders() == []
    kt2.close()


def _bang_ke(path):
    """Bảng kê mua vào kiểu mẫu quản trị: tiêu đề ở dòng 3, dòng nhóm 1 / 5, dòng 'Số dòng ='."""
    from datetime import datetime

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    ws.append(["BẢNG KÊ HÓA ĐƠN, CHỨNG TỪ HÀNG HÓA, DỊCH VỤ MUA VÀO (MẪU QUẢN TRỊ)"])
    ws.append(["Quý 2 năm 2026"])
    ws.append([None, "Số hóa đơn", "Ngày hóa đơn", "Tên người bán", "Mã số thuế người bán", "Mặt hàng",
               "Giá trị HHDV mua vào chưa có thuế", "Thuế suất", "Thuế GTGT", "TK thuế"])
    ws.append(["Nhóm HHDV mua vào : 1. Hàng hoá, dịch vụ dùng riêng cho SXKD chịu thuế GTGT (2 )", None, None, None,
               None, None, 277600, None, 22208])
    ws.append([None, "1285718", datetime(2026, 4, 30), "CÔNG TY CỔ PHẦN GIAO THÔNG SỐ VIỆT NAM", 109266456,
               "Cước đường bộ 30K90317", 94699, "10 %", 7576])
    ws.append([None, "5945912", "30/06/2026", "TỔNG CÔNG TY CỔ PHẦN BƯU CHÍNH VIETTEL", "0104093672",
               "Cước chuyển phát", 182901, 0.08, 14632])
    ws.append(["Nhóm HHDV mua vào : 5. Hàng hóa, dịch vụ không phải tổng hợp trên tờ khai 01/GTGT (3 )", None, None,
               None, None, None, 1220652])
    ws.append([None, "727227", datetime(2026, 4, 2), "CÔNG TY ĐIỆN LỰC GIA LÂM", "0100101114-009",
               "Điện tiêu thụ tháng 3/2026", 320652, "0 %", 0])
    ws.append([None, "1397065", datetime(2026, 4, 19), "CÔNG TY TNHH MTV PETROLIMEX HÀ NỘI", None,
               "Xăng Ron 95 mức 5", 900000, "KCT", 0])                        # thiếu MST
    ws.append([None, "727227", datetime(2026, 4, 2), "CÔNG TY ĐIỆN LỰC GIA LÂM", "0100101114-009",
               "Điện tiêu thụ tháng 3/2026", 320652, "0 %", 0])                  # lặp lại trong file
    ws.append([None, "Số dòng = 5", None, None, None, None, 1498251, None, 22208])
    wb.save(path)
    return path


def test_in_invoices_from_excel_bang_ke(qtbot, ctx, tmp_path, monkeypatch):
    from qlhd.core import in_invoice_excel
    from qlhd.ui import invoices_page
    from qlhd.ui.invoices_page import CHOOSE, BatchInvoiceDialog, InvoicesPage

    rows = in_invoice_excel.read_workbook(_bang_ke(tmp_path / "bang_ke.xlsx"))
    assert [r.values["SoHoaDonGoc"] for r in rows] == ["1285718", "5945912", "727227", "1397065", "727227"]
    first, second, power = rows[0].values, rows[1].values, rows[2].values
    assert first["MaThueNCC"] == "0109266456" and first["NhomThue"] == "Chịu thuế" and first["ThueSuat"] == "10%"
    assert first["GiaTriSauThue"] == 102275 and second["NgayNhanHD"] == "2026-06-30" and second["ThueSuat"] == "8%"
    assert power["NhomThue"] == "Không chịu thuế" and power["ThueSuat"] == "0%"
    assert rows[3].errors == ["Thiếu Mã số thuế người bán"]
    # file mẫu của phần mềm đọc lại được
    tpl = in_invoice_excel.write_template(tmp_path / "mau.xlsx")
    assert in_invoice_excel.read_workbook(tpl)[0].values["TenNhaCungCap"].startswith("CÔNG TY CỔ PHẦN GIAO THÔNG")

    page = InvoicesPage(ctx, "vao")
    qtbot.addWidget(page)
    items = page.excel_items(rows)
    assert all(it["values"]["MaHopDong"] == CHOOSE for it in items)          # không đoán bừa hợp đồng
    assert [it["status"] for it in items] == ["moi", "moi", "moi", "moi", "da_co"]
    dlg = BatchInvoiceDialog(page, items=items, source="bang_ke.xlsx")
    qtbot.addWidget(dlg)
    shown = []
    monkeypatch.setattr(invoices_page, "error", lambda _p, text, *a: shown.append(text))
    monkeypatch.setattr(invoices_page, "info", lambda _p, text, *a: shown.append(text))
    dlg._save()
    assert "thiếu thông tin" in shown[-1]                       # dòng thiếu MST chặn lưu
    dlg.table.item(3, 0).setCheckState(invoices_page.Qt.Unchecked)
    dlg._save()
    assert "Chưa chọn hợp đồng" in shown[-1]                    # phải chọn hợp đồng / mua trực tiếp
    dlg.bulk_contract.set_value(None)
    dlg._apply_contract()
    dlg._save()
    assert shown[-1] == "Đã nhập 3 hóa đơn."
    saved = {r["SoHoaDonGoc"]: r for r in ctx.cs.in_invoices()}
    assert saved["727227"]["NhomThue"] == "Không chịu thuế" and saved["5945912"]["VAT"] == 14632
    # nhập lại cùng file: tất cả đã có
    again = page.excel_items(in_invoice_excel.read_workbook(tmp_path / "bang_ke.xlsx"))
    assert [it["status"] for it in again] == ["da_co", "da_co", "da_co", "moi", "da_co"]


def test_assets_page_and_profit_tab(qtbot, ctx, tmp_path, monkeypatch):
    from pathlib import Path

    from PySide6.QtWidgets import QDialog

    from qlhd.ui import assets_page
    from qlhd.ui.assets_page import AssetsPage, ClassifyDialog
    from qlhd.ui.profit_page import ProfitTab

    page = AssetsPage(ctx)
    qtbot.addWidget(page)
    page.refresh()
    assert page.pending.rowCount() == 1 and page.tabs.tabText(0) == "Cần xem lại (1)"
    row = page.pending.rows[0]
    dlg = ClassifyDialog([row])
    qtbot.addWidget(dlg)
    dlg.cls.set_value("CCDC")
    dlg.group.set_value("TB02")
    dlg.months.setValue(24)
    assert dlg.values() == ("CCDC", "TB02", 24)
    page.svc.set_line_class(row["Ma"], *dlg.values())
    page.refresh()
    assert page.pending.rowCount() == 0
    shown = []
    monkeypatch.setattr(assets_page, "info", lambda _p, text, *a: shown.append(text))
    data = Path(__file__).parent / "data"
    previews = []

    def accept_preview(dlg):
        previews.append(dlg.table.rowCount())
        return QDialog.Accepted
    monkeypatch.setattr(assets_page.ImportPreviewDialog, "exec", accept_preview)
    for name in ("so_tscd_gia.xlsx", "so_ccdc_gia.xlsx"):
        monkeypatch.setattr(assets_page.QFileDialog, "getOpenFileName", lambda *a, n=name: (str(data / n), ""))
        page.tscd.import_book()
    assert previews == [4, 14] and "thêm 14" in shown[-1]
    page.tabs.setCurrentWidget(page.ccdc)
    page.ccdc.year.setValue(2025)
    assert page.ccdc.table.rowCount() == 14 + 1               # 14 dòng sổ + dòng tổng
    page.svc.save_asset({"LoaiSo": "CCDC", "TenTS": "Ổ cứng ngoài 1TB", "NgayGhiTang": "2025-11-25", "SoKy": 12,
                         "NguyenGia": 2_000_000})
    page.ccdc.reload()
    assert sum(1 for r in page.ccdc.table.rows if r.get("CanhBao")) == 2 and "nghi trùng" in page.ccdc.total.text()
    page.ccdc.year.setValue(2026)
    assert any(r.get("TenTS", "").startswith("Hóa đơn 7") for r in page.ccdc.table.rows)   # món vừa phân loại
    page.ccdc.extra.setChecked(True)
    out = tmp_path / "so.xlsx"
    monkeypatch.setattr(assets_page.QFileDialog, "getSaveFileName", lambda *a: (str(out), ""))
    page.ccdc.export()
    assert out.exists()
    # mẫu kê khai CCDC nhập thêm ngoài hóa đơn: tải mẫu, điền, nhập lại
    tpl = tmp_path / "mau_ccdc.xlsx"
    monkeypatch.setattr(assets_page.QFileDialog, "getSaveFileName", lambda *a: (str(tpl), ""))
    page.ccdc.save_extra_template()
    import openpyxl
    wb = openpyxl.load_workbook(tpl)
    ws = wb["Kê khai CCDC"]
    for j, v in enumerate(["", "Quạt cây", "", "", "Được tặng", "03/02/2026", "", 6, "Cái", 1, 900_000], start=1):
        ws.cell(3, j, v)
    wb.save(tpl)
    monkeypatch.setattr(assets_page.QFileDialog, "getOpenFileName", lambda *a: (str(tpl), ""))
    page.ccdc.import_extra()
    assert previews[-1] == 1 and "thêm 1" in shown[-1]
    assert any(r.get("TenTS") == "Quạt cây" for r in page.ccdc.table.rows)
    # xóa tài sản hết khấu hao khỏi sổ
    monkeypatch.setattr(assets_page, "confirm_delete", lambda *a, **k: True)
    monkeypatch.setattr(assets_page.RemoveDepreciatedDialog, "exec", lambda dlg: QDialog.Accepted)
    page.ccdc.year.setValue(2027)
    done = page.svc.fully_depreciated("CCDC", 2027)
    page.ccdc.remove_depreciated()
    assert not page.svc.fully_depreciated("CCDC", 2027)
    page.ccdc.case.set_value("da_xoa")
    assert len([r for r in page.ccdc.table.rows if not r.get("_kieu")]) == len(done)
    page.tabs.setCurrentIndex(1)
    page.l_year.setValue(2026)
    assert page.lines.rowCount() == 2

    ctx.finance, _ = FinanceStore.initialize(ctx.db, ctx.finance_path, ctx.user, "khoa-thu-chi-1", iterations=1000)
    tab = ProfitTab(ctx)
    qtbot.addWidget(tab)
    tab.year.setValue(2026)
    tab.reload()
    tab.reload()
    names = [r.get("TenDong") for r in tab.table.rows]
    assert "Doanh thu" in names and "Tổng chi phí phải nộp" in names
    dt = next(r for r in tab.table.rows if r.get("Nguon") == "doanh_thu")
    assert dt["GiaTri"] == 50_000_000
    ctx.lock_finance()


def test_contract_filters_and_edit_payment(qtbot, ctx, monkeypatch):
    from qlhd.ui import contracts_page
    from qlhd.ui.contracts_page import ContractsPage
    from qlhd.ui.invoices_page import InvoicePaymentsDialog, InvoicesPage

    page = ContractsPage(ctx)
    qtbot.addWidget(page)
    page.refresh()
    assert page.table.rowCount() == 2
    page.status.set_value("Đang thực hiện")
    assert [r["SoHopDong"] for r in page.table.rows] == ["01/2026/HĐTV"]
    page.status.set_value(None)
    page.paid.set_value("mot_phan")                 # thu 35 / 110 triệu
    assert [r["TTThanhToan"] for r in page.table.rows] == ["Thanh toán một phần"]
    page.paid.set_value("con_thu")                  # đã xuất 55, thu 35
    assert page.table.rowCount() == 1
    for state in ("du", "chua"):
        page.paid.set_value(state)
        assert page.table.rowCount() == 0           # hợp đồng đầu vào không xét thanh toán
    page.status.set_value("Hoàn thành")
    page.paid.set_value(None)
    assert page.table.rowCount() == 0
    page.status.set_value(None)
    xuat = page.box.combo("xuat")
    page.filter.set_value("Đầu ra")
    xuat.set_value("xuat_mot_phan")                 # HĐ đầu ra đã xuất 55 / 110 triệu
    assert [r["SoHopDong"] for r in page.table.rows] == ["01/2026/HĐTV"]
    xuat.set_value("chua_xuat")
    assert "01/2026/HĐTV" not in [r["SoHopDong"] for r in page.table.rows]
    xuat.set_value(None)
    page.filter.set_value(None)

    # sửa lần thanh toán ghi nhầm (30 triệu → 33 triệu) từ màn Hóa đơn đầu ra
    def fake_dialog(_ctx, _parent, ma_hd, values, title="Ghi nhận thanh toán"):
        assert title == "Sửa lần thanh toán" and values["SoTien"] == 30_000_000
        return {"MaHopDong": ma_hd, "NgayThanhToan": values["NgayThanhToan"], "SoTien": 33_000_000,
                "HinhThuc": "Chuyển khoản", "MaHDDauRa": values["MaHDDauRa"], "SoThamChieuSK": "FT123", "GhiChu": ""}
    monkeypatch.setattr(contracts_page, "payment_dialog", fake_dialog)
    inv_page = InvoicesPage(ctx, "ra")
    qtbot.addWidget(inv_page)
    inv_page.refresh()
    inv = inv_page.table.rows[0]
    assert inv["DaThu"] == 30_000_000
    dlg = InvoicePaymentsDialog(ctx, inv, inv_page)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 1
    dlg.table.selectRow(0)
    dlg.edit()
    assert dlg.changed and ctx.cs.invoice_paid(inv["Ma"]) == 33_000_000
    assert len(ctx.db.active("ThanhToanHopDong")) == 2      # sửa tại chỗ, không thêm bản ghi
    # sửa ở thẻ Thanh toán của hợp đồng cũng được
    page.select_contract(inv["MaHopDong"])
    page.payments.selectRow(0)
    monkeypatch.setattr(contracts_page, "payment_dialog",
                        lambda c, p, ma_hd, values, title="": dict(fake_dialog(c, p, ma_hd, dict(values, SoTien=30_000_000),
                                                                               "Sửa lần thanh toán"), SoTien=31_000_000))
    page.edit_payment()
    assert ctx.cs.invoice_paid(inv["Ma"]) == 31_000_000


def test_invoice_payment_details(qtbot, ctx, monkeypatch):
    from qlhd.ui import invoices_page
    from qlhd.ui.invoices_page import InvoicePaymentsDialog, InvoicesPage

    monkeypatch.setattr(invoices_page, "info", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a[1])))
    page = InvoicesPage(ctx, "ra")
    qtbot.addWidget(page)
    page.refresh()
    r = page.table.rows[0]
    assert (r["TTThanhToan"], r["NgayTT"], r["SoThamChieuSK"]) == ("Thu một phần", "10/03/2026", "FT123")
    page.search.setText("ft123")
    assert page.table.rowCount() == 1
    opened = []
    monkeypatch.setattr(InvoicePaymentsDialog, "exec", lambda dlg: opened.append(dlg.table.rowCount()))
    page.table.selectRow(0)
    page.add_payment()                       # đã có thanh toán: mở chi tiết thay vì ghi mới
    assert opened == [1]
    asked = []
    monkeypatch.setattr(invoices_page, "confirm_delete", lambda _p, text, *a, **k: asked.append(text) or False)
    dlg = InvoicePaymentsDialog(ctx, r, page)
    qtbot.addWidget(dlg)
    dlg.table.selectRow(0)
    dlg.delete()
    assert asked and "FT123" in asked[0] and ctx.cs.invoice_paid(r["Ma"]) == 30_000_000   # chưa xác nhận: không xóa
    monkeypatch.setattr(invoices_page, "confirm_delete", lambda *a, **k: True)
    dlg.delete()
    assert ctx.cs.invoice_paid(r["Ma"]) == 0 and dlg.table.rowCount() == 0
    page.search.clear()
    assert page.table.rows[0]["TTThanhToan"] == "Chưa thu"


def test_filter_and_fill_bank_refs(qtbot, ctx, monkeypatch):
    from qlhd.core.contracts import ValidationError
    from qlhd.ui import invoices_page
    from qlhd.ui.invoices_page import BankRefDialog, InvoicesPage

    cs = ctx.cs
    inv = cs.out_invoices()[0]
    p = cs.save_payment({"MaHopDong": inv["MaHopDong"], "MaHDDauRa": inv["Ma"], "NgayThanhToan": "2026-03-20",
                         "SoTien": 10_000_000, "HinhThuc": "Chuyển khoản"})       # đã thu, chưa đối chiếu sao kê
    page = InvoicesPage(ctx, "ra")
    qtbot.addWidget(page)
    page.only.set_value("chua_sk")
    assert [r["Ma"] for r in page.table.rows] == [inv["Ma"]]
    missing = cs.payments_missing_ref()
    assert [r["Ma"] for r in missing] == [p]                 # tiền mặt và lần đã có FT123 không nằm trong danh sách
    with pytest.raises(ValidationError):
        cs.set_payment_ref(p, "FT123")                       # số tham chiếu đã dùng
    dlg = BankRefDialog(ctx, missing, page)
    qtbot.addWidget(dlg)
    dlg.table.item(0, len(dlg.COLS) - 1).setText("FT26079999")
    asked = []
    monkeypatch.setattr(invoices_page, "confirm_changes", lambda _p, ch, *a: asked.append(ch) or False)
    dlg.save()
    assert asked[0][0][2] == "FT26079999" and not ctx.db.get("ThanhToanHopDong", p)["SoThamChieuSK"]   # chưa xác nhận
    monkeypatch.setattr(invoices_page, "confirm_changes", lambda *a: True)
    dlg.save()
    assert dlg.saved == 1 and ctx.db.get("ThanhToanHopDong", p)["SoThamChieuSK"] == "FT26079999"
    page.refresh()
    assert page.table.rowCount() == 0 and not cs.payments_missing_ref()
    monkeypatch.setattr(invoices_page, "info", lambda _p, text, *a: setattr(page, "_msg", text))
    page.update_refs()
    assert "đều đã có" in page._msg


def test_editing_existing_data_needs_confirmation(qtbot, ctx, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from qlhd.ui import widgets
    from qlhd.ui.widgets import FormDialog

    fields = [{"key": "TenHopDong", "label": "Tên hợp đồng"},
              {"key": "GiaTriTruocThue", "label": "Giá trị trước thuế", "kind": "money"},
              {"key": "NgayKy", "label": "Ngày ký", "kind": "date"}]
    asked = []
    monkeypatch.setattr(widgets, "confirm", lambda _p, text, *a: asked.append(text) or False)
    row = {"Ma": "x", "TenHopDong": "Cũ", "GiaTriTruocThue": 1_000_000, "NgayKy": "2026-01-15"}
    dlg = FormDialog("Hợp đồng", fields, row)
    qtbot.addWidget(dlg)
    dlg._accept()                                   # không đổi gì: lưu luôn, không hỏi
    assert not asked and dlg.result() == QDialog.Accepted
    dlg = FormDialog("Hợp đồng", fields, row)
    qtbot.addWidget(dlg)
    dlg.set_value("GiaTriTruocThue", 2_000_000)
    dlg._accept()                                   # có sửa: hỏi, chọn Không thì không lưu
    assert "Giá trị trước thuế: 1.000.000 → 2.000.000" in asked[0] and dlg.result() != QDialog.Accepted
    monkeypatch.setattr(widgets, "confirm", lambda *a: True)
    dlg._accept()
    assert dlg.result() == QDialog.Accepted
    asked.clear()
    new = FormDialog("Hợp đồng", fields, {"TenHopDong": "Mới"})   # thêm mới: không hỏi
    qtbot.addWidget(new)
    monkeypatch.setattr(widgets, "confirm", lambda _p, text, *a: asked.append(text) or False)
    new.set_value("GiaTriTruocThue", 5)
    new._accept()
    assert not asked and new.result() == QDialog.Accepted


def test_home_fits_window_with_long_contract_names(qtbot, ctx):
    from PySide6.QtWidgets import QBoxLayout, QScrollArea

    from qlhd.ui.home_page import HomePage

    long_name = "Tư vấn lập báo cáo kinh tế kỹ thuật, thiết kế bản vẽ thi công và dự toán " * 4
    ctx.db.execute("UPDATE HopDong SET TenHopDong = ?", (long_name,))
    for w in (1300, 820):
        page = HomePage(ctx)
        qtbot.addWidget(page)
        page.resize(w, 700)
        page.show()
        page.refresh()
        qtbot.wait(50)
        scroll = page.findChild(QScrollArea)
        body = scroll.widget()
        from PySide6.QtWidgets import QWidget
        wide = [f"{type(x).__name__}#{x.objectName()} min={x.minimumSizeHint().width()} "
                f"w={x.width()} {getattr(x, 'text', lambda: '')()[:30]!r}"
                for x in body.findChildren(QWidget) if x.minimumSizeHint().width() > 250]
        assert body.width() <= scroll.viewport().width(), wide              # không phải kéo ngang
        assert body.minimumSizeHint().width() <= scroll.viewport().width()
        assert page.bottom.direction() == (QBoxLayout.TopToBottom if w < page.NARROW else QBoxLayout.LeftToRight)
        assert not page.grab().isNull()


def test_out_invoice_contract_picker_filters(qtbot, ctx):
    from qlhd.ui.invoices_page import InvoicesPage, bind_type_filter
    from qlhd.ui.widgets import FormDialog

    cs = ctx.cs
    cdt = cs.partners()[0]["Ma"]
    giam_sat = cs.save_contract_type("Giám sát")
    done = cs.save_contract({"ThoiGianThucHien": 30, "SoHopDong": "02/2026/GS", "TenHopDong": "Giám sát A",
                             "ChieuHopDong": "Đầu ra", "MaLoaiHD": giam_sat, "MaDoiTac": cdt,
                             "GiaTriTongSauThue": 11_000_000, "ThueSuat": "10%", "NgayKy": "2026-02-01"})
    cs.save_out_invoice({"SoHoaDon": "99", "MaHopDong": done, "NgayXuatHD": "2026-03-01",
                         "GiaTriSauThue": 11_000_000, "ThueSuat": "10%"})       # đã xuất đủ hóa đơn
    open_ = cs.save_contract({"ThoiGianThucHien": 30, "SoHopDong": "03/2026/GS", "TenHopDong": "Giám sát B",
                              "ChieuHopDong": "Đầu ra", "MaLoaiHD": giam_sat, "MaDoiTac": cdt,
                              "GiaTriTongSauThue": 5_000_000, "NgayKy": "2026-02-01"})
    page = InvoicesPage(ctx, "ra")
    qtbot.addWidget(page)

    def ids(combo):
        return [combo.itemData(i) for i in range(combo.count())]
    dlg = FormDialog("Hóa đơn đầu ra", page._fields(), {}, page)
    qtbot.addWidget(dlg)
    bind_type_filter(dlg, page._contracts)
    combo = dlg.widget("MaHopDong")
    assert done not in ids(combo) and open_ in ids(combo)
    assert all(h["ChieuHopDong"] == "Đầu ra" for h in page._contracts)
    dlg.widget("_LoaiHD").set_value(giam_sat)
    assert [x for x in ids(combo) if x != "__chon_hop_dong__"] == [open_]
    # sửa hóa đơn của hợp đồng đã xuất đủ: vẫn thấy hợp đồng đang gắn
    dlg2 = FormDialog("Hóa đơn đầu ra", page._fields(keep=done), {"MaHopDong": done}, page)
    qtbot.addWidget(dlg2)
    assert dlg2.widget("MaHopDong").value() == done
    # hóa đơn đầu vào: giữ như cũ, không có ô lọc
    page_v = InvoicesPage(ctx, "vao")
    qtbot.addWidget(page_v)
    assert "_LoaiHD" not in [f["key"] for f in page_v._fields()]
    # màn hình nhập nhiều hóa đơn đầu ra: lọc cột Hợp đồng theo loại
    from qlhd.ui.invoices_page import BatchInvoiceDialog
    items = [{"path": "a.xml", "file": "a.xml", "status": "moi", "notes": [], "goi_y": [],
              "values": {"SoHoaDon": "100", "NgayXuatHD": "2026-04-01", "GiaTriSauThue": 1_000_000,
                         "MaHopDong": "__chon_hop_dong__"}}]
    batch = BatchInvoiceDialog(page, items=items)
    qtbot.addWidget(batch)
    cell = batch.table.cellWidget(0, 8)
    assert done not in ids(cell) and open_ in ids(cell) and len(ids(cell)) > 2
    batch.type_filter.set_value(giam_sat)
    assert [x for x in ids(batch.table.cellWidget(0, 8)) if x != "__chon_hop_dong__"] == [open_]


def test_contract_tab_adds_out_invoice_and_payment(qtbot, ctx, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from qlhd.ui import invoices_page
    from qlhd.ui.contracts_page import ContractsPage
    from qlhd.ui.invoices_page import InvoicesPage

    page = ContractsPage(ctx)
    qtbot.addWidget(page)
    page.refresh()
    hd = next(r for r in page.table.rows if r["ChieuHopDong"] == "Đầu ra")
    page.select_contract(hd["Ma"])
    page.detail.setCurrentIndex(1)
    seen = []

    def fake_save(self, values, ma=None, *a, parent=None, **k):
        seen.append((dict(values), ma, parent))
        dlg = invoices_page.FormDialog("x", self._fields(keep=values.get("MaHopDong")), values, parent)
        dlg.close()
        v = {"MaHopDong": values["MaHopDong"], "SoHoaDon": "77", "NgayXuatHD": "2026-05-02",
             "GiaTriTruocThue": 10_000_000, "ThueSuat": "10%"}
        ctx.cs.save_out_invoice(v, ma)
        return True
    monkeypatch.setattr(InvoicesPage, "_save", fake_save)
    page.add_invoice()
    assert seen[0][0]["MaHopDong"] == hd["Ma"] and seen[0][2] is page
    assert page.current_id() == hd["Ma"] and page.detail.currentIndex() == 1       # vẫn đứng ở hợp đồng đó
    row = next(r for r in page.invoices.rows if r["SoHoaDon"] == "77")
    assert row["TTThanhToan"] == "Chưa thu" and row["ConPhaiThu"] == 11_000_000
    # ghi nhận thanh toán ngay tại thẻ Hóa đơn
    page.invoices.selectRow(page.invoices.rows.index(row))
    monkeypatch.setattr(invoices_page, "advance_dialog", lambda *a: 0)
    monkeypatch.setattr(invoices_page, "payment_dialog", lambda c, p, ma_hd, v, *a: dict(
        v, MaHopDong=ma_hd, NgayThanhToan="2026-05-20", SoThamChieuSK="FT777"))
    page.invoice_payment()
    row = next(r for r in page.invoices.rows if r["SoHoaDon"] == "77")
    assert row["TTThanhToan"] == "Đã thu đủ" and "FT777" in row["SoThamChieuSK"]
    # đã có thanh toán: bấm lại thì mở chi tiết các lần thanh toán
    page.invoices.selectRow(page.invoices.rows.index(row))
    opened = []
    monkeypatch.setattr(invoices_page.InvoicePaymentsDialog, "exec", lambda d: opened.append(1) or QDialog.Rejected)
    page.invoice_payment()
    assert opened == [1]
    # xóa hóa đơn ngay tại thẻ Hóa đơn (hỏi xác nhận), lần thanh toán vẫn giữ ở hợp đồng
    from qlhd.ui import contracts_page
    monkeypatch.setattr(contracts_page, "confirm_delete", lambda *a, **k: True)
    page.invoices.selectRow(page.invoices.rows.index(row))
    page.delete_invoice()
    assert not any(r["SoHoaDon"] == "77" for r in page.invoices.rows) and page.current_id() == hd["Ma"]
    assert any(p["SoThamChieuSK"] == "FT777" for p in page.payments.rows)
    # xóa công việc
    ctx.cs.save_task({"MaHopDong": hd["Ma"], "TenNoiDungCV": "Khảo sát", "TinhTrangCV": "Chưa làm"})
    page._reload_keep()
    page.tasks.selectRow(next(i for i, t in enumerate(page.tasks.rows) if t["TenNoiDungCV"] == "Khảo sát"))
    page.delete_task()
    assert not any(t["TenNoiDungCV"] == "Khảo sát" for t in page.tasks.rows)


def test_cross_links_between_screens(qtbot, ctx, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from qlhd.ui import assets_page, contracts_page
    from qlhd.ui.contracts_page import ContractsPage
    from qlhd.ui.home_page import HomePage, _ClickFrame
    from qlhd.ui.invoices_page import InvoicesPage
    from qlhd.ui.main_window import MainWindow
    from qlhd.ui.reports_page import row_contract

    hd = ctx.cs.contracts("Đầu ra")[0]
    # hóa đơn → mở hợp đồng (trong cửa sổ chính chuyển sang trang Hợp đồng đúng hợp đồng đó)
    monkeypatch.setattr(MainWindow, "_startup_notices", lambda self: None)
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    inv_page = next(p for p in win.pages if isinstance(p, InvoicesPage) and p.kind == "ra")
    win.go_to("Hóa đơn đầu ra")
    inv_page.table.selectRow(0)
    inv_page.go_contract()
    assert win.top_title.text() == "Hợp đồng" and win.contracts_page.current_id() == hd["Ma"]
    # Tổng quan: bấm dòng công nợ mở hợp đồng
    home = HomePage(ctx)
    qtbot.addWidget(home)
    home.refresh()
    got = []
    home.open_contract.connect(got.append)
    home.findChildren(_ClickFrame)[0].clicked.emit()
    assert got == [hd["Ma"]]
    # báo cáo: dòng hợp đồng / hóa đơn / thanh toán biết hợp đồng của nó, dòng tổng thì không
    assert row_contract({"Ma": "x", "ChieuHopDong": "Đầu ra", "SoHopDong": "1"}) == "x"
    assert row_contract({"Ma": "i", "MaHopDong": "y"}) == "y" and row_contract({"_kieu": "tong", "MaHopDong": "y"}) is None
    # sửa đối tác ngay ở trang Hợp đồng: tên trên hợp đồng đổi theo
    page = ContractsPage(ctx)
    qtbot.addWidget(page)
    page.select_contract(hd["Ma"])
    monkeypatch.setattr(contracts_page.FormDialog, "exec", lambda d: (
        d.set_value("TenDoiTac", "Ban QLDA Hà Nội (đổi tên)"), QDialog.Accepted)[1])
    monkeypatch.setattr(contracts_page, "confirm_changes", lambda *a, **k: True, raising=False)
    from qlhd.ui import widgets
    monkeypatch.setattr(widgets, "confirm_changes", lambda *a, **k: True)
    page.edit_partner()
    assert ctx.db.get("HopDong", hd["Ma"])["TenDoiTac"] == "Ban QLDA Hà Nội (đổi tên)"
    assert page.current_id() == hd["Ma"]
    # sổ tài sản: phân loại lại tài sản sinh từ hóa đơn ngay trong sổ
    from qlhd.core.assets import AssetService
    svc = AssetService(ctx.db)
    line = svc.pending_lines()[0]
    svc.set_line_class(line["Ma"], "CCDC", "TB02", 24)
    asset = next(a for a in svc.assets("CCDC") if a["MaDongHD"] == line["Ma"])
    ap = assets_page.AssetsPage(ctx)
    qtbot.addWidget(ap)
    tab = ap.ccdc
    tab.year.setValue(2026)
    tab.reload()
    i = next(i for i, r in enumerate(tab.table.rows) if r.get("Ma") == asset["Ma"])
    tab.table.selectRow(i)
    monkeypatch.setattr(assets_page.ClassifyDialog, "exec", lambda d: QDialog.Accepted)
    from qlhd.core import asset_catalog as dm
    monkeypatch.setattr(assets_page.ClassifyDialog, "values", lambda d: (dm.CHI_PHI, "CP06", None))
    monkeypatch.setattr(assets_page, "error", lambda _p, text, *a: (_ for _ in ()).throw(AssertionError(text)))
    monkeypatch.setattr(assets_page, "confirm_changes", lambda *a, **k: True)
    tab.reclassify()
    assert not any(a["MaDongHD"] == line["Ma"] for a in svc.assets("CCDC"))


def test_report_filters_follow_context(qtbot, ctx):
    from qlhd.ui.reports_page import ReportsPage

    rp = ReportsPage(ctx)
    qtbot.addWidget(rp)
    rp.refresh()
    rp.context.set_value("hoa_don_ra")
    assert rp.loai.isEnabled() and rp.tt_tt.isEnabled()
    assert not rp.nhom_thue.isEnabled() and not rp.lien_ket.isEnabled() and not rp._labels["nhom_thue"].isEnabled()
    rp.nhom_thue.set_value("Chịu thuế")
    assert rp.filters().nhom_thue == "tach"                 # ô mờ không ảnh hưởng báo cáo
    rp.context.set_value("hoa_don_vao")
    assert rp.nhom_thue.isEnabled() and rp.lien_ket.isEnabled()
    assert not rp.nguoi_mua.isEnabled() and not rp.loai.isEnabled()
    assert rp.tt_tt.isEnabled()                              # hóa đơn đầu vào lọc được đã / chưa thanh toán
    assert rp.filters().nhom_thue == "Chịu thuế"
    rp.context.set_value("da_thanh_toan")
    assert rp.doi_tac.isEnabled() and not rp.nguoi_mua.isEnabled() and not rp.nhom_thue.isEnabled()
    assert not rp.chieu.isEnabled() and not rp.trong_ky.isEnabled()
    rp.context.set_value("thoi_gian")
    assert rp.ky.isEnabled() and not rp.tach_nam.isEnabled()
    rp.ky_td.set_value("thang_nay")
    assert not rp.tu.isEnabled()                             # kỳ tương đối vẫn khóa ô ngày
    rp.context.set_value("hop_dong")
    assert not rp.tu.isEnabled() and rp.ky_td.isEnabled() and not rp.ky.isEnabled()


def test_classify_dialog_lists_every_group_and_switches_class(qtbot):
    from qlhd.core import asset_catalog as dm
    from qlhd.ui.assets_page import ClassifyDialog
    d = ClassifyDialog([{"TenHang": "Thay lốp xe ô tô", "ThanhTien": 3_000_000, "DonGiaTinh": 3_000_000,
                         "PhanLoai": dm.CCDC, "MaNhomDM": "TB01", "SoThang": 12}])
    qtbot.addWidget(d)
    codes = [d.group.itemData(i) for i in range(d.group.count())]
    assert "CP03" in codes and len(codes) == len(dm.CATALOG)
    d.group.set_value("CP03")
    assert d.values() == (dm.CHI_PHI, "CP03", None)
    d.group.setEditText("lốp không có nhóm này")
    assert d.values()[1] is None
    assert dm.classify("Thay lốp xe ô tô", 3_000_000).nhom.ma == "CP03"
    assert dm.classify("Lốp xe Michelin", 3_000_000).nhom.ma == "CP03"


def test_invoice_payment_offers_advance_deduction(qtbot, ctx, monkeypatch):
    from qlhd.ui import invoices_page

    hd = next(h["Ma"] for h in ctx.cs.contracts("Đầu ra"))
    inv = ctx.cs.save_out_invoice({"SoHoaDon": "909", "MaHopDong": hd, "NgayXuatHD": "2026-06-01",
                                   "GiaTriTruocThue": 1_000_000, "VAT": 100_000})
    adv = ctx.cs.save_payment({"MaHopDong": hd, "NgayThanhToan": "2026-05-01", "SoTien": 400_000})
    for a in ctx.cs.advances(hd):
        if a["Ma"] != adv:
            ctx.cs.delete_payment(a["Ma"])                               # chỉ giữ một tạm ứng để kiểm tra
    asked, seen = [], []
    monkeypatch.setattr(invoices_page, "advance_dialog", lambda p, r, adv: asked.append(adv) or 300_000)
    monkeypatch.setattr(invoices_page, "info", lambda *a: None)
    monkeypatch.setattr(invoices_page, "payment_dialog", lambda c, p, ma_hd, v, *a: seen.append(v) or None)
    row = invoices_page.out_invoice_row(ctx, ctx.db.get("HoaDonDauRa", inv))
    assert invoices_page.invoice_payment(ctx, None, row)
    assert asked[0][0]["SoTien"] == 400_000 and seen[0]["SoTien"] == 800_000      # khấu trừ 300.000 / 400.000
    assert ctx.db.get("ThanhToanHopDong", adv)["SoTien"] == 100_000 and ctx.cs.advances(hd)


def test_delete_dialog_needs_explicit_agreement(qtbot):
    from qlhd.ui.widgets import DeleteDialog
    dlg = DeleteDialog("hợp đồng 01", ["2 công việc", ""], count=1)
    qtbot.addWidget(dlg)
    assert not dlg.ok.isEnabled()              # chưa tích đồng ý: không bấm Xóa được
    dlg.agree.setChecked(True)
    assert dlg.ok.isEnabled()


def test_font_size_scales_whole_app(qtbot):
    from PySide6.QtWidgets import QApplication
    from qlhd.ui import style
    try:
        style.apply_theme(QApplication.instance(), "sang", "lon")
        assert "11.5pt" in QApplication.instance().styleSheet() and style.pt(10) == "11.5pt"
    finally:
        style.apply_theme(QApplication.instance(), "sang", "vua")
    assert "font-size: 10pt" in QApplication.instance().styleSheet()


def test_alerts_many_contexts_and_telegram(qtbot, ctx, monkeypatch):
    from datetime import date, timedelta

    from qlhd.core import alerts as al
    from qlhd.ui import alerts_ui

    hd = ctx.cs.contracts("Đầu ra")[0]
    ctx.db.execute("UPDATE HopDong SET NgayKetThucDuKien = ?, NgayBatDauDuKien = NULL, "
                   "TinhTrangHopDong = 'Đang thực hiện' WHERE Ma = ?",
                   ((date.today() + timedelta(days=3)).isoformat(), hd["Ma"]))
    items = al.collect(ctx.db)
    kinds = {a.loai for a in items}
    assert {"hd_het_han", "thieu_du_lieu"} <= kinds
    assert any(a.loai == "thieu_du_lieu" and "ngày bắt đầu" in a.chi_tiet for a in items)
    only = al.collect(ctx.db, types=["thieu_du_lieu"])
    assert {a.loai for a in only} == {"thieu_du_lieu"}
    text = al.digest_text(items, "Công ty A")
    assert "Hợp đồng sắp hết hạn" in text and "Hợp đồng thiếu dữ liệu" in text
    # chọn ngữ cảnh trên phần mềm, nhớ theo tài khoản
    dlg = alerts_ui.NotificationsDialog(ctx)
    qtbot.addWidget(dlg)
    dlg.picker.boxes["thieu_du_lieu"].setChecked(False)
    assert "thieu_du_lieu" not in al.AlertSettings(ctx.db).app_types(ctx.user["TenDangNhap"])
    # Telegram: lưu cài đặt, đến giờ thì gửi, mỗi ngày một lần
    st = al.AlertSettings(ctx.db)
    st.set_telegram({"token": "1:abc", "chat": "42", "bat": True, "gio": "08:00", "khi_mo": False,
                     "loai": ["hd_het_han"]})
    today = date.today().isoformat()
    assert not st.due(f"{today} 07:59") and st.due(f"{today} 08:00")
    st.mark_sent(today)
    assert not st.due(f"{today} 09:00")
    sent = []
    monkeypatch.setattr(al, "_tg_call", lambda token, method, data=None, timeout=12: sent.append((token, data)) or
                        {"ok": True})
    assert al.tg_send("1:abc", "42", "x" * 5000) == 2 and sent[0][1]["chat_id"] == "42"
    assert al.split_message("a\nb", 10) == ["a\nb"]


def test_home_contexts_are_selectable_and_remembered(qtbot, ctx):
    from PySide6.QtWidgets import QLabel

    from qlhd.ui.home_page import HomePage

    page = HomePage(ctx)
    qtbot.addWidget(page)
    page.resize(1300, 900)
    page.show()
    page.refresh()
    titles = lambda: {lbl.text() for lbl in page.findChildren(QLabel) if lbl.objectName() == "CardTitle"  # noqa: E731
                      and lbl.isVisibleTo(page)}
    qtbot.wait(20)
    assert "Hợp đồng thiếu dữ liệu" in titles() and page.stats.count() == 8
    page.chips["canhbao"].setChecked(False)
    qtbot.wait(20)
    assert "Hợp đồng thiếu dữ liệu" not in titles()
    page.period.set_value("nam")
    again = HomePage(ctx)
    qtbot.addWidget(again)
    assert not again.chips["canhbao"].isChecked() and again.period.value() == "nam"
    if os.environ.get("QLHD_SHOT"):
        page.chips["canhbao"].setChecked(True)
        page.resize(1280, 800)
        qtbot.wait(500)
        page.grab().save(os.environ["QLHD_SHOT"])


def test_in_invoice_form_classifies_lines_in_one_step(qtbot, ctx, monkeypatch):
    import os

    from PySide6.QtWidgets import QDialog

    from qlhd.core import asset_catalog as dm
    from qlhd.core.assets import AssetService
    from qlhd.ui import widgets
    from qlhd.ui.in_invoice_form import InInvoiceDialog
    from qlhd.ui.invoices_page import InvoicesPage

    page = InvoicesPage(ctx, "vao")
    qtbot.addWidget(page)
    lines = [{"TenHang": "Thay lốp ô tô", "SoLuong": 4, "DonGia": 2_000_000, "ThanhTien": 8_000_000},
             {"TenHang": "Laptop và giấy vệ sinh", "SoLuong": 1, "DonGia": 15_000_000, "ThanhTien": 15_000_000}]
    values = {"SoHoaDonGoc": "901", "TenNhaCungCap": "Gara A", "MaThueNCC": "0109999999", "NgayNhanHD": "2026-05-05",
              "GiaTriTruocThue": 23_000_000, "VAT": 2_300_000, "_Dong": lines}
    seen = {}

    def run(dlg):
        assert isinstance(dlg, InInvoiceDialog)
        assert [ln["PhanLoai"] for ln in dlg.lines] == [dm.CHI_PHI, dm.CHUA_PHAN_LOAI]   # mơ hồ: không tự chọn
        assert dlg.table.item(1, 5).text() == "Có gợi ý, chờ xác nhận"
        cls, grp, months = dlg.rows[1]
        grp.set_value("TB02")                     # chọn nhóm máy tính → phân loại tự đổi theo nhóm
        months.setValue(24)
        seen["cls"] = cls.value()
        if os.environ.get("QLHD_SHOT_FORM"):
            dlg.resize(1250, 760)
            dlg.show()
            qtbot.wait(100)
            dlg.grab().save(os.environ["QLHD_SHOT_FORM"])
        dlg.validate_extra()
        return QDialog.Accepted

    monkeypatch.setattr(InInvoiceDialog, "exec", run)
    assert page._save(values)
    assert seen["cls"] == dm.CCDC
    ma = ctx.db.one("SELECT Ma FROM HoaDonDauVao WHERE SoHoaDonGoc = '901'")["Ma"]
    saved = AssetService(ctx.db).lines_of(ma)
    assert [(x["PhanLoai"], x["MaNhomDM"], x["SoThang"], x["TuDong"]) for x in saved] == [
        (dm.CHI_PHI, "CP03", None, 1), (dm.CCDC, "TB02", 24, 0)]
    # sửa: mọi thông tin và phân loại trong một form, đổi phân loại phải xác nhận cũ → mới
    asked = []
    monkeypatch.setattr(widgets, "confirm_changes", lambda _p, ch, *a: asked.append(ch) or True)
    dlg = InInvoiceDialog(ctx, "Hóa đơn đầu vào", page._fields(), dict(ctx.db.get("HoaDonDauVao", ma)), page, ma=ma)
    qtbot.addWidget(dlg)
    cls, grp, months = dlg.rows[0]
    grp.set_value("TB01")
    dlg.widget("GhiChuHDDV").setText("đổi")
    dlg._accept()
    assert asked and any(label.startswith("Dòng 1") for label, _o, _n in asked[0])


def test_filter_box_and_bulk_edit_many_rows(qtbot, ctx, monkeypatch):
    from qlhd.ui import widgets
    from qlhd.ui.contracts_page import ContractsPage
    from qlhd.ui.filter_box import FilterBox
    from qlhd.ui.invoices_page import InvoicesPage
    from qlhd.ui.widgets import BulkEditDialog

    # ô tìm kiếm có bộ lọc thời gian theo năm, nhớ theo tài khoản
    page = InvoicesPage(ctx, "ra")
    qtbot.addWidget(page)
    page.refresh()
    n_all = page.table.rowCount()
    assert n_all >= 1
    page.box.set_mode("nam")
    year = page.table.rows[0]["NgayXuatHD"][:4] if page.table.rows else None
    page.box.period.set_value(f"{year}-01-01")
    assert page.table.rowCount() == n_all and page.box.active()
    page.box.clear()
    again = FilterBox(ctx, "hoa_don_ra", "", [("Số", "SoHoaDon")], date_key="NgayXuatHD")
    qtbot.addWidget(again)
    assert again.mode == "all"

    # chọn 2 hợp đồng, đổi tình trạng cùng lúc: chỉ ô được tích Đổi mới đổi, có xác nhận cũ → mới
    cp = ContractsPage(ctx)
    qtbot.addWidget(cp)
    cp.refresh()
    assert cp.table.rowCount() == 2
    cp.table.selectAll()
    seen = {}

    def fake_exec(dlg):
        seen["n"] = len(dlg.rows)
        dlg.boxes["TinhTrangHopDong"].setChecked(True)
        dlg.widgets["TinhTrangHopDong"].set_value("Hoàn thành")
        seen["changes"] = dlg.change_list()
        return widgets.QDialog.Accepted

    monkeypatch.setattr(BulkEditDialog, "exec", fake_exec)
    names_before = {h["Ma"]: h["TenHopDong"] for h in ctx.cs.contracts()}
    cp.edit()
    assert seen["n"] == 2 and seen["changes"][0][2] == "Hoàn thành"
    assert {h["TinhTrangHopDong"] for h in ctx.cs.contracts()} == {"Hoàn thành"}
    assert {h["Ma"]: h["TenHopDong"] for h in ctx.cs.contracts()} == names_before     # ô không tích giữ nguyên

    # hộp sửa nhiều dòng: chưa tích ô nào thì không lưu
    errs = []
    monkeypatch.setattr(widgets, "error", lambda _p, t, *a: errs.append(t))
    dlg = BulkEditDialog("x", [{"key": "GhiChu", "label": "Ghi chú"}], [{"GhiChu": "a"}, {"GhiChu": "b"}])
    qtbot.addWidget(dlg)
    dlg._accept()
    assert errs and dlg.result() == 0
