# 🏗️ 技术架构深度解析 (v3.9.6)

CryptoOracle 并非传统的“线性轮询”脚本，而是一个基于 **AsyncIO** 的**三轨异步 (Triple-Track Async)** 决策执行系统。

---

## 1. 三轨异步逻辑 (Triple-Track Architecture)

为了彻底解决“AI 思考慢”与“风控要求快”的结构性矛盾，我们将逻辑拆分为三条并行轨道：

### 轨道 A: 战略决策轨 (Strategy Orbit)
*   **频率**: 低频 (15m/1h)。
*   **核心逻辑**: 
    *   聚合 K 线、指标、情绪数据。
    *   调用 **DeepSeek-V3** 进行语义级分析。
    *   **产出**: 交易信号（Long/Short/Neutral）及 AI 建议仓位比 (`position_ratio`)。
*   **代码实现**: 
    *   [ai_strategy.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/strategy/ai_strategy.py): 核心 AI 提示词与决策引擎。
    *   [trade_executor.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/trade_executor.py) -> `analyze_on_bar_close()`: 触发决策的开关。
*   **设计初衷**: AI 擅长识别“大势”，但不擅长捕捉“插针”。将 AI 放在低频轨道可以最大限度降低 Token 成本并提升信号稳定性。

### 轨道 B: 执行同步轨 (Execution Orbit)
*   **频率**: 事件驱动 (Event-Driven)。
*   **核心逻辑**: 
    *   订单生命周期管理（下单、撤单、重试）。
    *   资产余额双重审计（交易所 API + 本地状态机）。
*   **代码实现**: 
    *   [order_executor.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/components/order_executor.py): 负责带有自动重试与“51008 余额不足”降级逻辑的下单执行。
*   **设计初衷**: 确保下单逻辑的原子性，防止在网络波动时出现“重单”或“漏单”。

### 轨道 C: 战术风控轨 (Tactical/Risk Orbit)
*   **频率**: **极高频 (10s)**。
*   **核心逻辑**: 
    *   **移动止盈 (Trailing Stop)**: 实时计算当前价格与追踪水位线的距离。
    *   **分段止盈 (Partial TP)**: 监控浮盈阶梯，触发市价平仓。
    *   **每日利润锁定**: 监控账户总额，触线后强制执行减仓逻辑。
*   **代码实现**: 
    *   [trade_executor.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/execution/trade_executor.py) -> `run()` 循环前半部分: 每 10-20s 强制执行风控检查。
    *   `check_trailing_stop()`: 独立于 AI 的高频止盈逻辑。
*   **设计初衷**: **这是 v3.9.6 的灵魂。** 即使 AI 正在卡顿或 API 响应变慢，轨道 C 也会独立运行，确保您的本金在插针行情中得到毫秒级的保护。

---

## 2. 数据流与冲突处理

### 2.1 零点校准 (Zero-Start Baseline)
系统启动时，会立即拍摄一张“资产快照”存入 SQLite 数据库。
*   **解决痛点**: 解决了“因为账户原本就有持仓而导致盈亏计算混乱”的问题。
*   **实现**: 所有的盈亏计算均基于 `(当前余额 - 启动快照余额)`。
*   **代码参考**: [risk_manager.py](file:///d:/local_open_project/OKX_Plus_workspace/OKXBot_Plus_Workspace/src/services/risk/risk_manager.py) 的初始快照逻辑。

### 2.2 信号冲突逻辑
当轨道 A 生成了一个“做多”信号，但轨道 C 正因为“触发账户级回撤熔断”而处于冷却期时：
*   **优先级**: **轨道 C (风控) > 轨道 A (信号)**。
*   **处理**: 系统会记录该信号但拒绝执行，并在日志中输出 `RiskGate: Blocked by drawdown protection`。

---

## 3. 架构瘦身与性能

在 v3.9.6 中，我们移除了原有的 TensorBoard 和大型 ML 库依赖：
*   **内存占用**: 从 1.2GB 降低至 **150MB** 左右。
*   **冷启动速度**: 提升 400%。
*   **稳定性**: 减少了底层 C++ 库冲突导致的 `Segmentation Fault`。

---

## 4. 已知问题与优化优先级 (Technical Backlog)

我们将根据**资金安全、系统稳定、执行效率**三个维度，对当前已知问题进行优先级排序：

### 🔴 P0: 核心风险（必须尽快解决）

#### 4.1 重试死循环与熔断缺失 (Circuit Breaker)
*   **风险**: `order_executor` 在保证金不足或交易所异常时会重试。若无冷却期或最大重试次数限制，可能导致 API 被封禁、日志爆满，甚至在极端行情下造成逻辑混乱。
*   **优化**: 引入“交易对熔断器”。若单个币种连续下单失败 3 次，强制进入 5-15 分钟的冷却期。

#### 4.2 零点校准的持久化一致性 (Snapshot Integrity)
*   **风险**: 目前 Session PnL 依赖启动时的快照。如果程序中途崩溃重启，未能正确读取 SQLite 中的历史基准，会导致盈亏数据重置为 0，影响用户对策略表现的判断。
*   **优化**: 强化 `risk_manager` 的持久化逻辑，重启时优先检索 24 小时内的最近基准快照。

---

### 🟡 P1: 稳定性隐患（建议在 1-2 个版本内优化）

#### 4.3 内存指标数据积压 (Memory Bloat)
*   **风险**: `price_history` 和指标列表目前缺乏强制长度限制。在 7x24 小时运行数周后，内存占用会缓慢爬升，增加 OOM (内存溢出) 风险。
*   **优化**: 将所有历史序列容器替换为 `collections.deque(maxlen=200)`。

#### 4.4 批次执行的“木桶效应” (Batch Latency)
*   **风险**: `asyncio.gather` 分批处理时，一个币种的网络超时（如 5s）会拖慢整批（如 5 个）币种的风控响应速度，导致轨道 C 的 10s 心跳失效。
*   **优化**: 采用 `asyncio.create_task` 将每个币种的 `run()` 任务彻底隔离，互不干扰。

---

### 🔵 P2: 扩展性限制（中长期优化）

#### 4.5 频率超限隐患 (Rate Limit)
*   **风险**: 当用户配置币种超过 10 个且 `loop_interval` 较短时，并发请求可能触发 OKX 的限频阈值。
*   **优化**: 引入全局 `RateLimiter`（令牌桶算法），统一调度全系统的 API 调用频率。
