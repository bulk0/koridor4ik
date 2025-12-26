#!/usr/bin/env python3
"""
talk.py — интерактив для отбора персон из БД и общения (чат/батч).
Ветка chat: независима от пайплайна, использует только БД и локальный LLM‑клиент.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import List, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "personas.sqlite"
RUNS_DIR = ROOT / "runs"
try:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    # В средах с read-only FS (PaaS) просто пропускаем создание каталога на этапе импорта
    pass
CONFIG_DIR = ROOT / "config"
try:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
PRESETS_PATH = CONFIG_DIR / "presets.json"

from .llm_client import LLMClient  # type: ignore


def ensure_env():
    if load_dotenv is not None:
        load_dotenv(dotenv_path=ROOT / ".env", override=True)
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("Не найден API ключ (ANTHROPIC_API_KEY/OPENAI_API_KEY). Добавьте в synthetic_v2/.env.")


def conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"БД не найдена: {DB_PATH}. Сначала подготовьте персоны в пайплайне.")
    return sqlite3.connect(str(DB_PATH))


@dataclass
class Persona:
    persona_id: str
    title: str
    profile_md: str


def db_taxonomy() -> dict[str, set[str]]:
    """
    Возвращает известную таксономию из БД: {category -> set(values)}.
    Используется для валидации ответа LLM в фолбэке.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT DISTINCT category, value FROM persona_tags"
        ).fetchall()
    result: dict[str, set[str]] = {}
    for category, value in rows:
        result.setdefault(str(category), set()).add(str(value))
    return result


def list_all_tags() -> dict[str, list[tuple[str, int]]]:
    with conn() as c:
        rows = c.execute(
            "SELECT category, value, COUNT(DISTINCT persona_id) AS n FROM persona_tags GROUP BY category, value ORDER BY category, n DESC"
        ).fetchall()
    result: dict[str, list[tuple[str, int]]] = {}
    for category, value, n in rows:
        result.setdefault(category, []).append((value, int(n)))
    return result


def search_personas_advanced(
    include_all: dict[str, list[str]] | None = None,
    include_any: dict[str, list[str]] | None = None,
    exclude: dict[str, list[str]] | None = None,
    title_like: Optional[str] = None,
    limit: int = 200,
) -> List[Persona]:
    where_clauses: List[str] = []
    params: List[object] = []
    if title_like:
        where_clauses.append("p.title LIKE ?")
        params.append(f"%{title_like}%")
    for cat, values in (include_all or {}).items():
        for v in values:
            where_clauses.append("EXISTS (SELECT 1 FROM persona_tags t WHERE t.persona_id=p.persona_id AND t.category=? AND t.value=?)")
            params.extend([cat, v])
    any_pairs: List[tuple[str, str]] = []
    for cat, values in (include_any or {}).items():
        for v in values:
            any_pairs.append((cat, v))
    if any_pairs:
        or_terms = []
        for _ in any_pairs:
            or_terms.append("(t_any.category=? AND t_any.value=?)")
        where_clauses.append(
            "EXISTS (SELECT 1 FROM persona_tags t_any WHERE t_any.persona_id=p.persona_id AND (" + " OR ".join(or_terms) + "))"
        )
        for cat, v in any_pairs:
            params.extend([cat, v])
    for cat, values in (exclude or {}).items():
        for v in values:
            where_clauses.append("NOT EXISTS (SELECT 1 FROM persona_tags t_ex WHERE t_ex.persona_id=p.persona_id AND t_ex.category=? AND t_ex.value=?)")
            params.extend([cat, v])
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    sql = f"SELECT p.persona_id, p.title, p.profile_md FROM personas p{where_sql} LIMIT ?"
    params.append(limit)
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [Persona(*row) for row in rows]


def fts_sanitize_query(query: str) -> str:
    """
    Преобразует свободный текст в безопасный для FTS MATCH запрос:
    - убирает пунктуацию,
    - добавляет префиксный поиск (*),
    - соединяет токены оператором OR для повышения полноты.
    """
    import re
    s = (query or "").lower()
    s = re.sub(r"[^0-9A-Za-zА-Яа-яЁё\\s]+", " ", s)
    tokens = [t for t in s.split() if t]
    if not tokens:
        return ""
    prefixed = [t + "*" for t in tokens]
    return " OR ".join(prefixed)


