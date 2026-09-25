# 🎤 AI Voice Secretary — Beauty Salon Telegram Bot

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![Telegram](https://img.shields.io/badge/Telegram-Bot-blue?logo=telegram)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-orange?logo=openai)
![Google Sheets](https://img.shields.io/badge/Google-Sheets-darkgreen?logo=googlesheets)
![Docker](https://img.shields.io/badge/Docker-Compose-blue?logo=docker)
![License](https://img.shields.io/badge/License-MIT-green)

> Голосовой AI-администратор для салона красоты — общение через голосовые сообщения,
> как с живым человеком. Запись, перенос, отмена — всё через Telegram.

[Возможности](#-возможности) • [Архитектура](#️-архитектура) • [Быстрый старт](#-быстрый-старт) • [Конфигурация](#️-конфигурация) • [Деплой](#️-деплой)

---

## 📋 Содержание

- [О проекте](#-о-проекте)
- [Возможности](#-возможности)
- [Архитектура](#️-архитектура)
- [Технологии](#️-технологии)
- [Структура проекта](#-структура-проекта)
- [Быстрый старт](#-быстрый-старт)
- [Установка](#-установка)
- [Настройка Google Sheets](#-настройка-google-sheets)
- [Конфигурация](#️-конфигурация)
- [Деплой](#️-деплой)
- [Сценарии использования](#-сценарии-использования)
- [Мониторинг](#-мониторинг)
- [Решение проблем](#-решение-проблем)
- [Дорожная карта](#️-дорожная-карта)

---

## 🎯 О проекте

**AI Voice Secretary** — интеллектуальный голосовой ассистент для автоматизации записи
клиентов в салон красоты. Бот принимает голосовые сообщения, распознаёт речь,
обрабатывает запрос через GPT-4o и отвечает голосом — полностью заменяя
живого администратора для рутинных задач.

### ✨ Ключевые преимущества

| | Преимущество | Описание |
|---|---|---|
| 🎤 | **Голосовое общение** | Клиент говорит — бот отвечает голосом |
| 🤖 | **GPT-4o + Function Calling** | Понимает контекст, вызывает нужные функции |
| 👥 | **Несколько специалистов** | Расписание ведётся отдельно для каждого мастера |
| 🔄 | **Перенос записей** | Атомарная операция с изменением статуса в таблице |
| 🛡️ | **Защита от двойного бронирования** | Lock + повторная проверка слота перед записью |
| 📊 | **Google Sheets как БД** | Простое управление расписанием без технических знаний |

---

## ✨ Возможности

- 🎤 **Голосовой ввод и вывод** — OGG → Whisper STT → GPT-4o → TTS → голос
- 📅 **Запись на услуги** — с проверкой свободных слотов у конкретного специалиста
- 🔍 **Умное уточнение услуги** — «маникюр» → уточняет: классический или аппаратный?
- 🔄 **Перенос записи** — старая запись получает статус «перенесено», создаётся новая
- ❌ **Отмена записи** — через голос или текст
- 📋 **История записей** — «покажи мои записи» или `/my_bookings`
- 💬 **Контекстный диалог** — бот помнит историю разговора (TTL 30 минут)
- 📞 **Нормализация телефонов** — принимает `89161234567`, `+79161234567`, `9161234567`
- 🔔 **Два TTS провайдера** — OpenAI Nova или Yandex SpeechKit

---

## 🏗️ Архитектура
```Bash
Голосовое сообщение (OGG)
↓
Скачивание (Telegram Bot API)
↓
Конвертация OGG → MP3 (pydub + ffmpeg)
↓
OpenAI Whisper → Распознанный текст
↓
GPT-4o / GPT-4o-mini + История диалога
↓ Function Calling
├── check_available_slots → Читает расписание из Google Sheets
├── create_booking → Создаёт запись в Google Sheets
├── transfer_booking → Переносит запись (статус «перенесено»)
├── cancel_booking → Отменяет запись (статус «отменено»)
├── get_client_bookings → Возвращает записи клиента
└── get_services_list → Возвращает прайс-лист
↓
Текстовый ответ
↓
OpenAI TTS / Yandex SpeechKit → Синтез речи
↓
Голосовое сообщение + текстовая подпись → Клиент
```


### Алгоритм расчёта слотов
```
Каждая запись блокирует диапазон: `[start, start + duration + 15 мин подготовки]`
Стрижка мужская (30 мин) в 10:00 → блокирует до 10:45
Следующая запись к Анне возможна не раньше 10:45
```
При **переносе** — старая запись клиента исключается из занятых диапазонов,
чтобы не блокировать выбор нового времени.

---

## 🛠️ Технологии

| Технология | Назначение | Версия |
|---|---|---|
| Python | Основной язык | 3.11+ |
| python-telegram-bot | Telegram Bot API | 20+ |
| OpenAI API | GPT-4o, Whisper, TTS | openai >= 1.35 |
| Yandex SpeechKit | Альтернативный TTS | — |
| Google Sheets API | Хранилище расписания | gspread |
| pydub + ffmpeg | Конвертация аудио | — |
| pydantic-settings | Конфигурация | 2.x |
| structlog | Структурированные логи | — |
| tenacity | Retry-логика | — |
| cachetools | TTL-кэш прайс-листа | — |
| Docker | Контейнеризация | — |

---

## 📁 Структура проекта
```Bash
AI Voice Secretary/
├── bot/
│ ├── handlers.py # Обработчики Telegram (голос, текст, кнопки)
│ ├── keyboards.py # Inline-кнопки
│ └── middleware.py # Rate limiting, логирование
├── models/
│ ├── booking.py # Модель бронирования + BookingResult
│ ├── client.py # Модель клиента
│ └── time_slot.py # Модели слота, услуги, специалиста
├── prompts/
│ ├── system_prompt.py # Системный промпт с актуальными датами
│ └── function_schemas.py # JSON-схемы инструментов для LLM
├── services/
│ ├── booking_service.py # Бизнес-логика записи и переноса
│ ├── calendar_service.py # Google Sheets: чтение/запись расписания
│ ├── llm_service.py # Диалоговый движок GPT-4o
│ ├── speech_to_text.py # Whisper STT
│ ├── text_to_speech.py # OpenAI TTS / Yandex SpeechKit
│ └── audio_converter.py # OGG → MP3 конвертация
├── utils/
│ ├── date_parser.py # Парсинг русских дат
│ ├── validators.py # Валидация телефонов, имён, времени
│ └── logger.py # Конфигурация structlog
├── tests/
│ ├── test_booking_service.py
│ ├── test_calendar_service.py
│ └── test_llm_service.py
├── credentials/
│ └── service_account.json # Google API ключ (не в git!)
├── main.py # Точка входа
├── config.py # Pydantic Settings
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```


---

## ⚡ Быстрый старт

```bash
# 1. Клонируйте репозиторий
git clone https://github.com/your-username/ai-voice-secretary.git
cd ai-voice-secretary

# 2. Установите зависимости
python -m venv venv
source venv/bin/activate   # macOS/Linux
# venv\Scripts\activate    # Windows
pip install -r requirements.txt

# 3. Настройте конфигурацию
cp .env.example .env
# Откройте .env и заполните переменные

# 4. Добавьте Google credentials
# Положите service_account.json в папку credentials/

# 5. Запустите
python main.py
````
## 💻 Установка
```Bash
Предварительные требования
Python 3.11+
ffmpeg
Telegram Bot Token (от @BotFather)
OpenAI API Key (platform.openai.com)
Google Service Account (console.cloud.google.com)
Установка ffmpeg
Bash

# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows — скачать с https://ffmpeg.org и добавить в PATH
```

## 📊 Настройка Google Sheets

1. Создайте проект в [Google Cloud Console](https://console.cloud.google.com)

2. Включите **Google Sheets API** и **Google Drive API**

3. Создайте **Service Account** → скачайте JSON ключ → положите в `credentials/service_account.json`

4. Создайте Google Таблицу с тремя листами:

<details>
<summary><b>📋 Лист «Расписание»</b> — создаётся автоматически при первом запуске</summary>

Заголовки первой строки:

| Дата | Время | Услуга | Клиент | Телефон | Статус | Telegram ID | Создано | Примечания | Длительность | Специалист | ID специалиста |
|---|---|---|---|---|---|---|---|---|---|---|---|

Поле **Статус** принимает значения:
- `подтверждено` — активная запись
- `отменено` — клиент отменил
- `перенесено` — запись перенесена (в поле «Примечания» указано куда)

</details>

<details>
<summary><b>📋 Лист «Услуги»</b> — заполните вручную</summary>

| Название | Длительность (мин) | Цена | Описание | Специалист | ID специалиста |
|---|---|---|---|---|---|
| Стрижка женская | 120 | 6200 | Создание образа | Анна | 1 |
| Стрижка мужская | 30 | 1500 | Создание образа | Анна | 1 |
| Маникюр классический | 120 | 6100 | Уход с покрытием | Мария | 2 |
| Маникюр аппаратный | 60 | 3000 | Аппаратный уход | Мария | 2 |
| Макияж дневной | 150 | 7700 | Дневной образ | Ольга | 3 |
| Макияж вечерний | 150 | 7300 | Вечерний образ | Ольга | 3 |

> ⚠️ **ID специалиста** — числовой идентификатор. Все услуги одного мастера должны иметь одинаковый ID. Это ключевое поле для разделения расписания между специалистами.

</details>

<details>
<summary><b>📋 Лист «Клиенты»</b> — создаётся автоматически</summary>

| Telegram ID | Имя | Телефон | Кол-во визитов | Последний визит |
|---|---|---|---|---|

</details>

5. **Поделитесь таблицей** с `client_email` из `service_account.json` → дайте права **Редактора**

6. Скопируйте **ID таблицы** из URL (часть между `/d/` и `/edit`) → вставьте в `GOOGLE_SHEET_ID`
## ⚙️ Конфигурация

### Основные настройки (`.env`)

| Переменная | Описание | Обязательно | Пример / По умолчанию |
|---|---|---|---|
| **Telegram** | | | |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота от @BotFather | ✅ | `8214874151:AAHMNO...` |
| `ADMIN_TELEGRAM_ID` | ID администратора для уведомлений | ❌ | `0` |
| | | | |
| **OpenAI API** | | | |
| `OPENAI_API_KEY` | Ключ OpenAI API (STT, LLM, TTS) | ✅ | `sk-proj-xYWCKN520...` |
| `LLM_MODEL` | Модель для простых запросов | ❌ | `gpt-4o-mini` |
| `LLM_MODEL_COMPLEX` | Модель для сложных запросов (перенос, конфликты) | ❌ | `gpt-4o` |
| `WHISPER_MODEL` | Модель распознавания речи | ❌ | `whisper-1` |
| `TTS_MODEL` | Модель синтеза речи OpenAI | ❌ | `tts-1` |
| `TTS_VOICE` | Голос OpenAI TTS | ❌ | `nova` |
| | | | |
| **Google Sheets** | | | |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Путь к JSON ключу сервисного аккаунта | ✅ | `credentials/service_account.json` |
| `GOOGLE_SHEET_ID` | ID Google таблицы из URL | ✅ | `1vO5gV0gf-xlBHJg...` |
| | | | |
| **Yandex SpeechKit** | | | |
| `TTS_PROVIDER` | Провайдер голоса: `openai` или `yandex` | ❌ | `openai` |
| `YANDEX_API_KEY` | API ключ Yandex SpeechKit | ⚠️ | `AQVN0sr4LS...` |
| `YANDEX_FOLDER_ID` | Folder ID в Yandex Cloud | ⚠️ | `b1ga2jtpd...` |
| | | | |
| **Бизнес-настройки** | | | |
| `SALON_NAME` | Название салона | ❌ | `Салон красоты 'Элегант'` |
| `WORKING_HOURS_START` | Начало рабочего дня (час) | ❌ | `9` |
| `WORKING_HOURS_END` | Конец рабочего дня (час) | ❌ | `20` |
| `SLOT_DURATION_MINUTES` | Длительность слота по умолчанию (мин) | ❌ | `60` |
| `TIMEZONE` | Временная зона | ❌ | `Europe/Moscow` |
| | | | |
| **Лимиты и безопасность** | | | |
| `MAX_VOICE_DURATION_SEC` | Макс. длительность голосового (сек) | ❌ | `60` |
| `RATE_LIMIT_PER_MINUTE` | Лимит сообщений в минуту | ❌ | `10` |
| | | | |
| **Настройки диалога** | | | |
| `DIALOG_MAX_MESSAGES` | Глубина истории диалога | ❌ | `20` |
| `DIALOG_TTL_MINUTES` | Время жизни сессии (мин) | ❌ | `30` |
| | | | |
| **Кэширование** | | | |
| `SERVICES_CACHE_TTL_SECONDS` | TTL кэша прайс-листа (сек) | ❌ | `3600` |

### Условные обозначения

| Значок | Статус | Пояснение |
|---|---|---|
| ✅ | **Обязательно** | Без этого параметра бот не запустится |
| ⚠️ | **Условно** | Обязательно только при `TTS_PROVIDER=yandex` |
| ❌ | **Опционально** | Используется значение по умолчанию |

## ☁️ Деплой
<details> <summary><b>Docker (рекомендуется)</b></summary>

### Запуск
```Bash
docker-compose up -d
```
### Логи
```Bash
docker-compose logs -f bot
```
### Только ошибки
```Bash
docker-compose logs -f bot | grep "error"
```

</details><details> <summary><b>VPS (Ubuntu/Debian)</b></summary>

### 1. Подготовка сервера
```Bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv git ffmpeg -y
```
### 2. Клонирование
```Bash
cd /var/www
git clone https://github.com/your-username/ai-voice-secretary.git
cd ai-voice-secretary
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
### 3. Создайте systemd службу
```Bash
sudo nano /etc/systemd/system/ai-secretary.service
ini

[Unit]
Description=AI Voice Secretary Bot
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/ai-voice-secretary
Environment="PATH=/var/www/ai-voice-secretary/venv/bin"
ExecStart=/var/www/ai-voice-secretary/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
Bash
```
### 4. Запуск
```Bash
sudo systemctl daemon-reload
sudo systemctl enable ai-secretary
sudo systemctl start ai-secretary
sudo systemctl status ai-secretary
```
</details>

## 💬 Сценарии использования
```
Запись:

«Хочу на маникюр послезавтра вечером»
→ Уточняет: классический или аппаратный?
→ Предлагает слоты у Марии ближайшие к вечеру
→ Спрашивает имя и телефон → создаёт запись

Перенос:

«Хочу перенести стрижку с 26-го на попозже»
→ Находит запись по Telegram ID
→ Показывает свободные слоты
→ Меняет статус на «перенесено», создаёт новую запись

Проверка расписания:

«Когда есть свободное время у Ольги в пятницу?»

Отмена:

«Хочу отменить запись на 25-е в 10 утра»

Информация:

«Сколько стоит ламинирование ресниц?»
```
## 📊 Мониторинг
```Bash

# Локально
python main.py

# Docker
docker-compose logs -f bot

# Только ошибки
docker-compose logs -f bot | grep "error"

# По пользователю
docker-compose logs -f bot | grep "user_id=12345"
Ключевые события в логах:

Событие	Значение
booking_service.booking_success	Запись создана
booking_service.transfer_success	Запись перенесена
calendar_service.booking_cancelled	Запись отменена
llm_service.PHANTOM_BOOKING_DETECTED	LLM пытается подтвердить несуществующую запись
calendar_service.append_row_error 403	Нет прав на запись в Sheets
```
## 🔧 Решение проблем

<details> <summary><b>❌ Бот не отвечает на голосовые сообщения</b></summary>

```bash
# Проверьте токен
curl https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe

# Проверьте наличие ffmpeg
ffmpeg -version

# Проверьте логи на ошибки STT
python main.py 2>&1 | grep -i "stt\|whisper\|error"
```

</details><details> <summary><b>❌ Ошибка Google Sheets 403</b></summary>

```
# Убедитесь что таблица расшарена с email из service_account.json
# Поле: client_email

# Проверьте что включены оба API:
# - Google Sheets API
# - Google Drive API
```
</details><details> <summary><b>❌ LLM предлагает неверные слоты</b></summary>

```
Убедитесь что в function_schemas.py нет дублирующихся функций
с одинаковым именем — это путает модель.
```

</details><details> <summary><b>❌ Бот говорит «занято» для времени клиента при переносе</b></summary>

```
Используйте transfer_booking вместо cancel_booking + create_booking.
transfer_booking автоматически исключает старую запись клиента
из занятых диапазонов при проверке нового слота.
```
</details>

## 🗺️ Дорожная карта
```
Версия 1.0 (Текущая)
  ✅ Голосовой пайплайн (STT → LLM → TTS)
  ✅ Запись с проверкой свободных слотов
  ✅ Поддержка нескольких специалистов
  ✅ Атомарный перенос записей
  ✅ Отмена записей
  ✅ Защита от двойного бронирования
  ✅ Два TTS провайдера (OpenAI / Yandex)
  ✅ Docker-деплой

Версия 1.1 (Планируется)
  ⬜ Напоминания клиентам за 24 часа до записи
  ⬜ Панель администратора (веб-интерфейс)
  ⬜ Статистика и аналитика по записям
  ⬜ Экспорт расписания в Excel
  ⬜ Система лояльности (скидки постоянным клиентам)

Версия 2.0 (Будущее)
  ⬜ PostgreSQL вместо Google Sheets
  ⬜ Веб-API для интеграции с сайтом салона
  ⬜ Интеграция с Instagram / WhatsApp
  ⬜ Онлайн-оплата через ЮKassa
  ⬜ Мобильное приложение для администратора
```
## 🛡️ Безопасность
```
🔐 Все секреты в .env — не попадают в git
🗑️ Голосовые файлы удаляются сразу после обработки
⏱️ Rate limiting: 10 сообщений/минуту
🔒 asyncio.Lock защита от двойного бронирования
✅ Повторная проверка слота в момент записи
📞 Нормализация телефонов: 89161234567 → +79161234567
🚫 Phantom Booking Detection — бот не подтвердит несуществующую запись
```
## 📄 Лицензия
MIT License — свободное использование, модификация и распространение.

## 🚀 Тесты
```Bash

pytest tests/ -v
pytest tests/ -v --tb=short    # Краткий вывод ошибок
pytest tests/test_booking_service.py -v  # Конкретный модуль
