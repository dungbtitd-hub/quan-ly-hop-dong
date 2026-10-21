"""Đọc hóa đơn điện tử XML (chuẩn Tổng cục Thuế, Thông tư 78) không phụ thuộc namespace."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


class InvoiceXMLError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node, *path):
    """Tìm theo chuỗi tên thẻ (bỏ qua namespace), mỗi bước tìm ở mọi độ sâu."""
    cur = node
    for name in path:
        nxt = None
        for el in cur.iter():
            if el is not cur and _local(el.tag) == name:
                nxt = el
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _text(node, *path) -> str:
    el = _find(node, *path)
    return (el.text or "").strip() if el is not None and el.text else ""


def _money(s: str) -> int:
    if not s:
        return 0
    try:
        return int(round(float(s.replace(",", ""))))
    except ValueError:
        return 0


def parse_invoice(source: str | Path | bytes) -> dict:
    try:
        if isinstance(source, bytes):
            root = ET.fromstring(source)
        else:
            root = ET.parse(str(source)).getroot()
    except ET.ParseError as e:
        raise InvoiceXMLError(f"File XML lỗi cấu trúc: {e}") from e
    data = _find(root, "DLHDon") if _local(root.tag) != "DLHDon" else root
    if data is None:
        data = root
    so = _text(data, "TTChung", "SHDon")
    if not so:
        raise InvoiceXMLError("Không tìm thấy số hóa đơn (thẻ <SHDon>) trong file.")
    tong_truoc = _money(_text(data, "TToan", "TgTCThue"))
    vat = _money(_text(data, "TToan", "TgTThue"))
    tong_sau = _money(_text(data, "TToan", "TgTTTBSo")) or tong_truoc + vat
    thue_suat = []
    ts_root = _find(data, "THTTLTSuat")
    if ts_root is not None:
        for el in ts_root.iter():
            if _local(el.tag) == "LTSuat":
                thue_suat.append({"ThueSuat": _text(el, "TSuat"), "ThanhTien": _money(_text(el, "ThTien")),
                                  "TienThue": _money(_text(el, "TThue"))})
    items, rates = [], []
    for el in data.iter():
        if _local(el.tag) == "HHDVu":
            ten = " ".join(_text(el, "THHDVu").split())
            if ten and ten not in items:
                items.append(ten)
            rate = _text(el, "TSuat")
            if rate and rate not in rates:
                rates.append(rate)
    for ts in thue_suat:
        if ts["ThueSuat"] and ts["ThueSuat"] not in rates:
            rates.append(ts["ThueSuat"])
    so_hd = []
    for el in data.iter():
        if _local(el.tag) == "TTin":
            ten = _text(el, "TTruong").lower()
            if "hợp đồng" in ten or "hop dong" in ten or "hopdong" in ten or ten in ("shdong", "sohopdong", "hđ"):
                gia_tri = _text(el, "DLieu")
                if gia_tri:
                    so_hd.append(gia_tri)
    van_ban = "\n".join(" ".join((el.text or "").split()) for el in data.iter()
                        if el.text and el.text.strip() and _local(el.tag) not in ("DSCKS", "Signature"))
    return {
        "SoHopDong": "; ".join(so_hd),
        "VanBan": van_ban,
        "KyHieuMauSo": _text(data, "TTChung", "KHMSHDon"),
        "KyHieuHD": _text(data, "TTChung", "KHHDon"),
        "SoHoaDon": so,
        "NgayLap": _text(data, "TTChung", "NLap")[:10],
        "DonViTienTe": _text(data, "TTChung", "DVTTe") or "VND",
        "TyGia": _text(data, "TTChung", "TGia"),
        "TenNguoiBan": _text(data, "NDHDon", "NBan", "Ten"),
        "MSTNguoiBan": _text(data, "NDHDon", "NBan", "MST"),
        "TenNguoiMua": _text(data, "NDHDon", "NMua", "Ten"),
        "MSTNguoiMua": _text(data, "NDHDon", "NMua", "MST"),
        "GiaTriTruocThue": tong_truoc,
        "VAT": vat,
        "GiaTriSauThue": tong_sau,
        "ThueSuat": thue_suat,
        "MatHang": join_items(items),
        "ThueSuatHD": "; ".join(rates),
    }


def join_items(items: list[str], limit: int = 250) -> str:
    """Tên các mặt hàng nối bằng '; ', dài quá thì cắt và ghi số mặt hàng còn lại."""
    out = ""
    for i, name in enumerate(items):
        nxt = f"{out}; {name}" if out else name
        if len(nxt) > limit and out:
            return f"{out}; … (+{len(items) - i} mặt hàng)"
        out = nxt
    return out
