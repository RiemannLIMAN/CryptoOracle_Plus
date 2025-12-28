# 变更日志 (Changelog)

本项目的所有主要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)，
并且本项目遵循 [语义化版本控制 (Semantic Versioning)](https://semver.org/spec/v2.0.0.html)。

## [v3.1.17] - 2025-12-28 (AI Strategy & Dual-Freq)
### 🚀 功能增强 (Enhancements)
- **双频监控机制 (Dual-Frequency Monitoring)**:
  - 实现了 `Timeframe` (分析周期) 与 `Loop Interval` (轮询间隔) 的解耦。
  - 现在支持 "看 15分钟大图" (Timeframe=15m) 的同时，保持 "每 15秒 检查一次" (Loop=15s) 的敏捷反应，完美平衡了趋势判断的稳健性与止损的及时性。
- **AI 策略全面升级 (AI Strategy Upgrade)**:
  - **稳定币识别**: 自动检测 USDC/USDT 等稳定币对，自动切换为 "均值回归" 策略，专注于 1.0000 附近的微利套利，拒绝趋势跟踪。
  - **杠杆感知**: AI 现在能感知当前杠杆倍数，在高杠杆 (如 10x) 环境下会自动收紧止损建议。
  - **稳健化改造**: 移除了 Prompt 中过于激进的 "鼓励高频交易" 指令，强化了对 ADX 趋势强度和 K 线结构的依赖。
- **文档更新**: 新增 `doc/PROJECT_COMPLETE_FLOW.md`，包含核心逻辑详解与常见问题解答。

## [v3.1.16] - 2025-12-28 (Short Protection Fix)
### 🐛 关键修复 (Critical Fix)
- **做空止损失效 (Short Stop-Loss Gap)**:
  - **问题**: 在旧版代码中，`run_safety_check` 仅计算了做多 (Long) 方向的 PnL，导致做空 (Short) 仓位在价格上涨时无法正确识别亏损，从而无法触发硬止损。
  - **修复**: 重写了 PnL 计算逻辑，增加了对 `side == 'short'` 的支持，并确保在触发止损时发送正确的反向信号（平空=BUY）。
  - **收益**: 确保了做空仓位在遭遇暴涨行情时能被 Watchdog 及时拦截。

## [v3.1.15] - 2025-12-23 (Notification UI Overhaul)
### 🎨 体验优化 & 通知升级 (UX & Notifications)
- **飞书/Lark 卡片重构**:
  - **动态标题**: 移除了通用的标题，改用动态彩色标题（如 `🚀 Buy Executed | ETH/USDT` 或 `⚠️ Diagnostic Report`），并根据消息类型自动匹配颜色（绿/红/橙/蓝）。
  - **Markdown 排版优化**:
    - **诊断报告**: 重构为精简的 Markdown 格式，关键指标（数量、最小限制）加粗显示，深度分析部分使用引用块折叠。
    - **交易通知**: 采用紧凑的键值对格式展示数量、价格和信心，AI 理由使用引用块突出显示。
- **资金透明度 (Financial Transparency)**:
  - **交易详情**: 每笔交易通知新增 `金额 (U)` (估算价值) 和 `余额 (U)` (交易后可用余额) 字段。
  - **启动通知**: 启动卡片中新增 `权益 (Equity)` 显示，方便确认初始资金状态。

## [v3.1.14] - 2025-12-23 (Capital Backflow Fix)
### 💰 资金管理 (Fund Management)
- **资金回流检测 (Capital Backflow Detection)**:
  - **问题**: 此前，如果用户卖出原有资产（内部划转），USDT 的瞬间增加会被误判为“外部充值”，从而增加 `deposit_offset`。这会导致释放的资金被锁定，无法被机器人使用（因为 `Adjusted_Equity` 保持不变）。
  - **修复**: 实现了智能回流检测。如果 `当前权益 < 配置本金` 且 `Deposit_Offset > 0`，系统会自动**减少 Offset**，允许资金回流到可交易池中。
  - **收益**: 确保卖出资产后，购买力能正确恢复到配置的 `initial_balance` 上限。

## [v3.1.13] - 2025-12-22 (Execution Status UI Fix)
### 🐛 Bug 修复 (Bug Fixes)
- **看板 UI**: 修复了控制台看板中 `EXECUTION` 列始终显示 `N/A` 的问题。现在交易执行结果能正确传递并显示在 UI 表格中。

## [v3.1.12] - 2025-12-22 (Market Order Fix & Safety Pre-check)
### 🐛 Bug 修复 (Bug Fixes)
- **市价单限制**: 修复了 `Code 51202` 错误，即针对低价币（如 PEPE）的市价单数量超过交易所限制的问题。机器人现在会自动将订单截断至 `limits.market.max`。

### 🛡️ 安全与调试 (Safety & Debug)
- **下单预检**: 新增 "🔍 下单预检" 日志，详细记录执行前的估算价值、合约张数和数量。
- **安全拦截**: 实施了熔断机制，如果估算价值异常高（>5倍配置），则拦截订单，防止单位换算错误。

### ✨ 体验优化 (UX Improvements)
- **摘要扩展**: 将 "Analysis Summary" 的长度限制从 8 字符放宽至 20 字符，允许 AI 表达更完整的逻辑（例如 "RSI oversold bounce with MACD crossover"）。

## [v3.1.11] - 2025-12-22 (Execution Transparency)
### ✨ 体验优化 (UX Improvements)
- **执行状态表**:
  - 在控制台看板中新增了专属的 `EXECUTION` 列。
  - 明确显示交易被跳过的**原因**:
    - `🚫 QUOTA`: 资金配额不足 (固定本金模式)。
    - `🚫 MIN`: 数量低于交易所最小限制。
    - `⏸️ WAIT`: AI 信心不足 (HOLD)。
    - `🚫 PROFIT`: 触发微利拦截 (跳过平仓)。
    - `✅ DONE`: 交易执行成功。
- **收益**: 消除了“静默失败”带来的困惑，让用户知道机器人是在过滤无效交易而非不工作。

## [v3.1.10] - 2025-12-22 (Startup Anomaly Check)
### 💰 资金管理
- **启动 PnL 异常检测 (Startup Anomaly Check)**:
  - 增加了一个兜底机制：在机器人启动后第一次计算 PnL 时，如果发现 PnL 异常偏高（>10U 且 >10% 本金），系统会将其判定为“未初始化的闲置资金”（例如机器人离线期间的充值）。
  - 系统会自动将这部分差额加入 `deposit_offset`，强制将初始 PnL 修正为接近 0。
  - **解决痛点**: 彻底修复了在“锁定本金模式”下，如果账户内有大量闲置资金，机器人一启动就会误判为“瞬间暴利”并立即触发止盈停机的问题。

## [v3.1.9] - 2025-12-22 (Deposit Offset)
### 💰 资金管理精修
- **充值抵扣机制 (Deposit Offset)**:
  - 改进了“锁定本金模式”的实现逻辑。机器人现在不再单纯锁定基准，而是维护一个 `deposit_offset` 来追踪外部充值或闲置资金。
  - **启动时**: 如果 `实际权益 > 配置本金`，多出的差额自动记为 `deposit_offset`。盈亏计算公式调整为 `(实际权益 - 抵扣额) - 基准本金`。
  - **运行时**: 检测到充值时，自动增加 `deposit_offset`。
  - **效果**: 即使账户内有大量闲置资金，启动时盈亏也能正确归零（或显示真实交易盈亏），彻底解决了因闲置资金被误算为盈利而触发“自动止盈”的问题。

## [v3.1.8] - 2025-12-22 (Fixed Capital & Auto-Deposit)
### 💰 资金管理
- **锁定本金模式 (Fixed Capital Mode)**:
  - 实现了对 `config.json` 中 `initial_balance` 的严格遵循。
  - 当实际权益 > 配置本金（例如有额外闲置资金）时，机器人将**强制锁定基准**为配置值，不再自动向上校准。这确保了盈亏计算仅基于用户授权的资金部分。
- **自动充值识别 (Auto-Deposit Detection)**:
  - 增加了对资金瞬间暴涨（充值 > 10U 且 > 5%）的智能识别。
  - 系统会自动**上调基准 (Smart Baseline)** 以抵消充值带来的账面增量，保持 PnL 曲线连续，防止因充值导致的“虚假止盈”误触。
- **激进模式优化**:
  - 在 HIGH 信心模式下，全局资金池上限现在受限于 `min(实际余额, 配置本金)`。即使在激进模式下，机器人也绝不会动用未授权的闲置资金。

## [v3.1.7] - 2025-12-22 (Smart Fund Sharing)
### 💰 资金管理
- **智能资金共享 (弹性配额)**:
  - 实施了双模资金管理系统：
    - **稳健模式 (LOW/MED)**: 严格执行单币种 `allocation` 配额（如 50%），确保风险隔离。
    - **激进模式 (HIGH)**: 当 AI 信心极高时，允许**借用全局闲置资金**（最高 90%），最大化捕捉机会。
  - **修复**: 解决了全仓模式下多个机器人争抢同一账户余额导致过度开仓的问题。

### 🎨 体验与通知
- **通知颜色优化**:
  - **止盈 (🎉)**: 改为 **洋红/胭脂红 (Carmine)**，增加辨识度。
  - **警告 (⚠️)**: 改为 **黄色 (Yellow)**。
  - **失败 (❌)**: 明确使用 **红色 (Red)**。

## [v3.1.6] - 2025-12-22 (High-Frequency Logic)
### 🧠 逻辑精修 (Logic Refinement)
- **激进交易模式 (Aggressive Trading)**:
  - **提示词优化**: 更新了 DeepSeek 的系统 Prompt，明确加入“鼓励频繁交易”的指令。在盈亏比合理的前提下，AI 将更倾向于捕捉短线波段，减少无效观望 (HOLD)。
- **Bug 修复**:
  - **关键修复**: 修复了 `execute_trade` 中的 `UnboundLocalError` 变量未定义错误。该错误曾导致在执行止损或反手开空（信心豁免逻辑）时程序崩溃。

### 💰 资金管理 (Smart Fund Sharing)
- **弹性配额系统 (Elastic Quota)**:
  - **稳健模式 (LOW/MED 信心)**: 严格遵守每个交易对的 `allocation` 配额（如 50%）。确保多币种运行时资金互不干扰，风险隔离。
  - **激进模式 (HIGH 信心)**: 当 AI 确信度极高时，允许**突破单币种配额**，调用账户中最多 **90%** 的闲置资金。这既保证了日常运行的纪律性，又赋予了捕捉大行情的爆发力。
  - **修复**: 解决了全仓模式下机器人错误地将整个账户余额视为可用资金，导致多币种争抢资金或过度开仓的问题。

### 🎨 体验与通知
- **消息颜色分级**:
  - **止盈 (🎉)**: 启用 **洋红/胭脂红 (Carmine)**，与普通卖出区分，增加庆祝感。
  - **警告 (⚠️)**: 改为更醒目的 **黄色 (Yellow)**。
  - **失败 (❌)**: 明确使用 **红色 (Red)** 警示严重错误。

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
