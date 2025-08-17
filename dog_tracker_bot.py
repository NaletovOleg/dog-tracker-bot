import os
import logging
import sqlite3
import threading
from datetime import datetime, timedelta

from flask import Flask
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackContext,
    filters,
)

# ── конфиг ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ЗАДАТЬ в Render → Environment
DB_FILE = os.getenv("DB_PATH", "dog_tracker.db")  # на Free переживает рестарт контейнера, но не redeploy

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)

# ── клавиатура ────────────────────────────────────────────────────────────────
BTN_DAY   = "График дня 📅"
BTN_REG   = "Регулярность ⏱️"
BTN_RESET = "Сбросить статистику 🧹"

MAIN_KB = ReplyKeyboardMarkup(
    [[BTN_DAY, BTN_REG], [BTN_RESET]], resize_keyboard=True
)

# ── БД ────────────────────────────────────────────────────────────────────────
def db_exec(sql, params=()):
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur
    finally:
        conn.close()

def init_db():
    db_exec("""CREATE TABLE IF NOT EXISTS walks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL
    )""")

def save_walk(ts: datetime | None = None):
    ts = ts or datetime.now()
    db_exec("INSERT INTO walks(ts) VALUES (?)", (ts.isoformat(timespec="seconds"),))

def get_today():
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cur = db_exec("SELECT ts FROM walks WHERE ts >= ?", (start.isoformat(),))
    return [datetime.fromisoformat(r[0]) for r in cur.fetchall()]

def get_last_14d():
    since = datetime.now() - timedelta(days=14)
    cur = db_exec("SELECT ts FROM walks WHERE ts >= ?", (since.isoformat(),))
    return [datetime.fromisoformat(r[0]) for r in cur.fetchall()]

def reset_all():
    db_exec("DELETE FROM walks")

init_db()

# ── утилиты для безопасного доступа к update ─────────────────────────────────
def emsg(update: Update):
    return update.effective_message

def euser(update: Update):
    return update.effective_user

# ── команды/хендлеры ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m:
        return
    await m.reply_text(
        "Привет! 🐶\n"
        "• «График дня» — покажу времена прогулок за сегодня.\n"
        "• «Регулярность» — среднее время и разброс за 14 дней.\n"
        "• «Сбросить статистику» — очистить все записи.",
        reply_markup=MAIN_KB,
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m or not m.text:
        return
    text = m.text.strip()

    if text == BTN_DAY:
        times = get_today()
        if not times:
            await m.reply_text("Сегодня ещё не гуляли 🐾")
            return
        minutes = sorted([t.hour * 60 + t.minute for t in times])
        pretty = ", ".join(f"{mm//60:02d}:{mm%60:02d}" for mm in minutes)
        await m.reply_text(f"Сегодняшние прогулки:\n{pretty}")
        return

    if text == BTN_REG:
        times = get_last_14d()
        if not times:
            await m.reply_text("Недостаточно данных за 14 дней 📉")
            return
        mins = [t.hour * 60 + t.minute for t in times]
        avg = sum(mins) / len(mins)
        var = sum((x - avg) ** 2 for x in mins) / len(mins)
        std = int(var ** 0.5)
        h, mm = divmod(int(avg + 0.5), 60)
        await m.reply_text(f"Регулярность 14д:\nСреднее время: {h:02d}:{mm:02d}\nРазброс: ±{std} мин")
        return

    if text == BTN_RESET:
        reset_all()
        await m.reply_text("🧹 Готово. Все записи удалены.")
        return

    # любое другое сообщение — фиксируем прогулку
    save_walk()
    u = euser(update)
    name = (u.first_name or "user") if u else "user"
    await m.reply_text(f"Записал прогулку 🚶‍♂️ Спасибо, {name}!")

# ── обработка ошибок ─────────────────────────────────────────────────────────
async def on_error(update: object, context: CallbackContext):
    logging.exception("Unhandled error: %s", context.error)

# ── Telegram Application ─────────────────────────────────────────────────────
def build_tg_app() -> Application:
    if not BOT_TOKEN:
        raise SystemExit("❗ Переменная окружения BOT_TOKEN не задана.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)
    return app

# ── Flask-заглушка для Render Free (HTTP-порт) ───────────────────────────────
flask_app = Flask(__name__)

@flask_app.get("/")
def root():
    return "Dog tracker bot is running ✅"

@flask_app.get("/healthz")
def healthz():
    return "ok"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)

# ── точка входа ───────────────────────────────────────────────────────────────
def main():
    # 1) Flask в фоне, чтобы Render держал сервис живым
    threading.Thread(target=run_flask, daemon=True).start()
    # 2) Telegram-бот в главном потоке
    tg_app = build_tg_app()
    tg_app.run_polling()

if __name__ == "__main__":
    main()
