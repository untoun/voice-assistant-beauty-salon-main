"""
Конфигурация приложения через pydantic-settings.
Все параметры загружаются из .env файла с валидацией типов.
"""
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Типизированная конфигурация приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(..., description="Токен Telegram бота")

    # OpenAI
    openai_api_key: str = Field(..., description="API ключ OpenAI")

    # Google Sheets
    google_sheets_credentials_json: str = Field(
        default="credentials/service_account.json",
        description="Путь к JSON файлу service account",
    )
    google_sheet_id: str = Field(
        default="", description="ID Google таблицы"
    )

    # Бизнес-настройки
    salon_name: str = Field(
        default="Салон красоты 'Элегант'",
        description="Название салона",
    )
    working_hours_start: int = Field(
        default=9,
        ge=0,
        le=23,
        description="Начало рабочего дня (часы)",
    )
    working_hours_end: int = Field(
        default=20,
        ge=0,
        le=23,
        description="Конец рабочего дня (часы)",
    )
    slot_duration_minutes: int = Field(
        default=60,
        ge=15,
        description="Длительность слота в минутах",
    )
    timezone: str = Field(
        default="Europe/Moscow",
        description="Временная зона",
    )

    # Модели OpenAI
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="Основная LLM модель",
    )
    llm_model_complex: str = Field(
        default="gpt-4o",
        description="Модель для сложных случаев",
    )
    whisper_model: str = Field(
        default="whisper-1",
        description="Модель Whisper для STT",
    )
    tts_model: str = Field(
        default="tts-1",
        description="Модель TTS",
    )
    tts_voice: str = Field(
        default="nova",
        description="Голос TTS",
    )

    # Лимиты
    max_voice_duration_sec: int = Field(
        default=60,
        ge=5,
        description="Максимальная длительность голосового сообщения",
    )
    rate_limit_per_minute: int = Field(
        default=10,
        ge=1,
        description="Лимит сообщений в минуту от пользователя",
    )

    # Опциональные
    admin_telegram_id: int = Field(
        default=0,
        description="Telegram ID администратора для алертов",
    )

    # Контекст диалога
    dialog_max_messages: int = Field(
        default=20,
        description="Максимальное количество сообщений в истории диалога",
    )
    dialog_ttl_minutes: int = Field(
        default=30,
        description="TTL сессии диалога в минутах",
    )

    # Кэш
    services_cache_ttl_seconds: int = Field(
        default=3600,
        description="TTL кэша списка услуг в секундах",
    )

    # Yandex SpeechKit
    yandex_api_key: str = Field(default="", description="Яндекс SpeechKit API ключ")
    yandex_folder_id: str = Field(default="", description="Яндекс Cloud Folder ID")
    tts_provider: str = Field(default="openai", description="Провайдер TTS: openai или yandex")

    @field_validator("working_hours_end")
    @classmethod
    def validate_working_hours(cls, v: int, info) -> int:
        start = info.data.get("working_hours_start", 9)
        if v <= start:
            raise ValueError(
                f"working_hours_end ({v}) должен быть больше working_hours_start ({start})"
            )
        return v

    @property
    def working_hours_str(self) -> str:
        return f"{self.working_hours_start}:00 — {self.working_hours_end}:00"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Синглтон настроек приложения."""
    return Settings()


# Удобный алиас
settings = get_settings()