"""
Pydantic-модели для записей клиентов.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Booking(BaseModel):
    """Запись клиента на услугу."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    telegram_id: int
    client_name: str = Field(min_length=1, max_length=100)
    client_phone: Optional[str] = None
    date: date
    time: time
    service: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(default=60, ge=15, le=480)
    specialist_name: Optional[str] = None
    specialist_id: Optional[int] = None
    status: Literal["подтверждено", "отменено", "свободно"] = "подтверждено"
    created_at: datetime = Field(default_factory=datetime.now)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("client_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return v.strip().title()

    def get_datetime(self) -> datetime:
        return datetime.combine(self.date, self.time)

    def get_end_time(self) -> time:
        from datetime import timedelta
        end_dt = self.get_datetime() + timedelta(minutes=self.duration_minutes + 15)
        return end_dt.time()

    def to_sheet_row(self) -> list[str]:
        """Конвертация в строку для Google Sheets."""
        return [
            self.date.strftime("%Y-%m-%d"),       # A: Дата
            self.time.strftime("%H:%M"),            # B: Время
            self.service,                           # C: Услуга
            self.client_name,                       # D: Клиент
            self.client_phone or "",                # E: Телефон
            self.status,                            # F: Статус
            str(self.telegram_id),                  # G: Telegram ID
            self.created_at.strftime("%Y-%m-%d %H:%M"),  # H: Создано
            self.notes or "",                       # I: Примечания
            str(self.duration_minutes),             # J: Длительность
            self.specialist_name or "",             # K: Специалист
            str(self.specialist_id or ""),          # L: ID специалиста
        ]

    def __str__(self) -> str:
        from utils.date_parser import format_date_ru
        spec = f" ({self.specialist_name})" if self.specialist_name else ""
        return (
            f"{self.service}{spec} | {format_date_ru(self.date)} | "
            f"{self.time.strftime('%H:%M')} | {self.client_name}"
        )


class BookingResult(BaseModel):
    """Результат операции бронирования."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    message: str
    booking: Optional[Booking] = None
    alternative_slots: Optional[list] = None

    @classmethod
    def success_result(cls, booking: Booking, message: str) -> "BookingResult":
        return cls(success=True, message=message, booking=booking)

    @classmethod
    def failure_result(
        cls,
        message: str,
        alternative_slots: Optional[list] = None,
    ) -> "BookingResult":
        return cls(
            success=False,
            message=message,
            alternative_slots=alternative_slots,
        )