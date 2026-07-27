#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序启动入口：视频自动循环监控压缩工具。包含崩溃日志捕获。"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from styles import LIGHT_QSS


def log_uncaught_exceptions(ex_type, ex_value, ex_traceback):
    err_text = "".join(traceback.format_exception(ex_type, ex_value, ex_traceback))
    print("CRASH DETECTED:\n", err_text, file=sys.stderr)
    try:
        with open("crash_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] Uncaught Exception:\n{err_text}\n" + "=" * 50 + "\n")
    except Exception:
        pass


sys.excepthook = log_uncaught_exceptions


def main() -> None:
    # 在 Windows 上显式注册 AppUserModelID，确保任务栏正确呈现自定义图标而非 Python/Qt 默认窗口图标
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("giuiu9527.immcompressor.video.1.0")
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(LIGHT_QSS)

    from config import APP_ICON_PATH
    from PySide6.QtGui import QIcon
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
