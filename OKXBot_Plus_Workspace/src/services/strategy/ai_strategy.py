import json
import logging
import time
from openai import AsyncOpenAI
import httpx
from core.utils import to_float

class DeepSeekAgent:
    def __init__(self, api_key, base_url="https://api.deepseek.com/v1", proxy=None):
        self.logger = logging.getLogger("crypto_oracle")
        
        client_params = {
            'api_key': api_key,
            'base_url': base_url,
            'max_retries': 2  # [Fix] 增加重试次数，防止网络微抖动导致分析失败
        }
        if proxy:
            client_params['http_client'] = httpx.AsyncClient(proxies=proxy)
            
        self.client = AsyncOpenAI(**client_params)

    def _get_role_prompt(self, volatility_status="NORMAL"):
        # 基础角色设定 (纯静态，利用缓存加速)
        base_role = "身份: 具备机构视角的顶级加密货币狙击手 (Institutional Crypto Sniper)。\n"
        base_role += "核心能力: 能够识别市场噪音与真实信号，擅长在极端行情中保持绝对冷静。\n"
        base_role += "当前目标: **盈亏比优先 (R:R Ratio > 2.0)**。追求单边暴利，宁可止损 3 次，也要抓住 1 次大趋势。\n"
        
        # [New] 动态人格注入 (Dynamic Persona Injection) - 复刻 V2 经典逻辑
        if volatility_status == "HIGH_TREND":
            base_role += "【当前模式: 激进趋势猎人 (Aggressive Trend Hunter)】\n市场处于单边剧烈波动。**必须**紧咬趋势，果断全仓出击。不要在意短期回调，重点关注 '量价共振' 和 '关键位突破'。\n"
        elif volatility_status == "HIGH_CHOPPY":
            base_role += "【当前模式: 风控卫士 (Risk Guardian)】\n市场处于剧烈震荡，多空分歧巨大。请切换为'均值回归'思维，严禁追单。仅在价格触及布林带外轨或极端超买超卖时，执行反向猎杀（Mean Reversion）。\n"
        elif volatility_status == "LOW":
            base_role += "【当前模式: 网格交易员 (Grid Trader)】\n市场横盘震荡 (垃圾时间)。请寻找区间低买高卖的机会，切勿追涨杀跌。利用微小波动积累利润。\n"
        else:
            base_role += "【当前模式: 日内交易员 (Day Trader)】\n市场波动正常，趋势未爆发 (ADX < 30)。请平衡风险与收益，专注于K线形态和关键位博弈，拒绝追涨。\n"
            
        base_role += """
任务: 账户翻倍挑战 (Alpha Generation)。你管理着一笔全仓资金，必须在极短时间内捕捉趋势，实现资产的快速增值。
风格: 极度激进、敢于重仓、不知疲倦。
原则:
1. **盈亏比至上**: 只要潜在收益 > 风险的 2 倍，且趋势结构完整，就可以开仓。不要过分纠结胜率。
2. **多空双杀**: 
   - 价格站上布林中轨 + MACD金叉 -> 坚决做多。
   - 价格跌破布林中轨 + MACD死叉 -> 坚决做空。
3. **本金即子弹**: 每一分钱都是你的子弹，必须打出去才能消灭敌人。不要让资金闲置。
4. **猎杀陷阱**: 狙击手最喜欢猎杀那些被"假突破"困住的散户。重点关注"诱多"和"诱空"形态。
5. **信心分级**:
   - HIGH: 完美形态 + 关键位突破/回踩 + 量能配合 (R:R > 3)。
   - MEDIUM: 趋势对头，指标共振 (R:R > 2)。
   - LOW: 震荡或不明朗 (R:R < 1.5) -> 只有这种情况下才 HOLD。

【狙击手战术手册 (Tactical Playbook)】
1. **突破战法 (Breakout)**: 价格突破关键阻力位。即使缩量，只要站稳，也可以试仓。
2. **三线战法**: 出现 "Bullish/Bearish Strike" 形态，必须全仓出击。
3. **拒绝无效震荡**: 只有当布林带极度收口且成交量枯竭时，才允许观望。否则只要有波动，就要寻找交易机会。

【输出格式要求】
你必须严格只返回一个合法的 JSON 对象，不要包含任何 Markdown 标记或解释文字。格式如下：
{
    "signal": "BUY" | "SELL" | "HOLD",
    "reason": "核心逻辑(100字内，请用你最专业的术语直击要害)",
    "summary": "看板摘要(40字内)",
    "stop_loss": 止损价格(数字，0表示不设置),
    "take_profit": 止盈价格(数字，0表示不设置),
    "confidence": "HIGH" | "MEDIUM" | "LOW",
    "amount": 建议交易数量,
    "direction_prediction": {
        "trend": "UP" | "DOWN" | "SIDEWAYS",
        "timeframe": "4H",
        "probability": 0-100
    }
}
"""
        return base_role

    def _build_hard_constraints(self, taker_fee_rate, leverage):
        """
        构建客观约束提示词
        """
        fee_pct = taker_fee_rate * 100
        break_even = fee_pct * 2
        
        return f"""
        【客观约束 (Hard Constraints)】
        1. **成本线**: Taker费率 {fee_pct:.3f}%。任何建议的开仓，其预期浮盈必须能覆盖 >{break_even:.3f}% 的成本，否则就是给交易所打工。
        2. **风控线**: 当前杠杆 {leverage}x。请自行计算爆仓风险，并给出合理的止损位。
        3. **最小单**: 若资金不足，系统会自动拒绝，你无需担心，只需专注于策略本身。
        """

    def _build_profit_first_instruction(self, volatility_status, break_even):
        """
        构建盈利优先指令提示词
        """
        if volatility_status == "LOW":
             return f"""
        【盈利优先原则 (Profit First) - 网格模式】
        1. **区间套利**: 当前市场处于震荡期，请利用微小波动积累利润。
        2. **不止盈原则**: 除非触及布林带上轨阻力位，否则不设固定止盈，让利润奔跑 (Run Profits)。
        3. **高抛低吸**: 在布林带下轨/支撑位买入，在上轨/压力位卖出。
        """
        elif volatility_status == "HIGH_CHOPPY":
             return """
        【盈利优先原则 (Profit First) - 均值回归模式】
        1. **极端猎杀**: 市场处于剧烈震荡。严禁追涨杀跌！只做"均值回归" (Mean Reversion)。
        2. **反向操作**: 价格触及布林带上轨/超买区 -> **SELL** (做空)；触及下轨/超卖区 -> **BUY** (做多)。
        3. **不止盈原则**: 不设固定 TP，依赖移动止损 (Trailing Stop) 锁住利润。
        """
        else:
             return f"""
        【盈利优先原则 (Profit First) - 趋势模式】
        1. **无限利润 (No Take Profit)**: 我们的策略是"截断亏损，让利润奔跑"。**严禁设置固定止盈位 (TP=0)**。
        2. **移动止损**: 依靠后端的 Trailing Stop 来保护利润。你只需要关注何时趋势反转或触发硬止损。
        3. **趋势共振**: 在开新仓前，必须确认 大周期(趋势) 与 小周期(入场点) 共振。
        """

    def _build_funding_instruction(self, funding_rate):
        """
        构建资金费率指令提示词
        """
        funding_instruction = ""
        abs_fr = abs(funding_rate)
        if abs_fr > 0.0005: # > 0.05% (通常是 0.01%)
            if funding_rate > 0: # 费率为正，多头太挤，做多要付巨额利息
                funding_instruction = """
        ⚠️ **资金费率过热警报 (Funding Rate Overheat)**
        当前资金费率为正且极高 (多头拥挤)。
        1. **严禁开多 (No Long)**: 做多不仅要付高额利息，还极易被庄家"杀多头" (Long Squeeze)。
        2. **优先做空 (Short Bias)**: 市场有极高的回调需求以平抑费率。寻找做空机会。
        """
            else: # 费率为负，空头太挤
                funding_instruction = """
        ⚠️ **资金费率过冷警报 (Negative Funding Rate)**
        当前资金费率为负且极高 (空头拥挤)。
        1. **严禁开空 (No Short)**: 做空要付高额利息，极易被"逼空" (Short Squeeze)。
        2. **优先做多 (Long Bias)**: 市场有极高的反弹需求。寻找做多机会。
        """
        return funding_instruction

    def _build_risk_message(self, current_account_pnl, risk_control):
        """
        构建风险提示信息
        """
        max_profit_usdt = risk_control.get('max_profit_usdt', 0)
        max_loss_usdt = risk_control.get('max_loss_usdt', 0)
        risk_msg = ""
        
        if current_account_pnl != 0:
            risk_msg += f"- 当前账户总盈亏: {current_account_pnl:+.2f} U\n"
        
        if max_profit_usdt > 0:
            risk_msg += f"- 目标总止盈: +{max_profit_usdt} U"
            if current_account_pnl < max_profit_usdt:
                risk_msg += f" (距离目标还差: {max_profit_usdt - current_account_pnl:.2f} U)\n"
            else:
                risk_msg += " (🎉 已达成目标! 建议落袋为安)\n"
        
        if max_loss_usdt > 0: # 注意配置里通常是正数表示亏损额度，或者0禁用。这里假设配置是正数
            risk_msg += f"- 强制总止损: -{max_loss_usdt} U\n"
        
        return risk_msg

    def _build_closing_instruction(self, current_account_pnl, current_pos, risk_control, dynamic_tp=True):
        """
        构建平仓指令提示词
        """
        closing_instruction = ""
        max_profit_usdt = risk_control.get('max_profit_usdt', 0)
        
        if dynamic_tp and max_profit_usdt > 0:
            progress = current_account_pnl / max_profit_usdt
            if progress >= 1.0:
                 closing_instruction = "🔴 **最高优先级指令**：目标已达成！请立即建议 SELL (平仓) 或 HOLD (空仓)，严禁开新仓。"
            elif progress > 0.7:
                 closing_instruction = "🟠 **盈利保护指令**：目标接近完成 (>70%)。若市场走势不明朗或ADX下降，请优先选择 SELL 落袋为安，放弃鱼尾行情。"
        
        # 亏损/反手提示
        if current_pos and current_pos.get('unrealized_pnl', 0) < 0:
             pnl_val = current_pos['unrealized_pnl']
             closing_instruction += f"\n🔴 **亏损警报**：当前持仓浮亏 {pnl_val:.2f} U。请严格评估趋势是否已反转！如果确认趋势反转（如多单遇暴跌），请立即建议 SELL 并注明 '反手' 或 'Flip'。"
        
        return closing_instruction

    def _build_signal_definition(self, current_pos):
        """
        构建信号定义提示词
        """
        signal_def_msg = ""
        if current_pos and current_pos['side'] == 'short':
             signal_def_msg = """
        ⚠️ **当前持有空单 (Short)，请注意信号定义**:
        - **BUY** = 平空 (Close Short) / 反手开多。
          * 如果只想平空(Empty)，请设置 amount=0。
          * 如果想反手做多(Flip)，请设置 amount>0 (新多单数量)。
        - **SELL** = 加仓空单 (Pyramiding)。如果已满仓，SELL 信号将被忽略。
             """
        elif current_pos and current_pos['side'] == 'long':
             signal_def_msg = """
        ⚠️ **当前持有多单 (Long)，请注意信号定义**:
        - **SELL** = 平多 (Close Long) / 反手开空。
          * 如果只想平多(Empty)，请设置 amount=0。
          * 如果想反手开空(Flip)，请设置 amount>0 (新空单数量)。
        - **BUY** = 加仓多单 (Pyramiding)。如果已满仓，BUY 信号将被忽略。
             """
        return signal_def_msg

    def _build_kline_text(self, price_data, timeframe):
        """
        构建K线数据提示词
        """
        kline_count = len(price_data.get('kline_data', []))
        kline_text = f"【最近{kline_count}根{timeframe}K线数据】(时间倒序: 最新 -> 最旧)\n"
        # 稍微优化一下K线展示，只展示最近 15 根详细数据，避免 Token 过多，剩下的总结
        detailed_klines = price_data['kline_data'][-15:]
        for i, kline in enumerate(reversed(detailed_klines)): # 倒序展示更符合直觉
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            trend = "阳" if kline['close'] > kline['open'] else "阴"
            # 显示成交量和量比
            vol_str = f"Vol:{int(kline['volume'])}"
            if 'vol_ratio' in kline and kline['vol_ratio'] is not None:
                vr = kline['vol_ratio']
                if vr > 2.0: vol_str += f"(🔥爆量 x{vr:.1f})"
                elif vr > 1.2: vol_str += f"(放量 x{vr:.1f})"
                elif vr < 0.6: vol_str += f"(缩量 x{vr:.1f})"
            
            kline_text += f"T-{i}: {trend} O:{kline['open']:.4f} H:{kline['high']:.4f} L:{kline['low']:.4f} C:{kline['close']:.4f} ({change:+.2f}%) {vol_str}\n"
        
        if kline_count > 15:
            kline_text += f"...(更早的 {kline_count-15} 根K线已省略，但请基于整体结构分析)..."
        
        return kline_text

    def _build_indicator_text(self, price_data):
        """
        构建技术指标提示词
        """
        ind = price_data.get('indicators', {})
        rsi_str = f"{ind.get('rsi', 'N/A'):.2f}" if ind.get('rsi') else "N/A"
        macd_str = f"MACD: {ind.get('macd', 'N/A'):.4f}, Sig: {ind.get('macd_signal', 'N/A'):.4f}" if ind.get('macd') else "N/A"
        adx_str = f"{ind.get('adx', 'N/A'):.2f}" if ind.get('adx') else "N/A"
        atr_str = f"{ind.get('atr', 'N/A'):.4f}" if ind.get('atr') else "N/A"
        bb_str = f"Up: {ind.get('bb_upper', 'N/A'):.2f}, Low: {ind.get('bb_lower', 'N/A'):.2f}"
        
        # 成交量概况
        vol_ratio_val = ind.get('vol_ratio', 1.0)
        vol_status = "正常"
        if vol_ratio_val > 2.0: vol_status = "🔥 极度放量"
        elif vol_ratio_val > 1.5: vol_status = "📈 显著放量"
        elif vol_ratio_val < 0.5: vol_status = "📉 极度缩量"
        
        # 资金流向 (OBV & 买盘占比)
        obv_val = f"{ind.get('obv', 'N/A')}"
        buy_prop = ind.get('buy_prop', 0.5)
        buy_prop_str = f"{buy_prop*100:.1f}%"
        flow_status = "均衡"
        if buy_prop > 0.6: flow_status = "🟢 买盘主导"
        elif buy_prop < 0.4: flow_status = "🔴 卖盘主导"
        
        # 波动率因子 (ATR Ratio)
        atr_ratio_val = ind.get('atr_ratio', 1.0)
        volatility_factor_status = "正常"
        if atr_ratio_val < 0.5: volatility_factor_status = "💤 极度萎缩 (死鱼盘)"
        elif atr_ratio_val > 2.0: volatility_factor_status = "🌊 极度活跃 (巨浪)"
        
        return f"""【技术指标】
        RSI(14): {rsi_str}
        MACD: {macd_str}
        Bollinger: {bb_str}
        ADX(14): {adx_str} (趋势强度 >30为强) | ATR(14): {atr_str}
        Volatility Factor: ATR Ratio {atr_ratio_val:.2f} ({volatility_factor_status})
        Volume: 当前量比 {vol_ratio_val:.2f} ({vol_status})
        Capital Flow: 买盘占比 {buy_prop_str} ({flow_status}) | OBV: {obv_val} (能量潮)"""

    def _build_fund_status_message(self, balance, price_data):
        """
        构建资金状态提示词
        """
        min_notional_info = price_data.get('min_notional_info', '5.0')
        min_limit_info = price_data.get('min_limit_info', '0.0001') # Default value as fallback
        
        min_notional_val = to_float(min_notional_info) or 5.0
        fund_status_msg = ""
        # 这里的 balance 是可用余额 (Avail)。如果 < 5U，说明真的没钱了
        if balance < min_notional_val:
            fund_status_msg = f"""
        ⚠️ **状态更新：资金已满仓 (Full Position)**
        当前可用余额 ({balance:.2f} U) 已耗尽，说明资金利用率已达 100%。
        
        【你的决策逻辑需调整】：
        1. **关于加仓 (BUY)**：虽然你仍可以建议 BUY (表达你看涨的信心)，但请知悉系统将无法执行，会显示 "🔒 满仓持有"。
        2. **重点转向 (Focus)**：请把注意力从 "寻找买点" 转移到 "持仓管理" 和 "寻找卖点"。
        3. **风险评估**：既然已满仓，风险敞口最大。请更严格地审视 K 线结构，一旦发现趋势反转信号，必须果断建议 SELL (减仓/平仓) 以锁定利润或止损。
            """
        return fund_status_msg, min_notional_info, min_limit_info

    def _build_btc_instruction(self, btc_change_24h):
        """
        构建大盘联动指令提示词
        """
        btc_instruction = ""
        if btc_change_24h is not None:
             btc_icon = "📈" if btc_change_24h > 0 else "📉"
             btc_instruction = f"""
        【大盘环境 (BTC Context)】
        BTC 24H涨跌幅: {btc_change_24h:+.2f}% {btc_icon}
        """
             if btc_change_24h < -3.0:
                 btc_instruction += """
        ⚠️ **大盘暴跌警报**: BTC 大跌 (>3%)，山寨币通常会联动暴跌。
        - **慎做多**: 除非有独立行情，否则不要轻易接飞刀。
        - **防补跌**: 如果当前持有多单，请收紧止损或提前止盈。
        """
             elif btc_change_24h > 3.0:
                 btc_instruction += """
        🚀 **大盘暴涨**: BTC 大涨 (>3%)，市场情绪高昂。
        - **顺势做多**: 寻找补涨币种。
        - **慎做空**: 容易被踏空资金冲烂。
        """
        return btc_instruction

    def _build_market_instruction(self):
        """
        构建市场分析指令提示词
        """
        return """
        【狙击镜分析流程 (Sniper Scope)】
        请按以下步骤思考（体现在 reason 中）：
        1. **趋势预判**: 基于当前 K 线组合和量能，预测未来 4 小时的主流趋势（UP/DOWN/SIDEWAYS）及其概率。
        2. **形态识别 (三线战法 Three-Line Strike)**:
           - **看涨三线 (Bullish Strike)**: 连续三根阴线后，出现一根吞没大阳线。 -> **HIGH CONFIDENCE BUY** (若做空立即反手)
           - **看跌三线 (Bearish Strike)**: 连续三根阳线后，出现一根吞没大阴线。 -> **HIGH CONFIDENCE SELL** (若做多立即反手)
        3. **战场态势**: 当前是上涨趋势、下跌趋势还是垃圾震荡？(参考 ADX 和 EMA)
        4. **关键位置**: 价格是否处于关键支撑/阻力位？
        5. **寻找陷阱 (Trap)**: 是否出现"插针收回"、"假突破"等诱骗形态？这是最佳开火点！
        6. **量能验证**: 上涨放量？下跌缩量？(Volume Ratio)
        7. **最终扣动**: 
           - 如果是"假摔"后拉回 -> **BUY** (反手做多)。
           - 如果是"诱多"后砸盘 -> **SELL** (反手做空)。
           - 如果看不懂 -> **HOLD**。
        """

    def _build_surge_instruction(self, is_surge, candlestick_pattern=None):
        """
        构建异动唤醒指令提示词
        """
        if is_surge:
             msg = """
        🚀 **异动唤醒模式 (Surge Mode Triggered)**
        检测到成交量爆增或价格剧烈波动，系统强制唤醒了你！
        1. **快速反应**: 现在的行情极快，请忽略常规的 ADX 限制。
        2. **顺势猎杀**: 这通常是捕捉"大长腿"(Long Leg)的最佳时机。
        3. **快进快出 (Hit & Run)**: 异动通常不可持续。如果开仓，请务必设置较紧的动态止损，或者在下一轮分析时果断平仓。
        """
             if candlestick_pattern:
                 msg += f"""
        ✨ **K线形态确认 (Pattern Confirmed)**:
        Python 硬核算法检测到了 **{candlestick_pattern}** (三线战法)！
        这是极高置信度的反转信号。
        - BULLISH_STRIKE -> 强烈建议 BUY，止损设在形态最低点下方。
        - BEARISH_STRIKE -> 强烈建议 SELL，止损设在形态最高点上方。
        """
             return msg
        return ""

    def _build_user_prompt(self, symbol, timeframe, price_data, balance, position_text, amount, taker_fee_rate, leverage, risk_control, current_account_pnl, current_pos, funding_rate, dynamic_tp=True, volatility_status="NORMAL", btc_change_24h=None, is_surge=False, candlestick_pattern=None):
        """
        构建用户提示词
        """
        # 动态参数下沉到 User Prompt (Cache-Friendly)
        fee_pct = taker_fee_rate * 100
        break_even = fee_pct * 2
        
        # 构建各个部分的提示词
        hard_constraints = self._build_hard_constraints(taker_fee_rate, leverage)
        profit_first_instruction = self._build_profit_first_instruction(volatility_status, break_even)
        funding_instruction = self._build_funding_instruction(funding_rate)
        risk_msg = self._build_risk_message(current_account_pnl, risk_control)
        closing_instruction = self._build_closing_instruction(current_account_pnl, current_pos, risk_control, dynamic_tp)
        signal_def_msg = self._build_signal_definition(current_pos)
        kline_text = self._build_kline_text(price_data, timeframe)
        indicator_text = self._build_indicator_text(price_data)
        fund_status_msg, min_notional_info, min_limit_info = self._build_fund_status_message(balance, price_data)
        btc_instruction = self._build_btc_instruction(btc_change_24h)
        market_instruction = self._build_market_instruction()
        surge_instruction = self._build_surge_instruction(is_surge, candlestick_pattern)
        
        # 计算最大可买数量 (简单估算)
        max_buy_token = 0
        if price_data.get('price', 0) > 0:
            max_buy_token = (balance * leverage) / price_data['price']

        # 组合所有提示词
        return f"""
        # 市场数据
        交易对: {symbol}
        周期: {timeframe}
        当前价格: ${price_data['price']:,.4f}
        阶段涨跌: {price_data['price_change']:+.2f}%
        
        # 账户与风险
        当前持仓: {position_text}
        {signal_def_msg}
        可用余额: {balance:.2f} U
        当前杠杆: {leverage}x (高风险!)
        {risk_msg}
        {fund_status_msg}
        - 理论极限: {max_buy_token:.4f} 个 (标的资产数量，非合约张数)
        - 建议默认: {amount} 个 (仅供参考，请根据盘面调整)
        - **最小下单限制**: 数量 > {min_limit_info} 个 且 价值 > {min_notional_info} U (必须遵守!)
        
        # 技术指标
        {kline_text}
        {indicator_text}

        # 核心策略
        {surge_instruction}
        {profit_first_instruction}
        {funding_instruction}
        {btc_instruction}
        {closing_instruction}
        {market_instruction}
        """

    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, default_amount, taker_fee_rate=0.001, leverage=1, risk_control={}, current_account_pnl=0.0, funding_rate=0.0, dynamic_tp=True, btc_change_24h=None, is_surge=False, candlestick_pattern=None):
        """
        调用 DeepSeek 进行市场分析
        """
        try:
            volatility_status = "NORMAL" 
            if 'volatility_status' in price_data:
                volatility_status = price_data['volatility_status']

            role_prompt = self._get_role_prompt(volatility_status)
            
            position_text = "无持仓"
            if current_pos:
                pnl = current_pos.get('unrealized_pnl', 0)
                position_text = f"{current_pos['side']}仓, 数量:{current_pos['size']}, 浮盈:{pnl:.2f}U"

            prompt = self._build_user_prompt(
                symbol, timeframe, price_data, balance, position_text, default_amount, taker_fee_rate, leverage, risk_control, current_account_pnl, current_pos, funding_rate, dynamic_tp, volatility_status, btc_change_24h, is_surge, candlestick_pattern
            )

            # self.logger.info(f"[{symbol}] ⏳ 请求 DeepSeek (Async)...")
            
            req_start = time.time()
            
            response = await self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": role_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300, 
                timeout=30,
                response_format={"type": "json_object"}
            )
            
            req_time = time.time() - req_start
            # self.logger.info(f"[{symbol}] ✅ DeepSeek 响应完成 (耗时: {req_time:.2f}s)")

            result = response.choices[0].message.content
            # [Fix] 更健壮的 JSON 提取逻辑
            import re
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(0)
                signal_data = json.loads(json_str)
                
                signal_data['signal'] = str(signal_data.get('signal', '')).upper()
                signal_data['stop_loss'] = to_float(signal_data.get('stop_loss'))
                signal_data['take_profit'] = to_float(signal_data.get('take_profit'))
                
                ai_amount = to_float(signal_data.get('amount'))
                # [Fix] 允许 AI 建议 0 数量 (即仅平仓不反手)，不强制覆盖为 default_amount
                if ai_amount is not None:
                    signal_data['amount'] = ai_amount
                else:
                    signal_data['amount'] = default_amount
                
                return signal_data
            else:
                self.logger.error(f"[{symbol}] 无法解析JSON: {result}")
                return None

        except Exception as e:
            self.logger.error(f"[{symbol}] DeepSeek分析失败: {e}")
            return None
