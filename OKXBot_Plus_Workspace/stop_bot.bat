@echo off
setlocal

:: 设置 PID 文件路径 (与启动脚本保持一致)
set "PID_FILE=log\bot.pid"

:: 检查 PID 文件是否存在
if not exist "%PID_FILE%" (
    echo [ERROR] PID file not found: %PID_FILE%
    echo It seems the bot is not running or was not started with the start script.
    echo You may need to manually find and kill the process.
    pause
    exit /b 1
)

:: 读取 PID
set /p BOT_PID=<"%PID_FILE%"

echo [INFO] Found Bot PID: %BOT_PID%

:: 检查进程是否存在
tasklist /FI "PID eq %BOT_PID%" | find "%BOT_PID%" > nul
if errorlevel 1 (
    echo [WARNING] Process with PID %BOT_PID% is not running.
    echo Cleaning up stale PID file...
    del "%PID_FILE%"
    pause
    exit /b 0
)

:: 尝试优雅关闭 (发送 Ctrl+C / SIGINT 很难在批处理中对非子进程实现，直接 Kill)
:: 如果需要优雅退出，通常需要 Python 脚本监听信号或文件。
:: 这里使用 taskkill /F (强制结束)
echo [INFO] Killing process %BOT_PID%...
taskkill /F /PID %BOT_PID%

if errorlevel 1 (
    echo [ERROR] Failed to kill process. Please try running as Administrator.
) else (
    echo [SUCCESS] Bot stopped successfully.
    del "%PID_FILE%"
)

pause