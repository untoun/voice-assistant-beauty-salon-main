"""
Точка входа приложения.
Инициализация и запуск Telegram бота.
"""
from __future__ import annotations

import asyncio
import os
import sys

from telegram.ext import Application

from bot.handlers import setup_handlers, setup_services
from config import settings
from utils.logger import setup_logging, get_logger


async def post_init(application: Application) -> None:
    """Пост-инициализация приложения."""
    logger = get_logger(__name__)
    logger.info(
        "bot.started",
        salon=settings.salon_name,
        model=settings.llm_model,
        tts_voice=settings.tts_voice,
    )

    # Уведомление администратора (если настроен)
    if settings.admin_telegram_id:
        try:
            await application.bot.send_message(
                chat_id=settings.admin_telegram_id,
                text=f"✅ Бот {settings.salon_name} запущен",
            )
        except Exception:
            pass


async def post_shutdown(application: Application) -> None:
    """Действия при остановке."""
    logger = get_logger(__name__)
    logger.info("bot.shutdown")


def main() -> None:
    """Главная функция запуска бота."""
    # Настройка логирования
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    setup_logging(
        level=os.getenv("LOG_LEVEL", "INFO"),
        json_format=is_production,
    )

    logger = get_logger(__name__)
    logger.info("bot.initializing", environment=os.getenv("ENVIRONMENT", "development"))

    # Инициализация сервисов
    setup_services()

    # Создание Telegram приложения
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Регистрация обработчиков
    setup_handlers(app)

    logger.info("bot.running")

    # Запуск polling
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,  # Игнорировать накопившиеся сообщения
    )


if __name__ == "__main__":
    main()