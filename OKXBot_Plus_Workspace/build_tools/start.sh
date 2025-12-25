#!/bin/bash

# Configuration
APP_NAME="CryptoOracle"
CONFIG_FILE="config.json"
ENV_FILE=".env"
LOG_DIR="log"

# Check files
if [ ! -f "$APP_NAME" ]; then
    echo "❌ Error: Executable '$APP_NAME' not found!"
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo "⚠️  Warning: '$CONFIG_FILE' not found. Please create one."
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "⚠️  Warning: '$ENV_FILE' not found. Please create one."
fi

# Ensure permissions
chmod +x "$APP_NAME"
mkdir -p "$LOG_DIR"

echo "🚀 Starting $APP_NAME..."
echo "📝 Logging to $LOG_DIR/trading_bot.log"
echo "💡 Use 'tail -f $LOG_DIR/trading_bot.log' to monitor."

# Run in background
nohup ./$APP_NAME > /dev/null 2>&1 &

PID=$!
echo "✅ Started with PID: $PID"
