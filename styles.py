# -*- coding: utf-8 -*-
"""全局 QSS 样式表：高对比度勾选框及现代紧凑 GUI 样式。"""
from __future__ import annotations

from pathlib import Path
from config import APP_DIR


def get_check_icon_path() -> str:
    bin_dir = APP_DIR / "bin"
    bin_dir.mkdir(exist_ok=True)
    icon_path = bin_dir / "check_icon.png"
    if not icon_path.exists():
        try:
            from PySide6.QtCore import QPointF, Qt
            from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                pix = QPixmap(16, 16)
                pix.fill(Qt.transparent)
                p = QPainter(pix)
                p.setRenderHint(QPainter.Antialiasing)
                pen = QPen(QColor(255, 255, 255), 2.5)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.drawLine(QPointF(3.2, 8.5), QPointF(6.5, 11.8))
                p.drawLine(QPointF(6.5, 11.8), QPointF(12.8, 4.2))
                p.end()
                pix.save(str(icon_path))
        except Exception:
            pass
    return icon_path.as_posix()


ICON_PATH = get_check_icon_path()

LIGHT_QSS = f"""
/* ===== 全局 ===== */
QWidget {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", "Helvetica Neue", sans-serif;
    font-size: 12px;
    color: #2d3436;
}}

QMainWindow {{
    background-color: #f1f3f8;
}}

QToolTip {{
    background-color: #2d3436;
    color: #ecf0f1;
    border: 1px solid #2d3436;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11px;
}}

/* ===== 顶部标题条 ===== */
QFrame#HeaderFrame {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1f6fb2, stop:1 #4aa3df);
    border-radius: 6px;
    border: 0;
}}

QFrame#HeaderFrame QLabel {{
    background: transparent;
}}

QLabel#HeaderTitle {{
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

QLabel#HeaderSubtitle {{
    color: #d6eaf8;
    font-size: 10px;
}}

QLabel#HeaderBadge {{
    color: #ffffff;
    background: rgba(255, 255, 255, 0.2);
    border-radius: 6px;
    padding: 1px 6px;
    font-size: 10px;
}}

/* ===== 卡片式分组 ===== */
QGroupBox {{
    background-color: #ffffff;
    border: 1px solid #dfe4ea;
    border-radius: 6px;
    margin-top: 8px;
    padding: 8px 8px 6px 8px;
    font-weight: 600;
    font-size: 12px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 0px;
    padding: 0 6px;
    background-color: #ffffff;
    color: #1f6fb2;
}}

/* ===== 普通按钮 ===== */
QPushButton {{
    background-color: #ffffff;
    border: 1px solid #d6dbe1;
    border-radius: 4px;
    padding: 3px 10px;
    color: #2d3436;
    min-height: 18px;
    font-size: 12px;
}}

QPushButton:hover {{
    background-color: #eef5fb;
    border-color: #4aa3df;
    color: #1f6fb2;
}}

QPushButton:pressed {{
    background-color: #d6eaf8;
}}

QPushButton:disabled {{
    background-color: #f1f2f6;
    color: #a4b0be;
    border-color: #e2e7ee;
}}

/* ===== 主按钮（开始监控） ===== */
QPushButton#PrimaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2ecc71, stop:1 #27ae60);
    color: white;
    border: 1px solid #1e8449;
    font-weight: 600;
    padding: 4px 16px;
}}

QPushButton#PrimaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #58d68d, stop:1 #2ecc71);
}}

QPushButton#PrimaryButton:disabled {{
    background: #bdc3c7;
    border-color: #bdc3c7;
}}

/* ===== 危险按钮（停止） ===== */
QPushButton#DangerButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #e74c3c, stop:1 #c0392b);
    color: white;
    border: 1px solid #a93226;
    font-weight: 600;
    padding: 4px 16px;
}}

QPushButton#DangerButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ec7063, stop:1 #e74c3c);
}}

QPushButton#DangerButton:disabled {{
    background: #f5b7b1;
    color: #ffffff;
    border-color: #f5b7b1;
}}

/* ===== 文本输入 ===== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: #ffffff;
    border: 1px solid #d6dbe1;
    border-radius: 4px;
    padding: 2px 6px;
    selection-background-color: #4aa3df;
    selection-color: white;
    min-height: 18px;
    font-size: 12px;
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: #1f6fb2;
    background-color: #fbfdff;
}}

QLineEdit:read-only {{
    background-color: #f5f7fa;
    color: #57606f;
}}

/* ===== 表格控件 (QTableWidget) ===== */
QTableWidget {{
    background-color: #ffffff;
    border: 1px solid #dfe4ea;
    border-radius: 6px;
    gridline-color: #f1f2f6;
    alternate-background-color: #f8fafc;
    font-size: 12px;
}}

QTableWidget::item {{
    padding: 4px 6px;
}}

QTableWidget::item:selected {{
    background-color: #d6eaf8;
    color: #1f6fb2;
}}

QHeaderView::section {{
    background-color: #e8ecf1;
    color: #2d3436;
    font-weight: 600;
    border: 0;
    border-bottom: 1px solid #dfe4ea;
    border-right: 1px solid #dfe4ea;
    padding: 4px 8px;
}}

/* ===== 日志框 ===== */
QPlainTextEdit#LogBox {{
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", "Menlo", monospace;
    font-size: 11px;
    background-color: #1e272e;
    color: #d2dae2;
    border: 1px solid #1e272e;
    border-radius: 4px;
    padding: 6px;
}}

/* ===== Tab ===== */
QTabWidget::pane {{
    background: #ffffff;
    border: 1px solid #dfe4ea;
    border-radius: 6px;
    top: -1px;
}}

QTabBar::tab {{
    background: #e8ecf1;
    border: 1px solid #dfe4ea;
    border-bottom: 0;
    padding: 4px 14px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: #57606f;
    min-width: 80px;
    font-size: 12px;
}}

QTabBar::tab:selected {{
    background: #ffffff;
    color: #1f6fb2;
    font-weight: 600;
    border-color: #dfe4ea;
}}

/* ===== 进度条 ===== */
QProgressBar {{
    background-color: #e8ecf1;
    border: 1px solid #d6dbe1;
    border-radius: 4px;
    text-align: center;
    color: #2d3436;
    height: 18px;
    font-weight: 600;
    font-size: 11px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4aa3df, stop:0.5 #1f6fb2, stop:1 #2ecc71);
    border-radius: 3px;
    margin: 0.5px;
}}

/* ===== Checkbox 勾选框显眼强化样式 ===== */
QCheckBox {{
    spacing: 6px;
    color: #2d3436;
    background: transparent;
    font-size: 12px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid #718093;
    border-radius: 4px;
    background-color: #ffffff;
}}

QCheckBox::indicator:hover {{
    border-color: #1f6fb2;
    background-color: #eef5fb;
}}

QCheckBox::indicator:checked {{
    border-color: #1f6fb2;
    background-color: #1f6fb2;
    image: url("{ICON_PATH}");
}}

QCheckBox::indicator:disabled {{
    border-color: #dcdfe6;
    background-color: #f1f2f6;
}}

/* ===== 状态栏 ===== */
QStatusBar {{
    background: #ffffff;
    color: #57606f;
    border-top: 1px solid #dfe4ea;
    font-size: 11px;
}}

QLabel#StatusDot {{
    color: #2ecc71;
    font-size: 12px;
}}

QLabel#StatusDotBusy {{
    color: #f39c12;
    font-size: 12px;
}}
"""
