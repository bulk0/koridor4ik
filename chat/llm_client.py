#!/usr/bin/env python3
import os
from typing import Optional, Dict, Any, Tuple

from pathlib import Path
try:
    from dotenv import load_dotenv  # type: ignore
    # Загружаем .env из корня репозитория, если есть
    ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(dotenv_path=ROOT / ".env")
except Exception:
    pass

class LLMClient:
    """
    Лёгкая обёртка над LLM провайдером.
    Поддержан openai и anthropic через переменные окружения:
      - OPENAI_API_KEY
      - OPENAI_BASE_URL (опц.)
      - LLM_MODEL (имя модели)
      - ANTHROPIC_API_KEY
    """

    def __init__(self) -> None:
        self.provider = os.getenv("LLM_PROVIDER", "openai").lower()
        self.model = os.getenv("LLM_MODEL", "gpt-5-mini")
        self.base_url = os.getenv("OPENAI_BASE_URL")
        self.api_key = os.getenv("OPENAI_API_KEY")
        self._client = None
        self._openai_mod = None

        if self.provider == "openai":
            try:
                from openai import OpenAI  # type: ignore
                import openai  # type: ignore
            except Exception as e:
                raise RuntimeError(
                    "Пакет openai не установлен. Установите 'openai' или включите DRY_RUN."
                ) from e
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            self._openai_mod = openai
        elif self.provider == "anthropic":
            try:
                import anthropic  # type: ignore
                import httpx  # type: ignore
            except Exception as e:
                raise RuntimeError(
                    "Пакет anthropic не установлен. Установите 'anthropic' или задайте LLM_PROVIDER=openai."
                ) from e
            # Для Anthropic ключ берём из ANTHROPIC_API_KEY
            anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
            if not anthropic_api_key:
                raise RuntimeError("Не задан ANTHROPIC_API_KEY для LLM_PROVIDER=anthropic")
            # Опционально: отключить проверку SSL (на свой риск) через LLM_INSECURE_SKIP_VERIFY=true
            insecure_skip_verify = os.getenv("LLM_INSECURE_SKIP_VERIFY", "").lower() in ("1", "true", "yes")
            timeout_s = float(os.getenv("LLM_HTTP_TIMEOUT", "600"))
            try:
                http_client = httpx.Client(verify=not insecure_skip_verify, timeout=timeout_s)
                self._client = anthropic.Anthropic(api_key=anthropic_api_key, http_client=http_client)
            except Exception:
                # Фоллбек на стандартный клиент
                self._client = anthropic.Anthropic(api_key=anthropic_api_key)
        else:
            raise NotImplementedError(f"LLM_PROVIDER={self.provider} не поддержан")

    def chat_with_meta(self, system: str, user: str, *, temperature: float = 0.25, max_tokens: Optional[int] = None) -> Tuple[str, Optional[str]]:
        """
        Выполняет вызов и возвращает (text, причина_остановки).
        Для OpenAI причина_остановки = finish_reason, для Anthropic = stop_reason.
        """
        if self.provider == "openai":
            from openai import OpenAI  # type: ignore
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": float(temperature),
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = int(max_tokens)  # type: ignore[assignment]
            resp = self._client.chat(completions=None) if False else self._client.chat.completions.create(**kwargs)  # type: ignore
            choice = resp.data[0] if hasattr(resp, "data") else resp.choices[0]
            text = (getattr(choice.message, "content", "") or "").strip()
            reason = getattr(choice, "finish_reason", None)
            return text, str(reason) if reason is not None else None
        if self.provider == "anthropic":
            use_max_tokens = max_tokens if (isinstance(max_tokens, int) and max_tokens > 0) else 4000
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=use_max_tokens,
            )
            parts = []
            for part in getattr(resp, "content", []) or []:
                txt = getattr(part, "text", None)
                if isinstance(txt, str):
                    parts.append(txt)
            text = "\n".join(parts).strip()
            reason = getattr(resp, "stop_reason", None)
            return text, str(reason) if reason is not None else None
        raise NotImplementedError("Неподдержанный провайдер")

    def chat(self, system: str, user: str, temperature: float = 0.25, max_tokens: Optional[int] = None) -> str:
        """
        Возвращает текст первого сообщения ассистента.
        """
        txt, _ = self.chat_with_meta(system, user, temperature=temperature, max_tokens=max_tokens)
        return txt

    def preflight_check(self) -> Dict[str, Any]:
        """
        Дешёвые пробные вызовы, чтобы понять, какие параметры модель принимает.
        Возвращает: {'model': ..., 'supports': {'temperature': bool, 'max_tokens': bool}}
        """
        supports = {"temperature": True, "max_tokens": True}
        system = "Ты — утилита. Верни строго JSON {} без пояснений."
        user = "{}"
        if self.provider == "openai":
            try:
                self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.5,
                )
            except Exception as e:
                try:
                    if isinstance(e, self._openai_mod.BadRequestError):  # type: ignore
                        supports["temperature"] = False
                except Exception:
                    pass
            try:
                self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=1,
                )
            except Exception as e:
                try:
                    if isinstance(e, self._openai_mod.BadRequestError):  # type: ignore
                        supports["max_tokens"] = False
                except Exception:
                    pass
        elif self.provider == "anthropic":
            try:
                self._client.messages.create(
                    model=self.model,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    temperature=0.5,
                    max_tokens=1,
                )
            except Exception:
                # Не все версии SDK детализируют причину — считаем поддержкой по умолчанию
                pass
        return {"model": self.model, "supports": supports}


