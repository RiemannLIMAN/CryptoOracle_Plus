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
            'base_url': base_url
        }
        if proxy:
            client_params['http_client'] = httpx.AsyncClient(proxies=proxy)
            
        self.client = AsyncOpenAI(**client_params)

    def _is_stable_coin_pair(self, symbol):
        """
        判断是否为稳定币对 (如 USDC/USDT, DAI/USDT)
        """
        stable_coins = {'USDT', 'USDC', 'DAI', 'FDUSD', 'TUSD', 'USDE', 'BUSD'}
        try:
            base, quote = symbol.split('/')[:2]
            # 处理可能的后缀如 :USDT
            if ':' in quote: quote = quote.split(':')[0]
            
            return (base in stable_coins) and (quote in stable_coins)
        except:
            return False

    def _get_role_prompt(self, volatility_status, is_stable_pair=False):
        if is_stable_pair:
            return "你是一位专注于【稳定币套利】的量化交易员。当前交易对由两种稳定币组成，价格理论上应恒定在 1.0000。请忽略大部分趋势指标，专注于均值回归。你的目标是捕捉极其微小的脱锚波动（如 0.9995 买入，1.0005 卖出）。"
        
        # [Strategy Update] 趋势增强策略
        # 当 HIGH_TREND 时，鼓励金字塔加仓，但也必须警惕反转
        if volatility_status == "HIGH_TREND":
            return "你是一位激进的趋势猎手。当前市场处于【单边极强趋势】，ADX爆表。原则：顺势而为！\n1. 如果当前持仓方向与趋势一致（如持多且不断创新高），果断 HIGH 信心加仓。\n2. ⚠️ 关键：如果当前持仓与趋势相反（如持多但出现暴跌阴线），立即给出 SELL 信号并建议反手开空（Flip）。不要死扛！\n3. 理由中请明确指出趋势方向（Bullish/Bearish）。"
        elif volatility_status == "HIGH_CHOPPY":
            return "你是一位冷静的避险交易员。当前市场处于【剧烈震荡】。优先选择 HOLD。但如果出现【明确的假突破】或【关键位反转】，可以尝试 LOW 信心短线操作，不要过于保守。"
        elif volatility_status == "LOW":
            return "你是一位激进的超短线猎手。当前市场【缩量横盘】。请利用布林带轨道进行剥头皮交易！\n1. 价格触碰布林下轨且RSI<35 -> BUY\n2. 价格触碰布林上轨且RSI>65 -> SELL\n不要死等大行情，只要有 0.3% 利润空间即可出手。"
        else:
            return "你是一位敏锐的波段交易员。当前市场波动正常。请基于 K 线结构寻找机会。\n1. 顺势交易：如果趋势明确（如连续阴跌），请果断建议顺势开单，不要等待完美回调。\n2. 关键位：如果价格跌破支撑或突破压力，立即发出信号。\n3. 允许 LOW 信心试错：如果不确定性较高但盈亏比划算，可以给出 LOW 信心建议。"

    def _build_user_prompt(self, symbol, timeframe, price_data, balance, position_text, role_prompt, amount, taker_fee_rate, leverage, risk_control, current_account_pnl=0.0, current_pos=None, funding_rate=0.0):
        ind = price_data.get('indicators', {})
        min_limit_info = price_data.get('min_limit_info', '0.01')
        min_notional_info = price_data.get('min_notional_info', '5.0')
        
        is_stable = self._is_stable_coin_pair(symbol)
        
        # [New] 交易成本分析 (Cost Awareness)
        fee_cost_pct = taker_fee_rate * 100
        # 资金费率 (Funding Fee)
        funding_desc = "无"
        if funding_rate != 0:
            funding_desc = f"{funding_rate*100:.4f}%"
            if funding_rate > 0: funding_desc += " (多付空收)"
            else: funding_desc += " (空付多收)"
            
        cost_msg = f"""
        💰 **交易成本分析 (Cost Awareness)**:
        - 手续费 (Taker): {fee_cost_pct:.3f}% (单边)，一开一平需覆盖 {fee_cost_pct*2:.3f}% 的标的资产涨幅才能回本。
        - 资金费率: {funding_desc}。如果持仓方向与费率方向不利，每8小时会被扣费。
        - **决策原则**: 除非预期**标的资产价格波动**能覆盖 > 3倍的手续费成本 (即涨跌幅 > {fee_cost_pct*6:.3f}%)，否则不要频繁开仓。拒绝无效磨损！
        """
        
        # [Critical] 明确信号定义 (防止反手失败)
        signal_def_msg = ""
        if current_pos and current_pos['side'] == 'short':
             signal_def_msg = """
        ⚠️ **当前持有空单 (Short)，请注意信号定义**:
        - **BUY** = 平空 (Close Short) / 止盈 / 止损 / 反手开多。
        - **SELL** = 加仓空单 (Pyramiding)。如果已满仓，SELL 信号将被忽略。
        - **想反手做多？** 请务必发送 **BUY** 信号！不要发 SELL！
             """
        elif current_pos and current_pos['side'] == 'long':
             signal_def_msg = """
        ⚠️ **当前持有多单 (Long)，请注意信号定义**:
        - **SELL** = 平多 (Close Long) / 止盈 / 止损 / 反手开空。
        - **BUY** = 加仓多单 (Pyramiding)。如果已满仓，BUY 信号将被忽略。
        - **想反手开空？** 请务必发送 **SELL** 信号！不要发 BUY！
             """
        
        # 提取风控目标
        max_profit_usdt = risk_control.get('max_profit_usdt', 0)
        max_loss_usdt = risk_control.get('max_loss_usdt', 0)
        risk_msg = ""
        
        # [New] 添加资金进度信息
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
        
        # 动态生成止盈策略提示
        closing_instruction = ""
        if max_profit_usdt > 0:
            progress = current_account_pnl / max_profit_usdt
            if progress >= 1.0:
                 closing_instruction = "🔴 **最高优先级指令**：目标已达成！请立即建议 SELL (平仓) 或 HOLD (空仓)，严禁开新仓。"
            elif progress > 0.7:
                 closing_instruction = "🟠 **盈利保护指令**：目标接近完成 (>70%)。若市场走势不明朗或ADX下降，请优先选择 SELL 落袋为安，放弃鱼尾行情。"
        
        # [New] 亏损/反手提示
        if current_pos and current_pos.get('unrealized_pnl', 0) < 0:
             pnl_val = current_pos['unrealized_pnl']
             closing_instruction += f"\n🔴 **亏损警报**：当前持仓浮亏 {pnl_val:.2f} U。请严格评估趋势是否已反转！如果确认趋势反转（如多单遇暴跌），请立即建议 SELL 并注明 '反手' 或 'Flip'。"

        # [Modified] 动态获取 K 线数量，不再硬编码 30
        kline_count = len(price_data.get('kline_data', []))
        kline_text = f"【最近{kline_count}根{timeframe}K线数据】(时间倒序: 最新 -> 最旧)\n"
        # 稍微优化一下K线展示，只展示最近 15 根详细数据，避免 Token 过多，剩下的总结
        detailed_klines = price_data['kline_data'][-15:]
        for i, kline in enumerate(reversed(detailed_klines)): # 倒序展示更符合直觉
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            trend = "阳" if kline['close'] > kline['open'] else "阴"
            # [New] 显示成交量和量比
            vol_str = f"Vol:{int(kline['volume'])}"
            if 'vol_ratio' in kline and kline['vol_ratio'] is not None:
                vr = kline['vol_ratio']
                if vr > 2.0: vol_str += f"(🔥爆量 x{vr:.1f})"
                elif vr > 1.2: vol_str += f"(放量 x{vr:.1f})"
                elif vr < 0.6: vol_str += f"(缩量 x{vr:.1f})"
            
            kline_text += f"T-{i}: {trend} C:{kline['close']:.4f} ({change:+.2f}%) {vol_str}\n"
        
        if kline_count > 15:
            kline_text += f"...(更早的 {kline_count-15} 根K线已省略，但请基于整体结构分析)..."

        rsi_str = f"{ind.get('rsi', 'N/A'):.2f}" if ind.get('rsi') else "N/A"
        macd_str = f"MACD: {ind.get('macd', 'N/A'):.4f}, Sig: {ind.get('macd_signal', 'N/A'):.4f}" if ind.get('macd') else "N/A"
        adx_str = f"{ind.get('adx', 'N/A'):.2f}" if ind.get('adx') else "N/A"
        bb_str = f"Up: {ind.get('bb_upper', 'N/A'):.2f}, Low: {ind.get('bb_lower', 'N/A'):.2f}"
        
        # [New] 成交量概况
        vol_ratio_val = ind.get('vol_ratio', 1.0)
        vol_status = "正常"
        if vol_ratio_val > 2.0: vol_status = "🔥 极度放量"
        elif vol_ratio_val > 1.5: vol_status = "📈 显著放量"
        elif vol_ratio_val < 0.5: vol_status = "📉 极度缩量"
        
        # [New] 资金流向 (OBV & 买盘占比)
        obv_val = f"{ind.get('obv', 'N/A')}"
        buy_prop = ind.get('buy_prop', 0.5)
        buy_prop_str = f"{buy_prop*100:.1f}%"
        flow_status = "均衡"
        if buy_prop > 0.6: flow_status = "🟢 买盘主导"
        elif buy_prop < 0.4: flow_status = "🔴 卖盘主导"
        
        indicator_text = f"""【技术指标】
RSI(14): {rsi_str}
MACD: {macd_str}
Bollinger: {bb_str}
ADX(14): {adx_str} (趋势强度 >25为强)
Volume: 当前量比 {vol_ratio_val:.2f} ({vol_status})
Capital Flow: 买盘占比 {buy_prop_str} ({flow_status}) | OBV: {obv_val} (能量潮)"""

        # [New] 资金耗尽预警
        min_notional_val = to_float(min_notional_info) or 5.0
        fund_status_msg = ""
        # [Fix] 这里的 balance 是可用余额 (Avail)。如果 < 5U，说明真的没钱了
        if balance < min_notional_val:
            fund_status_msg = f"""
        � **状态更新：资金已满仓 (Full Position)**
        当前可用余额 ({balance:.2f} U) 已耗尽，说明资金利用率已达 100%。
        
        【你的决策逻辑需调整】：
        1. **关于加仓 (BUY)**：虽然你仍可以建议 BUY (表达你看涨的信心)，但请知悉系统将无法执行，会显示 "🔒 满仓持有"。
        2. **重点转向 (Focus)**：请把注意力从 "寻找买点" 转移到 "持仓管理" 和 "寻找卖点"。
        3. **风险评估**：既然已满仓，风险敞口最大。请更严格地审视 K 线结构，一旦发现趋势反转信号，必须果断建议 SELL (减仓/平仓) 以锁定利润或止损。
            """
        
        # 计算最大可买数量 (简单估算)
        max_buy_token = 0
        if price_data.get('price', 0) > 0:
            max_buy_token = (balance * leverage) / price_data['price']

        stable_coin_instruction = ""
        if is_stable:
            stable_coin_instruction = """
        ⚠️ **特殊规则 (稳定币对)**：
        1. 忽略 ADX 和 MACD 趋势信号。
        2. 核心逻辑：均值回归。价格总是倾向于回到 1.0000。
        3. 买入机会：价格 < 0.9992 (扣除手续费后有利可图)。
        4. 卖出机会：价格 > 1.0008。
        5. 止损：极其严格，如果脱锚超过 0.5% (如跌破 0.995) 立即止损。
            """
        else:
            stable_coin_instruction = f"""
        ⚠️ **特殊规则 (波动资产)**：
        1. **稳健第一**：在 1m 周期下，噪音极大。只有当 ADX > 25 且 K 线结构清晰（如突破回踩、双底）时才开单。
        2. **杠杆警示**：当前杠杆为 {leverage}x。波动 1% = 盈亏 {leverage}%。请根据此放大倍数收紧止损建议。
        3. **拒绝频繁交易**：如果当前形态模棱两可，或者处于布林带中轨，请果断 HOLD。宁可错过，不要做错。
            """

        return f"""
        # 角色设定
        {role_prompt}

        # 市场数据
        交易对: {symbol}
        周期: {timeframe}
        当前价格: ${price_data['price']:,.4f}
        阶段涨跌: {price_data['price_change']:+.2f}%
        {cost_msg}
        
        # 账户与风险
        当前持仓: {position_text}
        {signal_def_msg}
        可用余额: {balance:.2f} U
        当前杠杆: {leverage}x (高风险!)
        {risk_msg}
        {fund_status_msg}
        - 理论极限: {max_buy_token:.4f} 个 (标的资产数量，非合约张数)
        - 建议默认: {amount} 个 (仅供参考)
        - **最小下单限制**: 数量 > {min_limit_info} 个 且 价值 > {min_notional_info} U (必须遵守!)
        
        # 技术指标
        {kline_text}
        {indicator_text}

        # 核心策略
        {closing_instruction}
        {stable_coin_instruction}
        
        # 通用规则
        1. **卖出风控**：Taker费率 {taker_fee_rate*100:.3f}%。除非止损，否则浮盈必须覆盖双倍手续费 (>{(taker_fee_rate*2)*100:.2f}%)。
        2. **止损逻辑**：基于 {kline_count} 根 K 线的支撑/压力位设置止损，而不要只看百分比。
        3. **目标管理**：如果当前浮盈接近【目标总止盈】，请倾向于落袋为安。

        # 输出要求
        请严格返回如下JSON格式，不要包含Markdown标记：
        {{
            "signal": "BUY" | "SELL" | "HOLD",
            "reason": "核心逻辑(100字内，基于{timeframe}周期结构分析，需包含支撑/压力位具体价格、指标背离情况等细节)",
            "summary": "看板摘要(40字内)",
            "stop_loss": 止损价格(数字),
            "take_profit": 止盈价格(数字),
            "confidence": "HIGH" | "MEDIUM" | "LOW",
            "amount": 建议数量(数字，单位:个，建议值: {amount})
        }}
        """

    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, default_amount, taker_fee_rate=0.001, leverage=1, risk_control={}, current_account_pnl=0.0, funding_rate=0.0):
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
                symbol, timeframe, price_data, balance, position_text, role_prompt, default_amount, taker_fee_rate, leverage, risk_control, current_account_pnl, current_pos, funding_rate
            )

            # self.logger.info(f"[{symbol}] ⏳ 请求 DeepSeek (Async)...")
            
            req_start = time.time()
            
            response = await self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": role_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500, # 增加 Token 数以支持更复杂的分析
                timeout=45 # 适当延长超时
            )
            
            req_time = time.time() - req_start
            # self.logger.info(f"[{symbol}] ✅ DeepSeek 响应完成 (耗时: {req_time:.2f}s)")

            result = response.choices[0].message.content
            result = result.replace('```json', '').replace('```', '').strip()
            
            start_idx = result.find('{')
            end_idx = result.rfind('}') + 1
            if start_idx != -1 and end_idx != 0:
                json_str = result[start_idx:end_idx]
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
