# 🤖 CryptoOracle: AI 驱动的量化交易终端

> **"像华尔街专业人士一样思考，像算法机器人一样执行。"**

CryptoOracle 是一个集成 **DeepSeek** 大模型与 **CCXT** 交易框架的下一代量化交易系统。它采用全新的 **AsyncIO 异步内核** 与 **组件化架构**，实现了从单体脚本到企业级工程的蜕变。

---

## 🌟 核心亮点 (Core Highlights)

### 1. 🧠 **双脑协同决策 (Hybrid AI Strategy)**
*   **左脑 (硬计算)**: 实时计算 MACD/RSI/布林带等指标，使用 **100根** 历史数据确保数学精确性。
*   **右脑 (软感知)**: DeepSeek 接收最新 K 线（黄金分割窗口），专注于识别 W底、M头等形态。
*   **动态人格切换**: 根据 **ADX 趋势强度** 自动切换交易风格：
    *   🦁 **激进型 (Trend)**: ADX > 25，允许追涨杀跌，忽略超买信号。
    *   🦉 **避险型 (Defensive)**: 波动大但无趋势，仅在布林带极端位置博反弹。
    *   🐼 **网格型 (Grid)**: ADX < 20，横盘震荡高抛低吸。
    *   🚀 **异动唤醒 (Surge Override)**: 当检测到 **成交量爆增 (>3x)** 或 **价格瞬间剧烈波动 (>0.5%)** 时，强制绕过 ADX/RSI 门禁，确保不错过任何突发大行情。
*   **高信心特权**: 当 AI 给出 **HIGH** 信心信号时，自动触发“激进模式”，突破常规配置限制。

### 2. ⚡ **毫秒级异步内核 (Async High-Frequency Core)**
*   **全异步架构**: 基于 Python `asyncio` 重写，I/O 非阻塞。支持同时监控 **50+** 交易对而无延迟。
*   **极速轮询**: 支持 `1s` 甚至 `500ms` 的 K 线监测频率，比传统轮询机器人快 10 倍。
*   **智能降频**: 智能决定何时调用 DeepSeek 进行深度分析，既保证了对突发风控的毫秒级响应，又节省了昂贵的 Token 成本。
*   **智能 API 降级 (Smart Downgrade)**: 自动适配交易所 API 限制，以毫秒级频率获取最新的市场价格和实时指标。

### 3. 🛡️ **机构级风控体系 (Institutional Risk Control)**
*   **资金舱壁 (Capital Isolation)**: 每个币种拥有独立资金配额，杜绝单一币种亏损拖累全局。
*   **零点校准 (Zero-Start Baseline)**: 启动时自动消除系统初始化导致的时间差微小波动，精确反映 Session 盈亏。
*   **多重执行熔断 (Execution Gates)**:
    1.  **滑点保护**: 下单前毫秒级比对价格，偏差 > 1% 立即终止。
    2.  **微利拦截**: 实时计算 Taker 费率，拦截扣除手续费后无利可图的“无效交易”。
    3.  **信心过滤**: 严格执行配置的最低信心门槛。
    4.  **账户级熔断**: 15% 回撤强制冷静 1 小时。
    5.  **防连续止损**: 单次止损后强制冷却 15 分钟。
    6.  **移动止盈**: 实时追踪最高浮盈，回撤自动锁利。

### 4. 🔬 **精细化运营 (Operational Excellence)**
*   **自动费率校准**: 启动时自动获取账户 VIP 等级对应的真实手续费率。
*   **诊断式报错**: 下单失败时，发送包含账户余额、缺口金额、AI 建议值的详细诊断报告。
*   **安全优先**: 强制要求 API Key 存储于环境变量，拒绝明文配置。
*   **可视化战绩**: 内置 `plotter.py` 引擎，支持生成专业级 **PnL 资金曲线图** (Equity Curve)。
*   **多渠道通知**: 支持 **DingTalk (钉钉)**、**Feishu (飞书)**、**Telegram** 等多渠道实时推送交易信号与异常告警。
*   **模拟/实盘双模**: 提供 `test_mode` 模拟交易功能，在零风险环境下验证策略有效性。

### 5. ⚙️ 轮询周期与 AI 协同 (Polling & AI Synergy)

为了在毫秒级监控与 AI 成本之间取得平衡，我们引入了独特的协同机制：

1.  **极速轮询 (Fast Polling)**: 系统每 `1s`（或设定值）从交易所拉取最新 K 线和持仓数据，并在本地实时计算 RSI、MACD、布林带等技术指标。
2.  **本地预判 (Local Pre-check)**:
    *   首先检查本地指标是否达到关键点位（如 RSI 超买/超卖，或价格触及布林带轨道）。
    *   检查 `history_limit` 是否满足（数据量不足时不打扰 AI）。
    *   检查 `signal_limit`（避免在短时间内对同一信号重复请求 AI）。
3.  **按需唤醒 (On-Demand Wakeup)**: 只有当本地预判认为“有行情”时，才会打包所有数据发送给 DeepSeek 进行深度决策。

---

