"""Đồng bộ tự động qua một thư mục dùng chung (Google Drive, OneDrive, Dropbox, thư mục mạng…).

Mỗi máy (máy kế toán, máy Giám đốc) trỏ tới cùng một thư mục:
- Gửi: hễ có thay đổi (dù nhỏ nhất) kể từ lần gửi trước, máy tự xuất gói "các thay đổi" (không bao giờ là toàn bộ
  dữ liệu, không bao giờ kèm thu chi) vào thư mục, kiểm tra vài phút một lần và khi đóng phần mềm.
- Nhận: khi mở phần mềm và định kỳ (mặc định 60 phút), máy quét thư mục, nhận lần lượt theo thời gian các gói của
  máy khác chưa nhận; gói đã nhận thì bỏ qua. Bản ghi cần người chọn (cả hai máy cùng có người sửa, nghi trùng chưa
  rõ) để lại, gói được giữ để mở ra chọn sau.
- Dọn: gói quá số ngày Giám đốc cài (mặc định 3) mà máy bên kia đã nhận thì xóa khỏi thư mục.
Đồng bộ thủ công (xuất / nhận gói bằng tay, chuyển máy, Gói Giám đốc) vẫn giữ nguyên, chạy song song.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import local_secret
from .db import SYNCED_TABLES, now_iso
from .sync import PKG_EXT, ST_CONFLICT, ST_DUP, SyncError, SyncService, shared_setting

PARTNER = "__thu_muc_chung__"          # "máy nhận" của các gói tự gửi vào thư mục chung
PARTNER_NAME = "Thư mục đồng bộ chung"
MARK_DIR = ".da_nhan"                   # tệp đánh dấu máy nào đã nhận gói nào (để dọn an toàn)

K_FOLDER = "dbtm_thu_muc"
K_PASSWORD = "dbtm_mat_khau"
K_SEND = "dbtm_tu_gui"
K_SEND_MIN = "dbtm_gui_phut"
K_RECV = "dbtm_tu_nhan"
K_RECV_MIN = "dbtm_nhan_phut"
K_LAST_SCAN = "dbtm_lan_quet"
K_LAST_ERROR = "dbtm_loi"
K_PENDING = "dbtm_cho_chon"             # {đường dẫn gói: số bản ghi cần chọn}
K_REMIND_DAYS = "dbtm_nhac_ngay"
K_CLEAN_DAYS = "dong_bo_tm_don_ngay"    # dùng chung mọi máy (Giám đốc cài), đi theo gói

DEFAULT_SEND_MIN = 2
DEFAULT_RECV_MIN = 60
DEFAULT_CLEAN_DAYS = 3
DEFAULT_REMIND_DAYS = 2


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", name).strip("_")[:40] or "May"


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass
class ReceiveResult:
    nhan: int = 0                       # số gói đã nhận
    ban_ghi: int = 0                    # số bản ghi thêm / cập nhật
    cho_chon: int = 0                   # bản ghi còn chờ người chọn
    loi: list[str] = field(default_factory=list)
    tom_tat: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.ban_ghi > 0


class SyncFolder:
    def __init__(self, db, sync: SyncService):
        self.db = db
        self.sync = sync

    # ------------------------------------------------------------ cài đặt
    def folder(self) -> Path | None:
        p = self.db.setting(K_FOLDER)
        return Path(p) if p else None

    def password(self) -> str:
        return local_secret.load(self.db, K_PASSWORD)

    def settings(self) -> dict:
        g = self.db.setting
        return {"thu_muc": g(K_FOLDER) or "", "co_mat_khau": bool(self.password()),
                "tu_gui": g(K_SEND, "1") == "1", "gui_phut": int(g(K_SEND_MIN) or DEFAULT_SEND_MIN),
                "tu_nhan": g(K_RECV, "1") == "1", "nhan_phut": int(g(K_RECV_MIN) or DEFAULT_RECV_MIN),
                "don_ngay": int(g(K_CLEAN_DAYS) or DEFAULT_CLEAN_DAYS),
                "nhac_ngay": int(g(K_REMIND_DAYS) or DEFAULT_REMIND_DAYS)}

    def save_settings(self, v: dict, *, director: bool) -> None:
        if v.get("mat_khau") is not None:
            if v["mat_khau"] and len(v["mat_khau"]) < 6:
                raise SyncError("Mật khẩu đồng bộ phải có ít nhất 6 ký tự.")
            local_secret.save(self.db, K_PASSWORD, v["mat_khau"] or None)
        folder = (v.get("thu_muc") or "").strip()
        if folder and not Path(folder).is_dir():
            raise SyncError(f"Không tìm thấy thư mục:\n{folder}")
        s = self.db.set_setting
        if folder and not self.last_send():
            # mốc bắt đầu: tự động chỉ gửi các thay đổi từ đây, không gửi toàn bộ dữ liệu (chuyển toàn bộ dữ liệu
            # sang máy mới vẫn dùng đồng bộ thủ công "Toàn bộ dữ liệu")
            self.db.execute(
                "INSERT INTO LichSuDongBo (MaMayDoiTac, TenMayDoiTac, Chieu, ThoiDiem, MocDuLieu, PhamVi, KetQua,"
                " TenDangNhap) VALUES (?, ?, 'Gửi', ?, ?, ?, ?, ?)",
                (PARTNER, PARTNER_NAME, now_iso(), now_iso(), "Bắt đầu đồng bộ tự động qua thư mục chung",
                 "Chỉ gửi các thay đổi từ thời điểm này", v.get("nguoi") or None))
        s(K_FOLDER, folder or None)
        s(K_SEND, "1" if v.get("tu_gui", True) else "0")
        s(K_SEND_MIN, str(max(1, int(v.get("gui_phut") or DEFAULT_SEND_MIN))))
        s(K_RECV, "1" if v.get("tu_nhan", True) else "0")
        s(K_RECV_MIN, str(max(5, int(v.get("nhan_phut") or DEFAULT_RECV_MIN))))
        s(K_REMIND_DAYS, str(max(1, int(v.get("nhac_ngay") or DEFAULT_REMIND_DAYS))))
        if director and v.get("don_ngay") is not None:
            new = str(max(0, int(v["don_ngay"])))
            if new != (self.db.setting(K_CLEAN_DAYS) or str(DEFAULT_CLEAN_DAYS)):
                s(K_CLEAN_DAYS, new)

    def ready(self) -> bool:
        f = self.folder()
        return bool(f and self.password())

    def _error(self, msg: str | None) -> None:
        if (self.db.setting(K_LAST_ERROR) or None) != msg:
            self.db.set_setting(K_LAST_ERROR, msg)

    def last_error(self) -> str:
        return self.db.setting(K_LAST_ERROR) or ""

    # ------------------------------------------------------------ gửi
    def last_send(self) -> str | None:
        r = self.db.one("SELECT MAX(ThoiDiem) t FROM LichSuDongBo WHERE MaMayDoiTac = ? AND Chieu = 'Gửi'", (PARTNER,))
        return r["t"] if r else None

    def pending_changes(self) -> int:
        """Số bản ghi máy này sửa (kể cả xóa) kể từ lần gửi gần nhất vào thư mục, cộng cài đặt dùng chung đã đổi.
        Bản ghi nhận từ máy khác không tính (máy kia đã có) để hai máy không gửi qua gửi lại."""
        since = self.last_send()
        n = 0
        for t in SYNCED_TABLES:
            r = self.db.one(f"SELECT COUNT(*) c FROM {t} WHERE NgaySua > ? AND MaMaySua = ?",
                            (since or "", self.db.machine_id))
            n += r["c"]
        n += sum(1 for r in self.db.query("SELECT Khoa FROM CaiDat WHERE NgaySua > ?", (since or "",))
                 if shared_setting(r["Khoa"]))
        return n

    def send_if_changed(self, user) -> Path | None:
        """Có thay đổi thì xuất gói các thay đổi vào thư mục chung. Trả về tệp đã ghi (None nếu không có gì gửi)."""
        if not self.ready() or self.db.setting(K_SEND, "1") != "1" or user is None:
            return None
        folder = self.folder()
        if not folder.is_dir():
            self._error(f"Không thấy thư mục đồng bộ chung: {folder}")
            return None
        if not self.pending_changes():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"DongBo_{_safe(self.sync.machine_name())}_{stamp}_{self.db.machine_id[:6]}{PKG_EXT}"
        target = folder / name
        tmp = folder / (name + ".tmp")
        try:
            self.sync.export_package(tmp, self.password(), user=user, scope="changes", partner=PARTNER,
                                     partner_name=PARTNER_NAME)
            os.replace(tmp, target)    # đổi tên khi đã ghi xong: phần mềm đồng bộ đám mây không chép tệp dở
        except (OSError, SyncError) as e:
            tmp.unlink(missing_ok=True)
            self._error(f"Không gửi được gói vào thư mục chung: {e}")
            return None
        self._error(None)
        return target

    # ------------------------------------------------------------ nhận
    def _received(self, header: dict) -> bool:
        return self.db.one("SELECT 1 FROM LichSuDongBo WHERE Chieu = 'Nhận' AND MaMayDoiTac = ? AND MocDuLieu = ?",
                           (header["may_gui"], header["thoi_diem"])) is not None

    def packages(self) -> list[tuple[Path, dict]]:
        """Các gói trong thư mục (bỏ tệp đang ghi dở, tệp không đọc được), sắp theo thời điểm xuất."""
        folder = self.folder()
        out = []
        if not folder or not folder.is_dir():
            return out
        for p in folder.glob(f"*{PKG_EXT}"):
            try:
                h = self.sync.read_header(p)
            except (OSError, ValueError, SyncError):
                continue
            out.append((p, h))
        out.sort(key=lambda x: x[1].get("thoi_diem", ""))
        return out

    def waiting(self) -> list[tuple[Path, dict]]:
        mst = self.db.one("SELECT MaCongTy FROM BanQuyen WHERE Id = 1")
        mst = mst["MaCongTy"] if mst else None
        return [(p, h) for p, h in self.packages()
                if h.get("may_gui") != self.db.machine_id and h.get("mst") == mst and not self._received(h)]

    def pending_choices(self) -> dict[str, int]:
        try:
            d = json.loads(self.db.setting(K_PENDING) or "{}")
        except ValueError:
            d = {}
        return {p: n for p, n in d.items() if Path(p).exists()}

    def _set_pending(self, d: dict) -> None:
        self.db.set_setting(K_PENDING, json.dumps(d, ensure_ascii=False) if d else None)

    def clear_pending(self, path: str) -> None:
        d = self.pending_choices()
        if d.pop(str(path), None) is not None:
            self._set_pending(d)

    def receive(self, user) -> ReceiveResult:
        """Nhận lần lượt các gói chưa nhận. Bản ghi tự xử lý được (theo quy tắc người sửa thắng tự động, bản mới hơn…)
        được ghi ngay; bản ghi cần người chọn để lại và gói được giữ để mở ra chọn."""
        res = ReceiveResult()
        if not self.ready() or user is None:
            return res
        self.db.set_setting(K_LAST_SCAN, now_iso())
        pw = self.password()
        pending = self.pending_choices()
        for path, h in self.waiting():
            try:
                pkg = self.sync.read_package(path, pw)
            except SyncError as e:
                res.loi.append(f"{path.name}: {e}")
                continue
            if self.sync.wipe_order_for_me(pkg):
                res.loi.append(f"{path.name}: gói có lệnh của Giám đốc cho máy này, hãy mở gói ở màn Đồng bộ "
                               "để nhận tay.")
                continue
            pkg.body.pop("finance", None)          # gói tự động không nhận thu chi
            items = self.sync.compare(pkg)
            need = [it for it in items if it.action is None and it.status in (ST_CONFLICT, ST_DUP)]
            try:
                r = self.sync.apply(pkg, user=user)
            except SyncError as e:
                res.loi.append(f"{path.name}: {e}")
                continue
            res.nhan += 1
            res.ban_ghi += r["them"] + r["cap_nhat"] + r["bo_sung"] + r.get("cai_dat", 0)
            res.tom_tat.append(f"{h['ten_may']} ({h['thoi_diem'][:16].replace('T', ' ')} UTC): {r['tom_tat']}")
            if need:
                res.cho_chon += len(need)
                pending[str(path)] = len(need)
            self._mark(path)
        self._set_pending(pending)
        self._error(("; ".join(res.loi))[:500] if res.loi else None)
        self.cleanup()
        return res

    # ------------------------------------------------------------ dọn thư mục
    def _mark(self, path: Path) -> None:
        try:
            d = path.parent / MARK_DIR
            d.mkdir(exist_ok=True)
            (d / f"{path.stem}__{self.db.machine_id[:12]}.ok").write_text(now_iso())
        except OSError:
            pass

    def _marks(self, path: Path) -> list[Path]:
        d = path.parent / MARK_DIR
        return list(d.glob(f"{path.stem}__*.ok")) if d.is_dir() else []

    def cleanup(self) -> int:
        """Xóa gói quá số ngày Giám đốc cài, chỉ khi máy bên kia đã nhận (gói của máy này) hoặc máy này đã nhận
        (gói máy khác) và không còn bản ghi chờ chọn. 0 ngày = không tự dọn."""
        days = int(self.db.setting(K_CLEAN_DAYS) or DEFAULT_CLEAN_DAYS)
        if days <= 0:
            return 0
        limit = datetime.now(timezone.utc) - timedelta(days=days)
        pending = self.pending_choices()
        me = self.db.machine_id[:12]
        n = 0
        for p, h in self.packages():
            t = _parse_ts(h.get("thoi_diem", ""))
            if t is None or t > limit or str(p) in pending:
                continue
            marks = self._marks(p)
            others = [m for m in marks if not m.stem.endswith("__" + me)]
            if h.get("may_gui") == self.db.machine_id:
                ok = bool(others)
            else:
                ok = self._received(h)
            if not ok:
                continue
            try:
                p.unlink()
                for m in marks:
                    m.unlink(missing_ok=True)
                n += 1
            except OSError:
                continue
        folder = self.folder()
        if folder and (folder / MARK_DIR).is_dir():          # tệp đánh dấu mồ côi (gói đã bị xóa)
            for m in (folder / MARK_DIR).glob("*.ok"):
                stem = m.stem.rsplit("__", 1)[0]
                if not (folder / f"{stem}{PKG_EXT}").exists() and time.time() - m.stat().st_mtime > days * 86400:
                    m.unlink(missing_ok=True)
        for tmp in (folder.glob(f"*{PKG_EXT}.tmp") if folder and folder.is_dir() else []):
            if time.time() - tmp.stat().st_mtime > 86400:          # tệp ghi dở bị bỏ lại
                tmp.unlink(missing_ok=True)
        return n

    # ------------------------------------------------------------ nhắc việc
    def warnings(self, today: datetime | None = None) -> list[tuple[str, str]]:
        """[(tiêu đề, chi tiết)] các việc cần nhắc về đồng bộ (chỉ việc quan trọng)."""
        out: list[tuple[str, str]] = []
        if not self.db.setting(K_FOLDER):
            return out
        now = today or datetime.now(timezone.utc)
        if not self.ready():
            out.append(("Đồng bộ tự động chưa đủ cài đặt", "Chưa nhập mật khẩu đồng bộ cho thư mục chung."))
            return out
        err = self.last_error()
        if err:
            out.append(("Đồng bộ tự động bị lỗi", err))
        pend = self.pending_choices()
        if pend:
            out.append(("Đồng bộ: có bản ghi cần chọn",
                        f"{sum(pend.values())} bản ghi cả hai máy cùng có người sửa / nghi trùng, mở màn Đồng bộ để chọn."))
        days = int(self.db.setting(K_REMIND_DAYS) or DEFAULT_REMIND_DAYS)
        for r in self.db.query("SELECT MaMayDoiTac, TenMayDoiTac, MAX(ThoiDiem) t FROM LichSuDongBo "
                               "WHERE Chieu = 'Nhận' GROUP BY MaMayDoiTac"):
            t = _parse_ts(r["t"] or "")
            if t and (now - t).days >= days:
                out.append((f"Chưa nhận dữ liệu từ {r['TenMayDoiTac']} {(now - t).days} ngày",
                            "Kiểm tra máy đó có mở phần mềm, có mạng và cùng thư mục đồng bộ chung."))
        if self.db.setting(K_SEND, "1") == "1":
            last = _parse_ts(self.last_send() or "")
            if self.pending_changes() and last and (now - last).days >= days:
                out.append((f"Có thay đổi chưa gửi {(now - last).days} ngày",
                            "Máy này có dữ liệu mới chưa gửi được vào thư mục đồng bộ chung."))
        return out
