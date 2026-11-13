"""Slash-command handlers (the persistent Telegram command menu)."""
from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from content import texts
from handlers.common import deliver, get_learning, send_screen
from services.ui import Response, Screen

# Shown in Telegram's persistent command menu (set through the Bot API at startup).
BOT_COMMANDS = [
    BotCommand("start", "Приветствие и начало"),
    BotCommand("today", "Урок на сегодня"),
    BotCommand("review", "Повторить выученные слова"),
    BotCommand("tasks", "Дополнительные задания"),
    BotCommand("progress", "Мой прогресс"),
    BotCommand("settings", "Настройки"),
    BotCommand("feedback", "Оставить отзыв"),
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


async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _touch(update, context)
    await deliver(update, context, Response(send=[get_learning(context).feedback_prompt()]))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hidden helper: shows your Telegram id (needed for KAIZEN_ADMIN_IDS)."""
    uid = _touch(update, context)
    await deliver(update, context, Response(send=[Screen(f"Твой Telegram id: <code>{uid}</code>")]))


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    learning = get_learning(context)
    screen = learning.stats_screen() if learning.is_admin(uid) else Screen(learning.admin_denied_text(uid))
    await deliver(update, context, Response(send=[screen]))


async def feedbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _touch(update, context)
    learning = get_learning(context)
    screen = learning.feedbacks_screen() if learning.is_admin(uid) else Screen(learning.admin_denied_text(uid))
    await deliver(update, context, Response(send=[screen]))


async def any_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text: a review text if the user is leaving one, otherwise restore the interface from saved state."""
    uid = _touch(update, context)
    learning = get_learning(context)
    if update.message and update.message.text:
        resp = learning.accept_feedback_text(uid, update.message.text)
        if resp is not None:
            await deliver(update, context, resp)
            for admin_id in learning.cfg.admin_ids:           # tell the owner right away (best effort)
                try:
                    await send_screen(context.bot, admin_id, learning.feedback_admin_notice(uid))
                except Exception:  # noqa: BLE001 - e.g. owner never started the bot
                    pass
            return
    await deliver(update, context, Response(send=[get_learning(context).home_screen(uid, texts.UNKNOWN_TEXT)]))


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    for name, fn in [("start", start), ("today", today), ("review", review), ("tasks", tasks),
                     ("progress", progress), ("settings", settings), ("feedback", feedback), ("myid", myid),
                     ("stats", stats), ("feedbacks", feedbacks), ("reset", reset), ("help", help_cmd)]:
        app.add_handler(CommandHandler(name, fn, filters=private))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, any_text))
