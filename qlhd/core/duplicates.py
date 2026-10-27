"""Kiểm soát nhập trùng: khi thêm mới, tìm bản ghi đã có giống hoặc gần giống (cùng số tiền, ngày, số tham chiếu…).
Có bản ghi giống thì không tự thêm: phần mềm hiện danh sách để nhân viên rà soát, xác nhận mới được thêm."""
from __future__ import annotations

import json

class DuplicateWarning(Exception):
    """Bản ghi định thêm giống bản ghi đã có: cần nhân viên rà soát, xác nhận mới thêm.
    Lớp con ở contracts / finance để các chỗ bắt lỗi nghiệp vụ cũ vẫn bắt được (không thêm, báo lại)."""

    def __init__(self, what: str, matches: list[str]):
        self.what, self.matches = what, list(matches)
        super().__init__(f"{what} nghi trùng với dữ liệu đã có, chưa thêm:\n" + "\n".join(f"• {m}" for m in matches))


def fmt_date(s) -> str:
    d = str(s or "")[:10]
    return f"{d[8:]}/{d[5:7]}/{d[:4]}" if len(d) == 10 else d


def fmt_money(v: int) -> str:
    return f"{int(v or 0):,}".replace(",", ".")

IGNORE_PREFIX = "khong_trung:"      # nhóm đã rà soát, đánh dấu "không trùng" (theo loại dữ liệu)
CONFIRM_KEY = "_DaRaSoatTrung"      # đặt True trong values khi nhân viên đã rà soát và xác nhận không phải trùng
LIMIT = 8


def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def _digits(s) -> str:
    d = "".join(ch for ch in str(s or "") if ch.isdigit()).lstrip("0")
    return d or ""


def _money(v) -> str:
    return f"{fmt_money(int(v or 0))} đ"


def signature(ids) -> str:
    return ",".join(sorted(ids))


def ignored(db, kind: str) -> set[str]:
    try:
        return set(json.loads(db.setting(IGNORE_PREFIX + kind) or "[]"))
    except ValueError:
        return set()


def ignored_ids(db, kind: str) -> set[str]:
    """Mọi bản ghi thuộc nhóm đã đánh dấu không trùng (để cảnh báo, tính tạm ứng bỏ qua)."""
    return {m for sig in ignored(db, kind) for m in sig.split(",") if m}


def set_ignored(db, kind: str, keys, on: bool = True) -> None:
    cur = ignored(db, kind)
    cur = cur | set(keys) if on else cur - set(keys)
    db.set_setting(IGNORE_PREFIX + kind, json.dumps(sorted(cur)) if cur else None)


def payments(db, v: dict, ma: str | None = None) -> list[str]:
    """Lần thanh toán: cùng số tham chiếu sao kê; hoặc cùng hợp đồng + ngày tiền về + số tiền."""
    out = []
    ref = _norm(v.get("SoThamChieuSK"))
    for r in db.query("SELECT t.*, h.SoHopDong FROM ThanhToanHopDong t JOIN HopDong h ON h.Ma = t.MaHopDong"
                      " WHERE t.DaXoa = 0 AND t.Ma != ? AND (t.MaHopDong = ? OR COALESCE(t.SoThamChieuSK, '') != '')",
                      (ma or "", v.get("MaHopDong") or "")):
        why = []
        if ref and _norm(r["SoThamChieuSK"]) == ref:
            why.append("cùng số tham chiếu sao kê")
        if r["MaHopDong"] == v.get("MaHopDong") and (r["NgayThanhToan"] or "")[:10] == (v.get("NgayThanhToan") or "")[:10] \
                and int(r["SoTien"] or 0) == int(v.get("SoTien") or 0):
            why.append("cùng hợp đồng, ngày tiền về và số tiền")
        if why:
            out.append(f"Thanh toán HĐ {r['SoHopDong']} ngày {fmt_date(r['NgayThanhToan'])}: {_money(r['SoTien'])}"
                       + (f", sao kê {r['SoThamChieuSK']}" if r["SoThamChieuSK"] else "") + " – " + ", ".join(why))
    return out[:LIMIT]


