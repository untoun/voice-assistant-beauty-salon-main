"""
Валидаторы входных данных.
"""
import re
from datetime import date, time

from config import settings


def validate_phone(phone: str) -> tuple[bool, str]:
    """Валидация российского номера телефона."""
    # Убираем все нецифровые символы
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits[0] in ('7', '8'):
        normalized = '+7' + digits[1:]
        return True, normalized
    if len(digits) == 10:
        normalized = '+7' + digits
        return True, normalized
    return False, phone


def validate_time_in_working_hours(t: time) -> tuple[bool, str]:
    """Проверка что время в рабочих часах."""
    start = time(settings.working_hours_start, 0)
    end = time(settings.working_hours_end, 0)
    if t < start or t >= end:
        return (
            False,
            f"Время должно быть в рабочие часы: "
            f"{settings.working_hours_start}:00 — {settings.working_hours_end}:00",
        )
    return True, ""


def sanitize_text(text: str, max_length: int = 500) -> str:
    """Очистка пользовательского текста."""
    # Убираем лишние пробелы и переносы
    text = re.sub(r'\s+', ' ', text).strip()
    # Обрезаем до максимальной длины
    return text[:max_length]