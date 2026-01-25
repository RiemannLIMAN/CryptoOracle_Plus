import time
import logging
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime
from core.utils import to_float, send_notification_async, exception_handler, retry_async
from core.exceptions import (
    APIConnectionError, APIResponseError, TradingError, 
    DataProcessingError, RiskManagementError
)
from core.cache import cache_manager
from services.data.data_manager import DataManager
from .components import PositionManager, OrderExecutor, SignalProcessor
import json
import os

class DeepSeekTrader:
    def __init__(self, symbol_config, common_config, exchange, agent):
        self.symbol_config = symbol_config # Store for hot reload
        self.common_config = common_config # Store for hot reload
        self.symbol = symbol_config['symbol']
        self.config_amount = symbol_config.get('amount', 'auto') 
        self.amount = 0
        
        # [Fix] Handle string allocation in config (e.g. "0.95")
        raw_alloc = symbol_config.get('allocation', 1.0)
        
        # 如果是 'auto' (不分大小写)，则标记为 auto
        if str(raw_alloc).lower() == 'auto':
            self.allocation = 'auto'
        else:
            try:
                # 尝试转为 float
                self.allocation = float(raw_alloc)
            except:
                # 如果转换失败 (例如配了奇怪的字符串)，默认回退到 1.0
                self.allocation = 1.0 
                
        self.leverage = symbol_config['leverage']
        self.trade_mode = symbol_config.get('trade_mode', common_config.get('trade_mode', 'cross'))
        self.margin_mode = symbol_config.get('margin_mode', common_config.get('margin_mode', 'cross'))
        self.timeframe = common_config['timeframe']
        self.test_mode = common_config['test_mode']
        self.max_slippage = common_config.get('max_slippage_percent', 1.0)
        self.min_confidence = common_config.get('min_confidence', 'MEDIUM')
        
        strategy_config = common_config.get('strategy', {})
        # self.history_limit is deprecated, using internal defaults
        self.signal_limit = strategy_config.get('signal_limit', 30)
        
        # [New] Trailing Stop Configuration
        self.trailing_config = strategy_config.get('trailing_stop', {})
        self.trailing_max_pnl = 0.0 # High watermark for current position
        
        self.taker_fee_rate = 0.001
        self.maker_fee_rate = 0.0008
        self.is_swap = ':' in self.symbol
        if self.is_swap:
            self.taker_fee_rate = 0.0005
            self.maker_fee_rate = 0.0002

        # 深拷贝risk_control，确保每个交易对的配置是独立的，避免累积分配问题
        import copy
        self.risk_control = copy.deepcopy(common_config.get('risk_control', {}))
        self.initial_balance = self.risk_control.get('initial_balance_usdt', 0)
        self.notification_config = common_config.get('notification', {})
        
        # [New] 获取活跃交易对数量，用于自动资金分配
        self.active_symbols_count = common_config.get('active_symbols_count', 1)

        self.exchange = exchange
        self.agent = agent # DeepSeekAgent instance
        
        # [New] Data Manager
        self.data_manager = DataManager(f"data/trade_data_{self.symbol.replace('/', '_')}.db")
        
        # [Refactor] Initialize Components
        self.position_manager = PositionManager(
            self.exchange, 
            self.symbol, 
            self.trade_mode, 
            self.test_mode, 
            logging.getLogger("crypto_oracle")
        )
        self.position_manager.set_trailing_config(self.trailing_config)
        
        self.order_executor = OrderExecutor(
            self.exchange,
            self.symbol,
            self.trade_mode,
            self.test_mode,
            self.position_manager,
            logging.getLogger("crypto_oracle")
        )
        self.order_executor.set_fee_rate(self.taker_fee_rate)
        
        self.signal_processor = SignalProcessor(logging.getLogger("crypto_oracle"))
        
        self.price_history = []
        self.signal_history = []
        self.logger = logging.getLogger("crypto_oracle")
        
        # [New] Dynamic Risk Parameters (from AI)
        self.dynamic_stop_loss = 0.0
        self.dynamic_take_profit = 0.0
        self.dynamic_sl_side = None # 'long' or 'short'
        
        # [New] Store last indicators for execution logic
        self.last_indicators = {}
        
        # [New] Circuit Breaker (Cool-down)
        self.last_stop_loss_time = 0
        self.cool_down_seconds = 180 # [Safety] Increase to 180s (3 mins) to prevent rapid churn
        self.last_trade_time = 0     # [New] Track last trade time
        self.min_trade_interval = 300 # [New] Minimum 5 mins between OPENING new trades (Closing is always allowed)
        
        # [New] Hot Reload Config
        self.config_path = 'config.json'
        self.last_config_mtime = 0
        self._init_config_watcher()

        # [New] Watchdog State
        self.consecutive_errors = 0
        self.last_heartbeat_time = time.time()
        
        # [New] Global Circuit Breaker
        self.daily_high_equity = 0.0
        self.high_water_day = datetime.now().strftime('%Y%m%d')

        self.analyze_on_bar_close = bool(common_config.get('analyze_on_bar_close', False))
        self._last_analyzed_bar_ts = None

        # [New] State Persistence
        self.state_file = f"data/state_{self.symbol.replace('/', '_')}.json"
        
        # [New] Simulation State (Test Mode Only)
        self.sim_state_file = f"data/sim_state_{self.symbol.replace('/', '_')}.json"
        
        if self.test_mode:
            self._load_sim_state()
            # If no balance record, use allocated portion of initial_balance
            sim_bal = self.position_manager.sim_balance
            if sim_bal <= 0 or sim_bal == 10000.0: # Check against default
                 new_bal = 10000.0
                 if self.initial_balance > 0:
                     # Try to respect allocation logic
                     if isinstance(self.allocation, (int, float)) and self.allocation <= 1.0:
                         new_bal = self.initial_balance * self.allocation
                     elif isinstance(self.allocation, (int, float)) and self.allocation > 1.0:
                         new_bal = self.allocation
                     elif isinstance(self.allocation, str) and self.allocation == 'auto':
                         # For auto allocation, use actual active symbols count
                         symbols_count = max(1, self.active_symbols_count)
                         new_bal = self.initial_balance / symbols_count
                 
                 if new_bal != sim_bal:
                     self.position_manager.sim_balance = new_bal
                     self._log(f"🧪 模拟资金初始化: {new_bal:.2f} U")

        self.load_state()

    async def save_state(self):
        """Async save state to disk"""
        try:
            state = {
                'daily_high_equity': self.daily_high_equity,
                'high_water_day': self.high_water_day,
                'dynamic_stop_loss': self.dynamic_stop_loss,
                'dynamic_take_profit': self.dynamic_take_profit,
                'dynamic_sl_side': self.dynamic_sl_side,
                'trailing_max_pnl': self.trailing_max_pnl, # [New] Persist trailing stop
                'updated_at': time.time()
            }
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._save_state_sync, state)
        except Exception as e:
            self.logger.warning(f"[{self.symbol}] ⚠️ 保存状态失败: {e}")

    def load_state(self):
        """Load persistent state (Circuit Breaker & Dynamic Risk)"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.daily_high_equity = state.get('daily_high_equity', 0.0)
                    saved_day = state.get('high_water_day')
                    today = datetime.now().strftime('%Y%m%d')
                    # [Fix] Reset high water mark on new day to prevent stale drawdown
                    if saved_day != today:
                        self.daily_high_equity = 0.0
                        self.high_water_day = today
                    else:
                        self.high_water_day = saved_day or today
                    self.dynamic_stop_loss = state.get('dynamic_stop_loss', 0.0)
                    self.dynamic_take_profit = state.get('dynamic_take_profit', 0.0)
                    self.dynamic_sl_side = state.get('dynamic_sl_side')
                    self.trailing_max_pnl = state.get('trailing_max_pnl', 0.0) # [New] Restore
                    
                    self.logger.info(f"[{self.symbol}] 🔄 恢复状态: DailyHigh={self.daily_high_equity:.2f}, DynSL={self.dynamic_stop_loss}, TrailMax={self.trailing_max_pnl:.2%}")
            except Exception as e:
                self.logger.warning(f"[{self.symbol}] ⚠️ 加载状态失败: {e}")

    def _save_state_sync(self, state):
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f)

    async def check_trailing_stop(self, current_position=None):
        """检查并执行移动止盈 (Trailing Stop)"""
        return await self.position_manager.check_trailing_stop(
            current_position, 
            save_callback=self.save_state, 
            notification_callback=self.send_notification
        )

    async def get_current_position(self):
        return await self.position_manager.get_current_position()

    async def get_avg_entry_price(self, skip_pos=False):
        return await self.position_manager.get_avg_entry_price(skip_pos)

    async def get_spot_balance(self, total=False):
        return await self.position_manager.get_spot_balance(total)

    def _check_technical_filters(self, signal_type, indicators):
        return self.signal_processor.check_technical_filters(signal_type, indicators)

    def _check_candlestick_pattern(self, data_input):
        return self.signal_processor.check_candlestick_pattern(data_input)

    def _execute_sim_trade(self, signal_data, current_price):
        return asyncio.run(self.order_executor.execute_sim_trade(signal_data, current_price))

    # _record_sim_trade removed as it is handled by OrderExecutor

    def _load_sim_state(self):
        """Load simulation state from JSON"""
        if os.path.exists(self.sim_state_file):
            try:
                with open(self.sim_state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.position_manager.set_sim_state(
                        state.get('balance', 0.0),
                        state.get('position'),
                        state.get('trades', []),
                        state.get('realized_pnl', 0.0)
                    )
            except Exception as e:
                self._log(f"读取模拟状态失败: {e}", 'warning')
        else:
            self.position_manager.sim_trades = []

    def _save_sim_state(self):
        """Save simulation state to JSON"""
        try:
            state = self.position_manager.get_sim_state()
            # Map back to storage format
            storage_state = {
                'position': state['sim_position'],
                'realized_pnl': state['sim_realized_pnl'],
                'balance': state['sim_balance'],
                'trades': state['sim_trades']
            }
            with open(self.sim_state_file, 'w', encoding='utf-8') as f:
                json.dump(storage_state, f, indent=4)
        except Exception as e:
            self._log(f"保存模拟状态失败: {e}", 'warning')



    async def initialize(self):
        """Async Initialization"""
        # [New] Init Data Manager
        await self.data_manager.initialize()
        
        await self.setup_leverage()
        # [Fix] 使用内部已有的 _update_fee_rate 方法，避免重复定义
        if hasattr(self, '_update_fee_rate'):
            await self._update_fee_rate()

        # [New] Smart Balance Calibration (智能资金校准)
        # 解决配置资金与实际资金偏差导致的错误盈亏计算问题
        try:
            current_equity = await self.get_account_equity()
            if current_equity > 0:
                # [Modified] 放宽资金校准阈值 (10% -> 50%)
                # 用户反馈: 希望看到历史累计亏损，而不是每次重启都重置
                # 只有当偏差极大 (例如充值/提现导致变动 > 50%) 时才自动校准
                if self.initial_balance <= 0 or abs(self.initial_balance - current_equity) / current_equity > 0.5:
                    self._log(f"⚖️ 初始资金校准: 配置({self.initial_balance}) vs 实际({current_equity:.2f}) 偏差过大 -> 自动修正", 'warning')
                    self.initial_balance = current_equity
                    # 同时更新 risk_control 里的值，确保一致性
                    if self.risk_control:
                        self.risk_control['initial_balance_usdt'] = current_equity
        except Exception as e:
            # 只有在失败时才打印警告，成功时静默
            self._log(f"⚠️ 资金校准失败: {e}", 'warning')

    def _log(self, msg, level='info'):
        if level == 'info':
            self.logger.info(f"[{self.symbol}] {msg}")
        elif level == 'error':
            self.logger.error(f"[{self.symbol}] {msg}")
        elif level == 'warning':
            self.logger.warning(f"[{self.symbol}] {msg}")

    async def send_notification(self, message, title=None):
        if not self.notification_config.get('enabled', False):
            return
        webhook_url = self.notification_config.get('webhook_url')
        
        # 移除旧的 wrapper，直接发送干净的消息
        # title 默认加上 Symbol
        final_title = title if title else f"🤖 通知 | {self.symbol}"
        
        await send_notification_async(webhook_url, message, title=final_title)

    async def _update_amount_auto(self, current_price, current_balance=None):
        if self.config_amount != 'auto' and isinstance(self.config_amount, (int, float)) and self.config_amount > 0:
            self.amount = self.config_amount
            return

        try:
            # [Fix] 测试模式下使用每个交易对自己的模拟余额作为基础资金
            if self.test_mode:
                base_capital = self.position_manager.sim_balance
            else:
                # 实盘模式下，优先使用配置的初始本金，如果没有(0)，则使用当前实时余额
                base_capital = self.initial_balance if self.initial_balance > 0 else (current_balance if current_balance else 0)
            
            quota = 0
            if base_capital > 0:
                if isinstance(self.allocation, str) and self.allocation == 'auto':
                    # [Fix] 测试模式下，auto 分配直接使用完整的模拟余额
                    if self.test_mode:
                        quota = base_capital
                    else:
                        # 实盘模式下，按活跃交易对数量平均分配
                        if self.active_symbols_count > 0:
                            quota = base_capital / self.active_symbols_count
                elif isinstance(self.allocation, (int, float)):
                    if self.allocation <= 1.0:
                        quota = base_capital * self.allocation
                    else:
                        quota = self.allocation
            
            if quota <= 0:
                target_usdt = 10.0
            else:
                # [Adjusted] 恢复为 0.98 (留一点余量)，不再强制分批 (0.2)
                # 用户配置了 allocation 就是希望能用到那个比例
                target_usdt = quota * 0.98
            
            market = self.exchange.market(self.symbol)
            min_cost = 5.0
            cost_min = market.get('limits', {}).get('cost', {}).get('min')
            if cost_min is not None:
                min_cost = float(cost_min)
            
            # Use max(target_usdt, min_cost * 1.5)
            # Ensure min_cost is valid
            if min_cost > 0:
                target_usdt = max(target_usdt, min_cost * 1.5)
            else:
                target_usdt = max(target_usdt, 5.0)

            market = self.exchange.market(self.symbol)
            min_amount = market.get('limits', {}).get('amount', {}).get('min')
            
            # 获取精度作为最小限制的补充参考
            precision_amount = market.get('precision', {}).get('amount')
            limit_floor = min_amount if min_amount else precision_amount

            raw_amount = target_usdt / current_price
            
            # 自动适配最小下单数量 (防止精度报错)
            if limit_floor and raw_amount < limit_floor:
                # 如果资金允许，尝试提升到最小数量
                self._log(f"⚠️ 数量 {raw_amount:.6f} < 最小限额 {limit_floor}，自动修正", 'debug')
                raw_amount = limit_floor * 1.05 # 稍微多一点避免边界问题
            
            precise_amount_str = self.exchange.amount_to_precision(self.symbol, raw_amount)
            self.amount = float(precise_amount_str)
            
        except Exception as e:
            self._log(f"自动计算 amount 失败: {e}", 'error')
            self.amount = 0

    async def _update_fee_rate(self):
        try:
            fees = await self.exchange.fetch_trading_fee(self.symbol)
            if fees:
                new_taker = to_float(fees.get('taker', self.taker_fee_rate))
                new_maker = to_float(fees.get('maker', self.maker_fee_rate))
                if new_taker is not None and new_maker is not None:
                    if new_taker != self.taker_fee_rate or new_maker != self.maker_fee_rate:
                        self._log(f"💳 费率自动校准: Taker {new_taker*100:.4f}% | Maker {new_maker*100:.4f}%")
                        self.taker_fee_rate = new_taker
                        self.maker_fee_rate = new_maker
        except Exception as e:
            self._log(f"⚠️ 费率获取失败: {e}", 'warning')

    async def setup_leverage(self):
        try:
            if self.trade_mode == 'cash': return
            await self.exchange.set_leverage(self.leverage, self.symbol, {'mgnMode': self.margin_mode})
            # [Fix] Remove emoji dependency to prevent runtime errors if package missing
            # self._log(emoji.emojize(f":gear: 设置杠杆: {self.leverage}x ({self.margin_mode})"))
            self._log(f"⚙️ 设置杠杆: {self.leverage}x ({self.margin_mode})")
        except Exception as e:
            # self._log(emoji.emojize(f":no_entry: 杠杆设置失败: {e}"), 'error')
            self._log(f"🚫 杠杆设置失败: {e}", 'error')



    def normalize_data(self, df):
        """
        [Data Wrangling] 数据整理 - 时间对齐与缺省填充
        确保 K 线时间轴连续，填补因维护或停机导致的空洞
        """
        try:
            if df.empty: return df
            
            # [Fix] 去重：确保时间戳唯一 (Duplicate Labels Check)
            # [Hardcore Fix] 强制时间戳取整对齐，彻底消除毫秒级微小差异导致的 Duplicate Label
            # 例如: 10:00:00.001 和 10:00:00.002 会被统一为 10:00:00
            
            # 1. 确保是 datetime 类型
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            else:
                df = df.reset_index()
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # 2. 强制 Rounding (根据 timeframe 动态调整)
            # 这里统一 Round 到 '1s' 精度，足以应付所有 K 线 (最小 1m)
            # 如果是毫秒级高频 K 线，可能需要调整，但 CCXT 最小也是 1m
            df['timestamp'] = df['timestamp'].dt.floor('1s')
            
            # 3. 再次去重 (这次是基于 Round 后的时间戳)
            df = df.drop_duplicates(subset=['timestamp'], keep='last')
            
            # 4. 设置索引
            df = df.set_index('timestamp').sort_index()
            
            # 1. 转换 Timeframe 为 Pandas Offset
            # CCXT: 1m, 5m, 1h, 1d, 1w
            # Pandas: 1min, 5min, 1h, 1D, 1W
            tf = self.timeframe
            freq = None
            if tf.endswith('m'): freq = tf.replace('m', 'min')
            elif tf.endswith('h'): freq = tf.replace('h', 'H')
            elif tf.endswith('d'): freq = tf.replace('d', 'D')
            elif tf.endswith('w'): freq = tf.replace('w', 'W')
            
            if not freq: return df # 不支持的周期，跳过
            
            # [Fix] 再次去重 (Just in case index still has duplicates)
            df = df[~df.index.duplicated(keep='last')]
            
            # 3. 重采样 (Resample) - 强制对齐时间网格
            # 使用 asfreq() 插入缺失行 (值为 NaN)
            df_resampled = df.resample(freq).asfreq()
            # 规则: 
            # - Close: 沿用上一个 Close (Forward Fill)
            # - Open/High/Low: 既然无成交，价格应等于 Close (画十字星)
            # - Volume: 0
            
            if df_resampled.isnull().any().any():
                # self._log(f"🔧 检测到 K 线缺失，正在修补...", 'debug')
                
                df_resampled['close'] = df_resampled['close'].ffill()
                df_resampled['volume'] = df_resampled['volume'].fillna(0)
                
                # Open/High/Low 填充为 Close (此时 Close 已经是填充过的了)
                df_resampled['open'] = df_resampled['open'].fillna(df_resampled['close'])
                df_resampled['high'] = df_resampled['high'].fillna(df_resampled['close'])
                df_resampled['low'] = df_resampled['low'].fillna(df_resampled['close'])
            
            # 5. 还原索引
            df_final = df_resampled.reset_index()
            
            return df_final
            
        except Exception as e:
            self._log(f"数据整理失败: {e}", 'error')
            return df

    def clean_data(self, df):
        """
        [Data Cleaning] 数据清洗 - 剔除价格异常值 (Z-Score)
        防止插针导致指标计算错误
        """
        try:
            if len(df) < 20: return df
            
            # 计算 Close 价格的 Z-Score
            # 这里的窗口可以稍微大一点，比如 20
            rolling_mean = df['close'].rolling(window=20).mean()
            rolling_std = df['close'].rolling(window=20).std().replace(0, np.nan) # [Fix] Avoid div by zero
            
            # 异常阈值: 3倍标准差
            threshold = 3.0
            
            # 标记异常值 (Z-Score > 3)
            # 我们只清洗 "收盘价"，因为指标计算主要依赖 Close
            # 如果某根 K 线的 Close 极其离谱，我们用 rolling_mean 替换它
            z_score = abs(df['close'] - rolling_mean) / rolling_std
            
            outliers = z_score > threshold
            if outliers.any():
                outlier_count = outliers.sum()
                # self._log(f"🧹 检测到 {outlier_count} 个价格异常点，正在清洗...", 'warning')
                
                # 用均值填充异常值
                df.loc[outliers, 'close'] = rolling_mean[outliers]
                
                # 同时也修正 High/Low，防止 High < Close 或 Low > Close
                df.loc[outliers, 'high'] = df.loc[outliers, ['high', 'close']].max(axis=1)
                df.loc[outliers, 'low'] = df.loc[outliers, ['low', 'close']].min(axis=1)
                
            return df
        except Exception as e:
            # self._log(f"数据清洗失败: {e}", 'error')
            return df

    def calculate_indicators(self, df):
        try:
            if len(df) < 30: return df
            
            # [Step 0] Data Wrangling (Time Alignment)
            df = self.normalize_data(df)
            
            # [Step 1] Data Cleaning
            df = self.clean_data(df)
            
            # [Step 2] RSI (Wilder's Smoothing)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rs = gain / loss.replace(0, np.nan)
            df['rsi'] = 100 - (100 / (1 + rs))
            df['rsi'] = df['rsi'].fillna(50) # Fill initial NaNs with neutral 50

            # [Step 3] MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal_line'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['macd_hist'] = df['macd'] - df['signal_line']

            # [Step 4] Bollinger Bands
            df['sma_20'] = df['close'].rolling(window=20).mean()
            df['std_20'] = df['close'].rolling(window=20).std()
            df['upper_band'] = df['sma_20'] + (df['std_20'] * 2)
            df['lower_band'] = df['sma_20'] - (df['std_20'] * 2)
            
            # [Step 5] Volume Ratio
            df['vol_sma_20'] = df['volume'].rolling(window=20).mean().replace(0, np.nan)
            df['vol_ratio'] = df['volume'] / df['vol_sma_20'] # 量比
            df['vol_ratio'] = df['vol_ratio'].fillna(0)
            
            # [New] 计算买卖压力指标 (OBV & Delta Volume)
            # 1. OBV: Close > PrevClose => +Vol, else -Vol
            df['obv_change'] = 0.0
            df.loc[df['close'] > df['close'].shift(), 'obv_change'] = df['volume']
            df.loc[df['close'] < df['close'].shift(), 'obv_change'] = -df['volume']
            df['obv'] = df['obv_change'].cumsum()
            
            # 2. 估算买入量占比 (Buying Pressure)
            # 使用简单的 Close-Open 逻辑: 阳线视为买入主导，阴线视为卖出主导
            # 也可以用更细的 (Close-Low)/(High-Low)
            # 这里用最近 5 根 K 线的阳线成交量占比
            df['is_up_candle'] = df['close'] >= df['open']
            df['up_vol'] = df['volume'].where(df['is_up_candle'], 0)
            df['down_vol'] = df['volume'].where(~df['is_up_candle'], 0)
            
            # 5周期买盘占比 (0~1)
            vol_sum_5 = df['volume'].rolling(window=5).sum().replace(0, np.nan)
            df['buy_vol_prop_5'] = df['up_vol'].rolling(window=5).sum() / vol_sum_5
            df['buy_vol_prop_5'] = df['buy_vol_prop_5'].fillna(0.5) # Default to 0.5 if no volume
            
            # [Step 6] ADX & ATR (Wilder's Smoothing)
            df['tr0'] = abs(df['high'] - df['low'])
            df['tr1'] = abs(df['high'] - df['close'].shift())
            df['tr2'] = abs(df['low'] - df['close'].shift())
            df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
            
            df['up_move'] = df['high'] - df['high'].shift()
            df['down_move'] = df['low'].shift() - df['low']
            df['plus_dm'] = 0.0
            df['minus_dm'] = 0.0
            df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), 'plus_dm'] = df['up_move']
            df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), 'minus_dm'] = df['down_move']
            
            window = 14
            # Use EWM for Wilder's Smoothing (alpha=1/n)
            df['tr_smooth'] = df['tr'].ewm(alpha=1/window, adjust=False).mean()
            df['plus_di'] = 100 * (df['plus_dm'].ewm(alpha=1/window, adjust=False).mean() / df['tr_smooth'].replace(0, np.nan))
            df['minus_di'] = 100 * (df['minus_dm'].ewm(alpha=1/window, adjust=False).mean() / df['tr_smooth'].replace(0, np.nan))
            
            sum_di = df['plus_di'] + df['minus_di']
            df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / sum_di.replace(0, np.nan)
            df['adx'] = df['dx'].ewm(alpha=1/window, adjust=False).mean()
            
            # [New] ATR (Average True Range) Calculation
            # tr_smooth is basically ATR (Wilder's Smoothing)
            df['atr'] = df['tr_smooth']
            
            # [New] ATR Ratio (波动率因子)
            # 当前 ATR / 过去 50根 K线的平均 ATR
            # 如果 < 0.5，说明波动率极度萎缩 (死鱼盘)
            df['atr_ma50'] = df['atr'].rolling(window=50).mean().replace(0, np.nan)
            df['atr_ratio'] = df['atr'] / df['atr_ma50']
            
            return df
        except Exception as e:
            self._log(f"计算技术指标失败: {e}", 'error')
            return df
            



    @exception_handler
    @retry_async(retries=3, delay=1.0, backoff=2.0)
    async def get_ohlcv(self):
        # 生成缓存键
        cache_key = cache_manager.generate_key(
            'ohlcv',
            symbol=self.symbol,
            timeframe=self.timeframe
        )
        
        # 尝试从缓存获取数据
        cached_data = cache_manager.get(cache_key)
        if cached_data:
            self._log(f"使用缓存的K线数据", 'debug')
            return cached_data
        
        # [兼容性处理] 如果配置了毫秒级周期 (如 "500ms")，API 请求强制使用 "1m"
        # OKX 不支持 "1s", "30s" 等周期，最低为 "1m"
        api_timeframe = self.timeframe
        if 'ms' in self.timeframe or self.timeframe.endswith('s'):
            api_timeframe = '1m'
        
        # [Fix 51000 Error] 确保 limit 足够大，有些交易所对小周期请求有最小数量要求
        # 或者当 API 周期为 1m 时，不要请求奇怪的数量
        # 增加超时设置，防止 fetch_ohlcv 永久挂起
        # [Resume] 尝试从数据库加载最近的 K 线 (断点续传)
        # 优先使用本地数据，以减少 API 调用并保持状态连续性
        # 但为了数据的实时性，我们仍需要拉取最新的数据进行合并
        local_klines = []
        try:
            local_klines = await self.data_manager.get_recent_klines(self.symbol, self.timeframe, limit=200)
        except Exception as e:
            self._log(f"加载本地历史数据失败: {e}", 'warning')

        # [Optimization] 获取 200 根 K 线
        # 如果本地有足够数据，理论上我们可以只拉取最近的几十根，但为了安全起见（防止长时间停机导致的巨大 Gap），
        # 这里还是拉取 200 根，然后做 merge
        ohlcv = await asyncio.wait_for(
            self.exchange.fetch_ohlcv(self.symbol, api_timeframe, limit=200),
            timeout=10
        )
        df_new = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_new['timestamp'] = pd.to_datetime(df_new['timestamp'], unit='ms')
        
        # [Merge] 合并本地数据与新数据
        df = df_new
        if local_klines:
            try:
                df_local = pd.DataFrame(local_klines)
                # 确保 timestamp 类型一致
                df_local['timestamp'] = pd.to_datetime(df_local['timestamp'])
                
                # 合并并去重 (以 timestamp 为准)
                # [Fix] keep='last' to prefer new API data over local stale data
                # 如果时间戳冲突，说明本地存的是之前的"未收盘"快照，必须用新的覆盖
                df = pd.concat([df_local, df_new]).drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                
                # [New] 双重去重保险：如果 concat 后的索引出现重复
                if df.duplicated(subset=['timestamp']).any():
                     self._log("⚠️ 检测到重复时间戳，强制去重...", 'warning')
                     df = df.drop_duplicates(subset=['timestamp'], keep='last')

                # [Clean] 清洗本地脏数据：如果出现非时间戳的异常行，强制删除
                # 有时候数据库损坏会导致 null 或 0 时间戳
                df = df[df['timestamp'].notna()]
                
                # 保持长度在合理范围 (例如 500)
                df = df.tail(500)
            except Exception as e:
                self._log(f"合并本地K线失败: {e}", 'warning')
                df = df_new # Fallback to API data only
        
        # 维护历史 K 线记录
        self.price_history = df.tail(100).to_dict('records')
        
        # 使用默认值进行预热检查（不再依赖 config 中的 history_limit）
        if not self.price_history and len(df) > 50:
            self._log(f"🔥 正在预热历史数据...")
            pass
        
        # 计算指标
        df = self.calculate_indicators(df)
        
        # [Fix] 如果指标计算失败 (df 长度过短或异常)，直接返回 None
        # 否则后续访问 indicators['obv'] 会报错
        if 'obv' not in df.columns:
            self._log("指标计算异常: OBV 列缺失", 'warning')
            return None
        
        # [Fix] 先计算指标字典，用于确定 volatility_status
        current_data = df.iloc[-1]
        previous_data = df.iloc[-2] if len(df) > 1 else current_data

        indicators = {
            'rsi': float(current_data['rsi']) if pd.notna(current_data.get('rsi')) else None,
            'macd': float(current_data['macd']) if pd.notna(current_data.get('macd')) else None,
            'macd_signal': float(current_data['signal_line']) if pd.notna(current_data.get('signal_line')) else None,
            'macd_hist': float(current_data['macd_hist']) if pd.notna(current_data.get('macd_hist')) else None,
            'bb_upper': float(current_data['upper_band']) if pd.notna(current_data.get('upper_band')) else None,
            'bb_lower': float(current_data['lower_band']) if pd.notna(current_data.get('lower_band')) else None,
            'bb_middle': float(current_data['sma_20']) if pd.notna(current_data.get('sma_20')) else None,
            'adx': float(current_data['adx']) if pd.notna(current_data.get('adx')) else None,
            'vol_ratio': float(current_data['vol_ratio']) if pd.notna(current_data.get('vol_ratio')) else None,
            'obv': float(current_data['obv']) if pd.notna(current_data.get('obv')) else None,
            'buy_prop': float(current_data['buy_vol_prop_5']) if pd.notna(current_data.get('buy_vol_prop_5')) else None,
            'atr': float(current_data['atr']) if pd.notna(current_data.get('atr')) else None,
            'atr_ratio': float(current_data['atr_ratio']) if pd.notna(current_data.get('atr_ratio')) else None, # [New]
            }
        
        # [New] Store indicators for Smart Sizing usage in execute_trade
        self.last_indicators = indicators
        
        # [New] Determine Volatility Status (Moved Up for DB Saving)
        vol_status = "NORMAL"
        atr_r = indicators['atr_ratio'] if indicators['atr_ratio'] is not None else 1.0
        adx_val = indicators['adx'] if indicators['adx'] is not None else 25.0
        
        if atr_r < 0.6:
            vol_status = "LOW" # 死鱼盘 -> 网格模式
        elif adx_val > 30:
            vol_status = "HIGH_TREND" # 强趋势 -> 趋势模式
        elif atr_r > 1.5:
            vol_status = "HIGH_CHOPPY" # 剧烈震荡 -> 均值回归模式
        
        # [Fix] 将状态写回 DataFrame 的最后一行，以便 DataManager 保存
        # 注意: 这里只更新最后一行，历史行的 status 可能是空的，但我们主要关心最新的
        df.loc[df.index[-1], 'volatility_status'] = vol_status

        # [New] 异步保存 K 线数据 (现在包含了 volatility_status)
        # [Fix] 显式重置索引，确保 timestamp 作为普通列传递给 save_klines
        # 因为前面 set_index 导致 timestamp 变成了索引，直接 row['timestamp'] 会报错
        df_to_save = df.tail(1).reset_index()
        asyncio.create_task(self.data_manager.save_klines(self.symbol, self.timeframe, df_to_save))

        # 显式传递最小交易单位给 AI
        min_limit_info = "0.01"
        min_notional_info = "5.0"
        try:
            market = self.exchange.market(self.symbol)
            min_amount = market.get('limits', {}).get('amount', {}).get('min')
            if min_amount:
                min_limit_info = str(min_amount)
            min_cost = market.get('limits', {}).get('cost', {}).get('min')
            if min_cost:
                min_notional_info = str(min_cost)
        except:
            pass

        # [Modified] 动态计算投喂给 AI 的 K 线数量 (feed_limit)
        # 即使配置文件写死，这里也优先使用动态逻辑，以适应不同 Timeframe
        feed_limit = 24 # Default
        tf = self.timeframe
        if tf == '1m': feed_limit = 60    # 1h context
        elif tf == '3m': feed_limit = 40  # 2h context
        elif tf == '5m': feed_limit = 36  # 3h context
        elif tf == '15m': feed_limit = 32 # 8h context
        elif tf == '30m': feed_limit = 24 # 12h context
        elif tf == '1h': feed_limit = 24  # 24h context
        elif tf == '4h': feed_limit = 24  # 4d context
        elif tf == '1d': feed_limit = 14  # 2w context
        
        # 如果配置文件特别指定了极大的值 (例如为了 debug)，可以保留 override 逻辑，
        # 但这里我们默认采用动态逻辑覆盖配置，除非配置值为 "auto" (目前代码里是 int)
        # 简单起见，直接使用上述动态值，并确保不低于 10
        feed_limit = max(10, feed_limit)
        
        # [New] Determine Volatility Status for AI Persona
        # 这一步非常关键：它决定了 AI 是"趋势猎人"还是"网格交易员"
        # [Fix] Already calculated above
        
        # [Real-time Correction] 实时 Tick 修正
        # 获取最新成交价，计算其与 K 线收盘价的偏离度
        ticker_price = current_data['close'] # default
        price_divergence = 0.0
        try:
            ticker = await self.exchange.fetch_ticker(self.symbol)
            ticker_price = float(ticker['last'])
            # 偏离度 % (Tick - Close) / Close
            price_divergence = ((ticker_price - current_data['close']) / current_data['close']) * 100
        except:
            pass

        result = {
            'volatility_status': vol_status, # [New] Added for AI Persona
            'price': ticker_price, # [Modified] Use real-time ticker price instead of kline close
            'kline_close': current_data['close'], # Keep original close for reference
            'price_divergence': price_divergence, # [New] Tell AI about the lag
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': self.timeframe,
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            # 这里改为使用 dynamic feed_limit
            # [Fix] 显式重置索引，否则 to_dict('records') 会丢失 timestamp
            'kline_data': df.tail(feed_limit).reset_index()[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vol_ratio', 'obv']].to_dict('records'),
            'indicators': indicators,
            'min_limit_info': min_limit_info,
            'min_notional_info': min_notional_info,
        }
        
        # 生成缓存键并缓存结果
        cache_key = cache_manager.generate_key(
            'ohlcv',
            symbol=self.symbol,
            timeframe=self.timeframe
        )
        # [Optimized] Cache TTL tuning
        # 1m -> 30s
        # 5m/15m -> 60s
        # >=1h -> 300s (Reduce CPU load for higher timeframes)
        cache_ttl = 30
        if 'h' in self.timeframe or 'd' in self.timeframe:
             cache_ttl = 300
        elif self.timeframe in ['5m', '15m', '30m']:
             cache_ttl = 60
             
        cache_manager.set(cache_key, result, ttl=cache_ttl)
        
        # [New] Attach DataFrame object to result for immediate use (NOT cached)
        # This allows _check_candlestick_pattern to use the DataFrame directly
        result['df'] = df
        
        # [New] Pass indicators to result for SignalProcessor context awareness
        # Already included in 'indicators' key above
        
        return result







    async def get_my_trades(self, limit=100):
        """Helper to get recent trades (Real or Simulated)"""
        if self.test_mode:
            # Return last N trades
            return self.position_manager.sim_trades[-limit:]
        
        return await self.exchange.fetch_my_trades(self.symbol, limit=limit)

    async def _auto_detect_strategy_mode(self, balance_usdt):
        """[New] 根据资金规模自动切换策略模式 (Auto-Scaling Strategy)"""
        try:
            # 资金分层阈值 (USDT)
            THRESHOLD_MICRO = 100    # 微型资金 (<100U) -> 全仓现货狙击
            THRESHOLD_SMALL = 1000   # 小型资金 (<1000U) -> 现货分仓防御
            THRESHOLD_MEDIUM = 10000 # 中型资金 (<1W U) -> 现货+低倍合约混合
            # > 10000U -> 大型资金 -> 高频网格/套利 (需更复杂逻辑，暂归入混合)
            
            # 当前配置的模式
            current_mode = self.trade_mode # 'cash' or 'cross'
            current_alloc = self.allocation
            
            new_alloc = current_alloc
            strategy_tag = "UNKNOWN"

            if balance_usdt < THRESHOLD_MICRO:
                strategy_tag = "MICRO_SNIPER (全仓现货狙击)"
                # 微型资金：建议全仓 (0.95~0.98)，只做现货
                if current_mode == 'cash' and (current_alloc == 'auto' or float(current_alloc) < 0.9):
                     new_alloc = 0.98
                     self._log(f"💡 策略切换: 全仓狙击模式 (资金<{THRESHOLD_MICRO}U)", 'debug')
                elif current_mode != 'cash':
                     self._log(f"⚠️ 建议切换现货模式 (资金较小)", 'debug')

            elif balance_usdt < THRESHOLD_SMALL:
                strategy_tag = "SMALL_DEFENSE (现货分仓防御)"
                # 小型资金：建议分仓 (0.2~0.3)，防止单次重创
                if current_alloc == 'auto' or float(current_alloc) > 0.4:
                     # 如果之前是梭哈模式，现在资金大了，建议降下来
                     # 但我们不强制修改用户的明确配置，只在 'auto' 时介入，或打印建议
                     if current_alloc == 'auto':
                         new_alloc = 0.33 # 3等分
                         self._log(f"💡 策略切换: 分仓防御模式 (资金增长)", 'info')

            else:
                strategy_tag = "WHALE_MIX (组合策略)"
                # 大资金：建议更低的分仓
                if current_alloc == 'auto':
                    new_alloc = 0.1 # 10等分
            
            return new_alloc, strategy_tag

        except Exception as e:
            self._log(f"策略自动判断失败: {e}", 'warning')
            return self.allocation, "ERROR"



    async def execute_trade(self, signal_data, current_price=None, current_position=None, balance=None):
        """执行交易 (Async - Enhanced Logic)"""
        
        # [Moved Up] 提前获取持仓信息，供信心过滤逻辑使用
        if current_position is None:
            current_position = await self.get_current_position()

        # [New] 优先检查移动止盈 (Trailing Stop)
        # 如果触发了止盈，直接结束本次交易循环，防止 AI 再次开仓
        if await self.check_trailing_stop(current_position):
            self._log("⚡ 移动止盈已执行，跳过本次 AI 信号处理")
            return "EXECUTED", "移动止盈触发"

        # [New] Circuit Breaker (Cool-down)
        # 如果最近刚触发过止损，强制暂停开新仓 (Closing 操作除外)
        # 防止在震荡市中反复止损 (Whipsaw)
        is_opening = False
        if signal_data['signal'] == 'BUY':
             if not current_position or current_position['side'] == 'long': is_opening = True
             elif current_position['side'] == 'short' and signal_data.get('amount', 0) > 0: is_opening = True # Flip is also opening
        elif signal_data['signal'] == 'SELL':
             if self.trade_mode != 'cash' and (not current_position or current_position['side'] == 'short'): is_opening = True
             elif current_position and current_position['side'] == 'long' and signal_data.get('amount', 0) > 0: is_opening = True # Flip is also opening
        
        # 1. 交易频率限制 (Frequency Limit)
        # 强制限制开仓间隔，防止高频刷单 (Churning)
        # 默认间隔 5分钟 (300s)，可通过 min_trade_interval 配置
        # 仅针对开新仓 (is_opening)，平仓 (Closing) 不受限制以确保风险控制
        import time
        now = time.time()
        if is_opening:
             # Check Stop Loss Cool-down
             if self.last_stop_loss_time > 0:
                 time_since_sl = now - self.last_stop_loss_time
                 if time_since_sl < self.cool_down_seconds:
                     self._log(f"🧊 止损冷却中: 剩余 {int(self.cool_down_seconds - time_since_sl)}s (保护期)", 'warning')
                     return "SKIPPED_COOL_DOWN", "止损保护期"
             
             # Check Trade Frequency Cool-down
             if self.last_trade_time > 0:
                 time_since_trade = now - self.last_trade_time
                 # 如果上一笔交易发生还没多久，且这笔也是开仓，则拦截
                 # 除非是加仓 (Scaling In)? 暂不区分，统一限制，防止 AI 发疯连续下单
                 # 但如果是 AI 连续喊单，可能是为了分批建仓...
                 # 为了防止"日内高频刷单"，我们设置一个较短的间隔，比如 3分钟 (180s)
                 # 或者使用 min_trade_interval (300s)
                 limit_interval = getattr(self, 'min_trade_interval', 300)
                 if time_since_trade < limit_interval:
                      self._log(f"⏳ 交易频率限制: 距离上次开仓仅 {int(time_since_trade)}s (需等待 {limit_interval}s)", 'warning')
                      return "SKIPPED_FREQ_LIMIT", "交易频率限制"

        # 2. 信心过滤
        confidence_levels = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
        current_conf_val = confidence_levels.get(signal_data.get('confidence', 'LOW').upper(), 1)
        min_conf_val = confidence_levels.get(self.min_confidence.upper(), 2)
        
        # [New] 记录原始信心值，用于反手保护 (Flip Protection)
        # 如果因为平仓豁免了信心，但反手开新仓时必须检查原始信心
        original_conf_val = current_conf_val
        
        # [Fix] 如果是 SELL 信号（开空或平仓），且处于单边下跌趋势 (HIGH_TREND)，则放宽信心要求
        # 允许 LOW 信心执行，防止踏空暴跌
        is_strong_downtrend = False
        volatility_status = 'NORMAL'
        try:
            volatility_status = signal_data.get('volatility_status', 'NORMAL')
            # 如果 AI 没返回 volatility_status，我们可以尝试从 price_data 里拿（如果传进来的话）
            # 或者更直接地：如果 AI 建议 SELL 并且理由包含 "下跌趋势"、"空头" 等关键词
            reason_lower = signal_data.get('reason', '').lower()
            keywords = ["下跌", "趋势", "空头", "downtrend", "bearish", "flip", "reverse", "反手", "止损"]
            if any(k in reason_lower for k in keywords):
                is_strong_downtrend = True
        except:
            pass

        # 逻辑优化：
        # 1. 场景A: 持仓状态下的 SELL (止损/平仓) -> 始终允许 LOW 信心
        # 2. 场景B: 强趋势下的 SELL (开空) -> 允许 LOW 信心 (防止踏空)
        if signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                if current_conf_val < min_conf_val:
                    self._log(f"⚠️ 信心豁免(止损): 持仓状态下的 SELL，忽略信心阈值")
                    current_conf_val = max(current_conf_val, 2) # 强制提权到 MEDIUM
            elif is_strong_downtrend:
                if current_conf_val < min_conf_val:
                    self._log(f"⚠️ 信心豁免(趋势): 检测到下跌趋势描述，允许低信心开空")
                    current_conf_val = max(current_conf_val, 2) # 强制提权到 MEDIUM
        
        # [New] 如果是 BUY 信号且持空仓 (平空)，也允许 LOW 信心 (止损/止盈)
        if signal_data['signal'] == 'BUY' and current_position and current_position['side'] == 'short':
            if current_conf_val < min_conf_val:
                self._log(f"⚠️ 信心豁免(平空): 持空状态下的 BUY，忽略信心阈值")
                current_conf_val = max(current_conf_val, 2)

        # [New] Grid Trader Exemption: Allow LOW confidence BUYs in Low Volatility
        # 网格策略在震荡市中通常信心不高，但这是正常的吸筹行为
        # [Fix] 变量覆盖问题
        # 上面 978 行重新获取了 volatility_status，这里直接使用，不再覆盖
        # volatility_status = signal_data.get('volatility_status', 'NORMAL')
        if volatility_status == 'LOW' and signal_data['signal'] == 'BUY':
            if current_conf_val < min_conf_val:
                self._log(f"⚠️ 信心豁免(网格): 低波动市场(LOW Volatility)允许低信心吸筹")
                current_conf_val = max(current_conf_val, 2) # 强制提权到 MEDIUM

        if current_conf_val < min_conf_val:
            self._log(f"✋ 信心不足: {signal_data.get('confidence')} < {self.min_confidence}, 强制观望", 'debug')
            signal_data['signal'] = 'HOLD'
            return "SKIPPED_CONF", f"信心不足 {signal_data.get('confidence')}"

        if signal_data['signal'] == 'HOLD':
            # [New] Update Dynamic Risk Params even on HOLD
            if current_position:
                sl = float(signal_data.get('stop_loss', 0) or 0)
                tp = float(signal_data.get('take_profit', 0) or 0)
                # Only update if AI provides a non-zero value
                if sl > 0: 
                    self.dynamic_stop_loss = sl
                    self.dynamic_sl_side = current_position['side']
                if tp > 0: 
                    self.dynamic_take_profit = tp
                    self.dynamic_sl_side = current_position['side']

            return "HOLD", "AI建议观望"

        # [Disabled] Hard Technical Filters (Win Rate > 60%)
        # User Feedback: Remove this filter to allow more trades, especially for Shorting
        # is_entry = False
        # ... (Original logic commented out or removed)


        if self.test_mode:
            # Need a price for simulation
            exec_price = current_price
            if exec_price is None:
                 try:
                     ticker = await self.exchange.fetch_ticker(self.symbol)
                     exec_price = ticker['last']
                 except:
                     exec_price = 0
            
            if exec_price > 0:
                return self._execute_sim_trade(signal_data, exec_price)
            else:
                self._log(f"🧪 测试模式: {signal_data['signal']} (无法获取价格，跳过)")
                return "TEST_MODE", "无法获取价格"

        target_side = 'long' if signal_data['signal'] == 'BUY' else 'short'
            
        # 2. 价格滑点检查
        if current_price is None:
            ticker = await self.exchange.fetch_ticker(self.symbol)
            current_realtime_price = ticker['last']
        else:
            current_realtime_price = current_price
            
        try:
            # [Revised Slippage Logic]
            # analysis_price = 传入的 current_price (即 AI 分析时的 K 线 Close)
            # real_price = fetch_ticker() (当前最新成交价)
            
            analysis_price = current_price
            if analysis_price is None:
                 # 如果没有传入价格，尝试获取一次 (虽然慢)
                 try:
                     ohlcv_data = await self.get_ohlcv()
                     if ohlcv_data:
                         analysis_price = ohlcv_data['price']
                 except:
                     pass

            # 无论如何，获取最新的实时 Ticker 用于对比和下单
            ticker = await self.exchange.fetch_ticker(self.symbol)
            real_exec_price = ticker['last']
            
            # 更新后续逻辑使用的价格为最新成交价
            current_realtime_price = real_exec_price 
            
            # [Risk] Enforce Stop Loss / Take Profit
            sl = float(signal_data.get('stop_loss', 0) or 0)
            tp = float(signal_data.get('take_profit', 0) or 0)
            
            # Default Stop Loss if missing (User Requirement: 3-5%)
            if sl <= 0 and current_realtime_price > 0:
                 # Default 5% stop loss (using max_loss_rate from config if available)
                 risk_rate = 0.05
                 if hasattr(self, 'config') and 'risk_control' in self.config:
                     risk_rate = float(self.config['risk_control'].get('max_loss_rate', 0.05))
                 
                 # [ATR Dynamic SL]
                 # 如果是高波动币种，5% 太窄容易被洗。尝试用 3倍 ATR 作为止损
                 try:
                     last_indicators = getattr(self, 'last_indicators', {})
                     atr_val = last_indicators.get('atr')
                     if atr_val and atr_val > 0:
                         dynamic_rate = (atr_val / current_realtime_price) * 3.0
                         # 限制在 5% - 15% 之间 (太小就用 5%，太大不超过 15%)
                         new_risk_rate = max(0.05, min(dynamic_rate, 0.15))
                         if new_risk_rate > risk_rate:
                             self._log(f"🌊 高波动适配: ATR止损 {new_risk_rate*100:.1f}% > 默认 {risk_rate*100:.1f}%", 'info')
                             risk_rate = new_risk_rate
                 except:
                     pass

                 if target_side == 'long':
                     sl = current_realtime_price * (1 - risk_rate)
                 else:
                     sl = current_realtime_price * (1 + risk_rate)
                 
                 self._log(f"🛡️ 强制设置默认止损: {sl:.4f} (按照 {risk_rate*100:.1f}% 风控)", 'info')
            
            # Update Dynamic Risk Params
            if sl > 0:
                self.dynamic_stop_loss = sl
                self.dynamic_sl_side = target_side
            if tp > 0:
                self.dynamic_take_profit = tp
                self.dynamic_sl_side = target_side

            if analysis_price:
                 price_gap_percent = abs(real_exec_price - analysis_price) / analysis_price * 100
                 
                 if price_gap_percent > self.max_slippage:
                    self._log(f"⚠️ 价格波动过大: 偏差 {price_gap_percent:.2f}% > {self.max_slippage}%，取消交易", 'warning')
                    await self.send_notification(
                        f"**价格滑点保护**\n当前偏差: `{price_gap_percent:.2f}%` (阈值: `{self.max_slippage}%`)", 
                        title=f"⚠️ 交易取消 | {self.symbol}"
                    )
                    return "SKIPPED_SLIPPAGE", f"滑点 {price_gap_percent:.2f}%"
                 elif price_gap_percent > 0.5:
                    self._log(f"⚠️ 价格轻微波动: 偏差 {price_gap_percent:.2f}%，继续执行 (使用最新价)", 'info')
            
        except Exception as e:
            self._log(f"滑点检查失败: {e}", 'warning')

        # 3. 卖出微利风控 (仅针对平仓/减仓场景)
        # [Fix] 提前计算盈亏比例，防止 UnboundLocalError
        pnl_pct = 0
        if current_position and current_position.get('entry_price', 0) > 0:
             entry = current_position['entry_price']
             if current_position['side'] == 'long':
                 pnl_pct = (current_realtime_price - entry) / entry
             else:
                 pnl_pct = (entry - current_realtime_price) / entry

        # 如果 AI 信心为 HIGH，则认为是紧急离场，跳过此检查
        is_high_confidence = signal_data.get('confidence', '').upper() == 'HIGH'
        if signal_data['signal'] == 'SELL' and current_position and not is_high_confidence:
            # pnl_pct 已在上方计算，此处直接使用
            
            # 最小利润阈值: 双倍手续费 + 0.05% 滑点保护
            min_profit_threshold = (self.taker_fee_rate * 2) + 0.0005
            
            # 只有当处于微利状态 (0 < 收益 < 阈值) 时才拦截
            # 亏损状态(pnl < 0) 不拦截 (止损)
            # 暴利状态(pnl > 阈值) 不拦截 (止盈)
            if 0 <= pnl_pct < min_profit_threshold:
                self._log(f"🛑 拦截微利平仓: 浮盈 {pnl_pct*100:.3f}% < {min_profit_threshold*100:.3f}% (AI信心非HIGH)", 'warning')
                return "SKIPPED_PROFIT", f"微利拦截 {pnl_pct*100:.2f}%"

        # 4. 资金三方取小 & 最小数量适配
        ai_suggest = signal_data['amount']
        
        # [Pre-Fetch] Prepare Market Info for accurate calculation
        market_info = None
        try:
            market_info = self.exchange.market(self.symbol)
            is_contract = market_info.get('swap') or market_info.get('future') or market_info.get('option') or (market_info.get('type') in ['swap', 'future', 'option'])
            contract_size = 1.0
            if is_contract:
                 contract_size = float(market_info.get('contractSize', 1.0))
                 if contract_size <= 0: contract_size = 1.0
        except Exception as e:
            self._log(f"Market Info Fetch Failed: {e}", 'error')
            # [Critical Fix] 如果是合约模式但获取不到市场信息，必须报错，防止误判为现货导致下单事故
            if self.trade_mode != 'cash':
                 return "ERROR", f"市场信息获取失败且为非现货模式: {e}"
            is_contract = False
            contract_size = 1.0

        config_amt = self.amount
        
        # 获取余额
        if balance is None:
             balance = await self.get_account_balance()
             
        # [Double Check] 再次强制获取最新余额，防止并发争抢
        # (因为在 analyze 阶段传入的 balance 可能是几秒前的旧数据)
        try:
            latest_bal = await self.get_account_balance()
            if latest_bal < balance * 0.9: # 如果余额突然减少了 10% 以上
                self._log(f"⚠️ [Double Check] 余额骤减! (旧: {balance:.2f} -> 新: {latest_bal:.2f})，可能是其他币种已下单", 'warning')
                balance = latest_bal
        except:
            pass
        
        # [Fix] 计算基于配额的硬性资金上限 (USDT)
        # self.allocation 如果 <= 1 (如 0.5)，则是比例；如果 > 1，则是固定金额
        # self.initial_balance 是初始本金
        allocation_usdt_limit = 0
        base_capital = self.initial_balance if self.initial_balance > 0 else balance
        
        # [New] Auto Allocation Logic & Strategy Detection
        # 先调用自动检测，获取策略标签和建议配额
        detected_alloc, strategy_tag = await self._auto_detect_strategy_mode(base_capital)
        
        # 解析 alloc_ratio (确保是 float)
        alloc_ratio = 1.0
        try:
            # 如果检测返回的是 auto (未被修改)，则按活跃币种平分
            if detected_alloc == 'auto':
                 symbol_count = max(1, self.active_symbols_count)
                 alloc_ratio = 1.0 / symbol_count
            else:
                 alloc_ratio = float(detected_alloc)
        except:
            alloc_ratio = 1.0
            
        # [Smart Sizing] 动态仓位调整 (Dynamic Position Sizing)
        # 基础仓位 (alloc_ratio) * 信心因子 * 波动率惩罚
        
        # [New] 根据自动检测的策略模式，决定是否应用信心折扣
        # MICRO_SNIPER (全仓狙击) -> 永远满仓，不打折
        confidence_factor = 1.0
        if "MICRO_SNIPER" not in strategy_tag:
            conf_str = signal_data.get('confidence', 'LOW').upper()
            if conf_str == 'LOW': confidence_factor = 0.5
            elif conf_str == 'MEDIUM': confidence_factor = 0.8
            # HIGH = 1.0

        # [Optimized] 使用 RL 或启发式规则获取建议仓位比例
        # 替代原有的简单乘法逻辑
        suggested_ratio = self.position_manager.get_recommended_position_size(
            signal_data, 
            getattr(self, 'last_indicators', {}),
            sentiment_score=signal_data.get('sentiment_score', 50)
        )
        
        # [RL Override] 如果 RL 模块启用，则使用 RL 建议的比例
        # 注意: get_recommended_position_size 内部已经包含了 confidence 和 volatility 的考量
        # 但我们为了保守，可能还是会结合 confidence_factor (双重保险)
        # 或者完全信任 RL (如果 RL 模型已经训练得很好)
        # 这里采用混合模式: min(RL_Ratio, Confidence_Cap)
        
        final_ratio = suggested_ratio
        if "MICRO_SNIPER" not in strategy_tag:
             final_ratio = min(suggested_ratio, confidence_factor)
        
        # [Optimized] 日志简化: 只有当比例被大幅调整时才打印，否则静默
        if final_ratio < 0.9:
             self._log(f"🤖 [Smart Sizing] 仓位调整: {final_ratio:.2f}x (信心{confidence_factor:.1f})", 'debug')

        # 计算目标资金量 (USDT)
        allocation_usdt_limit = 0
        
        # 这里的 allocation 已经是经过 _auto_detect_strategy_mode 修正过的值
        if alloc_ratio <= 1.0:
            # 比例模式: Base * Alloc * Final_Ratio
            # 例如: 1000U * 0.33 (分仓) * 0.8 (RL) = 264U
            allocation_usdt_limit = base_capital * alloc_ratio * final_ratio
        else:
            # 固定金额模式: Fixed * Final_Ratio
            # 例如: 100U * 0.8 = 80U
            allocation_usdt_limit = alloc_ratio * final_ratio
            
        # [Fix] 最小下单金额保护 (Min Notional Guard)
        # 如果计算出的配额 < 11U (OKX通常最小10U)，且总资金充裕，强制提升配额
        # 只有当总资金 > 11U 时才提升，否则只能 All-in
        if allocation_usdt_limit < 11.0:
            if base_capital > 11.0:
                # [Optimized] 简化日志
                self._log(f"⚠️ 资金修正: {allocation_usdt_limit:.2f}U -> 11.0U (最小限额)", 'debug')
                allocation_usdt_limit = 11.0
            else:
                # 资金太少，只能梭哈
                allocation_usdt_limit = base_capital
            
        # 扣除当前持仓占用的保证金（粗略估算），防止重复占用配额
        used_quota = 0
        margin_to_release = 0
        if current_position:
             # 持仓价值 / 杠杆 = 占用保证金
             # [Fix] 必须乘上 contract_size，否则合约模式下 value 会偏大 100 倍
             used_quota = (current_position['size'] * contract_size * current_realtime_price) / self.leverage
             
             # [Fix] 如果是反向信号 (Flip)，预期会释放当前配额和保证金
             if (signal_data['signal'] == 'BUY' and current_position['side'] == 'short') or \
                (signal_data['signal'] == 'SELL' and current_position['side'] == 'long'):
                 margin_to_release = used_quota
                 used_quota = 0 # 视为释放

        remaining_quota = max(0, allocation_usdt_limit - used_quota)
        
        # 将剩余配额转换为币的数量
        quota_token_amount = (remaining_quota * self.leverage * 0.99) / current_realtime_price

        max_trade_limit = 0
        # [Fix] 余额也需要加上即将释放的保证金
        potential_balance = balance + margin_to_release
        
        # [Fix] 反手/平仓时，实际可用余额需加上未实现盈亏 (PnL) 并扣除平仓手续费
        # 如果亏损，potential_balance 会减少；如果盈利，会增加
        if margin_to_release > 0 and current_position:
             # 估算平仓手续费 (Taker)
             # [Fix] 必须乘上 contract_size
             close_fee = (current_position['size'] * contract_size * current_realtime_price) * self.taker_fee_rate
             
             # [Fix] 区分全仓 (Cross) 和逐仓 (Isolated) 的资金计算逻辑
             # 全仓: availBal 已经实时反映了 PnL (Total Equity = Avail + Used)。平仓释放的是 Used。
             # 逐仓: availBal 不受 PnL 影响。平仓释放的是 Used + PnL。
             if self.margin_mode == 'isolated':
                 pnl = current_position.get('unrealized_pnl', 0)
                 potential_balance += (pnl - close_fee)
             else:
                 # 全仓模式下，balance (availBal) 已经包含了浮亏的影响
                 # 所以不需要再加 PnL，只需要扣除手续费
                 potential_balance -= close_fee
                 
             # 确保不小于 0 (极端亏损情况)
             potential_balance = max(0, potential_balance)

        if signal_data['signal'] == 'BUY':
             if self.trade_mode == 'cash':
                 # 现货: 取 (余额, 配额) 的较小值
                 available_usdt = min(potential_balance, remaining_quota)
                 max_trade_limit = (available_usdt * 0.99) / current_realtime_price
             else:
                 # 合约: 取 (余额, 配额) 的较小值作为保证金
                 available_margin = min(potential_balance, remaining_quota)
                 max_trade_limit = (available_margin * self.leverage * 0.99) / current_realtime_price
        elif signal_data['signal'] == 'SELL':
             if self.trade_mode == 'cash':
                 max_trade_limit = await self.get_spot_balance()
             else:
                 # 开空能力: 同理，受配额限制
                 available_margin = min(potential_balance, remaining_quota)
                 max_trade_limit = (available_margin * self.leverage * 0.99) / current_realtime_price

        # 决策最终数量
        # [High Confidence Override] -> 弹性配额逻辑
        if signal_data.get('confidence', '').upper() == 'HIGH':
            # 🦁 激进模式: 允许突破单币种配额，调用账户闲置资金
            # 限制：最多使用账户余额的 90% (保留 10% 作为安全垫/其他币种救急)
            
            # [Safety Check] 防止在亏损时无限加仓 (Martingale Trap)
            # 只有当 current_position 盈利时，才允许 aggressive scaling
            can_scale_aggressively = True
            if current_position:
                 entry_price = float(current_position.get('entry_price', 0))
                 if entry_price > 0:
                     if current_position['side'] == 'long' and current_realtime_price < entry_price: can_scale_aggressively = False
                     if current_position['side'] == 'short' and current_realtime_price > entry_price: can_scale_aggressively = False
            
            if not can_scale_aggressively:
                 self._log(f"⚠️ 激进加仓被拦截: 当前持仓浮亏，禁止突破配额", 'warning')
            else:
                 self._log(f"🦁 激进模式激活: 突破单币种配额限制 (信心 HIGH)", 'debug')
                 max_trade_limit = max(max_trade_limit, (potential_balance * 0.9 * self.leverage) / current_realtime_price)
            
            # [Correct Logic] 资金计算逻辑修正 (Moved Up Logic)
            # 1. 还原当前总权益 (Total Equity)
            #    balance 是可用余额 (Avail)
            #    used_margin 是当前持仓占用
            used_margin = 0
            if current_position:
                used_margin = (current_position['size'] * contract_size * current_realtime_price) / self.leverage

            current_equity = balance + used_margin
            
            # 2. 确定资金上限 (Cap)
            effective_cap = current_equity
            if self.initial_balance > 0:
                effective_cap = min(current_equity, self.initial_balance)
            
            # 3. 计算可用资金 (Available Capital)
            #    如果反手 (Flip)，当前占用会被释放，所以不需要扣除
            is_potential_flip = False
            if current_position:
                if signal_data['signal'] == 'BUY' and current_position['side'] == 'short': is_potential_flip = True
                if signal_data['signal'] == 'SELL' and current_position['side'] == 'long': is_potential_flip = True

            margin_to_deduct = 0 if is_potential_flip else used_margin
            available_capital = max(0, effective_cap - margin_to_deduct)
            
            # 4. 如果是反手，还需要加上平仓带来的盈亏变动 (PnL) 并扣除手续费
            if is_potential_flip and current_position:
                 close_fee = (current_position['size'] * contract_size * current_realtime_price) * self.taker_fee_rate
                 if self.margin_mode == 'isolated':
                     pnl = current_position.get('unrealized_pnl', 0)
                     available_capital += (pnl - close_fee)
                 else:
                     available_capital -= close_fee
                 available_capital = max(0, available_capital)
                 self._log(f"🔄 检测到反手信号，预估释放资金: {available_capital:.2f} U", 'debug')

            # 计算物理最大可开仓数量 (Physical Max)
            buffer_rate = 0.98 
            
            max_physical_token = 0
            if self.trade_mode == 'cash':
                 max_physical_token = (available_capital * buffer_rate) / current_realtime_price
            else:
                 max_physical_token = (available_capital * self.leverage * buffer_rate) / current_realtime_price
            
            trade_amount = min(ai_suggest, max_physical_token)
            
            # [Fix] 信号量优先平仓逻辑 (Close First)
            if is_potential_flip and current_position:
                current_size = float(current_position['size'])
                if trade_amount < current_size and trade_amount > 0:
                     self._log(f"⚠️ 信号反转且建议量 ({trade_amount}) < 持仓量 ({current_size})，自动修正为全平: {current_size}", 'debug')
                     trade_amount = current_size
            
            # 检查是否真的突破了配额
            # [Logic Fix] 如果是反手 (Flip)，max_trade_limit (Opening Limit) 是很小的 (因为 quota 满了)
            # 但在这里，available_capital 已经包含了释放后的资金，所以 trade_amount 是真实的"翻身"能力
            # 我们不需要在这里再用 max_trade_limit 限制它，除非它真的超过了 Quota Cap (allocation_limit)
            
            # 计算纯粹的 Quota Limit (不扣减当前持仓，因为反手会释放)
            quota_cap_token = 0
            if is_potential_flip:
                 # 如果反手，我们比较的是 (Allocation Limit) vs (Order Amount)
                 # 之前的 remaining_quota 扣除了 used_quota，这里我们加回去
                 full_quota_usdt = remaining_quota + used_quota
                 quota_cap_token = (full_quota_usdt * self.leverage * 0.99) / current_realtime_price
            else:
                 quota_cap_token = max_trade_limit

            if trade_amount > quota_cap_token:
                 self._log(f"🦁 激进模式 (信心高): 突破配额限制，调用闲置资金。下单: {trade_amount:.4f}", 'debug')
            
            # [Fix] Update max_trade_limit to reflect the actual capability, 
            # so subsequent checks (min_amount, etc.) use the correct limit.
            # In High Confidence mode, we are allowed to use available_capital (Physical Max).
            # But for min_limit check, we should be consistent.
            max_trade_limit = max_physical_token

        else:
            # 🦊 稳健模式: 严格受配额限制
            # [Logic Fix] 如果是反手 (Flip)，max_trade_limit (Line 835/842) 是基于 remaining_quota (扣除了 used) 的
            # 这会导致反手时，明明平仓释放了额度，却被旧的 remaining_quota 限制住
            # 我们需要用"动态额度"
            
            effective_limit = max_trade_limit
            
            # 检测是否反手
            is_flip = False
            if current_position:
                if signal_data['signal'] == 'BUY' and current_position['side'] == 'short': is_flip = True
                if signal_data['signal'] == 'SELL' and current_position['side'] == 'long': is_flip = True
            
            if is_flip:
                 # 如果是反手，额度 = 当前剩余额度 + 释放额度
                 # max_trade_limit 是基于 remaining_quota 算的
                 # 释放额度对应的 Token 数 = used_quota * leverage / price
                 # 简单来说，就是把 used_quota 加回 remaining_quota 再算一遍
                 full_quota_usdt = remaining_quota + used_quota
                 
                 # 同时也要受限于 余额 (potential_balance)
                 # potential_balance 已经包含了释放的资金 (Line 804)
                 
                 if self.trade_mode == 'cash':
                      avail_usdt = min(potential_balance, full_quota_usdt)
                      effective_limit = (avail_usdt * 0.99) / current_realtime_price
                 else:
                      avail_margin = min(potential_balance, full_quota_usdt)
                      effective_limit = (avail_margin * self.leverage * 0.99) / current_realtime_price
            
            # [Fix] 如果不是反手，是纯开仓，也要确保 trade_amount 不小于 min_amount_coins (在余额允许范围内)
            # trade_amount 之前是 min(ai_suggest, config_amt, max_trade_limit)
            # 如果 ai_suggest 很小，这里就取了小的，后面会被拦截
            # 但如果 max_trade_limit 本身就比 min_amount 小 (比如渣渣钱)，那也没办法
            
            trade_amount = min(ai_suggest, config_amt, effective_limit)
            
            # [Fix] Update max_trade_limit for subsequent checks
            max_trade_limit = effective_limit
        
        is_closing = False
        if signal_data['signal'] == 'SELL':
            if self.trade_mode == 'cash':
                is_closing = True
                trade_amount = max_trade_limit # All out
            elif current_position and current_position['side'] == 'long':
                is_closing = True
        
        # [New] 如果是 BUY 平空 (Short -> Flat)
        if signal_data['signal'] == 'BUY' and current_position and current_position['side'] == 'short':
            is_closing = True

        # [New] Circuit Breaker Logic (Cool-down)
        # 如果不是平仓操作 (即 Opening 或 Pyramiding)，检查冷静期
        if not is_closing:
            time_since_sl = time.time() - self.last_stop_loss_time
            if time_since_sl < self.cool_down_seconds:
                # [Optimized] High Confidence Override
                # 如果 AI 信心为 HIGH，说明出现了极佳的形态 (如 V型反转)，允许豁免冷静期
                is_high_conf = (signal_data.get('confidence', '').upper() == 'HIGH')
                if is_high_conf:
                    self._log(f"🔥 冷静期豁免: 信心 HIGH，允许立即重返战场！", 'warning')
                else:
                    remaining = int(self.cool_down_seconds - time_since_sl)
                    self._log(f"🧊 止损冷静期: 刚触发止损不久，暂停开仓/加仓 (剩余 {remaining}s)", 'warning')
                    return "SKIPPED_COOL", f"冷静期 {remaining}s"

        # [New] 如果是加仓 (Pyramiding) 且信心为 HIGH，也跳过最小金额检查
        # 因为我们是想把剩余的一点点钱 (渣渣钱) 或者是大钱加进去
        # 但如果是加仓，trade_amount 可能是剩下的所有钱，如果这笔钱太少 (<5U)，会被 min_notional 拦截
        # 拦截加仓是合理的 (因为钱太少开不出来)，所以这里不需要 is_closing=True
        
        # [Fix] 这里的 check 移动到具体开仓逻辑中，防止阻断 "仅平仓" (Amount=0) 的操作
        # if trade_amount <= 0:
        #      return "SKIPPED_ZERO", "计算数量为0"

        # 5. 执行
        try:
            # 准备下单数量 (如果是合约，转换为张数)
            final_order_amount = trade_amount
            # [Optimization] Use pre-fetched contract info
            # is_contract and contract_size are already defined at the top of the function
            
            if is_contract:
                 # [Fix] 合约模式下，无论 contract_size 是多少，下单数量 (sz) 必须是整数 (张数)
                 if contract_size > 0:
                      # 向上取整还是向下取整？保守起见向下取整 (int)
                      # [Fix] Add epsilon to avoid float precision issues (e.g. 0.99999 -> 0)
                      final_order_amount = int(trade_amount / contract_size + 1e-9)
                      
                      # 如果计算出0张，但trade_amount>0，强制至少1张（将在后面最小数量检查中修正，这里先防0）
                      if final_order_amount == 0 and trade_amount > 0:
                          final_order_amount = 1
                      # self._log(f"💱 转换下单数量: {trade_amount} Coins -> {final_order_amount} Contracts")
            else:
                 # Spot (Cash or Margin)
                 pass

            if signal_data['signal'] == 'BUY':
                if current_position and current_position['side'] == 'short':
                    # 平空 (使用持仓自带的 size，通常已经是张数)
                    close_params = {}
                    if self.trade_mode != 'cash':
                        close_params['reduceOnly'] = True
                        close_params['tdMode'] = self.trade_mode
                    
                    await self.exchange.create_market_order(self.symbol, 'buy', current_position['size'], params=close_params)
                    self._log("🔄 平空仓成功", 'debug')
                    # [New] Reset Dynamic Risk Params on New Entry (Short)
                    # Wait, this is Close Short logic (BUY).
                    # If we close short, we reset risk params to 0.
                    self.dynamic_stop_loss = 0.0
                    self.dynamic_take_profit = 0.0

                    # [New] Record Stop Loss Event
                    # 如果这确实是一个止损操作 (PnL < 0)，更新冷却时间
                    # 注意: current_position 是平仓前的快照
                    if current_position:
                         # 计算已实现盈亏 (Realized PnL)
                         # 简单的估算: (Close - Entry) * Size
                         # 但我们这里没有成交均价，只能用 current_realtime_price 估算
                         entry_p = current_position.get('entry_price', 0)
                         is_loss = False
                         if entry_p > 0:
                             if current_position['side'] == 'long':
                                 if current_realtime_price < entry_p: is_loss = True
                             else: # short
                                 if current_realtime_price > entry_p: is_loss = True
                         
                         # 或者检查 reason 是否包含 "止损" / "Loss"
                         reason_str = signal_data.get('reason', '')
                         if "止损" in reason_str or "Loss" in reason_str or "STOP" in reason_str.upper() or is_loss:
                             self.last_stop_loss_time = time.time()
                             self._log(f"🛑 止损已触发，启动 60s 冷静期...", 'warning')

                    await self.send_notification(f"🔄 平空仓成功 {self.symbol}\n数量: {current_position['size']}\n理由: {signal_data['reason']}")
                    await asyncio.sleep(1)
                
                # 开多/买入
                if trade_amount <= 0:
                     if current_position and current_position['side'] == 'short':
                         return "EXECUTED", "仅平空"
                     return "SKIPPED_ZERO", "计算数量为0"

                # [New] 反手保护 (Flip Protection) - BUY (Short -> Long)
                # 策略调整: 如果是网格模式 (LOW Volatility)，允许低信心反手 (为了维持网格运转)
                is_grid_mode = (volatility_status == 'LOW')
                if is_closing and original_conf_val < min_conf_val and not is_grid_mode:
                     self._log(f"🛡️ 反手保护: 原始信心不足 ({signal_data.get('confidence')})，仅执行平空，禁止反手开多", 'warning')
                     return "EXECUTED", "仅平空(信心不足)"

                # [Safety] 同向开仓保护 (防止重复下单)
                # 策略调整：允许 HIGH 信心加仓，以及 Grid Mode (LOW Volatility) 下的补仓
                if not is_closing and current_position and current_position['side'] == 'long':
                     is_grid_mode = (volatility_status == 'LOW')
                     is_high_conf = (signal_data.get('confidence', '').upper() == 'HIGH')
                     
                     # [Optimized] 移除 is_grid_mode 的自动加仓权限，防止在震荡市中无限补仓导致亏损扩大
                     # 网格策略应该由专门的 GridBot 处理，这里作为趋势机器人，加仓必须基于 HIGH 信心
                     if is_high_conf:
                         # [Fix] 检查加仓数量是否为 0 (可能是没钱了)
                         if final_order_amount <= 0:
                             self._log(f"⚠️ 加仓失败: 余额不足或计算数量为0", 'warning')
                             return "SKIPPED_ZERO", "加仓无余额"
                         
                         mode_msg = "信心 HIGH"
                         self._log(f"🔥 加仓模式: 已持有 Long，({mode_msg})，允许加仓", 'info')
                         # 加仓逻辑... (继续往下走，不再 return)
                     else:
                         self._log(f"⚠️ 已持有 Long 仓位 ({current_position['size']})，跳过重复开仓 (信心非HIGH)", 'warning')
                         return "HOLD_DUP", "已持仓(防重)"

                # [Logic Fix] 无论是否是反手，都需要检查最小/最大数量限制
                # 放在这里是因为我们要先确认是否跳过了防重逻辑
                # 但如果是平仓 (Closing)，我们不应该受最小下单数量限制 (例如我只剩 0.001 ETH，必须能卖掉)
                # OKX 通常允许平仓单小于 min_limit
                if trade_amount > 0:
                     # 开仓检查最小数量
                    try:
                        # [Optimization] market info 已经在上面获取过了 (Lines 724+)
                        # market = self.exchange.market(self.symbol)
                        # contract_size = float(market.get('contractSize', 1.0))
                        # if not is_contract or contract_size <= 0:
                        #    contract_size = 1.0
                        
                        # [Fix] 确保 market 对象可用
                        market = market_info if market_info else self.exchange.market(self.symbol)

                        # 获取原始限制 (可能是张数，也可能是币数)
                        raw_min_amount = market.get('limits', {}).get('amount', {}).get('min')
                        raw_max_market = market.get('limits', {}).get('market', {}).get('max')
                        raw_max_amount = market.get('limits', {}).get('amount', {}).get('max')
                        
                        # 统一转换为 Coins 单位进行比较
                        min_amount_coins = raw_min_amount * contract_size if raw_min_amount else None
                        max_amount_coins = (raw_max_market if raw_max_market else raw_max_amount) * contract_size if (raw_max_market or raw_max_amount) else None
                        
                        min_cost = None
                        cost_min = market.get('limits', {}).get('cost', {}).get('min')
                        if cost_min is not None:
                            min_cost = float(cost_min)
                        
                        # [Modified] 如果是平仓操作 (is_closing=True)，跳过最小数量检查，防止尾仓无法平掉
                        # [Fix] 但是如果是合约反手 (trade_mode != cash)，即使是 is_closing 也需要检查，因为我们实际上是在开新仓
                        should_check_min = not is_closing or self.trade_mode != 'cash'
                        
                        # [New] 如果是反手开多 (Flip to Long)，且之前有 Short 仓位 (说明刚平掉)，
                        # 这种情况下，我们应该允许即使余额看起来紧张也尝试下单 (因为平仓会释放保证金)
                        # 但这里很难判断之前是否持有 Short，因为 current_position 是传入时的快照。
                        # 如果 current_position['side'] == 'short'，说明刚才执行了平空。
                        is_flipping = current_position and current_position['side'] == 'short'
                         
                        if should_check_min:
                            if min_amount_coins and trade_amount < min_amount_coins:
                                if max_trade_limit >= min_amount_coins:
                                    # [Double Check] 再次确认余额是否足以支付最小数量的保证金 (考虑手续费缓冲)
                                    # max_trade_limit 虽然是基于余额算的，但可能比较极限
                                    required_margin = (min_amount_coins * current_realtime_price) / self.leverage
                                    # 获取最新余额 (尽量用传入的 balance，或者再查一次？用传入的 balance 即可，减少请求)
                                    # 这里用 potential_balance (包含即将释放的)
                                    if potential_balance > required_margin * 1.02: # 2% buffer
                                        self._log(f"⚠️ 数量 {trade_amount} < 最小限制 {min_amount_coins:.6f}，自动提升 (需保证金 {required_margin:.2f} U)")
                                        trade_amount = min_amount_coins
                                        # 重新计算 final_order_amount
                                        if is_contract:
                                            # [Fix] Use int() with slight epsilon or round() to avoid float precision issues when boosting to min amount
                                            # e.g. 0.99999999 -> 1
                                            final_order_amount = int(trade_amount / contract_size + 1e-9)
                                        else:
                                            final_order_amount = trade_amount
                                    else:
                                        if is_flipping:
                                            self._log(f"🔄 [反手保护] 余额计算可能滞后，强制尝试反手开多...", 'info')
                                            # 强制提升到最小数量
                                            trade_amount = min_amount_coins
                                            if is_contract:
                                                final_order_amount = int(trade_amount / contract_size + 1e-9)
                                            else:
                                                final_order_amount = trade_amount
                                        else:
                                            self._log(f"🚫 余额不足以支付最小数量保证金: 需 {required_margin:.2f} U, 有 {potential_balance:.2f} U", 'warning')
                                            return "SKIPPED_MIN", f"余额不足最小限额"
                                else:
                                    if is_flipping:
                                        self._log(f"🔄 [反手保护] 余额计算可能滞后，强制尝试反手开多...", 'info')
                                        # 强制提升到最小数量
                                        trade_amount = min_amount_coins
                                        if is_contract:
                                            final_order_amount = int(trade_amount / contract_size + 1e-9)
                                        else:
                                            final_order_amount = trade_amount
                                    else:
                                        # [New] 如果是加仓场景 (Pyramiding) 导致的余额不足，则不算错误，而是满仓保护
                                        is_pyramiding = current_position and (
                                            (signal_data['signal'] == 'BUY' and current_position['side'] == 'long') or
                                            (signal_data['signal'] == 'SELL' and current_position['side'] == 'short')
                                        )
                                        
                                        if is_pyramiding:
                                            self._log(f"🔒 [满仓保护] 资金已打满，无法加仓，继续持有当前仓位让利润奔跑", 'info')
                                            return "SKIPPED_FULL", "满仓持有中"
                                        else:
                                            self._log(f"🚫 余额不足最小单位 {min_amount_coins:.6f}", 'warning')
                                            await self._send_diagnostic_report(trade_amount, min_amount_coins, max_trade_limit, ai_suggest, config_amt, signal_data, current_realtime_price, "余额不足以购买最小单位")
                                            return "SKIPPED_MIN", f"少于最小限额 {min_amount_coins}"

                            if min_cost and (trade_amount * current_realtime_price) < min_cost:
                                # 尝试提升
                                req_amount = (min_cost / current_realtime_price) * 1.05
                                if max_trade_limit >= req_amount:
                                    self._log(f"⚠️ 金额不足最小限制 {min_cost}U，自动提升数量至 {req_amount}")
                                    trade_amount = req_amount
                                    # 重新计算 final_order_amount
                                    if is_contract:
                                        # [Fix] Use int() with slight epsilon to avoid float precision issues
                                        final_order_amount = int(trade_amount / contract_size + 1e-9)
                                    else:
                                        final_order_amount = trade_amount
                                else:
                                    # [New] 反手保护 (Flip Protection)
                                    # 如果是反手操作，即使计算出的 max_trade_limit 看起来不足（因为旧仓位还没释放），
                                    # 我们也应该强制尝试下单，让交易所去撮合。
                                    # 否则在"平空开多"时，因为平仓钱还没到账，开空会被这里拦截。
                                    if is_flipping:
                                        self._log(f"🔄 [反手保护] 金额计算可能滞后，强制尝试反手开多...", 'info')
                                        trade_amount = req_amount
                                        if is_contract:
                                            # [Fix] Use int() with slight epsilon to avoid float precision issues
                                            final_order_amount = int(trade_amount / contract_size + 1e-9)
                                        else:
                                            final_order_amount = trade_amount
                                    else:
                                        # [New] 同上，如果是加仓场景，不算错误
                                        is_pyramiding = current_position and (
                                            (signal_data['signal'] == 'BUY' and current_position['side'] == 'long') or
                                            (signal_data['signal'] == 'SELL' and current_position['side'] == 'short')
                                        )
                                        
                                        if is_pyramiding:
                                            self._log(f"🔒 [满仓保护] 资金已打满，无法加仓，继续持有当前仓位让利润奔跑", 'info')
                                            return "SKIPPED_FULL", "满仓持有中"
                                        else:
                                            self._log(f"🚫 余额不足最小金额 {min_cost}U", 'warning')
                                            await self._send_diagnostic_report(trade_amount, min_cost, max_trade_limit, ai_suggest, config_amt, signal_data, current_realtime_price, f"余额不足最小金额 (需 {min_cost}U)")
                                            return "SKIPPED_MIN", f"金额 < {min_cost}U"

                        if max_amount_coins and trade_amount > max_amount_coins:
                            self._log(f"⚠️ 数量 {trade_amount} > 市场最大限制 {max_amount_coins}，自动截断")
                            trade_amount = max_amount_coins
                            # 重新计算 final_order_amount
                            if is_contract:
                                final_order_amount = int(trade_amount / contract_size + 1e-9)
                            else:
                                final_order_amount = trade_amount

                    except Exception as e:
                        self._log(f"下单限制检查异常: {e}", 'warning')

                # [Fix] 确保现货买入数量符合精度要求 (虽然 final_order_amount = trade_amount, 但 trade_amount 可能是计算值)
                if not is_contract:
                    precise_buy_str = self.exchange.amount_to_precision(self.symbol, final_order_amount)
                    final_order_amount = float(precise_buy_str)

                # [Fix] OKX Spot Buy requires tgtCcy='base_ccy' if we are passing Base Currency Amount as 'sz'
                # Otherwise 'sz' is treated as Quote Currency (USDT) amount
                buy_params = {'tdMode': self.trade_mode}
                if not is_contract:
                    buy_params['tgtCcy'] = 'base_ccy'
                
                # [Smart Execution] 智能挂单策略 (BUY)
                order_type = 'market'
                limit_price = None
                
                # [Fix] 现货模式下，DOGE/USDT 最小下单单位可能是 0.1 或 1 个币。
                # 如果 final_order_amount (例如 0.76) 小于 最小精度 (例如 1.0)，会导致 Code 51008。
                # 我们需要检查并向上取整到最小精度，或者如果余额不足就报错。
                # 但更安全的做法是向下取整，防止余额不足。
                # 这里我们假设 amount_to_precision 已经处理了精度。
                # 如果报错 51008 (Insufficient Balance/Margin)，可能是因为 Maker 挂单锁定了资金但没成交，或者市价单滑点不够。
                # 为了提高成交率，震荡市优先 Market 单，除非是大额单。
                
                # if volatility_status == 'HIGH_CHOPPY' and signal_data.get('confidence', '').upper() != 'HIGH':
                #    try:
                #        order_type = 'limit'
                #        limit_price = float(ticker['bid']) # 挂买一价
                #        self._log(f"🤖 [Smart Exec] 震荡市尝试 Maker 挂单: {limit_price}", 'info')
                #    except:
                #        order_type = 'market'
                #        limit_price = None

                # [Fix] 暂时禁用 Maker 挂单，全走 Market 以确保成交。
                # 之前报错 51008 可能是因为 Maker 单价格变动导致验资失败。
                
                try:
                    # [Enhance] 增加下单重试机制 (针对网络超时等非业务错误)
                    # 业务错误 (如余额不足) 由内部逻辑处理
                    await self.order_executor.create_order_with_retry(
                        'buy', 
                        final_order_amount, 
                        order_type, 
                        limit_price, 
                        params=buy_params
                    )
                    # self._log(f"🚀 买入成功: {final_order_amount} (模式: {self.trade_mode})")
                except Exception as e:
                    if "51008" in str(e) or "Insufficient" in str(e): # Insufficient balance/margin
                         # [Retry] 如果是精度导致的余额不足 (比如算出来 0.76 但最小 1)，或者滑点导致
                         # 尝试减少 5% 数量重试
                         retry_amount = final_order_amount * 0.95
                         if not is_contract:
                             retry_amount = float(self.exchange.amount_to_precision(self.symbol, retry_amount))
                         else:
                             retry_amount = int(retry_amount)
                             
                         self._log(f"⚠️ 余额不足 (51008)，尝试减少数量重试: {final_order_amount} -> {retry_amount}", 'warning')
                         
                         # [Fix] 如果重试数量为0，说明资金太少连最小交易单位都不够，直接放弃
                         if retry_amount <= 0:
                             self._log(f"❌ 重试数量为0，放弃交易 (资金过小)", 'error')
                             return "FAILED", "资金不足(Min)"

                         if retry_amount > 0:
                             try:
                                 await self.order_executor.create_order_with_retry(
                                    'buy', 
                                    retry_amount, 
                                    'market', # Retry with Market
                                    params=buy_params
                                 )
                                 final_order_amount = retry_amount # Update for log
                                 self._log(f"🚀 重试买入成功: {final_order_amount}")
                             except Exception as retry_e:
                                 self._log(f"❌ 重试也失败: {retry_e}", 'error')
                                 return "FAILED", f"重试失败: {retry_e}"
                         else:
                             raise e
                    else:
                        raise e
                
                # [New] Reset Dynamic Risk Params on New Entry
                new_sl = float(signal_data.get('stop_loss', 0) or 0)
                # new_tp = float(signal_data.get('take_profit', 0) or 0) # [Removed] TP
                
                # [Fix] Apply new dynamic risk params correctly
                self.dynamic_stop_loss = new_sl
                self.dynamic_take_profit = 0.0 # [Removed] Disable fixed TP
                self.dynamic_sl_side = 'long'
                # [Fix] Persist new risk params
                asyncio.create_task(self.save_state())
                
                msg = f"🚀 **买入执行 (BUY)**\n"
                msg += f"• 交易对: {self.symbol}\n"
                msg += f"• 数量: {trade_amount} 币 ({final_order_amount} 张)\n"
                msg += f"• 价格: ${current_realtime_price:,.2f}\n"
                msg += f"• 理由: {signal_data['reason'][:50]}..." # Truncate reason
                
                self._log(f"🚀 买入成功: {trade_amount} @ {current_realtime_price:.4f} | 理由: {signal_data['reason'][:30]}...", 'debug')
                
                # [Fix] 飞书推送 Title 增强
                await self.send_notification(msg, title=f"🚀 买入执行 | {self.symbol}")
                return "EXECUTED", f"买入 {trade_amount}"

            elif signal_data['signal'] == 'SELL':
                # [Fix] 仅在非现货模式下执行"平多"逻辑
                # 现货模式下，"平多"等同于"现货卖出"，由下方的 Spot Sell block 统一处理
                # 否则会导致双重下单 (Double Sell): 先执行 Close Long，再执行 Spot Sell
                if current_position and current_position['side'] == 'long' and is_contract:
                    # 平多
                    close_params = {}
                    if self.trade_mode != 'cash':
                        close_params['reduceOnly'] = True
                        close_params['tdMode'] = self.trade_mode
                    
                    await self.exchange.create_market_order(self.symbol, 'sell', current_position['size'], params=close_params)
                    self._log("🔄 平多仓成功")
                    
                    msg = f"🔄 **平多仓 (Close Long)**\n"
                    msg += f"• 交易对: {self.symbol}\n"
                    msg += f"• 数量: {current_position['size']}\n"
                    msg += f"• 盈亏: {pnl_pct*100:+.2f}% (估算)\n"
                    msg += f"• 理由: {signal_data['reason']}"
                    # [Fix] 飞书推送 Title 增强
                    await self.send_notification(msg, title=f"🔄 平多仓 | {self.symbol}")
                    await asyncio.sleep(1)
                    
                    # [Fix] 平仓后更新余额，以便后续可能的反手开空使用最新余额
                    balance = await self.get_account_balance()
                    # 更新 potential_balance 用于后续计算 (虽然 Flip 逻辑是分开的，但保持数据新鲜是个好习惯)
                    
                    # [Fix] 明确返回，不继续执行开空 (Flip 需等待下一个 Tick)
                    # 这是一个设计选择：为了安全，不在此刻立即反手，而是等待下一轮 AI 确认
                    
                    # [Fix] Reset dynamic risk params on Close Long
                    self.dynamic_stop_loss = 0.0
                    self.dynamic_take_profit = 0.0
                    self.dynamic_sl_side = None
                    asyncio.create_task(self.save_state())
                    
                    return "EXECUTED", "平多(等待反手)"
                
                if not is_contract:
                    # 现货卖出
                    if trade_amount <= 0: # 现货卖出如果没有数量，就无法执行
                         # 但如果前面已经通过 max_trade_limit 设置了全仓卖出，trade_amount 应该 > 0
                         # 除非余额为 0
                         return "SKIPPED_ZERO", "可卖数量为0"

                    # [New] 平仓时跳过最小金额检查 (在上面已经有 check，这里只是为了代码对齐)
                    # 现货的 is_closing=True 已经处理了

                    # [Fix] 确保现货卖出数量符合精度要求
                    # trade_amount 可能是 raw balance，需要格式化
                    precise_amount_str = self.exchange.amount_to_precision(self.symbol, trade_amount)
                    final_sell_amount = float(precise_amount_str)
                    
                    # [Fix] Sync final_order_amount for logging in catch block
                    final_order_amount = final_sell_amount

                    # [Fix] Explicitly set tgtCcy='base_ccy' for Spot Sell as well, for consistency
                    sell_params = {'tdMode': self.trade_mode}
                    if not is_contract:
                         sell_params['tgtCcy'] = 'base_ccy'
                    
                    # [Smart Execution] 智能挂单策略 (SELL - Spot)
                    order_type = 'market'
                    limit_price = None
                    
                    # [Fix] 同样禁用 Maker 挂单，防止 Insufficient Balance
                    # if volatility_status == 'HIGH_CHOPPY' and signal_data.get('confidence', '').upper() != 'HIGH':
                    #    try:
                    #        order_type = 'limit'
                    #        limit_price = float(ticker['ask']) # 挂卖一价
                    #        self._log(f"🤖 [Smart Exec] 震荡市尝试 Maker 挂单: {limit_price}", 'info')
                    #    except:
                    #        order_type = 'market'
                    #        limit_price = None

                    try:
                        # [Enhance] Add Retry for Sell Orders
                        await self.order_executor.create_order_with_retry(
                            'sell', 
                            final_sell_amount, 
                            order_type, 
                            limit_price, 
                            params=sell_params
                        )
                        self._log(f"📉 卖出成功: {final_sell_amount} (模式: {self.trade_mode})")
                    except Exception as e:
                        if "51008" in str(e): # Insufficient balance/margin
                             # [Retry] 现货卖出余额不足，通常是因为余额有极小变动或精度问题
                             # 尝试重新获取余额并向下取整更狠一点
                             # 或者直接减少 1%
                             retry_amount = final_sell_amount * 0.99
                             retry_amount = float(self.exchange.amount_to_precision(self.symbol, retry_amount))
                             
                             self._log(f"⚠️ 余额不足 (51008)，尝试减少卖出数量重试: {final_sell_amount} -> {retry_amount}", 'warning')
                             if retry_amount > 0:
                                 # [Critical Fix] 这里也使用 create_order_with_retry，但要避免它抛出冗长异常
                                 try:
                                     await self.order_executor.create_order_with_retry(
                                        'sell', 
                                        retry_amount, 
                                        'market', 
                                        params=sell_params
                                     )
                                     final_sell_amount = retry_amount
                                     self._log(f"📉 重试卖出成功: {final_sell_amount}")
                                 except Exception as e2:
                                     # [User Request] 再次简化
                                     self._log(f"❌ 卖出重试也失败 (Code 51008)", 'error')
                                     raise Exception("卖出失败: 余额不足") from None
                             else:
                                 raise e
                        else:
                            raise e
                    
                    post_balance = await self.get_account_balance()
                    est_revenue = final_sell_amount * current_realtime_price
                    
                    msg = f"**数量**: `{final_sell_amount}`\n"
                    msg += f"**价格**: `${current_realtime_price:,.2f}`\n"
                    msg += f"**金额**: `{est_revenue:.2f} U`\n"
                    msg += f"**余额**: `{post_balance:.2f} U` (Avail)\n"
                    msg += f"> **理由**: {signal_data['reason']}"
                    
                    await self.send_notification(msg, title=f"📉 现货卖出 | {self.symbol}")
                    return "EXECUTED", f"卖出 {trade_amount}"
                else:
                    # 开空
                    if trade_amount <= 0:
                         if current_position and current_position['side'] == 'long':
                             return {'status': 'EXECUTED', 'summary': "仅平多", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}
                         return {'status': 'SKIPPED_ZERO', 'summary': "计算数量为0", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}

                    # [New] 反手保护 (Flip Protection) - SELL (Long -> Short)
                    # 策略调整: 如果是网格模式 (LOW Volatility)，允许低信心反手
                    is_grid_mode = (volatility_status == 'LOW')
                    if is_closing and original_conf_val < min_conf_val and not is_grid_mode:
                         self._log(f"🛡️ 反手保护: 原始信心不足 ({signal_data.get('confidence')})，仅执行平多，禁止反手开空", 'warning')
                         return {'status': 'EXECUTED', 'summary': "仅平多(信心不足)", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}

                    # [Safety] 同向开仓保护 (防止重复下单)
                    # 策略调整：允许 HIGH 信心加仓，以及 Grid Mode (LOW Volatility) 下的补仓
                    if not is_closing and current_position and current_position['side'] == 'short':
                         is_grid_mode = (volatility_status == 'LOW')
                         is_high_conf = (signal_data.get('confidence', '').upper() == 'HIGH')
                         
                         # [Optimized] 移除 is_grid_mode 的自动加仓权限，防止在震荡市中无限补仓导致亏损扩大
                         if is_high_conf:
                             # [Fix] 检查加仓数量是否为 0
                             if final_order_amount <= 0:
                                 self._log(f"⚠️ 加仓失败: 余额不足或计算数量为0", 'warning')
                                 return {'status': 'SKIPPED_ZERO', 'summary': "加仓无余额", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}
                             
                             mode_msg = "信心 HIGH"
                             self._log(f"🔥 加仓模式: 已持有 Short，({mode_msg})，允许加仓", 'info')
                         else:
                             self._log(f"⚠️ 已持有 Short 仓位 ({current_position['size']})，跳过重复开仓 (信心非HIGH)", 'warning')
                             return {'status': 'HOLD_DUP', 'summary': "已持仓(防重)", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}

                    # [Logic Fix] 反手开仓 (Flip) 逻辑增强
                    # 如果当前有 Short 仓位，且正在 SELL 逻辑里，说明是加仓或反手？
                    # 等等，如果 signal 是 SELL，且当前持有 Short，那就是加仓。
                    # 如果 signal 是 SELL，且当前持有 Long，那已经在上面平多 (Close Long) 了。
                    # 所以走到这里 (trade_amount > 0)，要么是：
                    # 1. 空仓 -> 开空
                    # 2. 持有 Long -> 平多后 -> 反手开空
                    # 3. 持有 Short -> 加仓空
                    
                    # 关键修复：如果是反手 (之前持有 Long，现在这里 trade_amount > 0 要开空)，
                    # 此时保证金可能还没释放回来（如果没 await sleep），或者被视为开新仓检查。
                    # 我们需要确保 should_check_min 逻辑正确。
                    
                    if trade_amount > 0:
                        # [Fix] Initialize final_order_amount before try-block to prevent UnboundLocalError
                        final_order_amount = trade_amount
                        if is_contract and contract_size > 0:
                             final_order_amount = int(trade_amount / contract_size + 1e-9)
                             if final_order_amount == 0 and trade_amount > 0:
                                  final_order_amount = 1

                         # 开仓检查最小数量
                        try:
                            # [Optimization] market info 已经在上面获取过了
                            # [Fix] 确保 market 对象可用
                            market = market_info if market_info else self.exchange.market(self.symbol)

                            # 获取原始限制 (可能是张数，也可能是币数)
                            raw_min_amount = market.get('limits', {}).get('amount', {}).get('min')
                            raw_max_market = market.get('limits', {}).get('market', {}).get('max')
                            raw_max_amount = market.get('limits', {}).get('amount', {}).get('max')
                            
                            # 统一转换为 Coins 单位进行比较
                            min_amount_coins = raw_min_amount * contract_size if raw_min_amount else None
                            max_amount_coins = (raw_max_market if raw_max_market else raw_max_amount) * contract_size if (raw_max_market or raw_max_amount) else None
                            
                            min_cost = None
                            cost_min = market.get('limits', {}).get('cost', {}).get('min')
                            if cost_min is not None:
                                min_cost = float(cost_min)
                            
                            # [Modified] 如果是平仓操作 (is_closing=True)，跳过最小数量检查，防止尾仓无法平掉
                            # [Fix] 但是如果是合约反手 (trade_mode != cash)，即使是 is_closing 也需要检查，因为我们实际上是在开新仓
                            should_check_min = not is_closing or self.trade_mode != 'cash'
                            
                            # [New] 如果是反手开空 (Flip to Short)，且之前有 Long 仓位 (说明刚平掉)，
                            # 这种情况下，我们应该允许即使余额看起来紧张也尝试下单 (因为平仓会释放保证金)
                            # 但这里很难判断之前是否持有 Long，因为 current_position 是传入时的快照。
                            # 如果 current_position['side'] == 'long'，说明刚才执行了平多。
                            is_flipping = current_position and current_position['side'] == 'long'
                            
                            if should_check_min:
                                if min_amount_coins and trade_amount < min_amount_coins:
                                    if max_trade_limit >= min_amount_coins:
                                        # [Double Check] 再次确认余额是否足以支付最小数量的保证金 (考虑手续费缓冲)
                                        # max_trade_limit 虽然是基于余额算的，但可能比较极限
                                        required_margin = (min_amount_coins * current_realtime_price) / self.leverage
                                        
                                        if potential_balance > required_margin * 1.02: # 2% buffer
                                            # self._log(f"⚠️ 数量 {trade_amount} < 最小限制 {min_amount_coins:.6f} (Coins)，自动提升 (需保证金 {required_margin:.2f} U)") # [Silence]
                                            trade_amount = min_amount_coins
                                            # 重新计算 final_order_amount
                                            if is_contract:
                                                final_order_amount = int(trade_amount / contract_size + 1e-9)
                                            else:
                                                final_order_amount = trade_amount
                                        else:
                                            if is_flipping:
                                                # self._log(f"🔄 [反手保护] 余额计算可能滞后，强制尝试反手开空...", 'info') # [Silence]
                                                trade_amount = min_amount_coins
                                                if is_contract:
                                                    final_order_amount = int(trade_amount / contract_size + 1e-9)
                                                else:
                                                    final_order_amount = trade_amount
                                            else:
                                                # self._log(f"🚫 余额不足以支付最小数量保证金: 需 {required_margin:.2f} U, 有 {potential_balance:.2f} U", 'warning') # [Silence]
                                                return {'status': 'SKIPPED_MIN', 'summary': "余额不足最小限额", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}
                                    else:
                                        # [New] 如果是反手 (Flipping) 导致的余额计算不足，可能是因为平仓资金还没到账，
                                        # 或者计算 max_trade_limit 时用的是旧余额。
                                        # 我们尝试强制执行 (让交易所去判断)，而不是在这里拦截。
                                        if is_flipping:
                                            self._log(f"🔄 [反手保护] 余额计算可能滞后，强制尝试反手开空...", 'info')
                                            # 强制提升到最小数量
                                            trade_amount = min_amount_coins
                                            if is_contract:
                                                final_order_amount = int(trade_amount / contract_size + 1e-9)
                                            else:
                                                final_order_amount = trade_amount
                                        else:
                                            # [New] 如果是加仓场景 (Pyramiding) 导致的余额不足，则不算错误，而是满仓保护
                                            is_pyramiding = current_position and (
                                                (signal_data['signal'] == 'BUY' and current_position['side'] == 'long') or
                                                (signal_data['signal'] == 'SELL' and current_position['side'] == 'short')
                                            )
                                            
                                            if is_pyramiding:
                                                # self._log(f"🔒 [满仓保护] 资金已打满，无法加仓", 'info') # [Simplified] [Silence]
                                                return {'status': 'SKIPPED_FULL', 'summary': "满仓持有中", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}
                                            else:
                                                # self._log(f"🚫 余额不足最小单位", 'warning') # [Simplified] [Silence]
                                                return {'status': 'SKIPPED_MIN', 'summary': f"少于最小限额 {min_amount_coins}", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}

                            if min_cost and (trade_amount * current_realtime_price) < min_cost:
                                # 尝试提升
                                req_amount = (min_cost / current_realtime_price) * 1.05
                                if max_trade_limit >= req_amount:
                                    # self._log(f"⚠️ 金额不足最小限制 {min_cost}U，自动提升", 'info') # [Simplified] [Silence]
                                    trade_amount = req_amount
                                    # 重新计算 final_order_amount
                                    if is_contract:
                                        final_order_amount = int(trade_amount / contract_size + 1e-9)
                                    else:
                                        final_order_amount = trade_amount
                                else:
                                    # [New] 反手保护 (Flip Protection)
                                    # 如果是反手操作，即使计算出的 max_trade_limit 看起来不足（因为旧仓位还没释放），
                                    # 我们也应该强制尝试下单，让交易所去撮合。
                                    # 否则在"平空开多"时，因为平仓钱还没到账，开空会被这里拦截。
                                    if is_flipping:
                                        # self._log(f"🔄 [反手保护] 强制尝试反手...", 'info') # [Simplified] [Silence]
                                        trade_amount = req_amount
                                        if is_contract:
                                            final_order_amount = int(trade_amount / contract_size + 1e-9)
                                        else:
                                            final_order_amount = trade_amount
                                    else:
                                        # [New] 同上，如果是加仓场景，不算错误
                                        is_pyramiding = current_position and (
                                            (signal_data['signal'] == 'BUY' and current_position['side'] == 'long') or
                                            (signal_data['signal'] == 'SELL' and current_position['side'] == 'short')
                                        )
                                        
                                        if is_pyramiding:
                                            # self._log(f"🔒 [满仓保护] 资金已打满，无法加仓", 'info') # [Simplified] [Silence]
                                            return {'status': 'SKIPPED_FULL', 'summary': "满仓持有中", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}
                                        else:
                                            # self._log(f"🚫 余额不足最小金额 {min_cost}U", 'warning') # [Simplified] [Silence]
                                            return {'status': 'SKIPPED_MIN', 'summary': f"金额 < {min_cost}U", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}


                            if max_amount_coins and trade_amount > max_amount_coins:
                                self._log(f"⚠️ 数量 {trade_amount} > 市场最大限制 {max_amount_coins}，自动截断")
                                trade_amount = max_amount_coins
                                # 重新计算 final_order_amount
                                if is_contract:
                                    final_order_amount = int(trade_amount / contract_size + 1e-9)
                                else:
                                    final_order_amount = trade_amount

                        except Exception as e:
                            self._log(f"下单限制检查异常: {e}", 'warning')

                    # [Smart Execution] 智能挂单策略 (SELL - Contract Short)
                    order_type = 'market'
                    limit_price = None
                    
                    if volatility_status == 'HIGH_CHOPPY' and signal_data.get('confidence', '').upper() != 'HIGH':
                         try:
                             order_type = 'limit'
                             limit_price = float(ticker['ask']) # 挂卖一价 (做空是卖出)
                             self._log(f"🤖 [Smart Exec] 震荡市尝试 Maker 挂单: {limit_price}", 'info')
                         except:
                             order_type = 'market'
                             limit_price = None

                    # [Enhance] Add Retry for Short Orders
                    try:
                        result = await self.order_executor.create_order_with_retry(
                            'sell', 
                            final_order_amount, 
                            order_type, 
                            limit_price, 
                            params={'tdMode': self.trade_mode}
                        )
                        
                        # [Optimization] 下单成功后，直接打印日志并发送通知，然后返回结果
                        # 避免后面的代码重复执行或被 return 截断
                        
                        # [New] Reset Dynamic Risk Params on New Entry (Short)
                        new_sl = float(signal_data.get('stop_loss', 0) or 0)
                        
                        self.dynamic_stop_loss = new_sl
                        self.dynamic_take_profit = 0.0 # [Removed] Disable fixed TP
                        self.dynamic_sl_side = 'short'
                        # [Fix] Persist new risk params
                        asyncio.create_task(self.save_state())
                        
                        msg = f"📉 **开空执行 (SELL)**\n"
                        msg += f"• 交易对: {self.symbol}\n"
                        msg += f"• 数量: {trade_amount} Coins ({final_order_amount} sz)\n"
                        msg += f"• 价格: ${current_realtime_price:,.2f}\n"
                        msg += f"• 理由: {signal_data['reason'][:50]}..." 
                        
                        self._log(f"📉 开空成功: {trade_amount} @ {current_realtime_price:.4f} | 理由: {signal_data['reason'][:30]}...", 'debug')
                        
                        await self.send_notification(msg, title=f"📉 开空执行 | {self.symbol}")

                        return {
                            'status': 'EXECUTED',
                            'reason': signal_data.get('reason', ''),
                            'signal': signal_data.get('signal'),
                            'confidence': signal_data.get('confidence'),
                            'price': current_realtime_price,
                            'summary': signal_data.get('reason', '')[:60], # [Fix] Use reason as summary if summary is empty
                            'executed_qty': final_order_amount,
                            'order_id': result.get('id')
                        }
                    except Exception as e:
                         # [User Request] 下单失败时只返回简洁结果，不打印长JSON
                         return {
                             'status': 'FAILED',
                             'reason': str(e),
                             'signal': signal_data.get('signal'),
                             'confidence': signal_data.get('confidence'),
                             'price': current_realtime_price,
                             'summary': f"下单失败: {e}"
                         }

        except Exception as e:
            msg = str(e)
            if "51008" in msg or "Insufficient" in msg:
                # [User Request] 简化错误日志
                self._log(f"❌ 保证金不足 (Code 51008)", 'debug')
                return {'status': 'FAILED', 'summary': "保证金不足", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason'), 'price': current_realtime_price, 'confidence': signal_data.get('confidence')}
            else:
                self._log(f"下单失败: {e}", 'error')
                return {'status': 'FAILED', 'summary': f"API错误: {str(e)[:20]}", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason'), 'price': current_realtime_price, 'confidence': signal_data.get('confidence')}

        return {'status': 'SKIPPED', 'summary': "逻辑未覆盖", 'signal': signal_data.get('signal'), 'reason': signal_data.get('reason')}

    async def _update_real_trailing_sl(self, price_data, current_pos):
        """
        [Hardcore] 实时移动硬止损 (Real Trailing Hard Stop)
        每当价格有利移动时，直接修改交易所的止损单，确保止损线不断抬升。
        """
        if not current_pos:
            return
            
        try:
            current_price = price_data['price']
            side = current_pos['side']
            
            # [Safety Check] 获取持仓均价，确保只有在浮盈状态下才启用移动止损
            entry_price = float(current_pos.get('entry_price', 0) or 0)
            if entry_price <= 0: return

            # [Safety Check] 初始化动态止损 (如果为0或None)
            if not self.dynamic_stop_loss or self.dynamic_stop_loss <= 0:
                # 如果当前没有止损，为了安全起见，不要盲目设置，等待 AI 或后续逻辑设置
                # 或者，如果一定要设，可以设在开仓价的一定距离之外 (但这属于开仓逻辑)
                # 这里我们选择: 如果没有初始止损，就不启用移动逻辑，避免误伤
                return
            
            # 三线战法移动逻辑:
            # 如果是做多 (Long): 止损位 = 最近 3 根 K 线的最低点 (Low of last 3 candles)
            # 如果是做空 (Short): 止损位 = 最近 3 根 K 线的最高点 (High of last 3 candles)
            
            # [New] Breakeven Logic (保本优先)
            # 当浮盈达到设定阈值 (默认 2%) 时，强制把止损提到开仓价
            trailing_config = self.common_config.get('strategy', {}).get('trailing_stop', {})
            breakeven_trigger_pct = trailing_config.get('activation_pnl', 0.02)
            
            # [Fix] Calculate pnl_pct for Breakeven Logic
            pnl_pct = 0.0
            if entry_price > 0:
                if side == 'long':
                    pnl_pct = (current_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - current_price) / entry_price
            
            if pnl_pct > breakeven_trigger_pct:
                 breakeven_price = entry_price * (1.001 if side == 'long' else 0.999) # +0.1% to cover fees
                 
                 should_update_be = False
                 if side == 'long' and breakeven_price > self.dynamic_stop_loss:
                     should_update_be = True
                 elif side == 'short' and breakeven_price < self.dynamic_stop_loss:
                     should_update_be = True
                 elif self.dynamic_stop_loss == 0: # 初始状态
                     should_update_be = True
                     
                 if should_update_be:
                     self._log(f"🛡️ [Breakeven] 浮盈达标 ({pnl_pct*100:.1f}%) -> 强制保本: {breakeven_price:.4f}", 'info')
                     self.dynamic_stop_loss = breakeven_price
                     # 这里不 return，允许下方的 trailing 逻辑继续尝试能不能提得更高
            
            ohlcv = price_data.get('ohlcv', [])
            if len(ohlcv) < 3: return
            
            last_3 = ohlcv[-3:] # [k-2, k-1, k]
            
            new_sl = None
            if side == 'long':
                # 只有当当前价格高于开仓价 (浮盈) 时，才考虑移动止损
                if current_price > entry_price:
                    # 找出最近3根的最低点
                    lows = [float(k[3]) for k in last_3] # k[3] is Low
                    lowest = min(lows)
                    
                    # 只有当新止损位比旧止损位高时 (向上移动)，才更新
                    # 且必须在当前价格下方 (不能直接挂在市价上面)
                    if lowest > self.dynamic_stop_loss and lowest < current_price:
                        # [Double Check] 确保新止损位不低于开仓价 (保本原则，可选)
                        # if lowest < entry_price: lowest = entry_price
                        new_sl = lowest
                    
            elif side == 'short':
                # 只有当当前价格低于开仓价 (浮盈) 时，才考虑移动止损
                if current_price < entry_price:
                    # 找出最近3根的最高点
                    highs = [float(k[2]) for k in last_3] # k[2] is High
                    highest = max(highs)
                    
                    # 只有当新止损位比旧止损位低时 (向下移动)，才更新
                    # 且必须在当前价格上方
                    if highest < self.dynamic_stop_loss and highest > current_price:
                        new_sl = highest
            
            if new_sl:
                # 移动止损触发!
                change_pct = abs(new_sl - self.dynamic_stop_loss) / self.dynamic_stop_loss if self.dynamic_stop_loss else 0
                if change_pct > 0.001: # 只有变化超过 0.1% 才更新，避免频繁抖动
                    self._log(f"🛡️ [Trailing SL] 移动止损更新: {self.dynamic_stop_loss:.4f} -> {new_sl:.4f} (Entry: {entry_price:.4f})", 'info')
                    self.dynamic_stop_loss = new_sl
                    # [Fix] 必须 await 协程，否则不会执行
                    await self.save_state()
                    
                    # TODO: 如果想更激进，这里可以调用 API 修改交易所的委托单
                    # await self._modify_exchange_sl_order(new_sl)
                    
        except Exception as e:
            pass

    async def get_account_info(self):
        """获取账户余额和权益 (一次请求)"""
        if self.test_mode:
             # Calculate total equity including unrealized PnL
             sim_state = self.position_manager.get_sim_state()
             balance = sim_state['sim_balance']
             equity = balance
             
             sim_pos = await self.position_manager.get_current_position()
             if sim_pos:
                 if self.trade_mode == 'cash':
                     # [Fix] Cash Mode Equity = Cash Balance + Market Value of Holdings
                     # sim_pos['size'] is the coin amount
                     try:
                         # We need current price. 
                         # Try to get from position_manager's last updated price if possible, or fetch ticker
                         ticker = await self.exchange.fetch_ticker(self.symbol)
                         current_price = ticker['last']
                         market_value = float(sim_pos['size']) * current_price
                         equity = balance + market_value
                     except:
                         # Fallback if price fetch fails (unlikely in sim)
                         # Use entry price as approximation or just ignore
                         equity += sim_pos.get('unrealized_pnl', 0.0) # Wrong for Cash but fallback
                 else:
                     # Margin Mode: Equity = Margin Balance + PnL
                     equity += sim_pos.get('unrealized_pnl', 0.0)
                 
             return balance, equity

        try:
            params = {}
            balance = await self.exchange.fetch_balance(params)
            
            free_usdt = 0.0
            total_equity = 0.0
            
            # 1. 解析可用余额 (Free USDT)
            if 'USDT' in balance: 
                free_usdt = float(balance['USDT']['free'])
            elif 'info' in balance and 'data' in balance['info']:
                # [Fix] Handle empty data list for Unified Account
                if balance['info']['data']:
                    for asset in balance['info']['data'][0]['details']:
                        if asset['ccy'] == 'USDT':
                            free_usdt = float(asset['availBal'])
                            break
            
            # 2. 解析总权益 (Total Equity)
            if 'info' in balance and 'data' in balance['info']:
                if balance['info']['data']:
                    data0 = balance['info']['data'][0]
                    if 'totalEq' in data0:
                        total_equity = float(data0['totalEq'])
            elif 'USDT' in balance:
                if 'equity' in balance['USDT']: total_equity = float(balance['USDT']['equity'])
                elif 'total' in balance['USDT']: total_equity = float(balance['USDT']['total'])
                
            return free_usdt, total_equity
        except Exception as e:
            self._log(f"获取账户信息失败: {e}", 'warning')
            return 0.0, 0.0

    async def get_account_balance(self):
        # 保留兼容性，但建议使用 get_account_info
        b, _ = await self.get_account_info()
        return b

    async def get_account_equity(self):
        # 保留兼容性
        _, e = await self.get_account_info()
        return e

    async def close_all_positions(self):
        try:
            pos = await self.get_current_position()
            if pos:
                # [Fix] 区分现货和平仓
                if self.trade_mode == 'cash':
                    await self.exchange.create_market_order(self.symbol, 'sell', pos['size'])
                    self._log(f"现货清仓成功: {pos['size']}")
                else:
                    side = 'buy' if pos['side'] == 'short' else 'sell'
                    await self.exchange.create_market_order(self.symbol, side, pos['size'], params={'reduceOnly': True})
                    self._log("合约平仓成功")
        except Exception as e:
            self._log(f"平仓失败: {e}", 'error')

    async def run_safety_check(self, current_position=None, current_price=None):
        """
        高频安全检查 (每 5秒 运行)
        仅检查止损/止盈，不进行复杂分析
        """
        try:
            # 1. 获取最新价格 (Ticker) - 速度快，消耗资源少
            # [Optimization] 支持从外部传入 current_price 以减少 API 调用
            if current_price is None:
                ticker = await self.exchange.fetch_ticker(self.symbol)
                current_price = ticker['last']
            
            # 2. 获取持仓
            pos = current_position
            if pos is None:
                pos = await self.get_current_position()
            
            if not pos:
                self.trailing_max_pnl = 0.0 # 重置水位线
                return None # 空仓无需监控
                
            # 3. 计算 PnL
            pnl_pct = 0.0
            entry = pos['entry_price']
            if entry > 0:
                if pos['side'] == 'long':
                    pnl_pct = (current_price - entry) / entry
                elif pos['side'] == 'short':
                    pnl_pct = (entry - current_price) / entry
            
            # [New] 移动止盈 (Trailing Stop)
            if self.trailing_config.get('enabled', False):
                activation = self.trailing_config.get('activation_pnl', 0.01) # 默认 1% 激活
                callback = self.trailing_config.get('callback_rate', 0.003)   # 默认 0.3% 回撤
                
                # 更新最高水位线 (仅当 PnL 为正时)
                if pnl_pct > self.trailing_max_pnl:
                    self.trailing_max_pnl = pnl_pct
                
                # 检查触发条件
                # 1. 当前水位必须超过激活阈值 (已进入盈利区)
                # 2. 当前 PnL 相比最高水位回撤了 callback 幅度
                if self.trailing_max_pnl >= activation:
                    if pnl_pct <= (self.trailing_max_pnl - callback):
                        self._log(f"📉 [TRAILING] 触发移动止盈: 最高 {self.trailing_max_pnl*100:.2f}% -> 当前 {pnl_pct*100:.2f}% (回撤 > {callback*100}%)", 'info')
                        
                        fake_signal = {
                            'signal': 'SELL' if pos['side'] == 'long' else 'BUY', 
                            'confidence': 'HIGH', 
                            'amount': pos['size'], 
                            'reason': f"移动止盈触发: Peak {self.trailing_max_pnl*100:.2f}% -> Now {pnl_pct*100:.2f}%"
                        }
                        
                        await self.execute_trade(fake_signal)
                        return {
                            'symbol': self.symbol,
                            'type': 'TRAILING_STOP',
                            'pnl': pnl_pct
                        }

            # 4. 检查硬止损 (Hard Stop Loss) & 止盈 (Take Profit) - [Fixed] 双向监控
            # [New] Dynamic Stop Loss / Take Profit Check
            # Check if AI provided a specific price level for SL/TP
            if self.dynamic_sl_side == pos['side']:
                # Dynamic Stop Loss
                if self.dynamic_stop_loss > 0:
                    should_stop = False
                    if pos['side'] == 'long' and current_price <= self.dynamic_stop_loss:
                        should_stop = True
                    elif pos['side'] == 'short' and current_price >= self.dynamic_stop_loss:
                        should_stop = True
                    
                    if should_stop:
                        self._log(f"🚨 [WATCHDOG] 触发 AI 动态止损: Price {current_price} hit SL {self.dynamic_stop_loss}", 'warning')
                        fake_signal = {
                            'signal': 'SELL' if pos['side'] == 'long' else 'BUY', 
                            'confidence': 'HIGH', 
                            'amount': pos['size'], 
                            'reason': f"AI动态止损触发: {current_price} vs {self.dynamic_stop_loss}"
                        }
                        await self.execute_trade(fake_signal)
                        return {'symbol': self.symbol, 'type': 'STOP_LOSS_AI', 'price': current_price}

                # Dynamic Take Profit
                # [Removed] Per user instruction: No Take Profit, only Stop Loss
                pass

            if self.risk_control.get('max_loss_rate'):
                max_loss = float(self.risk_control['max_loss_rate'])
                if pnl_pct <= -max_loss:
                    self._log(f"🚨 [WATCHDOG] 触发硬止损: 当前亏损 {pnl_pct*100:.2f}% (阈值 -{max_loss*100}%)", 'warning')
                    
                    # 构造一个伪造的 SELL 信号立即平仓
                    fake_signal = {
                        'signal': 'SELL' if pos['side'] == 'long' else 'BUY', 
                        'confidence': 'HIGH', # 强制最高信心
                        'amount': pos['size'], # amount 0 在平仓逻辑中会被忽略，直接全平
                        'reason': f"硬止损触发: Loss {pnl_pct*100:.2f}%"
                    }
                    
                    await self.execute_trade(fake_signal)
                    return {
                        'symbol': self.symbol,
                        'type': 'STOP_LOSS',
                        'pnl': pnl_pct
                    }
            
            return None
            
        except Exception as e:
            # self._log(f"安全检查异常: {e}", 'error')
            return None

        self.config_path = "config.json"
        self.last_config_mtime = 0
        self._init_config_watcher()
        
        # [New] Watchdog & Heartbeat
        self.last_heartbeat_time = time.time()
        self.consecutive_errors = 0
        
        # [New] Global Circuit Breaker
        self.daily_high_equity = 0.0

    def _init_config_watcher(self):
        try:
            if os.path.exists(self.config_path):
                self.last_config_mtime = os.path.getmtime(self.config_path)
        except:
            pass

    async def _check_config_update(self):
        """[Hot Reload] 检查配置文件是否更新"""
        try:
            if not os.path.exists(self.config_path): return

            current_mtime = os.path.getmtime(self.config_path)
            if current_mtime > self.last_config_mtime:
                self._log("🔄 检测到配置更新，正在热重载...", 'info')
                
                # 读取新配置
                try:
                    # 使用 run_in_executor 避免文件IO阻塞
                    loop = asyncio.get_running_loop()
                    def read_config_sync():
                        with open(self.config_path, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    
                    new_config = await loop.run_in_executor(None, read_config_sync)
                except Exception as e:
                    self._log(f"⚠️ 配置文件读取失败: {e}", 'warning')
                    return
                
                if not isinstance(new_config, dict):
                    return

                # [Fix] Update mtime only after successful load to prevent skipping updates on read failure
                self.last_config_mtime = current_mtime
                
                # 找到当前 symbol 的配置
                symbols = new_config.get('symbols', [])
                if not isinstance(symbols, list):
                    return

                new_symbol_conf = next((s for s in symbols if isinstance(s, dict) and s.get('symbol') == self.symbol), None)
                if new_symbol_conf:
                    # 更新关键参数
                    old_alloc = self.allocation
                    new_alloc_val = new_symbol_conf.get('allocation', self.allocation)
                    
                    # [Fix] 保持类型一致性 (int/float or 'auto')
                    if str(new_alloc_val).lower() == 'auto':
                        self.allocation = 'auto'
                    else:
                        try:
                            self.allocation = float(new_alloc_val)
                        except:
                            self.allocation = 'auto' # Fallback
                    
                    try:
                        self.leverage = int(new_symbol_conf.get('leverage', self.leverage))
                    except:
                        pass # Keep old leverage if invalid
                    
                    # 重新应用杠杆
                    if self.trade_mode != 'cash':
                        await self.setup_leverage()
                        
                    self._log(f"✅ 热重载完成: Alloc {old_alloc}->{self.allocation}, Lev {self.leverage}x")
        except Exception as e:
            self._log(f"⚠️ 热重载失败: {e}", 'warning')

    async def _check_dynamic_risk_levels(self, current_price, current_pos):
        """
        [Orbit B] 实时检查动态止损/止盈 (基于 15m 三线战法计算)
        """
        if not current_pos: return

        side = current_pos['side']
        should_exit = False
        reason = ""

        # 1. 检查动态止损 (Dynamic SL)
        if self.dynamic_stop_loss > 0:
            if side == 'long' and current_price <= self.dynamic_stop_loss:
                should_exit = True
                reason = f"三线战法动态止损触发 ({current_price} <= {self.dynamic_stop_loss})"
            elif side == 'short' and current_price >= self.dynamic_stop_loss:
                should_exit = True
                reason = f"三线战法动态止损触发 ({current_price} >= {self.dynamic_stop_loss})"

        # 2. 检查动态止盈 (Dynamic TP)
        if not should_exit and self.dynamic_take_profit > 0:
            if side == 'long' and current_price >= self.dynamic_take_profit:
                should_exit = True
                reason = f"三线战法动态止盈触发 ({current_price} >= {self.dynamic_take_profit})"
            elif side == 'short' and current_price <= self.dynamic_take_profit:
                should_exit = True
                reason = f"三线战法动态止盈触发 ({current_price} <= {self.dynamic_take_profit})"

        if should_exit:
            # [Optimization] 动态风控触发时，打印简洁日志
            self._log(f"🚨 [Orbit B] {reason}", 'warning')
            
            # 执行平仓逻辑 (使用 create_order_with_retry 直接下单，绕过冗余日志)
            try:
                await self.order_executor.create_order_with_retry(
                    side='sell' if current_pos['side'] == 'long' else 'buy',
                    amount=float(current_pos['size']),
                    order_type='market',
                    params={'reduceOnly': True}
                )
            except Exception as e:
                # 即使下单失败，也要让流程继续，不要崩溃
                self._log(f"❌ [Orbit B] 动态止盈止损下单失败: {e}", 'error')
                return

            # 发送通知
            await self.send_notification(
                f"🚨 **动态风控触发**\n原因: {reason}\n当前价: {current_price}", 
                title=f"🛑 止盈止损 | {self.symbol}"
            )
            # 重置状态
            self.dynamic_stop_loss = 0.0
            self.dynamic_take_profit = 0.0
            self.dynamic_sl_side = None
            await self.save_state()

    async def run(self):
        """Async 单次运行 - 返回结果给调用者进行统一打印"""
        # [New] Hot Reload Check
        await self._check_config_update()
        
        # [New] Watchdog Check
        # 如果距离上次心跳超过 5 分钟，且连续错误 > 5，发送严重警报
        if time.time() - self.last_heartbeat_time > 300:
             self._log("🚨 [WATCHDOG] 心跳丢失超过 300s!", 'error')
             # 这里可以触发更高级别的报警，比如发送邮件或短信 (依赖外部服务)
             # 目前先重置，防止刷屏
             self.last_heartbeat_time = time.time()

        try:
            # self._log(f"🚀 开始分析...")
            
            if not hasattr(self, 'last_fee_update_time'):
                await self._update_fee_rate()
                self.last_fee_update_time = time.time()
            
            price_data = await self.get_ohlcv()
            if not price_data: return None

            # [New] Dynamic Risk Check (Orbit B)
            # 实时监控动态止盈止损 (基于 15m 三线战法计算出的点位)
            # 这个逻辑在 Orbit B (60s) 中每次都会运行
            
            # [Fix] Move current_pos initialization to the TOP of the risk check logic
            current_pos = None
            try:
                current_pos = await self.get_current_position()
            except Exception as e:
                self._log(f"获取持仓失败: {e}", 'warning')

            if current_pos and (self.dynamic_stop_loss > 0 or self.dynamic_take_profit > 0):
                await self._check_dynamic_risk_levels(price_data['price'], current_pos)
            
            # [New] Fast Pattern Exit (Monitor by Minute) - User Request: "monitor by minute... fetch volume/price... three-line strategy"
            # 移至 analyze_on_bar_close 之前，确保即使在 K 线未收盘时也能触发分钟级止盈
            # [Fix] current_pos already initialized above
            # current_pos = None
            # try:
            #    current_pos = await self.get_current_position()
            # except Exception as e:
            #    self._log(f"获取持仓失败: {e}", 'warning')
                
            if current_pos:
                try:
                    # [Debug] 显性化监控状态：只有持仓时才会打印此日志
                    # self._log(f"🔍 [1m监控] 正在扫描 {self.symbol} 持仓的三线形态...", 'debug')
                    
                    # 1. Fetch 1m data for fast exit monitoring
                    ohlcv_1m = await self.exchange.fetch_ohlcv(self.symbol, '1m', limit=10)
                    if ohlcv_1m:
                         df_1m = pd.DataFrame(ohlcv_1m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                         # Convert numeric
                         for col in ['open', 'high', 'low', 'close', 'volume']:
                             df_1m[col] = df_1m[col].astype(float)
                         
                         # 2. Check Pattern on 1m
                         pat_1m = self.signal_processor.check_candlestick_pattern(df_1m)
                         
                         # [User Request] 轨道B运行的时候把一些关键信息打印出来
                         # 打印当前 1m K线信息，确认数据已获取
                         # last_close = df_1m.iloc[-1]['close']
                         # last_vol = df_1m.iloc[-1]['volume']
                         # prev_vols = df_1m.iloc[-4:-1]['volume'].values
                         # max_vol3 = max(prev_vols) if len(prev_vols) > 0 else 0
                         # self._log(f"🚄 [Orbit B] 1m极速监控 | Price: {last_close} | Vol: {last_vol:.2f} (Max3: {max_vol3:.2f}) | Pattern: {pat_1m if pat_1m else 'None'}", 'info')
                         
                         # [Update] 仅打印关键触发理由，避免刷屏
                         # 信息已整合至下方 Monitoring Mode 的 summary 中显示在表格里
                         pass
                         
                         # [New] 在监控模式下，也需要更新 result 以便表格显示 SCAN 状态
                         # 但如果不是 AI 信号周期，我们不能返回 EXECUTED 或 SKIPPED
                         # 我们返回一个特殊的 "MONITOR" 状态包
                         
                         should_close = False
                         exit_reason = ""
                         
                         # 3. Decision Logic
                         if current_pos['side'] == 'long' and pat_1m == 'BEARISH_STRIKE':
                             should_close = True
                             exit_reason = "1m三线战法(看跌) - 极速止盈"
                         elif current_pos['side'] == 'short' and pat_1m == 'BULLISH_STRIKE':
                             should_close = True
                             exit_reason = "1m三线战法(看涨) - 极速止盈"
                             
                         if should_close:
                             self._log(f"⚡ [Fast Exit] 触发极速离场信号: {exit_reason}")
                             # Execute Close
                             # [Critical Fix] 使用 create_order_with_retry 直接下单，绕过 execute_order 的日志
                             try:
                                 await self.order_executor.create_order_with_retry(
                                     side='sell' if current_pos['side'] == 'long' else 'buy',
                                     amount=float(current_pos['size']),
                                     order_type='market',
                                     params={'reduceOnly': True}
                                 )
                             except Exception as e:
                                 self._log(f"❌ [Fast Exit] 极速离场下单失败: {e}", 'error')
                             
                             await self.send_notification(f"⚡ **极速止盈触发**\n原因: {exit_reason}\n周期: 1m监控", title=f"🚀 止盈离场 | {self.symbol}")
                             # [Fix] 极速止盈后直接返回，不继续等待 K 线收盘
                             return {
                                 'symbol': self.symbol,
                                 'price': price_data['price'],
                                 'change': price_data['price_change'],
                                 'signal': 'CLOSE',
                                 'confidence': 'HIGH',
                                 'reason': exit_reason,
                                 'status': 'EXECUTED',
                                 'summary': 'Fast Exit Triggered',
                                 'volatility': price_data.get('volatility_status', 'NORMAL'),
                                 'persona': 'Fast Guard',
                                 'recommended_sleep': 60.0
                             }
                except Exception as e:
                    self._log(f"Fast exit check failed: {e}", 'warning')

            if self.analyze_on_bar_close:
                # [Frequency Decoupling]
                # 即使是 analyze_on_bar_close，我们也需要检查是否到了用户配置的 loop_interval
                # 否则如果主循环是 60s，AI 也会每 60s 检查一次是否收盘 (这没问题)
                # 但如果用户想 300s 才检查一次 AI，这里需要节流
                
                ai_interval = self.common_config.get('actual_ai_interval', 60)
                if not hasattr(self, 'last_ai_check_time'):
                    self.last_ai_check_time = 0
                
                # 如果距离上次 AI 检查时间不足 loop_interval (且不是第一次)，则跳过 AI 部分
                # 但要允许一定的误差 (例如 1秒)，防止因为 sleep 精度导致刚好错过
                if time.time() - self.last_ai_check_time < (ai_interval - 2):
                    # 返回一个简单的状态，表明正在监控中
                    # [Fix] 增加必要的字段，防止表格显示为空
                    monitor_summary = f'监控中 ({int(ai_interval - (time.time() - self.last_ai_check_time))}s)'
                    
                    # [Feature] 如果有持仓，显示止盈止损价格和当前盈亏
                    if current_pos:
                        # 计算当前浮动盈亏比例
                        entry_price = float(current_pos.get('avgPx', 0))
                        if entry_price > 0:
                            current_price = price_data['price']
                            if current_pos['side'] == 'long':
                                pnl_pct = (current_price - entry_price) / entry_price * 100
                            else:
                                pnl_pct = (entry_price - current_price) / entry_price * 100
                            
                            # 添加 PnL 信息到 summary
                            pnl_str = f"{pnl_pct:+.2f}%"
                            monitor_summary = f"持仓监控 | PnL: {pnl_str} | " + monitor_summary
                            
                            # 添加止盈止损位显示
                            if self.dynamic_stop_loss > 0 or self.dynamic_take_profit > 0:
                                sl_str = f"{self.dynamic_stop_loss:.1f}" if self.dynamic_stop_loss > 0 else "-"
                                tp_str = f"{self.dynamic_take_profit:.1f}" if self.dynamic_take_profit > 0 else "-"
                                monitor_summary += f" | SL:{sl_str} TP:{tp_str}"

                    # 如果有 1m 形态，优先显示
                    if 'pat_1m' in locals() and pat_1m:
                        monitor_summary = f"⚠️ 形态预警: {pat_1m} | {monitor_summary}"

                    return {
                        'symbol': self.symbol,
                        'price': price_data['price'],
                        'change': price_data.get('price_change', 0.0), # Use .get for safety
                        'signal': 'HOLD',
                        'confidence': 'LOW',
                        'reason': 'AI冷却中',
                        'summary': monitor_summary,
                        'status': 'UNKNOWN', # [Critical] Return UNKNOWN so OKXBot_Plus handles it as WAIT/SCAN
                        'status_msg': 'Monitoring',
                        'volatility': price_data.get('volatility_status', 'NORMAL'),
                        'persona': 'Monitor',
                        'adx': price_data.get('indicators', {}).get('adx'),
                        'rsi': price_data.get('indicators', {}).get('rsi'),
                        'atr_ratio': price_data.get('indicators', {}).get('atr_ratio'),
                        'vol_ratio': price_data.get('indicators', {}).get('vol_ratio'),
                        'pattern': pat_1m if 'pat_1m' in locals() and pat_1m else '-', # Show 1m pattern if exists
                        'recommended_sleep': 1.0 # 保持活跃
                    }
                
                # 更新检查时间
                self.last_ai_check_time = time.time()
                
                try:
                    tf = self.timeframe
                    tf_sec = 0
                    if tf.endswith('m'):
                        tf_sec = int(tf[:-1]) * 60
                    elif tf.endswith('h'):
                        tf_sec = int(tf[:-1]) * 3600
                    elif tf.endswith('d'):
                        tf_sec = int(tf[:-1]) * 86400
                    last_rec = price_data.get('kline_data', [])[-1]
                    last_ts = pd.Timestamp(last_rec['timestamp']).timestamp() if last_rec else None
                    now_ts = time.time()
                    if last_ts and now_ts < last_ts + tf_sec:
                        persona_map = {
                            'HIGH_TREND': 'Trend Hunter (趋势猎人)',
                            'LOW': 'Grid Trader (网格交易)',
                            'HIGH_CHOPPY': 'Risk Guardian (风控卫士)',
                            'NORMAL': 'Day Trader (波段交易)'
                        }
                        persona = persona_map.get(price_data.get('volatility_status', 'NORMAL'), 'NORMAL')
                        return {
                            'symbol': self.symbol,
                            'price': price_data['price'],
                            'change': price_data.get('price_change', 0.0),
                            'signal': 'HOLD',
                            'confidence': 'LOW',
                            'reason': '等待K线收盘',
                            'summary': '等待K线收盘',
                            'status': 'HOLD',
                            'status_msg': '未收盘',
                            'volatility': price_data.get('volatility_status', 'NORMAL'),
                            'persona': persona,
                            'adx': price_data.get('indicators', {}).get('adx'),
                            'rsi': price_data.get('indicators', {}).get('rsi'),
                            'atr_ratio': price_data.get('indicators', {}).get('atr_ratio'),
                            'vol_ratio': price_data.get('indicators', {}).get('vol_ratio'),
                            'recommended_sleep': max(1.0, min(tf_sec, 60))
                        }
                    if last_ts and self._last_analyzed_bar_ts == last_ts:
                        persona_map = {
                            'HIGH_TREND': 'Trend Hunter (趋势猎人)',
                            'LOW': 'Grid Trader (网格交易)',
                            'HIGH_CHOPPY': 'Risk Guardian (风控卫士)',
                            'NORMAL': 'Day Trader (波段交易)'
                        }
                        persona = persona_map.get(price_data.get('volatility_status', 'NORMAL'), 'NORMAL')
                        return {
                            'symbol': self.symbol,
                            'price': price_data['price'],
                            'change': price_data.get('price_change', 0.0),
                            'signal': 'HOLD',
                            'confidence': 'LOW',
                            'reason': '本周期已分析',
                            'summary': '本周期已分析',
                            'status': 'HOLD',
                            'status_msg': '已分析',
                            'volatility': price_data.get('volatility_status', 'NORMAL'),
                            'persona': persona,
                            'adx': price_data.get('indicators', {}).get('adx'),
                            'rsi': price_data.get('indicators', {}).get('rsi'),
                            'atr_ratio': price_data.get('indicators', {}).get('atr_ratio'),
                            'vol_ratio': price_data.get('indicators', {}).get('vol_ratio'),
                            'recommended_sleep': 5.0
                        }
                    if last_ts:
                        self._last_analyzed_bar_ts = last_ts
                except Exception:
                    pass

            # [Optimized] 获取实时余额用于动态资金计算
            balance, equity = await self.get_account_info()
            
            # Call Agent
            # [Fix] 确保在调用 AI 之前获取最新的持仓信息
            # 即使前面 Fast Exit 已经获取过一次，这里为了保险起见（可能刚才被止盈了），最好再次确认
            # 但为了性能，如果刚才没触发止盈，复用 current_pos 也可以
            # 这里我们选择安全起见，复用之前获取的 current_pos，如果它为空，再尝试获取一次
            if not current_pos:
                 current_pos = await self.get_current_position()

            # [New] Global Circuit Breaker (账户级熔断)
            # 记录当日最高权益 (High Water Mark)
            # [Fix] Reset high water mark when day changes
            current_day = datetime.now().strftime('%Y%m%d')
            if self.high_water_day != current_day:
                self.high_water_day = current_day
                self.daily_high_equity = 0.0
            # Initialize high water with current equity to avoid stale large value
            if self.daily_high_equity == 0.0:
                self.daily_high_equity = equity
            if equity > self.daily_high_equity:
                self.daily_high_equity = equity
                # [Fix] Persist high water mark
                asyncio.create_task(self.save_state())
            
            # 如果从高点回撤超过 15% (硬性熔断线)
            if self.daily_high_equity > 0:
                drawdown = (equity - self.daily_high_equity) / self.daily_high_equity
                if drawdown < -0.15:
                    self._log(f"💀 [CIRCUIT BREAKER] 触发账户级熔断! 回撤 {drawdown*100:.2f}% (>15%)", 'critical')
                    await self.send_notification(
                        f"💀 **账户熔断报警**\n当前权益: {equity:.2f}\n当日最高: {self.daily_high_equity:.2f}\n回撤幅度: {drawdown*100:.2f}%\n> **系统将停止开新仓，仅允许平仓!**",
                        title=f"💀 熔断触发 | {self.symbol}"
                    )
                    # 这里我们可以选择 return None 跳过后续分析，或者传入一个 flag 让 AI 只做平仓
                    # 为了安全，直接 return，并尝试平仓 (TODO: 自动平仓逻辑需谨慎)
                    # [Fix] 调用 RiskManager 的 close_all_traders 是更安全的选择，而不是在这里局部处理
                    # 目前仅返回 Stop 信号，依赖 RiskManager 的全局风控去扫尾
                    return {
                        'symbol': self.symbol,
                        'price': price_data['price'],
                        'change': price_data.get('price_change', 0.0), # [Fix] Add missing key
                        'signal': 'STOPPED', # [Fix] Add missing signal
                        'confidence': 'HIGH', # [Fix] Add missing confidence
                        'reason': f"熔断触发: 回撤 {drawdown*100:.2f}%", # [Fix] Add missing reason
                        'status': 'STOPPED',
                        'status_msg': f"熔断触发: 回撤 {drawdown*100:.2f}%",
                        'recommended_sleep': 60.0 # 冷却 1 分钟
                    }

            await self._update_amount_auto(price_data['price'], balance)
            
            # Calculate volatility status
            ind = price_data.get('indicators', {})
            # [Fix] Already calculated in get_ohlcv with better logic (ATR Ratio)
            volatility_status = price_data.get('volatility_status', 'NORMAL')
            adx_val = ind.get('adx') # re-fetch for reporting
            
            rsi_val = ind.get('rsi')
            gate_conf = self.common_config.get('strategy', {}).get('signal_gate', {})
            rsi_min = float(gate_conf.get('rsi_min', 35))
            rsi_max = float(gate_conf.get('rsi_max', 65))
            adx_min = float(gate_conf.get('adx_min', 25))
            
            # [New] 量价异动唤醒机制 (Volume/Price Surge Override)
            # 只要满足以下任意一条，即使 ADX/RSI 不达标也强制放行:
            # 1. 成交量突增 (> 3倍均量)
            # 2. 价格瞬间剧烈波动 (> 0.5%)
            # 3. [New] 识别到三线战法 (Three-Line Strike) 形态
            
            is_surge = False
            surge_reason = ""
            
            # 检查三线战法形态
            candlestick_pattern, pat_levels = self._check_candlestick_pattern(price_data)
            if candlestick_pattern:
                is_surge = True
                surge_reason = f"形态突袭 ({candlestick_pattern})"
                try:
                    self._log(f"📐 三线战法识别: {candlestick_pattern}")
                    # [New] 保存动态止盈止损位
                    if pat_levels:
                        self.dynamic_stop_loss = pat_levels.get('sl', 0)
                        self.dynamic_take_profit = pat_levels.get('tp', 0)
                        self.dynamic_sl_side = 'long' if 'BULLISH' in candlestick_pattern else 'short'
                        self._log(f"🎯 设定动态风控位: SL={self.dynamic_stop_loss}, TP={self.dynamic_take_profit}")
                        asyncio.create_task(self.save_state())
                except Exception:
                    pass
            
            vol_ratio = ind.get('vol_ratio')
            if vol_ratio and vol_ratio > 3.0:
                is_surge = True
                surge_reason = f"成交量爆增 ({vol_ratio:.1f}x)"
                
            # 计算当前K线瞬间涨跌幅 (close vs open)
            # price_data['ohlcv'][-1] 是最新K线: [ts, o, h, l, c, v]
            try:
                last_k = price_data.get('ohlcv', [])[-1]
                open_p = float(last_k[1])
                close_p = float(last_k[4])
                if open_p > 0:
                    instant_change_pct = abs((close_p - open_p) / open_p) * 100
                    if instant_change_pct > 0.5:
                        is_surge = True
                        surge_reason = f"瞬间剧烈波动 ({instant_change_pct:.2f}%)"
            except:
                pass

            gate_reason = None
            # 只有当非异动状态时，才执行常规门禁
            if not is_surge:
                if volatility_status == 'HIGH_TREND':
                    if adx_val is None or adx_val < adx_min:
                        val_str = f"{adx_val:.1f}" if adx_val is not None else "NaN"
                        gate_reason = f"趋势不足 (ADX {val_str} < {adx_min})"
                else:
                    if rsi_val is None or rsi_val < rsi_min or rsi_val > rsi_max:
                        val_str = f"{rsi_val:.1f}" if rsi_val is not None else "NaN"
                        gate_reason = f"RSI超界 ({val_str} ∉ [{rsi_min}, {rsi_max}])"
                    elif adx_val is not None and adx_val < adx_min:
                        gate_reason = f"ADX不足 ({adx_val:.1f} < {adx_min})"
            else:
                # 如果是异动，记录日志提醒
                self._log(f"🚀 触发异动唤醒: {surge_reason} -> 绕过 ADX/RSI 门禁", 'info')

            if gate_reason:
                persona_map = {
                    'HIGH_TREND': 'Trend Hunter (趋势猎人)',
                    'LOW': 'Grid Trader (网格交易)',
                    'HIGH_CHOPPY': 'Risk Guardian (风控卫士)',
                    'NORMAL': 'Day Trader (波段交易)'
                }
                persona = persona_map.get(volatility_status, volatility_status)
                self.consecutive_errors = 0
                return {
                    'symbol': self.symbol,
                    'price': price_data['price'],
                    'change': price_data['price_change'],
                    'signal': 'HOLD',
                    'confidence': 'LOW',
                    'reason': gate_reason,
                    'summary': gate_reason,
                    'status': 'HOLD',
                    'status_msg': gate_reason,
                    'volatility': volatility_status,
                    'persona': persona,
                    'adx': adx_val,
                    'rsi': rsi_val,
                    'atr_ratio': ind.get('atr_ratio'),
                    'vol_ratio': ind.get('vol_ratio'),
                    'pattern': candlestick_pattern or '-',
                    'recommended_sleep': 60.0
                }

            # Call Agent (Wait, we already have current_pos above)
            # current_pos = await self.get_current_position() # Removed duplicate call
            
            # [New] 实时更新移动止损 (Real Trailing SL)
            if current_pos:
                await self._update_real_trailing_sl(price_data, current_pos)
            
            # [New] 获取账户总权益并计算 PnL
            current_pnl = 0.0
            if self.initial_balance > 0:
                if equity > 0:
                    current_pnl = equity - self.initial_balance

            # [New] 获取资金费率 (Funding Rate)
            funding_rate = 0.0
            try:
                 # 仅合约模式需要获取资金费率
                 if self.trade_mode != 'cash':
                     # [Optimization] Use fetch_funding_rate which is standard.
                     # Some exchanges need symbol, some don't. OKX needs it.
                     fr_data = await self.exchange.fetch_funding_rate(self.symbol)
                     if fr_data:
                         funding_rate = float(fr_data.get('fundingRate', 0))
            except Exception as e:
                 # self._log(f"获取资金费率失败: {e}", 'warning')
                 pass

            # [New] Global Market Context (BTC Beta)
            # 获取 BTC 走势作为大盘风向标
            btc_change_24h = None
            try:
                if 'BTC' not in self.symbol: # 如果自己不是 BTC
                    btc_ticker = await self.exchange.fetch_ticker('BTC/USDT')
                    if btc_ticker and 'percentage' in btc_ticker:
                        btc_change_24h = float(btc_ticker['percentage'])
                else:
                    # 如果自己就是 BTC，直接使用自己的涨跌幅
                    btc_change_24h = price_data['price_change']
            except:
                pass

            signal_data = await self.agent.analyze( 
                self.symbol, 
                self.timeframe, 
                price_data, 
                current_pos, 
                balance, 
                self.amount,
                self.taker_fee_rate,
                self.leverage, # 传入杠杆
                self.risk_control, # 传入风控配置
                current_pnl, # [New] 传入当前账户总盈亏
                funding_rate, # [New] 传入资金费率
                self.common_config.get('strategy', {}).get('dynamic_tp', False), # [New] 传入动态止盈开关 (False)
                btc_change_24h=btc_change_24h, # [New] 传入 BTC 涨跌幅
                is_surge=is_surge, # [New] 传入异动唤醒标志
                candlestick_pattern=candlestick_pattern # [New] 传入 K 线形态
            )
            
            if signal_data:
                # [New] 异步保存信号记录
                asyncio.create_task(self.data_manager.save_signal(self.symbol, signal_data, price_data['price']))
                
                # [Fix] 注入波动率状态，供 execution 阶段做信心豁免
                signal_data['volatility_status'] = volatility_status
                
                # [Log Cleanup] 这里的日志移交给上层统一打印
                reason = signal_data.get('reason', '无理由')
                signal = signal_data.get('signal', 'UNKNOWN')
                confidence = signal_data.get('confidence', 'LOW')
                
                exec_status, exec_msg = "UNKNOWN", ""
                try:
                    # [Optimization] Pass cached data to execute_trade
                    result = await self.execute_trade(
                        signal_data, 
                        current_price=price_data['price'], 
                        current_position=current_pos, 
                        balance=balance
                    )
                    
                    if isinstance(result, tuple) and len(result) == 2:
                        exec_status, exec_msg = result
                    elif result is None:
                        # execute_trade might return None if it just returned without value in some paths (legacy)
                        pass
                except Exception as e:
                    exec_status = "ERROR"
                    exec_msg = str(e)
                    self._log(f"执行交易失败: {e}", 'error')

                # 映射为用户友好的 "交易人格"
                persona_map = {
                    'HIGH_TREND': 'Trend Hunter (趋势猎人)',
                    'LOW': 'Grid Trader (网格交易)',
                    'HIGH_CHOPPY': 'Risk Guardian (风控卫士)',
                    'NORMAL': 'Day Trader (波段交易)'
                }
                persona = persona_map.get(volatility_status, volatility_status)

                # 返回结构化结果给上层打印表格
                # [Optimization] Calculate recommended sleep time based on volatility
                # 震荡市或空仓时，建议休眠 5s；趋势市或持仓时，建议休眠 1s
                recommended_sleep = 5.0
                if volatility_status == 'HIGH_TREND' or current_pos:
                    recommended_sleep = 1.0
                
                # [New] Reset consecutive errors on success
                self.consecutive_errors = 0
                
                return {
                    'symbol': self.symbol,
                    'price': price_data['price'],
                    'change': price_data['price_change'],
                    'signal': signal,
                    'confidence': confidence,
                    'reason': reason,
                    'summary': signal_data.get('summary', ''),
                    'status': exec_status,
                    'status_msg': exec_msg,
                    'volatility': volatility_status, # [New]
                    'persona': persona, # [New] Display Name
                    'adx': adx_val, # [New]
                    'rsi': ind.get('rsi'), # [New]
                    'atr_ratio': ind.get('atr_ratio'), # [New]
                    'vol_ratio': ind.get('vol_ratio'), # [New]
                    'pattern': candlestick_pattern or '-',
                    'recommended_sleep': recommended_sleep # [New]
                }
            return None
            
        except Exception as e:
            self.consecutive_errors += 1
            self._log(f"Run loop failed: {e}", 'error')
            
            # [Watchdog] 分级报警与熔断
            if self.consecutive_errors >= 10:
                # Level 3: Critical - Pause Trading
                await self.send_notification(
                    f"🛑 **系统熔断保护**\n连续失败 {self.consecutive_errors} 次\n错误: {str(e)[:100]}\n> **系统将暂停交易 30 分钟!**", 
                    title=f"💀 严重故障暂停 | {self.symbol}"
                )
                await asyncio.sleep(1800) # Sleep 30 mins
                self.consecutive_errors = 0 # Reset after long sleep to try again
                
            elif self.consecutive_errors >= 5:
                # Level 2: Alert
                await self.send_notification(
                    f"🚨 **系统危急报警**\n连续失败次数: {self.consecutive_errors}\n最后错误: {str(e)[:100]}", 
                    title=f"⚠️ 系统不稳定 | {self.symbol}"
                )
                await asyncio.sleep(10)
                
            elif self.consecutive_errors >= 3:
                # Level 1: Warning (Log only or minor delay)
                self._log(f"⚠️ 连续错误 {self.consecutive_errors} 次，正在重试...", 'warning')
                await asyncio.sleep(5)
                
            return None
        finally:
            # Update heartbeat regardless of success/failure to indicate liveness
            self.last_heartbeat_time = time.time()
