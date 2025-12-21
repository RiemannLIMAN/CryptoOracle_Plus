import os
import logging
import aiohttp
from logging.handlers import RotatingFileHandler
from datetime import datetime

import sys # Ensure sys is imported

async def send_notification_async(webhook_url, message):
    """
    异步发送通知，自动识别飞书与钉钉
    """
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        return

    headers = {'Content-Type': 'application/json'}
    payload = {}

    # 简单启发式识别
    if "feishu" in webhook_url or "lark" in webhook_url:
        # 飞书/Lark 格式 - 使用富文本 (post) 卡片以获得更好看的排版
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": "🤖 CryptoOracle 交易播报",
                        "content": [
                            [
                                {"tag": "text", "text": message}
                            ]
                        ]
                    }
                }
            }
        }
    elif "dingtalk" in webhook_url:
        # 钉钉 格式
        payload = {
            "msgtype": "text",
            "text": {"content": message}
        }
    else:
        # 默认尝试兼容格式
        payload = {"text": message}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, headers=headers, timeout=5) as response:
                if response.status != 200:
                    logging.getLogger("crypto_oracle").warning(f"Notification failed HTTP {response.status}: {await response.text()}")
    except Exception as e:
        logging.getLogger("crypto_oracle").error(f"Notification error: {e}")

def setup_logger(name="crypto_oracle"):
    # src/core/utils.py -> src/core -> src -> root
    # 如果通过 start_bot.sh 启动，cwd 通常是项目根目录
    # 直接使用 os.getcwd() 可能会更稳妥地指向用户认为的根目录
    # 但为了兼容性，我们还是优先探测脚本所在位置
    
    current_file = os.path.abspath(__file__) # .../src/core/utils.py
    src_dir = os.path.dirname(os.path.dirname(current_file)) # .../src
    
    # [优化] 判断当前工作目录是否已经是项目根目录
    # 如果 cwd 是 .../OKXBot_Plus_Workspace，那么就直接用 cwd，避免多余的路径计算
    cwd = os.getcwd()
    if os.path.basename(cwd) == "OKXBot_Plus_Workspace":
        project_root = cwd
    else:
        # 回退逻辑
        project_root = os.path.dirname(src_dir) # .../OKXBot_Plus_Workspace (项目根目录)
    
    log_dir = os.path.join(project_root, "log")

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # [优化] 使用固定文件名，以便 RotatingFileHandler 能正常工作（文件过大时自动轮转，而不是每次重启都生成新文件）
    log_filename = os.path.join(log_dir, "trading_bot.log")

    # 强制输出到 stdout，确保控制台可见
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler(log_filename, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            file_handler,
            console_handler
        ]
    )

    # Suppress noisy logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)

    return logging.getLogger(name)

def to_float(value):
    try:
        if value is None: return None
        if isinstance(value, (int, float)): return float(value)
        if isinstance(value, str):
            v = value.strip().replace(',', '')
            if v.lower() in ('n/a', 'na', 'none', ''): return None
            return float(v)
    except Exception: return None
    return None
