# -*- coding: utf-8 -*-
"""压缩参数模板：用户自定义模板（独立 JSON 保存）及管理对话框。"""
from __future__ import annotations

import json
from dataclasses import asdict, fields, replace
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

from config import APP_DIR, CompressionConfig
from params_form import NON_TEMPLATE_FIELDS, CompressionParamsForm

TEMPLATES_FILE = APP_DIR / "compress_templates.json"
BUILTIN_PREFIX = "★ "

# 不再预置内置模板，全部由用户自行创建。
_BUILTIN: dict[str, dict] = {}

_TEMPLATE_KEYS = [f.name for f in fields(CompressionConfig) if f.name not in NON_TEMPLATE_FIELDS]


def template_dict(cfg: CompressionConfig) -> dict:
    """提取配置中属于模板的字段。"""
    data = asdict(cfg)
    return {k: data[k] for k in _TEMPLATE_KEYS}


class TemplateStore:
    """模板仓库：名称 -> 参数字典。内置模板只读，名称带 ★ 前缀。"""

    def __init__(self) -> None:
        self.custom: dict[str, dict] = {}
        self.load()

    @staticmethod
    def is_builtin(name: str) -> bool:
        return name.startswith(BUILTIN_PREFIX)

    def names(self) -> list[str]:
        return [BUILTIN_PREFIX + n for n in _BUILTIN] + list(self.custom)

    def get(self, name: str) -> Optional[dict]:
        if self.is_builtin(name):
            return _BUILTIN.get(name[len(BUILTIN_PREFIX):])
        return self.custom.get(name)

    def apply_to(self, name: str, base: CompressionConfig) -> CompressionConfig:
        """把模板参数覆盖到 base（保留 base 的前缀与并发数）；模板不存在则原样返回。"""
        data = self.get(name)
        if data is None:
            return base
        return replace(base, **{k: v for k, v in data.items() if k in _TEMPLATE_KEYS})

    def load(self) -> None:
        self.custom = {}
        if not TEMPLATES_FILE.exists():
            return
        try:
            raw = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8-sig"))
            for name, data in raw.get("templates", {}).items():
                if isinstance(data, dict) and not self.is_builtin(name):
                    self.custom[name] = {k: v for k, v in data.items() if k in _TEMPLATE_KEYS}
        except Exception:
            self.custom = {}

    def save(self) -> None:
        TEMPLATES_FILE.write_text(
            json.dumps({"templates": self.custom}, ensure_ascii=False, indent=2), encoding="utf-8")

    def put(self, name: str, cfg: CompressionConfig) -> None:
        self.custom[name] = template_dict(cfg)
        self.save()

    def delete(self, name: str) -> None:
        if self.custom.pop(name, None) is not None:
            self.save()

    def rename(self, old: str, new: str) -> None:
        if old in self.custom:
            self.custom = {(new if k == old else k): v for k, v in self.custom.items()}
            self.save()


def ask_template_name(parent, store: TemplateStore, title: str, default: str = "") -> Optional[str]:
    """询问模板名并校验；覆盖已有自定义模板前确认。取消或非法返回 None。"""
    name, ok = QInputDialog.getText(parent, title, "模板名称:", text=default)
    name = name.strip()
    if not ok or not name:
        return None
    if name.startswith(BUILTIN_PREFIX.strip()):
        QMessageBox.warning(parent, "名称无效", "自定义模板名称不能以 ★ 开头。")
        return None
    if name in store.custom and name != default:
        if QMessageBox.question(parent, "覆盖模板", f"模板“{name}”已存在，是否覆盖？") != QMessageBox.Yes:
            return None
    return name


class TemplateManagerDialog(QDialog):
    """左侧模板列表，右侧参数编辑；内置模板只读，可复制后修改。"""

    def __init__(self, store: TemplateStore, current: CompressionConfig, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.current = current
        self.changed = False
        self.setWindowTitle("压缩模板管理")
        self.resize(900, 480)

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        self.btn_new = QPushButton("➕ 以当前主参数新建")
        self.btn_copy = QPushButton("⧉ 复制")
        self.btn_rename = QPushButton("✎ 重命名")
        self.btn_delete = QPushButton("🗑 删除")
        for b in (self.btn_new, self.btn_copy, self.btn_rename, self.btn_delete):
            left.addWidget(b)
        self.btn_new.clicked.connect(self._new)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_rename.clicked.connect(self._rename)
        self.btn_delete.clicked.connect(self._delete)
        root.addLayout(left, 0)

        right = QVBoxLayout()
        self.lbl_hint = QLabel(""); self.lbl_hint.setObjectName("HintLabel"); self.lbl_hint.setWordWrap(True)
        right.addWidget(self.lbl_hint)
        self.form = CompressionParamsForm(vertical=True)
        right.addWidget(self.form, 1)
        row = QHBoxLayout(); row.addStretch(1)
        self.btn_save = QPushButton("💾 保存修改")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_close = QPushButton("关闭")
        self.btn_save.clicked.connect(self._save)
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_save); row.addWidget(self.btn_close)
        right.addLayout(row)
        root.addLayout(right, 1)

        self._reload()

    def _selected_name(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _reload(self, select: Optional[str] = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for n in self.store.names():
            it = QListWidgetItem(n); it.setData(Qt.UserRole, n)
            self.list.addItem(it)
        self.list.blockSignals(False)
        names = self.store.names()
        row = names.index(select) if select in names else 0
        self.list.setCurrentRow(row)
        self._on_select(self.list.currentItem(), None)

    def _on_select(self, item, _prev) -> None:
        name = item.data(Qt.UserRole) if item else None
        builtin = bool(name) and self.store.is_builtin(name)
        if name:
            self.form.set_config(self.store.apply_to(name, CompressionConfig()))
        self.form.setEnabled(bool(name) and not builtin)
        self.btn_save.setEnabled(bool(name) and not builtin)
        self.btn_rename.setEnabled(bool(name) and not builtin)
        self.btn_delete.setEnabled(bool(name) and not builtin)
        self.btn_copy.setEnabled(bool(name))
        self.lbl_hint.setText("内置模板为只读，可点“复制”后再修改。" if builtin
                              else "修改右侧参数后点“保存修改”。")

    def _new(self) -> None:
        name = ask_template_name(self, self.store, "新建模板")
        if name:
            self.store.put(name, self.current)
            self.changed = True
            self._reload(name)

    def _copy(self) -> None:
        src = self._selected_name()
        if not src:
            return
        default = src.replace(BUILTIN_PREFIX, "") + " 副本"
        name = ask_template_name(self, self.store, "复制模板", default)
        if name:
            self.store.put(name, self.store.apply_to(src, CompressionConfig()))
            self.changed = True
            self._reload(name)

    def _rename(self) -> None:
        old = self._selected_name()
        if not old or self.store.is_builtin(old):
            return
        new = ask_template_name(self, self.store, "重命名模板", old)
        if new and new != old:
            self.store.rename(old, new)
            self.changed = True
            self._reload(new)

    def _delete(self) -> None:
        name = self._selected_name()
        if not name or self.store.is_builtin(name):
            return
        if QMessageBox.question(self, "删除模板", f"确定删除模板“{name}”？") == QMessageBox.Yes:
            self.store.delete(name)
            self.changed = True
            self._reload()

    def _save(self) -> None:
        name = self._selected_name()
        if not name or self.store.is_builtin(name):
            return
        self.store.put(name, self.form.get_config(CompressionConfig()))
        self.changed = True
        QMessageBox.information(self, "已保存", f"模板“{name}”已保存。")
