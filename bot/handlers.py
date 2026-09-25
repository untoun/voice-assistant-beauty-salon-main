"""
Обработчики сообщений Telegram бота.
Основной пайплайн: голос → STT → LLM → TTS → голос.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Optional

from telegram import Message, Update, Voice
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.keyboards import get_start_keyboard
from bot.middleware import get_rate_limiter
from config import settings
from services.audio_converter import AudioConverter, AudioConversionError
from services.booking_service import BookingService
from services.calendar_service import CalendarService
from services.llm_service import LLMService
from services.speech_to_text import (
    SpeechToTextService,
    EmptyTranscriptionError,
    SpeechToTextError,
)
from services.text_to_speech import TextToSpeechService, TextToSpeechError
from utils.logger import get_logger

logger = get_logger(__name__)

# Семафор — максимум 10 параллельных запросов к OpenAI
_openai_semaphore = asyncio.Semaphore(10)

# Глобальные сервисы
_calendar_service: Optional[CalendarService] = None
_booking_service: Optional[BookingService] = None
_llm_service: Optional[LLMService] = None
_stt_service: Optional[SpeechToTextService] = None
_tts_service: Optional[TextToSpeechService] = None


def setup_services() -> None:
    """Инициализация всех сервисов."""
    global _calendar_service, _booking_service, _llm_service
    global _stt_service, _tts_service

    _calendar_service = CalendarService()
    _booking_service = BookingService(_calendar_service)
    _llm_service = LLMService(_booking_service)
    _stt_service = SpeechToTextService()
    _tts_service = TextToSpeechService()

    logger.info("handlers.services_initialized")


# ─── Команды ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — приветствие."""
    user = update.effective_user
    logger.info("handlers.start", user_id=user.id)

    if _llm_service:
        _llm_service.reset_session(user.id)

    greeting = (
        f"Здравствуйте! Я виртуальный администратор {settings.salon_name}. "
        f"Отправьте голосовое сообщение, и я помогу записаться. "
        f"Например: «Хочу на стрижку в пятницу после обеда»."
    )

    await update.message.reply_text(
        greeting,
        reply_markup=get_start_keyboard(),
    )
    await _send_voice_greeting(update, greeting)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — справка."""
    help_text = (
        f"🎤 *{settings.salon_name}*\n\n"
        "Отправьте голосовое сообщение!\n\n"
        "*Что я умею:*\n"
        "• Записать на услугу\n"
        "• Проверить свободное время\n"
        "• Показать услуги и цены\n"
        "• Отменить запись\n\n"
        "*Команды:*\n"
        "/my\\_bookings — мои записи\n"
        "/cancel — отменить запись\n"
        "/start — начать заново\n\n"
        f"Работаем: пн–сб, {settings.working_hours_str}"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def cmd_my_bookings(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/my_bookings — записи пользователя."""
    # ИСПРАВЛЕНИЕ: определяем откуда пришёл вызов
    user_id = update.effective_user.id
    logger.info("handlers.my_bookings", user_id=user_id)

    # Получаем объект для ответа: message (команда) или callback query
    reply_target = update.message or (
        update.callback_query.message if update.callback_query else None
    )

    if not reply_target:
        logger.error("handlers.my_bookings_error", error="no reply target")
        return

    if not _booking_service:
        await reply_target.reply_text("Сервис временно недоступен.")
        return

    try:
        result = await _booking_service.get_client_bookings(user_id)
        bookings = result.get("bookings", [])

        if not bookings:
            text = "У вас нет предстоящих записей. Хотите записаться?"
        else:
            lines = ["📅 *Ваши записи:*\n"]
            for b in bookings:
                lines.append(
                    f"• {b['date_formatted']}, {b['time']} — {b['service']}"
                )
            text = "\n".join(lines)

        await reply_target.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error("handlers.my_bookings_error", error=str(e))
        await reply_target.reply_text(
            "Не удалось загрузить записи. Попробуйте позже."
        )


