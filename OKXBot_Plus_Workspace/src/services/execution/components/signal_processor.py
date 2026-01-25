import pandas as pd

class SignalProcessor:
    def __init__(self, logger):
        self.logger = logger

    def check_technical_filters(self, signal_type, indicators):
        """
        [New] 硬性技术过滤 (Hard Technical Filters)
        目标: 胜率 > 60%。宁可踏空，不可亏损。
        """
        if not indicators:
            return True, "无指标数据，跳过过滤"
            
        rsi = indicators.get('rsi')
        
        # 1. RSI 极端值过滤 (防止追涨杀跌)
        if rsi is not None:
            if signal_type == 'BUY':
                # 除非是超强趋势(ADX>40)，否则 RSI > 70 禁止追多
                if rsi > 70:
                    adx = indicators.get('adx', 0) or 0
                    if adx < 40:
                        return False, f"RSI超买 ({rsi:.1f}) 且趋势未爆发 (ADX {adx:.1f})，禁止追多"
            elif signal_type == 'SELL':
                # 除非是超强趋势(ADX>40)，否则 RSI < 30 禁止追空
                if rsi < 30:
                    adx = indicators.get('adx', 0) or 0
                    if adx < 40:
                        return False, f"RSI超卖 ({rsi:.1f}) 且趋势未爆发 (ADX {adx:.1f})，禁止追空"

        # 2. 波动率过滤 (ATR Ratio)
        atr_ratio = indicators.get('atr_ratio', 1.0)
        # [Config] ATR 阈值提高到 1.0 (平均水平)，拒绝死鱼盘
        if atr_ratio < 1.0:
            return False, f"波动率过低 (ATR Ratio {atr_ratio:.2f} < 1.0)，属于死鱼盘"

        # 3. 成交量过滤 (Volume Ratio)
        # [Config] 要求成交量至少达到过去均值的 80%
        vol_ratio = indicators.get('vol_ratio', 1.0)
        if vol_ratio < 0.8:
            return False, f"成交量低迷 (Vol Ratio {vol_ratio:.2f} < 0.8)，流动性不足"

        return True, "通过"

    def check_candlestick_pattern(self, data_input, indicators=None):
        """
        [Hardcore] Python 硬核识别 "三线战法" (Three-Line Strike)
        支持输入: DataFrame 或 包含 'kline_data' 的字典
        [Update] 增加 indicators 参数，用于环境过滤 (Market Regime Filter)
        """
        # [Market Regime Filter] 仅在趋势行情中启用三线战法
        if indicators:
            adx = indicators.get('adx', 0)
            if adx < 20:
                try:
                    self.logger.info(f"三线过滤: ADX {adx} < 20")
                except Exception:
                    pass
                return None

        df = None
        try:

            # 1. 如果输入是 DataFrame，直接使用
            if isinstance(data_input, pd.DataFrame):
                df = data_input
            # 2. 如果输入是字典 (price_data)，尝试提取 df 或 kline_data
            elif isinstance(data_input, dict):
                if 'df' in data_input and isinstance(data_input['df'], pd.DataFrame):
                    df = data_input['df']
                elif 'kline_data' in data_input:
                    # Fallback: 从 kline_data (list of dicts) 重构 DataFrame
                    df = pd.DataFrame(data_input['kline_data'])
            
            if df is None or len(df) < 4:
                try:
                    self.logger.info("三线过滤: K线不足4根")
                except Exception:
                    pass
                return None
            
            # 获取最近 4 根 K 线
            last_4 = df.iloc[-4:].copy()
            k1, k2, k3, k4 = last_4.iloc[0], last_4.iloc[1], last_4.iloc[2], last_4.iloc[3]
            
            def is_bull(k): return float(k['close']) > float(k['open'])
            def is_bear(k): return float(k['close']) < float(k['open'])
            
            # --- 识别看涨三线 (Bullish Strike) ---
            # 条件: 三连阴 (下台阶) + 一阳吞三阴
            if (is_bear(k1) and is_bear(k2) and is_bear(k3) and is_bull(k4)):
                if (float(k2['low']) < float(k1['low']) and float(k3['low']) < float(k2['low'])):
                    if float(k4['close']) > float(k1['open']):
                        # [Strict Standard] 成交量严格确认: 第4根K线的成交量必须大于前三根中的最大值
                        # 这代表了真正的“爆量吞没”，有效防止假突破
                        vol4 = float(k4.get('volume', 0))
                        max_vol3 = max(float(k1.get('volume', 0)), float(k2.get('volume', 0)), float(k3.get('volume', 0)))
                        
                        if vol4 > max_vol3:
                            # [Optimization] 返回具体的 SL/TP 价格点位
                            # 止损 (看涨): 取四根K线的【最低价】作为硬止损
                            sl = min(float(k1['low']), float(k2['low']), float(k3['low']), float(k4['low']))
                            # 止盈: 2倍风险收益比 (Entry + (Entry-SL)*2)
                            entry = float(k4['close'])
                            tp = entry + (entry - sl) * 2
                            return 'BULLISH_STRIKE', {'sl': sl, 'tp': tp}
                        else:
                            try:
                                self.logger.info(f"三线弱信号(量能不足): vol4 {vol4:.2f} <= max_prev {max_vol3:.2f}")
                            except Exception:
                                pass
            
            # --- 识别看跌三线 (Bearish Strike) ---
            # 条件: 三连阳 (上台阶) + 一阴吞三阳
            if (is_bull(k1) and is_bull(k2) and is_bull(k3) and is_bear(k4)):
                if (float(k2['high']) > float(k1['high']) and float(k3['high']) > float(k2['high'])):
                    if float(k4['close']) < float(k1['open']):
                        # [Strict Standard] 成交量严格确认
                        vol4 = float(k4.get('volume', 0))
                        max_vol3 = max(float(k1.get('volume', 0)), float(k2.get('volume', 0)), float(k3.get('volume', 0)))
                        
                        if vol4 > max_vol3:
                            # [Optimization] 返回具体的 SL/TP 价格点位
                            # 止损 (看跌): 取四根K线的【最高价】作为硬止损
                            sl = max(float(k1['high']), float(k2['high']), float(k3['high']), float(k4['high']))
                            # 止盈: 2倍风险收益比 (Entry - (SL-Entry)*2)
                            entry = float(k4['close'])
                            tp = entry - (sl - entry) * 2
                            return 'BEARISH_STRIKE', {'sl': sl, 'tp': tp}
                        else:
                            try:
                                self.logger.info(f"三线弱信号(量能不足): vol4 {vol4:.2f} <= max_prev {max_vol3:.2f}")
                            except Exception:
                                pass
                        
        except Exception as e:
            pass
            
        return None, {}
