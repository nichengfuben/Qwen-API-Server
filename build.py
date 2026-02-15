#!/usr/bin/env python3
"""
Nuitka 构建脚本 - Qwen API Server
支持 Windows, Linux, macOS 全平台构建
"""

import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path

# 构建配置
BUILD_CONFIG = {
    "app_name": "qwen-server",
    "main_file": "qwen_server.py",
    "output_dir": "dist",
    "include_packages": [
        "fastapi",
        "uvicorn",
        "pydantic",
        "aiohttp",
        "aiofiles",
        "httpx",
        "websockets",
        "PIL",
        "requests",
        "python_multipart",
    ],
}


def get_platform():
    """获取当前平台"""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    else:
        raise RuntimeError(f"不支持的平台: {system}")


def get_output_filename():
    """获取输出文件名"""
    platform_name = get_platform()
    if platform_name == "windows":
        return f"{BUILD_CONFIG['app_name']}.exe"
    else:
        return f"{BUILD_CONFIG['app_name']}-{platform_name}"


def check_dependencies():
    """检查依赖是否安装"""
    print("📦 检查依赖...")
    try:
        import nuitka

        print("✅ Nuitka 已安装")
    except ImportError:
        print("❌ Nuitka 未安装，正在安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])

    # 检查 requirements
    if os.path.exists("requirements.txt"):
        print("📦 安装项目依赖...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
        )


def clean_build():
    """清理之前的构建"""
    print("🧹 清理构建目录...")
    dirs_to_remove = [
        BUILD_CONFIG["output_dir"],
        f"{BUILD_CONFIG['app_name']}.build",
        f"{BUILD_CONFIG['app_name']}.dist",
        f"{BUILD_CONFIG['app_name']}.onefile-build",
    ]
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"  删除: {dir_name}")


def build():
    """执行构建"""
    platform_name = get_platform()
    output_filename = get_output_filename()

    print(f"\n🔨 开始构建 for {platform_name.upper()}")
    print(f"📁 输出文件: {output_filename}")
    print("-" * 50)

    # 构建命令
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        "--enable-plugin=anti-bloat",
        "--output-dir",
        BUILD_CONFIG["output_dir"],
        "--output-filename",
        output_filename,
    ]

    # 添加包含的包
    for package in BUILD_CONFIG["include_packages"]:
        cmd.extend(["--include-package", package])

    # 平台特定选项
    if platform_name == "windows":
        cmd.extend(
            [
                "--windows-disable-console",
                "--windows-icon-from-ico=NONE",
            ]
        )
    elif platform_name == "macos":
        cmd.extend(
            [
                "--macos-create-app-bundle",
            ]
        )

    # 添加主文件
    cmd.append(BUILD_CONFIG["main_file"])

    print(f"🚀 执行命令: {' '.join(cmd)}\n")

    try:
        subprocess.check_call(cmd)
        print("\n" + "=" * 50)
        print("✅ 构建成功!")
        print("=" * 50)

        # 显示输出路径
        output_path = os.path.join(BUILD_CONFIG["output_dir"], output_filename)
        if os.path.exists(output_path):
            size = os.path.getsize(output_path) / (1024 * 1024)  # MB
            print(f"📦 输出文件: {output_path}")
            print(f"📊 文件大小: {size:.2f} MB")

            # 创建启动脚本
            create_launch_script(platform_name)

        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 构建失败: {e}")
        return False


def create_launch_script(platform_name):
    """创建启动脚本"""
    script_name = "start-server" + (".bat" if platform_name == "windows" else ".sh")
    output_path = os.path.join(BUILD_CONFIG["output_dir"], script_name)

    if platform_name == "windows":
        content = f"""@echo off
chcp 65001 >nul
echo Starting Qwen API Server...
cd /d "%~dp0"
{get_output_filename()}
pause
"""
    else:
        content = f"""#!/bin/bash
echo "Starting Qwen API Server..."
cd "$(dirname "$0")"
./{get_output_filename()}
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    if platform_name != "windows":
        os.chmod(output_path, 0o755)

    print(f"📝 创建启动脚本: {output_path}")


def create_package():
    """创建分发包"""
    platform_name = get_platform()
    package_name = f"{BUILD_CONFIG['app_name']}-{platform_name}"

    print(f"\n📦 创建分发包: {package_name}")

    # 创建临时目录
    temp_dir = f"temp_{package_name}"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    # 复制文件
    output_filename = get_output_filename()
    shutil.copy2(os.path.join(BUILD_CONFIG["output_dir"], output_filename), temp_dir)

    # 复制启动脚本
    script_name = "start-server" + (".bat" if platform_name == "windows" else ".sh")
    if os.path.exists(os.path.join(BUILD_CONFIG["output_dir"], script_name)):
        shutil.copy2(os.path.join(BUILD_CONFIG["output_dir"], script_name), temp_dir)

    # 复制配置文件模板
    shutil.copy2("README.md", temp_dir)
    shutil.copy2("AGENTS.md", temp_dir)

    # 创建账号配置模板
    accounts_template = """# Qwen 账号配置
# 请将此文件保存为 qwen_accounts.py 并填入你的账号信息

ACCOUNTS = [
    {
        "email": "your-email@example.com",
        "password": "your-password",
    },
    # 可以添加更多账号
]
"""
    with open(os.path.join(temp_dir, "qwen_accounts.py.example"), "w") as f:
        f.write(accounts_template)

    # 创建压缩包
    archive_name = os.path.join(BUILD_CONFIG["output_dir"], package_name)
    if platform_name == "windows":
        shutil.make_archive(archive_name, "zip", temp_dir)
        print(f"✅ 创建压缩包: {archive_name}.zip")
    else:
        shutil.make_archive(archive_name, "gztar", temp_dir)
        print(f"✅ 创建压缩包: {archive_name}.tar.gz")

    # 清理临时目录
    shutil.rmtree(temp_dir)


def main():
    """主函数"""
    print("=" * 60)
    print("   Qwen API Server - Nuitka 构建工具")
    print("=" * 60)

    # 检查参数
    if len(sys.argv) > 1:
        if sys.argv[1] in ["-c", "--clean"]:
            clean_build()
            return
        elif sys.argv[1] in ["-h", "--help"]:
            print("""
用法: python build.py [选项]

选项:
    -c, --clean     清理构建目录
    -h, --help      显示帮助信息
    
示例:
    python build.py           # 执行完整构建
    python build.py --clean   # 清理构建目录
""")
            return

    # 检查依赖
    check_dependencies()

    # 清理之前的构建
    clean_build()

    # 执行构建
    if build():
        # 创建分发包
        create_package()

        print("\n" + "=" * 60)
        print("✅ 构建完成!")
        print("=" * 60)
        print(f"\n📦 分发包位于: {BUILD_CONFIG['output_dir']}/")
        print("\n使用说明:")
        print("  1. 解压分发包")
        print("  2. 创建 qwen_accounts.py 文件配置账号")
        print("  3. 运行 start-server 脚本启动服务")
        print("  4. 访问 http://localhost:1325")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
