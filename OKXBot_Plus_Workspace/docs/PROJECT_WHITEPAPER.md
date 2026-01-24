# CryptoOracle AI Trading System 深度白皮书 (v3.2)

> **目标读者**: 量化交易员、Python 开发者、DeFi 投资者
> **核心理念**: 让 AI 像人类一样思考，让代码像机器一样风控。

---

## 1. 项目全景 (Introduction)

### 1.1 什么是 CryptoOracle？
CryptoOracle 不仅仅是一个自动买卖脚本，它是一个**具备“双脑”架构的智能交易系统**。
*   **左脑 (Python)**: 负责精确计算、资金审计、毫秒级风控。
*   **右脑 (DeepSeek AI)**: 负责模糊推理、形态识别、趋势感知。

### 1.2 为什么需要它？(痛点 vs 解决方案)

| 传统痛点 | CryptoOracle 解决方案 | 实例说明 |
| :--- | :--- | :--- |
| **指标滞后** | **语义分析** | 传统 MACD 金叉往往发生在行情中段；AI 能识别 "RSI 超卖 + 底部吞没形态" 提前入场。 |
| **止损迟钝** | **双频心跳** | 15分钟 K线策略通常意味着 15分钟才检查一次止损；本项目 **每 1秒** 检查一次，插针也能跑。 |
| **资金乱用** | **账户隔离** | 交易所账户有 10000U，你想只用 500U 跑策略？本项目支持 **锁定本金**，多一分钱都不动。 |

---

## 2. 深度架构解析 (Architecture)

### 2.1 双频心跳机制 (Dual-Heartbeat) —— 核心中的核心
这是本项目最引以为傲的设计，解决了“AI 思考慢”与“风控要求快”的矛盾。

#### 场景模拟
假设您正在运行 `ETH/USDT` 策略：
*   **12:00:00 (慢心跳)**: 机器人唤醒 AI。DeepSeek 开始分析 15m K线，思考了 10秒钟。
*   **12:00:05 (快心跳)**: 此时 AI 还在思考，但市场突然暴跌 5%。
    *   *传统机器人*: 卡死在等待 AI 响应中，无法止损。
    *   *CryptoOracle*: 独立的 `RiskManager` 线程在第 5 秒检测到资产缩水 > 5%，立即触发 **硬止损**，强平仓位。
*   **12:00:10 (慢心跳)**: AI 思考结束，给出“建议卖出”。但此时仓位早已被风控系统安全清空。

### 2.2 模块交互图 (Data Flow)
```mermaid
graph TD
    A[OKX Exchange] -->|1. 实时价格 (1s)| B(Risk Manager)
    A -->|2. K线历史 (60s)| C(Trade Executor)
    
    B -->|3. 资金配额/硬止损| C
    C -->|4. 清洗后的数据| D[DeepSeek AI]
    
    D -->|5. 交易信号 (JSON)| C
    C -->|6. 下单指令| A
    C -->|7. 飞书通知| E[User]
```

---

## 3. 从零搭建指南 (Installation Guide)

