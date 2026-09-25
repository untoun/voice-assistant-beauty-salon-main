# test_config.py
from config import settings
import os

print("=== ПРОВЕРКА КОНФИГУРАЦИИ ===")
print(f"Токен Telegram: {settings.telegram_bot_token[:10]}...")
print(f"OpenAI Key: {settings.openai_api_key[:10]}...")
print(f"Google Sheet ID: {settings.google_sheet_id}")
print(f"Credentials файл: {settings.google_sheets_credentials_json}")
print(f"Файл существует: {os.path.exists(settings.google_sheets_credentials_json)}")
print(f"Салон: {settings.salon_name}")
print(f"Часы работы: {settings.working_hours_start}:00 - {settings.working_hours_end}:00")