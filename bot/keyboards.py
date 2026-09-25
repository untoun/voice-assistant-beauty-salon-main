"""
Inline-клавиатуры для Telegram бота.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура приветственного сообщения."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Мои записи", callback_data="my_bookings")],
        [InlineKeyboardButton("💰 Услуги и цены", callback_data="services")],
        [InlineKeyboardButton("❌ Отменить запись", callback_data="cancel_booking")],
    ])


def get_confirm_booking_keyboard(booking_data: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения записи."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm:{booking_data}"),
            InlineKeyboardButton("❌ Отменить", callback_data="cancel_confirm"),
        ]
    ])


def get_cancel_confirm_keyboard(date: str, time: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения отмены."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Да, отменить",
                callback_data=f"do_cancel:{date}:{time}",
            ),
            InlineKeyboardButton("Нет, оставить", callback_data="keep_booking"),
        ]
    ])