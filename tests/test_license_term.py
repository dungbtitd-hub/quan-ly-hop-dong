from datetime import date, timedelta

import pytest

from qlhd.core import licensing as lic
from qlhd.core.auth import AuthError
from tests.conftest import PASSWORD, Machine
from tests.test_sync import _send

TODAY = date.today()


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _director(tmp_path, vendor, han):
    priv, pub = vendor
    m = Machine(tmp_path, "gd", pub)
    m.acc.activate_director(lic.issue_director_key(priv, m.acc.activation_request(), "Cty", "0101", 3, han),
                            "giamdoc", "GD", PASSWORD)
    return m


def test_key_carries_term_and_cannot_be_issued_in_past(tmp_path, vendor):
    priv, _ = vendor
    req = Machine(tmp_path, "x", vendor[1]).acc.activation_request()
    with pytest.raises(lic.LicenseError):
        lic.issue_director_key(priv, req, "Cty", "0101", 3, _d(-1))
    p = lic.verify_director_key(lic.issue_director_key(priv, req, "Cty", "0101", 3, _d(365)), vendor_pub=vendor[1])
    assert lic.key_expiry(p) == _d(365) and lic.expiry_text(p).startswith("đến ")
    assert lic.expiry_text(lic.verify_director_key(lic.issue_director_key(priv, req, "C", "1", 1),
                                                   vendor_pub=vendor[1])) == "vĩnh viễn"


def test_status_warning_expiry_and_login_block(tmp_path, vendor):
    m = _director(tmp_path, vendor, _d(10))
    assert m.acc.license_status()["trang_thai"] == "sap_het"
    assert m.acc.license_status(TODAY - timedelta(days=0))["con_lai"] == 10
    m.acc.login("giamdoc", PASSWORD)
    assert m.acc.license_status(TODAY + timedelta(days=11))["trang_thai"] == "het_han"
    m.db.set_setting("ngay_lon_nhat", _d(11))          # máy đã chạy tới ngày sau hạn
    with pytest.raises(AuthError, match="lùi"):        # lùi đồng hồ về hôm nay cũng không qua
        m.acc.login("giamdoc", PASSWORD)


def test_director_renewal_keeps_data(tmp_path, vendor):
    priv, pub = vendor
    m = _director(tmp_path, vendor, _d(5))
    m.db.set_setting("ngay_lon_nhat", _d(6))
    st = m.acc.license_status(TODAY + timedelta(days=6))
    assert st["trang_thai"] == "het_han"
    req = m.acc.renewal_request()
    # Key cấp cho máy / công ty khác không gia hạn được
    other = Machine(tmp_path, "khac", pub)
    with pytest.raises((AuthError, lic.LicenseError)):
        m.acc.renew(lic.issue_director_key(priv, other.acc.activation_request(), "Cty", "0101", 3, _d(400)))
    m.acc.renew(lic.issue_director_key(priv, req, "Cty", "0101", 8, _d(400)), TODAY + timedelta(days=6))
    assert m.acc.license()["SoTaiKhoanToiDa"] == 8
    assert m.acc.license_status(TODAY + timedelta(days=6))["trang_thai"] == "con_han"
    m.db.set_setting("ngay_lon_nhat", TODAY.isoformat())
    m.acc.login("giamdoc", PASSWORD)


def test_member_machines_follow_director_renewal(tmp_path, vendor):
    priv, pub = vendor
    gd = _director(tmp_path, vendor, _d(20))
    gd.user = gd.acc.login("giamdoc", PASSWORD)
    kt = Machine(tmp_path, "kt", pub)
    kt.acc.activate_member(gd.acc.issue_code(ten_dang_nhap="kt", ho_ten="KT", vai_tro="Kế toán viên",
                                             machine_id=kt.id), PASSWORD)
    kt.user = kt.acc.login("kt", PASSWORD)
    assert kt.acc.license_status()["han"] == _d(20)
    gd.acc.renew(lic.issue_director_key(priv, gd.acc.renewal_request(), "Cty", "0101", 3, _d(500)))
    # cách 1: gói đồng bộ từ máy Giám đốc mang Key mới
    d, pkg = _send(gd, kt, tmp_path)
    res = d.apply(pkg, user=kt.user)
    assert res.get("gia_han") is True and kt.acc.license_status()["han"] == _d(500)
    # cách 2: dán Key Giám đốc hiện hành; Key của công ty khác bị từ chối
    other = Machine(tmp_path, "cty2", pub)
    with pytest.raises(AuthError):
        kt.acc.renew(lic.issue_director_key(priv, other.acc.activation_request(), "Cty 2", "0202", 3, _d(900)))
    assert kt.acc.renew(gd.acc.license()["KeyGiamDoc"])["han"] == _d(500)


def test_expired_key_cannot_activate_members(tmp_path, vendor):
    priv, pub = vendor
    gd = _director(tmp_path, vendor, _d(3))
    kt = Machine(tmp_path, "kt", pub)
    code = gd.acc.issue_code(ten_dang_nhap="kt", ho_ten="KT", vai_tro="Kế toán viên", machine_id=kt.id)
    import qlhd.core.licensing as L
    real = L.is_expired
    L.is_expired = lambda p, today=None: real(p, TODAY + timedelta(days=4))
    try:
        with pytest.raises(AuthError, match="hết hạn"):
            kt.acc.activate_member(code, PASSWORD)
    finally:
        L.is_expired = real
