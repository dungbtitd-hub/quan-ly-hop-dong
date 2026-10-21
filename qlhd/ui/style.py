"""Giao diện phẳng hiện đại, thanh điều hướng tối bên trái; hai chế độ sáng / tối."""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette

THEMES = {
    "sang": {
        "bg": "#f4f6f9", "card": "#ffffff", "border": "#e3e7ee", "border_strong": "#d0d5dd", "text": "#1f2937",
        "muted": "#6b7280", "accent": "#2563eb", "accent_hover": "#1d4ed8", "accent_soft": "#e8effd",
        "input": "#ffffff", "header_bg": "#f8fafc", "alt_row": "#fafbfc", "sel": "#dbe8fd", "sel_text": "#111827",
        "danger": "#dc2626", "danger_soft": "#fdecec", "warn": "#b45309", "warn_soft": "#fdf3e1",
        "success": "#15803d", "success_soft": "#e7f6ec", "group_bg": "#eef3fb", "total_bg": "#f3f4f6",
        "topbar": "#ffffff", "sidebar": "#1c2434", "sidebar_text": "#d5dbe5", "sidebar_head": "#7d8aa0",
        "sidebar_sel": "#2b3750", "sidebar_hover": "#252f43", "button": "#ffffff", "button_hover": "#f3f5f8",
    },
    "toi": {
        "bg": "#0f141c", "card": "#171e29", "border": "#263042", "border_strong": "#334155", "text": "#e5e7eb",
        "muted": "#94a3b8", "accent": "#3b82f6", "accent_hover": "#60a5fa", "accent_soft": "#1e2a44",
        "input": "#111823", "header_bg": "#1b2331", "alt_row": "#1a2230", "sel": "#1e3a66", "sel_text": "#ffffff",
        "danger": "#f87171", "danger_soft": "#3a1d22", "warn": "#fbbf24", "warn_soft": "#3a2e14",
        "success": "#4ade80", "success_soft": "#173324", "group_bg": "#1e2838", "total_bg": "#1b2331",
        "topbar": "#131a24", "sidebar": "#0b1017", "sidebar_text": "#cbd5e1", "sidebar_head": "#64748b",
        "sidebar_sel": "#1e293b", "sidebar_hover": "#151d2a", "button": "#1b2331", "button_hover": "#222c3d",
    },
}
THEME_NAMES = {"sang": "Sáng", "toi": "Tối"}
_current = "sang"

