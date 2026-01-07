import sqlite3
import pandas as pd
import logging
import os
from datetime import datetime
import asyncio
import aiosqlite

class DataManager:
    def __init__(self, db_path="data/trade_data.db"):
        self.db_path = db_path
        self.logger = logging.getLogger("data_manager")
        self._ensure_data_dir()
        
    def _ensure_data_dir(self):
        directory = os.path.dirname(self.db_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

    async def initialize(self):
        """初始化数据库表结构"""
        async with aiosqlite.connect(self.db_path) as db:
            # 1. K线表 (存储最近的K线和指标)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS klines (
                    symbol TEXT,
                    timeframe TEXT,
                    timestamp DATETIME,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    rsi REAL,
                    adx REAL,
                    atr REAL,
                    macd REAL,
                    volatility_status TEXT,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
            """)
            
            # 2. 信号表 (存储 AI 的决策记录)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    signal TEXT,
                    confidence TEXT,
                    reason TEXT,
                    price REAL,
                    amount REAL,
                    status TEXT,
                    pnl REAL
                )
            """)
            
            # 3. 交易表 (存储实际成交记录)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    side TEXT,
                    price REAL,
                    amount REAL,
                    cost REAL,
                    fee REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
            self.logger.info(f"💾 数据库已初始化: {self.db_path}")

    async def save_klines(self, symbol, timeframe, df):
        """
        保存 K 线数据 (增量更新)
        df: 包含 kline 数据和计算好的指标
        """
        if df.empty: return
        
        # 转换数据为 tuple 列表
        records = []
        for _, row in df.iterrows():
            # 确保指标字段存在，不存在填 None
            rsi = row.get('rsi') if pd.notna(row.get('rsi')) else None
            adx = row.get('adx') if pd.notna(row.get('adx')) else None
            atr = row.get('atr') if pd.notna(row.get('atr')) else None
            macd = row.get('macd') if pd.notna(row.get('macd')) else None
            # 从外部传入或 df 中获取 status，如果没有则为空
            status = row.get('volatility_status') 
            
            records.append((
                symbol, timeframe, row['timestamp'].to_pydatetime(),
                row['open'], row['high'], row['low'], row['close'], row['volume'],
                rsi, adx, atr, macd, status
            ))
            
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT OR REPLACE INTO klines 
                (symbol, timeframe, timestamp, open, high, low, close, volume, rsi, adx, atr, macd, volatility_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            await db.commit()

    async def save_signal(self, symbol, signal_data, price):
        """保存 AI 信号记录"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO signals (symbol, signal, confidence, reason, price, amount, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, 
                signal_data.get('signal'),
                signal_data.get('confidence'),
                signal_data.get('reason'),
                price,
                signal_data.get('amount'),
                'CREATED'
            ))
            await db.commit()

    async def get_recent_klines(self, symbol, timeframe, limit=200):
        """
        [Data Resume] 断点续传：获取最近的 K 线数据
        用于机器人重启后快速恢复状态，减少对交易所 API 的依赖
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(f"""
                    SELECT * FROM klines 
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (symbol, timeframe, limit)) as cursor:
                    rows = await cursor.fetchall()
                    if not rows: return []
                    
                    # 转换回字典列表，注意时间序 (DESC -> ASC)
                    data = []
                    for row in reversed(rows):
                        item = dict(row)
                        # 确保 timestamp 是 datetime 对象或字符串，视后续处理而定
                        # SQLite 存的是字符串，这里保持字符串或转为 pd.Timestamp
                        data.append(item)
                    return data
        except Exception as e:
            self.logger.error(f"读取历史数据失败: {e}")
            return []

    async def export_to_parquet(self, symbol, timeframe, output_dir="data/archive"):
        """
        [Data Processing] 定期归档：将旧数据导出为 Parquet
        Parquet 是列式存储，非常适合 Pandas 读取和大数据分析
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(f"""
                SELECT * FROM klines 
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp ASC
            """, (symbol, timeframe)) as cursor:
                # 获取列名
                cols = [description[0] for description in cursor.description]
                rows = await cursor.fetchall()
                
                if rows:
                    df = pd.DataFrame(rows, columns=cols)
                    filename = f"{symbol.replace('/', '_')}_{timeframe}_{datetime.now().strftime('%Y%m%d')}.parquet"
                    path = os.path.join(output_dir, filename)
                    df.to_parquet(path, compression='snappy')
                    self.logger.info(f"📦 数据已归档至: {path}")
                    
                    # 可选：归档后清理数据库中的旧数据 (保留最近 1000 条)
                    # await db.execute(...) 
