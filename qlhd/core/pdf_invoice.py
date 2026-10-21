"""Đọc hóa đơn điện tử dạng PDF có lớp chữ (PDF xuất trực tiếp từ hệ thống hóa đơn điện tử).

PDF scan / ảnh chụp không có chữ: trả lỗi để người dùng nhập tay và đính kèm tệp.
Kết quả có cùng dạng với xml_invoice.parse_invoice, thêm khóa "ThieuTruong" liệt kê ô chưa đọc được.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


class InvoicePDFError(Exception):
    pass


def extract_text(path: str | Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:  # pypdf ném nhiều loại lỗi khác nhau với file hỏng
        raise InvoicePDFError(f"Không mở được file PDF: {e}") from e
    if len(text.strip()) < 30:
        raise InvoicePDFError("PDF không có lớp chữ (bản scan / ảnh chụp). Hãy nhập tay và đính kèm file.")
    return text


def _money(s: str) -> int:
    s = s.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        return int(re.sub(r"[.,]", "", s))
    s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") >= 1 else s.replace(",", "")
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


AMOUNT = r"(\d{1,3}(?:[.,]\d{3})+|\d+)"


def _first(patterns, text, flags=re.IGNORECASE):
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m
    return None


def parse_text(text: str) -> dict:
    t = unicodedata.normalize("NFC", text)
    flat = re.sub(r"[ \t]+", " ", t)
    out: dict = {"KyHieuMauSo": "", "KyHieuHD": "", "SoHoaDon": "", "NgayLap": "", "TenNguoiBan": "",
                 "MSTNguoiBan": "", "TenNguoiMua": "", "MSTNguoiMua": "", "GiaTriTruocThue": 0, "VAT": 0,
                 "GiaTriSauThue": 0, "DonViTienTe": "VND", "ThueSuat": [], "SoHopDong": "", "VanBan": t}

    m = _first([r"K[ýy] hi[ệe]u(?:\s*\((?:Serial|Series)[^)]*\))?\s*[:：]?\s*([0-9]?[A-Z][0-9]{2}[A-Z]{2,4})\b"], flat)
    if m:
        kh = m.group(1).upper()
        if kh[0].isdigit():
            out["KyHieuMauSo"], out["KyHieuHD"] = kh[0], kh[1:]
        else:
            out["KyHieuHD"] = kh
    m = _first([r"S[ốo] h[óo]a đ[ơo]n\s*(?:\([^)]*\))?\s*[:：]\s*0*(\d+)",
                r"\bS[ốo]\s*\((?:No|Invoice No)\.?\)\s*[:：]?\s*0*(\d+)",
                r"\bS[ốo]\s*[:：]\s*0*(\d{1,8})\b"], flat)
    if m:
        out["SoHoaDon"] = m.group(1)
    m = _first([r"Ng[àa]y\s*(?:\(Date\))?\s*(\d{1,2})\s*th[áa]ng\s*(?:\(month\))?\s*(\d{1,2})\s*n[ăa]m\s*(?:\(year\))?\s*(\d{4})"],
               flat)
    if m:
        d, mo, y = m.groups()
        out["NgayLap"] = f"{y}-{int(mo):02d}-{int(d):02d}"
    else:
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", flat)
        if m:
            d, mo, y = m.groups()
            out["NgayLap"] = f"{y}-{int(mo):02d}-{int(d):02d}"
    msts = re.findall(r"M[ãa] s[ốo] thu[ếe](?:\s*\([^)]*\))?\s*[:：]?\s*([0-9][0-9 \-]{8,16}[0-9])", flat, re.IGNORECASE)
    msts = [re.sub(r"[ ]", "", x) for x in msts]
    if msts:
        out["MSTNguoiBan"] = msts[0]
    if len(msts) > 1:
        out["MSTNguoiMua"] = msts[1]
    m = _first([r"(?:Đơn vị bán hàng|Tên người bán|Người bán)(?:\s*\([^)]*\))?\s*[:：]\s*(.+)"], t)
    if m:
        out["TenNguoiBan"] = m.group(1).strip()
    m = _first([r"(?:Tên đơn vị|Đơn vị mua hàng|Tên người mua|Người mua hàng)(?:\s*\([^)]*\))?\s*[:：]\s*(.+)"], t)
    if m:
        out["TenNguoiMua"] = m.group(1).strip()
    m = _first([r"C[ộo]ng ti[ềe]n h[àa]ng(?:\s*h[óo]a, d[ịi]ch v[ụu])?(?:\s*\([^)]*\))?\s*[:：]?\s*" + AMOUNT], flat)
    if m:
        out["GiaTriTruocThue"] = _money(m.group(1))
    m = _first([r"Ti[ềe]n thu[ếe] GTGT(?:\s*\([^)]*\))?\s*[:：]?\s*" + AMOUNT,
                r"T[ổo]ng (?:c[ộo]ng )?ti[ềe]n thu[ếe](?: GTGT)?(?:\s*\([^)]*\))?\s*[:：]?\s*" + AMOUNT], flat)
    if m:
        out["VAT"] = _money(m.group(1))
    m = _first([r"T[ổo]ng (?:c[ộo]ng )?ti[ềe]n thanh to[áa]n(?:\s*\([^)]*\))?\s*[:：]?\s*" + AMOUNT], flat)
    if m:
        out["GiaTriSauThue"] = _money(m.group(1))
    if not out["GiaTriSauThue"] and out["GiaTriTruocThue"]:
        out["GiaTriSauThue"] = out["GiaTriTruocThue"] + out["VAT"]
    if not out["GiaTriTruocThue"] and out["GiaTriSauThue"]:
        out["GiaTriTruocThue"] = out["GiaTriSauThue"] - out["VAT"]
    m = _first([r"(?:S[ốo] h[ợo]p đ[ồo]ng|H[ợo]p đ[ồo]ng s[ốo])(?:\s*\([^)]*\))?\s*[:：]\s*([^\s,;]+)"], flat)
    if m:
        out["SoHopDong"] = m.group(1).strip(".")
    labels = {"SoHoaDon": "Số hóa đơn", "NgayLap": "Ngày lập", "KyHieuHD": "Ký hiệu",
              "MSTNguoiBan": "MST người bán", "GiaTriSauThue": "Tổng thanh toán"}
    out["ThieuTruong"] = [label for k, label in labels.items() if not out[k]]
    return out


def _ensure_flow(text: str) -> str:
    return text.replace("\r", "\n")


def parse_invoice(path: str | Path) -> dict:
    data = parse_text(_ensure_flow(extract_text(path)))
    if not data["SoHoaDon"] and not data["GiaTriSauThue"]:
        raise InvoicePDFError("Không nhận ra nội dung hóa đơn trong file PDF. Hãy nhập tay.")
    return data
