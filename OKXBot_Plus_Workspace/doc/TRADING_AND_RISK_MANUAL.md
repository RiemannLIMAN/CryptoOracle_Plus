# 核心交易逻辑与资金管理手册 (Trading & Risk Management Manual)

**版本**: v3.1.15 (Notification UI Overhaul)  
**更新日期**: 2025-12-23  
**适用系统**: CryptoOracle AI Trading System (OKXBot_Plus)

---

## 1. 资金管理体系 (Capital Management System)

本系统采用**基准资金 (Smart Baseline)** 与 **动态权益 (Dynamic Equity)** 相结合的双轨制资金管理模式，确保在剧烈波动中准确计算盈亏比率。

### 1.1 账户权益计算 (Equity Calculation)
系统优先使用交易所统一账户的 **总权益 (Total Equity)** 作为资金视图，该指标已包含：
- **可用余额 (Free Balance)**: 未被占用的 USDT。
- **仓位保证金 (Margin)**: 已被合约仓位占用的保证金。
- **未实现盈亏 (Unrealized PnL)**: 当前持仓的浮动盈亏。
- **现货市值 (Spot Valuation)**: 持有现货资产的当前 USDT 价值。

**计算公式**:
```python
Current_Total_Value = Exchange_Total_Equity (优先) 
                      OR (USDT_Balance + Spot_Market_Value) (降级模式)
```

### 1.2 智能基准资金 (Smart Baseline)
基准资金是计算账户整体盈亏比例的分母，系统支持两种模式：

1. **自动校准模式 (Auto-Calibration)**:
   - 默认模式。启动时若 `|实际权益 - 配置本金| > 10%` (通常是缩水)，则自动将基准重置为当前实际权益。

2. **锁定本金模式 (Fixed Capital Mode)**:
   - 当 `实际权益 > 配置本金` (有闲置资金) 时，系统**强制锁定**基准为配置的 `initial_balance`。
   - **目的**: 实现"专款专用"，忽略账户中的额外闲置资金，仅计算授权资金的盈亏。

3. **自动充值识别 (Auto-Deposit Detection)**:
   - **运行时**: 检测资金瞬间增长 (`> 10U` & `> 5%`) -> 自动增加 Offset。
   - **启动时 (Startup Anomaly Check)**: 若首次计算 PnL 异常偏高 (`> 10U` & `> 10%`)，判定为历史遗留闲置资金 -> 自动增加 Offset。
   - **目的**: 确保 PnL 曲线连续，防止触发虚假止盈。

### 1.3 单笔仓位控制 (Position Sizing)
单笔交易的数量由以下漏斗模型决定：

1. **基础配额 (Allocation)**:
   - **比例模式**: `allocation <= 1.0` (如 0.2 代表投入 20% 初始本金)。
   - **固定模式**: `allocation > 1.0` (如 100 代表固定 100 USDT)。
   - **公式**: `Target_USDT = Initial_Balance * Allocation` (比例模式)。

2. **交易所限制修正**:
   - **最小数量 (Min Amount)**: 必须大于交易所定义的最小币数。
   - **最小金额 (Min Cost)**: 必须大于交易所定义的最小下单金额 (通常为 5 USDT)。
   - **修正逻辑**: `Final_Target = max(Target_USDT, Min_Cost * 1.5, 5.0)`。

3. **最终数量决策 (Final Decision)**:
   取以下三者最小值：
   - `AI_Suggested_Amount`: AI 模型根据盘面给出的建议数量。
   - `Config_Limit`: 配置文件计算出的配额限制 (allocation)。
   - `Account_Max_Buy`: 当前余额支持的最大购买力 (受配额硬性限制)。
   
   > **例外 (Smart Fund Sharing)**: 当 AI 信心为 **HIGH** 时，系统进入“激进模式 (Aggressive Mode)”。此时允许**突破单币种配额**，调用账户中最多 **90%** 的闲置资金（弹性配额），以最大化捕捉高确定性机会。

---

## 2. 核心交易逻辑 (Core Trading Logic)

系统基于 **AI 驱动的混合策略**，结合传统技术指标与大语言模型（DeepSeek）的市场理解能力。

### 2.1 市场状态感知 (Market Sensing)
系统首先根据 K 线数据计算波动率状态，动态调整 AI 的交易人格：
- **HIGH_TREND (ADX > 25 + 高波幅)**: 角色切换为**激进趋势跟踪者** (Trend Follower)，追涨杀跌。
- **HIGH_CHOPPY (ADX < 25 + 高波幅)**: 角色切换为**冷静避险者** (Risk Averse)，建议观望或超短线。
- **LOW (低波幅)**: 角色切换为**网格交易员** (Grid Trader)，高抛低吸。
- **NORMAL**: 角色切换为**稳健波段交易员** (Swing Trader)。

