# check_sheets.py
import gspread
from google.oauth2.service_account import Credentials
from config import settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(
    settings.google_sheets_credentials_json,
    scopes=SCOPES,
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(settings.google_sheet_id)

required = ["Расписание", "Услуги", "Клиенты"]
existing = [ws.title for ws in sh.worksheets()]

print("=" * 50)
print(f"Таблица: {sh.title}")
print("=" * 50)

print("\nПроверка листов:")
all_ok = True
for name in required:
    status = "OK" if name in existing else "ОТСУТСТВУЕТ"
    print(f"  [{status}] Лист '{name}'")
    if name not in existing:
        all_ok = False

print(f"\nВсе существующие листы: {existing}")

print("\n" + "=" * 50)
print("Проверка услуг:")
try:
    ws = sh.worksheet("Услуги")
    rows = ws.get_all_values()
    print(f"Строк найдено: {len(rows)} (включая заголовок)")
    print(f"Услуг: {len(rows) - 1}")
    for i, row in enumerate(rows):
        print(f"  Строка {i+1}: {row}")
except Exception as e:
    print(f"Ошибка: {e}")

print("\n" + "=" * 50)
print("Проверка расписания:")
try:
    ws = sh.worksheet("Расписание")
    rows = ws.get_all_values()
    print(f"Строк найдено: {len(rows)} (включая заголовок)")
    for i, row in enumerate(rows[:5]):
        print(f"  Строка {i+1}: {row}")
    if len(rows) > 5:
        print(f"  ... и ещё {len(rows) - 5} строк")
except Exception as e:
    print(f"Ошибка: {e}")

print("\n" + "=" * 50)
if all_ok:
    print("Все листы найдены. Можно запускать бота!")
else:
    print("ВНИМАНИЕ: Некоторые листы отсутствуют!")
    print("Создайте их вручную в Google Sheets.")