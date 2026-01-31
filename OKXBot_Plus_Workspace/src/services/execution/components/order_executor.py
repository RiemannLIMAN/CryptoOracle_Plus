import time
import asyncio
from datetime import datetime
from core.utils import retry_async, rate_limiter

class OrderExecutor:
    def __init__(self, exchange, symbol, trade_mode, test_mode, position_manager, logger):
        self.exchange = exchange
        self.symbol = symbol
        self.trade_mode = trade_mode
        self.test_mode = test_mode
        self.position_manager = position_manager
        self.logger = logger
        
        self.taker_fee_rate = 0.001 # Default

        # [P0-4.1] Circuit Breaker (熔断器) 状态
        self.consecutive_failures = 0
        self.failure_threshold = 3      # 连续失败 3 次触发熔断
        self.cooldown_until = 0         # 熔断冷却截止时间
        self.cooldown_duration = 600    # 熔断冷却 10 分钟 (600s)

    def is_fused(self):
        """检查当前交易对是否处于熔断状态"""
        if self.cooldown_until > time.time():
            remaining = int(self.cooldown_until - time.time())
            self.logger.warning(f"🛡️ [{self.symbol}] 处于熔断保护中，剩余冷却时间: {remaining}s")
            return True
        return False

    def set_fee_rate(self, rate):
        self.taker_fee_rate = rate

    @retry_async(retries=2, delay=0.5)
    async def create_order_with_retry(self, side, amount, order_type='market', price=None, params={}):
        # [P0-4.1] 检查熔断状态
        if self.is_fused():
            raise Exception(f"Circuit Breaker active for {self.symbol}")

        # [P2-4.5] 全局限频
        await rate_limiter.acquire()

        try:
            res = await self.exchange.create_order(
                self.symbol,
                order_type,
                side,
                amount,
                price,
                params=params
            )
            # 成功则重置失败计数
            self.consecutive_failures = 0
            return res
        except Exception as e:
            error_msg = str(e)
            # [Auto-Fix] 余额不足 (51008) 自动降级重试
            if "51008" in error_msg and "Insufficient" in error_msg:
                # 提取余额不足的提示，尝试按比例减少
                self.logger.warning(f"⚠️ 余额不足 (51008)，尝试减少数量重试: {amount} -> {amount * 0.95:.4f}")
                
                try:
                    res2 = await self.exchange.create_order(
                        self.symbol,
                        order_type,
                        side,
                        amount * 0.95, # 降级 5%
                        price,
                        params=params
                    )
                    # 成功则重置失败计数
                    self.consecutive_failures = 0
                    return res2
                except Exception as e2:
                    # 如果降级后还是失败，累计失败次数
                    self.consecutive_failures += 1
                    if self.consecutive_failures >= self.failure_threshold:
                        self.cooldown_until = time.time() + self.cooldown_duration
                        self.logger.error(f"🚨 [{self.symbol}] 连续失败 {self.consecutive_failures} 次，触发熔断器！冷却 {self.cooldown_duration}s")
                    
                    # [User Request] 简化错误日志，并明确单位
                    unit = "张" if self.trade_mode == 'swap' else "个"
                    self.logger.error(f"❌ [{self.symbol}] × 保证金不足 (Code 51008): 尝试下单 {amount * 0.95:.4f} {unit}")
                    raise e
            
            # 其他错误也累计失败次数
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.failure_threshold:
                self.cooldown_until = time.time() + self.cooldown_duration
                self.logger.error(f"🚨 [{self.symbol}] 连续失败 {self.consecutive_failures} 次，触发熔断器！冷却 {self.cooldown_duration}s")
            
            raise e # 其他错误继续抛出，让 retry_async 处理

    async def execute_sim_trade(self, signal_data, current_price):
        """Execute trade in simulation mode"""
        signal = signal_data['signal']
        amount = signal_data.get('amount', 0)
        
        # Calculate fee (simplified)
        fee_rate = self.taker_fee_rate
        trade_value = amount * current_price
        fee = trade_value * fee_rate
        
        pnl = 0.0
        
        # Access state from PositionManager
        sim_position = self.position_manager.sim_position
        sim_balance = self.position_manager.sim_balance
        sim_realized_pnl = self.position_manager.sim_realized_pnl
        sim_trades = self.position_manager.sim_trades

        if signal == 'BUY':
            # Opening Long or Closing Short
            if sim_position and sim_position['side'] == 'short':
                # Closing Short (Buy to Cover)
                close_amount = amount
                current_size = sim_position['size']
                
                if close_amount >= current_size * 0.99: # Full close
                    close_amount = current_size
                    is_full_close = True
                else:
                    is_full_close = False
                    
                entry_price = sim_position['entry_price']
                
                pnl = (entry_price - current_price) * close_amount
                pnl -= fee 
                
                sim_realized_pnl += pnl
                sim_balance += pnl 
                
                self._record_sim_trade('buy', current_price, close_amount, fee, pnl)
                
                if is_full_close:
                    sim_position = None 
                    self.logger.info(f"🧪 模拟平空(全): {close_amount} @ {current_price} | PnL: {pnl:.2f} U")
                else:
                    sim_position['size'] -= close_amount
                    sim_position['coin_size'] -= close_amount 
                    self.logger.info(f"🧪 模拟平空(分): {close_amount} @ {current_price} | PnL: {pnl:.2f} U")
                
            elif sim_position and sim_position['side'] == 'long':
                # Adding to Long (Pyramiding)
                old_size = sim_position['size']
                old_entry = sim_position['entry_price']
                
                new_size = old_size + amount
                avg_entry = ((old_size * old_entry) + (amount * current_price)) / new_size
                
                sim_position['size'] = new_size
                sim_position['coin_size'] = new_size
                sim_position['entry_price'] = avg_entry
                
                sim_realized_pnl -= fee
                sim_balance -= fee
                
                self._record_sim_trade('buy', current_price, amount, fee, 0.0)
                self.logger.info(f"🧪 模拟加多: {amount} @ {current_price} | NewAvg: {avg_entry:.4f}")
                
            else:
                # Opening Long
                sim_position = {
                    'side': 'long',
                    'size': amount,
                    'coin_size': amount,
                    'entry_price': current_price,
                    'unrealized_pnl': 0.0,
                    'leverage': 1.0, # Default logic, maybe pass from config
                    'symbol': self.symbol,
                    'mode': 'cash' if self.trade_mode == 'cash' else 'margin'
                }
                sim_realized_pnl -= fee
                sim_balance -= fee
                
                if self.trade_mode == 'cash':
                    sim_balance -= trade_value
                
                self._record_sim_trade('buy', current_price, amount, fee, 0.0)
                self.logger.info(f"🧪 模拟开多: {amount} @ {current_price} | Fee: {fee:.2f} U")

        elif signal == 'SELL':
            # Opening Short or Closing Long
            if sim_position and sim_position['side'] == 'long':
                # Closing Long (Sell to Close)
                close_amount = amount
                current_size = sim_position['size']
                
                if close_amount >= current_size * 0.99: 
                    close_amount = current_size
                    is_full_close = True
                else:
                    is_full_close = False
                    
                entry_price = sim_position['entry_price']
                
                pnl = (current_price - entry_price) * close_amount
                pnl -= fee
                
                sim_realized_pnl += pnl
                sim_balance += pnl
                
                if self.trade_mode == 'cash':
                    cost_of_sold = close_amount * entry_price
                    sim_balance += cost_of_sold
                
                self._record_sim_trade('sell', current_price, close_amount, fee, pnl)
                
                if is_full_close:
                    sim_position = None
                    self.logger.info(f"🧪 模拟平多(全): {close_amount} @ {current_price} | PnL: {pnl:.2f} U")
                else:
                    sim_position['size'] -= close_amount
                    sim_position['coin_size'] -= close_amount
                    self.logger.info(f"🧪 模拟平多(分): {close_amount} @ {current_price} | PnL: {pnl:.2f} U")
                
            elif sim_position and sim_position['side'] == 'short':
                # Adding to Short
                old_size = sim_position['size']
                old_entry = sim_position['entry_price']
                
                new_size = old_size + amount
                avg_entry = ((old_size * old_entry) + (amount * current_price)) / new_size
                
                sim_position['size'] = new_size
                sim_position['coin_size'] = new_size
                sim_position['entry_price'] = avg_entry
                
                sim_realized_pnl -= fee
                sim_balance -= fee
                
                self._record_sim_trade('sell', current_price, amount, fee, 0.0)
                self.logger.info(f"🧪 模拟加空: {amount} @ {current_price} | NewAvg: {avg_entry:.4f}")
                
            else:
                # Opening Short
                if self.trade_mode == 'cash':
                    self.logger.info(f"🧪 现货模式无法开空")
                    return "FAILED", "现货无法开空"
                    
                sim_position = {
                    'side': 'short',
                    'size': amount,
                    'coin_size': amount,
                    'entry_price': current_price,
                    'unrealized_pnl': 0.0,
                    'leverage': 1.0,
                    'symbol': self.symbol,
                    'mode': 'margin'
                }
                sim_realized_pnl -= fee
                sim_balance -= fee
                
                self._record_sim_trade('sell', current_price, amount, fee, 0.0)
                self.logger.info(f"🧪 模拟开空: {amount} @ {current_price} | Fee: {fee:.2f} U")
        
        # Update PositionManager state
        self.position_manager.set_sim_state(sim_balance, sim_position, sim_trades, sim_realized_pnl)
        
        return "EXECUTED_SIM", "模拟交易成功"

    def _record_sim_trade(self, side, price, amount, fee=0.0, pnl=0.0):
        trade = {
            'symbol': self.symbol,
            'side': side.lower(),
            'price': price,
            'amount': amount,
            'cost': price * amount,
            'fee': {'cost': fee, 'currency': 'USDT'},
            'timestamp': int(time.time() * 1000),
            'datetime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            'info': {'pnl': pnl}
        }
        self.position_manager.sim_trades.append(trade)
