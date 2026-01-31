# 🚀 启动脚本使用指南 (Script Usage Guide)

为了方便在 Linux (Ubuntu/CentOS) 或 Windows 上长期稳定运行机器人，我们提供了位于项目根目录的一键启动脚本。

> **适用版本**: v3.9.6 (Alpha Sniper Optimized) 及以上

## ✨ 脚本功能
1.  **自动环境识别**：智能检测 `venv`, `conda` 或系统 Python 环境。
2.  **[v3.9.6] 自动状态清理**：启动脚本会自动清理旧的 `.db` 和 `state_*.json` 文件，防止状态冲突，确保“零点校准 (Zero-Start)”生效。
3.  **实时日志监控**：启动后自动进入 `tail -f` 模式，实时滚动显示机器人运行状态。
3.  **后台静默运行**：(Linux) 使用 `nohup` 让机器人在后台运行，退出日志查看或关闭 SSH 窗口均不会影响机器人。
4.  **防重复启动**：自动检测是否已有 `OKXBot_Plus.py` 实例运行，防止资金冲突。

---

## 🛠️ Linux / Mac 快速开始

### 1. 赋予执行权限 (首次需要)
进入项目根目录：

```bash
cd OKXBot_Plus_Workspace
chmod +x start_bot.sh
```

### 2. 启动机器人
直接运行根目录下的脚本：

```bash
./start_bot.sh
```

**启动成功示例**：
```text
✅ 检测到已激活的 Conda 环境: /root/anaconda3/envs/okx_ds
⚡ 正在启动后台进程...
⏳ 正在验证进程状态...
✅ 启动成功！机器人正在后台运行。
🆔 进程 PID: 69014
📄 日志文件: log/console_output.log
```

### 3. 查看日志
脚本启动后会自动进入日志查看模式。如果您退出了查看（Ctrl+C），可以随时再次查看：

```bash
# 查看实时交易逻辑 (自动轮转日志)
tail -f log/trading_bot.log
```

---

## 🪟 Windows 快速开始

### 启动方法
1. 进入项目根目录 `OKXBot_Plus_Workspace`。
2. 双击运行 `start_bot.bat`。
3. **注意**: 请保持黑色命令窗口开启。Windows 暂不支持像 Linux 那样的 `nohup` 后台模式，关闭窗口会停止机器人。

---

## 🩺 故障排查 (Diagnostics)

如果遇到启动失败或网络问题，建议先运行诊断工具：

```bash
# 测试 API 连接和通知功能
python test/test_connection.py
```

该工具会分别检测：
1.  OKX API 连通性及余额读取。
2.  DeepSeek API 连通性。
3.  Webhook 通知推送（支持飞书/钉钉）。

---

## ⚙️ 高级配置 (自定义虚拟环境)

如果您使用了自定义名称的虚拟环境（例如 `okx_ds`），`start_bot.sh` 默认可能找不到。

### 方法 A：先激活环境 (推荐)
```bash
conda activate okx_ds
./start_bot.sh
```

### 方法 B：修改脚本配置
编辑 `start_bot.sh`，修改 `CUSTOM_VENV_NAME` 变量：
```bash
CUSTOM_VENV_NAME="okx_ds"
```

---

## 🕹️ 运维管理

### 停止机器人 (Linux)
```bash
# 查找 PID
ps -ef | grep OKXBot_Plus.py

# 停止进程
kill <PID>
```

### 更新代码
如果您更新了代码，请先停止机器人，拉取代码，然后重新运行启动脚本。

```bash
git pull
./start_bot.sh
```
