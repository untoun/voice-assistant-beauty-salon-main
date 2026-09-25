"""
Мидлвари для Telegram бота.
Rate limiting, логирование, обработка ошибок.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import BaseHandler, CallbackContext

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """
    Скользящее окно для rate limiting.
    Thread-safe для asyncio.
    """

    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._user_requests: dict[int, deque] = defaultdict(deque)

    def is_allowed(self, user_id: int) -> bool:
        """Проверить разрешён ли запрос для пользователя."""
        now = time.monotonic()
        window_start = now - self.window_seconds

        requests = self._user_requests[user_id]

        # Удаляем устаревшие записи
        while requests and requests[0] < window_start:
            requests.popleft()

        if len(requests) >= self.max_requests:
            return False

        requests.append(now)
        return True

    def get_wait_time(self, user_id: int) -> float:
        """Время ожидания до следующего разрешённого запроса."""
        requests = self._user_requests.get(user_id)
        if not requests:
            return 0.0
        oldest = requests[0]
        wait = oldest + self.window_seconds - time.monotonic()
        return max(0.0, wait)


# Глобальный rate limiter
_rate_limiter = RateLimiter(
    max_requests=settings.rate_limit_per_minute,
    window_seconds=60,
)


async def rate_limit_middleware(
    update: Update,
    context: CallbackContext,
    next_handler: Callable,
) -> None:
    """Middleware для rate limiting."""
    user_id = update.effective_user.id if update.effective_user else 0

    if not _rate_limiter.is_allowed(user_id):
        wait_time = _rate_limiter.get_wait_time(user_id)
        logger.warning(
            "middleware.rate_limit_exceeded",
            user_id=user_id,
            wait_seconds=round(wait_time, 1),
        )
        if update.effective_message:
            await update.effective_message.reply_text(
                "Подождите немного, я обрабатываю ваш запрос. "
                f"Попробуйте через {int(wait_time) + 1} секунд."
            )
        return

    await next_handler(update, context)


def get_rate_limiter() -> RateLimiter:
    """Получить глобальный rate limiter."""
    return _rate_limiter