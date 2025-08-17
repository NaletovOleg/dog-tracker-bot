# dog_tracker_bot.py
import os
import csv
import datetime as dt
from typing import List, Tuple
import threading

import pandas as pd
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ====== НАСТРОЙКИ ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "dog_events.csv")  # на Free-деплое файл переживает рестарт, но пропадёт при redeploy

# ====== КЛАВИАТУРА ======
KEY_WALK_START = "Прогулка 🐾"
KEY_WALK_END   = "Прогулка завершена ⏱"
KEY_PEE        = "Лужа 🚰"
KEY_POO        = "Кучка 💩"
KEY_PEE_HOME   = "Лужа дома 🚨"
KEY_POO_HOME   = "Кучка дома 🚨"
KEY_FEED       = "Покормил 🍖"
KEY_DAY        = "График дня 📅"
KEY_REG        = "Регулярность ⏱️"
KEY_EXPORT     = "Экспорт CSV 📤"
KEY_RESET      = "Сбросить статистику 🧹"
KEY_CLOSE      = "❌ Закрыть бот"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KEY_WALK_START, KEY_WALK_END],
        [KEY_PEE, KEY_POO],
        [KEY_PEE_HOME, KEY_POO_HOME],
        [KEY_FEED],
        [KEY_DAY, KEY_REG],
        [KEY_EXPORT, KEY_RESET],
        [KEY_CLOSE],
    ],
    resize_keyboard=True,
)

# ====== УТИЛИТЫ ======
def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ensure_csv_headers(path: str):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp", "event", "user"])

