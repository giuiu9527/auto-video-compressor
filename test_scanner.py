# -*- coding: utf-8 -*-
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from config import WatchConfig
from main_window import ManualDropList, MainWindow
from scanner import FileStatus
from scanner import FolderWatcherWorker
import utils


class CompressedOutputDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "YS"
        self.output_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def touch(self, relative_path: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_detects_exact_same_name_mp4_in_ys(self) -> None:
        source = self.touch("示例.mkv")
        self.touch("YS/示例.mp4")
        self.assertTrue(FolderWatcherWorker._has_compressed_output(source))

    def test_detects_prefixed_and_numbered_output(self) -> None:
        source = self.touch("示例.mkv")
        self.touch("YS/(ys)示例_2.mp4")
        self.assertTrue(FolderWatcherWorker._has_compressed_output(source))

    def test_source_name_may_itself_end_with_number(self) -> None:
        source = self.touch("示例_2.mkv")
        self.touch("YS/(ys)示例_2.mp4")
        self.assertTrue(FolderWatcherWorker._has_compressed_output(source))

    def test_ignores_wrong_extension_and_output_outside_ys(self) -> None:
        source = self.touch("示例.mkv")
        self.touch("示例.mp4")
        self.touch("YS/(ys)示例.mkv")
        self.assertFalse(FolderWatcherWorker._has_compressed_output(source))

    def test_does_not_confuse_longer_sibling_name_with_prefix(self) -> None:
        source = self.touch("video.mkv")
        self.touch("myvideo.mkv")
        self.touch("YS/myvideo.mp4")
        self.assertFalse(FolderWatcherWorker._has_compressed_output(source))

    @patch("scanner.probe_duration", return_value=10.0)
    @patch("scanner.check_file_writing_status", return_value=(False, 0, ""))
    def test_force_modes_bypass_existing_output(self, _writing, _duration) -> None:
        source = self.touch("示例.mkv")
        self.touch("YS/示例.mp4")
        worker = FolderWatcherWorker(WatchConfig(watch_dir=str(self.root)))

        self.assertEqual(worker.scan_once()[0].status, FileStatus.SKIPPED_ALREADY)

        worker.set_force_compress(True)
        self.assertEqual(worker.scan_once()[0].status, FileStatus.WAITING)

        single_worker = FolderWatcherWorker(WatchConfig(watch_dir=str(self.root)))
        single_worker.force_process(source)
        self.assertEqual(single_worker.scan_once()[0].status, FileStatus.WAITING)


class StartupAndDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_manual_list_accepts_local_file_drop(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "拖入测试.mkv"
            source.touch()
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(source))])
            received: list[Path] = []
            widget = ManualDropList()
            widget.resize(400, 300)
            widget.files_dropped.connect(received.extend)

            enter = QDragEnterEvent(
                QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
            QApplication.sendEvent(widget.viewport(), enter)
            drop = QDropEvent(
                QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
            QApplication.sendEvent(widget.viewport(), drop)

            self.assertTrue(enter.isAccepted())
            self.assertTrue(drop.isAccepted())
            self.assertEqual(received, [source])
            widget.close()

    def test_startup_work_is_deferred_until_after_constructor(self) -> None:
        with patch("main_window.QTimer.singleShot") as single_shot, \
                patch.object(MainWindow, "check_updates") as check_updates, \
                patch.object(MainWindow, "_start_one_shot_scan") as start_scan:
            window = MainWindow()

            single_shot.assert_called_once()
            check_updates.assert_not_called()
            start_scan.assert_not_called()
            window.close()

    def test_dropped_video_is_added_to_manual_compression_list(self) -> None:
        with TemporaryDirectory() as temp_dir, \
                patch("main_window.QTimer.singleShot"):
            source = Path(temp_dir) / "手动压缩.mkv"
            source.touch()
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(source))])
            window = MainWindow()

            enter = QDragEnterEvent(
                QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
            QApplication.sendEvent(window.manual_list.viewport(), enter)
            drop = QDropEvent(
                QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
            QApplication.sendEvent(window.manual_list.viewport(), drop)

            self.assertTrue(drop.isAccepted())
            self.assertEqual(window.manual_list.count(), 1)
            self.assertEqual(Path(window.manual_list.item(0).data(Qt.UserRole)), source)
            self.assertNotIn("未添加视频", window.log_box.toPlainText())
            window.close()

    def test_startup_scan_runs_without_blocking_main_thread(self) -> None:
        with TemporaryDirectory() as temp_dir, \
                patch("main_window.QTimer.singleShot"), \
                patch.object(MainWindow, "check_updates"), \
                patch.object(FolderWatcherWorker, "scan_once",
                             side_effect=lambda: (time.sleep(0.3), [])[1]):
            window = MainWindow()
            window.edit_watch_dir.setText(temp_dir)
            started = time.perf_counter()
            window._run_post_show_tasks()
            elapsed = time.perf_counter() - started

            self.assertLess(elapsed, 0.1)
            self.assertIsNotNone(window.scan_once_worker)
            window.scan_once_worker.wait(2000)
            window.close()

    def test_frozen_binary_finds_ffmpeg_inside_internal_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            (app_dir / "bin").mkdir()
            internal_bin = app_dir / "_internal" / "bin"
            internal_bin.mkdir(parents=True)
            (internal_bin / "ffmpeg.exe").touch()
            (internal_bin / "ffprobe.exe").touch()

            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable", str(app_dir / "IMM-Compressor.exe")):
                self.assertEqual(utils._bundled_bin_dir(), internal_bin)


if __name__ == "__main__":
    unittest.main()
