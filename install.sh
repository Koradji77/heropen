#!/usr/bin/env bash
# heropen 一键安装脚本 (macOS / Linux)
# 用法: curl -sSL ksmn.cc/heropen/install.sh | bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

say()  { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*"; exit 1; }
info() { printf "${CYAN}→${NC} %s\n" "$*"; }

echo ""
printf "${BOLD}${CYAN}◇ heropen · Agent记忆系统${NC}\n"
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
        # macOS: 用 Homebrew
        if command -v brew &>/dev/null; then
            brew install python@3.12 2>/dev/null || brew install python3
        else
            err "请先安装 Homebrew: https://brew.sh"
        fi
    elif [ -f /etc/debian_version ]; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip
    elif [ -f /etc/fedora-release ]; then
        sudo dnf install -y python3 python3-pip
    elif [ -f /etc/arch-release ]; then
        sudo pacman -S --noconfirm python python-pip
    else
        err "未能自动安装 Python。请手动安装 Python 3.9+ 后重试：https://python.org/downloads/"
    fi

    # 重新检测
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
            major=$(echo "$ver" | cut -d. -f1)
            if [ "$major" -ge 3 ]; then
                PYTHON="$cmd"
                break
            fi
        fi
    done

    if [ -z "$PYTHON" ]; then
        err "Python 安装后仍无法检测到。请手动检查：https://python.org/downloads/"
    fi
    say "Python 安装完成"
fi

# ── Step 2: 确认 pip ──
if ! "$PYTHON" -m pip --version &>/dev/null; then
    info "安装 pip..."
    "$PYTHON" -m ensurepip --upgrade 2>/dev/null || true
fi

# ── Step 3: 安装 heropen ──
info "安装 heropen..."
if "$PYTHON" -m pip install heropen --upgrade 2>&1; then
    say "heropen 安装完成"
else
    err "heropen 安装失败，请检查网络连接后重试"
fi

# ── Step 4: 启动 Viewer ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "启动 Viewer..."

VIEWER_STARTED=false

# 优先用 heropen viewer 命令
if "$PYTHON" -m heropen viewer --help &>/dev/null 2>&1; then
    "$PYTHON" -m heropen viewer &
    VIEWER_PID=$!
    VIEWER_STARTED=true
else
    # 回退：找内置的 server.py
    SERVER_SCRIPT=$("$PYTHON" -c "import heropen; import os; d=os.path.dirname(heropen.__file__); s=os.path.join(d,'viewer','server.py'); print(s if os.path.exists(s) else '')" 2>/dev/null)
    if [ -n "$SERVER_SCRIPT" ]; then
        "$PYTHON" "$SERVER_SCRIPT" &
        VIEWER_PID=$!
        VIEWER_STARTED=true
    fi
fi

# 等 Viewer 就绪（最多 5 秒）
VIEWER_READY=false
if [ "$VIEWER_STARTED" = true ]; then
    for i in $(seq 1 10); do
        if curl -s http://127.0.0.1:9020/api/health >/dev/null 2>&1; then
            VIEWER_READY=true
            break
        fi
        sleep 0.5
    done
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "${BOLD}${GREEN}✨ 安装完成！${NC}\n"
echo ""
if [ "$VIEWER_READY" = true ]; then
    printf "  Viewer:    ${CYAN}http://127.0.0.1:9020${NC}  ${GREEN}(已启动)${NC}\n"
    printf "  首次使用会在面板中完成 AI 助手配置\n"
else
    printf "  启动 Viewer: ${CYAN}heropen viewer${NC}\n"
fi
printf "  升级 Plus: ${CYAN}https://ksmn.cc/heropen/${NC}\n"
echo ""

# 自动打开浏览器
if [ "$VIEWER_READY" = true ]; then
    sleep 0.5
    if command -v open &>/dev/null; then
        open "http://127.0.0.1:9020"
        say "已在浏览器中打开 Viewer"
    elif command -v xdg-open &>/dev/null; then
        xdg-open "http://127.0.0.1:9020"
        say "已在浏览器中打开 Viewer"
    fi
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
