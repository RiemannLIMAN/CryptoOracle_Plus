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
from services.data.market_data_service import MarketDataService # [New] Import MarketDataService
from services.data.data_manager import DataManager

SYSTEM_VERSION = "v3.9.8 (Strategy Factory Edition)"

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
        
        # print("🚀 系统自检完成")
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

    # [v3.9.6 Fix] Monkey Patch CCXT to prevent NoneType error in parse_market and sorting
    # This fixes the crash during load_markets() when OKX returns incomplete instrument data
    _original_okx_parse_market = ccxt.okx.parse_market
    def _patched_okx_parse_market(self, market):
        try:
            return _original_okx_parse_market(self, market)
        except Exception:
            return None
    ccxt.okx.parse_market = _patched_okx_parse_market

    # Also patch parse_markets to filter out the None results, preventing sort crashes later
    _original_okx_parse_markets = ccxt.okx.parse_markets
    def _patched_okx_parse_markets(self, markets):
        results = _original_okx_parse_markets(self, markets)
        return [m for m in results if m is not None]
    ccxt.okx.parse_markets = _patched_okx_parse_markets

    exchange = ccxt.okx(exchange_params)
    await exchange.load_markets()
    
    # [New] Initialize MarketDataService
    # 这里我们初始化一个新的 DataManager 实例传给 MarketDataService
    # 注意: TradeExecutor 内部也会初始化自己的 DataManager，但这没关系，只要数据库路径一样就行
    data_manager = DataManager(config['trading'].get('db_path', 'data/market_data.db'))
    # [Fix] 必须显式初始化全局数据库，否则 MarketDataService 写入时会报错 (no such table)
    await data_manager.initialize()
    
    market_data_service = MarketDataService(exchange, data_manager, logger)
    
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
            trader = DeepSeekTrader(
                symbol_conf, 
                config['trading'], 
                exchange, 
                agent,
                market_data_service=market_data_service # [New] Inject Service
            )
            await trader.initialize()
            batch_traders.append(trader)
        
        traders.extend(batch_traders)
        
        # 如果不是最后一批，暂停一下
        if i + batch_size < len(config['symbols']):
            logger.debug(f"⏳ 已初始化 {len(traders)}/{len(config['symbols'])} 个交易对，休息 2 秒...")
            await asyncio.sleep(2)

    risk_manager = RiskManager(exchange, config['trading'].get('risk_control', {}), traders)
    
    # 初始化插件系统
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
    
    

    # [Architecture Update] 频率解耦架构
    # AI 频率: 由 ai_interval 控制 (例如 300s)
    # 监控频率: 由 loop_interval 控制 (默认 60s)
    # 主循环: 必须按最快频率运行 (60s)，但在内部对 AI 任务进行节流 (Throttle)
    
    # 1. 获取 AI 分析间隔 (Strategy Level)
    ai_interval_conf = config['trading'].get('strategy', {}).get('ai_interval')
    if not ai_interval_conf:
        # 兼容旧配置: 如果没配 ai_interval，尝试从 loop_interval 或 timeframe 推导
        ai_interval_conf = config['trading'].get('loop_interval', 60)
    
    ai_loop_interval = int(ai_interval_conf)

    # 2. 获取主循环间隔 (System Level)
    sys_loop_interval = config['trading'].get('loop_interval', 60)
    main_tick_interval = int(sys_loop_interval)
    
    # 将 AI 间隔注入到 trading 配置中，供 Trader 内部节流使用
    config['trading']['actual_ai_interval'] = ai_loop_interval

    interval = main_tick_interval # Compatible with below logic

    # [User Request] 恢复启动概览表格
    await risk_manager.initialize_baseline(start_equity)
    risk_manager.display_pnl_history()
    
    logger.info("🏁 初始化完成，进入主循环...")
    
    # --- 进入主循环 ---
    timeframe = config['trading']['timeframe']
    
    # [New] 单频心跳机制 (Unified Loop)
    # 移除了旧版的双频模式 (tick_rate + analysis_tick)，现在统一使用 interval 进行轮询
    # 这样可以避免在"垃圾时间"频繁请求 API，且与"波动率过滤"逻辑更契合
    
    # [Dynamic Interval Support]
    # 如果发现处于 LOW volatility (Grid Mode)，我们可能希望加快轮询速度 (例如 15s)，
    # 因为网格交易需要捕捉微小的回调。
    # 默认 interval 通常跟随 Timeframe (如 15m=900s)，这对于 Grid Mode 来说太慢了。
    
    current_interval = interval
    
    # [v3.9.7 New] 全局热重载状态
    last_config_mtime = os.path.getmtime('config.json')

    try:
        while True:
            current_ts = time.time()
            
            # [v3.9.7 New] 全局配置同步 (增删币种热重载)
            try:
                mtime = os.path.getmtime('config.json')
                if mtime > last_config_mtime:
                    last_config_mtime = mtime
                    logger.info("🔄 [SYSTEM] 检测到 config.json 列表更新，正在同步交易对...")
                    
                    # 重新加载配置
                    new_config_obj = Config('config.json')
                    new_config = new_config_obj.data
                    
                    # 1. 识别新增币种
                    existing_symbols = {t.symbol for t in traders}
                    new_symbols_conf = {s['symbol']: s for s in new_config['symbols']}
                    
                    # 增加新币种
                    added_count = 0
                    for sym, sym_conf in new_symbols_conf.items():
                        if sym not in existing_symbols:
                            logger.info(f"🆕 [SYSTEM] 发现新币种: {sym}, 正在初始化 Trader...")
                            try:
                                new_trader = DeepSeekTrader(
                                    sym_conf, 
                                    new_config['trading'], 
                                    exchange, 
                                    agent,
                                    market_data_service=market_data_service
                                )
                                await new_trader.initialize()
                                traders.append(new_trader)
                                added_count += 1
                            except Exception as e:
                                logger.error(f"❌ [SYSTEM] 初始化新币种 {sym} 失败: {e}")
                    
                    # 2. 识别并移除已删除币种
                    new_symbols_set = set(new_symbols_conf.keys())
                    to_remove = []
                    for t in traders:
                        if t.symbol not in new_symbols_set:
                            logger.info(f"🗑️ [SYSTEM] 币种已从配置移除: {t.symbol}, 正在停止 Trader...")
                            to_remove.append(t)
                    
                    for t in to_remove:
                        traders.remove(t)
                    
                    if added_count > 0 or to_remove:
                        logger.info(f"✅ [SYSTEM] 同步完成: 新增 {added_count}, 移除 {len(to_remove)}, 当前共 {len(traders)} 个币种")
                        # 更新全局配置引用
                        config = new_config
                        # 更新活跃币种计数
                        config['trading']['active_symbols_count'] = len(traders)
                        # 更新风控管理器的交易员列表
                        risk_manager.traders = traders
                        
            except Exception as e:
                logger.error(f"⚠️ [SYSTEM] 同步配置失败: {e}")

            # 1. 批次执行开始日志 (静默模式)
            # current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # logger.info(f"─" * 60)
            # logger.info(f"⏰ 批次执行开始: {current_time_str}")
            # logger.info(f"─" * 60)

            # 2. 账户监控与风控检查
            # check() 会打印当前的 PnL 状态
            await risk_manager.check(force_log=False) # [User Request] 关闭风控日志强制打印
            
            # 3. 插件系统 - 每轮循环调用
            await plugin_manager.on_tick({"timestamp": current_ts, "traders": traders})
            
            # 3. 并行执行所有 Traders 的分析与交易任务 (P1-4.4: 彻底隔离任务，消除木桶效应)
            max_concurrent_traders = config['trading'].get('max_concurrent_traders', 5)
            semaphore = asyncio.Semaphore(max_concurrent_traders)
            
            async def run_trader_isolated(trader):
                async with semaphore:
                    try:
                        return await trader.run()
                    except Exception as e:
                        logger.error(f"❌ [{trader.symbol}] 执行异常: {e}")
                        return {'symbol': trader.symbol, 'status': 'ERROR', 'error': str(e)}

            # 创建所有任务并同时启动 (受 Semaphore 限制并发数)
            tasks = [run_trader_isolated(t) for t in traders]
            results = await asyncio.gather(*tasks)
            
            # 4. 结构化表格输出
            table_lines = []

            # [User Request] 移除表格上方所有 "交易执行" 相关的 JSON 打印
            # 这行代码之前是在表格循环外部打印的，现在将其移除
            # for res in results:
            #     if res.get('status') == 'EXECUTED':
            #         logger.info(f"交易执行: {res}")
            
            # [UI] 打印市场扫描表格
            # 计算动态总宽度，使其与分隔线一致
            # 目前列宽定义: 14+3 + 10+3 + 8+3 + 15+3 + 4+3 + 4+3 + 4+3 + 4+3 + 8+3 + 8+3 + 16+3 = 119 chars approx + summary
            # 分隔线长度需要足够长以覆盖所有列
            separator_line = "─" * 180 
            
            # [User Request] 移除表格上方所有冗余打印
            # logger.info("📊 MARKET SCAN | {} Symbols".format(len(results)))
            # logger.info(separator_line) # 上分割线也移除
            
            # [Fix] 移除表格上方的所有非必要日志，只保留表头
            # 下面的 INFO 日志其实是 risk_manager.check() 打印的，需要静默它
            # 但 risk_manager.check(force_log=False) 已经设置了
            # 剩下的那些 INFO [RIVER/USDT] 数量修正... 是在 trade_executor.run() 里打印的
            # 我们需要去 trade_executor 里把那些日志也静默掉
            
            # Header
            summary_line = risk_manager.get_summary_line(results)
            if summary_line:
                logger.info(f"─" * 60)
                logger.info(summary_line)
            
            header_str = (
                f"{'SYMBOL':<14} | "
                f"{'PRICE':<10} | "
                f"{'24H%':<8} | "  # Adjusted width
                f"{'PERSONA':<15} | "
                f"{'RSI':<4} | "
                f"{'ATR':<4} | "
                f"{'VOL':<4} | "
                f"{'PAT':<4} | "
                f"{'SIGNAL':<8} | "
                f"{'CONF':<8} | "
                f"{'EXECUTION':<16} | "
                f"{'ANALYSIS SUMMARY'}"
            )
            logger.info(separator_line)
            logger.info(header_str)
            logger.info(separator_line)
            # logger.info(separator_line) # Double line [Removed]
            
            # [Dynamic Interval Logic]
            # 统计所有交易对的波动率状态，如果任何一个处于 LOW 或 HIGH_TREND，
            # 说明市场有需要密集关注的机会，加速轮询。
            has_active_opportunity = False
            
            for res in results:
                if res:
                    # [Fix] 移除 DEBUG 打印，避免污染输出
                    # if res.get('status') == 'UNKNOWN':
                    #    logger.warning(f"DEBUG: Found UNKNOWN status in res: {res}")
                        
                    # 插件系统 - 交易执行后调用
                    # [User Request] 移除表格上方所有 "交易执行" 相关的 JSON 打印
                    # 原本这里可能还有其他地方在打印 res，确保彻底移除
                    # if res.get('status') == 'EXECUTED':
                    #     logger.info(f"交易执行: {res}")

                    # 检查是否有活跃机会 (用于动态心跳)
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

                    exec_status = res.get('status', 'WAIT') # Default to WAIT
                    status_icon = "❓"
                    if exec_status == 'EXECUTED': status_icon = "✅"
                    elif exec_status == 'HOLD': status_icon = "⏸️"
                    elif exec_status == 'HOLD_DUP': status_icon = "⏸️" # [Fix] HOLD_DUP is also a HOLD state
                    elif exec_status == 'SKIPPED_FULL': status_icon = "🔒" # 满仓锁
                    elif 'SKIPPED' in exec_status: status_icon = "🚫"
                    elif exec_status == 'FAILED': status_icon = "❌"
                    elif exec_status == 'TEST_MODE': status_icon = "🧪"
                    elif exec_status == 'WAIT' or exec_status == 'UNKNOWN': status_icon = "⏳" 
                    
                    display_status = exec_status.replace('SKIPPED_', '')
                    if display_status == 'EXECUTED': display_status = 'DONE'
                    elif display_status == 'FULL': display_status = 'FULL' 
                    elif display_status == 'UNKNOWN' or display_status == 'WAIT': display_status = 'WAIT'
                    elif display_status == 'HOLD_DUP': display_status = 'HOLD' # [Fix] Display HOLD for dup
                    
                    exec_display = f"{status_icon} {display_status}"
                    
                    summary_text = res.get('summary', '')
                    if not summary_text or len(summary_text) == 0:
                        reason = res['reason'].replace('\n', ' ')
                        summary_text = reason
                    
                    # [Optimization] 如果理由太长被表格截断，先在上面打印完整版
                    # [Config] 用户希望减少表格上方的打印，仅在真正有交易动作(EXECUTED)时才打印长理由
                    # 否则监控状态下的长文本只在表格内截断显示
                    if len(summary_text) > 40:
                        # [Modified] 用户明确要求移除表格上方的打印，认为其冗余且浪费时间
                        # 即使是 EXECUTED 状态，用户也倾向于只看表格或精简信息
                        # 因此彻底移除此处的 logger.info 调用
                        # if exec_status == 'EXECUTED':
                        #    logger.info(f"📜 [详细理由] {symbol_str}: {summary_text}")
                        
                        # 表格有足够的宽度 (180字符)，我们可以让 summary 稍微长一点
                        # 或者我们接受表格被撑开，只要不换行就行
                        # 这里我们放宽到 60 字符
                        summary_text = summary_text[:60] + '...'
                    
                    # [Fix] 临时修复：如果 status 是 UNKNOWN，强制改写为 WAIT
                    # 防止因为上游返回了 UNKNOWN 导致表格显示 WAIT 但日志里有 WARNING
                    if exec_status == 'UNKNOWN':
                        # [Optimization] 如果是 AI 冷却期间导致的 UNKNOWN (通常是因为 ai_interval 限制)
                        # 我们显示一个更友好的 "MONITOR" 状态
                        exec_status = 'WAIT'
                        status_icon = "⏳"
                        display_status = 'WAIT'
                        exec_display = f"{status_icon} {display_status}"
                        
                        # 如果 reason 里包含 "AI冷却"，显示为监控中
                        if "AI冷却" in res.get('reason', ''):
                            status_icon = "👀"
                            display_status = "SCAN"
                            exec_display = f"{status_icon} {display_status}"
                    
                    price_str = f"${res['price']:,.2f}"
                    
                    # [Optimization] 动态列宽适配
                    # 确保表格不会因为中文字符宽度问题导致错位
                    # 中文字符通常占 2 个显示宽度，len() 只算 1 个，所以需要手动补全
                    def pad_str(s, width):
                        # 简单的中文宽度补偿算法
                        import re
                        chinese_char_count = len(re.findall(r'[\u4e00-\u9fa5]', str(s)))
                        real_width = len(str(s)) + chinese_char_count
                        padding = width - real_width
                        return str(s) + ' ' * max(0, padding)

                    # 重新格式化行，使用 pad_str 处理包含中文的字段 (persona_short, summary_text)
                    line_str = (
                        f"{symbol_str:<14} | "
                        f"{price_str:<10} | "
                        f"{change_icon} {change_str:<5} | "
                        f"{pad_str(persona_short, 15)} | "  # Persona 可能含中文
                        f"{rsi_str:<4} | "
                        f"{atr_str:<4} | "
                        f"{vol_str:<4} | "
                        f"{pat_display:<4} | "
                        f"{signal_display:<8} | "
                        f"{conf_display:<8} | "
                        f"{exec_display:<16} | "
                        f"{summary_text}"
                    )
                    table_lines.append(line_str)
            
            table_lines.append("─" * 180)
            
            for line in table_lines:
                 # 不需要再过滤了，因为 header 已经直接打印了
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
            # logger.info(f"💤 本轮分析耗时 {elapsed:.4f}s")
            
            sleep_time = max(1, current_interval - elapsed)
            logger.debug(f"⏳ 休眠 {sleep_time:.2f}s 等待下一轮...")
            logger.info("") # Empty line for better readability
            
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
    
    # [New] Record PID for stop script in log folder
    try:
        if not os.path.exists("log"):
            os.makedirs("log")
        with open("log/bot.pid", "w") as f:
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