### 2.2 信号生成流程 (Signal Generation)
1. **数据输入**:
   - 最近 N 根 K 线数据 (OHLCV) —— 由 `history_limit` 配置决定 (默认 30~50)。
   - 技术指标: RSI, MACD, Bollinger Bands, ADX。
   - 账户状态: 持仓方向、浮动盈亏、可用余额。
2. **AI 分析**: DeepSeek 模型综合分析上述数据，输出 JSON 格式决策：
   - `Signal`: BUY / SELL / HOLD
   - `Reason`: 决策逻辑
   - `Confidence`: HIGH / MEDIUM / LOW
   - `Amount`: 建议数量

### 2.3 信号执行过滤器 (Execution Filters)
即便 AI 发出信号，仍需通过以下硬性过滤：
1. **信心阈值 (Confidence Filter)**:
   - 若 `Signal_Confidence < Config_Min_Confidence` (默认 MEDIUM)，强制转换为 HOLD。
2. **滑点保护 (Slippage Protection)**:
   - 若 `abs(Realtime_Price - Analysis_Price) > Max_Slippage` (默认 1%)，取消交易。
3. **微利拦截 (Micro-profit Filter)** (仅针对平仓):
   - 若试图平仓且 `0 < PnL% < (2 * Taker_Fee + 0.05%)`，且 AI 信心**非 HIGH**，则拦截平仓，避免因手续费导致实际亏损。
   - **注**: 亏损状态 (止损) 或 暴利状态 (止盈) 不受此限制。

### 2.4 执行状态透明化 (Execution Status) **[v3.1.11 New]**
为了让交易决策更加透明，控制台看板新增了 `EXECUTION` 状态列，明确展示每笔交易的最终命运：

| 状态码 | 图标 | 含义 | 解决方案 |
| :--- | :--- | :--- | :--- |
| **DONE** | ✅ | **交易成功** | 正常运行 |
| **QUOTA** | 🚫 | **配额不足** | 资金已达 `allocation` 上限，需增加配额或本金 |
| **MIN** | 🚫 | **金额过小** | 计算金额小于交易所最小限制 (如 <2U)，需增加配额 |
| **CONF** | ⏸️ | **信心不足** | AI 看涨但信心未达 `min_confidence`，建议观望 |
| **PROFIT**| 🚫 | **微利拦截** | 平仓收益不足以覆盖手续费，系统自动持有等待更大利润 |
| **SLIP** | 🚫 | **滑点过大** | 盘口剧烈波动，系统自动取消以保护本金 |

   - **安全拦截**: 若计算出的下单价值异常巨大 (超过配置金额的 5 倍)，系统会判定为单位换算错误 (如将 1000 个币算成了 1000 张合约) 并强制拦截。

### 2.5 下单预检机制 (Order Pre-check) **[v3.1.12 New]**
为了防止因交易所规则差异（如 PEPE 的市价单数量限制）导致的下单失败，v3.1.12 引入了预检机制：
1. **自动截断**: 下单前自动检查交易所的 `limits.market.max`，若数量超限则自动调整为允许的最大值。
2. **详细日志**: 控制台会输出 `🔍 下单预检` 日志，显示：
   - `数量(Coins)`: 实际下单币数
   - `估算价值(USDT)`: 预计消耗金额
   - `换算张数`: 合约张数 (仅合约模式)
   这有助于用户排查“为什么只买了这么点”或“为什么没成交”的疑问。

### 2.6 交易单位显示优化 (Unit Display) **[v3.1.16 New]**
为了消除对“交易数量”含义的歧义，系统在日志和通知中明确标注了单位：
- **合约交易 (Swap/Futures)**: 数量后显示 `张 (Cont)`，例如 `20.0 张 (Cont)`。
- **现货交易 (Cash)**: 数量后显示币种名称，例如 `0.5 ETH`。
此优化让用户一眼区分是买入了 20 个币还是 20 张合约。

---

## 3. 风险控制体系 (Risk Management System)

风控体系分为**全局账户级**和**单笔交易级**两层防护。

### 3.1 全局风控 (Global Risk Control)
由 `RiskManager` 服务每 15 秒扫描一次：
- **最大止盈 (Max Profit)**:
  - 触发条件: `Current_PnL >= max_profit_usdt` 或 `PnL_Rate >= max_profit_rate`。
  - 动作: **清空所有持仓**，发送喜报，停止程序。
- **最大止损 (Max Loss)**:
  - 触发条件: `Current_PnL <= -max_loss_usdt` 或 `PnL_Rate <= -max_loss_rate`。
  - 动作: **清空所有持仓**，发送警报，停止程序（熔断机制）。

