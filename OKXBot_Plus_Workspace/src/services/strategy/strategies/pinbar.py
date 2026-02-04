from ..base import BaseStrategy

class PinbarStrategy(BaseStrategy):
    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, **kwargs):
        kline_data = price_data.get('kline_data', [])
        if not kline_data or len(kline_data) < 1:
            return None
        
        # Analyze the last closed candle (or current if considering real-time)
        # Usually we look at the last completed candle for pattern confirmation
        # But 'kline_data' might contain current open candle at the end.
        # Let's check the last completed candle (index -2 if real-time, or -1 if closed)
        # Assuming kline_data[-1] is the latest available data.
        
        last_kline = kline_data[-1]
        
        open_p = float(last_kline['open'])
        close_p = float(last_kline['close'])
        high_p = float(last_kline['high'])
        low_p = float(last_kline['low'])
        
        total_len = high_p - low_p
        body_len = abs(close_p - open_p)
        
        if total_len == 0:
            return None
            
        upper_shadow = high_p - max(open_p, close_p)
        lower_shadow = min(open_p, close_p) - low_p
        
        signal = "HOLD"
        reason = ""
        entry_price = None
        stop_loss = None
        take_profit = None
        confidence = "LOW"
        
        # Thresholds
        shadow_ratio = 0.6
        body_ratio = 0.3
        
        # Bullish Pinbar (Hammer)
        if lower_shadow / total_len > shadow_ratio and body_len / total_len < body_ratio:
            signal = "BUY"
            reason = "Bullish Pinbar (Hammer) detected"
            confidence = "HIGH"
            # Limit Entry: 50% retrace of the tail
            entry_price = low_p + (lower_shadow * 0.5)
            # Stop Loss: Just below the low
            stop_loss = low_p * 0.998
            # Take Profit: 2x risk
            risk = entry_price - stop_loss
            take_profit = entry_price + (risk * 2)

        # Bearish Pinbar (Shooting Star)
        elif upper_shadow / total_len > shadow_ratio and body_len / total_len < body_ratio:
            signal = "SELL"
            reason = "Bearish Pinbar (Shooting Star) detected"
            confidence = "HIGH"
            # Limit Entry: 50% retrace of the tail
            entry_price = high_p - (upper_shadow * 0.5)
            # Stop Loss: Just above the high
            stop_loss = high_p * 1.002
            # Take Profit: 2x risk
            risk = stop_loss - entry_price
            take_profit = entry_price - (risk * 2)
            
        if signal == "HOLD":
            return None
            
        return {
            "signal": signal,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "amount": 0, # Let executor decide or AI decide
            "reason": reason,
            "confidence": confidence
        }
