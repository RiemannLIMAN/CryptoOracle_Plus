from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    @abstractmethod
    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, **kwargs):
        """
        Analyze the market and return a signal.
        
        Expected return format:
        {
            "signal": "BUY" | "SELL" | "HOLD",
            "entry_price": float (Limit price) or None (Market),
            "stop_loss": float,
            "take_profit": float,
            "amount": float,
            "reason": str,
            "confidence": "HIGH" | "MEDIUM" | "LOW"
        }
        """
        pass
