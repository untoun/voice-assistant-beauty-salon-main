"""
Сервис синтеза речи.
Поддерживает OpenAI TTS и Яндекс SpeechKit (голос Marina, нейтральное амплуа).
"""
from __future__ import annotations

import os
import tempfile
import time
from abc import ABC, abstractmethod

import aiohttp
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

TTS_MAX_TEXT_LENGTH = 4096

class TextToSpeechError(Exception):
    pass

# ─── Абстрактный провайдер ────────────────────────────────────────────────────

class BaseTTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> str:
        """Синтезировать речь, вернуть путь к .ogg файлу."""

# ─── OpenAI TTS ───────────────────────────────────────────────────────────────

class OpenAITTSProvider(BaseTTSProvider):
    """Провайдер OpenAI TTS."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def synthesize(self, text: str) -> str:
        if not text.strip():
            raise TextToSpeechError("Пустой текст")

        if len(text) > TTS_MAX_TEXT_LENGTH:
            text = text[:TTS_MAX_TEXT_LENGTH]

        fd, output_path = tempfile.mkstemp(suffix=".ogg", prefix="tts_")
        os.close(fd)

        start = time.monotonic()
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model=settings.tts_model,
                voice=settings.tts_voice,  # type: ignore
                input=text,
                response_format="opus",
                speed=1.0,
            ) as response:
                await response.stream_to_file(output_path)
        except Exception as e:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise TextToSpeechError(f"OpenAI TTS ошибка: {e}") from e

        elapsed = time.monotonic() - start
        logger.info(
            "tts.synthesis_complete",
            provider="openai",
            elapsed_sec=round(elapsed, 2),
            file_size_kb=os.path.getsize(output_path) // 1024,
        )
        return output_path

# ─── Яндекс SpeechKit TTS (REST API v1) ─────────────────────────────────────

class YandexTTSProvider(BaseTTSProvider):
    """
    Провайдер Яндекс SpeechKit.
    Голос: marina (нейтральное амплуа).
    Документация: https://cloud.yandex.ru/docs/speechkit/tts/request
    """
    YANDEX_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"

    def __init__(self) -> None:
        if not settings.yandex_api_key:
            raise TextToSpeechError(
                "YANDEX_API_KEY не задан в .env"
            )
        if not settings.yandex_folder_id:
            raise TextToSpeechError(
                "YANDEX_FOLDER_ID не задан в .env"
            )

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError, aiohttp.ClientError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def synthesize(self, text: str) -> str:
        """
        Синтез через Яндекс SpeechKit REST API v1.
        Голос: marina, амплуа: neutral (по умолчанию), формат: oggopus.
        """
        if not text.strip():
            raise TextToSpeechError("Пустой текст")

        # Яндекс ограничивает до 5000 символов
        if len(text) > 5000:
            text = text[:5000]

        fd, output_path = tempfile.mkstemp(suffix=".ogg", prefix="yatts_")
        os.close(fd)

        headers = {
            "Authorization": f"Api-Key {settings.yandex_api_key}",
        }

        # Параметры синтеза (только поддерживаемые в REST API v1)
        data = {
            "text": text,
            "lang": "ru-RU",
            "voice": "marina",           # Голос Marina (нейтральный по умолчанию)
            "speed": 1.0,              # Скорость (число, не строка)
            "format": "oggopus",         # Формат для Telegram
            "sampleRateHertz": 48000, # Частота дискретизации
            "folderId": settings.yandex_folder_id,
        }

        start = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.YANDEX_TTS_URL,
                    headers=headers,
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:

                    if response.status != 200:
                        error_body = await response.text()
                        raise TextToSpeechError(
                            f"Яндекс SpeechKit ошибка {response.status}: {error_body}"
                        )

                    audio_data = await response.read()

                    with open(output_path, "wb") as f:
                        f.write(audio_data)

        except TextToSpeechError:
            raise
        except Exception as e:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise TextToSpeechError(f"Яндекс TTS ошибка: {e}") from e

        elapsed = time.monotonic() - start
        file_size = os.path.getsize(output_path)

        if file_size == 0:
            os.remove(output_path)
            raise TextToSpeechError("Яндекс TTS вернул пустой файл")

        logger.info(
            "tts.synthesis_complete",
            provider="yandex",
            voice="marina",
            elapsed_sec=round(elapsed, 2),
            file_size_kb=file_size // 1024,
        )
        return output_path
# ─── Фасад ────────────────────────────────────────────────────────────────────

class TextToSpeechService:
    """
    Фасад для TTS провайдеров.
    Выбирает провайдер на основе TTS_PROVIDER в .env.
    При ошибке основного — fallback на OpenAI.
    """

    def __init__(self) -> None:
        self._primary = self._create_provider(settings.tts_provider)
        # Fallback всегда OpenAI
        self._fallback: BaseTTSProvider = OpenAITTSProvider()

    def _create_provider(self, name: str) -> BaseTTSProvider:
        if name.lower() == "yandex":
            try:
                provider = YandexTTSProvider()
                logger.info("tts.provider_selected", provider="yandex", voice="marina")
                return provider
            except TextToSpeechError as e:
                logger.warning(
                    "tts.yandex_unavailable",
                    error=str(e),
                    fallback="openai",
                )
                return OpenAITTSProvider()
        else:
            logger.info(
                "tts.provider_selected",
                provider="openai",
                voice=settings.tts_voice,
            )
            return OpenAITTSProvider()

    async def synthesize(self, text: str) -> str:
        """Синтез речи с автоматическим fallback."""
        try:
            return await self._primary.synthesize(text)
        except TextToSpeechError as e:
            logger.error(
                "tts.primary_failed",
                error=str(e),
                trying_fallback=True,
            )
            # Fallback на OpenAI если основной — Яндекс
            if not isinstance(self._primary, OpenAITTSProvider):
                try:
                    return await self._fallback.synthesize(text)
                except TextToSpeechError as fe:
                    raise TextToSpeechError(
                        f"Оба TTS провайдера недоступны: {fe}"
                    ) from fe
            raise e