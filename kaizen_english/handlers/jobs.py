"""Background job: optional daily reminders."""
from __future__ import annotations

import logging

from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, ContextTypes

from handlers.common import get_learning, send_screen

log = logging.getLogger(__name__)


async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs every minute. At most one reminder per user per day (tracked in the DB)."""
    learning = get_learning(context)
    for user_id in learning.users_to_remind():
        try:
            await send_screen(context.bot, user_id, learning.reminder_screen(user_id))
        except Forbidden:                              # user blocked the bot -> stop reminding
            learning.disable_notifications(user_id)
        except TelegramError as exc:
            log.warning("Reminder to %s failed: %s", user_id, exc)
            continue                                   # try again on the next tick
        learning.mark_reminded(user_id)


def register(app: Application) -> None:
    if app.job_queue is None:
        log.warning("JobQueue unavailable (install python-telegram-bot[job-queue]); reminders disabled")
        return
    app.job_queue.run_repeating(send_reminders, interval=60, first=15, name="reminders")
