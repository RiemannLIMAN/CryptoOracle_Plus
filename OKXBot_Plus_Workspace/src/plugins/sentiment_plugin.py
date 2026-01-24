from core.plugin import Plugin
import httpx
import time

class SentimentPlugin(Plugin):
    name = "SentimentAnalyzer"
    description = "实时市场情绪分析 (Fear & Greed Index)"
    version = "1.0.0"
    enabled = True

    def __init__(self, config, exchange=None, agent=None):
        super().__init__(config, exchange, agent)
        self.sentiment_cache = None
        self.last_update = 0
        self.cache_ttl = 3600 # 1 hour cache

    async def initialize(self):
        self.logger.info("🧠 情绪分析插件初始化")

    async def on_tick(self, data):
        # Configurable Thresholds
        strategy_config = self.config.get('trading', {}).get('strategy', {})
        filter_config = strategy_config.get('sentiment_filter', {})
        
        if not filter_config.get('enabled', True):
            return

        greed_threshold = filter_config.get('extreme_greed_threshold', 75)
        fear_threshold = filter_config.get('extreme_fear_threshold', 25)

        # Only fetch if cache expired
        if time.time() - self.last_update > self.cache_ttl:
            await self._update_sentiment()
            
        if self.sentiment_cache:
            # Inject sentiment data
            data['sentiment'] = self.sentiment_cache
            
            # Simple Filter Logic
            score = self.sentiment_cache.get('value', 50)
            classification = self.sentiment_cache.get('value_classification', 'Neutral')
            
            # 0-100 Scale: <25 Extreme Fear, >75 Extreme Greed
            try:
                score_val = int(score)
                
                # Contrarian Strategy
                if score_val > greed_threshold and data.get('signal') == 'BUY':
                    data['signal'] = 'HOLD'
                    data['reason'] += f" | ⚠️ 情绪过热 ({score_val} {classification} > {greed_threshold})，禁止追高"
                    self.logger.info(f"🚫 情绪插件拦截: 市场极度贪婪 ({score_val}) -> 暂停买入")
                    
                elif score_val < fear_threshold and data.get('signal') == 'SELL':
                    # Don't short at the bottom
                    data['signal'] = 'HOLD'
                    data['reason'] += f" | ⚠️ 情绪冰点 ({score_val} {classification} < {fear_threshold})，禁止追空"
                    self.logger.info(f"🚫 情绪插件拦截: 市场极度恐慌 ({score_val}) -> 暂停卖出")
                    
            except ValueError:
                pass

    async def _update_sentiment(self):
        try:
            async with httpx.AsyncClient() as client:
                # Alternative.me Fear & Greed Index API
                response = await client.get("https://api.alternative.me/fng/?limit=1")
                if response.status_code == 200:
                    result = response.json()
                    if result.get('data'):
                        self.sentiment_cache = result['data'][0]
                        self.last_update = time.time()
                        self.logger.info(f"🧠 更新市场情绪: {self.sentiment_cache['value']} ({self.sentiment_cache['value_classification']})")
        except Exception as e:
            self.logger.error(f"情绪数据获取失败: {e}")

    async def on_trade(self, trade_data): pass
    async def on_error(self, error): pass
    async def shutdown(self): pass
