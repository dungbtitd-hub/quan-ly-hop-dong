import pytest

from qlhd.core.contracts import ContractService
from qlhd.core.finance import FinanceStore, install_wrappers
from qlhd.core.sync import (A_BOTH, ST_CONFLICT, ST_DUP, ST_MINE, ST_NEW, ST_SAME, ST_THEIRS, SyncError,
                            SyncService)
from tests.helpers import sample_data

PW = "dongbo-2026"


def _send(src, dst, tmp_path, name="goi.qlhd", **kw):
    s = SyncService(src.db, src.dir, src.acc)
    p = tmp_path / name
    s.export_package(p, PW, user=src.user, **kw)
    d = SyncService(dst.db, dst.dir, dst.acc)
    return d, d.read_package(p, PW)


def test_full_roundtrip_and_identical(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    sample_data(kt.db)
    d, pkg = _send(kt, gd, tmp_path)
    items = d.compare(pkg)
    assert {i.status for i in items} == {ST_NEW}
    res = d.apply(pkg, user=gd.user)
    assert res["them"] == len(items) and res["loi"] == 0
    d, pkg = _send(kt, gd, tmp_path, "goi2.qlhd")
    assert {i.status for i in d.compare(pkg)} == {ST_SAME}
    for t in ("HopDong", "HoaDonDauRa", "ThanhToanHopDong", "HoaDonDauVao"):
        a = [dict(r) for r in kt.db.query(f"SELECT * FROM {t} ORDER BY Ma")]
        b = [dict(r) for r in gd.db.query(f"SELECT * FROM {t} ORDER BY Ma")]
        assert a == b


def test_newer_mine_conflict(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    d0 = sample_data(kt.db)
    d, pkg = _send(kt, gd, tmp_path)
    d.apply(pkg, user=gd.user)
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "kế toán sửa"})
    d, pkg = _send(kt, gd, tmp_path, "g2.qlhd")
    hd = next(i for i in d.compare(pkg) if i.table == "HopDong" and i.ma == d0["hd"])
    assert hd.status == ST_THEIRS and hd.diff == ["GhiChu"]
    d.apply(pkg, user=gd.user)
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "kế toán sửa"
    gd.db.update("HopDong", d0["hd"], {"GhiChu": "giám đốc sửa"})
    d, pkg = _send(kt, gd, tmp_path, "g3.qlhd")
    hd = next(i for i in d.compare(pkg) if i.table == "HopDong" and i.ma == d0["hd"])
    assert hd.status == ST_MINE
    gd.db.update("HopDong", d0["hd"], {"TinhTrangHopDong": "Hoàn thành"})
    kt.db.update("HopDong", d0["hd"], {"GhiChu": "kế toán sửa lần 2"})
    d, pkg = _send(kt, gd, tmp_path, "g4.qlhd")
    hd = next(i for i in d.compare(pkg) if i.table == "HopDong" and i.ma == d0["hd"])
    assert hd.status == ST_CONFLICT and hd.action is None
    res = d.apply(pkg, user=gd.user)
    assert res["chua_xu_ly"] == 1
    d.apply(pkg, user=gd.user, prefer="gui")
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "kế toán sửa lần 2"
    d.undo_last(gd.user)
    assert gd.db.get("HopDong", d0["hd"])["GhiChu"] == "giám đốc sửa"


