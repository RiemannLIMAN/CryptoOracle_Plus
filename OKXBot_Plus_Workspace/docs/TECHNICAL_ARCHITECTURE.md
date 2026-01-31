# 🏗️ 技术架构深度解析 (v3.9.7)

CryptoOracle 并非传统的“线性轮询”脚本，而是一个基于 **AsyncIO** 的**三轨异步 (Triple-Track Async)** 决策执行系统。

---

## 1. 三轨异步逻辑 (Triple-Track Architecture)

为了彻底解决“AI 思考慢”与“风控要求快”的结构性矛盾，我们将逻辑拆分为三条并行轨道：

### 轨道 A: 战略决策轨 (Strategy Orbit)
*   **频率**: 低频 (15m/1h)。
*   **核心逻辑**: 
    *   聚合 K 线、技术指标（MACD, RSI, 布林带）、市场情绪。
    *   调用 **DeepSeek-V3** 进行语义级分析与形态识别。
    *   **产出**: 交易信号（Long/Short/Neutral）及 AI 建议仓位比 (`position_ratio`)。
*   **代码实现**: 
    *   [ai_strategy.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/strategy/ai_strategy.py): 核心 AI 提示词与决策引擎。
    *   [trade_executor.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/trade_executor.py) -> `analyze_on_bar_close()`: 触发决策的开关。

### 轨道 B: 执行同步轨 (Execution Orbit)
*   **频率**: 事件驱动 (Event-Driven)。
*   **核心逻辑**: 
    *   **RL 智能调仓**: 接收 AI 信号，结合本地 [rl_position_sizer.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/components/rl_position_sizer.py) 进行二次风险加权。
    *   **订单生命周期管理**: 自动下单、撤单、重试。
    *   **熔断保护**: 自动识别连续失败并触发 10 分钟冷却。
*   **代码实现**: 
    *   [order_executor.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/components/order_executor.py): 负责带有自动重试与“51008 余额不足”降级逻辑的下单执行。

### 轨道 C: 战术风控轨 (Tactical/Risk Orbit)
*   **频率**: **极高频 (10s)**。
*   **核心逻辑**: 
    *   **移动止盈 (Trailing Stop)**: 实时追踪价格水位，自动锁定利润。采用 **ATR + 盈利阶梯双重算法**：
        *   **波动率调节 (ATR)**: 高波动放宽，低波动收紧。
        *   **盈利压制 (Profit Compression)**: 盈利越高，回撤容忍度越低。v3.9.7 引入了 **6 级深度阶梯**，旨在通过动态收紧“利润安全带”来锁定极端行情下的暴利：
            
            | 等级 | 浮盈区间 (PnL) | 压缩系数 (Factor) | 效果描述 |
            | :--- | :--- | :--- | :--- |
            | **Level 1** | **> 2%** | **0.8** | **初步保护**: 略微收紧，防止利润归零。 |
            | **Level 2** | **> 5%** | **0.6** | **适度锁定**: 开始进入获利期。 |
            | **Level 3** | **> 10%** | **0.4** | **强力防御**: 利润已达 10%，大幅收紧回撤空间。 |
            | **Level 4** | **> 20%** | **0.2** | **激进追踪**: 捕捉主升浪，几乎不容忍回调。 |
            | **Level 5** | **> 50%** | **0.1** | **绝对防御**: 半翻倍行情，紧贴水位线运行。 |
            | **Level 6** | **> 100%** | **0.05** | **终极锁定**: 翻倍行情，任何反向波动即刻平仓。 |

            > **计算公式**: `实际触发回撤 = 基础回撤 (ATR调节后) * 盈利压制系数`
            >
            > **实战举例**: 
            > 假设你在 `config.json` 设定的 `callback_rate` 为 **1%**。
            > 1. 当利润为 **4%** 时，回撤阈值保持为 **1%**。
            > 2. 当利润冲到 **12%** (Level 3) 时，回撤阈值自动压缩至 **0.4%** (`1% * 0.4`)。
            > 3. 当利润冲到 **105%** (Level 6) 时，回撤阈值仅为 **0.05%** (`1% * 0.05`)。
            > 这确保了在利润巨大的情况下，即便发生极小级别的回调，系统也能瞬间落袋为安。
    *   **分段止盈 (Partial TP)**: **[v3.9.7 深度优化]** 采用阶梯式平仓逻辑：
        *   **Stage 1**: 浮盈达 **5%** 时，自动平仓 **30%**，锁定基础利润。
        *   **Stage 2**: 浮盈达 **10%** 时，再平仓 **30%**，并重置追踪水位线，让剩余仓位从更高点位开始追踪。
    *   **每日利润锁定**: 账户收益达标后自动强制减仓保护战果。
