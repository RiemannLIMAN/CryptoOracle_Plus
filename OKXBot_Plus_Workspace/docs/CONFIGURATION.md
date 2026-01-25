# ⚙️ 配置文件 (config.json) 详解

本文档详细解释 `config.json` 中各个参数的含义与建议配置。
> **提示**: 更完整的项目使用说明，请参考 [项目概览](PROJECT_OVERVIEW.md)。

---

## 完整配置示例 (v3.4.4)

```json
{
  "exchange": {
    "api_key": "你的OKX_API_KEY",
    "secret": "你的OKX_SECRET",
    "password": "你的OKX_PASSPHRASE",
    "use_sandbox": false
  },
  "ai": {
    "provider": "deepseek",
    "api_key": "你的DeepSeek_API_KEY",
    "model": "deepseek-chat"
  },
  "trading": {
    "market_type": "swap",
    "timeframe": "15m",
    "loop_interval": 60,
    "leverage": 3,
    "risk_control": {
      "initial_balance_usdt": 0,
      "max_profit_usdt": 50.0,
      "max_loss_usdt": 10.0,
      "allocation": 0.9
    }
  },
  "symbols": [
    {
      "symbol": "BTC/USDT:USDT",
      "amount": "auto",
      "allocation": 0.5
    }
  ]
}
```

---

## 参数详解

### 1. 交易所配置 (exchange)
*   `api_key`, `secret`, `password`: OKX V5 API 的凭证。
*   `use_sandbox`: 是否使用模拟盘。
    *   `true`: 连接 OKX 模拟盘 (适合测试)。
    *   `false`: 连接实盘 (请注意风险)。

### 2. AI 配置 (ai)
*   `provider`: 目前固定为 `deepseek`。
*   `api_key`: DeepSeek 开放平台的 API Key。
*   `model`: 推荐使用 `deepseek-chat` (v3)。

### 3. 交易核心配置 (trading)
*   **`timeframe`**: K 线周期。
    *   **建议**: `15m` 或 `1h` (中线波段)。
    *   *说明*: 以前版本是 1m，现在为了减少手续费磨损，强烈建议使用中线周期。
    *   *注意*: AI 分析所需的 K 线数量 (history limit) 现已由代码自动根据周期动态调整，无需在配置中手动指定。
*   `loop_interval`: 机器人循环检测的间隔（秒）。建议 `60`。
*   **`strategy`** (策略配置):
    *   **`ai_interval`** [v3.8.0 新增]:
        *   AI 深度分析的间隔（秒）。
        *   **建议**: `300` (5分钟)。
        *   **原理**: 实现频率解耦。主循环 (`loop_interval`) 跑在 60s 甚至更快，负责实时监控止盈止损；而 AI 只需要每 5 分钟醒来一次看大方向，既省钱又高效。
    *   `dynamic_tp`: 是否启用动态止盈。
    *   **`trailing_stop`** (移动止盈) [v3.4.4 新增]:
        *   `enabled`: 是否开启。
        *   `activation_pnl`: 激活阈值 (如 `0.01` 代表 1% 浮盈)。只有当盈利超过此值时，移动止盈才生效。
        *   `callback_rate`: 回撤比例 (如 `0.003` 代表 0.3%)。从最高点回撤超过此比例时立即平仓。
*   **`leverage`**: 杠杆倍数。
    *   **建议**: `3` 到 `5`。不要超过 5 倍，因为中线止损较宽。
*   **`risk_control`** (风控模块):
    *   `initial_balance_usdt`: 初始本金。
        *   设为 `0`: 机器人启动时自动读取账户余额作为基准。
        *   设为具体数值 (如 `100`): 强制以 100U 为基准计算盈亏。如果实际余额偏差过大，机器人会自动校准。
    *   `max_loss_usdt`: 最大止损金额 (U)。例如设为 `10.0`，当总亏损达到 10U 时，机器人会自动清仓并停止运行。
    *   `max_profit_usdt`: 止盈金额。达到此盈利后自动停止。
    *   `allocation`: 总仓位限制。`0.9` 表示最多只使用 90% 的资金，保留 10% 作为安全垫。

### 4. 交易对配置 (symbols)
这是一个数组，支持同时监控多个币种。
*   **`symbol`**: 交易对名称。
    *   现货: `BTC/USDT`
    *   合约: `BTC/USDT:USDT` (必须带 `:USDT` 后缀)
*   `amount`: 单次开仓数量。
    *   `auto`: 机器人根据资金和 allocation 自动计算。
    *   数字: 强制固定数量 (如 `0.01`)。
*   `allocation`: 该币种占用总资金的比例 (0.0 ~ 1.0)。
    *   例如你有 100U，allocation=0.5，则该币种最多使用 50U 开仓。

---

## 常见配置方案

### 方案 A：稳健中线 (推荐)
*   `timeframe`: "1h"
*   `leverage`: 3
*   `symbols`: ["BTC/USDT:USDT", "ETH/USDT:USDT"]

### 方案 B：日内波段
*   `timeframe`: "15m"
*   `leverage`: 5
*   `symbols`: ["SOL/USDT:USDT", "DOGE/USDT:USDT"]
*   *注意*: 山寨币波动大，AI 会自动放宽止损，请控制好仓位。

### 方案 C：抢占式资金池 (小资金、多币种) [v3.4.6 新增]
**适用场景**: 本金较少 (<100U)，但想监控多个热门币种 (BTC, SOL, DOGE)，谁有机会就梭哈谁。

*   **配置技巧**:
    1.  监控 4-5 个币种。
    2.  每个币种的 `allocation` 都设为 **`0.95`** (允许占用 95% 资金)。
    3.  `amount` 设为 `"auto"`。
*   **效果**:
    *   这是一个“抢椅子”游戏。
    *   如果 BTC 先发出信号，它会瞬间占用 95% 的资金开仓。
    *   此时如果 SOL 再发出信号，会因为余额不足而被自动忽略。
    *   **优点**: 最大化资金利用率，避免小资金因分散而无法下单 (Code 51008)。

### 方案 D：差异化杠杆 (Advanced)
**适用场景**: 同时交易 BTC (低波) 和 PEPE (高波)。

*   **配置技巧**: 在 `symbols` 数组中为每个币单独指定 `leverage`。
    ```json
    "symbols": [
      { "symbol": "BTC/USDT:USDT", "leverage": 10 },  // BTC 波动小，上 10x
      { "symbol": "PEPE/USDT:USDT", "leverage": 2 }   // Meme 波动大，只敢 2x
    ]
    ```
*   **优先级**: 这里的 `leverage` 会覆盖全局 `trading.leverage` 设置。
