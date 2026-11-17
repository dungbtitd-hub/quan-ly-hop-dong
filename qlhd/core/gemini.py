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
from . import net
from . import local_secret
from .db import now_iso

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LIST_API = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
# tên gọi tắt luôn trỏ tới bản mới nhất; Google ngừng cấp mô hình cũ thì phần mềm tự tìm mô hình còn dùng được
MODELS = ["gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest"]
DEFAULT_MODEL = MODELS[0]
_RESOLVED: dict[str, str] = {}           # mô hình đã chọn → mô hình thay thế đang chạy được (trong phiên chạy)
_RETIRED: dict[str, str] = {}            # mô hình đã ngừng cấp → mô hình thay thế (lưu lại vào cài đặt)


def ranked_models(names: list[str], wanted: str = "") -> list[str]:
    """Mô hình thay thế từ danh sách Google trả về, tốt trước: cùng loại (flash / pro / flash-lite), bản chính thức
    số cao trước, rồi tên gọi tắt -latest; bỏ bản thử nghiệm, ảnh, giọng nói."""
    kind = "flash-lite" if "lite" in wanted else "pro" if "pro" in wanted else "flash"
    pat = re.compile(rf"^gemini-(\d+(?:\.\d+)?)-{kind}$")
    found = []
    for n in names:
        m = pat.match(n)
        if m:
            found.append((float(m.group(1)), n))
    out = [n for _, n in sorted(found, reverse=True)]
    alias = f"gemini-{kind}-latest"
    if alias in names:
        out.append(alias)
    return out


def best_model(names: list[str], wanted: str = "") -> str | None:
    r = ranked_models(names, wanted)
    return r[0] if r else None
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
        return self.kind in ("mang", "khoa", "han_muc", "chua_cai", "qua_tai")


_NO_THINK_CFG: set[str] = set()          # mô hình không nhận thinkingConfig


