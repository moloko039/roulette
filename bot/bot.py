import math
import os
import time
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

from db import get_player, init_db
from economy import HOUR

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Играть", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await update.message.reply_text(
        "Нажми кнопку, чтобы открыть игру", reply_markup=keyboard
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = int(time.time())
    player = get_player(update.effective_user.id, now=now)
    # до следующего начисления: от last_accrual ровно час, минуты округляем вверх
    minutes = math.ceil((player["last_accrual"] + HOUR - now) / 60)
    await update.message.reply_text(
        f"Баланс: {player['balance']} фишек\n"
        f"До следующего начисления: {minutes} мин"
    )


def build_application(token, use_updater=True):
    """Создаёт приложение бота и регистрирует обработчики, но не запускает его.

    use_updater=False нужен для webhook внутри чужого веб-сервера: обновления
    приходят снаружи и кладутся в update_queue, собственный Updater не нужен.
    """
    builder = Application.builder().token(token)
    if not use_updater:
        builder = builder.updater(None)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    return app


def main():
    if not TOKEN or not WEBAPP_URL:
        raise SystemExit("Не заданы BOT_TOKEN или WEBAPP_URL в файле .env")
    init_db()
    build_application(TOKEN).run_polling()


if __name__ == "__main__":
    main()