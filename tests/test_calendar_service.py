"""
Тесты для CalendarService (интеграционные с моком gspread).
"""
from __future__ import annotations

import pytest
from datetime import date, time
from unittest.mock import MagicMock, patch, AsyncMock

from models.booking import Booking
from datetime import datetime


@pytest.fixture
def mock_worksheet():
    """Мок листа Google Sheets."""
    ws = MagicMock()
    ws.get_all_values.return_value = [
        # Заголовок
        ["Дата", "Время", "Услуга", "Клиент", "Телефон", "Статус", "Telegram ID", "Создано", "Примечания", "Длительность"],
        # Данные
        ["2025-06-20", "10:00", "Стрижка", "Мария", "+79001234567", "подтверждено", "123456", "2025-06-19 10:00", "", "60"],
        ["2025-06-20", "11:00", "", "", "", "свободно", "", "", "", "60"],
        ["2025-06-20", "14:00", "", "", "", "свободно", "", "", "", "60"],
    ]
    return ws


class TestCalendarServiceSlots:
    """Тесты получения слотов."""

    def test_generate_default_slots(self):
        """Генерация слотов по умолчанию для нового дня."""
        from services.calendar_service import CalendarService
        service = CalendarService()
        slots = service._generate_default_slots(date(2025, 6, 23))
        assert len(slots) > 0
        assert all(s.is_available for s in slots)

    def test_sync_get_available_slots_with_booking(self, mock_worksheet):
        """Занятые слоты исключаются из доступных."""
        from services.calendar_service import CalendarService

        service = CalendarService()
        # Подменяем метод получения листа
        service._get_sheet = MagicMock(return_value=mock_worksheet)

        slots = service._sync_get_available_slots(date(2025, 6, 20), duration_minutes=60)

        # 10:00 занято, 11:00 и 14:00 свободны
        available = [s for s in slots if s.is_available]
        available_times = [s.time_str for s in available]

        assert "10:00" not in available_times
        assert "11:00" in available_times
        assert "14:00" in available_times


class TestCalendarServiceBooking:
    """Тесты создания записей."""

    def test_sync_create_booking_success(self, mock_worksheet):
        """Успешное создание записи."""
        from services.calendar_service import CalendarService

        mock_worksheet.get_all_values.return_value = [
            ["Дата", "Время", "Услуга", "Клиент", "Телефон", "Статус", "TG ID", "Создано", "Notes", "Dur"],
            ["2025-06-20", "11:00", "", "", "", "свободно", "", "", "", "60"],
        ]

        service = CalendarService()
        service._get_sheet = MagicMock(return_value=mock_worksheet)

        booking = Booking(
            telegram_id=123456,
            client_name="Тест",
            date=date(2025, 6, 20),
            time=time(11, 0),
            service="Стрижка",
            duration_minutes=60,
            status="подтверждено",
            created_at=datetime.now(),
        )

        result = service._sync_create_booking(booking)
        assert result is True
        mock_worksheet.update.assert_called_once()

    def test_race_condition_protection(self, mock_worksheet):
        """Защита от race condition при двойном бронировании."""
        from services.calendar_service import CalendarService, SlotUnavailableError

        # Слот уже занят в момент второй проверки
        mock_worksheet.get_all_values.return_value = [
            ["Дата", "Время", "Услуга", "Клиент", "Телефон", "Статус", "TG ID", "Создано", "Notes", "Dur"],
            ["2025-06-20", "11:00", "Стрижка", "Другой", "", "подтверждено", "999", "", "", "60"],
        ]

        service = CalendarService()
        service._get_sheet = MagicMock(return_value=mock_worksheet)

        booking = Booking(
            telegram_id=123456,
            client_name="Тест",
            date=date(2025, 6, 20),
            time=time(11, 0),
            service="Стрижка",
            duration_minutes=60,
            status="подтверждено",
            created_at=datetime.now(),
        )

        with pytest.raises(SlotUnavailableError):
            service._sync_create_booking(booking)