def parse_json_reply(data: dict) -> dict:
    """Phần trả lời của Gemini → JSON (bỏ phần suy nghĩ, bỏ ```json bao ngoài nếu có)."""
    try:
        cand = data["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        cand, text = (data.get("candidates") or [{}])[0], ""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
    for cand_text in (t, t[t.find("{"):t.rfind("}") + 1] if "{" in t else ""):
        if cand_text:
            try:
                return json.loads(cand_text)
            except ValueError:
                pass
    reason = (data.get("promptFeedback") or {}).get("blockReason") or cand.get("finishReason")
    if reason == "MAX_TOKENS":
        raise GeminiError("Hóa đơn dài, Gemini trả lời bị cắt giữa chừng.", "dai")
    names = {"SAFETY": "bị chặn vì lý do an toàn", "RECITATION": "bị chặn do trích dẫn", "OTHER": "lỗi khác của Gemini"}
    raise GeminiError("Gemini không trả về kết quả đọc được (" + (names.get(reason) or reason or "không rõ lý do")
                      + ").")


class Gemini:
    def __init__(self, key: str, model: str = DEFAULT_MODEL, timeout: float = 180):
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
            with net.urlopen(req, timeout=min(self.timeout, 30)) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise GeminiError("Khóa API Gemini không hợp lệ hoặc chưa được cấp quyền.", "khoa") from None
            raise GeminiError(f"Không lấy được danh sách mô hình Gemini ({e.code}).", "mang") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            if net.is_ssl_error(e):
                raise GeminiError(f"Lỗi chứng chỉ bảo mật khi kết nối Gemini: {getattr(e, 'reason', e)}", "mang") from None
            raise GeminiError(f"Không kết nối được Gemini (mất mạng?): {getattr(e, 'reason', e)}", "mang") from None
        return [m["name"].split("/", 1)[-1] for m in data.get("models", [])
                if "generateContent" in (m.get("supportedGenerationMethods") or [])]

    def _post(self, body: dict) -> dict:
        """Gửi yêu cầu. Mô hình đã ngừng cấp (404) hoặc đang quá tải / không trả lời: thử các mô hình cùng loại còn
        dùng được (tối đa 6 mô hình), nhớ mô hình chạy được."""
        try:
            return self._post_once(body)
        except GeminiError as e:
            if e.kind not in ("mo_hinh", "qua_tai", "han_muc"):
                raise
            first = e
        names = self.list_models()
        hints = [h.rstrip(".") for h in re.findall(r"models/(gemini-[\w.\-]+)", str(first.detail or ""))]
        tried = {self.model}
        cands = [h for h in hints if h in names] + ranked_models(names, self.requested)
        cands = [c for c in dict.fromkeys(cands) if c not in tried][:6]
        if not cands:
            if first.kind == "mo_hinh":
                raise GeminiError(f"Mô hình {self.model} không còn dùng được và không tìm thấy mô hình thay thế. "
                                  "Chọn mô hình khác ở Cài đặt › Thư mục hóa đơn và Gemini.") from None
            raise first
        # hỏi thử song song (câu rất ngắn), lấy mô hình trả lời nhanh nhất, rồi mới gửi yêu cầu thật
        good = self._fastest(cands)
        last = first
        for c in good + [c for c in cands if c not in good][:1]:
            self.model = c
            try:
                res = self._post_once(body)
            except GeminiError as e:
                if e.kind not in ("mo_hinh", "qua_tai", "han_muc"):
                    raise
                last = e
                continue
            _RESOLVED[self.requested] = c
            if first.kind == "mo_hinh":
                _RETIRED[self.requested] = c
            return res
        raise GeminiError(f"Gemini đang quá tải, đã thử {', '.join([*tried, *cands])}: {last}", "qua_tai")

    def _fastest(self, cands: list[str], wait: float = 20) -> list[str]:
        """Các mô hình trả lời được câu hỏi thử, nhanh trước."""
        import concurrent.futures as cf
        import time

        probe = {"contents": [{"role": "user", "parts": [{"text": "ok"}]}],
                 "generationConfig": {"maxOutputTokens": 8}}

        def one(c):
            g = Gemini(self.key, c, timeout=wait)
            g.model = c
            t = time.monotonic()
            g._post_once(probe)
            return time.monotonic() - t
        ok = []
        ex = cf.ThreadPoolExecutor(max_workers=len(cands))
        futs = {ex.submit(one, c): c for c in cands}
        try:
            for f in cf.as_completed(futs, timeout=wait + 5):
                try:
                    f.result()
                except Exception:  # noqa: BLE001 - mô hình quá tải / lỗi: bỏ qua
                    continue
                ok.append(futs[f])
                break                      # mô hình trả lời đầu tiên là đủ, không chờ mô hình đang treo
        except cf.TimeoutError:
            pass
        ex.shutdown(wait=False, cancel_futures=True)
        return ok

    def choose_model(self) -> list[str]:
        """Dùng khi Kiểm tra kết nối: hỏi thử song song các mô hình cùng loại còn được cấp, chọn mô hình trả lời
        nhanh nhất (bỏ mô hình đang quá tải). Trả danh sách mô hình khóa API dùng được."""
        names = self.list_models()
        cands = ranked_models(names, self.requested)[:6]
        if self.requested in names and self.requested not in cands:
            cands.insert(0, self.requested)
        good = self._fastest(cands) if cands else []
        if good:
            self.model = good[0]
            _RESOLVED[self.requested] = good[0]
        return names

    def _post_once(self, body: dict) -> dict:
        req = urllib.request.Request(API.format(model=self.model), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        try:
            with net.urlopen(req, timeout=self.timeout) as r:
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
                raise GeminiError(f"Mô hình {self.model} của Gemini đang quá tải / lỗi ({e.code}), thử lại sau.",
                                  "qua_tai") from None
            if e.code == 404 or "no longer available" in msg or "is not found" in msg:
                err = GeminiError(f"Mô hình {self.model} không còn dùng được: {msg[:200]}", "mo_hinh")
                err.detail = msg
                raise err from None
            raise GeminiError(f"Gemini từ chối yêu cầu ({e.code}): {msg[:200]}") from None
        except (socket.timeout, TimeoutError) as e:
            raise GeminiError(f"Mô hình {self.model} của Gemini không trả lời kịp ({e}).", "qua_tai") from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise GeminiError(f"Mô hình {self.model} của Gemini không trả lời kịp.", "qua_tai") from None
            if net.is_ssl_error(e):
                raise GeminiError(f"Lỗi chứng chỉ bảo mật khi kết nối Gemini: {e.reason}", "mang") from None
            raise GeminiError(f"Không kết nối được Gemini (mất mạng?): {e.reason}", "mang") from None
        except (ConnectionError, OSError) as e:
            raise GeminiError(f"Không kết nối được Gemini (mất mạng?): {e}", "mang") from None

    def ask_json(self, parts: list[dict], schema: dict | None, max_tokens: int = 32768,
                 temperature: float = 0) -> dict:
        """Hỏi, nhận JSON. Giới hạn phần "suy nghĩ" của mô hình (tốn chỗ trả lời, dễ bị cắt với hóa đơn dài);
        mô hình không nhận tham số này thì gửi lại không kèm. schema None: không ép khuôn JSON."""
        cfg = {"responseMimeType": "application/json", "temperature": temperature, "maxOutputTokens": max_tokens}
        if schema is not None:
            cfg["responseSchema"] = schema
        if self.model not in _NO_THINK_CFG:
            cfg["thinkingConfig"] = {"thinkingBudget": 1024}
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
        try:
            data = self._post(body)
        except GeminiError as e:
            if "thinkingConfig" not in cfg or "think" not in str(e).lower():
                raise
            _NO_THINK_CFG.add(self.model)
            del cfg["thinkingConfig"]
            data = self._post(body)
        return parse_json_reply(data)

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
        """Đọc đủ các trường như hóa đơn XML: nhiều dòng hàng, nhiều mức thuế, chiết khấu, phí / lệ phí khác.
        Hóa đơn rất dài (trả lời bị cắt) thì hỏi lại, gộp bớt dòng hàng."""
        part = self.file_part(path)
        tries = ((MAX_LINES, EXTRACT_SCHEMA, 0), (15, EXTRACT_SCHEMA, 0.2), (15, None, 0.2))
        for i, (n, schema, temp) in enumerate(tries):
            prompt = EXTRACT_PROMPT.format(n=n) + ("" if schema else "\n\nTrả về đúng một đối tượng JSON với các khóa: "
                                                  + ", ".join(EXTRACT_SCHEMA["properties"]) + ".")
            try:
                g = self.ask_json([part, {"text": prompt}], schema, temperature=temp)
                return self._fill_header(part, g)
            except GeminiError as e:
                # bị cắt (thường do mô hình lặp lại dòng khi ép khuôn JSON) hoặc trả lời hỏng: hỏi lại cách khác
                if e.kind not in ("dai", "khac") or i == len(tries) - 1:
                    if e.kind == "dai":
                        raise GeminiError("Gemini chưa đọc hết được hóa đơn này (trả lời bị cắt giữa chừng). Bấm "
                                          "\"Đọc bằng Gemini\" để thử lại hoặc nhập tay.", "dai") from None
                    raise
        raise GeminiError("Gemini chưa đọc được hóa đơn này.")

    def _fill_header(self, part: dict, g: dict) -> dict:
        """Còn thiếu thông tin chính (số, ngày, người bán, tổng tiền) thì hỏi riêng phần đầu hóa đơn (nhanh, ngắn)."""
        if not isinstance(g, dict) or g.get("la_hoa_don") is False:
            return g
        if not any(_empty(g.get(k)) for k in MAIN_FIELDS):
            return g
        try:
            h = self.ask_json([part, {"text": HEADER_PROMPT}], HEADER_SCHEMA, max_tokens=4096)
        except GeminiError as e:
            if e.offline:
                raise
            return g
        for k in HEADER_KEYS:
            if k != "la_hoa_don" and _empty(g.get(k)) and not _empty(h.get(k)):
                g[k] = h[k]
        return g

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
MAX_LINES = 80
EXTRACT_PROMPT = """Đây là hóa đơn Việt Nam (hóa đơn GTGT, hóa đơn bán hàng, hóa đơn điện / nước / viễn thông, vé,
biên lai… dạng ảnh chụp hoặc PDF). Đọc chính xác và trả về JSON đủ các trường như hóa đơn điện tử XML:
- Thông tin chung: mẫu số (mau_so), ký hiệu (ky_hieu), số (so_hoa_don), ngày lập (ngay_lap, YYYY-MM-DD), mã của
  cơ quan thuế (ma_cqt), đơn vị tiền tệ (don_vi_tien_te, mặc định VND), tỷ giá, hình thức thanh toán, số hợp đồng
  nếu có ghi trên hóa đơn (so_hop_dong).
- Người bán / người mua: tên, MST, địa chỉ.
- dong: từng dòng hàng hóa, dịch vụ theo đúng thứ tự trên hóa đơn: tên, đơn vị tính, số lượng, đơn giá, thành tiền
  CHƯA thuế, thuế suất của dòng, tính chất ("hang_hoa", "khuyen_mai", "chiet_khau", "ghi_chu"). Dòng chiết khấu
  thương mại ghi thành tiền là số dương và tính chất "chiet_khau". Hóa đơn có nhiều hơn {n} dòng thì ghi {n} dòng
  đầu, phần còn lại gộp thành MỘT dòng "Các hàng hóa, dịch vụ khác" với tổng thành tiền của các dòng đó.
- thue_theo_muc: bảng tổng hợp theo từng mức thuế suất (thuế suất, tiền chưa thuế, tiền thuế) — ghi đủ mọi mức
  có trên hóa đơn (VD 10% và 8%, KCT, KKKNT).
- phi_khac: các khoản phí, lệ phí, thuế khác ngoài thuế GTGT (VD phí bảo vệ môi trường, phí dịch vụ, lệ phí trước
  bạ, thuế tiêu thụ đặc biệt ghi riêng): tên và số tiền.
- Tổng: tiền chiết khấu (tien_chiet_khau), tổng tiền chưa thuế (tien_truoc_thue), tổng tiền thuế GTGT (tien_thue),
  tổng tiền thanh toán (tong_thanh_toan), số tiền bằng chữ.
Quy ước: số tiền là số nguyên (đồng), bỏ dấu chấm / phẩy phân cách nghìn. Thuế suất dạng '10%', '8%', '5%', '0%',
'KCT' hoặc 'KKKNT'. Trường nào không có hoặc không đọc chắc chắn thì để trống (không đoán). la_hoa_don = false nếu
tệp không phải hóa đơn."""
_DONG = {"type": "OBJECT", "properties": {
    "ten": _S, "dvt": _S, "so_luong": {"type": "NUMBER"}, "don_gia": {"type": "NUMBER"}, "thanh_tien": _I,
    "thue_suat": _S, "tinh_chat": {"type": "STRING", "enum": ["hang_hoa", "khuyen_mai", "chiet_khau", "ghi_chu"]}}}
EXTRACT_SCHEMA = {"type": "OBJECT", "properties": {
    "la_hoa_don": {"type": "BOOLEAN"}, "mau_so": _S, "ky_hieu": _S, "so_hoa_don": _S, "ngay_lap": _S,
    "ma_cqt": _S, "don_vi_tien_te": _S, "ty_gia": {"type": "NUMBER"}, "hinh_thuc_thanh_toan": _S, "so_hop_dong": _S,
    "ten_nguoi_ban": _S, "mst_nguoi_ban": _S, "dia_chi_nguoi_ban": _S,
    "ten_nguoi_mua": _S, "mst_nguoi_mua": _S, "dia_chi_nguoi_mua": _S,
    "dong": {"type": "ARRAY", "items": _DONG},
    "thue_theo_muc": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "thue_suat": _S, "tien_truoc_thue": _I, "tien_thue": _I}}},
    "phi_khac": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"ten": _S, "so_tien": _I}}},
    "tien_chiet_khau": _I, "tien_truoc_thue": _I, "thue_suat": _S, "tien_thue": _I, "tong_thanh_toan": _I,
    "tien_bang_chu": _S}}
