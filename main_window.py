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
from threading import Lock
from typing import Optional

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from server_updater import (
    ServerUpdateCheckWorker, UpdateServerSettingsDialog, apply_server_update_and_restart,
    load_update_config,
)
from updater import UpdateCheckWorker, UpdateProgressDialog, apply_zip_update_and_restart
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QStatusBar, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
    QListWidget, QListWidgetItem,
)

from compressor import VideoCompressor
from config import (
    APP_DIR, APP_ICON_PATH, APP_VERSION, OUTPUT_DIR_NAME, SETTINGS_FILE, VIDEO_EXTS,
    CompressionConfig, WatchConfig, is_under_dir, resolve_output_dir,
)
from scanner import FileStatus, FolderWatcherWorker, ScannedFile
from params_form import CompressionParamsForm
from templates import TemplateManagerDialog, TemplateStore, ask_template_name
from utils import format_time, now_str

# 手动压缩列表项状态（存于 Qt.UserRole + 1）
ST_WAITING, ST_QUEUED, ST_DONE, ST_FAILED, ST_SKIPPED = "waiting", "queued", "done", "failed", "skipped"
ROLE_STATE = Qt.UserRole + 1
ROLE_ROOT = Qt.UserRole + 2  # 手动条目的相对路径基准目录（自定义保存目录时保留其下的父文件夹结构）
FOLLOW_MAIN = "__main__"  # 手动压缩“跟随主界面参数”


