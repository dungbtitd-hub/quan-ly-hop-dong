"""Thông báo nhiều ngữ cảnh: hợp đồng sắp hết hạn, công việc quá hạn / sắp đến hạn, hóa đơn chưa thu, tạm ứng chưa
khấu trừ, hợp đồng thiếu dữ liệu, hóa đơn đầu vào cần xem lại… Dùng chung cho chuông thông báo, Tổng quan, bản tin
hằng ngày trên phần mềm và gửi qua Telegram."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

from .assets import AssetService
from .contracts import OPEN_STATUSES, ContractService
from .db import Database
from .reports import fmt_date, fmt_money

# mã → (tên ngữ cảnh, mô tả ngắn)
ALERT_TYPES = {
    "hd_het_han": ("Hợp đồng sắp hết hạn / quá hạn", "Theo số ngày nhắc trước của từng hợp đồng"),
    "cv_qua_han": ("Công việc quá hạn", "Chưa hoàn thành, đã qua ngày dự kiến"),
    "cv_sap_han": ("Công việc sắp đến hạn", "Trong 7 ngày tới"),
    "hd_chua_thu": ("Hóa đơn đầu ra chưa thu đủ", "Còn phải thu, kèm số ngày từ khi xuất"),
    "hd_chua_xuat": ("Hợp đồng chưa xuất đủ hóa đơn", "Đã nghiệm thu / hoàn thành mà còn giá trị chưa xuất"),
    "tam_ung": ("Tạm ứng chưa khấu trừ", "Đã nhận tạm ứng, chưa trừ vào hóa đơn nào"),
    "thieu_du_lieu": ("Hợp đồng thiếu dữ liệu", "Thiếu ngày thực hiện, giá trị, loại, MST đối tác…"),
    "hddv_xem_lai": ("Hóa đơn đầu vào cần xem lại", "Chưa phân loại, gợi ý chờ xác nhận, nghi sai, nghi trùng…"),
}
DEFAULT_TYPES = tuple(ALERT_TYPES)
DUE_SOON_DAYS = 7
TONE_ORDER = {"danger": 0, "warn": 1, "accent": 2}


@dataclass
class Alert:
    loai: str
    muc: str                 # danger / warn / accent
    tieu_de: str
    chi_tiet: str = ""
    nhan: str = ""           # chữ trên nhãn bên phải (VD "Quá hạn 3 ngày")
    ma_hd: str | None = None  # hợp đồng liên quan (bấm để mở)
    so_tien: int = 0
    thu_tu: int = 0          # sắp xếp trong cùng loại (nhỏ lên trước)


def missing_fields(hd) -> list[str]:
    """Các dữ liệu quan trọng hợp đồng còn thiếu (để quản lý bổ sung)."""
    out = []
    if not hd["NgayBatDauDuKien"]:
        out.append("ngày bắt đầu")
    if not hd["NgayKetThucDuKien"]:
        out.append("ngày kết thúc / thời gian thực hiện")
    if not hd["GiaTriTongSauThue"]:
        out.append("giá trị hợp đồng")
    if not hd["MaLoaiHD"]:
        out.append("loại hợp đồng")
    if not hd["MaThueDoiTac"]:
        out.append("MST đối tác")
    return out


def collect(db: Database, today: str | None = None, types=None) -> list[Alert]:
    today = today or date.today().isoformat()
    types = set(types or DEFAULT_TYPES)
    cs, t = ContractService(db), date.fromisoformat(today)
    out: list[Alert] = []
    if "hd_het_han" in types:
        for r in cs.expiring(today):
            out.append(Alert("hd_het_han", "danger" if r["ConLai"] <= 0 else "warn" if r["ConLai"] <= 7 else "accent",
                             f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']}",
                             f"{r['TenDoiTac']} · kết thúc {fmt_date(r['NgayKetThucDuKien'])}", r["ConLaiText"],
                             r["Ma"], thu_tu=r["ConLai"]))
    if "cv_qua_han" in types:
        for r in cs.overdue_tasks(today):
            out.append(Alert("cv_qua_han", "danger", r["TenNoiDungCV"],
                             f"Công việc của HĐ {r['SoHopDong']} · hạn {fmt_date(r['NgayDuKienHT'])}",
                             f"Quá hạn {r['QuaHan']} ngày", r["MaHopDong"], thu_tu=-r["QuaHan"]))
    if "cv_sap_han" in types:
        last = (t + timedelta(days=DUE_SOON_DAYS)).isoformat()
        for r in db.query(
                "SELECT c.TenNoiDungCV, c.NgayDuKienHT, h.SoHopDong, h.Ma FROM CongViec c JOIN HopDong h ON h.Ma = c.MaHopDong"
                " WHERE c.DaXoa = 0 AND h.DaXoa = 0 AND c.TinhTrangCV NOT IN ('Hoàn thành', 'Đã nghiệm thu')"
                " AND substr(c.NgayDuKienHT, 1, 10) BETWEEN ? AND ? ORDER BY c.NgayDuKienHT", (today, last)):
            left = (date.fromisoformat(r["NgayDuKienHT"][:10]) - t).days
            out.append(Alert("cv_sap_han", "warn" if left <= 2 else "accent", r["TenNoiDungCV"],
                             f"Công việc của HĐ {r['SoHopDong']} · hạn {fmt_date(r['NgayDuKienHT'])}",
                             "Hôm nay" if left == 0 else f"Còn {left} ngày", r["Ma"], thu_tu=left))
    if "hd_chua_thu" in types:
        for r in db.query(
                "SELECT i.Ma, i.SoHoaDon, i.NgayXuatHD, i.GiaTriSauThue, i.MaHopDong, h.SoHopDong, h.TenDoiTac,"
                " (SELECT COALESCE(SUM(SoTien), 0) FROM ThanhToanHopDong p WHERE p.DaXoa = 0 AND p.MaHDDauRa = i.Ma) Da"
                " FROM HoaDonDauRa i JOIN HopDong h ON h.Ma = i.MaHopDong"
                " WHERE i.DaXoa = 0 AND h.DaXoa = 0 AND i.TinhChatHD != 'Đã hủy' ORDER BY i.NgayXuatHD"):
            left = int(r["GiaTriSauThue"] or 0) - int(r["Da"] or 0)
            if left <= 0:
                continue
            age = (t - date.fromisoformat(r["NgayXuatHD"][:10])).days
            out.append(Alert("hd_chua_thu", "danger" if age > 60 else "warn" if age > 30 else "accent",
                             f"Hóa đơn {r['SoHoaDon']} – HĐ {r['SoHopDong']}",
                             f"{r['TenDoiTac']} · xuất {fmt_date(r['NgayXuatHD'])} · "
                             + ("chưa thu" if not r["Da"] else f"đã thu {fmt_money(r['Da'])} đ"),
                             f"{fmt_money(left)} đ · {age} ngày", r["MaHopDong"], left, thu_tu=-age))
    if types & {"hd_chua_xuat", "tam_ung", "thieu_du_lieu"}:
        for hd in db.active("HopDong", "TinhTrangHopDong != 'Hủy'", order="NgayKy"):
            if "thieu_du_lieu" in types and hd["TinhTrangHopDong"] in OPEN_STATUSES:
                miss = missing_fields(hd)
                if miss:
                    out.append(Alert("thieu_du_lieu", "warn", f"Hợp đồng {hd['SoHopDong']} – {hd['TenHopDong']}",
                                     "Thiếu " + ", ".join(miss), f"Thiếu {len(miss)} mục", hd["Ma"],
                                     thu_tu=-len(miss)))
            if hd["ChieuHopDong"] != "Đầu ra" or not types & {"hd_chua_xuat", "tam_ung"}:
                continue
            s = cs.summary(hd["Ma"])
            if "tam_ung" in types and s["TamUngConLai"] > 0:
                out.append(Alert("tam_ung", "accent", f"Hợp đồng {hd['SoHopDong']} – {hd['TenHopDong']}",
                                 f"{hd['TenDoiTac']} · tạm ứng chưa khấu trừ vào hóa đơn",
                                 f"{fmt_money(s['TamUngConLai'])} đ", hd["Ma"], s["TamUngConLai"]))
            if "hd_chua_xuat" in types and hd["TinhTrangHopDong"] in ("Đã nghiệm thu", "Hoàn thành") \
                    and s["ConPhaiXuat"] > 0:
                out.append(Alert("hd_chua_xuat", "warn", f"Hợp đồng {hd['SoHopDong']} – {hd['TenHopDong']}",
                                 f"{hd['TenDoiTac']} · {hd['TinhTrangHopDong'].lower()}, còn giá trị chưa xuất hóa đơn",
                                 f"{fmt_money(s['ConPhaiXuat'])} đ", hd["Ma"], s["ConPhaiXuat"]))
    if "hddv_xem_lai" in types:
        from .assets import FLAGS
        rows = AssetService(db).review_lines()
        by: dict[str, int] = {}
        for r in rows:
            by[r["MaCanhBao"]] = by.get(r["MaCanhBao"], 0) + 1
        for code, n in by.items():
            label, tone = FLAGS[code]
            out.append(Alert("hddv_xem_lai", tone, f"{n} dòng hóa đơn đầu vào: {label.lower()}",
                             "Mở Tài sản, CCDC → Cần xem lại để kế toán xử lý", f"{n} dòng", None, n,
                             thu_tu=list(FLAGS).index(code)))
    order = list(ALERT_TYPES)
    out.sort(key=lambda a: (order.index(a.loai), TONE_ORDER.get(a.muc, 3), a.thu_tu))
    return out


def counts(alerts: list[Alert]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in alerts:
        out[a.loai] = out.get(a.loai, 0) + 1
    return out


def digest_text(alerts: list[Alert], company: str = "", today: str | None = None, per_type: int = 8) -> str:
    """Bản tin tổng hợp (gửi Telegram / hiện khi mở phần mềm)."""
    today = today or date.today().isoformat()
    head = f"Bản tin {fmt_date(today)}" + (f" – {company}" if company else "")
    if not alerts:
        return head + "\nKhông có việc cần nhắc."
    lines = [head]
    for code, (name, _) in ALERT_TYPES.items():
        items = [a for a in alerts if a.loai == code]
        if not items:
            continue
        total = sum(a.so_tien for a in items) if code in ("hd_chua_thu", "tam_ung", "hd_chua_xuat") else 0
        lines.append("")
        lines.append(f"■ {name}: {len(items)}" + (f" (tổng {fmt_money(total)} đ)" if total else ""))
        for a in items[:per_type]:
            lines.append(f"• {a.tieu_de} – {a.nhan}" + (f" ({a.chi_tiet})" if a.chi_tiet and code != "hddv_xem_lai"
                                                         else ""))
        if len(items) > per_type:
            lines.append(f"… và {len(items) - per_type} mục khác.")
    return "\n".join(lines)


def period_overview(db: Database, tu: str, den: str) -> dict:
    """Số liệu hóa đơn đầu ra, đầu vào trong kỳ cho Tổng quan."""
    from . import asset_catalog as dm
    ra = {"n": 0, "tong": 0, "da_n": 0, "da": 0, "chua_n": 0, "chua": 0, "ds_chua": []}
    for r in db.query(
            "SELECT i.Ma, i.SoHoaDon, i.NgayXuatHD, i.GiaTriSauThue, i.MaHopDong, h.SoHopDong,"
            " (SELECT COALESCE(SUM(SoTien), 0) FROM ThanhToanHopDong p WHERE p.DaXoa = 0 AND p.MaHDDauRa = i.Ma) Da"
            " FROM HoaDonDauRa i JOIN HopDong h ON h.Ma = i.MaHopDong WHERE i.DaXoa = 0 AND h.DaXoa = 0"
            " AND i.TinhChatHD != 'Đã hủy' AND substr(i.NgayXuatHD, 1, 10) BETWEEN ? AND ? ORDER BY i.NgayXuatHD",
            (tu, den)):
        v, da = int(r["GiaTriSauThue"] or 0), int(r["Da"] or 0)
        ra["n"] += 1
        ra["tong"] += v
        ra["da"] += min(da, v)
        if da >= v:
            ra["da_n"] += 1
        else:
            ra["chua_n"] += 1
            ra["chua"] += v - da
            ra["ds_chua"].append({**dict(r), "ConLai": v - da})
    vao = {"n": 0, "tong": 0, "da_n": 0, "chua_n": 0, "khong_hd": 0, "nhom": []}
    for r in db.query("SELECT GiaTriSauThue, SoThamChieuSK, MaHopDong FROM HoaDonDauVao WHERE DaXoa = 0"
                      " AND substr(NgayNhanHD, 1, 10) BETWEEN ? AND ?", (tu, den)):
        vao["n"] += 1
        vao["tong"] += int(r["GiaTriSauThue"] or 0)
        vao["da_n" if r["SoThamChieuSK"] else "chua_n"] += 1
        vao["khong_hd"] += 0 if r["MaHopDong"] else 1
    groups: dict[str, list] = {}
    for r in db.query("SELECT d.MaNhomDM, d.PhanLoai, COUNT(DISTINCT d.MaHoaDon) n, SUM(d.ThanhTien) s"
                      " FROM DongHoaDonDauVao d JOIN HoaDonDauVao h ON h.Ma = d.MaHoaDon WHERE d.DaXoa = 0"
                      " AND h.DaXoa = 0 AND substr(h.NgayNhanHD, 1, 10) BETWEEN ? AND ? GROUP BY d.MaNhomDM, d.PhanLoai",
                      (tu, den)):
        nhom = dm.BY_CODE.get(r["MaNhomDM"] or "") if r["PhanLoai"] != dm.CHUA_PHAN_LOAI else None
        name = nhom.ten.split(":")[0].split(",")[0] if nhom else "Chưa phân loại"
        g = groups.setdefault(name, [name, 0, 0])
        g[1] += r["n"]
        g[2] += int(r["s"] or 0)
    vao["nhom"] = sorted(groups.values(), key=lambda g: -g[2])
    return {"ra": ra, "vao": vao}


# ---------------------------------------------------------------- Telegram
TG_API = "https://api.telegram.org"
TG_LIMIT = 4000


class TelegramError(Exception):
    pass


def _tg_call(token: str, method: str, data: dict | None = None, timeout: float = 12) -> dict:
    url = f"{TG_API}/bot{token.strip()}/{method}"
    body = urllib.parse.urlencode(data).encode() if data else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise TelegramError(f"Không gửi được qua Telegram: {e}") from e
    if not res.get("ok"):
        raise TelegramError("Telegram từ chối: " + str(res.get("description") or res))
    return res


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    parts, cur = [], ""
    lines = [ln[i:i + limit] for ln in text.split("\n") for i in range(0, max(len(ln), 1), limit)]
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        parts.append(cur)
    return parts


def tg_send(token: str, chat_id: str, text: str) -> int:
    if not token or not chat_id:
        raise TelegramError("Chưa nhập mã bot (token) hoặc mã nhóm / người nhận (chat id).")
    parts = split_message(text)
    for p in parts:
        _tg_call(token, "sendMessage", {"chat_id": chat_id.strip(), "text": p, "disable_web_page_preview": "true"})
    return len(parts)


def tg_find_chats(token: str) -> list[tuple[str, str]]:
    """(chat id, tên) của các cuộc trò chuyện vừa nhắn cho bot (người nhận nhắn /start cho bot trước)."""
    res = _tg_call(token, "getUpdates")
    seen, out = set(), []
    for u in res.get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat and chat["id"] not in seen:
            seen.add(chat["id"])
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
            out.append((str(chat["id"]), name or chat.get("username") or ""))
    return out


# ---------------------------------------------------------------- cài đặt (lưu trên máy này, không đồng bộ)
class AlertSettings:
    """Cài đặt thông báo trên máy này. Mã bot Telegram chỉ lưu ở máy này, không gửi kèm gói đồng bộ."""

    def __init__(self, db: Database):
        self.db = db

    def _list(self, key: str, default) -> list[str]:
        raw = self.db.setting(key)
        if raw is None:
            return list(default)
        try:
            return [x for x in json.loads(raw) if x in ALERT_TYPES]
        except ValueError:
            return list(default)

    def app_types(self, user: str) -> list[str]:
        return self._list(f"thong_bao_loai:{user}", DEFAULT_TYPES)

    def set_app_types(self, user: str, types) -> None:
        self.db.set_setting(f"thong_bao_loai:{user}", json.dumps([t for t in types if t in ALERT_TYPES]))

    def telegram(self) -> dict:
        return {"token": self.db.setting("tg_token") or "", "chat": self.db.setting("tg_chat") or "",
                "bat": self.db.setting("tg_bat") == "1", "gio": self.db.setting("tg_gio") or "08:00",
                "khi_mo": self.db.setting("tg_khi_mo", "1") == "1",
                "loai": self._list("tg_loai", DEFAULT_TYPES), "lan_cuoi": self.db.setting("tg_lan_cuoi") or ""}

    def set_telegram(self, v: dict) -> None:
        for key, val in (("tg_token", v.get("token", "").strip() or None), ("tg_chat", v.get("chat", "").strip() or None),
                         ("tg_bat", "1" if v.get("bat") else "0"), ("tg_gio", v.get("gio") or "08:00"),
                         ("tg_khi_mo", "1" if v.get("khi_mo") else "0"),
                         ("tg_loai", json.dumps([t for t in v.get("loai", []) if t in ALERT_TYPES]))):
            self.db.set_setting(key, val)

    def due(self, now: str, opening: bool = False) -> bool:
        """Đến giờ gửi bản tin hằng ngày chưa (mỗi ngày một lần)? now: 'YYYY-MM-DD HH:MM'."""
        tg = self.telegram()
        if not tg["bat"] or not tg["token"] or not tg["chat"] or tg["lan_cuoi"] == now[:10]:
            return False
        return now[11:16] >= tg["gio"] or (opening and tg["khi_mo"])

    def mark_sent(self, day: str) -> None:
        self.db.set_setting("tg_lan_cuoi", day)


__all__ = ["ALERT_TYPES", "Alert", "AlertSettings", "TelegramError", "collect", "counts", "digest_text",
           "missing_fields", "tg_find_chats", "tg_send"]
