# 更新日志 (Changelog)

## [Unreleased] - 2025-12-30

### 优化 (Optimization)
- **AI 策略角色升级**: 将 AI 角色设定更新为 "Crypto Sniper" (加密货币狙击手)，强调高胜率和极速决策，原则包括 "不见兔子不撒鹰" (90% 把握)。
- **Prompt 增强**:
  - 明确了 `amount` 字段的输出单位为标的货币数量（如 BTC 个数），严禁使用合约张数或 USDT 金额，解决了 AI 与交易所执行层单位不统一的问题。
  - 在 User Prompt 中增加了 "请根据盘面调整" 的建议，使默认交易数量更具参考性。
- **网络请求优化**: 在 `DeepSeekAgent` 初始化中禁用了自动重试 (`max_retries=0`)，遇到错误立即返回，避免阻塞交易循环。

### 修复 (Fixes)
- **语法修复**: 修复了 `src/services/strategy/ai_strategy.py` 中因文件损坏导致的严重 Python 语法错误（包括 f-string 格式、缩进混乱等）。
- **格式修正**: 修复了 JSON 格式说明中 f-string 的转义问题 (`}}`)。