async def cmd_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/cancel — начать диалог отмены записи."""
    await _process_text_message_internal(
        update, context,
        text="Я хочу отменить запись",
        reply_target=update.message,
    )


# ─── Основные обработчики сообщений ───────────────────────────────────────────

async def handle_voice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик голосовых сообщений — основной пайплайн."""
    user = update.effective_user
    message = update.message
    rate_limiter = get_rate_limiter()

    if not rate_limiter.is_allowed(user.id):
        wait = rate_limiter.get_wait_time(user.id)
        await message.reply_text(
            f"Подождите немного. Попробуйте через {int(wait) + 1} секунд."
        )
        return

    logger.info("handlers.voice_message", user_id=user.id)

    await context.bot.send_chat_action(
        chat_id=message.chat_id,
        action=ChatAction.RECORD_VOICE,
    )

    audio_paths: list[str] = []
    tts_path: Optional[str] = None
    # ИСПРАВЛЕНИЕ: флаг что ответ уже отправлен клиенту
    response_sent = False

    try:
        async with _openai_semaphore:
            # 1. Скачиваем OGG
            voice: Voice = message.voice
            voice_file = await context.bot.get_file(voice.file_id)
            fd, ogg_path = tempfile.mkstemp(suffix=".ogg", prefix="voice_")
            os.close(fd)
            audio_paths.append(ogg_path)
            await voice_file.download_to_drive(ogg_path)

            # 2. Конвертируем для Whisper
            converter = AudioConverter()
            try:
                mp3_path = await converter.convert_for_whisper(ogg_path)
                audio_paths.append(mp3_path)
            except AudioConversionError as e:
                await _reply_with_text_and_voice(
                    message,
                    f"Не смогла обработать голосовое. {e}",
                )
                response_sent = True
                return

            # 3. STT — Whisper
            await context.bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.TYPING,
            )
            try:
                recognized_text = await _stt_service.transcribe(mp3_path)
                logger.info("handlers.voice_recognized", user_id=user.id)
                await message.reply_text(
                    f"🎤 *Вы сказали:* _{recognized_text}_",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except EmptyTranscriptionError:
                await _reply_with_text_and_voice(
                    message,
                    "Не расслышала. Повторите или напишите текстом.",
                )
                response_sent = True
                return
            except SpeechToTextError as e:
                logger.error("handlers.stt_error", error=str(e))
                await _reply_with_text_and_voice(
                    message,
                    "Не смогла распознать речь. Напишите текстом.",
                )
                response_sent = True
                return

            # 4. LLM — обрабатываем сообщение
            try:
                llm_response = await _llm_service.process_message(
                    user_id=user.id,
                    user_text=recognized_text,
                )
            except Exception as e:
                logger.error("handlers.llm_error", error=str(e), exc_info=True)
                await _reply_with_text_and_voice(
                    message,
                    "Не удалось обработать запрос. Попробуйте ещё раз.",
                )
                response_sent = True
                return

            # 5. TTS + отправка
            try:
                tts_path = await _tts_service.synthesize(
                    llm_response.response_text
                )
                await _send_voice_response(
                    message, tts_path, llm_response.response_text
                )
                response_sent = True
            except TextToSpeechError as e:
                logger.error("handlers.tts_error", error=str(e))
                # Graceful degradation — только текст
                await message.reply_text(llm_response.response_text)
                response_sent = True

    except Exception as e:
        logger.error("handlers.voice_error", error=str(e), exc_info=True)
        # ИСПРАВЛЕНИЕ: отправляем ошибку ТОЛЬКО если ещё не отвечали
        if not response_sent:
            await _send_error_message(message)
        else:
            logger.warning(
                "handlers.error_after_response",
                error=str(e),
                note="Response already sent, suppressing error message",
            )

    finally:
        for path in audio_paths:
            _safe_remove(path)
        if tts_path:
            _safe_remove(tts_path)

async def handle_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик текстовых сообщений."""
    user = update.effective_user
    rate_limiter = get_rate_limiter()

    if not rate_limiter.is_allowed(user.id):
        wait = rate_limiter.get_wait_time(user.id)
        await update.message.reply_text(
            f"Подождите немного. Попробуйте через {int(wait) + 1} секунд."
        )
        return

    await _process_text_message_internal(
        update, context,
        text=update.message.text,
        reply_target=update.message,
    )


async def _process_text_message_internal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_target: Message,
) -> None:
    """Внутренняя обработка текста через LLM + TTS."""
    user = update.effective_user
    logger.info("handlers.text_message", user_id=user.id)

    await context.bot.send_chat_action(
        chat_id=reply_target.chat_id,
        action=ChatAction.TYPING,
    )

    tts_path: Optional[str] = None
    response_sent = False  # ИСПРАВЛЕНИЕ: флаг отправки ответа

    try:
        async with _openai_semaphore:
            # LLM
            try:
                llm_response = await _llm_service.process_message(
                    user_id=user.id,
                    user_text=text,
                )
            except Exception as e:
                logger.error("handlers.llm_error", error=str(e), exc_info=True)
                await reply_target.reply_text(
                    "Не удалось обработать запрос. Попробуйте ещё раз."
                )
                response_sent = True
                return

            # TTS
            try:
                tts_path = await _tts_service.synthesize(
                    llm_response.response_text
                )
                await _send_voice_response(
                    reply_target, tts_path, llm_response.response_text
                )
                response_sent = True
            except TextToSpeechError:
                await reply_target.reply_text(llm_response.response_text)
                response_sent = True

    except Exception as e:
        logger.error("handlers.text_error", error=str(e), exc_info=True)
        # ИСПРАВЛЕНИЕ: только если ещё не отвечали
        if not response_sent:
            await _send_error_message(reply_target)
        else:
            logger.warning(
                "handlers.error_after_response",
                error=str(e),
                note="Suppressing duplicate error message",
            )
    finally:
        if tts_path:
            _safe_remove(tts_path)

# ─── Callback Query ────────────────────────────────────────────────────────────

async def handle_callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик inline-кнопок."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    # ИСПРАВЛЕНИЕ: используем query.message, не update.message
    msg = query.message

    logger.info("handlers.callback", user_id=user_id, data=data)

    if data == "my_bookings":
        await _cb_my_bookings(msg, user_id)

    elif data == "services":
        await _cb_services(msg)

    elif data == "cancel_booking":
        await _cb_process_text(
            update, context, msg, user_id,
            text="Хочу отменить запись",
        )

    elif data == "cancel_confirm":
        await msg.reply_text(
            "Хорошо, запись сохранена. Если что — обращайтесь!"
        )

    elif data.startswith("do_cancel:"):
        parts = data.split(":")
        if len(parts) >= 3:
            cancel_date, cancel_time = parts[1], parts[2]
            await _cb_process_text(
                update, context, msg, user_id,
                text=f"Отмените мою запись на {cancel_date} в {cancel_time}",
            )


async def _cb_my_bookings(msg: Message, user_id: int) -> None:
    """Показать записи пользователя (из callback)."""
    if not _booking_service:
        await msg.reply_text("Сервис временно недоступен.")
        return
    try:
        result = await _booking_service.get_client_bookings(user_id)
        bookings = result.get("bookings", [])
        if not bookings:
            text = "У вас нет предстоящих записей. Хотите записаться?"
        else:
            lines = ["📅 *Ваши записи:*\n"]
            for b in bookings:
                lines.append(
                    f"• {b['date_formatted']}, {b['time']} — {b['service']}"
                )
            text = "\n".join(lines)
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("handlers.my_bookings_error", error=str(e))
        await msg.reply_text("Не удалось загрузить записи. Попробуйте позже.")


async def _cb_services(msg: Message) -> None:
    """Показать список услуг (из callback)."""
    if not _booking_service:
        await msg.reply_text("Сервис временно недоступен.")
        return
    try:
        result = await _booking_service.get_services()
        services = result.get("services", [])
        if services:
            lines = ["💅 *Наши услуги:*\n"]
            for s in services:
                line = f"• *{s['name']}* — {s['price']} руб., {s['duration_minutes']} мин."
                lines.append(line)
                if s.get("description"):
                    lines.append(f"  _{s['description']}_")
            text = "\n".join(lines)
        else:
            text = "Список услуг временно недоступен."
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("handlers.services_error", error=str(e))
        await msg.reply_text("Не удалось загрузить услуги.")


async def _cb_process_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    msg: Message,
    user_id: int,
    text: str,
) -> None:
    """Обработать текст через LLM из callback (отвечает в msg)."""
    if not _llm_service or not _tts_service:
        await msg.reply_text("Сервис временно недоступен.")
        return

    await context.bot.send_chat_action(
        chat_id=msg.chat_id,
        action=ChatAction.TYPING,
    )

    tts_path: Optional[str] = None
    try:
        async with _openai_semaphore:
            llm_response = await _llm_service.process_message(
                user_id=user_id,
                user_text=text,
            )
            try:
                tts_path = await _tts_service.synthesize(llm_response.response_text)
                await _send_voice_response(msg, tts_path, llm_response.response_text)
            except TextToSpeechError:
                await msg.reply_text(llm_response.response_text)
    except Exception as e:
        logger.error("handlers.cb_text_error", error=str(e), exc_info=True)
        await msg.reply_text(
            "Произошла ошибка. Напишите мне текстом что хотите сделать."
        )
    finally:
        if tts_path:
            _safe_remove(tts_path)


# ─── Вспомогательные функции ──────────────────────────────────────────────────

async def _send_voice_response(
    message: Message,
    tts_path: str,
    text: str,
) -> None:
    """Отправить голосовой ответ + текстовая подпись."""
    with open(tts_path, "rb") as audio_file:
        await message.reply_voice(voice=audio_file, caption=text)


async def _reply_with_text_and_voice(message: Message, text: str) -> None:
    """Ответить текстом и попытаться добавить голос."""
    tts_path: Optional[str] = None
    try:
        if _tts_service:
            tts_path = await _tts_service.synthesize(text)
            await _send_voice_response(message, tts_path, text)
        else:
            await message.reply_text(text)
    except Exception:
        await message.reply_text(text)
    finally:
        if tts_path:
            _safe_remove(tts_path)


async def _send_voice_greeting(update: Update, text: str) -> None:
    """Отправить голосовое приветствие (некритично если не удалось)."""
    if not _tts_service:
        return
    tts_path: Optional[str] = None
    try:
        tts_path = await _tts_service.synthesize(text)
        with open(tts_path, "rb") as f:
            await update.message.reply_voice(voice=f)
    except Exception as e:
        logger.warning("handlers.greeting_voice_failed", error=str(e))
    finally:
        if tts_path:
            _safe_remove(tts_path)


async def _send_error_message(message: Message) -> None:
    """Сообщение об ошибке пользователю."""
    await message.reply_text(
        "Извините, произошла техническая ошибка. "
        "Попробуйте повторить запрос."
    )


def _safe_remove(path: str) -> None:
    """Безопасное удаление временного файла."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("handlers.cleanup_error", path=path, error=str(e))


def setup_handlers(app: Application) -> None:
    """Регистрация всех обработчиков."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("my_bookings", cmd_my_bookings))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    logger.info("handlers.registered")