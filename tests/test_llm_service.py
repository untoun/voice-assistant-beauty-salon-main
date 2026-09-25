"""
Тесты для LLMService.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.llm_service import LLMService, DialogSession


@pytest.fixture
def mock_booking_service():
    """Мок BookingService."""
    booking = MagicMock()
    booking.check_available_slots = AsyncMock(return_value={
        "success": True,
        "date": "2025-06-20",
        "available_slots": ["10:00", "14:00"],
        "message": "Доступны слоты: 10:00, 14:00",
    })
    booking.check_and_book = AsyncMock(return_value=MagicMock(
        success=True,
        message="Запись создана",
        booking=MagicMock(model_dump=lambda **kw: {}),
        alternative_slots=None,
    ))
    booking.get_services = AsyncMock(return_value={
        "services": [
            {"name": "Стрижка", "duration_minutes": 60, "price": 2000}
        ]
    })
    booking.get_client_bookings = AsyncMock(return_value={"bookings": []})
    booking._calendar = MagicMock()
    booking._calendar.get_services = AsyncMock(return_value=[])
    booking._calendar.format_services_for_prompt = MagicMock(return_value="")
    return booking


class TestDialogSession:
    """Тесты сессии диалога."""

    def test_session_not_expired_initially(self):
        session = DialogSession(user_id=123)
        assert not session.is_expired(30)

    def test_session_expired_after_ttl(self):
        session = DialogSession(user_id=123)
        session.last_activity = 0  # Давно
        assert session.is_expired(30)

    def test_add_message(self):
        session = DialogSession(user_id=123)
        session.add_message("user", "Привет")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"

    def test_trim_history(self):
        session = DialogSession(user_id=123)
        for i in range(25):
            session.add_message("user", f"Message {i}")
        session.trim_history(20)
        assert len(session.messages) == 20


class TestLLMService:
    """Тесты диалогового движка."""

    def test_model_selection_simple(self, mock_booking_service):
        """Простой запрос → дешёвая модель."""
        service = LLMService(mock_booking_service)
        model = service._select_model("Запишите меня на стрижку", history_length=2)
        from config import settings
        assert model == settings.llm_model

    def test_model_selection_complex(self, mock_booking_service):
        """Сложный запрос → дорогая модель."""
        service = LLMService(mock_booking_service)
        model = service._select_model(
            "Перенесите мою запись на другое время",
            history_length=2,
        )
        from config import settings
        assert model == settings.llm_model_complex

    def test_session_creation(self, mock_booking_service):
        """Новая сессия создаётся при первом обращении."""
        service = LLMService(mock_booking_service)
        session = service._get_or_create_session(999)
        assert session.user_id == 999

    def test_session_reset(self, mock_booking_service):
        """Сброс сессии."""
        service = LLMService(mock_booking_service)
        service._get_or_create_session(123)
        service.reset_session(123)
        assert 123 not in service._sessions