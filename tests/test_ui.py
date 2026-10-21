"""Kiểm thử giao diện ở chế độ không màn hình (QT_QPA_PLATFORM=offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qlhd.core import licensing as lic  # noqa: E402
from qlhd.core.finance import FinanceStore  # noqa: E402
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
    for key in ("loai_hd", "nhom_cdt", "hoa_don_ra", "thoi_gian"):
        rp.context.set_value(key)
        rp.preview()
        assert rp.table.rowCount() > 0
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
