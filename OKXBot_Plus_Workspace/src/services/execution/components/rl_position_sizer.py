
class SmartPositionSizer:
    def __init__(self, logger=None):
        self.logger = logger
        if self.logger:
            self.logger.info("🧠 [Smart Sizing] 已激活 [AI + 启发式规则] 混合调仓模式")

    def predict(self, observation):
        """
        根据观测状态预测仓位比例 (启发式兜底)
        observation: [volatility, trend_strength, confidence, pnl_ratio, market_sentiment]
        return: float (0.0 - 1.0)
        """
        return self._heuristic_fallback(observation)

    def _heuristic_fallback(self, observation):
        """
        [规则引擎]
        使用启发式规则模拟智能调仓行为
        Obs: [volatility, trend_strength, confidence, pnl_ratio, sentiment]
        """
        try:
            volatility = observation[0] # ATR Ratio (1.0 = Normal)
            trend = observation[1]      # ADX (0-100)
            confidence = observation[2] # 1, 2, 3
            # pnl_ratio = observation[3]
            sentiment = observation[4]  # 0-100 (50=Neutral)
            
            base_size = 1.0
            
            # 1. 波动率调整: 波动过大减仓
            if volatility > 2.0:
                base_size *= 0.5
            elif volatility < 0.8:
                base_size *= 0.8 # 死鱼盘也减仓
                
            # 2. 趋势强度调整
            if trend > 50:
                base_size *= 1.2 # 强趋势加仓
            elif trend < 20:
                base_size *= 0.6 # 震荡减仓
                
            # 3. 情绪调整 (Sentiment Adjustment)
            if sentiment > 80: # 极度贪婪
                base_size *= 0.6 
            elif sentiment < 20: # 极度恐慌
                base_size *= 0.3 
                
            # [Risk] 限制单次最大加仓倍数
            max_position_ratio = 1.0
            if sentiment < 20:
                 max_position_ratio = 0.5 
                 
            base_size = min(base_size, max_position_ratio)
                
            # 4. 信心调整
            if confidence >= 3: # HIGH
                base_size *= 1.2
            elif confidence <= 1: # LOW
                base_size *= 0.5
                
            return min(max(base_size, 0.1), 1.0) # Clip 0.1 - 1.0
            
        except:
            return 1.0 # Default full size
