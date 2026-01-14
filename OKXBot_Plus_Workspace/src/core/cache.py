import time
from typing import Dict, Any, Optional

class CacheManager:
    """
    缓存管理器，用于缓存API请求结果
    """
    def __init__(self, default_ttl=60):
        """
        初始化缓存管理器
        
        Args:
            default_ttl: 默认缓存过期时间（秒）
        """
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl
    
    def generate_key(self, prefix: str, **kwargs) -> str:
        """
        生成缓存键
        
        Args:
            prefix: 缓存键前缀
            **kwargs: 缓存键参数
        
        Returns:
            生成的缓存键
        """
        parts = [prefix]
        for key, value in sorted(kwargs.items()):
            parts.append(f"{key}:{value}")
        return ":".join(parts)
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        设置缓存
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 缓存过期时间（秒），None表示使用默认值
        """
        ttl = ttl or self.default_ttl
        self.cache[key] = {
            'value': value,
            'expires_at': time.time() + ttl
        }
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存
        
        Args:
            key: 缓存键
        
        Returns:
            缓存值，如果缓存不存在或已过期则返回None
        """
        if key not in self.cache:
            return None
        
        item = self.cache[key]
        if time.time() > item['expires_at']:
            del self.cache[key]
            return None
        
        return item['value']
    
    def delete(self, key: str) -> None:
        """
        删除缓存
        
        Args:
            key: 缓存键
        """
        if key in self.cache:
            del self.cache[key]
    
    def clear(self, prefix: Optional[str] = None) -> None:
        """
        清空缓存
        
        Args:
            prefix: 缓存键前缀，如果提供则只清空该前缀的缓存
        """
        if prefix:
            keys_to_delete = [key for key in self.cache if key.startswith(prefix)]
            for key in keys_to_delete:
                del self.cache[key]
        else:
            self.cache.clear()
    
    def get_size(self) -> int:
        """
        获取缓存大小
        
        Returns:
            缓存大小
        """
        return len(self.cache)

# 创建全局缓存管理器实例
cache_manager = CacheManager()
