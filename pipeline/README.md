# Пайплайн подготовки и управления персонaми (Pipeline)

Независимый от общения процесс. На выходе — БД `synthetic_v2/db/personas.sqlite` и карточки `data/personas/**/cards_md/*.md`.

Основные шаги:
- Ингест расшифровок: `synthetic_v2/pipeline/ingest_transcripts.py`
- Генерация карточек из .txt: `synthetic_v2/pipeline/generate_personas_from_transcripts.py`
- Импорт готовых карточек: `synthetic_v2/pipeline/import_cards_to_db.py`
- Импорт новых версий карточек: `synthetic_v2/pipeline/import_persona_versions.py`
- Таксономия тегов: `synthetic_v2/pipeline/convert_tags.py`
- Автотеггер (LLM): `synthetic_v2/pipeline/auto_tag_personas.py`
- Загрузка тегов в БД: `synthetic_v2/pipeline/tag_personas.py`

Пример сценария:
```
python synthetic_v2/pipeline/generate_personas_from_transcripts.py --txt-dir synthetic_v2/data/interviews/2025-12-18/done --out-dir synthetic_v2/data/personas/v20251218_autoX --db-path synthetic_v2/db/personas.sqlite
python synthetic_v2/pipeline/import_persona_versions.py --db-path synthetic_v2/db/personas.sqlite --dir synthetic_v2/data/personas/v20251218_autoX --dir synthetic_v2/data/personas/v20251218_frozen1_generated
python synthetic_v2/pipeline/convert_tags.py --input synthetic_v2/tags.xlsx --out synthetic_v2/inputs/tags/taxonomy.yaml
python synthetic_v2/pipeline/auto_tag_personas.py --db-path synthetic_v2/db/personas.sqlite --taxonomy synthetic_v2/inputs/tags/taxonomy.yaml --all --export-triples
python synthetic_v2/pipeline/tag_personas.py --db-path synthetic_v2/db/personas.sqlite --input synthetic_v2/tmp/autotags/autotags_all.triples.json
```


