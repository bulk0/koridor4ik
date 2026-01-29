#!/usr/bin/env python3
"""
extract_tags.py — извлечение дополнительных тегов из профилей персон.

Добавляет теги:
- profession: профессия/сфера деятельности
- city_name: реальное название города
- ai_attitude: отношение к ИИ (enthusiast, pragmatic, skeptic)

Использование:
    python pipeline/extract_tags.py --dry-run        # тестовый прогон
    python pipeline/extract_tags.py --limit 5       # обработать 5 персон
    python pipeline/extract_tags.py                  # обработать всех
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# Добавляем корень проекта в путь
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline.llm_client import LLMClient

DB_PATH = ROOT / "db" / "personas.sqlite"

# Промпт для извлечения тегов через LLM
PROMPT_EXTRACT_TAGS = """Проанализируй профиль персоны и извлеки следующие характеристики.

ПРОФИЛЬ:
{profile}

ИЗВЛЕКИ:

1. **profession** — основная профессия или сфера деятельности.
   Выбери ОДНО значение из списка (или "other" если не подходит):
   - it (программист, разработчик, IT-специалист, сисадмин, DevOps)
   - engineer (инженер, конструктор, технолог - НЕ IT)
   - manager (менеджер, руководитель, директор, управленец)
   - sales (продажи, менеджер по продажам, консультант в магазине)
   - marketing (маркетолог, SMM, PR, реклама)
   - finance (бухгалтер, экономист, финансист, аудитор)
   - legal (юрист, адвокат, нотариус)
   - medical (врач, медсестра, фельдшер, фармацевт)
   - education (учитель, преподаватель, репетитор, воспитатель)
   - science (учёный, исследователь, аспирант-исследователь)
   - creative (дизайнер, художник, музыкант, писатель, фотограф)
   - media (журналист, блогер, видеоблогер, контент-мейкер)
   - hr (HR, кадровик, рекрутер)
   - student (студент, аспирант, учащийся)
   - homemaker (домохозяйка, в декрете, не работает)
   - entrepreneur (предприниматель, владелец бизнеса, самозанятый)
   - service (сфера услуг: парикмахер, официант, курьер и т.д.)
   - other (если ничего не подходит)

2. **city_name** — город проживания.
   Извлеки точное название города из профиля. Если город не указан, напиши "unknown".
   Примеры: Москва, Екатеринбург, Санкт-Петербург, Нижний Новгород, Самара

3. **ai_attitude** — отношение к ИИ/нейросетям.
   Выбери ОДНО значение:
   - enthusiast (активно использует, доверяет, продвигает)
   - pragmatic (использует как инструмент, без эмоций)
   - skeptic (критикует, не доверяет, использует минимально или не использует)

ФОРМАТ ОТВЕТА (строго):
profession: <значение>
city_name: <значение>
ai_attitude: <значение>

Ничего больше не пиши, только эти три строки."""


def get_all_personas(db_path: Path) -> list[tuple[str, str, str]]:
    """Возвращает список (persona_id, title, profile_md)."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT persona_id, title, profile_md FROM personas ORDER BY persona_id"
        ).fetchall()
    return rows


