"""Nhập hóa đơn đầu vào từ file Excel: file mẫu của phần mềm, hoặc bảng kê hóa đơn mua vào đã có sẵn
(nhận cột theo tên tiêu đề, không cần đúng thứ tự; hiểu dòng "Nhóm HHDV mua vào : 1. / 5." để xếp nhóm thuế)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import vat
from .contracts import TAX_GROUP_TITLES, TAX_GROUPS, ValidationError, guess_tax_group
from .invoice_match import fold

# (khóa, tiêu đề trong file mẫu, bắt buộc, kiểu, các cụm từ nhận diện tiêu đề — đã bỏ dấu, chữ thường)
# Thứ tự quan trọng: cụm cụ thể đứng trước (VD "mã số thuế người bán" trước "thuế").
COLUMNS = [
    ("_bo_qua", None, False, "text", ("tk thue", "tai khoan thue", "stt", "so thu tu")),
    ("KyHieuMauSo", "Ký hiệu mẫu số", False, "text", ("mau so",)),
    ("KyHieuHD", "Ký hiệu hóa đơn", False, "text", ("ky hieu",)),
    ("SoHopDong", "Số hợp đồng đầu vào", False, "text", ("so hop dong", "hop dong")),
    ("SoThamChieuSK", "Số tham chiếu sao kê", False, "text", ("sao ke", "tham chieu")),
    ("SoHoaDonGoc", "Số hóa đơn", True, "invoice_no", ("so hoa don", "so hd", "hoa don so", "so chung tu")),
    ("NgayNhanHD", "Ngày hóa đơn", True, "date", ("ngay",)),
    ("MaThueNCC", "Mã số thuế người bán", True, "mst", ("ma so thue", "mst")),
    ("TenNhaCungCap", "Tên người bán", True, "text", ("nguoi ban", "nha cung cap", "don vi ban", "ben ban")),
    ("NguoiMuaHang", "Người mua hàng", False, "text", ("nguoi mua",)),
    ("NhomThue", "Nhóm thuế GTGT", False, "group", ("nhom",)),
    ("ThueSuat", "Thuế suất", False, "rate", ("thue suat",)),
    ("GiaTriTruocThue", "Giá trị chưa có thuế", False, "money",
     ("chua co thue", "chua thue", "truoc thue", "doanh so", "thanh tien")),
    ("GiaTriSauThue", "Tổng thanh toán", False, "money", ("tong thanh toan", "sau thue", "tong cong", "tong tien",
                                                          "da co thue")),
    ("TyLePhiKhac", "Tỷ lệ phí BVMT (%)", False, "rate", ("ty le phi",)),
    ("PhiKhac", "Phí BVMT / phí khác", False, "money", ("phi bao ve moi truong", "phi bvmt", "phi khac", "le phi")),
    ("VAT", "Thuế GTGT", False, "money", ("thue gtgt", "tien thue", "vat", "thue")),
    ("MatHang", "Mặt hàng", False, "text", ("mat hang", "ten hang", "hang hoa", "noi dung", "dien giai")),
    ("GhiChuHDDV", "Ghi chú", False, "text", ("ghi chu",)),
]
TEMPLATE_KEYS = ["SoHoaDonGoc", "KyHieuHD", "NgayNhanHD", "TenNhaCungCap", "MaThueNCC", "MatHang", "GiaTriTruocThue",
                 "ThueSuat", "VAT", "PhiKhac", "TyLePhiKhac", "GiaTriSauThue", "NhomThue", "NguoiMuaHang", "SoThamChieuSK", "SoHopDong",
                 "GhiChuHDDV"]
SHEET = "Hóa đơn đầu vào"
LABELS = {c[0]: c[1] for c in COLUMNS}

GUIDE = [
    "HƯỚNG DẪN NHẬP HÓA ĐƠN ĐẦU VÀO TỪ EXCEL",
    "",
    "1. Điền từ dòng 2 của sheet 'Hóa đơn đầu vào'. Cột có dấu * là bắt buộc. Xóa dòng ví dụ trước khi nhập.",
    "2. Có thể nhập thẳng bảng kê hóa đơn mua vào đã có (VD bảng kê xuất từ phần mềm kế toán / cổng thuế):",
    "   phần mềm tìm dòng tiêu đề và nhận cột theo tên (Số hóa đơn, Ngày hóa đơn, Tên người bán, Mã số thuế người bán,",
    "   Mặt hàng, Giá trị chưa có thuế, Thuế suất, Thuế GTGT…), không cần đúng thứ tự cột.",
    "3. Dòng 'Nhóm HHDV mua vào : 1. …' và '… : 5. …' trong bảng kê dùng để xếp nhóm Chịu thuế / Không chịu thuế"
    " cho các dòng bên dưới. Dòng tổng, dòng 'Số dòng = …' được bỏ qua.",
    "4. Nhóm thuế GTGT: 'Chịu thuế' hoặc 'Không chịu thuế'. Để trống thì phần mềm xếp theo tiền thuế (có thuế →"
    " Chịu thuế) và nhắc kiểm tra lại.",
    "5. Số hợp đồng đầu vào: điền nếu hóa đơn thuộc hợp đồng. Không điền thì phần mềm thử nhận theo MST người bán;"
    " không chắc chắn thì bạn phải tự chọn hợp đồng (hoặc 'Không có – mua hàng trực tiếp') trên màn hình xem trước.",
    "   Hóa đơn nước sạch có Phí bảo vệ môi trường: điền cột Phí BVMT (số tiền) hoặc Tỷ lệ phí BVMT (VD 10%);"
    " tổng thanh toán = trước thuế + thuế GTGT + phí. Bảng kê ghi phí thành dòng thứ hai ngay dưới hóa đơn (ô số hóa"
    " đơn, ngày, người bán để trống hoặc gộp ô, mặt hàng 'Phí bảo vệ môi trường') thì phần mềm gộp vào hóa đơn trên."
    " Hóa đơn chỉ có một dòng là hóa đơn không có phí.",
    "6. Hóa đơn đã có trong phần mềm (cùng MST người bán + ký hiệu + số) hoặc lặp lại trong file sẽ được bỏ qua.",
    "7. Trong phần mềm: Hóa đơn đầu vào → Nhập từ Excel → chọn file → kiểm tra, chọn hợp đồng → Nhập.",
]
EXAMPLE = ["1285718", "C26TAA", date(2026, 4, 30), "CÔNG TY CỔ PHẦN GIAO THÔNG SỐ VIỆT NAM", "0109266456",
           "Cước đường bộ 30K90317", 94699, "10%", 7576, None, None, 102275, "Chịu thuế", "Nguyễn Văn A", "FT26120123456", "",
           "Ví dụ, xóa dòng này trước khi nhập"]


def write_template(path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    guide = wb.active
    guide.title = "Hướng dẫn"
    for i, line in enumerate(GUIDE, start=1):
        guide.cell(i, 1, line).font = Font(bold=(i == 1), size=14 if i == 1 else 11)
    guide.column_dimensions["A"].width = 130
    ws = wb.create_sheet(SHEET)
    kinds = {c[0]: (c[2], c[3]) for c in COLUMNS}
    for c, key in enumerate(TEMPLATE_KEYS, start=1):
        required, kind = kinds[key]
        cell = ws.cell(1, c, LABELS[key] + (" *" if required else ""))
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="FCE4D6" if required else "DCE8F5")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        letter = cell.column_letter
        ws.column_dimensions[letter].width = 16 if kind in ("money", "date", "rate", "invoice_no") else 26
        v = ws.cell(2, c, EXAMPLE[c - 1])
        fmt = "#,##0" if kind == "money" else "DD/MM/YYYY" if kind == "date" else "@" if kind in (
            "mst", "invoice_no") else None
        if fmt:
            v.number_format = fmt
            for r in range(3, 2001):
                ws[f"{letter}{r}"].number_format = fmt
        if kind == "group":
            dv = DataValidation(type="list", formula1='"' + ",".join(TAX_GROUPS) + '"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}2000")
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "A2"
    path = Path(path)
    wb.save(path)
    return path


# ---------------------------------------------------------------- đọc file
@dataclass
class ExcelInvoice:
    sheet: str
    line: int
    values: dict
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    so_hop_dong: str = ""
    blank: set = field(default_factory=set)     # các ô để trống trong file


def _column_key(header) -> str | None:
    h = fold(header).replace("\n", " ").strip()
    h = re.sub(r"[*:()]", " ", h)
    h = " ".join(h.split())
    if not h:
        return None
    for key, _, _, _, words in COLUMNS:
        if any(w == h or re.search(rf"(^|\W){re.escape(w)}(\W|$)", h) for w in words):
            return key
    return None


def _find_header(rows: list[tuple]) -> tuple[int, dict[int, str]] | None:
    """Dòng tiêu đề: dòng đầu tiên (trong 30 dòng đầu) nhận ra cột Số hóa đơn và ít nhất 2 cột khác."""
    for i, row in enumerate(rows[:30]):
        cols: dict[int, str] = {}
        for j, v in enumerate(row):
            if v in (None, "") or not isinstance(v, str):
                continue
            key = _column_key(v)
            if key and key != "_bo_qua" and key not in cols.values():
                cols[j] = key
        if "SoHoaDonGoc" in cols.values() and len(cols) >= 3:
            return i, cols
    return None


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _invoice_no(v) -> str:
    return _text(v)


def _mst(v) -> str:
    s = _text(v)
    if isinstance(v, (int, float)) and len(s) < 10:     # Excel lưu MST dạng số làm mất số 0 đầu
        s = s.zfill(10)
    return s.replace(" ", "")


def _date(v) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"ngày không hợp lệ: {s}")


def _money(v) -> int:
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).strip().replace(" ", "").replace("đ", "").replace(".", "").replace(",", "")
    if not s.lstrip("-").isdigit():
        raise ValueError(f"số tiền không hợp lệ: {v}")
    return int(s)


def _rate(v) -> str:
    if v in (None, ""):
        return ""
    try:
        return vat.parse_rate(v)
    except ValueError:
        return str(v).strip()


def _group(v) -> str | None:
    f = fold(v)
    if not f:
        return None
    if "khong" in f:
        return "Không chịu thuế"
    if "chiu" in f:
        return "Chịu thuế"
    return None


def group_from_heading(text) -> str | None:
    """Dòng nhóm của bảng kê: 'Nhóm HHDV mua vào : 1. …' → Chịu thuế, '… : 5. …' → Không chịu thuế."""
    f = fold(text)
    if not f.startswith("nhom"):
        return None
    m = re.search(r":\s*(\d+)\s*\.", f)
    if m:
        return "Chịu thuế" if m.group(1) == "1" else "Không chịu thuế"
    for g, title in TAX_GROUP_TITLES.items():
        if fold(title)[3:40] in f:
            return g
    return _group(f) or "Chịu thuế"


def _is_total(row) -> bool:
    for v in row:
        if isinstance(v, str):
            f = fold(v).strip()
            if f.startswith(("so dong", "tong cong", "cong:", "cong ", "tong:", "tong so")) or f in ("cong", "tong"):
                return True
    return False


def read_rows(rows: list[tuple], sheet: str) -> list[ExcelInvoice]:
    found = _find_header(rows)
    if not found:
        return []
    h, cols = found
    raw: list[tuple[ExcelInvoice, str | None]] = []
    group = None
    kinds = {c[0]: c[3] for c in COLUMNS}
    so_idx = next(j for j, k in cols.items() if k == "SoHoaDonGoc")
    for i in range(h + 1, len(rows)):
        row = rows[i]
        if row is None or all(v in (None, "") for v in row):
            continue
        texts = [v for v in row if isinstance(v, str) and v.strip()]
        g = next((group_from_heading(t) for t in texts if fold(t).startswith("nhom")), None)
        so = row[so_idx] if so_idx < len(row) else None
        if g and not _text(so):
            group = g
            continue
        if _is_total(row) and not (_text(so) and _text(so)[0].isdigit()):
            continue
        values, errors, blank = {}, [], set(kinds) - set(cols.values())
        for j, key in cols.items():
            v = row[j] if j < len(row) else None
            kind = kinds[key]
            if v in (None, ""):
                blank.add(key)
            try:
                values[key] = {"date": _date, "money": _money, "mst": _mst, "rate": _rate, "group": _group,
                               "invoice_no": _invoice_no}.get(kind, _text)(v)
            except ValueError as e:
                errors.append(f"{LABELS[key]}: {e}")
        if not values.get("SoHoaDonGoc") and not values.get("TenNhaCungCap"):
            if raw:
                _continue(raw[-1][0], values, blank, i + 1)
            continue
        raw.append((ExcelInvoice(sheet, i + 1, values, errors=errors, blank=blank), group))
    return [_finish(item, g) for item, g in raw]


FEE_WORDS = ("phi bao ve moi truong", "phi bvmt", "phi moi truong", "le phi", "phi khac")


def _continue(item: ExcelInvoice, values: dict, blank: set, line: int) -> bool:
    """Dòng tiếp theo của cùng một hóa đơn (bảng kê gộp ô Số hóa đơn, Ngày, Người bán):
    dòng 'Phí bảo vệ môi trường' thành phí của hóa đơn; dòng hàng hóa khác cộng vào giá trị hóa đơn."""
    amount = values.get("GiaTriTruocThue") or values.get("GiaTriSauThue") or values.get("PhiKhac") or 0
    if not amount:
        return False
    v = item.values
    name = values.get("MatHang") or ""
    if any(w in fold(name) for w in FEE_WORDS) and not values.get("VAT"):
        v["PhiKhac"] = (v.get("PhiKhac") or 0) + amount
        v["TenPhiKhac"] = name
        item.blank.discard("PhiKhac")
        if "GiaTriSauThue" not in item.blank:
            v["GiaTriSauThue"] = (v.get("GiaTriSauThue") or 0) + (values.get("GiaTriSauThue") or amount)
        item.notes.append(f"{name} {amount:,} đ lấy từ dòng {line} (dòng thứ hai của hóa đơn).".replace(",", "."))
        return True
    if not name:                       # dòng chỉ có số, không có tên hàng (VD dòng cộng): bỏ qua
        return False
    for k in ("GiaTriTruocThue", "VAT", "GiaTriSauThue"):
        if k not in blank and k not in item.blank:
            v[k] = (v.get(k) or 0) + (values.get(k) or 0)
    if name:
        v["MatHang"] = "; ".join(x for x in (v.get("MatHang"), name) if x)
    item.notes.append(f"Gộp dòng {line} ({name}) vào hóa đơn.")
    return True


def _finish(item: ExcelInvoice, group: str | None) -> ExcelInvoice:
    v = item.values
    def given(k):
        return None if k in item.blank else (v.get(k) or 0)

    if given("GiaTriTruocThue") or given("GiaTriSauThue"):
        res = vat.complete(given("GiaTriTruocThue") or None, given("VAT"), given("GiaTriSauThue") or None,
                           v.get("ThueSuat") or "", given("PhiKhac") or None, v.get("TyLePhiKhac") or "")
        if res.error:
            item.errors.append(res.error)
        else:
            v["GiaTriTruocThue"], v["VAT"], v["GiaTriSauThue"], v["ThueSuat"] = res.before, res.vat, res.after, res.rate
            v["PhiKhac"], v["TyLePhiKhac"] = res.fee, res.fee_rate
            item.notes += res.notes
            if res.computed and "Thuế suất" not in res.computed:
                item.notes.append("Tự tính: " + ", ".join(res.computed) + ".")
    for key in ("GiaTriTruocThue", "VAT", "GiaTriSauThue"):
        v[key] = v.get(key) or 0
    if not v.get("NhomThue"):
        if group:
            v["NhomThue"] = group
        else:
            v["NhomThue"] = guess_tax_group(v["VAT"])
            item.notes.append(f"Nhóm thuế xếp theo tiền thuế ({v['NhomThue']}), kiểm tra lại.")
    item.so_hop_dong = v.pop("SoHopDong", "") or ""
    for key in ("SoHoaDonGoc", "TenNhaCungCap", "MaThueNCC", "NgayNhanHD"):
        if not v.get(key) and not any(e.startswith(LABELS[key]) for e in item.errors):
            item.errors.append(f"Thiếu {LABELS[key]}")
    return item


def read_workbook(path: str | Path, password: str | None = None) -> list[ExcelInvoice]:
    """Đọc mọi sheet có dòng tiêu đề nhận ra được (sheet mẫu hoặc bảng kê)."""
    from .finance_import import _open_workbook

    if str(path).lower().endswith(".xls"):
        raise ValidationError("File Excel dạng cũ (.xls). Hãy mở bằng Excel và Lưu thành .xlsx rồi nhập lại.")
    wb = _open_workbook(path, password)
    out: list[ExcelInvoice] = []
    try:
        for ws in wb.worksheets:
            if ws.title == "Hướng dẫn":
                continue
            out += read_rows([tuple(r) for r in ws.iter_rows(values_only=True)], ws.title)
    finally:
        wb.close()
    if not out:
        raise ValidationError("Không tìm thấy bảng hóa đơn trong file: cần một dòng tiêu đề có cột \"Số hóa đơn\" "
                              "và các cột như Ngày hóa đơn, Tên người bán, Mã số thuế người bán. Có thể dùng file mẫu.")
    return out


def find_contract(db, so_hop_dong: str):
    """Hợp đồng đầu vào theo số ghi trong file (so sánh bỏ dấu, không phân biệt hoa thường)."""
    key = fold(so_hop_dong).replace(" ", "")
    hit = [h for h in db.active("HopDong", "ChieuHopDong = 'Đầu vào'")
           if fold(h["SoHopDong"]).replace(" ", "") == key]
    return hit[0] if len(hit) == 1 else None
