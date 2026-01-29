# 变更日志 (Changelog)

本项目的所有主要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)，
并且本项目遵循 [语义化版本控制 (Semantic Versioning)](https://semver.org/spec/v2.0.0.html)。


## [v3.9.6] - 2026-01-29 (Alpha Sniper Optimization)

### 🚀 策略核心升级 (Strategy Core Upgrade)
- **高频风控轨道 (Orbit C)**: 核心架构重大升级。将移动止盈与分段止盈监控从 AI 分析周期（如 300s）中剥离，移入独立的高频主循环轨道（Orbit C，约 10s）。确保在极端行情下，止盈止损能秒级响应，不再受 AI 思考延迟限制。
- **动态移动止盈 (ATR Trailing)**: 移动止盈的回撤阈值现已与 ATR 波动率挂钩。系统根据 `atr_ratio` 自动调节 `dynamic_callback`。高波动时放宽阈值（防止被洗），低波动时收紧（锁定微利）。
- **分段止盈机制 (Partial Profit Taking)**: 引入阶梯减仓逻辑。浮盈达到 5% 和 10% 时自动执行 30% 市价减仓。通过 `partial_tp_stages` 状态管理，确保减仓动作在单次持仓中仅触发一次，且减仓后自动下移追踪水位线。
- **软性技术过滤 (Soft Technical Filters)**: 彻底重构拦截逻辑。将 ATR、Volume、ADX 等硬性“一票否决”改为“降级信心”机制。当指标不完美但具备爆发潜力时，允许 AI 做出最终裁决，大幅降低在波动前夕的踏空概率。
- **每日利润锁定 (Daily Profit Lock)**: RiskManager 新增账户级保护。当日累计盈利超过 15% 后，系统自动激活 `global_risk_factor = 0.5`，后续交易强制减半仓位，守住战果。
- **AI 智能调仓 (Zero-Training AI Sizing)**: 革命性升级仓位管理。现在 DeepSeek 会根据盘面实时建议 `position_ratio` (0.1-1.0)。这解决了用户必须手动训练 RL 模型的痛点，实现了真正的“零配置”AI 驱动调仓。
- **架构瘦身**: 彻底移除了笨重的机器学习依赖 (`stable-baselines3`, `shimmy`)，显著降低系统内存占用与安装复杂度。仓位管理现在由“AI 建议 + 高性能启发式引擎”双重保障。

### 🛠️ 逻辑修正与增强 (Bug Fixes & Enhancements)
- **RL 仓位引擎优化**: 
    - **防御性减仓**: 修复了极度恐慌 (Sentiment < 20) 时的反向加仓逻辑，改为强制防御性减仓 (0.3x)，规避系统性崩盘。
    - **贪婪保护强化**: 将贪婪区间 (Sentiment > 80) 的减仓系数从 0.8 加强至 0.6，在高位滞涨期更主动地落袋为安。
- **AI 策略提示词强化**: 
    - **R:R 门槛**: 在高波动环境下强制 AI 追求盈亏比 (R:R) > 3.0 的机会。
    - **指令对齐**: 注入“分段止盈感知”指令，使 AI 决策与底层的 Orbit C 自动化风控逻辑保持一致。
- **可观测性增强**: 
    - **HOLD 原因日志**: 在指标不达标触发 HOLD 时，日志会详细打印具体的拦截理由（如 `ADX_WEAK`, `LOW_VOL`），而非笼统的“等待”。现在这些详细理由已默认降级至 `DEBUG` 级别，保持控制台整洁。
    - **状态追踪**: 增加了分段止盈执行的专用日志，清晰展示减仓比例、剩余仓位及调整后的最高水位线。
    - **资产简报 (Asset Summary)**: 启动时及每轮扫描表格上方新增账户资产快照，实时显示可用 USDT 余额、总权益、盈亏状态及持仓数量（如 `💰 当前权益: 104.81 U | 📈 盈亏: +4.81 U (+4.81%) | 📦 持仓: 3 个交易对`），让账户现状一目了然。
    - **Debug 模式支持**: 引入环境变量 `LOG_LEVEL=DEBUG` 开关。用户可根据需要随时开启详细日志，查看 AI 分析过程及初始化盘点表格。
- **环境优化**: 
    - **Git 过滤**: 统一根目录 `.gitignore` 配置，彻底排除 `test/`、`tools/` 等本地开发目录，确保 GitHub 仓库精简安全。
    - **Conda 支持**: `README.md` 增加了 Conda 环境创建指南，推荐在运行 RL 模块时使用以获得更稳健的依赖支持。
- **Bug 修复**:
    - **RL 模型路径修正**: 修复了 `rl_position_sizer.py` 中冗余的 `.zip` 后缀导致的路径错误（解决 `.zip.zip` 加载失败问题）。
    - **协程未等待修复**: 修复了 `trade_executor.py` 中 `save_state()` 协程未被 `await` 的 RuntimeWarning，确保系统状态（如资金校准、高水位线）能够正确持久化。

## [v3.9.5] - 2026-01-28 (Dynamic Profit Guard & Logic Refinement)

### 💎 阶梯式动态锁利 (Dynamic Profit Locking)
- **六级锁利机制 (6-Level Guard)**:
  - **Level 1 (保本)**: 收益 > 2% -> 止损移至开仓价。
  - **Level 2 (温和)**: 收益 > 5% -> 锁定 60% 利润。
  - **Level 3 (强势)**: 收益 > 10% -> 锁定 75% 利润。
  - **Level 4 (激进)**: 收益 > 20% -> 锁定 85% 利润。
  - **Level 5 (冲天)**: 收益 > 50% -> 锁定 92% 利润。
  - **Level 6 (巅峰)**: 收益 > 100% -> 锁定 95% 利润。
- **软件监控模式**: 完全采用本地软件实时监控，不再频繁修改交易所硬止损单，保护利润的同时减少 API 暴露。

### 🛠️ 关键逻辑修复 (Critical Bug Fixes)
- **状态同步修复 (State Sync Fix)**: 修复了 `trailing_max_pnl` 在重启或模块重置时无法正确传递给监控组件的问题，确保“最高水位线”记忆不丢失。
- **做空盈亏修正 (Short PnL Fix)**: 补全了做空 (Short) 持仓的盈亏计算公式，彻底解决空单移动止盈不生效的 Bug。
- **熔断阈值同步**: 账户熔断逻辑现已与配置文件中的 `max_loss_rate` 强关联，支持热重载。
- **调试增强**: 增加了 `🔍 [Trailing]` 专用调试日志，可实时查看每个币种的盈亏比率、水位线及锁利执行状态。

## [v3.9.0] - 2026-01-27 (Risk Fortification & Docs Update)

### 🛡️ 机构级风控加固 (Institutional Risk Fortification)
- **15分钟强制冷却 (Extended Cool-down)**:
  - **机制**: 将单次止损后的冷却时间从 3分钟 (180s) 延长至 **15分钟 (900s)**。
  - **目的**: 确保在单边暴跌行情中，机器人能完整跳过至少一根 15m K 线，避免在同一根 K 线的下跌途中反复接飞刀。
  - **移除特权**: 彻底移除了“高信心豁免”机制。现在无论 AI 信心多高，止损后都必须强制冷静，杜绝上头赌博。
- **账户级熔断优化 (Account Circuit Breaker)**:
  - **机制**: 简化熔断条件为纯粹的 **15% 回撤**。移除了“绝对亏损金额”的额外判断，确保小资金账户也能享受到同等严格的风控保护。
  - **持久化**: 熔断状态（如剩余冷却时间）支持本地持久化，重启后依然生效。
- **移动止盈修复 (Trailing Stop Fix)**:
  - **严重修复**: 修复了移动止盈检查逻辑被置于信号门禁（Signal Gate）之后的问题。
  - **现状**: 无论市场指标多么糟糕（导致 AI 信号被拦截），移动止盈检查都会在每轮循环的最开始**强制执行**，确保利润能及时落袋。

### 📚 文档全面升级 (Documentation Overhaul)
- **通用化重构**: 
  - `README.md` 去除了具体的版本号限制，重构为更通用的项目介绍文档，聚焦于核心架构与长期价值。
- **配置文档同步**: 
  - `docs/CONFIGURATION.md` 全面更新，详细解释了新增的 `ai_interval`、`risk_control` 等参数。
  - `config.example.json` 结构已同步至最新版代码逻辑。

## [v3.8.0] - 2026-01-25 (Dual-Track Monitor & Fast Exit)

### 🚀 核心架构升级 (Core Architecture)
- **双轨并行监控 (Dual-Track Monitoring)**:
  - **频率解耦**: 彻底分离了 **AI 战略层** (低频, 如 5m/15m) 与 **软件战术层** (高频, 60s)。
  - **机制**: 主循环强制以 60s 心跳运行，确保对市场的敏捷反应；AI 分析模块则严格遵守 `loop_interval` 配置 (如 300s)，仅在特定时间点唤醒。
  - **收益**: 既节省了 AI Token 成本，又实现了分钟级的实时风控。

### ⚡ 极速止盈 (Fast Pattern Exit)
- **1分钟三线战法 (1m Three-Line Strike)**:
  - **机制**: 在软件战术层 (高频轨道) 中集成了独立的三线战法扫描器。
  - **逻辑**: 即使 AI 跑在 15m 周期，系统也会每分钟拉取最新的 1m K线。一旦检测到 **看跌三线 (Bearish Strike)** (多单) 或 **看涨三线 (Bullish Strike)** (空单) 形态，立即市价止盈。
  - **严格确认**: 升级了量能标准，要求第4根K线的成交量必须 **大于前3根的最大值** (`vol4 > max(vol1,2,3)`)，有效过滤假突破。
  - **动态风控**: 根据 15m 形态自动计算并保存 SL (形态极值) 和 TP (1:2 盈亏比)，并在 60s 循环中实时监控触发。
  - **优势**: 实现了 "15分钟看趋势开仓，1分钟看形态逃顶" 的降维打击策略，大幅提高了利润锁定的及时性。

### 🎨 体验优化 (UX Improvements)
- **极简控制台 (Clean Console)**:
  - **去噪**: 移除了所有冗余的流水账日志（如 `Orbit B` 轮询日志、表格上方的重复理由）。
  - **信息聚合**: 将监控状态、倒计时、持仓盈亏 (PnL)、止盈止损位 (SL/TP) 以及形态预警全部整合进表格的 `ANALYSIS SUMMARY` 列。
  - **智能适配**: 增加了中文列宽补偿算法，确保表格在包含中文字符时依然对齐完美。
  - **静默守护**: 只有在真正触发极速止盈 (Fast Exit) 时才会打印详细日志和发送通知。

### 🛠️ 逻辑增强 (Logic Enhancement)
- **持仓数据保鲜 (Fresh Data Feed)**:
  - **修复**: 在 AI 分析前的最后一秒，强制刷新持仓状态 (`current_pos`)。
  - **目的**: 确保 AI 在做出决策时，使用的是毫秒级的最新持仓数据，防止因 1m 止盈刚刚触发而导致的数据不同步。
- **变量安全**: 彻底修复了 `UnboundLocalError`，优化了 `current_pos` 的初始化和异常处理流程。

## [v3.7.0] - 2026-01-25 (Hybrid Intelligence & Stability Core)

### 🚀 混合智能架构 (Hybrid Intelligence)
- **RL 仓位管理 (RL Position Sizing)**:
  - **机制**: 集成了强化学习 (RL) 模块。根据 ATR 波动率、ADX 趋势强度、情绪指数和信心分数，动态预测最佳仓位比例。
  - **兜底策略**: 在未训练模型或库缺失时，自动回退到精心设计的启发式规则引擎（死鱼盘减仓、强趋势加仓）。
  - **激进限制**: 引入了硬性风控——当市场情绪极度恐慌 (Sentiment < 20) 时，强制限制最大开仓比例为 50%，防止模型过度抄底。
- **情绪过滤插件 (Sentiment Filter)**:
  - **机制**: 实时拉取 Fear & Greed Index。在 "极度贪婪" (>75) 时禁止追高，在 "极度恐慌" (<25) 时禁止追空。
  - **可配置**: 阈值支持在 `config.json` 中动态调整。


### 🛡️ 稳定性与风控 (Stability & Risk)
- **API 智能熔断 (Circuit Breaker)**:
  - **机制**: 为 DeepSeek API 引入熔断保护。连续失败 3 次自动熔断 60 秒，防止网络波动卡死主线程。
- **权益计算修正 (Equity Fix)**:
  - **修复**: 彻底修复了测试模式下现货 (Cash Mode) 的权益计算逻辑。现在能正确统计 `Balance + MarketValue`，消除了双重计算或漏算的隐患。
- **反手优先平仓 (Close First)**:
  - **逻辑**: 在触发反手信号 (Flip) 时，如果建议仓位不足以覆盖旧仓位，系统将**强制提升下单量至全平**，确保策略意图彻底执行。
- **连续错误分级报警 (Graded Error Handling)**:
  - **分级**: 3次警告 -> 5次报警 -> 10次自动暂停交易 30分钟。

### ⚡ 性能优化 (Performance)
- **数据库批量写入 (Batch IO)**:
  - **机制**: 引入 Write Buffer，每 10 条或 5 秒刷盘一次，并添加了联合索引，大幅降低数据库 I/O 开销。
- **指标缓存调优 (TTL Tuning)**:
  - **机制**: 根据 Timeframe 动态调整缓存时间 (1m->30s, 1h->300s)，平衡实时性与 CPU 占用。
- **通知冷却 (Notification Cooldown)**:
  - **机制**: 同一类型报警在 60秒 内只发送一次，彻底解决高频报警刷屏问题。

## [v3.6.5] - 2026-01-24 (Three-Line Strike & Smart Trailing SL)

### 🚀 策略升级 (Strategy Upgrade)
- **三线战法 (Three-Line Strike)**:
  - **硬核识别 (Hardcore Detection)**: 引入了 `_check_candlestick_pattern` 函数，使用纯数学逻辑精准识别 "三连阴+一阳吞没" (看涨) 和 "三连阳+一阴吞没" (看跌) 形态。
  - **AI 联动**: 一旦识别出形态，直接点亮 `is_surge` 信号灯，强制唤醒 DeepSeek，并将形态信息注入 Prompt，作为极高置信度的反转信号。
  - **优势**: 结合了传统技术分析的确定性与 AI 的环境感知能力，显著提高胜率。

### 🛡️ 风控增强 (Risk Control)
- **实时移动硬止损 (Real Trailing Hard Stop)**:
  - **机制**: 实现了真正的 K 线跟随止损。做多时止损位跟随最近 3 根 K 线的最低点上移，做空时跟随最高点下移。
  - **安全闭环**: 增加了 `entry_price` 检查，只有在**浮盈状态**下才启动移动止损，确保不会在亏损时误操作。
  - **稳健性**: 增加了 `dynamic_stop_loss` 的非空检查，防止初始化阶段的止损跳变。
- **纯粹趋势策略**:
  - **移除止盈**: 全面清理了 Take Profit (TP) 相关逻辑。
  - **截断亏损，让利润奔跑**: 现在机器人只设止损，不设止盈。离场完全依赖移动止损触发或 AI 反手信号，确保吃满大趋势。

## [v3.6.4] - 2026-01-24 (Surge Override & Zero-Start Baseline)

### � 策略升级 (Strategy Upgrade)
- **异动唤醒机制 (Surge Override)**:
  - **机制**: 当检测到 **成交量爆增 (>3x)** 或 **价格瞬间剧烈波动 (>0.5%)** 时，强制绕过 ADX/RSI 门禁。
  - **人设切换**: 触发异动时，DeepSeek 会收到 "Surge Mode" 指令，切换为 "快进快出" 的猎杀模式，不再受常规趋势指标束缚。
  - **收益**: 确保了在突发爆发行情（如新闻利好、大户拉盘）时，机器人能第一时间响应，而不是因为 ADX 滞后而踏空。

### 💰 资金管理 (Fund Management)
- **零点校准 (Zero-Start Baseline)**:
  - **问题**: 机器人启动初始化与第一次风控检查之间存在微小的时间差（约1-3秒），导致刚启动时 PnL 可能会因为币价波动显示为非零（如 -0.04 U），给追求完美的用户带来困扰。
  - **修复**: 在首次运行时，如果检测到实际权益与基准的偏差极小 (<0.5 U)，系统会自动强制对齐基准，确保 Session PnL 从完美的 **0.00 U** 开始。
- **测试模式资金分配优化**: 
  - 修改了 `trade_executor.py` 中的 `_update_amount_auto` 函数，让它在测试模式下使用每个交易对自己的 `sim_balance` 作为基础资金。
  - 当 `allocation` 为 `auto` 时，直接使用完整的模拟余额作为配额，不再除以活跃交易对的数量。

### 🛠️ 核心修复 (Core Fixes)
- **配置读取路径修复**: 
  - 修改了 `config.py` 中的通知 Webhook 注入逻辑，确保兼容性。
  - 解决了启动通知能发送但交易执行通知失败的问题。

## [v3.6.3] - 2026-01-08 (Trend Prediction)

### 🧠 AI 策略增强 (AI Upgrade)
- **趋势预测 (Trend Prediction)**: 
  - 强制 AI 在输出中明确预测未来 4 小时的趋势方向 (UP/DOWN/SIDEWAYS) 和概率。
  - 在 Prompt 中强化“趋势预判”思维链，迫使 AI 先看大势再做决策。
- **参数落地**: 
  - 在 `config.json` 中实装了 `signal_gate` (RSI/ADX 门控) 和 `token_budget_daily` (预算) 配置。

## [v3.6.1] - 2026-01-08 (Win Rate Priority)

### 🚀 策略升级 (Strategy Upgrade)
- **胜率优先模式 (Win Rate > 60%)**:
  - **人设重塑**: 在 System Prompt 中明确注入 "胜率优先" 指令，要求 AI 宁可踏空也不开低胜率单子。
  - **硬性技术过滤 (Hard Technical Filters)**: 
    - 在执行层引入了不依赖 AI 的硬性指标拦截机制。
    - **RSI 过滤**: 禁止在 RSI > 70 时追多，或在 RSI < 30 时追空（除非 ADX > 40 确认极强趋势）。
    - **波动率过滤**: 当 ATR Ratio < 0.4 (死鱼盘) 时，强制拦截所有开仓请求。
  - **反手保护降级**: 当触发反手信号 (Flip) 但被技术指标拦截时，不再完全取消交易，而是自动**降级为仅平仓** (Close Only)，确保利润落袋或及时止损。
  - **AI 调用门控**: 仅在强信号时调用模型（RSI区间、ADX阈值、趋势状态）。
  - **Token 预算器**: 支持按日限制模型调用次数 (`token_budget_daily`)，预算耗尽后自动 HOLD。

## [v3.6.0] - 2026-01-07 (Spot/Swap Hybrid Edition)

### 🚀 核心架构升级 (Core Architecture)
- **现货/合约双模混动 (Hybrid Mode)**: 彻底重构了交易执行层，支持无缝切换 `Spot` (现货) 和 `Swap` (合约) 模式。修复了现货模式下尝试使用杠杆、以及错误计算最小下单数量的问题。
- **智能资金感知 (Auto-Scaling Strategy)**: 
    - 引入了基于账户余额的**自动策略分层机制**。
    - **微型资金 (<100U)**: 自动强制进入“全仓现货狙击模式” (0.98仓位)，取消信心折扣，最大化资金利用率。
    - **小型资金 (<1000U)**: 自动切换为“分仓防御模式”，建议 1/3 仓位。
    - **中型资金 (>10000U)**: 自动建议组合策略。
- **动态风控持久化 (State Persistence)**: 实现了 `daily_high_equity` (当日最高权益) 和 `trailing_max_pnl` (移动止盈水位线) 的本地持久化存储。即使机器人重启，风控状态（如熔断进度、止盈回撤点）也不会丢失。

### 🛡️ 风控与执行 (Risk & Execution)
- **交易精度自适应修复**: 
    - 针对小资金现货交易（如 25U 买 DOGE），增加了**自动降级重试机制**。
    - 当遇到 `Code 51008` (余额不足) 时，自动减少 5%~1% 的下单数量再次尝试，确保不错过行情。
    - 修复了滑点计算中潜在的除零错误风险。
- **幽灵止损清除**: 修复了平仓后 `dynamic_stop_loss` 未重置的严重逻辑漏洞，防止旧止损参数影响新开仓位。
- **现货移动止盈屏蔽**: 修复了在现货模式下误触发合约移动止盈逻辑的问题，防止意外卖出。
- **反手逻辑优化**: 明确了“平仓后等待下一轮开仓”的安全反手逻辑，避免余额竞争导致的双重下单。

### 📊 监控与显示 (Dashboard & UX)
- **扫描表格全面升级**: 
    - 新增 **ATR (波动率比率)** 列：直观展示市场是“死鱼”还是“巨浪”。
    - 新增 **VOL (量比)** 列：实时监控资金流入流出情况。
    - 优化了表格对齐和交易对名称截断逻辑，防止长名称破坏版面。
- **配置热重载**: 实现了 `config.json` 的非阻塞热更新，无需重启即可微调止盈止损参数（注：切换现货/合约模式仍需重启）。

### 📝 文档更新 (Documentation)
- **白皮书 (Whitepaper)**: 更新了 `3.4` 章节，详细解释了控制台新表格各字段（ATR, VOL, PERSONA）的含义及判读技巧。
- **配置策略**: 针对小资金用户，推荐并预设了“全明星现货狙击池” (DOGE, SOL, PEPE, WIF, BTC)。

---

## [v3.5.1] - 2026-01-04 (Persona Optimization)
### 🧠 策略微调 (Strategy Tuning)
- **趋势猎人阈值优化 (Trend Hunter Threshold)**:
  - 将 **Trend Hunter** 模式的触发阈值从 `ADX > 25` 提升至 `ADX > 30`。
  - **目的**: 过滤掉 ADX 25-30 区间的“伪趋势”行情（通常表现为宽幅震荡），减少在震荡市中误判为趋势而频繁止损的情况。
- **静默爬升优化 (Silent Climb Optimization)**:
  - 新增逻辑: 如果波动率极低 (Low Volatility) 但趋势极强 (`ADX > 40`)，系统将强制判定为 **HIGH_TREND** 而非 **LOW (Grid Mode)**。
  - **目的**: 防止在“缩量拉升”或“温和单边上涨”的行情中，网格策略因为波动率低而过早卖出，确保能吃到主升浪。

### ✨ 体验优化 (UX)
- **人格显性化 (Persona Visualization)**:
  - 在控制台看板 (`Dashboard`) 中新增了 **PERSONA** 列。
  - 实时显示当前 AI 所处的交易人格（`Trend Hunter`, `Grid Trader`, `Risk Guardian`, `Normal`），帮助用户更直观地理解 AI 的决策逻辑。

## [v3.5.0] - 2026-01-02 (Execution Hardening)
### 🛠️ 交易引擎重构 (Execution Engine Overhaul)
- **合约精度与单位修复 (Contract Precision)**:
  - **强制取整**: 在所有合约下单逻辑中，强制执行 `int(amount / contract_size + 1e-9)`。
  - **精度保护**: 引入 `1e-9` 偏移量，彻底修复了浮点数除法（如 `0.99999`）被截断为 0 导致下单失败的问题。
  - **模式隔离**: 严格区分现货与合约的 API 参数（如 `reduceOnly`, `tgtCcy`），杜绝了现货模式下发送合约参数导致的 API 报错。
- **资金逻辑闭环 (Fund Logic Fix)**:
  - **反手死锁修复 (Flip Protection)**: 修复了“反手开仓”时的资金计算逻辑。现在系统能正确预估“平旧仓”释放的保证金，允许在满仓状态下直接反手，不再误报“余额不足”。
  - **激进模式增强**: 在 `HIGH` 信心模式下，允许突破单币种配额限制，但严格受限于全局 `initial_balance`，既保证了进攻性又守住了总资金安全底线。
  - **余额双重确认**: 在自动提升最小下单数量时，增加了二次余额检查（含手续费 Buffer），防止因强制提升导致资金透支。
- **健壮性提升 (Robustness)**:
  - **异常熔断**: 当合约模式下获取市场信息失败时，直接熔断交易，防止错误的降级（误判为现货）导致巨额下单事故。
  - **防崩修复**: 修复了 SELL 侧逻辑中潜在的 `UnboundLocalError`，确保在网络异常时程序能优雅降级而非崩溃。

## [v3.4.5] - 2026-01-02 (Prompt Engineering & Cache)
### 🧠 AI 决策核心升级 (AI Decision Core)
- **Prompt 缓存加速 (Prompt Cache Optimization)**:
  - **机制**: 将 `taker_fee_rate` (费率) 和 `leverage` (杠杆) 等动态参数从 System Prompt 移至 User Prompt。
  - **收益**: 使 System Prompt 成为纯静态字符串，完美命中 DeepSeek 的前缀缓存 (Context Cache)，显著降低首字延迟 (TTFT) 并减少 Token 消耗。
- **职业操盘手人设 (Professional Persona)**:
  - **人设重塑**: 从“救命钱”升级为 **"账户翻倍挑战 (Alpha Generation)"**。移除了可能导致 AI 情绪化（恐惧/犹豫）的描述，转而强调职业交易员的“理性”与“进攻性”。
  - **原则升级**:
    - **进攻即防守**: 明确“犹豫就是对利润的犯罪”，防止踏空。
    - **猎杀陷阱 (Trap Hunting)**: 新增专门针对“假突破”和“插针”的猎杀逻辑。
- **链式思考 (Chain-of-Thought)**:
  - 在 `market_instruction` 中强制植入 **Sniper Scope** 思考流程：
    1.  **态势** (ADX/EMA) -> 2. **位置** (Support/Resist) -> 3. **陷阱** (Trap) -> 4. **量能** (Volume) -> 5. **扣动** (Action)。
  - 杜绝了 AI“看图说话”式的泛泛而谈，迫使其逻辑闭环。

## [v3.4.4] - 2026-01-02 (Trailing Stop)
### 💰 利润保护 (Profit Protection)
- **移动止盈 (Trailing Stop)**:
  - **痛点解决**: 解决了 "AI 分析周期 (15m) 太慢，无法捕捉 1分钟级别的脉冲式暴跌反弹，导致过山车" 的问题。
  - **机制**:
    - 在 `Fast Heartbeat` (1s) 中引入毫秒级盈亏监控。
    - **激活**: 当单笔浮盈 > 1% 时自动激活。
    - **触发**: 记录最高浮盈水位，一旦回撤超过 0.3%，立即市价止盈。
  - **配置**: 新增 `config.json` -> `strategy.trailing_stop` 字段，可自定义激活阈值和回撤比例。

### 🧠 策略调整 (Sniper Recovery)
- **狙击手回归 (Mission Critical Sniper)**:
  - 将 AI 人设重塑为 "救命钱狙击手"，解决了之前版本过于佛系或因恐惧踏空而亏损的问题。
  - **原则新增**:
    - **"使命必达"**: 胜率 > 70% 必须开火。
    - **"不对称警觉"**: 做空要比做多更敏感。
  - **置信度调整**: HIGH (>85%), MEDIUM (>70%)。

### 🛡️ 波动率过滤 (Volatility Filter)
- **阈值优化**: 将拦截阈值从 `1.5x Cost` 降至 `0.8x Cost`。
- **目的**: 避免过于严格的过滤导致错过潜在的突破机会，同时仍能拦截死鱼盘。

### 📚 文档更新 (Docs)
- 全面更新了 `PROJECT_EXECUTION_LOGIC.md` (架构图重绘)、`CONFIG_README.md` 和 `SCRIPT_USAGE.md`，确保文档与代码逻辑完全同步。

## [v3.4.3] - 2026-01-02 (Sniper Recovery Mode)
### 🧠 策略调整 (Strategy Tuning)
- **狙击手人设回归 (Sniper Persona Returns)**:
  - 将 AI 人设回滚至经典的 "Crypto Sniper" (狙击手)，并赋予 "救命钱" (Mission Critical) 的紧迫感。
  - **原则重塑**:
    - **使命必达**: 强调 "防止踏空" (FOMO Prevention)，在胜率 > 70% 时果断出手。
    - **弹无虚发**: 强调 "本金第一"，拒绝低胜率的赌博。
  - **收益**: 在震荡市中找回了 "敢于开枪" 与 "绝不乱开枪" 之间的微妙平衡。

### 🛡️ 风控与参数 (Risk & Config)
- **波动率过滤器 (Volatility Filter)**:
  - 恢复并提升波动率过滤阈值至 `0.8` (此前为 0.6)。
  - **目的**: 过滤掉更多无效的微小震荡，只在市场真正活跃时唤醒 AI，减少磨损。
- **节奏降频 (Pacing)**:
  - 将轮询间隔 (`loop_interval`) 调整为 `60s`，K线周期 (`timeframe`) 调整为 `15m`。
  - **目的**: 进入 "回血防御模式"，放慢节奏，减少高频噪音干扰，专注于捕捉 15 分钟级别的确定性趋势。

## [v3.4.2] - 2025-12-30 (Hotfix)
### 🐛 Bug 修复 (Bug Fixes)
- **保证金不足保护 (Margin Insufficiency Protection)**:
  - 增强了 `Code 51008` (保证金不足) 的错误处理逻辑。
  - **二次检查**: 在自动提升最小下单数量前，会二次计算所需保证金是否超过当前可用余额 (含手续费缓冲)。如果余额不足，不再盲目尝试下单，而是直接拦截并报警 `SKIPPED_MIN`。
  - **收益**: 彻底杜绝了因强行提升下单数量而导致的 API 报错，减少了无效请求。
- **配置属性缺失 (AttributeError Fix)**:
  - 修复了 `DeepSeekTrader` 类在初始化时未正确保存 `common_config` 导致 `risk_manager` 在读取 `loop_interval` 时报错的问题。
  - **影响**: 确保了动态 API 冷却机制和动态止盈配置能正常生效。

## [v3.4.1] - 2025-12-30 (ATR & Data Integrity)
### 🧠 策略增强 (Strategy Enhancement)
- **ATR 波动率感知 (ATR Integration)**:
  - 引入 `ATR` (平均真实波幅) 指标计算，并将其透传给 AI。
  - 在 System Prompt 中新增 "止损参考: Entry ± 2*ATR" 指令，引导 AI 做出更科学的动态止损决策。
- **K线数据完整性 (Full OHLC)**:
  - 向 AI 投喂完整的 **OHLC** (Open, High, Low, Close) 数据，而不仅仅是收盘价。
  - 这使 AI 能够识别 "插针 (Wicks)"、"锤子线" 等关键价格行为形态，大幅提升反转识别能力。

### ⚡ 性能与逻辑优化 (Performance & Logic)
- **实盘统计并行化 (Parallel Stats)**:
  - 重构 `risk_manager.py` 中的实盘战绩统计逻辑，使用 `asyncio.gather` 并行请求交易所 API。
  - **智能冷却**: 引入动态冷却机制，冷却时间严格跟随 `loop_interval` 配置。既不阻塞高频交易，也不滥用 API 配额。
- **资金分配一致性**:
  - 修复了 `trade_executor.py` 中自动资金分配 (`amount: auto`) 的逻辑缺陷，确保其正确读取 `active_symbols_count` 并执行平分策略。
- **代码瘦身**:
  - 移除了 `ai_strategy.py` 中过时的硬编码逻辑 (如 `is_stable_pair`)，将决策权完全归还给 AI 大脑。

## [v3.4.0] - 2025-12-30 (Crypto Sniper Mode)
### 🚀 策略升级 (Strategy Upgrade)
- **Crypto Sniper 模式**: 
  - 将 AI 角色从通用的 "Alpha Hunter" 升级为 "Crypto Sniper" (狙击手)。
  - **原则升级**: 引入 "不见兔子不撒鹰" (90% 把握) 和 "极速决策" 两大铁律，专注于高胜率的确定性机会。
- **单位语义统一 (Unit Unification)**:
  - 彻底解决了 AI 建议数量与交易所执行单位不一致的顽疾。
  - **强制约束**: 在 Prompt 中明确规定 `amount` 必须为 **标的货币数量** (如 0.1 BTC)，严禁输出合约张数或 USDT 金额。
  - **执行层适配**: 执行器自动读取合约面值 (Contract Size) 进行换算，确保 0.1 BTC 能正确转换为对应的张数 (如 10 张)。

### ⚡ 性能优化 (Performance)
- **网络零阻塞**: 
  - 在 DeepSeek API 客户端中禁用了自动重试 (`max_retries=0`)。
  - **收益**: 遇到网络抖动或 API 错误时立即失败并释放资源，而不是卡在重试循环中阻塞整个交易线程，显著提升了机器人的响应敏捷度。

### 🐛 Bug 修复 (Fixes)
- **语法修复**: 修复了 `ai_strategy.py` 中因 f-string 格式错误导致的 JSON 解析异常。

## [v3.3.4] - 2025-12-28 (Cost Awareness)
### 🛡️ 交易防磨损 (Cost Awareness)
- **交易成本感知 (Cost Awareness)**:
  - 在 AI Prompt 中明确计算了手续费和资金费率的磨损比例。
  - 强制 AI 遵守“预期利润 > 3倍成本”的决策原则。
  - **注意**：移除了硬性的时间冷却限制，给予 AI 在极端行情下追单的自由，但 AI 需自行承担成本评估责任。

## [v3.3.3] - 2025-12-28 (Fix)
### 🐛 Bug 修复
- **修复做空加仓的满仓逻辑**:
  - 之前只在 "买入 (BUY)" 侧修复了满仓报错逻辑。
  - 现已同步修复 "卖出 (SELL/Short)" 侧的逻辑。现在做空加仓遇到余额不足时，也会正确显示 "🔒 [满仓保护]"，而不是报错。

## [v3.3.2] - 2025-12-28 (Hotfix)
### 🐛 Bug 修复
- **修复 NameError**: 
  - 修复了在 Prompt 构建过程中因缺少 `max_buy_token` 定义而导致 DeepSeek 分析失败的问题。
  - 这是由于在添加 "资金耗尽预警" 逻辑时意外删除了变量定义。现已恢复。

## [v3.3.1] - 2025-12-28 (Fund-Aware AI & Full-Lock)
### 🧠 AI 逻辑增强 (Smart AI)
- **资金耗尽感知 (Fund Awareness)**: 
  - **问题**: 之前当可用余额不足时，AI 仍然会因为看好趋势而建议 "加仓"，导致执行器报错 "余额不足"，这既浪费算力又让用户感到焦虑。
  - **修复**: 现在当可用余额低于最小下单金额 (如 5U) 时，系统会向 AI 发送 **🔴 严重警告**，明确告知 "资金已耗尽，严禁建议 BUY"。AI 将被迫转入 "持仓管理模式"，专注于何时平仓变现。
- **满仓状态识别 (Full Lock)**:
  - **体验优化**: 如果 AI 依然头铁建议加仓但余额不足，执行器不再报错 "🚫 余额不足"，而是显示绿色的 "🔒 [满仓保护]"。
  - **UI**: 看板状态更新为 `🔒 FULL`，传达出 "资金利用率已打满，正在让利润奔跑" 的积极信号，而非错误警告。

## [v3.3.0] - 2025-12-28 (Smart-Calibration & UX Polish)
### 💰 资金管理 (Smart Calibration)
- **实盘战绩核对 (Realized PnL)**: 
  - 新增 `calculate_realized_performance`，自动从交易所拉取最近 100 笔真实交易来计算胜率和盈亏，拒绝本地计算的“自嗨”数据。
- **自动盈亏校准 (Auto-Calibration)**: 
  - 彻底解决了“重启后显示假盈利”的顽疾。
  - 机器人现在会实时比对“显示盈亏”和“交易所实盘盈亏”。如果发现差异过大（例如重启导致基准偏移），会自动调整 `deposit_offset`，强制归零虚假盈利。
- **账户目标透视**: 
  - 在日志中新增“目标资金 (Target Equity)”显示。
  - 例子: `当前 104 U | 目标 134 U`。让用户一眼看清：虽然账户里多了 4 U (Offset)，但目标依然是实打实的 +30 U，没有被吃掉。

### ✨ 体验优化 (UX Polish)
- **日志轮转升级 (Log Rotation)**: 
  - 日志文件名固定为 `trading_bot.log`（不再带时间戳），并增加了自动轮转（保留最近5个，每个10MB）。
  - **收益**: 终于可以愉快地使用 `tail -f log/trading_bot.log` 长期监控了，不用每次重启都找新文件名。
- **界面精简**: 
  - 移除了启动时的“历史战绩回顾”刷屏，只保留经典的资金曲线和实盘统计，界面更加清爽。
  - 删除了无意义的“暂无数据”占位符。
- **状态前置**: 
  - 强制在每轮“批次分析”开始前，先打印账户资金状态。让用户在看 AI 吹牛之前，先看到兜里的钱还在不在。

### ⚙️ 配置修复 (Config Fix)
- **自动开启通知**: 
  - 只要检测到 `.env` 文件里配了 `NOTIFICATION_WEBHOOK`，机器人就会自动强制开启通知功能。再也不用因为忘了改 `config.json` 里的 `enabled: false` 而收不到消息了。

## [v3.2.0] - 2025-12-28 (Dual-Heartbeat Architecture)
### 🚀 架构升级 (Architecture Upgrade)
- **双频心跳机制 (Dual-Heartbeat)**:
  - 彻底重构主循环，将 **AI 分析** 与 **风控检查** 的频率解耦。
  - **快心跳 (Tick Rate)**: 默认为 `1s`。每秒执行一次硬止损检查、账户权益监控和异常状态拦截，确保对市场插针行情的“秒级”反应。
  - **慢心跳 (Analysis Interval)**: 默认为 `60s`。每分钟唤醒一次 AI 进行深度趋势分析 (基于 15m K线)。
  - **收益**: 解决了旧版“AI 思考期间风控停摆”的致命缺陷，实现了“深思熟虑”与“敏捷反应”的完美共存。

### 💰 策略增强 (Strategy Enhancement)
- **动态目标止盈 (Dynamic Target)**:
  - AI 策略层新增 `current_account_pnl` 上下文。
  - **盈利保护**: 当距离目标止盈 < 30% 时，AI 会收到“保护指令”，倾向于落袋为安。
  - **强制停手**: 当目标达成 (100%) 时，AI 会收到“最高优先级指令”，禁止开新仓，只许平仓。
- **智能加仓 (Pyramiding)**:
  - 允许在 HIGH 信心下对已有持仓进行加仓操作。
  - 优化了 AI 人设，在 HIGH_TREND 模式下更激进地寻找加仓机会。
- **减仓/平仓优化**:
  - 修复了“尾仓无法平掉”的 Bug。平仓单不再受最小下单数量 (`min_amount/min_cost`) 的限制，确保能彻底清仓。
- **保守资金回流**:
  - 调整了 Offset 回流逻辑。只有当有效资金严重偏离 (>5%) 时才触发回流，防止在正常亏损时自动补仓导致掩盖风险。

### 📚 文档完善 (Documentation)
- **全流程图解**: 新增 `doc/PROJECT_COMPLETE_FLOW.md`，详细展示了 Python (快心跳) 与 AI (慢心跳) 的协作流程。
- **深度白皮书**: 更新 `PROJECT_WHITEPAPER.md` 至 v3.2，增加实战场景、故障排查和详细安装指南。

### 🛠️ 体验与修复 (UX & Fixes)
- **智能日志 (Smart Logging)**:
  - 引入日志去重与心跳机制。只有当账户 PnL 变动超过 `0.005 U` 时才打印日志，或每分钟打印一次保活心跳。彻底解决了控制台刷屏问题。
  - 限制了 CSV 写入和图表更新频率（1分钟/次），降低磁盘 I/O。
- **Bug 修复**:
  - 修复了 `DeepSeekTrader` 中缺失 `get_account_equity` 方法导致崩溃的问题。
  - 修复了 `RiskManager` 中变量引用顺序错误导致的 `UnboundLocalError`。
  - 修复了日志文件名生成逻辑，恢复为“每次启动生成新文件”模式，便于历史回溯。

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
  - **警告 (⚠️)**: 改为 **黄色 (Yellow)**。
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
