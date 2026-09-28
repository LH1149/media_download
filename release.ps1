<#
.SYNOPSIS
    MediaDownloader 一键发布脚本：提交代码 -> 打包 exe -> 打 tag -> 推送 -> 创建 GitHub Release 并上传附件

.DESCRIPTION
    使用方法：
      1. 先完成代码修改与测试
      2. 在项目根目录运行，例如：
           .\release.ps1 -Version v1.1 -Notes "修复下载失败问题"
      3. 如无需重新打包（dist\MediaDownloader.exe 已是最新）：
           .\release.ps1 -Version v1.1 -Notes "..." -SkipBuild

    前置条件：
      - 已安装 git 且能推送（凭据已缓存，即 git push 可成功）
      - 已安装 Python 依赖：yt-dlp pyinstaller imageio-ffmpeg
      - 远程仓库 origin 已配置

.PARAMETER Version
    版本号（同时作为 git tag），例如 v1.1

.PARAMETER Notes
    Release 发布说明（纯文本，支持简单换行）

.PARAMETER SkipBuild
    跳过 PyInstaller 打包，直接使用现有 dist\MediaDownloader.exe

.PARAMETER Prerelease
    标记为预发布版本
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [string]$Notes,

    [switch]$SkipBuild,
    [switch]$Prerelease
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

# 统一找 git（PATH 里没有时用常见安装路径）
function Find-Git {
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in "C:\Program Files\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe") {
        if (Test-Path $p) { return $p }
    }
    throw "未找到 git，请先安装 Git for Windows"
}
$git = Find-Git

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    & $git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') 失败 (exit $LASTEXITCODE)" }
}

# 版本号简单校验
if ($Version -notmatch '^v?\d+\.\d+(\.\d+)?([-+].+)?$') {
    throw "版本号格式不正确，示例：v1.1 或 v1.1.0"
}
if (-not $Version.StartsWith('v')) { $Version = "v$Version" }

Write-Host "=== 发布 $Version ===" -ForegroundColor Cyan

# ---------- 0. 确认在 git 仓库中且有远程 ----------
Invoke-Git rev-parse --is-inside-work-tree | Out-Null
$remote = (& $git remote get-url origin 2>$null)
if (-not $remote) { throw "未配置 origin 远程仓库" }
Write-Host "远程仓库：$remote"

# ---------- 1. 打包 exe ----------
$exePath = Join-Path $repoRoot "dist\MediaDownloader.exe"
if (-not $SkipBuild) {
    Write-Host "`n[1/5] 打包 exe ..." -ForegroundColor Yellow
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $py) { throw "未找到 python，请先安装 Python 3.10+" }

    # 确保 bin\ffmpeg.exe 存在（从 imageio-ffmpeg 复制）
    if (-not (Test-Path "bin\ffmpeg.exe")) {
        Write-Host "准备 ffmpeg.exe ..."
        New-Item -ItemType Directory -Force -Path "bin" | Out-Null
        & $py -c "import imageio_ffmpeg, shutil; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), r'bin\ffmpeg.exe')"
        if ($LASTEXITCODE -ne 0) { throw "ffmpeg 准备失败，请先 pip install imageio-ffmpeg" }
    }

    & $py -m PyInstaller --noconfirm --onefile --windowed --name MediaDownloader `
        --add-binary "bin\ffmpeg.exe;." --collect-all yt_dlp media_downloader.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
} else {
    Write-Host "`n[1/5] 跳过打包（-SkipBuild）" -ForegroundColor DarkGray
}
if (-not (Test-Path $exePath)) { throw "未找到 $exePath" }

# ---------- 2. 提交未保存的改动 ----------
Write-Host "`n[2/5] 检查待提交改动 ..." -ForegroundColor Yellow
$status = (& $git status --porcelain)
if ($status) {
    Write-Host "发现未提交的文件："
    $status | ForEach-Object { Write-Host "  $_" }
    Invoke-Git add -A

    # 若本机未配置 git 身份，使用默认身份提交
    $identArgs = @()
    if (-not (& $git config user.name)) {
        Write-Host "未配置 git 身份，使用 LH1149 默认身份提交"
        $identArgs = @("-c", "user.name=LH1149", "-c", "user.email=LH1149@users.noreply.github.com")
    }
    Invoke-Git @identArgs commit -m "release: $Version"
    Write-Host "已提交"
} else {
    Write-Host "工作区干净，无需提交"
}

