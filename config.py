# -*- coding: utf-8 -*-
"""路径常量与配置数据类。"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".ts", ".m4v", ".webm"}

APP_VERSION = "1.0.6"


def app_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = app_root_dir()


def get_app_icon_path() -> Path:
    candidates = [
        APP_DIR / "icon.ico",
        APP_DIR / "_internal" / "icon.ico",
        Path(__file__).resolve().parent / "icon.ico",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / "icon.ico")
    for p in candidates:
        if p.exists():
            return p
    return APP_DIR / "icon.ico"


APP_ICON_PATH = get_app_icon_path()
SETTINGS_FILE = APP_DIR / "watch_compress_settings.json"


@dataclass
class CompressionConfig:
    # 编码
    video_codec: str = "h264_nvenc"
    audio_codec: str = "aac"
    output_format: str = "mp4"

    # 码率/质量
    rate_mode: str = "cq"        # cq / bitrate / source_match
    cq_value: int = 23
    bitrate_kbps: int = 2500
    audio_bitrate_kbps: int = 128
    preset: str = "p4"

    # 裁剪
    auto_crop: bool = False
    extra_top: int = 0
    extra_bottom: int = 0
    extra_left: int = 0
    extra_right: int = 0

    # 输出前缀
    output_prefix: str = "(ys)"

    # 并发
    max_workers: int = 1

    # 尾部剪切 (提前结束)
    trim_end: bool = False
    trim_end_sec: float = 7.0


@dataclass
class WatchConfig:
    watch_dir: str = ""
    recursive: bool = True
    enable_timer: bool = True
    interval_sec: int = 30
    skip_prefix: str = "(ys)"
    auto_start_compress: bool = True
    min_stable_sec: int = 180
