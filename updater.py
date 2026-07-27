# -*- coding: utf-8 -*-
"""软件自动检测与在线更新模块（对接 GitHub Release API），支持下载进度条。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from config import APP_VERSION, app_root_dir

GITHUB_OWNER = "giuiu9527"
GITHUB_REPO = "auto-video-compressor"
API_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def parse_version_tuple(ver_str: str) -> tuple[int, ...]:
    """解析版本号字符串为元组比较，如 'v1.0.2' -> (1, 0, 2)。"""
    ver = ver_str.lstrip("vV").strip()
    parts = []
    for p in ver.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_github_update(current_version: str = APP_VERSION) -> tuple[bool, str, str, str]:
    """
    检查 GitHub Release 是否有新版本。
    返回: (是否有更新, 最新版本号, 更新日志, 下载包链接)
    """
    req = urllib.request.Request(
        API_RELEASE_URL,
        headers={"User-Agent": "AutoVideoCompressor-Updater", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return False, current_version, "", ""
            data = json.loads(resp.read().decode("utf-8"))

        tag_name = data.get("tag_name", "")
        body = data.get("body", "")
        assets = data.get("assets", [])

        remote_ver = parse_version_tuple(tag_name)
        curr_ver = parse_version_tuple(current_version)

        if remote_ver > curr_ver:
            download_url = ""
            for asset in assets:
                name = asset.get("name", "").lower()
                if name.endswith(".zip") or name.endswith(".exe"):
                    download_url = asset.get("browser_download_url", "")
                    break
            return True, tag_name, body, download_url
    except Exception:
        pass

    return False, current_version, "", ""


class UpdateCheckWorker(QThread):
    """后台异步更新检测线程。"""
    check_finished_signal = Signal(bool, str, str, str)  # (has_update, new_version, notes, url)

    def __init__(self, current_ver: str = APP_VERSION) -> None:
        super().__init__()
        self.current_ver = current_ver

    def run(self) -> None:
        has_update, new_ver, notes, url = check_github_update(self.current_ver)
        self.check_finished_signal.emit(has_update, new_ver, notes, url)


class UpdateDownloadWorker(QThread):
    """后台下载线程，报告下载进度百分比。"""
    progress_signal = Signal(int, str)       # (百分比 0-100, 状态描述)
    finished_signal = Signal(bool, str)      # (是否成功, 下载路径或错误信息)

    def __init__(self, zip_url: str) -> None:
        super().__init__()
        self.zip_url = zip_url
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        temp_dir = Path(tempfile.gettempdir())
        download_path = temp_dir / "auto_video_compressor_update.zip"
        try:
            req = urllib.request.Request(
                self.zip_url,
                headers={"User-Agent": "AutoVideoCompressor-Updater"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 64 * 1024

                with open(download_path, "wb") as f:
                    while True:
                        if self._cancelled:
                            self.finished_signal.emit(False, "用户取消下载")
                            return

                        buf = resp.read(chunk_size)
                        if not buf:
                            break
                        f.write(buf)
                        downloaded += len(buf)

                        if total_size > 0:
                            pct = min(99, int(downloaded / total_size * 100))
                            size_mb = downloaded / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024)
                            self.progress_signal.emit(
                                pct,
                                f"正在下载: {size_mb:.1f} / {total_mb:.1f} MB ({pct}%)"
                            )
                        else:
                            size_mb = downloaded / (1024 * 1024)
                            self.progress_signal.emit(-1, f"正在下载: {size_mb:.1f} MB")

            self.progress_signal.emit(100, "下载完成，准备安装更新…")
            self.finished_signal.emit(True, str(download_path))

        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class UpdateProgressDialog(QDialog):
    """带进度条的下载更新对话框。"""

    def __init__(self, zip_url: str, new_ver: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"正在更新到 {new_ver}")
        self.setFixedSize(460, 140)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.lbl_status = QLabel("正在连接下载服务器…")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #1f6fb2;")
        layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setFixedWidth(80)
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # 启动后台下载
        self.download_worker = UpdateDownloadWorker(zip_url)
        self.download_worker.progress_signal.connect(self._on_progress)
        self.download_worker.finished_signal.connect(self._on_finished)
        self.download_worker.start()

        self._success = False
        self._result_path = ""

    def _on_progress(self, pct: int, desc: str) -> None:
        self.lbl_status.setText(desc)
        if pct >= 0:
            self.progress_bar.setValue(pct)
        else:
            # 未知总大小时使用忙碌模式
            self.progress_bar.setRange(0, 0)

    def _on_finished(self, success: bool, result: str) -> None:
        if success:
            self._success = True
            self._result_path = result
            self.progress_bar.setValue(100)
            self.lbl_status.setText("✅ 下载完成，正在应用更新…")
            self.btn_cancel.setEnabled(False)
            self.accept()
        else:
            self.lbl_status.setText(f"❌ 下载失败: {result}")
            self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #e74c3c;")
            self.btn_cancel.setText("关闭")

    def _on_cancel(self) -> None:
        if self.download_worker.isRunning():
            self.download_worker.cancel()
            self.download_worker.wait(3000)
        self.reject()

    def closeEvent(self, event) -> None:
        if self.download_worker.isRunning():
            self.download_worker.cancel()
            self.download_worker.wait(3000)
        super().closeEvent(event)

    @property
    def success(self) -> bool:
        return self._success

    @property
    def downloaded_zip_path(self) -> str:
        return self._result_path


def apply_zip_update_and_restart(zip_path: str, log_cb=None) -> None:
    """用已下载的 ZIP 文件生成重启覆盖批处理脚本。"""
    app_dir = app_root_dir()
    temp_dir = Path(tempfile.gettempdir())
    bat_script = temp_dir / "update_restart.bat"

    bat_content = f"""@echo off
chcp 65001 > nul
timeout /t 2 /nobreak > nul
powershell -Command "Expand-Archive -Path '{zip_path}' -DestinationPath '{app_dir}' -Force"
start "" "{app_dir}\\IMM-Compressor.exe"
del "%~f0"
"""
    bat_script.write_text(bat_content, encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", str(bat_script)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sys.exit(0)
