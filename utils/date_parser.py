"""
Парсинг разговорных формулировок дат и времени на русском языке.
Используется для валидации и дополнительной обработки данных от LLM.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# ─── Безопасный импорт ZoneInfo ───────────────────────────────────────────────
# На Windows без tzdata ZoneInfo не работает — используем fallback на date.today()
_ZONEINFO_AVAILABLE = False
_ZoneInfo = None

try:
    from zoneinfo import ZoneInfo as _ZoneInfo  # type: ignore
    # Проверяем что tzdata действительно доступна
    _ZoneInfo("Europe/Moscow")
    _ZONEINFO_AVAILABLE = True
except Exception:
    logger.warning = lambda *a, **kw: None  # Подавляем до инициализации логгера
    _ZONEINFO_AVAILABLE = False

# ─── Маппинги ─────────────────────────────────────────────────────────────────

WEEKDAYS_RU: dict[str, int] = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "среда": 2,
    "четверг": 3,
    "пятницу": 4,
    "пятница": 4,
    "субботу": 5,
    "суббота": 5,
    "воскресенье": 6,
}

MONTHS_RU: dict[str, int] = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


# ─── Основные функции ─────────────────────────────────────────────────────────

def get_current_date() -> date:
    """
    Получить текущую дату.

    Использует временную зону из настроек если tzdata доступна.
    Иначе — системное время (корректно на большинстве Windows машин
    если системная зона настроена правильно).
    """
    if _ZONEINFO_AVAILABLE and _ZoneInfo is not None:
        try:
            from config import settings
            tz = _ZoneInfo(settings.timezone)
            return datetime.now(tz).date()
        except Exception:
            pass
    # Fallback: системное локальное время
    return datetime.now().date()


def get_current_datetime() -> datetime:
    """Получить текущее datetime."""
    if _ZONEINFO_AVAILABLE and _ZoneInfo is not None:
        try:
            from config import settings
            tz = _ZoneInfo(settings.timezone)
            return datetime.now(tz)
        except Exception:
            pass
    return datetime.now()


def parse_relative_date(
    text: str,
    reference_date: Optional[date] = None,
) -> Optional[date]:
    """
    Парсинг относительных дат из текста на русском языке.

    Args:
        text: Текст с упоминанием даты
        reference_date: Базовая дата (по умолчанию сегодня)

    Returns:
        Распознанная дата или None
    """
    if reference_date is None:
        reference_date = get_current_date()

    text_lower = text.lower().strip()

    # ── Простые относительные даты ────────────────────────────────────────────
    if re.search(r'\bсегодня\b', text_lower):
        return reference_date

    if re.search(r'\bзавтра\b', text_lower):
        return reference_date + timedelta(days=1)

    if re.search(r'\bпослезавтра\b', text_lower):
        return reference_date + timedelta(days=2)

    # "через N дней"
    match = re.search(r'через\s+(\d+)\s+дн', text_lower)
    if match:
        return reference_date + timedelta(days=int(match.group(1)))

    # "через неделю"
    if re.search(r'через\s+неделю', text_lower):
        return reference_date + timedelta(weeks=1)

    # ── Следующая неделя ──────────────────────────────────────────────────────
    if re.search(r'следующ', text_lower):
        days_ahead = 7 - reference_date.weekday()
        next_monday = reference_date + timedelta(days=days_ahead)
        for day_name, day_num in WEEKDAYS_RU.items():
            if day_name in text_lower:
                return next_monday + timedelta(days=day_num)
        return next_monday

    # ── День недели ───────────────────────────────────────────────────────────
    for day_name, day_num in WEEKDAYS_RU.items():
        if day_name in text_lower:
            days_until = (day_num - reference_date.weekday()) % 7
            if days_until == 0:
                days_until = 7
            return reference_date + timedelta(days=days_until)

    # ── "25 января", "25го января" ────────────────────────────────────────────
    months_pattern = '|'.join(MONTHS_RU.keys())
    match = re.search(
        rf'(\d{{1,2}})[\-\s]*(го|е|ое)?\s*({months_pattern})',
        text_lower,
    )
    if match:
        day = int(match.group(1))
        month = MONTHS_RU[match.group(3)]
        year = reference_date.year
        try:
            target = date(year, month, day)
            if target < reference_date:
                target = date(year + 1, month, day)
            return target
        except ValueError:
            return None

    # ── "25го", "25 числа" ────────────────────────────────────────────────────
    match = re.search(r'(\d{1,2})\s*(го|числа)\b', text_lower)
    if match:
        day = int(match.group(1))
        try:
            target = reference_date.replace(day=day)
            if target <= reference_date:
                if reference_date.month == 12:
                    target = date(reference_date.year + 1, 1, day)
                else:
                    target = reference_date.replace(
                        month=reference_date.month + 1, day=day
                    )
            return target
        except ValueError:
            return None

    # ── Числовая дата "25.01", "25/01", "25.01.2025" ─────────────────────────
    match = re.search(
        r'(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?',
        text_lower,
    )
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else reference_date.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def parse_time_of_day(text: str) -> Optional[tuple[time, time]]:
    """
    Парсинг времени суток из текста.

    Returns:
        Кортеж (начало, конец) диапазона или None
    """
    # Импортируем здесь чтобы избежать circular import при загрузке модуля
    from config import settings

    text_lower = text.lower()

    if any(w in text_lower for w in ["утр", "утром"]):
        return (time(settings.working_hours_start, 0), time(12, 0))

    if any(w in text_lower for w in ["вечер", "вечером", "вечерком"]):
        return (time(16, 0), time(settings.working_hours_end, 0))

    if any(w in text_lower for w in ["днём", "днем", "после обеда", "полдень"]):
        return (time(12, 0), time(16, 0))

    if any(w in text_lower for w in ["ночь", "ночью"]):
        return None

    # "в 3", "в 15:00"
    match = re.search(r'в\s+(\d{1,2})(?::(\d{2}))?', text_lower)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        if (
            hour < settings.working_hours_start
            and hour + 12 <= settings.working_hours_end
        ):
            hour += 12
        start = time(hour, minute)
        end_hour = hour + 1 if hour < 23 else 23
        return (start, time(end_hour, minute))

    return None


def is_valid_booking_date(d: date) -> tuple[bool, str]:
    """
    Проверка допустимости даты для бронирования.

    Returns:
        (is_valid, error_message)
    """
    today = get_current_date()  # Теперь с безопасным fallback

    if d < today:
        return False, "Нельзя записаться на прошедшую дату"

    if d.weekday() == 6:  # Воскресенье
        return False, "Воскресенье — выходной день"

    max_date = today + timedelta(days=30)
    if d > max_date:
        return False, "Запись возможна не более чем на 30 дней вперёд"

    return True, ""


def format_date_ru(d: date) -> str:
    """Форматирование даты в русском формате."""
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    weekdays = [
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье",
    ]
    return f"{d.day} {months[d.month - 1]} ({weekdays[d.weekday()]})"