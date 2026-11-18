"""Rà soát dữ liệu đã có: nhóm các bản ghi nghi trùng (thanh toán, hóa đơn, hợp đồng, đối tác, thu chi) và các hóa
đơn / hợp đồng thu thừa, để người dùng chọn rồi sửa, xóa hoặc đánh dấu "không trùng".

Mỗi nhóm: bản ghi nhập trước là bản gốc, các bản sau giống nó là bản nghi trùng. Nhóm đã đánh dấu "không trùng" được
nhớ theo đúng tập bản ghi (setting `khong_trung:<loại>`, đồng bộ sang máy khác); có bản ghi mới trùng thêm thì nhóm
hiện lại."""
from __future__ import annotations

from dataclasses import dataclass, field

from .duplicates import _digits, _norm, fmt_date, fmt_money, ignored, ignored_ids, overpaid_items
from .duplicates import set_ignored, signature

__all__ = ["KINDS", "KIND_LABELS", "COLUMNS", "TABLES", "Group", "scan", "counts", "payment_groups",
           "ignored", "ignored_ids", "set_ignored", "signature"]

KINDS = [("thanh_toan", "Thanh toán nghi trùng"), ("thu_thua", "Thu thừa"), ("hoa_don_ra", "Hóa đơn đầu ra"),
         ("hoa_don_vao", "Hóa đơn đầu vào"), ("hop_dong", "Hợp đồng"), ("doi_tac", "Đối tác"), ("thu_chi", "Thu chi")]
KIND_LABELS = dict(KINDS)
TABLES = {"thanh_toan": "ThanhToanHopDong", "thu_thua": "ThanhToanHopDong", "hoa_don_ra": "HoaDonDauRa",
          "hoa_don_vao": "HoaDonDauVao", "hop_dong": "HopDong", "doi_tac": "DoiTac", "thu_chi": "PhieuThuChi"}

_PAY_COLS = [("SoHopDong", "Số HĐ", "text"), ("NgayThanhToan", "Ngày tiền về", "date"), ("SoTien", "Số tiền", "money"),
             ("SoThamChieuSK", "Số tham chiếu", "text"), ("HinhThuc", "Hình thức", "text"),
             ("SoHoaDon", "Hóa đơn", "text")]
_TAIL = [("LyDo", "Lý do nghi trùng", "text"), ("NgayTao", "Ngày nhập", "date")]
COLUMNS = {
    "thanh_toan": _PAY_COLS + _TAIL,
    "thu_thua": _PAY_COLS + [("LyDo", "Ghi chú", "text"), ("NgayTao", "Ngày nhập", "date")],
    "hoa_don_ra": [("SoHoaDon", "Số hóa đơn", "text"), ("KyHieuHD", "Ký hiệu", "text"),
                   ("NgayXuatHD", "Ngày lập", "date"), ("SoHopDong", "Số HĐ", "text"),
                   ("TenNguoiMua", "Người mua", "text"), ("GiaTriSauThue", "Sau thuế", "money"),
                   ("DaThu", "Đã thu", "money")] + _TAIL,
    "hoa_don_vao": [("SoHoaDonGoc", "Số hóa đơn", "text"), ("KyHieuHD", "Ký hiệu", "text"),
                    ("NgayNhanHD", "Ngày hóa đơn", "date"), ("TenNhaCungCap", "Người bán", "text"),
                    ("MaThueNCC", "MST", "text"), ("GiaTriSauThue", "Sau thuế", "money"),
                    ("SoHopDong", "Số HĐ", "text")] + _TAIL,
    "hop_dong": [("SoHopDong", "Số HĐ", "text"), ("TenHopDong", "Tên hợp đồng", "text"),
                 ("TenDoiTac", "Đối tác", "text"), ("NgayKy", "Ngày ký", "date"),
                 ("GiaTriTongSauThue", "Giá trị", "money"), ("SoHoaDon", "Số hóa đơn", "int"),
                 ("SoThanhToan", "Số lần thanh toán", "int")] + _TAIL,
    "doi_tac": [("TenDoiTac", "Tên đối tác", "text"), ("MaSoThue", "MST", "text"), ("VaiTroDoiTac", "Vai trò", "text"),
                ("DiaChi", "Địa chỉ", "text"), ("SoHopDong", "Số hợp đồng", "int")] + _TAIL,
    "thu_chi": [("SoPhieu", "Số phiếu", "text"), ("LoaiThuChi", "Loại", "text"), ("NgayPhatSinh", "Ngày", "date"),
                ("SoTien", "Số tiền", "money"), ("NoiDung", "Nội dung", "text"), ("DoiTuong", "Đối tượng", "text"),
                ("ThamChieu", "Tham chiếu", "text")] + _TAIL,
}


