"""Glue between Telegram updates and the Telegram-agnostic `Response` objects."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from services.learning import Learning
from services.ui import Response, Screen

log = logging.getLogger(__name__)


def get_learning(context: ContextTypes.DEFAULT_TYPE) -> Learning:
    return context.application.bot_data["learning"]


def keyboard(screen: Screen) -> InlineKeyboardMarkup | None:
    if not screen.rows:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.text, callback_data=b.data) for b in row] for row in screen.rows]
    )


async def send_screen(bot, chat_id: int, screen: Screen) -> None:
    await bot.send_message(chat_id, screen.text, parse_mode=ParseMode.HTML,
                           reply_markup=keyboard(screen), disable_web_page_preview=True)


async def deliver(update: Update, context: ContextTypes.DEFAULT_TYPE, resp: Response) -> None:
    """Apply a Response: answer the callback, edit/strip the pressed message, send new messages."""
    chat = update.effective_chat
    query = update.callback_query
    if query is not None:
        await query.answer(text=resp.toast, show_alert=resp.toast_alert)
        message = query.message
        if resp.edit is not None and message is not None:
            try:
                await message.edit_text(resp.edit.text, parse_mode=ParseMode.HTML,
                                        reply_markup=keyboard(resp.edit), disable_web_page_preview=True)
            except BadRequest as exc:                       # same content / message too old
                if "not modified" not in str(exc).lower():
                    log.warning("edit failed (%s); sending as a new message", exc)
                    await send_screen(context.bot, chat.id, resp.edit)
        elif resp.strip and message is not None:
            try:
                await message.edit_reply_markup(reply_markup=None)
            except BadRequest:
                pass
    elif resp.edit is not None:                              # command context: nothing to edit
        await send_screen(context.bot, chat.id, resp.edit)
    for screen in resp.send:
        await send_screen(context.bot, chat.id, screen)