### 3.1 环境准备 (Windows 示例)
**步骤 1: 安装 Python**
1.  下载 [Python 3.10 Installer](https://www.python.org/downloads/)。
2.  安装时务必勾选 ✅ **Add Python to PATH**。
3.  验证安装: 打开 CMD 输入 `python --version`，应显示 `Python 3.10.x`。

**步骤 2: 获取代码**
在存放项目的文件夹右键 -> "Open in Terminal":
```powershell
git clone https://github.com/your-repo/OKXBot_Plus_Workspace.git
cd OKXBot_Plus_Workspace
```

### 3.2 配置文件详解 (Configuration)

**步骤 3: 填写 `.env` (API 密钥)**
复制 `.env.example` 重命名为 `.env`，使用记事本打开：
```ini
# 申请地址: OKX 官网 -> API -> 创建 V5 API
OKX_API_KEY="your_api_key_here"
OKX_SECRET_KEY="your_secret_key_here"
OKX_PASSPHRASE="your_passphrase_here"

# 申请地址: DeepSeek 开放平台
DEEPSEEK_API_KEY="sk-xxxxxxxx"

# (可选) 飞书 Webhook 用于接收通知
NOTIFICATION_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

**步骤 4: 调整 `config.json` (策略参数)**
这是机器人的控制面板，关键参数解释如下：
```json
{
  "trading": {
    "market_type": "swap",       // 模式: spot(现货) 或 swap(合约)
    "timeframe": "15m",          // AI 看图周期: 15分钟
    "loop_interval": 60,         // AI 思考间隔: 60秒 (越短越灵敏，但费钱)
    "risk_control": {
      "initial_balance_usdt": 1000.0, // 🔒 锁定本金: 即使账户有 10000U，机器人只当 1000U 用
      "max_loss_rate": 0.05           // 🛑 硬止损: 亏损 5% (50U) 立即清仓停机
    }
  },
  "symbols": [
    {
      "symbol": "ETH/USDT:USDT", // 交易对 (注意合约要加 :USDT)
      "allocation": 0.3          // 仓位分配: 占总资金的 30% (即 300U)
    }
  ]
}
```

### 3.3 启动与验证
双击 `start_bot.bat`。
*   **成功标志**:
    1.  看到 ASCII Logo。
    2.  日志显示 `✅ 初始本金确认: 1000.00 U`。
    3.  控制台开始每分钟刷新 `MARKET SCAN` 表格。

### 3.4 读懂市场扫描表格 (Market Scan Dashboard)
控制台每轮扫描会输出如下表格，各项含义如下：

```text
SYMBOL         | PRICE      | 24H%     | PERSONA         | RSI  | ATR  | VOL  | SIGNAL   | CONF     | EXECUTION
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
DOGE/USDT      | $0.15      | 🔴 -0.22% | Risk Guardian   | 32   | 0.8  | 0.9  | ✋ HOLD   | 💤 LOW   | 🚫 CONF
```

| 字段 | 含义 | 判读技巧 |
| :--- | :--- | :--- |
| **PRICE** | 当前最新成交价 | - |
| **24H%** | 24小时涨跌幅 | 🟢 上涨 / 🔴 下跌 |
| **PERSONA** | **AI当前人格** | AI 根据行情自动切换的模式：<br>• **Trend Hunter**: 趋势猎人 (追涨杀跌)<br>• **Reversal Sniper**: 反转狙击手 (抄底摸顶)<br>• **Risk Guardian**: 风控守卫 (空仓观望)<br>• **Sleepy Cat**: 沉睡猫 (死鱼盘) |
| **RSI** | 相对强弱指标 | • `>70`: 超买 (可能回调)<br>• `<30`: 超卖 (可能反弹) |
| **ATR** | **波动率比率** | • `< 0.6`: 死鱼盘 (适合网格或观望)<br>• `> 1.5`: 巨浪 (适合趋势策略，风险高) |
| **VOL** | **量比 (Volume Ratio)** | • `> 1.0`: 放量 (成交活跃)<br>• `< 0.8`: 缩量 (人气低迷)<br>• `> 2.0`: 爆量 (可能变盘) |
| **SIGNAL** | AI 交易信号 | `🚀 BUY` (买入), `📉 SELL` (卖出/平仓), `✋ HOLD` (持有/观望) |
| **CONF** | AI 信心指数 | • `🔥 HIGH`: 强烈建议 (可能重仓)<br>• `⚡ MED`: 一般建议<br>• `💤 LOW`: 犹豫不决 (通常不操作) |
| **EXECUTION** | 执行状态 | • `✅ DONE`: 交易成功<br>• `🚫 CONF`: 信心不足，放弃交易<br>• `🔒 FULL`: 仓位已满，无法加仓<br>• `⏸️ HOLD`: 无操作 |

---

## 4. 交易逻辑实战 (Trading Logic in Action)

### 4.1 场景一：AI 如何决定买入？
**市场背景**: ETH 在 2000U 横盘震荡，突然放量突破 2050U。

1.  **数据采集**: 机器人抓取最近 15 根 K 线，计算出 `RSI=65`, `ADX=30` (强趋势)。
2.  **AI 思考**:
    > "检测到 ADX > 25，进入**趋势猎手**模式。价格突破布林带上轨，且 MACD 零轴上方金叉。这是一次有效的突破。"
3.  **资金计算**:
    *   可用本金: 1000U
    *   配置配额: 30% -> 300U
    *   AI 信心: **HIGH** (触发激进模式，允许突破配额)
    *   最终下单: 决定投入 500U (50%) 追涨。
4.  **执行**: 市价买入 0.25 ETH。

### 4.2 场景二：资金回流与自动充值
**背景**: 机器人正在运行，您突然往账户里充了 5000U。

1.  **快心跳检测**: 1秒后，`RiskManager` 发现账户总权益从 1000U 变成了 6000U。
2.  **异常判定**: 变化量 (+5000U) 远超正常波动范围。
3.  **自动修正**:
    *   系统不会误判为“赚了 5000U”而触发止盈。
    *   系统会自动增加 `Deposit Offset = 5000`。
    *   **计算结果**: `有效权益 = 6000 - 5000 = 1000U`。
    *   **结果**: 机器人继续按 1000U 的本金跑策略，不受充值干扰。

---

## 5. 故障排查手册 (Troubleshooting)

### 5.1 启动报错 "Invalid API Key"
*   **现象**: 启动后立即退出，日志提示 `{"code": "50004", "msg": "Endpoint request failed"}`。
*   **原因**: API Key 填错，或者选错了 `Simulated` (模拟盘) 模式但用了实盘 Key。
*   **解决**:
    1.  检查 `.env` 文件，确保没有多余空格。
    2.  检查 `config.json` 中的 `"test_mode": true` 是否与您的 API 权限匹配（模拟盘 Key 只能用于模拟盘）。

### 5.2 报错 "Insufficient Balance" 但账户有钱
*   **现象**: 账户有 1000U，下单 10U 却提示余额不足。
*   **原因**: OKX 账户模式问题。您可能处于“简单交易模式”或资金在“资金账户”而非“交易账户”。
*   **解决**:
    1.  去 OKX 网页端 -> 资产 -> 划转 -> 将资金转入 **交易账户**。
    2.  设置 -> 账户模式 -> 选择 **单币种保证金** 或 **跨币种保证金**。

### 5.3 控制台长时间无反应
*   **现象**: 超过 5 分钟没有新日志。
*   **原因**:
    1.  这是正常的！如果行情波动 < 0.1%，智能日志系统会静默。
    2.  或者 DeepSeek API 卡死。
*   **验证**: 观察是否有 `💓` 心跳日志每分钟出现一次。如果有，说明程序正常；如果没有，请重启。

---

## 6. 进阶玩法 (Advanced)

### 6.1 如何同时跑 ETH 和 BTC？
只需在 `config.json` 的 `symbols` 列表中添加：
```json
"symbols": [
  { "symbol": "ETH/USDT:USDT", "allocation": 0.4 }, // 40% 给 ETH
  { "symbol": "BTC/USDT:USDT", "allocation": 0.4 }  // 40% 给 BTC
]
```
*注意：剩余 20% 建议留作缓冲金。*

### 6.2 如何修改 AI 的性格？
在 `src/services/strategy/ai_strategy.py` 中，您可以修改 `_get_role_prompt` 函数。
*   想更激进？将 `ADX > 25` 的阈值改为 `ADX > 15`。
*   想做超短线？提示 AI 关注 `1m` K线而非 `15m`。

---
**CryptoOracle Team** | 2025
