from .ai_strategy import DeepSeekAgent
from .strategies.pinbar import PinbarStrategy
import logging

class StrategyFactory:
    def __init__(self, common_config):
        self.logger = logging.getLogger("crypto_oracle")
        self.common_config = common_config
        self.registry = {
            "ai_trend": DeepSeekAgent,
            "pinbar_reversal": PinbarStrategy
        }
        
    def get_strategies(self, active_names, **kwargs):
        strategies = []
        for name in active_names:
            if name in self.registry:
                try:
                    # Instantiate strategy
                    # Note: DeepSeekAgent requires api_key etc.
                    # We need a way to pass specific config to specific strategies.
                    # For now, we assume kwargs contains necessary keys or we handle it here.
                    
                    if name == "ai_trend":
                        # AI Strategy needs specific args
                        # [Fix] 优先使用传入的 shared_agent 实例
                        shared_agent = kwargs.get('shared_agent')
                        if shared_agent:
                            strategy = shared_agent
                        else:
                            api_key = kwargs.get('api_key')
                            if not api_key:
                                self.logger.warning(f"⚠️ 跳过 {name}: 缺少 api_key 且无 shared_agent")
                                continue
                            strategy = self.registry[name](api_key=api_key)
                    else:
                        # Simple strategies
                        strategy = self.registry[name]()
                        
                    strategies.append(strategy)
                    self.logger.info(f"✅ 策略加载成功: {name}")
                except Exception as e:
                    self.logger.error(f"❌ 策略加载失败 {name}: {e}")
            else:
                self.logger.warning(f"⚠️ 未知策略: {name}")
                
        return strategies
