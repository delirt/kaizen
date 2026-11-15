"""Kaizen English — Telegram bot entry point.  Run:  python main.py"""
from __future__ import annotations

import logging
import sys

from telegram import MenuButtonCommands, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

import config as config_module
from content import texts
from database.db import Database
from database.repository import Repository
from handlers import callbacks, commands, jobs
from services.clock import Clock
from services.learning import Learning

log = logging.getLogger("kaizen")


async def post_init(app: Application) -> None:
    """Configure the persistent command menu through the Bot API."""
    await app.bot.set_my_commands(commands.BOT_COMMANDS)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    log.info("Command menu configured: %s", ", ".join(c.command for c in commands.BOT_COMMANDS))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            if update.callback_query:
                await update.callback_query.answer()
            await context.bot.send_message(update.effective_chat.id, texts.ERROR_TEXT, parse_mode=ParseMode.HTML)
        except Exception:  # noqa: BLE001 - never let the error handler crash
            pass


def build_app(cfg: config_module.Config, db: Database) -> Application:
    app = Application.builder().token(cfg.bot_token).post_init(post_init).build()
    app.bot_data["learning"] = Learning(Repository(db), Clock(cfg.timezone), cfg)
    commands.register(app)
    callbacks.register(app)
    jobs.register(app)
    app.add_error_handler(on_error)
    return app


def main() -> None:
    cfg = config_module.load_config()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        level=getattr(logging, cfg.log_level, logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not cfg.bot_token:
        sys.exit("BOT_TOKEN is not set. Put it in the environment or in a .env file (see .env.example).")
    db = Database(cfg.db_path)
    db.migrate()
    log.info("Database ready at %s (schema v%s)", cfg.db_path, db.schema_version())
    app = build_app(cfg, db)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
