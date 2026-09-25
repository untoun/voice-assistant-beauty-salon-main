"""
Тесты для BookingService.
Все даты — динамические (будущее), чтобы тесты не устаревали.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.booking import Booking, BookingResult
from models.time_slot import Service, TimeSlot
from services.booking_service import BookingService
from services.calendar_service import CalendarService, SlotUnavailableError


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def next_weekday(weekday: int, min_days_ahead: int = 1) -> date:
    """
    Найти ближайший день недели не раньше чем через min_days_ahead дней.

    Args:
        weekday: 0=пн, 1=вт, ..., 5=сб, 6=вс
        min_days_ahead: Минимальный отступ от сегодня

    Returns:
        Дата искомого дня недели
    """
    start = date.today() + timedelta(days=min_days_ahead)
    days_ahead = (weekday - start.weekday()) % 7
    return start + timedelta(days=days_ahead)


def future_workday(days_ahead: int = 1) -> date:
    """
    Вернуть рабочий день через days_ahead дней (пропуская воскресенья).

    Args:
        days_ahead: Минимальный отступ от сегодня

    Returns:
        Ближайший рабочий день
    """
    d = date.today() + timedelta(days=days_ahead)
    # Пропускаем воскресенья
    while d.weekday() == 6:
        d += timedelta(days=1)
    return d


def next_sunday() -> date:
    """Вернуть дату ближайшего воскресенья (минимум завтра)."""
    return next_weekday(weekday=6, min_days_ahead=1)


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_calendar() -> MagicMock:
    """Полностью замоканный CalendarService."""
    cal = MagicMock(spec=CalendarService)

    # Услуги
    cal.get_services = AsyncMock(return_value=[
        Service(name="Стрижка женская", duration_minutes=60, price=2000),
        Service(name="Маникюр", duration_minutes=90, price=2500),
        Service(name="Окрашивание", duration_minutes=180, price=5000),
    ])

    # Слоты на будущий рабочий день
    workday = future_workday(1)
    cal.get_available_slots = AsyncMock(return_value=[
        TimeSlot(date=workday, time=time(10, 0), is_available=True),
        TimeSlot(date=workday, time=time(11, 0), is_available=True),
        TimeSlot(date=workday, time=time(14, 0), is_available=True),
    ])

    cal.create_booking = AsyncMock(return_value=True)
    cal.cancel_booking = AsyncMock(return_value=True)
    cal.get_client_bookings = AsyncMock(return_value=[])
    cal.get_or_create_client = AsyncMock()
    cal.format_services_for_prompt = MagicMock(return_value="- Стрижка: 60 мин, 2000 руб.")
    return cal


@pytest.fixture
def booking_service(mock_calendar: MagicMock) -> BookingService:
    """BookingService с замоканными зависимостями."""
    return BookingService(mock_calendar)


# ─── TestCheckAvailableSlots ─────────────────────────────────────────────────

class TestCheckAvailableSlots:
    """Тесты метода check_available_slots."""

    @pytest.mark.asyncio
    async def test_valid_date_returns_slots(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Корректная будущая дата → возвращает слоты."""
        workday = future_workday(1)

        # Мок возвращает слоты на нужную дату
        mock_calendar.get_available_slots = AsyncMock(return_value=[
            TimeSlot(date=workday, time=time(10, 0), is_available=True),
            TimeSlot(date=workday, time=time(11, 0), is_available=True),
            TimeSlot(date=workday, time=time(14, 0), is_available=True),
        ])

        result = await booking_service.check_available_slots(
            date_str=workday.isoformat(),
            service_name="Стрижка женская",
        )

        assert result["success"] is True
        assert len(result["available_slots"]) == 3
        assert "10:00" in result["available_slots"]

    @pytest.mark.asyncio
    async def test_invalid_date_format(self, booking_service: BookingService):
        """Неверный формат даты → ошибка с описанием."""
        result = await booking_service.check_available_slots(
            date_str="25-06-2025",
        )
        assert result["success"] is False
        assert "формат" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_past_date_returns_error(self, booking_service: BookingService):
        """Прошедшая дата → ошибка."""
        past = (date.today() - timedelta(days=1)).isoformat()
        result = await booking_service.check_available_slots(date_str=past)
        assert result["success"] is False
        assert "прошедшую" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_sunday_returns_error(self, booking_service: BookingService):
        """Воскресенье → ошибка (выходной)."""
        sunday = next_sunday().isoformat()
        result = await booking_service.check_available_slots(date_str=sunday)
        assert result["success"] is False
        assert "воскресенье" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_slots_available(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Нет свободных слотов → success=True, пустой список."""
        workday = future_workday(1)
        mock_calendar.get_available_slots = AsyncMock(return_value=[])

        result = await booking_service.check_available_slots(
            date_str=workday.isoformat(),
        )

        assert result["success"] is True
        assert result["available_slots"] == []


# ─── TestCheckAndBook ─────────────────────────────────────────────────────────

class TestCheckAndBook:
    """Тесты метода check_and_book."""

    @pytest.mark.asyncio
    async def test_successful_booking(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Все данные корректны → запись создана."""
        workday = future_workday(1)

        mock_calendar.get_available_slots = AsyncMock(return_value=[
            TimeSlot(date=workday, time=time(10, 0), is_available=True),
            TimeSlot(date=workday, time=time(11, 0), is_available=True),
        ])
        mock_calendar.create_booking = AsyncMock(return_value=True)

        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария Иванова",
            date_str=workday.isoformat(),
            time_str="10:00",
            service_name="Стрижка женская",
        )

        assert result.success is True
        assert result.booking is not None
        assert result.booking.client_name == "Мария Иванова"
        assert result.booking.service == "Стрижка женская"
        mock_calendar.create_booking.assert_called_once()

    @pytest.mark.asyncio
    async def test_unavailable_slot(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Запрошенный слот занят → предлагает альтернативы."""
        workday = future_workday(1)

        # 10:00 отсутствует в доступных → слот занят
        mock_calendar.get_available_slots = AsyncMock(return_value=[
            TimeSlot(date=workday, time=time(11, 0), is_available=True),
            TimeSlot(date=workday, time=time(14, 0), is_available=True),
        ])

        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария",
            date_str=workday.isoformat(),
            time_str="10:00",
            service_name="Стрижка женская",
        )

        assert result.success is False
        assert result.alternative_slots is not None
        assert len(result.alternative_slots) > 0

    @pytest.mark.asyncio
    async def test_race_condition_protection(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Race condition: слот перехватили параллельно → корректный отказ."""
        workday = future_workday(1)

        mock_calendar.get_available_slots = AsyncMock(return_value=[
            TimeSlot(date=workday, time=time(10, 0), is_available=True),
        ])
        # create_booking выбрасывает исключение — слот уже занят
        mock_calendar.create_booking = AsyncMock(
            side_effect=SlotUnavailableError("Слот занят в процессе записи")
        )

        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария",
            date_str=workday.isoformat(),
            time_str="10:00",
            service_name="Стрижка женская",
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_sunday_rejected(self, booking_service: BookingService):
        """Воскресенье → отказ."""
        sunday = next_sunday().isoformat()
        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария",
            date_str=sunday,
            time_str="10:00",
            service_name="Стрижка женская",
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_past_date_rejected(self, booking_service: BookingService):
        """Прошедшая дата → отказ."""
        past = (date.today() - timedelta(days=1)).isoformat()
        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария",
            date_str=past,
            time_str="10:00",
            service_name="Стрижка женская",
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_outside_working_hours_rejected(
        self, booking_service: BookingService
    ):
        """Время до открытия → отказ."""
        workday = future_workday(1)
        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария",
            date_str=workday.isoformat(),
            time_str="07:00",
            service_name="Стрижка женская",
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_duplicate_booking_rejected(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Повторная запись на то же время → отказ с нужным сообщением."""
        workday = future_workday(1)

        # Уже есть запись на это время
        existing = Booking(
            telegram_id=123456,
            client_name="Мария",
            date=workday,
            time=time(10, 0),
            service="Стрижка женская",
            duration_minutes=60,
            status="подтверждено",
            created_at=datetime.now(),
        )
        mock_calendar.get_client_bookings = AsyncMock(return_value=[existing])
        mock_calendar.get_available_slots = AsyncMock(return_value=[
            TimeSlot(date=workday, time=time(10, 0), is_available=True),
        ])

        result = await booking_service.check_and_book(
            telegram_id=123456,
            client_name="Мария",
            date_str=workday.isoformat(),
            time_str="10:00",
            service_name="Стрижка женская",
        )

        assert result.success is False
        assert "уже есть запись" in result.message


# ─── TestCancelBooking ────────────────────────────────────────────────────────

class TestCancelBooking:
    """Тесты метода cancel_booking."""

    @pytest.mark.asyncio
    async def test_successful_cancellation(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Запись найдена и отменена успешно."""
        mock_calendar.cancel_booking = AsyncMock(return_value=True)
        workday = future_workday(1)

        result = await booking_service.cancel_booking(
            telegram_id=123456,
            client_name="Мария",
            date_str=workday.isoformat(),
            time_str="10:00",
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_booking_not_found(
        self,
        booking_service: BookingService,
        mock_calendar: MagicMock,
    ):
        """Запись не найдена → сообщение об ошибке."""
        mock_calendar.cancel_booking = AsyncMock(return_value=False)
        workday = future_workday(1)

        result = await booking_service.cancel_booking(
            telegram_id=123456,
            client_name="Мария",
            date_str=workday.isoformat(),
            time_str="10:00",
        )

        assert result["success"] is False