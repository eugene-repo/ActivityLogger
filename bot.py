from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import sys
import os
import asyncio
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
from features.report import generate_daily_report_with_gpt
import traceback
from apscheduler.schedulers.background import BackgroundScheduler


# -----------------------------
# Fix for asyncio event loop in Flask/WSGI environment
# -----------------------------
try:
    nest_asyncio.apply()
    logging.info("✅ nest_asyncio applied to fix event loop issues")
except Exception as e:
    logging.warning(f"⚠️ Could not apply nest_asyncio: {e}")

# -----------------------------
# TIME & CONFIG
# -----------------------------
TZ = ZoneInfo("Europe/Warsaw")

def get_local_now():
    """Вспомогательная функция для получения текущего времени в нужном поясе."""
    return datetime.now(TZ)

def parse_time(text):
    """Разбирает строку времени (напр. '14:30') и возвращает datetime с учётом TZ."""
    try:
        now = get_local_now()
        parsed_time = datetime.strptime(text, "%H:%M").time()
        return datetime.combine(now.date(), parsed_time, tzinfo=TZ)
    except Exception:
        return None

def parse_start_time_from_cells(date_str, time_str):
    """
    Создаёт "осознанный" datetime (tz-aware) из date_str и time_str,
    учитывая возможные форматы времени, которые возвращает Google Sheets:
      - "YYYY-MM-DD" + "HH:MM" или "HH:MM:SS"
      - иногда time_str может быть числом (дробь дня), например 0.6875
    Возвращает datetime с tzinfo=TZ. В случае ошибки — логируем и возвращаем get_local_now().
    """
    try:
        # Защита от None и лишних пробелов
        date_raw = (date_str or "").strip()
        time_raw = (str(time_str) if time_str is not None else "").strip()

        # Парсим дату
        # Если date_raw пустой — используем сегодняшнюю дату (дальше будет лог)
        if date_raw == "":
            logging.warning("parse_start_time_from_cells: empty date_str, using today")
            date_obj = get_local_now().date()
        else:
            # Попробуем ожидаемый формат YYYY-MM-DD
            try:
                date_obj = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except Exception:
                # Иногда Google Sheets возвращает дату в другом виде — попробуем fromisoformat
                try:
                    date_obj = datetime.fromisoformat(date_raw).date()
                except Exception:
                    logging.warning(f"parse_start_time_from_cells: can't parse date '{date_raw}', using today")
                    date_obj = get_local_now().date()

        # Парсим время
        time_obj = None
        if time_raw == "":
            logging.warning("parse_start_time_from_cells: empty time_str, using current time")
            return get_local_now()

        # Если это число (напр. '0.6875'), попробуем привести к float и конвертировать в часы/минуты
        try:
            if "." in time_raw or time_raw.isdigit():
                maybe_float = float(time_raw)
                if 0.0 <= maybe_float < 1.0:
                    total_minutes = int(maybe_float * 24 * 60 + 0.5)
                    hours = total_minutes // 60
                    minutes = total_minutes % 60
                    time_obj = datetime.time(datetime(year=1, month=1, day=1, hour=hours, minute=minutes))
        except Exception:
            # не фатально, продолжим к строковым парсерам
            time_obj = None

        # Если не numeric, попробуем форматы "HH:MM" или "HH:MM:SS"
        if time_obj is None:
            for fmt in ("%H:%M", "%H:%M:%S"):
                try:
                    parsed = datetime.strptime(time_raw, fmt).time()
                    time_obj = parsed
                    break
                except Exception:
                    continue

        if time_obj is None:
            logging.warning(f"parse_start_time_from_cells: can't parse time '{time_raw}' — using current time")
            return get_local_now()

        # Собираем окончательный datetime с TZ
        dt = datetime.combine(date_obj, time_obj, tzinfo=TZ)
        return dt

    except Exception as e:
        logging.error(f"parse_start_time_from_cells: unexpected error: {e}")
        return get_local_now()

def format_duration(minutes):
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours} h {mins} min" if mins > 0 else f"{hours} h"

