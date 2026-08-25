# -*- coding: utf-8 -*-
"""工具库：文件占用检测、FFmpeg 命令构建、格式化与 probe 探测。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _bundled_bin_dir() -> Path:
    """返回随程序分发的 bin 目录(打包前/后都对)。"""
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        for candidate in (app_dir / "bin", app_dir / "_internal" / "bin"):
            if (candidate / "ffmpeg.exe").exists() and (candidate / "ffprobe.exe").exists():
                return candidate
        return app_dir / "bin"
    return Path(__file__).resolve().parent / "bin"


_BIN = _bundled_bin_dir()
if _BIN.exists():
    os.environ["PATH"] = str(_BIN) + os.pathsep + os.environ.get("PATH", "")


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def run_cmd(cmd: list[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
    """运行外部命令并捕获文本输出。安全捕获 FileNotFoundError。"""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))


def format_time(seconds: float) -> str:
    """格式化秒数为 HH:MM:SS 或 MM:SS 形式。"""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def unique_path(path: Path) -> Path:
    """产生非冲突的目标路径。"""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def ffmpeg_input_prefix(use_cuda: bool) -> list[str]:
    """硬件加速输入前缀。"""
    return ["-hwaccel", "cuda"] if use_cuda else []


def probe_dimensions(video: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video)
    ]
    p = run_cmd(cmd, timeout=10)
    if p.returncode != 0:
        return 0, 0
    lines = (p.stdout or "").strip().splitlines()
    if len(lines) >= 2:
        try:
            return int(lines[0]), int(lines[1])
        except ValueError:
            pass
    return 0, 0


def probe_bitrate_kbps(video: Path) -> int:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video)
    ]
    p = run_cmd(cmd, timeout=10)
    s = (p.stdout or "").strip()
    if s and s != "N/A":
        try:
            return max(0, int(s) // 1000)
        except ValueError:
            pass
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video)
    ]
    p = run_cmd(cmd, timeout=10)
    s = (p.stdout or "").strip()
    if s and s != "N/A":
        try:
            total = int(s) // 1000
            return max(0, total - 160)
        except ValueError:
            pass
    return 0


def probe_duration(video: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video)
    ]
    p = run_cmd(cmd, timeout=10)
    try:
        return max(0.0, float((p.stdout or "").strip()))
    except ValueError:
        return 0.0


def check_file_writing_status(video_path: Path, min_stable_sec: int = 30) -> tuple[bool, int, str]:
    """
    4重防御机制检测 Syncthing 实时同步及边录边传未完成的视频：
    返回 (is_locked: bool, remaining_sec: int, reason_desc: str)
    """
    if not video_path.exists():
        return True, 0, "文件不存在"

    name_lower = video_path.name.lower()
    # 1. 临时/同步中间文件模式判断
    if (
        name_lower.startswith(".")
        or ".syncthing." in name_lower
        or name_lower.endswith(".tmp")
        or name_lower.endswith(".part")
        or name_lower.endswith(".crdownload")
        or "!syncthing" in name_lower
    ):
        return True, 0, "Syncthing/临时文件写入中"

    # 2. 独占句柄检测（兼容只读文件）
    try:
        with open(video_path, "r+b"):
            pass
    except PermissionError:
        # 若被录制软件独占写入或无法写访问，尝试只读读入
        try:
            with open(video_path, "rb"):
                pass
        except Exception:
            return True, 0, "文件被录制/同步软件独占锁定"
    except OSError:
        return True, 0, "文件系统锁定"

    # 3. 文件修改时间 (st_mtime) 冷却判定
    try:
        mtime = video_path.stat().st_mtime
        elapsed = time.time() - mtime
        if elapsed < min_stable_sec:
            remaining = int(min_stable_sec - elapsed) + 1
            return True, remaining, f"静止冷却中 (还需 {remaining}s)"
    except Exception:
        return True, 0, "获取文件属性失败"

    # 4. 容器完整性探测校验 (MKV/MP4 尚未写完时，尾部索引/Cluster 不完整，ffprobe 会报 error)
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
    ]
    p = run_cmd(cmd, timeout=8)
    if p.returncode != 0 or not (p.stdout or "").strip():
        return True, 0, "视频结构未录制完整"

    return False, 0, ""


def is_file_writing_or_locked(video_path: Path, min_stable_sec: int = 30) -> bool:
    is_locked, _, _ = check_file_writing_status(video_path, min_stable_sec)
    return is_locked
