"""Phân loại, thiết lập làm ở bất kỳ máy nào phải đi theo khi đồng bộ qua máy trung gian."""
from qlhd.core import asset_catalog as dm
from qlhd.core.assets import AssetService
from tests.helpers import sample_data
from tests.test_sync import _send


def _line(m):
    AssetService(m.db).backfill()
    return m.db.one("SELECT * FROM DongHoaDonDauVao WHERE DaXoa = 0 ORDER BY Ma")


def _classify(m, ma):
    AssetService(m.db).set_line_class(ma, dm.CHI_PHI, "CP03", None)


def test_classification_relayed_through_staff_machine(company, tmp_path):
    gd, kt, gd2 = company["gd"], company["kt"], company["gd2"]
    sample_data(gd.db)
    _line(gd)
    for dst, name in ((kt, "a"), (gd2, "b")):
        d, pkg = _send(gd, dst, tmp_path, name + ".qlhd")
        d.apply(pkg, user=dst.user)
        _line(dst)
    d, pkg = _send(kt, gd2, tmp_path, "c.qlhd", scope="changes", partner=gd2.id)
    d.apply(pkg, user=gd2.user)
    ln = _line(gd)
    _classify(gd, ln["Ma"])
    d, pkg = _send(gd, kt, tmp_path, "d.qlhd", scope="changes", partner=kt.id)
    d.apply(pkg, user=kt.user)
    _line(kt)
    assert kt.db.get("DongHoaDonDauVao", ln["Ma"])["MaNhomDM"] == "CP03"
    d, pkg = _send(kt, gd2, tmp_path, "e.qlhd", scope="changes", partner=gd2.id)
    d.apply(pkg, user=gd2.user)
    _line(gd2)
    assert gd2.db.get("DongHoaDonDauVao", ln["Ma"])["MaNhomDM"] == "CP03"


def test_classification_relayed_after_staff_sent_earlier(company, tmp_path):
    """Máy nhân viên gửi máy GĐ2 trước khi nhận phân loại; lần gửi sau vẫn phải mang phân loại theo."""
    gd, kt, gd2 = company["gd"], company["kt"], company["gd2"]
    sample_data(gd.db)
    ln = _line(gd)
    d, pkg = _send(gd, kt, tmp_path, "a.qlhd")
    d.apply(pkg, user=kt.user)
    _classify(gd, ln["Ma"])
    d, pkg = _send(gd, kt, tmp_path, "b.qlhd", scope="changes", partner=kt.id)
    d0, pkg0 = _send(kt, gd2, tmp_path, "c.qlhd", scope="changes", partner=gd2.id)
    d0.apply(pkg0, user=gd2.user)
    d.apply(pkg, user=kt.user)
    d, pkg = _send(kt, gd2, tmp_path, "e.qlhd", scope="changes", partner=gd2.id)
    d.apply(pkg, user=gd2.user)
    assert gd2.db.get("DongHoaDonDauVao", ln["Ma"])["MaNhomDM"] == "CP03"


def test_classification_kept_when_lines_were_made_on_each_machine(company, tmp_path):
    """Hóa đơn sang máy khác trước khi có dòng: mỗi máy tự tạo dòng (mã khác nhau), phân loại tay vẫn phải sang."""
    gd, kt, gd2 = company["gd"], company["kt"], company["gd2"]
    sample_data(gd.db)
    for dst, name in ((kt, "a"), (gd2, "b")):
        d, pkg = _send(gd, dst, tmp_path, name + ".qlhd")
        d.apply(pkg, user=dst.user)
        dst.db.conn.execute("DELETE FROM DongHoaDonDauVao")        # dữ liệu cũ: máy nhận tự tạo dòng của mình
        dst.db.conn.commit()
        _line(dst)
    ln = _line(gd)
    assert kt.db.get("DongHoaDonDauVao", ln["Ma"]) is None
    _classify(gd, ln["Ma"])
    d, pkg = _send(gd, kt, tmp_path, "c.qlhd")
    d.apply(pkg, user=kt.user)
    d, pkg = _send(kt, gd2, tmp_path, "d.qlhd")
    d.apply(pkg, user=gd2.user)
    for m in (kt, gd2):
        row = m.db.one("SELECT * FROM DongHoaDonDauVao WHERE DaXoa = 0 AND MaHoaDon = ? AND STT = ?",
                       (ln["MaHoaDon"], ln["STT"]))
        assert (row["PhanLoai"], row["MaNhomDM"], row["TuDong"]) == (dm.CHI_PHI, "CP03", 0), m.id
