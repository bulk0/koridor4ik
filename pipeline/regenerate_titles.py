#!/usr/bin/env python3
"""
Перегенерация заголовков персон:
- Генерирует уникальные имя+фамилию через LLM
- Генерирует краткую характеристику через LLM
- Извлекает возраст, город, профессию из профиля
- Собирает новый заголовок и сохраняет в отдельную БД
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Set, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from pipeline.llm_client import LLMClient  # noqa: E402

# ============== ПРОМПТЫ ==============

PROMPT_NAME = """Придумай запоминающееся имя и фамилию для персоны на основе её профиля.

Требования:
- Фамилия должна быть КОРОТКОЙ: максимум 12-15 букв, идеально 6-10
- Имя и фамилия должны мгновенно ассоциироваться с этой конкретной персоной
- Фамилия должна быть "говорящей" — отражать ключевую черту, привычку или особенность
- Допустимы неологизмы, игра слов, но они должны легко читаться и произноситься
- Главное — лаконичность, запоминаемость и лёгкая ирония

ПЛОХИЕ фамилии (слишком длинные/громоздкие или банальные):
- Промптозаменитель, Тридцатьшестьчасовая, Многозадачников — НЕТ!
- Промптов, Промтов, Промтер, Промтова и любые производные от "промпт/промт" — ЗАПРЕЩЕНЫ!

ХОРОШИЕ фамилии (короткие, ёмкие):
- Табов, Спринтер, Кликов, Дедлайн, Копипаст, Чатов, Буфер, Скролл

КРИТИЧЕСКИ ВАЖНО — ИЗБЕГАЙ ДУБЛИРОВАНИЯ:
Фамилия НЕ должна повторять краткое описание персоны!
- Если в описании "вахтовик" — фамилия НЕ про вахту
- Если в описании "гуглит" — фамилия НЕ про гугл и поиск  
- Если в описании "экономит время" — фамилия НЕ про время
- Если в описании Яндекс/Алиса — фамилия НЕ про них

Найди ДРУГУЮ черту: хобби, словечко, профессиональный жаргон, семейную ситуацию.

{used_names_block}

Краткое описание персоны (НЕ дублируй его в фамилии!):
{description}

Ответь ТОЛЬКО именем и фамилией в формате "Имя Фамилия", без пояснений.

Профиль персоны:
{profile_md}"""

PROMPT_PROFESSION = """Извлеки профессию или статус человека из профиля.

Требования:
- Верни ТОЛЬКО профессию/должность/статус (1-4 слова)
- Если человек студент — укажи "студент" или "студент-[специальность]"
- Если домохозяйка/в декрете — укажи это
- Если предприниматель — укажи "предприниматель" или "ИП"
- Если информации нет — ответь "нет"

Примеры ответов: юрист, инженер-технолог, студент-медик, репетитор, предприниматель, менеджер, домохозяйка, нет

Ответь ТОЛЬКО профессией, без пояснений.

Профиль:
{profile_md}"""


PROMPT_DESCRIPTION = """Прочитай профиль персоны и придумай ёмкую характеристику (3-7 слов).

Требования:
- Характеристика должна отражать ГЛАВНОЕ в этом человеке: его профессию, ключевое занятие или уникальную особенность
- Фокусируйся на том, ЧТО человек делает, КАК использует технологии
- НЕ упоминай случайные детали (какой поисковик использует, что устал от чего-то, что куда-то ездит)
- НЕ используй шаблонные фразы: "экономящий время", "гуглящий всё подряд", "живущий в нейросетях"
- Характеристика должна быть уникальной и запоминающейся

ПЛОХИЕ примеры (шаблонно или неуместно):
- "экономящий время через нейросети" — слишком общее
- "гуглящий всё подряд" — ничего не говорит о персоне
- "с Яндексом вместо Google" — случайная деталь
- "уставший от Урала" — не относится к сути
- "возит детей на хоккей" — слишком конкретно и неважно

ХОРОШИЕ примеры (суть персоны):
- "копирайтер, пишущий тексты через GPT"
- "мама троих с ботами в телеграме"
- "инженер, учащий Python по вечерам"
- "студентка, сдающая сессию через нейросети"
- "предприниматель с 5 бизнесами"

