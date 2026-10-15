import os
import logging
import random
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

telegram_app = Application.builder().token(TOKEN).build()


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS names (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


def get_names(user_id):
    with db() as conn:
        return conn.execute(
            "SELECT id, name FROM names WHERE user_id=%s ORDER BY id",
            (user_id,)
        ).fetchall()


def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить имена", callback_data="add"),
         InlineKeyboardButton("📋 Мой список", callback_data="list")],
        [InlineKeyboardButton("🎯 Выбрать человека", callback_data="pick"),
         InlineKeyboardButton("🏆 Несколько победителей", callback_data="multi")],
        [InlineKeyboardButton("➖ Удалить имя", callback_data="delete"),
         InlineKeyboardButton("🗑 Очистить список", callback_data="clear")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ])


WELCOME = (
    "🎲 <b>РАНДОМАЙЗЕР</b>\n\n"
    "Сохраняй свой список имён и выбирай случайного человека. "
    "Список виден только тебе.\n\nВыбери действие:"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("mode", None)
    await update.effective_message.reply_text(
        WELCOME, reply_markup=keyboard(), parse_mode="HTML"
    )


async def show_list(query, user_id):
    names = get_names(user_id)
    if not names:
        await query.message.reply_text(
            "📋 Список пока пуст. Нажми «Добавить имена».", reply_markup=keyboard()
        )
        return
    text = "📋 <b>Твой список:</b>\n\n" + "\n".join(
        f"{i}. {row['name']}" for i, row in enumerate(names, 1)
    )
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard())


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    action = query.data

    if action == "add":
        context.user_data["mode"] = "add"
        await query.message.reply_text(
            "✍️ Отправь имена одним сообщением — каждое с новой строки или через запятую."
        )
    elif action == "list":
        await show_list(query, user_id)
    elif action == "pick":
        names = get_names(user_id)
        if not names:
            await query.message.reply_text("Список пуст. Сначала добавь имена.", reply_markup=keyboard())
        else:
            winner = random.choice(names)["name"]
            await query.message.reply_text(
                f"🎯 Случайный выбор:\n\n🏆 <b>{winner}</b>",
                parse_mode="HTML", reply_markup=keyboard()
            )
    elif action == "multi":
        names = get_names(user_id)
        if not names:
            await query.message.reply_text("Список пуст. Сначала добавь имена.", reply_markup=keyboard())
        else:
            context.user_data["mode"] = "multi"
            await query.message.reply_text(
                f"🏆 Сколько победителей выбрать? В списке {len(names)} имён. "
                f"Отправь число от 1 до {len(names)}."
            )
    elif action == "delete":
        names = get_names(user_id)
        if not names:
            await query.message.reply_text("Список пуст.", reply_markup=keyboard())
        else:
            context.user_data["mode"] = "delete"
            text = "➖ Какое имя удалить? Отправь его номер из списка:\n\n"
            text += "\n".join(f"{i}. {row['name']}" for i, row in enumerate(names, 1))
            await query.message.reply_text(text)
    elif action == "clear":
        names = get_names(user_id)
        if not names:
            await query.message.reply_text("Список уже пуст.", reply_markup=keyboard())
        else:
            context.user_data["mode"] = "clear_confirm"
            await query.message.reply_text("⚠️ Чтобы удалить все имена, напиши ДА. Для отмены напиши НЕТ.")
    elif action == "help":
        await query.message.reply_text(
            "ℹ️ <b>Как пользоваться</b>\n"
            "• Добавляй несколько имён сразу — через запятую или с новой строки.\n"
            "• Список хранится в PostgreSQL и не пропадает при перезапуске.\n"
            "• Каждый пользователь видит только свой список.\n"
            "• При выборе нескольких победителей имена не повторяются.",
            parse_mode="HTML", reply_markup=keyboard()
        )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    if not mode:
        await update.effective_message.reply_text(
            "Используй меню ниже или отправь /start.", reply_markup=keyboard()
        )
        return

    user_id = update.effective_user.id
    text = update.effective_message.text.strip()

    if mode == "add":
        raw_names = text.replace("\r", "\n").replace(",", "\n").split("\n")
        clean = []
        for item in raw_names:
            item = " ".join(item.split()).strip()
            if item and item not in clean:
                clean.append(item[:100])
        if not clean:
            await update.effective_message.reply_text("Не нашёл имён. Попробуй ещё раз.")
            return
        with db() as conn:
            existing = {r["name"].casefold() for r in conn.execute(
                "SELECT name FROM names WHERE user_id=%s", (user_id,)
            ).fetchall()}
            to_add = [n for n in clean if n.casefold() not in existing]
            conn.executemany(
                "INSERT INTO names (user_id, name) VALUES (%s, %s)",
                [(user_id, n) for n in to_add]
            )
        context.user_data.pop("mode", None)
        await update.effective_message.reply_text(
            f"✅ Добавлено имён: {len(to_add)}.\nВсего в списке: {len(get_names(user_id))}.",
            reply_markup=keyboard()
        )
    elif mode == "multi":
        try:
            count = int(text)
        except ValueError:
            await update.effective_message.reply_text("Отправь число, например 3.")
            return
        names = get_names(user_id)
        if count < 1 or count > len(names):
            await update.effective_message.reply_text(f"Укажи число от 1 до {len(names)}.")
            return
        winners = random.sample(names, count)
        context.user_data.pop("mode", None)
        result = "🏆 <b>Победители:</b>\n\n" + "\n".join(
            f"{i}. {row['name']}" for i, row in enumerate(winners, 1)
        )
        await update.effective_message.reply_text(result, parse_mode="HTML", reply_markup=keyboard())
    elif mode == "delete":
        names = get_names(user_id)
        try:
            index = int(text) - 1
        except ValueError:
            await update.effective_message.reply_text("Отправь номер имени из списка.")
            return
        if index < 0 or index >= len(names):
            await update.effective_message.reply_text(f"Укажи номер от 1 до {len(names)}.")
            return
        row = names[index]
        with db() as conn:
            conn.execute("DELETE FROM names WHERE id=%s AND user_id=%s", (row["id"], user_id))
        context.user_data.pop("mode", None)
        await update.effective_message.reply_text(f"✅ Удалено: {row['name']}", reply_markup=keyboard())
    elif mode == "clear_confirm":
        context.user_data.pop("mode", None)
        if text.casefold() in ("да", "yes", "да!"):
            with db() as conn:
                conn.execute("DELETE FROM names WHERE user_id=%s", (user_id,))
            await update.effective_message.reply_text("🗑 Список очищен.", reply_markup=keyboard())
        else:
            await update.effective_message.reply_text("Очистка отменена.", reply_markup=keyboard())


async def post_init(application: Application):
    init_db()
    if not PUBLIC_URL:
        raise RuntimeError("Render did not provide RENDER_EXTERNAL_URL")
    await application.bot.set_webhook(
        url=f"{PUBLIC_URL}/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES
    )
    logging.info("Telegram webhook configured")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", start))
telegram_app.add_handler(CallbackQueryHandler(button))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))


@asynccontextmanager
async def lifespan(web_app: FastAPI):
    await telegram_app.initialize()
    init_db()
    if not PUBLIC_URL:
        raise RuntimeError("Render did not provide RENDER_EXTERNAL_URL")
    await telegram_app.bot.set_webhook(
        url=f"{PUBLIC_URL}/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES
    )
    await telegram_app.start()
    logging.info("Bot started and webhook configured")
    try:
        yield
    finally:
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok", "service": "telegram-randomizer"}


@app.post("/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
