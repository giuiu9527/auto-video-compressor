# 🎬 AutoVideoCompressor — 视频自动循环监控压缩工具

<p align="center">
  <img src="https://img.shields.io/badge/版本-v1.0.0-blue?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/平台-Windows_10%2F11-green?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/框架-PySide6_(Qt_6)-orange?style=flat-square" alt="Framework">
  <img src="https://img.shields.io/badge/编码器-FFmpeg_+_NVENC-red?style=flat-square" alt="Encoder">
  <img src="https://img.shields.io/github/license/giuiu9527/auto-video-compressor?style=flat-square" alt="License">
</p>

> 基于 **PySide6 + FFmpeg** 构建的全自动视频监控与压缩工具。  
> 专为 **Syncthing 跨机同步**、**OBS 边录边传** 以及 **大批量视频自动压制** 场景设计。  
> 放着不管就能自动帮你把视频压好，省心省力。

---

## ✨ 核心特性

| 功能 | 说明 |
|------|------|
| 🔄 **自动循环扫描** | 对目标文件夹及所有子目录进行定时自动扫描，发现新视频自动加入压缩队列 |
| 🛡️ **4 重防冲突机制** | 临时文件过滤 → 句柄独占检测 → mtime 冷却期 → ffprobe 容器校验，绝不误压正在写入的文件 |
| ⏱️ **实时冷却倒计时** | 正在冷却等待的文件显示精确剩余秒数（如 `⏳ 暂跳过 (还需 45s 冷却)`） |
| ⚡ **右键强制压缩** | 右键表格行可「强制立即提交压缩」，跳过冷却等待 |
| 🎮 **NVIDIA NVENC 硬件加速** | 优先使用 GPU 硬件编码，速度极快；不支持时自动回退 CPU 软编码 |
| ✂️ **智能尾部裁剪** | 可选自动去除视频末尾指定秒数（去除录制结束时的多余画面） |
| 🔁 **绝对排重** | 通过 `(ys)` 前缀识别已压缩产物，防止二次重复压缩 |
| 📊 **实时进度条** | 跨线程 Qt Signal 安全通信，精准渲染单文件压缩进度 |
| 🔄 **自动更新** | 启动时静默检查 GitHub Release 新版本，一键下载更新 |

---

## 🛡️ Syncthing / 边录边传 4 重安全防护

本工具专门针对 **Syncthing 跨机同步** 和 **OBS/录屏软件边录边传** 场景设计了 4 层安全防护，确保不会误压正在传输或正在录制的文件：

```
第 1 层：临时文件名过滤
  └─ 自动跳过 .syncthing.、.tmp、.part、!syncthing 等中间文件

第 2 层：文件句柄独占测试
  └─ 尝试以 r+b 模式打开文件，如果被占用则跳过

第 3 层：mtime 修改时间冷却期（默认 180 秒）
  └─ 文件最后修改时间距今不足冷却期的，不处理

第 4 层：ffprobe 容器完整性探针
  └─ 深度校验 MKV/MP4 的 EBML/Cluster 结构，确保文件完整
```

---

## 📦 下载与安装

### 方式一：直接下载可执行文件（推荐）

