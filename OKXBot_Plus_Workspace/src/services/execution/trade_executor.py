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

    def _log(self, msg, level='info'):
        if level == 'info':
            self.logger.info(f"[{self.symbol}] {msg}")
        elif level == 'error':
            self.logger.error(f"[{self.symbol}] {msg}")
        elif level == 'warning':
            self.logger.warning(f"[{self.symbol}] {msg}")

    async def send_notification(self, message):
        if not self.notification_config.get('enabled', False):
            return
        webhook_url = self.notification_config.get('webhook_url')
        
        full_msg = f"🤖 CryptoOracle 通知 [{self.symbol}]\n--------------------\n{message}"
        await send_notification_async(webhook_url, full_msg)

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
                self._log(f"⚠️ 计算数量 {raw_amount} < 最小限额 {limit_floor}，尝试自动修正", 'warning')
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
            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, api_timeframe, limit=100)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

            # 维护 100 根 K 线的历史记录
            self.price_history = df.tail(100).to_dict('records')

            if not self.price_history and len(df) > self.history_limit:
                self._log(f"🔥 正在预热历史数据...")
                # 这一段逻辑似乎有些冗余，因为上面已经更新了 self.price_history
                # 但为了兼容可能的旧逻辑，我们保留它，或者考虑移除
                pass

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
            }

            return {
                'price': current_data['close'],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'high': current_data['high'],
                'low': current_data['low'],
                'volume': current_data['volume'],
                'timeframe': self.timeframe,
                'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
                'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(15).to_dict('records'),
                'indicators': indicators
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
                        return {
                            'side': pos['side'],
                            'size': contracts,
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
        
        # 1. 信心过滤
        confidence_levels = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
        current_conf_val = confidence_levels.get(signal_data.get('confidence', 'LOW').upper(), 1)
        min_conf_val = confidence_levels.get(self.min_confidence.upper(), 2)
        
        if current_conf_val < min_conf_val:
            self._log(f"✋ 信心不足: {signal_data.get('confidence')} < {self.min_confidence}, 强制观望")
            signal_data['signal'] = 'HOLD'

        if signal_data['signal'] == 'HOLD':
            return

        if self.test_mode:
            self._log(f"🧪 测试模式: {signal_data['signal']} {signal_data['amount']} (不执行)")
            return

        current_position = await self.get_current_position()
        
        # 2. 价格滑点检查
        ticker = await self.exchange.fetch_ticker(self.symbol)
        current_realtime_price = ticker['last']
        try:
            analysis_price = (await self.get_ohlcv())['price']
            
            price_gap_percent = abs(current_realtime_price - analysis_price) / analysis_price * 100
            if price_gap_percent > self.max_slippage:
                self._log(f"⚠️ 价格波动过大: 偏差 {price_gap_percent:.2f}% > {self.max_slippage}%，取消交易", 'warning')
                await self.send_notification(f"⚠️ 交易取消: 价格滑点保护\n偏差 {price_gap_percent:.2f}%")
                return
        except Exception:
            pass

        # 3. 卖出微利风控
        if signal_data['signal'] == 'SELL' and current_position:
            pnl_pct = 0
            entry = current_position['entry_price']
            if entry > 0:
                if current_position['side'] == 'long':
                    pnl_pct = (current_realtime_price - entry) / entry
                else:
                    pnl_pct = (entry - current_realtime_price) / entry
            
            min_profit_threshold = (self.taker_fee_rate * 2) + 0.0005
            if 0 <= pnl_pct < min_profit_threshold:
                self._log(f"🛑 拦截微利平仓: 浮盈 {pnl_pct*100:.3f}% < {min_profit_threshold*100:.3f}% (手续费覆盖线)", 'warning')
                return

        # 4. 资金三方取小 & 最小数量适配
        ai_suggest = signal_data['amount']
        config_amt = self.amount
        
        # 获取余额
        balance = await self.get_account_balance()
        max_trade_limit = 0
        if signal_data['signal'] == 'BUY':
             if self.trade_mode == 'cash':
                 max_trade_limit = (balance * 0.99) / current_realtime_price
             else:
                 max_trade_limit = (balance * self.leverage * 0.99) / current_realtime_price
        elif signal_data['signal'] == 'SELL':
             if self.trade_mode == 'cash':
                 max_trade_limit = await self.get_spot_balance()
             else:
                 # 开空能力
                 max_trade_limit = (balance * self.leverage * 0.99) / current_realtime_price

        # 决策最终数量
        # [High Confidence Override]
        if signal_data.get('confidence', '').upper() == 'HIGH':
            trade_amount = min(ai_suggest, max_trade_limit)
            self._log(f"🦁 激进模式 (信心高): 忽略配置限制 {config_amt}，跟随 AI 建议 {ai_suggest}")
        else:
            trade_amount = min(ai_suggest, config_amt, max_trade_limit)
        
        # 如果是平仓(SELL现有持仓)，则直接用持仓量，不受配额限制
        is_closing = False
        if signal_data['signal'] == 'SELL':
            if self.trade_mode == 'cash':
                # 现货卖出就是平仓
                is_closing = True
                trade_amount = max_trade_limit # All out
            elif current_position and current_position['side'] == 'long':
                # 合约平多
                is_closing = True
                trade_amount = current_position['size']
        
        if not is_closing:
             # 开仓检查最小数量
             try:
                 market = self.exchange.market(self.symbol)
                 min_amount = market.get('limits', {}).get('amount', {}).get('min')
                 min_cost = market.get('limits', {}).get('cost', {}).get('min')
                 
                 if min_amount and trade_amount < min_amount:
                     if max_trade_limit >= min_amount:
                         self._log(f"⚠️ 数量 {trade_amount} < 最小限制 {min_amount}，自动提升")
                         trade_amount = min_amount
                     else:
                         self._log(f"🚫 余额不足最小单位 {min_amount}", 'warning')
                         await self._send_diagnostic_report(trade_amount, min_amount, max_trade_limit, ai_suggest, config_amt, signal_data, current_realtime_price, "余额不足以购买最小单位")
                         return

                 if min_cost and (trade_amount * current_realtime_price) < min_cost:
                      # 尝试提升
                      req_amount = (min_cost / current_realtime_price) * 1.05
                      if max_trade_limit >= req_amount:
                           self._log(f"⚠️ 金额不足最小限制 {min_cost}U，自动提升数量至 {req_amount}")
                           trade_amount = req_amount
                      else:
                           self._log(f"🚫 余额不足最小金额 {min_cost}U", 'warning')
                           await self._send_diagnostic_report(trade_amount, min_cost, max_trade_limit, ai_suggest, config_amt, signal_data, current_realtime_price, f"余额不足最小金额 (需 {min_cost}U)")
                           return
             except Exception:
                 pass


        # 精度处理
        try:
            precise_amount = self.exchange.amount_to_precision(self.symbol, trade_amount)
            trade_amount = float(precise_amount)
        except:
            pass
            
        if trade_amount <= 0: return

        # 5. 执行
        try:
            if signal_data['signal'] == 'BUY':
                if current_position and current_position['side'] == 'short':
                    # 平空
                    await self.exchange.create_market_order(self.symbol, 'buy', current_position['size'], params={'reduceOnly': True})
                    self._log("🔄 平空仓成功")
                    await asyncio.sleep(1)
                
                # 开多/买入
                await self.exchange.create_market_order(self.symbol, 'buy', trade_amount, params={'tdMode': self.trade_mode})
                self._log(f"🚀 买入成功: {trade_amount}")
                await self.send_notification(f"🚀 买入 {self.symbol} {trade_amount}\n理由: {signal_data['reason']}")

            elif signal_data['signal'] == 'SELL':
                if current_position and current_position['side'] == 'long':
                    # 平多
                    await self.exchange.create_market_order(self.symbol, 'sell', current_position['size'], params={'reduceOnly': True})
                    self._log("🔄 平多仓成功")
                    await asyncio.sleep(1)
                elif self.trade_mode == 'cash':
                    # 现货卖出
                    await self.exchange.create_market_order(self.symbol, 'sell', trade_amount)
                    self._log(f"📉 卖出成功: {trade_amount}")
                    await self.send_notification(f"📉 卖出 {self.symbol} {trade_amount}\n理由: {signal_data['reason']}")
                else:
                    # 开空
                    await self.exchange.create_market_order(self.symbol, 'sell', trade_amount, params={'tdMode': self.trade_mode})
                    self._log(f"📉 开空成功: {trade_amount}")
                    await self.send_notification(f"📉 开空 {self.symbol} {trade_amount}\n理由: {signal_data['reason']}")

        except Exception as e:
            msg = str(e)
            if "51008" in msg or "Insufficient" in msg:
                self._log("❌ 保证金不足 (Code 51008)", 'error')
            else:
                self._log(f"下单失败: {e}", 'error')

    async def get_account_balance(self):
        try:
            balance = await self.exchange.fetch_balance()
            if 'USDT' in balance: return float(balance['USDT']['free'])
            # 统一账户
            if 'info' in balance and 'data' in balance['info']:
                for asset in balance['info']['data'][0]['details']:
                    if asset['ccy'] == 'USDT':
                        return float(asset['availBal'])
            return 0.0
        except: return 0.0

    async def close_all_positions(self):
        try:
            pos = await self.get_current_position()
            if pos:
                side = 'buy' if pos['side'] == 'short' else 'sell'
                await self.exchange.create_market_order(self.symbol, side, pos['size'], params={'reduceOnly': True})
                self._log("平仓成功")
        except Exception as e:
            self._log(f"平仓失败: {e}", 'error')

    async def run(self):
        """Async 单次运行"""
        self._log(f"🚀 开始分析...")
        
        if not hasattr(self, 'last_fee_update_time'):
            await self._update_fee_rate()
            self.last_fee_update_time = time.time()
        
        price_data = await self.get_ohlcv()
        if not price_data: return

        await self._update_amount_auto(price_data['price'])
        
        # Calculate volatility status
        ind = price_data.get('indicators', {})
        adx_val = ind.get('adx')
        volatility_status = self.get_market_volatility(price_data['kline_data'], adx_val)
        price_data['volatility_status'] = volatility_status
        
        arrow = "🟢" if price_data['price_change'] > 0 else "🔴"
        self._log(f"📊 价格: ${price_data['price']:,.2f} {arrow} ({price_data['price_change']:+.2f}%)")

        # Call Agent
        current_pos = await self.get_current_position()
        balance = await self.get_account_balance()
        
        signal_data = await self.agent.analyze(
            self.symbol, 
            self.timeframe, 
            price_data, 
            current_pos, 
            balance, 
            self.amount,
            self.taker_fee_rate
        )
        
        if signal_data:
            # 打印 AI 思考结果，让用户能看到
            reason = signal_data.get('reason', '无理由')
            signal = signal_data.get('signal', 'UNKNOWN')
            confidence = signal_data.get('confidence', 'LOW')
            
            icon = "🤔"
            if signal == 'BUY': icon = "🟢"
            elif signal == 'SELL': icon = "🔴"
            elif signal == 'HOLD': icon = "✋"
            
            self._log(f"{icon} AI决策: {signal} ({confidence}) | 理由: {reason}")
            
            await self.execute_trade(signal_data)
