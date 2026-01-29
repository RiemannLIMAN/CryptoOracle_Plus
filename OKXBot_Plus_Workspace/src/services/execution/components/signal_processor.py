import pandas as pd

class SignalProcessor:
    def __init__(self, logger):
        self.logger = logger

    def check_technical_filters(self, signal_type, indicators):
        """
        检查技术指标过滤条件 (ATR, Volume, RSI, ADX)
        [v3.9.6 Optimized] 软过滤机制：不再直接拦截 (return False)，而是降级信心 (LOW)。
        """
        reason = []
        is_valid = True  # 默认为 True，仅在极度恶劣情况下才 False
        
        try:
            # 1. ATR 波动率过滤 (ATR_Ratio < 1.0 -> 波动过小，或者是死鱼)
            atr_ratio = indicators.get('atr_ratio', 1.0)
            if atr_ratio < 1.0:
                # [Optimized] 不拦截，改为标记低信心
                # is_valid = False
                reason.append(f"低波动(ATR:{atr_ratio:.1f})->降级信心")
                
            # 2. 成交量过滤 (Vol_Ratio < 0.8 -> 量能不足)
            vol_ratio = indicators.get('vol_ratio', 1.0)
            if vol_ratio < 0.8:
                # [Optimized] 不拦截，改为标记低信心
                # is_valid = False
                reason.append(f"低量(Vol:{vol_ratio:.1f})->降级信心")
                
            # 3. RSI 极端值过滤 (超买/超卖区域禁止追单，但允许反转)
            rsi = indicators.get('rsi', 50)
            if signal_type == 'BUY' and rsi > 75:
                # 追涨风险极大，建议拦截
                is_valid = False
                reason.append(f"RSI超买({rsi:.0f})禁止追多")
            elif signal_type == 'SELL' and rsi < 25:
                # 追空风险极大，建议拦截
                is_valid = False
                reason.append(f"RSI超卖({rsi:.0f})禁止追空")
                
            # 4. ADX 趋势强度过滤 (ADX < 20 -> 无趋势震荡)
            # [Optimized] 提高阈值至 20 (原 15)
            adx = indicators.get('adx', 20)
            if adx < 20:
                reason.append(f"弱趋势(ADX:{adx:.0f})->降级信心")
                
        except Exception as e:
            pass
            
        return is_valid, " | ".join(reason)

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
                            # [Optimization] 放大硬性止盈目标至 5 倍 (R:R = 1:5)
                            # 原理: 硬性 TP 仅作为"梦想目标"，实际离场交由 Level 3 移动止损 (Dynamic Trailing) 接管
                            # 这样可以防止在趋势行情中过早止盈 (卖飞)
                            entry = float(k4['close'])
                            tp = entry + (entry - sl) * 5
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
                            # [Optimization] 放大硬性止盈目标至 5 倍 (R:R = 1:5)
                            entry = float(k4['close'])
                            tp = entry - (sl - entry) * 5
                            return 'BEARISH_STRIKE', {'sl': sl, 'tp': tp}
                        else:
                            try:
                                self.logger.info(f"三线弱信号(量能不足): vol4 {vol4:.2f} <= max_prev {max_vol3:.2f}")
                            except Exception:
                                pass
                        
        except Exception as e:
            pass
            
        return None, {}