# Trường chung bắt buộc phải trả (để trống nếu không có): chỉ ghi "required" mỗi la_hoa_don thì Gemini hay bỏ qua
# luôn số hóa đơn, ngày, người bán… → phần mềm báo "Thiếu …". Thứ tự: thông tin chung, tổng tiền trước, dòng hàng
# (dài nhất) sau cùng — trả lời bị cắt vẫn còn đủ thông tin chính.
HEADER_KEYS = ["la_hoa_don", "mau_so", "ky_hieu", "so_hoa_don", "ngay_lap", "ten_nguoi_ban", "mst_nguoi_ban",
               "ten_nguoi_mua", "mst_nguoi_mua", "tien_truoc_thue", "tien_thue", "tong_thanh_toan", "thue_suat"]
EXTRACT_SCHEMA["required"] = HEADER_KEYS + ["thue_theo_muc", "dong"]
EXTRACT_SCHEMA["propertyOrdering"] = HEADER_KEYS + [k for k in EXTRACT_SCHEMA["properties"]
                                                     if k not in HEADER_KEYS and k != "dong"] + ["dong"]
HEADER_SCHEMA = {"type": "OBJECT", "properties": {k: EXTRACT_SCHEMA["properties"][k] for k in HEADER_KEYS},
                 "required": HEADER_KEYS, "propertyOrdering": HEADER_KEYS}
