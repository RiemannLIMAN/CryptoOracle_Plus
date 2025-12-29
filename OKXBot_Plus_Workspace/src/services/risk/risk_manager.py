import os
import json
import time
import logging
import asyncio
import aiohttp
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

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.smart_baseline = state.get('smart_baseline')
                self.deposit_offset = state.get('deposit_offset', 0.0) # 恢复 offset
                if self.smart_baseline:
                    print(f"🔄 已恢复历史基准资金: {self.smart_baseline:.2f} U (闲置抵扣: {self.deposit_offset:.2f} U)")
            except Exception as e:
                print(f"⚠️ 加载状态失败: {e}")

    def save_state(self):
        try:
            state = {
                'smart_baseline': self.smart_baseline,
                'deposit_offset': self.deposit_offset
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
        except Exception as e:
            print(f"⚠️ 保存状态失败: {e}")

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

    def record_pnl_to_csv(self, total_equity, current_pnl, pnl_percent):
        file_exists = os.path.isfile(self.csv_file)
        try:
            with open(self.csv_file, 'a', encoding='utf-8') as f:
                if not file_exists:
                    f.write("timestamp,total_equity,pnl_usdt,pnl_percent\n")
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"{timestamp},{total_equity:.2f},{current_pnl:.2f},{pnl_percent:.2f}\n")
            
            try:
                # Need to check plot_pnl import
                # It is in src/core/plot_pnl.py
                # We need to make sure we can import it.
                # Since we added src to sys.path in main.py, 'from core import plotter' should work.
                from core import plotter
                plotter.generate_pnl_chart(csv_path=self.csv_file, output_path=self.chart_path, verbose=False)
                self.logger.info(f"盈亏折线图已更新: {self.chart_path}")
            except Exception as e:
                self._log(f"生成折线图失败: {e}", 'warning')

        except Exception as e:
            self._log(f"写入CSV失败: {e}", 'error')

    async def close_all_traders(self):
        self._log("🛑 正在执行全局清仓...")
        tasks = [trader.close_all_positions() for trader in self.traders]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def calculate_realized_performance(self):
        """基于交易所历史订单计算已实现盈亏与胜率"""
        try:
            sep_line = "=" * 80
            
            total_realized_pnl = 0.0
            total_trades = 0
            win_trades = 0
            
            has_data = False
            report_body = ""
            
            for trader in self.traders:
                try:
                    # 获取最近 100 条成交
                    trades = await trader.exchange.fetch_my_trades(trader.symbol, limit=100)
                    if not trades:
                        continue
                    
                    symbol_pnl = 0.0
                    symbol_wins = 0
                    symbol_count = 0
                    
                    for trade in trades:
                        # 仅统计有 PnL 的订单 (通常是合约平仓单)
                        # 现货交易通常没有直接的 PnL 字段，需要更复杂的匹配逻辑，暂只统计合约
                        pnl = 0.0
                        if 'info' in trade and 'pnl' in trade['info']:
                            try:
                                pnl = float(trade['info']['pnl'])
                            except:
                                pnl = 0.0
                        
                        # 如果 API 没返回 PnL (如现货)，暂时跳过统计，避免误导
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
                    else:
                        # 只有在有数据时才显示这一行，如果完全没数据就不显示了，免得占地方
                        # report_body += f"\n{trader.symbol:<15} | 暂无已实现盈亏记录 (仅统计合约平仓)"
                        pass
                        
                except Exception as e:
                    self._log(f"计算 {trader.symbol} 绩效失败: {e}", 'warning')
            
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
                # 没数据就不打印了，清爽一点
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
                    trades = await trader.exchange.fetch_my_trades(trader.symbol, limit=5)
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
            header = "\n" + "="*40 + f"\n� 历史盈亏回顾 (共 {len(df)} 条记录)\n" + "="*40
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

    async def check(self, force_log=False):
        """执行风控检查 (Async)"""
        try:
            balance = await self.exchange.fetch_balance()
            total_equity = 0
            found_usdt = False
            used_total_eq = False

            if 'info' in balance and 'data' in balance['info']:
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
            
            if not found_usdt:
                if 'USDT' in balance and 'equity' in balance['USDT']:
                    total_equity = float(balance['USDT']['equity'])
                elif 'USDT' in balance and 'total' in balance['USDT']:
                     total_equity = float(balance['USDT']['total'])
            
            if total_equity <= 0:
                return

            # [Fix] 每次重启强制进入初始化流程，重新计算 offset，而不是仅依赖 baseline 是否为空
            if not self.is_initialized:
                await self.initialize_baseline(total_equity)
            
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
            adjusted_equity = current_total_value - self.deposit_offset
            raw_pnl = adjusted_equity - self.smart_baseline
            
            # [Fix] 首次运行 PnL 异常检测 (Startup Anomaly Check)
            # 如果这是本次启动后第一次计算 PnL，且 PnL 巨大 (说明 initialize_baseline 可能漏掉了 offset)
            # 我们直接将其视为 Offset，而不是盈利
            # 只有当 raw_pnl 是正数时才进行此检查。如果是负数（亏损），则如实反映。
            if not hasattr(self, 'last_known_pnl'):
                # 首次计算
                if raw_pnl > max(10.0, self.smart_baseline * 0.1):
                    self._log(f"⚠️ 检测到首次 PnL 异常偏高 (+{raw_pnl:.2f} U)，判定为未初始化的闲置资金/充值")
                    self.deposit_offset += raw_pnl
                    self._log(f"🔄 自动修正抵扣额: {self.deposit_offset:.2f} U")
                    self.save_state()
                    # 重新计算
                    adjusted_equity = current_total_value - self.deposit_offset
                    raw_pnl = adjusted_equity - self.smart_baseline
                
                self.last_known_pnl = raw_pnl
            
            pnl_delta = raw_pnl - self.last_known_pnl
            
            # 阈值: 瞬间增长 > 10 U 且 > 5% 本金 (防止正常大波动误判)
            # 正常交易很难在 10秒内(check间隔) 赚这么多
            threshold_val = max(10.0, self.smart_baseline * 0.05)
            
            if pnl_delta > threshold_val:
                self._log(f"💸 检测到资金瞬间增加 (+{pnl_delta:.2f} U)，判定为外部充值")
                # 调整 offset，吃掉这部分增量，保持 PnL 不变
                # New_Offset = Old_Offset + Delta
                self.deposit_offset += pnl_delta
                self._log(f"🔄 自动增加抵扣额: {self.deposit_offset:.2f} U (维持 PnL 连续)")
                self.save_state()
                # 重新计算 PnL
                adjusted_equity = current_total_value - self.deposit_offset
                raw_pnl = adjusted_equity - self.smart_baseline
            
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
            if self.deposit_offset > 0 and adjusted_equity < self.initial_balance * 0.95:
                 gap = self.initial_balance - adjusted_equity
                 recoverable = min(gap, self.deposit_offset)
                 
                 if recoverable > 0:
                     self._log(f"💧 资金异常回流: 有效资金 ({adjusted_equity:.2f}) 严重偏离配置 ({self.initial_balance})，判定为Offset误判，释放 {recoverable:.2f} U")
                     self.deposit_offset -= recoverable
                     self.save_state()
                     # 重新计算
                     adjusted_equity = current_total_value - self.deposit_offset
                     raw_pnl = adjusted_equity - self.smart_baseline
            
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
            pnl_percent = (current_pnl / self.smart_baseline) * 100
            
            # [Fix] 限制 CSV 写入和图表更新频率 (例如每分钟一次，而不是每秒)
            current_ts = time.time()
            if current_ts - getattr(self, 'last_csv_record_time', 0) > 60:
                self.record_pnl_to_csv(current_total_value, current_pnl, pnl_percent)
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
        sep_line = "-" * 115
        header = f"\n{sep_line}\n📊 资产初始化盘点 (Asset Initialization)\n{sep_line}"
        # 使用纯英文表头以确保对齐
        # User requested Chinese header to match old screenshot
        table_header = f"{'交易对':<18} | {'分配比例':<8} | {'理论配额(U)':<12} | {'持仓数量':<10} | {'持仓市值(U)':<12} | {'占用%':<6} | {'成本':<10} | {'估算盈亏'}"
        
        # 改回使用 logger.info 以确保日志文件中可见，与老版本保持一致
        self.logger.info(header)
        self.logger.info(table_header)
        self.logger.info(sep_line)
        
        total_position_value = 0.0
        
        symbols = [t.symbol for t in self.traders]
        prices = {}
        try:
            tickers = await self.exchange.fetch_tickers(symbols)
            for s, t in tickers.items():
                prices[s] = t['last']
        except Exception as e:
            self._log(f"初始化获取价格失败: {e}", 'warning')

        for trader in self.traders:
            quota = 0.0
            allocation_str = "N/A"
            
            if hasattr(trader, 'initial_balance') and trader.initial_balance and trader.initial_balance > 0:
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
        
        # [New] 显示当前资金总数 (响应用户需求)
        self.logger.info(f"💰 当前资金总数 (Total Equity): {real_total_equity:.2f} U")
        
        if self.initial_balance and self.initial_balance > 0:
            # [Logic Change] 固定本金模式
            # 如果 实际权益 > 初始配置 (说明有额外充值)，则强制维持 初始配置 作为基准
            # 只有当 实际权益 < 初始配置 * 0.9 (说明亏损严重或提现)，才向下校准
            
            if real_total_equity < self.initial_balance * 0.9:
                self.smart_baseline = real_total_equity
                self.deposit_offset = 0.0 # 缩水了，清空抵扣
                self._log(f"⚠️ 资产缩水校准: 配置 {self.initial_balance} -> 实际 {real_total_equity:.2f} (缩水 >10%)")
            else:
                # 即使实际权益远大于配置，也坚持使用配置值，实现"专款专用"
                self.smart_baseline = self.initial_balance
                if real_total_equity > self.initial_balance * 1.1:
                    # 初始化 offset: 实际权益 - 配置本金
                    # 如果之前没有 offset 或者 需要重新计算
                    # 这里为了防止重启时重复计算，我们只在 smart_baseline 是 None 时，或者 offset 为 0 时初始化
                    # 或者，如果 offset + baseline != real_total_equity (偏差很大)，也校准一下？
                    # 简化逻辑：每次启动如果处于锁定模式，直接把多出来的部分算作 offset
                    self.deposit_offset = real_total_equity - self.initial_balance
                    self._log(f"🔒 锁定本金模式: 忽略额外资金 {self.deposit_offset:.2f} U，仅管理 {self.smart_baseline:.2f} U")
                else:
                    self.deposit_offset = 0.0
                    self._log(f"✅ 初始本金确认: {self.smart_baseline:.2f} U")
                    
                    # [New] 提示用户如果这是初始资金差异
                    diff = real_total_equity - self.initial_balance
                    if diff > 0.5:
                        self._log(f"💡 提示: 当前资金 ({real_total_equity:.2f}) > 配置本金 ({self.initial_balance})。差额 {diff:.2f} U 即将进行【自动校准】。")
                        # self._log(f"👉 如果这是您的初始本金，请在 config.json 中将 initial_balance_usdt 修改为 {real_total_equity:.2f} 以归零盈亏。")
        else:
            if not self.smart_baseline:
                self.smart_baseline = real_total_equity
        
        self.save_state()
        self.is_initialized = True # [Fix] 标记初始化完成
