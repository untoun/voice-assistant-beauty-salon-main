"""
Сервис распознавания речи через OpenAI Whisper API.
"""
from __future__ import annotations

import time
from pathlib import Path

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


class SpeechToTextError(Exception):
    """Ошибка распознавания речи."""
    pass


class EmptyTranscriptionError(SpeechToTextError):
    """Пустой результат распознавания."""
    pass


class SpeechToTextService:
    """Сервис транскрипции голосовых сообщений через Whisper."""

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client or AsyncOpenAI(api_key=settings.openai_api_key)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def transcribe(self, audio_file_path: str) -> str:
        """
        Транскрипция аудиофайла через Whisper API.

        Args:
            audio_file_path: Путь к аудиофайлу (.ogg, .mp3, .wav и др.)

        Returns:
            Распознанный текст

        Raises:
            SpeechToTextError: При ошибке API или пустом результате
            EmptyTranscriptionError: Если речь не была распознана
        """
        path = Path(audio_file_path)
        if not path.exists():
            raise SpeechToTextError(f"Аудиофайл не найден: {audio_file_path}")

        start_time = time.monotonic()

        try:
            with open(audio_file_path, "rb") as audio_file:
                logger.debug(
                    "stt.whisper_request",
                    model=settings.whisper_model,
                    file_size_kb=path.stat().st_size // 1024,
                )

                response = await self._client.audio.transcriptions.create(
                    model=settings.whisper_model,
                    file=audio_file,
                    language="ru",
                    response_format="text",
                )

        except Exception as e:
            logger.error("stt.whisper_error", error=str(e), error_type=type(e).__name__)
            raise SpeechToTextError(f"Ошибка Whisper API: {e}") from e

        elapsed = time.monotonic() - start_time

        # Whisper с response_format="text" возвращает строку напрямую
        text = str(response).strip() if response else ""

        if not text:
            raise EmptyTranscriptionError(
                "Whisper не смог распознать речь в аудиофайле"
            )

        logger.info(
            "stt.transcription_complete",
            elapsed_sec=round(elapsed, 2),
            text_length=len(text),
            # Не логируем сам текст для приватности
        )

        return text