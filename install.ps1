<#
.SYNOPSIS
    HeroPen — AI Agent Long-term Memory System 一键安装
.DESCRIPTION
    安装 HeroPen 并自动配置，无需手动操作。
    用法: irm https://ksmn.cc/install.ps1 | iex
#>

$ErrorActionPreference = "Continue"

# 切 UTF-8 编码
chcp 65001 > $null
$host.UI.RawUI.WindowTitle = "HeroPen Install"

Write-Host ""
Write-Host "  *** HeroPen - AI Agent ***" -foreground Cyan
Write-Host "  =================================" -foreground Cyan
Write-Host ""

# 检查 Python
try {
    $ver = & python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "  [OK] $ver" -foreground Green
} catch {
    Write-Host "  [FAIL] 未检测到 Python" -foreground Red
    Write-Host "  请先安装 Python 3.10+：https://www.python.org/downloads/" -foreground Yellow
    pause; exit 1
}

# 清理 pip 残留
$pyDir = Split-Path (Get-Command python).Source
Remove-Item "$pyDir\Lib\site-packages\~*" -Recurse -Force -ErrorAction SilentlyContinue

# 安装 heropen
Write-Host "  正在安装 HeroPen..." -foreground Yellow
& pip install heropen -q 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] pip 安装失败" -foreground Red
    Write-Host "  请手动运行：pip install heropen" -foreground Yellow
    pause; exit 1
}
Write-Host "  [OK] HeroPen 安装成功" -foreground Green

Write-Host ""
Write-Host "  启动配置向导..." -foreground Cyan
Write-Host ""

# 运行配置
Write-Host "  HeroPen 配置完成，请复制下面的文字发给你的 AI 助手`n" -foreground Green
& heropen
