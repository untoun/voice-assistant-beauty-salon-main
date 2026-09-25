FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание директорий
RUN mkdir -p credentials logs

# Непривилегированный пользователь
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Запуск
CMD ["python", "main.py"]