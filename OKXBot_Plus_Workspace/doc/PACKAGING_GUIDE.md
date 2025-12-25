# 📦 项目打包与部署指南 (Packaging & Deployment)

本项目支持两种打包部署方式：**Docker 容器化部署** (推荐服务器使用) 和 **Windows 可执行文件** (推荐个人电脑使用)。

---

## 方案一：Windows EXE 打包 (个人用户)

如果您希望在没有安装 Python 环境的电脑上运行，或者希望生成一个独立的 `.exe` 程序。

### 1. 运行构建脚本
双击项目根目录下的 `build_exe.bat` 脚本。
脚本会自动安装 `pyinstaller` 并开始打包。

### 2. 获取程序
打包完成后，可执行文件位于 `dist/CryptoOracle.exe`。

### 3. 运行须知
`CryptoOracle.exe` **不是** 完全独立的，它需要依赖配置文件。
请确保将以下文件复制到 `.exe` 同级目录下：
*   `config.json` (您的配置文件)
*   `.env` (您的密钥文件)
*   `log/` (可选，程序会自动创建)

---

## 方案二：Docker 部署 (服务器/VPS)

如果您需要在 Linux 服务器上 7x24 小时稳定运行，推荐使用 Docker。

### 1. 安装 Docker
确保服务器已安装 Docker 和 Docker Compose。

### 2. 准备配置
在服务器项目目录中准备好 `config.json` 和 `.env` 文件。

### 3. 启动容器
在项目根目录运行：
```bash
docker-compose up -d
```

### 4. 常用指令
*   **查看日志**: `docker-compose logs -f`
*   **重启**: `docker-compose restart`
*   **停止**: `docker-compose down`
*   **更新镜像**: 
    ```bash
    git pull
    docker-compose build --no-cache
    docker-compose up -d
    ```

---

## 目录结构说明
打包/容器化后的核心文件映射关系：

| 文件/目录 | 说明 |
| :--- | :--- |
| `config.json` | 交易策略配置文件 (必须存在) |
| `.env` | API 密钥与敏感配置 (必须存在) |
| `log/` | 运行日志输出目录 |
| `png/` | 资金曲线图输出目录 |
