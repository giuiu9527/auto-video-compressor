# -*- coding: utf-8 -*-
"""主窗口逻辑：支持文件夹选择、定时循环扫描、实列表界面展示与多线程自动压缩。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from updater import UpdateCheckWorker, UpdateProgressDialog, apply_zip_update_and_restart
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QStatusBar, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from compressor import (
    AUDIO_CODEC_OPTIONS, OUTPUT_FORMAT_OPTIONS, PRESET_OPTIONS,
    RATE_MODE_OPTIONS, VIDEO_CODEC_OPTIONS, VideoCompressor,
)
from config import (
    APP_DIR, APP_ICON_PATH, APP_VERSION, SETTINGS_FILE, CompressionConfig, WatchConfig,
)
from scanner import FileStatus, FolderWatcherWorker, ScannedFile
from utils import format_time, now_str


def open_dir_folder(path: Path) -> None:
    if not path.exists():
        return
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"视频自动循环监控压缩工具 v{APP_VERSION}")
        self.resize(1080, 620)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))

        self.watch_cfg = WatchConfig()
        self.comp_cfg = CompressionConfig()

        self.watcher_worker: Optional[FolderWatcherWorker] = None
        self.compressor_worker: Optional[VideoCompressorWorker] = None

        self._build_ui()
        self._build_statusbar()
        self.load_settings(silent=True)
        self.set_status("就绪", "ok")

    @staticmethod
    def double_spin(min_v, max_v, step, value, decimals=2) -> QDoubleSpinBox:
        b = QDoubleSpinBox(); b.setRange(min_v, max_v); b.setSingleStep(step)
        b.setDecimals(decimals); b.setValue(value); return b

    @staticmethod
    def add_form_row(grid: QGridLayout, row: int, col: int, label: str, widget: QWidget) -> None:
        lab = QLabel(label); lab.setObjectName("FieldLabel")
        grid.addWidget(lab, row, col); grid.addWidget(widget, row, col + 1)

    # ── 界面建构 ──────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget(); self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(6, 4, 6, 4); main.setSpacing(4)

        main.addWidget(self._build_header())
        main.addWidget(self._build_watch_card())

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4); splitter.setChildrenCollapsible(False)
        main.addWidget(splitter, 1)

        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_tabs_panel())
        splitter.setSizes([260, 180])

        main.addLayout(self._build_run_row())

    def _build_header(self) -> QWidget:
        frame = QFrame(); frame.setObjectName("HeaderFrame"); frame.setFixedHeight(36)
        lay = QHBoxLayout(frame); lay.setContentsMargins(12, 4, 12, 4); lay.setSpacing(8)
        title_box = QVBoxLayout(); title_box.setSpacing(1)
        title = QLabel("视频自动循环监控压缩工具"); title.setObjectName("HeaderTitle")
        subtitle = QLabel("文件夹递归巡检  ·  智能排重检测  ·  NVENC 硬件加速  ·  自动后台压缩")
        subtitle.setObjectName("HeaderSubtitle")
        title_box.addWidget(title); title_box.addWidget(subtitle)
        badge = QLabel(f"v{APP_VERSION}"); badge.setObjectName("HeaderBadge")
        badge.setAlignment(Qt.AlignCenter)
        self.btn_check_update = QPushButton("🚀 检查更新")
        self.btn_check_update.clicked.connect(lambda: self.check_updates(manual=True))
        lay.addLayout(title_box, 1)
        lay.addWidget(self.btn_check_update, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(badge, 0, Qt.AlignRight | Qt.AlignVCenter)
        return frame

    def _build_watch_card(self) -> QWidget:
        grp = QGroupBox("📁 监控目标与自动巡检策略")
        grid = QGridLayout(grp)
        grid.setHorizontalSpacing(8); grid.setVerticalSpacing(4)

        self.edit_watch_dir = QLineEdit()
        self.edit_watch_dir.setPlaceholderText("请选择或拖入需要监控视频的根目录路径...")
        self.btn_browse_dir = QPushButton("📁 选择文件夹")
        self.btn_scan_now = QPushButton("🔄 立即扫盘")
        self.btn_clear_table = QPushButton("🧹 清空列表")
        self.btn_open_dir = QPushButton("📂 打开目录")

        self.btn_browse_dir.clicked.connect(self._choose_watch_dir)
        self.btn_scan_now.clicked.connect(self._on_scan_now_clicked)
        self.btn_clear_table.clicked.connect(self._clear_table)
        self.btn_open_dir.clicked.connect(self._open_watch_dir)

        grid.addWidget(QLabel("监控根目录"), 0, 0)
        grid.addWidget(self.edit_watch_dir, 0, 1)
        grid.addWidget(self.btn_browse_dir, 0, 2)
        grid.addWidget(self.btn_scan_now, 0, 3)
        grid.addWidget(self.btn_clear_table, 0, 4)
        grid.addWidget(self.btn_open_dir, 0, 5)

        self.chk_enable_timer = QCheckBox("开启定时循环监听")
        self.chk_enable_timer.setChecked(True)
        self.spin_interval = QSpinBox(); self.spin_interval.setRange(5, 3600); self.spin_interval.setValue(30)
        self.spin_min_stable = QSpinBox(); self.spin_min_stable.setRange(5, 7200); self.spin_min_stable.setValue(180)
        self.spin_min_stable.setToolTip("文件修改时间距当前时间少于此秒数时，认定该视频仍处于录制或 Syncthing 同步中，暂不处理")
        self.chk_recursive = QCheckBox("递归扫描所有子文件夹")
        self.chk_recursive.setChecked(True)
        self.chk_auto_start = QCheckBox("扫到新视频自动提交压缩")
        self.chk_auto_start.setChecked(True)

        row2 = QHBoxLayout(); row2.setSpacing(12)
        row2.addWidget(self.chk_enable_timer)
        row2.addWidget(QLabel("检查间隔(秒):"))
        row2.addWidget(self.spin_interval)
        row2.addSpacing(6)
        row2.addWidget(QLabel("录制/同步防卡冷却(秒):"))
        row2.addWidget(self.spin_min_stable)
        row2.addSpacing(6)
        row2.addWidget(self.chk_recursive)
        row2.addWidget(self.chk_auto_start)
        row2.addStretch(1)

        grid.addLayout(row2, 1, 0, 1, 6)
        return grp

    def _build_table_panel(self) -> QWidget:
        wrap = QWidget(); v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "文件名", "相对路径", "大小 (MB)", "时长", "当前状态", "压缩进度"
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 220)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        v.addWidget(self.table)
        return wrap

    def _build_tabs_panel(self) -> QWidget:
        wrap = QWidget(); v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(); self.tabs.setDocumentMode(True)
        v.addWidget(self.tabs, 1)

        self._build_compress_tab()
        self._build_log_tab()
        return wrap

    def _build_compress_tab(self) -> None:
        tab = QWidget(); layout = QHBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6); layout.setSpacing(6)

        # 左侧: 编码与码率
        grp_codec = QGroupBox("编码与码率")
        g1 = QGridLayout(grp_codec)
        g1.setHorizontalSpacing(8); g1.setVerticalSpacing(4)
        self.combo_v_codec = QComboBox()
        for k, val in VIDEO_CODEC_OPTIONS.items(): self.combo_v_codec.addItem(val, userData=k)
        self.combo_a_codec = QComboBox()
        for k, val in AUDIO_CODEC_OPTIONS.items(): self.combo_a_codec.addItem(val, userData=k)
        self.combo_format = QComboBox(); self.combo_format.addItems(OUTPUT_FORMAT_OPTIONS)
        self.combo_rate_mode = QComboBox()
        for k, val in RATE_MODE_OPTIONS.items(): self.combo_rate_mode.addItem(val, userData=k)
        self.combo_preset = QComboBox(); self.combo_preset.addItems(PRESET_OPTIONS)
        self.spin_cq = QSpinBox(); self.spin_cq.setRange(0, 51); self.spin_cq.setValue(23)
        self.spin_bitrate = QSpinBox(); self.spin_bitrate.setRange(100, 50000); self.spin_bitrate.setValue(2500)
        self.spin_a_bitrate = QSpinBox(); self.spin_a_bitrate.setRange(32, 320); self.spin_a_bitrate.setValue(128)

        self.add_form_row(g1, 0, 0, "格式", self.combo_format)
        self.add_form_row(g1, 0, 2, "视频编码", self.combo_v_codec)
        self.add_form_row(g1, 1, 0, "码率模式", self.combo_rate_mode)
        self.add_form_row(g1, 1, 2, "预设", self.combo_preset)
        self.add_form_row(g1, 2, 0, "CQ/CRF", self.spin_cq)
        self.add_form_row(g1, 2, 2, "比特率(k)", self.spin_bitrate)
        self.add_form_row(g1, 3, 0, "音频编码", self.combo_a_codec)
        self.add_form_row(g1, 3, 2, "音频码率(k)", self.spin_a_bitrate)
        layout.addWidget(grp_codec, 1)

        # 中间: 去黑边与尾部剪切
        grp_cut = QGroupBox("✂ 去黑边 / 尾部剪切")
        g2 = QGridLayout(grp_cut)
        g2.setHorizontalSpacing(8); g2.setVerticalSpacing(4)
        self.chk_auto_crop = QCheckBox("自动去黑边 (cropdetect 采样)")
        self.chk_auto_crop.setStyleSheet("QCheckBox{font-weight:600; color:#1f6fb2;}")
        g2.addWidget(self.chk_auto_crop, 0, 0, 1, 4)

        self.spin_crop_top = QSpinBox(); self.spin_crop_top.setRange(0, 4000)
        self.spin_crop_bottom = QSpinBox(); self.spin_crop_bottom.setRange(0, 4000)
        self.spin_crop_left = QSpinBox(); self.spin_crop_left.setRange(0, 4000)
        self.spin_crop_right = QSpinBox(); self.spin_crop_right.setRange(0, 4000)
        self.add_form_row(g2, 1, 0, "额外 上(px)", self.spin_crop_top)
        self.add_form_row(g2, 1, 2, "额外 下(px)", self.spin_crop_bottom)
        self.add_form_row(g2, 2, 0, "额外 左(px)", self.spin_crop_left)
        self.add_form_row(g2, 2, 2, "额外 右(px)", self.spin_crop_right)

        self.chk_trim_end = QCheckBox("启用尾部剪切 (提前指定秒数结束)")
        self.chk_trim_end.setStyleSheet("QCheckBox{font-weight:600; color:#1f6fb2;}")
        self.spin_trim_end_sec = self.double_spin(0.1, 3600.0, 0.5, 7.0, 1)
        self.spin_trim_end_sec.setEnabled(False)
        self.chk_trim_end.toggled.connect(self.spin_trim_end_sec.setEnabled)
        g2.addWidget(self.chk_trim_end, 3, 0, 1, 2)
        self.add_form_row(g2, 3, 2, "提前结束(s)", self.spin_trim_end_sec)

        layout.addWidget(grp_cut, 1)

        # 右侧: 前缀与并发
        grp_out = QGroupBox("前缀与并发")
        g3 = QGridLayout(grp_out)
        g3.setHorizontalSpacing(8); g3.setVerticalSpacing(4)
        self.edit_prefix = QLineEdit("(ys)")
        self.spin_workers = QSpinBox(); self.spin_workers.setRange(1, 8); self.spin_workers.setValue(1)
        self.add_form_row(g3, 0, 0, "输出前缀", self.edit_prefix)
        self.add_form_row(g3, 0, 2, "并发数", self.spin_workers)

        hint = QLabel("压缩后的视频统一保存到源视频所在目录的 YS 文件夹（不存在将自动创建），并自动附加输出前缀。")
        hint.setObjectName("HintLabel"); hint.setWordWrap(True)
        g3.addWidget(hint, 1, 0, 2, 4)
        layout.addWidget(grp_out, 1)

        self.tabs.addTab(tab, "视频压缩参数")

    def _build_log_tab(self) -> None:
        tab = QWidget(); layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6); layout.setSpacing(4)
        self.log_box = QPlainTextEdit(); self.log_box.setObjectName("LogBox")
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)

        btns = QHBoxLayout()
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.log_box.clear)
        btns.addStretch(1); btns.addWidget(self.btn_clear_log)
        layout.addLayout(btns)

        self.tabs.addTab(tab, "运行日志")

    def _build_run_row(self) -> QHBoxLayout:
        box = QHBoxLayout(); box.setSpacing(8)

        self.lbl_summary = QLabel("监控项目: 0 个 | 等待: 0 | 完成: 0 | 跳过: 0")
        self.lbl_summary.setStyleSheet("color:#1f6fb2; font-weight:600;")
        box.addWidget(self.lbl_summary, 1)

        self.btn_save_cfg = QPushButton("💾 保存配置")
        self.btn_start = QPushButton("▶ 开始自动监控与压缩")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_stop.setEnabled(False)

        self.btn_save_cfg.clicked.connect(lambda: self.save_settings(silent=False))
        self.btn_start.clicked.connect(self.start_watching)
        self.btn_stop.clicked.connect(self.stop_watching)

        box.addWidget(self.btn_save_cfg)
        box.addWidget(self.btn_start)
        box.addWidget(self.btn_stop)
        return box

    def _build_statusbar(self) -> None:
        bar = QStatusBar(); self.setStatusBar(bar)
        self.status_dot = QLabel("●"); self.status_dot.setObjectName("StatusDot")
        self.status_text = QLabel("就绪")
        self.status_path = QLabel(f"配置: {SETTINGS_FILE}")
        self.status_path.setStyleSheet("color:#95a5a6;")
        bar.addWidget(self.status_dot); bar.addWidget(self.status_text, 1)
        bar.addPermanentWidget(self.status_path)

    def set_status(self, text: str, level: str = "ok") -> None:
        self.status_text.setText(text)
        if level == "ok":     self.status_dot.setObjectName("StatusDot")
        elif level == "busy": self.status_dot.setObjectName("StatusDotBusy")
        else:                self.status_dot.setObjectName("StatusDotError")
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    def log(self, text: str) -> None:
        if not text.startswith("["):
            text = f"[{now_str()}] {text}"
        self.log_box.appendPlainText(text)
        self.log_box.moveCursor(QTextCursor.End)

    # ── 交互动作 ────────────────────────────────────────
    def _choose_watch_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择要监控的文件夹", self.edit_watch_dir.text() or str(APP_DIR))
        if d:
            self.edit_watch_dir.setText(d)
            self._on_scan_now_clicked()

    def _open_watch_dir(self) -> None:
        path_str = self.edit_watch_dir.text().strip()
        if path_str:
            open_dir_folder(Path(path_str))

    def _on_scan_now_clicked(self) -> None:
        w_cfg = self.collect_watch_config()
        if not w_cfg.watch_dir or not Path(w_cfg.watch_dir).exists():
            QMessageBox.warning(self, "路径错误", "请先选择或输入有效的监控根目录。")
            return
        self.log(f"手动发起扫盘目录: {w_cfg.watch_dir}")
        worker = self.watcher_worker or FolderWatcherWorker(w_cfg)
        scanned = worker.scan_once()
        self._on_scan_completed(scanned)

    def _clear_table(self) -> None:
        self.table.setRowCount(0)
        if self.watcher_worker:
            self.watcher_worker.known_status_map.clear()
        self.lbl_summary.setText("监控总项目: 0 个 | 等待处理: 0 | 完成: 0 | 跳过: 0")
        self.log("已清空视频列表与历史扫描状态。")

    def collect_watch_config(self) -> WatchConfig:
        return WatchConfig(
            watch_dir=self.edit_watch_dir.text().strip(),
            recursive=self.chk_recursive.isChecked(),
            enable_timer=self.chk_enable_timer.isChecked(),
            interval_sec=self.spin_interval.value(),
            skip_prefix=self.edit_prefix.text().strip() or "(ys)",
            auto_start_compress=self.chk_auto_start.isChecked(),
            min_stable_sec=self.spin_min_stable.value(),
        )

    def collect_compression_config(self) -> CompressionConfig:
        return CompressionConfig(
            video_codec=self.combo_v_codec.currentData() or "h264_nvenc",
            audio_codec=self.combo_a_codec.currentData() or "aac",
            output_format=self.combo_format.currentText() or "mp4",
            rate_mode=self.combo_rate_mode.currentData() or "cq",
            cq_value=self.spin_cq.value(),
            bitrate_kbps=self.spin_bitrate.value(),
            audio_bitrate_kbps=self.spin_a_bitrate.value(),
            preset=self.combo_preset.currentText() or "p4",
            auto_crop=self.chk_auto_crop.isChecked(),
            extra_top=self.spin_crop_top.value(),
            extra_bottom=self.spin_crop_bottom.value(),
            extra_left=self.spin_crop_left.value(),
            extra_right=self.spin_crop_right.value(),
            output_prefix=self.edit_prefix.text().strip() or "(ys)",
            max_workers=self.spin_workers.value(),
            trim_end=self.chk_trim_end.isChecked(),
            trim_end_sec=self.spin_trim_end_sec.value(),
        )

    def apply_config(self, w: WatchConfig, c: CompressionConfig) -> None:
        self.edit_watch_dir.setText(w.watch_dir)
        self.chk_recursive.setChecked(w.recursive)
        self.chk_enable_timer.setChecked(w.enable_timer)
        self.spin_interval.setValue(w.interval_sec)
        self.spin_min_stable.setValue(getattr(w, "min_stable_sec", 180))
        self.chk_auto_start.setChecked(w.auto_start_compress)

        idx = self.combo_v_codec.findData(c.video_codec)
        self.combo_v_codec.setCurrentIndex(max(0, idx))
        idx = self.combo_a_codec.findData(c.audio_codec)
        self.combo_a_codec.setCurrentIndex(max(0, idx))
        idx = self.combo_format.findText(c.output_format)
        self.combo_format.setCurrentIndex(max(0, idx))
        idx = self.combo_rate_mode.findData(c.rate_mode)
        self.combo_rate_mode.setCurrentIndex(max(0, idx))
        idx = self.combo_preset.findText(c.preset)
        self.combo_preset.setCurrentIndex(max(0, idx))

        self.spin_cq.setValue(c.cq_value)
        self.spin_bitrate.setValue(c.bitrate_kbps)
        self.spin_a_bitrate.setValue(c.audio_bitrate_kbps)
        self.chk_auto_crop.setChecked(c.auto_crop)
        self.spin_crop_top.setValue(c.extra_top)
        self.spin_crop_bottom.setValue(c.extra_bottom)
        self.spin_crop_left.setValue(c.extra_left)
        self.spin_crop_right.setValue(c.extra_right)
        self.edit_prefix.setText(c.output_prefix)
        self.spin_workers.setValue(c.max_workers)
        self.chk_trim_end.setChecked(c.trim_end)
        self.spin_trim_end_sec.setValue(c.trim_end_sec)
        self.spin_trim_end_sec.setEnabled(c.trim_end)

    def save_settings(self, silent: bool = False) -> None:
        try:
            data = {
                "watch": asdict(self.collect_watch_config()),
                "compression": asdict(self.collect_compression_config()),
            }
            SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8-sig")
            if not silent:
                QMessageBox.information(self, "已保存", f"配置已成功保存:\n{SETTINGS_FILE}")
            self.log(f"配置已保存: {SETTINGS_FILE}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def load_settings(self, silent: bool = False) -> None:
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
            w_defaults = asdict(WatchConfig())
            w_defaults.update({k: v for k, v in data.get("watch", {}).items() if k in w_defaults})
            c_defaults = asdict(CompressionConfig())
            c_defaults.update({k: v for k, v in data.get("compression", {}).items() if k in c_defaults})
            self.apply_config(WatchConfig(**w_defaults), CompressionConfig(**c_defaults))
            self.log(f"已自动加载配置文件: {SETTINGS_FILE}")
            if self.edit_watch_dir.text().strip() and Path(self.edit_watch_dir.text().strip()).exists():
                self._on_scan_now_clicked()
            # 自动后台检查更新
            self.check_updates(manual=False)
        except Exception as exc:
            self.log(f"配置文件加载异常: {exc}")

    def check_updates(self, manual: bool = False) -> None:
        self.manual_check = manual
        self.update_worker = UpdateCheckWorker(APP_VERSION)
        self.update_worker.check_finished_signal.connect(self._on_update_check_finished)
        self.update_worker.start()

    def _on_update_check_finished(self, has_update: bool, new_ver: str, notes: str, url: str) -> None:
        if has_update:
            msg = f"发现新版本 [{new_ver}]！\n\n当前版本: v{APP_VERSION}\n最新版本: {new_ver}\n\n更新日志:\n{notes}\n\n是否立即下载升级？"
            res = QMessageBox.question(self, "版本更新提示", msg, QMessageBox.Yes | QMessageBox.No)
            if res == QMessageBox.Yes and url:
                self.log(f"正在准备在线升级: {url}")
                dlg = UpdateProgressDialog(url, new_ver, parent=self)
                if dlg.exec() and dlg.success:
                    try:
                        apply_zip_update_and_restart(dlg.downloaded_zip_path, self.log)
                    except Exception as exc:
                        QMessageBox.critical(self, "更新失败", f"应用更新失败: {exc}")
                elif not dlg.success:
                    self.log("更新已取消或下载失败。")
        elif getattr(self, "manual_check", False):
            QMessageBox.information(self, "更新检查", f"当前已是最新版本 (v{APP_VERSION})！")

    # ── 扫描与表格渲染 ────────────────────────────────────
    def _on_scan_completed(self, items: list[ScannedFile]) -> None:
        self.table.setRowCount(len(items))
        waiting_count = 0
        completed_count = 0
        skipped_count = 0

        for r, item in enumerate(items):
            it_name = self.table.item(r, 0) or QTableWidgetItem()
            it_name.setText(item.file_path.name)

            it_rel = self.table.item(r, 1) or QTableWidgetItem()
            it_rel.setText(item.rel_path)

            it_size = self.table.item(r, 2) or QTableWidgetItem()
            it_size.setText(f"{item.size_mb:.1f}")

            it_dur = self.table.item(r, 3) or QTableWidgetItem()
            it_dur.setText(format_time(item.duration) if item.duration > 0 else "—")

            it_status = self.table.item(r, 4) or QTableWidgetItem()
            it_status.setText(item.status)

            # 颜色设置
            if item.status == FileStatus.COMPLETED:
                it_status.setForeground(QColor("#2ecc71"))
                completed_count += 1
            elif item.status == FileStatus.PROCESSING:
                it_status.setForeground(QColor("#1f6fb2"))
            elif item.status.startswith("⏩"):
                it_status.setForeground(QColor("#95a5a6"))
                skipped_count += 1
            elif item.status.startswith("⏳"):
                it_status.setForeground(QColor("#e67e22"))
                skipped_count += 1
            elif item.status == FileStatus.WAITING:
                it_status.setForeground(QColor("#2980b9"))
                waiting_count += 1
            elif item.status == FileStatus.FAILED:
                it_status.setForeground(QColor("#e74c3c"))

            self.table.setItem(r, 0, it_name)
            self.table.setItem(r, 1, it_rel)
            self.table.setItem(r, 2, it_size)
            self.table.setItem(r, 3, it_dur)
            self.table.setItem(r, 4, it_status)

            pbar = self.table.cellWidget(r, 5)
            if not isinstance(pbar, QProgressBar):
                pbar = QProgressBar()
                self.table.setCellWidget(r, 5, pbar)
            pbar.setValue(item.progress)

        self.lbl_summary.setText(
            f"监控总项目: {len(items)} 个 | 等待处理: {waiting_count} | "
            f"已完成: {completed_count} | 已跳过: {skipped_count}"
        )

        # 如果开启了自动提交压缩
        w_cfg = self.collect_watch_config()
        if w_cfg.auto_start_compress and self.compressor_worker and waiting_count > 0:
            for item in items:
                if item.status == FileStatus.WAITING:
                    self.compressor_worker.enqueue(item.file_path)

    def _show_table_context_menu(self, pos) -> None:
        selected_rows = set(item.row() for item in self.table.selectedItems())
        if not selected_rows:
            return

        menu = QMenu(self)
        act_force = menu.addAction("⚡ 强制立即提交压缩 (跳过冷却等待)")
        act_open_dir = menu.addAction("📂 打开所在文件夹")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_force:
            self._force_compress_selected_rows(selected_rows)
        elif action == act_open_dir:
            self._open_selected_rows_folder(selected_rows)

    def _force_compress_selected_rows(self, selected_rows: set[int]) -> None:
        watch_dir = Path(self.edit_watch_dir.text().strip())
        if not watch_dir.exists():
            return

        for r in selected_rows:
            rel_item = self.table.item(r, 1)
            if not rel_item:
                continue
            file_path = watch_dir / rel_item.text()
            if not file_path.exists():
                name_item = self.table.item(r, 0)
                if name_item:
                    file_path = watch_dir / name_item.text()

            if file_path.exists():
                self.log(f"⚡ 手动强制解除冷却等待，提交压缩: {file_path.name}")
                if self.watcher_worker:
                    self.watcher_worker.force_process(file_path)
                if self.compressor_worker:
                    self.compressor_worker.force_enqueue(file_path)

        if self.watcher_worker:
            scanned = self.watcher_worker.scan_once()
            self._on_scan_completed(scanned)

    def _open_selected_rows_folder(self, selected_rows: set[int]) -> None:
        watch_dir = Path(self.edit_watch_dir.text().strip())
        for r in selected_rows:
            rel_item = self.table.item(r, 1)
            if rel_item:
                p = watch_dir / rel_item.text()
                if p.exists():
                    open_dir_folder(p.parent)
                    break

    # ── 启动 / 停止 监控 ──────────────────────────────────
    def start_watching(self) -> None:
        w_cfg = self.collect_watch_config()
        if not w_cfg.watch_dir or not Path(w_cfg.watch_dir).exists():
            QMessageBox.warning(self, "路径错误", "请先选择有效的监控根目录！")
            return

        self.c_cfg = self.collect_compression_config()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.set_status("监控运行中", "busy")

        # 启动压缩执行器
        self.compressor_worker = VideoCompressorWorker(self.c_cfg)
        self.compressor_worker.file_progress_signal.connect(self._on_file_progress_update)
        self.compressor_worker.log_signal.connect(self.log)
        self.compressor_worker.start()

        # 启动文件夹扫描器
        self.watcher_worker = FolderWatcherWorker(w_cfg)
        self.watcher_worker.scan_completed_signal.connect(self._on_scan_completed)
        self.watcher_worker.log_signal.connect(self.log)
        self.watcher_worker.status_signal.connect(lambda msg: self.set_status(msg, "busy"))
        self.watcher_worker.start()

        self.log(f"▶ 自动监控与压缩引擎已全面启动，根目录: {w_cfg.watch_dir}")

    def stop_watching(self) -> None:
        if self.watcher_worker:
            self.watcher_worker.stop()
            self.watcher_worker.wait(2000)
            self.watcher_worker = None

        if self.compressor_worker:
            self.compressor_worker.stop()
            self.compressor_worker.wait(2000)
            self.compressor_worker = None

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.set_status("就绪 (监控已停止)", "ok")
        self.log("■ 监控与压缩引擎已停止。")

    def _on_file_progress_update(self, src_path: Path, status_str: str, progress: int) -> None:
        if self.watcher_worker:
            self.watcher_worker.update_file_status(src_path, status_str, progress)
            # 刷新表格展示
            scanned = self.watcher_worker.scan_once()
            self._on_scan_completed(scanned)


# ── 后台压缩队列处理线程 ──────────────────────────────────
class VideoCompressorWorker(QThread):
    file_progress_signal = Signal(object, str, int)
    log_signal = Signal(str)

    def __init__(self, c_cfg: CompressionConfig) -> None:
        super().__init__()
        self.c_cfg = c_cfg
        self.queue: list[Path] = []
        self.active_set: set[str] = set()
        self.ever_enqueued: set[str] = set()  # 记录所有曾入队的文件，防止重复压缩
        self._stop_requested = False

    def enqueue(self, video_path: Path) -> None:
        """入队压缩（自动去重：同一文件在本轮监控中只会被压缩一次）。"""
        path_str = str(video_path)
        if path_str not in self.ever_enqueued:
            self.ever_enqueued.add(path_str)
            self.queue.append(video_path)

    def force_enqueue(self, video_path: Path) -> None:
        """强制入队（忽略去重历史，用于右键手动强制压缩）。"""
        path_str = str(video_path)
        if path_str not in self.active_set:
            self.ever_enqueued.add(path_str)
            self.queue.append(video_path)

    def stop(self) -> None:
        self._stop_requested = True

    def emit_log(self, msg: str) -> None:
        self.log_signal.emit(msg)

    def _compress_one(self, compressor: VideoCompressor, target: Path) -> None:
        path_str = str(target)
        # active_set.add 已在 run() 中 pool.submit 之前完成，此处无需重复添加
        self.file_progress_signal.emit(target, FileStatus.PROCESSING, 0)
        try:
            def file_pct(pct: int):
                self.file_progress_signal.emit(target, FileStatus.PROCESSING, pct)

            compressor.compress(target, progress_cb=file_pct)
            self.file_progress_signal.emit(target, FileStatus.COMPLETED, 100)
        except Exception as exc:
            self.emit_log(f"压缩任务失败 [{target.name}]: {exc}")
            self.file_progress_signal.emit(target, FileStatus.FAILED, 0)
        finally:
            self.active_set.discard(path_str)

    def run(self) -> None:
        compressor = VideoCompressor(self.c_cfg, self.emit_log, lambda: self._stop_requested)
        workers = max(1, self.c_cfg.max_workers)
        import time
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            while not self._stop_requested:
                if not self.queue:
                    time.sleep(0.5)
                    continue

                target = self.queue.pop(0)
                if self._stop_requested:
                    break

                # 在提交到线程池之前就加入 active_set，
                # 消除 pop 与 _compress_one 之间的去重空窗期
                self.active_set.add(str(target))
                fut = pool.submit(self._compress_one, compressor, target)
                futures.append(fut)
