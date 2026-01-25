# 🏗️ 技术架构与设计文档 (Technical Architecture)

## 1. 系统概览 (System Overview)

CryptoOracle (OKXBot Plus) 采用 **“双轨并行 (Dual-Track)”** 架构，旨在解决“AI 深度思考耗时”与“高频风控实时性”之间的矛盾。

### 1.1 核心设计理念
*   **左脑 (Python)**: 负责精确计算、资金审计、毫秒级风控、高频止盈。
*   **右脑 (AI/LLM)**: 负责模糊推理、形态识别、宏观趋势研判。

### 1.2 双轨并行机制
系统主循环 (`Orbit`) 强制以 **60秒** (或更短) 的频率运行，但在内部逻辑上分为两条独立轨道：

| 轨道 | 频率 | 职责 | 关键模块 |
| :--- | :--- | :--- | :--- |
| **轨道 A (战略层)** | 低频 (如 5m/15m) | 大周期趋势分析、开仓决策 | `AIStrategy`, `DeepSeek` |
| **轨道 B (战术层)** | 高频 (60s) | 持仓监控、1m 极速止盈、硬风控 | `RiskManager`, `FastExit` |

---

## 2. 核心模块 (Core Modules)

### 2.1 TradeExecutor (交易执行器)
*   **位置**: `src/services/execution/trade_executor.py`
*   **职责**: 整个系统的“大脑皮层”，协调数据、AI 和风控。
*   **特性**:
    *   维护 `current_pos` (当前持仓) 状态。
    *   执行 `Orbit B` 逻辑：每分钟拉取 1m K线，计算三线战法形态。
    *   管理动态止盈止损 (Dynamic SL/TP)。

### 2.2 RiskManager (风控管理器)
*   **位置**: `src/services/risk/risk_manager.py`
*   **职责**: 资金安全的最后一道防线。
*   **功能**:
    *   **账户级熔断**: 当总权益回撤超过阈值 (如 5%)，强制清仓并停止机器人。
    *   **资金隔离**: 锁定初始本金，确保利润回撤时不伤及本金。

### 2.3 DataManager (数据管理器)
*   **位置**: `src/services/data/data_manager.py`
*   **职责**: 高效的数据清洗与存储。
*   **特性**:
    *   **SQLite**: 本地持久化存储历史 K 线。
    *   **Buffer**: 写入缓冲机制 (10条或5秒)，保护磁盘 IO。

---

## 3. 执行逻辑流程 (Execution Flow)

### 3.1 信号生成 (AI Track)
1.  **触发**: 时间到达 `ai_interval` (如 300s) 且 K 线收盘。
2.  **数据准备**: 拉取最近 100 根 K 线，计算 RSI, MACD, ATR, ADX。
3.  **AI 推理**: 将技术指标打包发送给 DeepSeek LLM。
4.  **信号解析**: 解析 AI 返回的 JSON (BUY/SELL/HOLD)。
5.  **风控过滤**: 检查 `SignalGate` (如 RSI 是否超买超卖)。
6.  **执行**: 调用交易所 API 下单。

### 3.2 极速止盈 (Fast Exit Track)
1.  **触发**: 主循环每 60s 运行一次。
2.  **检查**: 是否持有仓位？
3.  **扫描**: 拉取最新 1m K 线。
4.  **形态识别**:
    *   **看跌三线**: 三连阳 + 爆量阴线吞没 -> **平多**。
    *   **看涨三线**: 三连阴 + 爆量阳线吞没 -> **平空**。
5.  **量能确认**: 第4根 K 线成交量必须大于前3根的最大值 (`Vol4 > Max(Vol1,2,3)`)。
6.  **执行**: 市价全平，发送通知。

---

## 4. 技术选型决策 (Design Decisions)

### 4.1 为什么选择 Python Async?
*   **背景**: 早期版本使用多线程，但在处理大量 IO (HTTP/WebSocket) 时资源消耗大。
*   **决策**: 全面转向 `asyncio`。
*   **优势**:
    *   **非阻塞**: 在等待 AI 回复 (10s+) 的同时，仍能处理 WebSocket 心跳和风控检查。
    *   **生态**: 完美兼容 Pandas, TA-Lib 等数据科学库。

### 4.2 为什么不使用 Go/Rust?
虽然 Go/Rust 性能更高，但在当前架构下 **LLM API 的网络延迟 (5-15s)** 是最大瓶颈，语言本身的执行速度 (ms级差异) 对总耗时影响微乎其微。Python 提供了最高的开发效率和生态支持。

---

## 5. 项目结构 (Project Structure)
```
src/
├── core/           # 基础组件 (配置, 日志, 缓存)
├── services/
│   ├── data/       # 数据层 (SQLite, DataManager)
│   ├── execution/  # 执行层 (TradeExecutor, OrderExecutor)
│   ├── risk/       # 风控层 (RiskManager)
│   └── strategy/   # 策略层 (AIStrategy, Prompt Engineering)
└── OKXBot_Plus.py  # 主程序入口
```
