# -*- coding: utf-8 -*-
"""软件自动检测与在线更新模块（对接 GitHub Release API）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QThread, Signal

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


def apply_zip_update_and_restart(zip_url: str, log_cb=None) -> None:
    """下载 ZIP 文件并生成重启覆盖批处理脚本。"""
    temp_dir = Path(tempfile.gettempdir())
    download_path = temp_dir / "auto_video_compressor_update.zip"

    req = urllib.request.Request(zip_url, headers={"User-Agent": "AutoVideoCompressor-Updater"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(download_path, "wb") as f:
        shutil_copyfileobj(resp, f)

    app_dir = app_root_dir()
    bat_script = temp_dir / "update_restart.bat"
    
    # 撰写重命名/解压覆盖重写脚本
    bat_content = f"""@echo off
chcp 65001 > nul
timeout /t 2 /nobreak > nul
powershell -Command "Expand-Archive -Path '{download_path}' -DestinationPath '{app_dir}' -Force"
start "" "{app_dir}\\视频自动循环监控压缩工具.exe"
del "%~f0"
"""
    bat_script.write_text(bat_content, encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", str(bat_script)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sys.exit(0)


def shutil_copyfileobj(fsrc, fdst, length=64*1024):
    while True:
        buf = fsrc.read(length)
        if not buf:
            break
        fdst.write(buf)
