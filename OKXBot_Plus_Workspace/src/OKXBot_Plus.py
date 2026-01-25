import sys
import os
import time
import asyncio
import ccxt.async_support as ccxt
# import emoji # [Fix] Removed unused/unsafe dependency
from datetime import datetime

# Ensure src is in python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Local imports
from core.config import Config
from core.utils import setup_logger
from core.monitor import health_monitor
from core.plugin import plugin_manager
from services.strategy.ai_strategy import DeepSeekAgent
from services.execution.trade_executor import DeepSeekTrader
from services.risk.risk_manager import RiskManager

SYSTEM_VERSION = "v3.8.0 (Dual-Track Monitor & Fast Exit)"

BANNER = r"""
   _____                  __           ____                  __   
  / ___/______  ______   / /_____     / __ \_________ ______/ /__ 
 / /__/ ___/ / / / __ \ / __/ __ \   / / / / ___/ __ `/ ___/ / _ \
/ /__/ /  / /_/ / /_/ // /_/ /_/ /  / /_/ / /  / /_/ / /__/ /  __/
\___/_/   \__, / .___/ \__/\____/   \____/_/   \__,_/\___/_/\___/ 
         /____/_/                                                 
  
  🤖 CryptoOracle AI Trading System | """ + SYSTEM_VERSION + r"""
  ==================================================================
"""

