import sys
import os
import time
import asyncio
import ccxt.async_support as ccxt
import emoji
from datetime import datetime

# Ensure src is in python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Local imports
from core.config import Config
from core.utils import setup_logger
from services.strategy.ai_strategy import DeepSeekAgent
from services.execution.trade_executor import DeepSeekTrader
from services.risk.risk_manager import RiskManager

SYSTEM_VERSION = "v3.1.2 (Async Core)"

BANNER = r"""
  ___  ____  _  _  ____  ____  __    __  ____   __    ___  __    ____ 
 / __)(  _ \( \/ )(  _ \(_  _)/  \  /  \(  _ \ / _\  / __)(  )  (  __)
( (__  )   / )  /  ) __/  )( (  O )(  O ))   //    \( (__ / (_/\ ) _) 
 \___)(__\_)(__/  (__)   (__) \__/  \__/(__\_)\_/\_/ \___)\____/(____)

  🐯 CryptoOracle AI Trading System | """ + SYSTEM_VERSION + r"""
  ==================================================================
"""

async def run_system_check(logger, exchange, agent, config):
    """启动自检程序"""
    print("\n" + "="*50)
    logger.info("🚀 系统启动 (SYSTEM STARTUP)")
    print("="*50)
    
    try:
        # 1. 检查 OKX 连接
        balance = await exchange.fetch_balance()
        logger.info("✅ OKX API 连接成功")
        
        # 资金盘点
        total_usdt = 0
        free_usdt = 0
        if 'USDT' in balance:
            total_usdt = float(balance['USDT']['total'])
            free_usdt = float(balance['USDT']['free'])
        elif 'info' in balance and 'data' in balance['info']: # 统一账户
             for asset in balance['info']['data'][0]['details']:
                 if asset['ccy'] == 'USDT':
                     total_usdt = float(asset['eq'])
                     free_usdt = float(asset['availBal'])
        
        logger.info(f"💰 账户 USDT 权益: {total_usdt:.2f} U (可用: {free_usdt:.2f} U)")
        
        # 检查编外资产
        configured_symbols = [s['symbol'].split('/')[0] for s in config['symbols']]
        unmanaged_assets = []
        if 'total' in balance:
            for currency, amount in balance['total'].items():
                if amount > 0 and currency != 'USDT' and currency not in configured_symbols:
                    unmanaged_assets.append(f"{currency}({amount})")
        
        if unmanaged_assets:
            logger.warning(f"⚠️ 发现编外资产: {', '.join(unmanaged_assets)}")
            
        # 2. 检查 DeepSeek 连接
        logger.info("⏳ 正在测试 DeepSeek API...")
        await agent.client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=10
        )
        logger.info("✅ DeepSeek API 连接成功")
        
        print("🚀 系统自检完成")
        print("="*30 + "\n")
        return total_usdt
        
    except Exception as e:
        logger.error(f"❌ 自检失败: {e}")
        return 0

