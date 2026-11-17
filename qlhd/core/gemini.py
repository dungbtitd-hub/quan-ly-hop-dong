"""Kết nối Gemini (Google) để hỗ trợ hóa đơn:
- kiểm tra tình trạng hóa đơn (file XML / PDF / ảnh): đọc lại các trường chính, soát cấu trúc, đối chiếu số liệu
  trong phần mềm → OK hoặc Có vấn đề;
- đọc số liệu hóa đơn từ file ảnh / PDF quét khi không có XML;
- phân loại dòng hóa đơn đầu vào theo danh mục cài sẵn của phần mềm (chỉ chọn mã có trong danh mục).

Gọi thẳng API REST (không cần cài thêm thư viện), khóa API do người dùng nhập ở Cài đặt (lưu mã hóa, riêng máy).
Gemini chỉ đọc nội dung file, không tra cứu được trên trang của cơ quan thuế.
"""
from __future__ import annotations

import base64
import json
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import asset_catalog as dm
from . import local_secret
from .db import now_iso

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LIST_API = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
# tên gọi tắt luôn trỏ tới bản mới nhất; Google ngừng cấp mô hình cũ thì phần mềm tự tìm mô hình còn dùng được
MODELS = ["gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest"]
DEFAULT_MODEL = MODELS[0]
_RESOLVED: dict[str, str] = {}           # mô hình đã chọn → mô hình thay thế còn dùng được (trong phiên chạy)


def best_model(names: list[str], wanted: str = "") -> str | None:
    """Chọn mô hình thay thế từ danh sách Google trả về: cùng loại (flash / pro / flash-lite), bản số cao nhất,
    không phải bản thử nghiệm."""
    kind = "flash-lite" if "lite" in wanted else "pro" if "pro" in wanted else "flash"
    pat = re.compile(rf"^gemini-(\d+(?:\.\d+)?)-{kind}$")
    stable = sorted(((float(m.group(1)), n) for n in names if (m := pat.match(n))), reverse=True)
    if stable:
        return stable[0][1]
    alias = f"gemini-{kind}-latest"
    if alias in names:
        return alias
    loose = [n for n in names if kind in n and "preview" not in n and "exp" not in n and "image" not in n
             and "tts" not in n and "audio" not in n and "live" not in n]
    return sorted(loose)[-1] if loose else None
MAX_FILE = 18 * 1024 * 1024
IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

K_KEY = "gemini_khoa"
K_MODEL = "gemini_mo_hinh"
K_STATE = "gemini_trang_thai"            # ok / loi
K_ERROR = "gemini_loi"
K_TIME = "gemini_luc"
K_AUTO_CHECK = "gemini_tu_kiem"          # tự kiểm tra hóa đơn mới
K_AUTO_CLASS = "gemini_tu_phan_loai"     # tự phân loại dòng hóa đơn đầu vào mới
K_AUTO_READ = "gemini_doc_anh"           # đọc số liệu file ảnh / PDF quét
K_ASK = "gemini_hoi_truoc"               # hỏi lại trước mỗi lần gửi (kiểm tra tay)

ST_OK = "OK"
ST_ISSUE = "Có vấn đề"
ST_SEEN = "Đã xem"                       # có vấn đề nhưng người dùng đã xem, bỏ lưu ý

# trạng thái rà soát phân loại của một dòng
CL_AGREE = "dong_y"
CL_CHANGED = "da_doi"
CL_UNSURE = "khong_chac"
CL_DIFF = "khac"                         # kế toán đã chọn, Gemini gợi ý khác (chỉ tô màu)
CL_LABELS = {None: "Chưa được Gemini rà soát", "": "Chưa được Gemini rà soát", CL_AGREE: "Gemini đồng ý",
             CL_CHANGED: "Gemini đã phân loại", CL_UNSURE: "Gemini không chắc", CL_DIFF: "Gemini gợi ý khác"}


class GeminiError(Exception):
    def __init__(self, msg: str, kind: str = "khac"):
        super().__init__(msg)
        self.kind = kind                 # mang / khoa / han_muc / chua_cai / mo_hinh / khac
        self.detail = ""

    @property
    def offline(self) -> bool:
        return self.kind in ("mang", "khoa", "han_muc", "chua_cai")


