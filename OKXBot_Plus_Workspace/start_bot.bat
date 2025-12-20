@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo 🤖 CryptoOracle Windows 启动脚本
echo ===================================================

:: 1. 检测虚拟环境
if exist "..\venv\Scripts\activate.bat" (
    echo ✅ 检测到 Python 虚拟环境 (venv)
    echo ⏳ 正在激活环境...
    call "..\venv\Scripts\activate.bat"
) else (
    echo ⚠️ 未检测到 venv，将尝试使用系统 Python
)

:: 2. 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 错误: 未找到 Python，请先安装 Python 并添加到 PATH。
    pause
    exit /b
)

:: 2.5 防重复启动检查
wmic process where "name='python.exe' and commandline like '%%OKXBot_Plus.py%%'" get processid 2>nul | findstr [0-9] >nul
if %errorlevel% equ 0 (
    echo.
    echo ⚠️ 警告: 检测到 OKXBot_Plus 似乎已经在运行中！
    echo 💡 如果是误报，请输入 y 继续。
    echo.
    set /p choice="是否继续启动新的实例? (y/n): "
    if /i not "%choice%"=="y" exit /b
)

python -u src/OKXBot_Plus.py

if %errorlevel% neq 0 (
    echo.
    echo ❌ 程序异常退出 (Exit Code: %errorlevel%)
    echo 💡 请检查上方报错信息 (通常是 API Key 错误或网络问题)
)

pause