def overpaid(db, v: dict, ma: str | None = None) -> list[str]:
    """Lần thanh toán làm số đã thu vượt giá trị hóa đơn liên quan hoặc vượt giá trị hợp đồng (thường do ghi 2 lần)."""
    out = []
    amount = int(v.get("SoTien") or 0)
    if v.get("MaHDDauRa"):
        inv = db.one("SELECT SoHoaDon, GiaTriSauThue FROM HoaDonDauRa WHERE Ma = ?", (v["MaHDDauRa"],))
        da = db.one("SELECT COALESCE(SUM(SoTien), 0) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND MaHDDauRa = ?"
                    " AND Ma != ?", (v["MaHDDauRa"], ma or ""))["s"]
        if inv and da + amount > int(inv["GiaTriSauThue"] or 0):
            out.append(f"Thu thừa: hóa đơn số {inv['SoHoaDon']} giá trị {_money(inv['GiaTriSauThue'])}, đã thu "
                       f"{_money(da)}, thêm lần này thành {_money(da + amount)}")
    hd = db.one("SELECT SoHopDong, GiaTriTongSauThue FROM HopDong WHERE Ma = ?", (v.get("MaHopDong") or "",))
    if hd and int(hd["GiaTriTongSauThue"] or 0) > 0:
        da = db.one("SELECT COALESCE(SUM(SoTien), 0) s FROM ThanhToanHopDong WHERE DaXoa = 0 AND MaHopDong = ?"
                    " AND Ma != ?", (v["MaHopDong"], ma or ""))["s"]
        if da + amount > int(hd["GiaTriTongSauThue"]):
            out.append(f"Thu thừa: hợp đồng {hd['SoHopDong']} giá trị {_money(hd['GiaTriTongSauThue'])}, đã thu "
                       f"{_money(da)}, thêm lần này thành {_money(da + amount)}")
    return out


def payment_checks(db, v: dict, ma: str | None = None) -> list[str]:
    return payments(db, v, ma) + overpaid(db, v, ma)


def overpaid_items(db) -> list[dict]:
    """Hóa đơn đầu ra / hợp đồng đã thu nhiều hơn giá trị (tiền thu vào > giá trị hóa đơn hoặc hợp đồng)."""
    out = []
    for r in db.query(
            "SELECT i.Ma, i.SoHoaDon, i.GiaTriSauThue, i.MaHopDong, h.SoHopDong,"
            " (SELECT SUM(SoTien) FROM ThanhToanHopDong p WHERE p.DaXoa = 0 AND p.MaHDDauRa = i.Ma) Da"
            " FROM HoaDonDauRa i JOIN HopDong h ON h.Ma = i.MaHopDong WHERE i.DaXoa = 0 AND h.DaXoa = 0"
            " AND i.TinhChatHD != 'Đã hủy'"):
        if int(r["Da"] or 0) > int(r["GiaTriSauThue"] or 0):
            out.append({"loai": "hoa_don", "MaHopDong": r["MaHopDong"], "SoHopDong": r["SoHopDong"], "MaHDDauRa": r["Ma"],
                        "nhan": f"Hóa đơn {r['SoHoaDon']}", "GiaTri": int(r["GiaTriSauThue"] or 0),
                        "Da": int(r["Da"])})
    for r in db.query(
            "SELECT h.Ma, h.SoHopDong, h.GiaTriTongSauThue,"
            " (SELECT SUM(SoTien) FROM ThanhToanHopDong p WHERE p.DaXoa = 0 AND p.MaHopDong = h.Ma) Da"
            " FROM HopDong h WHERE h.DaXoa = 0 AND h.ChieuHopDong = 'Đầu ra'"):
        if int(r["GiaTriTongSauThue"] or 0) > 0 and int(r["Da"] or 0) > int(r["GiaTriTongSauThue"]):
            out.append({"loai": "hop_dong", "MaHopDong": r["Ma"], "SoHopDong": r["SoHopDong"],
                        "nhan": f"Hợp đồng {r['SoHopDong']}", "GiaTri": int(r["GiaTriTongSauThue"]), "Da": int(r["Da"])})
    return out