class Gemini:
    def __init__(self, key: str, model: str = DEFAULT_MODEL, timeout: float = 120):
        if not key:
            raise GeminiError("Chưa nhập khóa API Gemini (Cài đặt › Thư mục hóa đơn và Gemini).", "chua_cai")
        model = model or DEFAULT_MODEL
        self.key, self.model, self.timeout = key.strip(), _RESOLVED.get(model, model), timeout
        self.requested = model

    # ---------------------------------------------------------------- gọi API
    def list_models(self) -> list[str]:
        """Các mô hình khóa API này dùng được để tạo nội dung."""
        req = urllib.request.Request(LIST_API, headers={"x-goog-api-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=min(self.timeout, 30)) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise GeminiError("Khóa API Gemini không hợp lệ hoặc chưa được cấp quyền.", "khoa") from None
            raise GeminiError(f"Không lấy được danh sách mô hình Gemini ({e.code}).", "mang") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise GeminiError(f"Không kết nối được Gemini (mất mạng?): {getattr(e, 'reason', e)}", "mang") from None
        return [m["name"].split("/", 1)[-1] for m in data.get("models", [])
                if "generateContent" in (m.get("supportedGenerationMethods") or [])]

    def _post(self, body: dict) -> dict:
        try:
            return self._post_once(body)
        except GeminiError as e:
            if e.kind != "mo_hinh":
                raise
            # mô hình đã ngừng cấp / không có: tìm mô hình thay thế còn dùng được rồi gửi lại một lần
            names = self.list_models()
            hints = [h.rstrip(".") for h in re.findall(r"models/(gemini-[\w.\-]+)", str(e.detail or ""))]
            new = next((h for h in hints if h != self.model and h in names), None)
            new = new or best_model(names, self.requested)
            if not new or new == self.model:
                raise GeminiError(f"Mô hình {self.model} không còn dùng được và không tìm thấy mô hình thay thế. "
                                  "Chọn mô hình khác ở Cài đặt › Thư mục hóa đơn và Gemini.") from None
            self.model = new
            _RESOLVED[self.requested] = new
            return self._post_once(body)

    def _post_once(self, body: dict) -> dict:
        req = urllib.request.Request(API.format(model=self.model), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                msg = ""
            if e.code in (401, 403) or "API key" in msg:
                raise GeminiError("Khóa API Gemini không hợp lệ hoặc chưa được cấp quyền.", "khoa") from None
            if e.code == 429:
                raise GeminiError("Gemini báo hết hạn mức sử dụng, thử lại sau.", "han_muc") from None
            if e.code >= 500:
                raise GeminiError(f"Máy chủ Gemini đang lỗi ({e.code}), thử lại sau.", "mang") from None
            if e.code == 404 or "no longer available" in msg or "is not found" in msg:
                err = GeminiError(f"Mô hình {self.model} không còn dùng được: {msg[:200]}", "mo_hinh")
                err.detail = msg
                raise err from None
            raise GeminiError(f"Gemini từ chối yêu cầu ({e.code}): {msg[:200]}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise GeminiError(f"Không kết nối được Gemini (mất mạng?): {getattr(e, 'reason', e)}", "mang") from None

    def ask_json(self, parts: list[dict], schema: dict) -> dict:
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema,
                                     "temperature": 0}}
        data = self._post(body)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            return json.loads(text)
        except (KeyError, IndexError, ValueError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or \
                ((data.get("candidates") or [{}])[0].get("finishReason"))
            raise GeminiError(f"Gemini không trả về kết quả đọc được ({reason or 'không rõ lý do'}).") from None

    def ping(self) -> None:
        self.ask_json([{"text": "Trả lời {\"ok\": true}"}],
                      {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]})

    @staticmethod
    def file_part(path: str | Path) -> dict:
        p = Path(path)
        if not p.exists():
            raise GeminiError(f"Không tìm thấy tệp: {p}")
        if p.stat().st_size > MAX_FILE:
            raise GeminiError(f"Tệp quá lớn để gửi Gemini ({p.stat().st_size // 1_048_576} MB): {p.name}")
        ext = p.suffix.lower()
        if ext == ".xml":
            raw = p.read_bytes()
            for enc in ("utf-8-sig", "utf-16", "cp1258", "latin-1"):
                try:
                    return {"text": "Nội dung file hóa đơn điện tử XML:\n" + raw.decode(enc)}
                except UnicodeDecodeError:
                    continue
        mime = "application/pdf" if ext == ".pdf" else IMAGE_EXT.get(ext)
        if not mime:
            raise GeminiError(f"Gemini chưa đọc được loại tệp {ext}.")
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(p.read_bytes()).decode()}}

    # ---------------------------------------------------------------- nghiệp vụ
    def check_invoice(self, path: str | Path) -> dict:
        prompt = ("Bạn là chuyên gia kiểm tra hóa đơn điện tử tại Việt Nam. Hãy phân tích hóa đơn này và trả về "
                  "JSON với các trường: tên, MST đơn vị bán; tên, MST người mua; ký hiệu, số hóa đơn; ngày lập "
                  "(YYYY-MM-DD); tổng tiền trước thuế, tiền thuế GTGT, tổng tiền thanh toán (số nguyên, đồng); mã "
                  "của cơ quan thuế (nếu có); đánh giá tính hợp lệ về cấu trúc hóa đơn (hop_le) và liệt kê các "
                  "điểm cần lưu ý (luu_y), ví dụ thiếu mã CQT, tổng tiền bằng chữ khác bằng số, cộng các dòng "
                  "không khớp tổng, thiếu chữ ký số. Không có điểm cần lưu ý thì để danh sách rỗng.")
        return self.ask_json([self.file_part(path), {"text": prompt}], CHECK_SCHEMA)

    def extract_invoice(self, path: str | Path) -> dict:
        prompt = ("Đây là hóa đơn GTGT Việt Nam (ảnh chụp hoặc PDF quét). Hãy đọc chính xác các trường và trả về "
                  "JSON. Số tiền là số nguyên (đồng), bỏ dấu chấm phân cách. Ngày dạng YYYY-MM-DD. Thuế suất dạng "
                  "'10%', '8%', '5%', '0%' hoặc 'KCT'. Trường nào không đọc được chắc chắn thì để trống (không "
                  "đoán). la_hoa_don = false nếu đây không phải hóa đơn. Liệt kê từng dòng hàng hóa, dịch vụ.")
        return self.ask_json([self.file_part(path), {"text": prompt}], EXTRACT_SCHEMA)

    def classify_lines(self, lines: list[dict]) -> list[dict]:
        """lines: [{id, ten, so_luong, don_gia, thanh_tien, nguoi_ban}] → [{id, ma_nhom, so_thang, do_chac, ly_do}]."""
        prompt = (CLASSIFY_RULES + "\n\nDANH MỤC (mã | tên nhóm | kiểu | khung thời gian | tháng mặc định):\n"
                  + catalog_text() + "\n\nCÁC DÒNG CẦN PHÂN LOẠI (JSON):\n"
                  + json.dumps(lines, ensure_ascii=False))
        return self.ask_json([{"text": prompt}], CLASSIFY_SCHEMA).get("ket_qua", [])


