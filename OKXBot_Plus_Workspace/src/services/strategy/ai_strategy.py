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
        
        if volatility_status == "HIGH_TREND":
            return "你是一位稳健的趋势跟踪交易员。当前市场处于【单边剧烈波动】，ADX显示趋势极强。请顺势而为，但不要在回调的第一根K线就恐慌离场。只有在趋势结构被明显破坏（如高点不再抬高）时才考虑止盈。"
        elif volatility_status == "HIGH_CHOPPY":
            return "你是一位冷静的避险交易员。当前市场处于【剧烈震荡】，波动大且无方向。请极度谨慎，优先选择 HOLD 观望。严禁在震荡区间中间位置开单，只有在布林带极端突破且有明确反转信号时才考虑超短线操作。"
        elif volatility_status == "LOW":
            return "你是一位耐心的网格交易员。当前市场【窄幅横盘】。这是垃圾时间，严禁频繁开仓。请寻找箱体上下沿的高抛低吸机会，中间位置一律 HOLD。"
        else:
            return "你是一位稳健的波段交易员。当前市场波动正常。请忽略 1m 周期内的微小噪音，基于整体 K 线结构（50根）寻找盈亏比 > 1.5 的确定性形态。如果当前持仓浮亏不大且形态未坏，请多一点耐心 (HOLD)。"

    def _build_user_prompt(self, symbol, timeframe, price_data, balance, position_text, role_prompt, amount, taker_fee_rate, leverage, risk_control):
        ind = price_data.get('indicators', {})
        min_limit_info = price_data.get('min_limit_info', '0.01')
        min_notional_info = price_data.get('min_notional_info', '5.0')
        
        is_stable = self._is_stable_coin_pair(symbol)
        
        # 提取风控目标
        max_profit_usdt = risk_control.get('max_profit_usdt', 0)
        max_loss_usdt = risk_control.get('max_loss_usdt', 0)
        risk_msg = ""
        if max_profit_usdt > 0:
            risk_msg += f"- 目标总止盈: +{max_profit_usdt} U\n"
        if max_loss_usdt > 0: # 注意配置里通常是正数表示亏损额度，或者0禁用。这里假设配置是正数
            risk_msg += f"- 强制总止损: -{max_loss_usdt} U\n"
        
        # [Modified] 动态获取 K 线数量，不再硬编码 30
        kline_count = len(price_data.get('kline_data', []))
        kline_text = f"【最近{kline_count}根{timeframe}K线数据】(时间倒序: 最新 -> 最旧)\n"
        # 稍微优化一下K线展示，只展示最近 15 根详细数据，避免 Token 过多，剩下的总结
        detailed_klines = price_data['kline_data'][-15:]
        for i, kline in enumerate(reversed(detailed_klines)): # 倒序展示更符合直觉
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            trend = "阳" if kline['close'] > kline['open'] else "阴"
            kline_text += f"T-{i}: {trend} C:{kline['close']:.4f} ({change:+.2f}%)\n"
        
        if kline_count > 15:
            kline_text += f"...(更早的 {kline_count-15} 根K线已省略，但请基于整体结构分析)..."

        rsi_str = f"{ind.get('rsi', 'N/A'):.2f}" if ind.get('rsi') else "N/A"
        macd_str = f"MACD: {ind.get('macd', 'N/A'):.4f}, Sig: {ind.get('macd_signal', 'N/A'):.4f}" if ind.get('macd') else "N/A"
        adx_str = f"{ind.get('adx', 'N/A'):.2f}" if ind.get('adx') else "N/A"
        bb_str = f"Up: {ind.get('bb_upper', 'N/A'):.2f}, Low: {ind.get('bb_lower', 'N/A'):.2f}"
        
        indicator_text = f"""【技术指标】
RSI(14): {rsi_str}
MACD: {macd_str}
Bollinger: {bb_str}
ADX(14): {adx_str} (趋势强度 >25为强)"""

        # 计算最大可买数量 (简单估算)
        max_buy_token = 0
        if price_data['price'] > 0:
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
            stable_coin_instruction = """
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
        
        # 账户与风险
        当前持仓: {position_text}
        可用余额: {balance:.2f} U
        当前杠杆: {leverage}x (高风险!)
        {risk_msg}
        - 理论极限: {max_buy_token:.4f} 个 (标的资产数量，非合约张数)
        - 建议默认: {amount} 个 (仅供参考)
        - **最小下单限制**: 数量 > {min_limit_info} 个 且 价值 > {min_notional_info} U (必须遵守!)
        
        # 技术指标
        {kline_text}
        {indicator_text}

        # 核心策略
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

    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, default_amount, taker_fee_rate=0.001, leverage=1, risk_control={}):
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
                symbol, timeframe, price_data, balance, position_text, role_prompt, default_amount, taker_fee_rate
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
                signal_data['amount'] = ai_amount if (ai_amount and ai_amount > 0) else default_amount
                
                return signal_data
            else:
                self.logger.error(f"[{symbol}] 无法解析JSON: {result}")
                return None

        except Exception as e:
            self.logger.error(f"[{symbol}] DeepSeek分析失败: {e}")
            return None
