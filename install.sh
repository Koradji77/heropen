#!/usr/bin/env bash
# HeroPen 一键安装脚本 (macOS / Linux)
# 用法: curl -sSL ksmn.cc/heropen/install.sh | bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

say()  { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
info() { printf "${CYAN}→${NC} %s\n" "$*"; }

echo ""
printf "${BOLD}${CYAN}◇ HeroPen · AI 记忆系统${NC}\n"
printf "  一行安装，开箱即用\n\n"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: 检查 Python ──
info "检查 Python 环境..."

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
        major=$(echo "$ver" | cut -d. -f1)
        if [ "$major" -ge 3 ]; then
            PYTHON="$cmd"
            say "Python $ver ($(command -v "$PYTHON"))"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "未检测到 Python 3，正在自动安装..."
    OS="$(uname -s)"
    if [ "$OS" = "Darwin" ]; then
        if command -v brew &>/dev/null; then
            brew install python@3.12 2>/dev/null || brew install python3
        else
            echo " 请先安装 Python: https://python.org/downloads/"
            exit 1
        fi
    elif [ -f /etc/debian_version ]; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip
    elif [ -f /etc/fedora-release ]; then
        sudo dnf install -y python3 python3-pip
    elif [ -f /etc/arch-release ]; then
        sudo pacman -S --noconfirm python python-pip
    else
        echo " 请手动安装 Python 3.9+ : https://python.org/downloads/"
        exit 1
    fi
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
            major=$(echo "$ver" | cut -d. -f1)
            if [ "$major" -ge 3 ]; then PYTHON="$cmd"; break; fi
        fi
    done
    [ -n "$PYTHON" ] || { echo " Python 安装失败"; exit 1; }
    say "Python 安装完成"
fi

# ── Step 2: 确认 pip ──
if ! "$PYTHON" -m pip --version &>/dev/null; then
    info "安装 pip..."
    "$PYTHON" -m ensurepip --upgrade 2>/dev/null || true
fi

# ── Step 3: 安装 HeroPen ──
info "安装 HeroPen..."
if "$PYTHON" -m pip install heropen --upgrade 2>&1; then
    say "HeroPen 安装完成"
else
    echo " HeroPen 安装失败，请检查网络连接后重试"
    exit 1
fi

# ── Step 4: 启动 Viewer ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "启动 Web Viewer..."

# Launch heropen viewer in background
VIEWER_PID=""
if command -v heropen &>/dev/null; then
    nohup heropen viewer >/dev/null 2>&1 &
    VIEWER_PID=$!
elif "$PYTHON" -m heropen viewer --help &>/dev/null 2>&1; then
    nohup "$PYTHON" -m heropen viewer >/dev/null 2>&1 &
    VIEWER_PID=$!
else
    # Fallback: direct module launch
    nohup "$PYTHON" -c "
import heropen.viewer_server
heropen.viewer_server.main()
" >/dev/null 2>&1 &
    VIEWER_PID=$!
fi

# Wait for viewer readiness (up to 5 seconds)
VIEWER_READY=false
for i in $(seq 1 10); do
    if curl -s http://127.0.0.1:9020/api/health >/dev/null 2>&1; then
        VIEWER_READY=true
        break
    fi
    sleep 0.5
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "${BOLD}${GREEN}✨ 安装完成！${NC}\n"
echo ""
if [ "$VIEWER_READY" = true ]; then
    printf "  Viewer:    ${CYAN}http://127.0.0.1:9020${NC}  ${GREEN}(已启动)${NC}\n"
    say "浏览器已自动打开"
    # Auto-open browser (viewer command already does this, but as fallback:)
    if command -v open &>/dev/null; then
        open "http://127.0.0.1:9020" 2>/dev/null || true
    elif command -v xdg-open &>/dev/null; then
        xdg-open "http://127.0.0.1:9020" 2>/dev/null || true
    fi
else
    printf "  启动 Viewer: ${CYAN}heropen viewer${NC}\n"
fi
printf "  升级 Plus: ${CYAN}https://ksmn.cc/heropen/${NC}\n"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
