# synthetic_v2 — синтетические респонденты (ветка v2)

Коротко:
- Источник правды для LLM‑параметров — `.env` в `synthetic_v2/config/`.
- Подготовка/управление персонами — в `synthetic_v2/pipeline/`.
- Общение с персонами — в `synthetic_v2/chat/`.
- Входы вопросов — `synthetic_v2/inputs/questions/`.
- База персон (стартовые карточки) — `synthetic_v2/data/personas/seed/`.

Что где находится:
- `pipeline/*` — пайплайн подготовки: ingest, генерация, импорт/версии, таксономия, автотеггер, загрузка тегов.
- `chat/*` — инструменты общения/опросов по БД.
- `pipeline/llm_client.py` и `chat/llm_client.py` — провайдеры LLM (читают `.env`).
- `inputs/questions/*` — вопросы для сценариев общения.
- `data/personas/seed/v20251215_profiles_from_Claude/` — стартовые карточки.

Фиксация провайдера и параметров:
- См. `synthetic_v2/config/.env` — здесь зафиксированы:
  - `LLM_PROVIDER=anthropic`
  - `LLM_MODEL=claude-sonnet-4-5`
  - `LLM_INSECURE_SKIP_VERIFY=true`
  - `LLM_MAX_TOKENS=8000`
  - Температура задаётся параметром CLI `--temperature` в скриптах (по умолчанию 1.0).

Дорожная карта по персонaм (ingest → генерация → БД → теги):
1) Вы создаёте папку с датой и кладёте туда расшифровки (`.txt` предпочтительно).
2) `pipeline/ingest_transcripts.py` конвертирует вход в `.txt`.
3) `pipeline/generate_personas_from_transcripts.py` вызывает LLM для сборки карточек (MD) и добавляет их в БД (`SQLite`).
4) Теги: `pipeline/auto_tag_personas.py` + `pipeline/tag_personas.py` — автотеггер и загрузка в БД.

База данных:
- `SQLite` в `synthetic_v2/db/personas.sqlite`
- Таблицы:
  - `personas(persona_id TEXT PRIMARY KEY, title TEXT, profile_md TEXT, created_at TEXT)`
  - `persona_tags(persona_id TEXT, category TEXT, value TEXT, PRIMARY KEY(persona_id, category, value))`

Примеры запуска:
```bash
# 1) Менеджер персон (импорт/генерация/теги) — пайплайн
python synthetic_v2/pipeline/manage_personas.py

# 2) Общение/опросы (отбор из БД, чат, батч, пресеты) — чат
python synthetic_v2/chat/talk.py

# 3) При необходимости — старый режим “по папке карточек”
LLM_INSECURE_SKIP_VERIFY=true python synthetic_v2/tools/qa_personas.py --personas-dir synthetic_v2/data/personas/seed/v20251215_profiles_from_Claude --glob "*" --question-file synthetic_v2/inputs/questions/q6_search_human_language.md --temperature 1.0 --max-tokens 8000
```

Где зафиксирована модель:
- Только в `synthetic_v2/.env` (см. шаблон `config/env.example`). Параметр `--model` удалён: скрипты всегда читают модель/провайдера из `.env`.


