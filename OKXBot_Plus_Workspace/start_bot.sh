#!/bin/bash

# ==========================================
# 🤖 CryptoOracle 启动脚本 (Ubuntu/Linux)
# ==========================================

# 1. 切换到项目根目录
# 获取脚本所在的绝对路径 (兼容 sh/bash)
SCRIPT_DIR="$( cd "$( dirname "$0" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR"

# 定义颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 正在准备启动 CryptoOracle 交易机器�?..${NC}"

# 2. 检查配置文�?if [ ! -f "config.json" ]; then
    echo -e "${RED}�?错误: 未找�?config.json${NC}"
    echo "   请先复制 config.example.json 并配置您�?API Key"
    echo "   命令: cp config.example.json config.json"
    exit 1
fi

# [New] 自动清理旧数�?(Auto-Clean)
# 防止数据库损�?(no such table) 或加载过期状�?echo -e "${YELLOW}🧹 正在执行自动清理...${NC}"
if [ -d "data" ]; then
    rm -f data/*.db 2>/dev/null
    rm -f data/state_*.json 2>/dev/null
    # 保留 config.json �?bot_state.json
    echo -e "${GREEN}�?已清理旧数据库和状态文�?{NC}"
fi

# 3. 智能查找 Python 解释�?# [可配置] 如果您使用自定义名称的虚拟环境，请在此处设置名称
# 例如: CUSTOM_VENV_NAME="my_env" �?CUSTOM_VENV_NAME="okx_ds"
CUSTOM_VENV_NAME="okx_ds"

# 优先查找虚拟环境
PYTHON_CMD=""
VENV_PATH=""

# 检查常见的虚拟环境路径
# 注意：为了兼�?sh (dash)，此处不使用数组语法
POSSIBLE_VENVS=""

# 只有�?CUSTOM_VENV_NAME 不为空时才添加相关路�?if [ -n "$CUSTOM_VENV_NAME" ]; then
    POSSIBLE_VENVS="../$CUSTOM_VENV_NAME $CUSTOM_VENV_NAME"
fi

# 追加标准路径
POSSIBLE_VENVS="$POSSIBLE_VENVS ../venv venv ../.venv .venv"

for venv in $POSSIBLE_VENVS; do
    if [ -n "$venv" ] && [ -d "$venv" ]; then
        # 检查是否是 conda 环境
        if [ -d "$venv/conda-meta" ]; then
             # Conda 环境通常需�?source activate，脚本里处理比较复杂
             # 简单起见，我们尝试直接调用该环境下�?python
             if [ -f "$venv/bin/python" ]; then
                VENV_PATH="$venv"
                break
             fi
        # 检查标�?venv
        elif [ -f "$venv/bin/activate" ]; then
            VENV_PATH="$venv"
            break
        fi
    fi
done

if [ -n "$VENV_PATH" ]; then
    # echo -e "${GREEN}�?检测到虚拟环境: $VENV_PATH${NC}"
    # 如果�?venv，激活它
    if [ -f "$VENV_PATH/bin/activate" ]; then
        source "$VENV_PATH/bin/activate"
        PYTHON_CMD="python"
    else
        # 可能�?conda 或其他，直接使用完整路径
        PYTHON_CMD="$VENV_PATH/bin/python"
    fi
else
    # [新增] 检查当�?shell 是否已经激活了 Conda 环境 (CONDA_PREFIX)
    if [ -n "$CONDA_PREFIX" ]; then
        echo -e "${GREEN}�?检测到已激活的 Conda 环境: $CONDA_PREFIX${NC}"
        PYTHON_CMD="python"
    elif [ -n "$VIRTUAL_ENV" ]; then
         # [新增] 检查是否已激活了 standard venv (VIRTUAL_ENV)
         echo -e "${GREEN}�?检测到已激活的 Venv 环境: $VIRTUAL_ENV${NC}"
         PYTHON_CMD="python"
    else
        # [新增] 终极回退：尝试使�?conda run 直接调用指定名称的环�?        # [优化] 为了避免双重进程 (kill不掉的问�?，我们不再使�?'conda run'
        # 而是尝试解析出该 conda 环境�?python 绝对路径
        if [ -n "$CUSTOM_VENV_NAME" ] && command -v conda &> /dev/null; then
            # 获取 conda 环境的路�?            CONDA_ENV_PATH=$(conda env list | grep "$CUSTOM_VENV_NAME" | awk '{print $NF}')
            
            if [ -n "$CONDA_ENV_PATH" ] && [ -f "$CONDA_ENV_PATH/bin/python" ]; then
                echo -e "${GREEN}�?检测到 Conda 环境 '$CUSTOM_VENV_NAME' (路径: $CONDA_ENV_PATH)${NC}"
                PYTHON_CMD="$CONDA_ENV_PATH/bin/python"
            else
                echo -e "${YELLOW}⚠️ 未找到名�?'$CUSTOM_VENV_NAME' �?Conda 环境，或无法解析其路�?{NC}"
            fi
        fi

        # 如果上面也没找到，才回退到系�?Python
        if [ -z "$PYTHON_CMD" ]; then
             echo -e "${YELLOW}⚠️ 未检测到虚拟环境目录，尝试使用系�?Python...${NC}"
             # 检查系�?python3
             if command -v python3 &> /dev/null; then
                 PYTHON_CMD="python3"
             elif command -v python &> /dev/null; then
                 PYTHON_CMD="python"
             else
                 echo -e "${RED}�?致命错误: 未找�?python3 �?python 命令${NC}"
                 echo "   请先安装 Python: sudo apt install python3"
                 exit 127
             fi
        fi
    fi
fi

# 打印 Python 版本信息
PY_VERSION=$($PYTHON_CMD --version 2>&1)
echo "🐍 Python 版本: $PY_VERSION"

# 4. 准备日志文件
# 确保日志目录存在
LOG_DIR="log"
if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR"
fi

# 我们不再使用 shell 重定向生�?startup_xxx.log
# 而是直接依赖 python 脚本内部生成�?trading_bot_xxx.log
# 但为了能看到 nohup 的输�?(print)，我们还是需要一个文�?# 统一命名�?console_output.log，避免每次生成新文件
STARTUP_LOG="$LOG_DIR/console_output.log"

# echo "📝 控制台输出将重定向至: $STARTUP_LOG"

# 5. 检查是否已有实例运�?(基于 PID 文件)
# [v3.1.5] 仅检查当前目录下�?bot.pid，允许同一服务器多实例运行
PID_FILE="log/bot.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    # 检�?PID 是否仍存�?    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️ 警告: 检测到当前目录已有实例在运�?(PID: $OLD_PID)${NC}"
        echo -e "${YELLOW}   (如果这是另一个目录的实例，请忽略此警�?${NC}"
        
        read -p "是否停止旧进程并重新启动? (y/n): " choice
        if [[ "$choice" == "y" || "$choice" == "Y" ]]; then
            kill "$OLD_PID"
            sleep 2
            echo "�?旧进程已停止"
        else
            echo "操作已取�?
            exit 0
        fi
    else
        # PID 文件存在但进程不存在，清理残�?        echo "🧹 清理残留�?PID 文件..."
        rm "$PID_FILE"
    fi
fi

# 检查全局是否有其他实�?(仅作为友好提示，不强制阻�?
OTHER_PIDS=$(ps -ef | grep "OKXBot_Plus.py" | grep -v grep | awk '{print $2}')
if [ -n "$OTHER_PIDS" ]; then
    echo -e "${YELLOW}ℹ️ 提示: 服务器上还有其他 OKXBot 实例在运�?(PIDs: $OTHER_PIDS)${NC}"
    echo -e "${YELLOW}   请确保不同的实例使用不同�?API Key 或配置，以免冲突�?{NC}"
fi

# 6. 后台启动
# -u: 禁用 Python 输出缓冲，确保日志实时写�?# nohup: 让进程忽略挂起信号，允许后台运行
echo -e "${GREEN}�?正在启动后台进程...${NC}"
# [v3.0] 启动入口变更�?OKXBot_Plus.py
# [Fix] 移除 nohup 输出，直接在当前终端前台运行 (因为用户不需要重定向)
# 如果您希望后台运行，可以恢复 nohup
nohup "$PYTHON_CMD" -u src/OKXBot_Plus.py > "$STARTUP_LOG" 2>&1 &

NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# 更新 latest log 链接 (不再需�?
# cp "$STARTUP_LOG" "$LATEST_LOG" 2>/dev/null

# 7. 验证启动结果 (关键步骤)
# echo "�?正在验证进程状�?(PID: $NEW_PID)，请等待 5 �?.."
sleep 5

if ps -p $NEW_PID > /dev/null; then
    echo -e "${GREEN}�?启动成功！机器人正在后台运行�?{NC}"
    # echo "--------------------------------------------------"
    echo -e "🆔 进程 PID: ${GREEN}$NEW_PID${NC}"
    echo -e "📄 日志文件: ${GREEN}$STARTUP_LOG${NC}"
    # echo "--------------------------------------------------"
    echo -e "${YELLOW}👀 正在进入实时日志监控模式...${NC}"
    echo -e "�?�?${YELLOW}Ctrl+C${NC} 可退出日志查�?(机器人将继续在后台运�?"
    echo "--------------------------------------------------"
    # 使用 tail -f 实时跟踪日志，从最�?50 行开始显�?    tail -n 50 -f "$STARTUP_LOG"
else
    echo -e "${RED}�?启动失败！进程在启动后立即退出了�?{NC}"
    echo "--------------------------------------------------"
    echo "🔍 错误日志内容:"
    echo "--------------------------------------------------"
    cat "$STARTUP_LOG"
    echo "--------------------------------------------------"
    echo "可能的原�?"
    echo "1. 依赖库未安装 (请尝�? pip install -r requirements.txt)"
    echo "2. config.json 配置错误"
    echo "3. API 连接失败"
fi