# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v3.1.16] - 2025-12-28 (Short Protection Fix)
### 🐛 Critical Fix
- **Short Stop-Loss Gap**:
  - **Problem**: In the legacy codebase, `run_safety_check` only calculated PnL for Long positions. Short positions failed to recognize losses when prices rose, preventing Hard Stop-Loss from triggering.
  - **Fix**: Rewrote PnL calculation logic to support `side == 'short'` and ensured correct reverse signals (Close Short = BUY) are sent upon stop-loss trigger.
  - **Benefit**: Ensures Short positions are safely intercepted by the Watchdog during pump rallies.

## [v3.1.15] - 2025-12-23 (Notification UI Overhaul)
### 🎨 UX & Notifications
- **Lark/Feishu Cards Overhaul**:
  - **Dynamic Titles**: Removed generic titles. Now uses dynamic titles like `🚀 Buy Executed | ETH/USDT` or `⚠️ Diagnostic Report` with color-coded headers (Green/Red/Orange/Blue).
  - **Markdown Optimization**:
    - **Diagnostic Reports**: Refactored into clean Markdown with collapsible "Deep Analysis" blocks and bold key metrics (Quantity, Min Limit).
    - **Trade Alerts**: Compact key-value format for Quantity, Price, and Confidence, with reasoning highlighted in blockquotes.
- **Financial Transparency**:
  - **Trade Details**: Added `Amount (U)` (Estimated Value) and `Balance (U)` (Post-trade Available) to every trade notification.
  - **Startup**: Added `Equity` display to the startup notification card.

## [v3.1.14] - 2025-12-23 (Capital Backflow Fix)
### 💰 Fund Management
- **Capital Backflow Detection**:
  - **Problem**: Previously, if a user sold existing assets (Internal Transfer), the sudden increase in USDT was misinterpreted as an "External Deposit", increasing the `deposit_offset`. This "locked" the released funds, preventing the bot from using them (as `Adjusted_Equity` remained unchanged).
  - **Fix**: Implemented smart backflow detection. If `Adjusted_Equity < Initial_Balance` AND `Deposit_Offset > 0`, the system automatically **decreases the offset** to allow funds to flow back into the tradable pool.
  - **Benefit**: Ensures that selling assets correctly restores purchasing power up to the configured `initial_balance` limit.

## [v3.1.13] - 2025-12-22 (Execution Status UI Fix)
### 🐛 Bug Fixes
- **Dashboard UI**: Fixed an issue where the `EXECUTION` column in the console dashboard always showed `N/A`. The trade execution result is now correctly propagated to the UI table.

## [v3.1.12] - 2025-12-22 (Market Order Fix & Safety Pre-check)
### 🐛 Bug Fixes
- **Market Order Limits**: Fixed `Code 51202` error where market orders for low-value coins (e.g., PEPE) exceeded exchange quantity limits. The bot now auto-truncates orders to `limits.market.max`.

### 🛡️ Safety & Debug
- **Order Pre-check**: Added a "🔍 Order Pre-check" log that details estimated value, contract size, and quantity before execution.
- **Safety Intercept**: Implemented a failsafe that blocks orders if the estimated value is abnormally high (>5x config), preventing unit conversion errors.

### ✨ UX Improvements
- **Expanded Summary**: Relaxed the "Analysis Summary" limit from 8 to 20 chars, allowing AI to express more complete logic (e.g., "RSI oversold bounce with MACD crossover").

## [v3.1.11] - 2025-12-22 (Execution Transparency)
### ✨ UX Improvements
- **Execution Status Table**:
  - Added a dedicated `EXECUTION` column to the console dashboard.
  - Now explicitly shows **WHY** a trade was skipped:
    - `🚫 QUOTA`: Insufficient fund allocation (Fixed Capital Mode).
    - `🚫 MIN`: Amount below exchange minimum limit.
    - `⏸️ WAIT`: AI confidence insufficient (HOLD).
    - `🚫 PROFIT`: Micro-profit filter triggered (Skipped close).
    - `✅ DONE`: Trade executed successfully.
