"""
Диалоговый движок на основе GPT-4o с Function Calling.
Управляет историей диалога и интеграцией с бизнес-сервисами.
"""
from __future__ import annotations

import json
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage
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
    # НОВОЕ: отслеживаем была ли реальная запись
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
    booking_created: bool = False  # НОВОЕ: была ли реальная запись


class LLMService:
    """
    Диалоговый движок с управлением историей и Function Calling.
    """

    def __init__(
        self,
        booking_service: BookingService,
        client: Optional[AsyncOpenAI] = None,
    ) -> None:
        self._booking = booking_service
        self._client = client or AsyncOpenAI(api_key=settings.openai_api_key)
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
        complex_keywords = [
            "перенести", "перенос", "изменить", "поменять",
            "два", "две", "несколько", "и ", "также",
            "перенесите", "сдвиньте",
        ]
        text_lower = user_text.lower()

        if (
            any(kw in text_lower for kw in complex_keywords)
            or history_length > 10
        ):
            return settings.llm_model_complex

        return settings.llm_model

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def process_message(
        self, user_id: int, user_text: str
    ) -> LLMResponse:
        """Обработка сообщения пользователя через LLM."""
        session = self._get_or_create_session(user_id)
        session.add_message("user", user_text)
        session.trim_history(settings.dialog_max_messages)
        # Сброс флага записи для нового запроса
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

        max_tool_calls = 5
        tool_call_count = 0
        booking_was_created = False  # Отслеживаем реальную запись

        while tool_call_count < max_tool_calls:
            start_time = time_module.monotonic()

            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=500,
            )

            elapsed = time_module.monotonic() - start_time
            logger.debug(
                "llm_service.response",
                model=model,
                elapsed_sec=round(elapsed, 2),
                finish_reason=response.choices[0].finish_reason,
            )

            message: ChatCompletionMessage = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                final_text = message.content or (
                    "Простите, не могу ответить. Попробуйте ещё раз."
                )

                # ═══════════════════════════════════════════════════════
                # ЗАЩИТА: Если LLM говорит "записаны/подтверждено"
                # но create_booking НЕ вызывался или ПРОВАЛИЛСЯ
                # → предупреждаем
                # ═══════════════════════════════════════════════════════
                booking_keywords = [
                    "записаны", "записана", "запись создана",
                    "подтверждена", "вы записаны", "запись подтверждена",
                    "ждём вас", "ждем вас",
                ]
                text_lower = final_text.lower()
                looks_like_confirmation = any(
                    kw in text_lower for kw in booking_keywords
                )

                if looks_like_confirmation and not booking_was_created:
                    logger.error(
                        "llm_service.PHANTOM_BOOKING_DETECTED",
                        user_id=user_id,
                        response_text=final_text,
                        booking_was_created=booking_was_created,
                    )
                    # Заменяем ответ — не даём ложное подтверждение
                    final_text = (
                        "Извините, произошла ошибка при создании записи. "
                        "Давайте попробуем ещё раз. "
                        "На какую дату и время вас записать?"
                    )

                session.add_message("assistant", final_text)

                return LLMResponse(
                    response_text=final_text,
                    used_model=model,
                    booking_created=booking_was_created,
                )

            # Обрабатываем вызовы инструментов
            tool_results, any_booking_success = (
                await self._execute_tool_calls(
                    message.tool_calls, user_id
                )
            )

            if any_booking_success:
                booking_was_created = True
                session.booking_confirmed = True

            for result in tool_results:
                messages.append(result)

            tool_call_count += len(message.tool_calls)

        logger.warning(
            "llm_service.max_tool_calls_reached",
            user_id=user_id,
            count=tool_call_count,
        )
        return LLMResponse(
            response_text=(
                "Извините, произошла ошибка обработки. "
                "Попробуйте переформулировать запрос."
            ),
            used_model=model,
        )

    async def _execute_tool_calls(
            self,
            tool_calls: list,
            user_id: int,
    ) -> tuple[list[dict], bool]:
        """
        Выполнение вызовов инструментов.

        Returns:
            (messages, any_booking_success)
        """
        import asyncio

        tasks = [
            self._execute_single_tool(tc, user_id)
            for tc in tool_calls
        ]
        results_data = await asyncio.gather(*tasks, return_exceptions=True)

        messages = []
        any_booking_success = False

        for tc, result in zip(tool_calls, results_data):
            if isinstance(result, Exception):
                logger.error(
                    "llm_service.tool_error",
                    tool=tc.function.name,
                    error=str(result),
                    exc_info=True,
                )
                result_str = json.dumps(
                    {"error": f"Ошибка выполнения: {str(result)}"},
                    ensure_ascii=False,
                )
            else:
                # Проверяем успех записи / переноса
                is_booking_success = (
                        tc.function.name in ("create_booking", "transfer_booking")
                        and isinstance(result, dict)
                        and result.get("success") is True
                )

                if is_booking_success:
                    any_booking_success = True
                    logger.info(
                        "llm_service.booking_SUCCESS_confirmed",
                        user_id=user_id,
                        tool=tc.function.name,
                    )

                # Сериализуем результат в строку
                result_str = json.dumps(
                    result, ensure_ascii=False, default=str
                )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

        return messages, any_booking_success

    async def _execute_single_tool(
        self, tool_call: Any, user_id: int
    ) -> dict:
        """Выполнение одного вызова инструмента."""
        func_name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            logger.error(
                "llm_service.tool_args_error",
                tool=func_name,
                error=str(e),
                arguments=tool_call.function.arguments,
            )
            args = {}

        logger.info(
            "llm_service.tool_call",
            tool=func_name,
            user_id=user_id,
            args=args,
        )

        # ─── check_available_slots ────────────────────────────────────
        if func_name == "check_available_slots":
            result = await self._booking.check_available_slots(
                date_str=args.get("date", ""),
                service_name=args.get("service"),
                preferred_time_str=args.get("preferred_time"),
            )
            logger.debug(
                "llm_service.check_slots_result",
                user_id=user_id,
                slots=result.get("available_slots", []),
            )
            return result

        # ─── create_booking ───────────────────────────────────────────
        elif func_name == "create_booking":
            # ПРОВЕРКА 1: Имя обязательно
            client_name = args.get("client_name", "").strip()
            if not client_name:
                logger.warning(
                    "llm_service.create_booking_missing_name",
                    user_id=user_id,
                )
                return {
                    "success": False,
                    "error": "ТРЕБУЕТСЯ_ИМЯ",
                    "message": (
                        "Для записи мне нужно ваше имя. "
                        "Как к вам обращаться?"
                    ),
                    "booking": None,
                    "alternative_slots": [],
                }

            # ПРОВЕРКА 2: Телефон для новых клиентов
            client_phone = args.get("client_phone", "").strip() or None
            if not client_phone:
                try:
                    client = (
                        await self._booking._calendar.get_or_create_client(
                            user_id, client_name, None
                        )
                    )
                    if client.visit_count == 0 and not client.phone:
                        logger.info(
                            "llm_service.create_booking_missing_phone",
                            user_id=user_id,
                            client_name=client_name,
                        )
                        return {
                            "success": False,
                            "error": "ТРЕБУЕТСЯ_ТЕЛЕФОН",
                            "message": (
                                f"{client_name}, для первой записи "
                                f"мне нужен ваш номер телефона. "
                                f"Подскажите, пожалуйста?"
                            ),
                            "booking": None,
                            "alternative_slots": [],
                        }
                except Exception as e:
                    logger.warning(
                        "llm_service.client_check_error",
                        error=str(e),
                    )

            result = await self._booking.check_and_book(
                telegram_id=user_id,
                client_name=client_name,
                date_str=args.get("date", ""),
                time_str=args.get("time", ""),
                service_name=args.get("service", ""),
                phone=client_phone,
                notes=args.get("notes"),
            )

            # Подробное логирование
            logger.info(
                "llm_service.create_booking_result",
                user_id=user_id,
                success=result.success,
                message=result.message,
                has_booking=result.booking is not None,
            )

            response = {
                "success": result.success,
                "message": result.message,
                "booking": None,
                "alternative_slots": [],
            }

            if result.booking:
                response["booking"] = {
                    "date": result.booking.date.strftime("%Y-%m-%d"),
                    "time": result.booking.time.strftime("%H:%M"),
                    "service": result.booking.service,
                    "client_name": result.booking.client_name,
                    "specialist_name": result.booking.specialist_name,
                }

            if result.alternative_slots:
                response["alternative_slots"] = [
                    {"time": s.time_str}
                    for s in result.alternative_slots
                ]

            return response

        # ─── transfer_booking ─────────────────────────────────────────
        elif func_name == "transfer_booking":
            old_date = args.get("old_date", "")
            old_time = args.get("old_time")  # может быть None
            new_date = args.get("new_date", "")
            new_time = args.get("new_time", "")
            service = args.get("service")
            t_client_name = args.get("client_name")

            logger.info(
                "llm_service.transfer_booking_call",
                user_id=user_id,
                old_date=old_date,
                old_time=old_time,
                new_date=new_date,
                new_time=new_time,
                service=service,
            )

            result = await self._booking.transfer_booking(
                telegram_id=user_id,
                service_name=service or "",
                old_date_str=old_date,
                old_time_str=old_time,
                new_date_str=new_date,
                new_time_str=new_time,
                client_name=t_client_name,
            )

            logger.info(
                "llm_service.transfer_booking_result",
                user_id=user_id,
                success=result.success,
                message=result.message,
            )

            response = {
                "success": result.success,
                "message": result.message,
                "booking": None,
                "alternative_slots": [],
            }

            if result.booking:
                response["booking"] = {
                    "date": result.booking.date.strftime("%Y-%m-%d"),
                    "time": result.booking.time.strftime("%H:%M"),
                    "service": result.booking.service,
                    "client_name": result.booking.client_name,
                    "specialist_name": result.booking.specialist_name,
                }

            if result.alternative_slots:
                response["alternative_slots"] = [
                    {"time": s.time_str} for s in result.alternative_slots
                ]

            return response

        # ─── cancel_booking ───────────────────────────────────────────
        elif func_name == "cancel_booking":
            return await self._booking.cancel_booking(
                telegram_id=user_id,
                client_name=args.get("client_name", ""),
                date_str=args.get("date", ""),
                time_str=args.get("time", ""),
            )

        # ─── get_services_list ────────────────────────────────────────
        elif func_name == "get_services_list":
            return await self._booking.get_services()

        # ─── get_client_bookings ──────────────────────────────────────
        elif func_name == "get_client_bookings":
            return await self._booking.get_client_bookings(user_id)

        else:
            logger.warning("llm_service.unknown_tool", tool=func_name)
            return {"error": f"Неизвестный инструмент: {func_name}"}