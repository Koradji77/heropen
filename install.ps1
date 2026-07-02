# HeroPen 一键安装脚本 (Windows PowerShell)
# 用法: irm ksmn.cc/heropen/install.ps1 | iex
# 如果执行策略限制: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

$ErrorActionPreference = "Stop"

# Force UTF-8 for Chinese console
$codepage = chcp 65001 2>$null | Out-Null

Write-Host ""
Write-Host "[ HeroPen - AI 记忆系统 ]" -ForegroundColor Cyan
Write-Host "  一行安装，开箱即用" -ForegroundColor DarkGray
Write-Host ("-" * 55)
Write-Host ""

# -- Step 1: Check Python --
Write-Host ">> 检查 Python 环境..." -ForegroundColor Cyan

$python = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -and [int]($ver.Split('.')[0]) -ge 3) {
            $python = $cmd
            Write-Host "[OK] Python $ver" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "[!] Python 3 未安装，请先安装:" -ForegroundColor Yellow
    Write-Host "    https://python.org/downloads/" -ForegroundColor Yellow
    Write-Host "    安装时记得勾选 'Add Python to PATH'" -ForegroundColor Yellow
    exit 1
}

# -- Step 2: Install/upgrade HeroPen --
Write-Host ">> 安装 HeroPen..." -ForegroundColor Cyan
try {
    & $python -m pip install heropen --upgrade -q
    Write-Host "[OK] HeroPen 安装完成" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] 安装失败，请检查网络" -ForegroundColor Red
    exit 1
}

# -- Step 3: Launch Viewer --
Write-Host ""
Write-Host ("-" * 55)
Write-Host ">> 启动 Web Viewer..." -ForegroundColor Cyan

# Try heropen viewer command first
$viewerProcess = $null
try {
    $viewerProcess = Start-Process -FilePath "heropen" -ArgumentList "viewer" -WindowStyle Hidden -PassThru
} catch {
    try {
        $viewerProcess = Start-Process -FilePath "python" -ArgumentList "-m heropen viewer" -WindowStyle Hidden -PassThru
    } catch {
        try {
            $viewerProcess = Start-Process -FilePath "python" -ArgumentList "-c `"import heropen.viewer_server; heropen.viewer_server.main()`"" -WindowStyle Hidden -PassThru
        } catch {}
    }
}

$viewerReady = $false
if ($viewerProcess) {
    Start-Sleep -Seconds 2
    for ($i = 0; $i -lt 12; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:9020/api/health" -ErrorAction Stop
            $viewerReady = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
}

Write-Host ""
Write-Host ("-" * 55)
Write-Host "✨ 安装完成！" -ForegroundColor Green
Write-Host ""

if ($viewerReady) {
    Write-Host "  Viewer: http://127.0.0.1:9020  (已启动)" -ForegroundColor Cyan
    Start-Process "http://127.0.0.1:9020"
    Write-Host "[OK] 浏览器已自动打开" -ForegroundColor Green
} else {
    Write-Host "  启动 Viewer: heropen viewer" -ForegroundColor Cyan
}
Write-Host "  升级 Plus: https://ksmn.cc/heropen/" -ForegroundColor Gray
Write-Host ""
Write-Host ("-" * 55)
