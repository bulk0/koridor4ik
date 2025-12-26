FROM python:3.11-alpine AS builder

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache build-base
COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip wheel --wheel-dir /wheels -r /app/bot/requirements.txt

FROM python:3.11-alpine AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache ca-certificates
COPY --from=builder /wheels /wheels
# Сначала скопируем requirements.txt, затем установим зависимости из wheel-кэша
COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /app/bot/requirements.txt

# Копируем проект
COPY . /app

EXPOSE 8080
CMD ["python", "-m", "bot.main"]


