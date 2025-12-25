# 使用官方轻量级 Python 镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 安装系统依赖 (如有需要，例如 gcc, git)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目源码
COPY src/ ./src/
COPY config.example.json .
COPY .env.example .

# 创建日志和图表目录
RUN mkdir -p log png

# 声明挂载点 (用于持久化配置和日志)
VOLUME ["/app/config.json", "/app/.env", "/app/log", "/app/png"]

# 启动命令
CMD ["python", "src/OKXBot_Plus.py"]
