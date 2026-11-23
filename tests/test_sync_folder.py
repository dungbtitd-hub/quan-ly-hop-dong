"""Đồng bộ tự động qua thư mục dùng chung và quy tắc "người sửa thắng dữ liệu tự động"."""
import os
import time
from datetime import datetime, timedelta, timezone

from qlhd.core.sync import ST_CONFLICT, ST_MINE, ST_THEIRS, SyncService
from qlhd.core.sync_folder import K_CLEAN_DAYS, MARK_DIR, SyncFolder
from tests.helpers import sample_data

PW = "dongbo-2026"


def _folder(m, shared):
    f = SyncFolder(m.db, SyncService(m.db, m.dir, m.acc))
    f.save_settings({"thu_muc": str(shared), "mat_khau": PW}, director=m.user["VaiTro"] == "Giám đốc")
    return f


def _start(kt, gd, shared):
    """Hai máy bắt đầu từ cùng dữ liệu (chuyển toàn bộ bằng đồng bộ thủ công), rồi bật thư mục chung."""
    s = SyncService(kt.db, kt.dir, kt.acc)
    p = shared.parent / "toan_bo.qlhd"
    s.export_package(p, PW, user=kt.user)
    d = SyncService(gd.db, gd.dir, gd.acc)
    d.apply(d.read_package(p, PW), user=gd.user)
    return _folder(kt, shared), _folder(gd, shared)


def test_manual_edit_beats_auto_update(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    d0 = sample_data(kt.db)
    shared = tmp_path / "chung"
    shared.mkdir()
    fk, fg = _start(kt, gd, shared)
    # máy kế toán: người sửa trước; máy Giám đốc: phần mềm tự cập nhật sau (giờ sửa mới hơn)
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "kế toán đã rà soát"})
    time.sleep(0.01)
    with gd.db.auto():
        gd.db.update("HopDong", d0["hd"], {"GhiChu": "tự động"})
    s = SyncService(gd.db, gd.dir, gd.acc)
    p = fk.send_if_changed(kt.user)
    pkg = s.read_package(p, PW)
    hd = next(i for i in s.compare(pkg) if i.table == "HopDong" and i.ma == d0["hd"])
    assert hd.status == ST_THEIRS and "người sửa" in hd.note
    fg.receive(gd.user)
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "kế toán đã rà soát"
    # ngược lại: máy này có người sửa, bên gửi chỉ tự động → giữ máy này
    gd.db.update("HopDong", d0["hd"], {"GhiChu": "giám đốc sửa"})
    time.sleep(0.01)
    with kt.db.auto():
        kt.db.update("HopDong", d0["hd"], {"GhiChu": "kế toán tự động"})
    p = fk.send_if_changed(kt.user)
    hd = next(i for i in s.compare(s.read_package(p, PW)) if i.table == "HopDong" and i.ma == d0["hd"])
    assert hd.status == ST_MINE
    # cả hai cùng tự động → bản mới hơn, không hỏi
    with gd.db.auto():
        gd.db.update("HoaDonDauRa", d0["inv"], {"MatHang": "gd tự động"})
    time.sleep(0.01)
    with kt.db.auto():
        kt.db.update("HoaDonDauRa", d0["inv"], {"MatHang": "kt tự động"})
    with gd.db.auto(), kt.db.auto():
        kt.db.update("HoaDonDauRa", d0["inv"], {"SuaTay": None})
        gd.db.update("HoaDonDauRa", d0["inv"], {"SuaTay": None})
    p = fk.send_if_changed(kt.user)
    inv = next(i for i in s.compare(s.read_package(p, PW)) if i.table == "HoaDonDauRa" and i.ma == d0["inv"])
    assert inv.status != ST_CONFLICT and inv.action is not None


def test_both_manual_last_edit_wins_without_asking(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    d0 = sample_data(kt.db)
    shared = tmp_path / "chung"
    shared.mkdir()
    fk, fg = _start(kt, gd, shared)
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "kt"})
    gd.db.update("HopDong", d0["hd"], {"GhiChu": "gd"})
    fk.send_if_changed(kt.user)
    r = fg.receive(gd.user)
    assert r.nhan == 1 and r.cho_chon == 0
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "gd"          # máy GĐ sửa sau cùng: giữ, không hỏi
    assert not fg.pending_choices()
    fg.send_if_changed(gd.user)
    assert fk.receive(kt.user).cho_chon == 0
    assert kt.db.get("HopDong", d0["hd"])["GhiChu"] == "gd"          # máy kia tự nhận bản sửa sau cùng


