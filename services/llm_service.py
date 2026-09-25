"""
Диалоговый движок на основе Yandex GPT с Function Calling.
Управляет историей диалога и интеграцией с бизнес-сервисами.
"""
from __future__ import annotations

import asyncio
import json
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from prompts.function_schemas import TOOLS
from prompts.system_prompt import get_system_prompt
from services.booking_service import BookingService
from utils.logger import get_logger

logger = get_logger(__name__)

COMPLEX_INTENTS = {
    "transfer",
    "multi_service",
    "conflict",
}


@dataclass
class DialogSession:
    """Сессия диалога пользователя."""
    user_id: int
    messages: list[dict] = field(default_factory=list)
    last_activity: float = field(default_factory=time_module.time)
    is_new_client: bool = True
    booking_confirmed: bool = False

    def is_expired(self, ttl_minutes: int) -> bool:
        elapsed = (time_module.time() - self.last_activity) / 60
        return elapsed > ttl_minutes

    def touch(self) -> None:
        self.last_activity = time_module.time()

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self.touch()

    def trim_history(self, max_messages: int) -> None:
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]


@dataclass
class LLMResponse:
    """Ответ от LLM с метаданными."""
    response_text: str
    intent: Optional[str] = None
    action: Optional[str] = None
    extracted_data: Optional[dict] = None
    used_model: Optional[str] = None
    booking_created: bool = False


class YandexGPTClient:
    """Клиент для взаимодействия с Yandex GPT через REST API."""
    
    YANDEX_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    
    def __init__(self):
        if not settings.yandex_api_key:
            raise ValueError("YANDEX_API_KEY не задан в .env")
        if not settings.yandex_folder_id:
            raise ValueError("YANDEX_FOLDER_ID не задан в .env")
    
    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 500,
    ) -> dict:
        """Отправить запрос к Yandex GPT и получить ответ."""
        
        headers = {
            "Authorization": f"Api-Key {settings.yandex_api_key}",
            "Content-Type": "application/json",
        }
        
        # Преобразуем messages в формат Yandex
        system_message = None
        conversation_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                conversation_messages.append({
                    "role": msg["role"],
                    "text": msg["content"],
                })
        
        payload = {
            "modelUri": f"gpt://{settings.yandex_folder_id}/yandexgpt/latest",
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens),
            },
            "messages": conversation_messages,
        }
        
        if system_message:
            payload["systemPrompt"] = {
                "text": system_message,
            }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.YANDEX_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(
                            f"Yandex GPT API ошибка {response.status}: {error_text}"
                        )
                    
                    result = await response.json()
                    
                    # Парсим ответ
                    if "result" in result and "alternatives" in result["result"]:
                        alternatives = result["result"]["alternatives"]
                        if alternatives:
                            message_text = alternatives[0].get("message", {}).get("text", "")
                            return {
                                "content": message_text,
                                "finish_reason": "stop",
                            }
                    
                    raise Exception("Неожиданный формат ответа от Yandex GPT")
        
        except asyncio.TimeoutError:
            raise Exception("Timeout при запросе к Yandex GPT")
        except aiohttp.ClientError as e:
            raise Exception(f"Ошибка сети при запросе к Yandex GPT: {e}")


class LLMService:
    """
    Диалоговый движок с управлением историей.
    Поддерживает Yandex GPT для обработки сообщений.
    """

    def __init__(
        self,
        booking_service: BookingService,
        client: Optional[YandexGPTClient] = None,
    ) -> None:
        self._booking = booking_service
        self._client = client or YandexGPTClient()
        self._sessions: dict[int, DialogSession] = {}
        self._services_prompt_cache: Optional[str] = None

    def _get_or_create_session(self, user_id: int) -> DialogSession:
        session = self._sessions.get(user_id)

        if session is None or session.is_expired(settings.dialog_ttl_minutes):
            if session and session.is_expired(settings.dialog_ttl_minutes):
                logger.info("llm_service.session_expired", user_id=user_id)
            session = DialogSession(user_id=user_id)
            self._sessions[user_id] = session

        return session

    def reset_session(self, user_id: int) -> None:
        if user_id in self._sessions:
            del self._sessions[user_id]
            logger.info("llm_service.session_reset", user_id=user_id)

    async def _get_system_prompt(self) -> str:
        try:
            services = await self._booking._calendar.get_services()
            services_text = (
                self._booking._calendar.format_services_for_prompt(services)
            )
            return get_system_prompt(services_text)
        except Exception as e:
            logger.warning(
                "llm_service.services_prompt_error", error=str(e)
            )
            return get_system_prompt(None)

    def _select_model(self, user_text: str, history_length: int) -> str:
        """Выбор модели (для совместимости, Yandex GPT используется одна)."""
        return "yandexgpt"

    async def _extract_tool_calls_from_response(
        self,
        response_text: str,
        user_id: int,
    ) -> list[dict]:
        """
        Пытаемся распарсить вызовы инструментов из текста ответа.
        Yandex GPT в базовом режиме не поддерживает function calling,
        поэтому используем простую эвристику.
        """
        # Пока возвращаем пустой список - основной сценарий без tool calls
        # В будущем можно добавить парсинг JSON из текста ответа
        return []

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def process_message(
        self, user_id: int, user_text: str
    ) -> LLMResponse:
        """Обработка сообщения пользователя через Yandex GPT."""
        session = self._get_or_create_session(user_id)
        session.add_message("user", user_text)
        session.trim_history(settings.dialog_max_messages)
        session.booking_confirmed = False

        system_prompt = await self._get_system_prompt()
        model = self._select_model(user_text, len(session.messages))

        logger.debug(
            "llm_service.request",
            user_id=user_id,
            model=model,
            history_length=len(session.messages),
        )

        messages = [
            {"role": "system", "content": system_prompt},
        ] + session.messages

        try:
            start_time = time_module.monotonic()

            response = await self._client.complete(
                messages=messages,
                temperature=0.7,
                max_tokens=500,
            )

            elapsed = time_module.monotonic() - start_time

            logger.debug(
                "llm_service.response",
                model=model,
                elapsed_sec=round(elapsed, 2),
                finish_reason=response.get("finish_reason", "unknown"),
            )

            final_text = response.get("content", "").strip()

            if not final_text:
                final_text = "Простите, не смогла получить ответ. Попробуйте ещё раз."

            session.add_message("assistant", final_text)

            return LLMResponse(
                response_text=final_text,
                used_model=model,
                booking_created=False,
            )

        except Exception as e:
            logger.error(
                "llm_service.error",
                error=str(e),
                exc_info=True,
                user_id=user_id,
            )
            raise

    async def _execute_tool_calls(
        self,
        tool_calls: list,
        user_id: int,
    ) -> tuple[list[dict], bool]:
        """
        Выполнение вызовов инструментов.
        Для Yandex GPT в базовом режиме это не используется,
        но оставляем для совместимости.
        """
        return [], False

    async def _execute_single_tool(
        self, tool_call: Any, user_id: int
    ) -> dict:
        """Выполнение одного вызова инструмента (заглушка)."""
        return {"error": "Tool calls не поддерживаются в базовом режиме Yandex GPT"}
