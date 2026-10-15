import os
import logging
import random
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats)
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_names (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                added_by BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (chat_id, name)
            )
        """)


def get_names(user_id):
    with db() as conn:
        return conn.execute(
            "SELECT id, name FROM names WHERE user_id=%s ORDER BY id",
            (user_id,)
        ).fetchall()


def get_group_names(chat_id):
    with db() as conn:
        return conn.execute(
            "SELECT id, name FROM group_names WHERE chat_id=%s ORDER BY id",
            (chat_id,)
        ).fetchall()


def group_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить участников", callback_data="group_add"),
         InlineKeyboardButton("📋 Список участников", callback_data="group_list")],
        [InlineKeyboardButton("🎯 Выбрать участника", callback_data="group_pick"),
         InlineKeyboardButton("🏆 Несколько победителей", callback_data="group_multi")],
        [InlineKeyboardButton("🗑 Очистить список группы", callback_data="group_clear")]
    ])


def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить имена", callback_data="add"),
         InlineKeyboardButton("📋 Мой список", callback_data="list")],
        [InlineKeyboardButton("🎯 Выбрать человека", callback_data="pick"),
         InlineKeyboardButton("🏆 Несколько победителей", callback_data="multi")],
        [InlineKeyboardButton("➖ Удалить имя", callback_data="delete"),
         InlineKeyboardButton("🗑 Очистить список", callback_data="clear")],
        [InlineKeyboardButton("👥 Групповой режим", callback_data="group_help")],
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


async def group_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type not in ("group", "supergroup"):
        await message.reply_text(
            "👥 Добавь меня в групповой чат, затем используй там команду /group. "
            "В группе добавляйте участников командой /groupadd Имя1, Имя2."
        )
        return
    await message.reply_text(
        "👥 <b>Групповой рандомайзер</b>\n\n"
        "Выбери действие кнопкой ниже. Имена для розыгрыша добавляются вручную: "
        "бот не может автоматически получить полный список участников чата.",
        parse_mode="HTML",
        reply_markup=group_keyboard()
    )


async def group_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type not in ("group", "supergroup"):
        await message.reply_text("Эту команду нужно использовать в группе Telegram.")
        return
    raw = " ".join(context.args).replace("\r", "\n").replace(",", "\n").split("\n")
    clean = []
    for item in raw:
        name = " ".join(item.split()).strip()
        if name and name.casefold() not in {n.casefold() for n in clean}:
            clean.append(name[:100])
    if not clean:
        await message.reply_text("Укажи имена после команды, например:\n/groupadd Анна, Борис, Виктор")
        return
    with db() as conn:
        conn.cursor().executemany(
            """INSERT INTO group_names (chat_id, name, added_by)
               VALUES (%s, %s, %s) ON CONFLICT (chat_id, name) DO NOTHING""",
            [(message.chat.id, name, update.effective_user.id if update.effective_user else None)
             for name in clean]
        )
    count = len(get_group_names(message.chat.id))
    await message.reply_text(f"✅ Список группы обновлён. Всего участников в списке: {count}.")


async def group_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type not in ("group", "supergroup"):
        await message.reply_text("Эту команду нужно использовать в группе Telegram.")
        return
    names = get_group_names(message.chat.id)
    if not names:
        await message.reply_text("Список пуст. Добавь имена командой /groupadd Анна, Борис.")
        return
    text = "📋 <b>Список участников группы:</b>\n\n" + "\n".join(
        f"{i}. {row['name']}" for i, row in enumerate(names, 1)
    )
    await message.reply_text(text, parse_mode="HTML")


async def group_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type not in ("group", "supergroup"):
        await message.reply_text("Эту команду нужно использовать в группе Telegram.")
        return
    names = get_group_names(message.chat.id)
    if not names:
        await message.reply_text("Список пуст. Сначала добавь участников командой /groupadd.")
        return
    winner = random.choice(names)["name"]
    await message.reply_text(f"🎯 Случайный участник группы: <b>{winner}</b>", parse_mode="HTML")


async def group_multi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type not in ("group", "supergroup"):
        await message.reply_text("Эту команду нужно использовать в группе Telegram.")
        return
    names = get_group_names(message.chat.id)
    if not names:
        await message.reply_text("Список пуст. Сначала добавь участников командой /groupadd.")
        return
    if not context.args:
        await message.reply_text(
            f"🏆 Сколько участников выбрать? В списке {len(names)} имён.\n"
            f"Напиши число командой /groupmulti 3"
        )
        return
    try:
        count = int(context.args[0])
    except ValueError:
        await message.reply_text("Укажи количество числом, например /groupmulti 3.")
        return
    if count < 1 or count > len(names):
        await message.reply_text(f"Укажи число от 1 до {len(names)}.")
        return
    winners = random.sample(names, count)
    result = "🏆 <b>Выбранные участники:</b>\n\n" + "\n".join(
        f"{i}. {row['name']}" for i, row in enumerate(winners, 1)
    )
    await message.reply_text(result, parse_mode="HTML")


async def group_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type not in ("group", "supergroup"):
        await message.reply_text("Эту команду нужно использовать в группе Telegram.")
        return
    with db() as conn:
        conn.execute("DELETE FROM group_names WHERE chat_id=%s", (message.chat.id,))
    await message.reply_text("🗑 Список участников этой группы очищен.")


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
    elif action == "group_help":
        await query.message.reply_text(
            "👥 Для группового режима добавь бота в групповой чат и отправь там /group. "
            "После этого управляй розыгрышем кнопками в сообщении бота.",
            reply_markup=keyboard()
        )
    elif action.startswith("group_"):
        chat = query.message.chat if query.message else None
        if not chat or chat.type not in ("group", "supergroup"):
            await query.message.reply_text(
                "Открой групповое меню командой /group прямо в нужной группе."
            )
            return

        if action == "group_add":
            context.user_data["mode"] = "group_add"
            context.user_data["group_chat_id"] = chat.id
            await query.message.reply_text(
                "✍️ Отправь имена участников одним сообщением — каждое с новой строки или через запятую. "
                "Они будут добавлены в список этой группы."
            )
        elif action == "group_list":
            names = get_group_names(chat.id)
            if not names:
                await query.message.reply_text(
                    "Список группы пока пуст. Нажми «➕ Добавить участников».",
                    reply_markup=group_keyboard()
                )
            else:
                result = "📋 <b>Участники для розыгрыша:</b>\n\n" + "\n".join(
                    f"{i}. {row['name']}" for i, row in enumerate(names, 1)
                )
                await query.message.reply_text(
                    result, parse_mode="HTML", reply_markup=group_keyboard()
                )
        elif action == "group_pick":
            names = get_group_names(chat.id)
            if not names:
                await query.message.reply_text(
                    "Список группы пуст. Сначала нажми «➕ Добавить участников».",
                    reply_markup=group_keyboard()
                )
            else:
                winner = random.choice(names)["name"]
                await query.message.reply_text(
                    f"🎯 Случайный участник: <b>{winner}</b>",
                    parse_mode="HTML", reply_markup=group_keyboard()
                )
        elif action == "group_multi":
            names = get_group_names(chat.id)
            if not names:
                await query.message.reply_text(
                    "Список группы пуст. Сначала добавь участников.",
                    reply_markup=group_keyboard()
                )
            else:
                context.user_data["mode"] = "group_multi"
                context.user_data["group_chat_id"] = chat.id
                await query.message.reply_text(
                    f"🏆 Сколько победителей выбрать? В списке {len(names)} участников. "
                    f"Отправь число от 1 до {len(names)}."
                )
        elif action == "group_clear":
            with db() as conn:
                conn.execute("DELETE FROM group_names WHERE chat_id=%s", (chat.id,))
            await query.message.reply_text(
                "🗑 Список участников этой группы очищен.",
                reply_markup=group_keyboard()
            )
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
    message = update.effective_message
    if not message or not message.text:
        return

    mode = context.user_data.get("mode")
    chat = message.chat
    logging.info("Incoming text message: user_id=%s chat_id=%s chat_type=%s mode=%s text=%r",
                 update.effective_user.id if update.effective_user else None,
                 chat.id, chat.type, mode, message.text)

    # Не отвечаем на обычные сообщения группы. В группе обрабатываем текст
    # только после нажатия соответствующей кнопки группового меню.
    if chat.type in ("group", "supergroup"):
        if mode not in ("group_add", "group_multi") or context.user_data.get("group_chat_id") != chat.id:
            return
    elif chat.type != "private":
        return
    elif mode in ("group_add", "group_multi"):
        return

    if not mode:
        # Личное меню показываем только в личном чате; в группах молчим.
        await message.reply_text(
            "Используй меню ниже или отправь /start.", reply_markup=keyboard()
        )
        return

    user_id = update.effective_user.id
    text = message.text.strip()

    if mode == "group_add":
        raw_names = text.replace("\r", "\n").replace(",", "\n").split("\n")
        clean = []
        seen = set()
        for item in raw_names:
            name = " ".join(item.split()).strip()
            if name and name.casefold() not in seen:
                clean.append(name[:100])
                seen.add(name.casefold())
        if not clean:
            await message.reply_text("Не нашёл имён. Отправь их ещё раз или нажми кнопку позже.")
            return
        with db() as conn:
            conn.cursor().executemany(
                """INSERT INTO group_names (chat_id, name, added_by)
                   VALUES (%s, %s, %s) ON CONFLICT (chat_id, name) DO NOTHING""",
                [(chat.id, name, user_id) for name in clean]
            )
        context.user_data.pop("mode", None)
        context.user_data.pop("group_chat_id", None)
        await message.reply_text(
            f"✅ Список группы обновлён. Всего участников: {len(get_group_names(chat.id))}.",
            reply_markup=group_keyboard()
        )
        return
    elif mode == "group_multi":
        try:
            count = int(text)
        except ValueError:
            await message.reply_text("Отправь количество числом, например 3.")
            return
        names = get_group_names(chat.id)
        if count < 1 or count > len(names):
            await message.reply_text(f"Укажи число от 1 до {len(names)}.")
            return
        winners = random.sample(names, count)
        context.user_data.pop("mode", None)
        context.user_data.pop("group_chat_id", None)
        result = "🏆 <b>Победители:</b>\n\n" + "\n".join(
            f"{i}. {row['name']}" for i, row in enumerate(winners, 1)
        )
        await message.reply_text(result, parse_mode="HTML", reply_markup=group_keyboard())
        return

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
            conn.cursor().executemany(
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled Telegram update error", exc_info=context.error)
    message = getattr(update, "effective_message", None)
    if message:
        try:
            await message.reply_text("⚠️ Произошла ошибка при обработке сообщения. Ошибка записана в логи; попробуй ещё раз чуть позже.")
        except Exception:
            logging.exception("Could not send error notice to user")


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


telegram_app.add_error_handler(error_handler)
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", start))
telegram_app.add_handler(CommandHandler("group", group_help))
telegram_app.add_handler(CommandHandler("groupadd", group_add))
telegram_app.add_handler(CommandHandler("grouplist", group_list))
telegram_app.add_handler(CommandHandler("grouppick", group_pick))
telegram_app.add_handler(CommandHandler("groupmulti", group_multi))
telegram_app.add_handler(CommandHandler("groupclear", group_clear))
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

    # Показываем список команд Telegram при вводе символа "/".
    await telegram_app.bot.set_my_commands(
        [
            BotCommand("start", "Запустить бота и открыть меню"),
            BotCommand("help", "Помощь по использованию бота"),
        ],
        scope=BotCommandScopeAllPrivateChats()
    )
    await telegram_app.bot.set_my_commands(
        [
            BotCommand("group", "Открыть меню розыгрыша в группе"),
            BotCommand("groupadd", "Добавить имена: /groupadd Анна, Борис"),
            BotCommand("grouplist", "Показать список участников"),
            BotCommand("grouppick", "Выбрать одного участника случайно"),
            BotCommand("groupmulti", "Выбрать нескольких: /groupmulti 3"),
            BotCommand("groupclear", "Очистить список участников группы"),
        ],
        scope=BotCommandScopeAllGroupChats()
    )
    logging.info("Telegram command menus configured")

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
    logging.info("Webhook update received: update_id=%s message=%s callback=%s", data.get("update_id"), bool(data.get("message")), bool(data.get("callback_query")))
    await telegram_app.process_update(update)
    return {"ok": True}
