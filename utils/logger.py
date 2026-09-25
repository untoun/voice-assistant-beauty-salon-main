"""
Настройка структурированного логирования через structlog.
JSON-формат для продакшена, читаемый формат для разработки.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def setup_logging(level: str = "INFO", json_format: bool = False) -> None:
    """
    Инициализация structlog с заданными параметрами.

    Args:
        level: Уровень логирования (DEBUG/INFO/WARNING/ERROR)
        json_format: True для JSON вывода (продакшен), False для читаемого (разработка)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Настройка stdlib logging — structlog будет использовать его как backend
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Отключаем избыточные логи внешних библиотек
    for noisy_logger in ["httpx", "httpcore", "telegram", "gspread"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Процессоры которые работают с stdlib logging (имеют .name)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # ИСПРАВЛЕНИЕ: add_logger_name только со stdlib backend
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_format:
        # Продакшен: JSON через stdlib
        formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=shared_processors,
        )
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(handler)
        root_logger.setLevel(log_level)

        structlog.configure(
            processors=shared_processors + [
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            cache_logger_on_first_use=True,
        )
    else:
        # Разработка: читаемый цветной вывод через PrintLogger
        # ИСПРАВЛЕНИЕ: убираем add_logger_name из цепочки для PrintLogger
        dev_processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ]

        structlog.configure(
            processors=dev_processors,
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> structlog.BoundLogger:
    """Получить логгер с именем модуля."""
    return structlog.get_logger(name)