_S = {"type": "STRING"}
_I = {"type": "INTEGER"}
CHECK_SCHEMA = {"type": "OBJECT", "properties": {
    "ten_nguoi_ban": _S, "mst_nguoi_ban": _S, "ten_nguoi_mua": _S, "mst_nguoi_mua": _S, "ky_hieu": _S,
    "so_hoa_don": _S, "ngay_lap": _S, "tien_truoc_thue": _I, "tien_thue": _I, "tong_thanh_toan": _I,
    "ma_cqt": _S, "hop_le": {"type": "BOOLEAN"}, "luu_y": {"type": "ARRAY", "items": _S}},
    "required": ["hop_le", "luu_y"]}
EXTRACT_SCHEMA = {"type": "OBJECT", "properties": {
    "la_hoa_don": {"type": "BOOLEAN"}, "mau_so": _S, "ky_hieu": _S, "so_hoa_don": _S, "ngay_lap": _S,
    "ten_nguoi_ban": _S, "mst_nguoi_ban": _S, "ten_nguoi_mua": _S, "mst_nguoi_mua": _S,
    "tien_truoc_thue": _I, "thue_suat": _S, "tien_thue": _I, "tong_thanh_toan": _I, "ma_cqt": _S,
    "dong": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "ten": _S, "dvt": _S, "so_luong": {"type": "NUMBER"}, "don_gia": _I, "thanh_tien": _I, "thue_suat": _S}}}},
    "required": ["la_hoa_don"]}
