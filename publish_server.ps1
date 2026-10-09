# 发布到自建 OpenList 更新服务器（更新方式 2）。规范：D:\Test Code\软件包更新方式.md
# 用法：publish_server.ps1 [-Notes "说明"] [-PrepareOnly]
#   -PrepareOnly  只登录、建好 /IMM-Compressor 目录并确认，不上传（新软件第一步，之后手动开公开分享）
[CmdletBinding()]
param([string]$Notes = "", [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

foreach ($name in 'OPENLIST_BASE_URL', 'OPENLIST_USERNAME', 'OPENLIST_PASSWORD') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { throw "缺少环境变量: $name" }
}
$baseUrl  = [Environment]::GetEnvironmentVariable('OPENLIST_BASE_URL').TrimEnd('/')
$username = [Environment]::GetEnvironmentVariable('OPENLIST_USERNAME')
$password = [Environment]::GetEnvironmentVariable('OPENLIST_PASSWORD')

$App        = 'IMM-Compressor'   # 写死，不读环境变量
$remotePath = "/$App"
$channel    = '/p/%E8%B0%B7%E6%AD%8C%E4%BA%91%E7%9B%98/%E8%BD%AF%E4%BB%B6%E6%94%B6%E9%9B%86/myAPP/IMM-Compressor/update.json?sign=_f0sfNLHiwEmgH2P3bQo6rvRdYo-d7sbReU1kICfQEQ=:0'  # 签名直链模式(规范 7.3)：新目录公开分享会 500，改用 /p/...?sign=... 直链；需与 server_updater.py 的 DEFAULT_CHANNEL 一致

# --- 目录自检（规范第 3.3 节）---
if ($remotePath -notmatch '^/[^/]+$')              { throw "远端目录必须是一层: $remotePath" }
if ($remotePath -match '谷歌云盘|软件收集|myAPP')   { throw "远端目录不能含 base_path 片段: $remotePath" }
$others = '/IMM-Touping-Next','/Pan-Auto','/TFF-Card-Refill','/深造机型修改','/DeviceSpoofer-Scope','/IMM-PDD-AutoReply'
if ($others -contains $remotePath)                 { throw "这是别的软件的目录: $remotePath" }

$login = Invoke-RestMethod -Uri "$baseUrl/api/auth/login" -Method Post -ContentType 'application/json' `
    -Body (@{ username = $username; password = $password } | ConvertTo-Json -Compress)
if ($login.code -ne 200 -or [string]::IsNullOrWhiteSpace($login.data.token)) { throw "OpenList 登录失败: $($login.message)" }
$token = $login.data.token
$hdr = @{ Authorization = $token }

function Get-RootNames {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/fs/list" -Method Post -Headers $hdr -ContentType 'application/json' `
        -Body (@{ path = '/'; refresh = $true; page = 1; per_page = 0 } | ConvertTo-Json -Compress)
    return @($r.data.content | ForEach-Object { $_.name })
}
if ((Get-RootNames) -notcontains $App) {
    $m = Invoke-RestMethod -Uri "$baseUrl/api/fs/mkdir" -Method Post -Headers $hdr -ContentType 'application/json' `
        -Body (@{ path = $remotePath } | ConvertTo-Json -Compress)
    if ($m.code -ne 200) { throw "建目录失败: $($m.message)" }
    if ((Get-RootNames) -notcontains $App) { throw "目录 $remotePath 创建后仍未出现在根目录列表" }
    Write-Host "已创建远端目录 $remotePath"
} else {
    Write-Host "远端目录 $remotePath 已存在"
}
if ($PrepareOnly) { Write-Host "准备完成。请在 OpenList 里为 $remotePath 开公开分享，把 /sd/xxxx/ 链接路径告诉我/填入脚本。"; return }
if ([string]::IsNullOrWhiteSpace($channel)) { throw "尚未填写 `$channel（公开分享路径），请先开好分享并填入" }

# --- 版本与打包（压缩包内是一层目录，目录名=包名）---
$version = [regex]::Match((Get-Content config.py -Raw -Encoding UTF8), 'APP_VERSION\s*=\s*"([^"]+)"').Groups[1].Value
$pkg = "$App-v$version-portable"
$stage = Join-Path $PSScriptRoot "dist\_stage"
$zip = Join-Path $PSScriptRoot "dist\$pkg.zip"
if (-not (Test-Path "dist\$App\$App.exe")) { throw "找不到 dist\$App，请先打包" }
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path "$stage\$pkg" | Out-Null
Copy-Item "dist\$App\*" "$stage\$pkg" -Recurse -Force
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\$pkg" -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$sha = "$zip.sha256"
[System.IO.File]::WriteAllText($sha, "$hash  $pkg.zip", [System.Text.Encoding]::ASCII)

function Send-File([string]$localFile, [string]$contentType = 'application/octet-stream') {
    $remoteFile = "$remotePath/$(Split-Path $localFile -Leaf)"
    for ($i = 1; $i -le 2; $i++) {
        $resp = & curl.exe -sS -X PUT -H "Authorization: $token" -H "File-Path: $([uri]::EscapeDataString($remoteFile))" `
            -H "Content-Type: $contentType" --upload-file $localFile "$baseUrl/api/fs/put"
        $res = $resp | ConvertFrom-Json
        if ($res.code -eq 200) { return }
        Write-Host "上传 $remoteFile 失败($i/2): $resp"
    }
    throw "上传失败: $remoteFile"
}

# 签名直链：返回 /p/... 路径（去掉服务器地址）；刷新目录缓存后重试
function Get-SignedPath([string]$remoteFile) {
    for ($i = 1; $i -le 20; $i++) {
        $r = Invoke-RestMethod -Uri "$baseUrl/api/fs/get" -Method Post -Headers $hdr -ContentType 'application/json' `
            -Body (@{ path = $remoteFile } | ConvertTo-Json -Compress)
        if ($r.code -eq 200 -and $r.data.raw_url) {
            $url = $r.data.raw_url
            $code = & curl.exe -sS -r 0-0 -o NUL -w '%{http_code}' --max-time 60 $url
            if ($code -eq '200' -or $code -eq '206') { return ([uri]$url).PathAndQuery }
            Write-Host "直链暂不可下(HTTP $code)，$i/20"
        } else {
            Write-Host "fs/get 失败: $($r.message)，$i/20"
            $null = Invoke-RestMethod -Uri "$baseUrl/api/fs/list" -Method Post -Headers $hdr -ContentType 'application/json' `
                -Body (@{ path = $remotePath; refresh = $true; page = 1; per_page = 0 } | ConvertTo-Json -Compress)
        }
        Start-Sleep -Seconds 10
    }
    throw "拿不到可用的签名直链: $remoteFile"
}

# 顺序：包 -> sha256 -> 取直链(确认可下) -> 最后写清单
Send-File $zip
Send-File $sha
$archivePath  = Get-SignedPath "$remotePath/$pkg.zip"
$checksumPath = Get-SignedPath "$remotePath/$pkg.zip.sha256"

if (-not $Notes) { $Notes = "$App $version" }
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) "update.json"
$manifest = [ordered]@{
    version = $version
    archive_path = $archivePath
    checksum_path = $checksumPath
    notes = $Notes
    published_at = (Get-Date).ToUniversalTime().ToString('o')
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($manifestPath, $manifest, [System.Text.UTF8Encoding]::new($false))
try { Send-File $manifestPath 'application/json' } finally { Remove-Item $manifestPath -Force -ErrorAction SilentlyContinue }

# --- 发布后自验 ---
$manifestUrl = $baseUrl + $channel
$remote = Invoke-RestMethod -Uri $manifestUrl
Write-Host "清单版本: $($remote.version)"
$code = & curl.exe -sS -r 0-0 -o NUL -w '%{http_code}' ($baseUrl + $remote.archive_path)
Write-Host "包 HTTP: $code (必须 200/206)"
$shaRemote = (& curl.exe -sS ($baseUrl + $remote.checksum_path)).Split(' ')[0]
Write-Host "本地 SHA256: $hash"
Write-Host "远端 SHA256: $shaRemote"
if ($shaRemote -ne $hash) { throw "SHA256 不一致" }
Write-Host "服务器更新已发布: $pkg"

# --- 归档旧版本（规范 3.4）：自验通过后，把根目录里不是本次版本的 zip/.sha256 移到 历史版本/ ---
# 放最后、失败只警告：归档出错不该让一次已经成功的发布报失败。只移动，不删除。
try {
    $Hist = -join ([char[]](0x5386,0x53F2,0x7248,0x672C))   # 历史版本
    $histPath = "$remotePath/$Hist"
    function Invoke-OpenListJson([string]$api, $obj) {
        # 5.1 直接传字符串会把中文发成 ?,要转 UTF-8 字节
        $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 5))
        Invoke-RestMethod -Uri "$baseUrl$api" -Method Post -Headers $hdr -ContentType 'application/json; charset=utf-8' -Body $bytes
    }
    $null = Invoke-OpenListJson '/api/fs/mkdir' @{ path = $histPath }
    $listing = Invoke-OpenListJson '/api/fs/list' @{ path = $remotePath; refresh = $true; page = 1; per_page = 0 }
    $inHist = @((Invoke-OpenListJson '/api/fs/list' @{ path = $histPath; refresh = $true; page = 1; per_page = 0 }).data.content | ForEach-Object { $_.name })
    $keep = @("$pkg.zip", "$pkg.zip.sha256")
    $old = @($listing.data.content | Where-Object { -not $_.is_dir -and $_.name -match '^IMM-Compressor-v.*-portable\.zip(\.sha256)?$' -and ($keep -notcontains $_.name) -and ($inHist -notcontains $_.name) } | ForEach-Object { $_.name })
    if ($old.Count) {
        $mv = Invoke-OpenListJson '/api/fs/move' @{ src_dir = $remotePath; dst_dir = $histPath; names = $old }
        if ($mv.code -ne 200) { throw "fs/move: $($mv.message)" }
        $null = Invoke-OpenListJson '/api/fs/list' @{ path = $remotePath; refresh = $true; page = 1; per_page = 1 }
        Write-Host "已归档到 ${Hist}/: $($old -join ', ')"
    } else { Write-Host '没有需要归档的旧版本' }
} catch {
    Write-Warning "发布已成功，但归档旧版本失败: $($_.Exception.Message)（手动在 OpenList 里移动即可）"
}