def out_invoices(db, v: dict, ma: str | None = None) -> list[str]:
    """Hóa đơn đầu ra: cùng số (khác ký hiệu) và số tiền; hoặc cùng hợp đồng, ngày lập, số tiền."""
    out = []
    so = _digits(v.get("SoHoaDon"))
    total = int(v.get("GiaTriSauThue") or 0)
    for r in db.query("SELECT * FROM HoaDonDauRa WHERE DaXoa = 0 AND Ma != ? AND TinhChatHD != 'Đã hủy'", (ma or "",)):
        why = []
        same_total = total and int(r["GiaTriSauThue"] or 0) == total
        if so and _digits(r["SoHoaDon"]) == so and same_total:
            why.append("cùng số hóa đơn và số tiền")
        if r["MaHopDong"] == v.get("MaHopDong") and same_total \
                and (r["NgayXuatHD"] or "")[:10] == (v.get("NgayXuatHD") or "")[:10]:
            why.append("cùng hợp đồng, ngày lập và số tiền")
        if why:
            out.append(f"Hóa đơn đầu ra {r['KyHieuHD'] or ''} số {r['SoHoaDon']} ngày {fmt_date(r['NgayXuatHD'])}: "
                       f"{_money(r['GiaTriSauThue'])} – " + ", ".join(why))
    return out[:LIMIT]


def in_invoices(db, v: dict, ma: str | None = None) -> list[str]:
    """Hóa đơn đầu vào: cùng người bán + số (khác ký hiệu); hoặc cùng người bán, ngày và số tiền."""
    out = []
    so = _digits(v.get("SoHoaDonGoc"))
    mst = (v.get("MaThueNCC") or "").strip()
    total = int(v.get("GiaTriSauThue") or 0)
    if not mst:
        return out
    for r in db.query("SELECT * FROM HoaDonDauVao WHERE DaXoa = 0 AND Ma != ? AND MaThueNCC = ?", (ma or "", mst)):
        why = []
        if so and _digits(r["SoHoaDonGoc"]) == so:
            why.append("cùng người bán và số hóa đơn")
        if total and int(r["GiaTriSauThue"] or 0) == total \
                and (r["NgayNhanHD"] or "")[:10] == (v.get("NgayNhanHD") or "")[:10]:
            why.append("cùng người bán, ngày và số tiền")
        if why:
            out.append(f"Hóa đơn đầu vào {r['KyHieuHD'] or ''} số {r['SoHoaDonGoc']} của {r['TenNhaCungCap']} ngày "
                       f"{fmt_date(r['NgayNhanHD'])}: {_money(r['GiaTriSauThue'])} – " + ", ".join(why))
    return out[:LIMIT]


def contracts(db, v: dict, ma: str | None = None) -> list[str]:
    """Hợp đồng (khác số): cùng đối tác và tên; hoặc cùng đối tác, ngày ký và giá trị."""
    out = []
    name = _norm(v.get("TenHopDong"))
    value = int(v.get("GiaTriTongSauThue") or 0)
    if not v.get("MaDoiTac"):
        return out
    for r in db.query("SELECT * FROM HopDong WHERE DaXoa = 0 AND Ma != ? AND MaDoiTac = ?", (ma or "", v["MaDoiTac"])):
        why = []
        if name and _norm(r["TenHopDong"]) == name:
            why.append("cùng đối tác và tên hợp đồng")
        if value and int(r["GiaTriTongSauThue"] or 0) == value and (r["NgayKy"] or "")[:10] == (v.get("NgayKy") or "")[:10]:
            why.append("cùng đối tác, ngày ký và giá trị")
        if why:
            out.append(f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']} (ký {fmt_date(r['NgayKy'])}, "
                       f"{_money(r['GiaTriTongSauThue'])}) – " + ", ".join(why))
    return out[:LIMIT]