### 3.2 交易级风控 (Trade Level Risk)
- **AI 动态止损**: 每次开仓时，AI 需指定 `stop_loss` 价格。
- **反向平仓**:
  - 开多前，自动检查是否持有空单，若有则优先平空 (Close Short)。
  - 开空前，自动检查是否持有多单，若有则优先平多 (Close Long)。
- **最小交易单位检查**:
  - 下单前自动校验是否满足 `Exchange_Min_Amount` 和 `Exchange_Min_Cost`，不足则尝试自动提升数量，余额不足则报警并放弃。

---

## 4. 异常处理与容错 (Error Handling)

### 4.1 网络与 API 容错
- **数据获取**: K 线获取包含超时控制 (`timeout=10s`)，失败返回 `None` 跳过本次循环。
- **下单失败**: 捕获所有异常，特判 `51008` (余额/保证金不足) 错误并记录。
- **诊断报告**: 当因余额不足导致下单失败时，自动发送包含账户能力、AI 建议、配置限制的详细诊断报告。

### 4.2 数据完整性
- **费率自适应**: 启动时自动同步交易所当前 Taker/Maker 费率，确保盈亏估算准确。
- **精度自动修正**: 下单数量自动按照交易所精度要求 (`amount_to_precision`) 进行截断，防止 API 报错。

---

## 5. 附录：关键配置参数 (Configuration)

| 参数路径 | 参数名 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `trading.risk_control` | `initial_balance_usdt` | 73.54 | 初始本金设定 (用于计算收益率) |
| `trading.risk_control` | `max_loss_rate` | 0.15 | 最大亏损率 (15% 熔断) |
| `trading` | `max_slippage_percent` | 1.0 | 最大允许滑点 (1%) |
| `trading` | `min_confidence` | MEDIUM | 最低开仓信心要求 |
| `symbols[].amount` | `amount` | "auto" | "auto" 为自动计算，数字为固定数量 |
| `symbols[].allocation` | `allocation` | 1 | 资金分配比例 (<=1) 或固定金额 (>1) |

---

## 6. 常见问题排查 (Troubleshooting) **[v3.2.0 New]**

### 6.1 资金死锁 (Capital Deadlock)
**现象**: 第一笔交易成功，但后续交易（加仓/反手）频繁报错 "余额不足 (Insufficient Balance)"。

**原因**:
仓位配置过于激进（通常是 `allocation: 1.0` 即 100% 全仓）。在全仓模式 (`cross`) 下，第一笔交易会占用绝大部分可用余额作为保证金。当 AI 试图进行调整（如补仓、反手）时，账户已无剩余资金可用，导致操作失败，陷入“满仓无法动弹”的死局。

**解决方案**:
1.  **推荐配置**: 将 `config.json` 中的 `allocation` 设置为 **0.3 ~ 0.5** (30% ~ 50%)。
2.  **自动安全保护 (v3.2.0+)**: 如果您坚持使用 `allocation: 1.0` (100%)，且 `amount` 设为 `"auto"`，系统现在会自动强制仅使用 **60%** 的配额资金开仓，强制保留 40% 的安全垫。
    - **示例**: 配置 `allocation: 1` -> 实际单次使用 60% 资金。
    - **示例**: 配置 `allocation: 0.5` -> 实际单次使用 30% 资金 (0.5 * 0.6)。
这能确保账户始终保留一半以上的资金作为：
1.  **安全垫**: 提高强平价格，抵抗波动。
2.  **机动部队**: 用于执行补仓 (DCA) 或反手 (Stop & Reverse) 操作。

### 6.2 虚假熔断 (False Stop-Loss Trigger)
**现象**: 配置文件中已将 `initial_balance_usdt` 设为 0，但启动后依然按照旧的本金计算盈亏，导致瞬间触发熔断。

**原因**:
系统会缓存上一次运行的风控状态 (`data/risk_state.json`)。即使修改了配置文件，系统为了数据连续性，仍优先加载历史状态。

**解决方案**:
1.  停止机器人。
2.  删除 `data/risk_state.json` 文件。
3.  重启机器人。系统将重新以当前实际余额作为基准。

### 6.3 频繁止损 (Whipsaw)
**现象**: 在震荡行情中，AI 频繁买入后立即止损，造成资金磨损。

**原因**:
- `history_limit` 设置过短 (如 <20)，导致 AI 视野受限，对噪音敏感。
- `signal_limit` 设置过短 (如 <10s)，导致过度交易。

**解决方案**:
- 增大 `history_limit` 至 50 或 100，让 AI 关注更大级别的趋势。
- 增大 `signal_limit` 至 60s 或更高，强制冷却。
- 调高 `min_confidence` 至 HIGH，仅做高胜率机会。
