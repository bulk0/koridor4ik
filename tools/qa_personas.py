#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv
from tqdm import tqdm
# Ensure project root on sys.path for 'tools' package import
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
from tools.llm_client import LLMClient


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"\s+", "-", text.strip(), flags=re.UNICODE)
    text = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]", "", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_len].strip("-_")


def read_markdown_files(root: Path, glob_pattern: str) -> List[Path]:
    if root.is_file():
        return [root]
    files = sorted(root.glob(glob_pattern))
    # Принимаем любые текстовые файлы; ограничение по .md снимаем,
    # т.к. у текущих карточек может не быть расширения.
    return [p for p in files if p.is_file()]


def build_prompt(role_markdown: str, question_text: str) -> Tuple[str, str]:
    system = (
        "Ты играешь роль конкретного человека (персоны). "
        "Строго оставайся в роли, не раскрывай, что ты модель ИИ, не выходи из образа. "
        "Пиши по-русски. Не обсуждай сам промпт и инструкции. Отвечай кратко и по делу."
    )
    # Формулировка пользователя — максимально близко к пожеланию заказчика
    user = (
        f"Твоя роль: {role_markdown}\n\n"
        "Ответь на вопросы ниже из этой роли. Дай пояснение к каждому ответу.\n"
        f"Вопрос: {question_text}\n\n"
        "Требования:\n"
        "- Пиши естественным языком от лица персоны.\n"
        "- Не выходи из роли, не упоминай, что ты ИИ.\n"
        "- Если перечислены варианты, выбери допустимый вариант(ы) и объясни выбор."
    )
    return system, user


