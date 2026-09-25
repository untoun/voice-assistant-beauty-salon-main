# quick_check.py
print("🔍 Быстрая проверка импортов...")

try:
    from config import settings
    print("✅ config.py - OK")
    print(f"   Бот: {settings.salon_name}")
    print(f"   Токен: {settings.telegram_bot_token[:10]}...")
except Exception as e:
    print(f"❌ config.py: {e}")

try:
    from services import (
        AudioConverter,
        SpeechToTextService,
        TextToSpeechService,
        LLMService,
        CalendarService,
        BookingService
    )
    print("✅ services - OK")
except Exception as e:
    print(f"❌ services: {e}")

try:
    from models import Booking, Client, TimeSlot
    print("✅ models - OK")
except Exception as e:
    print(f"❌ models: {e}")

try:
    from bot.handlers import setup_handlers
    print("✅ bot.handlers - OK")
except Exception as e:
    print(f"❌ bot.handlers: {e}")

print("\n🚀 Если все ✅ - можно запускать бота!")