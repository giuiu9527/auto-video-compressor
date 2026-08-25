# -*- coding: utf-8 -*-
"""后台文件夹遍历监控器：检测视频文件状态、文件锁定/写入状态及已压缩判定。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional

from PySide6.QtCore import QThread, Signal

from config import OUTPUT_DIR_NAME, VIDEO_EXTS, WatchConfig
from utils import check_file_writing_status, probe_duration


class FileStatus:
    WAITING = "等待处理"
    PROCESSING = "🎬 压缩中"
    COMPLETED = "✅ 已完成"
    SKIPPED_ALREADY = "⏩ 已跳过 (YS 已有同名 MP4)"
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
        self.excluded_paths = {self._path_key(Path(p)) for p in watch_cfg.excluded_paths}

    @staticmethod
    def _path_key(path: Path) -> str:
        """用于 Windows 下不区分大小写的稳定路径比较。"""
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path.absolute()).casefold()

    def stop(self) -> None:
        with self._stop_lock:
            self._stop_requested = True

    def is_stopped(self) -> bool:
        with self._stop_lock:
            return self._stop_requested

    def update_file_status(self, file_path: Path, status: str, progress: int = 0) -> None:
        self.known_status_map[str(file_path)] = (status, progress)

    def force_process(self, file_path: Path) -> None:
        """让指定文件绕过已有产物与写入冷却检查。"""
        path_str = str(file_path)
        self.forced_paths.add(path_str)
        self.known_status_map.pop(path_str, None)

    def set_force_compress(self, enabled: bool) -> None:
        """实时更新强制压缩设置，并清除由已有产物产生的跳过缓存。"""
        self.watch_cfg.force_compress = enabled
        if enabled:
            self.known_status_map = {
                path: value
                for path, value in self.known_status_map.items()
                if value[0] != FileStatus.SKIPPED_ALREADY
            }

    def exclude_path(self, file_path: Path) -> None:
        """将文件加入本次及后续持久化配置使用的排除集合。"""
        path_str = str(file_path)
        self.excluded_paths.add(self._path_key(file_path))
        self.forced_paths.discard(path_str)
        self.known_status_map.pop(path_str, None)

    @staticmethod
    def _is_in_output_dir(path: Path, root_path: Path) -> bool:
        """压缩输出目录不参与扫描，避免将产物再次作为源视频处理。"""
        try:
            parent_parts = path.relative_to(root_path).parts[:-1]
        except ValueError:
            parent_parts = path.parts[:-1]
        return any(part.casefold() == OUTPUT_DIR_NAME.casefold() for part in parent_parts)

    @staticmethod
    def _has_compressed_output(source: Path) -> bool:
        """检查源文件同级的 YS 中是否已有同名 MP4，匹配时忽略输出前缀。"""
        output_dir = source.parent / OUTPUT_DIR_NAME
        source_stem = source.stem.casefold()
        try:
            # 如果目录中同时有 video.mkv 和 myvideo.mkv，myvideo.mp4 应只归属于
            # 后者，避免把另一个源文件名误当作 video 的输出前缀。
            sibling_source_stems = {
                item.stem.casefold()
                for item in source.parent.iterdir()
                if item.is_file() and item.suffix.casefold() in VIDEO_EXTS
            }
            for candidate in output_dir.iterdir():
                if not candidate.is_file() or candidate.suffix.casefold() != ".mp4":
                    continue

                candidate_stem = candidate.stem.casefold()
                # 先按完整文件名匹配，避免把源文件名本身的 _数字误删；随后再兼容
                # unique_path 为重名产物追加的 _数字。
                stem_variants = [candidate_stem]
                base, separator, counter = candidate_stem.rpartition("_")
                if separator and counter.isdigit():
                    stem_variants.append(base)

                for comparable_stem in stem_variants:
                    if comparable_stem == source_stem:
                        return True
                    if not comparable_stem.endswith(source_stem):
                        continue
                    if (comparable_stem in sibling_source_stems
                            and comparable_stem != source_stem):
                        continue
                    # YS 是专用输出目录，源文件名前的任意文本都视为输出前缀。
                    return True
        except OSError:
            pass
        return False

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

            # YS 是压缩产物专用目录。递归扫描时必须先整体排除，
            # 以免输出文件被再次识别为新的待压缩视频。
            if self._is_in_output_dir(p, root_path):
                continue

            if self._path_key(p) in self.excluded_paths:
                continue

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

            # 1. 仅检查源视频同级 YS 目录中的同名 MP4；输出前缀不参与判定。
            # 单文件右键强制和全局强制压缩均可绕过该检查。
            if (not cfg.force_compress and path_str not in self.forced_paths
                    and self._has_compressed_output(p)):
                st, pct = FileStatus.SKIPPED_ALREADY, 100
                self.known_status_map[path_str] = (st, pct)
                results.append(ScannedFile(p, rel_str, p.stat().st_size / (1024 * 1024), 0.0, st, pct))
                continue

            # 2. 检查文件是否正在被录制/写入独占中（多重防护），支持用户手动强制跳过检测
            if path_str not in self.forced_paths:
                is_locked, remaining_sec, reason = check_file_writing_status(p, cfg.min_stable_sec)
                if is_locked:
                    if remaining_sec > 0:
                        st_desc = f"⏳ 暂跳过 (还需 {remaining_sec}s 冷却)"
                    else:
                        st_desc = f"⏳ 暂跳过 ({reason})"
                    results.append(ScannedFile(p, rel_str, p.stat().st_size / (1024 * 1024), 0.0, st_desc, 0))
                    continue

            # 3. 正常可压缩文件
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