## ️ 故障排查工具 (Diagnostic Tool)
*   **一键诊断**: 内置 `test/test_connection.py`，可独立运行以测试 OKX API、DeepSeek API 及 Webhook 通知的连通性，无需启动主程序即可快速定位网络或配置问题。

## 📂 项目结构 (File Structure)

```text
OKXBot_Plus_Workspace/
├── config.json           # [配置] 交易参数与策略配置 (主配置)
├── config.example.json   # [配置] 配置模板
├── .env                  # [安全] API 密钥与敏感信息 (本地)
├── .env.example          # [安全] 环境变量模板
├── start_bot.sh          # [启动] Linux/Mac 一键启动脚本
├── start_bot.bat         # [启动] Windows 一键启动脚本
├── requirements.txt      # [依赖] Python 依赖库
├── CHANGELOG_CN.md       # [日志] 变更日志 (中文)
├── LICENSE               # [授权] 开源许可证
├── src/                  # [源码] 核心代码仓库
│   ├── OKXBot_Plus.py    #     -> 程序入口 (Bootstrap)
│   ├── core/             #     -> 基础设施层
│   │   ├── config.py     #         -> 配置加载与校验
│   │   ├── plotter.py    #         -> 盈亏绘图引擎
│   │   └── utils.py      #         -> 通用工具函数
│   └── services/         #     -> [SOA] 业务服务层
│       ├── strategy/     #         -> 策略服务
│       │   └── ai_strategy.py      # -> AI 决策大脑 (DeepSeek)
│       ├── execution/    #         -> 执行服务
│       │   └── trade_executor.py   # -> 交易执行与指标计算
│       └── risk/         #         -> 风控服务
│           └── risk_manager.py     # -> 全局熔断与仓位管理
├── test/                 # [测试] 单元测试与诊断工具
│   └── test_connection.py  #     -> 网络与API连通性诊断
├── docs/                 # [文档] 详细使用手册
│   ├── CONFIGURATION.md  #     -> 配置详解
│   └── ...
├── log/                  # [日志] 运行日志
└── png/                  # [图表] PnL 资金曲线图
```

---

## 🚀 快速开始 (Quick Start)

### 1. 环境准备
确保已安装 Python 3.10+。

#### 创建虚拟环境 (推荐)
为了避免依赖冲突，建议使用虚拟环境运行机器人。

**Linux/Mac:**
```bash
# 1. 进入项目目录
cd OKXBot_Plus_Workspace

# 2. 创建虚拟环境
python3 -m venv okx_ds

# 3. 激活环境
source okx_ds/bin/activate
```

**Windows:**
```powershell
# 1. 进入项目目录
cd OKXBot_Plus_Workspace

# 2. 创建虚拟环境
python -m venv okx_ds

# 3. 激活环境
.\okx_ds\Scripts\activate
```

```bash
# 安装依赖
pip install -r requirements.txt
```

### 2. 配置密钥
在项目根目录 (`OKXBot_Plus_Workspace`) 创建 `.env` 文件：

```bash
cp .env.example .env
# 编辑 .env 填入 OKX_API_KEY, DEEPSEEK_API_KEY 等
```

### 3. 配置策略
修改根目录下的 `config.json`：

```bash
cp config.example.json config.json
# 根据 docs/CONFIGURATION.md 调整资金分配与交易对
```

### 4. 启动机器人

*   **Windows**: 双击 `start_bot.bat`
*   **Linux/Mac**: 运行 `./start_bot.sh`

---

## 📄 文档索引 (Documentation)

*   **[配置指南 (Config Guide)](docs/CONFIGURATION.md)**: 详解 `config.json` 各项参数。
*   **[项目运行逻辑手册 (Project Execution Logic)](doc/PROJECT_EXECUTION_LOGIC.md)**: 深入理解系统启动、异步调度与数据流转。
*   **[核心交易与资金管理手册 (Trading & Capital Manual)](doc/TRADING_AND_RISK_MANUAL.md)**: 详解双脑决策、资金隔离、风控熔断机制。
*   **[架构分析 (Architecture)](doc/ARCHITECTURE_ANALYSIS.md)**: 异步架构深度解析。

---

## 🤝 支持与贡献 (Support & Contribution)

CryptoOracle 是一个开放源代码项目，我们欢迎任何形式的贡献与支持！

### 💻 参与开发 (Contributing)
我们非常欢迎开发者参与到项目的改进中来：
*   **Bug 反馈**: 遇到问题请提交 [GitHub Issue](https://github.com/your-repo/CryptoOracle/issues)。
*   **代码贡献**: 欢迎提交 Pull Request (PR) 修复 Bug 或添加新功能。
*   **文档改进**: 帮助完善中文/英文文档。

### 📬 联系我们 (Contact)
*   Email: 1211018392@qq.com

---

## ⚠️ 免责声明 (Disclaimer)

本软件为开源量化工具，**不构成任何投资建议**。
加密货币市场风险巨大，请务必使用 **Simulated Trading (模拟盘)** 或 **小资金** 进行充分测试。
开发者不对因软件错误、API 故障或极端行情导致的资金损失负责。

> **License**: CC-BY-NC-SA-4.0