def partners(db, v: dict, ma: str | None = None) -> list[str]:
    """Đối tác: cùng tên (MST trùng đã bị chặn riêng)."""
    name = _norm(v.get("TenDoiTac"))
    if not name:
        return []
    return [f"Đối tác {r['TenDoiTac']}" + (f" – MST {r['MaSoThue']}" if r["MaSoThue"] else "") + " – cùng tên"
            for r in db.query("SELECT * FROM DoiTac WHERE DaXoa = 0 AND Ma != ?", (ma or "",))
            if _norm(r["TenDoiTac"]) == name][:LIMIT]


def tasks(db, v: dict, ma: str | None = None) -> list[str]:
    """Công việc: cùng hợp đồng và cùng nội dung."""
    name = _norm(v.get("TenNoiDungCV"))
    if not name or not v.get("MaHopDong"):
        return []
    return [f"Công việc \"{r['TenNoiDungCV']}\" (hạn {fmt_date(r['NgayDuKienHT'])}) – cùng hợp đồng và nội dung"
            for r in db.query("SELECT * FROM CongViec WHERE DaXoa = 0 AND Ma != ? AND MaHopDong = ?",
                              (ma or "", v["MaHopDong"]))
            if _norm(r["TenNoiDungCV"]) == name][:LIMIT]


def vouchers(fdb, v: dict, ma: str | None = None) -> list[str]:
    """Phiếu thu chi: cùng loại, ngày, số tiền và (cùng tham chiếu hoặc cùng nội dung / đối tượng)."""
    out = []
    ref = _norm(v.get("ThamChieu"))
    for r in fdb.query("SELECT * FROM PhieuThuChi WHERE DaXoa = 0 AND Ma != ? AND LoaiThuChi = ?",
                       (ma or "", v.get("LoaiThuChi") or "")):
        why = []
        if ref and _norm(r["ThamChieu"]) == ref:
            why.append("cùng số tham chiếu")
        if int(r["SoTien"] or 0) == int(v.get("SoTien") or 0) \
                and (r["NgayPhatSinh"] or "")[:10] == (v.get("NgayPhatSinh") or "")[:10] \
                and (_norm(r["NoiDung"]) == _norm(v.get("NoiDung"))
                     or (_norm(v.get("DoiTuong")) and _norm(r["DoiTuong"]) == _norm(v.get("DoiTuong")))):
            why.append("cùng ngày, số tiền và nội dung / đối tượng")
        if why:
            out.append(f"Phiếu {r['LoaiThuChi'].lower()} {r['SoPhieu']} ngày {fmt_date(r['NgayPhatSinh'])}: "
                       f"{_money(r['SoTien'])} – {r['NoiDung']} – " + ", ".join(why))
    return out[:LIMIT]


def duplicate_payments(db) -> list[dict]:
    """Các lần thanh toán đã ghi nghi trùng nhau (để rà soát, xóa dòng thừa): cùng số tham chiếu sao kê và số tiền,
    hoặc cùng hợp đồng + ngày + số tiền. Mỗi phần tử: lần ghi sau, kèm lần ghi trước giống nó."""
    seen: dict = {}
    out = []
    skip = ignored_ids(db, "thanh_toan")
    for r in db.query("SELECT t.*, h.SoHopDong, h.TenHopDong FROM ThanhToanHopDong t JOIN HopDong h ON h.Ma = t.MaHopDong"
                      " WHERE t.DaXoa = 0 AND h.DaXoa = 0 ORDER BY t.NgayTao, t.Ma"):
        keys = [("hd", r["MaHopDong"], (r["NgayThanhToan"] or "")[:10], int(r["SoTien"] or 0))]
        if _norm(r["SoThamChieuSK"]):
            keys.append(("sk", _norm(r["SoThamChieuSK"]), int(r["SoTien"] or 0)))
        first = next((seen[k] for k in keys if k in seen), None)
        if first is not None and not (r["Ma"] in skip and first["Ma"] in skip):
            out.append({**dict(r), "TrungVoi": first["Ma"]})
        for k in keys:
            seen.setdefault(k, dict(r))
    return out
