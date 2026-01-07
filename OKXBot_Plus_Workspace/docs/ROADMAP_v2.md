# OKXBot Plus v2.0 Architecture Roadmap

## 1. 核心架构升级 (Core Architecture)

### 现状 (Current State)
- **单体循环 (Monolithic Loop)**: `trade_executor.py` 使用 `while True` 循环，串行处理 `fetch_ohlcv` -> `analyze` -> `execute`。
- **轮询机制 (Polling)**: 依赖 HTTP API 定期拉取 K 线数据。
- **单策略绑定**: 每个 Symbol 只能运行一个 `DeepSeekTrader` 实例，策略逻辑耦合在 Executor 中。

### v2.0 目标 (Target State)
- **事件驱动 (Event-Driven)**: 引入 `EventBus` (事件总线)。
    - 市场数据流 (Market Data Stream) 产生 `MarketEvent`。
    - 策略引擎 (Strategy Engine) 监听事件并产生 `SignalEvent`。
    - 执行引擎 (Execution Engine) 监听信号并产生 `OrderEvent`。
- **WebSocket 优先**: 全面接入 OKX WebSocket API，实现毫秒级行情推送，废弃 HTTP 轮询（仅作为降级备份）。
- **多进程/集群**: 使用 `multiprocessing` 或 Kubernetes，将 Data, Strategy, Execution 拆分为独立服务。

---

## 2. 数据层进化 (Data Layer Evolution)

### 现状
- **SQLite + Pandas**: 适合单机、中小规模数据。
- **内存状态**: `price_history` 存在内存 list 中，重启依赖 SQLite 恢复。

### v2.0 目标
- **TimescaleDB / InfluxDB**: 引入专业的时序数据库，支持海量 Tick 级数据的高效写入和查询。
- **Redis Pub/Sub**: 用于实时分发 Tick 数据，让多个策略进程共享同一个行情源，减少对交易所的连接数。
- **Shared Memory (Plasma)**: 使用 Apache Arrow/Plasma 在进程间共享巨大的 K 线 DataFrame，避免数据序列化开销。

---

## 3. 策略层解耦 (Strategy Decoupling)

### 现状
- **Hardcoded Logic**: 策略逻辑（Trend/Grid）混合在 `trade_executor.py` 和 `deepseek_agent.py` 中。
- **单币单策**: 一个币种通常只跑一个主策略。

### v2.0 目标
- **Strategy Interface**: 定义标准的 `IStrategy` 接口 (`on_tick`, `on_bar`, `on_order_update`)。
- **Strategy Portfolio**: 允许在同一个 ETH/USDT 上同时运行 "RSI Mean Reversion" (做震荡) 和 "MACD Trend" (做趋势)，并通过一个 **Portfolio Manager** 模块汇总信号，计算净仓位 (Net Positioning)。
    - 例: 策略A 想买 1 ETH，策略B 想卖 0.5 ETH -> 实际执行: 买入 0.5 ETH。

---

## 4. 执行层增强 (Execution Layer)

### 现状
- **简单路由**: Maker/Taker 切换逻辑虽然有，但比较基础。
- **单账户**: 只能操作一个 OKX 账户。

### v2.0 目标
- **TWAP/VWAP 算法下单**: 对于大额订单，自动拆单，在时间轴上平滑执行，隐藏踪迹。
- **多账户/多交易所支持**: 
    - `ExchangeAdapter` 层，支持 Binance, Bybit 等。
    - 跨交易所套利 (Arbitrage) 能力。
- **订单生命周期管理 (OMS)**: 独立维护所有挂单的状态，处理部分成交 (Partial Fill)、撤单重发等复杂场景。

---

## 5. 运维与监控 (Ops & Monitoring)

### 现状
- **日志文件**: 依赖 `trade.log` 和控制台输出。
- **飞书通知**: 简单的文本报警。

### v2.0 目标
- **Prometheus + Grafana**: 
    - 实时监控系统延迟 (Latency)、API 权重消耗、策略 PnL 曲线、持仓敞口。
    - 可视化 Dashboard，不再看黑底白字的 Log。
- **Web UI Control Plane**: 
    - 提供一个 React/Vue 前端，可以手动干预策略（一键平仓、暂停开仓、修改参数），而无需 SSH 上服务器改配置。

---

## 6. 回测系统 (Backtesting)

### 现状
- **无内建回测**: 依赖实盘跑数据。

### v2.0 目标
- **Event-Driven Backtester**: 复用实盘的 `Strategy` 代码，喂入历史 Tick 数据进行回测。确保 "回测如实盘" (What You Test Is What You Trade)。
- **参数优化器 (Optimizer)**: 自动寻找最佳的 `timeframe`, `rsi_threshold` 等参数。

