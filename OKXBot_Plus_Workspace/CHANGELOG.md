# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v3.1.2] - 2025-12-21 (Trade Logic Perfection)
### 🐛 Logic Fixes
- **Asymmetric Trading Logic**: 
  - Fixed a logic gap where SELL signals would only close long positions but fail to open short positions (reverse trade).
  - Now fully symmetric: SELL signal = Close Long + Open Short (if configured).
- **Micro-Profit Filter**: 
  - Refined the micro-profit filter logic. It now **bypasses** the filter if AI confidence is `HIGH`, allowing emergency exits even with small profits.
- **Notification Completeness**: 
  - Added missing notifications for "Close Long" and "Close Short" actions. Now every trade action triggers a Lark/Webhook alert.
- **Notification Style**:
  - Upgraded Lark/Feishu notifications to use **Rich Text Cards (Post)**. Now alerts come with a clear title and better layout.

## [v3.1.1] - 2025-12-21 (Stability & Fixes)
### 🐛 Critical Fixes
- **Network Stability**: 
  - Added strict `timeout=10` constraint to DeepSeek API and Exchange OHLCV requests to prevent permanent hanging during network instability.
- **Risk Calculation Fix**: 
  - Fixed a critical bug in `RiskManager` where contract position value was double-counted (once in Total Equity, once in Position Value), causing inflated baseline capital and false stop-loss triggers.
- **Notification Fix**: 
  - Fixed Webhook notification failure caused by incorrect configuration scope reading (`root` vs `trading`). Now correctly injects notification config into trading scope.

### ✨ UX Improvements
- **Real-time Log Monitoring**: 
  - Upgraded `start_bot.sh` to automatically enter `tail -f` mode after startup, providing immediate visibility of the bot's status.
- **Log Persistence**: 
  - Changed asset initialization output from `print` to `logger.info` to ensure it's recorded in log files.

## [v3.1.0] - 2025-12-21 (Logic Refinement & Safety)
### 🧠 Logic Refinement
- **K-Line Context Golden Ratio**: 
  - Finalized AI input context to **15 candles**.
  - **Rationale**: Covers the RSI(14) calculation period, allowing AI to see complete patterns (W-bottom, M-top) while maintaining focus.
  - **Local Calculation**: Still requests 100 candles for precise local indicator calculation (MACD/EMA).
- **Four Personalities Clarification**: 
  - Defined explicit ADX thresholds for AI personality switching:
    - 🦁 **Trend**: ADX > 25
    - 🐼 **Grid**: ADX < 20
    - 🦉 **Choppy**: Volatility > 0.5
- **Webhook Safety Injection**: 
  - Completely removed `webhook_url` field from `config.json` to prevent accidental commits.
  - Enhanced `config.py` to auto-inject from `NOTIFICATION_WEBHOOK` environment variable.

### 🛡️ Script & Safety
- **Windows Anti-Dual-Instance**: 
  - Added `wmic` process scan to `start_bot.bat`.
  - Prevents double ordering risks by blocking multiple instances of `OKXBot_Plus.py`.
- **Linux Adaptability**: 
  - Enhanced `start_bot.sh` with smart detection for Conda and custom Venv names.

### 📚 Documentation
- **Deep Rewrite**: `TRADING_AND_RISK_MANUAL.md` overhauled.
  - Introduced "Left Brain (Hard Indicators) + Right Brain (AI Perception)" architecture description.
  - Disclosed detailed formulas for Execution Risk Control (Slippage, Micro-profit filtering).
- **Logic Completion**: `PROJECT_EXECUTION_LOGIC.md` added details on Smart Baseline persistence and Graceful Exit.

## [v3.0.1] - 2025-12-20 (SOA & High-Frequency)
### 🚀 Core Refactoring & Optimization
- **SOA Architecture**: Finalized project structure using **Service-Oriented Architecture (SOA)**.
  - `src/services/strategy`: AI Decision Brain.
  - `src/services/execution`: Trade Executor & Indicators.
  - `src/services/risk`: Global Risk Guardian.
- **High-Frequency Polling**: 
  - Added support for **second/millisecond-level** (`1s`, `500ms`) polling intervals.
  - Optimized sleep logic for microsecond-level precision.
  - Implemented smart API downgrade: Automatically requests `1s` K-line data when millisecond timeframe is configured.
- **Configuration Security**:
  - **Mandatory Security**: Removed API Key fields from `config.json` and enforced usage of environment variables (`.env`).
  - **Path Fixes**: Resolved path resolution issues for `config.py` in nested directories.

### ✨ Feature Restoration & Enhancements
- **🦁 High Confidence Override**: Restored v2.3's aggressive mode.
  - When AI confidence is `HIGH`, the system ignores the `amount` config limit to maximize capital usage.
- **🩺 Diagnostic Reports**: 
  - Instead of silent failures, the system now generates detailed reports for failed orders (insufficient balance, min limit).
  - Reports include account equity, gap amount, and AI suggestions.
- **🎨 UX Improvements**:
  - **ASCII Banner**: Restored the cool startup ASCII art logo.
  - **Log Visibility**: Fixed logging stream not showing in Windows terminals.
  - **Visualizations**: Explicitly supported generating PnL Equity Curves and Scatter Plots.

### 📝 Documentation
- **Full Sync**: Updated `README.md`, `CONFIG_README.md`, `STRATEGY_DETAILS.md`, etc.
- **New Manuals**:
  - `doc/PROJECT_EXECUTION_LOGIC.md`: Detailed system startup & async scheduling logic.
  - `doc/TRADING_AND_RISK_MANUAL.md`: Detailed capital isolation & dual-brain decision logic.
- **Highlights**: Organized core strengths like "Dual-Brain Synergy" and "Capital Isolation" in README.

### 🧹 Cleanup
- **Code Cleanup**: Completely removed legacy synchronous code backups in `src/legacy`.
- **Structure Verification**: Fixed discrepancies between `ARCHITECTURE_ANALYSIS.md` and actual code structure.

## [v3.0.0] - 2025-12-20
### Added
- **AsyncIO Core**: Complete rewrite of the trading engine using Python's `asyncio` for non-blocking operations.
- **Componentization**: Refactored monolithic script into modular components (`core`, `agents`, `trading`).
- **High Concurrency**: Support for monitoring 50+ symbols simultaneously with minimal latency.
- **Async DeepSeek Client**: Integrated `AsyncOpenAI` for non-blocking LLM inference.
- **Async CCXT**: Migrated to `ccxt.async_support` for asynchronous exchange interactions.

### Changed
- **Architecture**: Shifted from synchronous threading model (v2.3) to asynchronous event loop model (v3.0).
- **Performance**: Reduced I/O blocking time to near zero; significant throughput improvement.
- **Project Structure**: Split `src/okx_deepseek.py` into multiple modules for better maintainability.
- **Configuration**: `config.json` and startup scripts moved to the project root directory.

### Removed
- Removed dependency on `ThreadPoolExecutor` for network requests.

## [v2.3.0] - 2025-12-20
### Added
- **Multithreading**: Implemented `ThreadPoolExecutor` in the main loop to parallelize trader execution.
- **LLM Optimization**: Added `max_tokens=500` constraint to DeepSeek API calls to reduce latency.
- **Performance Analysis**: Added detailed performance comparison (Sync vs Threading vs Async) in documentation.

## [v2.2.0] - 2025-12-19
### Added
- **Risk Management**: Implemented "Smart Capital Governance" and "Triple-Check Risk Control".
- **Visualizations**: Added PnL chart generation and CLI dashboard updates.
