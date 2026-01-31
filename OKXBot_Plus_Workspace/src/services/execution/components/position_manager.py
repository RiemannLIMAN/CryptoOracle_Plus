import asyncio
from collections import deque
from core.utils import to_float
from .rl_position_sizer import SmartPositionSizer

class PositionManager:
    def __init__(self, exchange, symbol, trade_mode, test_mode, logger):
        self.exchange = exchange
        self.symbol = symbol
        self.trade_mode = trade_mode
        self.test_mode = test_mode
        self.logger = logger
        
        self.trailing_max_pnl = 0.0
        self.trailing_config = {}
        
        # [v3.9.6] Smart Sizing Module (AI + Heuristic)
        self.rl_sizer = SmartPositionSizer(logger=self.logger)
        
        # Simulation State
        self.sim_position = None 
        self.sim_balance = 10000.0 # Default sim balance
        # [P1-4.3] 使用 deque 限制模拟交易历史长度
        self.sim_trades = deque(maxlen=100)
        self.sim_realized_pnl = 0.0
        
        # [v3.9.6 New] Risk Control Factor (0.0 - 1.0)
        self.global_risk_factor = 1.0

    def set_trailing_config(self, config):
        self.trailing_config = config

    def set_sim_state(self, balance, position, trades, realized_pnl):
        self.sim_balance = balance
        self.sim_position = position
        self.sim_trades = trades
        self.sim_realized_pnl = realized_pnl
        
    def get_sim_state(self):
        return {
            'sim_balance': self.sim_balance,
            'sim_position': self.sim_position,
            'sim_trades': self.sim_trades,
            'sim_realized_pnl': self.sim_realized_pnl
        }

    def get_recommended_position_size(self, signal_data, indicators, sentiment_score=50):
        """
        [New] 获取建议的仓位比例 (RL or Heuristic)
        Returns: float (0.0 - 1.0)
        """
        try:
            # Construct Observation
            # [volatility, trend_strength, confidence, pnl_ratio, sentiment]
            
            # 1. Volatility (ATR Ratio)
            volatility = 1.0
            if indicators:
                volatility = indicators.get('atr_ratio', 1.0)
            
            # 2. Trend Strength (ADX)
            trend = 20.0
            if indicators:
                trend = indicators.get('adx', 20.0)
            
            # 3. Confidence
            conf_map = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
            confidence = conf_map.get(signal_data.get('confidence', 'LOW').upper(), 1)
            
            # 4. PnL Ratio (Optional, maybe current position pnl?)
            # For now use 0.0 as placeholder for new entry
            pnl_ratio = 0.0
            
            # 5. Sentiment
            sentiment = sentiment_score
            
            obs = [volatility, trend, confidence, pnl_ratio, sentiment]
            
            base_ratio = self.rl_sizer.predict(obs)
            
            # [v3.9.6 New] Apply Global Risk Factor
            return base_ratio * self.global_risk_factor
            
        except Exception as e:
            self.logger.error(f"仓位计算失败: {e}")
            return 1.0

    async def get_current_position(self):
        # [New] Test Mode Simulation Interception
        if self.test_mode:
            if self.sim_position:
                # Update unrealized PnL based on current price
                try:
                    ticker = await self.exchange.fetch_ticker(self.symbol)
                    current_price = ticker['last']
                    
                    entry = float(self.sim_position['entry_price'])
                    size = float(self.sim_position['coin_size']) # Use coin_size for calculation
                    
                    if self.sim_position['side'] == 'long':
                        self.sim_position['unrealized_pnl'] = (current_price - entry) * size
                    else:
                        self.sim_position['unrealized_pnl'] = (entry - current_price) * size
                except:
                    pass
            return self.sim_position

        try:
            # Identify if this is a Contract instrument or Spot
            market_info = self.exchange.market(self.symbol)
            is_contract = market_info.get('swap') or market_info.get('future') or market_info.get('option') or (market_info.get('type') in ['swap', 'future', 'option'])

            # [Fix] 优先检查交易所返回的标准 Position 数据 (包含合约持仓 和 现货杠杆持仓)
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
                            if not is_contract or contract_size <= 0:
                                contract_size = 1.0
                        except:
                            pass

                        return {
                            'side': pos['side'],
                            'size': contracts,
                            'coin_size': contracts * contract_size, # 实际币数
                            'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                            'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                            'leverage': float(pos['leverage']) if pos['leverage'] else 1.0,
                            'symbol': pos['symbol']
                        }

            # [Fix] 增加对现货模式 (Cash) 和 现货杠杆 (Spot Margin) 的持仓支持
            if not is_contract:
                 spot_bal = await self.get_spot_balance(total=True)
                 
                 current_price = 0
                 try:
                     ticker = await self.exchange.fetch_ticker(self.symbol)
                     current_price = ticker['last']
                 except:
                     pass
                 
                 min_cost = 5.0
                 try:
                     market = self.exchange.market(self.symbol)
                     cost_min = market.get('limits', {}).get('cost', {}).get('min')
                     if cost_min is not None:
                         min_cost = float(cost_min)
                 except:
                     pass

                 if spot_bal * current_price >= min_cost:
                     avg_price = await self.get_avg_entry_price(skip_pos=True)
                     if avg_price == 0: avg_price = current_price # Fallback
                     
                     pnl = (current_price - avg_price) * spot_bal
                     
                     return {
                         'side': 'long', 
                         'size': spot_bal,
                         'coin_size': spot_bal,
                         'entry_price': avg_price,
                         'unrealized_pnl': pnl,
                         'leverage': 1.0,
                         'symbol': self.symbol,
                         'mode': 'cash' if self.trade_mode == 'cash' else 'margin'
                     }
            
            return None
        except Exception as e:
            self.logger.error(f"获取持仓失败: {e}")
            return None

    async def get_avg_entry_price(self, skip_pos=False):
        try:
            if not skip_pos:
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

    async def get_spot_balance(self, total=False):
        if self.test_mode:
            if self.sim_position and self.sim_position.get('mode') == 'cash':
                 return float(self.sim_position['size'])
            return 0.0

        try:
            base_currency = self.symbol.split('/')[0]
            balance = await self.exchange.fetch_balance()
            if base_currency in balance:
                if total:
                    return float(balance[base_currency]['total'])
                return float(balance[base_currency]['free'])
            elif 'info' in balance and 'data' in balance['info']:
                if not balance['info']['data']:
                     return 0.0
                     
                for asset in balance['info']['data'][0]['details']:
                    if asset['ccy'] == base_currency:
                        if total:
                            return float(asset.get('cashBal', 0))
                        return float(asset['availBal'])
            return 0.0
        except Exception:
            return 0.0

    async def check_trailing_stop(self, current_position, indicators=None, save_callback=None, notification_callback=None):
        """
        检查并执行移动止盈 (Trailing Stop)
        [v3.9.6 Optimized] 动态回调比例 (ATR驱动) + 分段止盈机制
        """
        if not self.trailing_config.get('enabled', False):
            return False

        if not current_position:
            self.trailing_max_pnl = 0.0
            # [New] 重置分段止盈标记
            self.partial_tp_stages = []
            return False
            
        if self.trade_mode == 'cash':
            return False

        try:
            pnl_ratio = 0.0
            has_valid_pnl = False
            
            if 'uplRatio' in current_position:
                try:
                    pnl_ratio = float(current_position['uplRatio'])
                    has_valid_pnl = True
                except (ValueError, TypeError):
                    pass
            elif 'unrealized_pnl' in current_position and 'size' in current_position:
                try:
                    unrealized_pnl = float(current_position['unrealized_pnl'])
                    size = float(current_position['size'])
                    if size > 0:
                        entry = float(current_position['entry_price'])
                        if entry > 0:
                             pnl_ratio = (float(unrealized_pnl) / (size * entry))
                             has_valid_pnl = True
                except (ValueError, TypeError):
                    pass
            
            if not has_valid_pnl:
                return False

            # 1. 动态计算回调比例 (ATR 驱动 + 盈利阶梯驱动)
            # [Optimized] 
            # 维度 A: 波动率 (ATR) -> 高波动放宽，低波动收紧
            atr_ratio = indicators.get('atr_ratio', 1.0) if indicators else 1.0
            
            # [Fix] 确保 callback_rate 是 float
            raw_callback = self.trailing_config.get('callback_rate', 0.005)
            try:
                base_callback = float(raw_callback)
            except (ValueError, TypeError):
                base_callback = 0.005
            
            # ATR 调节因子
            if atr_ratio > 2.0:         dynamic_callback = 0.025
            elif atr_ratio > 1.5:       dynamic_callback = 0.015
            elif atr_ratio < 0.8:       dynamic_callback = 0.003
            else:                       dynamic_callback = base_callback

            # 维度 B: 盈利阶梯 (Profit Compression) -> 盈利越高，回撤容忍度越低 (锁定利润)
            # [v3.9.7 Refined] 6级深度阶梯锁定
            profit_compression = 1.0
            if pnl_ratio >= 1.00:       profit_compression = 0.05 # 利润 > 100%，回撤仅允许原有的 5% (极速锁定)
            elif pnl_ratio >= 0.50:     profit_compression = 0.1  # 利润 > 50%，回撤仅允许 10%
            elif pnl_ratio >= 0.20:     profit_compression = 0.2  # 利润 > 20%，回撤仅允许 20%
            elif pnl_ratio >= 0.10:     profit_compression = 0.4  # 利润 > 10%，回撤仅允许 40%
            elif pnl_ratio >= 0.05:     profit_compression = 0.6  # 利润 > 5%，回撤仅允许 60%
            elif pnl_ratio >= 0.02:     profit_compression = 0.8  # 利润 > 2%，回撤仅允许 80% (初步保护)
            
            dynamic_callback *= profit_compression

            # [Fix] 确保 activation_pnl 是 float
            raw_activation = self.trailing_config.get('activation_pnl', 0.01)
            try:
                activation_pnl = float(raw_activation)
            except (ValueError, TypeError):
                activation_pnl = 0.01

            # 2. 更新最高水位线
            if pnl_ratio > self.trailing_max_pnl:
                self.trailing_max_pnl = pnl_ratio
                if self.trailing_max_pnl > 0.01 and save_callback: 
                    asyncio.create_task(save_callback())

            # 3. [New] 分段止盈机制 (Partial Profit Taking)
            # 达到 5% 利润平 30%，10% 利润平 30%
            if not hasattr(self, 'partial_tp_stages'):
                self.partial_tp_stages = []
            
            current_size = float(current_position['size'])
            side = 'buy' if current_position['side'] == 'short' else 'sell'
            close_params = {'reduceOnly': True, 'tdMode': self.trade_mode}

            if pnl_ratio >= 0.10 and 'stage_10' not in self.partial_tp_stages:
                self.logger.info(f"💰 [Partial TP] 触及 10% 利润节点，执行 30% 分批减仓")
                await self.exchange.create_market_order(self.symbol, side, current_size * 0.3, params=close_params)
                self.partial_tp_stages.append('stage_10')
                # [Refined] 减仓后重置追踪点，让剩余仓位从当前盈亏水平重新追踪
                self.trailing_max_pnl = pnl_ratio * 0.7 
                if notification_callback:
                    await notification_callback(f"💰 [Partial TP] {self.symbol} 触及 10% 节点，已减仓 30%")

            elif pnl_ratio >= 0.05 and 'stage_5' not in self.partial_tp_stages:
                self.logger.info(f"💰 [Partial TP] 触及 5% 利润节点，执行 30% 分批减仓")
                await self.exchange.create_market_order(self.symbol, side, current_size * 0.3, params=close_params)
                self.partial_tp_stages.append('stage_5')
                # [Refined] 减仓后重置追踪点
                self.trailing_max_pnl = pnl_ratio * 0.7
                if notification_callback:
                    await notification_callback(f"💰 [Partial TP] {self.symbol} 触及 5% 节点，已减仓 30%")

            # 4. 检查是否激活移动止盈
            if self.trailing_max_pnl >= activation_pnl:
                # 5. 检查回撤
                drawdown = self.trailing_max_pnl - pnl_ratio
                if drawdown >= dynamic_callback:
                    self.logger.info(f"⚡ 触发移动止盈! 最高: {self.trailing_max_pnl*100:.2f}%, 当前: {pnl_ratio*100:.2f}%, 回撤: {drawdown*100:.2f}% (阈值:{dynamic_callback*100:.2f}%)")
                    
                    await self.exchange.create_market_order(self.symbol, side, current_size, params=close_params)
                    
                    if notification_callback:
                        msg = f"⚡ 移动止盈触发 ({self.symbol})\n锁定收益: {pnl_ratio*100:.2f}%\n最高浮盈: {self.trailing_max_pnl*100:.2f}%"
                        await notification_callback(msg)
                    
                    self.trailing_max_pnl = 0.0
                    self.partial_tp_stages = []
                    return True

        except Exception as e:
            self.logger.error(f"Trailing Stop Check Failed: {e}")
            return False
        return False
