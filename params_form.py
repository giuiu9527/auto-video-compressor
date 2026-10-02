# -*- coding: utf-8 -*-
"""可复用的压缩参数表单（主界面与模板管理对话框共用）。"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox,
    QLabel, QSpinBox, QWidget,
)

from compressor import (
    AUDIO_CODEC_OPTIONS, OUTPUT_FORMAT_OPTIONS, PRESET_OPTIONS,
    RATE_MODE_OPTIONS, VIDEO_CODEC_OPTIONS,
)
from config import CompressionConfig

# 不属于“压缩参数模板”的字段：输出前缀与并发数由主界面全局控制。
NON_TEMPLATE_FIELDS = ("output_prefix", "max_workers")


def _form_row(grid: QGridLayout, row: int, col: int, label: str, widget: QWidget) -> None:
    lab = QLabel(label); lab.setObjectName("FieldLabel")
    grid.addWidget(lab, row, col); grid.addWidget(widget, row, col + 1)


class CompressionParamsForm(QWidget):
    """编码/码率 + 去黑边/尾部剪切 两组参数。"""

    def __init__(self, vertical: bool = False, parent=None) -> None:
        super().__init__(parent)
        lay = QBoxLayout(QBoxLayout.TopToBottom if vertical else QBoxLayout.LeftToRight, self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        lay.addWidget(self._build_codec_group(), 1)
        lay.addWidget(self._build_cut_group(), 1)

    def _build_codec_group(self) -> QGroupBox:
        grp = QGroupBox("编码与码率")
        g = QGridLayout(grp)
        g.setHorizontalSpacing(8); g.setVerticalSpacing(4)
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

        _form_row(g, 0, 0, "格式", self.combo_format)
        _form_row(g, 0, 2, "视频编码", self.combo_v_codec)
        _form_row(g, 1, 0, "码率模式", self.combo_rate_mode)
        _form_row(g, 1, 2, "预设", self.combo_preset)
        _form_row(g, 2, 0, "CQ/CRF", self.spin_cq)
        _form_row(g, 2, 2, "比特率(k)", self.spin_bitrate)
        _form_row(g, 3, 0, "音频编码", self.combo_a_codec)
        _form_row(g, 3, 2, "音频码率(k)", self.spin_a_bitrate)
        return grp

    def _build_cut_group(self) -> QGroupBox:
        grp = QGroupBox("✂ 去黑边 / 尾部剪切")
        g = QGridLayout(grp)
        g.setHorizontalSpacing(8); g.setVerticalSpacing(4)
        self.chk_auto_crop = QCheckBox("自动去黑边 (cropdetect 采样)")
        self.chk_auto_crop.setStyleSheet("QCheckBox{font-weight:600; color:#1f6fb2;}")
        g.addWidget(self.chk_auto_crop, 0, 0, 1, 4)

        self.spin_crop_top = QSpinBox(); self.spin_crop_top.setRange(0, 4000)
        self.spin_crop_bottom = QSpinBox(); self.spin_crop_bottom.setRange(0, 4000)
        self.spin_crop_left = QSpinBox(); self.spin_crop_left.setRange(0, 4000)
        self.spin_crop_right = QSpinBox(); self.spin_crop_right.setRange(0, 4000)
        _form_row(g, 1, 0, "额外 上(px)", self.spin_crop_top)
        _form_row(g, 1, 2, "额外 下(px)", self.spin_crop_bottom)
        _form_row(g, 2, 0, "额外 左(px)", self.spin_crop_left)
        _form_row(g, 2, 2, "额外 右(px)", self.spin_crop_right)

        self.chk_trim_end = QCheckBox("启用尾部剪切 (提前指定秒数结束)")
        self.chk_trim_end.setStyleSheet("QCheckBox{font-weight:600; color:#1f6fb2;}")
        self.spin_trim_end_sec = QDoubleSpinBox()
        self.spin_trim_end_sec.setRange(0.1, 3600.0); self.spin_trim_end_sec.setSingleStep(0.5)
        self.spin_trim_end_sec.setDecimals(1); self.spin_trim_end_sec.setValue(7.0)
        self.spin_trim_end_sec.setEnabled(False)
        self.chk_trim_end.toggled.connect(self.spin_trim_end_sec.setEnabled)
        g.addWidget(self.chk_trim_end, 3, 0, 1, 2)
        _form_row(g, 3, 2, "提前结束(s)", self.spin_trim_end_sec)
        return grp

    def get_config(self, base: CompressionConfig) -> CompressionConfig:
        """把表单参数覆盖到 base 上（base 的前缀/并发数保持不变）。"""
        return replace(
            base,
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
            trim_end=self.chk_trim_end.isChecked(),
            trim_end_sec=self.spin_trim_end_sec.value(),
        )

    def set_config(self, c: CompressionConfig) -> None:
        self.combo_v_codec.setCurrentIndex(max(0, self.combo_v_codec.findData(c.video_codec)))
        self.combo_a_codec.setCurrentIndex(max(0, self.combo_a_codec.findData(c.audio_codec)))
        self.combo_format.setCurrentIndex(max(0, self.combo_format.findText(c.output_format)))
        self.combo_rate_mode.setCurrentIndex(max(0, self.combo_rate_mode.findData(c.rate_mode)))
        self.combo_preset.setCurrentIndex(max(0, self.combo_preset.findText(c.preset)))
        self.spin_cq.setValue(c.cq_value)
        self.spin_bitrate.setValue(c.bitrate_kbps)
        self.spin_a_bitrate.setValue(c.audio_bitrate_kbps)
        self.chk_auto_crop.setChecked(c.auto_crop)
        self.spin_crop_top.setValue(c.extra_top)
        self.spin_crop_bottom.setValue(c.extra_bottom)
        self.spin_crop_left.setValue(c.extra_left)
        self.spin_crop_right.setValue(c.extra_right)
        self.chk_trim_end.setChecked(c.trim_end)
        self.spin_trim_end_sec.setValue(c.trim_end_sec)
        self.spin_trim_end_sec.setEnabled(c.trim_end)
