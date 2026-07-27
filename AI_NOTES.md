# AI 开发与维护记忆日志 (AI_NOTES.md)

本文档记录了 **视频自动循环监控压缩工具 (AutoVideoCompressor)** 项目的架构设计、核心坑点解决方案、Syncthing 跨机同步与录制防冲突机制、PyInstaller 打包细节以及 GitHub 自动更新协议。

---

## 📌 项目基本信息

- **项目名称**: 视频自动循环监控压缩工具 (AutoVideoCompressor)
- **项目仓库**: [giuiu9527/auto-video-compressor](https://github.com/giuiu9527/auto-video-compressor)
- **运行环境**: Windows 10 / 11, Python 3.12, PySide6 (Qt 6)
- **打包可执行文件名**: `IMM-Compressor.exe`
- **默认打包输出路径**: `dist/IMM-Compressor/IMM-Compressor.exe`

---

## 🛡️ 核心机制与防御设计

### 1. Syncthing 跨机传输与 OBS/MKV 实时录制 4 重防冲突机制 (`utils.py`)
为防止 Syncthing 正在同步中或另一台电脑 OBS 正在录制 MKV 视频时，本软件误读并提前启动压缩造成文件损坏，设计了 **4 重安全防护**：
1. **临时文件名过滤**: 排除 `.syncthing.`, `.tmp`, `.part`, `!syncthing` 等临时写入扩展名。
2. **独占文件句柄校验**: 使用 Python `open(path, 'r+b')` 进行独占锁测试，若被 Syncthing 或录制软件占用则立即避让。
3. **mtime 修改冷却期校验 (用户可调)**:
   - 默认冷却时间调整为 **180 秒（3 分钟）**（UI 界面配置 `spin_min_stable` 可实时修改）。
   - 必须满足 `当前时间 - 文件最后修改时间 >= min_stable_sec` 才判定为静止写入文件。
4. **ffprobe 容器与 Cluster 完整性校验**: 使用内置 `ffprobe` 检测文件头与流信息，确保 EBML/Cluster 结构合法。

---

### 2. 线程安全与 UI 状态同步 (`main_window.py` & `compressor.py`)
- **多线程解耦**: 压缩任务使用 `ThreadPoolExecutor` 后台执行，完全分离 PySide6 GUI 主线程。
- **Qt Signal 通信**: `VideoCompressorWorker` 通过 `file_progress_signal` 和 `log_signal` 将进度和日志回调给主窗口，彻底避免 C++ Segfault 崩溃。
- **并发锁与重复去重**: 维护 `active_set` 集合，防止同一文件被扫盘器和 Watchdog 监听器重复添加或并发压缩。

---

### 3. 文件匹配与表格过滤规则 (`scanner.py`)
- **压缩输出识别**: 压缩文件统一使用 `(ys)` 后缀前缀（如 `xxx(ys).mp4`）。
- **表格显示过滤**:
  - `(ys)` 结尾的压缩结果文件**自动在表格中隐藏**，列表只呈现源视频。
  - 已完成压缩的源视频在列表中直接标记为 `✅ 已完成 100%`，避免重复压缩。
- **手动清空机制**: 提供 `🧹 清空列表` 按钮，可清空 UI 表格并重置内存扫描历史。

---

## 📦 PyInstaller 打包与故障排除 (`AutoVideoCompressor.spec`)

### 1. 启动报错 `Failed to start embedded python interpreter!`
- **原因**: 可执行文件名包含中文字符（如 `视频自动循环监控压缩工具.exe`）时，Windows C 语言 Bootloader 解析 `_internal/python312.dll` 存在 ANSI/Codepage 路径编码转换故障。
- **解决方案**: 可执行文件统一命名为纯英文 **`AutoVideoCompressor.exe`**，软件窗口标题与 UI 保持中文。

### 2. 报错 `ModuleNotFoundError: No module named 'PySide6.QtCore'` 或 `'shiboken6.Shiboken'`
- **原因**: PySide6 依赖 C++ 绑定生成器 `shiboken6`（包含原生 `.pyd` 扩展）。普通 PyInstaller 打包未完整收集 `shiboken6.Shiboken` 二进制库。
- **解决方案**: 在 `.spec` 配置文件中配置：
  ```python
  from PyInstaller.utils.hooks import collect_all

  datas_p, binaries_p, hiddenimports_p = collect_all('PySide6')
  datas_s, binaries_s, hiddenimports_s = collect_all('shiboken6')
  ```

### 3. FFmpeg 环境自动注册 (`utils.py`)
- 自动检测并优先加载打包好的 `bin/ffmpeg.exe` 与 `bin/ffprobe.exe`，并自动注入 `os.environ["PATH"]` 头部，无需用户在系统配置环境变量。

---

## 🚀 自动更新协议 (`updater.py`)

- **更新检查地址**: `https://api.github.com/repos/giuiu9527/auto-video-compressor/releases/latest`
- **对比机制**: 解析 Release Tag 版本号（如 `v1.0.0`），与本地 `config.py` 中的 `APP_VERSION` 对比。
- **更新流程**:
  1. 后台线程 `UpdateCheckWorker` 静默检查。
  2. 发现新版本弹出更新对话框，展示 Release Notes。
  3. 确认后自动下载 Zip 并解压生成批处理自替换脚本，重启换新。

---

## 📁 目录结构指南

```
视频自动循环监控压缩工具/
├── AutoVideoCompressor.spec    # PyInstaller 编译配置文件 (重点维护)
├── main.py                     # 程序入口
├── main_window.py              # PySide6 主界面与多线程控制器
├── compressor.py                # FFmpeg 压制引擎
├── scanner.py                 # 文件夹扫描与去重逻辑
├── utils.py                   # 4重防冲突测试与 FFmpeg 路径注入
├── config.py                  # 版本号与默认配置
├── updater.py                 # GitHub Release 自动更新器
├── styles.py                  # Modern Dark 主题 QSS 样式表
├── bin/                       # 随包附带的 ffmpeg.exe & ffprobe.exe
└── AI_NOTES.md                # 本维护记忆文档
```

---
*由 AIpair 编程辅助记录于 2026-07-26*
