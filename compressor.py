# -*- coding: utf-8 -*-
"""视频压缩核心：硬件/软件解码回退，支持自动去黑边、裁切与尾部剪切。"""
from __future__ import annotations

import re
import time
import subprocess
from pathlib import Path
from typing import Callable, Optional

from config import CompressionConfig, OUTPUT_DIR_NAME
from utils import (
    CREATE_NO_WINDOW, ffmpeg_input_prefix, format_time,
    probe_bitrate_kbps, probe_dimensions, probe_duration, run_cmd, unique_path,
)

VIDEO_CODEC_OPTIONS: dict[str, str] = {
    "h264_nvenc": "H.264 (NVENC)",
    "hevc_nvenc": "H.265 (NVENC)",
    "libx264":    "H.264 (CPU)",
    "libx265":    "H.265 (CPU)",
    "copy":       "复制 (不重编码)",
}

AUDIO_CODEC_OPTIONS: dict[str, str] = {
    "aac":        "AAC",
    "libmp3lame": "MP3",
    "copy":       "复制",
}

RATE_MODE_OPTIONS: dict[str, str] = {
    "cq":           "质量 (CQ/CRF)",
    "bitrate":      "比特率 (kbps)",
    "source_match": "保持原画质 (匹配源)",
}

OUTPUT_FORMAT_OPTIONS = ["mp4", "mkv", "mov"]
PRESET_OPTIONS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "fast", "medium", "slow"]


