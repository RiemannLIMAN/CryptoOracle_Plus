import os
import json
import time
import logging
import asyncio
# import aiohttp # [Fix] Removed unused import
import pandas as pd
from datetime import datetime

# Local imports - if run from main.py via src, these should work with sys.path hack
# But inside package, better use relative or absolute
# Assuming running from src root context
from core.utils import to_float, send_notification_async

class RiskManager:
    """全局风控管理器 (Async)"""
    def __init__(self, exchange, risk_config, traders):
        self.logger = logging.getLogger("crypto_oracle")
        self.exchange = exchange
        self.config = risk_config
        self.traders = traders
        self.is_test_mode = False
        try:
            if traders and hasattr(traders[0], 'common_config'):
                self.is_test_mode = bool(traders[0].common_config.get('test_mode', False))
        except Exception:
            pass
        self.initial_balance = risk_config.get('initial_balance_usdt', 0)
        
        self.max_profit = risk_config.get('max_profit_usdt')
        self.max_loss = risk_config.get('max_loss_usdt')
        self.max_profit_pct = risk_config.get('max_profit_rate')
        self.max_loss_pct = risk_config.get('max_loss_rate')
        
        self.smart_baseline = None
        self.deposit_offset = 0.0 # [New] 充值/闲置资金抵扣额
        
        # 获取项目根目录 (src/services/risk -> src/services -> src -> root)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.data_dir = os.path.join(project_root, "data")
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        self.state_file = os.path.join(self.data_dir, "bot_state.json")
        self.csv_file = os.path.join(self.data_dir, "pnl_history.csv")
        if self.is_test_mode:
            self.csv_file = os.path.join(self.data_dir, "pnl_history_sim.csv")
        
        self.load_state()
        
        self.notification_config = {}
        if traders and hasattr(traders[0], 'notification_config'):
             self.notification_config = traders[0].notification_config

        self.chart_dir = os.path.join(project_root, "png")
        if not os.path.exists(self.chart_dir):
            os.makedirs(self.chart_dir)
        self.chart_path = os.path.join(self.chart_dir, f"pnl_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        self.last_chart_display_time = 0
        self.is_initialized = False # [Fix] 强制初始化标记，确保每次重启都重新校准 offset
        
        # [v3.9.6 New] Daily Profit Lock Tracking
        self.daily_start_equity = 0.0
        self.daily_date = ""
        self.is_risk_reduced = False

    def load_state(self):
        # 不加载历史基准资金，始终使用配置文件中的初始资金
        self.smart_baseline = None
        self.deposit_offset = 0.0
        self.logger.info("✅ 使用配置文件中的初始资金，不加载历史基准")

    def save_state(self):
        try:
            state = {
                'smart_baseline': self.smart_baseline,
                'deposit_offset': self.deposit_offset
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
        except Exception as e:
            self.logger.warning(f"⚠️ 保存状态失败: {e}")

    def _log(self, msg, level='info'):
        if level == 'info':
            self.logger.info(f"[RISK_MGR] {msg}")
        elif level == 'error':
            self.logger.error(f"[RISK_MGR] {msg}")
        elif level == 'debug':
            self.logger.debug(f"[RISK_MGR] {msg}")

    async def send_notification(self, message, title=None):
        """发送通知 (Async)"""
        if not self.notification_config.get('enabled', False):
            return
        webhook_url = self.notification_config.get('webhook_url')
        
        # 移除旧的 wrapper
        final_title = title if title else "🛡️ 风控通知"
        await send_notification_async(webhook_url, message, title=final_title)

    async def record_pnl_to_csv(self, total_equity, current_pnl, pnl_percent):
        """Async 记录 PnL 并生成图表 (非阻塞)"""
        file_exists = os.path.isfile(self.csv_file)
        try:
            # 1. 写入 CSV (使用 asyncio.to_thread 避免文件IO阻塞)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            line = f"{timestamp},{total_equity:.2f},{current_pnl:.2f},{pnl_percent:.2f}\n"
            
            def write_csv_sync():
                with open(self.csv_file, 'a', encoding='utf-8') as f:
                    if not file_exists:
                        f.write("timestamp,total_equity,pnl_usdt,pnl_percent\n")
                    f.write(line)
            
            await asyncio.to_thread(write_csv_sync)
            
            # 2. 生成图表 (Matplotlib 很慢，必须放到后台线程/进程)
            try:
                # 使用 asyncio.to_thread 将绘图任务扔到后台线程执行
                # 这样就不会阻塞主循环的 await check()
                await asyncio.to_thread(self._generate_chart_in_background)
            except Exception as e:
                self._log(f"调度图表生成任务失败: {e}", 'warning')

        except Exception as e:
            self._log(f"写入CSV失败: {e}", 'error')

    def _generate_chart_in_background(self):
        """后台线程执行绘图"""
        try:
            from core import plotter
            plotter.generate_pnl_chart(csv_path=self.csv_file, output_path=self.chart_path, verbose=False)
            self.logger.debug(f"盈亏折线图已更新: {self.chart_path}")
        except Exception as e:
            self._log(f"生成折线图失败: {e}", 'warning')

    async def close_all_traders(self):
        self._log("🛑 正在执行全局清仓...")
        # [Fix] 使用 gather(return_exceptions=True) 确保所有清仓任务都被尝试，即使部分失败
        # 并且检查结果，记录失败的任务
        tasks = [trader.close_all_positions() for trader in self.traders]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        failures = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                trader_name = self.traders[i].symbol
                failures.append(f"{trader_name}: {res}")
                
        if failures:
            self._log(f"⚠️ 部分清仓失败: {'; '.join(failures)}", 'error')
            # 可以在这里加入重试逻辑，或者至少发通知
            fail_msg = '\n'.join(failures)
            await self.send_notification(f"⚠️ 全局清仓部分失败!\n{fail_msg}", title="🚑 清仓异常")

    async def calculate_realized_performance(self):
        """基于交易所历史订单计算已实现盈亏与胜率 (Parallel with Configured Cooldown)"""
        # [Cooldown] 防止过于频繁调用交易所 API
        # 默认冷却时间为 5 分钟，但如果 loop_interval 更长，则跟随 loop_interval
        # 或者我们可以在 config.json 的 trading 部分添加一个 'stats_interval'
        # 这里暂时使用 loop_interval 的 5 倍作为默认值，或者硬编码 300s
        
        # 获取配置的 loop_interval
        loop_interval = 60
        if self.traders and hasattr(self.traders[0], 'common_config'):
             loop_interval = self.traders[0].common_config.get('loop_interval', 60)
        
        # 冷却时间严格跟随 loop_interval，不再强制最低 60s
        # 用户既然配置了高频，说明他能接受高频的 API 消耗
        cooldown_seconds = loop_interval
        
        current_time = time.time()
        if hasattr(self, 'last_realized_calc_time'):
            if current_time - self.last_realized_calc_time < cooldown_seconds:
                return

        try:
            self.last_realized_calc_time = current_time
            sep_line = "=" * 80
            
            # 使用 asyncio.gather 并行获取所有交易员的历史订单
            # tasks = [trader.exchange.fetch_my_trades(trader.symbol, limit=100) for trader in self.traders]
            # results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 为了保留 trader 信息，我们构造一个辅助函数
            async def fetch_trader_stats(trader):
                try:
                    # [Fix] Use get_my_trades wrapper to support simulation
                    trades = await trader.get_my_trades(limit=100)
                    return {'trader': trader, 'trades': trades, 'error': None}
                except Exception as e:
                    return {'trader': trader, 'trades': None, 'error': str(e)}

            fetch_tasks = [fetch_trader_stats(t) for t in self.traders]
            results = await asyncio.gather(*fetch_tasks)

            total_realized_pnl = 0.0
            total_trades = 0
            win_trades = 0
            
            has_data = False
            report_body = ""
            
            for res in results:
                trader = res['trader']
                if res['error']:
                    self._log(f"计算 {trader.symbol} 绩效失败: {res['error']}", 'warning')
                    continue
                    
                trades = res['trades']
                if not trades:
                    continue
                
                symbol_pnl = 0.0
                symbol_wins = 0
                symbol_count = 0
                
                for trade in trades:
                    # 仅统计有 PnL 的订单 (通常是合约平仓单)
                    pnl = 0.0
                    if 'info' in trade and 'pnl' in trade['info']:
                        try:
                            pnl = float(trade['info']['pnl'])
                        except:
                            pnl = 0.0
                    
                    if pnl != 0:
                        symbol_pnl += pnl
                        symbol_count += 1
                        if pnl > 0:
                            symbol_wins += 1
                
                if symbol_count > 0:
                    has_data = True
                    win_rate = (symbol_wins / symbol_count) * 100
                    pnl_icon = "🟢" if symbol_pnl > 0 else "🔴"
                    report_body += f"\n{trader.symbol:<15} | 交易: {symbol_count:<3} | 胜率: {win_rate:>5.1f}% | 累计盈亏: {symbol_pnl:+.2f} U {pnl_icon}"
                    
                    total_realized_pnl += symbol_pnl
                    total_trades += symbol_count
                    win_trades += symbol_wins
            
            if has_data:
                 report = f"\n{sep_line}\n📊 实盘数据统计 (Performance Stats)\n{sep_line}"
                 report += report_body
                 
                 if total_trades > 0:
                    avg_win_rate = (win_trades / total_trades) * 100
                    report += f"\n{sep_line}\n🏆 总计表现     | 交易: {total_trades:<3} | 胜率: {avg_win_rate:>5.1f}% | 总盈亏: {total_realized_pnl:+.2f} U"
                    
                    # [New] 缓存已实现盈亏，供 check() 函数进行自我校准
                    self.realized_pnl_cache = total_realized_pnl
                 
                 report += f"\n{sep_line}"
                 self.logger.info(report)
            else:
                self.realized_pnl_cache = 0.0
            
        except Exception as e:
            self._log(f"生成实盘统计失败: {e}", 'error')

    async def display_recent_trades(self):
        """显示最近成交记录 (真实战绩)"""
        try:
            sep_line = "=" * 80
            
            for trader in self.traders:
                try:
                    # 获取最近 5 条成交
                    # [Fix] Use get_my_trades wrapper to support simulation
                    trades = await trader.get_my_trades(limit=5)
                    if not trades:
                        continue
                    
                    # 只有当有数据时才打印标题
                    self.logger.info(f"\n{sep_line}\n📜 历史战绩回顾 (Trade History)\n{sep_line}")
                        
                    for trade in reversed(trades): # 时间正序
                        # 解析字段
                        symbol = trade['symbol']
                        side = trade['side'].upper() # BUY/SELL
                        price = float(trade['price'])
                        amount = float(trade['amount'])
                        cost = float(trade['cost']) if trade.get('cost') else price * amount
                        fee = 0.0
                        if trade.get('fee'):
                            fee = float(trade['fee']['cost'])
                        
                        ts = datetime.fromtimestamp(trade['timestamp']/1000).strftime('%m-%d %H:%M')
                        
                        icon = "🟢" if side == 'BUY' else "🔴"
                        
                        # 尝试计算 PnL (仅限合约平仓单)
                        # OKX 的 fetch_my_trades 返回的数据结构里，info 字段可能包含 pnl
                        pnl_str = ""
                        if 'info' in trade and 'pnl' in trade['info']:
                            pnl = float(trade['info']['pnl'])
                            if pnl != 0:
                                pnl_icon = "🎉" if pnl > 0 else "💸"
                                pnl_str = f" | PnL: {pnl:+.2f} U {pnl_icon}"
                        
                        log_str = f"{ts} | {symbol} | {icon} {side:<4} | 价格: {price} | 数量: {amount} | 金额: {cost:.2f} U{pnl_str}"
                        self.logger.info(log_str)
                    
                    self.logger.info(sep_line + "\n")
                        
                except Exception as e:
                    self._log(f"获取 {trader.symbol} 历史成交失败: {e}", 'warning')
            
        except Exception as e:
            self._log(f"显示成交记录失败: {e}", 'error')

    def display_pnl_history(self):
        # 保持同步方法
        if not os.path.isfile(self.csv_file):
            return
        try:
            df = pd.read_csv(self.csv_file)
            if df.empty: return
            
            # [Reverted] 恢复为经典的 "历史盈亏回顾" 标题，这才是用户记忆中的设计
            header = "\n" + "="*40 + f"\n 历史盈亏回顾 (共 {len(df)} 条记录)\n" + "="*40
            self.logger.info(header)
            # print(header) # Duplicate print removed
              
            recent = df.tail(10)
            max_pnl = recent['pnl_usdt'].abs().max()
            scale_factor = 1.0
            if max_pnl > 0:
                if max_pnl < 1.5: scale_factor = 10.0
                elif max_pnl < 5: scale_factor = 2.0
                elif max_pnl > 20: scale_factor = 0.5
            
            for _, row in recent.iterrows():
                timestamp = row['timestamp'][5:-3]
                pnl = row['pnl_usdt']
                
                # [Fix] 恢复原始逻辑，只显示 PnL，不显示复杂公式
                # 您的原始截图显示的是：12-28 20:02 | 4.16 U | [绿条]
                # 这里必须严格还原那个格式
                
                bar = ""
                num_blocks = abs(pnl) * scale_factor
                full_blocks = int(num_blocks)
                
                if pnl > 0:
                    bar = "▫️" if full_blocks == 0 and num_blocks > 0.1 else "🟩" * min(full_blocks, 20)
                elif pnl < 0:
                    bar = "▪️" if full_blocks == 0 and num_blocks > 0.1 else "🟥" * min(full_blocks, 20)
                else:
                    bar = "➖"
                
                # 严格还原格式: "时间 | 盈亏 U | 进度条"
                line = f"{timestamp} | {pnl:>6.2f} U | {bar}"
                self.logger.info(line)
                # print(line) # Duplicate print removed
            
            footer = "="*30 + "\n"
            self.logger.info(footer)
            # print(footer) # Duplicate print removed
            
            # 更新最后显示时间，防止短时间内重复打印
            self.last_chart_display_time = time.time()
        except Exception as e:
            self._log(f"显示历史战绩失败: {e}", 'warning')

    async def _verify_funding_flow(self, pnl_delta):
        """查询交易所流水，核实是否为充提币 (Fact-based Check)"""
        try:
            if not self.traders: return False
            
            # 使用第一个 trader 的 exchange 实例
            exchange = self.traders[0].exchange
            
            # 查询最近 5 条流水 (USDT)
            # 注意：OKX 的 bill type 很多，ccxt 会统一映射
            ledger = await exchange.fetch_ledger('USDT', limit=5)
            
            # 过滤最近 2 分钟内的记录
            now_ms = time.time() * 1000
            recent_flows = [
                entry for entry in ledger 
                if (now_ms - entry['timestamp']) < 120 * 1000
            ]
            
            confirmed_amount = 0.0
            flow_found = False
            
            # [Fix] 充值去重逻辑
            # 使用 Set 记录已处理的流水 ID，避免在多次轮询中重复计算同一笔充值
            if not hasattr(self, 'processed_ledger_ids'):
                self.processed_ledger_ids = set()

            for entry in recent_flows:
                # [Fix] Skip if already processed
                if entry['id'] in self.processed_ledger_ids:
                    continue

                amount = float(entry['amount'])
                flow_type = entry['type'] # deposit, withdrawal, transfer
                
                # 匹配充值
                if pnl_delta > 0 and flow_type in ['deposit', 'transfer']:
                    # transfer 只有当 amount > 0 时才算转入
                    if amount > 0:
                        confirmed_amount += amount
                        flow_found = True
                        self.processed_ledger_ids.add(entry['id'])
                        self._log(f"🧾 账本核实: 发现充值/转入 +{amount} U (ID: {entry['id']})")

                # 匹配提现
                elif pnl_delta < 0 and flow_type in ['withdrawal', 'transfer']:
                    # transfer 只有当 amount < 0 时才算转出
                    # ccxt withdrawal amount is usually negative
                    if amount < 0:
                        confirmed_amount += amount # amount is negative
                        flow_found = True
                        self.processed_ledger_ids.add(entry['id'])
                        self._log(f"🧾 账本核实: 发现提现/转出 {amount} U (ID: {entry['id']})")
            
            if flow_found:
                self.deposit_offset += confirmed_amount
                self._log(f"🔄 自动校准 Offset: {self.deposit_offset:.2f} U (基于账本)")
                self.save_state()
                return True
                
        except Exception as e:
            # 某些 API Key 可能没有权限查账单，或者 fetch_ledger 不支持
            # self._log(f"查账失败 (可能是权限不足): {e}", 'debug')
            pass
            
        return False

    def get_summary_line(self, results):
        """获取简短的资金与持仓摘要 (用于表格上方打印)"""
        if not hasattr(self, 'current_equity'):
            return ""
            
        pnl_pct = (self.current_pnl / self.smart_baseline * 100) if self.smart_baseline > 0 else 0.0
        
        # 统计持仓数量
        pos_count = 0
        if results:
            for res in results:
                if res and res.get('has_position'):
                    pos_count += 1
        
        pnl_icon = "📈" if self.current_pnl >= 0 else "📉"
        
        summary = (
            f"💰 当前权益: {self.current_equity:.2f} U | "
            f"{pnl_icon} 盈亏: {self.current_pnl:+.2f} U ({pnl_pct:+.2f}%) | "
            f"📦 持仓: {pos_count} 个交易对"
        )
        return summary

    async def check(self, force_log=False):
        """执行风控检查 (Async)"""
        try:
            total_equity = 0
            found_usdt = False
            used_total_eq = False
            if self.is_test_mode:
                # [Fix] 测试模式下，使用所有交易对的sim_balance总和作为total_equity
                # 必须包含未实现盈亏，否则无法正确反映浮亏
                eq_sum = 0.0
                for t in self.traders:
                    _, sim_equity = await t.get_account_info()
                    eq_sum += sim_equity
                total_equity = eq_sum
                found_usdt = True
                used_total_eq = True
            else:
                balance = await self.exchange.fetch_balance()

            if not self.is_test_mode and 'info' in balance and 'data' in balance['info']:
                # [Fix] Handle empty data list for Unified Account
                if balance['info']['data']:
                    data0 = balance['info']['data'][0]
                    # [优化] 优先使用 totalEq (统一账户总权益，已折算为 USD/USDT)
                    if 'totalEq' in data0:
                        total_equity = float(data0['totalEq'])
                        found_usdt = True
                        used_total_eq = True
                    else:
                        for asset in data0['details']:
                            if asset['ccy'] == 'USDT':
                                total_equity = float(asset['eq'])
                                found_usdt = True
                                break
            
            if not self.is_test_mode and not found_usdt:
                if 'USDT' in balance and 'equity' in balance['USDT']:
                    total_equity = float(balance['USDT']['equity'])
                elif 'USDT' in balance and 'total' in balance['USDT']:
                     total_equity = float(balance['USDT']['total'])
            
            if total_equity <= 0:
                return

            # [v3.9.6 New] Daily Profit Lock Logic
            today = datetime.now().strftime('%Y-%m-%d')
            if self.daily_date != today:
                self.daily_date = today
                self.daily_start_equity = total_equity
                self.is_risk_reduced = False
                self._log(f"📅 新的一天开始，初始权益: {self.daily_start_equity:.2f} U", 'info')

            if self.daily_start_equity > 0 and not self.is_risk_reduced:
                daily_pnl_pct = (total_equity - self.daily_start_equity) / self.daily_start_equity
                if daily_pnl_pct > 0.15: # 当日盈利 > 15%
                    self.is_risk_reduced = True
                    self._log(f"💎 [DAILY PROFIT LOCK] 当日收益率 {daily_pnl_pct*100:.2f}% 已达标 (15%)，触发防御模式（降低仓位比例）", 'info')
                    await self.send_notification(
                        f"💎 **每日利润锁定触发**\n当日收益率: `{daily_pnl_pct*100:.2f}%`\n> **系统已自动调低仓位比例，保护利润!**",
                        title="💎 利润保护 | 全局"
                    )
                    # 动态调低所有交易员的仓位建议
                    for trader in self.traders:
                        if hasattr(trader, 'position_manager'):
                            trader.position_manager.global_risk_factor = 0.5 # 降至 50% 仓位

            # [Fix] 每次重启强制进入初始化流程，重新计算 offset，而不是仅依赖 baseline 是否为空
            if not self.is_initialized:
                await self.initialize_baseline(total_equity)
            
            # [Fix] 首次运行时，为了消除 initialize_baseline 和 check 之间的时间差导致的微小波动
            # 我们在第一次 check 时强制对齐基准 (仅当波动非常小时)
            if not hasattr(self, 'last_known_pnl') and self.smart_baseline:
                diff = total_equity - self.smart_baseline
                # 如果偏差在 -0.5 ~ 0.5 U 之间，且不是充值导致的（deposit_offset 为 0 或稳定），则视为抖动
                if abs(diff) < 0.5:
                    self.smart_baseline = total_equity
                    # self._log(f"🔧 启动微调: 消除时间差波动 ({diff:+.4f} U) -> PnL 归零", 'debug')
            
            current_total_value = total_equity
            
            # 批量获取价格 (Async)
            symbols_to_fetch = [t.symbol for t in self.traders if t.trade_mode == 'cash']
            prices = {}
            if symbols_to_fetch and not used_total_eq:
                try:
                    tickers = await self.exchange.fetch_tickers(symbols_to_fetch)
                    for s, t in tickers.items():
                        prices[s] = t['last']
                except Exception as e:
                    self._log(f"获取价格失败: {e}", 'warning')

            # 只有当没有使用 totalEq 时，才需要手动累加现货资产价值
            # 因为 totalEq 通常已经包含了所有资产的折算价值
            if not used_total_eq:
                for trader in self.traders:
                    if trader.trade_mode == 'cash':
                            spot_bal = await trader.get_spot_balance()
                            if spot_bal > 0:
                                price = prices.get(trader.symbol, 0)
                                if price == 0:
                                    try:
                                        ticker = await self.exchange.fetch_ticker(trader.symbol)
                                        price = ticker['last']
                                    except:
                                        pass
                                current_total_value += spot_bal * price

            if not self.smart_baseline or self.smart_baseline <= 0:
                return
            
            # [Removed] 用户要求删除 "历史战绩回顾" (display_recent_trades)
            # 仅保留 "实盘数据统计" (calculate_realized_performance) 用于校准
            # 和 "历史盈亏回顾" (display_pnl_history) 用于看资金曲线
            if not hasattr(self, 'realized_pnl_cache'):
                 # await self.display_recent_trades() # Deleted
                 # 延迟一秒，避免日志乱序
                 # await asyncio.sleep(1)
                 await self.calculate_realized_performance()

            # [Auto-Deposit Detection] 充值自动识别逻辑
            # 如果计算出的 PnL 比上一次瞬间增加了太多 (例如 > 20% 本金 或 > 50U)，且不是因为暴涨
            # 则认为是充值，自动上调 deposit_offset 以抵消影响
            
            # PnL = (Total - Offset) - Baseline
            self.current_equity = current_total_value
            adjusted_equity = current_total_value - self.deposit_offset
            self.current_pnl = adjusted_equity - self.smart_baseline
            raw_pnl = self.current_pnl
            
            # [Fix] 首次运行 PnL 异常检测 (Startup Anomaly Check)
            # 只有当 Baseline 为 None (全新启动) 时，才允许激进的 PnL 归零逻辑
            # 如果是重启 (load_state 成功)，则信任上次的状态，不要随意归零盈利
            
            if not hasattr(self, 'last_known_pnl'):
                # 首次计算
                # 仅当 Baseline 未加载 (说明 bot_state.json 不存在) 时才执行此检测
                if not self.smart_baseline: 
                    # ... (这里原本也不会执行，因为 smart_baseline 是 None 会直接 return)
                    pass
                else:
                    # 如果是从文件加载的 baseline，我们信任它。
                    # 只有一种情况例外：bot_state.json 丢失，但 config.json 里配了 initial_balance
                    # 此时 raw_pnl 可能会很大 (例如重启前赚了 50%)
                    # 我们是否应该把这 50% 视为充值？
                    # 答案：不应该。用户更希望看到历史盈利。
                    # 只有当 raw_pnl 异常大到不合理 (例如 > 200%)，才可能是真正的充值
                    
                    if raw_pnl > max(50.0, self.smart_baseline * 2.0): # 阈值提高到 200%
                        self._log(f"⚠️ 检测到首次 PnL 异常巨大 (+{raw_pnl:.2f} U)，判定为未初始化的闲置资金/充值")
                        self.deposit_offset += raw_pnl
                        self._log(f"🔄 自动修正抵扣额: {self.deposit_offset:.2f} U")
                        self.save_state()
                        # 重新计算
                        adjusted_equity = current_total_value - self.deposit_offset
                        raw_pnl = adjusted_equity - self.smart_baseline
                
                self.last_known_pnl = raw_pnl
            
            pnl_delta = raw_pnl - self.last_known_pnl
            
            # 阈值: 瞬间增长 > 10 U 且 > 5% 本金 (防止正常大波动误判)
            threshold_val = max(10.0, self.smart_baseline * 0.05)
            
            # [New] 查账模式 (Fact-based Funding Check)
            # 只有当资金变动显著时，才调用 API 查流水
            if not self.is_test_mode and abs(pnl_delta) > threshold_val:
                has_flow = await self._verify_funding_flow(pnl_delta)
                if has_flow:
                    # 如果确认了流水，Offset 已更新
                    # 重新计算 PnL
                    adjusted_equity = current_total_value - self.deposit_offset
                    raw_pnl = adjusted_equity - self.smart_baseline
            
            if hasattr(self, 'realized_pnl_cache'):
                self.last_realized_pnl = self.realized_pnl_cache

            # [Fix] 充值后的资金回补检测 (反向充值/资产恢复)
            # 场景: 账户有100U，20U买了币(剩余80U)，配置100U，机器人按80U跑(错误) -> 实际上机器人应该始终按100U跑
            # 场景: 初始80U，配置100U(锁定)，Offset=0。突然卖了币回来20U，总资产变100U。
            # 这时候 pnl_delta 会增加 20U (因为 current_total_value 增加了)。
            # 如果我们把它判定为“充值”，offset 会增加 20U，导致有效资金(Adjusted)还是 80U。
            # 但实际上这 20U 是“内部资产转化”(币->U)，不应该增加 Offset。
            
            # 解决方案: 只有当 Total Equity (U + 币) 真的增加时，才算充值。
            # 但我们这里计算的 current_total_value 本身就是 (U余额 + 币市值) 或者 (账户总权益)。
            # 如果只是卖币 (币->U)，Total Equity 理论上是不变的 (忽略滑点/手续费)。
            # 所以 pnl_delta 应该接近 0。
            
            # 您的案例: "交易账户有100U但是20U已经购买了一个币种... 实际上只能使用80U"
            # 这说明您使用的是 `risk_manager.py` 里的 `used_total_eq = False` 逻辑 (未开启统一账户)，或者 `total_equity` 取值有问题。
            # 如果 `used_total_eq` 为 True (OKX 统一账户模式)，Total Equity 是包含持仓价值的，卖币不会导致总权益突变。
            # 如果是非统一账户 (经典账户)，`total_equity` 可能只取了 USDT 余额。
            # 在这种情况下，卖币会让 USDT 余额从 80 -> 100，导致 `current_total_value` 突增 20。
            # 这会被错误地判定为“充值”，从而增加 offset，导致机器人依然认为只有 80U 可用。
            
            # [修正逻辑]
            # 我们在计算 `current_total_value` 时，必须包含所有受监控币种的持仓价值。
            # 代码第 217-229 行已经做了这件事 (累加 spot_balance * price)。
            # 所以，只要那个币在 `config['symbols']` 里，它的价值就已经被算进去了。卖币只是从“持仓价值”转移到了“USDT余额”，总值不变。
            
            # 唯一的问题是：如果您买的那 20U 的币，**不在**机器人的监控列表里 (编外资产)。
            # 1. 初始: USDT=80, 编外币=20。机器人监控 USDT=80。Offset=0。Effective=80。
            # 2. 卖出: 编外币->USDT。USDT=100。
            # 3. 变化: 机器人发现总值从 80 -> 100。
            # 4. 判定: 瞬间增加 20U -> 判定为充值 -> Offset += 20 -> Effective 依然是 80。
            
            # 如果您希望这 20U 回归后能被机器人使用，我们需要一个机制来“释放”Offset。
            # 比如：当 `adjusted_equity < initial_balance` (说明有效资金不足配置额) 且 `deposit_offset > 0` 时，
            # 我们可以尝试减少 offset，让资金“流回”有效池。
            
            # [Fix] 资金回流检测逻辑调整
            # 只有当 `deposit_offset` 异常大 (说明之前判定了充值) 且有效资金不足时，才考虑回流
            # 但用户指出：如果亏损了就自动补，会导致无限亏损，掩盖真实风险。
            # 因此，我们应该只在一种情况下允许回流：当 "当前总资产" 显著大于 "配置本金" 时 (即依然处于盈余或充值状态)
            # 或者是用户手动开启了 "自动补仓" (目前没有这个开关)
            
            # 现在的逻辑改为：
            # 1. 只有当 Adjusted Equity (有效资金) 严重低于配置 (例如 < 90%)，且 Offset 很大时，才怀疑是 Offset 算多了，尝试修复。
            # 2. 对于微小的亏损 (例如 100 -> 99.9)，不要动 Offset，让它如实反映亏损。
            
            # if self.deposit_offset > 0 and adjusted_equity < self.initial_balance:
            #     # ... (原有的激进回流逻辑) ...
            
            # [New] 保守回流逻辑: 仅在检测到明显的“Offset 误判”时才回流
            # 判定标准: 如果 Offset 占据了太多的资金，导致有效资金连配置的 95% 都不到，那可能是之前把卖币回来的钱误判为充值了。
            # [Reverted] 移除此逻辑。用户反馈 "想看到真实亏损"。
            # 如果我们在这里自动减少 Offset，会导致 "Adjusted Equity" 回升，从而掩盖真实的亏损 (PnL 归零)。
            # 例如: 初始100(配80, Off20). 亏损5 -> 总95. Adj=75. PnL=-5.
            # 如果触发回流: Off->15. Adj->80. PnL->0. 亏损被掩盖了！
            # 因此，必须禁用此逻辑，让亏损如实反映。
            
            # if self.deposit_offset > 0 and adjusted_equity < self.initial_balance * 0.95:
            #      gap = self.initial_balance - adjusted_equity
            #      recoverable = min(gap, self.deposit_offset)
            #      
            #      if recoverable > 0:
            #          self._log(f"💧 资金异常回流: 有效资金 ({adjusted_equity:.2f}) 严重偏离配置 ({self.initial_balance})，判定为Offset误判，释放 {recoverable:.2f} U")
            #          self.deposit_offset -= recoverable
            #          self.save_state()
            #          # 重新计算
            #          adjusted_equity = current_total_value - self.deposit_offset
            #          raw_pnl = adjusted_equity - self.smart_baseline
            
            # [Fix] 逻辑补丁：如果当前计算出的 PnL 与“实盘交易统计”里的 PnL 差异巨大，说明 Baseline 错了
            # 这是一个自我纠错机制。
            # 只有当用户没有手动干预过 offset 时才生效
            if self.is_initialized and hasattr(self, 'realized_pnl_cache'):
                 # 容差: 1 U (避免因为手续费/滑点计算微小差异导致跳变)
                 # 逻辑: 如果 (显示盈亏 - 交易所统计盈亏) > 5 U，说明 Baseline 偏低了，我们在虚报盈利
                 #       如果 (显示盈亏 - 交易所统计盈亏) < -5 U，说明 Baseline 偏高了，我们在虚报亏损
                 
                 # 仅当两者方向一致时才校准，防止逻辑打架
                 # 例如: 显示 +4.81，统计 +0.00。Diff = 4.81。
                 # 我们应该把显示盈亏校准到 +0.00。
                 # 方法: 调整 deposit_offset。
                 # Target_PnL = (Total - Offset) - Baseline
                 # Target = Realized_PnL
                 # Offset = Total - Baseline - Realized_PnL
                 
                 # 为了稳健，我们只在首次启动后的前几分钟做这个校准
                 if not hasattr(self, 'pnl_calibrated') and abs(raw_pnl - self.realized_pnl_cache) > 2.0:
                      new_offset = current_total_value - self.smart_baseline - self.realized_pnl_cache
                      
                      # 只有当 new_offset 是正数时（即确实是初始资金多了）才校准
                      if new_offset > 0:
                          self._log(f"⚖️ 盈亏自动校准: 检测到显示盈亏 ({raw_pnl:.2f}) 与交易所实盘统计 ({self.realized_pnl_cache:.2f}) 不符")
                          self._log(f"🔄 修正前 Offset: {self.deposit_offset:.2f} -> 修正后: {new_offset:.2f}")
                          self.deposit_offset = new_offset
                          self.save_state()
                          
                          # 立即重新计算
                          adjusted_equity = current_total_value - self.deposit_offset
                          raw_pnl = adjusted_equity - self.smart_baseline
                          self.pnl_calibrated = True

            # [Fix] 防止重复打印日志
            # 策略优化：基于百分比变化的智能日志
            # 1. 如果 PnL 变化超过本金的 0.1%，立即打印
            # 2. 或者，如果绝对值变化超过 0.5 U，立即打印 (针对小资金)
            # 3. 否则，保持静默 (由心跳机制兜底)
            
            current_ts = time.time()
            pnl_diff = abs(raw_pnl - getattr(self, 'last_logged_pnl', 0))
            
            # 动态阈值: 0.1% 的基准资金 (例如 1000U -> 1U, 100U -> 0.1U)
            dynamic_threshold = max(0.5, self.smart_baseline * 0.001)
            
            is_significant_change = not hasattr(self, 'last_logged_pnl') or pnl_diff > dynamic_threshold
            is_heartbeat_time = (current_ts - getattr(self, 'last_log_ts', 0)) > 60
            
            if is_significant_change or is_heartbeat_time or force_log:
                pnl_percent = (raw_pnl / self.smart_baseline) * 100
                log_icon = "💰" if is_significant_change else "💓"
                
                # [Mod] 将高频心跳日志降级为 DEBUG，避免刷屏
                # 只有当触发真正的止损/止盈时，才使用 INFO 级别
                # 如果是 force_log (如每轮交易开始前)，则强制使用 INFO 确保可见
                log_level = 'info' if force_log else 'debug'
                
                # [Improved] 显示 PnL 计算公式，解决用户疑惑 "我没赚啊"
                # PnL = (Current - Offset) - Baseline
                # Eq = Current - Offset
                log_msg = f"{log_icon} 账户监控: 基准 {self.smart_baseline:.2f} U | 当前总值 {current_total_value:.2f} U"
                if self.deposit_offset != 0:
                    log_msg += f" (抵扣 {self.deposit_offset:.2f})"
                
                log_msg += f" | 盈亏 {raw_pnl:+.2f} U ({pnl_percent:+.2f}%)"
                
                # [New] 显示实盘战绩 (Realized PnL)
                # 理论盈亏(raw_pnl) = 当前权益 - 初始权益 (包含浮动盈亏)
                # 实盘战绩(realized) = 交易所统计的已平仓盈亏
                if hasattr(self, 'realized_pnl_cache') and self.realized_pnl_cache != 0:
                     pnl_icon = "🎉" if self.realized_pnl_cache > 0 else "💸"
                     log_msg += f" | 实盘战绩 {self.realized_pnl_cache:+.2f} U {pnl_icon}"
                
                # 如果有误解，显示详细公式
                if raw_pnl > 0:
                     log_msg += f" [公式: {adjusted_equity:.2f} - {self.smart_baseline:.2f}]"
                
                # [New] 显示目标权益 (Target Equity)
                if self.max_profit:
                     target_eq = self.smart_baseline + self.deposit_offset + self.max_profit
                     remaining = self.max_profit - raw_pnl
                     log_msg += f" | 目标: {target_eq:.2f} U (还差 {remaining:.2f})"
                
                self._log(log_msg, level=log_level)
                
                self.last_logged_pnl = raw_pnl
                self.last_log_ts = current_ts
            
            self.last_known_pnl = raw_pnl # 更新记录
            
            current_pnl = raw_pnl
            
            # [Fix] Prevent Division by Zero if smart_baseline is 0 (e.g. startup failed)
            if self.smart_baseline > 0:
                pnl_percent = (current_pnl / self.smart_baseline) * 100
            else:
                pnl_percent = 0.0
            
            # [Fix] 限制 CSV 写入和图表更新频率 (例如每分钟一次，而不是每秒)
            current_ts = time.time()
            if current_ts - getattr(self, 'last_csv_record_time', 0) > 60:
                await self.record_pnl_to_csv(current_total_value, current_pnl, pnl_percent)
                self.last_csv_record_time = current_ts
            
            if time.time() - self.last_chart_display_time > 3600:
                self.display_pnl_history()
                self.last_chart_display_time = time.time()
            
            should_take_profit = False
            tp_trigger_msg = ""
            
            if self.max_profit and current_pnl >= self.max_profit:
                should_take_profit = True
                tp_trigger_msg = f"盈利金额达标 (+{current_pnl:.2f} U >= {self.max_profit} U)"
            elif self.max_profit_pct and pnl_percent >= (self.max_profit_pct * 100):
                should_take_profit = True
                tp_trigger_msg = f"盈利比例达标 (+{pnl_percent:.2f}% >= {self.max_profit_pct*100}%)"

            if should_take_profit:
                self._log(f"🎉🎉🎉 {tp_trigger_msg}")
                await self.close_all_traders()
                await self.send_notification(
                    f"**{tp_trigger_msg}**\n当前权益: `{total_equity:.2f} U`",
                    title="🎉 止盈达成"
                )
                import sys
                sys.exit(0)

            should_stop_loss = False
            sl_trigger_msg = ""
            
            if self.max_loss and current_pnl <= -self.max_loss:
                should_stop_loss = True
                sl_trigger_msg = f"亏损金额触线 ({current_pnl:.2f} U <= -{self.max_loss} U)"
            elif self.max_loss_pct and pnl_percent <= -(self.max_loss_pct * 100):
                should_stop_loss = True
                sl_trigger_msg = f"亏损比例触线 ({pnl_percent:.2f}% <= -{self.max_loss_pct*100}%)"

            if should_stop_loss:
                self._log(f"😭😭😭 {sl_trigger_msg}")
                await self.close_all_traders()
                await self.send_notification(
                    f"**{sl_trigger_msg}**\n当前权益: `{total_equity:.2f} U`",
                    title="🚑 止损警报"
                )
                import sys
                sys.exit(0)

        except Exception as e:
            self._log(f"检查全局盈亏失败: {e}", 'error')

    async def initialize_baseline(self, current_usdt_equity):
        """初始化基准资金 (Async)"""
        if self.is_test_mode:
            sim_eq = 0.0
            for t in self.traders:
                _, e = await t.get_account_info()
                sim_eq += e
            current_usdt_equity = sim_eq

        # 1. 先获取所有交易对的价格，用于后续估值
        symbols = [t.symbol for t in self.traders]
        prices = {}
        try:
            tickers = await self.exchange.fetch_tickers(symbols)
            for s, t in tickers.items():
                prices[s] = t['last']
        except Exception as e:
            self._log(f"初始化获取价格失败: {e}", 'warning')

        # 2. [New] 在盘点开始前，简单打印账户可用资产及估值情况
        try:
            balance = await self.exchange.fetch_balance()
            total_usdt_avail = balance.get('USDT', {}).get('free', 0.0)
            
            # 收集持有的非零资产
            other_assets_info = []
            held_currencies = [c for c, d in balance.get('total', {}).items() if c != 'USDT' and d > 0.00001]
            
            # 如果持有资产较多，尝试批量获取价格用于估值
            asset_prices = {}
            if held_currencies:
                try:
                    # 构造现货交易对名称进行查询 (如 SOL/USDT)
                    price_query_symbols = [f"{c}/USDT" for c in held_currencies]
                    tickers = await self.exchange.fetch_tickers(price_query_symbols)
                    for s, t in tickers.items():
                        base = s.split('/')[0]
                        asset_prices[base] = t['last']
                except:
                    pass

            for currency in held_currencies:
                amount = balance['total'][currency]
                price = asset_prices.get(currency)
                if price:
                    valuation = amount * price
                    other_assets_info.append(f"{amount:.4f} {currency} (≈ {valuation:.2f} U)")
                else:
                    other_assets_info.append(f"{amount:.4f} {currency}")
            
            asset_summary = f"💰 当前可用余额: {total_usdt_avail:.2f} USDT"
            if other_assets_info:
                # 换行显示持有资产，避免单行太长
                assets_str = ", ".join(other_assets_info[:6])
                self.logger.info(f"\n{'='*50}\n{asset_summary}\n📦 持有资产: {assets_str}\n{'='*50}")
            else:
                self.logger.info(f"\n{'='*50}\n{asset_summary}\n{'='*50}")
        except:
            pass

        sep_line = "-" * 115
        header = f"\n{sep_line}\n📊 资产初始化盘点 (Asset Initialization)\n{sep_line}"
        # 使用纯英文表头以确保对齐
        # User requested Chinese header to match old screenshot
        table_header = f"{'交易对':<18} | {'分配比例':<8} | {'理论配额(U)':<12} | {'持仓数量':<10} | {'持仓市值(U)':<12} | {'占用%':<6} | {'成本':<10} | {'估算盈亏'}"
        
        # [Fix] 打印分隔线以区分表格
        self.logger.info(header)
        self.logger.info(table_header)
        self.logger.info("-" * 115) # Add separator line
        
        total_position_value = 0.0
        
        for trader in self.traders:
            quota = 0.0
            allocation_str = "N/A"
            
            # 测试模式下，使用 sim_balance 作为基础资金
            if hasattr(trader, 'test_mode') and trader.test_mode and hasattr(trader, 'sim_balance') and trader.sim_balance > 0:
                base_capital = trader.sim_balance
                if isinstance(trader.allocation, str) and trader.allocation == 'auto':
                    quota = base_capital
                    allocation_str = "Auto"
                elif isinstance(trader.allocation, (int, float)):
                    if trader.allocation <= 1.0:
                        quota = base_capital * trader.allocation
                        allocation_str = f"{trader.allocation*100:.0f}%"
                    else:
                        quota = trader.allocation
                        allocation_str = "Fixed"
            # 实盘模式下，使用 initial_balance 作为基础资金
            elif hasattr(trader, 'initial_balance') and trader.initial_balance and trader.initial_balance > 0:
                if isinstance(trader.allocation, str) and trader.allocation == 'auto':
                    # 实盘模式下，按活跃交易对数量平均分配
                    active_count = len(self.traders)
                    if active_count > 0:
                        quota = trader.initial_balance / active_count
                    allocation_str = "Auto"
                elif isinstance(trader.allocation, (int, float)):
                    if trader.allocation <= 1.0:
                        quota = trader.initial_balance * trader.allocation
                        allocation_str = f"{trader.allocation*100:.0f}%"
                    else:
                        quota = trader.allocation
                        allocation_str = "Fixed"
            
            holding_amount = 0.0
            position_val = 0.0
            
            current_price = prices.get(trader.symbol, 0)
            if current_price == 0:
                try:
                    ohlcv = await trader.get_ohlcv()
                    if ohlcv:
                        current_price = ohlcv['price']
                except:
                    pass
                
            if trader.trade_mode == 'cash':
                holding_amount = await trader.get_spot_balance()
                # [Fix] 现货模式下，DOGE/USDT:USDT 返回的是 USDT 余额而不是 DOGE 余额
                # 这是因为 config.json 里 symbol 配置是 DOGE/USDT:USDT (线性合约格式) 但 trade_mode 是 cash
                # ccxt.okx 在 cash 模式下 fetch_balance 返回的是所有币种
                # get_spot_balance 内部调用的是 fetch_balance['base_currency']['free']
                # 我们需要确保获取的是 Base Currency (DOGE) 的余额
                
                # 如果 holding_amount 非常小 (精度误差)，归零
                if holding_amount < 1e-6: holding_amount = 0
                
                if holding_amount > 0 and current_price > 0:
                    position_val = holding_amount * current_price
                    total_position_value += position_val
            else:
                pos = await trader.get_current_position()
                if pos:
                    # [Fix] 优先使用 coin_size (实际币数)
                    holding_amount = pos.get('coin_size', pos['size'])
                    # 对于合约，市值估算可能需要更精确，这里简化为 持仓数量 * 价格
                    # 实际上合约价值 = 数量 * 合约面值 * 价格 (如果是币本位) 或者 数量 * 价格 (如果是U本位且单位是币)
                    # OKX U本位合约 size 通常是 币的数量
                    position_val = holding_amount * current_price
                    # [Fix] 合约模式下，total_position_value 不应累加到 real_total_equity 中
                    # 因为账户权益 (Equity) 已经包含了合约保证金和未实现盈亏
                    # 所以我们只记录 position_val 用于展示，但不加到 total_position_value 中
                    # total_position_value 变量在最后用于修正 current_usdt_equity
                    # 只有 cash 模式下，现货价值才需要加回去
                    # total_position_value += position_val  <-- Remove this for contract
            
            usage_pct = 0.0
            if quota > 0:
                usage_pct = (position_val / quota) * 100
            
            entry_price = await trader.get_avg_entry_price()
            entry_price_str = f"{entry_price:.4f}" if entry_price > 0 else "N/A"
            
            pnl_est_str = "-"
            if entry_price > 0 and holding_amount > 0 and current_price > 0:
                # 简单估算盈亏 (默认为做多/现货)
                raw_pnl = (current_price - entry_price) * holding_amount
                
                # 如果是合约，检查是否为做空
                if trader.trade_mode != 'cash':
                     pos = await trader.get_current_position()
                     if pos and pos['side'] == 'short':
                         raw_pnl = (entry_price - current_price) * holding_amount

                pnl_est_str = f"{raw_pnl:+.2f} U"

            row_str = f"{trader.symbol:<18} | {allocation_str:<8} | {quota:<12.2f} | {holding_amount:<10.4f} | {position_val:<12.2f} | {usage_pct:>5.1f}% | {entry_price_str:<10} | {pnl_est_str}"
            self.logger.info(row_str)

        self.logger.info(sep_line)
        
        real_total_equity = current_usdt_equity + total_position_value
        
        # [Fix] 测试模式下，current_usdt_equity 已经是包含持仓PnL的总权益
        # 而 total_position_value 是现货持仓市值
        # 由于模拟器的 equity = balance + u_pnl，这已经涵盖了现货价值变动
        # 所以不应该再重复累加 total_position_value
        if self.is_test_mode:
            real_total_equity = current_usdt_equity
        
        # [New] 显示当前资金总数 (响应用户需求)
        self.logger.info(f"💰 当前资金总数 (Total Equity): {real_total_equity:.2f} U")
        self.logger.info("✨ 初始化完成，进入主循环... (Initialization complete, entering main loop...)")
        
        if self.initial_balance and self.initial_balance > 0:
            # [Logic Change] 智能基准模式 (Smart Baseline)
            # 优先尊重用户的 config 配置，但如果实际资金与配置偏差过大 (可能是配置没填对)，则提示并自动适配
            
            diff = real_total_equity - self.initial_balance
            
            # 1. 如果实际资金略少于配置 (例如少于 2U 或 5%)，通常是手续费磨损或零头差异
            #    此时应该【强制】把基准设为实际资金，避免一启动就显示亏损
            if -5.0 < diff < 0 or (0.95 < real_total_equity / self.initial_balance < 1.0):
                 self.smart_baseline = real_total_equity
                 self.deposit_offset = 0.0
                 self._log(f"📉 微小差额自动校准: 配置 {self.initial_balance} -> 实际 {real_total_equity:.2f} (归零启动盈亏)")
            
            # 2. 如果实际资金远小于配置 (例如配置 1000U，实际只有 30U)
            #    说明用户可能填错了，或者提现了。也应该以实际为准。
            elif real_total_equity < self.initial_balance * 0.95:
                 self.smart_baseline = real_total_equity
                 self.deposit_offset = 0.0
                 self._log(f"⚠️ 资产显著缩水: 配置 {self.initial_balance} -> 实际 {real_total_equity:.2f} (以实际资金重置基准)")
            
            # 3. 如果实际资金大于配置 (例如配置 30U，实际 100U)
            #    这通常是用户想“专款专用”。此时保持配置值作为基准，多出来的部分算作 Offset (闲置资金)
            else:
                self.smart_baseline = self.initial_balance
                self.deposit_offset = real_total_equity - self.initial_balance
                if self.deposit_offset > 0.1:
                    self._log(f"� 锁定本金模式: 仅管理 {self.smart_baseline:.2f} U，闲置/额外资金 {self.deposit_offset:.2f} U")
                else:
                    self._log(f"✅ 初始本金完美匹配: {self.smart_baseline:.2f} U")

        else:
            if not self.smart_baseline:
                self.smart_baseline = real_total_equity
        
        self.save_state()
        self.is_initialized = True # [Fix] 标记初始化完成
