# Деплой (Вариант A: VM + Docker Compose + Nginx)

План:
1) Создать VM в Yandex Cloud (Ubuntu), статический публичный IP, SG (80/443/22), доступ по SSH.
2) Установить Docker и Docker Compose.
3) Склонировать репозиторий и подготовить `.env` в корне (см. `README` проекта).
4) Настроить домен `bot.<домен>` на публичный IP (A‑запись).
5) Выпустить TLS‑сертификат (Let’s Encrypt) и подставить пути в `deploy/nginx.conf`.
6) Запустить `docker-compose` из `deploy/`.
7) Установить вебхук Telegram на `https://bot.<домен>/<WEBHOOK_SECRET_PATH>`.

Команды (пример):
```
# На VM
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER
# перелогиньтесь

git clone <repo> synthetic_v2 && cd synthetic_v2/deploy
cp ../config/env.example ../.env
# заполните .env: TELEGRAM_BOT_TOKEN, LLM_* и WEBHOOK_*

# Сертификаты (пример с certbot вне контейнера)
sudo apt install -y certbot
sudo mkdir -p /var/www/certbot
sudo certbot certonly --webroot -w /var/www/certbot -d bot.<домен>
# обновите пути в nginx.conf: /etc/letsencrypt/live/bot.<домен>/

docker compose build
docker compose up -d
```

Проверка:
- Логи Nginx: `/var/log/nginx/access.log`, `/var/log/nginx/error.log` (в контейнере).
- Логи бота: `runs/chats/bot/events.jsonl` (в volume).
- `curl -I https://bot.<домен>/health` — health‑эндпоинт отвечает `200 OK`.
- Вебхук: бот автоматически выставляет webhook при старте (если `TELEGRAM_MODE=webhook` и заданы `WEBHOOK_*`).

Хранилища и логи:
- База `db/personas.sqlite` монтируется read‑only.
- Логи/выгрузки: `runs/chats/bot` (персистентный том).
- Рекомендуется периодически архивировать/выгружать в Object Storage.