Ответь ТОЛЬКО характеристикой (3-7 слов), без пояснений.

Профиль персоны:
{profile_md}"""


def extract_age(profile_md: str) -> Optional[str]:
    """Извлекает возраст из профиля."""
    patterns = [
        r'\*\*Возраст:\*\*\s*(\d+)\s*(?:лет|год|года)',
        r'Возраст:\s*(\d+)\s*(?:лет|год|года)',
        r'(\d+)\s*(?:лет|год|года)\s*\((?:точно|указан)',
        r'[Мм]не\s+(\d+)\b',
        r'(\d+)\s*(?:лет|год|года)',  # Общий паттерн
        r'(\d+)-летн',  # "20-летний"
    ]
    for pattern in patterns:
        match = re.search(pattern, profile_md)
        if match:
            age = int(match.group(1))
            if 14 <= age <= 80:
                return format_age(age)
    return None


def format_age(age: int) -> str:
    """Форматирует возраст с правильным склонением."""
    if age % 10 == 1 and age != 11:
        return f"{age} год"
    elif age % 10 in [2, 3, 4] and age not in [12, 13, 14]:
        return f"{age} года"
    else:
        return f"{age} лет"


def extract_city(profile_md: str) -> Optional[str]:
    """Извлекает город из профиля."""
    patterns = [
        r'\*\*Место проживания:\*\*\s*([А-Яа-яЁё-]+)',
        r'из\s+([А-Яа-яЁё]+(?:а|ы|и|ска|ова)?)\s*[,\.]',
        r'живу в (?:городе\s+)?([А-Яа-яЁё-]+)',
        r'в\s+([А-Яа-яЁё]+(?:е|ске|ве))\s+(?:всю жизнь|живу)',
    ]
    # Список известных городов для валидации
    known_cities = {
        'Москва', 'Санкт-Петербург', 'Питер', 'Петербург', 'Новосибирск', 
        'Екатеринбург', 'Казань', 'Нижний Новгород', 'Челябинск', 'Самара',
        'Ростов', 'Ростов-на-Дону', 'Уфа', 'Красноярск', 'Воронеж', 'Пермь',
        'Краснодар', 'Сочи', 'Ярославль', 'Тюмень', 'Саратов', 'Тольятти',
        'Ижевск', 'Барнаул', 'Ульяновск', 'Иркутск', 'Хабаровск', 'Владивосток',
        'Махачкала', 'Томск', 'Оренбург', 'Кемерово', 'Новокузнецк', 'Рязань',
        'Астрахань', 'Пенза', 'Липецк', 'Киров', 'Чебоксары', 'Тула', 'Калининград',
        'Сызрань', 'Петропавловск', 'Петропавловск-Камчатский'
    }
    
    for pattern in patterns:
        match = re.search(pattern, profile_md)
        if match:
            city = match.group(1).strip()
            # Нормализация
            if city in known_cities:
                return city
            # Проверяем склонённые формы
            for known in known_cities:
                if known.lower() in city.lower() or city.lower() in known.lower():
                    return known
    return None


def extract_profession_llm(client: LLMClient, profile_md: str, temperature: float = 0.3) -> Optional[str]:
    """Извлекает профессию через LLM."""
    prompt = PROMPT_PROFESSION.format(profile_md=profile_md[:6000])
    
    response = client.chat(
        system="Ты — аналитик, извлекающий структурированные данные из текста.",
        user=prompt,
        temperature=temperature,
    )
    prof = response.strip().strip('"').strip().lower()
    
    # Проверяем ответ
    if prof in ['нет', 'не указано', 'не найдено', 'неизвестно', '-', '']:
        return None
    
    # Капитализируем первую букву
    prof = prof[0].upper() + prof[1:] if prof else None
    
    return prof if prof and len(prof) > 2 else None


def get_all_personas(db_path: Path) -> List[Tuple[str, str, str]]:
    """Получает все персоны из БД: (persona_id, title, profile_md)."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        rows = cur.execute("SELECT persona_id, title, profile_md FROM personas").fetchall()
        return rows
    finally:
        conn.close()


