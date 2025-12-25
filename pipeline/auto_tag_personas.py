#!/usr/bin/env python3
"""
auto_tag_personas.py — автотеггер персон на основе их карточек (profile_md) и taxonomy.yaml.

Режимы:
  1) Превью одной персоны:
     python synthetic_v2/tools/auto_tag_personas.py \
       --db-path synthetic_v2/db/personas.sqlite \
       --taxonomy synthetic_v2/inputs/tags/taxonomy.yaml \
       --persona-id p_XXXX
     (Если --persona-id не указан, будет взята первая персона из БД.)

  2) Пакетный режим:
     python synthetic_v2/tools/auto_tag_personas.py \
       --db-path synthetic_v2/db/personas.sqlite \
       --taxonomy synthetic_v2/inputs/tags/taxonomy.yaml \
       --all --out synthetic_v2/tmp/autotags/autotags.json

Выход:
  - JSON со списком объектов:
      {"persona_id":"...", "tags":{"key":"value", "multikey":["v1","v2"]}}
  - Дополнительно можно выгрузить в формат троек для БД:
      [{"persona_id":"...", "category":"...", "value":"..."}]

Загрузка в БД:
  python synthetic_v2/tools/tag_personas.py --db-path synthetic_v2/db/personas.sqlite --input path/to/autotags.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import yaml  # type: ignore

import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
from tools.llm_client import LLMClient  # type: ignore


# ----------------------------
# Модели данных
# ----------------------------
@dataclass
class TagDef:
    key: str
    type: str  # enum | multienum | string | number | bool
    allow_multiple: bool
    enum: List[str]
    synonyms: Dict[str, str]
    prompt_hint: str


@dataclass
class Persona:
    persona_id: str
    title: str
    profile_md: str


# ----------------------------
# Taxonomy
# ----------------------------
def load_taxonomy(path: Path) -> List[TagDef]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "tags" not in doc:
        raise ValueError("taxonomy.yaml: верхний уровень должен содержать ключ 'tags'")
    tags_raw = doc["tags"]
    if not isinstance(tags_raw, list):
        raise ValueError("taxonomy.yaml: 'tags' должен быть списком объектов")
    tag_defs: List[TagDef] = []
    for item in tags_raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        typ = str(item.get("type", "")).strip().lower()
        allow_multiple = bool(item.get("allow_multiple", typ == "multienum"))
        enum_vals = item.get("enum", [])
        enum_list = [str(x).strip() for x in enum_vals] if isinstance(enum_vals, list) else []
        synonyms = item.get("synonyms", {}) or {}
        synonyms_map = {str(k).strip().lower(): str(v).strip() for k, v in synonyms.items()} if isinstance(synonyms, dict) else {}
        prompt_hint = str(item.get("prompt_hint", "")).strip()
        if not key or not typ:
            continue
        tag_defs.append(
            TagDef(
                key=key,
                type=typ,
                allow_multiple=allow_multiple,
                enum=enum_list,
                synonyms=synonyms_map,
                prompt_hint=prompt_hint,
            )
        )
    if not tag_defs:
        raise ValueError("taxonomy.yaml: список 'tags' пуст")
    return tag_defs


# ----------------------------
# DB
# ----------------------------
def fetch_personas(conn: sqlite3.Connection, *, limit: Optional[int] = None) -> List[Persona]:
    sql = "SELECT persona_id, COALESCE(title, '') as title, COALESCE(profile_md, '') as profile_md FROM personas ORDER BY created_at DESC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    return [Persona(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def fetch_persona_by_id(conn: sqlite3.Connection, persona_id: str) -> Optional[Persona]:
    row = conn.execute(
        "SELECT persona_id, COALESCE(title, ''), COALESCE(profile_md, '') FROM personas WHERE persona_id = ?",
        (persona_id,),
    ).fetchone()
    if not row:
        return None
    return Persona(str(row[0]), str(row[1]), str(row[2]))


# ----------------------------
# LLM prompting
# ----------------------------
def build_system_prompt() -> str:
    return (
        "Ты — утилита по извлечению структурированных тегов из карточек персон.\n"
        "Возвращай СТРОГО валидный JSON без комментариев и без пояснений.\n"
        "Не выдумывай значения: если данных по тегу нет, не включай его в результат."
    )


def build_user_prompt(persona: Persona, tag_defs: List[TagDef]) -> str:
    lines: List[str] = []
    lines.append("Карточка персоны (Markdown):")
    lines.append(persona.profile_md.strip())
    lines.append("")
    lines.append("Требуется извлечь значения по следующей таксономии (только то, что явно присутствует в карточке):")
    for t in tag_defs:
        desc = f"- key={t.key}, type={t.type}"
        if t.enum:
            desc += f", enum={t.enum}"
        if t.prompt_hint:
            desc += f", hint={t.prompt_hint}"
        lines.append(desc)
    lines.append("")
    lines.append("Верни строго JSON следующего вида:")
    lines.append('{"persona_id":"<id>", "tags": {"<key>": "<value_or_list>", ...}}')
    lines.append("Где:")
    lines.append('- persona_id = идентификатор персоны из БД')
    lines.append('- Ключи только из перечисленной таксономии.')
    lines.append('- Для enum/multienum — значения только из допустимого списка enum.')
    lines.append('- Если по тегу нет явных данных — этот ключ целиком отсутствует.')
    lines.append("- Не добавляй никаких полей, кроме persona_id и tags.")
    lines.append("")
    lines.append(f"persona_id для этой карточки: {persona.persona_id}")
    return "\n".join(lines)


def parse_llm_json(text: str) -> Dict[str, Any]:
    """
    Пытаемся распарсить ответ модели как JSON.
    Если встречаются артефакты, выдёргиваем первый JSON-объект.
    """
    text = text.strip()
    # Быстрый путь
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Попробуем вырезать первый JSON-объект
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        s = match.group(0)
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    raise ValueError("Не удалось распарсить JSON из ответа модели")


def canonicalize_value(tag: TagDef, value: Any) -> Optional[Union[str, List[str], bool, float]]:
    """
    Приводит значение к канону с учётом enum и synonyms.
    Возвращает None, если значение невалидное или пустое.
    """
    if tag.type in ("string", "number", "bool"):
        # Для MVP: просто возвращаем как есть (строки приводим)
        if tag.type == "string":
            s = str(value).strip()
            return s or None
        if tag.type == "number":
            try:
                return float(value)
            except Exception:
                return None
        if tag.type == "bool":
            s = str(value).strip().lower()
            if s in ("1", "true", "yes", "y", "t"):
                return True
            if s in ("0", "false", "no", "n", "f"):
                return False
            return None

    def canon_one(x: str) -> Optional[str]:
        raw = str(x).strip()
        if not raw:
            return None
        # применим синонимы по нижнему регистру
        mapped = tag.synonyms.get(raw.lower(), raw)
        if tag.enum:
            # сопоставляем по точному совпадению канонических значений
            if mapped in tag.enum:
                return mapped
            # пробуем кей-инсенситив
            for ev in tag.enum:
                if ev.lower() == mapped.lower():
                    return ev
            return None
        return mapped

    if tag.type == "enum":
        return canon_one(str(value))

    if tag.type == "multienum":
        items: List[str] = []
        if isinstance(value, list):
            source = value
        else:
            # Разделители: запятая или вертикальная черта
            source = [p.strip() for p in str(value).replace("|", ",").split(",")]
        for v in source:
            cv = canon_one(v)
            if cv and cv not in items:
                items.append(cv)
        return items or None

    return None


def validate_and_canonicalize(obj: Dict[str, Any], tag_defs: List[TagDef]) -> Dict[str, Any]:
    """
    Оставляет только допустимые ключи, приводит значения к канону и отбрасывает пустые.
    """
    tags_map = {t.key: t for t in tag_defs}
    raw_tags = obj.get("tags", {}) or {}
    if not isinstance(raw_tags, dict):
        return {"persona_id": obj.get("persona_id", ""), "tags": {}}
    out: Dict[str, Any] = {}
    for key, value in raw_tags.items():
        if key not in tags_map:
            continue
        tag = tags_map[key]
        cv = canonicalize_value(tag, value)
        if cv is None:
            continue
        out[key] = cv
    return {"persona_id": str(obj.get("persona_id", "")).strip(), "tags": out}


def tags_to_triples(persona_id: str, tags: Dict[str, Any]) -> List[Dict[str, str]]:
    triples: List[Dict[str, str]] = []
    for k, v in tags.items():
        if isinstance(v, list):
            for item in v:
                triples.append({"persona_id": persona_id, "category": k, "value": str(item)})
        else:
            triples.append({"persona_id": persona_id, "category": k, "value": str(v)})
    return triples


# ----------------------------
# Главная логика
# ----------------------------
def process_one_persona(llm: LLMClient, persona: Persona, tag_defs: List[TagDef]) -> Dict[str, Any]:
    system = build_system_prompt()
    user = build_user_prompt(persona, tag_defs)
    # Предполётная проверка (дешёвая)
    # Внимание: это реальный вызов LLM (короткий). Если провалится — подскажет, что не так.
    _ = llm.preflight_check()
    text = llm.chat(system=system, user=user, temperature=0.1)
    obj = parse_llm_json(text)
    clean = validate_and_canonicalize(obj, tag_defs)
    # Принудительно проставим persona_id, если модель не вернула
    if not clean.get("persona_id"):
        clean["persona_id"] = persona.persona_id
    return clean


def main() -> None:
    ap = argparse.ArgumentParser(description="Автотеггер персон из БД по taxonomy.yaml (LLM)")
    ap.add_argument("--db-path", type=Path, required=True, help="Путь к SQLite (personas.sqlite)")
    ap.add_argument("--taxonomy", type=Path, required=True, help="Путь к taxonomy.yaml")
    ap.add_argument("--persona-id", type=str, default=None, help="ID персоны для превью")
    ap.add_argument("--one", action="store_true", help="Взять первую персону из БД для превью")
    ap.add_argument("--all", action="store_true", help="Обработать всех персон")
    ap.add_argument("--out", type=Path, default=None, help="Путь для сохранения JSON результата")
    ap.add_argument("--export-triples", action="store_true", help="Сохранить также список троек для БД")
    args = ap.parse_args()

    tag_defs = load_taxonomy(args.taxonomy)
    llm = LLMClient()

    conn = sqlite3.connect(str(args.db_path))
    try:
        results: List[Dict[str, Any]] = []
        if args.persona_id or args.one:
            if args.persona_id:
                p = fetch_persona_by_id(conn, args.persona_id)
                if not p:
                    raise SystemExit(f"Персона {args.persona_id} не найдена")
            else:
                ps = fetch_personas(conn, limit=1)
                if not ps:
                    raise SystemExit("В БД нет персон")
                p = ps[0]
            res = process_one_persona(llm, p, tag_defs)
            results = [res]
        elif args.all:
            personas = fetch_personas(conn)
            if not personas:
                raise SystemExit("В БД нет персон")
            for p in personas:
                res = process_one_persona(llm, p, tag_defs)
                results.append(res)
        else:
            raise SystemExit("Не указан режим: используйте --persona-id/--one для превью или --all для батча")
    finally:
        conn.close()

    # Печать превью в stdout
    if results and (args.persona_id or args.one):
        print(json.dumps(results[0], ensure_ascii=False, indent=2))

    # Запись на диск
    out_path = args.out
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path("synthetic_v2/tmp/autotags") / f"autotags_{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"autotags_written={out_path}")

    if args.export_triples:
        all_triples: List[Dict[str, str]] = []
        for item in results:
            pid = str(item.get("persona_id", "")).strip()
            tags = item.get("tags", {}) or {}
            if not pid or not isinstance(tags, dict):
                continue
            all_triples.extend(tags_to_triples(pid, tags))
        triples_path = out_path.with_suffix(".triples.json")
        triples_path.write_text(json.dumps(all_triples, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"triples_written={triples_path}")


if __name__ == "__main__":
    main()


