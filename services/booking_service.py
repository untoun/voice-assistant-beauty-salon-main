"""
Бизнес-логика бронирования с поддержкой специалистов.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Optional

from models.booking import Booking, BookingResult
from models.time_slot import Service, TimeSlot
from services.calendar_service import CalendarService, SlotUnavailableError
from utils.date_parser import format_date_ru, is_valid_booking_date
from utils.logger import get_logger
from utils.validators import validate_time_in_working_hours

logger = get_logger(__name__)

PREP_TIME_MINUTES = 15


class BookingService:
    """Сервис бизнес-логики записи с поддержкой специалистов."""

    def __init__(self, calendar: CalendarService) -> None:
        self._calendar = calendar

    # ─── Поиск услуг ──────────────────────────────────────────────────────────

    async def _find_services_variants(
        self, service_name: str
    ) -> list[Service]:
        if not service_name:
            return []
        try:
            services = await self._calendar.get_services()
        except Exception as e:
            logger.warning(
                "booking_service.service_lookup_error", error=str(e)
            )
            return []

        name_lower = service_name.lower().strip()

        for svc in services:
            if svc.name.lower() == name_lower:
                return [svc]

        partial: list[Service] = [
            svc for svc in services
            if name_lower in svc.name.lower()
        ]
        if partial:
            return partial

        keywords = [w for w in name_lower.split() if len(w) > 2]
        if keywords:
            keyword_matches: list[Service] = [
                svc for svc in services
                if any(kw in svc.name.lower() for kw in keywords)
            ]
            if keyword_matches:
                return keyword_matches

        return []

    async def _find_service(
        self, service_name: Optional[str]
    ) -> Optional[Service]:
        if not service_name:
            return None
        variants = await self._find_services_variants(service_name)
        if len(variants) == 1:
            return variants[0]
        return None

    def _format_service_options(self, services: list[Service]) -> str:
        lines = []
        for svc in services:
            line = (
                f"• {svc.name} — {svc.duration_minutes} мин, "
                f"{svc.price} руб."
            )
            if svc.specialist_name:
                line += f" (мастер: {svc.specialist_name})"
            lines.append(line)
        return "\n".join(lines)

    # ─── Слоты ────────────────────────────────────────────────────────────────

    async def check_available_slots(
        self,
        date_str: str,
        service_name: Optional[str] = None,
        preferred_time_str: Optional[str] = None,
        excluded_time_str: Optional[str] = None,
    ) -> dict:
        """
        Проверить свободные слоты.

        Args:
            excluded_time_str: Время текущей записи клиента (при переносе).
                Если указано — это время НЕ считается занятым.
        """
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "error": f"Неверный формат даты: {date_str}"}

        is_valid, error_msg = is_valid_booking_date(target_date)
        if not is_valid:
            return {"success": False, "error": error_msg}

        service_obj: Optional[Service] = None

        if service_name:
            variants = await self._find_services_variants(service_name)

            if len(variants) == 0:
                return {
                    "success": False,
                    "error": "УСЛУГА_НЕ_НАЙДЕНА",
                    "message": f"Услуга «{service_name}» не найдена.",
                }

            if len(variants) > 1:
                options_detailed = self._format_service_options(variants)
                return {
                    "success": False,
                    "error": "УТОЧНИТЕ_УСЛУГУ",
                    "message": (
                        f"У нас несколько вариантов «{service_name}». "
                        f"Какой именно вас интересует?"
                    ),
                    "options": [v.name for v in variants],
                    "options_detailed": options_detailed,
                    "date": date_str,
                    "preferred_time": preferred_time_str,
                }

            service_obj = variants[0]

        duration = (
            service_obj.duration_minutes if service_obj
            else self._default_duration()
        )
        specialist_id = service_obj.specialist_id if service_obj else None
        specialist_name = service_obj.specialist_name if service_obj else None

        preferred_min: Optional[int] = None
        if preferred_time_str:
            try:
                ph, pm = map(int, preferred_time_str.split(":"))
                preferred_min = ph * 60 + pm
            except (ValueError, AttributeError):
                pass

        try:
            available_slots = await self._calendar.get_available_slots(
                target_date, duration, specialist_id, preferred_min,
            )
        except Exception as e:
            logger.error("booking_service.get_slots_error", error=str(e))
            return {"success": False, "error": f"Ошибка расписания: {e}"}

        slot_times = [s.time_str for s in available_slots]
        date_formatted = format_date_ru(target_date)

        # При переносе — добавляем excluded_time в свободные
        # (т.к. эту запись мы отменим)
        if excluded_time_str and excluded_time_str not in slot_times:
            slot_times.append(excluded_time_str)
            slot_times.sort()

        if not slot_times:
            next_date = await self._find_next_available_date(
                target_date, duration, specialist_id
            )
            return {
                "success": True,
                "date": date_str,
                "date_formatted": date_formatted,
                "available_slots": [],
                "count": 0,
                "service_name": service_obj.name if service_obj else None,
                "specialist_name": specialist_name,
                "message": (
                    f"На {date_formatted} у "
                    f"{specialist_name or 'мастера'} нет свободного времени."
                ),
                "next_available_date": next_date,
            }

        svc_info = f" для «{service_obj.name}»" if service_obj else ""
        if preferred_min is not None and preferred_time_str:
            pref_str = f"{preferred_min // 60:02d}:{preferred_min % 60:02d}"
            if pref_str in slot_times:
                message = (
                    f"На {date_formatted} в {pref_str} у "
                    f"{specialist_name or 'мастера'} есть место{svc_info}!"
                )
            else:
                message = (
                    f"В {pref_str} у {specialist_name or 'мастера'} "
                    f"занято{svc_info}. Ближайшее свободное: "
                    f"{', '.join(slot_times)}."
                )
        else:
            message = (
                f"На {date_formatted} у {specialist_name or 'мастера'} "
                f"доступно{svc_info}: {', '.join(slot_times)}."
            )

        logger.info(
            "booking_service.slots_result",
            date=date_str,
            specialist_id=specialist_id,
            service=service_obj.name if service_obj else None,
            preferred_time=preferred_time_str,
            excluded_time=excluded_time_str,
            slots=slot_times,
        )

        return {
            "success": True,
            "date": date_str,
            "date_formatted": date_formatted,
            "available_slots": slot_times,
            "count": len(slot_times),
            "service_name": service_obj.name if service_obj else None,
            "service_duration_minutes": duration,
            "specialist_name": specialist_name,
            "specialist_id": specialist_id,
            "preferred_time": preferred_time_str,
            "message": message,
        }

    # ─── Бронирование ─────────────────────────────────────────────────────────

    async def check_and_book(
        self,
        telegram_id: int,
        client_name: str,
        date_str: str,
        time_str: str,
        service_name: str,
        phone: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> BookingResult:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return BookingResult.failure_result(f"Неверный формат даты: {date_str}")

        is_valid, error_msg = is_valid_booking_date(target_date)
        if not is_valid:
            return BookingResult.failure_result(error_msg)

        try:
            h, m = map(int, time_str.split(":"))
            target_time = time(h, m)
        except ValueError:
            return BookingResult.failure_result(f"Неверный формат времени: {time_str}")

        time_valid, time_error = validate_time_in_working_hours(target_time)
        if not time_valid:
            return BookingResult.failure_result(time_error)

        variants = await self._find_services_variants(service_name)
        if len(variants) == 0:
            return BookingResult.failure_result(
                message=f"Услуга «{service_name}» не найдена.",
                need_clarification=True,
            )
        if len(variants) > 1:
            return BookingResult.failure_result(
                message=f"Уточните услугу:\n" + self._format_service_options(variants),
                need_clarification=True,
                service_variants=variants,
            )

        service_obj = variants[0]
        duration = service_obj.duration_minutes
        specialist_id = service_obj.specialist_id
        specialist_name = service_obj.specialist_name

        try:
            existing = await self._calendar.get_client_bookings(telegram_id)
            for b in existing:
                if b.date == target_date and b.time == target_time:
                    return BookingResult.failure_result(
                        f"У вас уже есть запись на "
                        f"{format_date_ru(target_date)} в {time_str}."
                    )
        except Exception as e:
            logger.warning("booking_service.check_existing_error", error=str(e))

        requested_min = h * 60 + m
        try:
            slot_is_free = await self._check_single_slot(
                target_date, requested_min, duration, specialist_id,
            )
        except Exception as e:
            logger.error("booking_service.slot_check_error", error=str(e))
            return BookingResult.failure_result("Не удалось проверить расписание.")

        if not slot_is_free:
            try:
                nearest = await self._calendar.get_available_slots(
                    target_date, duration, specialist_id,
                    preferred_time_min=requested_min,
                )
            except Exception:
                nearest = []

            alt_times = [s.time_str for s in nearest]
            msg = f"К сожалению, {time_str} у {specialist_name or 'мастера'} занято."
            if alt_times:
                msg += f" Свободны: {', '.join(alt_times)}."

            return BookingResult.failure_result(message=msg, alternative_slots=nearest)

        is_new_client = True
        try:
            existing_bookings = await self._calendar.get_client_bookings(telegram_id)
            is_new_client = len(existing_bookings) == 0
        except Exception:
            pass

        if is_new_client and not phone:
            return BookingResult.failure_result(
                message="Для первой записи нужен номер телефона.",
                need_phone=True,
            )

        booking = Booking(
            telegram_id=telegram_id,
            client_name=client_name,
            client_phone=phone,
            date=target_date,
            time=target_time,
            service=service_obj.name,
            duration_minutes=duration,
            specialist_name=specialist_name,
            specialist_id=specialist_id,
            status="подтверждено",
            created_at=datetime.now(),
            notes=notes,
        )

        try:
            success = await self._calendar.create_booking(booking)
            if not success:
                return BookingResult.failure_result("Не удалось создать запись.")
        except SlotUnavailableError as e:
            fresh = await self._calendar.get_available_slots(
                target_date, duration, specialist_id,
                preferred_time_min=requested_min,
            )
            return BookingResult.failure_result(message=str(e), alternative_slots=fresh[:3])
        except Exception as e:
            logger.error("booking_service.create_error", error=str(e))
            return BookingResult.failure_result("Не удалось создать запись.")

        try:
            await self._calendar.get_or_create_client(telegram_id, client_name, phone)
        except Exception:
            pass

        logger.info(
            "booking_service.booking_success",
            telegram_id=telegram_id, date=date_str, time=time_str,
            service=service_obj.name, specialist=specialist_name,
        )

        return BookingResult.success_result(
            booking=booking,
            message=(
                f"Запись создана! {service_obj.name} у "
                f"{specialist_name or 'мастера'}, "
                f"{format_date_ru(target_date)}, {time_str}. "
                f"Длительность: {duration} мин. Ждём вас!"
            ),
        )

    # ─── Перенос записи ──────────────────────────────────────────────────────

    async def transfer_booking(
        self,
        telegram_id: int,
        service_name: str,
        old_date_str: str,
        old_time_str: Optional[str],
        new_date_str: str,
        new_time_str: str,
        client_name: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> BookingResult:
        """
        Перенос записи — атомарная операция:
        1. Находим существующую запись клиента
        2. Проверяем свободность нового слота (исключая старую запись)
        3. Помечаем старую как «перенесено»
        4. Создаём новую запись
        """

        # ── 1. Находим существующую запись ────────────────────────────────
        try:
            existing_bookings = await self._calendar.get_client_bookings(
                telegram_id
            )
        except Exception as e:
            logger.error("booking_service.transfer_get_bookings_error", error=str(e))
            return BookingResult.failure_result(
                "Не удалось получить ваши записи."
            )

        if not existing_bookings:
            return BookingResult.failure_result(
                "У вас нет активных записей для переноса."
            )

        booking_to_transfer: Optional[Booking] = None

        # Приоритет 1: точное совпадение дата + время
        if old_time_str:
            for b in existing_bookings:
                if (
                    b.date.strftime("%Y-%m-%d") == old_date_str
                    and b.time.strftime("%H:%M") == old_time_str
                ):
                    booking_to_transfer = b
                    break

        # Приоритет 2: дата + услуга
        if not booking_to_transfer and service_name:
            variants = await self._find_services_variants(service_name)
            variant_names_lower = {v.name.lower() for v in variants}
            for b in existing_bookings:
                if (
                    b.date.strftime("%Y-%m-%d") == old_date_str
                    and b.service.lower() in variant_names_lower
                ):
                    booking_to_transfer = b
                    break

        # Приоритет 3: любая запись на эту дату
        if not booking_to_transfer:
            for b in existing_bookings:
                if b.date.strftime("%Y-%m-%d") == old_date_str:
                    booking_to_transfer = b
                    break

        if not booking_to_transfer:
            bookings_info = ", ".join(
                f"{b.date.strftime('%d.%m')} {b.time.strftime('%H:%M')} {b.service}"
                for b in existing_bookings
            )
            return BookingResult.failure_result(
                f"Не нашли вашу запись на {old_date_str}. "
                f"Ваши записи: {bookings_info}"
            )

        # Данные из найденной записи
        actual_old_time = booking_to_transfer.time.strftime("%H:%M")
        actual_old_date = booking_to_transfer.date.strftime("%Y-%m-%d")
        actual_service = booking_to_transfer.service
        actual_name = booking_to_transfer.client_name
        actual_phone = booking_to_transfer.client_phone or phone
        specialist_id = booking_to_transfer.specialist_id
        specialist_name = booking_to_transfer.specialist_name
        duration = booking_to_transfer.duration_minutes

        logger.info(
            "booking_service.transfer_source_found",
            telegram_id=telegram_id,
            service=actual_service,
            old_date=actual_old_date,
            old_time=actual_old_time,
            new_date=new_date_str,
            new_time=new_time_str,
            specialist_id=specialist_id,
            duration=duration,
        )

        # ── 2. Валидация нового времени ───────────────────────────────────
        try:
            new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
        except ValueError:
            return BookingResult.failure_result(f"Неверный формат даты: {new_date_str}")

        is_valid, error_msg = is_valid_booking_date(new_date)
        if not is_valid:
            return BookingResult.failure_result(error_msg)

        try:
            nh, nm = map(int, new_time_str.split(":"))
            new_time_obj = time(nh, nm)
        except ValueError:
            return BookingResult.failure_result(f"Неверный формат времени: {new_time_str}")

        time_valid, time_error = validate_time_in_working_hours(new_time_obj)
        if not time_valid:
            return BookingResult.failure_result(time_error)

        # ── 3. Проверяем слот, ИСКЛЮЧАЯ старую запись из blocked ──────────
        new_requested_min = nh * 60 + nm

        try:
            slot_is_free = await self._check_single_slot_excluding(
                target_date=new_date,
                requested_min=new_requested_min,
                duration=duration,
                specialist_id=specialist_id,
                exclude_date_str=actual_old_date,
                exclude_time_str=actual_old_time,
                exclude_telegram_id=telegram_id,
            )
        except Exception as e:
            logger.error("booking_service.transfer_slot_check_error", error=str(e))
            return BookingResult.failure_result("Не удалось проверить расписание.")

        if not slot_is_free:
            try:
                nearest = await self._calendar.get_available_slots(
                    new_date, duration, specialist_id,
                    preferred_time_min=new_requested_min,
                )
            except Exception:
                nearest = []

            alt_times = [s.time_str for s in nearest]
            msg = (
                f"К сожалению, {new_time_str} у "
                f"{specialist_name or 'мастера'} занято."
            )
            if alt_times:
                msg += f" Свободны: {', '.join(alt_times)}."

            return BookingResult.failure_result(message=msg, alternative_slots=nearest)

        # ── 4. Помечаем старую запись как «перенесено» ────────────────────
        try:
            marked = await self._calendar.mark_as_transferred(
                telegram_id=telegram_id,
                date_str=actual_old_date,
                time_str=actual_old_time,
                new_time_str=new_time_str,
                new_date_str=new_date_str,
            )
            if not marked:
                # Fallback — обычная отмена
                logger.warning("booking_service.transfer_mark_fallback_to_cancel")
                await self._calendar.cancel_booking(
                    telegram_id, actual_old_date, actual_old_time
                )
        except Exception as e:
            logger.error("booking_service.transfer_mark_error", error=str(e))
            return BookingResult.failure_result(
                "Не удалось обновить старую запись."
            )

        # ── 5. Создаём новую запись ───────────────────────────────────────
        new_booking = Booking(
            telegram_id=telegram_id,
            client_name=client_name or actual_name,
            client_phone=actual_phone,
            date=new_date,
            time=new_time_obj,
            service=actual_service,
            duration_minutes=duration,
            specialist_name=specialist_name,
            specialist_id=specialist_id,
            status="подтверждено",
            created_at=datetime.now(),
            notes=f"Перенос с {actual_old_date} {actual_old_time}",
        )

        try:
            success = await self._calendar.create_booking(new_booking)
            if not success:
                await self._restore_cancelled_booking(
                    telegram_id, actual_old_date, actual_old_time
                )
                return BookingResult.failure_result(
                    "Не удалось создать новую запись. Старая запись сохранена."
                )
        except Exception as e:
            logger.error("booking_service.transfer_create_error", error=str(e))
            await self._restore_cancelled_booking(
                telegram_id, actual_old_date, actual_old_time
            )
            return BookingResult.failure_result(
                "Не удалось создать новую запись. Старая запись сохранена."
            )

        logger.info(
            "booking_service.transfer_success",
            telegram_id=telegram_id,
            service=actual_service,
            old=f"{actual_old_date} {actual_old_time}",
            new=f"{new_date_str} {new_time_str}",
            specialist=specialist_name,
        )

        return BookingResult.success_result(
            booking=new_booking,
            message=(
                f"Запись перенесена! {actual_service} у "
                f"{specialist_name or 'мастера'}, "
                f"{format_date_ru(new_date)}, {new_time_str}. "
                f"Старая запись на {actual_old_time} отменена."
            ),
        )

    async def _restore_cancelled_booking(
        self, telegram_id: int, date_str: str, time_str: str
    ) -> None:
        """Восстановить запись если перенос провалился."""
        try:
            sheet = self._calendar._get_sheet("Расписание")
            all_rows = sheet.get_all_values()
            for idx, row in enumerate(all_rows[1:], start=2):
                if len(row) <= 6:
                    continue
                if (
                    row[0] == date_str
                    and row[1] == time_str
                    and row[6] == str(telegram_id)
                    and "перенесено" in row[5].strip().lower()
                ):
                    sheet.update_cell(idx, 6, "подтверждено")
                    logger.info(
                        "booking_service.booking_restored",
                        date=date_str, time=time_str,
                    )
                    return
        except Exception as e:
            logger.error("booking_service.restore_failed", error=str(e))

    # ─── Проверка слота с исключением ─────────────────────────────────────

    async def _check_single_slot_excluding(
        self,
        target_date: date,
        requested_min: int,
        duration: int,
        specialist_id: Optional[int],
        exclude_date_str: str,
        exclude_time_str: str,
        exclude_telegram_id: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._sync_check_single_slot_excluding,
            target_date, requested_min, duration, specialist_id,
            exclude_date_str, exclude_time_str, exclude_telegram_id,
        )

    def _sync_check_single_slot_excluding(
        self,
        target_date: date,
        requested_min: int,
        duration: int,
        specialist_id: Optional[int],
        exclude_date_str: str,
        exclude_time_str: str,
        exclude_telegram_id: int,
    ) -> bool:
        """Проверка слота с исключением записи клиента."""
        sheet = self._calendar._get_sheet("Расписание")
        all_rows = sheet.get_all_values()
        date_str = target_date.strftime("%Y-%m-%d")

        # Фильтруем — убираем старую запись из расчёта
        filtered_rows = [all_rows[0]]
        excluded_count = 0
        for row in all_rows[1:]:
            if (
                len(row) > 6
                and row[0].strip() == exclude_date_str
                and row[1].strip() == exclude_time_str
                and row[6].strip() == str(exclude_telegram_id)
                and row[5].strip().lower() == "подтверждено"
            ):
                excluded_count += 1
                continue
            filtered_rows.append(row)

        blocked = self._calendar._get_blocked_ranges(
            filtered_rows, date_str, specialist_id
        )

        is_available = self._calendar._is_slot_available(
            requested_min, duration, blocked
        )

        logger.info(
            "booking_service.slot_check_excluding",
            date=date_str,
            requested_time=f"{requested_min // 60:02d}:{requested_min % 60:02d}",
            duration=duration,
            specialist_id=specialist_id,
            excluded=f"{exclude_date_str} {exclude_time_str}",
            excluded_rows=excluded_count,
            blocked_count=len(blocked),
            is_available=is_available,
        )

        return is_available

    # ─── Прямая проверка слота ────────────────────────────────────────────

    async def _check_single_slot(
        self, target_date: date, requested_min: int,
        duration: int, specialist_id: Optional[int],
    ) -> bool:
        return await asyncio.to_thread(
            self._sync_check_single_slot,
            target_date, requested_min, duration, specialist_id,
        )

    def _sync_check_single_slot(
        self, target_date: date, requested_min: int,
        duration: int, specialist_id: Optional[int],
    ) -> bool:
        sheet = self._calendar._get_sheet("Расписание")
        all_rows = sheet.get_all_values()
        date_str = target_date.strftime("%Y-%m-%d")

        blocked = self._calendar._get_blocked_ranges(
            all_rows, date_str, specialist_id
        )
        is_available = self._calendar._is_slot_available(
            requested_min, duration, blocked
        )

        logger.info(
            "booking_service.direct_slot_check",
            date=date_str,
            requested_time=f"{requested_min // 60:02d}:{requested_min % 60:02d}",
            duration=duration, specialist_id=specialist_id,
            blocked_count=len(blocked), is_available=is_available,
        )
        return is_available

    # ─── Отмена ───────────────────────────────────────────────────────────

    async def cancel_booking(
        self, telegram_id: int, client_name: str,
        date_str: str, time_str: str,
    ) -> dict:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            date_formatted = format_date_ru(target_date)
        except ValueError:
            return {"success": False, "error": f"Неверный формат даты: {date_str}"}

        try:
            success = await self._calendar.cancel_booking(
                telegram_id, date_str, time_str
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

        if success:
            return {"success": True, "message": f"Запись на {date_formatted} в {time_str} отменена."}
        return {"success": False, "error": f"Запись на {date_str} в {time_str} не найдена."}

    # ─── Вспомогательные ──────────────────────────────────────────────────

    async def get_services(self) -> dict:
        try:
            services = await self._calendar.get_services()
            return {
                "services": [
                    {
                        "name": s.name,
                        "duration_minutes": s.duration_minutes,
                        "total_block_minutes": s.total_block_minutes,
                        "price": s.price,
                        "description": s.description or "",
                        "specialist_name": s.specialist_name or "",
                        "specialist_id": s.specialist_id,
                    }
                    for s in services
                ]
            }
        except Exception as e:
            return {"services": [], "error": str(e)}

    async def get_client_bookings(self, telegram_id: int) -> dict:
        try:
            bookings = await self._calendar.get_client_bookings(telegram_id)
        except Exception as e:
            return {"bookings": [], "error": str(e)}

        if not bookings:
            return {"bookings": [], "message": "У вас нет предстоящих записей."}

        return {
            "bookings": [
                {
                    "date": b.date.strftime("%Y-%m-%d"),
                    "date_formatted": format_date_ru(b.date),
                    "time": b.time.strftime("%H:%M"),
                    "service": b.service,
                    "specialist": b.specialist_name or "",
                    "duration_minutes": b.duration_minutes,
                    "status": b.status,
                }
                for b in bookings
            ],
            "count": len(bookings),
        }

    async def _find_next_available_date(
        self, from_date: date, duration: int,
        specialist_id: Optional[int] = None,
    ) -> Optional[str]:
        dates, tasks = [], []
        for i in range(1, 8):
            nd = from_date + timedelta(days=i)
            if nd.weekday() == 6:
                continue
            dates.append(nd)
            tasks.append(self._calendar.get_available_slots(nd, duration, specialist_id))

        if not tasks:
            return None
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for d, r in zip(dates, results):
                if isinstance(r, list) and r:
                    return format_date_ru(d)
        except Exception:
            pass
        return None

    @staticmethod
    def _default_duration() -> int:
        from config import settings
        return settings.slot_duration_minutes