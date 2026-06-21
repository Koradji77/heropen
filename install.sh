#!/usr/bin/env bash
#
# HeroPen — AI Agent Long-term Memory System 一键安装
# 用法: curl -sSL https://ksmn.cc/install.sh | bash
#

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${CYAN}  🖊  HeroPen — AI Agent 长期记忆系统${NC}"
echo -e "${CYAN}  ═══════════════════════════════════════${NC}"
echo ""

# Check Python
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo -e "${RED}  ❌ 未检测到 Python${NC}"
    echo -e "${YELLOW}     请先安装 Python 3.10+${NC}"
    exit 1
fi

VER=$($PY --version 2>&1)
echo -e "${GREEN}  ✅ 检测到 $VER${NC}"

# Install/upgrade heropen
echo -e "${YELLOW}  📦 正在安装 HeroPen...${NC}"
$PY -m pip install heropen -q 2>/dev/null
echo -e "${GREEN}  ✅ HeroPen 安装成功${NC}"

echo -e "${CYAN}  ✨ 配置完成，请复制下面的文字发给你的 AI 助手${NC}"
echo ""

# Run setup (outputs copy-paste text for AI)
heropen 2>/dev/null || $PY -m heropen 2>/dev/null || {
    echo ""
    echo -e "${YELLOW}  ⚠️  heropen 命令未找到，请手动运行：${NC}"
    echo -e "      heropen"
}