def get_existing_tags(db_path: Path, persona_id: str) -> dict[str, list[str]]:
    """Возвращает существующие теги персоны."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT category, value FROM persona_tags WHERE persona_id = ?",
            (persona_id,)
        ).fetchall()
    result: dict[str, list[str]] = {}
    for cat, val in rows:
        result.setdefault(cat, []).append(val)
    return result


def parse_llm_response(response: str) -> dict[str, str]:
    """Парсит ответ LLM в словарь тегов."""
    result = {}
    for line in response.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip().lower()
            if key in ("profession", "city_name", "ai_attitude"):
                result[key] = value
    return result


def extract_city_from_title(title: str) -> Optional[str]:
    """Быстрое извлечение города из заголовка (без LLM)."""
    # Паттерн: "..., N лет, Город" или "..., Город"
    # Известные города
    cities = [
        "москва", "санкт-петербург", "петербург", "екатеринбург", "новосибирск",
        "казань", "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
        "уфа", "красноярск", "воронеж", "пермь", "волгоград", "краснодар",
        "саратов", "тюмень", "тольятти", "ижевск", "барнаул", "ульяновск",
        "иркутск", "хабаровск", "ярославль", "владивосток", "махачкала",
        "томск", "оренбург", "кемерово", "новокузнецк", "рязань", "астрахань",
        "набережные челны", "пенза", "липецк", "киров", "чебоксары", "тула",
        "калининград", "курск", "сочи", "петропавловск", "сургут"
    ]
    title_lower = title.lower()
    for city in cities:
        if city in title_lower:
            # Возвращаем с заглавной буквы
            return city.title().replace("-На-", "-на-")
    return None


def extract_profession_from_title(title: str) -> Optional[str]:
    """Быстрое извлечение профессии из заголовка (без LLM)."""
    title_lower = title.lower()
    
    # Маппинг ключевых слов на категории
    mappings = {
        "it": ["программист", "разработчик", "it-", "айти", "девелопер", "devops", "сисадмин", "backend", "frontend"],
        "engineer": ["инженер", "конструктор", "технолог", "оператор чпу"],
        "manager": ["руководител", "директор", "зам директора", "начальник", "управляющ"],
        "sales": ["менеджер по продаж", "продавец", "консультант"],
        "marketing": ["маркетолог", "smm", "рекламщик", "pr-"],
        "finance": ["бухгалтер", "экономист", "финансист", "аудитор"],
        "legal": ["юрист", "адвокат", "нотариус"],
        "medical": ["врач", "медик", "фельдшер", "медсестр", "фармацевт", "стоматолог"],
        "education": ["учитель", "преподаватель", "репетитор", "воспитатель", "педагог"],
        "science": ["учёный", "исследователь", "научный сотрудник"],
        "creative": ["дизайнер", "художник", "музыкант", "писатель", "фотограф", "архитектор"],
        "media": ["журналист", "блогер", "видеоблогер", "контент"],
        "hr": ["hr", "кадровик", "рекрутер", "управление персоналом"],
        "student": ["студент", "аспирант", "учащийся", "выпускник"],
        "homemaker": ["домохозяйка", "в декрете", "мама в декрете"],
        "entrepreneur": ["предприниматель", "владелец бизнеса", "владелица бизнеса", "самозанят"],
        "service": ["парикмахер", "официант", "курьер", "повар", "бариста"],
    }
    
    for category, keywords in mappings.items():
        for kw in keywords:
            if kw in title_lower:
                return category
    
    # Общие паттерны для менеджеров
    if "менеджер" in title_lower:
        return "manager"
    
    return None


def save_tags(db_path: Path, persona_id: str, tags: dict[str, str], dry_run: bool = False) -> None:
    """Сохраняет теги в БД."""
    if dry_run:
        print(f"  [DRY-RUN] Сохранил бы теги: {tags}")
        return
    
    with sqlite3.connect(str(db_path)) as conn:
        for category, value in tags.items():
            if value and value not in ("unknown", "other", ""):
                # Удаляем старый тег этой категории, если есть
                conn.execute(
                    "DELETE FROM persona_tags WHERE persona_id = ? AND category = ?",
                    (persona_id, category)
                )
                # Добавляем новый
                conn.execute(
                    "INSERT INTO persona_tags (persona_id, category, value) VALUES (?, ?, ?)",
                    (persona_id, category, value)
                )
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Извлечение дополнительных тегов из профилей персон")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    parser.add_argument("--limit", type=int, default=0, help="Обработать только N персон")
    parser.add_argument("--use-llm", action="store_true", help="Использовать LLM для извлечения (медленнее, но точнее)")
    parser.add_argument("--persona-id", type=str, help="Обработать только одну персону")
    args = parser.parse_args()
    
    personas = get_all_personas(DB_PATH)
    print(f"Всего персон в базе: {len(personas)}")
    
    if args.persona_id:
        personas = [(pid, title, profile) for pid, title, profile in personas if pid == args.persona_id]
        if not personas:
            print(f"Персона {args.persona_id} не найдена")
            return
    
    if args.limit > 0:
        personas = personas[:args.limit]
        print(f"Обрабатываем первые {args.limit} персон")
    
    client = None
    if args.use_llm:
        client = LLMClient()
        # Проверка работы LLM
        info = client.preflight_check()
        print(f"LLM: {info.get('model')}")
    
    stats = {"profession": 0, "city_name": 0, "ai_attitude": 0, "skipped": 0}
    
    for i, (persona_id, title, profile_md) in enumerate(personas, 1):
        print(f"\n[{i}/{len(personas)}] {title[:60]}...")
        
        existing_tags = get_existing_tags(DB_PATH, persona_id)
        new_tags: dict[str, str] = {}
        
        # Быстрое извлечение из заголовка
        city = extract_city_from_title(title)
        if city:
            new_tags["city_name"] = city.lower()
            stats["city_name"] += 1
        
        profession = extract_profession_from_title(title)
        if profession:
            new_tags["profession"] = profession
            stats["profession"] += 1
        
        # Если нужен LLM для недостающих данных
        if args.use_llm and (not city or not profession):
            profile_short = (profile_md or "")[:2000]
            prompt = PROMPT_EXTRACT_TAGS.format(profile=f"Заголовок: {title}\n\n{profile_short}")
            
            try:
                response = client.chat(
                    system="Ты извлекаешь структурированные данные из текста. Отвечай строго по формату.",
                    user=prompt,
                    temperature=0.0
                )
                parsed = parse_llm_response(response)
                
                # Дополняем недостающие
                if not city and parsed.get("city_name") and parsed["city_name"] != "unknown":
                    new_tags["city_name"] = parsed["city_name"]
                    stats["city_name"] += 1
                
                if not profession and parsed.get("profession") and parsed["profession"] != "other":
                    new_tags["profession"] = parsed["profession"]
                    stats["profession"] += 1
                
                if parsed.get("ai_attitude"):
                    new_tags["ai_attitude"] = parsed["ai_attitude"]
                    stats["ai_attitude"] += 1
                    
            except Exception as e:
                print(f"  Ошибка LLM: {e}")
        
        if new_tags:
            print(f"  Теги: {new_tags}")
            save_tags(DB_PATH, persona_id, new_tags, dry_run=args.dry_run)
        else:
            stats["skipped"] += 1
            print(f"  Нет новых тегов")
    
    print(f"\n--- Статистика ---")
    print(f"profession: {stats['profession']}")
    print(f"city_name: {stats['city_name']}")
    print(f"ai_attitude: {stats['ai_attitude']}")
    print(f"skipped: {stats['skipped']}")


if __name__ == "__main__":
    main()
