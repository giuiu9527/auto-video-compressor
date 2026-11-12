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
- **非阻塞启动**: 配置只在构造阶段读取；目录扫描和版本检查在窗口显示后启动，一次性扫盘也始终使用后台线程。

---

### 3. 文件匹配与表格过滤规则 (`scanner.py`)
- **保存目录** (`WatchConfig.output_root`, `config.resolve_output_dir`): 为空时输出到源视频同级 `YS`；自定义时保留父文件夹结构——自动监控相对监控根目录（`根/01/1.mkv -> A/01/1.mp4`），手动拖入文件夹保留该文件夹名，拖入单个文件保留其所在文件夹名。自定义目录本身不参与扫描。
- **压缩输出识别**: 只检查上述输出目录中的同名 `.mp4`，比较时忽略可选输出前缀。
- **表格显示过滤**:
  - `YS` 输出目录整体不参与递归扫描，列表只呈现源视频。
  - 已有压缩产物的源视频标记为 `⏩ 已跳过`；全局强制开关或右键强制可绕过此检查。
- **手动清空机制**: 提供 `🧹 清空列表` 按钮，可清空 UI 表格并重置内存扫描历史。

### 4. 编码参数细节 (`compressor.py`)
- **压缩模板** (`templates.py` + `params_form.py`): 用户模板存于 `compress_templates.json`；输出前缀与并发数不属于模板 (`NON_TEMPLATE_FIELDS`)。手动任务可单独选模板。
- **预设映射**: 界面预设默认 `p4`（NVENC 专用）。CPU 编码 (libx264/libx265) 不认 p1~p7，`cpu_preset()` 映射为 veryfast~veryslow。
- **不加 `-tune film`**: libx265 不支持该值会直接失败；libx264 上网课素材实测同 SSIM 下体积更大。
- **NVENC CQ 锁定 qmin=qmax=cq 是有意的**: 实测放开后（`-b:v 0`）同 CQ 体积大 1.9~2.3 倍，别"优化"掉。
- **ycgco 色彩标记**: 手机投屏 (scrcpy) 录像的 H.264 常标为 ycgco，FFmpeg 像素格式转换不支持，`-hwaccel cuda` 必失败。`build_command` 探测到非常规色彩矩阵时在滤镜链最前加 `setparams=colorspace=bt709`（只改标记）。
- **产物不比源小**: 低码率源（如 160kbps 网课）用 CQ23 反而变大。`_keep_smaller` 改为 `-c copy` 直接封装源视频写回同一 dst（保证扫描器认为已压缩、不会反复重压）；启用裁切时保留重编码结果。

---

## 📦 PyInstaller 打包与故障排除 (`AutoVideoCompressor.spec`)

### 1. 启动报错 `Failed to start embedded python interpreter!`
- **原因**: 可执行文件名包含中文字符（如 `视频自动循环监控压缩工具.exe`）时，Windows C 语言 Bootloader 解析 `_internal/python312.dll` 存在 ANSI/Codepage 路径编码转换故障。
- **解决方案**: 可执行文件统一命名为纯英文 **`IMM-Compressor.exe`**（spec 文件名仍是 `AutoVideoCompressor.spec`），软件窗口标题与 UI 保持中文。

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
- **123 云盘通道** (`server_updater.py`, v1.2.8+): 规范见 `D:\Test Code\软件包更新方式.md`。清单直链写死为 `https://1836258598.cdn.123clouddisk.com/1836258598/IMM-%E8%BD%AF%E4%BB%B6/IMM-Compressor/update.json`，带 SHA-256 校验。
  - 发布用 `publish_123pan.ps1 -Notes "..."`（薄壳：打 portable 包后调通用脚本 `publish_123pan.js`）。
  - v1.2.1~v1.2.7 指向的 OpenList（38.65.91.124）已废弃、无法访问；这些旧版本只能走 GitHub 更新或手动装 ≥1.2.8。用户保存过旧地址时 `load_update_config` 会自动改回默认。
- **⚠️ 发布要两步都做**: `release.ps1` 只发 GitHub Release（`win64.zip`），**不会**推 123 云盘；必须再跑 `publish_123pan.ps1`。

## 🧪 测试
- `python -m unittest test_scanner`（未装 pytest）。`EncodeIntegrationTests` 会用 `bin/ffmpeg.exe` 真实编码几秒合成视频。

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
├── server_updater.py          # 123 云盘更新通道 (SHA-256 校验)
├── templates.py / params_form.py  # 压缩模板与参数表单
├── release.ps1 / publish_123pan.ps1  # 发布到 GitHub / 发布到 123 云盘
├── test_scanner.py            # 单元测试 + ffmpeg 编码集成测试
├── styles.py                  # Modern Dark 主题 QSS 样式表
├── bin/                       # 随包附带的 ffmpeg.exe & ffprobe.exe
└── AI_NOTES.md                # 本维护记忆文档
```

---
*由 AIpair 编程辅助记录于 2026-07-26*
