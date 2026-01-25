import os
import importlib
import logging
from abc import ABC, abstractmethod

class Plugin(ABC):
    """插件基类"""
    name = "BasePlugin"
    description = "基础插件"
    version = "1.0.0"
    enabled = True
    
    def __init__(self, config, exchange=None, agent=None):
        self.config = config
        self.exchange = exchange
        self.agent = agent
        self.logger = logging.getLogger("crypto_oracle")
    
    @abstractmethod
    async def initialize(self):
        """初始化插件"""
        pass
    
    @abstractmethod
    async def on_tick(self, data):
        """每轮循环调用"""
        pass
    
    @abstractmethod
    async def on_trade(self, trade_data):
        """交易执行后调用"""
        pass
    
    @abstractmethod
    async def on_error(self, error):
        """发生错误时调用"""
        pass
    
    @abstractmethod
    async def shutdown(self):
        """关闭插件"""
        pass

class PluginManager:
    """插件管理器"""
    def __init__(self):
        self.plugins = []
        self.logger = logging.getLogger("crypto_oracle")
    
    def load_plugins(self, config, exchange=None, agent=None):
        """加载插件"""
        try:
            # 插件目录
            plugin_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")
            
            if not os.path.exists(plugin_dir):
                self.logger.info("插件目录不存在，跳过插件加载")
                return
            
            # 遍历插件目录
            for filename in os.listdir(plugin_dir):
                if filename.endswith(".py") and not filename.startswith("_"):
                    module_name = f"plugins.{filename[:-3]}"
                    try:
                        # 导入插件模块
                        module = importlib.import_module(module_name)
                        
                        # 查找插件类
                        for name, obj in module.__dict__.items():
                            if isinstance(obj, type) and issubclass(obj, Plugin) and obj != Plugin:
                                # 创建插件实例
                                plugin = obj(config, exchange, agent)
                                self.plugins.append(plugin)
                                self.logger.debug(f"加载插件: {plugin.name} v{plugin.version}")
                    except Exception as e:
                        self.logger.error(f"加载插件 {module_name} 失败: {e}")
        except Exception as e:
            self.logger.error(f"加载插件失败: {e}")
    
    async def initialize_plugins(self):
        """初始化所有插件"""
        for plugin in self.plugins:
            if plugin.enabled:
                try:
                    await plugin.initialize()
                    self.logger.debug(f"初始化插件: {plugin.name}")
                except Exception as e:
                    self.logger.error(f"初始化插件 {plugin.name} 失败: {e}")
    
    async def on_tick(self, data):
        """调用所有插件的 on_tick 方法"""
        for plugin in self.plugins:
            if plugin.enabled:
                try:
                    await plugin.on_tick(data)
                except Exception as e:
                    self.logger.error(f"插件 {plugin.name} on_tick 失败: {e}")
    
    async def on_trade(self, trade_data):
        """调用所有插件的 on_trade 方法"""
        for plugin in self.plugins:
            if plugin.enabled:
                try:
                    await plugin.on_trade(trade_data)
                except Exception as e:
                    self.logger.error(f"插件 {plugin.name} on_trade 失败: {e}")
    
    async def on_error(self, error):
        """调用所有插件的 on_error 方法"""
        for plugin in self.plugins:
            if plugin.enabled:
                try:
                    await plugin.on_error(error)
                except Exception as e:
                    self.logger.error(f"插件 {plugin.name} on_error 失败: {e}")
    
    async def shutdown_plugins(self):
        """关闭所有插件"""
        for plugin in self.plugins:
            if plugin.enabled:
                try:
                    await plugin.shutdown()
                    self.logger.info(f"关闭插件: {plugin.name}")
                except Exception as e:
                    self.logger.error(f"关闭插件 {plugin.name} 失败: {e}")

# 创建全局插件管理器实例
plugin_manager = PluginManager()