@dataclass
class Group:
    kind: str
    title: str
    rows: list[dict]                 # mỗi dòng: dữ liệu hiển thị + Ma, Goc (bản gốc), LyDo
    ignored: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return signature(r["Ma"] for r in self.rows)

    def suspects(self) -> list[dict]:
        return [r for r in self.rows if not r.get("Goc")]


def _group(rows: list[dict], keys_of) -> list[list[dict]]:
    """Gom các dòng (đã xếp theo thứ tự nhập) có chung một khóa. keys_of(r) -> [(khóa, lý do)].
    Dòng đầu nhóm là bản gốc; các dòng sau mang LyDo là lý do khớp."""
    owner: dict = {}
    groups: list[list[dict] | None] = []
    for r in rows:
        ks = [(k, why) for k, why in keys_of(r) if k is not None]
        hit = sorted({owner[k] for k, _ in ks if k in owner})
        if not hit:
            gi = len(groups)
            groups.append([dict(r, Goc=True, LyDo="Bản ghi gốc (nhập trước)")])
        else:
            gi = hit[0]
            for other in hit[1:]:                     # hai nhóm nối với nhau qua dòng này: gộp làm một
                groups[gi] += [dict(x, Goc=False) for x in groups[other]]
                for k, v in list(owner.items()):
                    if v == other:
                        owner[k] = gi
                groups[other] = None
            whys = [why for k, why in ks if owner.get(k) == gi]
            groups[gi].append(dict(r, Goc=False, LyDo=", ".join(dict.fromkeys(whys))))
        for k, _ in ks:
            owner.setdefault(k, gi)
    return [g for g in groups if g and len(g) > 1]


def _in_range(d, tu, den) -> bool:
    d = (d or "")[:10]
    return (not tu or d >= tu) and (not den or d <= den)


def _payments(db) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT t.*, h.SoHopDong, h.MaDoiTac, COALESCE(i.SoHoaDon, '(tạm ứng)') SoHoaDon FROM ThanhToanHopDong t"
        " JOIN HopDong h ON h.Ma = t.MaHopDong LEFT JOIN HoaDonDauRa i ON i.Ma = t.MaHDDauRa AND i.DaXoa = 0"
        " WHERE t.DaXoa = 0 AND h.DaXoa = 0 ORDER BY t.NgayTao, t.Ma")]


def payment_groups(db) -> list[list[dict]]:
    def keys(r):
        amount = int(r["SoTien"] or 0)
        out = [(("hd", r["MaHopDong"], (r["NgayThanhToan"] or "")[:10], amount), "cùng hợp đồng, ngày và số tiền")]
        if _norm(r["SoThamChieuSK"]):
            out.append((("sk", _norm(r["SoThamChieuSK"]), amount), "cùng số tham chiếu sao kê và số tiền"))
        return out
    return _group(_payments(db), keys)


