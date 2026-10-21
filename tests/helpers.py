from qlhd.core.contracts import ContractService


def sample_data(db):
    cs = ContractService(db)
    loai = cs.save_contract_type("Tư vấn thiết kế")
    cdt = cs.save_partner({"TenDoiTac": "Ban QLDA Hà Nội", "MaSoThue": "0100000001", "VaiTroDoiTac": "Chủ đầu tư"})
    ncc = cs.save_partner({"TenDoiTac": "Công ty Vật tư X", "MaSoThue": "0200000002", "VaiTroDoiTac": "Nhà cung cấp"})
    hd = cs.save_contract({"SoHopDong": "01/2026/HĐTV", "TenHopDong": "Thiết kế trường học", "ChieuHopDong": "Đầu ra",
                           "MaLoaiHD": loai, "MaDoiTac": cdt, "GiaTriTruocThue": 100_000_000, "VAT": 10_000_000,
                           "NgayKy": "2026-01-15", "TinhTrangHopDong": "Đang thực hiện"})
    hdv = cs.save_contract({"SoHopDong": "05/2026/MB", "TenHopDong": "Mua vật tư", "ChieuHopDong": "Đầu vào",
                            "MaLoaiHD": loai, "MaDoiTac": ncc, "GiaTriTruocThue": 20_000_000, "VAT": 2_000_000,
                            "NgayKy": "2026-02-01"})
    inv = cs.save_out_invoice({"KyHieuHD": "C26TAA", "SoHoaDon": "12", "MaHopDong": hd, "NgayXuatHD": "2026-03-01",
                               "GiaTriTruocThue": 50_000_000, "VAT": 5_000_000})
    cs.save_payment({"MaHopDong": hd, "MaHDDauRa": inv, "NgayThanhToan": "2026-03-10", "SoTien": 30_000_000,
                     "HinhThuc": "Chuyển khoản", "SoThamChieuSK": "FT123"})
    cs.save_payment({"MaHopDong": hd, "NgayThanhToan": "2026-04-05", "SoTien": 5_000_000, "HinhThuc": "Tiền mặt"})
    cs.save_in_invoice({"KyHieuHD": "K26T", "SoHoaDonGoc": "88", "TenNhaCungCap": "Công ty Vật tư X",
                        "MaThueNCC": "0200000002", "NgayNhanHD": "2026-02-10", "GiaTriTruocThue": 10_000_000,
                        "VAT": 1_000_000, "MaHopDong": hdv})
    cs.save_in_invoice({"SoHoaDonGoc": "7", "TenNhaCungCap": "Cửa hàng Y", "MaThueNCC": "0300000003",
                        "NgayNhanHD": "2026-03-02", "GiaTriTruocThue": 1_000_000, "VAT": 80_000})
    nhom = cs.save_group("CĐT nhà nước", [cdt])
    return {"cs": cs, "loai": loai, "cdt": cdt, "ncc": ncc, "hd": hd, "hdv": hdv, "inv": inv, "nhom": nhom}