*   **代码实现**: 
    *   [trade_executor.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/trade_executor.py) -> `run()` 循环前半部分: 独立于 AI 逻辑的强制风控扫描。

---

## 2. 核心模块详解：RL 智能调仓 (Smart Position Sizing)

[rl_position_sizer.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/components/rl_position_sizer.py) 是系统的“油门与刹车”。它在 AI 给出 `position_ratio` 后，会根据以下 5 个实时维度进行二次过滤：

| 维度 | 计算逻辑 | 调仓行为 |
| :--- | :--- | :--- |
| **波动率 (ATR)** | 比较当前 ATR 与 20 周期平均值 | 波动激增 (>2x) 减仓 50%，防止插针爆仓 |
| **趋势强度 (ADX)** | 检测 ADX 指标值 | 强趋势 (>50) 增加 20% 仓位，弱震荡 (<20) 减仓 40% |
| **AI 信心度** | 匹配 DeepSeek 返回的信心等级 | HIGH 加码 20%，LOW 强制砍半 |
| **市场情绪** | 对接 Sentiment 插件 | 极度贪婪/恐慌时主动收缩 40-70% 风险敞口 |
| **盈亏反馈** | 当前持仓的浮盈百分比 | 浮亏时禁止任何形式的加仓动作 |

---

## 3. 全新热重载机制 (v3.9.7)

在 [OKXBot_Plus.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/OKXBot_Plus.py) 中，主循环实现了真正的“不停机动态同步”：

1.  **文件监听**: 每一轮循环都会通过 `os.path.getmtime` 监测 `config.json` 的修改时间。
2.  **增量同步**:
    *   **新增币种**: 自动初始化 `DeepSeekTrader` 实例并加入异步执行池。
    *   **移除币种**: 优雅停止对应的协程任务并释放资源。
    *   **参数更新**: 实时更新运行中 Trader 的杠杆、分配权重等参数。

---

## 4. 已知问题与优化优先级 (Technical Backlog)

我们将根据**资金安全、系统稳定、执行效率**三个维度，对当前已知问题进行优先级排序：

### 🔴 P0: 核心风险 (已解决)
*   **4.1 交易对熔断器**: ✅ 已修复。连续下单失败 3 次触发 10 分钟冷却。
*   **4.2 零点校准持久化**: ✅ 已修复。支持 24h 内快照自动恢复，重启不丢盈亏基准。
*   **4.3 反手逻辑优化**: ✅ 已修复。允许高信心/明确指令突破策略保护，极速反转。

### 🟡 P1: 稳定性隐患 (已解决)
*   **4.4 内存积压优化**: ✅ 已修复。历史序列全部替换为 `collections.deque(maxlen=200)`。
*   **4.5 异步任务隔离**: ✅ 已修复。使用 `Semaphore` 彻底消除单个币种超时导致的“木桶效应”。

### 🔵 P2: 扩展性限制 (已解决)
*   **4.6 全局限频器 (Rate Limiter)**: ✅ 已修复。基于令牌桶算法，全系统统一调度 API 调用频率 (10 req/s)。
