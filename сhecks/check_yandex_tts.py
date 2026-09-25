"""
Диагностика подключения к Яндекс SpeechKit v3.
Запуск: python check_yandex_tts.py
"""
import asyncio
import base64
import json
import os

import aiohttp
from config import settings


async def test_yandex_tts():
    print("=" * 50)
    print("Диагностика Яндекс SpeechKit v3")
    print("=" * 50)
    print(f"API Key: {'*' * 20}{settings.yandex_api_key[-6:] if settings.yandex_api_key else 'НЕ ЗАДАН'}")
    print(f"Folder ID: {settings.yandex_folder_id or 'НЕ ЗАДАН'}")
    print()

    if not settings.yandex_api_key or not settings.yandex_folder_id:
        print("ОШИБКА: Заполните YANDEX_API_KEY и YANDEX_FOLDER_ID в .env")
        return

    url = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
    headers = {
        "Authorization": f"Api-Key {settings.yandex_api_key}",
        "Content-Type": "application/json",
        "x-folder-id": settings.yandex_folder_id,
    }
    body = {
        "text": "Привет! Я виртуальный администратор салона красоты.",
        "hints": [
            {"voice": "lera"},
            {"role": "friendly"},
            {"speed": 1.0},
        ],
        "outputAudioSpec": {
            "containerAudio": {
                "containerAudioType": "OGG_OPUS"
            }
        },
        "folderId": settings.yandex_folder_id,
    }

    print(f"URL: {url}")
    print(f"Тело запроса: {json.dumps(body, ensure_ascii=False, indent=2)}")
    print()

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers=headers,
            json=body,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            status = response.status
            text = await response.text()
            print(f"HTTP статус: {status}")
            print(f"Ответ (первые 500 символов): {text[:500]}")
            print()

            if status == 200:
                # Парсим чанки
                chunks = []
                for line in text.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        b64 = (
                            data.get("result", {})
                            .get("audioChunk", {})
                            .get("data", "")
                        )
                        if b64:
                            chunks.append(base64.b64decode(b64))
                    except Exception as e:
                        print(f"  Ошибка парсинга чанка: {e}")

                if chunks:
                    # Сохраняем тестовый файл
                    test_file = "test_yandex_output.ogg"
                    with open(test_file, "wb") as f:
                        for chunk in chunks:
                            f.write(chunk)
                    size = os.path.getsize(test_file)
                    print(f"УСПЕХ! Аудио сохранено: {test_file} ({size} байт)")
                    print("Воспроизведите файл для проверки качества голоса.")
                else:
                    print("ОШИБКА: Аудио чанки не найдены в ответе")
            else:
                print(f"ОШИБКА: HTTP {status}")
                try:
                    error_data = json.loads(text)
                    print(f"Код ошибки: {error_data.get('error_code', 'N/A')}")
                    print(f"Сообщение: {error_data.get('error_message', 'N/A')}")
                except Exception:
                    print(f"Сырой ответ: {text}")

    print()
    print("Доступные голоса Яндекс SpeechKit:")
    print("  alena   — нейтральный женский")
    print("  lera    — дружелюбный женский (требует API v3)")
    print("  marina  — нейтральный женский")
    print("  alexander — мужской")
    print()
    print("Доступные амплуа для голоса lera:")
    print("  friendly  — дружелюбный")
    print("  strict    — строгий")


if __name__ == "__main__":
    asyncio.run(test_yandex_tts())