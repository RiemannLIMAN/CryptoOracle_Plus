import time
import asyncio
from datetime import datetime
from core.utils import retry_async

class OrderExecutor:
    def __init__(self, exchange, symbol, trade_mode, test_mode, position_manager, logger):
        self.exchange = exchange
        self.symbol = symbol
        self.trade_mode = trade_mode
        self.test_mode = test_mode
        self.position_manager = position_manager
        self.logger = logger
        
        self.taker_fee_rate = 0.001 # Default

    def set_fee_rate(self, rate):
        self.taker_fee_rate = rate

    @retry_async(retries=2, delay=0.5)
    async def create_order_with_retry(self, side, amount, order_type='market', price=None, params={}):
        try:
            return await self.exchange.create_order(
                self.symbol,
                order_type,
                side,
                amount,
                price,
                params=params
            )
        except Exception as e:
            error_msg = str(e)
            # [Auto-Fix] 余额不足 (51008) 自动降级重试
            if "51008" in error_msg and "Insufficient" in error_msg:
                # 提取余额不足的提示，尝试按比例减少
                self.logger.warning(f"⚠️ 余额不足 (51008)，尝试减少数量重试: {amount} -> {amount * 0.95:.4f}")
                # 递归调用自己，减少 5% 数量，最多递归几次由外部重试控制
                # 但这里是内部逻辑，为了防止无限递归，我们只尝试一次降级
                # 由于这是在 retry_async 装饰器内部，抛出异常会触发装饰器的重试
                # 我们可以在这里直接抛出一个带有特殊标记的异常，或者直接修改 amount
                
                # 更好的方式：抛出异常让 retry_async 捕获，但 retry_async 只是重试相同的参数
                # 所以我们必须在这里手动执行一次降级后的下单
                return await self.exchange.create_order(
                    self.symbol,
                    order_type,
                    side,
                    amount * 0.95, # 降级 5%
                    price,
                    params=params
                )
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
        if len(self.position_manager.sim_trades) > 100:
            self.position_manager.sim_trades = self.position_manager.sim_trades[-100:]
