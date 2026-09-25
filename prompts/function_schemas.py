TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_available_slots",
            "description": (
                "Проверить доступные слоты специалиста на дату. "
                "Система автоматически определит специалиста по услуге. "
                "ВАЖНО: если клиент назвал время — ВСЕГДА передавай preferred_time. "
                "Без preferred_time возвращаются первые слоты дня, "
                "а не ближайшие к запрошенному времени!"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "description": (
                            "Дата в формате YYYY-MM-DD. "
                            "Конвертируй: 'завтра', '26 февраля', 'в пятницу'."
                        ),
                    },
                    "service": {
                        "type": "string",
                        "description": (
                            "Точное название услуги из прайс-листа. "
                            "Например: 'Макияж дневной', 'Маникюр классический'."
                        ),
                    },
                    "preferred_time": {
                        "type": "string",
                        "pattern": r"^\d{2}:\d{2}$",
                        "description": (
                            "Предпочтительное время в формате HH:MM. "
                            "ОБЯЗАТЕЛЬНО передавай если клиент назвал время! "
                            "Примеры: клиент сказал 'в 11' → '11:00', "
                            "'после обеда' → '13:00', 'вечером' → '17:00', "
                            "'утром' → '09:00'. "
                            "Без этого параметра система предложит начало дня."
                        ),
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": (
                "Создать запись клиента. "
                "Вызывай ТОЛЬКО после: "
                "1) уточнена точная услуга, "
                "2) клиент подтвердил время из предложенных слотов, "
                "3) получено имя клиента, "
                "4) получен телефон (для новых клиентов)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {
                        "type": "string",
                        "description": "Имя клиента",
                    },
                    "date": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "description": "Дата записи YYYY-MM-DD",
                    },
                    "time": {
                        "type": "string",
                        "pattern": r"^\d{2}:\d{2}$",
                        "description": "Время записи HH:MM (24-часовой формат)",
                    },
                    "service": {
                        "type": "string",
                        "description": (
                            "Точное название услуги из прайс-листа. "
                            "Например: 'Макияж дневной', а не просто 'макияж'."
                        ),
                    },
                    "client_phone": {
                        "type": "string",
                        "description": (
                            "Телефон в формате +7XXXXXXXXXX. "
                            "Обязателен для новых клиентов."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": "Дополнительные пожелания",
                    },
                },
                "required": ["client_name", "date", "time", "service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Отменить существующую запись клиента.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {
                        "type": "string",
                        "description": "Имя клиента",
                    },
                    "date": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "description": "Дата записи YYYY-MM-DD",
                    },
                    "time": {
                        "type": "string",
                        "pattern": r"^\d{2}:\d{2}$",
                        "description": "Время записи HH:MM",
                    },
                },
                "required": ["client_name", "date", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_services_list",
            "description": (
                "Получить список услуг с ценами. "
                "Вызывай если клиент спрашивает об услугах или ценах."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_booking",
            "description": (
                "Перенести существующую запись клиента на другое время или дату. "
                "ИСПОЛЬЗУЙ ТОЛЬКО ЭТОТ ИНСТРУМЕНТ при переносе — "
                "НЕ вызывай cancel_booking + create_booking по отдельности. "
                "Слова-триггеры: 'перенести', 'сдвинуть', 'изменить время', "
                "'перезаписаться', 'можно на другое время', 'хочу позже/раньше'. "
                "Система сама найдёт запись по telegram_id и дате."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_date": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "description": (
                            "Дата ТЕКУЩЕЙ записи YYYY-MM-DD. "
                            "Если клиент сказал '26 числа' → '2026-02-26'."
                        ),
                    },
                    "old_time": {
                        "type": "string",
                        "pattern": r"^\d{2}:\d{2}$",
                        "description": (
                            "Время ТЕКУЩЕЙ записи HH:MM. "
                            "Необязательно — система найдёт по дате автоматически."
                        ),
                    },
                    "new_date": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "description": (
                            "Дата НОВОЙ записи YYYY-MM-DD. "
                            "Если тот же день — повтори old_date."
                        ),
                    },
                    "new_time": {
                        "type": "string",
                        "pattern": r"^\d{2}:\d{2}$",
                        "description": "Время НОВОЙ записи HH:MM.",
                    },
                    "service": {
                        "type": "string",
                        "description": (
                            "Название услуги (если клиент назвал). "
                            "Необязательно — система найдёт автоматически."
                        ),
                    },
                    "client_name": {
                        "type": "string",
                        "description": "Имя клиента (если известно из диалога).",
                    },
                },
                "required": ["old_date", "new_date", "new_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_client_bookings",
            "description": (
                "Получить записи клиента. "
                "Вызывай если клиент хочет посмотреть или отменить запись."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

]