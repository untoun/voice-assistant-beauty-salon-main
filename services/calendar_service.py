"""
Сервис работы с Google Sheets.
Поддержка нескольких специалистов.

Алгоритм расчёта слотов:
- Специалист занят в диапазоне [start, start + duration + PREP]
- Новая запись [new_start, new_start + new_duration + PREP] не должна
  пересекаться ни с одним занятым диапазоном
- Предлагаем ближайшие к запрошенному времени свободные моменты
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Optional

import gspread
from cachetools import TTLCache
from google.oauth2.service_account import Credentials
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from models.booking import Booking
from models.client import Client
from models.time_slot import Service, Specialist, TimeSlot
from utils.logger import get_logger

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_SCHEDULE = "Расписание"
SHEET_SERVICES = "Услуги"
SHEET_CLIENTS = "Клиенты"

HEADERS_SCHEDULE = [
    "Дата", "Время", "Услуга", "Клиент", "Телефон",
    "Статус", "Telegram ID", "Создано", "Примечания",
    "Длительность", "Специалист", "ID специалиста",
]
HEADERS_SERVICES = [
    "Название", "Длительность (мин)", "Цена",
    "Описание", "Специалист", "ID специалиста",
]
HEADERS_CLIENTS = [
    "Telegram ID", "Имя", "Телефон",
    "Кол-во визитов", "Последний визит",
]

COL_DATE = 0
COL_TIME = 1
COL_SERVICE = 2
COL_CLIENT = 3
COL_PHONE = 4
COL_STATUS = 5
COL_TG_ID = 6
COL_CREATED = 7
COL_NOTES = 8
COL_DURATION = 9
COL_SPECIALIST = 10
COL_SPECIALIST_ID = 11

SVC_NAME = 0
SVC_DURATION = 1
SVC_PRICE = 2
SVC_DESC = 3
SVC_SPECIALIST = 4
SVC_SPECIALIST_ID = 5

PREP_TIME_MINUTES = 15

# Шаг сетки слотов в минутах
SLOT_STEP_MINUTES = 15


class CalendarServiceError(Exception):
    pass


class SlotUnavailableError(CalendarServiceError):
    pass


class CalendarService:
    """Сервис управления расписанием через Google Sheets."""

    def __init__(self) -> None:
        self._gc: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._lock = asyncio.Lock()
        self._services_cache: TTLCache = TTLCache(
            maxsize=1,
            ttl=settings.services_cache_ttl_seconds,
        )
        self._sheets_initialized = False

    # ─── Подключение ──────────────────────────────────────────────────────────

    def _get_client(self) -> gspread.Client:
        if self._gc is None:
            creds = Credentials.from_service_account_file(
                settings.google_sheets_credentials_json,
                scopes=SCOPES,
            )
            self._gc = gspread.authorize(creds)
            logger.info("calendar_service.google_sheets_connected")
        return self._gc

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None:
            gc = self._get_client()
            self._spreadsheet = gc.open_by_key(settings.google_sheet_id)
            existing = [ws.title for ws in self._spreadsheet.worksheets()]
            logger.info(
                "calendar_service.spreadsheet_opened",
                title=self._spreadsheet.title,
                sheets=existing,
            )
        return self._spreadsheet

    def _ensure_sheets_exist(self) -> None:
        if self._sheets_initialized:
            return
        ss = self._get_spreadsheet()
        existing = {ws.title for ws in ss.worksheets()}
        if SHEET_SCHEDULE not in existing:
            ws = ss.add_worksheet(title=SHEET_SCHEDULE, rows=2000, cols=12)
            ws.append_row(HEADERS_SCHEDULE)
        if SHEET_SERVICES not in existing:
            ws = ss.add_worksheet(title=SHEET_SERVICES, rows=100, cols=6)
            ws.append_row(HEADERS_SERVICES)
        if SHEET_CLIENTS not in existing:
            ws = ss.add_worksheet(title=SHEET_CLIENTS, rows=1000, cols=5)
            ws.append_row(HEADERS_CLIENTS)
        self._sheets_initialized = True

    def _get_sheet(self, name: str) -> gspread.Worksheet:
        self._ensure_sheets_exist()
        return self._get_spreadsheet().worksheet(name)

    # ─── Услуги ───────────────────────────────────────────────────────────────

    def _sync_get_services(self) -> list[Service]:
        sheet = self._get_sheet(SHEET_SERVICES)
        rows = sheet.get_all_values()
        services = []
        for row in rows[1:]:
            if not row or not row[SVC_NAME].strip():
                continue
            try:
                specialist_id = None
                if len(row) > SVC_SPECIALIST_ID and row[SVC_SPECIALIST_ID].strip():
                    specialist_id = int(row[SVC_SPECIALIST_ID].strip())
                services.append(Service(
                    name=row[SVC_NAME].strip(),
                    duration_minutes=int(row[SVC_DURATION].strip()),
                    price=int(row[SVC_PRICE].strip()),
                    description=(
                        row[SVC_DESC].strip() if len(row) > SVC_DESC else None
                    ),
                    specialist_name=(
                        row[SVC_SPECIALIST].strip()
                        if len(row) > SVC_SPECIALIST else None
                    ),
                    specialist_id=specialist_id,
                ))
            except (ValueError, IndexError) as e:
                logger.warning(
                    "calendar_service.service_parse_error", error=str(e)
                )
        return services

    async def get_services(self) -> list[Service]:
        if "services" in self._services_cache:
            return self._services_cache["services"]
        services = await asyncio.to_thread(self._sync_get_services)
        self._services_cache["services"] = services
        return services

    async def get_specialists(self) -> list[Specialist]:
        services = await self.get_services()
        specialists: dict[int, Specialist] = {}
        for svc in services:
            if svc.specialist_id and svc.specialist_name:
                sid = svc.specialist_id
                if sid not in specialists:
                    specialists[sid] = Specialist(
                        id=sid, name=svc.specialist_name, services=[]
                    )
                specialists[sid].services.append(svc.name)
        return list(specialists.values())

    def format_services_for_prompt(self, services: list[Service]) -> str:
        if not services:
            return "Список услуг временно недоступен"
        by_specialist: dict[str, list[Service]] = {}
        for svc in services:
            key = svc.specialist_name or "Другие"
            by_specialist.setdefault(key, []).append(svc)
        lines = []
        for specialist, svcs in by_specialist.items():
            lines.append(f"\n👤 {specialist}:")
            for s in svcs:
                lines.append(
                    f"  - {s.name}: {s.duration_minutes} мин, {s.price} руб."
                    + (f" — {s.description}" if s.description else "")
                )
        return "\n".join(lines)

    # ─── Ключевая логика: занятые диапазоны ───────────────────────────────────

    def _get_blocked_ranges(
        self,
        all_rows: list[list[str]],
        date_str: str,
        specialist_id: Optional[int],
    ) -> list[tuple[int, int]]:
        """
        Вернуть список занятых диапазонов [start_min, end_min) для специалиста.

        Правила фильтрации по специалисту:
        - Если specialist_id указан И запись имеет ID → сравниваем ID
        - Если specialist_id указан, но в записи нет ID → ПРОПУСКАЕМ
          (не блокируем: нет оснований считать, что запись принадлежит этому спецу)
        - Если specialist_id не указан → берём ВСЕ подтверждённые записи
        """
        blocked: list[tuple[int, int]] = []

        for row in all_rows[1:]:
            # Базовые проверки
            if len(row) <= COL_STATUS:
                continue
            if not row[COL_TIME].strip():
                continue
            if row[COL_DATE].strip() != date_str:
                continue
            if row[COL_STATUS].strip().lower() != "подтверждено":
                continue

            # Определяем ID специалиста в строке
            row_spec_id: Optional[int] = None
            if len(row) > COL_SPECIALIST_ID and row[COL_SPECIALIST_ID].strip():
                try:
                    row_spec_id = int(row[COL_SPECIALIST_ID].strip())
                except ValueError:
                    logger.warning(
                        "calendar_service.invalid_specialist_id",
                        value=row[COL_SPECIALIST_ID],
                    )

            # Фильтрация по специалисту
            if specialist_id is not None:
                if row_spec_id is None:
                    # ФИО специалиста как fallback, если ID не заполнен
                    row_spec_name = (
                        row[COL_SPECIALIST].strip()
                        if len(row) > COL_SPECIALIST
                        else ""
                    )
                    # Нет надёжного способа сопоставить — пропускаем
                    logger.debug(
                        "calendar_service.row_without_specialist_id_skipped",
                        row_specialist_name=row_spec_name,
                        target_specialist_id=specialist_id,
                    )
                    continue
                if row_spec_id != specialist_id:
                    # Запись другого специалиста — не блокируем
                    continue
            # Если specialist_id is None — берём все записи (общий режим)

            try:
                h, m = map(int, row[COL_TIME].strip().split(":"))
                start_min = h * 60 + m

                duration = 60  # Дефолт если не заполнено
                if len(row) > COL_DURATION and row[COL_DURATION].strip():
                    try:
                        duration = int(row[COL_DURATION].strip())
                    except ValueError:
                        logger.warning(
                            "calendar_service.invalid_duration",
                            value=row[COL_DURATION],
                        )

                # Конец блока = начало + работа + подготовка
                end_min = start_min + duration + PREP_TIME_MINUTES
                blocked.append((start_min, end_min))

                logger.debug(
                    "calendar_service.blocked_range_added",
                    specialist_id=specialist_id,
                    start=f"{h:02d}:{m:02d}",
                    end=f"{end_min // 60:02d}:{end_min % 60:02d}",
                )
            except (ValueError, IndexError) as e:
                logger.warning(
                    "calendar_service.row_time_parse_error", error=str(e)
                )
                continue

        return sorted(blocked, key=lambda x: x[0])

    def _is_slot_available(
        self,
        start_min: int,
        new_duration: int,
        blocked_ranges: list[tuple[int, int]],
    ) -> bool:
        """
        Проверить доступность слота.

        Слот [start_min, start_min + new_duration + PREP] свободен если:
        - Начало не раньше начала рабочего дня
        - Конец ВМЕСТЕ С PREP не выходит за конец рабочего дня
        - Не пересекается ни с одним занятым диапазоном

        Пересечение [a1, a2) и [b1, b2): a1 < b2 AND a2 > b1
        """
        day_start = settings.working_hours_start * 60
        day_end = settings.working_hours_end * 60

        # Полный блок новой записи (услуга + подготовка)
        new_end = start_min + new_duration + PREP_TIME_MINUTES

        # Проверка рабочего времени
        # ИСПРАВЛЕНО: проверяем new_end (с PREP), а не только new_duration
        if start_min < day_start:
            return False
        if new_end > day_end:
            # Запись выходит за пределы рабочего дня с учётом подготовки
            return False

        # Проверка пересечений с занятыми диапазонами
        for b_start, b_end in blocked_ranges:
            if start_min < b_end and new_end > b_start:
                logger.debug(
                    "calendar_service.slot_conflict",
                    requested=f"{start_min // 60:02d}:{start_min % 60:02d}",
                    our_block_end=f"{new_end // 60:02d}:{new_end % 60:02d}",
                    conflict_start=f"{b_start // 60:02d}:{b_start % 60:02d}",
                    conflict_end=f"{b_end // 60:02d}:{b_end % 60:02d}",
                )
                return False

        return True

    def _find_all_free_slots(
        self,
        new_duration: int,
        blocked_ranges: list[tuple[int, int]],
        target_date: date,
        specialist_id: Optional[int],
    ) -> list[TimeSlot]:
        """
        Найти все свободные слоты в рабочем дне с шагом SLOT_STEP_MINUTES.

        Если target_date — сегодня, слоты в прошлом не предлагаем.
        Шаг сетки: SLOT_STEP_MINUTES (15 минут).
        """
        day_start = settings.working_hours_start * 60
        day_end = settings.working_hours_end * 60

        # Для сегодняшней даты — не предлагаем прошедшее время
        # ИСПРАВЛЕНО: добавлена проверка текущего времени
        now = datetime.now()
        min_start = day_start
        if target_date == now.date():
            # Округляем текущее время вверх до ближайшего шага сетки
            current_min = now.hour * 60 + now.minute
            # +1 чтобы не предлагать "прямо сейчас" без запаса
            rounded_up = (
                (current_min // SLOT_STEP_MINUTES + 1) * SLOT_STEP_MINUTES
            )
            min_start = max(day_start, rounded_up)

        free_slots: list[TimeSlot] = []
        current = min_start

        while current + new_duration + PREP_TIME_MINUTES <= day_end:
            if self._is_slot_available(current, new_duration, blocked_ranges):
                h = current // 60
                m = current % 60
                free_slots.append(TimeSlot(
                    date=target_date,
                    time=time(h, m),
                    is_available=True,
                    specialist_id=specialist_id,
                ))
            current += SLOT_STEP_MINUTES  # ИСПРАВЛЕНО: константа вместо магии

        return free_slots

    def _get_nearest_slots(
        self,
        free_slots: list[TimeSlot],
        preferred_time_min: Optional[int],
        max_count: int = 3,
    ) -> list[TimeSlot]:
        """
        Выбрать до max_count слотов ближайших к предпочтительному времени.

        Алгоритм:
        1. Если предпочтительное время указано:
           - Приоритет: слоты >= preferred_time (начиная с ближайшего)
           - Если слотов после недостаточно — добавляем ближайшие ДО
           - Итоговый список всегда отсортирован по времени
        2. Если предпочтительное время не указано:
           - Возвращаем первые max_count слотов

        Пример: хочу в 16:00, свободны 14:00, 15:00, 16:00, 17:00
          → возвращаем [16:00, 17:00] (2 слота после) + [15:00] если нужен 3й
          → итог: [15:00, 16:00, 17:00]
        """
        if not free_slots:
            return []

        if preferred_time_min is None:
            return free_slots[:max_count]

        def slot_to_min(s: TimeSlot) -> int:
            return s.time.hour * 60 + s.time.minute

        # Слоты начиная с предпочтительного времени (включительно)
        at_or_after = [
            s for s in free_slots
            if slot_to_min(s) >= preferred_time_min
        ]

        # Слоты строго до предпочтительного времени
        before = [
            s for s in free_slots
            if slot_to_min(s) < preferred_time_min
        ]

        result: list[TimeSlot] = []

        if len(at_or_after) >= max_count:
            # Достаточно слотов после — берём первые max_count
            result = at_or_after[:max_count]
        else:
            # Не хватает слотов после — добираем из "до" (ближайшие к запросу)
            needed = max_count - len(at_or_after)
            # before отсортирован по возрастанию, берём хвост (ближайшие к preferred)
            closest_before = before[-needed:] if needed <= len(before) else before
            # Объединяем: сначала "до" (уже по возрастанию), затем "после"
            result = closest_before + at_or_after

        return result[:max_count]

    # ─── Публичный метод получения слотов ─────────────────────────────────────

    def _sync_get_available_slots(
        self,
        target_date: date,
        duration_minutes: int,
        specialist_id: Optional[int] = None,
        preferred_time_min: Optional[int] = None,
    ) -> list[TimeSlot]:
        """
        Найти свободные слоты специалиста на дату.

        Args:
            target_date: Дата записи
            duration_minutes: Длительность услуги в минутах
            specialist_id: ID специалиста (None — без фильтрации)
            preferred_time_min: Предпочтительное время в минутах от полуночи
                                (например 16:00 → 960)

        Returns:
            До 3 ближайших свободных слотов к предпочтительному времени,
            отсортированных по возрастанию.
        """
        # Прошедшая дата — слотов нет
        if target_date < date.today():
            logger.info(
                "calendar_service.past_date_requested",
                date=target_date.isoformat(),
            )
            return []

        sheet = self._get_sheet(SHEET_SCHEDULE)
        all_rows = sheet.get_all_values()
        date_str = target_date.strftime("%Y-%m-%d")

        # 1. Занятые диапазоны специалиста
        blocked = self._get_blocked_ranges(all_rows, date_str, specialist_id)

        logger.info(
            "calendar_service.schedule_check",
            date=date_str,
            specialist_id=specialist_id,
            duration=duration_minutes,
            blocked_count=len(blocked),
            blocked=[
                f"{b // 60:02d}:{b % 60:02d}-{e // 60:02d}:{e % 60:02d}"
                for b, e in blocked
            ],
            preferred=(
                f"{preferred_time_min // 60:02d}:{preferred_time_min % 60:02d}"
                if preferred_time_min is not None
                else "не указано"
            ),
        )

        # 2. Все свободные слоты в рабочем дне
        all_free = self._find_all_free_slots(
            duration_minutes, blocked, target_date, specialist_id
        )

        logger.info(
            "calendar_service.free_slots_found",
            date=date_str,
            specialist_id=specialist_id,
            total_free_slots=len(all_free),
            first_3=[s.time_str for s in all_free[:3]],
            last_3=[s.time_str for s in all_free[-3:]],
        )

        # 3. Ближайшие к предпочтительному времени
        result = self._get_nearest_slots(all_free, preferred_time_min, max_count=3)

        logger.info(
            "calendar_service.slots_to_offer",
            date=date_str,
            specialist_id=specialist_id,
            preferred=(
                f"{preferred_time_min // 60:02d}:{preferred_time_min % 60:02d}"
                if preferred_time_min is not None
                else "не указано"
            ),
            slots=[s.time_str for s in result],
        )

        return result

    # ─── Проверка конкретного слота ───────────────────────────────────────────

    def _sync_check_specific_slot(
        self,
        target_date: date,
        target_time: time,
        duration_minutes: int,
        specialist_id: Optional[int] = None,
    ) -> bool:
        """
        Проверить доступность КОНКРЕТНОГО слота напрямую.

        НЕ ограничена 3 слотами — проверяет именно запрошенное время
        через blocked_ranges.
        """
        if target_date < date.today():
            return False

        sheet = self._get_sheet(SHEET_SCHEDULE)
        all_rows = sheet.get_all_values()
        date_str = target_date.strftime("%Y-%m-%d")

        blocked = self._get_blocked_ranges(all_rows, date_str, specialist_id)
        start_min = target_time.hour * 60 + target_time.minute

        is_free = self._is_slot_available(start_min, duration_minutes, blocked)

        logger.info(
            "calendar_service.specific_slot_check",
            date=date_str,
            time=target_time.strftime("%H:%M"),
            specialist_id=specialist_id,
            duration=duration_minutes,
            is_free=is_free,
            blocked_count=len(blocked),
        )

        return is_free

    async def check_specific_slot(
        self,
        target_date: date,
        target_time: time,
        duration_minutes: int,
        specialist_id: Optional[int] = None,
    ) -> bool:
        """Проверить конкретный слот (async обёртка)."""
        return await asyncio.to_thread(
            self._sync_check_specific_slot,
            target_date,
            target_time,
            duration_minutes,
            specialist_id,
        )
    async def get_available_slots(
        self,
        target_date: date,
        duration_minutes: int = 60,
        specialist_id: Optional[int] = None,
        preferred_time_min: Optional[int] = None,
    ) -> list[TimeSlot]:
        """Получить свободные слоты (async обёртка)."""
        return await asyncio.to_thread(
            self._sync_get_available_slots,
            target_date,
            duration_minutes,
            specialist_id,
            preferred_time_min,
        )

    # ─── Бронирование ─────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((gspread.exceptions.APIError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _sync_create_booking(self, booking: Booking) -> bool:
        """Атомарное создание записи с повторной проверкой."""
        sheet = self._get_sheet(SHEET_SCHEDULE)
        date_str = booking.date.strftime("%Y-%m-%d")
        time_str = booking.time.strftime("%H:%M")
        all_rows = sheet.get_all_values()

        h, m = map(int, time_str.split(":"))
        new_start = h * 60 + m

        # Повторная проверка race condition
        blocked = self._get_blocked_ranges(
            all_rows, date_str, booking.specialist_id
        )

        if not self._is_slot_available(
            new_start, booking.duration_minutes, blocked
        ):
            new_end = new_start + booking.duration_minutes + PREP_TIME_MINUTES
            for b_start, b_end in blocked:
                if new_start < b_end and new_end > b_start:
                    raise SlotUnavailableError(
                        f"Специалист {booking.specialist_name} занят: "
                        f"{b_start // 60:02d}:{b_start % 60:02d}–"
                        f"{b_end // 60:02d}:{b_end % 60:02d}"
                    )
            raise SlotUnavailableError(
                f"Слот {date_str} {time_str} недоступен"
            )

        row_data = [
            str(v) if v is not None else ""
            for v in booking.to_sheet_row()
        ]

        try:
            sheet.append_row(row_data, value_input_option="RAW")
        except Exception as e:
            logger.error("calendar_service.append_row_error", error=str(e))
            # Проверяем: вдруг запись добавилась несмотря на ошибку
            fresh_rows = sheet.get_all_values()
            for row in fresh_rows[1:]:
                if (
                    len(row) > COL_TG_ID
                    and row[COL_DATE] == date_str
                    and row[COL_TIME] == time_str
                    and row[COL_TG_ID] == str(booking.telegram_id)
                    and row[COL_STATUS].strip() == "подтверждено"
                ):
                    logger.info(
                        "calendar_service.booking_confirmed_despite_error",
                        date=date_str,
                        time=time_str,
                    )
                    return True
            raise

        new_end = new_start + booking.duration_minutes + PREP_TIME_MINUTES
        logger.info(
            "calendar_service.booking_created",
            date=date_str,
            time=time_str,
            service=booking.service,
            specialist=booking.specialist_name,
            duration=booking.duration_minutes,
            freed_at=f"{new_end // 60:02d}:{new_end % 60:02d}",
        )
        return True

    async def create_booking(self, booking: Booking) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._sync_create_booking, booking)

    def _sync_cancel_booking(
        self, telegram_id: int, date_str: str, time_str: str
    ) -> bool:
        sheet = self._get_sheet(SHEET_SCHEDULE)
        all_rows = sheet.get_all_values()
        for idx, row in enumerate(all_rows[1:], start=2):
            if len(row) <= COL_TG_ID:
                continue
            if (
                row[COL_DATE] == date_str
                and row[COL_TIME] == time_str
                and row[COL_TG_ID] == str(telegram_id)
                and row[COL_STATUS].strip() == "подтверждено"
            ):
                sheet.update_cell(idx, COL_STATUS + 1, "отменено")
                logger.info(
                    "calendar_service.booking_cancelled",
                    telegram_id=telegram_id,
                    date=date_str,
                    time=time_str,
                )
                return True
        return False

    async def cancel_booking(
        self, telegram_id: int, date_str: str, time_str: str
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._sync_cancel_booking, telegram_id, date_str, time_str
            )

    # ─── Перенос записи ───────────────────────────────────────────────────────────

    def _sync_mark_as_transferred(
            self,
            telegram_id: int,
            date_str: str,
            time_str: str,
            new_time_str: str,
            new_date_str: str,
    ) -> bool:
        """Пометить запись как перенесённую (не отменённую)."""
        sheet = self._get_sheet(SHEET_SCHEDULE)
        all_rows = sheet.get_all_values()
        for idx, row in enumerate(all_rows[1:], start=2):
            if len(row) <= COL_TG_ID:
                continue
            if (
                    row[COL_DATE] == date_str
                    and row[COL_TIME] == time_str
                    and row[COL_TG_ID] == str(telegram_id)
                    and row[COL_STATUS].strip() == "подтверждено"
            ):
                new_status = f"перенесено на {new_date_str} {new_time_str}"
                sheet.update_cell(idx, COL_STATUS + 1, new_status)
                logger.info(
                    "calendar_service.booking_transferred",
                    telegram_id=telegram_id,
                    old_date=date_str,
                    old_time=time_str,
                    new_date=new_date_str,
                    new_time=new_time_str,
                )
                return True
        return False

    async def mark_as_transferred(
            self,
            telegram_id: int,
            date_str: str,
            time_str: str,
            new_time_str: str,
            new_date_str: str,
    ) -> bool:
        """Пометить запись как перенесённую (async обёртка)."""
        async with self._lock:
            return await asyncio.to_thread(
                self._sync_mark_as_transferred,
                telegram_id,
                date_str,
                time_str,
                new_time_str,
                new_date_str,
            )

    def _sync_restore_booking_status(
            self,
            telegram_id: int,
            date_str: str,
            time_str: str,
    ) -> bool:
        """
        Восстановить статус записи обратно в 'подтверждено'.
        Используется при откате если новая запись не создалась.
        """
        sheet = self._get_sheet(SHEET_SCHEDULE)
        all_rows = sheet.get_all_values()

        for idx, row in enumerate(all_rows[1:], start=2):
            if len(row) <= COL_TG_ID:
                continue
            if (
                    row[COL_DATE].strip() == date_str
                    and row[COL_TIME].strip() == time_str
                    and row[COL_TG_ID].strip() == str(telegram_id)
                    and row[COL_STATUS].strip() == "перенесено"
            ):
                sheet.update_cell(idx, COL_STATUS + 1, "подтверждено")
                logger.info(
                    "calendar_service.booking_status_restored",
                    telegram_id=telegram_id,
                    date=date_str,
                    time=time_str,
                )
                return True

        logger.warning(
            "calendar_service.restore_booking_not_found",
            telegram_id=telegram_id,
            date=date_str,
            time=time_str,
        )
        return False

    async def restore_booking_status(
            self,
            telegram_id: int,
            date_str: str,
            time_str: str,
    ) -> bool:
        """Восстановить статус записи (async обёртка)."""
        async with self._lock:
            return await asyncio.to_thread(
                self._sync_restore_booking_status,
                telegram_id,
                date_str,
                time_str,
            )
    def _sync_get_client_bookings(self, telegram_id: int) -> list[Booking]:
        sheet = self._get_sheet(SHEET_SCHEDULE)
        all_rows = sheet.get_all_values()
        today = date.today()
        bookings = []
        for row in all_rows[1:]:
            if len(row) <= COL_TG_ID:
                continue
            if row[COL_TG_ID] != str(telegram_id):
                continue
            if row[COL_STATUS].strip() != "подтверждено":
                continue
            try:
                booking_date = datetime.strptime(
                    row[COL_DATE], "%Y-%m-%d"
                ).date()
                if booking_date < today:
                    continue
                booking_time = datetime.strptime(
                    row[COL_TIME], "%H:%M"
                ).time()
                duration = (
                    int(row[COL_DURATION].strip())
                    if len(row) > COL_DURATION and row[COL_DURATION].strip()
                    else 60
                )
                specialist_id = None
                if (
                    len(row) > COL_SPECIALIST_ID
                    and row[COL_SPECIALIST_ID].strip()
                ):
                    try:
                        specialist_id = int(row[COL_SPECIALIST_ID].strip())
                    except ValueError:
                        pass
                bookings.append(Booking(
                    telegram_id=telegram_id,
                    client_name=row[COL_CLIENT],
                    client_phone=row[COL_PHONE] or None,
                    date=booking_date,
                    time=booking_time,
                    service=row[COL_SERVICE],
                    duration_minutes=duration,
                    specialist_name=(
                        row[COL_SPECIALIST]
                        if len(row) > COL_SPECIALIST else None
                    ),
                    specialist_id=specialist_id,
                    status="подтверждено",
                    created_at=datetime.now(),
                    notes=(
                        row[COL_NOTES] if len(row) > COL_NOTES else None
                    ),
                ))
            except (ValueError, IndexError) as e:
                logger.warning(
                    "calendar_service.row_parse_error", error=str(e)
                )
        return bookings

    async def get_client_bookings(self, telegram_id: int) -> list[Booking]:
        return await asyncio.to_thread(
            self._sync_get_client_bookings, telegram_id
        )

    def _sync_get_or_create_client(
        self,
        telegram_id: int,
        name: str,
        phone: Optional[str] = None,
    ) -> Client:
        try:
            sheet = self._get_sheet(SHEET_CLIENTS)
            all_rows = sheet.get_all_values()
            for row in all_rows[1:]:
                if len(row) > 0 and row[0] == str(telegram_id):
                    visit_count = (
                        int(row[3]) if len(row) > 3 and row[3] else 0
                    )
                    last_visit = None
                    if len(row) > 4 and row[4]:
                        try:
                            last_visit = datetime.strptime(
                                row[4], "%Y-%m-%d"
                            ).date()
                        except ValueError:
                            pass
                    return Client(
                        telegram_id=telegram_id,
                        name=row[1] if len(row) > 1 else name,
                        phone=(
                            row[2] if len(row) > 2 and row[2] else phone
                        ),
                        visit_count=visit_count,
                        last_visit=last_visit,
                    )
            sheet.append_row(
                [str(telegram_id), name, phone or "", "0", ""]
            )
            return Client(telegram_id=telegram_id, name=name, phone=phone)
        except Exception as e:
            logger.error("calendar_service.client_error", error=str(e))
            return Client(telegram_id=telegram_id, name=name, phone=phone)

    async def get_or_create_client(
        self,
        telegram_id: int,
        name: str,
        phone: Optional[str] = None,
    ) -> Client:
        return await asyncio.to_thread(
            self._sync_get_or_create_client, telegram_id, name, phone
        )