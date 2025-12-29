import time
import logging
import asyncio
import aiohttp
import emoji
import pandas as pd
from datetime import datetime
from core.utils import to_float, send_notification_async

class DeepSeekTrader:
    def __init__(self, symbol_config, common_config, exchange, agent):
        self.symbol = symbol_config['symbol']
        self.config_amount = symbol_config.get('amount', 'auto') 
        self.amount = 0
        self.allocation = symbol_config.get('allocation', 1.0)
        self.leverage = symbol_config['leverage']
        self.trade_mode = symbol_config.get('trade_mode', common_config.get('trade_mode', 'cross'))
        self.margin_mode = symbol_config.get('margin_mode', common_config.get('margin_mode', 'cross'))
        self.timeframe = common_config['timeframe']
        self.test_mode = common_config['test_mode']
        self.max_slippage = common_config.get('max_slippage_percent', 1.0)
        self.min_confidence = common_config.get('min_confidence', 'MEDIUM')
        
        strategy_config = common_config.get('strategy', {})
        self.history_limit = strategy_config.get('history_limit', 20)
        self.signal_limit = strategy_config.get('signal_limit', 30)
        
        self.taker_fee_rate = 0.001
        self.maker_fee_rate = 0.0008
        self.is_swap = ':' in self.symbol
        if self.is_swap:
            self.taker_fee_rate = 0.0005
            self.maker_fee_rate = 0.0002

        self.risk_control = common_config.get('risk_control', {})
        self.initial_balance = self.risk_control.get('initial_balance_usdt', 0)
        self.notification_config = common_config.get('notification', {})

        self.exchange = exchange
        self.agent = agent # DeepSeekAgent instance
        
        self.price_history = []
        self.signal_history = []
        self.logger = logging.getLogger("crypto_oracle")
        
    async def initialize(self):
        """Async Initialization"""
        await self.setup_leverage()
        # [Fix] 使用内部已有的 _update_fee_rate 方法，避免重复定义
        if hasattr(self, '_update_fee_rate'):
            await self._update_fee_rate()

        # [New] Smart Balance Calibration (智能资金校准)
        # 解决配置资金与实际资金偏差导致的错误盈亏计算问题
        try:
            current_equity = await self.get_account_equity()
            if current_equity > 0:
                # 如果 config 中的 initial_balance 明显异常 (偏差 > 10%)
                # 或者如果它是默认值 (比如 0)
                # 则自动校准为当前权益，以此作为本次运行的盈亏基准
                if self.initial_balance <= 0 or abs(self.initial_balance - current_equity) / current_equity > 0.1:
                    self._log(f"⚖️ 初始资金校准: 配置({self.initial_balance}) vs 实际({current_equity:.2f}) -> 自动修正为实际值", 'warning')
                    self.initial_balance = current_equity
                    # 同时更新 risk_control 里的值，确保一致性
                    if self.risk_control:
                        self.risk_control['initial_balance_usdt'] = current_equity
                else:
                    self._log(f"✅ 初始资金确认: {self.initial_balance} U (实际: {current_equity:.2f} U)")
        except Exception as e:
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

    async def _update_amount_auto(self, current_price):
        if self.config_amount != 'auto' and isinstance(self.config_amount, (int, float)) and self.config_amount > 0:
            self.amount = self.config_amount
            return

        try:
            quota = 0
            if self.initial_balance > 0:
                if self.allocation <= 1.0:
                    quota = self.initial_balance * self.allocation
                else:
                    quota = self.allocation
            
            if quota <= 0:
                target_usdt = 10.0
            else:
                target_usdt = quota * 0.1
            
            market = self.exchange.market(self.symbol)
            min_cost = market.get('limits', {}).get('cost', {}).get('min')
            if min_cost:
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
                self._log(f"⚠️ 数量 {raw_amount:.6f} < 最小限额 {limit_floor}，自动修正", 'info')
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
            self._log(emoji.emojize(f":gear: 设置杠杆: {self.leverage}x ({self.margin_mode})"))
        except Exception as e:
            self._log(emoji.emojize(f":no_entry: 杠杆设置失败: {e}"), 'error')

    def calculate_indicators(self, df):
        try:
            if len(df) < 30: return df
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal_line'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['macd_hist'] = df['macd'] - df['signal_line']

            df['sma_20'] = df['close'].rolling(window=20).mean()
            df['std_20'] = df['close'].rolling(window=20).std()
            df['upper_band'] = df['sma_20'] + (df['std_20'] * 2)
            df['lower_band'] = df['sma_20'] - (df['std_20'] * 2)
            
            df['vol_sma_20'] = df['volume'].rolling(window=20).mean()
            df['vol_ratio'] = df['volume'] / df['vol_sma_20'] # 量比
            
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
            df['buy_vol_prop_5'] = df['up_vol'].rolling(window=5).sum() / df['volume'].rolling(window=5).sum()
            
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
            df['tr_smooth'] = df['tr'].rolling(window=window).mean()
            df['plus_di'] = 100 * (df['plus_dm'].rolling(window=window).mean() / df['tr_smooth'])
            df['minus_di'] = 100 * (df['minus_dm'].rolling(window=window).mean() / df['tr_smooth'])
            df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
            df['adx'] = df['dx'].rolling(window=window).mean()
            return df
        except Exception as e:
            self._log(f"计算技术指标失败: {e}", 'error')
            return df

    async def get_ohlcv(self):
        try:
            # [兼容性处理] 如果配置了毫秒级周期 (如 "500ms")，API 请求强制使用 "1m"
            # OKX 不支持 "1s", "30s" 等周期，最低为 "1m"
            api_timeframe = self.timeframe
            if 'ms' in self.timeframe or self.timeframe.endswith('s'):
                api_timeframe = '1m'
            
            # [Fix 51000 Error] 确保 limit 足够大，有些交易所对小周期请求有最小数量要求
            # 或者当 API 周期为 1m 时，不要请求奇怪的数量
            # 增加超时设置，防止 fetch_ohlcv 永久挂起
            ohlcv = await asyncio.wait_for(
                self.exchange.fetch_ohlcv(self.symbol, api_timeframe, limit=100),
                timeout=10
            )
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

            # 维护历史 K 线记录
            self.price_history = df.tail(100).to_dict('records')
            
            # 使用配置中的 history_limit 进行预热检查（虽然主要逻辑已改为直接使用 API 的 limit）
            if not self.price_history and len(df) > self.history_limit:
                self._log(f"🔥 正在预热历史数据...")
                pass
            
            # 计算指标
            df = self.calculate_indicators(df)
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
            }
            
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

            # [Modified] 动态使用配置文件中的 history_limit 截取 K 线数据投喂给 AI
            # 确保至少有 10 条数据，防止过少
            feed_limit = max(10, self.history_limit)
            
            return {
                'price': current_data['close'],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'high': current_data['high'],
                'low': current_data['low'],
                'volume': current_data['volume'],
                'timeframe': self.timeframe,
                'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
                # 这里改为使用 dynamic feed_limit
                'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vol_ratio', 'obv']].tail(feed_limit).to_dict('records'),
                'indicators': indicators,
                'min_limit_info': min_limit_info,
                'min_notional_info': min_notional_info
            }
        except Exception as e:
            self._log(f"获取K线数据失败: {e}", 'error')
            return None

    async def get_current_position(self):
        try:
            positions = await self.exchange.fetch_positions([self.symbol])
            for pos in positions:
                if pos['symbol'] == self.symbol:
                    contracts = float(pos['contracts']) if pos['contracts'] else 0
                    if contracts > 0:
                        # [Fix] 获取合约面值，计算实际持币数量
                        contract_size = 1.0
                        try:
                            market = self.exchange.market(self.symbol)
                            contract_size = float(market.get('contractSize', 1.0))
                        except:
                            pass

                        return {
                            'side': pos['side'],
                            'size': contracts,
                            'coin_size': contracts * contract_size, # 实际币数
                            'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                            'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                            'leverage': float(pos['leverage']) if pos['leverage'] else self.leverage,
                            'symbol': pos['symbol']
                        }
            return None
        except Exception as e:
            self._log(f"获取持仓失败: {e}", 'error')
            return None

    def get_market_volatility(self, kline_data, adx_value=None):
        try:
            if len(kline_data) < 5: return "NORMAL"
            ranges = []
            for k in kline_data:
                high = k['high']
                low = k['low']
                if low > 0:
                    ranges.append((high - low) / low * 100)
            avg_volatility = sum(ranges) / len(ranges)
            is_trending = False
            # [Reverted] 恢复默认趋势判断阈值，保持稳健
            if adx_value is not None and adx_value > 25:
                is_trending = True
            if avg_volatility > 0.5:
                return "HIGH_TREND" if is_trending else "HIGH_CHOPPY"
            elif avg_volatility < 0.1: 
                return "LOW"
            else:
                return "NORMAL"
        except Exception:
            return "NORMAL"

    async def get_avg_entry_price(self):
        try:
            pos = await self.get_current_position()
            if pos and pos.get('entry_price', 0) > 0:
                return pos['entry_price']
            trades = await self.exchange.fetch_my_trades(self.symbol, limit=100)
            if not trades: return 0.0
            for trade in reversed(trades):
                if trade['side'] == 'buy':
                    return float(trade['price'])
            return 0.0
        except Exception:
            return 0.0

    async def get_spot_balance(self):
        try:
            base_currency = self.symbol.split('/')[0]
            balance = await self.exchange.fetch_balance()
            if base_currency in balance:
                return float(balance[base_currency]['free'])
            elif 'info' in balance and 'data' in balance['info']:
                for asset in balance['info']['data'][0]['details']:
                    if asset['ccy'] == base_currency:
                        return float(asset['availBal'])
            return 0.0
        except Exception:
            return 0.0

    async def _send_diagnostic_report(self, trade_amount, min_limit, max_trade_limit, ai_suggest, config_amt, signal_data, current_price, reason_msg):
        """发送下单失败诊断报告"""
        report = [
            "⚠️ 下单失败诊断报告",
            "------------------",
            f"交易对: {self.symbol}",
            f"失败原因: {reason_msg}",
            f"尝试数量: {trade_amount}",
            f"最小限制: {min_limit}",
            "",
            "🔍 深度分析:",
            f"1. 账户能力: 最大可买 {max_trade_limit:.4f}",
            f"2. AI 建议: {ai_suggest}",
            f"3. 配置限制: {config_amt}",
            f"4. 信号方向: {signal_data['signal']}",
            f"5. 当前价格: {current_price}",
            "",
            "💡 建议排查:",
            "- 账户余额是否充足？",
            "- 是否已达到最大持仓配额？",
            "- 最小下单金额是否满足？"
        ]
        await self.send_notification("\n".join(report))

    async def execute_trade(self, signal_data):
        """执行交易 (Async - Enhanced Logic)"""
        
        # [Moved Up] 提前获取持仓信息，供信心过滤逻辑使用
        current_position = await self.get_current_position()

        # 1. 信心过滤
        confidence_levels = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
        current_conf_val = confidence_levels.get(signal_data.get('confidence', 'LOW').upper(), 1)
        min_conf_val = confidence_levels.get(self.min_confidence.upper(), 2)
        
        # [Fix] 如果是 SELL 信号（开空或平仓），且处于单边下跌趋势 (HIGH_TREND)，则放宽信心要求
        # 允许 LOW 信心执行，防止踏空暴跌
        is_strong_downtrend = False
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

        if current_conf_val < min_conf_val:
            self._log(f"✋ 信心不足: {signal_data.get('confidence')} < {self.min_confidence}, 强制观望")
            signal_data['signal'] = 'HOLD'
            return "SKIPPED_CONF", f"信心不足 {signal_data.get('confidence')}"

        if signal_data['signal'] == 'HOLD':
            return "HOLD", "AI建议观望"

        if self.test_mode:
            self._log(f"🧪 测试模式: {signal_data['signal']} {signal_data['amount']} (不执行)")
            return "TEST_MODE", f"模拟执行 {signal_data['signal']}"

        # 2. 价格滑点检查
        ticker = await self.exchange.fetch_ticker(self.symbol)
        current_realtime_price = ticker['last']
        try:
            analysis_price = (await self.get_ohlcv())['price']
            
            price_gap_percent = abs(current_realtime_price - analysis_price) / analysis_price * 100
            if price_gap_percent > self.max_slippage:
                self._log(f"⚠️ 价格波动过大: 偏差 {price_gap_percent:.2f}% > {self.max_slippage}%，取消交易", 'warning')
                await self.send_notification(
                    f"**价格滑点保护**\n当前偏差: `{price_gap_percent:.2f}%` (阈值: `{self.max_slippage}%`)", 
                    title=f"⚠️ 交易取消 | {self.symbol}"
                )
                return "SKIPPED_SLIPPAGE", f"滑点 {price_gap_percent:.2f}%"
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
        config_amt = self.amount
        
        # 获取余额
        balance = await self.get_account_balance()
        
        # [Fix] 计算基于配额的硬性资金上限 (USDT)
        # self.allocation 如果 <= 1 (如 0.5)，则是比例；如果 > 1，则是固定金额
        # self.initial_balance 是初始本金
        allocation_usdt_limit = 0
        if self.allocation <= 1.0:
            # 如果配置了初始本金，按本金比例计算；否则按当前余额比例
            base_capital = self.initial_balance if self.initial_balance > 0 else balance
            allocation_usdt_limit = base_capital * self.allocation
        else:
            allocation_usdt_limit = self.allocation
            
        # 扣除当前持仓占用的保证金（粗略估算），防止重复占用配额
        used_quota = 0
        margin_to_release = 0
        if current_position:
             # 持仓价值 / 杠杆 = 占用保证金
             used_quota = (current_position['size'] * current_realtime_price) / self.leverage
             
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
            # [Logic Change] 必须同时受限于 initial_balance (如果配置了)
            # 即: Global Limit = min(Real_Balance, Configured_Balance) * 0.9
            
            effective_balance = balance
            if self.initial_balance > 0:
                 effective_balance = min(balance, self.initial_balance)
            
            # 扣除当前持仓占用的保证金，计算剩余可用资金
            # 注意: 这里计算的是 "整个 Bot" 的剩余资金
            used_margin = 0
            if current_position:
                 used_margin = (current_position['size'] * current_realtime_price) / self.leverage
            
            # [Fix] 资金计算逻辑修正
            # 如果配置了 initial_balance，则 effective_balance 代表"总资金上限"，需要减去 used_margin 得到剩余可用
            # 如果没配置 (initial_balance=0)，effective_balance 就是交易所返回的 Free Balance (可用余额)，本身就不包含 used_margin
            if self.initial_balance > 0:
                 available_capital = max(0, effective_balance - used_margin)
            else:
                 available_capital = effective_balance

            # [Logic Fix] 如果是反手信号 (Flip)，预期会释放当前保证金
            # 否则如果满仓时反手，available_capital 接近 0，会导致无法开出新仓位
            is_potential_flip = False
            if current_position:
                if signal_data['signal'] == 'BUY' and current_position['side'] == 'short': is_potential_flip = True
                if signal_data['signal'] == 'SELL' and current_position['side'] == 'long': is_potential_flip = True
            
            if is_potential_flip:
                # 将当前保证金加回可用资金 (保守起见暂不计算未实现盈利)
                available_capital += used_margin
                self._log(f"🔄 检测到反手信号，预估释放保证金: {used_margin:.2f} U")
            
            # 计算物理最大可开仓数量 (Physical Max)
            max_physical_token = 0
            if self.trade_mode == 'cash':
                 max_physical_token = (available_capital * 0.90) / current_realtime_price
            else:
                 max_physical_token = (available_capital * self.leverage * 0.90) / current_realtime_price
            
            trade_amount = min(ai_suggest, max_physical_token)
            
            # 检查是否真的突破了配额
            current_quota_token = max_trade_limit # 上面计算的 max_trade_limit 是受配额限制的
            if trade_amount > current_quota_token:
                 self._log(f"🦁 激进模式 (信心高): 突破配额限制，调用闲置资金。下单: {trade_amount:.4f}")
        else:
            # 🦊 稳健模式: 严格受配额限制
            trade_amount = min(ai_suggest, config_amt, max_trade_limit)
        
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
            if self.trade_mode != 'cash':
                 market = self.exchange.market(self.symbol)
                 c_size = float(market.get('contractSize', 1.0))
                 if c_size > 0 and c_size != 1.0:
                      # [Fix] 确保合约张数是整数
                      final_order_amount = int(trade_amount / c_size)
                      # 如果计算出0张，但trade_amount>0，强制至少1张（将在后面最小数量检查中修正，这里先防0）
                      if final_order_amount == 0 and trade_amount > 0:
                          final_order_amount = 1
                      # self._log(f"💱 转换下单数量: {trade_amount} Coins -> {final_order_amount} Contracts")

            if signal_data['signal'] == 'BUY':
                if current_position and current_position['side'] == 'short':
                    # 平空 (使用持仓自带的 size，通常已经是张数)
                    await self.exchange.create_market_order(self.symbol, 'buy', current_position['size'], params={'reduceOnly': True})
                    self._log("🔄 平空仓成功")
                    await self.send_notification(f"🔄 平空仓成功 {self.symbol}\n数量: {current_position['size']}\n理由: {signal_data['reason']}")
                    await asyncio.sleep(1)
                
                # 开多/买入
                if trade_amount <= 0:
                     if current_position and current_position['side'] == 'short':
                         return "EXECUTED", "仅平空"
                     return "SKIPPED_ZERO", "计算数量为0"

                # [Safety] 同向开仓保护 (防止重复下单)
                # 策略调整：允许 HIGH 信心加仓
                if not is_closing and current_position and current_position['side'] == 'long':
                     if signal_data.get('confidence', '').upper() == 'HIGH':
                         # [Fix] 检查加仓数量是否为 0 (可能是没钱了)
                         if final_order_amount <= 0:
                             self._log(f"⚠️ 加仓失败: 余额不足或计算数量为0", 'warning')
                             return "SKIPPED_ZERO", "加仓无余额"
                         self._log(f"🔥 加仓模式: 已持有 Long，但信心 HIGH，允许加仓", 'info')
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
                         market = self.exchange.market(self.symbol)
                         contract_size = float(market.get('contractSize', 1.0))
                         if self.trade_mode == 'cash' or contract_size <= 0:
                             contract_size = 1.0

                         # 获取原始限制 (可能是张数，也可能是币数)
                         raw_min_amount = market.get('limits', {}).get('amount', {}).get('min')
                         raw_max_market = market.get('limits', {}).get('market', {}).get('max')
                         raw_max_amount = market.get('limits', {}).get('amount', {}).get('max')
                         
                         # 统一转换为 Coins 单位进行比较
                         min_amount_coins = raw_min_amount * contract_size if raw_min_amount else None
                         max_amount_coins = (raw_max_market if raw_max_market else raw_max_amount) * contract_size if (raw_max_market or raw_max_amount) else None
                         
                         min_cost = market.get('limits', {}).get('cost', {}).get('min')
                         
                         # [Modified] 如果是平仓操作 (is_closing=True)，跳过最小数量检查，防止尾仓无法平掉
                         # [Fix] 但是如果是合约反手 (trade_mode != cash)，即使是 is_closing 也需要检查，因为我们实际上是在开新仓
                         should_check_min = not is_closing or self.trade_mode != 'cash'
                         
                         if should_check_min:
                            if min_amount_coins and trade_amount < min_amount_coins:
                                if max_trade_limit >= min_amount_coins:
                                    self._log(f"⚠️ 数量 {trade_amount} < 最小限制 {min_amount_coins:.6f} (Coins)，自动提升")
                                    trade_amount = min_amount_coins
                                    # 重新计算 final_order_amount
                                    if self.trade_mode != 'cash':
                                        final_order_amount = int(trade_amount / contract_size)
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
                                    if self.trade_mode != 'cash':
                                        final_order_amount = int(trade_amount / contract_size)
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
                             if self.trade_mode != 'cash':
                                 final_order_amount = int(trade_amount / contract_size)

                     except Exception as e:
                         self._log(f"下单限制检查异常: {e}", 'warning')

                await self.exchange.create_market_order(self.symbol, 'buy', final_order_amount, params={'tdMode': self.trade_mode})
                self._log(f"🚀 买入成功: {trade_amount} Coins ({final_order_amount} 张)")
                
                msg = f"🚀 **买入执行 (BUY)**\n"
                msg += f"• 交易对: {self.symbol}\n"
                msg += f"• 数量: {trade_amount} 币 ({final_order_amount} 张)\n"
                msg += f"• 价格: ${current_realtime_price:,.2f}\n"
                msg += f"• 理由: {signal_data['reason']}\n"
                msg += f"• 信心: {signal_data.get('confidence', 'N/A')}"
                # [Fix] 飞书推送 Title 增强
                await self.send_notification(msg, title=f"🚀 买入执行 | {self.symbol}")
                return "EXECUTED", f"买入 {trade_amount}"

            elif signal_data['signal'] == 'SELL':
                if current_position and current_position['side'] == 'long':
                    # 平多
                    await self.exchange.create_market_order(self.symbol, 'sell', current_position['size'], params={'reduceOnly': True})
                    self._log("🔄 平多仓成功")
                    
                    msg = f"🔄 **平多仓 (Close Long)**\n"
                    msg += f"• 交易对: {self.symbol}\n"
                    msg += f"• 数量: {current_position['size']}\n"
                    msg += f"• 盈亏: {pnl_pct*100:+.2f}% (估算)\n"
                    msg += f"• 理由: {signal_data['reason']}"
                    # [Fix] 飞书推送 Title 增强
                    await self.send_notification(msg, title=f"🔄 平多仓 | {self.symbol}")
                    await asyncio.sleep(1)
                
                if self.trade_mode == 'cash':
                    # 现货卖出
                    if trade_amount <= 0: # 现货卖出如果没有数量，就无法执行
                         # 但如果前面已经通过 max_trade_limit 设置了全仓卖出，trade_amount 应该 > 0
                         # 除非余额为 0
                         return "SKIPPED_ZERO", "可卖数量为0"

                    # [New] 平仓时跳过最小金额检查 (在上面已经有 check，这里只是为了代码对齐)
                    # 现货的 is_closing=True 已经处理了

                    await self.exchange.create_market_order(self.symbol, 'sell', trade_amount)
                    self._log(f"📉 卖出成功: {trade_amount}")
                    
                    post_balance = await self.get_account_balance()
                    est_revenue = trade_amount * current_realtime_price
                    
                    msg = f"**数量**: `{trade_amount}`\n"
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
                             return "EXECUTED", "仅平多"
                         return "SKIPPED_ZERO", "计算数量为0"

                    # [Safety] 同向开仓保护 (防止重复下单)
                    # 策略调整：允许 HIGH 信心加仓
                    if not is_closing and current_position and current_position['side'] == 'short':
                         if signal_data.get('confidence', '').upper() == 'HIGH':
                             # [Fix] 检查加仓数量是否为 0
                             if final_order_amount <= 0:
                                 self._log(f"⚠️ 加仓失败: 余额不足或计算数量为0", 'warning')
                                 return "SKIPPED_ZERO", "加仓无余额"
                             self._log(f"🔥 加仓模式: 已持有 Short，但信心 HIGH，允许加仓", 'info')
                         else:
                             self._log(f"⚠️ 已持有 Short 仓位 ({current_position['size']})，跳过重复开仓 (信心非HIGH)", 'warning')
                             return "HOLD_DUP", "已持仓(防重)"

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
                         # 开仓检查最小数量
                         try:
                             market = self.exchange.market(self.symbol)
                             contract_size = float(market.get('contractSize', 1.0))
                             if self.trade_mode == 'cash' or contract_size <= 0:
                                 contract_size = 1.0

                             # 获取原始限制 (可能是张数，也可能是币数)
                             raw_min_amount = market.get('limits', {}).get('amount', {}).get('min')
                             raw_max_market = market.get('limits', {}).get('market', {}).get('max')
                             raw_max_amount = market.get('limits', {}).get('amount', {}).get('max')
                             
                             # 统一转换为 Coins 单位进行比较
                             min_amount_coins = raw_min_amount * contract_size if raw_min_amount else None
                             max_amount_coins = (raw_max_market if raw_max_market else raw_max_amount) * contract_size if (raw_max_market or raw_max_amount) else None
                             
                             min_cost = market.get('limits', {}).get('cost', {}).get('min')
                             
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
                                         self._log(f"⚠️ 数量 {trade_amount} < 最小限制 {min_amount_coins:.6f} (Coins)，自动提升")
                                         trade_amount = min_amount_coins
                                         # 重新计算 final_order_amount
                                         if self.trade_mode != 'cash':
                                             final_order_amount = int(trade_amount / contract_size)
                                     else:
                                         # [New] 如果是反手 (Flipping) 导致的余额计算不足，可能是因为平仓资金还没到账，
                                         # 或者计算 max_trade_limit 时用的是旧余额。
                                         # 我们尝试强制执行 (让交易所去判断)，而不是在这里拦截。
                                         if is_flipping:
                                              self._log(f"🔄 [反手保护] 余额计算可能滞后，强制尝试反手开空...", 'info')
                                              # 强制提升到最小数量
                                              trade_amount = min_amount_coins
                                              final_order_amount = int(trade_amount / contract_size)
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
                                         if self.trade_mode != 'cash':
                                             final_order_amount = int(trade_amount / contract_size)
                                     else:
                                         if is_flipping:
                                              self._log(f"🔄 [反手保护] 金额计算可能滞后，强制尝试反手开空...", 'info')
                                              trade_amount = req_amount
                                              final_order_amount = int(trade_amount / contract_size)
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
                                  if self.trade_mode != 'cash':
                                      final_order_amount = int(trade_amount / contract_size)

                         except Exception as e:
                             self._log(f"下单限制检查异常: {e}", 'warning')

                    await self.exchange.create_market_order(self.symbol, 'sell', final_order_amount, params={'tdMode': self.trade_mode})
                    self._log(f"📉 开空成功: {trade_amount} Coins ({final_order_amount} sz)")
                    
                    post_balance = await self.get_account_balance()
                    est_cost = trade_amount * current_realtime_price
                    
                    msg = f"**数量**: `{trade_amount}` Coins\n"
                    msg += f"**价格**: `${current_realtime_price:,.2f}`\n"
                    msg += f"**金额**: `{est_cost:.2f} U`\n"
                    msg += f"**余额**: `{post_balance:.2f} U` (Avail)\n"
                    msg += f"**信心**: `{signal_data.get('confidence', 'N/A')}`\n"
                    msg += f"> **理由**: {signal_data['reason']}"
                    
                    await self.send_notification(msg, title=f"📉 开空执行 | {self.symbol}")
                    return "EXECUTED", f"开空 {trade_amount}"

        except Exception as e:
            msg = str(e)
            if "51008" in msg or "Insufficient" in msg:
                self._log("❌ 保证金不足 (Code 51008)", 'error')
                return "FAILED", "保证金不足"
            else:
                self._log(f"下单失败: {e}", 'error')
                return "FAILED", f"API错误: {str(e)[:20]}"

        return "SKIPPED", "逻辑未覆盖"

    async def get_account_balance(self):
        try:
            params = {}
            if self.test_mode:
                params = {'simulated': True}
                
            balance = await self.exchange.fetch_balance(params)
            if 'USDT' in balance: return float(balance['USDT']['free'])
            # 统一账户
            if 'info' in balance and 'data' in balance['info']:
                for asset in balance['info']['data'][0]['details']:
                    if asset['ccy'] == 'USDT':
                        return float(asset['availBal'])
            return 0.0
        except: return 0.0

    async def get_account_equity(self):
        """获取账户总权益 (USDT)"""
        try:
            params = {}
            if self.test_mode:
                params = {'simulated': True}
            
            balance = await self.exchange.fetch_balance(params)
            
            # 1. 优先尝试统一账户 Total Equity
            if 'info' in balance and 'data' in balance['info']:
                data0 = balance['info']['data'][0]
                if 'totalEq' in data0:
                    return float(data0['totalEq'])
            
            # 2. 尝试经典账户 USDT Equity
            if 'USDT' in balance:
                if 'equity' in balance['USDT']: return float(balance['USDT']['equity'])
                if 'total' in balance['USDT']: return float(balance['USDT']['total'])
                
            return 0.0
        except Exception as e:
            self._log(f"获取账户权益失败: {e}", 'warning')
            return 0.0

    async def close_all_positions(self):
        try:
            pos = await self.get_current_position()
            if pos:
                side = 'buy' if pos['side'] == 'short' else 'sell'
                await self.exchange.create_market_order(self.symbol, side, pos['size'], params={'reduceOnly': True})
                self._log("平仓成功")
        except Exception as e:
            self._log(f"平仓失败: {e}", 'error')

    async def run_safety_check(self):
        """
        高频安全检查 (每 5秒 运行)
        仅检查止损/止盈，不进行复杂分析
        """
        try:
            # 1. 获取最新价格 (Ticker) - 速度快，消耗资源少
            ticker = await self.exchange.fetch_ticker(self.symbol)
            current_price = ticker['last']
            
            # 2. 获取持仓
            pos = await self.get_current_position()
            if not pos:
                return None # 空仓无需监控
                
            # 3. 计算 PnL
            pnl_pct = 0.0
            entry = pos['entry_price']
            if entry > 0:
                if pos['side'] == 'long':
                    pnl_pct = (current_price - entry) / entry
                elif pos['side'] == 'short':
                    pnl_pct = (entry - current_price) / entry
            
            # 4. 检查硬止损 (Hard Stop Loss) - [Fixed] 双向监控
            if self.risk_control.get('max_loss_rate'):
                max_loss = float(self.risk_control['max_loss_rate'])
                if pnl_pct <= -max_loss:
                    self._log(f"🚨 [WATCHDOG] 触发硬止损: 当前亏损 {pnl_pct*100:.2f}% (阈值 -{max_loss*100}%)", 'warning')
                    
                    # 构造一个伪造的 SELL 信号立即平仓
                    fake_signal = {
                        'signal': 'SELL' if pos['side'] == 'long' else 'BUY', # 这里的逻辑稍显混乱，execute_trade 中 SELL 涵盖了平多和开空
                        # 实际上 execute_trade 里：
                        # if signal == 'BUY' and pos.side == 'short' -> 平空
                        # if signal == 'SELL' and pos.side == 'long' -> 平多
                        # 所以这里我们需要根据持仓方向给反向信号
                        
                        # 但 wait，execute_trade 的逻辑是：
                        # BUY = 平空 + 开多
                        # SELL = 平多 + 开空
                        # 所以如果我是 Long，我要平仓，我应该发 SELL
                        # 如果我是 Short，我要平仓，我应该发 BUY
                        'signal': 'SELL' if pos['side'] == 'long' else 'BUY',
                        
                        'confidence': 'HIGH', # 强制最高信心
                        'amount': 0, # amount 0 在平仓逻辑中会被忽略，直接全平
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

    async def run(self):
        """Async 单次运行 - 返回结果给调用者进行统一打印"""
        # self._log(f"🚀 开始分析...")
        
        if not hasattr(self, 'last_fee_update_time'):
            await self._update_fee_rate()
            self.last_fee_update_time = time.time()
        
        price_data = await self.get_ohlcv()
        if not price_data: return None

        await self._update_amount_auto(price_data['price'])
        
        # Calculate volatility status
        ind = price_data.get('indicators', {})
        adx_val = ind.get('adx')
        volatility_status = self.get_market_volatility(price_data['kline_data'], adx_val)
        price_data['volatility_status'] = volatility_status
        
        # [Log Cleanup] 这里的日志移交给上层统一打印
        # icon = "🟢" if price_data['price_change'] > 0 else "🔴"
        # self._log(f"📊 当前价格: ${price_data['price']:,.2f} {icon} ({price_data['price_change']:+.2f}%)")

        # Call Agent
        current_pos = await self.get_current_position()
        balance = await self.get_account_balance()
        
        # [New] 获取账户总权益并计算 PnL
        current_pnl = 0.0
        if self.initial_balance > 0:
            equity = await self.get_account_equity()
            if equity > 0:
                current_pnl = equity - self.initial_balance

        # [New] 获取资金费率 (Funding Rate)
        funding_rate = 0.0
        try:
             # 仅合约模式需要获取资金费率
             if self.trade_mode != 'cash':
                 fr_data = await self.exchange.fetch_funding_rate(self.symbol)
                 if fr_data:
                     funding_rate = float(fr_data.get('fundingRate', 0))
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
            funding_rate # [New] 传入资金费率
        )
        
        if signal_data:
            # [Log Cleanup] 这里的日志移交给上层统一打印
            reason = signal_data.get('reason', '无理由')
            signal = signal_data.get('signal', 'UNKNOWN')
            confidence = signal_data.get('confidence', 'LOW')
            
            # icon = "🤔"
            # if signal == 'BUY': icon = "🟢"
            # elif signal == 'SELL': icon = "🔴"
            # elif signal == 'HOLD': icon = "✋"
            
            # self._log(f"{icon} AI决策: {signal} ({confidence}) | 理由: {reason}")
            
            exec_status, exec_msg = "UNKNOWN", ""
            try:
                result = await self.execute_trade(signal_data)
                if isinstance(result, tuple) and len(result) == 2:
                    exec_status, exec_msg = result
                elif result is None:
                    # execute_trade might return None if it just returned without value in some paths (legacy)
                    # But we covered all paths now
                    pass
            except Exception as e:
                exec_status = "ERROR"
                exec_msg = str(e)

            # 返回结构化结果给上层打印表格
            return {
                'symbol': self.symbol,
                'price': price_data['price'],
                'change': price_data['price_change'],
                'signal': signal,
                'confidence': confidence,
                'reason': reason,
                'summary': signal_data.get('summary', ''),
                'status': exec_status,
                'status_msg': exec_msg
            }
        return None
