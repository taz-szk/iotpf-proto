FROM python:3.12-slim
RUN apt-get update && apt-get upgrade -y && apt-get install -y libpq-dev gcc curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
# 非rootで実行する(侵害されても、コンテナ内でrootを取られたりマウントされた鍵ファイルを読まれたりしにくくする)
RUN useradd --system --uid 10001 --create-home app
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
