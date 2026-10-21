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
    return {
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
    }