CLASSIFY_SCHEMA = {"type": "OBJECT", "properties": {"ket_qua": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {"id": _S, "ma_nhom": _S, "so_thang": _I,
                                     "do_chac": {"type": "STRING", "enum": ["cao", "trung_binh", "thap"]},
                                     "ly_do": _S},
    "required": ["id", "do_chac", "ly_do"]}}}, "required": ["ket_qua"]}

CLASSIFY_RULES = """Bạn là kế toán Việt Nam. Phân loại từng dòng hàng hóa, dịch vụ trên hóa đơn đầu vào của công ty
vào ĐÚNG MỘT nhóm trong DANH MỤC bên dưới (danh mục theo quy định đã cài trong phần mềm). Chỉ được trả mã nhóm có
trong danh mục, không tự đặt nhóm mới. Quy tắc:
- Nhóm kiểu GIA_TRI / CCDC: đơn giá chưa thuế một đơn vị từ 30.000.000 đồng là TSCĐ, dưới là CCDC.
- Nhóm CHO_PB: chi phí trả trước phân bổ nhiều tháng; CHI_PHI: chi phí trong kỳ; KHONG: không tính chi phí.
- Sửa chữa, bảo dưỡng, thuê, dịch vụ liên quan tới một tài sản là chi phí dịch vụ đó, không phải tài sản.
- so_thang: thời gian phân bổ / khấu hao hợp lý trong khung của nhóm (bỏ trống với chi phí trong kỳ).
- do_chac: "cao" khi rõ ràng, "trung_binh" khi khá chắc, "thap" khi tên hàng không đủ thông tin (khi đó có thể bỏ
  trống ma_nhom). ly_do: một câu ngắn tiếng Việt."""


def catalog_text() -> str:
    return "\n".join(f"{n.ma} | {n.ten} | {n.kieu} | {n.khung or '-'} | {n.thang or '-'}" for n in dm.CATALOG)


def apply_suggestion(s: dict, don_gia: int) -> dict | None:
    """Gợi ý của Gemini → giá trị phân loại hợp lệ theo danh mục (mã có trong danh mục, đúng ngưỡng 30 triệu);
    không hợp lệ / không chắc thì None."""
    nhom = dm.BY_CODE.get((s.get("ma_nhom") or "").strip().upper())
    if nhom is None or s.get("do_chac") == "thap":
        return None
    pl = dm.default_class(nhom, don_gia or 0)
    months = s.get("so_thang") if pl in dm.TAI_SAN else None
    if pl in dm.TAI_SAN and not (isinstance(months, int) and 1 <= months <= 600):
        months = dm.default_months(nhom, pl, don_gia or 0)
    if pl in dm.TAI_SAN and not months:
        return None
    return {"PhanLoai": pl, "MaNhomDM": nhom.ma, "SoThang": months}


