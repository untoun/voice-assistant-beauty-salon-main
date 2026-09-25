"""
Модели временного слота и услуги.
"""
from __future__ import annotations

from datetime import date, time
from typing import Optional, Any

from pydantic import BaseModel, ConfigDict


class TimeSlot(BaseModel):
    """Временной слот в расписании."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    date: date
    time: time
    is_available: bool = True
    booking: Optional[Any] = None
    specialist_id: Optional[int] = None
    specialist_name: Optional[str] = None

    def __str__(self) -> str:
        status = "свободно" if self.is_available else "занято"
        return f"{self.date} {self.time.strftime('%H:%M')} [{status}]"

    @property
    def time_str(self) -> str:
        return self.time.strftime("%H:%M")


class Service(BaseModel):
    """Услуга салона с привязкой к специалисту."""

    name: str
    duration_minutes: int
    price: int
    description: Optional[str] = None
    specialist_name: Optional[str] = None
    specialist_id: Optional[int] = None

    @property
    def total_block_minutes(self) -> int:
        """Длительность услуги + 15 мин подготовки."""
        return self.duration_minutes + 15

    def __str__(self) -> str:
        base = f"{self.name} — {self.price} руб., {self.duration_minutes} мин."
        if self.specialist_name:
            base += f" (специалист: {self.specialist_name})"
        return base


class Specialist(BaseModel):
    """Специалист салона."""

    id: int
    name: str
    services: list[str] = []