def log_event(event_type: str, user: str):
    ensure_csv_headers(DB_PATH)
    with open(DB_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([now_str(), event_type, user])

def load_df() -> pd.DataFrame:
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return pd.DataFrame(columns=["timestamp", "event", "user"])
    df = pd.read_csv(DB_PATH)
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "event", "user"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    for col in ["event", "user"]:
        if col in df.columns:
            df[col] = df[col].astype(str)
        else:
            df[col] = ""
    return df

def pair_walks_today(df_today: pd.DataFrame):
    starts = df_today[df_today["event"] == KEY_WALK_START]["timestamp"].tolist()
    ends   = df_today[df_today["event"] == KEY_WALK_END]["timestamp"].tolist()
    pairs = []
    i = j = 0
    while i < len(starts) and j < len(ends):
        if ends[j] > starts[i]:
            pairs.append((starts[i], ends[j]))
            i += 1; j += 1
        else:
            j += 1
    return pairs

def format_times(series: pd.Series) -> str:
    if series.empty:
        return "—"
    return ", ".join(pd.to_datetime(series).dt.strftime("%H:%M").tolist())

def mean_and_sigma_minutes(times: List[dt.time]):
    if not times:
        return "—", 0, 0
    minutes = [t.hour * 60 + t.minute for t in times]
    avg = sum(minutes) / len(minutes)
    sigma = (sum((m - avg) ** 2 for m in minutes) / len(minutes)) ** 0.5
    h, m = divmod(int(avg + 0.5), 60)
    return f"{h:02d}:{m:02d}", int(sigma), len(times)

# ====== TELEGRAM ХЕНДЛЕРЫ ======
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 🐶 Бот работает.\n"
        "«График дня» покажет минуты прогулок за сегодня,\n"
        "«Регулярность» — среднее время и разброс за 14 дней.\n"
        "Кнопка «Сбросить статистику» очищает все записи.",
        reply_markup=MAIN_KB,
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = (update.message.from_user.first_name or "").strip() or "user"
    text = (update.message.text or "").strip()

    # Экспорт
    if text == KEY_EXPORT:
        if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
            await update.message.reply_document(open(DB_PATH, "rb"))
        else:
            await update.message.reply_text("Нет данных для экспорта.")
        return

    # Сброс статистики
    if text == KEY_RESET:
        try:
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)
            ensure_csv_headers(DB_PATH)
            await update.message.reply_text("🧹 Готово. Статистика очищена.", reply_markup=MAIN_KB)
        except Exception as e:
            await update.message.reply_text(f"Не удалось очистить: {e}", reply_markup=MAIN_KB)
        return

    # Закрыть
    if text == KEY_CLOSE:
        await update.message.reply_text("Бот остановлен. Напиши /start, чтобы снова включить.")
        return

    # График дня
    if text == KEY_DAY:
        df = load_df()
        today = pd.Timestamp.now().date()
        df_today = df[df["timestamp"].dt.date == today]
        if df_today.empty:
            await update.message.reply_text("Сегодня ещё нет событий 🐶")
            return
        pairs = pair_walks_today(df_today)
        total_minutes = sum(max(0, int((e - s).total_seconds() // 60)) for s, e in pairs)

        pee_times = df_today[df_today["event"] == KEY_PEE]["timestamp"]
        poo_times = df_today[df_today["event"] == KEY_POO]["timestamp"]
        feed_times = df_today[df_today["event"] == KEY_FEED]["timestamp"]
        home_inc = df_today[df_today["event"].isin([KEY_PEE_HOME, KEY_POO_HOME])]

        msg = [
            f"📅 Сегодня ({today.strftime('%d.%m')}):",
            f"• Прогулки: {total_minutes} мин ({len(pairs)} шт)",
            f"• Лужи: {format_times(pee_times)}",
            f"• Кучки: {format_times(poo_times)}",
            f"• Кормления: {format_times(feed_times)}",
            f"• Инциденты дома: {0 if home_inc.empty else len(home_inc)}",
        ]
        await update.message.reply_text("\n".join(msg), reply_markup=MAIN_KB)
        return

    # Регулярность
    if text == KEY_REG:
        df = load_df()
        if df.empty:
            await update.message.reply_text("Нет данных для анализа.")
            return
        since = (pd.Timestamp.now() - pd.Timedelta(days=14)).date()
        df14 = df[df["timestamp"].dt.date >= since]
        if df14.empty:
            await update.message.reply_text("За последние 14 дней событий нет.")
            return

        pee_list = [t.time() for t in df14[df14["event"] == KEY_PEE]["timestamp"].tolist()]
        poo_list = [t.time() for t in df14[df14["event"] == KEY_POO]["timestamp"].tolist()]
        pee_home = df14[df14["event"] == KEY_PEE_HOME]
        poo_home = df14[df14["event"] == KEY_POO_HOME]

        pee_avg, pee_sigma, pee_n = mean_and_sigma_minutes(pee_list)
        poo_avg, poo_sigma, poo_n = mean_and_sigma_minutes(poo_list)

        msg = [
            "⏱️ Регулярность (14 дней)",
            "",
            f"• Лужи: среднее {pee_avg} ±{pee_sigma} мин (n={pee_n})",
            f"• Кучки: среднее {poo_avg} ±{poo_sigma} мин (n={poo_n})",
            f"• Инциденты дома: лужи {len(pee_home)}, кучки {len(poo_home)}",
        ]
        await update.message.reply_text("\n".join(msg), reply_markup=MAIN_KB)
        return

    # Остальные кнопки — лог
    if text in {KEY_WALK_START, KEY_WALK_END, KEY_PEE, KEY_POO, KEY_PEE_HOME, KEY_POO_HOME, KEY_FEED}:
        log_event(text, user)
        await update.message.reply_text(f"✅ {text} сохранено", reply_markup=MAIN_KB)
        return

    await update.message.reply_text("Не понял. Используй кнопки ниже или /start.", reply_markup=MAIN_KB)

# ====== TELEGRAM APP ======
def build_app() -> Application:
    if not BOT_TOKEN:
        raise SystemExit("❗ Установите переменную окружения BOT_TOKEN (токен от @BotFather).")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

def start_polling():
    tg_app = build_app()
    # run_polling блокирует поток → запускаем в отдельном
    tg_app.run_polling(close_loop=False)

# ====== FLASK (HTTP-заглушка для Render Web Service Free) ======
flask_app = Flask(__name__)

@flask_app.get("/")
def root():
    return "Dog tracker bot is running ✅"

@flask_app.get("/healthz")
def health():
    return "ok"

if __name__ == "__main__":
    # 1) запускаем бота в отдельном потоке
    t = threading.Thread(target=start_polling, daemon=True)
    t.start()

    # 2) запускаем HTTP-сервер, чтобы Render Free держал сервис живым
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
