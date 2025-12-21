# 变更日志 (Changelog)

本项目的所有主要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)，
并且本项目遵循 [语义化版本控制 (Semantic Versioning)](https://semver.org/spec/v2.0.0.html)。

## [v3.1.5] - 2025-12-21 (Multi-Instance)
### 🛠️ 基础设施 (Infrastructure)
- **多实例支持 (Multi-Instance Support)**:
  - 升级 `start_bot.sh` 启动脚本，采用基于当前目录的 PID 锁 (`log/bot.pid`) 代替全局进程扫描。
  - 现在允许在同一台服务器的不同目录下运行多个机器人实例，互不冲突。
- **日志格式优化 (Log Formatting)**:
  - 修复了日志表格输出时的空行问题和对齐问题，使得 `tail -f` 查看体验更佳。

## [v3.1.4] - 2025-12-21 (Log Rotation)
### 🛠️ 维护 (Maintenance)
- **日志管理 (Log Management)**:
  - 将日志文件名还原为固定的 `trading_bot.log`，以支持 `RotatingFileHandler` 的自动轮转功能。
  - 更新了启动 Banner 中的日志查看提示信息。

## [v3.1.3] - 2025-12-21 (Adaptive Risk)
### 🧠 逻辑精修 (Logic Refinement)
- **智能信心豁免 (Smart Confidence Waiver)**:
  - **止损优先**: 如果在持有仓位（止损/止盈）时触发 SELL 信号，系统现在会**强制覆盖** `min_confidence` 过滤器。即使 AI 信心为 `LOW`，也会果断执行，防止深套。
  - **趋势跟随**: 如果 SELL 信号的理由中包含 "下跌"、"趋势"、"空头" 等关键词，系统允许以 `LOW` 信心开空，避免错过明确的趋势反转。
- **做空资金修正**:
  - 修复了“反手开空”（平多 -> 开空）时可能因平仓资金未及时计入可用余额而导致开空失败的问题。（注：逻辑已增强，完整余额预估待实装）。

### ✨ 体验优化 (UX Improvements)
- **仪表盘表格视图 (Dashboard Table View)**:
  - 将原本杂乱的滚动日志重构为清爽的 **结构化表格仪表盘**，完美适配多币种（50+）监控场景。
  - 支持实时价格刷新、24小时涨跌幅图标 (🟢/🔴) 以及精简的 AI 决策理由摘要，一眼掌握全局动态。
- **视觉优化**:
  - 更新了系统 Banner 以显示 v3.1.3 版本号。

## [v3.1.2] - 2025-12-21 (Async Core)
### 🧠 逻辑精修 (Logic Refinement)
- **动态 AI 上下文 (Dynamic AI Context)**:
  - 投喂给 AI 的 K 线数量现在由 `config.json` 中的 `history_limit` 动态控制（此前硬编码为 30）。
  - 增加了安全底线 (`max(10, history_limit)`)，确保 AI 始终有足够的上下文。
- **非对称交易逻辑 (Asymmetric Trading Logic)**: 
  - 修复了 SELL 信号只平多单但不反手开空的逻辑缺失。
  - 现在的逻辑完全对称：SELL 信号 = 平多 + 开空（如果配置允许）。
- **微利拦截优化 (Micro-Profit Filter)**: 
  - 改进了微利拦截逻辑。当 AI 信心为 `HIGH`（紧急）时，将**强制绕过**微利检查，允许微利甚至亏损逃顶。

### ✨ 体验优化 (UX Improvements)
- **通知补全 (Notification Completeness)**: 
  - 补全了“平多”和“平空”操作的通知。现在每一个交易动作（开/平）都会触发飞书报警。
- **通知样式升级 (Notification Style)**:
  - 将飞书/Lark 通知升级为 **富文本卡片 (Post)** 格式。
  - 现在的通知带有清晰的标题（🤖 CryptoOracle 交易播报）和更好的排版，视觉体验大幅提升。
- **视觉优化 (Visuals)**:
  - 更新了启动 Banner 为 "ANSI Shadow" 风格。

## [v3.1.1] - 2025-12-21 (Stability & Fixes)
### 🐛 关键修复 (Critical Fixes)
- **网络稳定性 (Network Stability)**: 
  - 为 DeepSeek API 和交易所 K 线获取请求添加了严格的 `timeout=10` 限制，防止网络不稳定时程序永久卡死。
- **风控计算修复 (Risk Calculation Fix)**: 
  - 修复了 `RiskManager` 中的一个严重 Bug：在合约模式下，持仓价值被重复计算了两次（一次在总权益中，一次在持仓市值中），导致基准资金虚高并误触止损。
- **通知修复 (Notification Fix)**: 
  - 修复了 Webhook 通知无法发送的问题（原因是配置读取作用域错误，`root` vs `trading`）。现已修正注入逻辑。

### ✨ 体验优化 (UX Improvements)
- **实时日志监控 (Real-time Log Monitoring)**: 
  - 升级了 `start_bot.sh`，启动后自动进入 `tail -f` 模式，让您能立即看到机器人的运行状态。
- **日志持久化 (Log Persistence)**: 
  - 将资产初始化盘点的输出从 `print` 改为 `logger.info`，确保这些重要信息被记录在日志文件中。

## [v3.1.0] - 2025-12-21 (Logic Refinement & Safety)
### 🧠 逻辑精修 (Logic Refinement)
- **K线上下文黄金分割**: 
  - 最终敲定发送给 AI 的 K 线数量为 **15 根**。
  - **原理**: 15 根 K 线刚好覆盖 RSI(14) 的计算周期，让 AI 能够直观看到导致指标变化的完整形态（如 W底、M头），同时避免长上下文导致的注意力分散。
  - **本地计算**: 依然保持 100 根的历史数据请求，确保本地 MACD/EMA 等长周期指标计算精准。
- **四重人格明确化**: 
  - 在文档和代码中明确了 AI 人格切换的 ADX 阈值：
    - 🦁 **激进 (Trend)**: ADX > 25
    - 🐼 **网格 (Grid)**: ADX < 20
    - 🦉 **避险 (Choppy)**: Volatility > 0.5
- **Webhook 安全注入**: 
  - `config.json` 中彻底移除了 `webhook_url` 字段，消除了误提交风险。
  - 代码层 (`config.py`) 完善了从环境变量 `NOTIFICATION_WEBHOOK` 自动注入的逻辑。

### 🛡️ 脚本与安全 (Script & Safety)
- **Windows 防双开**: 
  - `start_bot.bat` 新增 `wmic` 进程扫描逻辑。
  - 启动前自动检测是否已有 `OKXBot_Plus.py` 实例运行，如有则弹出警告，防止双重下单导致的资金风险。
- **Linux 环境自适应**: 
  - `start_bot.sh` 增强了对 Conda、自定义 Venv 名称的智能识别能力。

### 📚 文档重构 (Documentation)
- **深度重写**: `TRADING_AND_RISK_MANUAL.md` 全面翻新。
  - 引入“左脑（硬指标）+ 右脑（AI感知）”的协同架构描述。
  - 公开了所有执行风控（滑点、微利拦截）的具体计算公式。
- **逻辑补全**: `PROJECT_EXECUTION_LOGIC.md` 补充了状态持久化（Smart Baseline）与优雅退出的详细流程。

## [v3.0.1] - 2025-12-20 (SOA & High-Frequency)
### 🚀 核心重构与优化 (Core Refactoring)
- **SOA 架构落地**: 完成项目结构的最终形态改造，采用 **Service-Oriented Architecture (SOA)**。
  - `src/services/strategy`: 策略大脑 (AI Decision)。
  - `src/services/execution`: 交易手 (Execution & Indicators)。
  - `src/services/risk`: 守门员 (Global Risk)。
- **极速高频轮询**: 
  - 支持 **秒级/毫秒级** (`1s`, `500ms`) 的行情轮询。
  - 优化了休眠逻辑，支持 `sleep(0.01)` 级别的微秒响应。
  - 新增 API 智能降级机制：在毫秒级配置下，自动请求 `1s` K线数据以保证兼容性。
- **配置系统升级**:
  - **强制安全**: 移除了 `config.json` 中的 API Key 字段，强制要求通过环境变量 (`.env`) 注入。
  - **路径修正**: 修复了 `config.py` 在深层目录下的路径解析问题，支持任意层级调用。

### ✨ 功能回归与增强 (Feature Restoration)
- **🦁 激进模式回归**: 恢复了 v2.3 的 "High Confidence Override" 特性。
  - 当 AI 信心为 `HIGH` 时，自动忽略 `amount` 配置限制，允许最大化资金利用率。
- **🩺 诊断报告系统**: 
  - 下单失败时（余额不足、最小额限制），不再沉默。
  - 自动生成包含账户权益、缺口金额、AI 建议值的详细诊断报告并推送。
- **🎨 体验优化**:
  - **ASCII Banner**: 恢复了启动时的酷炫字符画 Logo。
  - **日志可见性**: 修复了 Windows 终端下日志流未输出到 stdout 的问题。
  - **可视化增强**: 明确支持生成 PnL 资金曲线图与盈亏散点图。

### 📝 文档体系完善 (Documentation)
- **全文档同步**: 更新了 `README.md`, `CONFIG_README.md`, `STRATEGY_DETAILS.md` 等所有文档。
- **新增手册**:
  - `doc/PROJECT_EXECUTION_LOGIC.md`: 详解系统启动与异步调度逻辑。
  - `doc/TRADING_AND_RISK_MANUAL.md`: 详解资金隔离与双脑决策逻辑。
- **亮点梳理**: 在 `README` 中详细整理了“双脑协同”、“资金舱壁”、“毫秒级内核”等核心优势。

### 🧹 清理与维护 (Cleanup)
- **代码清理**: 彻底移除了 `src/legacy` 目录下的旧版同步代码备份。
- **结构校验**: 修复了 `ARCHITECTURE_ANALYSIS.md` 中描述的结构与实际不符的问题。


## [v3.0.0] - 2025-12-20
### 新增功能 (Added)
- **AsyncIO 核心重构**: 使用 Python 的 `asyncio` 完全重写了交易引擎，实现非阻塞操作。
- **组件化架构**: 将单体脚本重构为模块化组件（`core`, `agents`, `trading`）。
- **高并发支持**: 支持同时监控 50+ 个交易对，延迟极低。
- **异步 DeepSeek 客户端**: 集成 `AsyncOpenAI` 进行非阻塞的大模型推理。
- **异步 CCXT**: 迁移至 `ccxt.async_support` 进行异步交易所交互。

### 变更 (Changed)
- **架构升级**: 从同步多线程模型 (v2.3) 升级为异步事件循环模型 (v3.0)。
- **性能优化**: 将 I/O 阻塞时间降至接近零；显著提高了吞吐量。
- **项目结构**: 将 `src/okx_deepseek.py` 拆分为多个模块以提高可维护性。
  - `src/core/`: 核心配置与工具 (`config.py`, `utils.py`)
  - `src/agents/`: AI 代理服务 (`ai_agent.py`)
  - `src/trading/`: 交易逻辑与风控 (`trader.py`, `risk_manager.py`)
- **配置下沉**: 配置文件 `config.json` 和启动脚本现位于项目根目录。

### 移除 (Removed)
- 移除了对 `ThreadPoolExecutor` 的依赖。

## [v2.3.0] - 2025-12-20
### 新增功能 (Added)
- **多线程优化**: 在主循环中实现 `ThreadPoolExecutor` 并行执行交易逻辑。
- **LLM 延迟优化**: 为 DeepSeek API 调用添加 `max_tokens=500` 约束以减少延迟。
- **性能分析**: 在文档中添加了详细的性能对比（同步 vs 多线程 vs 异步）。

## [v2.2.0] - 2025-12-19
### 新增功能 (Added)
- **风险管理**: 实施了“智能资金治理”和“三重风控体系”。
- **可视化**: 添加了 PnL 图表生成和 CLI 仪表板更新。
