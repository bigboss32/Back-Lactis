FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads && chmod +x start.sh

EXPOSE 8000

# Corre migraciones + seed y luego el servidor (respeta $PORT de la plataforma)
CMD ["sh", "start.sh"]
