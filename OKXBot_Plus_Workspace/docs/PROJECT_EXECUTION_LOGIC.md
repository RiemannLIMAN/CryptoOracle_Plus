#  机器人核心执行逻辑 (v3.4.6)

本文档详细描述 OKXBot Plus (CryptoOracle) 的内部决策流程与状态流转机制。

---

## 1. 整体架构流 (Data Flow)

```mermaid
graph TD
    A[OKX Exchange] -->|K线数据/余额| B(Data Collector)
    B -->|计算指标 (MACD/RSI/Bollinger)| C(Data Processor)
    C -->|结构化Prompt| D{AI Brain (DeepSeek)}
    D -->|决策: BUY/SELL/HOLD| E(Trade Executor)
    E -->|毫秒级移动止盈 (Trailing Stop)| F[Order Execution]
    F -->|更新仓位| G(Risk Manager)
```

---

## 2. 核心循环 (Main Loop)

机器人采用 **单频轮询机制 (Unified Loop)**，默认每隔 `loop_interval` (30秒) 执行一次完整循环，但在执行层引入了毫秒级风控。

1.  **资产盘点与风控 (Asset & Risk)**:
    *   读取账户总权益 (USDT + 持仓市值)。
    *   **智能资金校准**: 自动修正配置资金与实际资金的偏差。
    *   **移动止盈 (Trailing Stop) [v3.4.4]**: 
        *   **机制**: 无需等待 AI 思考，在本地代码层毫秒级监控价格。
        *   **触发**: 当盈利 > 1% (Activation) 且回撤 > 0.3% (Callback) 时，立即市价止盈。
        *   *目的*: 解决 AI 反应慢的问题，精准锁定脉冲行情的利润。

2.  **波动率过滤 (Volatility Filter)**:
    *   **初筛**: 计算最近 K 线的平均振幅。
    *   **硬性拦截**: 如果 `平均振幅 < 交易成本 * 0.8`，系统判断为“死鱼盘”。
    *   **动作**: 直接跳过 AI 分析，强制 **HOLD**。
    *   *目的*: 节省 AI Token 费用，避免在无利可图的震荡中磨损本金。

3.  **行情分析 (Market Analysis)**:
    *   **动态获取历史 K 线**: 系统根据 Timeframe 自动调整投喂给 AI 的数据量。
    *   计算技术指标：RSI, MACD, Bollinger Bands, ADX, ATR, OBV (能量潮)。

4.  **AI 决策 (AI Decision)**:
    *   **Persona (v3.4.6)**: **"Professional Sniper (Alpha Generation)"**。
    *   **Prompt Cache**: 将 System Prompt 静态化，利用 DeepSeek 缓存加速，降低延迟。
    *   **Tactical Playbook**: 
        *   **Breakout**: 只有 Volume > 1.5 倍时才追突破。
        *   **No Chop**: ADX < 20 且布林带收口时强制休息。
        *   **Sniper Scope**: 结合 Price Action 和 Volume 进行综合判断。

5.  **信号执行 (Execution)**:
    *   **自动资金分配**: 动态计算仓位。
    *   **执行动作**: 支持反手 (Flip)，即多单直接转空单，一步到位。

---

## 3. 关键逻辑变更 (v3.4.6)

### 3.1 狙击手模式 (Tactical Sniper)
*   **旧版**: "Life-Saving Money" (救命钱)，因过度恐惧导致不开单。
*   **新版**: "Alpha Generation" (职业狙击手)，因极度理性而选择空仓。
    *   **核心差异**: 不是"不敢开"，而是"不值得开"。
    *   **战术手册**: 引入了明确的量价规则 (Volume > 1.5, Pullback)，杜绝模棱两可。

### 3.2 提示词缓存 (Prompt Cache)
*   **优化**: 将 System Prompt 固定，动态变量移至 User Prompt。
*   **效果**: 击中缓存 (Cache Hit)，显著降低 Time-To-First-Token (TTFT) 延迟。

### 3.3 移动止盈 (Trailing Stop)
*   解决了 "AI 反应慢" 的痛点。
*   现在，只要利润跑起来，止损线就像影子一样紧紧跟随。一旦行情反转，毫秒级离场。

### 3.4 智能资金基准 (Smart Baseline)
*   **启动时**: `Baseline = 实际账户权益`。
*   **运行中**: `PnL = 当前权益 - Baseline`。
*   确保盈亏统计永远基于“本次启动时的本金”，不受历史充提影响。

---

## 4. 交易与风控 (Trade Executor & Risk)
`src/services/execution/trade_executor.py`

### 4.1 核心状态流转
1.  **开仓检查 (Opening)**:
    *   资金检查: 现货/合约保证金充足。
    *   方向检查: 避免同向重复开仓 (除非满足 Pyramiding 条件)。
    *   **[新] 熔断检查**: 如果最近 60秒 内触发过止损，且 AI 信心不足 HIGH，则拒绝开仓。

2.  **平仓检查 (Closing)**:
    *   **AI 信号平仓**: 收到 `SELL` (多转空) 或 `BUY` (空转多) 信号。
    *   **硬止损/止盈**: 触发预设的 2% 止损或 5% 止盈。
    *   **移动止盈**: 利润回撤 0.3% 立即离场。
    *   **[新] 动态风控**: 触发 AI 实时计算的动态止损价 (Dynamic SL)。

3.  **风控卫士 (Watchdog)**:
    *   每 1-5 秒运行一次。
    *   批量检查所有持仓的盈亏状态。
    *   一旦触发红线，立即市价强平。

---

## 5. 策略实战行为解析 (Strategy Behavior Verification)

*基于 v3.4.6 实盘日志分析 (2026-01-02)*

### 5.1 克服“指标依赖症” (Overcoming Indicator Dependency)
*   **场景**: SOL/USDT RSI 跌至 **17-20** (极度超卖)。
*   **常规脚本**: 立即抄底做多 (Buy the Dip)。
*   **本策略行为**: **HOLD (观望)**。
*   **逻辑**:
    > **"超卖 (RSI < 20) + 缩量 (Low Volume) = 阴跌无底洞"**
    > AI 识别到下跌过程中没有成交量放大 (Panic Selling)，说明市场只是在慢慢失血，没有恐慌盘涌出，也没有主力承接。此时做多等于接飞刀。
    > **结论**: 拒绝单纯根据 RSI 数值开单，必须等待 Volume 确认。

### 5.2 垃圾时间过滤 (Garbage Time Filtering)
*   **场景**: DOGE/USDT RSI 在 **50** 附近窄幅波动。
*   **行为**: **HOLD**。
*   **逻辑**:
    > **"ADX 低 + 布林带收口 = 市场睡眠模式"**
    > 在这种行情下开单，胜率仅为 50% (抛硬币)，但还要扣除手续费。
    > **结论**: 策略选择“装死”，直到波动率回归。

### 5.3 狙击手纪律 (Sniper Discipline)
*   **核心特质**: **80% 的时间在等待，20% 的时间在进攻。**
*   **不开单原因**: 不是因为“恐惧”，而是因为“不屑”。策略在等待 **Volume > 1.5** 的爆量信号或明确的 **Reversal Pattern** (反转形态)。
