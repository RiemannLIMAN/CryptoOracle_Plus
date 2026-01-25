from core.plugin import Plugin

class ExamplePlugin(Plugin):
    """示例插件"""
    name = "ExamplePlugin"
    description = "示例插件，展示插件系统的使用方法"
    version = "1.0.0"
    enabled = True
    
    async def initialize(self):
        """初始化插件"""
        self.logger.info(f"初始化示例插件: {self.name}")
        # 初始化代码
    
    async def on_tick(self, data):
        """每轮循环调用"""
        # 处理每轮循环数据
        pass
    
    async def on_trade(self, trade_data):
        """交易执行后调用"""
        # 处理交易数据
        # [User Request] 移除 "交易执行: ..." 打印
        # self.logger.info(f"交易执行: {trade_data}")
        pass
    
    async def on_error(self, error):
        """发生错误时调用"""
        # 处理错误
        self.logger.error(f"捕获错误: {error}")
    
    async def shutdown(self):
        """关闭插件"""
        self.logger.info(f"关闭示例插件: {self.name}")
        # 清理代码