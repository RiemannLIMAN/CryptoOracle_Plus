# ⚙️ 配置文件 (config.json) 详解

本文档详细解释 `config.json` 中各个参数的含义与建议配置。
> **提示**: 更完整的项目使用说明，请参考 [项目概览](PROJECT_OVERVIEW.md)。

---

## 完整配置示例 (v3.9.6 Alpha Sniper Optimized)

```json
{
  "exchanges": {
    "okx": {
      "options": {
        "defaultType": "swap"
      }
    }
  },
  "models": {
    "deepseek": {
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-chat"
    }
  },
  "trading": {
    "market_type": "swap",      
    "timeframe": "15m",
    "loop_interval": 10,
    "test_mode": false,
    "proxy": "",
    "max_slippage_percent": 2.0,
    "min_confidence": "MEDIUM",
    "strategy": {
      "enable_4h_filter": true,
      "ai_interval": 300,
      "signal_limit": 30,
      "trailing_stop": {
        "enabled": true,
        "activation_pnl": 0.02,
        "callback_rate": "auto"
      },
      "partial_tp_stages": [
        {"threshold": 0.05, "ratio": 0.3},
        {"threshold": 0.10, "ratio": 0.3}
      ],
      "signal_gate": {
        "rsi_min": 30,
        "rsi_max": 70,
        "adx_min": 20
      }
    },
    "risk_control": {
      "initial_balance_usdt": 100.0,
      "max_profit_usdt": 15.0,
      "max_loss_usdt": 15.0,
      "max_loss_rate": 0.15,
      "global_risk_factor": 1.0
    }
  },
  "symbols": [
    {
      "symbol": "BTC/USDT:USDT",
      "amount": "auto",
      "allocation": 0.2,
      "leverage": 5
    }
  ]
}
```

---

## 参数详解

### 1. 交易核心配置 (trading)
*   **`loop_interval`**: 机器人主循环检测间隔（秒）。在 v3.9.6 中，这代表了 **轨道 C (Orbit C)** 的频率。建议 `10` 秒，用于高频止盈止损监控。
*   **`strategy`** (策略配置):
    *   `ai_interval`: AI 深度分析的间隔（秒）。建议 `300` 秒 (5分钟)。
    *   **`trailing_stop`** (动态移动止盈):
        *   `callback_rate`: 回撤比例。设置为 `"auto"` 时，将根据 ATR 波动率自动调节。
    *   **`partial_tp_stages`**: **[v3.9.6 新增]** 分段止盈阶梯。例如在浮盈 5% 时减仓 30%。
*   **`risk_control`** (风控模块):
    *   **`max_profit_usdt`**: **[v3.9.6 关键]** 每日利润锁定目标。达到该金额后，系统自动激活利润保护模式。
    *   **`global_risk_factor`**: 全局风险因子。利润锁定触发后，该值会自动降至 `0.5`，使后续开仓量减半。

### 4. 通知配置 (notification)
*   `enabled`: 是否开启通知。
*   `telegram_token`: Telegram Bot Token。
*   `telegram_chat_id`: Telegram Chat ID。

### 5. 交易对配置 (symbols)
这是一个数组，支持同时监控多个币种。
*   **`symbol`**: 交易对名称。
    *   合约: `BTC/USDT:USDT` (必须带 `:USDT` 后缀)
*   `amount`: 单次开仓数量。`auto` 表示根据 allocation 自动计算。
*   `allocation`: 该币种占用总资金的比例 (0.0 ~ 1.0)。
*   `leverage`: 杠杆倍数。建议 `3` 到 `5`。
*   `trade_mode`: 交易模式，`cross` (全仓) 或 `isolated` (逐仓)。
