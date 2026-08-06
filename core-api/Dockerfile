FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl wget && rm -rf /var/lib/apt/lists/*

# step CLI インストール（Step-CA証明書発行に使用）
RUN wget -q https://dl.smallstep.com/gh-release/cli/gh-release-header/v0.27.0/step_linux_0.27.0_amd64.tar.gz \
    && tar xzf step_linux_0.27.0_amd64.tar.gz \
    && mv step_0.27.0/bin/step /usr/local/bin/ \
    && rm -rf step_linux_0.27.0_amd64.tar.gz step_0.27.0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