async def run_system_check(logger, exchange, agent, config):
    """启动自检程序"""
    print("\n" + "="*50)
    logger.info("🚀 系统启动 (SYSTEM STARTUP)")
    print("="*50)
    
    try:
        # 检查是否为测试模式
        test_mode = config['trading'].get('test_mode', False)
        
        # 1. 检查 OKX 连接
        total_usdt = 0
        free_usdt = 0
        balance = {}  # 初始化 balance 变量，确保测试模式下也有定义
        
        if test_mode:
            # 测试模式下使用模拟资金
            # 优先使用配置文件中的初始资金值
            if 'risk_control' in config and 'initial_balance_usdt' in config['risk_control']:
                total_usdt = float(config['risk_control']['initial_balance_usdt'])
                free_usdt = total_usdt
            else:
                total_usdt = 10000.00
                free_usdt = 10000.00
            logger.info("✅ 测试模式: 模拟资金初始化")
        else:
            # 实盘模式下从交易所获取真实余额
            balance = await exchange.fetch_balance()
            logger.info("✅ OKX API 连接成功")
            
            # 资金盘点
            if 'USDT' in balance:
                total_usdt = float(balance['USDT']['total'])
                free_usdt = float(balance['USDT']['free'])
            elif 'info' in balance and 'data' in balance['info']: # 统一账户
                 # [Fix] Handle empty data list for Unified Account
                 if balance['info']['data']:
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
    # [Fix] 日志路径动态化
    today_str = datetime.now().strftime('%Y%m%d')
    logger.info(f"💡 [实时日志] tail -f log/crypto_oracle_{today_str}.log")
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

    # [New] 可选日志抑制过滤器（针对噪声警告）
    try:
        import logging as _logging
        suppress_patterns = config['trading'].get('log_suppress_patterns', [])
        if suppress_patterns:
            class MsgSuppressFilter(_logging.Filter):
                def __init__(self, patterns):
                    super().__init__()
                    self.patterns = patterns
                def filter(self, record):
                    msg = record.getMessage()
                    for p in self.patterns:
                        if p in msg and record.levelno <= _logging.WARNING:
                            return False
                    return True
            filt = MsgSuppressFilter(suppress_patterns)
            for h in _logging.getLogger("crypto_oracle").handlers:
                h.addFilter(filt)
            logger.info(f"🔇 已启用日志抑制: {', '.join(suppress_patterns)}")
    except Exception:
        pass

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
        'options': okx_config.get('options', {'defaultType': 'swap'}),
        'enableRateLimit': True
    }
    if proxy:
        exchange_params['aiohttp_proxy'] = proxy

    exchange = ccxt.okx(exchange_params)
    await exchange.load_markets()
    
    # Init Traders
    traders = []
    
    # [New] 注入总币种数量，用于 Auto Allocation
    config['trading']['active_symbols_count'] = len(config['symbols'])
    
    # [New] 并发交易对数量限制
    max_concurrent_traders = config['trading'].get('max_concurrent_traders', 5)
    logger.info(f"⚡ 并发交易对限制: {max_concurrent_traders}")
    
    # 分批初始化交易对
    batch_size = min(max_concurrent_traders, len(config['symbols']))
    for i in range(0, len(config['symbols']), batch_size):
        batch_symbols = config['symbols'][i:i+batch_size]
        batch_traders = []
        
        for symbol_conf in batch_symbols:
            trader = DeepSeekTrader(symbol_conf, config['trading'], exchange, agent)
            await trader.initialize()
            batch_traders.append(trader)
        
        traders.extend(batch_traders)
        
        # 如果不是最后一批，暂停一下
        if i + batch_size < len(config['symbols']):
            logger.info(f"⏳ 已初始化 {len(traders)}/{len(config['symbols'])} 个交易对，休息 2 秒...")
            await asyncio.sleep(2)

    risk_manager = RiskManager(exchange, config['trading'].get('risk_control', {}), traders)
    
    # 初始化插件系统
    logger.info("🔌 初始化插件系统...")
    plugin_manager.load_plugins(config, exchange, agent)
    await plugin_manager.initialize_plugins()
    
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
    
    # [Hack] 即使配置是 "15m"，我们依然可以强制更快的轮询速度
    # 如果用户想在 config.json 里写 "1m" 来避免报错，但又想 30s 跑一次
    # 我们可以在这里硬编码覆盖 interval
    
    # [Smart Interval] 默认轮询间隔设定为 60s
    # 用户痛点: "现在是看15分钟的K线... 循环周期就定在五分钟或者十五分钟... 代码不对"
    # 解释: 机器人必须高频轮询 (如 60s) 才能实现:
    # 1. 及时发现 "三线战法" 等形态的完成 (K线收盘确认)
    # 2. 实时监控止盈止损 (价格/成交量监控)
    # 如果死板地等待 15分钟，会导致严重的信号滞后。
    
    default_interval = 60 # 默认 1分钟检查一次
    
    # 正常解析逻辑 (仅用于校验 Timeframe 格式)
    # if 'm' in timeframe: interval = int(timeframe.replace('m', '')) * 60 ...
    
    # [方案 A] 优先使用 config 中的 loop_interval (如果存在)
    custom_interval = config['trading'].get('loop_interval')
    # [Architecture Update] 频率解耦架构
    # AI 频率: 由 loop_interval 控制 (例如 300s)
    # 监控频率: 固定 60s (或更短)
    # 主循环: 必须按最快频率运行 (60s)，但在内部对 AI 任务进行节流 (Throttle)
    
    ai_loop_interval = 60 # Default fallback
    
    if custom_interval and isinstance(custom_interval, (int, float)) and custom_interval > 0:
        logger.info(f"⚡ [AI配置] AI分析周期: {custom_interval}s (由配置文件控制)")
        ai_loop_interval = custom_interval
    else:
        # 如果没有设定，则使用 Timeframe 动态计算，同原逻辑
        tf_seconds = 900 
        if 'm' in timeframe: tf_seconds = int(timeframe.replace('m', '')) * 60
        elif 'h' in timeframe: tf_seconds = int(timeframe.replace('h', '')) * 3600
        ai_loop_interval = min(60, max(30, int(tf_seconds / 5)))
        logger.info(f"⏰ [智能模式] AI分析周期: {ai_loop_interval}s")

    # 主循环 tick 必须足够快，以满足 1m 监控需求
    # 因此，我们取 min(ai_loop_interval, 60) 作为物理 tick
    main_tick_interval = min(ai_loop_interval, 60)
    logger.info(f"🏎️ [系统核心] 主循环心跳: {main_tick_interval}s (保障软件级高频监控)")
    
    # 将 AI 间隔注入到 trading 配置中，供 Trader 内部节流使用
    config['trading']['actual_ai_interval'] = ai_loop_interval

    logger.info(f"⏰ 最终轮询间隔: {main_tick_interval}秒")
    
    interval = main_tick_interval # Compatible with below logic
    
    # [New] 单频心跳机制 (Unified Loop)
    # 移除了旧版的双频模式 (tick_rate + analysis_tick)，现在统一使用 interval 进行轮询
    # 这样可以避免在"垃圾时间"频繁请求 API，且与"波动率过滤"逻辑更契合
    
    # [Dynamic Interval Support]
    # 如果发现处于 LOW volatility (Grid Mode)，我们可能希望加快轮询速度 (例如 15s)，
    # 因为网格交易需要捕捉微小的回调。
    # 默认 interval 通常跟随 Timeframe (如 15m=900s)，这对于 Grid Mode 来说太慢了。
    
    current_interval = interval
    
    try:
        while True:
            current_ts = time.time()
            
            # 1. 批次执行开始日志
            current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"─" * 60)
            logger.info(f"⏰ 批次执行开始: {current_time_str}")
            logger.info(f"─" * 60)

            # 2. 账户监控与风控检查
            # check() 会打印当前的 PnL 状态
            await risk_manager.check(force_log=True)
            
            # 3. 插件系统 - 每轮循环调用
            await plugin_manager.on_tick({"timestamp": current_ts, "traders": traders})
            
            # 3. 并行执行所有 Traders 的分析与交易任务 (带并发限制)
            max_concurrent_traders = config['trading'].get('max_concurrent_traders', 5)
            results = []
            
            # 分批执行交易任务
            batch_size = min(max_concurrent_traders, len(traders))
            for i in range(0, len(traders), batch_size):
                batch_traders = traders[i:i+batch_size]
                batch_tasks = [trader.run() for trader in batch_traders]
                batch_results = await asyncio.gather(*batch_tasks)
                results.extend(batch_results)
                
                # 如果不是最后一批，暂停一下
                if i + batch_size < len(traders):
                    logger.info(f"⏳ 已处理 {len(results)}/{len(traders)} 个交易对，休息 1 秒...")
                    await asyncio.sleep(1)
            
            # 4. 结构化表格输出
            table_lines = []
            header = f"📊 MARKET SCAN | {len(results)} Symbols"
            table_lines.append(header) 
            table_lines.append("─" * 160)
            table_lines.append(f"{'SYMBOL':<14} | {'PRICE':<10} | {'24H%':<8} | {'PERSONA':<15} | {'RSI':<4} | {'ATR':<4} | {'VOL':<4} | {'PAT':<4} | {'SIGNAL':<8} | {'CONF':<8} | {'EXECUTION':<16} | {'ANALYSIS SUMMARY'}")
            # [Fix] 增加表头分隔线的长度以覆盖所有列
            table_lines.append("─" * 180) 
            
            # [Dynamic Interval Logic]
            # 统计所有交易对的波动率状态，如果任何一个处于 LOW 或 HIGH_TREND，
            # 说明市场有需要密集关注的机会，加速轮询。
            has_active_opportunity = False
            
            for res in results:
                if res:
                    # 插件系统 - 交易执行后调用
                    if res.get('status') == 'EXECUTED':
                        await plugin_manager.on_trade(res)
                    
                    symbol_str = res['symbol'].split(':')[0]
                    # [Fix] 截断过长的 symbol 名称，防止破坏表格结构
                    if len(symbol_str) > 14: symbol_str = symbol_str[:11] + "..."
                    
                    change_val = res['change']
                    change_icon = "🟢" if change_val > 0 else "🔴"
                    change_str = f"{change_val:+.2f}%"
                    
                    # [New] Persona Display
                    # 从 trade_executor 返回的 persona (e.g., "Trend Hunter (趋势猎人)") 中提取短名
                    full_persona = res.get('persona', 'Normal')
                    persona_short = full_persona.split('(')[0].strip()
                    if len(persona_short) > 15: persona_short = persona_short[:15]
                    
                    vol_val = res.get('volatility', 'N/A')
                    if vol_val == 'HIGH_TREND' or vol_val == 'LOW' or vol_val == 'HIGH_CHOPPY':
                        has_active_opportunity = True
                    
                    rsi_val = res.get('rsi')
                    rsi_str = f"{int(rsi_val)}" if rsi_val is not None else "N/A"
                    
                    # [New] Show ATR Ratio & Vol Ratio
                    atr_ratio = res.get('atr_ratio')
                    atr_str = f"{atr_ratio:.1f}" if atr_ratio is not None else "-"
                    
                    vol_ratio = res.get('vol_ratio')
                    vol_str = f"{vol_ratio:.1f}" if vol_ratio is not None else "-"

                    signal = res['signal']
                    sig_icon = "✋"
                    if signal == 'BUY': sig_icon = "🚀"
                    elif signal == 'SELL': sig_icon = "📉"
                    pat = res.get('pattern', '-')
                    pat_display = '-'
                    if pat == 'BULLISH_STRIKE':
                        pat_display = 'BULL'
                    elif pat == 'BEARISH_STRIKE':
                        pat_display = 'BEAR'

                    signal_display = f"{sig_icon} {signal}"
                    
                    conf = res['confidence']
                    conf_display = conf
                    if conf == 'HIGH': conf_display = "🔥 HIGH" # Shortened
                    elif conf == 'MEDIUM': conf_display = "⚡ MED"
                    elif conf == 'LOW': conf_display = "💤 LOW"

                    exec_status = res.get('status', 'N/A')
                    status_icon = "❓"
                    if exec_status == 'EXECUTED': status_icon = "✅"
                    elif exec_status == 'HOLD': status_icon = "⏸️"
                    elif exec_status == 'SKIPPED_FULL': status_icon = "🔒" # 满仓锁
                    elif 'SKIPPED' in exec_status: status_icon = "🚫"
                    elif exec_status == 'FAILED': status_icon = "❌"
                    elif exec_status == 'TEST_MODE': status_icon = "🧪"
                    
                    display_status = exec_status.replace('SKIPPED_', '')
                    if display_status == 'EXECUTED': display_status = 'DONE'
                    elif display_status == 'FULL': display_status = 'FULL' # 显示 FULL
                    exec_display = f"{status_icon} {display_status}"
                    
                    summary_text = res.get('summary', '')
                    if not summary_text or len(summary_text) == 0:
                        reason = res['reason'].replace('\n', ' ')
                        summary_text = reason
                    
                    # [Optimization] 如果理由太长被表格截断，先在上面打印完整版
                    if len(summary_text) > 40:
                        logger.info(f"📜 [详细理由] {symbol_str}: {summary_text}")
                        summary_text = summary_text[:40] + '...'
                    
                    price_str = f"${res['price']:,.2f}"
                    
                    table_lines.append(f"{symbol_str:<14} | {price_str:<10} | {change_icon} {change_str:<5} | {persona_short:<15} | {rsi_str:<4} | {atr_str:<4} | {vol_str:<4} | {pat_display:<4} | {signal_display:<8} | {conf_display:<8} | {exec_display:<16} | {summary_text}")
            
            table_lines.append("─" * 180)
            
            for line in table_lines:
                logger.info(line)
            
            # [Dynamic Interval]
            # 用户要求: 活跃行情的时候不要缩短分析时间，配置多少就按照多少
            current_interval = interval

            
            # 5. 定期记录系统健康状态报告
            loop_count = getattr(main, 'loop_count', 0)
            loop_count += 1
            setattr(main, 'loop_count', loop_count)
            
            # 每执行10次循环记录一次健康状态报告
            if loop_count % 10 == 0:
                health_monitor.log_health_report()
            
            # 6. Sleep
            elapsed = time.time() - current_ts
            logger.info(f"💤 本轮分析耗时 {elapsed:.4f}s")
            
            sleep_time = max(1, current_interval - elapsed)
            logger.info(f"⏳ 休眠 {sleep_time:.2f}s 等待下一轮...")
            
            await asyncio.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("🛑 用户停止程序")
    except Exception as e:
        logger.error(f"Main loop error: {e}")
        # 插件系统 - 发生错误时调用
        await plugin_manager.on_error(e)
    finally:
        # 插件系统 - 关闭插件
        logger.info("🔌 关闭插件系统...")
        await plugin_manager.shutdown_plugins()
        
        await exchange.close()
        # agent.client closes automatically

if __name__ == "__main__":
    # Windows 平台下的 event loop 策略调整
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # 强制 Windows 终端使用 UTF-8 编码，防止中文乱码
        sys.stdout.reconfigure(encoding='utf-8')
    
    # print(f"🔥 正在启动 CryptoOracle 进程 (PID: {os.getpid()})...", flush=True)
    
    # [New] Record PID for stop script
    try:
        with open("bot.pid", "w") as f:
            f.write(str(os.getpid()))
    except:
        pass
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 用户手动停止")
    except Exception as e:
        print(f"❌ 致命错误: {e}")
        import traceback
        traceback.print_exc()
        input("按 Enter 键退出...")