def _scan(db, kind: str, fdb=None) -> list[Group]:
    if kind == "thanh_toan":
        return [Group(kind, f"HĐ {g[0]['SoHopDong']} · {fmt_money(g[0]['SoTien'])} đ · {len(g)} lần ghi", g)
                for g in payment_groups(db)]
    if kind == "thu_thua":
        pays = _payments(db)
        dup = {r["Ma"] for g in payment_groups(db) for r in g if not r["Goc"]} - ignored_ids(db, "thanh_toan")
        out = []
        for it in overpaid_items(db):
            if it["loai"] == "hoa_don":
                rows = [p for p in pays if p["MaHDDauRa"] == it["MaHDDauRa"]]
            else:
                rows = [p for p in pays if p["MaHopDong"] == it["MaHopDong"]]
            # bản "gốc" = lần không nghi trùng (không tích sẵn); lần nghi ghi trùng thì tích sẵn
            rows = [dict(p, Goc=p["Ma"] not in dup,
                         LyDo="Nghi ghi trùng lần trước" if p["Ma"] in dup else "") for p in rows]
            title = (f"{it['nhan']} · HĐ {it['SoHopDong']} · giá trị {fmt_money(it['GiaTri'])} đ · đã thu "
                     f"{fmt_money(it['Da'])} đ · thừa {fmt_money(it['Da'] - it['GiaTri'])} đ")
            out.append(Group(kind, title, rows, extra={"thua": it["Da"] - it["GiaTri"], "loai": it["loai"]}))
        return out
    if kind == "hoa_don_ra":
        rows = [dict(r) for r in db.query(
            "SELECT i.*, h.SoHopDong, h.MaDoiTac, (SELECT COALESCE(SUM(SoTien),0) FROM ThanhToanHopDong p"
            " WHERE p.DaXoa = 0 AND p.MaHDDauRa = i.Ma) DaThu FROM HoaDonDauRa i JOIN HopDong h ON h.Ma = i.MaHopDong"
            " WHERE i.DaXoa = 0 AND i.TinhChatHD != 'Đã hủy' ORDER BY i.NgayTao, i.Ma")]

        def keys(r):
            total = int(r["GiaTriSauThue"] or 0)
            so = _digits(r["SoHoaDon"])
            return [(("so", so, total) if so else None, "cùng số hóa đơn và số tiền"),
                    (("hd", r["MaHopDong"], (r["NgayXuatHD"] or "")[:10], total), "cùng hợp đồng, ngày lập, số tiền")]
        return [Group(kind, f"Hóa đơn số {g[0]['SoHoaDon']} · {fmt_money(g[0]['GiaTriSauThue'])} đ", g)
                for g in _group(rows, keys)]
    if kind == "hoa_don_vao":
        rows = [dict(r) for r in db.query(
            "SELECT i.*, COALESCE(h.SoHopDong, '(mua trực tiếp)') SoHopDong, h.MaDoiTac FROM HoaDonDauVao i"
            " LEFT JOIN HopDong h ON h.Ma = i.MaHopDong WHERE i.DaXoa = 0 ORDER BY i.NgayTao, i.Ma")]

        def keys(r):
            mst = (r["MaThueNCC"] or "").strip()
            so = _digits(r["SoHoaDonGoc"])
            if not mst:
                return []
            return [(("so", mst, so) if so else None, "cùng người bán và số hóa đơn"),
                    (("ngay", mst, (r["NgayNhanHD"] or "")[:10], int(r["GiaTriSauThue"] or 0)),
                     "cùng người bán, ngày và số tiền")]
        return [Group(kind, f"{g[0]['TenNhaCungCap']} · hóa đơn {g[0]['SoHoaDonGoc']}", g) for g in _group(rows, keys)]
    if kind == "hop_dong":
        rows = [dict(r) for r in db.query(
            "SELECT h.*, (SELECT COUNT(*) FROM HoaDonDauRa i WHERE i.DaXoa = 0 AND i.MaHopDong = h.Ma) SoHoaDon,"
            " (SELECT COUNT(*) FROM ThanhToanHopDong p WHERE p.DaXoa = 0 AND p.MaHopDong = h.Ma) SoThanhToan"
            " FROM HopDong h WHERE h.DaXoa = 0 ORDER BY h.NgayTao, h.Ma")]

        def keys(r):
            value = int(r["GiaTriTongSauThue"] or 0)
            p = r["MaDoiTac"] or _norm(r["TenDoiTac"])
            return [(("so", _norm(r["SoHopDong"])), "cùng số hợp đồng"),
                    (("ten", p, _norm(r["TenHopDong"])), "cùng đối tác và tên hợp đồng"),
                    (("gt", p, (r["NgayKy"] or "")[:10], value) if value else None,
                     "cùng đối tác, ngày ký và giá trị")]
        return [Group(kind, f"Hợp đồng {g[0]['SoHopDong']} – {g[0]['TenHopDong']}", g) for g in _group(rows, keys)]
    if kind == "doi_tac":
        rows = [dict(r) for r in db.query(
            "SELECT d.*, d.Ma MaDoiTac, (SELECT COUNT(*) FROM HopDong h WHERE h.DaXoa = 0 AND h.MaDoiTac = d.Ma) SoHopDong"
            " FROM DoiTac d WHERE d.DaXoa = 0 ORDER BY d.NgayTao, d.Ma")]

        def keys(r):
            mst = (r["MaSoThue"] or "").strip()
            return [(("ten", _norm(r["TenDoiTac"])), "cùng tên"), (("mst", mst) if mst else None, "cùng mã số thuế")]
        return [Group(kind, f"Đối tác {g[0]['TenDoiTac']}", g) for g in _group(rows, keys)]
    if kind == "thu_chi":
        if fdb is None:
            return []
        rows = [dict(r) for r in fdb.query("SELECT * FROM PhieuThuChi WHERE DaXoa = 0 ORDER BY NgayTao, Ma")]

        def keys(r):
            ref = _norm(r["ThamChieu"])
            return [(("ref", r["LoaiThuChi"], ref) if ref else None, "cùng số tham chiếu"),
                    (("nd", r["LoaiThuChi"], (r["NgayPhatSinh"] or "")[:10], int(r["SoTien"] or 0),
                      _norm(r["NoiDung"])), "cùng ngày, số tiền và nội dung")]
        return [Group(kind, f"Phiếu {g[0]['LoaiThuChi'].lower()} {g[0]['SoPhieu']} · {fmt_money(g[0]['SoTien'])} đ "
                            f"· {fmt_date(g[0]['NgayPhatSinh'])}", g) for g in _group(rows, keys)]
    raise ValueError(kind)


