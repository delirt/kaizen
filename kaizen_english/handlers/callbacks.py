"""Inline-button handler. Callback data formats:

  a:<activity>:<rev>:<op>   lesson step (op: go | o<i> | dk | nx | rp)
  m:<target>                navigation (today | review | tasks | progress | settings | home)
  lvl:<level>:<o|s>         level choice (o = onboarding, s = settings)
  set:<key>:<value>         settings
  fb:<1-5|skip|pub:0/1>     feedback flow
  rst:<yes|no>              reset confirmation
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from content import texts
from handlers.common import deliver, get_learning
from services.ui import Response

log = logging.getLogger(__name__)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    learning = get_learning(context)
    learning.ensure_user(user.id, user.username, user.first_name)
    parts = (query.data or "").split(":", 3)
    kind = parts[0]
    try:
        if kind == "a" and len(parts) == 4:
            resp = learning.handle_action(user.id, int(parts[1]), int(parts[2]), parts[3])
        elif kind == "m" and len(parts) >= 2:
            resp = learning.handle_menu(user.id, parts[1])
        elif kind == "lvl" and len(parts) == 3:
            resp = learning.choose_level(user.id, parts[1], onboarding=parts[2] == "o")
        elif kind == "set" and len(parts) >= 2:
            resp = learning.handle_setting(user.id, parts[1], ":".join(parts[2:]))
        elif kind == "fb" and len(parts) >= 2:
            resp = learning.handle_feedback(user.id, parts[1], parts[2] if len(parts) > 2 else "")
        elif kind == "rst" and len(parts) == 2:
            resp = learning.handle_reset(user.id, parts[1])
        else:
            resp = Response(toast=texts.STALE_BUTTON, toast_alert=True)
    except (ValueError, IndexError):
        log.warning("Bad callback data %r", query.data)
        resp = Response(toast=texts.STALE_BUTTON, toast_alert=True)
    await deliver(update, context, resp)


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(on_callback))
