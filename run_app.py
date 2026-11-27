"""Tệp khởi chạy dùng cho PyInstaller (đóng gói .exe / .app)."""
import sys

from qlhd.app import main

if __name__ == "__main__":
    sys.exit(main())
