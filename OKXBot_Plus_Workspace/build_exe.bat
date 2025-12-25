@echo off
echo ========================================================
echo   CryptoOracle Packaging Script (PyInstaller)
echo ========================================================

:: 1. 检查是否安装 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] PyInstaller not found. Installing...
    pip install pyinstaller
)

:: 2. 清理旧构建
if exist build rd /s /q build
if exist dist rd /s /q dist
if exist *.spec del *.spec

:: 3. 执行打包
:: --name: 生成文件名
:: --onefile: 单文件模式 (方便携带)
:: --icon: 图标 (如果有的话，这里暂时省略)
:: --paths: 添加 src 到搜索路径
:: --hidden-import: 显式导入可能丢失的库
echo [INFO] Building EXE...
pyinstaller --noconfirm ^
    --name CryptoOracle ^
    --onefile ^
    --paths src ^
    --hidden-import pandas ^
    --hidden-import ccxt ^
    --hidden-import aiohttp ^
    --hidden-import emoji ^
    --hidden-import python-dotenv ^
    src/OKXBot_Plus.py

echo.
if exist dist\CryptoOracle.exe (
    echo ========================================================
    echo [SUCCESS] Build completed!
    echo Executable is located at: dist\CryptoOracle.exe
    echo.
    echo [IMPORTANT]
    echo Please ensure 'config.json' and '.env' are in the same folder 
    echo as the EXE before running.
    echo ========================================================
) else (
    echo [ERROR] Build failed. Please check the output above.
)
pause
