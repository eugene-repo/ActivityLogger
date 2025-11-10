import openai
from datetime import datetime
from zoneinfo import ZoneInfo
import os




print("OpenAI version:", openai.__version__)
print("OpenAI file:", openai.__file__)

# ⚡ Ваш API ключ
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TZ = ZoneInfo("Europe/Warsaw")

async def generate_daily_report_with_gpt_async(sheet=None):
    """
    Отправляем тестовое сообщение в GPT и возвращаем ответ.
    В ответе сразу видно, какой запрос улетает в GPT.
    sheet нужен для совместимости с bot.py.
    """
    try:
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

        # --- Текст запроса для GPT ---
        prompt = "Привет, ChatGPT! Это тестовое сообщение для дебага отчёта."

        # --- Отправляем запрос к GPT ---
        print("🚀 Отправляем запрос в GPT...")
        print(f"🔹 Время: {now_str}")
        print(f"🔹 Текст: {prompt}")

        response = openai.chat.completions.create(
            model="gpt-4o-mini",  # можно заменить на gpt-3.5-turbo, если хочешь
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
        )

        # --- Обработка ответа ---
        answer = response.choices[0].message.content.strip()

        report_text = (
            f"📋 DEBUG INFO:\n"
            f"Текущее время: {now_str}\n"
            f"Текст запроса в GPT: '{prompt}'\n\n"
            f"✅ Ответ GPT:\n{answer}"
        )

        return report_text

    except Exception as e:
        import traceback
        tb = traceback.format_exc()

        error_text = (
            f"⚠️ Ошибка при обращении к GPT:\n{e}\n\n"
            f"📜 Traceback:\n{tb}"
        )

        # Печатаем в консоль и возвращаем в Telegram
        print(error_text)
        return error_text[:4000]  # чтобы Telegram не обрезал сообщение
