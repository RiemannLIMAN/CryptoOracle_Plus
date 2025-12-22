import ccxt
import os
import traceback
from dotenv import load_dotenv

load_dotenv()

def main():
    print("Initializing exchange (Sync)...")
    exchange = ccxt.okx({
        'timeout': 30000,
        'enableRateLimit': True,
    })
    try:
        print("Loading markets...")
        exchange.load_markets(True)
        print(f"Loaded {len(exchange.markets)} markets.")
        
        target_symbol = 'PEPE/USDT:USDT'
        
        if target_symbol in exchange.markets:
            market = exchange.market(target_symbol)
            print(f"\n--- {target_symbol} ---")
            print(f"Symbol: {market['symbol']}")
            print(f"Contract: {market.get('contract')}")
            print(f"ContractSize: {market.get('contractSize')}")
            print(f"Precision: {market.get('precision')}")
            print(f"Limits: {market.get('limits')}")
        else:
            print(f"\nSymbol {target_symbol} not found.")
            # Print closely matching
            matches = [s for s in exchange.markets if 'PEPE' in s]
            print(f"Matches: {matches}")
            
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
    finally:
        pass

if __name__ == "__main__":
    main()