import asyncio
import os
import sys
import ccxt.async_support as ccxt
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Add src to path
# test/test_connection.py -> test -> root -> src
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
src_path = os.path.join(project_root, 'src')
sys.path.append(src_path)

from core.config import Config
from core.utils import setup_logger, send_notification_async

async def test_okx(config):
    print("\n🔍 Testing OKX Connection...")
    okx_conf = config['exchanges']['okx']
    exchange = ccxt.okx({
        'apiKey': okx_conf['api_key'],
        'secret': okx_conf['secret'],
        'password': okx_conf['password'],
        'enableRateLimit': True
    })
    
    if config['trading'].get('proxy'):
        exchange.aiohttp_proxy = config['trading']['proxy']
        print(f"   Proxy enabled: {config['trading']['proxy']}")

    try:
        balance = await exchange.fetch_balance()
        print("✅ OKX Connection Successful!")
        if 'USDT' in balance:
            print(f"   USDT Balance: {balance['USDT']['free']}")
        elif 'info' in balance and 'data' in balance['info']:
             # Unified account
             data = balance['info']['data'][0]
             print(f"   Unified Equity: {data.get('totalEq', 'N/A')} USD")
    except Exception as e:
        print(f"❌ OKX Connection Failed: {e}")
    finally:
        await exchange.close()

async def test_deepseek(config):
    print("\n🔍 Testing DeepSeek Connection...")
    ds_conf = config['models']['deepseek']
    client = AsyncOpenAI(
        api_key=ds_conf['api_key'],
        base_url=ds_conf.get('base_url', "https://api.deepseek.com/v1")
    )
    
    try:
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "Ping"}],
            max_tokens=5
        )
        print(f"✅ DeepSeek Connection Successful! Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"❌ DeepSeek Connection Failed: {e}")

async def test_notification(config):
    print("\n🔍 Testing Notification...")
    webhook = config['notification'].get('webhook_url')
    if not webhook or "YOUR_WEBHOOK" in webhook:
        print("⚠️ Notification skipped (No webhook configured)")
        return
        
    print(f"   Target: {webhook}")
    await send_notification_async(webhook, "🔔 This is a test message from CryptoOracle diagnostic tool.")
    print("✅ Notification sent (Check your app)")

async def main():
    print("==========================================")
    print("🛠️ CryptoOracle Diagnostic Tool")
    print("==========================================")
    
    # Load Config
    try:
        config = Config()
        if not config.data:
            print("❌ Config load failed")
            return
        print("✅ Configuration loaded")
    except Exception as e:
        print(f"❌ Config Error: {e}")
        return

    await test_okx(config)
    await test_deepseek(config)
    await test_notification(config)
    
    print("\n==========================================")
    print("Done.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
