# dog_tracker_bot.py
import os
import csv
import math
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import List, Tuple

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

# ──────────────────────────────────────────────────────────────────────────────
# Конфиг
# ──────────────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ОБЯЗАТЕЛЬНО задать в Render → Environment
DB_FILE = os.getenv("DB_PATH", "dog_tracker.db")  # на Free переживает рестарт, но стирается на redeploy

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)

# ──────────────────────────────────────────────────────────────────────────────
# Кнопки
# ──────────────────────────────────────────────────────────────────────────────
BTN_WALK_START = "Прогулка 🐾"
BTN_WALK_END   = "Прогулка завершена ⏱"
BTN_PEE        = "Лужа 🚰"
BTN_POO        = "Кучка 💩"
BTN_PEE_HOME   = "Лужа дома 🚨"
BTN_POO_HOME   = "Кучка дома 🚨"
BTN_FEED       = "Покормил 🍖"
BTN_DAY        = "График дня 📅"
BTN_REG        = "Регулярность ⏱️"
BTN_EXPORT     = "Экспорт CSV 📤"
BTN_RESET      = "Сбросить статистику 🧹"
BTN_CLOSE      = "❌ Закрыть бот"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [BTN_WALK_START, BTN_WALK_END],
        [BTN_PEE, BTN_POO],
        [BTN_PEE_HOME, BTN_POO_HOME],
        [BTN_FEED],
        [BTN_DAY, BTN_REG],
        [BTN_EXPORT, BTN_RESET],
        [BTN_CLOSE],
    ],
    resize_keyboard=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# База данных (SQLite)
# ──────────────────────────────────────────────────────────────────────────────
def db_conn():
    return sqlite3.connect(DB_FILE)

