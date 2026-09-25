"""
Модель клиента салона.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Client(BaseModel):
    """Клиент салона красоты."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    telegram_id: int
    name: str = Field(min_length=1, max_length=100)
    phone: Optional[str] = None
    visit_count: int = Field(default=0, ge=0)
    last_visit: Optional[date] = None

    # ИСПРАВЛЕНИЕ: is_new_client — метод, не property
    # В Pydantic v2 @property с логикой безопасен только для простых типов
    def is_new(self) -> bool:
        return self.visit_count == 0