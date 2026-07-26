# -*- coding: utf-8 -*-
"""后台文件夹遍历监控器：检测视频文件状态、文件锁定/写入状态及已压缩判定。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional

from PySide6.QtCore import QThread, Signal

from config import VIDEO_EXTS, WatchConfig
from utils import check_file_writing_status, probe_duration


class FileStatus:
    WAITING = "等待处理"
    PROCESSING = "🎬 压缩中"
    COMPLETED = "✅ 已完成"
    SKIPPED_ALREADY = "⏩ 已跳过 (已包含产物)"
    SKIPPED_WRITING = "⏳ 暂跳过 (正在录制/写入)"
    FAILED = "❌ 压缩失败"


@dataclass
class ScannedFile:
    file_path: Path
    rel_path: str
    size_mb: float
    duration: float
    status: str
    progress: int = 0


class FolderWatcherWorker(QThread):
    scan_completed_signal = Signal(list)
    log_signal = Signal(str)
    status_signal = Signal(str)

    def __init__(self, watch_cfg: WatchConfig) -> None:
        super().__init__()
        self.watch_cfg = watch_cfg
        self._stop_requested = False
        self._stop_lock = Lock()
        self.known_status_map: dict[str, tuple[str, int]] = {}  # filepath_str -> (status, progress)
        self.forced_paths: set[str] = set()

    def stop(self) -> None:
        with self._stop_lock:
            self._stop_requested = True

    def is_stopped(self) -> bool:
        with self._stop_lock:
            return self._stop_requested

    def update_file_status(self, file_path: Path, status: str, progress: int = 0) -> None:
        self.known_status_map[str(file_path)] = (status, progress)

    def force_process(self, file_path: Path) -> None:
        path_str = str(file_path)
        self.forced_paths.add(path_str)
        self.known_status_map.pop(path_str, None)

    def scan_once(self) -> list[ScannedFile]:
        cfg = self.watch_cfg
        root_path = Path(cfg.watch_dir)
        if not cfg.watch_dir or not root_path.exists():
            return []

        try:
            if cfg.recursive:
                candidates = [p for p in root_path.rglob("*") if p.is_file()]
            else:
                candidates = [p for p in root_path.iterdir() if p.is_file()]
        except Exception as exc:
            self.log_signal.emit(f"扫描文件夹失败: {exc}")
            return []

        results: list[ScannedFile] = []

        for p in candidates:
            if self.is_stopped():
                break

            if p.suffix.lower() not in VIDEO_EXTS:
                continue

            # 计算相对路径
            try:
                rel_str = str(p.relative_to(root_path))
            except ValueError:
                rel_str = p.name

            path_str = str(p)

            # 已经有记忆的状态（如正在压缩/已完成/失败）
            if path_str in self.known_status_map:
                st, pct = self.known_status_map[path_str]
                size_mb = p.stat().st_size / (1024 * 1024) if p.exists() else 0
                results.append(ScannedFile(
                    file_path=p,
                    rel_path=rel_str,
                    size_mb=size_mb,
                    duration=0.0,  # 已经有状态时不重测探针
                    status=st,
                    progress=pct,
                ))
                continue

            # 1. 自动滤除前缀 (ys) 开头的压缩产物文件（不显示在列表里）
            prefix = cfg.skip_prefix.strip()
            if prefix and (p.name.startswith(prefix) or f"{prefix}" in p.stem):
                continue

            # 2. 检查目录下是否已存在相应的压缩产物 (ys)original_stem*
            if prefix:
                out_stem_prefix = f"{prefix}{p.stem}"
                already_compressed = False
                try:
                    for sibling in p.parent.iterdir():
                        if sibling.is_file() and sibling.name.startswith(out_stem_prefix):
                            already_compressed = True
                            break
                except Exception:
                    pass

                if already_compressed:
                    st, pct = FileStatus.COMPLETED, 100
                    self.known_status_map[path_str] = (st, pct)
                    results.append(ScannedFile(p, rel_str, p.stat().st_size / (1024 * 1024), 0.0, st, pct))
                    continue

            # 3. 检查文件是否正在被录制/写入独占中（多重防护），支持用户手动强制跳过检测
            if path_str not in self.forced_paths:
                is_locked, remaining_sec, reason = check_file_writing_status(p, cfg.min_stable_sec)
                if is_locked:
                    if remaining_sec > 0:
                        st_desc = f"⏳ 暂跳过 (还需 {remaining_sec}s 冷却)"
                    else:
                        st_desc = f"⏳ 暂跳过 ({reason})"
                    results.append(ScannedFile(p, rel_str, p.stat().st_size / (1024 * 1024), 0.0, st_desc, 0))
                    continue

            # 4. 正常可压缩文件
            dur = probe_duration(p)
            size_mb = p.stat().st_size / (1024 * 1024)
            st, pct = FileStatus.WAITING, 0
            self.known_status_map[path_str] = (st, pct)
            results.append(ScannedFile(p, rel_str, size_mb, dur, st, pct))

        return results

    def run(self) -> None:
        self.status_signal.emit("后台监控已启动")
        while not self.is_stopped():
            scanned = self.scan_once()
            if self.is_stopped():
                break

            self.scan_completed_signal.emit(scanned)

            # 定时循环等待
            if not self.watch_cfg.enable_timer:
                break

            interval = max(3, self.watch_cfg.interval_sec)
            for _ in range(interval):
                if self.is_stopped():
                    break
                time.sleep(1)

        self.status_signal.emit("后台监控已停止")
