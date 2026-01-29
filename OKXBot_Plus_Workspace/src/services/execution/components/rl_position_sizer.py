try:
    import numpy as np
    from stable_baselines3 import PPO
    HAS_RL_LIB = True
except ImportError:
    HAS_RL_LIB = False

class RLPositionSizer:
    def __init__(self, model_path="models/rl_position_model.zip", logger=None):
        self.logger = logger
        self.model = None
        self.enabled = False
        
        if HAS_RL_LIB:
            try:
                # 尝试加载预训练模型
                # 注意：如果文件不存在，这里会抛出异常
                self.model = PPO.load(model_path)
                self.enabled = True
                if self.logger:
                    self.logger.info(f"🤖 RL模型加载成功: {model_path}")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"RL模型加载失败或未找到 ({e})，将使用规则引擎兜底")
        else:
            if self.logger:
                self.logger.info("未检测到 stable-baselines3 库，RL 模式不可用 (请 pip install stable-baselines3 shimmy)")

    def predict(self, observation):
        """
        根据观测状态预测仓位比例
        observation: [volatility, trend_strength, confidence, pnl_ratio, market_sentiment]
        return: float (0.0 - 1.0)
        """
        if self.enabled and self.model:
            try:
                # deterministic=True for consistent output
                action, _ = self.model.predict(observation, deterministic=True)
                # 假设 action 是 0-1 之间的连续值 (Box space)
                # 如果是离散值，需做映射
                if isinstance(action, (list, np.ndarray)):
                    return float(np.clip(action[0], 0.1, 1.0))
                return float(action)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"RL推理失败: {e}")
                return self._heuristic_fallback(observation)
        else:
            return self._heuristic_fallback(observation)

    def _heuristic_fallback(self, observation):
        """
        [规则引擎兜底]
        当没有模型时，使用启发式规则模拟 RL 行为
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
            # [v3.9.6 Optimized] 移除极度恐慌加仓逻辑，改为防御减仓
            if sentiment > 80: # 极度贪婪
                base_size *= 0.7 # 减仓防回调
            elif sentiment < 20: # 极度恐慌
                base_size *= 0.3 # [Modified] 极度恐慌时显著减仓，防止抄底爆仓
                
            # [Risk] 限制单次最大加仓倍数
            max_position_ratio = 1.0
            if sentiment < 20:
                 max_position_ratio = 0.5 # 即使信心再高，极度恐慌下也只给 50% 额度
                 
            base_size = min(base_size, max_position_ratio)
                
            # 4. 信心调整
            if confidence >= 3: # HIGH
                base_size *= 1.2
            elif confidence <= 1: # LOW
                base_size *= 0.5
                
            return min(max(base_size, 0.1), 1.0) # Clip 0.1 - 1.0
            
        except:
            return 1.0 # Default full size
