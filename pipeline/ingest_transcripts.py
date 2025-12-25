#!/usr/bin/env python3
"""
Проверяет и конвертирует расшифровки интервью в .txt.
Поддержка входа: .txt (как есть), .md (удаляем разметку), .docx (извлечение текста).
"""
import argparse
from pathlib import Path
import re
import sys

def md_to_text(md: str) -> str:
    # примитивное удаление Markdown-разметки
    text = re.sub(r"`{3}[\s\S]*?`{3}", "", md)  # код-блоки
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", text)  # изображения
    text = re.sub(r"\[[^\]]*\]\([^\)]*\)", r"", text)  # ссылки
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    return text

def docx_to_text(path: Path) -> str:
    try:
        import docx  # python-docx
    except Exception as e:
        print("Нужен пакет python-docx (см. requirements.txt).", file=sys.stderr)
        raise
    doc = docx.Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)

def ensure_txt(in_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(in_dir.iterdir()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in (".txt",):
            out_path = out_dir / (p.stem + ".txt")
            out_path.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        elif ext in (".md",):
            md = p.read_text(encoding="utf-8")
            out = md_to_text(md)
            out_path = out_dir / (p.stem + ".txt")
            out_path.write_text(out, encoding="utf-8")
        elif ext in (".docx",):
            out = docx_to_text(p)
            out_path = out_dir / (p.stem + ".txt")
            out_path.write_text(out, encoding="utf-8")
        else:
            # попытаемся прочитать как текст
            try:
                raw = p.read_text(encoding="utf-8")
            except Exception:
                continue
            out_path = out_dir / (p.stem + ".txt")
            out_path.write_text(raw, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(description="Конвертация расшифровок в .txt")
    ap.add_argument("--in-dir", required=True, type=Path, help="Папка с исходниками (md/txt/docx/...)")
    ap.add_argument("--out-dir", required=True, type=Path, help="Куда положить .txt")
    args = ap.parse_args()

    ensure_txt(args.in_dir, args.out_dir)
    print(str(args.out_dir))

if __name__ == "__main__":
    main()


