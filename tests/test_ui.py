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
    from qlhd.ui.style import WIN7_QSS
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(WIN7_QSS)
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
