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
            'max_retries': 0
        }
        if proxy:
            client_params['http_client'] = httpx.AsyncClient(proxies=proxy)
            
        self.client = AsyncOpenAI(**client_params)

    def _is_stable_coin_pair(self, symbol):
        # [Deprecated] 现在的顶级交易员不需要这种硬编码的辅助
        return False

    def _get_role_prompt(self, volatility_status="NORMAL"):
        # 基础角色设定 (纯静态，利用缓存加速)
        base_role = "身份: 顶级加密货币狙击手 (Crypto Sniper)。\n"
        
        # [New] 动态人格注入 (Dynamic Persona Injection) - 复刻 V2 经典逻辑
        if volatility_status == "HIGH_TREND":
            base_role += "【当前模式: 趋势猎人 (Trend Hunter)】\n市场处于单边剧烈波动，ADX显示趋势极强。请紧咬趋势，果断追涨杀跌，不要轻易猜顶猜底。\n"
        elif volatility_status == "HIGH_CHOPPY":
            base_role += "【当前模式: 避险专家 (Risk Averse)】\n市场处于剧烈震荡，无明显方向。请极度谨慎，优先选择观望，或在布林带极端位置做超短线反转。\n"
        elif volatility_status == "LOW":
            base_role += "【当前模式: 网格交易员 (Grid Trader)】\n市场横盘震荡 (垃圾时间)。请寻找区间低买高卖的机会，切勿追涨杀跌。利用微小波动积累利润。\n"
        else:
            base_role += "【当前模式: 波段交易员 (Swing Trader)】\n市场波动正常。请平衡风险与收益，寻找确定性高的形态信号。\n"
            
        base_role += """
任务: 账户翻倍挑战 (Alpha Generation)。你管理着一笔高风险资金，必须在极短时间内捕捉趋势，实现资产的快速增值。
风格: 极度理性、杀伐果断、不知疲倦。
原则:
1. **进攻是最好的防守**: 在趋势确立时（胜率 > 70%），必须果断出击。犹豫就是对利润的犯罪（防止踏空）。
2. **本金即生命**: 每一分钱都是你的士兵。绝不打无准备之仗，绝不抗单。
3. **猎杀陷阱**: 狙击手最喜欢猎杀那些被"假突破"困住的散户。重点关注"诱多"和"诱空"形态。
4. **信心分级**:
   - HIGH: 完美形态 + 关键位突破/回踩 + 量能配合 (胜率 > 85%)。
   - MEDIUM: 趋势对头，但位置稍差 (胜率 > 70%)。
   - LOW: 震荡或不明朗 (胜率 < 60%)。

【狙击手战术手册 (Tactical Playbook)】
1. **突破战法 (Breakout)**: 仅当价格强势突破关键阻力位且**伴随爆量 (Volume > 1.5)** 时，视为有效突破。缩量突破多为假突破，坚决不追。
2. **回调战法 (Pullback)**: 上涨趋势中的缩量回调是最佳买点。寻找支撑位附近的"企稳信号"（如长下影线、锤子线）。
3. **拒绝震荡 (No Chop)**: 如果 ADX < 20 且布林带收口，说明市场在睡觉。此时严禁开单，耐心等待波动率回归。

【输出格式要求】
你必须严格只返回一个合法的 JSON 对象，不要包含任何 Markdown 标记或解释文字。格式如下：
{
    "signal": "BUY" | "SELL" | "HOLD",
    "reason": "核心逻辑(100字内，请用你最专业的术语直击要害)",
    "summary": "看板摘要(40字内)",
    "stop_loss": 止损价格(数字，0表示不设置),
    "take_profit": 止盈价格(数字，0表示不设置),
    "confidence": "HIGH" | "MEDIUM" | "LOW",
    "amount": 建议交易数量 (单位: 标的货币数量。如果要反手，请填写新开仓数量；如果仅想平仓/止损/止盈而不反手，请务必填写 0)
}
"""
        return base_role

    def _build_user_prompt(self, symbol, timeframe, price_data, balance, position_text, amount, taker_fee_rate, leverage, risk_control, current_account_pnl, current_pos, funding_rate, dynamic_tp=True, volatility_status="NORMAL"):
        
        # [New] 动态参数下沉到 User Prompt (Cache-Friendly)
        fee_pct = taker_fee_rate * 100
        break_even = fee_pct * 2
        
        hard_constraints = f"""
        【客观约束 (Hard Constraints)】
        1. **成本线**: Taker费率 {fee_pct:.3f}%。任何建议的开仓，其预期浮盈必须能覆盖 >{break_even:.3f}% 的成本，否则就是给交易所打工。
        2. **风控线**: 当前杠杆 {leverage}x。请自行计算爆仓风险，并给出合理的止损位。
        3. **最小单**: 若资金不足，系统会自动拒绝，你无需担心，只需专注于策略本身。
        """

        # [New] 盈利优先指令 (Profit First Instruction)
        # 用户反馈: "我是想实现盈利的，但是现在不盈利反而亏啊"
        # 针对: 避免频繁止损反手 (Double Slap) 和无效磨损
        
        # [Dynamic Strategy Adjustment]
        # 如果是网格模式 (LOW Volatility)，我们需要允许"吃小肉" (Scalping)，否则 AI 会一直观望
        if volatility_status == "LOW":
             profit_first_instruction = """
        【盈利优先原则 (Profit First) - 网格模式】
        1. **区间套利**: 当前市场处于震荡期，请利用微小波动积累利润。不要期待大趋势。
        2. **积少成多**: 允许赚取 0.5% - 1.0% 的小幅利润 (Scalping)。只要覆盖成本 ({break_even:.3f}%) 即可获利了结。
        3. **高抛低吸**: 在布林带下轨/支撑位买入，在上轨/压力位卖出。
        """
        else:
             profit_first_instruction = """
        【盈利优先原则 (Profit First) - 趋势模式】
        1. **严禁频繁反手 (No Flip Flop)**: 如果你在做"止损"(Stop Loss)，请优先建议 **amount=0** (仅平仓观望)。除非你有 90% 以上的把握确信这是"假突破+真反转"，否则严禁立即反手开新仓！
        2. **拒绝小肉 (No Scalping)**: 不要为了赚 0.5% 的波动去冒 1% 的风险。我们是狙击手，不是高频刷单机器。
        3. **趋势共振**: 在开新仓前，必须确认 大周期(趋势) 与 小周期(入场点) 共振。逆势接飞刀必须有极强的背离信号。
        """

        # 交易成本分析、杠杆警示等通用规则已移入 System Prompt
        # Funding Fee 仍然保留在这里，因为它是动态的
        funding_desc = "无"
        if funding_rate != 0:
            funding_desc = f"{funding_rate*100:.4f}%"
            if funding_rate > 0: funding_desc += " (多付空收)"
            else: funding_desc += " (空付多收)"
            
        cost_msg = f"""
        💰 **动态成本 (Funding)**:
        - 资金费率: {funding_desc}。如果持仓方向与费率方向不利，每8小时会被扣费。
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
        
        # 动态生成止盈策略提示 (仅当 dynamic_tp=True 时生效)
        closing_instruction = ""
        if dynamic_tp and max_profit_usdt > 0:
            progress = current_account_pnl / max_profit_usdt
            if progress >= 1.0:
                 closing_instruction = "🔴 **最高优先级指令**：目标已达成！请立即建议 SELL (平仓) 或 HOLD (空仓)，严禁开新仓。"
            elif progress > 0.7:
                 closing_instruction = "🟠 **盈利保护指令**：目标接近完成 (>70%)。若市场走势不明朗或ADX下降，请优先选择 SELL 落袋为安，放弃鱼尾行情。"
        
        # [New] 亏损/反手提示
        if current_pos and current_pos.get('unrealized_pnl', 0) < 0:
             pnl_val = current_pos['unrealized_pnl']
             closing_instruction += f"\n🔴 **亏损警报**：当前持仓浮亏 {pnl_val:.2f} U。请严格评估趋势是否已反转！如果确认趋势反转（如多单遇暴跌），请立即建议 SELL 并注明 '反手' 或 'Flip'。"

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
            
            kline_text += f"T-{i}: {trend} O:{kline['open']:.4f} H:{kline['high']:.4f} L:{kline['low']:.4f} C:{kline['close']:.4f} ({change:+.2f}%) {vol_str}\n"
        
        if kline_count > 15:
            kline_text += f"...(更早的 {kline_count-15} 根K线已省略，但请基于整体结构分析)..."

        ind = price_data.get('indicators', {})
        rsi_str = f"{ind.get('rsi', 'N/A'):.2f}" if ind.get('rsi') else "N/A"
        macd_str = f"MACD: {ind.get('macd', 'N/A'):.4f}, Sig: {ind.get('macd_signal', 'N/A'):.4f}" if ind.get('macd') else "N/A"
        adx_str = f"{ind.get('adx', 'N/A'):.2f}" if ind.get('adx') else "N/A"
        atr_str = f"{ind.get('atr', 'N/A'):.4f}" if ind.get('atr') else "N/A"
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
        ADX(14): {adx_str} (趋势强度 >25为强) | ATR(14): {atr_str} (波动率，建议止损参考: Entry ± 2*ATR)
        Volume: 当前量比 {vol_ratio_val:.2f} ({vol_status})
        Capital Flow: 买盘占比 {buy_prop_str} ({flow_status}) | OBV: {obv_val} (能量潮)"""

        # [New] 资金耗尽预警
        min_notional_info = price_data.get('min_notional_info', '5.0')
        min_limit_info = price_data.get('min_limit_info', '0.0001') # Default value as fallback
        
        min_notional_val = to_float(min_notional_info) or 5.0
        fund_status_msg = ""
        # [Fix] 这里的 balance 是可用余额 (Avail)。如果 < 5U，说明真的没钱了
        if balance < min_notional_val:
            fund_status_msg = f"""
        ⚠️ **状态更新：资金已满仓 (Full Position)**
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

        # [Removed] 删除了基于 if-else 的稳定币/高波动币硬编码指令
        # 既然是顶级交易员，他自己看盘口和波动率就知道该怎么做，不需要我们教
        market_instruction = """
        【狙击镜分析流程 (Sniper Scope)】
        请按以下步骤思考（体现在 reason 中）：
        1. **战场态势**: 当前是上涨趋势、下跌趋势还是垃圾震荡？(参考 ADX 和 EMA)
        2. **关键位置**: 价格是否处于关键支撑/阻力位？
        3. **寻找陷阱 (Trap)**: 是否出现"插针收回"、"假突破"等诱骗形态？这是最佳开火点！
        4. **量能验证**: 上涨放量？下跌缩量？(Volume Ratio)
        5. **最终扣动**: 
           - 如果是"假摔"后拉回 -> **BUY** (反手做多)。
           - 如果是"诱多"后砸盘 -> **SELL** (反手做空)。
           - 如果看不懂 -> **HOLD**。
        """

        return f"""
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
        - 建议默认: {amount} 个 (仅供参考，请根据盘面调整)
        - **最小下单限制**: 数量 > {min_limit_info} 个 且 价值 > {min_notional_info} U (必须遵守!)
        
        # 技术指标
        {kline_text}
        {indicator_text}

        # 核心策略
        {profit_first_instruction}
        {closing_instruction}
        {market_instruction}
        """

    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, default_amount, taker_fee_rate=0.001, leverage=1, risk_control={}, current_account_pnl=0.0, funding_rate=0.0, dynamic_tp=True):
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
                symbol, timeframe, price_data, balance, position_text, default_amount, taker_fee_rate, leverage, risk_control, current_account_pnl, current_pos, funding_rate, dynamic_tp, volatility_status
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
