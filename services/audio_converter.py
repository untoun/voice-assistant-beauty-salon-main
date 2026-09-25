"""
Конвертация аудиоформатов для совместимости с Whisper API и Telegram.
Использует pydub + ffmpeg.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Форматы, принимаемые Whisper API
WHISPER_SUPPORTED_FORMATS = {
    "mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg"
}

# Максимальный размер файла Whisper (25 MB)
WHISPER_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024


class AudioConversionError(Exception):
    """Ошибка конвертации аудио."""
    pass


class AudioConverter:
    """Конвертация аудиоформатов."""

    def __init__(self) -> None:
        self._temp_files: list[str] = []

    async def ogg_to_wav(self, input_path: str) -> str:
        """
        Конвертация OGG → WAV.

        Args:
            input_path: Путь к входному OGG файлу

        Returns:
            Путь к выходному WAV файлу

        Raises:
            AudioConversionError: При ошибке конвертации
        """
        return await self._convert(input_path, "wav")

    async def ogg_to_mp3(self, input_path: str) -> str:
        """Конвертация OGG → MP3."""
        return await self._convert(input_path, "mp3")

    async def convert_for_whisper(self, input_path: str) -> str:
        """
        Конвертация аудио в формат, совместимый с Whisper API.

        Проверяет длительность и размер файла.
        Возвращает MP3 (оптимальный для Whisper).

        Args:
            input_path: Путь к входному аудиофайлу

        Returns:
            Путь к конвертированному файлу

        Raises:
            AudioConversionError: При нарушении ограничений или ошибке конвертации
        """
        import asyncio
        return await asyncio.to_thread(self._sync_convert_for_whisper, input_path)

    def _sync_convert_for_whisper(self, input_path: str) -> str:
        """Синхронная конвертация (для запуска в thread pool)."""
        path = Path(input_path)

        if not path.exists():
            raise AudioConversionError(f"Файл не найден: {input_path}")

        # Проверка размера до загрузки
        file_size = path.stat().st_size
        if file_size > WHISPER_MAX_FILE_SIZE_BYTES:
            raise AudioConversionError(
                f"Файл слишком большой: {file_size / 1024 / 1024:.1f} MB "
                f"(максимум 25 MB)"
            )

        # Загружаем аудио
        try:
            suffix = path.suffix.lower().lstrip(".")
            if suffix == "ogg":
                # pydub может иметь проблемы с opus-encoded ogg от Telegram
                audio = AudioSegment.from_ogg(str(path))
            elif suffix in WHISPER_SUPPORTED_FORMATS:
                audio = AudioSegment.from_file(str(path))
            else:
                raise AudioConversionError(f"Неподдерживаемый формат: {suffix}")
        except CouldntDecodeError as e:
            raise AudioConversionError(f"Не удалось декодировать аудио: {e}") from e

        # Проверка длительности
        duration_sec = len(audio) / 1000.0
        logger.debug(
            "audio_converter.duration_check",
            duration_sec=duration_sec,
            max_sec=settings.max_voice_duration_sec,
        )

        if duration_sec > settings.max_voice_duration_sec:
            raise AudioConversionError(
                f"Аудио слишком длинное: {duration_sec:.0f} сек "
                f"(максимум {settings.max_voice_duration_sec} сек)"
            )

        if duration_sec < 0.5:
            raise AudioConversionError("Аудио слишком короткое для распознавания")

        # Конвертируем в MP3
        output_path = self._make_temp_path(".mp3")
        audio.export(
            output_path,
            format="mp3",
            parameters=["-q:a", "2"],  # Хорошее качество
        )

        self._temp_files.append(output_path)
        logger.debug(
            "audio_converter.converted",
            input=str(path),
            output=output_path,
            duration_sec=duration_sec,
        )
        return output_path

    async def _convert(self, input_path: str, output_format: str) -> str:
        """Универсальная конвертация."""
        import asyncio
        return await asyncio.to_thread(
            self._sync_convert, input_path, output_format
        )

    def _sync_convert(self, input_path: str, output_format: str) -> str:
        """Синхронная конвертация в указанный формат."""
        try:
            audio = AudioSegment.from_file(input_path)
        except CouldntDecodeError as e:
            raise AudioConversionError(f"Не удалось декодировать: {e}") from e

        output_path = self._make_temp_path(f".{output_format}")
        audio.export(output_path, format=output_format)
        self._temp_files.append(output_path)
        return output_path

    def _make_temp_path(self, suffix: str) -> str:
        """Создать путь для временного файла."""
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="aibot_")
        os.close(fd)
        return path

    def cleanup(self, *specific_paths: str) -> None:
        """
        Удаление временных файлов.

        Args:
            specific_paths: Конкретные пути для удаления.
                           Если не указаны — удаляет все временные файлы объекта.
        """
        paths = list(specific_paths) if specific_paths else self._temp_files.copy()
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.debug("audio_converter.cleanup", path=path)
            except OSError as e:
                logger.warning("audio_converter.cleanup_error", path=path, error=str(e))

        if not specific_paths:
            self._temp_files.clear()