# ---------- 3. 打 tag 并推送 ----------
Write-Host "`n[3/5] 推送代码与 tag ..." -ForegroundColor Yellow
Invoke-Git push origin main

$existingTag = (& $git tag -l $Version)
if ($existingTag) {
    Write-Host "tag $Version 已存在（本地）"
} else {
    Invoke-Git tag -a $Version -m "Release $Version"
}

# 带网络重试的推送（GitHub 连接偶尔抖动）
$pushed = $false
for ($i = 1; $i -le 3 -and -not $pushed; $i++) {
    & $git push origin $Version 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -eq 0) { $pushed = $true } else { Start-Sleep -Seconds 5 }
}
if (-not $pushed) { throw "tag 推送失败，请检查网络后重试 git push origin $Version" }

# ---------- 4. 获取凭据并创建 Release ----------
Write-Host "`n[4/5] 创建 GitHub Release ..." -ForegroundColor Yellow
$cred = ("protocol=https`nhost=github.com`n`n" | & $git credential fill)
$token = ($cred | Where-Object { $_ -like "password=*" }) -replace "^password=", ""
if (-not $token) { throw "未能从 git 凭据管理器获取 GitHub 令牌，请先成功执行一次 git push" }

# 从 remote URL 解析 owner/repo
$remotePath = $remote -replace '^https://github.com/', '' -replace '\.git$', '' -replace '^git@github.com:', ''
$owner, $repo = $remotePath -split '/', 2
if (-not $repo) { throw "无法从远程地址解析仓库名：$remote" }

$headers = @{
    "Authorization"        = "Bearer $token"
    "Accept"               = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$releaseBody = @{
    tag_name   = $Version
    name       = "MediaDownloader $Version"
    body       = $Notes
    draft      = $false
    prerelease = [bool]$Prerelease
} | ConvertTo-Json -Depth 5

$bodyFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($bodyFile, $releaseBody, (New-Object System.Text.UTF8Encoding($false)))

$resp = $null
try {
    $resp = Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$repo/releases" `
        -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -InFile $bodyFile
} catch {
    $errText = ""
    if ($_.ErrorDetails) { $errText = $_.ErrorDetails.Message }
    throw "Release 创建失败：$($_.Exception.Message) $errText"
} finally {
    Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
}
$releaseId = $resp.id
Write-Host "Release 已创建：$($resp.html_url)"

# ---------- 5. 上传 exe 附件 ----------
Write-Host "`n[5/5] 上传 MediaDownloader.exe ..." -ForegroundColor Yellow
$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host "文件大小：$sizeMB MB"

$uploadUrl = "https://uploads.github.com/repos/$owner/$repo/releases/$releaseId/assets?name=MediaDownloader.exe"
$uploadOk = $false
for ($i = 1; $i -le 3 -and -not $uploadOk; $i++) {
    $assetResp = curl.exe -sS --retry 3 -X POST `
        -H "Authorization: Bearer $token" `
        -H "Accept: application/vnd.github+json" `
        -H "Content-Type: application/octet-stream" `
        --data-binary "@$exePath" $uploadUrl
    if ($assetResp -match '"state"\s*:\s*"uploaded"') {
        $uploadOk = $true
    } else {
        Write-Host "上传尝试 $i 未成功：$assetResp"
        Start-Sleep -Seconds 5
    }
}
if (-not $uploadOk) {
    throw "附件上传失败。Release 已存在，可手动到 $($resp.html_url) 上传 $exePath"
}

Write-Host "`n=== 发布完成 ===" -ForegroundColor Green
Write-Host "Release：$($resp.html_url)"
Write-Host "下载直链：https://github.com/$owner/$repo/releases/download/$Version/MediaDownloader.exe"
