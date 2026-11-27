import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qlhd.core import licensing as lic  # noqa: E402
from qlhd.core.auth import AccountService  # noqa: E402
from qlhd.core.db import Database  # noqa: E402

PASSWORD = "matkhau123"


class Machine:
    def __init__(self, root: Path, name: str, vendor_pub):
        self.dir = root / name
        self.dir.mkdir()
        self.id = "QLHD-" + name.upper()
        self.db = Database(self.dir / "QuanLyHopDong.db", self.id)
        self.acc = AccountService(self.db, self.id, vendor_pub)
        self.user = None

    @property
    def finance_path(self):
        return self.dir / "ThuChi.dat"


@pytest.fixture
def vendor():
    priv = lic.new_private_key()
    return priv, priv.public_key()


@pytest.fixture
def company(tmp_path, vendor):
    """Máy Giám đốc chính, máy kế toán và máy Giám đốc thứ hai đã kích hoạt."""
    priv, pub = vendor
    gd = Machine(tmp_path, "giamdoc", pub)
    key = lic.issue_director_key(priv, gd.acc.activation_request(), "Công ty ABC", "0101234567", 5)
    gd.acc.activate_director(key, "giamdoc", "Nguyễn Văn A", PASSWORD)
    gd.user = gd.acc.login("giamdoc", PASSWORD)

    kt = Machine(tmp_path, "ketoan", pub)
    code = gd.acc.issue_code(ten_dang_nhap="ketoan", ho_ten="Trần Thị B", vai_tro="Kế toán viên", machine_id=kt.id)
    kt.acc.activate_member(code, PASSWORD)
    kt.user = kt.acc.login("ketoan", PASSWORD)

    gd2 = Machine(tmp_path, "giamdoc2", pub)
    code = gd.acc.issue_code(ten_dang_nhap="giamdoc", ho_ten="Nguyễn Văn A", vai_tro="Giám đốc", machine_id=gd2.id)
    gd2.acc.activate_member(code, PASSWORD)
    gd2.user = gd2.acc.login("giamdoc", PASSWORD)
    return {"gd": gd, "kt": kt, "gd2": gd2, "vendor": vendor}
