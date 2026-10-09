# -*- coding: utf-8 -*-
"""更新方式 2：从自建 OpenList 服务器更新（规范见《软件包更新方式.md》）。

协议：<server><channel>update.json 清单 -> 下载 archive -> 校验 .sha256 -> 覆盖安装并重启。
与 GitHub 更新方式（updater.py）互不影响，两者可同时使用。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from config import APP_VERSION, app_root_dir
from updater import parse_version_tuple

DEFAULT_SERVER = "http://38.65.91.124:5244"
# 本软件在 OpenList 上的公开分享路径（以 / 结尾）。开好分享后填入，见《软件包更新方式.md》第 2.2 节。
DEFAULT_CHANNEL = "/p/%E8%B0%B7%E6%AD%8C%E4%BA%91%E7%9B%98/%E8%BD%AF%E4%BB%B6%E6%94%B6%E9%9B%86/myAPP/IMM-Compressor/update.json?sign=_f0sfNLHiwEmgH2P3bQo6rvRdYo-d7sbReU1kICfQEQ=:0"
EXE_NAME = "IMM-Compressor.exe"

CONFIG_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "IMM-Compressor" / "update_config.json"


def normalize_server(text: str) -> str:
    """规范化服务器地址：只允许 协议://主机[:端口]；缺协议自动补 http://。非法抛 ValueError。"""
    text = text.strip().rstrip("/")
    if not text:
        raise ValueError("服务器地址不能为空")
    if "://" not in text:
        text = "http://" + text
    u = urllib.parse.urlparse(text)
    if u.scheme not in ("http", "https") or not u.netloc or u.path not in ("", "/") or u.query:
        raise ValueError("服务器地址只能是 http(s)://主机[:端口]，不能带路径")
    return f"{u.scheme}://{u.netloc}"


def load_update_config() -> tuple[str, str]:
    """返回 (server, channel)；用户配置优先，缺省用默认值。"""
    server, channel = DEFAULT_SERVER, DEFAULT_CHANNEL
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if data.get("server"):
            server = normalize_server(data["server"])
        if data.get("channel"):
            channel = str(data["channel"]).strip()
    except Exception:
        pass
    return server, channel


def save_update_config(server: str, channel: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"server": server, "channel": channel}, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_update_config() -> None:
    CONFIG_PATH.unlink(missing_ok=True)


def manifest_url(server: str, channel: str) -> str:
    if not channel:
        raise ValueError("尚未配置链接路径，请在“更新服务器设置”里填写")
    if not channel.startswith("/"):
        channel = "/" + channel
    return server + channel + ("update.json" if channel.endswith("/") else "")


def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AutoVideoCompressor-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_manifest(server: str, channel: str) -> dict:
    """读取并校验清单，返回 {version, notes, archive_url, sha_url}；失败抛异常（消息可直接展示）。"""
    url = manifest_url(server, channel)
    try:
        data = json.loads(_http_get(url).decode("utf-8-sig"))
    except Exception as exc:
        raise RuntimeError(f"无法读取更新清单 ({url}): {exc}") from exc
    version = str(data.get("version", "")).strip()
    if not version:
        raise RuntimeError("更新清单缺少 version 字段")
    if data.get("archive_path"):
        if not data.get("checksum_path"):
            raise RuntimeError("更新清单缺少 checksum_path 字段")
        archive_url = server + data["archive_path"]
        sha_url = server + data["checksum_path"]
    elif data.get("archive"):
        archive_url = url.rsplit("/", 1)[0] + "/" + urllib.parse.quote(str(data["archive"]))
        sha_url = archive_url + ".sha256"
    else:
        raise RuntimeError("更新清单缺少 archive 字段")
    return {"version": version, "notes": str(data.get("notes", "")),
            "archive_url": archive_url, "sha_url": sha_url}


class ServerUpdateCheckWorker(QThread):
    """后台检查服务器更新。(ok, has_update, version, notes, archive_url, sha_url, error)"""
    finished_signal = Signal(bool, bool, str, str, str, str, str)

    def __init__(self, server: str, channel: str, current: str = APP_VERSION) -> None:
        super().__init__()
        self.server, self.channel, self.current = server, channel, current

    def run(self) -> None:
        try:
            m = fetch_manifest(self.server, self.channel)
        except Exception as exc:
            self.finished_signal.emit(False, False, "", "", "", "", str(exc))
            return
        newer = parse_version_tuple(m["version"]) > parse_version_tuple(self.current)
        self.finished_signal.emit(True, newer, m["version"], m["notes"], m["archive_url"], m["sha_url"], "")


