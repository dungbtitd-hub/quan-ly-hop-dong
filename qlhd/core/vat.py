"""Tự tính bộ giá trị VAT: trước thuế, thuế suất, tiền VAT, sau thuế.

Chỉ cần đủ hai dữ kiện (trong đó có ít nhất một số tiền):
  trước thuế + thuế suất  → VAT = trước thuế × thuế suất (làm tròn đồng), sau thuế = trước thuế + VAT
  sau thuế + thuế suất    → trước thuế = sau thuế ÷ (1 + thuế suất) (làm tròn đồng), VAT = sau thuế − trước thuế
  trước thuế + VAT        → sau thuế = trước thuế + VAT
  sau thuế + VAT          → trước thuế = sau thuế − VAT
  trước thuế + sau thuế   → VAT = sau thuế − trước thuế
Thuế suất còn trống thì suy ra từ số tiền (chỉ nhận mức chuẩn 0%, 5%, 8%, 10%).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from fractions import Fraction

NO_TAX = ("KCT", "KKKNT", "KHAC")       # không chịu thuế, không kê khai nộp thuế, khác: tiền VAT = 0
STANDARD = (10, 8, 5)


def _fold(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def parse_rate(v) -> str:
    """Chuẩn hóa thuế suất: 0.1 / 10 / "10" / "10 %" → "10%"; "kct" → "KCT"; trống → ""."""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        n = v * 100 if 0 < abs(v) < 1 else v
        return f"{round(n, 4):g}%"
    s = _fold(v).replace(" ", "").upper()
    if s in NO_TAX:
        return s
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)%?", s)
    if not m:
        raise ValueError(f"thuế suất không hợp lệ: {v}")
    n = float(m.group(1).replace(",", "."))
    if 0 < n < 1 and "%" not in s:
        n *= 100
    return f"{round(n, 4):g}%"


def rate_value(rate: str) -> Fraction | None:
    """"10%" → 10; "KCT" → 0; "" → None."""
    if not rate:
        return None
    if rate in NO_TAX:
        return Fraction(0)
    return Fraction(rate.rstrip("%"))


def _round(x: Fraction) -> int:
    """Làm tròn đến đồng, nửa đồng làm tròn lên (xa số 0), như hóa đơn."""
    n = abs(x)
    r = int(n + Fraction(1, 2))
    return r if x >= 0 else -r


def guess_rate(before: int, vat: int) -> str:
    if not before:
        return ""
    if vat == 0:
        return ""
    r = Fraction(vat * 100, before)
    return next((f"{std}%" for std in STANDARD if abs(r - std) < Fraction(1, 5)), "")


@dataclass
class VatResult:
    before: int = 0
    vat: int = 0
    after: int = 0
    rate: str = ""
    computed: list[str] = field(default_factory=list)   # các ô phần mềm đã tự tính
    notes: list[str] = field(default_factory=list)      # lưu ý (lệch nhỏ, thiếu dữ kiện…)
    error: str = ""                                     # số liệu mâu thuẫn


def _money(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def complete(before: int | None, vat: int | None, after: int | None, rate: str | None = "") -> VatResult:
    """None = ô trống. Trả về bộ số đầy đủ và các ô đã tự tính."""
    rate = rate or ""
    r = rate_value(rate)
    res = VatResult(rate=rate)
    if before is None and after is None:
        if vat is not None:
            res.error = "Chỉ có tiền VAT, thiếu giá trị trước thuế hoặc sau thuế."
        res.vat = vat or 0
        return res
    if before is not None and after is not None and vat is not None:
        if before + vat != after:
            res.error = (f"Trước thuế {_money(before)} + VAT {_money(vat)} = {_money(before + vat)}, "
                         f"khác sau thuế {_money(after)}.")
    elif before is not None and after is not None:
        vat = after - before
        res.computed.append("VAT")
    elif vat is not None:
        if before is not None:
            after = before + vat
            res.computed.append("Sau thuế")
        else:
            before = after - vat
            res.computed.append("Trước thuế")
    elif r is not None:
        if before is not None:
            vat = _round(before * r / 100)
            after = before + vat
            res.computed += ["VAT", "Sau thuế"]
        else:
            before = _round(after * 100 / (100 + r))
            vat = after - before
            res.computed += ["Trước thuế", "VAT"]
    else:
        vat = 0
        if before is not None:
            after = before
            res.computed.append("Sau thuế")
        else:
            before = after
            res.computed.append("Trước thuế")
        res.notes.append("Chưa có thuế suất hoặc tiền VAT: tính như không có thuế (VAT = 0).")
    res.before, res.vat, res.after = before, vat, after
    if r is not None and "VAT" not in res.computed and before:
        expect = _round(before * r / 100)
        if abs(expect - vat) > 1:
            res.notes.append(f"Tiền VAT {_money(vat)} khác {rate} × trước thuế = {_money(expect)}.")
    if not rate:
        res.rate = guess_rate(before, vat)
        if res.rate:
            res.computed.append("Thuế suất")
    return res


def fill(v: dict, after_key: str = "GiaTriSauThue", strict: bool = True) -> VatResult:
    """Điền bộ số vào dict giá trị (khóa GiaTriTruocThue, VAT, after_key, ThueSuat); ô 0 / trống coi là chưa nhập.
    strict: số liệu mâu thuẫn thì báo lỗi (ValueError)."""
    def amount(k):
        x = v.get(k)
        return int(x) if x not in (None, "", 0) else None

    rate = parse_rate(v.get("ThueSuat"))
    res = complete(amount("GiaTriTruocThue"), amount("VAT"), amount(after_key), rate)
    if res.error and strict:
        raise ValueError(res.error)
    v["GiaTriTruocThue"], v["VAT"], v[after_key] = res.before, res.vat, res.after
    if "ThueSuat" in v or res.rate:
        v["ThueSuat"] = res.rate
    return res
