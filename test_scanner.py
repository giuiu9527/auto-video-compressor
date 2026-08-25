# -*- coding: utf-8 -*-
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from config import WatchConfig
from scanner import FileStatus
from scanner import FolderWatcherWorker


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


if __name__ == "__main__":
    unittest.main()