async def main():
    # print(BANNER) # 不再直接打印，交给 logger 统一管理
    logger = setup_logger()
    logger.info("\n" + BANNER) # 确保 Banner 前有换行，防止挤在一起
    logger.info(f"🚀 启动 CryptoOracle {SYSTEM_VERSION}")

    # 将启动脚本中的提示信息也记录到日志
    logger.info("=" * 60)
    logger.info(f"💡 [实时日志] tail -f log/trading_bot.log")
    logger.info(f"💡 [后台进程] ps -ef | grep OKXBot_Plus.py")
    logger.info(f"💡 [停止指令] kill -9 {os.getpid()}")
    logger.info("=" * 60)

    
    config = Config()
    if not config.data:
        logger.error("配置文件加载失败，程序退出。")
        return

    # [Fix] 注入 notification 配置到 trading 中，以便 Trader 能正确读取
    # config.json 中 notification 是 root 级，但 Trader 期望在 common_config (trading) 中找到它
    if 'notification' in config.data:
        config['trading']['notification'] = config['notification']

    # DeepSeek Client (Async)
    deepseek_config = config['models']['deepseek']
    proxy = config['trading'].get('proxy', '')
    
    agent = DeepSeekAgent(
        api_key=deepseek_config['api_key'],
        base_url=deepseek_config.get('base_url', "https://api.deepseek.com/v1"),
        proxy=proxy
    )

    # Exchange (Async)
    okx_config = config['exchanges']['okx']
    exchange_params = {
        'apiKey': okx_config['api_key'],
        'secret': okx_config['secret'],
        'password': okx_config['password'],
        'options': {'defaultType': 'swap'},
        'enableRateLimit': True
    }
    if proxy:
        exchange_params['aiohttp_proxy'] = proxy

    exchange = ccxt.okx(exchange_params)
    await exchange.load_markets()
    
    # Init Traders
    traders = []
    for symbol_conf in config['symbols']:
        trader = DeepSeekTrader(symbol_conf, config['trading'], exchange, agent)
        await trader.initialize()
        traders.append(trader)

    risk_manager = RiskManager(exchange, config['trading'].get('risk_control', {}), traders)
    
    # --- 启动前自检与初始化 ---
    start_equity = await run_system_check(logger, exchange, agent, config)
    
    # 发送启动通知
    if config['trading'].get('notification', {}).get('enabled', False):
        logger.info("📨 发送启动通知...")
        await risk_manager.send_notification(
            f"🚀 机器人已启动 ({SYSTEM_VERSION})\n"
            f"模式: {'测试模式' if config['trading']['test_mode'] else '实盘模式'}\n"
            f"监控: {len(traders)} 个币种"
        )

    # 预热数据
    logger.info("⏳ 正在预热市场数据...")
    pre_warm_tasks = [trader.get_ohlcv() for trader in traders]
    await asyncio.gather(*pre_warm_tasks, return_exceptions=True)
    logger.info("✅ 数据预热完成")
    

    # 初始化资产基准
    await risk_manager.initialize_baseline(start_equity)
    
    # 显示历史战绩
    risk_manager.display_pnl_history()
    
    # [新增] 打印分割线，明确初始化阶段结束
    print("\n" + "=" * 50)
    logger.info("🏁 初始化完成，进入主循环")
    print("=" * 50 + "\n")
    
    # --- 进入主循环 ---
    timeframe = config['trading']['timeframe']
    
    # [Hack] 即使配置是 "1m"，我们依然可以强制更快的轮询速度
    # 如果用户想在 config.json 里写 "1m" 来避免报错，但又想 30s 跑一次
    # 我们可以在这里硬编码覆盖 interval
    
    interval = 60 # default 1m
    
    # 正常解析逻辑
    if 'm' in timeframe: interval = int(timeframe.replace('m', '')) * 60
    elif 'h' in timeframe: interval = int(timeframe.replace('h', '')) * 3600
    elif 'ms' in timeframe: interval = int(timeframe.replace('ms', '')) / 1000
    elif 's' in timeframe: interval = int(timeframe.replace('s', ''))
    
    # [强制覆盖] 如果是 1m，尝试读取 loop_interval 配置，默认 15s
    if timeframe == '1m':
        custom_interval = config['trading'].get('loop_interval', 15)
        logger.info(f"⚡ [极速模式 Pro] 配置为 1m，强制轮询间隔为 {custom_interval}s")
        interval = custom_interval

    logger.info(f"⏰ 轮询间隔: {interval}秒")
    
    try:
        while True:
            start_ts = time.time()
            
            # 还原经典分割线样式
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info("▼" * 70)
            logger.info(f"⏰ 批次执行开始: {current_time}")
            logger.info("▲" * 70)
            
            # 1. Risk Check
            await risk_manager.check()
            
            # 2. Parallel Execution
            tasks = [trader.run() for trader in traders]
            await asyncio.gather(*tasks)
            
            elapsed = time.time() - start_ts
            sleep_time = max(0.01, interval - elapsed) # 允许毫秒级休眠
            print(f"💤 本轮耗时 {elapsed:.4f}s, 休眠 {sleep_time:.4f}s...")
            await asyncio.sleep(sleep_time)
            
    except KeyboardInterrupt:
        logger.info("🛑 停止中...")
    except Exception as e:
        logger.error(f"Main loop error: {e}")
    finally:
        await exchange.close()
        # agent.client closes automatically

if __name__ == "__main__":
    # Windows 平台下的 event loop 策略调整
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # 强制 Windows 终端使用 UTF-8 编码，防止中文乱码
        sys.stdout.reconfigure(encoding='utf-8')
    
    # print(f"🔥 正在启动 CryptoOracle 进程 (PID: {os.getpid()})...", flush=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 用户手动停止")
    except Exception as e:
        print(f"❌ 致命错误: {e}")
        import traceback
        traceback.print_exc()
        input("按 Enter 键退出...")