def test_two_way_changes_only_skip_received_and_no_ping_pong(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    d0 = sample_data(kt.db)
    shared = tmp_path / "chung"
    shared.mkdir()
    fk, fg = _start(kt, gd, shared)
    assert fk.send_if_changed(kt.user) is None          # bật thư mục chung: không gửi toàn bộ dữ liệu
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "kế toán"})
    assert fk.send_if_changed(kt.user)
    assert fk.send_if_changed(kt.user) is None          # không có gì mới thì không gửi
    assert fg.receive(gd.user).nhan == 1
    assert fg.receive(gd.user).nhan == 0                # gói đã nhận thì bỏ qua
    assert fg.send_if_changed(gd.user) is None          # dữ liệu vừa nhận không gửi ngược lại
    gd.db.update("HopDong", d0["hd"], {"GhiChu": "giám đốc ghi chú"})
    p = fg.send_if_changed(gd.user)
    pkg = SyncService(kt.db, kt.dir, kt.acc).read_package(p, PW)
    assert pkg.header["pham_vi"].startswith("Các thay đổi")
    assert sum(len(v) for v in pkg.body["tables"].values()) < 5
    assert "finance" not in pkg.body
    assert fk.receive(kt.user).nhan == 1
    assert kt.db.get("HopDong", d0["hd"])["GhiChu"] == "giám đốc ghi chú"
    # gói tự động không xuất hiện trong danh sách "máy nhận" của đồng bộ thủ công
    assert all(p["MaMay"] != "__thu_muc_chung__" for p in SyncService(kt.db, kt.dir, kt.acc).partners())


def test_cleanup_only_old_and_received(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    d0 = sample_data(kt.db)
    shared = tmp_path / "chung"
    shared.mkdir()
    fk, fg = _start(kt, gd, shared)
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "kế toán"})
    p = fk.send_if_changed(kt.user)
    # quá hạn nhưng máy Giám đốc chưa nhận: không xóa
    old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    raw = p.read_bytes()
    h = SyncService.read_header(p)
    assert fk.cleanup() == 0 and p.exists()
    fg.receive(gd.user)
    assert (shared / MARK_DIR).is_dir()
    assert fg.cleanup() == 0 and p.exists()             # mới 0 ngày: chưa đến hạn dọn
    gd.db.set_setting(K_CLEAN_DAYS, "0")
    assert fg.cleanup() == 0
    gd.db.set_setting(K_CLEAN_DAYS, "3")
    # giả lập gói cũ 5 ngày: đổi thời điểm trong lịch sử nhận và trong header không được (gói đã ký), nên dùng
    # mốc dọn: đặt ngày dọn = 0 thì tắt, còn kiểm tra quá hạn bằng cách lùi đồng hồ của hàm dọn
    import qlhd.core.sync_folder as sf
    real = sf.datetime

    class Later(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(days=4)
    sf.datetime = Later
    try:
        assert fg.cleanup() == 1
    finally:
        sf.datetime = real
    assert not p.exists()
    assert old and raw and h
    assert not list((shared / MARK_DIR).glob("*.ok"))


def test_partial_file_ignored(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    shared = tmp_path / "chung"
    shared.mkdir()
    (shared / "DongBo_dang_ghi.qlhd").write_bytes(b"QLHDSYNC1\n\x00")
    fg = _folder(gd, shared)
    assert fg.receive(gd.user).nhan == 0
    os.remove(shared / "DongBo_dang_ghi.qlhd")


def test_new_packages_seen_quickly_and_receive_again_keeps_newest(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    d0 = sample_data(kt.db)
    shared = tmp_path / "chung"
    shared.mkdir()
    fk, fg = _start(kt, gd, shared)
    assert not fg.has_new()
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "lần 1"})
    assert fk.send_if_changed(kt.user)
    assert fg.has_new()                                   # kiểm tra nhanh thấy gói mới
    assert fg.receive(gd.user).nhan == 1
    assert not fg.has_new()
    time.sleep(1.1)
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "lần 2"})
    assert fk.send_if_changed(kt.user)
    assert fg.has_new() and fg.receive(gd.user).nhan == 1  # lần sửa sau vẫn nhận được
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "lần 2"
    # quét lại toàn bộ: đọc lại cả 2 gói, bản cũ ("lần 1") không đè bản mới
    r = fg.receive(gd.user, again=True)
    assert r.nhan == 2 and not r.loi
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "lần 2"
    # máy nhận sửa sau đó: quét lại không đè chỗ người dùng máy này vừa sửa
    time.sleep(1.1)
    gd.db.update("HopDong", d0["hd"], {"GhiChu": "giám đốc sửa"})
    fg.receive(gd.user, again=True)
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "giám đốc sửa"
    assert fg.receive(gd.user).nhan == 0                  # quét lại không làm mất dấu "đã nhận"


def test_unreachable_folder_is_reported(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    sample_data(kt.db)
    shared = tmp_path / "chung"
    shared.mkdir()
    fk, fg = _start(kt, gd, shared)
    shared.rename(tmp_path / "doi_ten")
    r = fg.receive(gd.user)
    assert r.loi and "Không đọc được thư mục đồng bộ chung" in r.loi[0]
    assert "Không đọc được" in fg.last_error()
    assert not fg.has_new()
