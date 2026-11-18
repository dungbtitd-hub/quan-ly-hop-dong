"""Thông tin nhà phát triển, hiển thị mỗi lần khởi chạy phần mềm và trong mục Giới thiệu."""

DEVELOPER = {
    "ten": "Công ty cổ phần Công nghệ ITD Việt Nam",
    "dien_thoai": "0988095867",
    "email": "vanthu.itdvn@gmail.com",
    "website": "itdvietnam.com.vn",
    "dia_chi": "Số 1 Ngõ 30 đường Lý Sơn, Hà Nội",
    "nam": "2026",
}


def developer_lines() -> list[tuple[str, str]]:
    return [
        ("Điện thoại / Zalo", DEVELOPER["dien_thoai"]),
        ("Email", DEVELOPER["email"]),
        ("Website", DEVELOPER["website"]),
        ("Địa chỉ", DEVELOPER["dia_chi"]),
    ]


def copyright_line() -> str:
    return f"© {DEVELOPER['nam']} {DEVELOPER['ten']}"
