#!/usr/bin/env python3
"""
convert_tags.py — конвертирует исходный список тегов (XLSX/CSV/YAML)
в нормализованный классификатор taxonomy.yaml для автотеггера.

Ожидаемые поля входа (строка = один тег):
- key (обяз.)              : системное имя тега (snake_case)
- type (обяз.)             : enum | multienum | string | number | bool
- enum_values (опц.)       : допустимые значения для (multi)enum; строка (разделители: ',' или '|') или список
- allow_multiple (опц.)    : true/false (по умолчанию: для multienum=true, иначе false)
- title (опц.)             : человеко-читаемое название
- description (опц.)       : краткое пояснение
- synonyms (опц.)          : JSON-объект {сырой_вариант: каноническое_значение} для маппинга значений
- prompt_hint (опц.)       : подсказка для LLM, как искать этот тег

Примеры запуска:
  python synthetic_v2/tools/convert_tags.py \
    --input /Users/jbaukova/Documents/Projects/Synthetic_v1/synthetic_v2/tags.xlsx \
    --out /Users/jbaukova/Documents/Projects/Synthetic_v1/synthetic_v2/inputs/tags/taxonomy.yaml
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml  # type: ignore

try:
    import pandas as pd  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Требуется пакет pandas. Установите зависимости из requirements.txt"
    ) from e


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    # Разделители: запятая или вертикальная черта
    parts = [p.strip() for p in s.replace("|", ",").split(",")]
    return [p for p in parts if p]


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "t"):
        return True
    if s in ("0", "false", "no", "n", "f"):
        return False
    return default


def _parse_synonyms(value: Any) -> Dict[str, str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    if isinstance(value, dict):
        return {str(k).strip(): str(v).strip() for k, v in value.items() if str(k).strip() and str(v).strip()}
    # Попробуем как JSON
    s = str(value).strip()
    if not s:
        return {}
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return {str(k).strip(): str(v).strip() for k, v in obj.items() if str(k).strip() and str(v).strip()}
    except Exception:
        pass
    return {}


def load_raw_tags(path: Path, *, sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        dfs = pd.read_excel(path, sheet_name=sheet or None)
        if isinstance(dfs, dict):
            # Если не указан лист — берем первый
            df = next(iter(dfs.values()))
        else:
            df = dfs
        df.columns = [str(c).strip() for c in df.columns]
        # Поддержка табличного формата вида:
        #  col0='Теги', col1..N = значения (enum)
        if "Теги" in df.columns and "type" not in df.columns and "key" not in df.columns:
            records: List[Dict[str, Any]] = []
            value_cols = [c for c in df.columns if c != "Теги"]
            for _, row in df.iterrows():
                key = str(row.get("Теги", "")).strip()
                if not key:
                    continue
                vals: List[str] = []
                for c in value_cols:
                    v = row.get(c, None)
                    if pd.isna(v):
                        continue
                    s = str(v).strip()
                    if s:
                        vals.append(s)
                # Эвристика типов: если только true/false → bool, иначе enum
                lowered = {s.lower() for s in vals}
                if lowered and lowered.issubset({"true", "false"}):
                    records.append({"key": key, "type": "bool"})
                else:
                    records.append({"key": key, "type": "enum", "enum_values": vals})
            return records
        # Обычная форма (строка=тег)
        records = df.to_dict(orient="records")
        return records
    if suffix == ".csv":
        df = pd.read_csv(path)
        df.columns = [str(c).strip() for c in df.columns]
        return df.to_dict(orient="records")
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("YAML должен содержать список объектов (строка=тег)")
        return [dict(x) for x in data]
    raise ValueError(f"Неподдержанный формат: {suffix}")


def normalize_tags(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        key = str(row.get("key", "")).strip()
        typ = str(row.get("type", "")).strip().lower()
        if not key or not typ:
            # Пропускаем пустые строки, но не падаем
            continue
        if typ not in ("enum", "multienum", "string", "number", "bool"):
            raise ValueError(f"Строка {idx}: неизвестный type={typ} (ожидается enum|multienum|string|number|bool)")

        enum_values = _as_list(row.get("enum_values"))
        allow_multiple = _parse_bool(row.get("allow_multiple"),
                                     default=(typ == "multienum"))
        title = str(row.get("title", "")).strip() or key
        description = str(row.get("description", "")).strip() or ""
        synonyms = _parse_synonyms(row.get("synonyms"))
        prompt_hint = str(row.get("prompt_hint", "")).strip() or ""

        item: Dict[str, Any] = {
            "key": key,
            "title": title,
            "type": typ,
            "allow_multiple": bool(allow_multiple),
        }
        if description:
            item["description"] = description
        if prompt_hint:
            item["prompt_hint"] = prompt_hint
        if typ in ("enum", "multienum"):
            item["enum"] = enum_values
        if synonyms:
            item["synonyms"] = synonyms
        normalized.append(item)
    return normalized


def write_taxonomy(out_path: Path, tags: List[Dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "version": "v1",
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "tags": tags,
    }
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
    out_path.write_text(text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Конвертер: tags.xlsx/csv/yaml → taxonomy.yaml")
    ap.add_argument("--input", type=Path, required=True, help="Путь к исходнику (XLSX/CSV/YAML)")
    ap.add_argument("--sheet", type=str, default=None, help="Имя листа (для XLSX, опционально)")
    ap.add_argument("--out", type=Path, required=True, help="Путь для taxonomy.yaml")
    args = ap.parse_args()

    rows = load_raw_tags(args.input, sheet=args.sheet)
    tags = normalize_tags(rows)
    if not tags:
        raise SystemExit("Не найдено ни одного валидного тега в входном файле.")
    write_taxonomy(args.out, tags)
    print(f"taxonomy_written={args.out}")


if __name__ == "__main__":
    main()