def ensure_output_db(db_path: Path) -> None:
    """Создаёт таблицу для новых заголовков."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS personas_new_titles (
                persona_id TEXT PRIMARY KEY,
                old_title TEXT,
                new_title TEXT,
                generated_name TEXT,
                generated_description TEXT,
                extracted_age TEXT,
                extracted_city TEXT,
                extracted_profession TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_new_title(db_path: Path, persona_id: str, old_title: str, new_title: str,
                   name: str, description: str, age: Optional[str], 
                   city: Optional[str], profession: Optional[str]) -> None:
    """Сохраняет новый заголовок в БД."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO personas_new_titles 
            (persona_id, old_title, new_title, generated_name, generated_description,
             extracted_age, extracted_city, extracted_profession, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (persona_id, old_title, new_title, name, description, 
              age, city, profession, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()


def get_used_names(db_path: Path) -> Tuple[Set[str], Set[str]]:
    """Получает уже использованные имена и фамилии."""
    first_names = set()
    surnames = set()
    if not db_path.exists():
        return first_names, surnames
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        rows = cur.execute("SELECT generated_name FROM personas_new_titles").fetchall()
        for (name,) in rows:
            if name and ' ' in name:
                parts = name.split()
                first_names.add(parts[0].lower())
                surnames.add(parts[-1].lower())
        return first_names, surnames
    except Exception:
        return first_names, surnames
    finally:
        conn.close()


def check_surname_duplicates_description(surname: str, description: str) -> bool:
    """Проверяет, не дублирует ли фамилия описание."""
    surname_lower = surname.lower()
    desc_lower = description.lower()
    
    # Извлекаем корни слов из фамилии (упрощённо)
    # Убираем типичные окончания
    surname_roots = []
    for suffix in ['ов', 'ев', 'ин', 'ич', 'ский', 'ская', 'ная', 'ный', 'ая', 'ий', 'ый']:
        if surname_lower.endswith(suffix) and len(surname_lower) > len(suffix) + 2:
            surname_roots.append(surname_lower[:-len(suffix)])
            break
    if not surname_roots:
        surname_roots = [surname_lower]
    
    # Также добавляем саму фамилию целиком
    surname_roots.append(surname_lower)
    
    # Проверяем вхождение
    for root in surname_roots:
        if len(root) >= 4 and root in desc_lower:
            return True
    
    # Проверяем семантические дубли
    semantic_pairs = [
        (['гугл', 'гугли', 'google'], ['гугл', 'гугли', 'ищет', 'поиск']),
        (['яндекс'], ['яндекс', 'алис']),
        (['алис'], ['алис', 'яндекс']),
        (['минут', 'секунд', 'час', 'время'], ['минут', 'секунд', 'час', 'время', 'экономи']),
        (['вахт'], ['вахт', 'вахтовик']),
        (['телеграм', 'телега'], ['телеграм', 'мессендж']),
        (['делег'], ['делег']),
        (['нейро', 'нейросет'], ['нейро', 'нейросет']),
        (['хокке', 'шайб'], ['хокке', 'хоккеист']),
        (['футбол', 'мяч'], ['футбол']),
    ]
    
    for surname_keywords, desc_keywords in semantic_pairs:
        surname_match = any(kw in surname_lower for kw in surname_keywords)
        desc_match = any(kw in desc_lower for kw in desc_keywords)
        if surname_match and desc_match:
            return True
    
    return False


def generate_name(client: LLMClient, profile_md: str, description: str,
                  used_first_names: Set[str], used_surnames: Set[str], 
                  temperature: float = 0.9, max_retries: int = 7) -> str:
    """Генерирует уникальное имя и фамилию, не дублирующее описание."""
    used_block = ""
    if used_surnames or used_first_names:
        parts = []
        if used_first_names:
            parts.append(f"Имена: {', '.join(sorted(used_first_names))}")
        if used_surnames:
            parts.append(f"Фамилии: {', '.join(sorted(used_surnames))}")
        used_block = f"\nУже использованные (НЕ повторяй): {'; '.join(parts)}\n"
    
    rejected_surnames = []
    rejection_reasons = []
    
    for attempt in range(max_retries):
        # Добавляем отклонённые фамилии в промпт
        extra_block = used_block
        if rejected_surnames:
            reasons_str = "; ".join(rejection_reasons[-3:])  # Последние 3 причины
            extra_block += f"\nОтклонённые фамилии: {', '.join(rejected_surnames[-5:])}. Причины: {reasons_str}. Придумай ДРУГУЮ!\n"
        
        prompt = PROMPT_NAME.format(
            used_names_block=extra_block,
            description=description,
            profile_md=profile_md[:8000]
        )
        
        response = client.chat(
            system="Ты — креативный копирайтер, специализирующийся на создании запоминающихся персонажей.",
            user=prompt,
            temperature=min(1.0, temperature + (attempt * 0.03)),
        )
        name = response.strip().strip('"').strip()
        
        # Проверяем формат
        if ' ' in name and len(name.split()) >= 2:
            parts = name.split()
            first_name = parts[0].lower()
            surname = parts[-1]
            surname_lower = surname.lower()
            
            # Проверяем уникальность имени/фамилии
            if surname_lower in used_surnames or first_name in used_first_names:
                continue
            
            # Проверяем длину фамилии (максимум 15 символов)
            if len(surname) > 15:
                rejected_surnames.append(surname)
                rejection_reasons.append(f"'{surname}' слишком длинная")
                continue
            
            # Проверяем дублирование с описанием
            if check_surname_duplicates_description(surname_lower, description):
                rejected_surnames.append(surname)
                rejection_reasons.append(f"'{surname}' дублирует описание")
                continue
            
            return name
    
    return name


def generate_description(client: LLMClient, profile_md: str, 
                         used_descriptions: Set[str],
                         temperature: float = 0.7, max_retries: int = 3) -> str:
    """Генерирует уникальную краткую характеристику."""
    
    for attempt in range(max_retries):
        # Добавляем использованные описания в промпт
        used_block = ""
        if used_descriptions and attempt > 0:
            similar = [d for d in used_descriptions if len(d) > 10][:10]
            if similar:
                used_block = f"\n\nУже использованные описания (НЕ повторяй похожие): {'; '.join(similar)}\n"
        
        prompt = PROMPT_DESCRIPTION.format(profile_md=profile_md[:8000]) + used_block
        
        response = client.chat(
            system="Ты — опытный UX-исследователь, создающий портреты пользователей.",
            user=prompt,
            temperature=min(1.0, temperature + (attempt * 0.1)),
        )
        desc = response.strip().strip('"').strip()
        
        # Проверяем на дублирование
        desc_lower = desc.lower()
        is_duplicate = False
        for used in used_descriptions:
            # Проверяем похожесть
            used_lower = used.lower()
            # Если больше 50% слов совпадают — дубликат
            desc_words = set(re.findall(r'[а-яё]+', desc_lower))
            used_words = set(re.findall(r'[а-яё]+', used_lower))
            if len(desc_words) > 2 and len(used_words) > 2:
                overlap = len(desc_words & used_words)
                if overlap >= len(desc_words) * 0.6:
                    is_duplicate = True
                    break
        
        if not is_duplicate:
            # Убираем случайные символы разметки
            desc = desc.lstrip('#').strip()
            return desc
    
    # Убираем случайные символы разметки
    desc = desc.lstrip('#').strip()
    return desc  # Возвращаем последний вариант


def build_new_title(name: str, description: str, age: Optional[str], 
                    city: Optional[str], profession: Optional[str]) -> str:
    """Собирает новый заголовок по формату: Имя Фамилия, характеристика, возраст, город, профессия."""
    parts = [name]
    
    if description:
        # Убираем точку в конце, если есть
        parts.append(description.rstrip('.'))
    
    if age:
        parts.append(age)
    
    if city:
        parts.append(city)
    
    # Профессию добавляем только если она не дублируется в характеристике
    if profession:
        desc_lower = description.lower() if description else ""
        prof_lower = profession.lower()
        
        # Убираем "студент-" prefix для сравнения
        prof_clean = re.sub(r'^студент[-\s]*', '', prof_lower)
        
        # Проверяем прямое вхождение
        if prof_lower in desc_lower or prof_clean in desc_lower:
            pass  # Не добавляем — уже есть
        else:
            # Проверяем по словам
            prof_words = set(re.findall(r'[а-яё]+', prof_lower))
            desc_words = set(re.findall(r'[а-яё]+', desc_lower))
            # Если большинство значимых слов профессии есть в описании — не добавляем
            significant_words = {w for w in prof_words if len(w) > 3}
            if significant_words:
                overlap = len(significant_words & desc_words)
                if overlap < len(significant_words) * 0.5:
                    parts.append(profession)
            else:
                parts.append(profession)
    
    return ", ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Перегенерация заголовков персон")
    ap.add_argument("--db-path", type=Path, default=ROOT / "db" / "personas.sqlite",
                    help="Путь к исходной БД")
    ap.add_argument("--out-db", type=Path, default=ROOT / "db" / "personas_new_titles.sqlite",
                    help="Путь к БД с новыми заголовками")
    ap.add_argument("--limit", type=int, default=None,
                    help="Ограничить количество персон (для тестов)")
    ap.add_argument("--temperature", type=float, default=0.85,
                    help="Температура для LLM")
    ap.add_argument("--dry-run", action="store_true",
                    help="Только показать, что будет сделано")
    ap.add_argument("--persona-id", type=str, default=None,
                    help="Обработать только указанную персону")
    args = ap.parse_args()

    if args.dry_run:
        print("[DRY-RUN] Режим просмотра, LLM вызываться не будет")
    else:
        client = LLMClient()
        # Проверка подключения
        print(f"[INFO] Подключение к LLM: provider={client.provider}, model={client.model}")

    ensure_output_db(args.out_db)
    personas = get_all_personas(args.db_path)
    
    if args.persona_id:
        personas = [(pid, t, p) for pid, t, p in personas if pid == args.persona_id]
    
    if args.limit:
        personas = personas[:args.limit]
    
    print(f"[INFO] Всего персон для обработки: {len(personas)}")
    
    used_first_names, used_surnames = get_used_names(args.out_db)
    used_descriptions: Set[str] = set()
    print(f"[INFO] Уже использованных имён: {len(used_first_names)}, фамилий: {len(used_surnames)}")
    
    for i, (persona_id, old_title, profile_md) in enumerate(personas, 1):
        print(f"\n[{i}/{len(personas)}] {persona_id}: {old_title[:60]}...")
        
        # Извлекаем данные из профиля
        age = extract_age(profile_md)
        city = extract_city(profile_md)
        
        if args.dry_run:
            profession = None
        else:
            profession = extract_profession_llm(client, profile_md)
        
        print(f"  Возраст: {age or 'не найден'}")
        print(f"  Город: {city or 'не найден'}")
        print(f"  Профессия: {profession or 'не найдена'}")
        
        if args.dry_run:
            print("  [DRY-RUN] Пропуск генерации LLM")
            continue
        
        # Сначала генерируем описание
        description = generate_description(client, profile_md, used_descriptions, args.temperature)
        used_descriptions.add(description)
        print(f"  Характеристика: {description}")
        
        # Потом генерируем имя с учётом описания (чтобы не дублировать)
        name = generate_name(client, profile_md, description, used_first_names, used_surnames, args.temperature)
        if ' ' in name:
            parts = name.split()
            used_first_names.add(parts[0].lower())
            used_surnames.add(parts[-1].lower())
        print(f"  Имя: {name}")
        
        # Собираем новый заголовок
        new_title = build_new_title(name, description, age, city, profession)
        print(f"  НОВЫЙ ЗАГОЛОВОК: {new_title}")
        
        # Сохраняем
        save_new_title(args.out_db, persona_id, old_title, new_title,
                       name, description, age, city, profession)
        
        print(f"  Сохранено в {args.out_db}")

    print("\n[DONE] Обработка завершена")


if __name__ == "__main__":
    main()
