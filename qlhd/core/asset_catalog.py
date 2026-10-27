"""Danh mục phân loại dòng hóa đơn đầu vào: chi phí trong kỳ, công cụ dụng cụ (CCDC), tài sản cố định (TSCĐ),
chi phí chờ phân bổ.

Danh mục cài sẵn theo quy định (TT 45/2013/TT-BTC, TT 147/2016/TT-BTC, NĐ 320/2025/NĐ-CP) và các tài sản trong sổ
kế toán của công ty; người dùng chỉ chọn từ danh mục, không thêm hoặc sửa nhóm. Danh mục chỉ đổi qua bản cập nhật
phần mềm. Thời gian khấu hao, phân bổ thì nhập tự do.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from dataclasses import dataclass, field

NGUONG_TSCD = 30_000_000          # đơn giá từ 30 triệu đồng và dùng trên 1 năm là TSCĐ
# Nhóm dịch vụ (sửa chữa, thuê ngoài, thuê theo tháng): tên hàng nhắc tới tài sản thì vẫn là chi phí dịch vụ
SERVICE_GROUPS = ("CP03", "CP10", "CP13")

# Phân loại một dòng hóa đơn
CHUA_PHAN_LOAI = "Chưa phân loại"
CHI_PHI = "Chi phí trong kỳ"
TSCD = "TSCĐ"
CCDC = "CCDC"
CHO_PB = "Chờ phân bổ"
KHONG_TINH = "Không tính chi phí"
PHAN_LOAI = (CHUA_PHAN_LOAI, CHI_PHI, TSCD, CCDC, CHO_PB, KHONG_TINH)
TAI_SAN = (TSCD, CCDC, CHO_PB)    # các loại đưa vào sổ TSCĐ / sổ CCDC


@dataclass(frozen=True)
class Nhom:
    ma: str
    ten: str
    kieu: str                 # GIA_TRI (theo đơn giá: TSCĐ/CCDC), CCDC, CHO_PB, CHI_PHI, KHONG
    nhom_khung: str           # nhóm khung khấu hao, Phụ lục I TT 45/2013
    khung: str
    thang: int | None         # thời gian mặc định (tháng) khi là CCDC / chờ phân bổ
    bac: tuple | None         # (ngưỡng đơn giá, số tháng dưới ngưỡng, số tháng từ ngưỡng)
    dong: str                 # dòng bảng lãi lỗ: '6', '7', '6a'; tài sản đi qua sổ
    tu_khoa: list = field(default_factory=list)
    ghi_chu: str = ""


CATALOG: list[Nhom] = [
    Nhom('TB01', 'Ô tô, xe máy, phương tiện vận tải', 'GIA_TRI', 'D.1', '6–10 năm', 72, None, 'TSCĐ/CCDC',
         ['ô tô', 'oto', 'xe ô tô', 'xe con', 'xe tải', 'xe bán tải', 'xe điện', 'vinfast', 'toyota', 'hyundai', 'ford', 'kia', 'mazda', 'xe máy', 'xe gắn máy'],
         'Sổ kế toán: VinFast VF6 72 tháng, TK 21113. Ô tô dưới 9 chỗ: phần khấu hao của nguyên giá vượt 1,6 tỷ không được tính chi phí (NĐ 320/2025). Nguyên giá gồm cả lệ phí trước bạ, đăng ký, đăng kiểm.'),
    Nhom('TB02', 'Máy tính xách tay, máy tính để bàn, máy chủ', 'GIA_TRI', 'E.2', '3–8 năm', 36, None, 'TSCĐ/CCDC',
         ['máy tính xách tay', 'máy tính sách tay', 'mtxt', 'mtxd', 'laptop', 'notebook', 'macbook', 'máy tính để bàn', 'pc', 'case máy tính', 'máy chủ', 'server', 'workstation'],
         'Sổ kế toán: MacBook, HP, Lenovo đều 36 tháng.'),
    Nhom('TB03', 'Điện thoại, máy tính bảng, đồng hồ thông minh', 'GIA_TRI', 'E.2', '3–8 năm', 24, (5000000, 24, 36), 'TSCĐ/CCDC',
         ['điện thoại', 'đtdđ', 'dtdd', 'iphone', 'samsung galaxy', 'galaxy', 'samsung fold', 'samsung z fold', 'samsung z flip', 'redmi', 'xiaomi', 'oppo', 'vivo', 'ipad', 'máy tính bảng', 'tablet'],
         'Sổ kế toán: Redmi 24 tháng; Samsung A73, Fold6 (từ khoảng 6 triệu) 36 tháng. Từ 30 triệu (iPhone 16 Pro Max) là TSCĐ, 36 tháng.'),
    Nhom('TB04', 'Máy in, máy quét, máy photo, máy chiếu', 'GIA_TRI', 'E.2', '3–8 năm', 24, None, 'TSCĐ/CCDC',
         ['máy in', 'printer', 'máy quét', 'scanner', 'máy scan', 'máy photo', 'photocopy', 'máy chiếu', 'projector', 'máy hủy tài liệu'],
         'Sổ kế toán: máy in Brother, máy quét Fujitsu 24 tháng.'),
    Nhom('TB15', 'Màn hình máy tính', 'GIA_TRI', 'E.2', '3–8 năm', 12, None, 'TSCĐ/CCDC',
         ['màn hình', 'monitor'],
         'Sổ kế toán: các màn hình đều 12 tháng.'),
    Nhom('TB05', 'Linh kiện, phụ kiện tin học dùng trên 1 năm', 'CCDC', 'E.2', '3–8 năm', 12, None, 'TSCĐ/CCDC',
         ['ổ cứng', 'ssd', 'hdd', 'ram máy tính', 'bộ nhớ ram', 'usb', 'tai nghe', 'loa', 'webcam', 'bàn phím cơ', 'chuột không dây', 'bộ lưu điện', 'ups'],
         'Món dưới 1 triệu đồng cũng vào CCDC, phân bổ 12 tháng (anh chốt 21/10).'),
    Nhom('TB06', 'Thiết bị mạng, camera, tổng đài', 'GIA_TRI', 'E.2', '3–8 năm', 24, None, 'TSCĐ/CCDC',
         ['router', 'bộ phát wifi', 'wifi', 'switch', 'modem', 'camera', 'đầu ghi', 'tổng đài', 'nas'],
         ''),
    Nhom('TB07', 'Thiết bị điện gia dụng văn phòng', 'GIA_TRI', 'E.3', '5–10 năm', 24, None, 'TSCĐ/CCDC',
         ['điều hòa', 'máy lạnh', 'quạt', 'tủ lạnh', 'bình nóng lạnh', 'máy lọc không khí', 'máy hút ẩm'],
         'Sổ kế toán: điều hòa Casper, quạt trần 24 tháng.'),
    Nhom('TB16', 'Đồ gia dụng nhỏ văn phòng', 'CCDC', 'E.3', '5–10 năm', 12, None, 'TSCĐ/CCDC',
         ['máy lọc nước', 'cây nước nóng lạnh', 'lò vi sóng', 'nồi cơm', 'ấm siêu tốc', 'máy pha cà phê'],
         'Sổ kế toán: máy lọc nước Kangaroo 12 tháng, lò vi sóng 11 tháng.'),
    Nhom('TB17', 'Đồng hồ thông minh', 'CCDC', 'E.2', '3–8 năm', 12, (5000000, 12, 24), 'TSCĐ/CCDC',
         ['đồng hồ thông minh', 'đồng hồ', 'đông hồ', 'smartwatch', 'watch', 'apple watch', 'galaxy watch'],
         'Sổ kế toán: Huawei 2 triệu 12 tháng; Huawei GT6 7,6 triệu 24 tháng.'),
    Nhom('TB08', 'Bàn ghế, tủ, kệ, két sắt, nội thất', 'GIA_TRI', 'E.3', '5–10 năm', 24, None, 'TSCĐ/CCDC',
         ['bàn làm việc', 'ghế', 'tủ tài liệu', 'tủ hồ sơ', 'kệ sách', 'kệ tài liệu', 'giá kệ', 'két sắt', 'sofa', 'bàn họp', 'vách ngăn'],
         ''),
    Nhom('TB09', 'Thiết bị năng lượng, điện', 'GIA_TRI', 'A.4', '6–15 năm', 36, None, 'TSCĐ/CCDC',
         ['pin mặt trời', 'tấm pin', 'solar', 'biến tần', 'inverter', 'máy phát điện', 'ắc quy lưu trữ', 'pin lưu trữ', 'ổn áp'],
         'Xét theo đơn giá từng tấm/từng máy; hệ thống lắp đặt đồng bộ không tách rời thì xét giá trị cả hệ thống.'),
    Nhom('TB10', 'Máy ảnh, ống kính, flycam, thiết bị quay phim', 'GIA_TRI', 'E.3', '5–10 năm', 24, None, 'TSCĐ/CCDC',
         ['máy ảnh', 'camera sony', 'canon', 'nikon', 'ống kính', 'lens', 'flycam', 'drone', 'gimbal', 'máy quay'],
         'Nhóm khung do kế toán chọn (E.2 hoặc E.3) tùy công dụng.'),
    Nhom('TB11', 'Máy, dụng cụ đo đạc, thi công, khảo sát', 'GIA_TRI', 'E.1', '5–8 năm', 24, None, 'TSCĐ/CCDC',
         ['máy toàn đạc', 'máy thủy bình', 'gps rtk', 'máy đo', 'máy khoan', 'máy cắt', 'máy hàn', 'thước đo laser'],
         ''),
    Nhom('TB12', 'Phần mềm mua bản quyền vĩnh viễn', 'GIA_TRI', 'K', '2–20 năm', 36, None, 'TSCĐ/CCDC',
         ['bản quyền vĩnh viễn', 'license vĩnh viễn', 'perpetual', 'phần mềm bản quyền'],
         'Từ 30 triệu là TSCĐ vô hình; dưới 30 triệu đưa vào chờ phân bổ.'),
    Nhom('TB13', 'Sửa chữa lớn, cải tạo văn phòng, sơn chống nóng, chống thấm', 'CHO_PB', '', '', 24, None, 'TSCĐ/CCDC',
         ['cải tạo', 'sửa chữa lớn', 'sơn chống nóng', 'chống thấm', 'lắp đặt nội thất', 'thi công văn phòng'],
         'Nâng cấp làm tăng công năng TSCĐ thì cộng vào nguyên giá tài sản đó (kế toán chọn).'),
    Nhom('TB14', 'Phụ kiện lắp thêm cho xe', 'CHO_PB', '', '', 24, None, 'TSCĐ/CCDC',
         ['phim cách nhiệt', 'camera hành trình', 'bọc ghế', 'thảm sàn', 'độ xe'],
         ''),
    Nhom('PB01', 'Phí sử dụng đường bộ, bảo hiểm xe, bảo hiểm tài sản trả cho nhiều tháng', 'CHO_PB', '', '', 12, None, 'CCDC',
         ['phí sử dụng đường bộ', 'cước sử dụng đường bộ', 'phí bảo trì đường bộ', 'bảo hiểm vật chất', 'bảo hiểm thân vỏ', 'bảo hiểm tnds', 'bảo hiểm tài sản', 'bảo hiểm cháy nổ'],
         'Phân bổ theo số tháng ghi trên chứng từ (ví dụ 12, 18, 24, 30 tháng).'),
    Nhom('PB02', 'Dịch vụ phần mềm, chữ ký số, hóa đơn điện tử trả trước nhiều kỳ', 'CHO_PB', '', '', 12, None, 'CCDC',
         ['chữ ký số', 'token', 'i-van', 'ivan', 'hóa đơn điện tử', 'gói hóa đơn', 'phần mềm kế toán', 'phần mềm thuê bao', 'microsoft 365', 'google workspace', 'tên miền', 'hosting', 'cloud', 'vps'],
         'Gói từ 12 tháng trở lên thì phân bổ theo thời hạn gói; gói tháng thì là Chi phí trong kỳ.'),
    Nhom('PB03', 'Tiền thuê văn phòng, kho, xe trả trước nhiều tháng', 'CHO_PB', '', '', 12, None, 'CCDC',
         ['thuê văn phòng', 'tiền thuê nhà', 'thuê kho', 'thuê mặt bằng'],
         'Hóa đơn thuê của đúng tháng đó thì là Chi phí trong kỳ.'),
    Nhom('CP01', 'Xăng dầu', 'CHI_PHI', '', '', None, None, '6',
         ['xăng', 'dầu diesel', 'dầu do', 'ron 95', 'ron95', 'e5', 'petrolimex', 'pvoil'],
         'Xe phải đứng tên công ty hoặc có hợp đồng thuê xe.'),
    Nhom('CP02', 'Phí qua trạm, vé cầu đường, gửi xe, rửa xe', 'CHI_PHI', '', '', None, None, '6',
         ['cước đường bộ', 'etc', 'vetc', 'epass', 'giao thông số', 'trạm thu phí', 'phí qua trạm', 'vé cầu đường', 'gửi xe', 'trông giữ xe', 'rửa xe'],
         'Cước ETC từng lượt là chi phí ngay; phí đường bộ trả cho nhiều tháng thuộc PB01.'),
    Nhom('CP03', 'Sửa chữa, bảo dưỡng xe ô tô, xe máy và thiết bị (gara, thay lốp, thay dầu…)', 'CHI_PHI', '', '', None,
         None, '6',
         ['bảo dưỡng', 'thay dầu', 'sửa chữa', 'thay thế linh kiện', 'vá lốp', 'bảo trì', 'gara', 'garage',
          'đồng sơn', 'sơn xe', 'gò hàn', 'làm máy', 'má phanh', 'phanh xe', 'ắc quy', 'gầm xe', 'cân chỉnh',
          'đảo lốp', 'thay lốp', 'lốp xe', 'lốp ô tô', 'săm lốp', 'ắc quy xe', 'ắc quy ô tô', 'dầu nhớt', 'lọc gió', 'lọc dầu', 'nước làm mát', 'công thợ', 'tiền công sửa'],
         ''),
    Nhom('CP04', 'Văn phòng phẩm, mực in, giấy', 'CHI_PHI', '', '', None, None, '6',
         ['văn phòng phẩm', 'vpp', 'giấy a4', 'giấy in', 'mực in', 'hộp mực', 'bút', 'sổ tay', 'sổ sách', 'kẹp', 'file hồ sơ', 'phong bì'],
         ''),
    Nhom('CP05', 'Cước viễn thông, internet', 'CHI_PHI', '', '', None, None, '6',
         ['cước điện thoại', 'cước viễn thông', 'cước di động', 'internet', 'cáp quang', '4g', '5g', 'data', 'viettel', 'vnpt', 'mobifone', 'fpt telecom'],
         ''),
    Nhom('CP06', 'Điện, nước', 'CHI_PHI', '', '', None, None, '6',
         ['điện tiêu thụ', 'tiền điện', 'điện lực', 'evn', 'tiền nước', 'nước sạch', 'cấp nước', 'nước tiêu thụ',
          'nước sinh hoạt', 'phí bảo vệ môi trường', 'phí bvmt', 'nước thải'],
         ''),
    Nhom('CP07', 'Ăn uống, tiếp khách, quà tặng', 'CHI_PHI', '', '', None, None, '7',
         ['ăn uống', 'tiếp khách', 'nhà hàng', 'cà phê', 'cafe', 'đồ uống', 'quà tặng', 'hoa tươi', 'lẵng hoa', 'giỏ quà'],
         'Hóa đơn từ 5 triệu phải thanh toán không dùng tiền mặt mới được tính chi phí.'),
    Nhom('CP08', 'Công tác phí: vé máy bay, tàu xe, khách sạn, taxi', 'CHI_PHI', '', '', None, None, '7',
         ['vé máy bay', 'vietnam airlines', 'vietjet', 'bamboo', 'vé tàu', 'khách sạn', 'phòng nghỉ', 'lưu trú', 'taxi', 'grab', 'xanh sm'],
         ''),
    Nhom('CP09', 'Vật tư tiêu hao, đồ dùng nhỏ', 'CHI_PHI', '', '', None, None, '6',
         ['bóng đèn', 'ổ cắm', 'dây điện', 'cáp mạng', 'dây cáp', 'cáp hdmi', 'pin aa', 'chuột', 'bàn phím', 'nước uống', 'giấy vệ sinh', 'đồ dùng vệ sinh'],
         ''),
    Nhom('CP10', 'Dịch vụ thuê ngoài theo tháng', 'CHI_PHI', '', '', None, None, '6',
         ['vệ sinh', 'bảo vệ', 'vận chuyển', 'in ấn', 'photo tài liệu', 'dịch thuật', 'tư vấn', 'kiểm toán', 'phí dịch vụ'],
         ''),
    Nhom('CP11', 'Phí ngân hàng', 'CHI_PHI', '', '', None, None, '6',
         ['phí chuyển tiền', 'phí ngân hàng', 'phí quản lý tài khoản', 'phí sms banking'],
         ''),
    Nhom('CP12', 'Vật tư, hàng hóa, thầu phụ cho dự án', 'CHI_PHI', '', '', None, None, '6a',
         [],
         'Hóa đơn đã gắn hợp đồng đầu ra: tính vào chi phí của dự án đó.'),
    Nhom('CP13', 'Thuê văn phòng, thuê xe trả theo tháng', 'CHI_PHI', '', '', None, None, '6',
         ['tiền thuê tháng', 'thuê xe tháng', 'phí quản lý tòa nhà'],
         ''),
    Nhom('CP14', 'Đào tạo, tài liệu, hội nghị, quảng cáo', 'CHI_PHI', '', '', None, None, '7',
         ['đào tạo', 'khóa học', 'hội thảo', 'hội nghị', 'sách tham khảo', 'tài liệu chuyên môn', 'quảng cáo', 'facebook ads', 'google ads'],
         ''),
    Nhom('CP15', 'Sạc điện xe', 'CHI_PHI', '', '', None, None, '7',
         ['sạc điện', 'sạc xe', 'v-green', 'trạm sạc', 'vinfast charging'],
         ''),
    Nhom('CP16', 'Chuyển phát nhanh, giao hàng', 'CHI_PHI', '', '', None, None, '7',
         ['chuyển phát', 'cpn', 'viettel post', 'vnpost', 'ems', 'giao hàng nhanh', 'ghn', 'ghtk', 'j&t', 'ninja van'],
         ''),
    Nhom('KX01', 'Chi phí không phục vụ kinh doanh, chi cá nhân', 'KHONG', '', '', None, None, '',
         [],
         'Không đưa vào bảng lãi lỗ; vẫn lưu hóa đơn.'),]

BY_CODE = {n.ma: n for n in CATALOG}


@lru_cache(maxsize=50_000)       # gọi lại rất nhiều lần với cùng tên hàng: nhớ kết quả
def normalize(text: str) -> str:
    """Chữ thường, bỏ dấu, đ → d, tách chữ khỏi số, chỉ giữ chữ số và chữ cái; bọc hai đầu bằng dấu cách để so khớp nguyên từ."""
    t = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])", " ", t)     # "fold6" → "fold 6"
    return " " + re.sub(r"[^a-z0-9]+", " ", t).strip() + " "


_KEYWORDS = sorted(((normalize(k), n) for n in CATALOG for k in n.tu_khoa if normalize(k).strip()),
                   key=lambda x: -len(x[0]))
_RAW = {(normalize(k), n.ma): unicodedata.normalize("NFC", k.lower()) for n in CATALOG for k in n.tu_khoa}


@lru_cache(maxsize=50_000)       # gọi lại rất nhiều lần với cùng tên hàng: nhớ kết quả
def find_group(name: str) -> Nhom | None:
    """Nhóm có từ khóa dài nhất xuất hiện trong tên hàng hóa, dịch vụ."""
    n = normalize(name)
    for k, nhom in _KEYWORDS:
        if k in n:
            return nhom
    return None


def _has_marks(text: str) -> bool:
    return any(unicodedata.category(c) == "Mn" for c in unicodedata.normalize("NFD", text)) or "đ" in text


@lru_cache(maxsize=50_000)       # gọi lại rất nhiều lần với cùng tên hàng: nhớ kết quả
def rival_groups(name: str, don_gia: int = 0) -> tuple[Nhom, ...]:
    """Nhóm khác cũng khớp tên hàng ở một cụm từ riêng (không nằm trong cụm của nhóm khớp nhất) mà dẫn tới phân loại
    khác: tên hàng mơ hồ (VD "máy tính kèm dịch vụ cài đặt"), không tự chọn được."""
    n = normalize(name)
    best, span, out = None, None, []
    for k, nhom in _KEYWORDS:
        i = n.find(k)
        if i < 0:
            continue
        if best is None:
            best, span = nhom, (i, i + len(k))
            continue
        if nhom is best or nhom in out:
            continue
        raw = _RAW.get((k, nhom.ma), "")
        if raw and _has_marks(raw) and raw not in unicodedata.normalize("NFC", str(name).lower()):    # trùng chữ khi bỏ dấu (lưu trữ / lưu trú)
            continue
        if span[0] <= i and i + len(k) <= span[1]:          # cụm ngắn nằm trong cụm dài đã khớp: không tính
            continue
        if default_class(nhom, don_gia) != default_class(best, don_gia):
            out.append(nhom)
    return tuple(out)


def allowed_classes(nhom: Nhom, don_gia: int = 0) -> list[str]:
    """Các phân loại được chọn cho một nhóm (kế toán chỉ chọn trong danh sách này)."""
    if nhom.kieu in ("GIA_TRI", "CCDC"):
        return [TSCD, CCDC] if nhom.kieu == "GIA_TRI" or don_gia >= NGUONG_TSCD else [CCDC, TSCD]
    if nhom.kieu == "CHO_PB":
        return [CHO_PB, CHI_PHI]
    if nhom.kieu == "CHI_PHI":
        return [CHI_PHI]
    return [KHONG_TINH]


def default_class(nhom: Nhom, don_gia: int) -> str:
    if nhom.kieu in ("GIA_TRI", "CCDC"):
        return TSCD if don_gia >= NGUONG_TSCD else CCDC
    return {"CHO_PB": CHO_PB, "CHI_PHI": CHI_PHI}.get(nhom.kieu, KHONG_TINH)


def default_months(nhom: Nhom, phan_loai: str, don_gia: int) -> int | None:
    """Thời gian mặc định như sổ kế toán: TSCĐ lấy mức tối thiểu của khung (ô tô 72 tháng, còn lại 36 tháng);
    CCDC theo bậc đơn giá nếu có, không thì theo nhóm; món dưới 1 triệu đồng 12 tháng.
    Nhóm chờ phân bổ trả về None: kế toán nhập theo thời hạn trên chứng từ."""
    if phan_loai == TSCD:
        return 72 if nhom.ma == "TB01" else 36
    if phan_loai == CCDC:
        if don_gia < 1_000_000:
            return 12
        if nhom.bac:
            nguong, duoi, tren = nhom.bac
            return duoi if don_gia < nguong else tren
        return min(nhom.thang or 12, 36)
    return None


@dataclass(frozen=True)       # dùng chung qua bộ nhớ đệm của classify: không sửa tại chỗ
class KetQua:
    phan_loai: str
    nhom: Nhom | None          # với "Chưa phân loại": nhóm gợi ý (chưa chọn), để kế toán xác nhận
    so_thang: int | None
    ly_do: str = ""

    @property
    def can_ke_toan(self) -> bool:
        return self.phan_loai == CHUA_PHAN_LOAI or (self.phan_loai in TAI_SAN and not self.so_thang)


@lru_cache(maxsize=50_000)       # gọi lại rất nhiều lần với cùng tên hàng: nhớ kết quả
def classify(name: str, don_gia: int, *, co_hop_dong: bool = False) -> KetQua:
    """Phân loại một dòng hóa đơn đầu vào theo tên và đơn giá chưa thuế của một đơn vị.
    Không khớp từ khóa nào: hóa đơn gắn hợp đồng đầu vào thì là vật tư, thầu phụ dự án (CP12), còn lại để kế toán
    phân loại."""
    nhom = find_group(name)
    if nhom is None:
        if co_hop_dong:
            return KetQua(CHI_PHI, BY_CODE["CP12"], None)
        return KetQua(CHUA_PHAN_LOAI, None, None)
    rivals = rival_groups(name, don_gia)
    service = next((g for g in [nhom, *rivals] if g.ma in SERVICE_GROUPS), None)
    if rivals and service is not None:      # sửa chữa, thuê, dịch vụ cho một tài sản: là chi phí dịch vụ đó
        nhom, rivals = service, []
    if rivals:      # tên hàng khớp nhiều nhóm khác phân loại: không tự chọn, chỉ gợi ý
        return KetQua(CHUA_PHAN_LOAI, nhom, None, ambiguity_text(nhom, rivals))
    pl = default_class(nhom, don_gia)
    return KetQua(pl, nhom, default_months(nhom, pl, don_gia))


def ambiguity_text(nhom: Nhom, rivals: list[Nhom]) -> str:
    return (f"Tên hàng khớp nhiều nhóm: {nhom.ma} – {nhom.ten}; "
            + "; ".join(f"{r.ma} – {r.ten}" for r in rivals[:3]) + ".")


def near_threshold(don_gia: int) -> bool:
    """Đơn giá sát ngưỡng 30 triệu (±15%): dễ nhầm CCDC với TSCĐ, cần kế toán xác nhận."""
    return NGUONG_TSCD * 0.85 <= (don_gia or 0) < NGUONG_TSCD * 1.15


def group_options(phan_loai: str | None = None, *, all_groups: bool = False) -> list[tuple[str, str]]:
    """(nhãn, mã nhóm) cho hộp chọn; nhóm hợp với phân loại lên đầu. all_groups: kèm cả các nhóm thuộc phân loại
    khác (ghi rõ phân loại của nhóm) để kế toán tìm thấy mọi nhóm; chọn nhóm đó thì phân loại đổi theo."""
    out = []
    for n in CATALOG:
        fits = not phan_loai or phan_loai in allowed_classes(n, NGUONG_TSCD) + allowed_classes(n, 0)
        if not fits and not all_groups:
            continue
        main = not phan_loai or phan_loai in (default_class(n, 0), default_class(n, NGUONG_TSCD))
        label = f"{n.ma} – {n.ten}"
        if not fits:
            label += f"  (→ {default_class(n, 0)})"
        out.append((0 if main else 1 if fits else 2, label, n.ma))
    return [(label, ma) for _, label, ma in sorted(out, key=lambda x: x[0])]


def fixed_asset_kind(ma_nhom: str | None) -> tuple[str, str, str]:
    """(Loại TSCĐ, TK nguyên giá, TK khấu hao) theo nhóm, như sổ kế toán."""
    if ma_nhom == "TB01":
        return "Phương tiện vận tải, truyền dẫn", "21113", "2141"
    if ma_nhom == "TB12":
        return "TSCĐ vô hình", "2135", "2143"
    return "Máy móc, thiết bị", "21112", "2141"


def parse_class_text(text: str, ten_hang: str, don_gia: int) -> tuple[KetQua | None, str]:
    """Đọc ô "Loại hàng hóa" người dùng ghi trong file Excel: mã nhóm (VD "CP03 – Sửa chữa…"), có thể kèm
    phân loại ("TSCĐ", "CCDC"…), hoặc chỉ tên nhóm / phân loại. Trả (kết quả, ghi chú); không hiểu thì (None, lý do)."""
    t = (text or "").strip()
    if not t:
        return None, ""
    low = normalize(t)
    pl = next((c for c in (CHO_PB, KHONG_TINH, CHI_PHI, TSCD, CCDC) if normalize(c) in low), None)
    m = re.search(r"\b([A-Za-z]{2}\d{2})\b", t)
    nhom = BY_CODE.get(m.group(1).upper()) if m else None
    if nhom is None:
        nhom = find_group(t)
        if nhom is None and pl:
            g = find_group(ten_hang)
            nhom = g if g and pl in allowed_classes(g, don_gia) else None
    if nhom is None:
        if pl:
            return None, (f'Loại hàng ghi "{pl}" nhưng chưa rõ nhóm: ghi kèm mã nhóm (VD "{pl} TB05"), '
                          "hoặc phân loại sau khi nhập.")
        return None, f'Không hiểu loại hàng "{t}": ghi mã nhóm trong danh mục (VD CP03), hãy phân loại sau khi nhập.'
    if pl is None or pl not in allowed_classes(nhom, don_gia):
        if pl:
            return None, f'Nhóm {nhom.ma} không xếp vào "{pl}" được, hãy phân loại sau khi nhập.'
        pl = default_class(nhom, don_gia)
    months = default_months(nhom, pl, don_gia)
    note = f"Loại hàng theo file: {pl} · {nhom.ma} – {nhom.ten}."
    if pl in TAI_SAN and not months:
        note += " Nhập số tháng phân bổ ở thẻ Tài sản, CCDC sau khi nhập."
    return KetQua(pl, nhom, months), note
