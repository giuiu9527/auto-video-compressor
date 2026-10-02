# 交互式一键发布：询问版本号与更新说明 -> 改 config.py -> 提交 -> 打包 -> 推送 -> 创建 GitHub Release
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot

$cfgText = Get-Content config.py -Raw -Encoding UTF8
$cur = [regex]::Match($cfgText, 'APP_VERSION\s*=\s*"([^"]+)"').Groups[1].Value
$p = $cur.Split(".")
$suggest = "{0}.{1}.{2}" -f $p[0], $p[1], ([int]$p[2] + 1)

Write-Host "当前版本: v$cur" -ForegroundColor Cyan
$ver = Read-Host "新版本号 (直接回车 = $suggest)"
if (-not $ver) { $ver = $suggest }
$ver = $ver.Trim().TrimStart("vV")
if ($ver -notmatch '^\d+\.\d+\.\d+$') { throw "版本号格式应为 x.y.z" }
$tag = "v$ver"

Write-Host "`n请输入更新说明，可多行；输入空行结束 (直接空行 = 使用最近一次提交标题):" -ForegroundColor Cyan
$lines = @()
while ($true) {
    $l = Read-Host ">"
    if (-not $l) { break }
    $lines += $l
}
$notes = if ($lines) { $lines -join "`n" } else { (git log -1 --pretty=%s) }

$commitMsg = Read-Host "`n提交信息 (直接回车 = release: $tag)"
if (-not $commitMsg) { $commitMsg = "release: $tag" }

Write-Host "`n========== 即将执行 ==========" -ForegroundColor Yellow
Write-Host "版本:     v$cur -> $tag"
Write-Host "提交信息: $commitMsg"
Write-Host "更新说明:`n$notes"
Write-Host "步骤: 改版本号 > 提交全部改动 > 打包 > 推送 > 创建 Release"
if ((Read-Host "确认发布? (y/N)") -notin @("y", "Y")) { Write-Host "已取消"; exit 0 }

if ($ver -ne $cur) {
    $new = $cfgText -replace '(APP_VERSION\s*=\s*")[^"]+(")', "`${1}$ver`${2}"
    [System.IO.File]::WriteAllText("$PSScriptRoot\config.py", $new, (New-Object System.Text.UTF8Encoding($false)))
}

git add -A
if (git status --porcelain) {
    git commit -m $commitMsg
    if ($LASTEXITCODE -ne 0) { throw "git commit 失败" }
}

Write-Host "==> 打包 $tag" -ForegroundColor Cyan
python -m PyInstaller AutoVideoCompressor.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$zip = Join-Path $PSScriptRoot "dist\IMM-Compressor-$tag-win64.zip"
Write-Host "==> 生成 ZIP" -ForegroundColor Cyan
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\IMM-Compressor\*" -DestinationPath $zip -CompressionLevel Optimal

Write-Host "==> 推送并发布 Release" -ForegroundColor Cyan
git push origin master
if ($LASTEXITCODE -ne 0) { throw "git push 失败" }
gh release create $tag $zip --title $tag --notes $notes
if ($LASTEXITCODE -ne 0) { throw "创建 Release 失败" }
Write-Host "`n完成: $tag" -ForegroundColor Green