_DATE_KEY = {"thanh_toan": "NgayThanhToan", "thu_thua": "NgayThanhToan", "hoa_don_ra": "NgayXuatHD",
             "hoa_don_vao": "NgayNhanHD", "hop_dong": "NgayKy", "doi_tac": None, "thu_chi": "NgayPhatSinh"}


def scan(db, kind: str, *, fdb=None, tu: str | None = None, den: str | None = None, doi_tac: str | None = None,
         ma_hd: str | None = None, show_ignored: bool = False) -> list[Group]:
    """Các nhóm nghi trùng (hoặc thu thừa) của một loại dữ liệu, lọc theo kỳ, đối tác, hợp đồng.
    Nhóm được giữ khi có ít nhất một dòng khớp bộ lọc (để vẫn thấy bản gốc nằm ngoài kỳ)."""
    groups = _scan(db, kind, fdb)
    skip = ignored(db, kind)
    dk = _DATE_KEY[kind]
    out = []
    for g in groups:
        g.ignored = kind != "thu_thua" and g.key in skip
        if g.ignored and not show_ignored:
            continue
        rows = g.rows
        if dk and (tu or den):
            rows = [r for r in rows if _in_range(r.get(dk), tu, den)]
        if doi_tac:
            rows = [r for r in rows if r.get("MaDoiTac") == doi_tac]
        if ma_hd:
            rows = [r for r in rows if r.get("MaHopDong") == ma_hd or r.get("Ma") == ma_hd]
        if rows:
            out.append(g)
    return out


def counts(db, fdb=None) -> dict[str, int]:
    """Số nhóm cần rà soát của từng loại (hiện trên thẻ ngữ cảnh)."""
    return {k: len(scan(db, k, fdb=fdb)) for k, _ in KINDS if k != "thu_chi" or fdb is not None}
