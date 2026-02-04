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
        
        # [Rule 2] 位置要对，震荡行情无效 (Market Regime Filter)
        # 引入 ADX 指标过滤震荡
        # 引入 RSI 指标辅助判断超买超卖 (Location)
        
        adx = price_data.get('adx', 20)
        rsi = price_data.get('rsi', 50)
        
        # 震荡过滤: 如果 ADX < 20，认为是无趋势的垃圾时间，Pinbar 往往是陷阱
        if adx < 20:
             return None

        # [Rule 4] 至少2条以上入场理由 (Confluence)
        # 我们使用 "积分制" (Score System)
        # 基础分: Pinbar 形态成立 (+1)
        # 额外分: 
        #   1. 顺大势 (Trend Alignment)
        #   2. 关键位置 (Support/Resistance via RSI)
        #   3. 量能配合 (Volume Confirmation)
        
        confluence_score = 0
        confluence_reasons = []

        # Bullish Pinbar (Hammer)
        if lower_shadow / total_len > shadow_ratio and body_len / total_len < body_ratio:
            # 基础形态成立
            confluence_score += 1
            confluence_reasons.append("Hammer Pattern")
            
            # 额外理由 A: 位置 (RSI 超卖或低位) -> 认为是支撑位
            if rsi < 40:
                confluence_score += 1
                confluence_reasons.append("RSI Oversold Zone")
            
            # 额外理由 B: 顺势 (EMA 过滤，这里简化用 K 线排列)
            # 如果上一根是阴线，且 Pinbar 低点更低，可能是反转。
            # 如果我们在上升趋势中 (EMA20 > EMA50)，效果更好。
            # 暂时用 ADX > 25 作为趋势强度的佐证
            if adx > 25:
                confluence_score += 1
                confluence_reasons.append("Strong Trend Context")
            
            # [Check Rule 4] 至少 2 条理由
            if confluence_score < 2:
                 return None

            signal = "BUY"
            reason = f"Bullish Pinbar confirmed by {', '.join(confluence_reasons)}"
            confidence = "HIGH"
            # Limit Entry: 50% retrace of the tail
            entry_price = low_p + (lower_shadow * 0.5)
            # Stop Loss: Just below the low
            stop_loss = low_p * 0.998
            
            # [Requirement 2] Take Profit: At least 1x Pinbar amplitude (total_len)
            # [Requirement 1] Risk/Reward Ratio > 1:1.5
            
            risk = entry_price - stop_loss
            reward_target = max(risk * 1.5, total_len) # 取 两者中的最大值
            
            take_profit = entry_price + reward_target

        # Bearish Pinbar (Shooting Star)
        elif upper_shadow / total_len > shadow_ratio and body_len / total_len < body_ratio:
            # 基础形态成立
            confluence_score += 1
            confluence_reasons.append("Shooting Star Pattern")
            
            # 额外理由 A: 位置 (RSI 超买或高位) -> 认为是阻力位
            if rsi > 60:
                confluence_score += 1
                confluence_reasons.append("RSI Overbought Zone")
                
            # 额外理由 B: 趋势强度
            if adx > 25:
                confluence_score += 1
                confluence_reasons.append("Strong Trend Context")
            
            # [Check Rule 4] 至少 2 条理由
            if confluence_score < 2:
                 return None

            signal = "SELL"
            reason = f"Bearish Pinbar confirmed by {', '.join(confluence_reasons)}"
            confidence = "HIGH"
            # Limit Entry: 50% retrace of the tail
            entry_price = high_p - (upper_shadow * 0.5)
            # Stop Loss: Just above the high
            stop_loss = high_p * 1.002
            
            # [Requirement 2] Take Profit: At least 1x Pinbar amplitude (total_len)
            # [Requirement 1] Risk/Reward Ratio > 1:1.5
            
            risk = stop_loss - entry_price
            reward_target = max(risk * 1.5, total_len) # 取 两者中的最大值
            
            take_profit = entry_price - reward_target
            
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
