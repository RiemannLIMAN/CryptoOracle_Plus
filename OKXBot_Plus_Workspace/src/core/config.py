import os
import json
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

class Config:
    def __init__(self, config_path='config.json'):
        self.config_path = config_path
        self.data = self._load_config()

    def _load_config(self):
        try:
            # 尝试在当前目录或 src 目录下寻找 config.json
            current_dir = os.path.dirname(os.path.abspath(__file__)) # src/core
            src_dir = os.path.dirname(current_dir) # src
            root_dir = os.path.dirname(src_dir) # project root
            
            paths_to_check = [
                self.config_path, # CWD
                os.path.join(root_dir, self.config_path), # Root
                os.path.join(src_dir, self.config_path), # src (legacy)
            ]
            
            loaded_config = None
            for path in paths_to_check:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        loaded_config = json.load(f)
                    break
            
            if not loaded_config:
                return None

            # 环境变量覆盖 (严格模式：密钥必须从环境变量读取，忽略 config.json 中的残留值)
            loaded_config.setdefault('exchanges', {}).setdefault('okx', {})
            loaded_config['exchanges']['okx']['api_key'] = os.getenv('OKX_API_KEY', '')
            loaded_config['exchanges']['okx']['secret'] = os.getenv('OKX_SECRET', '')
            loaded_config['exchanges']['okx']['password'] = os.getenv('OKX_PASSWORD', '')
            
            loaded_config.setdefault('models', {}).setdefault('deepseek', {})
            loaded_config['models']['deepseek']['api_key'] = os.getenv('DEEPSEEK_API_KEY', '')
            
            # Notification Webhook 注入
            loaded_config.setdefault('notification', {})
            env_webhook = os.getenv('NOTIFICATION_WEBHOOK', '')
            if env_webhook:
                loaded_config['notification']['webhook_url'] = env_webhook
                
            return loaded_config
        except Exception as e:
            print(f"配置文件加载失败: {e}")
            return None

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]
