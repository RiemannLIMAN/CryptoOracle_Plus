import os
import time
import logging
import aiohttp
import asyncio
from logging.handlers import RotatingFileHandler
from datetime import datetime

import sys # Ensure sys is imported
from .exceptions import (
    CryptoOracleException, APIConnectionError, APIResponseError,
    ConfigError, TradingError, RiskManagementError,
    DataProcessingError, AIError
)

# [New] Notification Cooldown Cache
_notification_cooldowns = {}

# [New] Global Rate Limiter (P2-4.5)
class GlobalRateLimiter:
    """
    全局限频器 (令牌桶算法)
    确保全系统的 API 调用频率符合交易所限制
    """
    def __init__(self, requests_per_second=10):
        self.capacity = requests_per_second
        self.tokens = requests_per_second
        self.last_fill_time = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """获取令牌，若无则等待"""
        async with self.lock:
            while self.tokens < 1:
                now = time.time()
                elapsed = now - self.last_fill_time
                # 填充令牌
                self.tokens = min(self.capacity, self.tokens + elapsed * self.capacity)
                self.last_fill_time = now
                
                if self.tokens < 1:
                    await asyncio.sleep(0.1)
            
            self.tokens -= 1

# 全局单例
rate_limiter = GlobalRateLimiter(requests_per_second=10)

async def send_notification_async(webhook_url, message, title=None):
    """
    异步发送通知，自动识别飞书与钉钉
    """
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        return

    # [Enhance] Notification Cooldown
    # Key: type_symbol (Need to infer symbol/type from message/title)
    # Simple Heuristic: Use Title as key component
    if title:
        key = f"{title}"
        now = time.time()
        if key in _notification_cooldowns:
            last_time = _notification_cooldowns[key]
            if now - last_time < 60: # 60s cooldown
                return
        _notification_cooldowns[key] = now

    headers = {'Content-Type': 'application/json'}
    payload = {}

    # 简单启发式识别
    if "feishu" in webhook_url or "lark" in webhook_url:
        # 飞书/Lark 格式 - 使用互动卡片 (interactive)
        
        # 确定卡片头部的颜色 (基于消息内容)
        header_color = "blue" # 默认蓝色
        card_title = title if title else "🤖 CryptoOracle 消息"
        
        if "诊断报告" in message or "诊断报告" in str(title):
            header_color = "orange" # 诊断 -> 橙色
        elif "失败" in message or "Failed" in message or "❌" in str(title):
            header_color = "red"    # 失败 -> 红色
        elif "警告" in message or "⚠️" in message:
            header_color = "yellow" # 警告 -> 黄色
        elif "止盈" in message or "🎉" in message:
            header_color = "carmine" # 止盈 -> 洋红
        elif "止损" in message or "😭" in message or "🚑" in message:
            header_color = "grey"   # 止损 -> 灰色
        elif "买入" in message or "BUY" in message or "🚀" in message:
            header_color = "green"  # 买入 -> 绿色
        elif "卖出" in message or "SELL" in message or "📉" in message:
            header_color = "red"    # 卖出 -> 红色
        elif "启动" in message:
            header_color = "blue"

        # [Fix] 飞书互动卡片对正文长度有限制，且需要转义
        # 如果 message 太长，进行截断
        safe_msg = message
        if len(safe_msg) > 5000: safe_msg = safe_msg[:5000] + "..."
        
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True
                },
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": card_title
                    },
                    "template": header_color
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": safe_msg
                        }
                    },
                    {
                        "tag": "hr"
                    },
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": f"Time: {datetime.now().strftime('%H:%M:%S')}"
                            }
                        ]
                    }
                ]
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
        
    # [New] 自动清理旧日志 (保留最近3天)
    # 虽然改为单文件模式，但为了清理之前残留的时间戳文件，保留此逻辑一次
    try:
        now = time.time()
        retention_days = 3
        for f in os.listdir(log_dir):
            if f.endswith(".log") and f.startswith("trading_bot_"):
                f_path = os.path.join(log_dir, f)
                if os.stat(f_path).st_mtime < now - (retention_days * 86400):
                    os.remove(f_path)
    except Exception:
        pass

    # [Fix] 恢复多文件模式，按日期命名
    today_str = datetime.now().strftime('%Y%m%d')
    log_filename = os.path.join(log_dir, f"crypto_oracle_{today_str}.log")

    # [v3.9.6] Debug Mode Support
    # 优先检查环境变量，如果没有则默认 INFO
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = logging.DEBUG if log_level_str == 'DEBUG' else logging.INFO

    # 强制输出到 stdout，确保控制台可见
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # 使用 RotatingFileHandler，最大 10MB，保留 3 个备份
    file_handler = RotatingFileHandler(log_filename, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setLevel(log_level)

    logging.basicConfig(
        level=log_level,
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

    logger = logging.getLogger(name)
    logger.info(f"📝 日志文件已创建: {log_filename}")
    return logger

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

import asyncio

def retry_async(retries=3, delay=1.0, backoff=2.0, exceptions=(Exception,)):
    """
    异步重试装饰器 (Exponential Backoff)
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    logger = logging.getLogger("crypto_oracle")
                    # [User Request] 余额不足 (Code 51008) 是已知业务逻辑，不需要在 retry 装饰器中打印 ERROR 日志
                    # 因为 create_order_with_retry 内部已经处理并抛出了简洁的异常
                    is_insufficient_fund = "51008" in str(e) or "保证金不足" in str(e)
                    
                    if attempt == retries - 1:
                        # 只有在最后一次尝试失败时才决定是否打印
                        if not is_insufficient_fund:
                             logger.error(f"❌ {func.__name__} 失败 (尝试 {attempt+1}/{retries}): {e}")
                        raise e
                    else:
                        # 对于余额不足，重试期间也不需要打印 warning，因为 order_executor 内部已经打印了降级提示
                        if not is_insufficient_fund:
                             logger.warning(f"⚠️ {func.__name__} 失败: {e} | {current_delay:.1f}s 后重试 ({attempt+1}/{retries})")
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
        return wrapper
    return decorator

def exception_handler(func):
    """
    异常处理装饰器
    """
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except APIConnectionError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"API连接异常: {e}")
            return None
        except APIResponseError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"API响应异常: {e}")
            return None
        except ConfigError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"配置异常: {e}")
            return None
        except TradingError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"交易异常: {e}")
            return None
        except RiskManagementError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"风险管理异常: {e}")
            return None
        except DataProcessingError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"数据处理异常: {e}")
            return None
        except AIError as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"AI分析异常: {e}")
            return None
        except Exception as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"未知异常: {e}")
            return None
    return wrapper

def sync_exception_handler(func):
    """
    同步函数异常处理装饰器
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger = logging.getLogger("crypto_oracle")
            logger.error(f"未知异常: {e}")
            return None
    return wrapper
