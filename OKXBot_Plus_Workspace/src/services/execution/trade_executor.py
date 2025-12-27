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
        self.enable_aggressive_mode = common_config.get('enable_aggressive_mode', True)
        
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
            # [Fix] 自动模式下也需要获取权益来计算动态配额
            equity = 0
            if self.initial_balance > 0:
                equity = self.initial_balance
            else:
                equity = await self.get_account_equity()

            quota = 0
            if self.allocation <= 1.0:
                quota = equity * self.allocation
            else:
                quota = self.allocation
            
            if quota <= 0:
                target_usdt = 10.0
            else:
                # [Logic Change] 自动模式下，每次只使用 allocation 的 60%
                # 这样即使 allocation 配置为 0.5 (50%)，实际单次只用 30%
                # 留出 40% (即总资金的 20%) 给补仓或安全垫
                target_usdt = quota * 0.6
            
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
            # [Safety] 尝试获取当前持仓/杠杆信息，告知用户实际运行的杠杆
            try:
                positions = await self.exchange.fetch_positions([self.symbol])
                for pos in positions:
                    if pos['symbol'] == self.symbol:
                        actual_lev = pos.get('leverage', 'Unknown')
                        self._log(f"⚠️ 当前实际运行杠杆: {actual_lev}x", 'warning')
            except:
                pass

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
            }
            
            # 显式传递最小交易单位给 AI
            min_limit_info = "0.01"
            try:
                market = self.exchange.market(self.symbol)
                min_amount = market.get('limits', {}).get('amount', {}).get('min')
                if min_amount:
                    min_limit_info = str(min_amount)
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
                'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(feed_limit).to_dict('records'),
                'indicators': indicators,
                'min_limit_info': min_limit_info
            }
        except Exception as e:
            self._log(f"获取K线数据失败: {e}", 'error')
            return None

    async def get_current_position(self):
        try:
            positions = await self.exchange.fetch_positions([self.symbol])
            
            # [Fix] 获取 ContractSize 以便后续计算准确的持仓价值
            contract_size = 1.0
            if self.trade_mode != 'cash':
                try:
                    market = self.exchange.market(self.symbol)
                    contract_size = float(market.get('contractSize', 1.0))
                    if contract_size <= 0: contract_size = 1.0
                except:
                    pass

            for pos in positions:
                if pos['symbol'] == self.symbol:
                    contracts = float(pos['contracts']) if pos['contracts'] else 0
                    if contracts > 0:
                        return {
                            'side': pos['side'],
                            'size': contracts, # 张数
                            'contract_size': contract_size, # 单张大小
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

    async def _wait_for_margin_release(self, old_balance, timeout=2.0):
        """[Smart Wait] 智能轮询等待保证金释放，比固定死等更高效"""
        start_time = time.time()
        attempt = 0
        while (time.time() - start_time) < timeout:
            attempt += 1
            await asyncio.sleep(0.2) # 200ms 轮询间隔
            new_balance = await self.get_account_balance()
            
            # 判定标准: 余额显著增加 (释放了保证金)
            # 如果旧余额接近0，只要新余额大于10U就算释放成功
            # 如果旧余额不为0，要求增加一定比例
            diff = new_balance - old_balance
            if diff > 10.0 or (old_balance > 0 and new_balance / old_balance > 1.1):
                self._log(f"⚡ 保证金快速释放! 耗时: {(time.time() - start_time)*1000:.0f}ms | 余额: {old_balance:.2f} -> {new_balance:.2f}")
                return new_balance
        
        self._log(f"⚠️ 等待保证金超时 ({timeout}s)，继续尝试...", 'warning')
        return await self.get_account_balance()

    async def execute_trade(self, signal_data):
        """执行交易 (Async - Enhanced Logic)"""
        
        # [Moved Up] 提前获取持仓信息，供信心过滤逻辑使用
        current_position = await self.get_current_position()

        # 1. 信心过滤
        confidence_levels = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
        current_conf_val = confidence_levels.get(signal_data.get('confidence', 'LOW').upper(), 1)
        min_conf_val = confidence_levels.get(self.min_confidence.upper(), 2)
        
        # [Strict Confidence Enforcement] 严格信心执行
        # 移除了所有对 "SELL" 信号的信心豁免逻辑
        # 即使是止损或趋势做空，也必须满足最低信心要求
        # 理由: 减少无效的恐慌性止损和频繁的趋势试错
        
        # [Enhancement] 对做空 (Open Short) 信号的额外保护
        # 如果是 开空 (SELL且无持仓)，建议进一步提高信心门槛，因为做空风险通常大于做多
        # 但目前为了保持逻辑统一，暂时与做多保持一致，只依赖 min_confidence
        
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
        except Exception:
            pass

        # 3. 卖出微利风控 (仅针对平仓/减仓场景)
        # 如果 AI 信心为 HIGH，则认为是紧急离场，跳过此检查
        is_high_confidence = signal_data.get('confidence', '').upper() == 'HIGH'
        if signal_data['signal'] == 'SELL' and current_position and not is_high_confidence:
            pnl_pct = 0
            entry = current_position['entry_price']
            if entry > 0:
                if current_position['side'] == 'long':
                    pnl_pct = (current_realtime_price - entry) / entry
                else:
                    pnl_pct = (entry - current_realtime_price) / entry
            
            # [Logic Enhancement] 动态调整最小利润阈值
            # 1. 基础门槛: 双倍手续费 + 滑点
            base_threshold = (self.taker_fee_rate * 2) + 0.0005
            
            # 2. 波动率惩罚: 如果市场波动小 (ADX低)，则要求更高利润才平仓，避免被噪音洗出去
            # 如果市场波动大，可以跑得快一点
            min_profit_threshold = base_threshold
            
            # [Anti-Churn] 防止频繁小额止盈磨损本金
            # 只有当浮盈显著大于手续费时才允许平仓
            # 除非是止损 (pnl < 0)
            
            if 0 <= pnl_pct < min_profit_threshold:
                # 增加对 "盘整期" 的判断，如果是盘整期，更要拿住
                # 这里简单处理: 直接拦截微利平仓
                self._log(f"🛑 拦截微利平仓: 浮盈 {pnl_pct*100:.3f}% < {min_profit_threshold*100:.3f}% (AI信心非HIGH)", 'warning')
                return "SKIPPED_PROFIT", f"微利拦截 {pnl_pct*100:.2f}%"

        # [Added] 频繁交易风控: 开仓冷却
        # 如果最近一笔交易是在 N 分钟内，且当前信号不是 HIGH 信心，则拦截
        # 防止 AI 在短时间内反复横跳 (Whipsaw)
        # [Logic Refined] 即便是 HIGH 信心，如果间隔极短 (如 < 30s)，也可能是 AI 抽风
        # 所以我们设置一个 "绝对冷却期" (15s) 和 "普通冷却期" (60s)
        if hasattr(self, 'last_trade_time') and self.last_trade_time:
            time_since_last = time.time() - self.last_trade_time
            
            # 绝对冷却: 无论信心多高，必须等待 15s (防止程序错误连续下单)
            if time_since_last < 15:
                 self._log(f"⏳ 绝对冷却中: 距上次交易仅 {time_since_last:.0f}s < 15s", 'warning')
                 return "SKIPPED_COOL", f"冷却中 {time_since_last:.0f}s"
            
            # 普通冷却: 如果信心不足 HIGH，需等待 60s
            cooldown = 60 
            if time_since_last < cooldown and not is_high_confidence:
                 self._log(f"⏳ 交易冷却中: 距上次交易仅 {time_since_last:.0f}s < {cooldown}s (非HIGH)", 'warning')
                 return "SKIPPED_COOL", f"冷却中 {time_since_last:.0f}s"


        # 4. 资金三方取小 & 最小数量适配
        ai_suggest = signal_data['amount']
        config_amt = self.amount
        
        # 获取余额
        balance = await self.get_account_balance()
        equity = await self.get_account_equity()
        
        # [Fix] 计算基于配额的硬性资金上限 (USDT)
        # self.allocation 如果 <= 1 (如 0.5)，则是比例；如果 > 1，则是固定金额
        # self.initial_balance 是初始本金
        allocation_usdt_limit = 0
        if self.allocation <= 1.0:
            # 如果配置了初始本金，按本金比例计算；否则按当前权益比例 (Fix: Use Equity not Balance for Auto-Fund)
            # 解决 "资金自动模式下，随着余额减少，配额不断缩水" 的死循环问题
            base_capital = self.initial_balance if self.initial_balance > 0 else equity
            allocation_usdt_limit = base_capital * self.allocation
        else:
            allocation_usdt_limit = self.allocation
            
        # 扣除当前持仓占用的保证金（粗略估算），防止重复占用配额
        used_quota = 0
        if current_position:
             # 持仓价值 / 杠杆 = 占用保证金
             # [Fix] 必须考虑 contract_size
             c_size = current_position.get('contract_size', 1.0)
             used_quota = (current_position['size'] * c_size * current_realtime_price) / self.leverage
        
        remaining_quota = max(0, allocation_usdt_limit - used_quota)
        
        # 将剩余配额转换为币的数量
        quota_token_amount = (remaining_quota * self.leverage * 0.99) / current_realtime_price

        max_trade_limit = 0
        if signal_data['signal'] == 'BUY':
             if self.trade_mode == 'cash':
                 # 现货: 取 (余额, 配额) 的较小值
                 available_usdt = min(balance, remaining_quota)
                 max_trade_limit = (available_usdt * 0.99) / current_realtime_price
             else:
                 # 合约: 取 (余额, 配额) 的较小值作为保证金
                 available_margin = min(balance, remaining_quota)
                 max_trade_limit = (available_margin * self.leverage * 0.99) / current_realtime_price
        elif signal_data['signal'] == 'SELL':
             if self.trade_mode == 'cash':
                 max_trade_limit = await self.get_spot_balance()
             elif current_position and current_position['side'] == 'long':
                 # [Fix] 平多仓逻辑: 最大可卖数量 = 持仓数量
                 # 只要有持仓，就不受 USDT 配额限制 (防止因配额耗尽无法止损)
                 max_trade_limit = current_position['size']
             else:
                 # 开空能力: 同理，受配额限制
                 available_margin = min(balance, remaining_quota)
                 max_trade_limit = (available_margin * self.leverage * 0.99) / current_realtime_price

        # 决策最终数量
        # [High Confidence Override] -> 弹性配额逻辑
        if signal_data.get('confidence', '').upper() == 'HIGH':
            # 🦁 激进模式: 允许突破单币种配额，调用账户闲置资金
            # 限制：最多使用账户余额的 90% (保留 10% 作为安全垫/其他币种救急)
            # [Logic Change] 必须同时受限于 initial_balance (如果配置了)
            # 即: Global Limit = min(Equity, Configured_Balance) * safety_buffer
            
            effective_balance = equity # Use Equity as base
            if self.initial_balance > 0:
                 effective_balance = min(equity, self.initial_balance)
            
            # [Dynamic Safety Buffer] 动态安全缓冲
            # 1. 现货 或 低倍合约 (<= 5x): 风险低 -> 缓冲 5% (系数 0.95)
            # 2. 高倍合约 (> 5x): 风险高 -> 缓冲 10% (系数 0.90)
            safety_buffer = 0.95
            if self.trade_mode != 'cash' and self.leverage > 5:
                safety_buffer = 0.90
            
            # Global limit is based on TOTAL equity, but we can only spend AVAILABLE balance
            # So max_spendable = min(balance, effective_balance * safety_buffer)
            # Actually, effective_balance * safety_buffer is the TARGET exposure cap.
            # We want to know how much MORE we can add.
            # But simplify: just use available balance with safety buffer
            
            global_max_usdt = balance * safety_buffer # Available balance is the hard limit for new trades
            global_max_token = 0
            if self.trade_mode == 'cash':
                 global_max_token = global_max_usdt / current_realtime_price
            else:
                 global_max_token = (global_max_usdt * self.leverage) / current_realtime_price
            
            trade_amount = min(ai_suggest, global_max_token)
            
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
        
        if not is_closing:
             # 开仓检查最小数量
             try:
                 market = self.exchange.market(self.symbol)
                 min_amount = market.get('limits', {}).get('amount', {}).get('min')
                 min_cost = market.get('limits', {}).get('cost', {}).get('min')
                 
                 # [Fix] 统一单位: min_amount 通常是合约张数 (Contracts)，需转换为币数 (Coins) 进行比较
                 # 否则会导致 Coins < Contracts (如 50 < 1) 的逻辑错误
                 contract_size = 1.0
                 if self.trade_mode != 'cash':
                     contract_size = float(market.get('contractSize', 1.0))
                 
                 min_amount_coins = min_amount
                 if self.trade_mode != 'cash' and min_amount:
                     min_amount_coins = min_amount * contract_size
                 
                 if min_amount_coins and trade_amount < min_amount_coins:
                     if max_trade_limit >= min_amount_coins:
                         self._log(f"⚠️ 数量 {trade_amount} < 最小限制 {min_amount_coins} (Coins)，自动提升")
                         trade_amount = min_amount_coins
                     else:
                         self._log(f"🚫 余额不足最小单位 {min_amount_coins} (Coins)", 'warning')
                         await self._send_diagnostic_report(trade_amount, min_amount_coins, max_trade_limit, ai_suggest, config_amt, signal_data, current_realtime_price, "余额不足以购买最小单位")
                         return "SKIPPED_MIN", f"少于最小限额 {min_amount_coins}"

                 if min_cost and (trade_amount * current_realtime_price) < min_cost:
                      # 尝试提升
                      req_amount = (min_cost / current_realtime_price) * 1.05
                      if max_trade_limit >= req_amount:
                           self._log(f"⚠️ 金额不足最小限制 {min_cost}U，自动提升数量至 {req_amount}")
                           trade_amount = req_amount
                      else:
                           self._log(f"🚫 余额不足最小金额 {min_cost}U", 'warning')
                           await self._send_diagnostic_report(trade_amount, min_cost, max_trade_limit, ai_suggest, config_amt, signal_data, current_realtime_price, f"余额不足最小金额 (需 {min_cost}U)")
                           return "SKIPPED_MIN", f"金额 < {min_cost}U"

                 # [Fix] 超出最大下单数量限制 (Code 51202)
                 # 优先检查 limits.market.max (OKX 市价单专属限制)，其次检查 limits.amount.max
                 max_market_amount = market.get('limits', {}).get('market', {}).get('max')
                 max_amount = market.get('limits', {}).get('amount', {}).get('max')
                 effective_max = max_market_amount if max_market_amount else max_amount
                 
                 if effective_max and trade_amount > effective_max:
                      self._log(f"⚠️ 数量 {trade_amount} > 市场最大限制 {effective_max}，自动截断")
                      trade_amount = effective_max

             except Exception:
                 pass

        # [Debug] 详细记录下单参数，排查 "Exceeds maximum" 问题
        contract_size = 1.0
        try:
            market = self.exchange.market(self.symbol)
            contract_size = float(market.get('contractSize', 1.0))
            if contract_size <= 0: contract_size = 1.0
            
            # 估算下单价值
            est_value = trade_amount * current_realtime_price
            
            log_msg = f"🔍 下单预检: {self.symbol} | 模式: {self.trade_mode} | "
            log_msg += f"数量(Coins): {trade_amount} | 价格: {current_realtime_price} | "
            log_msg += f"估算价值: {est_value:.2f} U | ContractSize: {contract_size}"
            
            if self.trade_mode != 'cash':
                num_contracts = trade_amount / contract_size
                log_msg += f" | 换算张数: {num_contracts:.4f}"
                
                # [Safety Check] 如果估算价值远超配额 (例如 > 2倍)，说明可能单位搞错了 (Coins vs Contracts)
                # 只有当张数 > 10 且 价值异常大时才拦截，防止误判
                if est_value > (config_amt * 5) and est_value > 100:
                    self._log(log_msg, 'warning')
                    self._log(f"🛑 异常拦截: 估算价值 {est_value:.2f}U 远超配置 {config_amt}U，可能是合约单位换算错误", 'error')
                    return "SKIPPED_SAFETY", f"单位异常 Val:{est_value:.0f}U"
            
            self._log(log_msg)
            
        except Exception as e:
            self._log(f"预检异常: {e}")


        # 精度处理
        try:
            precise_amount = self.exchange.amount_to_precision(self.symbol, trade_amount)
            trade_amount = float(precise_amount)
        except:
            pass
            
        if trade_amount <= 0:
             return "SKIPPED_ZERO", "计算数量为0"

        # [Unit Conversion Fix] 合约模式下，将币数转换为张数 (Contracts)
        # 必须在下单前进行，否则会因数量放大 ContractSize 倍而导致保证金不足 (Code 51008)
        final_order_amount = trade_amount
        contract_size = 1.0
        
        if self.trade_mode != 'cash':
            try:
                market = self.exchange.market(self.symbol)
                contract_size = float(market.get('contractSize', 1.0))
                if contract_size <= 0: contract_size = 1.0
                
                # 转换为张数 (向下取整，保证不超额)
                # OKX 合约通常要求整数张
                num_contracts = int(trade_amount / contract_size)
                
                if num_contracts < 1:
                    # 如果算出来连一张都买不起，但之前的金额检查通过了，说明是单位问题
                    # 尝试至少买1张 (如果资金允许)
                    # 但为了安全，这里先拦截
                    self._log(f"⚠️ 数量 {trade_amount:.4f} 币不足 1 张合约 (Sz:{contract_size})", 'warning')
                    return "SKIPPED_MIN", "不足1张合约"
                
                final_order_amount = float(num_contracts)
                
                # 记录转换日志
                self._log(f"🔄 单位换算: {trade_amount:.4f} 币 -> {final_order_amount} 张 (Sz:{contract_size})")
                
                # 更新 trade_amount 为实际成交的币数，以便后续日志计算金额准确
                trade_amount = final_order_amount * contract_size
                
            except Exception as e:
                self._log(f"单位换算失败: {e}", 'error')
                return "FAILED", "单位换算失败"

        # 5. 执行
        try:
            if signal_data['signal'] == 'BUY':
                if current_position and current_position['side'] == 'short':
                    # 平空 (使用持仓自带的 size，已经是张数)
                    await self.exchange.create_market_order(self.symbol, 'buy', current_position['size'], params={'reduceOnly': True})
                    self._log("🔄 平空仓成功")
                    
                    unit_str = "张 (Cont)" if self.trade_mode != 'cash' else f"{self.symbol.split('/')[0]}"
                    await self.send_notification(
                        f"**数量**: `{current_position['size']} {unit_str}`\n> **理由**: {signal_data['reason']}",
                        title=f"🔄 平空仓成功 | {self.symbol}"
                    )
                    
                    # [Smart Wait] 智能等待保证金释放，替代死等 2s
                    # 记录旧余额以便对比
                    old_balance_check = balance # 这里用之前的 balance 变量
                    if old_balance_check <= 0: old_balance_check = 0.1 # 防止除0
                    
                    balance = await self._wait_for_margin_release(balance, timeout=2.0)
                    
                    # [Fix Flip Logic] 平仓后，保证金已释放，需要重新获取最新的余额和配额
                    # 否则后续开仓会使用旧的(较小的)余额，导致"余额不足"
                    # balance 已由 _wait_for_margin_release 更新
                    equity = await self.get_account_equity() # 虽然 Equity 不变，但 Balance 变了
                    
                    # 重新计算配额 (简化版，直接复用 allocation_usdt_limit)
                    # 因为 allocation_usdt_limit 基于 initial_balance 或 equity，这两者变化不大
                    # 但 remaining_quota 需要减去 used_quota (此时为0)，所以 remaining_quota = allocation_usdt_limit
                    remaining_quota = allocation_usdt_limit 
                    
                    if self.trade_mode == 'cash':
                        available_usdt = min(balance, remaining_quota)
                        max_trade_limit = (available_usdt * 0.99) / current_realtime_price
                    else:
                        available_margin = min(balance, remaining_quota)
                        max_trade_limit = (available_margin * self.leverage * 0.99) / current_realtime_price
                    
                    # 重新应用稳健模式限制 (AI建议 vs 配额)
                    if signal_data.get('confidence', '').upper() == 'HIGH':
                         # Recalculate aggressive limit
                         global_max_usdt = balance * safety_buffer 
                         global_max_token = (global_max_usdt * self.leverage) / current_realtime_price
                         trade_amount = min(ai_suggest, global_max_token)
                    else:
                         trade_amount = min(ai_suggest, config_amt, max_trade_limit)

                    # 重新进行最小数量检查 (因为 trade_amount 变了)
                    # 这里简单处理: 如果太小就放弃开仓，或者依赖后续的 checks
                    # 由于我们已经到了 execution 阶段，最好是直接更新 final_order_amount
                    
                    # 重新计算 contracts
                    num_contracts = int(trade_amount / contract_size)
                    final_order_amount = float(num_contracts)
                    trade_amount = final_order_amount * contract_size
                    
                    if num_contracts < 1:
                        self._log(f"⚠️ 反手开仓资金不足 (Min 1 Cont)，仅平仓", 'warning')
                        return "PARTIAL", "平空成功，反手资金不足"
                
                # 开多/买入 (使用转换后的 final_order_amount)
                await self.exchange.create_market_order(self.symbol, 'buy', final_order_amount, params={'tdMode': self.trade_mode})
                
                unit_str = "张 (Cont)" if self.trade_mode != 'cash' else f"{self.symbol.split('/')[0]}"
                self._log(f"🚀 买入成功: {final_order_amount} {unit_str} (= {trade_amount} Coins)")
                
                # [Fix] 获取最新余额和估算花费
                post_balance = await self.get_account_balance()
                est_cost = trade_amount * current_realtime_price # trade_amount 已更新为实际币数

                msg = f"🚀 **买入执行 (BUY)**\n"
                msg += f"• 交易对: {self.symbol}\n"
                msg += f"• 数量: `{final_order_amount} {unit_str}`\n"
                msg += f"• 价格: `${current_realtime_price:,.2f}`\n"
                msg += f"• 金额: `{est_cost:.2f} U`\n"
                msg += f"• 余额: `{post_balance:.2f} U` (Avail)\n"
                msg += f"• 信心: `{signal_data.get('confidence', 'N/A')}`\n"
                msg += f"> **理由**: {signal_data['reason']}"
                
                await self.send_notification(msg, title=f"🚀 买入执行 | {self.symbol}")
                self.last_trade_time = time.time() # [Update] 更新最后交易时间
                return "EXECUTED", f"买入 {final_order_amount}{unit_str}"

            elif signal_data['signal'] == 'SELL':
                if current_position and current_position['side'] == 'long':
                    # 平多 (使用持仓自带的 size，已经是张数)
                    await self.exchange.create_market_order(self.symbol, 'sell', current_position['size'], params={'reduceOnly': True})
                    self._log("🔄 平多仓成功")
                    
                    msg = f"🔄 **平多仓 (Close Long)**\n"
                    msg += f"• 交易对: {self.symbol}\n"
                    msg += f"• 数量: {current_position['size']}\n"
                    msg += f"• 盈亏: {pnl_pct*100:+.2f}% (估算)\n"
                    msg += f"• 理由: {signal_data['reason']}"
                    await self.send_notification(msg)
                    
                    # [Smart Wait]
                    old_balance_check = balance
                    if old_balance_check <= 0: old_balance_check = 0.1
                    
                    balance = await self._wait_for_margin_release(balance, timeout=2.0)

                    # [Fix Flip Logic] 平多后反手开空
                    # balance 已更新
                    equity = await self.get_account_equity()
                    remaining_quota = allocation_usdt_limit
                    
                    if self.trade_mode == 'cash':
                         # 现货不能反手开空，逻辑上不应该走到这里 (signal=SELL 且 trade_mode=cash -> 只平仓)
                         pass
                    else:
                        available_margin = min(balance, remaining_quota)
                        max_trade_limit = (available_margin * self.leverage * 0.99) / current_realtime_price
                    
                        if signal_data.get('confidence', '').upper() == 'HIGH':
                             global_max_usdt = balance * safety_buffer 
                             global_max_token = (global_max_usdt * self.leverage) / current_realtime_price
                             trade_amount = min(ai_suggest, global_max_token)
                        else:
                             trade_amount = min(ai_suggest, config_amt, max_trade_limit)

                        num_contracts = int(trade_amount / contract_size)
                        final_order_amount = float(num_contracts)
                        trade_amount = final_order_amount * contract_size
                        
                        if num_contracts < 1:
                            self._log(f"⚠️ 反手开仓资金不足 (Min 1 Cont)，仅平仓", 'warning')
                            return "PARTIAL", "平多成功，反手资金不足"
                
                if self.trade_mode == 'cash':
                    # 现货卖出
                    await self.exchange.create_market_order(self.symbol, 'sell', final_order_amount)
                    
                    unit_str = f"{self.symbol.split('/')[0]}"
                    self._log(f"📉 卖出成功: {final_order_amount} {unit_str}")
                    
                    post_balance = await self.get_account_balance()
                    est_revenue = trade_amount * current_realtime_price
                    
                    msg = f"**数量**: `{final_order_amount} {unit_str}`\n"
                    msg += f"**价格**: `${current_realtime_price:,.2f}`\n"
                    msg += f"**金额**: `{est_revenue:.2f} U`\n"
                    msg += f"**余额**: `{post_balance:.2f} U` (Avail)\n"
                    msg += f"> **理由**: {signal_data['reason']}"
                    
                    await self.send_notification(msg, title=f"📉 现货卖出 | {self.symbol}")
                    self.last_trade_time = time.time() # [Update]
                    return "EXECUTED", f"卖出 {final_order_amount}"
                else:
                    # 开空 (使用转换后的 final_order_amount)
                    await self.exchange.create_market_order(self.symbol, 'sell', final_order_amount, params={'tdMode': self.trade_mode})
                    
                    unit_str = "张 (Cont)"
                    self._log(f"📉 开空成功: {final_order_amount} {unit_str} (= {trade_amount} Coins)")
                    
                    post_balance = await self.get_account_balance()
                    est_cost = trade_amount * current_realtime_price
                    
                    msg = f"**数量**: `{final_order_amount} {unit_str}`\n"
                    msg += f"**价格**: `${current_realtime_price:,.2f}`\n"
                    msg += f"**金额**: `{est_cost:.2f} U`\n"
                    msg += f"**余额**: `{post_balance:.2f} U` (Avail)\n"
                    msg += f"**信心**: `{signal_data.get('confidence', 'N/A')}`\n"
                    msg += f"> **理由**: {signal_data['reason']}"
                    
                    await self.send_notification(msg, title=f"📉 开空执行 | {self.symbol}")
                    self.last_trade_time = time.time() # [Update]
                    return "EXECUTED", f"开空 {final_order_amount}{unit_str}"

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
        """获取账户总权益 (Equity = 余额 + 未实现盈亏)"""
        try:
            params = {}
            if self.test_mode:
                params = {'simulated': True}
            
            balance = await self.exchange.fetch_balance(params)
            # 1. 尝试直接获取 total (有些交易所支持)
            if 'USDT' in balance and 'total' in balance['USDT']:
                return float(balance['USDT']['total'])
            
            # 2. OKX 统一账户 totalEq
            if 'info' in balance and 'data' in balance['info']:
                return float(balance['info']['data'][0]['totalEq'])
            
            # 3. 降级: Free + Used
            if 'USDT' in balance:
                free = float(balance['USDT'].get('free', 0))
                used = float(balance['USDT'].get('used', 0))
                return free + used
                
            return 0.0
        except Exception as e:
            self._log(f"获取权益失败: {e}", 'error')
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