def fts_candidates(query: str, k: int = 30) -> List[Persona]:
    safe = fts_sanitize_query(query)
    if not safe:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT persona_id, title, profile_md FROM personas_fts WHERE personas_fts MATCH ? LIMIT ?",
            (safe, k),
        ).fetchall()
    return [Persona(*row) for row in rows]


def llm_rerank(client: LLMClient, query: str, personas: List[Persona], top_k: int = 10) -> List[Persona]:
    scored: List[tuple[float, Persona]] = []
    system = "Ты — ассистент по поиску релевантных персон. Отвечай только числом от 0.0 до 1.0."
    for p in personas:
        user = (
            "Пользователь описывает целевую персону так:\n"
            f"\"{query.strip()}\"\n\n"
            "Профиль персоны:\n"
            f"{p.title}\n\n"
            f"{p.profile_md[:1200]}\n\n"
            "Верни ТОЛЬКО одно число от 0.0 до 1.0 — оценку релевантности. Без пояснений."
        )
        try:
            txt = client.chat(system=system, user=user, temperature=0.0, max_tokens=16).strip()
            score = float(txt.replace(",", "."))
        except Exception:
            score = 0.0
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:top_k]]


def tags_for_persona(pid: str) -> dict[str, list[str]]:
    with conn() as c:
        rows = c.execute(
            "SELECT category, value FROM persona_tags WHERE persona_id=? ORDER BY category, value",
            (pid,),
        ).fetchall()
    res: dict[str, list[str]] = {}
    for cat, val in rows:
        res.setdefault(cat, []).append(val)
    return res


def format_tags_line(pid: str, max_len: int = 140) -> str:
    """
    Формирует компактную строку тегов: 'cat:val1,val2; cat2:val1; ...'
    Длинные строки обрезаются с многоточием.
    """
    tag_map = tags_for_persona(pid)
    parts: list[str] = []
    for cat, vals in tag_map.items():
        if not vals:
            continue
        joined_vals = ",".join(vals)
        parts.append(f"{cat}:{joined_vals}")
    line = "; ".join(parts)
    if len(line) > max_len:
        return line[: max_len - 1] + "…"
    return line


def export_personas_csv(personas: List[Persona], path: Path) -> None:
    import csv
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["persona_id", "title", "tags", "profile_md"])
        for p in personas:
            tag_map = tags_for_persona(p.persona_id)
            tags_str = "; ".join([f"{cat}:{','.join(vals)}" for cat, vals in tag_map.items()])
            full_text = (p.profile_md or "").replace("\r", "").replace("\n", "\\n")
            w.writerow([p.persona_id, p.title, tags_str, full_text])