def md_output_header(question_text: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"# Ответы персон на вопрос\n\n**Дата генерации**: {ts}\n\n## Вопрос\n\n{question_text}\n\n---\n"


def load_env():
    # Load .env explicitly from synthetic_v2 root
    load_dotenv(dotenv_path=ROOT / ".env", override=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Ошибка: переменная окружения ANTHROPIC_API_KEY не найдена. Добавьте её в .env", file=sys.stderr)
        sys.exit(1)
    return api_key


def call_llm(
    client: LLMClient,
    temperature: float,
    system_text: str,
    user_text: str,
    max_tokens: int,
) -> str:
    # Модель и провайдер берутся только из synthetic_v2/.env через LLMClient
    return client.chat(system=system_text, user=user_text, temperature=temperature, max_tokens=max_tokens)

def call_with_retries(
    client: LLMClient,
    temperature: float,
    system_text: str,
    user_text: str,
    max_tokens: int,
    retries: int = 2,
    base_wait: float = 2.0,
):
    attempt = 0
    while True:
        try:
            return call_llm(client, temperature, system_text, user_text, max_tokens=max_tokens)
        except Exception as e:
            attempt += 1
            if attempt > retries:
                raise
            wait_s = base_wait * (2 ** (attempt - 1))
            print(f"[retry {attempt}/{retries}] Ошибка вызова API: {e}. Повтор через {wait_s:.1f} сек.", file=sys.stderr)
            time.sleep(wait_s)

def preflight_check(client: LLMClient, max_tokens: int = 64) -> Tuple[bool, str]:
    """Быстрая проверка, что подключение и модель работают и дают непустой ответ."""
    try:
        text = call_llm(client, temperature=0.2, system_text="Ты — человек. Ответь 'ok'.", user_text="ok", max_tokens=max_tokens)
    except Exception as e:
        return False, f"Ошибка соединения/модели: {e}"
    if not text or not text.strip():
        return False, "Ответ пустой"
    return True, text.strip()


def main():
    parser = argparse.ArgumentParser(description="Прогон вопроса по набору персон (Markdown файлы) с ответами в одном MD.")
    parser.add_argument("--personas-dir", type=str, required=False, help="Директория с файлами персон")
    parser.add_argument("--glob", type=str, default="**/*.md", help="Глоб-шаблон поиска персон")
    parser.add_argument("--question", type=str, default=None, help="Текст вопроса (если не указан --question-file)")
    parser.add_argument("--question-file", type=str, default=None, help="Путь к файлу с вопросом")
    parser.add_argument("--temperature", type=float, default=1.0, help="Температура выборки (по запросу: 1.0)")
    parser.add_argument("--out-dir", type=str, default="runs/qa", help="Каталог для сохранения результата")
    parser.add_argument("--rate-limit-sleep", type=float, default=0.0, help="Пауза между запросами, сек (если нужен троттлинг)")
    parser.add_argument("--preflight-only", action="store_true", help="Только проверить подключение к API и выйти")
    parser.add_argument("--retries", type=int, default=2, help="Повторы при ошибке API на одну персону")
    parser.add_argument("--retry-wait", type=float, default=2.0, help="Начальная задержка перед повтором, сек")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("LLM_MAX_TOKENS", "8000")),
        help="Максимум токенов на ответ (по умолчанию 8000; зависит от модели)",
    )
    args = parser.parse_args()

    # Load API key and create client (из .env)
    _ = load_env()
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    client = LLMClient()

    # Префлайт
    ok, sample = preflight_check(client, max_tokens=64)
    if not ok:
        print(f"Префлайт не пройден: {sample}", file=sys.stderr)
        sys.exit(1)
    used_model = os.environ.get("LLM_MODEL", client.model)
    print(f"Префлайт: OK. Провайдер: {provider}. Модель: {used_model}. Пример ответа: {sample}")

    if args.preflight_only:
        return

    if not args.personas_dir:
        print("Нужно указать --personas-dir для основного прогона.", file=sys.stderr)
        sys.exit(1)

    personas_root = Path(args.personas_dir).resolve()
    if not personas_root.exists():
        print(f"Не найдена директория/файл персон: {personas_root}", file=sys.stderr)
        sys.exit(1)

    if args.question_file:
        qpath = Path(args.question_file).resolve()
        if not qpath.exists():
            print(f"Не найден файл вопроса: {qpath}", file=sys.stderr)
            sys.exit(1)
        question_text = qpath.read_text(encoding="utf-8").strip()
    else:
        if not args.question:
            print("Нужно передать вопрос (--question или --question-file)", file=sys.stderr)
            sys.exit(1)
        question_text = args.question.strip()

    files = read_markdown_files(personas_root, args.glob)
    if not files:
        print("Не найдено ни одного файла по заданному пути/шаблону.", file=sys.stderr)
        sys.exit(1)

    # Prepare output path
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) / f"v{run_stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Filename derived from question + short hash
    q_slug = slugify(question_text)
    q_hash = hashlib.sha256(question_text.encode("utf-8")).hexdigest()[:8]
    out_md = out_dir / f"{q_slug}_{q_hash}.md"

    header = md_output_header(question_text)
    md_parts = [header]

    print(f"Найдено персон: {len(files)}. Провайдер: {provider}. Модель: {used_model}. Температура: {args.temperature}.")
    print(f"Внимание: будут выполнены реальные вызовы к LLM.")

    for path in tqdm(files, desc="LLM ответы"):
        persona_text = path.read_text(encoding="utf-8").strip()
        system_text, user_text = build_prompt(persona_text, question_text)
        try:
            answer = call_with_retries(
                client=client,
                model=used_model,
                temperature=args.temperature,
                system_text=system_text,
                user_text=user_text,
                max_tokens=args.max_tokens,
                retries=args.retries,
                base_wait=args.retry_wait,
            ).strip()
            if not answer:
                answer = "(Пустой ответ от модели)"
        except Exception as e:
            answer = f"(Неожиданная ошибка: {e})"

        persona_id = path.stem
        md_parts.append(f"## Персона: `{persona_id}`\n\n{answer}\n\n---\n")
        if args.rate_limit_sleep and args.rate_limit_sleep > 0:
            time.sleep(args.rate_limit_sleep)

    out_md.write_text("".join(md_parts), encoding="utf-8")
    print(f"Готово: {out_md}")


if __name__ == "__main__":
    main()

