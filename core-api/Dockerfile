FROM smallstep/step-cli:latest AS step-cli

FROM python:3.12-slim

RUN apt-get update && apt-get upgrade -y && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# step CLI インストール（Step-CA証明書発行に使用）
COPY --from=step-cli /usr/local/bin/step /usr/local/bin/step

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY tests/ ./tests/

# 非rootで実行する(侵害されても、コンテナ内でrootを取られたりマウントされた鍵ファイルを読まれたりしにくくする)
RUN useradd --system --uid 10001 --create-home app
USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8"]
