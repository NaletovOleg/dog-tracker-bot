import logging
import sqlite3
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackContext,
)

# -------------------
# ЛОГИРОВАНИЕ
# -------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# -------------------
# КНОПКИ
# -------------------
MAIN_KB = ReplyKeyboardMarkup(
    [["График дня", "Регулярность"], ["Сбросить статистику"]], resize_keyboard=True
)

# -------------------
# БАЗА ДАННЫХ
# -------------------
DB_FILE = "dog_tracker.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS walks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts DATETIME NOT NULL
    )"""
    )
    conn.commit()
    conn.close()


init_db()

# -------------------
# УТИЛИТЫ
# -------------------
def emsg(update: Update):
    """Безопасно получить message (или None)."""
    return update.effective_message


def euser(update: Update):
    """Безопасно получить пользователя (или None)."""
    return update.effective_user


# -------------------
# КОМАНДЫ
# -------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m:
        return
    await m.reply_text(
        "Привет! 🐶 Бот работает.\n\n"
        "📊 «График дня» — минуты прогулок за сегодня.\n"
        "📈 «Регулярность» — среднее время и разброс за 14 дней.\n"
        "🗑 «Сбросить статистику» — очистка всех записей.",
        reply_markup=MAIN_KB,
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m:
        return
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM walks")
    conn.commit()
    conn.close()
    await m.reply_text("✅ Вся статистика обнулена.")


# -------------------
# ЛОГИКА ХЕНДЛЕРОВ
# -------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m or not m.text:
        return  # игнорируем апдейты без текста

    u = euser(update)
    user = (u.first_name or "").strip() if u else "user"

    text = m.text.strip()

    if text == "График дня":
        await show_today(m)
    elif text == "Регулярность":
        await show_regular(m)
    elif text == "Сбросить статистику":
        await cmd_reset(update, context)
    else:
        # любое сообщение = новая прогулка
        save_walk()
        await m.reply_text(f"Записал прогулку 🚶‍♂️ Спасибо, {user}!")


def save_walk():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO walks (ts) VALUES (?)", (datetime.now(),))
    conn.commit()
    conn.close()


async def show_today(m):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cur.execute("SELECT ts FROM walks WHERE ts >= ?", (start,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await m.reply_text("Сегодня ещё не гуляли 🐾")
        return

    times = [datetime.fromisoformat(r[0]) for r in rows]
    minutes = [t.hour * 60 + t.minute for t in times]
    minutes.sort()

    msg = "Сегодняшние прогулки:\n" + ", ".join(f"{m//60:02d}:{m%60:02d}" for m in minutes)
    await m.reply_text(msg)


async def show_regular(m):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    since = datetime.now() - timedelta(days=14)
    cur.execute("SELECT ts FROM walks WHERE ts >= ?", (since,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await m.reply_text("Недостаточно данных для анализа 📉")
        return

    times = [datetime.fromisoformat(r[0]) for r in rows]
    minutes = [t.hour * 60 + t.minute for t in times]

    avg = sum(minutes) / len(minutes)
    avg_h, avg_m = divmod(int(avg), 60)

    variance = sum((m - avg) ** 2 for m in minutes) / len(minutes)
    std = variance ** 0.5

    await m.reply_text(
        f"Регулярность прогулок за 14 дней:\n"
        f"Среднее время: {avg_h:02d}:{avg_m:02d}\n"
        f"Разброс: ±{int(std)} мин"
    )


# -------------------
# ОШИБКИ
# -------------------
async def on_error(update: object, context: CallbackContext):
    logging.exception("Unexpected error: %s", context.error)


# -------------------
# APP
# -------------------
def build_app() -> Application:
    app = Application.builder().token("YOUR_TELEGRAM_BOT_TOKEN").build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    return app


if __name__ == "__main__":
    build_app().run_polling()
