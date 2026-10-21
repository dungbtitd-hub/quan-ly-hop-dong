"""Phần mềm cấp mã của Nhà phát triển (Công ty cổ phần Công nghệ ITD Việt Nam).

Dùng riêng tại nhà phát triển, KHÔNG phát hành cho khách hàng. Giữ tệp khóa bí mật cẩn thận:
ai có tệp này đều cấp được Key Giám đốc.

Cặp khóa là CỐ ĐỊNH: phần mềm chỉ nhận tệp khóa bí mật khớp với khóa công khai chính thức
đã đóng gói trong phần mềm Quản lý Hợp đồng (qlhd/core/vendor_pubkey.py). Giao diện không có chức năng
tạo cặp khóa mới. Thay khóa chính thức (làm mọi Key đã cấp mất hiệu lực) chỉ làm được từ mã nguồn:
    python tools/license_manager.py init --out KHOA_MOI.key --write-app --thay-khoa-chinh-thuc

Dòng lệnh:
    python tools/license_manager.py issue --key KHOA_BI_MAT.key --request "QLHD-YC:..." \
        --company "Công ty ABC" --mst 0101234567 --accounts 10
    python tools/license_manager.py            (mở giao diện)
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qlhd.core import licensing as lic  # noqa: E402

PUBKEY_FILE = Path(__file__).resolve().parents[1] / "qlhd" / "core" / "vendor_pubkey.py"


def init_keypair(out: Path) -> str:
    if out.exists():
        raise SystemExit(f"Tệp {out} đã tồn tại, không ghi đè.")
    priv = lic.new_private_key()
    out.write_text(lic.private_to_b64(priv) + "\n")
    return lic.public_to_b64(priv.public_key())


KEY_FILE_NAME = "KHOA_BI_MAT_NHA_PHAT_TRIEN.key"


class WrongKeyError(Exception):
    pass


def official_pub():
    """Khóa công khai chính thức đóng gói trong phần mềm Quản lý Hợp đồng."""
    return lic.vendor_public_key()


def fingerprint(pub) -> str:
    b = lic.public_to_b64(pub)
    return f"{b[:6]}…{b[-6:]}"


def load_key(path: Path):
    try:
        priv = lic.private_from_b64(Path(path).read_text().strip())
    except Exception:  # noqa: BLE001
        raise WrongKeyError(f"Tệp {Path(path).name} không phải tệp khóa bí mật hợp lệ.") from None
    if lic.public_to_b64(priv.public_key()) != lic.public_to_b64(official_pub()):
        raise WrongKeyError(f"Tệp {Path(path).name} không khớp với cặp khóa chính thức "
                            f"({fingerprint(official_pub())}). Hãy chọn đúng tệp {KEY_FILE_NAME}.")
    return priv


def find_default_key() -> Path | None:
    """Tìm tệp khóa cạnh phần mềm cấp mã, trong Tài liệu hoặc thư mục người dùng."""
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        here = Path(sys.executable).parents[3]          # thư mục chứa CapMaBanQuyen.app
    for d in (here, Path.home() / "Documents", Path.home()):
        if (d / KEY_FILE_NAME).is_file():
            return d / KEY_FILE_NAME
    return None


def issue(key_path: Path, request: str, company: str, mst: str, accounts: int) -> str:
    priv = load_key(key_path)
    key = lic.issue_director_key(priv, request, company, mst, accounts)
    payload = lic.verify_director_key(key, vendor_pub=priv.public_key())
    log = Path(key_path).with_name("NhatKyCapKey.csv")
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Thời điểm", "Mã Key", "Công ty", "MST", "Mã máy", "Số tài khoản", "Key"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), payload["id"], company, mst, payload["may"],
                    accounts, key])
    return key


def write_pubkey_module(pub: str, label: str = "PHAT-HANH") -> None:
    PUBKEY_FILE.write_text(f'''"""Khóa công khai của Nhà phát triển, dùng để kiểm tra Key Giám đốc.

