"""Sinh Mã máy ổn định cho từng máy tính (Windows, macOS, Linux)."""
from __future__ import annotations

import hashlib
import platform
import subprocess
import uuid
from pathlib import Path


def _windows_guid() -> str | None:
    try:
        import winreg  # type: ignore

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value)
    except Exception:
        return None


def _mac_uuid() -> str | None:
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split("=")[-1].strip().strip('"')
    except Exception:
        return None
    return None


def _linux_id() -> str | None:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text().strip()
            if v:
                return v
        except OSError:
            pass
    return None


def hardware_seed(data_dir: Path) -> str:
    system = platform.system()
    seed = {"Windows": _windows_guid, "Darwin": _mac_uuid}.get(system, _linux_id)()
    if seed:
        return seed
    # Dự phòng: mã ngẫu nhiên lưu cố định trong thư mục dữ liệu.
    f = data_dir / "machine.seed"
    if not f.exists():
        f.write_text(str(uuid.uuid4()))
    return f.read_text().strip()


def format_machine_id(seed: str) -> str:
    h = hashlib.sha256(("qlhd:" + seed).encode()).hexdigest().upper()[:20]
    return "QLHD-" + "-".join(h[i:i + 4] for i in range(0, 20, 4))


def machine_id(data_dir: Path) -> str:
    return format_machine_id(hardware_seed(data_dir))