class VideoCompressor:
    def __init__(
        self,
        cfg: CompressionConfig,
        log: Callable[[str], None],
        stop_check: Callable[[], bool],
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.stop_check = stop_check

    def _get_source_bitrate(self, src: Path) -> int:
        kbps = probe_bitrate_kbps(src)
        if kbps > 0:
            return kbps
        try:
            file_bytes = src.stat().st_size
            dur = probe_duration(src)
            if dur > 0:
                total_kbps = int(file_bytes * 8 / dur / 1000)
                return max(100, total_kbps - 160)
        except Exception:
            pass
        return 0

    def detect_crop(self, src: Path) -> str:
        cfg = self.cfg
        w_src, h_src = probe_dimensions(src)
        if w_src <= 0 or h_src <= 0:
            self.log(f">> [{src.name}] 无法获取尺寸,放弃裁剪")
            return ""

        et, eb, el, er = (
            max(0, cfg.extra_top),
            max(0, cfg.extra_bottom),
            max(0, cfg.extra_left),
            max(0, cfg.extra_right),
        )
        extra_total = et + eb + el + er

        left, top, right, bottom = 0, 0, w_src, h_src
        auto_detected = False

        if cfg.auto_crop:
            duration = probe_duration(src)
            if duration > 0:
                if duration > 600:
                    pcts = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95)
                elif duration > 60:
                    pcts = (0.05, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90)
                elif duration > 10:
                    pcts = (0.1, 0.3, 0.5, 0.7, 0.9)
                else:
                    pcts = (0.5,)
                offsets = [duration * p for p in pcts]

                lefts, rights, tops, bottoms = [], [], [], []
                use_cuda = "nvenc" in cfg.video_codec
                for off in offsets:
                    if self.stop_check():
                        return ""
                    cmd = ["ffmpeg", "-hide_banner", *ffmpeg_input_prefix(use_cuda),
                           "-ss", f"{off:.2f}", "-i", str(src),
                           "-t", "2", "-an", "-sn",
                           "-vf", "cropdetect=limit=40:round=2:reset=1",
                           "-f", "null", "-"]
                    p = run_cmd(cmd, timeout=25)
                    for m in re.finditer(r"crop=(\d+):(\d+):(\d+):(\d+)", p.stderr or ""):
                        w, h, x, y = map(int, m.groups())
                        lefts.append(x); rights.append(x + w)
                        tops.append(y);  bottoms.append(y + h)

                if len(lefts) >= 6:
                    def pct(arr: list[int], q: float) -> int:
                        s = sorted(arr)
                        return s[max(0, min(int(len(s) * q), len(s) - 1))]
                    left   = pct(lefts,   0.75)
                    right  = pct(rights,  0.25)
                    top    = pct(tops,    0.75)
                    bottom = pct(bottoms, 0.25)
                    auto_detected = True
                    self.log(
                        f">> [{src.name}] 黑边 {len(lefts)} 帧 -> "
                        f"L{left} R{right} T{top} B{bottom}"
                    )

        if not auto_detected and extra_total == 0:
            return ""
        if not auto_detected:
            self.log(f">> [{src.name}] 仅按用户额外裁切处理")

        if extra_total > 0:
            self.log(f">> [{src.name}] 额外裁切 上{et} 下{eb} 左{el} 右{er}")
        left += el; right -= er
        top  += et; bottom -= eb

        left   = max(0, left)
        top    = max(0, top)
        right  = min(w_src, right)
        bottom = min(h_src, bottom)

        w, h = right - left, bottom - top
        if w <= 16 or h <= 16:
            self.log(f">> [{src.name}] 裁剪计算异常 (w={w} h={h}),放弃")
            return ""
        w -= w % 2; h -= h % 2
        x = left - (left % 2); y = top - (top % 2)
        self.log(f">> [{src.name}] crop={w}:{h}:{x}:{y} (源 {w_src}x{h_src})")
        return f"crop={w}:{h}:{x}:{y}"

    def build_command(
        self,
        src: Path,
        dst: Path,
        crop_filter: str = "",
        use_hwaccel: bool = True,
        target_duration: Optional[float] = None,
    ) -> list[str]:
        cfg = self.cfg
        v_codec = cfg.video_codec
        cmd = ["ffmpeg", "-hide_banner", "-y", "-threads", "0",
               *ffmpeg_input_prefix(use_hwaccel and "nvenc" in v_codec), "-i", str(src)]

        if target_duration is not None and target_duration > 0:
            cmd += ["-t", f"{target_duration:.3f}"]

        if v_codec == "copy":
            cmd += ["-c:v", "copy"]
        else:
            cmd += ["-c:v", v_codec]
            if crop_filter:
                cmd += ["-vf", crop_filter]

            if cfg.rate_mode == "source_match":
                src_kbps = self._get_source_bitrate(src)
                if src_kbps <= 0:
                    src_kbps = 2000
                    self.log(f">> [{src.name}] 无法探测源码率,回退 2000kbps")
                target = max(int(src_kbps * 1.08), 100)
                self.log(f">> [{src.name}] 源码率 {src_kbps}kbps -> 目标 {target}kbps")
                if "nvenc" in v_codec:
                    cmd += ["-rc", "vbr",
                            "-b:v", f"{target}k",
                            "-maxrate", f"{int(target * 1.5)}k",
                            "-bufsize", f"{target * 3}k",
                            "-preset", "p7",
                            "-profile:v", "high",
                            "-spatial-aq", "1", "-temporal-aq", "1"]
                else:
                    cmd += ["-b:v", f"{target}k",
                            "-maxrate", f"{int(target * 1.5)}k",
                            "-bufsize", f"{target * 3}k",
                            "-preset", "slow"]
            elif "nvenc" in v_codec:
                if cfg.rate_mode == "cq":
                    q = str(cfg.cq_value)
                    cmd += ["-rc", "vbr", "-cq", q, "-qmin", q, "-qmax", q]
                else:
                    br = cfg.bitrate_kbps
                    cmd += ["-b:v", f"{br}k",
                            "-maxrate", f"{br * 2}k",
                            "-bufsize", f"{br * 4}k"]
                cmd += ["-preset", cfg.preset, "-profile:v", "high", "-spatial-aq", "1"]
            else:
                if cfg.rate_mode == "cq":
                    cmd += ["-crf", str(cfg.cq_value)]
                else:
                    cmd += ["-b:v", f"{cfg.bitrate_kbps}k"]
                cmd += ["-preset", cfg.preset, "-tune", "film"]

            cmd += ["-pix_fmt", "yuv420p"]

        a_codec = cfg.audio_codec
        cmd += ["-c:a", a_codec]
        if a_codec != "copy":
            cmd += ["-b:a", f"{cfg.audio_bitrate_kbps}k"]
        cmd += ["-progress", "pipe:1", str(dst)]
        return cmd

    def _run_encode(
        self,
        cmd: list[str],
        src: Path,
        duration: float,
        progress_cb: Optional[Callable[[int], None]],
        status_cb: Optional[Callable[[str, int, float, float], None]],
    ) -> tuple[int, list[str]]:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None
        tail: list[str] = []
        try:
            for line in proc.stdout:
                if self.stop_check():
                    proc.terminate()
                    raise RuntimeError("用户停止任务。")
                if "out_time_ms=" in line:
                    try:
                        ms = int(line.split("=", 1)[1])
                        cur = ms / 1_000_000
                        if duration > 0:
                            pct = min(99, int(cur / duration * 100))
                            if progress_cb:
                                progress_cb(pct)
                            if status_cb:
                                status_cb(src.name, pct, cur, duration)
                    except ValueError:
                        pass
                else:
                    tail.append(line.rstrip())
                    del tail[:-20]
            ret = proc.wait()
        finally:
            if proc.poll() is None:
                proc.terminate()
        return ret, tail

    def compress(
        self,
        src: Path,
        out_dir: Optional[Path] = None,
        progress_cb: Optional[Callable[[int], None]] = None,
        status_cb: Optional[Callable[[str, int, float, float], None]] = None,
    ) -> Path:
        cfg = self.cfg
        # 所有压缩产物统一存放在源视频同级的 YS 文件夹中；不存在时自动创建。
        out_dir = out_dir or src.parent / OUTPUT_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)

        crop_filter = ""
        if cfg.auto_crop or any((cfg.extra_top, cfg.extra_bottom,
                                 cfg.extra_left, cfg.extra_right)):
            crop_filter = self.detect_crop(src)

        duration = probe_duration(src)
        target_duration = None
        if cfg.trim_end and cfg.trim_end_sec > 0:
            if duration > cfg.trim_end_sec:
                target_duration = duration - cfg.trim_end_sec
                self.log(
                    f">> [{src.name}] 尾部剪切启用: 提前 {cfg.trim_end_sec:.1f}s 结束 "
                    f"({format_time(duration)} -> {format_time(target_duration)})"
                )
            else:
                self.log(
                    f">> [{src.name}] 视频时长 ({format_time(duration)}) <= "
                    f"剪切秒数 ({cfg.trim_end_sec:.1f}s)，跳过尾部剪切"
                )

        encode_duration = target_duration if target_duration is not None else duration
        src_size_mb = src.stat().st_size / (1024 * 1024)
        dst = unique_path(out_dir / f"{cfg.output_prefix}{src.stem}.{cfg.output_format}")
        cmd = self.build_command(src, dst, crop_filter, target_duration=target_duration)
        self.log(f">> [{src.name}] 源: {src_size_mb:.1f}MB | {format_time(duration)}")
        self.log(f">> [{src.name}] 命令: {' '.join(cmd)}")
        self.log(f"压缩开始: {src.name} -> {dst.name}")

        t0 = time.monotonic()
        ret, tail = self._run_encode(cmd, src, encode_duration, progress_cb, status_cb)

        if (ret != 0 or not dst.exists()) and "-hwaccel" in cmd:
            self.log(f">> [{src.name}] 硬件解码失败(可能是源视频色彩空间不兼容)，回退软件解码重试")
            cmd = self.build_command(src, dst, crop_filter, use_hwaccel=False, target_duration=target_duration)
            self.log(f">> [{src.name}] 命令: {' '.join(cmd)}")
            ret, tail = self._run_encode(cmd, src, encode_duration, progress_cb, status_cb)

        if ret != 0 or not dst.exists():
            detail = "\n".join(tail)
            raise RuntimeError(f"压缩失败: {src.name} (返回码 {ret})\n{detail}")
        if progress_cb:
            progress_cb(100)
        if status_cb:
            status_cb(src.name, 100, encode_duration, encode_duration)
        elapsed = time.monotonic() - t0
        dst_size_mb = dst.stat().st_size / (1024 * 1024)
        ratio = dst_size_mb / src_size_mb * 100 if src_size_mb > 0 else 0
        self.log(
            f">> [{src.name}] 输出: {dst_size_mb:.1f}MB (源 {ratio:.0f}%) | "
            f"耗时 {elapsed:.0f}s"
        )
        self.log(f"压缩完成: {dst.name}")
        return dst
