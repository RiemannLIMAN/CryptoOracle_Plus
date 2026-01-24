#!/bin/bash

# 设置 PID 文件路径
PID_FILE="bot.pid"

# 检查 PID 文件是否存在
if [ ! -f "$PID_FILE" ]; then
    echo "❌ [ERROR] PID file not found: $PID_FILE"
    echo "It seems the bot is not running or was not started with the start script."
    exit 1
fi

# 读取 PID
BOT_PID=$(cat "$PID_FILE")

echo "ℹ️ [INFO] Found Bot PID: $BOT_PID"

# 检查进程是否存在
if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "⚠️ [WARNING] Process with PID $BOT_PID is not running."
    echo "Cleaning up stale PID file..."
    rm "$PID_FILE"
    exit 0
fi

# 尝试优雅关闭 (SIGTERM)
echo "🛑 [INFO] Stopping process $BOT_PID (sending SIGTERM)..."
kill "$BOT_PID"

# 等待进程结束
for i in {1..5}; do
    if ! kill -0 "$BOT_PID" 2>/dev/null; then
        echo "✅ [SUCCESS] Bot stopped successfully."
        rm "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# 如果还在运行，强制关闭 (SIGKILL)
echo "⚠️ [WARNING] Process did not exit gracefully. Force killing (SIGKILL)..."
kill -9 "$BOT_PID"

if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "✅ [SUCCESS] Bot force killed."
    rm "$PID_FILE"
else
    echo "❌ [ERROR] Failed to kill process. Please check permissions."
    exit 1
fi