- **Benefit**: Removes the "Silent Failure" confusion where bots seemed inactive but were actually filtering invalid trades.

## [v3.1.10] - 2025-12-22 (Startup Anomaly Check)
### 💰 Fund Management
- **Startup PnL Anomaly Detection**:
  - Added a failsafe check during the first PnL calculation after startup.
  - If the initial PnL is abnormally high (>10U & >10%), it is automatically identified as "Uninitialized Idle Funds" (e.g., from deposits while the bot was offline).
  - The system automatically adds this amount to `deposit_offset`, forcing the initial PnL to near-zero. This prevents immediate "False Take Profit" triggers upon startup when the account has excess idle funds.

## [v3.1.9] - 2025-12-22 (Deposit Offset)
### 💰 Fund Management Refinement
- **Deposit Offset Mechanism**:
  - Improved "Fixed Capital Mode" logic. Instead of adjusting the baseline, the bot now maintains a `deposit_offset` to track external deposits or idle funds.
  - **Startup**: If `Actual_Equity > Configured_Capital`, the difference is automatically recorded as `deposit_offset`. PnL is calculated as `(Actual - Offset) - Baseline`.
  - **Runtime**: When a deposit is detected, the `deposit_offset` increases automatically.
  - **Benefit**: This ensures that PnL always starts at 0 (or actual trading PnL) even if the account has significant idle funds, preventing false "Take Profit" triggers on startup.

## [v3.1.8] - 2025-12-22 (Fixed Capital & Auto-Deposit)
### 💰 Fund Management
- **Fixed Capital Mode**:
  - Implemented logic to strictly respect the `initial_balance` configured in `config.json`.
  - If actual equity > configured balance (e.g., due to extra deposits), the bot now **locks the baseline** to the configured amount instead of auto-calibrating upwards. This ensures PnL is calculated based on the "authorized capital" only.
- **Auto-Deposit Detection**:
  - Added smart detection for sudden equity spikes (Deposit > 10U & > 5%).
  - The bot automatically **adjusts the baseline upwards** to offset the deposit amount, keeping the PnL curve continuous and preventing false "Take Profit" triggers.
- **Aggressive Mode Refinement**:
  - In HIGH confidence mode, the "Global Fund Pool" is now capped by `min(Actual_Balance, Configured_Capital)`. The bot will never touch unauthorized idle funds even in aggressive mode.

## [v3.1.7] - 2025-12-22 (Smart Fund Sharing)
### 💰 Fund Management
- **Smart Fund Sharing (Elastic Quota)**:
  - Implemented a dual-mode fund management system:
    - **Standard Mode (LOW/MED)**: Strictly enforces `allocation` quotas (e.g., 50%) per symbol to ensure risk isolation.
    - **Aggressive Mode (HIGH)**: Allows the bot to **borrow idle funds** from the global account pool (up to 90% of total balance) when AI confidence is HIGH.
  - **Fix**: Resolved critical issue in Cross-Margin mode where multiple bots would compete for the same total balance, leading to over-leveraging.

### 🎨 UX & Notifications
- **Enhanced Color Coding**:
  - **Profit Take (🎉)**: Changed to **Carmine/Magenta** for distinct celebration visibility.
  - **Warning (⚠️)**: Changed to **Yellow** for better alert visibility.
  - **Failure (❌)**: Explicit **Red** for errors.

## [v3.1.6] - 2025-12-22 (High-Frequency Logic)
### 🧠 Logic Refinement
- **Aggressive Trading Mode**:
  - **Prompt Engineering**: Updated DeepSeek system prompt to explicitly encourage "Frequent Trading" when risk-reward ratio is favorable, reducing "HOLD" bias.
- **Bug Fix**:
  - **Critical Fix**: Resolved `UnboundLocalError: current_position` in trade executor. This bug previously caused crashes when executing "Smart Confidence Waiver" logic for STOP-LOSS or trend reversals.

