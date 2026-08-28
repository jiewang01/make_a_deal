FROM python:3.12-slim

LABEL maintainer="make_a_deal"
LABEL version="1.0.0"

WORKDIR /app

# 安装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY src/ src/
COPY config/ config/

# 测试入口（CI 可覆盖）
CMD ["python", "-m", "pytest", "-m", "not online", "-q"]
