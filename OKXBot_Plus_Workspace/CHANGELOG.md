# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v3.1.1] - 2025-12-21 (Classic Features Restoration)
### 🐛 Missing Features Fix
- **Periodic Fee Calibration**: 
  - Fixed the missing periodic fee update logic during v3.0 refactoring.
  - Restored behavior: Updates on startup and automatically refreshes VIP fee rates every **4 hours** to ensure accurate micro-profit filtering.
- **Test Mode Params**:
  - Fixed `get_account_balance` ignoring `test_mode`.
  - Now correctly passes `{'simulated': True}` to the exchange when `test_mode: true`.

### 📝 Docs & UX
- **Version Comparison Update**: 
  - Updated `README.md` to correctly reflect v2.3's capabilities, focusing the comparison on **Architecture Upgrade** and **Async Core**.
- **UI Polish**:
  - Optimized `start_bot.sh` startup logs.
  - Fixed double logging issue in Windows terminals.
  - Fixed broken image paths in `README.md`.

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
