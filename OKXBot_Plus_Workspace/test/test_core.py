import unittest
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.plugin import Plugin, PluginManager
from core.cache import CacheManager
from core.exceptions import APIConnectionError, TradingError

class TestPluginSystem(unittest.TestCase):
    """测试插件系统"""
    
    def test_plugin_base_class(self):
        """测试插件基类"""
        # 测试插件基类是否为抽象类
        with self.assertRaises(TypeError):
            Plugin({})
    
    def test_plugin_manager_initialization(self):
        """测试插件管理器初始化"""
        manager = PluginManager()
        self.assertEqual(len(manager.plugins), 0)

class TestCacheSystem(unittest.TestCase):
    """测试缓存系统"""
    
    def test_cache_set_get(self):
        """测试缓存的设置和获取"""
        cache = CacheManager()
        key = "test_key"
        value = "test_value"
        
        # 设置缓存
        cache.set(key, value)
        # 获取缓存
        result = cache.get(key)
        
        self.assertEqual(result, value)
    
    def test_cache_expiration(self):
        """测试缓存过期"""
        cache = CacheManager()
        key = "test_key"
        value = "test_value"
        
        # 设置缓存，过期时间1秒
        cache.set(key, value, ttl=1)
        
        # 立即获取
        result = cache.get(key)
        self.assertEqual(result, value)
        
        # 等待2秒后获取
        import time
        time.sleep(2)
        result = cache.get(key)
        self.assertIsNone(result)

class TestExceptions(unittest.TestCase):
    """测试异常处理"""
    
    def test_api_connection_error(self):
        """测试API连接异常"""
        error_msg = "API connection error"
        with self.assertRaises(APIConnectionError) as context:
            raise APIConnectionError(error_msg)
        self.assertIn(error_msg, str(context.exception))
    
    def test_trading_error(self):
        """测试交易错误异常"""
        error_msg = "Trading error"
        with self.assertRaises(TradingError) as context:
            raise TradingError(error_msg)
        self.assertIn(error_msg, str(context.exception))

if __name__ == '__main__':
    unittest.main()