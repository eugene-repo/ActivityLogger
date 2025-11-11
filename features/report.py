import os
from datetime import datetime
from zoneinfo import ZoneInfo
# Важно: импортируем сам класс OpenAI
from openai import OpenAI

TZ = ZoneInfo("Europe/Warsaw")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROMPT_GPT = os.getenv("PROMPT_GPT")

def generate_daily_report_with_gpt(sheet):
    """
    Отправляет все данные из Google Sheets в GPT для анализа.
    Возвращает текст с сегодняшними активностями и оценкой эффективности.
    """
    try:
        # --- Создаём локальный клиент ---
        # Это ключ к исправлению.
        # Клиент создается "свежий" для каждого запроса.
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        records = sheet.get_all_records()
        if not records:
            return "📭 Таблица пуста, нет данных для анализа."

        today_str = datetime.now(TZ).strftime("%Y-%m-%d")

        # --- Составляем текст сегодняшних активностей ---
        today_activities = "📅 Активности на сегодня:\n"
        has_today = False
        for r in records:
            date_val = str(r.get("Date Activity", "")).strip()
            activity = str(r.get("Activity", "")).strip()
            duration = str(r.get("Duration", "")).strip()
            if date_val == today_str:
                has_today = True
                today_activities += f"{activity} — {duration}\n"

        if not has_today:
            today_activities += "Нет активностей за сегодня.\n"

        # --- Формируем текст для GPT (анализ всей таблицы) ---
        table_text = "Date Activity\tActivity\tDuration\n"
        for r in records:
            date_val = str(r.get("Date Activity", "")).strip()
            activity = str(r.get("Activity", "")).strip()
            duration = str(r.get("Duration", "")).strip()
            table_text += f"{date_val}\t{activity}\t{duration}\n"

        prompt = f"{PROMPT_GPT}\n\n{table_text}"
        
        # --- Используем локальный клиент 'client' ---
        response = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": prompt}],
        )
        print("✅ ОТВЕТ ОТ GPT:")
        print(response)
        answer = response.choices[0].message.content.strip()
              
        # --- Итоговое сообщение ---
        report_text = (
            f"{today_activities}\n"
            f"📋 Обзор:\n{answer}"
        )

        return report_text

    except Exception as e:
        # Добавим traceback для лучшей диагностики, если ошибка останется
        import traceback
        logging.error(f"⚠️ Ошибка при обращении к GPT: {e}\n{traceback.format_exc()}")
        return f"⚠️ Ошибка при обращении к GPT: {e}"
