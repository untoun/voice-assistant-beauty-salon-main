#!/usr/bin/env python3
"""
Скрипт для автоматического создания структуры папок проекта AI-Секретарь.
Запустить: python create_project_structure.py
"""

import os
import sys
from pathlib import Path

# Цвета для вывода
GREEN = '\033[92m'
BLUE = '\033[94m'
RED = '\033[91m'
RESET = '\033[0m'

PROJECT_STRUCTURE = {
    # Корневые файлы
    "main.py": """#!/usr/bin/env python3
\"\"\"
Точка входа приложения.
Инициализация и запуск Telegram бота.
\"\"\"

def main():
    print("Бот запущен")

if __name__ == "__main__":
    main()
""",
    "config.py": """\"\"\"
Конфигурация приложения через pydantic-settings.
\"\"\"
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    \"\"\"Типизированная конфигурация приложения.\"\"\"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = "your_token_here"

    # OpenAI
    openai_api_key: str = "your_key_here"

    # Google Sheets
    google_sheets_credentials_json: str = "credentials/service_account.json"
    google_sheet_id: str = ""

    # Бизнес-настройки
    salon_name: str = "Салон красоты 'Элегант'"
    working_hours_start: int = 9
    working_hours_end: int = 20
    slot_duration_minutes: int = 60
    timezone: str = "Europe/Moscow"

    # Модели OpenAI
    llm_model: str = "gpt-4o-mini"
    llm_model_complex: str = "gpt-4o"
    whisper_model: str = "whisper-1"
    tts_model: str = "tts-1"
    tts_voice: str = "nova"

    # Лимиты
    max_voice_duration_sec: int = 60
    rate_limit_per_minute: int = 10

    # Опциональные
    admin_telegram_id: int = 0

    # Контекст диалога
    dialog_max_messages: int = 20
    dialog_ttl_minutes: int = 30

    # Кэш
    services_cache_ttl_seconds: int = 3600


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    \"\"\"Синглтон настроек приложения.\"\"\"
    return Settings()


settings = get_settings()
""",
    "requirements.txt": """python-telegram-bot>=20.7
openai>=1.12.0
gspread>=6.0.0
google-auth>=2.25.0
pydub>=0.25.1
pydantic>=2.5.0
pydantic-settings>=2.1.0
python-dotenv>=1.0.0
aiofiles>=23.2.1
cachetools>=5.3.2
tenacity>=8.2.3
structlog>=23.2.0
pytest>=7.4.0
pytest-asyncio>=0.23.0
pytest-mock>=3.12.0
""",
    ".env.example": """# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# OpenAI
OPENAI_API_KEY=sk-your_openai_api_key_here

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS_JSON=credentials/service_account.json
GOOGLE_SHEET_ID=your_google_sheet_id_here

# Salon settings
SALON_NAME=Салон красоты 'Элегант'
WORKING_HOURS_START=9
WORKING_HOURS_END=20
SLOT_DURATION_MINUTES=60
TIMEZONE=Europe/Moscow

# AI Models
LLM_MODEL=gpt-4o-mini
LLM_MODEL_COMPLEX=gpt-4o
WHISPER_MODEL=whisper-1
TTS_MODEL=tts-1
TTS_VOICE=nova

# Limits
MAX_VOICE_DURATION_SEC=60
RATE_LIMIT_PER_MINUTE=10

# Admin (optional)
ADMIN_TELEGRAM_ID=0
""",
    "Dockerfile": """FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \\
    ffmpeg \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p credentials logs

RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python", "main.py"]
""",
    "docker-compose.yml": """version: '3.9'

services:
  bot:
    build: .
    restart: unless-stopped
    environment:
      - ENVIRONMENT=production
      - LOG_LEVEL=INFO
    env_file:
      - .env
    volumes:
      - ./credentials:/app/credentials:ro
      - ./logs:/app/logs
    tmpfs:
      - /tmp:size=100m
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'
""",
    "pytest.ini": """[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
log_cli = true
log_cli_level = WARNING
""",
    "README.md": """# 🎤 AI-Секретарь для Салона Красоты

Голосовой Telegram-бот для записи клиентов.
""",

    # Папки с __init__.py
    "bot/__init__.py": "",
    "services/__init__.py": "",
    "models/__init__.py": "",
    "prompts/__init__.py": "",
    "utils/__init__.py": "",
    "tests/__init__.py": "",

    # Файлы в папках
    "bot/handlers.py": "\"\"\"Обработчики сообщений.\"\"\"\n",
    "bot/middleware.py": "\"\"\"Мидлвари.\"\"\"\n",
    "bot/keyboards.py": "\"\"\"Inline-клавиатуры.\"\"\"\n",

    "services/audio_converter.py": "\"\"\"Конвертация аудио.\"\"\"\n",
    "services/speech_to_text.py": "\"\"\"Whisper STT.\"\"\"\n",
    "services/text_to_speech.py": "\"\"\"OpenAI TTS.\"\"\"\n",
    "services/llm_service.py": "\"\"\"GPT диалог.\"\"\"\n",
    "services/calendar_service.py": "\"\"\"Google Sheets.\"\"\"\n",
    "services/booking_service.py": "\"\"\"Бизнес-логика.\"\"\"\n",

    "models/booking.py": "\"\"\"Модели записей.\"\"\"\n",
    "models/client.py": "\"\"\"Модели клиентов.\"\"\"\n",
    "models/time_slot.py": "\"\"\"Модели слотов.\"\"\"\n",

    "prompts/system_prompt.py": "\"\"\"Системный промпт.\"\"\"\n",
    "prompts/function_schemas.py": "\"\"\"Схемы функций.\"\"\"\n",

    "utils/logger.py": "\"\"\"Логирование.\"\"\"\n",
    "utils/date_parser.py": "\"\"\"Парсинг дат.\"\"\"\n",
    "utils/validators.py": "\"\"\"Валидаторы.\"\"\"\n",

    "tests/test_booking_service.py": "\"\"\"Тесты бронирования.\"\"\"\n",
    "tests/test_llm_service.py": "\"\"\"Тесты LLM.\"\"\"\n",
    "tests/test_calendar_service.py": "\"\"\"Тесты календаря.\"\"\"\n",
}


