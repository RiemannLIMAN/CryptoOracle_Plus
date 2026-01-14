import os
import json
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

class Config:
    def __init__(self, config_path='config.json'):
        self.config_path = config_path
        self.data = self._load_config()
        if self.data:
            self._validate_config()

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
            
            # Notification Webhook 注入 - 同时注入到顶层和 trading 子目录，确保兼容性
            env_webhook = os.getenv('NOTIFICATION_WEBHOOK', '')
            
            # 注入到顶层
            loaded_config.setdefault('notification', {})
            if env_webhook:
                loaded_config['notification']['webhook_url'] = env_webhook
                # [Fix] 如果环境变量里配了 webhook，强制开启通知功能
                loaded_config['notification']['enabled'] = True
            
            # 注入到 trading 子目录，确保交易执行器能读取到
            loaded_config.setdefault('trading', {})
            loaded_config['trading'].setdefault('notification', {})
            if env_webhook:
                loaded_config['trading']['notification']['webhook_url'] = env_webhook
                loaded_config['trading']['notification']['enabled'] = True
            # 如果顶层有配置，同步到 trading 子目录
            elif 'notification' in loaded_config:
                loaded_config['trading']['notification'].update(loaded_config['notification'])
            
            # 如果环境变量里没有，但 config.json 里有 enabled=true 且有 url，则保持原样
            # 但如果 config.json 里 enabled=false，上面这行强制开启会覆盖它，这通常是符合预期的
            # 因为只要用户特意去配了 .env，通常就是想用。
                
            return loaded_config
        except Exception as e:
            print(f"配置文件加载失败: {e}")
            return None

    def _validate_config(self):
        """验证配置文件的有效性"""
        try:
            # 验证基本结构
            required_sections = ['trading', 'symbols']
            for section in required_sections:
                if section not in self.data:
                    print(f"配置文件缺少必要部分: {section}")
                    return False
            
            # 验证交易对配置
            symbols = self.data['symbols']
            if not isinstance(symbols, list) or len(symbols) == 0:
                print("配置文件中的交易对列表为空")
                return False
            
            for symbol_config in symbols:
                if 'symbol' not in symbol_config:
                    print("交易对配置缺少 symbol 字段")
                    return False
                if 'leverage' not in symbol_config:
                    print(f"交易对 {symbol_config['symbol']} 缺少 leverage 字段")
                    return False
            
            # 验证 API 密钥
            okx_config = self.data.get('exchanges', {}).get('okx', {})
            deepseek_config = self.data.get('models', {}).get('deepseek', {})
            
            if not okx_config.get('api_key'):
                print("警告: OKX API 密钥未配置")
            if not deepseek_config.get('api_key'):
                print("警告: DeepSeek API 密钥未配置")
            
            # 验证交易配置
            trading_config = self.data['trading']
            if 'timeframe' not in trading_config:
                print("警告: 未配置交易周期，使用默认值 1m")
                trading_config['timeframe'] = '1m'
            
            if 'test_mode' not in trading_config:
                trading_config['test_mode'] = True
                print("警告: 未配置测试模式，默认开启")
            
            return True
        except Exception as e:
            print(f"配置验证失败: {e}")
            return False

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]