class LocalPathDropMixin:
    """为控件提供稳定的本地文件/文件夹拖放支持。"""

    @staticmethod
    def _drop_paths(event) -> list[Path]:
        mime_data = event.mimeData()
        if not mime_data or not mime_data.hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in mime_data.urls()
                if url.isLocalFile() and url.toLocalFile()]

    def dragEnterEvent(self, event) -> None:
        if self._drop_paths(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._drop_paths(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._drop_paths(event)
        if paths:
            self.files_dropped.emit(paths)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()


class ManualDropList(LocalPathDropMixin, QListWidget):
    """接收资源管理器拖入的视频或文件夹。"""
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(True)


class ManualDropTab(LocalPathDropMixin, QWidget):
    """允许拖到手动压缩页的空白区域，而不局限于列表内部。"""
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)


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
        self.resize(1080, 700)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))

        self.watch_cfg = WatchConfig()
        self.comp_cfg = CompressionConfig()
        self.excluded_paths: set[str] = set()
        self.template_store = TemplateStore()

        self.watcher_worker: Optional[FolderWatcherWorker] = None
        self.scan_once_worker: Optional[FolderWatcherWorker] = None
        self.compressor_worker: Optional[VideoCompressorWorker] = None
        self.manual_compressor_worker: Optional[VideoCompressorWorker] = None
        self._post_show_tasks_started = False

        self._build_ui()
        self._build_statusbar()
        self._refresh_template_combos()
        self.load_settings(silent=True)
        self.set_status("就绪", "ok")
        # 窗口先完成显示，再启动网络检查和目录扫描，避免大目录阻塞启动界面。
        QTimer.singleShot(150, self._run_post_show_tasks)

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
        splitter.setSizes([230, 250])

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
        self.btn_check_update.setToolTip("更新方式 1：从 GitHub Release 检查更新")
        self.btn_check_update.clicked.connect(lambda: self.check_updates(manual=True))
        self.btn_server_update = QPushButton("🌐 服务器更新")
        self.btn_server_update.setToolTip("更新方式 2：从自建更新服务器检查更新")
        self.btn_server_update.clicked.connect(self.check_server_updates)
        self.btn_server_settings = QPushButton("⚙")
        self.btn_server_settings.setFixedWidth(30)
        self.btn_server_settings.setToolTip("更新服务器设置")
        self.btn_server_settings.clicked.connect(self._open_update_server_settings)
        lay.addLayout(title_box, 1)
        lay.addWidget(self.btn_check_update, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.btn_server_update, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.btn_server_settings, 0, Qt.AlignRight | Qt.AlignVCenter)
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
        self._build_manual_compress_tab()
        self._build_log_tab()
        return wrap

    def _build_compress_tab(self) -> None:
        tab = QWidget(); layout = QHBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6); layout.setSpacing(6)

        self.params_form = CompressionParamsForm()
        layout.addWidget(self.params_form, 2)

        # 右侧: 前缀与并发
        grp_out = QGroupBox("前缀与并发")
        g3 = QGridLayout(grp_out)
        g3.setHorizontalSpacing(8); g3.setVerticalSpacing(4)
        self.edit_prefix = QLineEdit("(ys)")
        self.spin_workers = QSpinBox(); self.spin_workers.setRange(1, 8); self.spin_workers.setValue(1)
        self.add_form_row(g3, 0, 0, "输出前缀", self.edit_prefix)
        self.add_form_row(g3, 0, 2, "并发数", self.spin_workers)

        self.edit_output_root = QLineEdit()
        self.edit_output_root.setPlaceholderText("留空 = 源视频同级的 YS 文件夹")
        self.edit_output_root.setToolTip(
            "选择文件夹后，产物会保留源视频的父文件夹结构，例如 01/1.mkv -> 所选文件夹/01/1.mp4")
        self.btn_browse_output = QPushButton("📁 选择")
        self.btn_reset_output = QPushButton("↺ 原目录")
        self.btn_reset_output.setToolTip("恢复为保存到源视频同级的 YS 文件夹")
        self.btn_browse_output.clicked.connect(self._choose_output_root)
        self.btn_reset_output.clicked.connect(self.edit_output_root.clear)
        out_row = QHBoxLayout()
        out_row.addWidget(self.edit_output_root, 1)
        out_row.addWidget(self.btn_browse_output); out_row.addWidget(self.btn_reset_output)
        g3.addWidget(QLabel("保存目录"), 2, 0)
        g3.addLayout(out_row, 2, 1, 1, 3)

        self.chk_force_compress = QCheckBox("强制压缩（忽略已有压缩产物）")
        self.chk_force_compress.setToolTip("开启后，即使输出目录中已有同名 MP4，也会再次压缩并生成带数字后缀的新文件")
        self.chk_force_compress.setStyleSheet("QCheckBox{font-weight:600; color:#c0392b;}")
        self.chk_force_compress.toggled.connect(self._on_force_compress_toggled)
        g3.addWidget(self.chk_force_compress, 3, 0, 1, 4)

        self.combo_template = QComboBox()
        self.combo_template.setToolTip("选择模板后立即套用到左侧参数")
        self.combo_template.activated.connect(self._on_template_activated)
        self.btn_save_template = QPushButton("💾 存为模板")
        self.btn_manage_templates = QPushButton("⚙ 管理模板")
        self.btn_save_template.clicked.connect(self._save_current_as_template)
        self.btn_manage_templates.clicked.connect(self._open_template_manager)
        self.add_form_row(g3, 1, 0, "参数模板", self.combo_template)
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(self.btn_save_template); tpl_row.addWidget(self.btn_manage_templates)
        g3.addLayout(tpl_row, 1, 2, 1, 2)

        hint = QLabel("保存目录留空时产物保存到源视频同级的 YS 文件夹；选择文件夹后保留父文件夹结构"
                      "（如 01/1.mkv -> 所选文件夹/01/1.mp4）。排重只比较源文件名与 MP4 文件名，忽略输出前缀。")
        hint.setObjectName("HintLabel"); hint.setWordWrap(True)
        g3.addWidget(hint, 4, 0, 1, 4)
        layout.addWidget(grp_out, 1)

        self.tabs.addTab(tab, "视频压缩参数")

    def _build_manual_compress_tab(self) -> None:
        tab = ManualDropTab(); layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(6)
        tab.files_dropped.connect(self._add_manual_paths)

        hint = QLabel("将视频文件或文件夹拖到下方列表；文件夹会递归查找视频。输出位置跟随“视频压缩参数”页的保存目录（自定义目录时保留拖入文件夹/文件所在文件夹的结构）。")
        hint.setObjectName("HintLabel"); hint.setWordWrap(True)
        layout.addWidget(hint)

        opt = QHBoxLayout()
        opt.addWidget(QLabel("压缩模板:"))
        self.combo_manual_template = QComboBox()
        self.combo_manual_template.setMinimumWidth(240)
        self.combo_manual_template.setToolTip("手动压缩使用的参数模板；新提交的任务按提交时的选择为准")
        opt.addWidget(self.combo_manual_template)
        self.chk_manual_auto_submit = QCheckBox("压缩进行中，新拖入/添加的视频自动加入队列")
        self.chk_manual_auto_submit.setChecked(True)
        opt.addSpacing(12); opt.addWidget(self.chk_manual_auto_submit); opt.addStretch(1)
        layout.addLayout(opt)

        self.manual_list = ManualDropList()
        self.manual_list.setAlternatingRowColors(True)
        self.manual_list.setToolTip("拖入视频文件或包含视频的文件夹")
        self.manual_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.manual_list.files_dropped.connect(self._add_manual_paths)
        self.manual_list.customContextMenuRequested.connect(self._show_manual_context_menu)
        layout.addWidget(self.manual_list, 1)

        row = QHBoxLayout()
        self.btn_manual_add_files = QPushButton("➕ 添加视频")
        self.btn_manual_add_folder = QPushButton("📁 添加文件夹")
        self.btn_manual_remove = QPushButton("移除选中")
        self.btn_manual_clear = QPushButton("清空")
        self.btn_manual_start = QPushButton("▶ 开始 / 提交待处理（不扫描）")
        self.btn_manual_start.setObjectName("PrimaryButton")
        self.btn_manual_stop = QPushButton("■ 停止手动压缩")
        self.btn_manual_stop.setObjectName("DangerButton")
        self.btn_manual_stop.setEnabled(False)
        self.btn_manual_add_files.clicked.connect(self._choose_manual_files)
        self.btn_manual_add_folder.clicked.connect(self._choose_manual_folder)
        self.btn_manual_remove.clicked.connect(self._remove_manual_selected)
        self.btn_manual_clear.clicked.connect(self._clear_manual_list)
        self.btn_manual_start.clicked.connect(self._start_manual_compress)
        self.btn_manual_stop.clicked.connect(self._stop_manual_compress)
        row.addWidget(self.btn_manual_add_files); row.addWidget(self.btn_manual_add_folder)
        row.addWidget(self.btn_manual_remove); row.addWidget(self.btn_manual_clear)
        row.addStretch(1); row.addWidget(self.btn_manual_start); row.addWidget(self.btn_manual_stop)
        layout.addLayout(row)
        self.tabs.addTab(tab, "手动压缩")

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

    def _choose_output_root(self) -> None:
        start = self.edit_output_root.text().strip() or self.edit_watch_dir.text().strip() or str(APP_DIR)
        d = QFileDialog.getExistingDirectory(self, "选择压缩产物保存目录", start)
        if d:
            self.edit_output_root.setText(d)

    def _output_root(self) -> str:
        return self.edit_output_root.text().strip()

    def _watch_output_dir(self, path: Path) -> Path:
        """自动监控任务的输出目录：以运行中扫描器的配置为准，保证与排重判定一致。"""
        cfg = self.watcher_worker.watch_cfg if self.watcher_worker else self.collect_watch_config()
        return resolve_output_dir(path, cfg.output_root, Path(cfg.watch_dir))

    def _open_watch_dir(self) -> None:
        path_str = self.edit_watch_dir.text().strip()
        if path_str:
            open_dir_folder(Path(path_str))

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path.absolute()).casefold()

    def _is_output_path(self, path: Path) -> bool:
        return (any(part.casefold() == OUTPUT_DIR_NAME.casefold() for part in path.parts[:-1])
                or is_under_dir(path, self._output_root()))

    def _manual_output_dir(self, item: QListWidgetItem) -> Path:
        path = Path(item.data(Qt.UserRole))
        root = item.data(ROLE_ROOT)
        return resolve_output_dir(path, self._output_root(), Path(root) if root else None)

    def _has_manual_compressed_output(self, item: QListWidgetItem) -> bool:
        """手动任务沿用自动监控的产物检测，防止重复压缩。"""
        if self.chk_force_compress.isChecked():
            return False
        return FolderWatcherWorker._has_compressed_output(
            Path(item.data(Qt.UserRole)), self._manual_output_dir(item))

    def _on_force_compress_toggled(self, enabled: bool) -> None:
        """让运行中的扫描器立即采用强制压缩设置。"""
        if self.watcher_worker:
            self.watcher_worker.set_force_compress(enabled)
        mode = "开启" if enabled else "关闭"
        if hasattr(self, "log_box"):
            self.log(f"强制压缩已{mode}。")

    def _set_manual_item(self, item: QListWidgetItem, state: str, text: str) -> None:
        path = item.data(Qt.UserRole)
        item.setData(ROLE_STATE, state)
        item.setText(f"{text}  |  {path}")

    def _clear_manual_list(self) -> None:
        """清空列表；已提交但未启动的任务一并取消。"""
        if self.manual_compressor_worker:
            for i in range(self.manual_list.count()):
                self.manual_compressor_worker.cancel_queued(Path(self.manual_list.item(i).data(Qt.UserRole)))
        self.manual_list.clear()

    def _add_manual_paths(self, paths: list[Path]) -> None:
        """将手动选择/拖入的文件或文件夹展开为待压缩视频，自动去重。
        若手动压缩正在运行且勾选了自动加入，新增视频会直接入队。"""
        existing = {
            self._path_key(Path(self.manual_list.item(i).data(Qt.UserRole)))
            for i in range(self.manual_list.count())
        }
        added: list[QListWidgetItem] = []
        already_compressed = 0
        for path in paths:
            try:
                # 自定义保存目录时保留的结构基准：拖入文件夹保留该文件夹名，
                # 拖入单个文件保留其所在文件夹名（01/1.mkv -> 保存目录/01/1.mp4）。
                is_dir = path.is_dir()
                root = path.parent if is_dir else path.parent.parent
                candidates = path.rglob("*") if is_dir else [path]
                for candidate in candidates:
                    if (not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTS
                            or self._is_output_path(candidate)):
                        continue
                    key = self._path_key(candidate)
                    if key in existing:
                        continue
                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, str(candidate))
                    item.setData(ROLE_ROOT, str(root))
                    if self._has_manual_compressed_output(item):
                        self._set_manual_item(item, ST_SKIPPED, "⏩ 已跳过 (已有压缩产物)")
                        already_compressed += 1
                    else:
                        self._set_manual_item(item, ST_WAITING, "等待处理")
                    self.manual_list.addItem(item)
                    existing.add(key)
                    added.append(item)
            except OSError as exc:
                self.log(f"读取手动添加路径失败 [{path}]: {exc}")
        if added:
            self.log(f"手动压缩列表已添加 {len(added)} 个视频。")
        if already_compressed:
            self.log(f"其中 {already_compressed} 个视频已有压缩产物，已标记为跳过。")
        if not added and paths:
            self.log("未添加视频：文件可能不受支持、已在列表中，或位于输出目录。")
        if added and self.manual_compressor_worker and self.chk_manual_auto_submit.isChecked():
            self._submit_manual_items(added)

    def _choose_manual_files(self) -> None:
        filters = "视频文件 (" + " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTS)) + ")"
        files, _ = QFileDialog.getOpenFileNames(self, "选择要压缩的视频", str(APP_DIR), filters)
        self._add_manual_paths([Path(p) for p in files])

    def _choose_manual_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择包含视频的文件夹", str(APP_DIR))
        if folder:
            self._add_manual_paths([Path(folder)])

    def _remove_manual_selected(self) -> None:
        for item in self.manual_list.selectedItems():
            path = Path(item.data(Qt.UserRole))
            # 已提交但尚未启动的任务也一并取消，避免“删除后仍压缩”。
            if self.manual_compressor_worker:
                self.manual_compressor_worker.cancel_queued(path)
            self.manual_list.takeItem(self.manual_list.row(item))

    def _show_manual_context_menu(self, pos) -> None:
        if not self.manual_list.selectedItems():
            return
        menu = QMenu(self)
        act_delete = menu.addAction("🗑 删除选中项")
        if menu.exec(self.manual_list.viewport().mapToGlobal(pos)) == act_delete:
            self._remove_manual_selected()

    def _start_compressor_worker(self) -> None:
        """启动自动监控专用的压缩队列。"""
        if self.compressor_worker:
            return
        self.c_cfg = self.collect_compression_config()
        self.compressor_worker = VideoCompressorWorker(self.c_cfg)
        self.compressor_worker.file_progress_signal.connect(self._on_file_progress_update)
        self.compressor_worker.log_signal.connect(self.log)
        self.compressor_worker.start()

    def _manual_config(self) -> CompressionConfig:
        """手动压缩当前选择的参数：模板覆盖主界面参数（前缀/并发数沿用主界面）。"""
        base = self.collect_compression_config()
        name = self.combo_manual_template.currentData()
        if name and name != FOLLOW_MAIN:
            return self.template_store.apply_to(name, base)
        return base

    def _submit_manual_items(self, items: list[QListWidgetItem]) -> int:
        """把等待中的条目提交给手动压缩线程（按当前模板），返回实际提交数。"""
        worker = self.manual_compressor_worker
        if not worker:
            return 0
        cfg = self._manual_config()
        submitted = skipped = 0
        for item in items:
            if item.data(ROLE_STATE) != ST_WAITING:
                continue
            path = Path(item.data(Qt.UserRole))
            if not path.exists() or path.suffix.lower() not in VIDEO_EXTS:
                continue
            if self._has_manual_compressed_output(item):
                self._set_manual_item(item, ST_SKIPPED, "⏩ 已跳过 (已有压缩产物)")
                skipped += 1
                continue
            worker.enqueue(path, cfg, out_dir=self._manual_output_dir(item))
            self._set_manual_item(item, ST_QUEUED, "排队中")
            submitted += 1
        if submitted:
            self.log(f"▶ 手动压缩已提交 {submitted} 个视频 (模板: {self.combo_manual_template.currentText()})。")
            self.btn_manual_stop.setEnabled(True)
            self.set_status("手动压缩运行中", "busy")
        elif skipped:
            self.log(f"手动压缩未提交任务：{skipped} 个视频已有压缩产物。")
        return submitted

    def _start_manual_compress(self) -> None:
        if not self.manual_list.count():
            QMessageBox.information(self, "手动压缩", "请先拖入或选择至少一个视频文件。")
            return
        items = [self.manual_list.item(i) for i in range(self.manual_list.count())]
        if not any(it.data(ROLE_STATE) == ST_WAITING for it in items):
            self.log("手动压缩：没有待处理的视频。")
            return
        if not self.manual_compressor_worker:
            worker = VideoCompressorWorker(self._manual_config())
            worker.file_progress_signal.connect(self._on_manual_file_progress_update)
            worker.queue_idle_signal.connect(self._on_manual_queue_idle)
            worker.log_signal.connect(self.log)
            self.manual_compressor_worker = worker
            worker.start()
        self._submit_manual_items(items)

    def _stop_manual_compress(self) -> None:
        if self.manual_compressor_worker:
            self.manual_compressor_worker.stop()
            self.manual_compressor_worker.wait(2000)
            self.manual_compressor_worker = None
        # 未完成的条目恢复为待处理，之后可重新提交
        for i in range(self.manual_list.count()):
            item = self.manual_list.item(i)
            if item.data(ROLE_STATE) == ST_QUEUED:
                self._set_manual_item(item, ST_WAITING, "等待处理")
        self.btn_manual_stop.setEnabled(False)
        if not self.watcher_worker:
            self.set_status("就绪 (手动压缩已停止)", "ok")
        self.log("■ 手动压缩已停止。")

    def _on_manual_file_progress_update(self, src_path: Path, status_str: str, progress: int) -> None:
        key = self._path_key(src_path)
        for i in range(self.manual_list.count()):
            item = self.manual_list.item(i)
            if self._path_key(Path(item.data(Qt.UserRole))) == key:
                if status_str == FileStatus.COMPLETED:
                    self._set_manual_item(item, ST_DONE, "✅ 已完成")
                elif status_str == FileStatus.FAILED:
                    self._set_manual_item(item, ST_FAILED, "❌ 压缩失败")
                else:
                    self._set_manual_item(item, ST_QUEUED, f"压缩中 {progress}%")
                break

    def _on_manual_queue_idle(self) -> None:
        """手动任务全部完成（成功或失败）后自动释放独立压缩线程。"""
        worker = self.manual_compressor_worker
        # 信号排队期间可能又有新视频入队，此时不能停止线程。
        if not worker or worker.is_busy():
            return
        self.log("✓ 手动压缩任务已全部完成，已自动停止。")
        self._stop_manual_compress()

    def _on_scan_now_clicked(self) -> None:
        w_cfg = self.collect_watch_config()
        if not w_cfg.watch_dir or not Path(w_cfg.watch_dir).exists():
            QMessageBox.warning(self, "路径错误", "请先选择或输入有效的监控根目录。")
            return
        self.log(f"手动发起扫盘目录: {w_cfg.watch_dir}")
        self._start_one_shot_scan(w_cfg)

    def _start_one_shot_scan(self, w_cfg: WatchConfig) -> None:
        """在独立线程执行一次扫描，避免目录遍历和 ffprobe 阻塞界面。"""
        if self.scan_once_worker and self.scan_once_worker.isRunning():
            self.log("扫盘任务正在运行，本次请求已忽略。")
            return

        w_cfg.enable_timer = False
        worker = FolderWatcherWorker(w_cfg)
        if self.watcher_worker:
            worker.known_status_map = dict(self.watcher_worker.known_status_map)
        worker.scan_completed_signal.connect(self._on_scan_completed)
        worker.log_signal.connect(self.log)
        worker.finished.connect(lambda: self._on_one_shot_scan_finished(worker))
        self.scan_once_worker = worker
        worker.start()

    def _on_one_shot_scan_finished(self, worker: FolderWatcherWorker) -> None:
        if self.scan_once_worker is worker:
            self.scan_once_worker = None
        worker.deleteLater()

    def _run_post_show_tasks(self) -> None:
        """窗口显示后再执行可延迟的启动任务。"""
        if self._post_show_tasks_started:
            return
        self._post_show_tasks_started = True
        self.check_updates(manual=False)

        w_cfg = self.collect_watch_config()
        if w_cfg.watch_dir and Path(w_cfg.watch_dir).exists():
            self.log(f"后台加载监控目录: {w_cfg.watch_dir}")
            self._start_one_shot_scan(w_cfg)

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
            auto_start_compress=self.chk_auto_start.isChecked(),
            force_compress=self.chk_force_compress.isChecked(),
            min_stable_sec=self.spin_min_stable.value(),
            output_root=self._output_root(),
            excluded_paths=sorted(self.excluded_paths),
        )

    def collect_compression_config(self) -> CompressionConfig:
        base = CompressionConfig(
            output_prefix=self.edit_prefix.text().strip(),
            max_workers=self.spin_workers.value(),
        )
        return self.params_form.get_config(base)

    def apply_config(self, w: WatchConfig, c: CompressionConfig) -> None:
        self.edit_watch_dir.setText(w.watch_dir)
        self.chk_recursive.setChecked(w.recursive)
        self.chk_enable_timer.setChecked(w.enable_timer)
        self.spin_interval.setValue(w.interval_sec)
        self.spin_min_stable.setValue(getattr(w, "min_stable_sec", 180))
        self.chk_auto_start.setChecked(w.auto_start_compress)
        self.chk_force_compress.setChecked(getattr(w, "force_compress", False))
        self.edit_output_root.setText(getattr(w, "output_root", ""))
        self.excluded_paths = {self._path_key(Path(p)) for p in getattr(w, "excluded_paths", [])}

        self.params_form.set_config(c)
        self.edit_prefix.setText(c.output_prefix)
        self.spin_workers.setValue(c.max_workers)

    # ── 压缩模板 ──────────────────────────────────────────
    def _refresh_template_combos(self) -> None:
        """重建主界面/手动压缩的模板下拉框，并尽量保留原选择。"""
        names = self.template_store.names()
        manual_prev = self.combo_manual_template.currentData()
        self.combo_template.clear()
        self.combo_template.addItem("— 选择模板套用 —", userData=None)
        self.combo_manual_template.clear()
        self.combo_manual_template.addItem("跟随主界面参数", userData=FOLLOW_MAIN)
        for n in names:
            self.combo_template.addItem(n, userData=n)
            self.combo_manual_template.addItem(n, userData=n)
        idx = self.combo_manual_template.findData(manual_prev)
        self.combo_manual_template.setCurrentIndex(max(0, idx))

    def _on_template_activated(self, _index: int) -> None:
        name = self.combo_template.currentData()
        if not name:
            return
        self.params_form.set_config(
            self.template_store.apply_to(name, self.collect_compression_config()))
        self.log(f"已套用压缩模板: {name}（点“保存配置”后成为默认参数；自动监控需重新启动生效）")

    def _save_current_as_template(self) -> None:
        name = ask_template_name(self, self.template_store, "存为模板")
        if not name:
            return
        self.template_store.put(name, self.collect_compression_config())
        self._refresh_template_combos()
        self.log(f"已保存压缩模板: {name}")

    def _open_template_manager(self) -> None:
        dlg = TemplateManagerDialog(self.template_store, self.collect_compression_config(), self)
        dlg.exec()
        if dlg.changed:
            self._refresh_template_combos()

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

    # ── 更新方式 2：自建更新服务器 ──────────────────────────
    def _open_update_server_settings(self) -> None:
        UpdateServerSettingsDialog(self).exec()

    def check_server_updates(self) -> None:
        server, channel = load_update_config()
        self.btn_server_update.setEnabled(False)
        self.log(f"检查服务器更新: {server}{channel}")
        self.server_update_worker = ServerUpdateCheckWorker(server, channel, APP_VERSION)
        self.server_update_worker.finished_signal.connect(self._on_server_update_checked)
        self.server_update_worker.start()

    def _on_server_update_checked(self, ok: bool, has_update: bool, new_ver: str, notes: str,
                                  archive_url: str, sha_url: str, error: str) -> None:
        self.btn_server_update.setEnabled(True)
        server, _ = load_update_config()
        if not ok:
            box = QMessageBox(QMessageBox.Warning, "服务器更新检查失败",
                              f"{error}\n\n当前更新服务器: {server}\n可能服务器地址已变更，可点“修改服务器地址”。",
                              parent=self)
            btn_edit = box.addButton("修改服务器地址", QMessageBox.ActionRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is btn_edit:
                self._open_update_server_settings()
            return
        if not has_update:
            QMessageBox.information(self, "更新检查", f"当前已是最新版本 (v{APP_VERSION})！\n服务器版本: v{new_ver.lstrip('vV')}")
            return
        msg = (f"发现新版本 [{new_ver}]！\n\n当前版本: v{APP_VERSION}\n最新版本: {new_ver}\n\n"
               f"更新日志:\n{notes}\n\n是否立即下载升级？")
        if QMessageBox.question(self, "版本更新提示", msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        dlg = UpdateProgressDialog(archive_url, new_ver, parent=self, sha_url=sha_url)
        if dlg.exec() and dlg.success:
            try:
                apply_server_update_and_restart(dlg.downloaded_zip_path)
            except Exception as exc:
                QMessageBox.critical(self, "更新失败", f"应用更新失败: {exc}")
        else:
            self.log("服务器更新已取消或下载失败。")

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
                    self.compressor_worker.enqueue(
                        item.file_path, out_dir=self._watch_output_dir(item.file_path))

    def _show_table_context_menu(self, pos) -> None:
        selected_rows = set(item.row() for item in self.table.selectedItems())
        if not selected_rows:
            return

        menu = QMenu(self)
        act_force = menu.addAction("⚡ 强制立即提交压缩 (忽略已有产物与冷却)")
        act_exclude = menu.addAction("🚫 排除选中项 (不再扫描)")
        act_open_dir = menu.addAction("📂 打开所在文件夹")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_force:
            self._force_compress_selected_rows(selected_rows)
        elif action == act_exclude:
            self._exclude_selected_rows(selected_rows)
        elif action == act_open_dir:
            self._open_selected_rows_folder(selected_rows)

    def _exclude_selected_rows(self, selected_rows: set[int]) -> None:
        watch_dir = Path(self.edit_watch_dir.text().strip())
        if not watch_dir.exists():
            return
        excluded = 0
        for r in selected_rows:
            rel_item = self.table.item(r, 1)
            if not rel_item:
                continue
            file_path = watch_dir / rel_item.text()
            if not file_path.exists():
                continue
            key = self._path_key(file_path)
            if key not in self.excluded_paths:
                self.excluded_paths.add(key)
                excluded += 1
            if self.watcher_worker:
                self.watcher_worker.exclude_path(file_path)
            if self.compressor_worker and self.compressor_worker.cancel_queued(file_path):
                self.log(f"已从压缩队列移除: {file_path.name}")
        if not excluded:
            return
        self.save_settings(silent=True)
        self.log(f"🚫 已排除 {excluded} 个视频；后续扫描将不再显示或压缩它们。")
        worker = self.watcher_worker or FolderWatcherWorker(self.collect_watch_config())
        self._on_scan_completed(worker.scan_once())

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
                self.log(f"⚡ 手动强制忽略已有产物与冷却，提交压缩: {file_path.name}")
                if self.watcher_worker:
                    self.watcher_worker.force_process(file_path)
                if self.compressor_worker:
                    self.compressor_worker.force_enqueue(
                        file_path, out_dir=self._watch_output_dir(file_path))

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

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.set_status("监控运行中", "busy")

        # 启动压缩执行器（手动压缩与自动监控共享同一队列）
        self._start_compressor_worker()

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
    queue_idle_signal = Signal()

    def __init__(self, c_cfg: CompressionConfig) -> None:
        super().__init__()
        self.c_cfg = c_cfg
        self.queue: list[Path] = []
        self.active_set: set[str] = set()
        self.ever_enqueued: set[str] = set()  # 记录所有曾入队的文件，防止重复压缩
        self.task_cfgs: dict[str, CompressionConfig] = {}  # 任务专用参数（如手动模板）
        self.task_out_dirs: dict[str, Path] = {}  # 任务输出目录；缺省为源视频同级 YS
        self._queue_lock = Lock()
        self._active_lock = Lock()
        self._has_received_work = False
        self._idle_reported = False
        self._stop_requested = False

    def is_busy(self) -> bool:
        """队列中仍有待处理或正在压缩的任务。"""
        with self._queue_lock:
            if self.queue:
                return True
        with self._active_lock:
            return bool(self.active_set)

    def enqueue(self, video_path: Path, cfg: Optional[CompressionConfig] = None,
                out_dir: Optional[Path] = None) -> None:
        """入队压缩（自动去重：同一文件在本轮监控中只会被压缩一次）。
        cfg 为该任务专用的压缩参数，缺省使用线程创建时的参数。"""
        path_str = str(video_path)
        with self._queue_lock:
            if path_str not in self.ever_enqueued:
                self.ever_enqueued.add(path_str)
                if cfg is not None:
                    self.task_cfgs[path_str] = cfg
                if out_dir is not None:
                    self.task_out_dirs[path_str] = out_dir
                self.queue.append(video_path)
                self._has_received_work = True
                self._idle_reported = False

    def force_enqueue(self, video_path: Path, out_dir: Optional[Path] = None) -> None:
        """强制入队（忽略去重历史，用于右键手动强制压缩）。"""
        path_str = str(video_path)
        with self._queue_lock, self._active_lock:
            if path_str not in self.active_set:
                self.ever_enqueued.add(path_str)
                if out_dir is not None:
                    self.task_out_dirs[path_str] = out_dir
                self.queue.append(video_path)
                self._has_received_work = True
                self._idle_reported = False

    def cancel_queued(self, video_path: Path) -> bool:
        """移除尚未启动的任务；正在压缩的文件不会被强制中断。"""
        path_str = str(video_path)
        with self._queue_lock:
            original_count = len(self.queue)
            self.queue = [p for p in self.queue if str(p) != path_str]
            self.task_cfgs.pop(path_str, None)
            self.task_out_dirs.pop(path_str, None)
            if len(self.queue) < original_count:
                self.ever_enqueued.discard(path_str)
                return True
        return False

    def stop(self) -> None:
        self._stop_requested = True

    def emit_log(self, msg: str) -> None:
        self.log_signal.emit(msg)

    def _compress_one(self, target: Path) -> None:
        path_str = str(target)
        with self._queue_lock:
            cfg = self.task_cfgs.pop(path_str, None) or self.c_cfg
            out_dir = self.task_out_dirs.pop(path_str, None)
        compressor = VideoCompressor(cfg, self.emit_log, lambda: self._stop_requested)
        # active_set.add 已在 run() 中 pool.submit 之前完成，此处无需重复添加
        self.file_progress_signal.emit(target, FileStatus.PROCESSING, 0)
        try:
            def file_pct(pct: int):
                self.file_progress_signal.emit(target, FileStatus.PROCESSING, pct)

            compressor.compress(target, out_dir=out_dir, progress_cb=file_pct)
            self.file_progress_signal.emit(target, FileStatus.COMPLETED, 100)
        except Exception as exc:
            self.emit_log(f"压缩任务失败 [{target.name}]: {exc}")
            self.file_progress_signal.emit(target, FileStatus.FAILED, 0)
        finally:
            with self._active_lock:
                self.active_set.discard(path_str)

    def run(self) -> None:
        workers = max(1, self.c_cfg.max_workers)
        import time
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            while not self._stop_requested:
                with self._queue_lock:
                    target = self.queue.pop(0) if self.queue else None
                if target is None:
                    with self._active_lock:
                        has_active_work = bool(self.active_set)
                    if self._has_received_work and not has_active_work and not self._idle_reported:
                        self._idle_reported = True
                        self.queue_idle_signal.emit()
                    time.sleep(0.5)
                    continue
                if self._stop_requested:
                    break

                # 在提交到线程池之前就加入 active_set，
                # 消除 pop 与 _compress_one 之间的去重空窗期
                with self._active_lock:
                    self.active_set.add(str(target))
                fut = pool.submit(self._compress_one, target)
                futures.append(fut)