### 💰 Fund Management (Smart Fund Sharing)
- **Elastic Quota System**:
  - **Standard Mode (LOW/MED Confidence)**: Strictly adheres to the configured `allocation` (e.g., 50%) for each pair. Prevents fund contention and isolates risks.
  - **Aggressive Mode (HIGH Confidence)**: Allows the bot to **break the quota limit** and utilize up to **90%** of the total account idle balance. This enables the bot to seize high-certainty opportunities with maximum capital efficiency while keeping a 10% safety buffer.
  - **Fix**: Resolved an issue where cross-margin mode would incorrectly calculate `max_trade_limit` using the entire account balance, causing over-leveraging or fund contention between multiple bots.

### 🎨 UX & Notifications
- **Color-Coded Alerts**:
  - **Profit Take (🎉)**: Now uses **Carmine/Magenta** to distinguish from generic sells.
  - **Warning (⚠️)**: Changed to **Yellow** for better visibility.
  - **Failure (❌)**: Explicit **Red** for critical errors.

## [v3.1.5] - 2025-12-21 (Multi-Instance)
### 🛠️ Infrastructure
- **Multi-Instance Support**:
  - Updated `start_bot.sh` to use directory-based PID locking (`log/bot.pid`) instead of global process scanning.
  - Allows running multiple bot instances on the same server (in different directories) without conflict.
- **Log Formatting**:
  - Improved log table formatting to fix empty header lines and alignment issues in `tail -f` view.

## [v3.1.4] - 2025-12-21 (Log Rotation)
### 🛠️ Maintenance
- **Log Management**:
  - Reverted log filename to fixed `trading_bot.log` to support standard `RotatingFileHandler`.
  - Updated startup banner instructions to reflect the fixed filename.

## [v3.1.3] - 2025-12-21 (Adaptive Risk)
### 🧠 Logic Refinement
- **Smart Confidence Waiver**:
  - **Stop-Loss Priority**: If a SELL signal is generated while holding a position (Stop Loss / Take Profit), the system now **overrides** the `min_confidence` filter. Even if AI confidence is `LOW`, the trade will execute to prevent deep drawdowns.
  - **Trend Following**: If a SELL signal is generated with keywords like "downtrend", "bearish", or "falling" in the reason, the system allows `LOW` confidence entry to avoid missing strong trend reversals.
- **Accurate Short Selling Capital**:
  - Fixed a potential issue where "Reverse Opening" (Close Long -> Open Short) might fail due to insufficient available balance calculation before the long position was closed. (Note: Logic enhanced, pending full implementation of `expected_balance` calculation).

### ✨ UX Improvements
- **Dashboard Table View**:
  - Replaced the cluttered scrolling logs with a clean, **structured table dashboard** for monitoring multiple symbols (50+).
  - Features real-time price updates, 24h change icons (🟢/🔴), and concise AI reasoning summaries in a single glance.
- **Visuals**:
  - Updated system banner to reflect v3.1.3.

## [v3.1.2] - 2025-12-21 (Async Core)
### 🧠 Logic Refinement
- **Dynamic AI Context**: 
  - The number of K-lines fed to AI is now dynamically controlled by `history_limit` in `config.json` (previously hardcoded to 30).
  - Added a safety floor (`max(10, history_limit)`) to ensure AI always has enough context.
- **Asymmetric Trading Logic**: 
  - Fixed a logic gap where SELL signals would only close long positions but fail to open short positions (reverse trade).
  - Now fully symmetric: SELL signal = Close Long + Open Short (if configured).
- **Micro-Profit Filter**: 
  - Refined the micro-profit filter logic. It now **bypasses** the filter if AI confidence is `HIGH`, allowing emergency exits even with small profits.

### ✨ UX Improvements
- **Notification Completeness**: 
  - Added missing notifications for "Close Long" and "Close Short" actions. Now every trade action triggers a Lark/Webhook alert.
- **Notification Style**:
  - Upgraded Lark/Feishu notifications to use **Rich Text Cards (Post)**. Now alerts come with a clear title and better layout.
- **Visuals**:
  - Refreshed startup ASCII banner to "ANSI Shadow" style.

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
