FROM smallstep/step-cli:latest AS step-cli

FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# step CLI インストール（Step-CA証明書発行に使用）
COPY --from=step-cli /usr/local/bin/step /usr/local/bin/step

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