def test_duplicates_merge_and_keep_both(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    a = sample_data(kt.db)
    b = sample_data(gd.db)
    d, pkg = _send(kt, gd, tmp_path)
    items = d.compare(pkg)
    hd = next(i for i in items if i.table == "HopDong" and i.their_ma == a["hd"])
    assert hd.status == ST_DUP and hd.dup_ma == b["hd"]
    pay = [i for i in items if i.table == "ThanhToanHopDong"]
    assert all(i.status == ST_DUP for i in pay)
    res = d.apply(pkg, user=gd.user)
    assert res["them"] == 0 and res["loi"] == 0
    assert len(ContractService(gd.db).contracts()) == 2
    kt.db.update("HopDong", a["hd"], {"GhiChu": "sửa sau khi gộp"})
    d, pkg = _send(kt, gd, tmp_path, "g2.qlhd")
    hd = next(i for i in d.compare(pkg) if i.table == "HopDong" and i.their_ma == a["hd"])
    assert hd.ma == b["hd"] and hd.status == ST_THEIRS
    d.apply(pkg, user=gd.user)
    assert gd.db.get("HopDong", b["hd"])["GhiChu"] == "sửa sau khi gộp"


def test_keep_both_and_selected_contracts(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    a = sample_data(kt.db)
    sample_data(gd.db)
    d, pkg = _send(kt, gd, tmp_path, scope="contracts", contract_ids=[a["hd"]])
    assert not pkg.body["tables"]["HoaDonDauVao"] or all(r["MaHopDong"] == a["hd"]
                                                         for r in pkg.body["tables"]["HoaDonDauVao"])
    d.apply(pkg, user=gd.user, decisions={("HopDong", a["hd"]): A_BOTH})
    assert gd.db.get("HopDong", a["hd"]) is not None


def test_wrong_password_other_company_self(company, tmp_path, vendor):
    kt, gd = company["kt"], company["gd"]
    s = SyncService(kt.db, kt.dir, kt.acc)
    p = tmp_path / "x.qlhd"
    s.export_package(p, PW, user=kt.user)
    with pytest.raises(SyncError, match="Sai mật khẩu"):
        SyncService(gd.db, gd.dir, gd.acc).read_package(p, "sai-mat-khau")
    with pytest.raises(SyncError, match="chính máy này"):
        s.read_package(p, PW)
    raw = bytearray(p.read_bytes())
    raw[-5] ^= 0xFF
    p.write_bytes(bytes(raw))
    with pytest.raises(SyncError):
        SyncService(gd.db, gd.dir, gd.acc).read_package(p, PW)


def test_normal_package_never_has_finance(company, tmp_path):
    kt, gd = company["kt"], company["gd"]
    FinanceStore.initialize(gd.db, gd.finance_path, gd.user, "khoa-thu-chi-1", iterations=1000)
    d, pkg = _send(gd, kt, tmp_path)
    assert "finance" not in pkg.body and not pkg.is_director
    store = FinanceStore.unlock(gd.db, gd.finance_path, gd.user, "khoa-thu-chi-1")
    with pytest.raises(SyncError):
        SyncService(kt.db, kt.dir, kt.acc).export_package(tmp_path / "y.qlhd", PW, user=kt.user, finance=store)


def test_director_package_to_second_director_machine(company, tmp_path):
    gd, gd2 = company["gd"], company["gd2"]
    sample_data(gd.db)
    store, _ = FinanceStore.initialize(gd.db, gd.finance_path, gd.user, "khoa-thu-chi-1", iterations=1000)
    cat = store.category_tree("Chi")[1][0]
    store.add_voucher({"NgayPhatSinh": "2026-03-01", "LoaiThuChi": "Chi", "PhanLoaiChi": "Văn phòng",
                       "MaKhoanMuc": cat["Ma"], "MaQuy": store.default_fund("Tiền mặt"), "SoTien": 500_000,
                       "NoiDung": "Văn phòng phẩm"})
    d, pkg = _send(gd, gd2, tmp_path, finance=store)
    assert pkg.is_director
    assert "Văn phòng phẩm".encode() not in (tmp_path / "goi.qlhd").read_bytes()
    d.apply(pkg, user=gd2.user)
    install_wrappers(gd2.db, pkg.body["finance"]["wrappers"])
    with pytest.raises(Exception):
        FinanceStore.unlock(gd2.db, gd2.finance_path, gd2.user, "sai-khoa-123")
    gd2.db.set_setting("tc_sai:giamdoc", None)
    store2 = FinanceStore.unlock(gd2.db, gd2.finance_path, gd2.user, "khoa-thu-chi-1")
    res = d.apply_finance(pkg, store2)
    assert res["them"] > 0
    assert store2.vouchers()[0]["NoiDung"] == "Văn phòng phẩm"
    assert store2.totals("2026-01-01", "2026-12-31")["thu_hop_dong"] == 35_000_000
    # xóa ở máy chính lan sang máy phụ
    store.delete_vouchers([v["Ma"] for v in store.vouchers()])
    d, pkg = _send(gd, gd2, tmp_path, "g2.qlhd", finance=store)
    d.apply(pkg, user=gd2.user)
    d.apply_finance(pkg, store2)
    assert store2.vouchers() == []


def test_suspected_invoice_duplicates_and_fill_on_merge(company, tmp_path):
    from qlhd.core.sync import A_MERGE
    kt, gd = company["kt"], company["gd"]
    sample_data(kt.db)
    inv_kt = kt.db.one("SELECT Ma FROM HoaDonDauVao WHERE SoHoaDonGoc = '88'")["Ma"]
    kt.db.update("HoaDonDauVao", inv_kt, {"GhiChuHDDV": "kế toán ghi", "NguoiMuaHang": "Lan"})
    cs = ContractService(gd.db)
    # máy nhận nhập cùng hóa đơn nhưng thiếu ký hiệu, số có số 0 đầu, chưa gắn hợp đồng
    mine = cs.save_in_invoice({"SoHoaDonGoc": "0088", "TenNhaCungCap": "Công ty Vật tư X", "MaThueNCC": "0200000002",
                               "NgayNhanHD": "2026-02-10", "GiaTriTruocThue": 10_000_000, "VAT": 1_000_000,
                               "GhiChuHDDV": "máy GĐ ghi"})
    # khác số hóa đơn nhưng cùng người bán, cùng tiền, cùng ngày: chỉ nghi ngờ, để người dùng chọn
    other = cs.save_in_invoice({"SoHoaDonGoc": "999", "TenNhaCungCap": "Cửa hàng Y", "MaThueNCC": "0300000003",
                                "NgayNhanHD": "2026-03-02", "GiaTriTruocThue": 1_000_000, "VAT": 80_000})
    d, pkg = _send(kt, gd, tmp_path)
    items = {i.their_ma: i for i in d.compare(pkg) if i.table == "HoaDonDauVao"}
    strong = items[inv_kt]
    assert strong.status == ST_DUP and strong.dup_ma == mine and strong.action == A_MERGE
    assert "cùng số hóa đơn" in strong.note and "cùng MST người bán" in strong.note
    assert {"KyHieuHD", "MaHopDong", "NguoiMuaHang"} <= set(strong.fill) and "GhiChuHDDV" not in strong.fill
    assert "GhiChuHDDV" in strong.diff
    weak = next(i for i in items.values() if i.their_ma != inv_kt)
    assert weak.status == ST_DUP and weak.dup_ma == other and weak.action is None
    res = d.apply(pkg, user=gd.user)
    row = gd.db.get("HoaDonDauVao", mine)
    assert row["SoHoaDonGoc"] == "0088" and row["GhiChuHDDV"] == "máy GĐ ghi"      # giữ nguyên hóa đơn máy này
    assert row["KyHieuHD"] == "K26T" and row["NguoiMuaHang"] == "Lan" and row["MaHopDong"]   # bổ sung ô trống
    assert res["bo_sung"] >= 1 and res["chua_xu_ly"] >= 1
    assert gd.db.one("SELECT COUNT(*) n FROM HoaDonDauVao WHERE DaXoa = 0")["n"] == 2
    # chọn giữ cả hai cho dòng nghi ngờ
    d, pkg = _send(kt, gd, tmp_path, "g2.qlhd")
    weak = next(i for i in d.compare(pkg) if i.table == "HoaDonDauVao" and i.status == ST_DUP)
    d.apply(pkg, user=gd.user, decisions={("HoaDonDauVao", weak.their_ma): A_BOTH})
    assert gd.db.one("SELECT COUNT(*) n FROM HoaDonDauVao WHERE DaXoa = 0")["n"] == 3


def test_sync_all_carries_shared_settings_not_local_ones(company, tmp_path):
    gd, kt = company["gd"], company["kt"]
    gd.db.set_setting("nhac_het_han_truoc", "45")
    gd.db.set_setting("mau_bao_cao:Quý", '{"context": "hop_dong"}')
    gd.db.set_setting("tg_token", "bi-mat")
    gd.db.set_setting("ten_may", "May GD")
    kt.db.set_setting("mau_bao_cao:Cũ", '{"context": "loai_hd"}')
    d, pkg = _send(gd, kt, tmp_path)
    d.apply(pkg, user=kt.user, only_groups={"x"})                   # đồng bộ hợp đồng đã tích: không nhận cài đặt
    assert kt.db.setting("nhac_het_han_truoc") is None
    res = d.apply(pkg, user=kt.user)
    assert res["cai_dat"] >= 2
    assert kt.db.setting("nhac_het_han_truoc") == "45" and kt.db.setting("mau_bao_cao:Quý")
    assert kt.db.setting("tg_token") is None and kt.db.setting("ten_may") != "May GD"
    assert kt.db.setting("mau_bao_cao:Cũ")                            # cài đặt riêng của máy nhận vẫn còn
    # xóa ở máy gửi (sau) thì máy nhận cũng xóa theo; máy nhận sửa sau thì giữ của máy nhận
    gd.db.set_setting("mau_bao_cao:Quý", None)
    kt.db.set_setting("nhac_het_han_truoc", "20")
    d, pkg = _send(gd, kt, tmp_path, "goi2.qlhd")
    d.apply(pkg, user=kt.user)
    assert kt.db.setting("mau_bao_cao:Quý") is None
    assert kt.db.setting("nhac_het_han_truoc") == "20"
