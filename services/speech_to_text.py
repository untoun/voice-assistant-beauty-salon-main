"""
Сервис распознавания речи.
Поддерживает Yandex SpeechKit STT.
"""
from __future__ import annotations

import time
from pathlib import Path

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class SpeechToTextError(Exception):
    """Ошибка распознавания речи."""
    pass


class EmptyTranscriptionError(SpeechToTextError):
    """Пустой результат распознавания."""
    pass


class YandexSTTProvider:
    """Провайдер распознавания речи от Yandex SpeechKit."""
    YANDEX_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"

    def __init__(self) -> None:
        if not settings.yandex_api_key:
            raise SpeechToTextError("YANDEX_API_KEY не задан в .env")
        if not settings.yandex_folder_id:
            raise SpeechToTextError("YANDEX_FOLDER_ID не задан в .env")

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError, aiohttp.ClientError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def transcribe(self, audio_file_path: str) -> str:
        """
        Распознавание аудиофайла через Yandex SpeechKit REST API.

        Args:
            audio_file_path: Путь к аудиофайлу (.ogg, .mp3, .wav и др.)

        Returns:
            Распознанный текст

        Raises:
            SpeechToTextError: При ошибке API
            EmptyTranscriptionError: Если речь не была распознана
        """
        path = Path(audio_file_path)
        if not path.exists():
            raise SpeechToTextError(f"Аудиофайл не найден: {audio_file_path}")

        headers = {
            "Authorization": f"Api-Key {settings.yandex_api_key}",
        }

        params = {
            "lang": "ru-RU",
            "folderId": settings.yandex_folder_id,
        }

        start_time = time.monotonic()

        try:
            with open(audio_file_path, "rb") as audio_file:
                logger.debug(
                    "stt.yandex_request",
                    file_size_kb=path.stat().st_size // 1024,
                )

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.YANDEX_STT_URL,
                        headers=headers,
                        params=params,
                        data=audio_file,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as response:
                        if response.status != 200:
                            error_body = await response.text()
                            raise SpeechToTextError(
                                f"Yandex STT ошибка {response.status}: {error_body}"
                            )

                        result = await response.json()
                        text = str(result.get("result", "")).strip()

                        if not text:
                            raise EmptyTranscriptionError(
                                "Yandex STT не смог распознать речь в аудиофайле"
                            )

        except (SpeechToTextError, EmptyTranscriptionError):
            raise
        except Exception as e:
            logger.error("stt.yandex_error", error=str(e), error_type=type(e).__name__)
            raise SpeechToTextError(f"Ошибка Yandex STT: {e}") from e

        elapsed = time.monotonic() - start_time

        logger.info(
            "stt.transcription_complete",
            elapsed_sec=round(elapsed, 2),
            text_length=len(text),
            provider="yandex",
        )

        return text


class SpeechToTextService:
    """Сервис транскрипции голосовых сообщений через Yandex SpeechKit."""

    def __init__(self) -> None:
        self._provider = YandexSTTProvider()

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def transcribe(self, audio_file_path: str) -> str:
        """
        Транскрипция аудиофайла через Yandex SpeechKit.

        Args:
            audio_file_path: Путь к аудиофайлу (.ogg, .mp3, .wav и др.)

        Returns:
            Распознанный текст

        Raises:
            SpeechToTextError: При ошибке API или пустом результате
            EmptyTranscriptionError: Если речь не была распознана
        """
        return await self._provider.transcribe(audio_file_path)