HEADER_PROMPT = """Đây là hóa đơn Việt Nam. Chỉ đọc phần thông tin chung và tổng tiền (KHÔNG cần dòng hàng): mẫu số
(mau_so), ký hiệu (ky_hieu), số hóa đơn (so_hoa_don), ngày lập (ngay_lap, YYYY-MM-DD), tên và MST người bán, tên và MST
người mua, tổng tiền chưa thuế (tien_truoc_thue), tiền thuế GTGT (tien_thue), tổng tiền thanh toán (tong_thanh_toan),
thuế suất (thue_suat, VD '10%'; nhiều mức thì ghi các mức cách nhau bằng '; '). Số tiền là số nguyên (đồng). Trường
nào thật sự không có trên hóa đơn thì để trống. la_hoa_don = false nếu tệp không phải hóa đơn."""
MAIN_FIELDS = ("so_hoa_don", "ngay_lap", "ten_nguoi_ban", "mst_nguoi_ban", "tong_thanh_toan")


def _empty(v) -> bool:
    return v in (None, "", 0, [], {})
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
        if m and _RETIRED.get(m) and _RETIRED[m] != m:          # mô hình cũ đã ngừng cấp: lưu mô hình thay thế
            self.db.set_setting(K_MODEL, _RETIRED[m])
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
