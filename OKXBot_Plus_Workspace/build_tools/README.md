# 📦 CryptoOracle 打包与部署指南

本文档介绍如何将 CryptoOracle 打包为 Linux 下的单文件可执行程序，方便在服务器上快速部署。

## 1. 准备工作

由于目标环境是 Linux，而您当前可能在 Windows 上开发，我们提供了一套基于 **Docker** 的交叉编译方案。

**您需要安装:**
- [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)

## 2. 一键打包

1.  确保 Docker Desktop 已启动。
2.  双击运行 `build_tools/build_linux.bat` 脚本。
3.  等待编译完成（第一次运行需要下载基础镜像，可能较慢）。

脚本会自动执行以下步骤：
- 创建构建环境
- 安装所有依赖 (`requirements.txt`)
- 使用 PyInstaller 打包成单文件
- 自动整理发布包

## 3. 部署

编译成功后，会在根目录下生成 `release` 文件夹，其中包含：

```text
release/
├── CryptoOracle      # 主程序 (Linux可执行文件)
├── config.json       # 配置文件 (已包含默认模版)
├── .env              # 密钥文件 (需填写 API Key)
└── start.sh          # 启动脚本
```

**部署步骤:**
1.  将 `release` 文件夹上传到您的 Linux 服务器。
2.  编辑 `.env` 填入您的 API Key。
3.  编辑 `config.json` 调整策略。
4.  运行启动脚本：
    ```bash
    ./start.sh
    ```

## 4. 常见问题

**Q: 为什么生成的包在 Windows 上打不开？**
A: 因为这是 Linux 的二进制文件（ELF格式），只能在 Linux 系统（如 Ubuntu, CentOS）上运行。

**Q: 可以在 Linux 服务器上直接编译吗？**
A: 可以。如果服务器上有 Python 环境，直接运行：
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   cd build_tools
   pyinstaller --clean --distpath ../dist --workpath ../build linux_build.spec
   ```