# -----------------------------
# Flask app setup
# -----------------------------
app = Flask(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logging.info("✅ Flask app initialized")

# -----------------------------
# Telegram token
# -----------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise ValueError("⚠️ TELEGRAM_BOT_TOKEN is not set in environment variables")

# -----------------------------
# Google Sheets setup
# -----------------------------
"""
# --- Вариант через локальный файл (закомментирован) ---
try:
    logging.info("📄 Setting up Google Sheets connection...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open("TelegramMessages").sheet1
    logging.info("✅ Connected to Google Sheets successfully")
except Exception as e:
    logging.error(f"❌ Failed to connect Google Sheets: {e}")
    sheet = None
"""
try:
    logging.info("📄 Setting up Google Sheets connection...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # Загружаем credentials из переменной окружения
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json_str:
        raise ValueError("⚠️ Не найдена переменная окружения GOOGLE_CREDENTIALS_JSON")


    # Парсим JSON
    creds_dict = json.loads(creds_json_str)

    # Создаём объект ServiceAccountCredentials
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("TelegramMessages").sheet1
    logging.info("✅ Connected to Google Sheets successfully")
except Exception as e:
    logging.error(f"❌ Failed to connect Google Sheets: {e}")
    sheet = None
    

# -----------------------------
# Telegram bot setup
# -----------------------------
try:
    app_telegram = ApplicationBuilder().token(TOKEN).build()
    logging.info("✅ Telegram bot created (ApplicationBuilder)")
    asyncio.run(app_telegram.initialize())
    logging.info("✅ Telegram Application initialized successfully")
except Exception as e:
    logging.error(f"❌ Telegram bot init error: {e}")

# -----------------------------
# Планировщик ежедневного репорта
# -----------------------------
def schedule_daily_report():
    try:
        def send_fake_report():
            try:
                fake_update = {
                    "update_id": 999999999,
                    "message": {
                        "message_id": 1,
                        "from": {
                            "id": 884672440,
                            "is_bot": False,
                            "first_name": "Eugene",
                            "username": "JskSrm",
                            "language_code": "en"
                        },
                        "chat": {
                            "id": 884672440,
                            "first_name": "Eugene",
                            "username": "JskSrm",
                            "type": "private"
                        },
                        "date": int(datetime.now().timestamp()),
                        "text": "репорт"
                    }
                }

                update = Update.de_json(fake_update, app_telegram.bot)
                asyncio.run(app_telegram.process_update(update))
                logging.info("✅ Ежедневный репорт отправлен")
            except Exception as e:
                logging.error(f"❌ Ошибка при отправке ежедневного репорта: {e}")

        scheduler = BackgroundScheduler(timezone="Europe/Warsaw")
        scheduler.add_job(send_fake_report, 'cron', hour=14, minute=20)
        scheduler.start()
        logging.info("✅ Планировщик ежедневного репорта запущен")
    except Exception as e:
        logging.error(f"❌ Ошибка при инициализации планировщика: {e}")

# Запуск планировщика
schedule_daily_report()

# -----------------------------
# Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"💬 /start from {update.effective_user.id}")
    await update.message.reply_text("Привет! Я бот, и я умею записывать твои активности.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global creds # Нам нужны глобальные creds, которые мы загрузили
    
    if not creds:
        logging.error("❌ Message skipped: Google Credentials not loaded.")
        await update.message.reply_text("⚠️ Ошибка подключения к Google Sheets. Учетные данные не загружены.")
        return

    raw_text = update.message.text.strip()
    text = raw_text.lower().strip()
    logging.info(f"📩 Received message: '{text}'")


    # --- Отчёт за день с анализом GPT ---
    if text.lower() in ["репорт", "Репорт", "report"]:
        error_text = ""
        try:
            # --- СОЗДАЁМ ЛОКАЛЬНЫЙ SHEET ЗДЕСЬ ---
            client = gspread.authorize(creds)
            sheet = client.open("TelegramMessages").sheet1
            
            # Асинхронно вызываем GPT
            analysis = generate_daily_report_with_gpt(sheet)
            await update.message.reply_text(analysis)

        except Exception as e:
            error_text = f"❌ Ошибка при генерации отчёта:\n\n{str(e)}\n\n{traceback.format_exc()}"
            try:
                await update.message.reply_text(error_text[:4000])
            except Exception as inner_e:
                print("Ошибка при отправке лога в Telegram:", inner_e)
        return

    try:
        # --- СОЗДАЁМ ЛОКАЛЬНЫЙ SHEET ЗДЕСЬ (ДЛЯ ВСЕХ ОСТАЛЬНЫХ КОМАНД) ---
        client = gspread.authorize(creds)
        sheet = client.open("TelegramMessages").sheet1

        # Проверяем незакрытую активность
        records = sheet.get_all_records()
        open_record = None
        for idx in range(len(records) - 1, -1, -1):
            rec = records[idx]
            if rec.get("End Time", "").strip() == "":
                open_record = (idx, rec)
                break

        # --- Команды окончания ---
        if text.startswith(("end", "stop", "стоп", "конец", "finish")):
            if not open_record:
                await update.message.reply_text("⚠️ Нет открытых активностей для завершения.")
                return

            parts = raw_text.split()
            custom_end = None
            if len(parts) >= 2:
                custom_end = parse_time(parts[-1])  # возвращает datetime или None

            idx, rec = open_record
            start_dt = parse_start_time_from_cells(rec.get("Date", ""), rec.get("Start Time", ""))
            end_dt = custom_end if custom_end else get_local_now()

            if custom_end:
                end_dt = datetime.combine(start_dt.date(), custom_end.time(), tzinfo=TZ)

            duration_min = int((end_dt - start_dt).total_seconds() / 60)
            duration_str = format_duration(duration_min)
            row_number = idx + 2  # индекс + заголовок

            sheet.update_cell(row_number, 4, end_dt.strftime("%H:%M:%S"))
            sheet.update_cell(row_number, 5, duration_str)
            logging.info(f"✅ Ended '{rec.get('Activity')}' ({duration_str})")
            await update.message.reply_text(f"✅ Ended '{rec.get('Activity')}' ({duration_str})")
            return

        # --- Новая активность ---
        if open_record:
            await update.message.reply_text(
                f"⚠️ Сначала завершите предыдущую активность '{open_record[1]['Activity']}'!"
            )
            return

        parts = raw_text.split()
        if len(parts) == 0:
            await update.message.reply_text("⚠️ Пустое сообщение.")
            return

        # Пробуем распознать последнее слово как время
        custom_start = parse_time(parts[-1])
        if custom_start:
            activity = " ".join(parts[:-1]).strip().capitalize()
        else:
            activity = " ".join(parts).strip().capitalize()

        # Время старта
        start_dt = custom_start if custom_start else get_local_now()

        # Сохраняем в таблицу
        sheet.append_row([
            start_dt.strftime("%Y-%m-%d"),
            activity,
            start_dt.strftime("%H:%M:%S"),
            "",
            ""
        ])

        logging.info(f"🏁 Started '{activity}' at {start_dt.strftime('%H:%M')}")
        await update.message.reply_text(f"🏁 Started '{activity}' at {start_dt.strftime('%H:%M')}")

    except Exception as e:
        logging.error(f"❌ Error in echo handler: {e}\n{traceback.format_exc()}")
        await update.message.reply_text("⚠️ Произошла ошибка, проверьте логи.")

# Регистрируем обработчики
if 'app_telegram' in locals():
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

# -----------------------------
# Flask routes
# -----------------------------
@app.route("/")
def index():
    logging.info("📡 Flask route / called — app is alive")
    return "✅ Telegram bot Flask app is running!"

@app.route(f"/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        logging.info("📨 Webhook POST received from Telegram")
         # 👇 добавляем вывод всего тела запроса
        logging.info(f"📦 Full Telegram update:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        update = Update.de_json(data, app_telegram.bot)
        asyncio.run(app_telegram.process_update(update))
        logging.info("✅ Telegram update processed successfully")
        return "ok"
    except Exception as e:
        logging.error(f"❌ Webhook processing error: {e}")
        return "error", 500


# 🔹 GET эндпоинт для отправки сообщений в обработку
@app.route("/send_message", methods=["GET"])
def send_message():
    try:
        text = request.args.get("text", "").strip()
        if not text:
            return "❌ Добавь параметр ?text=твой_текст", 400

        # Собираем искусственный update в Telegram-формате
        fake_update = {
            "update_id": 999999999,
            "message": {
                "message_id": 1,
                "from": {
                    "id": 884672440,
                    "is_bot": False,
                    "first_name": "Eugene",
                    "username": "JskSrm",
                    "language_code": "en"
                },
                "chat": {
                    "id": 884672440,
                    "first_name": "Eugene",
                    "username": "JskSrm",
                    "type": "private"
                },
                "date": int(datetime.now().timestamp()),
                "text": text
            }
        }

        # Превращаем в объект Update и обрабатываем
        update = Update.de_json(fake_update, app_telegram.bot)
        asyncio.run(app_telegram.process_update(update))

        logging.info(f"🧪 Тестовое сообщение '{text}' отправлено в обработку")
        return f"✅ Сообщение '{text}' отправлено в обработку", 200

    except Exception as e:
        logging.error(f"❌ Ошибка в send_message: {e}")
        return f"Ошибка: {e}", 500

# -----------------------------
# WSGI entry point
# -----------------------------
application = app

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()  # нужен для asyncio внутри Flask
    app.run(host="0.0.0.0", port=10000, debug=True)
