# OKXBot Plus (CryptoOracle)

![Version](https://img.shields.io/badge/version-3.4.4-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.8+-yellow.svg)

**OKXBot Plus** (CryptoOracle) 是一个基于 **DeepSeek AI** 大模型的智能加密货币交易机器人。它结合了传统技术指标与 AI 的逻辑推理能力，能够像人类分析师一样进行市场分析、趋势判断和风险控制。

> **核心更新 (v3.4.4)**: 引入 **Sniper Recovery Mode** 和 **Trailing Stop (移动止盈)**，既能毫秒级锁定利润，又能通过 AI 狙击手策略在波动中精准捕获机会。

---

## 📚 文档导航 (Documentation)

请阅读 `doc/` 目录下的详细文档以快速上手：

*   📖 **[OKXBot Plus 项目说明书 (推荐)](doc/OKXBot_Plus_CryptoOracle_项目说明书.md)**
    *   *必读！包含完整的环境搭建、配置说明、启动教程。*
*   ⚙️ **[配置文件详解](doc/CONFIG_README.md)**
    *   *详细解释 config.json 中的每一个参数。*
*   🧠 **[核心执行逻辑](doc/PROJECT_EXECUTION_LOGIC.md)**
    *   *深入了解 AI 是如何决策的。*

---

## 🚀 快速开始 (Quick Start)

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置账号
复制 `config.example.json` 为 `config.json`，并填入您的 OKX 和 DeepSeek API Key。

### 3. 启动机器人
```bash
python src/OKXBot_Plus.py
```

---

## 💡 为什么选择 OKXBot Plus?

1.  **AI 驱动**: 不是简单的指标堆砌，而是由 DeepSeek 理解 K 线形态。
2.  **成本感知**: AI 会计算手续费，只有利润 > 3倍成本才开单。
3.  **智能风控**: 自动校准账户资金，防止虚假盈利显示；针对山寨币自动放宽止损。
4.  **中线策略**: 捕捉 15m/1h 级别的大趋势，省心、省力、省手续费。

---

## ⚠️ 免责声明

本项目仅供学习研究和辅助交易使用。加密货币市场风险极高，使用本软件产生的任何盈亏均由用户自行承担。**强烈建议先使用模拟盘进行测试！**
