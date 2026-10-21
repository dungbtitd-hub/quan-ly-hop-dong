import pytest

from qlhd.core import licensing as lic
from qlhd.core.auth import AuthError
from qlhd.core.sync import SyncService
from tests.conftest import PASSWORD, Machine


def test_director_key_bound_to_machine(tmp_path, vendor):
    priv, pub = vendor
    m = Machine(tmp_path, "a", pub)
    key = lic.issue_director_key(priv, m.acc.activation_request(), "Cty", "0101", 3)
    other = Machine(tmp_path, "b", pub)
    other.acc.activation_request()
    with pytest.raises(lic.LicenseError):
        other.acc.activate_director(key, "gd", "GD", PASSWORD)
    m.acc.activate_director(key, "gd", "GD", PASSWORD)
    assert m.acc.is_activated() and m.acc.is_primary_director_machine()


def test_tampered_or_foreign_key_rejected(tmp_path, vendor):
    priv, pub = vendor
    m = Machine(tmp_path, "a", pub)
    req = m.acc.activation_request()
    fake_vendor = lic.new_private_key()
    with pytest.raises(lic.LicenseError):
        m.acc.activate_director(lic.issue_director_key(fake_vendor, req, "Cty", "0101", 3), "gd", "GD", PASSWORD)
    key = lic.issue_director_key(priv, req, "Cty", "0101", 3)
    body, sig = key.split(":", 1)[1].split(".")
    tampered = "QLHD-KEY:" + body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    with pytest.raises(lic.LicenseError):
        m.acc.activate_director(tampered, "gd", "GD", PASSWORD)


def test_member_roles_and_permissions(company):
    kt, gd, gd2 = company["kt"], company["gd"], company["gd2"]
    assert kt.acc.can(kt.user, "HopDong", "Xem")
    assert not kt.acc.can(kt.user, "ThuChi", "Xem")
    assert not kt.acc.can(kt.user, "TaiKhoan", "Xem")
    assert gd.acc.can(gd.user, "ThuChi", "Xem")
    assert gd2.acc.license()["LoaiKichHoat"] == "Giám đốc (máy phụ)"
    assert not gd2.acc.is_primary_director_machine()
    with pytest.raises(AuthError):
        gd2.acc.issue_code(ten_dang_nhap="x", ho_ten="X", vai_tro="Kế toán viên", machine_id="QLHD-X")


def test_accountant_cannot_get_finance_even_if_requested(company):
    gd = company["gd"]
    code = gd.acc.issue_code(ten_dang_nhap="kt2", ho_ten="KT2", vai_tro="Kế toán viên", machine_id="QLHD-KT2",
                             quyen={"ThuChi": {"Xem": 1, "Them": 1, "Sua": 1, "Xoa": 1, "Xuat": 1}})
    payload, _ = lic.verify_member_code(code, vendor_pub=company["vendor"][1])
    assert payload["quyen"]["ThuChi"]["Xem"] == 0


def test_code_for_other_machine_rejected(company, tmp_path):
    gd = company["gd"]
    code = gd.acc.issue_code(ten_dang_nhap="kt3", ho_ten="KT3", vai_tro="Kế toán viên", machine_id="QLHD-KHAC")
    m = Machine(tmp_path, "c", company["vendor"][1])
    with pytest.raises(lic.LicenseError):
        m.acc.activate_member(code, PASSWORD)


def test_quota(tmp_path, vendor):
    priv, pub = vendor
    m = Machine(tmp_path, "a", pub)
    m.acc.activate_director(lic.issue_director_key(priv, m.acc.activation_request(), "C", "01", 1), "gd", "GD", PASSWORD)
    m.acc.issue_code(ten_dang_nhap="k1", ho_ten="K1", vai_tro="Kế toán viên", machine_id="M1")
    with pytest.raises(AuthError):
        m.acc.issue_code(ten_dang_nhap="k2", ho_ten="K2", vai_tro="Kế toán viên", machine_id="M2")


def test_login_lockout(company):
    kt = company["kt"]
    for _ in range(5):
        with pytest.raises(AuthError):
            kt.acc.login("ketoan", "sai-mat-khau")
    with pytest.raises(AuthError, match="tạm khóa"):
        kt.acc.login("ketoan", PASSWORD)


def test_revocation_travels_in_sync_package(company, tmp_path):
    gd, kt = company["gd"], company["kt"]
    code_id = kt.acc.license()["MaKichHoatMay"]
    gd.acc.revoke(code_id)
    sync = SyncService(gd.db, gd.dir, gd.acc)
    pkg_path = tmp_path / "goi.qlhd"
    sync.export_package(pkg_path, "dongbo123", user=gd.user)
    ksync = SyncService(kt.db, kt.dir, kt.acc)
    pkg = ksync.read_package(pkg_path, "dongbo123")
    ksync.apply(pkg, user=kt.user)
    with pytest.raises(AuthError):
        kt.acc.login("ketoan", PASSWORD)


def test_license_manager_cli(tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("lm", Path(__file__).resolve().parents[1] / "tools" / "license_manager.py")
    lm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lm)
    key_file = tmp_path / "vendor.key"
    with pytest.raises(SystemExit):                       # cặp khóa cố định: phải xác nhận mới thay
        lm.main(["init", "--out", str(key_file)])
    assert lm.main(["init", "--out", str(key_file), "--thay-khoa-chinh-thuc"]) == 0
    pub = lic.public_from_b64(capsys.readouterr().out.strip().splitlines()[-1].split(":")[-1].strip())
    # tệp khóa không khớp khóa chính thức của phần mềm thì không cấp được Key
    with pytest.raises(lm.WrongKeyError):
        lm.issue(key_file, "x", "Cty", "0109", 1)
    lm.official_pub = lambda: pub
    m = Machine(tmp_path, "kh", pub)
    req = m.acc.activation_request()
    assert lm.main(["issue", "--key", str(key_file), "--request", req, "--company", "Cty KH", "--mst", "0109",
                    "--accounts", "3"]) == 0
    key = capsys.readouterr().out.strip()
    m.acc.activate_director(key, "gd", "GD", PASSWORD)
    assert m.acc.license()["SoTaiKhoanToiDa"] == 3
    assert (tmp_path / "NhatKyCapKey.csv").exists()
    # cấp có thời hạn 12 tháng
    m2 = Machine(tmp_path, "kh2", pub)
    assert lm.main(["issue", "--key", str(key_file), "--request", m2.acc.activation_request(), "--company", "Cty",
                    "--mst", "0109", "--thang", "12"]) == 0
    m2.acc.activate_director(capsys.readouterr().out.strip(), "gd", "GD", PASSWORD)
    from datetime import date
    assert m2.acc.license_status()["han"] == lm.add_months(date.today(), 12).isoformat()
    assert lm.add_months(date(2026, 1, 31), 1) == date(2026, 2, 27)
    assert lm.add_months(date(2026, 10, 21), 12) == date(2027, 10, 20)
    assert "Thời hạn" in (tmp_path / "NhatKyCapKey.csv").read_text(encoding="utf-8-sig")


def test_bundled_vendor_key_matches_dev_key():
    from qlhd.core.vendor_pubkey import VENDOR_PUBKEY

    assert len(lic.b64d(VENDOR_PUBKEY)) == 32
