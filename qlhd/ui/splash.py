"""Màn hình thông tin nhà phát triển: hiện mỗi lần khởi chạy và trong Trợ giúp → Giới thiệu."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import APP_NAME, __version__
from ..developer import DEVELOPER, copyright_line, developer_lines


class _Banner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(92)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#4f8fd8"))
        g.setColorAt(0.5, QColor("#2f6fbf"))
        g.setColorAt(1, QColor("#1d4f93"))
        p.fillRect(self.rect(), g)
        p.setPen(QPen(QColor(255, 255, 255, 60)))
        p.drawLine(0, self.height() // 2, self.width(), self.height() // 2)
        # biểu tượng tài liệu
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(24, 18, 44, 56, 4, 4)
        for i in range(4):
            p.drawLine(32, 32 + i * 10, 60, 32 + i * 10)
        p.setPen(QColor("white"))
        f = QFont(self.font())
        f.setPointSize(17)
        f.setBold(True)
        p.setFont(f)
        p.drawText(86, 46, APP_NAME.upper())
        f.setPointSize(9)
        f.setBold(False)
        p.setFont(f)
        p.drawText(88, 68, f"Phiên bản {__version__} · Hợp đồng · Hóa đơn · Thu chi · Đồng bộ nhiều máy")


class DeveloperInfo(QWidget):
    def __init__(self, license_text: str, extra: list[tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 14, 24, 8)
        cap = QLabel("NHÀ PHÁT TRIỂN")
        cap.setStyleSheet("color:#5a6b80; font-weight:600; letter-spacing:1px;")
        lay.addWidget(cap)
        name = QLabel(DEVELOPER["ten"])
        name.setStyleSheet("color:#1e395b; font-size:12pt; font-weight:700;")
        lay.addWidget(name)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        rows = developer_lines() + (extra or [])
        for i, (label, value) in enumerate(rows):
            l1 = QLabel(label + ":")
            l1.setStyleSheet("color:#5a6b80;")
            l2 = QLabel(value)
            l2.setTextInteractionFlags(Qt.TextSelectableByMouse)
            l2.setStyleSheet("color:#1b1b1b;")
            grid.addWidget(l1, i, 0, Qt.AlignRight)
            grid.addWidget(l2, i, 1)
        lay.addLayout(grid)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#c9d6e8;")
        lay.addWidget(line)
        lic = QLabel(license_text)
        lic.setWordWrap(True)
        if "HẾT HIỆU LỰC" in license_text:
            color = "#a32020"
        elif "Đã kích hoạt" in license_text and "(còn" not in license_text:
            color = "#1e7b34"
        else:
            color = "#9c5700"
        lic.setStyleSheet(f"color:{color}; font-weight:600;")
        lay.addWidget(lic)
        cp = QLabel(copyright_line())
        cp.setStyleSheet("color:#5a6b80;")
        lay.addWidget(cp)


def license_text(acc) -> str:
    row = acc.license() if acc is not None else None
    if not row:
        return "Bản quyền: Chưa kích hoạt"
    loai = "Doanh nghiệp" if row["LoaiKichHoat"] == "Giám đốc" else row["LoaiKichHoat"]
    st = acc.license_status()
    if st["trang_thai"] in ("het_han", "lui_ngay", "loi"):
        return f"Bản quyền: HẾT HIỆU LỰC · {row['TenCongTy']}"
    han = "vĩnh viễn" if not st["han"] else "đến " + date.fromisoformat(st["han"]).strftime("%d/%m/%Y")
    if st["trang_thai"] == "sap_het":
        han += f" (còn {st['con_lai']} ngày)"
    return f"Bản quyền: Đã kích hoạt · {loai} · {han} · {row['TenCongTy']}"


class SplashDialog(QDialog):
    """Hiện khi khởi chạy; tự đóng sau vài giây hoặc khi bấm Tiếp tục."""

    def __init__(self, acc=None, seconds: int = 4, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("Splash")
        self.setStyleSheet("#Splash { background:white; border:1px solid #6d8fbf; }")
        self.setFixedWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_Banner())
        lay.addWidget(DeveloperInfo(license_text(acc)))
        bottom = QHBoxLayout()
        bottom.setContentsMargins(24, 4, 16, 14)
        self.count = QLabel()
        self.count.setStyleSheet("color:#8a97a8;")
        bottom.addWidget(self.count)
        bottom.addStretch(1)
        btn = QPushButton("Tiếp tục")
        btn.setObjectName("Primary")
        btn.setDefault(True)
        btn.clicked.connect(self.accept)
        bottom.addWidget(btn)
        lay.addLayout(bottom)
        self.remaining = seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        if seconds > 0:
            self._tick()
            self.timer.start(1000)

    def _tick(self):
        if self.remaining <= 0:
            self.timer.stop()
            self.accept()
            return
        self.count.setText(f"Tự đóng sau {self.remaining} giây")
        self.remaining -= 1

    def mousePressEvent(self, _):
        self.accept()


class AboutDialog(QDialog):
    def __init__(self, acc, machine_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Giới thiệu")
        self.setFixedWidth(540)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.addWidget(_Banner())
        lay.addWidget(DeveloperInfo(license_text(acc), [("Mã máy này", machine_id)]))
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Đóng")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.setContentsMargins(0, 0, 16, 0)
        lay.addLayout(row)
