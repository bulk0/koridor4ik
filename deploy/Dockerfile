FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --no-cache-dir -r /app/bot/requirements.txt

# Копируем весь проект (для доступа к chat/, db/, bot/)
COPY . /app

# Порт для webhook (aiohttp web server в main.py)
EXPOSE 8080

CMD ["python", "-m", "bot.main"]


