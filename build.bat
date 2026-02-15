@echo off
chcp 65001 >nul
echo ==========================================
echo    Qwen API Server - 构建脚本
echo ==========================================

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python 未安装
    exit /b 1
)

echo ✅ Python 已安装

:: 安装依赖
echo.
echo 📦 安装依赖...
pip install nuitka -q
pip install -r requirements.txt -q

:: 执行构建
echo.
echo 🔨 开始构建...
python build.py

pause
