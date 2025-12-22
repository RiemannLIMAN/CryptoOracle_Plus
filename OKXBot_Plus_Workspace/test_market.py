import asyncio
import ccxt.async_support as ccxt
import os
import traceback
from dotenv import load_dotenv

load_dotenv()

async def main():
    print("Initializing exchange...")
    exchange = ccxt.okx({
        'timeout': 30000,
        'enableRateLimit': True,
    })
    try:
        print("Loading markets... (This may take a while)")
        # Force reload
        await exchange.load_markets(True)
        print(f"Loaded {len(exchange.markets)} markets.")
        
        target_symbol = 'PEPE/USDT:USDT'
        
        # Search for PEPE symbols
        pepe_markets = [s for s in exchange.markets if 'PEPE' in s]
        print(f"Found PEPE markets: {len(pepe_markets)}")
        print(pepe_markets[:10]) # Print first 10
        
        if target_symbol in exchange.markets:
            market = exchange.market(target_symbol)
            print(f"\n--- {target_symbol} ---")
            print(f"Symbol: {market['symbol']}")
            print(f"Id: {market['id']}")
            print(f"Contract: {market.get('contract')}")
            print(f"ContractSize: {market.get('contractSize')}")
            print(f"Precision: {market.get('precision')}")
            print(f"Limits: {market.get('limits')}")
        else:
            print(f"\nSymbol {target_symbol} not found in loaded markets.")
            
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
    finally:
        if exchange:
            await exchange.close()
        print("Exchange closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as e:
        print(f"Main Error: {e}")