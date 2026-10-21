"""Giao diện kiểu Windows 7 (Aero Basic): nền xanh nhạt, nút bóng gradient, viền xanh."""

WIN7_QSS = """
* { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 9pt; }
QMainWindow, QDialog { background: #f0f0f0; }
QWidget#Header {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e3eefb, stop:0.5 #cfe0f5, stop:1 #b9d1ee);
    border-bottom: 1px solid #8eaacf;
}
QLabel#HeaderTitle { color: #1e395b; font-size: 13pt; font-weight: 600; }
QLabel#HeaderSub { color: #3b5a82; }
QLabel#PageTitle { color: #1e395b; font-size: 12pt; font-weight: 600; padding: 2px 0 6px 0; }
QLabel#Hint { color: #5a6b80; }
QLabel#Warn { color: #9c5700; }
QLabel#Danger { color: #b00020; font-weight: 600; }
QLabel#Card {
    background: #ffffff; border: 1px solid #b6c9e3; border-radius: 4px; padding: 10px; color: #1e395b;
}

QListWidget#Nav {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f4f8fd, stop:1 #e6eef8);
    border: none; border-right: 1px solid #a9bfdc; padding-top: 6px; outline: 0;
}
QListWidget#Nav::item { padding: 7px 10px; margin: 1px 6px; border: 1px solid transparent; border-radius: 3px; color: #1e395b; }
QListWidget#Nav::item:hover { background: #e5f1fb; border-color: #b8d6fb; }
QListWidget#Nav::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dcebfc, stop:1 #c1dcfc);
    border-color: #7da2ce; color: #0b2a4f;
}

QPushButton, QToolButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f6f6f6, stop:0.49 #e9e9e9, stop:0.5 #dddddd, stop:1 #d1d1d1);
    border: 1px solid #707070; border-radius: 3px; padding: 4px 12px; color: #000; min-height: 16px;
}
QPushButton:hover, QToolButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #eaf6fd, stop:0.49 #d9f0fc, stop:0.5 #bee6fd, stop:1 #a7d9f5);
    border-color: #3c7fb1;
}
QPushButton:pressed, QToolButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e5f4fc, stop:0.5 #98d1ef, stop:1 #68b3db);
    border-color: #2c628b;
}
QPushButton:default { border: 1px solid #3c7fb1; }
QPushButton:disabled, QToolButton:disabled { color: #8c8c8c; border-color: #adb2b5; background: #f4f4f4; }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5aa0e6, stop:0.5 #3a84d3, stop:1 #2a6fbd);
    color: white; border-color: #1f5aa0; font-weight: 600;
}
QPushButton#Primary:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #6fb0f0, stop:1 #3d86d6); }
QPushButton#Danger { color: #a00000; }

QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit, QTextEdit {
    background: white; border: 1px solid #abadb3; border-top-color: #8e8f8f; border-radius: 2px; padding: 3px 4px;
    selection-background-color: #3399ff;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus { border-color: #3d7bad; }
QLineEdit:read-only { background: #f4f4f4; }
QComboBox::drop-down { width: 18px; border-left: 1px solid #c5c5c5; }

QTableWidget, QTableView, QTreeWidget, QListWidget {
    background: white; alternate-background-color: #f5f9fe; border: 1px solid #8e9fb5;
    gridline-color: #e3e8ef; selection-background-color: #cce8ff; selection-color: black;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e8eef6);
    border: none; border-right: 1px solid #d5dde8; border-bottom: 1px solid #a9bcd6; padding: 4px 6px; color: #1e395b;
}
QTabWidget::pane { border: 1px solid #8e9fb5; background: white; top: -1px; }
QTabBar::tab {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2f2f2, stop:1 #dcdcdc);
    border: 1px solid #8e9fb5; border-bottom: none; padding: 5px 12px; margin-right: 2px;
    border-top-left-radius: 3px; border-top-right-radius: 3px;
}
QTabBar::tab:selected { background: white; }
QTabBar::tab:hover:!selected { background: #e5f1fb; }
QGroupBox { border: 1px solid #b6c9e3; border-radius: 4px; margin-top: 10px; padding: 8px; background: #fafcff; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #1e395b; font-weight: 600; }
QStatusBar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f1f5fb, stop:1 #dae3ef); color: #1e395b; }
QMenuBar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbfcfe, stop:1 #e4ecf7); }
QMenuBar::item:selected { background: #d5e6fa; border: 1px solid #a8c8ef; }
QMenu { background: #f0f0f0; border: 1px solid #979797; }
QMenu::item { padding: 4px 24px; }
QMenu::item:selected { background: #d5e6fa; border: 1px solid #a8c8ef; }
QToolBar { background: transparent; border: none; spacing: 4px; }
QScrollBar:vertical { width: 15px; background: #f0f0f0; }
"""
