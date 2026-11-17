"""Gọi HTTPS (Gemini, Telegram) dùng bộ chứng chỉ gốc đóng gói kèm phần mềm (certifi).

Bản đóng gói trên macOS không dùng được kho chứng chỉ của hệ điều hành: urllib báo CERTIFICATE_VERIFY_FAILED, nên
mọi kết nối HTTPS hỏng (VD không lưu / kiểm tra được khóa API Gemini). Dùng certifi thì Windows và macOS như nhau."""
from __future__ import annotations

import ssl
import urllib.request

_CTX: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _CTX
    if _CTX is None:
        _CTX = ssl.create_default_context()           # kho chứng chỉ của máy (Windows: có cả chứng chỉ công ty)
        try:
            import certifi
            _CTX.load_verify_locations(cafile=certifi.where())   # thêm bộ chứng chỉ gốc đóng gói kèm
        except Exception:  # noqa: BLE001 - thiếu certifi: chỉ dùng kho của máy
            pass
    return _CTX


def urlopen(req, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def is_ssl_error(e) -> bool:
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)
