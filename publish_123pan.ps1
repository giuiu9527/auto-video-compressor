# 发布到 123 云盘更新通道（规范：D:\Test Code\软件包更新方式.md）。
# 薄壳：打 portable 包（一层目录，目录名=包名），再交给通用脚本上传、生成 .sha256、最后写 update.json。
# 用法：publish_123pan.ps1 -Notes "更新说明" [-New]   （-New 仅第一次发布本软件时使用，允许建目录）
[CmdletBinding()]
param([string]$Notes = "", [switch]$New)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$App = 'IMM-Compressor'   # 网盘目录名，写死
$tool = 'D:\Test Code\123pan-更新工具\publish_123pan.js'

$version = [regex]::Match((Get-Content config.py -Raw -Encoding UTF8), 'APP_VERSION\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $version) { throw "读不到 config.py 里的 APP_VERSION" }
$pkg = "$App-v$version-portable"
$stage = Join-Path $PSScriptRoot "dist\_stage"
$zip = Join-Path $PSScriptRoot "dist\$pkg.zip"
if (-not (Test-Path "dist\$App\$App.exe")) { throw "找不到 dist\$App，请先打包" }
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path "$stage\$pkg" | Out-Null
Copy-Item "dist\$App\*" "$stage\$pkg" -Recurse -Force
Remove-Item "$stage\$pkg\update.log", "$stage\$pkg\watch_compress_settings.json", "$stage\$pkg\compress_templates.json" -ErrorAction SilentlyContinue
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\$pkg" -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force
if (-not $Notes) { $Notes = (git log -1 --pretty=%s) }

$nodeArgs = @($tool, '--app', $App, '--file', $zip, '--version', $version, '--notes', $Notes)
if ($New) { $nodeArgs += '--new' }
& node @args
if ($LASTEXITCODE -ne 0) { throw "发布到 123 云盘失败（先跑: node `"$tool`" --app $App --check）" }
