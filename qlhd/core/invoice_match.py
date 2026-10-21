"""Tự gắn hóa đơn (đọc từ XML / PDF) với hợp đồng đầu ra / đầu vào đang có trong phần mềm.

Thứ tự ưu tiên:
1. Số hợp đồng của phần mềm xuất hiện trong hóa đơn (ô thông tin khác, dòng hàng hóa, ghi chú…),
   khớp cả MST đối tác nếu hợp đồng có MST.
2. Hóa đơn không ghi số hợp đồng: đối tác (MST) chỉ có đúng một hợp đồng còn hiệu lực theo chiều đó.
Không chắc chắn thì không tự gắn, trả về danh sách hợp đồng gợi ý để người dùng chọn.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

CLOSED = ("Hoàn thành", "Hủy")

_REF = re.compile(
    r"(?:hop dong|\bhd)\b[^\n:]{0,25}?\b(?:so|no)\b\.?\s*[:.]?\s*([0-9a-z][0-9a-z/\-._]*[0-9a-z])"
    r"|\bhd\s*[:.]?\s*([0-9]+[0-9a-z]*[/\-][0-9a-z/\-._]*[0-9a-z])")


def fold(s: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt, đ→d, thống nhất gạch nối, bỏ khoảng trắng quanh / và -."""
    s = unicodedata.normalize("NFD", str(s or "").replace("Đ", "D").replace("đ", "d"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    s = re.sub(r"\s*([/\-])\s*", r"\1", s)
    return re.sub(r"[ \t]+", " ", s)


def referenced_numbers(text: str) -> list[str]:
    """Số hợp đồng được ghi trong hóa đơn (dạng đã chuẩn hóa), VD 'theo HĐ số 01/2026/HĐTV'."""
    out = []
    for m in _REF.finditer(fold(text)):
        num = (m.group(1) or m.group(2)).strip("._-")
        if any(ch.isdigit() for ch in num) and num not in out:
            out.append(num)
    return out


def _mentions(text_f: str, number: str) -> bool:
    num = fold(number).replace(" ", "")
    if not num:
        return False
    pat = r"(?<![0-9a-z])" + re.escape(num) + r"(?![0-9a-z])"
    strong = len(num) >= 5 and ("/" in num or (any(c.isalpha() for c in num) and any(c.isdigit() for c in num)))
    if strong:
        return re.search(pat, text_f) is not None
    # số hợp đồng ngắn (VD "12") chỉ tính khi đứng ngay sau chữ hợp đồng / HĐ
    return re.search(r"(?:hop dong|\bhd)\b[^\n]{0,25}?" + pat, text_f) is not None


@dataclass
class ContractMatch:
    ma: str | None = None
    so: str = ""
    ly_do: str = ""
    goi_y: list = field(default_factory=list)       # các hợp đồng có thể đúng khi không tự gắn được
    so_tren_hoa_don: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        if self.ma:
            return f"Đã tự gắn hợp đồng {self.so} ({self.ly_do}). Kiểm tra lại trước khi lưu."
        parts = []
        if self.so_tren_hoa_don:
            parts.append("Hóa đơn ghi hợp đồng số " + ", ".join(self.so_tren_hoa_don).upper()
                         + " nhưng chưa tìm thấy hợp đồng này trong phần mềm.")
        if self.goi_y:
            parts.append("Có thể là: " + "; ".join(h["SoHopDong"] for h in self.goi_y[:5]) + ". Hãy chọn hợp đồng.")
        return " ".join(parts) or "Chưa xác định được hợp đồng, hãy chọn."


def match_contract(db, kind: str, invoice: dict) -> ContractMatch:
    """kind 'ra' (hợp đồng đầu ra, đối tác = người mua) hoặc 'vao' (đầu vào, đối tác = người bán)."""
    chieu = "Đầu ra" if kind == "ra" else "Đầu vào"
    mst = (invoice.get("MSTNguoiMua") if kind == "ra" else invoice.get("MSTNguoiBan")) or ""
    mst = re.sub(r"\D", "", mst.split("-")[0]) if mst else ""
    contracts = db.active("HopDong", "ChieuHopDong = ?", (chieu,), order="NgayKy DESC")

    def same_partner(h) -> bool | None:
        ct = re.sub(r"\D", "", (h["MaThueDoiTac"] or "").split("-")[0])
        if not ct or not mst:
            return None
        return ct == mst

    text = "\n".join(str(x) for x in (invoice.get("SoHopDong"), invoice.get("VanBan")) if x)
    text_f = fold(text)
    refs = referenced_numbers(text)
    res = ContractMatch(so_tren_hoa_don=refs)
    by_number = [h for h in contracts if text_f and _mentions(text_f, h["SoHopDong"])]
    by_number = [h for h in by_number if same_partner(h) is not False]
    if len(by_number) > 1:
        exact = [h for h in by_number if same_partner(h)]
        by_number = exact if exact else by_number
        if len(by_number) > 1:
            longest = max(len(fold(h["SoHopDong"])) for h in by_number)
            by_number = [h for h in by_number if len(fold(h["SoHopDong"])) == longest]
    if len(by_number) == 1:
        h = by_number[0]
        res.ma, res.so = h["Ma"], h["SoHopDong"]
        res.ly_do = "theo số hợp đồng ghi trên hóa đơn"
        res.so_tren_hoa_don = []
        return res
    if by_number:
        res.goi_y = by_number
        return res
    partner = [h for h in contracts if same_partner(h)]
    if refs:
        # hóa đơn có ghi số hợp đồng nhưng không khớp: không đoán theo MST
        res.goi_y = partner
        return res
    open_ = [h for h in partner if h["TinhTrangHopDong"] not in CLOSED]
    pick = open_ if open_ else partner
    if len(pick) == 1:
        h = pick[0]
        res.ma, res.so = h["Ma"], h["SoHopDong"]
        res.ly_do = "đối tác (MST) chỉ có một hợp đồng " + ("đang thực hiện" if open_ else chieu.lower())
        return res
    res.goi_y = pick
    return res
