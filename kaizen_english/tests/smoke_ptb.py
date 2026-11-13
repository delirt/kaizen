"""Smoke test: feed real Telegram-shaped updates through python-telegram-bot's dispatcher with a fake network.

Run from kaizen_english:  python tests/smoke_ptb.py
"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Bot, Update
from telegram.ext import Application

import config, main
from database.db import Database

SENT = []  # (endpoint, payload)
_mid = [100]


class FakeBot(Bot):
    async def _do_post(self, endpoint, data=None, **kwargs):
        params = data or {}
        SENT.append((endpoint, params))
        if endpoint == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "Kaizen", "username": "kaizen_bot"}
        if endpoint in ("sendMessage", "editMessageText"):
            _mid[0] += 1
            return {"message_id": _mid[0], "date": int(time.time()),
                    "chat": {"id": 7, "type": "private"}, "text": params.get("text", "")}
        return True


def msg_update(i, text):
    ents = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None
    m = {"message_id": i, "date": int(time.time()), "chat": {"id": 7, "type": "private"},
         "from": {"id": 7, "is_bot": False, "first_name": "Tester"}, "text": text}
    if ents:
        m["entities"] = ents
    return {"update_id": i, "message": m}


def cb_update(i, data, mid=101):
    return {"update_id": i, "callback_query": {
        "id": str(i), "chat_instance": "x", "data": data,
        "from": {"id": 7, "is_bot": False, "first_name": "Tester"},
        "message": {"message_id": mid, "date": int(time.time()), "chat": {"id": 7, "type": "private"}, "text": "old"}}}


async def run():
    with tempfile.TemporaryDirectory() as d:
        cfg = config.Config("1:fake", os.path.join(d, "k.db"), "Europe/Moscow", "INFO")
        db = Database(cfg.db_path); db.migrate()
        app = Application.builder().bot(FakeBot("1:fake")).post_init(main.post_init).build()
        from handlers import callbacks, commands, jobs
        from services.clock import Clock
        from services.learning import Learning
        from database.repository import Repository
        app.bot_data["learning"] = Learning(Repository(db), Clock(cfg.timezone), cfg)
        commands.register(app); callbacks.register(app); jobs.register(app); app.add_error_handler(main.on_error)
        await app.initialize()
        await main.post_init(app)
        uid = 1
        async def send(raw):
            await app.process_update(Update.de_json(raw, app.bot))
        for cmd in ["/start", "/today", "/review", "/tasks", "/progress", "/settings", "/reset", "/help", "/feedback", "/myid", "/stats", "/feedbacks", "hello there"]:
            n = len(SENT)
            uid += 1
            await send(msg_update(uid, cmd))
            assert len(SENT) > n, f"{cmd}: no reply"
            print(f"{cmd:12} -> {SENT[-1][0]}: {SENT[-1][1]['text'][:60]!r}")
        # onboarding button flow
        for data in ["m:today", "lvl:A2:o"]:
            uid += 1; await send(cb_update(uid, data))
        last = SENT[-1][1]
        print("after level pick:", last["text"][:50].replace("\n", " "), "| buttons:", last["reply_markup"].inline_keyboard[0][0].callback_data)
        cmds = [e for e in SENT if e[0] == "setMyCommands"]
        menu = [e for e in SENT if e[0] == "setChatMenuButton"]
        assert cmds and menu, "command menu not configured"
        print("menu commands:", [c["command"] for c in __import__("json").loads(cmds[0][1]["commands"])] if isinstance(cmds[0][1]["commands"], str) else cmds[0][1]["commands"])
        # reset confirmation does not delete by itself
        uid += 1; await send(msg_update(uid, "/reset"))
        assert db.scalar("SELECT COUNT(*) FROM activities") >= 1
        uid += 1; await send(cb_update(uid, "rst:yes"))
        assert db.scalar("SELECT COUNT(*) FROM activities") == 0
        # feedback flow through real handlers
        uid += 1; await send(cb_update(uid, "fb:5"))
        uid += 1; await send(msg_update(uid, "Классный бот!"))
        assert db.scalar("SELECT text FROM feedback") == "Классный бот!", "review text not saved"
        print("review saved:", SENT[-1][1]["text"][:40].replace("\n", " "))
        # garbage callback does not crash
        uid += 1; await send(cb_update(uid, "zzz"))
        uid += 1; await send(cb_update(uid, "a:abc:1:go"))
        await app.shutdown()
        print("SMOKE OK")

asyncio.run(run())
