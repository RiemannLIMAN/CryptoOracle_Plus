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

SYSTEM_VERSION = "v3.3.13 (Dual-Watchdog)"

BANNER = r"""
   _____                  __           ____                  __   
  / ___/______  ______   / /_____     / __ \_________ ______/ /__ 
 / /__/ ___/ / / / __ \ / __/ __ \   / / / / ___/ __ `/ ___/ / _ \
/ /__/ /  / /_/ / /_/ // /_/ /_/ /  / /_/ / /  / /_/ / /__/ /  __/
\___/_/   \__, / .___/ \__/\____/   \____/_/   \__,_/\___/_/\___/ 
         /____/_/                                                 
  
  🤖 CryptoOracle Plus AI Trading System | """ + SYSTEM_VERSION + r"""
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
    logger.info(f"💡 [实时日志] tail -f log/trading_bot_*.log")
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
            f"**版本**: {SYSTEM_VERSION}\n"
            f"**模式**: {'🧪 测试模式' if config['trading']['test_mode'] else '🔥 实盘模式'}\n"
            f"**权益**: `{start_equity:.2f} U`\n"
            f"**监控**: `{len(traders)}` 个币种",
            title="🚀 机器人启动成功"
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
    
    # [通用覆盖] 如果配置了 loop_interval，则优先使用它，不再局限于 1m
    # 这允许用户设置 timeframe="5m" (AI看大周期) 但 interval=60 (每分钟检查一次信号)
    custom_interval = config['trading'].get('loop_interval')
    if custom_interval and isinstance(custom_interval, (int, float)) and custom_interval > 0:
         logger.info(f"⚡ [自定义轮询] 使用配置的 loop_interval: {custom_interval}s (原 timeframe: {timeframe})")
         interval = custom_interval
    elif timeframe == '1m':
        # 旧的 1m 默认逻辑 (如果没有显式配置 loop_interval，默认 30s)
        logger.info(f"⚡ [极速模式 Pro] 配置为 1m，默认轮询间隔为 30s")
        interval = 30

    logger.info(f"⏰ 轮询间隔: {interval}秒")
    
    # [Watchdog] 独立的安全监控线程
    # 每 5秒 快速检查一次止损，不进行 AI 分析
    # 这解决了 "长轮询周期" 带来的 "止损不及时" 问题
    safety_interval = 5.0
    last_strategy_run = 0.0
    last_safety_run = 0.0
    
    try:
        while True:
            current_ts = time.time()
            
            # --- 1. 高频安全监控 (每5秒) ---
            if current_ts - last_safety_run >= safety_interval:
                # 仅在策略未运行时运行安全检查，避免冲突
                # (虽然 asyncio 是单线程的，但这里是逻辑上的分时复用)
                
                # 遍历所有 trader 进行快速检查
                safety_tasks = [trader.run_safety_check() for trader in traders]
                safety_results = await asyncio.gather(*safety_tasks, return_exceptions=True)
                
                # 如果有触发止损/止盈，打印日志
                triggered_sl = False
                for res in safety_results:
                    if isinstance(res, dict):
                        if res.get('type') == 'STOP_LOSS':
                            triggered_sl = True
                            logger.warning(f"🚨 [WATCHDOG] 触发硬止损: {res['symbol']} (PnL {res['pnl']*100:.2f}%)")
                        elif res.get('type') == 'TAKE_PROFIT':
                            triggered_sl = True
                            logger.info(f"💰 [WATCHDOG] 触发硬止盈: {res['symbol']} (PnL {res['pnl']*100:.2f}%)")
                
                last_safety_run = current_ts
            
            # --- 2. 低频策略分析 (每 interval 秒) ---
            if current_ts - last_strategy_run >= interval:
                start_ts = time.time()
                
                # 还原经典分割线样式
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                logger.info(f"─" * 60) 
                logger.info(f"⏰ 批次执行开始: {current_time}")
                logger.info(f"─" * 60)
                
                # 2.1 Risk Check
                await risk_manager.check()
                
                # 2.2 Parallel Execution
                tasks = [trader.run() for trader in traders]
                results = await asyncio.gather(*tasks)
                
                # [Added] 结构化表格输出
                table_lines = []
                header = f"📊 MARKET SCAN | {len(results)} Symbols"
                table_lines.append(header) 
                table_lines.append("─" * 130)
                table_lines.append(f"{'SYMBOL':<14} | {'PRICE':<10} | {'24H%':<8} | {'SIGNAL':<8} | {'CONF':<8} | {'EXECUTION':<16} | {'ANALYSIS SUMMARY'}")
                table_lines.append("─" * 130)
                
                for res in results:
                    if res:
                        symbol_str = res['symbol'].split(':')[0]
                        change_val = res['change']
                        change_icon = "🟢" if change_val > 0 else "🔴"
                        change_str = f"{change_val:+.2f}%"
                        
                        signal = res['signal']
                        sig_icon = "✋"
                        if signal == 'BUY': sig_icon = "🚀"
                        elif signal == 'SELL': sig_icon = "📉"
                        signal_display = f"{sig_icon} {signal}"
                        
                        conf = res['confidence']
                        conf_display = conf
                        if conf == 'HIGH': conf_display = "🔥🔥 HIGH"
                        elif conf == 'MEDIUM': conf_display = "⚡ MED"
                        elif conf == 'LOW': conf_display = "💤 LOW"

                        exec_status = res.get('status', 'N/A')
                        status_icon = "❓"
                        if exec_status == 'EXECUTED': status_icon = "✅"
                        elif exec_status == 'HOLD': status_icon = "⏸️"
                        elif 'SKIPPED' in exec_status: status_icon = "🚫"
                        elif exec_status == 'FAILED': status_icon = "❌"
                        elif exec_status == 'TEST_MODE': status_icon = "🧪"
                        
                        display_status = exec_status.replace('SKIPPED_', '')
                        if display_status == 'EXECUTED': display_status = 'DONE'
                        exec_display = f"{status_icon} {display_status}"

                        summary_text = res.get('summary', '')
                        if not summary_text or len(summary_text) == 0:
                            reason = res['reason'].replace('\n', ' ')
                            summary_text = (reason[:40] + '...') if len(reason) > 40 else reason
                        
                        price_str = f"${res['price']:,.2f}"
                        table_lines.append(f"{symbol_str:<14} | {price_str:<10} | {change_icon} {change_str:<5} | {signal_display:<8} | {conf_display:<8} | {exec_display:<16} | {summary_text}")
                
                table_lines.append("─" * 130)
                logger.info("\n".join(table_lines))
                
                elapsed = time.time() - start_ts
                logger.info(f"💤 本轮策略耗时 {elapsed:.4f}s")
                last_strategy_run = current_ts

            # 主循环休眠 1s，保持对 watchdog 的响应
            await asyncio.sleep(1.0)
            
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
