# heropen 一键安装脚本 (Windows PowerShell)
# 用法: irm ksmn.cc/heropen/install.ps1 | iex
# 如果遇到执行策略限制，运行: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "◇ heropen · Agent记忆系统" -ForegroundColor Cyan
Write-Host "  一行安装，开箱即用" -ForegroundColor DarkGray
Write-Host ("━" * 52)
Write-Host ""

# ── Step 1: 检查 Python ──
Write-Host "→ 检查 Python 环境..." -ForegroundColor Cyan

$python = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -and [int]($ver.Split('.')[0]) -ge 3) {
            $python = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
            Write-Host "✓ Python $ver ($python)" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "⚠ 未检测到 Python 3，正在自动安装..." -ForegroundColor Yellow

    # 尝试 winget
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "→ 通过 winget 安装 Python 3.12..." -ForegroundColor Cyan
        winget install Python.Python.3.12 --silent --accept-package-agreements
        # 刷新 PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    } else {
        Write-Host "✗ 未找到 winget，请手动安装 Python：https://python.org/downloads/" -ForegroundColor Red
        Write-Host "  安装时务必勾选 'Add Python to PATH'" -ForegroundColor Yellow
        exit 1
    }

    # 重新检测
    foreach ($cmd in @("python3", "python")) {
        try {
            $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver -and [int]($ver.Split('.')[0]) -ge 3) {
                $python = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
                break
            }
        } catch {}
    }

    if (-not $python) {
        Write-Host "✗ Python 安装后仍无法检测到。" -ForegroundColor Red
        Write-Host "  请关闭 PowerShell 后重新打开，再运行此脚本。" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "✓ Python 安装完成" -ForegroundColor Green
}

# ── Step 2: 确认 pip ──
try {
    & $python -m pip --version 2>$null | Out-Null
} catch {
    Write-Host "→ 安装 pip..." -ForegroundColor Cyan
    & $python -m ensurepip --upgrade 2>$null
}

# ── Step 3: 安装 heropen ──
Write-Host "→ 安装 heropen..." -ForegroundColor Cyan
try {
    & $python -m pip install heropen --upgrade
    Write-Host "✓ heropen 安装完成" -ForegroundColor Green
} catch {
    Write-Host "✗ heropen 安装失败，请检查网络连接后重试" -ForegroundColor Red
    exit 1
}

# ── Step 4: 启动 Viewer ──
Write-Host ""
Write-Host ("━" * 52)
Write-Host "→ 启动 Viewer..." -ForegroundColor Cyan

$viewerStarted = $false

# 优先用 heropen viewer 命令
try {
    $null = & $python -m heropen viewer --help 2>&1
    $viewerJob = Start-Job -ScriptBlock {
        param($py)
        & $py -m heropen viewer
    } -ArgumentList $python
    $viewerStarted = $true
} catch {}

if (-not $viewerStarted) {
    # 回退：找包内的 server.py
    try {
        $serverScript = & $python -c "import heropen,os; d=os.path.dirname(heropen.__file__); s=os.path.join(d,'viewer','server.py'); print(s if os.path.exists(s) else '')" 2>$null
        if ($serverScript) {
            $viewerJob = Start-Job -ScriptBlock {
                param($py, $script)
                & $py $script
            } -ArgumentList $python, $serverScript
            $viewerStarted = $true
        }
    } catch {}
}

# 等 Viewer 就绪（最多 5 秒）
$viewerReady = $false
if ($viewerStarted) {
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:9020/api/health" -UseBasicParsing -TimeoutSec 1
            $viewerReady = $true
            break
        } catch {}
        Start-Sleep -Milliseconds 500
    }
}

Write-Host ""
Write-Host ("━" * 52)
Write-Host ""
Write-Host "✨ 安装完成！" -ForegroundColor Green
Write-Host ""
if ($viewerReady) {
    Write-Host "  Viewer:    " -NoNewline
    Write-Host "http://127.0.0.1:9020" -ForegroundColor Cyan -NoNewline
    Write-Host "  (已启动)" -ForegroundColor Green
    Write-Host "  首次使用会在面板中完成 AI 助手配置" -ForegroundColor DarkGray
} else {
    Write-Host "  启动 Viewer:  " -NoNewline
    Write-Host "heropen viewer" -ForegroundColor Cyan
}
Write-Host "  升级 Plus:     " -NoNewline
Write-Host "https://ksmn.cc/heropen/" -ForegroundColor Cyan
Write-Host ""

# 自动打开浏览器
if ($viewerReady) {
    Start-Sleep -Milliseconds 500
    try {
        Start-Process "http://127.0.0.1:9020"
        Write-Host "✓ 已在浏览器中打开 Viewer" -ForegroundColor Green
    } catch {}
}

Write-Host ("━" * 52)
