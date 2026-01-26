import pandas as pd
import numpy as np
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any

class MarketDataService:
    def __init__(self, exchange, data_manager, logger=None):
        self.exchange = exchange
        self.data_manager = data_manager
        self.logger = logger
        self.cache = {} # Simple memory cache

    def _log(self, message: str, level: str = 'info'):
        if self.logger:
            if level == 'debug': self.logger.debug(message)
            elif level == 'info': self.logger.info(message)
            elif level == 'warning': self.logger.warning(message)
            elif level == 'error': self.logger.error(message)
        else:
            print(f"[{level.upper()}] {message}")

    async def get_market_context(self, symbol: str, main_tf: str = '15m') -> Dict[str, Any]:
        """
        获取完整的市场上下文，包含主周期和 4H 趋势
        """
        # 1. 获取主周期数据 (15m)
        df_main = await self.fetch_and_process_ohlcv(symbol, main_tf)
        
        # 2. 获取趋势周期数据 (4h)
        # 这里即使数据库没存，也会实时拉取并计算
        df_trend = await self.fetch_and_process_ohlcv(symbol, '4h')
        
        # 3. 计算 4H 趋势
        trend_4h = "NEUTRAL"
        if df_trend is not None and not df_trend.empty:
            last_row = df_trend.iloc[-1]
            # 如果最后一根未收盘，最好参考前一根已收盘的，或者接受未收盘的实时性
            # 这里为了稳健，如果倒数第二根存在，取倒数第二根（已收盘）
            if len(df_trend) >= 2:
                last_row = df_trend.iloc[-2]
            
            ema20 = last_row.get('ema20')
            ema50 = last_row.get('ema50')
            if ema20 and ema50:
                if ema20 > ema50: trend_4h = "UP"
                elif ema20 < ema50: trend_4h = "DOWN"
        
        return {
            'main_df': df_main,
            'trend_4h': trend_4h,
            'trend_df': df_trend
        }

    async def fetch_and_process_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """
        通用的 K 线获取、合并、清洗、指标计算流程
        """
        try:
            # 1. 尝试从数据库加载近期数据 (断点续传)
            local_klines = []
            try:
                # 假设 data_manager 已经支持 timeframe 参数
                local_klines = await self.data_manager.get_recent_klines(symbol, timeframe, limit=limit)
            except Exception as e:
                self._log(f"[{timeframe}] 加载本地数据失败: {e}", 'debug')

            # 2. 从 API 拉取最新数据
            # 兼容性处理
            api_tf = '1m' if 'ms' in timeframe or timeframe.endswith('s') else timeframe
            
            ohlcv = await self.exchange.fetch_ohlcv(symbol, api_tf, limit=limit)
            if not ohlcv:
                return None
                
            df_new = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_new['timestamp'] = pd.to_datetime(df_new['timestamp'], unit='ms')

            # 3. 合并数据
            df = df_new
            if local_klines:
                df_local = pd.DataFrame(local_klines)
                df_local['timestamp'] = pd.to_datetime(df_local['timestamp'])
                # 合并并去重，保留最新的 API 数据
                df = pd.concat([df_local, df_new]).drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
            
            # 4. 计算技术指标
            df = self._calculate_indicators(df)
            
            # 5. 异步保存回数据库 (只保存最新的部分，避免全量写入)
            # 保存最近 5 根，确保覆盖可能更新的未收盘 K 线
            if self.data_manager:
                to_save = df.tail(5).reset_index(drop=True)
                # 注意：这里不 await，放后台跑
                asyncio.create_task(self.data_manager.save_klines(symbol, timeframe, to_save))
                
            return df
            
        except Exception as e:
            self._log(f"[{timeframe}] 获取/处理数据失败: {e}", 'error')
            return None

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算全套技术指标 (RSI, MACD, BB, ADX, ATR)
        复用 TradeExecutor 中的逻辑
        """
        try:
            # Ensure numeric
            cols = ['open', 'high', 'low', 'close', 'volume']
            for col in cols:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # EMA 20/50/200
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

            # RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            # MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']

            # Bollinger Bands
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ma20'] + (df['std'] * 2)
            df['lower_bb'] = df['ma20'] - (df['std'] * 2)

            # ADX
            df['tr1'] = df['high'] - df['low']
            df['tr2'] = abs(df['high'] - df['close'].shift())
            df['tr3'] = abs(df['low'] - df['close'].shift())
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            df['atr'] = df['tr'].rolling(window=14).mean() # ATR 14
            
            # ATR Ratio (Volatility Factor)
            df['atr_ma50'] = df['atr'].rolling(window=50).mean()
            df['atr_ratio'] = df['atr'] / df['atr_ma50']

            # Volume Ratio
            df['vol_ma20'] = df['volume'].rolling(window=20).mean()
            df['vol_ratio'] = df['volume'] / df['vol_ma20']
            
            # [New] OBV (On-Balance Volume)
            # Close > PrevClose => +Vol, else -Vol
            df['obv_change'] = 0.0
            df.loc[df['close'] > df['close'].shift(), 'obv_change'] = df['volume']
            df.loc[df['close'] < df['close'].shift(), 'obv_change'] = -df['volume']
            df['obv'] = df['obv_change'].cumsum()
            
            # [New] Buy Volume Proportion (Capital Flow)
            # 简单估算：阳线视为买入主导，阴线视为卖出主导
            df['is_up_candle'] = df['close'] >= df['open']
            df['up_vol'] = df['volume'].where(df['is_up_candle'], 0)
            
            # 5周期买盘占比 (0~1)
            vol_sum_5 = df['volume'].rolling(window=5).sum().replace(0, np.nan)
            df['buy_vol_prop_5'] = df['up_vol'].rolling(window=5).sum() / vol_sum_5
            df['buy_vol_prop_5'] = df['buy_vol_prop_5'].fillna(0.5)

            return df
        except Exception as e:
            self._log(f"指标计算错误: {e}", 'error')
            return df