def create_file(path: str, content: str = "") -> None:
    """Создать файл с содержимым."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"{GREEN}✓{RESET} Создан файл: {BLUE}{path}{RESET}")
    except Exception as e:
        print(f"{RED}✗{RESET} Ошибка создания {path}: {e}")


def create_directory(path: str) -> None:
    """Создать директорию."""
    Path(path).mkdir(parents=True, exist_ok=True)
    print(f"{GREEN}✓{RESET} Создана папка: {BLUE}{path}/{RESET}")


def create_project_structure(base_path: str = ".") -> None:
    """Создать структуру проекта."""
    print(f"\n{BLUE}🚀 Создание структуры проекта AI-Секретарь{BLUE}\n")
    print("=" * 50)

    # Создаём корневые файлы
    for file_path, content in PROJECT_STRUCTURE.items():
        full_path = os.path.join(base_path, file_path)

        # Создаём родительскую директорию если нужно
        parent = os.path.dirname(full_path)
        if parent and parent != base_path:
            Path(parent).mkdir(parents=True, exist_ok=True)

        # Создаём файл
        create_file(full_path, content)

    # Создаём специальные папки
    special_dirs = ["credentials", "logs"]
    for dir_name in special_dirs:
        create_directory(os.path.join(base_path, dir_name))

    # Создаём .gitkeep в пустых папках
    keep_dirs = ["credentials", "logs"]
    for dir_name in keep_dirs:
        keep_file = os.path.join(base_path, dir_name, ".gitkeep")
        create_file(keep_file, "")

    print("\n" + "=" * 50)
    print(f"{GREEN}✅ Структура проекта успешно создана!{RESET}")
    print(f"\n{BLUE}📁 Корневая папка: {base_path}{RESET}")
    print(f"\n{BLUE}Далее:{RESET}")
    print("1. Скопируйте service account JSON в credentials/")
    print("2. Создайте .env из .env.example и заполните переменные")
    print("3. Установите зависимости: pip install -r requirements.txt")
    print("4. Запустите проект: python main.py\n")


def main():
    """Основная функция."""
    # Получаем путь к проекту
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
    else:
        project_path = input(f"{BLUE}Введите путь для создания проекта (Enter - текущая папка): {RESET}").strip()
        if not project_path:
            project_path = ".."

    # Спрашиваем подтверждение
    print(f"\n{BLUE}Проект будет создан в:{RESET} {os.path.abspath(project_path)}")
    confirm = input(f"{BLUE}Продолжить? (y/n): {RESET}").strip().lower()

    if confirm == 'y':
        create_project_structure(project_path)
    else:
        print(f"{RED}❌ Операция отменена{RESET}")


if __name__ == "__main__":
    main()