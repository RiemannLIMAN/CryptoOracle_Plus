# ⚙️ 配置文件 (config.json) 详解

本文档详细解释 `config.json` 中各个参数的含义与建议配置。
> **提示**: 更完整的项目使用说明，请参考 [项目概览](PROJECT_OVERVIEW.md)。

---

## 完整配置示例 (v3.9.0)

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
    "enable_aggressive_mode": false,
    "strategy": {
      "enable_4h_filter": true,
      "ai_interval": 60,
      "signal_limit": 30,
      "trailing_stop": {
        "enabled": true,
        "activation_pnl": 0.02,
        "callback_rate": 0.005
      },
      "signal_gate": {
        "rsi_min": 30,
        "rsi_max": 70,
        "adx_min": 15
      },
      "sentiment_filter": {
        "enabled": true,
        "extreme_fear_threshold": 25,
        "extreme_greed_threshold": 75
      }
    },
    "margin_mode": "cross",
    "trade_mode": "cross",
    "risk_control": {
      "initial_balance_usdt": 30.0,
      "max_loss_usdt": 10.0,
      "max_loss_rate": 0.15,
      "max_drawdown_per_trade": 0.08
    }
  },
  "notification": {
    "enabled": true,
    "telegram_token": "",
    "telegram_chat_id": ""
  },
  "symbols": [
    {
      "symbol": "BTC/USDT:USDT",
      "amount": "auto",
      "allocation": 0.2,
      "leverage": 5,
      "trade_mode": "cross"
    }
  ]
}
```

---

## 参数详解

### 1. 交易所配置 (exchanges)
*   `okx`: 目前支持 OKX 交易所。
*   `options`: 交易所特定选项，`defaultType: "swap"` 表示默认交易永续合约。

### 2. AI 模型配置 (models)
*   `deepseek`:
    *   `base_url`: API 地址，通常为 `https://api.deepseek.com`。
    *   `model`: 模型名称，推荐使用 `deepseek-chat`。

### 3. 交易核心配置 (trading)
*   `market_type`: 市场类型，通常为 `swap` (永续合约)。
*   **`timeframe`**: K 线周期。建议 `15m` 或 `1h` (中线波段)。
*   `loop_interval`: 机器人主循环检测间隔（秒）。建议 `10` 秒，以确保实时监控移动止盈。
*   `test_mode`: 是否开启测试模式 (模拟盘)。
*   `max_slippage_percent`: 最大允许滑点百分比 (如 `2.0`%)。
*   `min_confidence`: 最小开仓信心阈值 (`MEDIUM` 或 `HIGH`)。
*   **`strategy`** (策略配置):
    *   `enable_4h_filter`: 是否开启 4H 趋势过滤 (顺势而为)。
    *   `ai_interval`: AI 深度分析的间隔（秒）。建议 `60` 秒。
    *   `signal_limit`: 信号有效性检查 (如 `30` 秒)。
    *   **`trailing_stop`** (移动止盈):
        *   `enabled`: 是否开启。
        *   `activation_pnl`: 激活阈值 (如 `0.02` 代表 2% 浮盈)。
        *   `callback_rate`: 回撤比例 (如 `0.005` 代表 0.5%)。
    *   `signal_gate`: 信号过滤门限 (RSI, ADX)。
    *   `sentiment_filter`: 情绪过滤 (恐惧/贪婪指数)。
*   **`risk_control`** (风控模块):
    *   `initial_balance_usdt`: 初始本金基准。用于计算总账户盈亏。
    *   `max_loss_usdt`: 最大允许亏损金额 (U)。触及此线机器人停止。
    *   `max_loss_rate`: 最大允许亏损比例 (如 `0.15` 代表 15%)。触及此线触发账户级熔断。
    *   `max_drawdown_per_trade`: 单笔交易最大回撤限制。

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
