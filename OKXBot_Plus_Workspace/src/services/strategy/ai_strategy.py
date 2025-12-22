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

    def _get_role_prompt(self, volatility_status):
        if volatility_status == "HIGH_TREND":
            return "你是一位激进的趋势跟踪交易员。当前市场处于【单边剧烈波动】，ADX显示趋势极强。请紧咬趋势，果断追涨杀跌，不要轻易猜顶猜底。对于做空机会，即使信心只有MEDIUM也请果断出手。"
        elif volatility_status == "HIGH_CHOPPY":
            return "你是一位冷静的避险交易员。当前市场处于【剧烈震荡】，波动大但无明显方向。请极度谨慎，优先选择观望，或在布林带极端位置做超短线反转。"
        elif volatility_status == "LOW":
            return "你是一位耐心的网格交易员。当前市场横盘震荡，请寻找区间低买高卖的机会，切勿追涨杀跌。"
        else:
            return "你是一位稳健的波段交易员。当前市场波动正常，请平衡风险与收益，寻找确定性高的形态信号。"

    def _build_user_prompt(self, symbol, timeframe, price_data, balance, position_text, role_prompt, amount, taker_fee_rate):
        ind = price_data.get('indicators', {})
        min_limit_info = price_data.get('min_limit_info', '0.01')
        
        # [Modified] 动态获取 K 线数量，不再硬编码 30
        kline_count = len(price_data.get('kline_data', []))
        kline_text = f"【最近{kline_count}根{timeframe}K线数据】\n"
        for i, kline in enumerate(price_data['kline_data']):
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            trend = "阳线" if kline['close'] > kline['open'] else "阴线"
            kline_text += f"K线{i + 1}: {trend} 收盘:{kline['close']:.2f} 涨跌:{change:+.2f}%\n"

        rsi_str = f"{ind.get('rsi', 'N/A'):.2f}" if ind.get('rsi') else "N/A"
        macd_str = f"MACD: {ind.get('macd', 'N/A'):.4f}, Signal: {ind.get('macd_signal', 'N/A'):.4f}, Hist: {ind.get('macd_hist', 'N/A'):.4f}" if ind.get('macd') else "N/A"
        adx_str = f"{ind.get('adx', 'N/A'):.2f}" if ind.get('adx') else "N/A"
        bb_str = f"Upper: {ind.get('bb_upper', 'N/A'):.2f}, Lower: {ind.get('bb_lower', 'N/A'):.2f}"
        
        indicator_text = f"""【技术指标】
RSI (14): {rsi_str}
MACD: {macd_str}
Bollinger: {bb_str}
ADX (14): {adx_str} (趋势强度)"""

        # 计算最大可买数量 (简单估算)
        max_buy = 0
        if price_data['price'] > 0:
            max_buy = balance / price_data['price']

        return f"""
        # 角色设定
        {role_prompt}

        # 市场数据
        交易对: {symbol}
        周期: {timeframe}
        当前价格: ${price_data['price']:,.2f}
        阶段涨跌: {price_data['price_change']:+.2f}%
        最小交易单位: {min_limit_info}
        
        # 账户状态
        当前持仓: {position_text}
        可用余额: {balance:.2f} U
        理论最大可买: {max_buy:.4f}
        配置建议数量: {amount}
        
        # 技术指标
        {kline_text}
        {indicator_text}

        # 分析任务与规则
        请综合上述数据进行激进的短线决策：
        1. **趋势研判**：密切关注 ADX 和均线。如果持仓方向与主趋势背离，**必须承认错误**并建议平仓。
        2. **反手逻辑**：如果确信趋势已反转（如强劲的阴线吞没），请在建议 SELL 的同时，在 reason 中暗示反手开空意图（Confidence设为HIGH）。
        3. **止损优先**：如果浮亏 > 3% 且形态崩坏，不要死扛，立即建议 SELL。
        4. **卖出风控 (关键)**：
           - 当前 Taker 费率: {taker_fee_rate*100:.3f}%
           - **严禁微利平仓**：除非是为了止损，否则建议浮盈 > {(taker_fee_rate*2 + 0.0005)*100:.2f}% (覆盖双向手续费+滑点) 再考虑止盈。
           - **智能持有**：如果上涨动能未衰竭（MACD金叉扩大中），请选择 HOLD 让利润奔跑。
        5. **买入逻辑**：盈亏比 > 1.2 且形态良好（如底分型、突破回踩）时果断 BUY。
        6. **频繁交易鼓励**：在确保盈亏比合理的前提下，鼓励进行高频交易，捕捉短线波动利润。
        
        # 输出要求
        请严格返回如下JSON格式，不要包含Markdown标记：
        {{
            "signal": "BUY" | "SELL" | "HOLD",
            "reason": "核心逻辑(50字内，使用专业术语，言简意赅但逻辑完整)",
            "stop_loss": 止损价格(数字，必须设置),
            "take_profit": 止盈价格(数字，建议R/R > 1.1),
            "confidence": "HIGH" | "MEDIUM" | "LOW",
            "amount": 建议交易数量(数字，必须大于 {min_limit_info}，如果信号强烈，建议 {max_buy*0.5:.4f} 左右)
        }}
        """

    async def analyze(self, symbol, timeframe, price_data, current_pos, balance, default_amount, taker_fee_rate=0.001):
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