# -------------------------------------------------------------------- dịch vụ gắn với CSDL
class GeminiService:
    """Cài đặt, trạng thái kết nối và các việc Gemini làm trên dữ liệu của máy này."""

    def __init__(self, db):
        self.db = db

    def key(self) -> str:
        return local_secret.load(self.db, K_KEY)

    def model(self) -> str:
        m = self.db.setting(K_MODEL) or DEFAULT_MODEL
        return _RESOLVED.get(m, m)

    def configured(self) -> bool:
        return bool(self.key())

    def flag(self, key: str, default: bool = True) -> bool:
        return self.db.setting(key, "1" if default else "0") == "1"

    def settings(self) -> dict:
        return {"co_khoa": self.configured(), "mo_hinh": self.model(), "tu_kiem": self.flag(K_AUTO_CHECK),
                "tu_phan_loai": self.flag(K_AUTO_CLASS), "doc_anh": self.flag(K_AUTO_READ),
                "hoi_truoc": self.flag(K_ASK, False)}

    def save_settings(self, v: dict) -> None:
        if v.get("khoa") is not None:
            local_secret.save(self.db, K_KEY, v["khoa"].strip() or None)
            self.db.set_setting(K_STATE, None)
        self.db.set_setting(K_MODEL, v.get("mo_hinh") or DEFAULT_MODEL)
        for key, name in ((K_AUTO_CHECK, "tu_kiem"), (K_AUTO_CLASS, "tu_phan_loai"), (K_AUTO_READ, "doc_anh"),
                          (K_ASK, "hoi_truoc")):
            if name in v:
                self.db.set_setting(key, "1" if v[name] else "0")

    def client(self) -> Gemini:
        return Gemini(self.key(), self.model())

    # ---- trạng thái kết nối: xanh (đã kết nối), đỏ (mất kết nối), xám (chưa cài khóa)
    def state(self) -> tuple[str, str]:
        if not self.configured():
            return "chua_cai", "Gemini: chưa cài khóa API"
        st, err, t = self.db.setting(K_STATE), self.db.setting(K_ERROR) or "", self.db.setting(K_TIME) or ""
        when = ""
        if t:
            try:
                when = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone().strftime("%H:%M %d/%m")
            except ValueError:
                pass
        if st == "loi":
            return "loi", f"Gemini: mất kết nối từ {when}. {err}".strip()
        if st == "ok":
            return "ok", f"Gemini: đã kết nối (lần cuối {when})"
        return "chua_ro", "Gemini: đã cài khóa, chưa kết nối lần nào"

    def record_ok(self) -> None:
        m = self.db.setting(K_MODEL)
        if m and _RESOLVED.get(m) and _RESOLVED[m] != m:        # đã tự chuyển sang mô hình còn dùng được: lưu lại
            self.db.set_setting(K_MODEL, _RESOLVED[m])
        if self.db.setting(K_STATE) != "ok":
            self.db.set_setting(K_STATE, "ok")
            self.db.set_setting(K_ERROR, None)
        self.db.set_setting(K_TIME, now_iso())

    def record_error(self, e: GeminiError) -> None:
        if not e.offline:
            return
        if self.db.setting(K_STATE) != "loi":
            self.db.set_setting(K_TIME, now_iso())
        self.db.set_setting(K_STATE, "loi")
        self.db.set_setting(K_ERROR, str(e))

    # ---- việc chờ Gemini (để báo khi mất kết nối và để "Rà soát lại bằng Gemini")
    def unreviewed_lines(self) -> list:
        """Dòng hóa đơn đầu vào phần mềm tự phân loại chưa được Gemini rà soát."""
        return self.db.query(
            "SELECT d.*, h.TenNhaCungCap, h.MaHopDong FROM DongHoaDonDauVao d JOIN HoaDonDauVao h ON h.Ma = d.MaHoaDon"
            " WHERE d.DaXoa = 0 AND h.DaXoa = 0 AND (d.GeminiTT IS NULL OR d.GeminiTT = '')"
            " ORDER BY h.NgayNhanHD DESC")

    def issue_invoices(self) -> list[tuple[str, dict]]:
        out = []
        for table, kind in (("HoaDonDauVao", "vao"), ("HoaDonDauRa", "ra")):
            for r in self.db.query(f"SELECT * FROM {table} WHERE DaXoa = 0 AND GeminiTT = ?", (ST_ISSUE,)):
                out.append((kind, dict(r)))
        return out

    # ---- phân loại dòng hóa đơn
    def classify(self, lines: list, gem: Gemini | None = None, batch: int = 40) -> dict:
        """Gửi Gemini các dòng (kèm danh mục), ghi kết quả vào CSDL:
        - dòng phần mềm tự phân loại (TuDong = 1): kết quả hợp lệ được tự cập nhật (giữ phân loại cũ để trả về);
        - dòng kế toán đã chọn (TuDong = 0): không ghi đè, chỉ ghi Gemini đồng ý / gợi ý khác;
        - Gemini không chắc: không cập nhật, tô màu để kế toán phân loại.
        Lỗi mạng / khóa: ném GeminiError (các dòng còn lại vẫn "chưa rà soát")."""
        from .assets import AssetService

        gem = gem or self.client()
        svc = AssetService(self.db)
        res = {"doi": 0, "dong_y": 0, "khong_chac": 0, "khac": 0}
        lines = [dict(x) for x in lines]
        for i in range(0, len(lines), batch):
            chunk = lines[i:i + batch]
            ask = [{"id": ln["Ma"], "ten": ln["TenHang"], "so_luong": ln["SoLuong"],
                    "don_gia": AssetService.unit_price(ln), "thanh_tien": ln["ThanhTien"],
                    "nguoi_ban": ln.get("TenNhaCungCap") or ""} for ln in chunk]
            try:
                out = gem.classify_lines(ask)
            except GeminiError as e:
                self.record_error(e)
                raise
            self.record_ok()
            by_id = {o.get("id"): o for o in out}
            with self.db.auto():
                for ln in chunk:
                    s = by_id.get(ln["Ma"])
                    if s is None:
                        continue
                    res[self._apply_line(svc, ln, s)] += 1
            self.db.conn.commit()
        return res

    def _apply_line(self, svc, ln: dict, s: dict) -> str:
        unit = svc.unit_price(ln)
        val = apply_suggestion(s, unit)
        goi_y = json.dumps({"ma": (s.get("ma_nhom") or "").upper() or None, "so_thang": s.get("so_thang"),
                            "do_chac": s.get("do_chac"), "ly_do": s.get("ly_do") or "",
                            "phan_loai": val["PhanLoai"] if val else None}, ensure_ascii=False)
        cur = {"PhanLoai": ln["PhanLoai"], "MaNhomDM": ln["MaNhomDM"], "SoThang": ln["SoThang"]}
        same = val is not None and val["PhanLoai"] == cur["PhanLoai"] and val["MaNhomDM"] == cur["MaNhomDM"]
        if val is None:
            self.db.update("DongHoaDonDauVao", ln["Ma"], {"GeminiTT": CL_UNSURE, "GeminiGoiY": goi_y}, commit=False)
            return "khong_chac"
        if same:
            self.db.update("DongHoaDonDauVao", ln["Ma"], {"GeminiTT": CL_AGREE, "GeminiGoiY": goi_y}, commit=False)
            return "dong_y"
        if not ln["TuDong"]:                       # kế toán đã chọn: Gemini không ghi đè
            self.db.update("DongHoaDonDauVao", ln["Ma"], {"GeminiTT": CL_DIFF, "GeminiGoiY": goi_y}, commit=False)
            return "khac"
        v = dict(val, GeminiTT=CL_CHANGED, GeminiGoiY=goi_y, PLTruocGemini=json.dumps(cur, ensure_ascii=False),
                 TuDong=1)
        self.db.update("DongHoaDonDauVao", ln["Ma"], v, commit=False)
        line = self.db.get("DongHoaDonDauVao", ln["Ma"])
        svc._sync_asset(line, self.db.get("HoaDonDauVao", line["MaHoaDon"]))
        svc.renumber_ccdc(commit=False)
        return "doi"

    def revert(self, mas: list[str]) -> int:
        """Trả các dòng về phân loại trước khi Gemini đổi (người dùng bấm, tính là người sửa)."""
        from .assets import AssetService

        svc, n = AssetService(self.db), 0
        for ma in mas:
            ln = self.db.get("DongHoaDonDauVao", ma)
            if not ln or not ln["PLTruocGemini"]:
                continue
            old = json.loads(ln["PLTruocGemini"])
            self.db.update("DongHoaDonDauVao", ma, dict(old, PLTruocGemini=None, GeminiTT=CL_DIFF, TuDong=0),
                           commit=False)
            line = self.db.get("DongHoaDonDauVao", ma)
            svc._sync_asset(line, self.db.get("HoaDonDauVao", line["MaHoaDon"]))
            n += 1
        svc.renumber_ccdc(commit=False)
        self.db.conn.commit()
        return n

    # ---- kiểm tra tình trạng hóa đơn
    @staticmethod
    def compare(kind: str, inv: dict, kq: dict) -> tuple[str, list[dict], list[str]]:
        """So kết quả Gemini đọc với số liệu trong phần mềm → (trạng thái, các dòng so sánh, lưu ý)."""
        def digits(x):
            return "".join(ch for ch in str(x or "") if ch.isdigit())
        seller = (inv.get("TenNhaCungCap"), inv.get("MaThueNCC")) if kind == "vao" else (None, None)
        so = inv.get("SoHoaDonGoc") if kind == "vao" else inv.get("SoHoaDon")
        ngay = (inv.get("NgayNhanHD") if kind == "vao" else inv.get("NgayXuatHD")) or ""
        rows = []
        if kind == "vao":
            rows.append(("Tên người bán", seller[0], kq.get("ten_nguoi_ban"), None))
            rows.append(("MST người bán", seller[1], kq.get("mst_nguoi_ban"),
                         digits(seller[1]) == digits(kq.get("mst_nguoi_ban")) if kq.get("mst_nguoi_ban") else None))
        else:
            rows.append(("Người mua", inv.get("TenNguoiMua"), kq.get("ten_nguoi_mua"), None))
            rows.append(("MST người mua", inv.get("MSTNguoiMua"), kq.get("mst_nguoi_mua"),
                         digits(inv.get("MSTNguoiMua")) == digits(kq.get("mst_nguoi_mua"))
                         if kq.get("mst_nguoi_mua") and inv.get("MSTNguoiMua") else None))
        rows.append(("Số hóa đơn", so, kq.get("so_hoa_don"),
                     digits(so).lstrip("0") == digits(kq.get("so_hoa_don")).lstrip("0") if kq.get("so_hoa_don") else None))
        rows.append(("Ngày lập", ngay[:10], kq.get("ngay_lap"),
                     ngay[:10] == (kq.get("ngay_lap") or "")[:10] if kq.get("ngay_lap") else None))
        tong = inv.get("GiaTriSauThue")
        rows.append(("Tổng thanh toán", tong, kq.get("tong_thanh_toan"),
                     abs(int(tong or 0) - int(kq.get("tong_thanh_toan") or 0)) <= 1
                     if kq.get("tong_thanh_toan") is not None else None))
        rows.append(("Mã cơ quan thuế", "", kq.get("ma_cqt") or "", None))
        cmp = [{"truong": a, "phan_mem": b, "gemini": c, "khop": d} for a, b, c, d in rows]
        notes = [n for n in (kq.get("luu_y") or []) if n and n.strip()]
        # lưu ý lặt vặt của Gemini không tính là có vấn đề; chỉ số liệu lệch hoặc Gemini đánh giá không hợp lệ
        bad = any(r["khop"] is False for r in cmp) or kq.get("hop_le") is False
        return (ST_ISSUE if bad else ST_OK), cmp, notes

    def save_check(self, kind: str, ma: str, kq: dict) -> str:
        table = "HoaDonDauVao" if kind == "vao" else "HoaDonDauRa"
        inv = dict(self.db.get(table, ma))
        st, cmp, notes = self.compare(kind, inv, kq)
        data = {"ket_qua": kq, "so_sanh": cmp, "luu_y": notes, "mo_hinh": self.model()}
        with self.db.auto():
            self.db.update(table, ma, {"GeminiTT": st, "GeminiKQ": json.dumps(data, ensure_ascii=False),
                                       "GeminiLuc": now_iso()})
        return st

    def mark_seen(self, kind: str, ma: str) -> None:
        table = "HoaDonDauVao" if kind == "vao" else "HoaDonDauRa"
        self.db.update(table, ma, {"GeminiTT": ST_SEEN})