def init_db():
    with db_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events(
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                ts   TEXT    NOT NULL,
                type TEXT    NOT NULL,
                user TEXT    NOT NULL
            )"""
        )

def log_event(ev_type: str, user: str):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO events(ts, type, user) VALUES (?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), ev_type, user),
        )

def load_today() -> list[tuple[datetime, str, str]]:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with db_conn() as conn:
        cur = conn.execute(
            "SELECT ts, type, user FROM events WHERE ts >= ? ORDER BY ts ASC",
            (start.isoformat(),),
        )
        rows = [(datetime.fromisoformat(ts), t, u) for ts, t, u in cur.fetchall()]
    return rows

def load_last_days(days: int) -> list[tuple[datetime, str, str]]:
    since = datetime.now() - timedelta(days=days)
    with db_conn() as conn:
        cur = conn.execute(
            "SELECT ts, type, user FROM events WHERE ts >= ? ORDER BY ts ASC",
            (since.isoformat(),),
        )
        rows = [(datetime.fromisoformat(ts), t, u) for ts, t, u in cur.fetchall()]
    return rows

def reset_all():
    with db_conn() as conn:
        conn.execute("DELETE FROM events")

init_db()

# ──────────────────────────────────────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────────────────────────────────────
def emsg(update: Update):
    return update.effective_message

def euser(update: Update):
    return update.effective_user

def pair_walks(events_today: list[tuple[datetime, str, str]]) -> List[Tuple[datetime, datetime]]:
    """Сопоставляет пары старт/финиш прогулок по порядку."""
    starts = [ts for ts, tp, _ in events_today if tp == BTN_WALK_START]
    ends   = [ts for ts, tp, _ in events_today if tp == BTN_WALK_END]
    pairs = []
    i = j = 0
    while i < len(starts) and j < len(ends):
        if ends[j] > starts[i]:
            pairs.append((starts[i], ends[j]))
            i += 1; j += 1
        else:
            j += 1
    return pairs

def timestr_list(dts: List[datetime]) -> str:
    if not dts:
        return "—"
    return ", ".join(dt.strftime("%H:%M") for dt in dts)

def mean_std_minutes(times: List[datetime.time]) -> tuple[str, int, int]:
    """Среднее время HH:MM и σ минут для набора time()."""
    if not times:
        return "—", 0, 0
    mins = [t.hour * 60 + t.minute for t in times]
    avg = sum(mins) / len(mins)
    var = sum((x - avg) ** 2 for x in mins) / len(mins)
    std = int(math.sqrt(var) + 0.5)
    h, m = divmod(int(avg + 0.5), 60)
    return f"{h:02d}:{m:02d}", std, len(mins)

def export_csv(path: str):
    with db_conn() as conn, open(path, "w", newline="", encoding="utf-8") as f:
        cur = conn.execute("SELECT ts, type, user FROM events ORDER BY ts ASC")
        w = csv.writer(f)
        w.writerow(["timestamp", "event", "user"])
        w.writerows(cur.fetchall())

# ──────────────────────────────────────────────────────────────────────────────
# Хендлеры
# ──────────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m:
        return
    await m.reply_text(
        "Привет! 🐶 Я бот-фиксатор.\n"
        "• «График дня» — минуты прогулок за сегодня и события.\n"
        "• «Регулярность» — среднее время и разброс за 14 дней (лужи/кучки).\n"
        "• «Экспорт CSV» — выгрузка всех записей.\n"
        "• «Сбросить статистику» — очистка базы.\n",
        reply_markup=MAIN_KB,
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = emsg(update)
    if not m or not m.text:
        return
    text = m.text.strip()
    u = euser(update)
    username = (u.first_name or "user") if u else "user"

    # Экспорт
    if text == BTN_EXPORT:
        tmp_path = "/tmp/dog_events.csv"
        export_csv(tmp_path)
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            await m.reply_document(open(tmp_path, "rb"))
        else:
            await m.reply_text("Нет данных для экспорта.")
        return

    # Сброс
    if text == BTN_RESET:
        reset_all()
        await m.reply_text("🧹 Готово. Вся статистика очищена.", reply_markup=MAIN_KB)
        return

    # Закрыть
    if text == BTN_CLOSE:
        await m.reply_text("Ок. Напиши /start, чтобы снова открыть меню.", reply_markup=MAIN_KB)
        return

    # График дня
    if text == BTN_DAY:
        evs = load_today()
        if not evs:
            await m.reply_text("Сегодня ещё нет событий 🐾")
            return

        pairs = pair_walks(evs)
        total_minutes = sum(max(0, int((e - s).total_seconds() // 60)) for s, e in pairs)

        pee_times = [ts for ts, tp, _ in evs if tp == BTN_PEE]
        poo_times = [ts for ts, tp, _ in evs if tp == BTN_POO]
        feed_times = [ts for ts, tp, _ in evs if tp == BTN_FEED]
        home_inc = [ts for ts, tp, _ in evs if tp in (BTN_PEE_HOME, BTN_POO_HOME)]

        today = datetime.now().strftime("%d.%m")
        msg = [
            f"📅 Сегодня ({today})",
            f"• Прогулки: {total_minutes} мин ({len(pairs)} шт)",
            f"• Лужи: {timestr_list(pee_times)}",
            f"• Кучки: {timestr_list(poo_times)}",
            f"• Кормления: {timestr_list(feed_times)}",
            f"• Инциденты дома: {len(home_inc)}",
        ]
        await m.reply_text("\n".join(msg), reply_markup=MAIN_KB)
        return

    # Регулярность 14 дней
    if text == BTN_REG:
        evs = load_last_days(14)
        if not evs:
            await m.reply_text("Недостаточно данных за 14 дней 📉")
            return

        pee_list = [ts.time() for ts, tp, _ in evs if tp == BTN_PEE]
        poo_list = [ts.time() for ts, tp, _ in evs if tp == BTN_POO]
        pee_home = [1 for _, tp, _ in evs if tp == BTN_PEE_HOME]
        poo_home = [1 for _, tp, _ in evs if tp == BTN_POO_HOME]

        pee_avg, pee_std, pee_n = mean_std_minutes(pee_list)
        poo_avg, poo_std, poo_n = mean_std_minutes(poo_list)

        msg = [
            "⏱️ Регулярность (14 дней)",
            f"• Лужи: среднее {pee_avg} ±{pee_std} мин (n={pee_n})",
            f"• Кучки: среднее {poo_avg} ±{poo_std} мин (n={poo_n})",
            f"• Инциденты дома: лужи {sum(pee_home)}, кучки {sum(poo_home)}",
        ]
        await m.reply_text("\n".join(msg), reply_markup=MAIN_KB)
        return

    # Кнопки-фиксации
    if text in {BTN_WALK_START, BTN_WALK_END, BTN_PEE, BTN_POO, BTN_PEE_HOME, BTN_POO_HOME, BTN_FEED}:
        log_event(text, username)
        await m.reply_text(f"✅ {text} сохранено", reply_markup=MAIN_KB)
        return

    # Любой другой текст — игнор/подсказка
    await m.reply_text("Используй кнопки ниже или /start.", reply_markup=MAIN_KB)

# Ошибки
async def on_error(update: object, context: CallbackContext):
    logging.exception("Unhandled error: %s", context.error)

# ──────────────────────────────────────────────────────────────────────────────
# Telegram Application
# ──────────────────────────────────────────────────────────────────────────────
def build_tg_app() -> Application:
    if not BOT_TOKEN:
        raise SystemExit("❗ Переменная окружения BOT_TOKEN не задана.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)
    return app

# ──────────────────────────────────────────────────────────────────────────────
# Flask-заглушка для Render Free
# ──────────────────────────────────────────────────────────────────────────────
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

# ──────────────────────────────────────────────────────────────────────────────
# Точка входа
# ──────────────────────────────────────────────────────────────────────────────
def main():
    # 1) HTTP-сервер в фоне — чтобы Render Free держал сервис активным
    threading.Thread(target=run_flask, daemon=True).start()

    # 2) Telegram-бот в главном потоке; сбрасываем возможные конфликты
    app = build_tg_app()
    app.run_polling(
        drop_pending_updates=True,           # лечит конфликт с чужим polling/webhook
        allowed_updates=Update.ALL_TYPES,    # принимаем все типы апдейтов
    )

if __name__ == "__main__":
    main()