Thay bằng khóa công khai do `python tools/license_manager.py init` in ra trước khi phát hành chính thức.
"""
VENDOR_PUBKEY = "{pub}"
VENDOR_KEY_LABEL = "{label}"
''', encoding="utf-8")


# ---------------------------------------------------------------- giao diện
def run_gui() -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import (QApplication, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                   QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

    from qlhd.developer import DEVELOPER
    from qlhd.ui.style import WIN7_QSS

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(WIN7_QSS)
    w = QWidget()
    w.setWindowTitle(f"Phần mềm cấp mã – {DEVELOPER['ten']}")
    w.resize(720, 560)
    lay = QVBoxLayout(w)
    title = QLabel("CẤP KEY GIÁM ĐỐC – PHẦN MỀM QUẢN LÝ HỢP ĐỒNG")
    title.setObjectName("PageTitle")
    lay.addWidget(title)
    settings = QSettings("ITD Viet Nam", "CapMaBanQuyen")
    fixed = QLabel(f"Cặp khóa cố định của phần mềm: {fingerprint(official_pub())}. "
                   f"Chỉ nhận tệp {KEY_FILE_NAME} khớp với khóa này.")
    fixed.setObjectName("Hint")
    fixed.setWordWrap(True)
    lay.addWidget(fixed)
    form = QFormLayout()
    key_path = QLineEdit()
    key_path.setReadOnly(True)
    key_state = QLabel("")
    pick = QPushButton("Chọn tệp khóa…")
    row = QHBoxLayout()
    row.addWidget(key_path, 1)
    row.addWidget(pick)
    form.addRow("Tệp khóa bí mật", row)
    form.addRow("", key_state)
    request = QPlainTextEdit()
    request.setPlaceholderText("Dán Mã yêu cầu (QLHD-YC:…) khách hàng gửi")
    request.setFixedHeight(70)
    company = QLineEdit()
    mst = QLineEdit()
    accounts = QSpinBox()
    accounts.setRange(1, 1000)
    accounts.setValue(10)
    form.addRow("Mã yêu cầu", request)
    form.addRow("Tên công ty khách hàng", company)
    form.addRow("Mã số thuế", mst)
    form.addRow("Số tài khoản nhân viên tối đa", accounts)
    lay.addLayout(form)
    info_lbl = QLabel("")
    info_lbl.setObjectName("Hint")
    lay.addWidget(info_lbl)
    go = QPushButton("Cấp Key")
    go.setObjectName("Primary")
    lay.addWidget(go)
    out = QPlainTextEdit()
    out.setReadOnly(True)
    lay.addWidget(out)
    copy = QPushButton("Sao chép Key")
    lay.addWidget(copy)

    def on_request_changed():
        try:
            r = lic.read_request(request.toPlainText())
            info_lbl.setText(f"Mã máy khách hàng: {r['may']}")
        except lic.LicenseError:
            info_lbl.setText("")

    def use_key(p: str, quiet: bool = False) -> bool:
        try:
            load_key(Path(p))
        except (WrongKeyError, OSError) as e:
            if not quiet:
                QMessageBox.warning(w, "Tệp khóa không đúng", str(e))
            return False
        key_path.setText(p)
        key_state.setText("✔ Đúng cặp khóa chính thức")
        key_state.setStyleSheet("color:#1e7b34; font-weight:600;")
        settings.setValue("tep_khoa", p)
        return True

    def on_pick():
        p, _ = QFileDialog.getOpenFileName(w, "Chọn tệp khóa bí mật", "", "Khóa (*.key);;Tất cả (*)")
        if p:
            use_key(p)

    def on_issue():
        if not key_path.text() or not company.text().strip() or not mst.text().strip():
            return QMessageBox.warning(w, "Thiếu thông tin", "Chọn tệp khóa, nhập tên công ty và MST.")
        try:
            k = issue(Path(key_path.text()), request.toPlainText(), company.text().strip(), mst.text().strip(),
                      accounts.value())
        except Exception as e:  # noqa: BLE001
            return QMessageBox.warning(w, "Lỗi", str(e))
        out.setPlainText(k)

    request.textChanged.connect(on_request_changed)
    pick.clicked.connect(on_pick)
    remembered = settings.value("tep_khoa", "")
    if not (remembered and use_key(remembered, quiet=True)):
        found = find_default_key()
        if not (found and use_key(str(found), quiet=True)):
            key_state.setText("Chưa chọn tệp khóa. Bấm \"Chọn tệp khóa…\" (chỉ cần chọn một lần).")
    go.clicked.connect(on_issue)
    copy.clicked.connect(lambda: QApplication.clipboard().setText(out.toPlainText()))
    w.show()
    return app.exec()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phần mềm cấp mã của Nhà phát triển")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("init", help="Thay cặp khóa chính thức (mọi Key đã cấp mất hiệu lực)")
    a.add_argument("--out", default="KHOA_BI_MAT_NHA_PHAT_TRIEN.key")
    a.add_argument("--write-app", action="store_true", help="Ghi luôn khóa công khai vào qlhd/core/vendor_pubkey.py")
    a.add_argument("--thay-khoa-chinh-thuc", action="store_true",
                   help="Xác nhận thay cặp khóa chính thức đang dùng")
    b = sub.add_parser("issue", help="Cấp Key Giám đốc từ Mã yêu cầu")
    b.add_argument("--key", required=True)
    b.add_argument("--request", required=True)
    b.add_argument("--company", required=True)
    b.add_argument("--mst", required=True)
    b.add_argument("--accounts", type=int, default=10)
    args = ap.parse_args(argv)
    if args.cmd == "init":
        if getattr(sys, "frozen", False):
            raise SystemExit("Phần mềm cấp mã dùng cặp khóa cố định, không tạo cặp khóa mới.")
        if not args.thay_khoa_chinh_thuc:
            raise SystemExit("Đang dùng cặp khóa chính thức cố định. Chỉ thay khi thật cần, thêm "
                             "--thay-khoa-chinh-thuc để xác nhận (mọi Key đã cấp sẽ mất hiệu lực).")
        pub = init_keypair(Path(args.out))
        print("Đã tạo tệp khóa bí mật:", args.out)
        print("Khóa công khai:", pub)
        if args.write_app:
            write_pubkey_module(pub)
            print("Đã ghi vào", PUBKEY_FILE)
        return 0
    if args.cmd == "issue":
        print(issue(Path(args.key), args.request, args.company, args.mst, args.accounts))
        return 0
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