class UpdateServerSettingsDialog(QDialog):
    """更新服务器设置：改地址、测试连接、恢复默认。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("更新服务器设置")
        self.setMinimumWidth(480)
        server, channel = load_update_config()

        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.edit_server = QLineEdit(server)
        self.edit_channel = QLineEdit(channel)
        self.edit_channel.setPlaceholderText("例如 /sd/xxxxxxxx/")
        form.addRow("服务器地址", self.edit_server)
        form.addRow("链接路径", self.edit_channel)
        lay.addLayout(form)
        self.lbl_result = QLabel(""); self.lbl_result.setWordWrap(True)
        lay.addWidget(self.lbl_result)

        row = QHBoxLayout()
        btn_test = QPushButton("测试连接"); btn_save = QPushButton("保存")
        btn_reset = QPushButton("恢复默认"); btn_close = QPushButton("关闭")
        btn_save.setObjectName("PrimaryButton")
        btn_test.clicked.connect(self._test)
        btn_save.clicked.connect(self._save)
        btn_reset.clicked.connect(self._reset)
        btn_close.clicked.connect(self.reject)
        for b in (btn_test, btn_reset): row.addWidget(b)
        row.addStretch(1); row.addWidget(btn_save); row.addWidget(btn_close)
        lay.addLayout(row)

    def _values(self) -> tuple[str, str]:
        return normalize_server(self.edit_server.text()), self.edit_channel.text().strip()

    def _test(self) -> None:
        try:
            server, channel = self._values()
            m = fetch_manifest(server, channel)
            self.lbl_result.setText(f"✅ 成功：服务器上版本 {m['version']}")
        except Exception as exc:
            self.lbl_result.setText(f"❌ {exc}")

    def _save(self) -> None:
        try:
            server, channel = self._values()
        except ValueError as exc:
            QMessageBox.warning(self, "地址无效", str(exc)); return
        save_update_config(server, channel)
        self.edit_server.setText(server)
        self.lbl_result.setText(f"已保存: {server}{channel}")

    def _reset(self) -> None:
        reset_update_config()
        server, channel = load_update_config()
        self.edit_server.setText(server); self.edit_channel.setText(channel)
        self.lbl_result.setText("已恢复默认")


def build_update_bat(zip_path: str, app_dir: Path) -> str:
    """生成覆盖更新脚本内容。for 变量必须恰好两个 %，删自己用单个 %。"""
    return f"""@echo off
chcp 65001 > nul
set "APP={app_dir}"
set "ZIP={zip_path}"
set "LOG=%APP%\\update.log"
set "TMPD=%TEMP%\\imm_update_extract"
echo [%date% %time%] update start >> "%LOG%"
set /a N=0
:wait
tasklist /FI "IMAGENAME eq {EXE_NAME}" 2>nul | find /I "{EXE_NAME}" > nul
if errorlevel 1 goto extract
set /a N+=1
if %N% GEQ 15 goto force
timeout /t 1 /nobreak > nul
goto wait
:force
taskkill /F /IM {EXE_NAME} >> "%LOG%" 2>&1
timeout /t 2 /nobreak > nul
:extract
if exist "%TMPD%" rmdir /s /q "%TMPD%"
powershell -NoProfile -Command "Expand-Archive -LiteralPath $env:ZIP -DestinationPath $env:TMPD -Force" >> "%LOG%" 2>&1
if errorlevel 1 ( echo [%date% %time%] extract FAILED >> "%LOG%" & goto relaunch )
set "SRC=%TMPD%"
if not exist "%TMPD%\\{EXE_NAME}" for /d %%I in ("%TMPD%\\*") do set "SRC=%%I"
xcopy "%SRC%\\*" "%APP%\\" /Y /E /H /C /I >> "%LOG%" 2>&1
echo [%date% %time%] xcopy exit %errorlevel% >> "%LOG%"
:relaunch
start "" "%APP%\\{EXE_NAME}"
rmdir /s /q "%TMPD%" 2> nul
del "%ZIP%" 2> nul
del "%~f0"
"""


def apply_server_update_and_restart(zip_path: str) -> None:
    """写入 UTF-8 BOM 的 .bat，经 cmd.exe /c 启动（不能直接启动 .bat），随后退出自身。"""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("当前为源码运行模式，不会覆盖源码目录；请使用打包后的程序更新。")
    bat = Path(tempfile.gettempdir()) / "imm_server_update.bat"
    bat.write_text(build_update_bat(zip_path, app_root_dir()), encoding="utf-8-sig")
    subprocess.Popen(["cmd.exe", "/c", str(bat)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sys.exit(0)