def export_personas_md(personas: List[Persona], path: Path, heading: str, criteria: dict | None = None) -> None:
    lines: List[str] = [f"# Экспорт персон", ""]
    if heading:
        lines.append(f"## {heading}")
        lines.append("")
    if criteria:
        import json
        lines.append("```json")
        lines.append(json.dumps(criteria, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    for p in personas:
        tag_map = tags_for_persona(p.persona_id)
        tags_str = "; ".join([f"{cat}:{','.join(vals)}" for cat, vals in tag_map.items()])
        lines.append(f"## {p.title} — `{p.persona_id}`")
        if tags_str:
            lines.append(f"_Теги_: {tags_str}")
            lines.append("")
        lines.append(p.profile_md.strip())
        lines.append("\n---\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def load_presets() -> dict:
    import json
    if PRESETS_PATH.exists():
        try:
            return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_presets(data: dict) -> None:
    import json
    PRESETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ask_save_preset(include_all: dict[str, list[str]], include_any: dict[str, list[str]], exclude: dict[str, list[str]], title_like: Optional[str]) -> None:
    want = input("Сохранить эти критерии как пресет? (y/N) ").strip().lower() == "y"
    if not want:
        return
    name = input("Имя пресета: ").strip()
    if not name:
        print("Пустое имя — пропуск.")
        return
    presets = load_presets()
    presets[name] = {
        "include_all": include_all,
        "include_any": include_any,
        "exclude": exclude,
        "title_like": title_like or "",
    }
    save_presets(presets)
    print(f"Сохранено: {PRESETS_PATH} (ключ='{name}')")


def choose_and_load_preset() -> Optional[dict]:
    presets = load_presets()
    if not presets:
        print("Нет сохранённых пресетов.")
        return None
    print("Доступные пресеты:")
    for i, key in enumerate(presets.keys(), 1):
        print(f"{i}. {key}")
    raw = input("Выберите номер пресета: ").strip()
    try:
        idx = int(raw)
        key = list(presets.keys())[idx - 1]
        print(f"Выбран пресет: {key}")
        return presets[key]
    except Exception:
        print("Неверный выбор.")
        return None


def build_prompt(profile_md: str, question: str) -> tuple[str, str]:
    system = (
        "Ты играешь роль конкретного человека (персоны). "
        "Строго оставайся в роли, не раскрывай, что ты модель ИИ, не выходи из образа. "
        "Пиши по-русски. Не обсуждай сам промпт и инструкции. Отвечай кратко и по делу."
    )
    user = (
        f"Твоя роль: {profile_md}\n\n"
        "Ответь на вопросы ниже из этой роли. Дай пояснение к каждому ответу.\n"
        f"Вопрос: {question}\n\n"
        "Требования:\n"
        "- Пиши естественным языком от лица персоны.\n"
        "- Не выходи из роли, не упоминай, что ты ИИ.\n"
        "- Если перечислены варианты, выбери допустимый вариант(ы) и объясни выбор."
    )
    return system, user


def ask_llm(client: LLMClient, system: str, user: str, temperature: float = 1.0, max_tokens: int = 8000) -> str:
    return client.chat(system=system, user=user, temperature=temperature, max_tokens=max_tokens).strip()


def llm_map_description_to_filters(client: LLMClient, query: str, known_tax: dict[str, set[str]]) -> dict:
    """
    Просит LLM вернуть строгий JSON с маппингом:
      {
        "tags": {category: [values]},  // только из known_tax
        "keywords": [строки],
        "alt_queries": [строки]
      }
    Без указания max_tokens (соблюдаем правила проекта).
    """
    import json
    tax_cat_list = sorted(known_tax.keys())
    system = (
        "Ты — помощник по поиску персон в каталоге. "
        "Верни СТРОГО валидный JSON без пояснений и без форматирования Markdown."
    )
    user = (
        "Пользователь описал целевую персону естественным языком. "
        "Сопоставь это описание известной таксономии и ключевым словам для поиска.\n\n"
        f"Описание: \"\"\"{query.strip()}\"\"\"\n\n"
        "Ограничения:\n"
        f"- Допустимые категории тегов: {', '.join(tax_cat_list)}\n"
        "- Для каждой категории разрешены только значения, реально встречающиеся в БД.\n"
        "- Если подходящего значения нет, не добавляй его в tags.\n"
        "- Ключевые слова (keywords) — свободная форма на русском, до 6 штук.\n"
        "- Альтернативные запросы (alt_queries) — 2–4 перефраза для полнотекстового поиска.\n\n"
        "Формат ответа (строгий JSON):\n"
        "{\n"
        "  \"tags\": {\"<category>\": [\"<value>\", \"<value2>\"]},\n"
        "  \"keywords\": [\"...\"],\n"
        "  \"alt_queries\": [\"...\", \"...\"]\n"
        "}\n"
    )
    txt = client.chat(system=system, user=user, temperature=0.0)
    try:
        data = json.loads((txt or "").strip())
    except Exception:
        return {"tags": {}, "keywords": [], "alt_queries": []}
    # Валидация по известной таксономии
    tags = {}
    raw_tags = data.get("tags") or {}
    for cat, vals in raw_tags.items():
        if cat in known_tax and isinstance(vals, list):
            filtered_vals = [v for v in vals if isinstance(v, str) and v in known_tax[cat]]
            if filtered_vals:
                tags[cat] = filtered_vals
    keywords = [k for k in (data.get("keywords") or []) if isinstance(k, str)]
    alt_queries = [q for q in (data.get("alt_queries") or []) if isinstance(q, str)]
    return {"tags": tags, "keywords": keywords, "alt_queries": alt_queries}


def search_by_description_with_fallback(query: str, client: LLMClient, k_fts: int = 50, top_k: int = 15) -> List[Persona]:
    """
    1) Пробуем FTS кандидатов.
    2) Если пусто — LLM маппинг в (tags/keywords/alt_queries) и поиск по тегам/FTS-синонимам.
    3) Реренжируем найденных через LLM.
    """
    # 1) FTS первичный
    candidates = fts_candidates(query, k=k_fts)
    if not candidates:
        print("FTS не нашёл результатов. Пробуем умный поиск по смыслу (LLM).")
        # Префлайт перед обращением к LLM
        info = client.preflight_check()
        print(f"LLM preflight: model={info.get('model')} supports={info.get('supports')}")
        known = db_taxonomy()
        mapped = llm_map_description_to_filters(client, query, known)
        include_any: dict[str, list[str]] = {}
        for cat, values in (mapped.get("tags") or {}).items():
            include_any[str(cat)] = [str(v) for v in values]
        # Поиск по тегам
        tag_hits = search_personas_advanced(include_all={}, include_any=include_any, exclude={}, title_like=None, limit=500) if include_any else []
        # Поиск по alt_queries с FTS
        alt_hits: list[Persona] = []
        for alt in (mapped.get("alt_queries") or [])[:4]:
            alt_hits.extend(fts_candidates(alt, k=max(10, k_fts // 2)))
        # Объединяем кандидатов
        combined: dict[str, Persona] = {}
        for p in tag_hits + alt_hits:
            combined[p.persona_id] = p
        candidates = list(combined.values())
    if not candidates:
        return []
    # 3) Реренжирование
    ranked = llm_rerank(client, query, candidates, top_k=top_k)
    return ranked or candidates[:top_k]


def scenario_chat_one():
    print("\n— Чат с одной персоной —")
    catalog = list_all_tags()
    if catalog:
        print("Доступные теги (category: value (n)):")
        for cat, vals in catalog.items():
            top = ", ".join([f"{v} ({n})" for v, n in vals[:8]])
            more = "..." if len(vals) > 8 else ""
            print(f"- {cat}: {top}{more}")
    include_all: dict[str, list[str]] = {}
    include_any: dict[str, list[str]] = {}
    exclude: dict[str, list[str]] = {}
    while True:
        line = input("include_all: category=value1,value2 (ENTER — закончить): ").strip()
        if not line:
            break
        if "=" in line:
            cat, values = line.split("=", 1)
            include_all[cat.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    while True:
        line = input("include_any: category=value1,value2 (ENTER — закончить): ").strip()
        if not line:
            break
        if "=" in line:
            cat, values = line.split("=", 1)
            include_any[cat.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    while True:
        line = input("exclude: category=value1,value2 (ENTER — закончить): ").strip()
        if not line:
            break
        if "=" in line:
            cat, values = line.split("=", 1)
            exclude[cat.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    smart = input("Использовать умный ИИ‑поиск по описанию персоны? (y/N) ").strip().lower() == "y"
    personas: List[Persona]
    title_like = None
    if smart:
        query = input("Опишите нужную персону своими словами: ").strip()
        candidates = fts_candidates(query, k=30)
        client = LLMClient()
        ranked = llm_rerank(client, query, candidates, top_k=10)
        allowed_ids = {p.persona_id for p in search_personas_advanced(include_all, include_any, exclude, title_like, limit=1000)}
        personas = [p for p in ranked if p.persona_id in allowed_ids] or ranked
    else:
        personas = search_personas_advanced(include_all, include_any, exclude, title_like, limit=200)
    if not personas:
        print("Ничего не найдено.")
        return
    print(f"Найдено: {len(personas)}")
    for p in personas[:20]:
        print(f"- {p.persona_id} — {p.title}")
    chosen = input("Введите persona_id выбранной персоны: ").strip()
    persona = next((p for p in personas if p.persona_id == chosen), None)
    if not persona:
        print("Персона не найдена.")
        return
    client = LLMClient()
    chat_dir = RUNS_DIR / "chats" / persona.persona_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_md = chat_dir / f"chat_{ts}.md"
    out_md.write_text(f"# Чат с персоной `{persona.persona_id}` — {persona.title}\n\n", encoding="utf-8")
    print("Введите вопросы. Пустая строка — выход.")
    while True:
        q = input("\nВопрос: ").strip()
        if not q:
            break
        system, user = build_prompt(persona.profile_md, q)
        ans = ask_llm(client, system, user, temperature=1.0, max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8000")))
        with out_md.open("a", encoding="utf-8") as f:
            f.write(f"## Вопрос\n\n{q}\n\n## Ответ\n\n{ans}\n\n---\n")
        print("Ответ записан.")
    print(f"Чат сохранён: {out_md}")


def scenario_nl_pick_and_chat():
    print("\n— По описанию — выбрать персону(ы) и чат —")
    print("Внимание: будет обращение к LLM на реальных данных. Сначала выполняется короткая проверка доступности.")
    query = input("Кого ищем? Опишите персону своими словами: ").strip()
    if not query:
        print("Описание пустое — отмена.")
        return
    client = LLMClient()
    # Дешёвая проверка до любых запросов
    info = client.preflight_check()
    print(f"LLM preflight: model={info.get('model')} supports={info.get('supports')}")
    # Поиск кандидатов + фолбэк
    personas = search_by_description_with_fallback(query, client, k_fts=50, top_k=15)
    if not personas:
        print("Ничего не найдено. Попробуйте переформулировать описание.")
        return
    # Если совпадений много — показываем 5 примеров и просим уточнить
    if len(personas) >= 10:
        print(f"Совпадений много: {len(personas)}. Примеры (5):")
        for idx, p in enumerate(personas[:5], 1):
            tags_line = format_tags_line(p.persona_id)
            print(f"{idx}) {p.title}")
            if tags_line:
                print(f"    теги: {tags_line}")
        refine = input("Уточните поиск (город, возраст, дети, поисковик) или ENTER, чтобы продолжить: ").strip()
        if refine:
            query_refined = f"{query}; {refine}"
            personas_refined = search_by_description_with_fallback(query_refined, client, k_fts=60, top_k=15)
            if personas_refined:
                personas = personas_refined
    print(f"Найдено подходящих персон: {len(personas)} (показаны первые 15)")
    for idx, p in enumerate(personas[:15], 1):
        tags_line = format_tags_line(p.persona_id)
        print(f"{idx}) {p.title}")
        if tags_line:
            print(f"    теги: {tags_line}")
    # Мультивыбор произвольного подмножества
    raw_sel = input("Выберите индексы (напр. 1,3-5) или напишите краткое описание (напр. \"выведи маму из Казани\"): ").strip()
    if not raw_sel:
        print("Ничего не выбрано — отмена.")
        return
    chosen: list[Persona] = []
    parts = [s.strip() for s in raw_sel.split(",") if s.strip()]
    by_id = {p.persona_id: p for p in personas}
    by_idx = {str(i): personas[i - 1] for i in range(1, min(15, len(personas)) + 1)}
    # Попытка интерпретации списком индексов/диапазонов
    for token in parts:
        if token in by_idx:
            chosen.append(by_idx[token])
        else:
            # диапазоны вида 2-4
            if "-" in token and all(t.strip().isdigit() for t in token.split("-", 1)):
                try:
                    a, b = token.split("-", 1)
                    ai, bi = int(a), int(b)
                    for i in range(ai, bi + 1):
                        s = str(i)
                        if s in by_idx:
                            chosen.append(by_idx[s])
                except Exception:
                    pass
    # Если ничего не распознали как номера — попробуем свободный текст
    if not chosen and any(ch.isalpha() for ch in raw_sel):
        def _normalize(s: str) -> str:
            import re
            return re.sub(r"[^\\p{L}0-9\\s]+", " ", s, flags=re.UNICODE).lower()
        # Простые стоп-слова команд
        stop = {"выведи", "покажи", "найди", "дай", "хочу", "нужно", "про", "из", "с", "в", "на", "и", "или"}
        qnorm = _normalize(raw_sel)
        tokens = [w for w in qnorm.split() if len(w) >= 3 and w not in stop]
        scores: list[tuple[int, int]] = []  # (score, idx)
        for i, p in enumerate(personas[:15]):
            hay = f"{p.title} {format_tags_line(p.persona_id)}".lower()
            sc = 0
            # грубая метрика вхождений токенов
            for t in tokens:
                if t in hay:
                    sc += 1
            # бонус за вхождение всей фразы без стоп-слов
            if tokens:
                phrase = " ".join(tokens)
                if phrase in hay:
                    sc += 1
            if sc > 0:
                scores.append((sc, i))
        scores.sort(key=lambda x: x[0], reverse=True)
        for _, i in scores[:3]:
            chosen.append(personas[i])
    # Удаляем дубликаты сохраняя порядок
    seen: set[str] = set()
    uniq: list[Persona] = []
    for p in chosen:
        if p.persona_id not in seen:
            uniq.append(p)
            seen.add(p.persona_id)
    if not uniq:
        print("Не удалось распознать выбор — отмена.")
        return
    print(f"Выбрано персон: {len(uniq)}")
    # Подготовка логов
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = RUNS_DIR / "chats" / f"multi_{ts}"
    session_dir.mkdir(parents=True, exist_ok=True)
    summary_md = session_dir / f"chat_summary_{ts}.md"
    summary_md.write_text("# Диалог с несколькими персонами\n\n", encoding="utf-8")
    print("Введите вопросы для выбранных персон. Пустая строка — выход.")
    while True:
        q = input("\nВопрос: ").strip()
        if not q:
            break
        with summary_md.open("a", encoding="utf-8") as fsum:
            fsum.write(f"## Вопрос\n\n{q}\n\n")
        for p in uniq:
            system, user = build_prompt(p.profile_md, q)
            # Без ограничения max_tokens согласно правилам проекта
            ans = client.chat(system=system, user=user, temperature=1.0).strip()
            # Печать и логирование
            print(f"\n— Ответ персоны — {p.title} —")
            print(ans)
            # Файл персоны
            per_dir = RUNS_DIR / "chats" / p.persona_id
            per_dir.mkdir(parents=True, exist_ok=True)
            per_md = per_dir / f"chat_{ts}.md"
            if not per_md.exists():
                per_md.write_text(f"# Чат с персоной `{p.persona_id}` — {p.title}\n\n", encoding="utf-8")
            with per_md.open("a", encoding="utf-8") as fp:
                fp.write(f"## Вопрос\n\n{q}\n\n## Ответ\n\n{ans}\n\n---\n")
            with summary_md.open("a", encoding="utf-8") as fsum:
                fsum.write(f"### Персона: {p.title}\n\n{ans}\n\n---\n")
    print(f"Диалог завершён. Сводный лог: {summary_md}")


def scenario_batch_qa():
    print("\n— Батч‑вопрос по выборке персон —")
    catalog = list_all_tags()
    if catalog:
        print("Доступные теги (category: value (n)):")
        for cat, vals in catalog.items():
            top = ", ".join([f"{v} ({n})" for v, n in vals[:8]])
            more = "..." if len(vals) > 8 else ""
            print(f"- {cat}: {top}{more}")
    include_all: dict[str, list[str]] = {}
    include_any: dict[str, list[str]] = {}
    exclude: dict[str, list[str]] = {}
    while True:
        line = input("include_all: category=value1,value2 (ENTER — закончить): ").strip()
        if not line:
            break
        if "=" in line:
            cat, values = line.split("=", 1)
            include_all[cat.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    while True:
        line = input("include_any: category=value1,value2 (ENTER — закончить): ").strip()
        if not line:
            break
        if "=" in line:
            cat, values = line.split("=", 1)
            include_any[cat.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    while True:
        line = input("exclude: category=value1,value2 (ENTER — закончить): ").strip()
        if not line:
            break
        if "=" in line:
            cat, values = line.split("=", 1)
            exclude[cat.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    smart = input("Добавить умный ИИ‑поиск по описанию (добавит релевантных)? (y/N) ").strip().lower() == "y"
    title_like = None
    personas = search_personas_advanced(include_all, include_any, exclude, title_like, limit=500)
    if smart:
        query = input("Опишите нужную персону своими словами: ").strip()
        candidates = fts_candidates(query, k=50)
        client = LLMClient()
        reranked = llm_rerank(client, query, candidates, top_k=20)
        known = {p.persona_id for p in personas}
        personas.extend([p for p in reranked if p.persona_id not in known])
    if not personas:
        print("Ничего не найдено.")
        return
    print(f"Найдено персон: {len(personas)}")
    # экспорт (опционально)
    do_export = input("Экспортировать выборку перед прогоном? (csv/md/N) ").strip().lower()
    if do_export in ("csv", "md"):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        exports_dir = RUNS_DIR / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        criteria = {"include_all": include_all, "include_any": include_any, "exclude": exclude}
        if do_export == "csv":
            out_csv = exports_dir / f"export_{ts}.csv"
            export_personas_csv(personas, out_csv)
            print(f"CSV экспорт: {out_csv}")
        else:
            out_md = exports_dir / f"export_{ts}.md"
            export_personas_md(personas, out_md, "Выборка персон (перед прогоном)", criteria)
            print(f"MD экспорт: {out_md}")
    # сам вопрос
    question = input("Введите текст вопроса (обязательно): ").strip()
    if not question:
        print("Вопрос пустой — отмена.")
        return
    client = LLMClient()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RUNS_DIR / "qa" / f"v{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"{question[:60].replace(' ', '-')}_{ts}.md"
    out_md = out_dir / out_name
    out_md.write_text(f"# Ответы персон на вопрос\n\n## Вопрос\n\n{question}\n\n---\n", encoding="utf-8")
    for p in personas:
        system, user = build_prompt(p.profile_md, question)
        ans = ask_llm(client, system, user, temperature=1.0, max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8000")))
        with out_md.open("a", encoding="utf-8") as f:
            f.write(f"## Персона: `{p.persona_id}` — {p.title}\n\n{ans}\n\n---\n")
    print(f"Готово: {out_md}")


def main():
    ensure_env()
    while True:
        print("\nTalk (chat):")
        print("1) Чат с одной персоной")
        print("2) Батч‑вопрос по выборке персон")
        print("3) Батч‑вопрос по пресету")
        print("4) По описанию — выбрать персону(ы) и чат")
        print("0) Выход")
        choice = input("Выбор: ").strip()
        if choice == "1":
            scenario_chat_one()
        elif choice == "2":
            scenario_batch_qa()
        elif choice == "3":
            preset = choose_and_load_preset()
            if not preset:
                continue
            include_all = preset.get("include_all") or {}
            include_any = preset.get("include_any") or {}
            exclude = preset.get("exclude") or {}
            title_like = preset.get("title_like") or None
            personas = search_personas_advanced(include_all, include_any, exclude, title_like, limit=500)
            if not personas:
                print("Выборка пуста.")
                continue
            print(f"Найдено персон: {len(personas)}")
            question = input("Введите текст вопроса (обязательно): ").strip()
            if not question:
                print("Вопрос пустой — отмена.")
                continue
            client = LLMClient()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = RUNS_DIR / "qa" / f"v{ts}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_name = f"{question[:60].replace(' ', '-')}_{ts}.md"
            out_md = out_dir / out_name
            out_md.write_text(f"# Ответы персон на вопрос\n\n## Вопрос\n\n{question}\n\n---\n", encoding="utf-8")
            for p in personas:
                system, user = build_prompt(p.profile_md, question)
                ans = ask_llm(client, system, user, temperature=1.0, max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8000")))
                with out_md.open("a", encoding="utf-8") as f:
                    f.write(f"## Персона: `{p.persona_id}` — {p.title}\n\n{ans}\n\n---\n")
            print(f"Готово: {out_md}")
        elif choice == "4":
            scenario_nl_pick_and_chat()
        elif choice == "0":
            break
        else:
            print("Неверный выбор.")


if __name__ == "__main__":
    main()