1. 前往 [Releases 页面](https://github.com/giuiu9527/auto-video-compressor/releases/latest) 下载最新版 ZIP
2. 解压到任意目录
3. 双击运行 `AutoVideoCompressor.exe`

> ⚠️ 已内置 `ffmpeg.exe` 和 `ffprobe.exe`，无需额外安装 FFmpeg

### 方式二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/giuiu9527/auto-video-compressor.git
cd auto-video-compressor

# 安装依赖
pip install PySide6

# 将 ffmpeg.exe 和 ffprobe.exe 放入 bin/ 目录

# 启动程序
python main.py
```

---

## 🚀 使用说明

1. **启动程序** → 双击 `AutoVideoCompressor.exe` 或 `python main.py`
2. **选择监控目录** → 点击「📂 选择目录」按钮，选择要监控的视频根目录
3. **配置参数**（可选）→ 调整编码模式、质量、冷却时间等参数
4. **开始监控** → 点击「▶ 开始自动监控与压缩」
5. **放着不管** → 程序会自动循环扫描、发现新视频、排队压缩

### 常用操作

| 操作 | 说明 |
|------|------|
| **右键表格行** | 显示菜单：⚡ 强制立即压缩 / 📂 打开所在文件夹 |
| **调整冷却时间** | 界面上的「冷却等待秒数」滑块，默认 180 秒 |
| **切换编码器** | 支持 `h264_nvenc`（GPU加速）和 `libx264`（CPU软编码） |
| **尾部裁剪** | 勾选「尾部裁剪」并设置秒数，自动去除录制末尾多余画面 |

---

## 📁 项目结构

```
auto-video-compressor/
├── main.py                    # 🚀 程序启动入口（含崩溃日志捕获）
├── main_window.py             # 🖥️ PySide6 主窗口 & 多线程调度控制器
├── compressor.py              # 🎬 FFmpeg 编码引擎（NVENC / CPU 回退）
├── scanner.py                 # 🔍 文件夹巡检 & 4 重防冲突状态判定
├── utils.py                   # 🛠️ 工具库（探针校验、锁检测、命令运行）
├── config.py                  # ⚙️ 版本号、路径常量、配置数据类
├── updater.py                 # 🔄 GitHub Release 自动更新检测器
├── styles.py                  # 🎨 Modern Dark 主题 QSS 样式表
├── icon.ico                   # 📎 应用程序图标
├── AutoVideoCompressor.spec   # 📦 PyInstaller 打包配置
├── bin/                       # 📂 内置 ffmpeg.exe & ffprobe.exe
├── AI_NOTES.md                # 🤖 AI 开发记忆日志（架构与坑点记录）
└── README.md                  # 📖 本文档
```

---

## 🔧 开发指南

### 环境要求

- Python 3.10+
- PySide6 (Qt 6)
- FFmpeg（放入 `bin/` 目录或系统 PATH）
- Windows 10 / 11

### 打包为可执行文件

```bash
# 安装打包工具
pip install pyinstaller

# 使用项目 spec 文件打包
pyinstaller AutoVideoCompressor.spec

# 输出位于 dist/AutoVideoCompressor/AutoVideoCompressor.exe
```

> ⚠️ **重要**：可执行文件名必须为英文（`AutoVideoCompressor.exe`），中文文件名会导致 PyInstaller C Bootloader 路径编码故障

### 打包注意事项

- 使用 `collect_all('shiboken6')` 确保 PySide6 C++ 绑定二进制模块完整
- 已排除 QtWebEngine、Qt3D、QtQml 等未使用的重型模块（节省约 1GB 体积）
- 内置 `ffmpeg.exe` 和 `ffprobe.exe` 通过 `bin/` 目录随包分发

---

## 🔄 自动更新机制

程序启动时会静默检查 GitHub Release：
1. 对比远端最新 Release Tag 与本地 `APP_VERSION`
2. 发现新版本后弹出更新对话框，展示更新日志
3. 确认后自动下载 ZIP 并通过批处理脚本覆盖更新、重启

更新 API 地址：`https://api.github.com/repos/giuiu9527/auto-video-compressor/releases/latest`

---

## 📝 更新日志

### v1.0.0 (2026-07-26)
- 🎉 首个正式发布版本
- ✅ 4 重 Syncthing 防冲突机制
- ✅ NVIDIA NVENC 硬件加速 + CPU 软编码回退
- ✅ 实时冷却倒计时显示
- ✅ 右键菜单：强制压缩 / 打开文件夹
- ✅ 智能尾部裁剪功能
- ✅ GitHub Release 自动更新检测
- ✅ Modern Dark 主题 UI
- ✅ PyInstaller 单目录打包（含内置 FFmpeg）

---

## 📄 开源许可

[MIT License](LICENSE)

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/giuiu9527">giuiu9527</a>
</p>
