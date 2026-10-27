"""Thông báo trên phần mềm (chuông, bản tin hằng ngày) và cài đặt gửi qua Telegram."""
from __future__ import annotations

import threading
from datetime import date, datetime

from PySide6.QtCore import QObject, Qt, QTime, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QPushButton, QScrollArea, QTimeEdit, QVBoxLayout,
                               QWidget)

from ..core import alerts as al
from .widgets import error, hint, info, tagged


class AlertRow(QFrame):
    clicked = Signal()

    def __init__(self, a: al.Alert, parent=None):
        super().__init__(parent)
        tagged(self, "AlertRow", a.muc)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Bấm để mở" if a.ma_hd or a.loai == "hddv_xem_lai" else "")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 4, 6)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel(a.tieu_de)
        t.setWordWrap(True)
        t.setStyleSheet("font-weight:600;")
        col.addWidget(t)
        if a.chi_tiet:
            col.addWidget(hint(a.chi_tiet))
        lay.addLayout(col, 1)
        if a.nhan:
            lay.addWidget(tagged(QLabel(a.nhan), "Pill", a.muc), 0, Qt.AlignTop)

    def mouseReleaseEvent(self, ev):
        self.clicked.emit()
        super().mouseReleaseEvent(ev)


class TypePicker(QWidget):
    """Ô tích chọn các ngữ cảnh thông báo."""
    changed = Signal()

    def __init__(self, chosen, counts: dict | None = None, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        self.boxes = {}
        for i, (code, (name, desc)) in enumerate(al.ALERT_TYPES.items()):
            n = (counts or {}).get(code)
            b = QCheckBox(name + (f" ({n})" if n else ""))
            b.setToolTip(desc)
            b.setChecked(code in chosen)
            b.toggled.connect(lambda _=False: self.changed.emit())
            grid.addWidget(b, i // 2, i % 2)
            self.boxes[code] = b

    def values(self) -> list[str]:
        return [c for c, b in self.boxes.items() if b.isChecked()]


class NotificationsDialog(QDialog):
    """Bảng thông báo: chọn ngữ cảnh muốn theo dõi, bấm dòng để mở hợp đồng / màn hình liên quan."""
    open_contract = Signal(str)
    open_review = Signal()

    def __init__(self, ctx, parent=None, title: str = "Thông báo"):
        super().__init__(parent)
        self.ctx = ctx
        self.settings = al.AlertSettings(ctx.db)
        self.user = ctx.user["TenDangNhap"]
        self.setWindowTitle(title)
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        self.all = al.collect(ctx.db)
        lay.addWidget(QLabel("Ngữ cảnh thông báo (tích để theo dõi, phần mềm nhớ cho tài khoản này):"))
        self.picker = TypePicker(self.settings.app_types(self.user), al.counts(self.all))
        self.picker.changed.connect(self._changed)
        lay.addWidget(self.picker)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self.list = QVBoxLayout(body)
        self.list.setSpacing(6)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        bb = QDialogButtonBox()
        if ctx.user["VaiTro"] == "Giám đốc":
            b = bb.addButton("Cài đặt Telegram…", QDialogButtonBox.ActionRole)
            b.clicked.connect(lambda: TelegramDialog(ctx, self).exec())
        bb.addButton("Đóng", QDialogButtonBox.RejectRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()

    def _changed(self):
        self.settings.set_app_types(self.user, self.picker.values())
        self._fill()
        win = self.parent().window() if self.parent() else None
        if win is not None and hasattr(win, "update_badges"):
            win.update_badges()

    def _fill(self):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        chosen = set(self.picker.values())
        items = [a for a in self.all if a.loai in chosen]
        for code, (name, _) in al.ALERT_TYPES.items():
            group = [a for a in items if a.loai == code]
            if not group:
                continue
            head = QLabel(f"{name} ({len(group)})")
            head.setObjectName("CardTitle")
            self.list.addWidget(head)
            for a in group[:50]:
                row = AlertRow(a)
                row.clicked.connect(lambda a=a: self._open(a))
                self.list.addWidget(row)
            if len(group) > 50:
                self.list.addWidget(hint(f"… và {len(group) - 50} mục khác."))
        if not items:
            self.list.addWidget(tagged(QLabel("Không có việc cần nhắc trong các ngữ cảnh đã chọn."), "Success"))
        self.list.addStretch(1)

    def _open(self, a: al.Alert):
        if a.ma_hd:
            self.open_contract.emit(a.ma_hd)
            self.accept()
        elif a.loai == "hddv_xem_lai":
            self.open_review.emit()
            self.accept()


class _Sender(QObject):
    done = Signal(str)          # "" = gửi được, ngược lại là lỗi


def send_digest_async(ctx, on_done=None, force: bool = False) -> bool:
    """Gửi bản tin qua Telegram ở luồng nền (không làm đơ phần mềm). Trả False nếu chưa cài đặt."""
    st = al.AlertSettings(ctx.db)
    tg = st.telegram()
    if not tg["token"] or not tg["chat"] or (not tg["bat"] and not force):
        return False
    text = al.digest_text(al.collect(ctx.db, types=tg["loai"]), ctx.company)
    sender = _Sender()
    ctx._tg_sender = sender                    # giữ đối tượng tới khi luồng gửi xong
    if on_done:
        sender.done.connect(on_done)

    def run():
        try:
            al.tg_send(tg["token"], tg["chat"], text)
            sender.done.emit("")
        except al.TelegramError as e:
            sender.done.emit(str(e))

    threading.Thread(target=run, daemon=True).start()
    return True


class TelegramDialog(QDialog):
    """Cài đặt nhận thông báo qua Telegram: bot, người nhận, giờ gửi bản tin, các ngữ cảnh gửi."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.st = al.AlertSettings(ctx.db)
        tg = self.st.telegram()
        self.setWindowTitle("Cảnh báo qua Telegram")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.addWidget(hint("1) Trên Telegram, nhắn @BotFather lệnh /newbot để tạo bot, chép mã bot (token) dán vào dưới. "
                           "2) Người nhận (hoặc nhóm có thêm bot) nhắn /start cho bot, rồi bấm \"Tìm người nhận\". "
                           "Mã bot chỉ lưu trên máy này, không gửi kèm gói đồng bộ."))
        form = QFormLayout()
        self.bat = QCheckBox("Gửi bản tin hằng ngày qua Telegram")
        self.bat.setChecked(tg["bat"])
        form.addRow("", self.bat)
        self.token = QLineEdit(tg["token"])
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("VD 123456789:AA…")
        form.addRow("Mã bot (token)", self.token)
        chat_row = QHBoxLayout()
        self.chat = QLineEdit(tg["chat"])
        self.chat.setPlaceholderText("Mã người nhận / nhóm (chat id)")
        chat_row.addWidget(self.chat, 1)
        find = QPushButton("Tìm người nhận")
        find.clicked.connect(self._find)
        chat_row.addWidget(find)
        form.addRow("Người nhận", chat_row)
        self.gio = QTimeEdit(QTime.fromString(tg["gio"], "HH:mm"))
        self.gio.setDisplayFormat("HH:mm")
        form.addRow("Giờ gửi hằng ngày", self.gio)
        self.khi_mo = QCheckBox("Mở phần mềm mà hôm nay chưa gửi thì gửi luôn")
        self.khi_mo.setChecked(tg["khi_mo"])
        form.addRow("", self.khi_mo)
        lay.addLayout(form)
        lay.addWidget(QLabel("Ngữ cảnh gửi trong bản tin:"))
        self.picker = TypePicker(tg["loai"], al.counts(al.collect(ctx.db)))
        lay.addWidget(self.picker)
        last = tg["lan_cuoi"]
        lay.addWidget(hint(f"Lần gửi gần nhất: {date.fromisoformat(last).strftime('%d/%m/%Y')}." if last
                           else "Chưa gửi lần nào."))
        self.status = hint("")
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        test = bb.addButton("Gửi thử ngay", QDialogButtonBox.ActionRole)
        test.clicked.connect(self._test)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {"token": self.token.text(), "chat": self.chat.text(), "bat": self.bat.isChecked(),
                "gio": self.gio.time().toString("HH:mm"), "khi_mo": self.khi_mo.isChecked(),
                "loai": self.picker.values()}

    def _find(self):
        if not self.token.text().strip():
            return error(self, "Nhập mã bot trước.")
        try:
            chats = al.tg_find_chats(self.token.text())
        except al.TelegramError as e:
            return error(self, str(e))
        if not chats:
            return info(self, "Chưa thấy ai nhắn cho bot. Người nhận nhắn /start cho bot rồi bấm lại.")
        labels = [f"{name} ({cid})" for cid, name in chats]
        pick, ok = QInputDialog.getItem(self, "Chọn người nhận", "Người / nhóm đã nhắn cho bot:", labels, 0, False)
        if ok:
            self.chat.setText(chats[labels.index(pick)][0])

    def _test(self):
        v = self.values()
        text = "Gửi thử từ phần mềm quản lý hợp đồng.\n\n" + al.digest_text(al.collect(self.ctx.db, types=v["loai"]),
                                                                            self.ctx.company)
        try:
            n = al.tg_send(v["token"], v["chat"], text)
        except al.TelegramError as e:
            return error(self, str(e))
        self.status.setText(f"Đã gửi thử ({n} tin) lúc {datetime.now().strftime('%H:%M')}.")

    def _save(self):
        v = self.values()
        if v["bat"] and (not v["token"].strip() or not v["chat"].strip()):
            return error(self, "Bật gửi Telegram thì phải có mã bot và người nhận.")
        self.st.set_telegram(v)
        self.ctx.db.log(self.ctx.user["TenDangNhap"], "Cài đặt Telegram", "bật" if v["bat"] else "tắt")
        self.accept()


__all__ = ["AlertRow", "NotificationsDialog", "TelegramDialog", "TypePicker", "send_digest_async"]
