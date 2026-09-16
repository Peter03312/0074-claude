FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖以利用层缓存
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt

# 复制应用、测试与 verify 脚本
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts
COPY conftest.py pytest.ini ./
RUN chmod +x scripts/verify.sh

EXPOSE 8000

# 默认启动长期 API 服务；verify 服务用 command 覆盖
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
