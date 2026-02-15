#!/bin/bash
# Linux/macOS 构建脚本

echo "=========================================="
echo "   Qwen API Server - 构建脚本"
echo "=========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 未安装"
    exit 1
fi

echo "✅ Python3 已安装: $(python3 --version)"

# 安装依赖
echo ""
echo "📦 安装依赖..."
pip3 install nuitka -q
pip3 install -r requirements.txt -q

# 执行构建
echo ""
echo "🔨 开始构建..."
python3 build.py
