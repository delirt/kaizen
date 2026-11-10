"""Slash-command handlers (the persistent Telegram command menu)."""
from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from content import texts
from handlers.common import deliver, get_learning
from services.ui import Response, Screen

# Shown in Telegram's persistent command menu (set through the Bot API at startup).
BOT_COMMANDS = [
    BotCommand("start", "Приветствие и начало"),
    BotCommand("today", "Урок на сегодня"),
    BotCommand("review", "Повторить выученные слова"),
    BotCommand("tasks", "Дополнительные задания"),
    BotCommand("progress", "Мой прогресс"),
    BotCommand("settings", "Настройки"),
    BotCommand("reset", "Сбросить прогресс"),
    BotCommand("help", "Как пользоваться ботом"),
]


def _touch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Register/refresh the user on every interaction and return the user id."""
    u = update.effective_user
    get_learning(context).ensure_user(u.id, u.username, u.first_name)
    return u.id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    await deliver(update, context, Response(send=[get_learning(context).start_screen(uid)]))


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    await deliver(update, context, get_learning(context).start_daily(uid))


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    await deliver(update, context, get_learning(context).start_review(uid))


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    await deliver(update, context, get_learning(context).start_practice(uid))


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    await deliver(update, context, Response(send=[get_learning(context).progress_screen(uid)]))


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    await deliver(update, context, Response(send=[get_learning(context).settings_screen(uid)]))


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Only asks for confirmation; the data is deleted by the `rst:yes` button."""
    _touch(update, context)
    await deliver(update, context, Response(send=[get_learning(context).reset_prompt()]))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    home = get_learning(context).home_screen(uid, intro="Что делаем?")
    await deliver(update, context, Response(send=[Screen(texts.HELP, home.rows)]))


async def any_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text: never leave the user without buttons — restore the interface from saved state."""
    uid = _touch(update, context)
    await deliver(update, context, Response(send=[get_learning(context).home_screen(uid, texts.UNKNOWN_TEXT)]))


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    for name, fn in [("start", start), ("today", today), ("review", review), ("tasks", tasks),
                     ("progress", progress), ("settings", settings), ("reset", reset), ("help", help_cmd)]:
        app.add_handler(CommandHandler(name, fn, filters=private))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, any_text))