QSS = Template("""
* { font-family: "Segoe UI", "SF Pro Text", "Helvetica Neue", Arial, sans-serif; font-size: 10pt; color: $text; }
QMainWindow, QDialog, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget { background: $bg; }
QWidget#Page, QWidget#Content { background: $bg; }
QToolTip { background: $card; color: $text; border: 1px solid $border_strong; padding: 4px; }

/* ---- thanh điều hướng tối */
QWidget#Sidebar { background: $sidebar; }
QWidget#Sidebar QLabel { color: $sidebar_text; background: transparent; }
QLabel#AppName { color: #ffffff; font-size: 12pt; font-weight: 700; }
QLabel#AppCompany { color: $sidebar_text; font-size: 9pt; }
QLabel#Logo { background: $accent; color: #ffffff; border-radius: 6px; font-weight: 700; font-size: 12pt; }
QListWidget#Nav { background: $sidebar; border: none; outline: 0; padding: 4px 8px; }
QListWidget#Nav::item { color: $sidebar_text; padding: 8px 10px; margin: 1px 0; border-radius: 6px; }
QListWidget#Nav::item:hover { background: $sidebar_hover; }
QListWidget#Nav::item:selected { background: $sidebar_sel; color: #ffffff; font-weight: 600; }
QFrame#DevCard { background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.08);
                 border-radius: 8px; }
QWidget#Sidebar QLabel#DevCaption { color: $sidebar_head; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }
QWidget#Sidebar QLabel#DevName { color: #ffffff; font-weight: 600; }
QWidget#Sidebar QLabel#DevLine { color: $sidebar_text; font-size: 9pt; }

/* ---- thanh trên */
QWidget#TopBar { background: $topbar; border-bottom: 1px solid $border; }
QLabel#TopTitle { font-weight: 600; }
QToolButton#UserCard { background: $card; border: 1px solid $border; border-radius: 8px; padding: 4px 10px;
                       text-align: left; }
QToolButton#UserCard:hover { border-color: $border_strong; }
QToolButton#UserCard::menu-indicator { image: none; }
QToolButton#IconButton { background: $card; border: 1px solid $border; border-radius: 8px; padding: 5px; }
QToolButton#IconButton:hover { border-color: $border_strong; background: $button_hover; }
QLabel#Badge { background: $danger; color: #ffffff; border-radius: 8px; font-size: 8pt; font-weight: 700;
               padding: 0 4px; }

/* ---- tiêu đề, chữ */
QLabel#PageTitle { font-size: 17pt; font-weight: 700; padding: 2px 0 2px 0; }
QLabel#PageSub, QLabel#Hint { color: $muted; }
QLabel#Warn { color: $warn; font-weight: 600; }
QLabel#Danger { color: $danger; font-weight: 600; }
QLabel#Success { color: $success; font-weight: 600; }
QLabel#CardTitle { font-size: 11pt; font-weight: 700; }
QLabel#StatLabel { color: $muted; }
QLabel#StatValue { font-size: 20pt; font-weight: 700; }
QLabel#StatValue[tone="danger"] { color: $danger; }
QLabel#StatValue[tone="warn"] { color: $warn; }
QLabel#StatValue[tone="accent"] { color: $accent; }
QLabel#Pill { border-radius: 9px; padding: 1px 8px; font-size: 9pt; font-weight: 600; }
QLabel#Pill[tone="danger"] { background: $danger_soft; color: $danger; }
QLabel#Pill[tone="warn"] { background: $warn_soft; color: $warn; }
QLabel#Pill[tone="success"] { background: $success_soft; color: $success; }
QLabel#Pill[tone="accent"] { background: $accent_soft; color: $accent; }
QLabel#Mono { font-family: Consolas, Menlo, monospace; font-size: 11pt; }

/* ---- thẻ */
QFrame#Card, QLabel#Card { background: $card; border: 1px solid $border; border-radius: 10px; }
QFrame#Card QLabel { background: transparent; }
QFrame#Day { background: $card; border: 1px solid $border; border-radius: 8px; }
QFrame#Day[today="true"] { border: 2px solid $accent; }
QFrame#Day[busy="true"] { background: $header_bg; }
QFrame#AlertRow { border: none; border-left: 3px solid $border_strong; background: transparent; }
QFrame#AlertRow[tone="danger"] { border-left-color: $danger; }
QFrame#AlertRow[tone="warn"] { border-left-color: $warn; }
QFrame#Line { background: $border; max-height: 1px; min-height: 1px; border: none; }

/* ---- nút */
QPushButton, QToolButton {
    background: $button; border: 1px solid $border_strong; border-radius: 6px; padding: 5px 12px; min-height: 18px;
}
QPushButton:hover, QToolButton:hover { background: $button_hover; }
QPushButton:pressed { background: $accent_soft; }
QPushButton:disabled, QToolButton:disabled { color: $muted; background: $bg; border-color: $border; }
QPushButton#Primary, QPushButton:default#Primary { background: $accent; color: #ffffff; border-color: $accent;
                                                   font-weight: 600; }
QPushButton#Primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:disabled { background: $border_strong; border-color: $border_strong; color: $card; }
QPushButton#Danger { color: $danger; }
QPushButton#Danger:hover { background: $danger_soft; border-color: $danger; }
QPushButton#Link { border: none; background: transparent; color: $accent; padding: 2px 4px; }

/* ---- ô nhập */
QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit, QTextEdit {
    background: $input; border: 1px solid $border_strong; border-radius: 6px; padding: 4px 6px;
    selection-background-color: $accent; selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QPlainTextEdit:focus { border-color: $accent; }
QLineEdit:read-only { background: $bg; }
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QSpinBox:disabled { color: $muted; background: $bg; }
QComboBox::drop-down, QDateEdit::drop-down { width: 20px; border: none; }
QComboBox QAbstractItemView { background: $card; border: 1px solid $border_strong; selection-background-color: $sel;
                              selection-color: $sel_text; outline: 0; }
QCheckBox, QRadioButton { background: transparent; spacing: 6px; }

/* ---- bảng, danh sách */
QTableWidget, QTableView, QTreeWidget, QListWidget {
    background: $card; alternate-background-color: $alt_row; border: 1px solid $border; border-radius: 8px;
    gridline-color: $border; selection-background-color: $sel; selection-color: $sel_text; outline: 0;
}
QTableView::item { padding: 2px 6px; border: none; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: $header_bg; border: none; border-bottom: 1px solid $border; border-right: 1px solid $border;
    padding: 6px 8px; color: $muted; font-weight: 600;
}
QTableCornerButton::section { background: $header_bg; border: none; }
QTabWidget::pane { border: 1px solid $border; background: $card; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 7px 14px;
               color: $muted; }
QTabBar::tab:selected { color: $accent; border-bottom-color: $accent; font-weight: 600; }
QTabBar::tab:hover:!selected { color: $text; }
QGroupBox { background: $card; border: 1px solid $border; border-radius: 10px; margin-top: 14px; padding: 12px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; font-weight: 700; }
QSplitter::handle { background: transparent; }
QProgressBar { background: $border; border: none; border-radius: 4px; height: 8px; text-align: center; }
QProgressBar::chunk { background: $success; border-radius: 4px; }

/* ---- thanh trạng thái, menu */
QStatusBar { background: $topbar; color: $muted; border-top: 1px solid $border; }
QStatusBar QLabel { color: $muted; padding: 0 8px; border-left: 1px solid $border; }
QMenuBar { background: $topbar; }
QMenu { background: $card; border: 1px solid $border_strong; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 4px; }
QMenu::item:selected { background: $sel; color: $sel_text; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { height: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMessageBox QLabel { background: transparent; }
""")


def qss(theme: str = "sang") -> str:
    return QSS.substitute(THEMES.get(theme, THEMES["sang"]))


def color(key: str) -> str:
    """Màu của chế độ đang dùng (để tô dòng nhóm / dòng tổng trong bảng)."""
    return THEMES[_current][key]


def current() -> str:
    return _current


def apply_theme(app, theme: str = "sang") -> None:
    global _current
    _current = theme if theme in THEMES else "sang"
    t = THEMES[_current]
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "card"),
                      (QPalette.AlternateBase, "alt_row"), (QPalette.Text, "text"), (QPalette.Button, "button"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"), (QPalette.ToolTipBase, "card"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "muted"), (QPalette.Link, "accent")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(t["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(qss(_current))


# Giữ tên cũ cho mã đang dùng.
WIN7_QSS = qss("sang")
