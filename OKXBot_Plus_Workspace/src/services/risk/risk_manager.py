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

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.smart_baseline = state.get('smart_baseline')
                    if self.smart_baseline:
                        print(f"🔄 已恢复历史基准资金: {self.smart_baseline:.2f} U")
            except Exception as e:
                print(f"⚠️ 加载状态失败: {e}")

    def save_state(self):
        try:
            state = {'smart_baseline': self.smart_baseline}
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
        except Exception as e:
            print(f"⚠️ 保存状态失败: {e}")

    def _log(self, msg, level='info'):
        if level == 'info':
            self.logger.info(f"[RISK_MGR] {msg}")
        elif level == 'error':
            self.logger.error(f"[RISK_MGR] {msg}")

    async def send_notification(self, message):
        """发送通知 (Async)"""
        if not self.notification_config.get('enabled', False):
            return
        webhook_url = self.notification_config.get('webhook_url')
        
        full_msg = f"🛡️ CryptoOracle 风控通知\n--------------------\n{message}"
        await send_notification_async(webhook_url, full_msg)

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

    def display_pnl_history(self):
        # 保持同步方法
        if not os.path.isfile(self.csv_file):
            return
        try:
            df = pd.read_csv(self.csv_file)
            if df.empty: return
            
            header = "\n" + "="*40 + f"\n📜 历史战绩回顾 (共 {len(df)} 条记录)\n" + "="*40
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
                bar = ""
                num_blocks = abs(pnl) * scale_factor
                full_blocks = int(num_blocks)
                
                if pnl > 0:
                    bar = "▫️" if full_blocks == 0 and num_blocks > 0.1 else "🟩" * min(full_blocks, 20)
                elif pnl < 0:
                    bar = "▪️" if full_blocks == 0 and num_blocks > 0.1 else "🟥" * min(full_blocks, 20)
                else:
                    bar = "➖"
                
                line = f"{timestamp} | {pnl:>6.2f} U | {bar}"
                self.logger.info(line)
                # print(line) # Duplicate print removed
            
            footer = "="*30 + "\n"
            self.logger.info(footer)
            # print(footer) # Duplicate print removed
            
            # 更新最后显示时间，防止短时间内重复打印
            self.last_chart_display_time = time.time()
        except Exception:
            pass

    async def check(self):
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

            if self.smart_baseline is None:
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
                except:
                    pass

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

            current_pnl = current_total_value - self.smart_baseline
            pnl_percent = (current_pnl / self.smart_baseline) * 100

            self._log(f"💰 账户监控: 基准 {self.smart_baseline:.2f} U | 当前总值 {current_total_value:.2f} U | 盈亏 {current_pnl:+.2f} U ({pnl_percent:+.2f}%)")
            self.record_pnl_to_csv(current_total_value, current_pnl, pnl_percent)
            
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
                await self.send_notification(f"🎉 止盈退出\n{tp_trigger_msg}\n当前权益: {total_equity:.2f} U")
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
                await self.send_notification(f"🚑 止损退出\n{sl_trigger_msg}\n当前权益: {total_equity:.2f} U")
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
        except:
            pass

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
                    holding_amount = pos['size']
                    # 对于合约，市值估算可能需要更精确，这里简化为 持仓数量 * 价格
                    # 实际上合约价值 = 数量 * 合约面值 * 价格 (如果是币本位) 或者 数量 * 价格 (如果是U本位且单位是币)
                    # OKX U本位合约 size 通常是 币的数量
                    position_val = holding_amount * current_price
                    total_position_value += position_val
            
            usage_pct = 0.0
            if quota > 0:
                usage_pct = (position_val / quota) * 100
            
            entry_price = await trader.get_avg_entry_price()
            entry_price_str = f"{entry_price:.4f}" if entry_price > 0 else "N/A"
            
            pnl_est_str = "-"
            if entry_price > 0 and holding_amount > 0 and current_price > 0:
                # 简单估算盈亏
                raw_pnl = (current_price - entry_price) * holding_amount
                # 如果是做空，盈亏反向
                if hasattr(trader, 'position_side') and trader.position_side == 'short': 
                     # 这里假设 DeepSeekTrader 有 position_side 属性或者我们需要从 get_current_position 获取
                     # 实际上 get_current_position 返回了 side
                     pass
                
                # 为了准确，我们重新获取一次 position 信息
                if trader.trade_mode != 'cash':
                     pos = await trader.get_current_position()
                     if pos and pos['side'] == 'short':
                         raw_pnl = (entry_price - current_price) * holding_amount

                pnl_est_str = f"{raw_pnl:+.2f} U"

            row_str = f"{trader.symbol:<18} | {allocation_str:<8} | {quota:<12.2f} | {holding_amount:<10.4f} | {position_val:<12.2f} | {usage_pct:>5.1f}% | {entry_price_str:<10} | {pnl_est_str}"
            self.logger.info(row_str)

        self.logger.info(sep_line)
        
        real_total_equity = current_usdt_equity + total_position_value
        
        if self.initial_balance and self.initial_balance > 0:
            gap_percent = abs(real_total_equity - self.initial_balance) / self.initial_balance * 100
            if gap_percent > 10.0:
                self.smart_baseline = real_total_equity
                self._log(f"⚠️ 初始本金校准: 配置 {self.initial_balance} vs 实际总值 {real_total_equity:.2f}")
                self._log(f"🔄 已校准盈亏计算基准为: {self.smart_baseline:.2f} U")
            else:
                if not self.smart_baseline:
                    self.smart_baseline = self.initial_balance
                    self._log(f"✅ 初始本金校准通过: {self.smart_baseline:.2f} U")
                else:
                     self._log(f"✅ 延续历史基准: {self.smart_baseline:.2f} U")
        else:
            if not self.smart_baseline:
                self.smart_baseline = real_total_equity
        
        self.save_state()
