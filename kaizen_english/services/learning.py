"""Learning logic: daily lesson, review, extra practice, settings, progress.

Everything the user sees is returned as `Response`/`Screen` objects, so this module has no Telegram
dependency and can be tested directly. All state lives in SQLite (table `activities` stores the
exact position inside a lesson), so a restart resumes where the user stopped.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from html import escape as e
from typing import Any

import content
from config import Config
from content import texts
from database.repository import WORD_FIELDS, Repository
from services import exercises, srs
from services.clock import Clock
from services.ui import Btn, Response, Screen

NOTIFY_TIMES = ["08:00", "12:00", "16:00", "18:00", "19:00", "20:00", "21:00"]
LEVEL_HINTS = {
    "A1": "только начинаю", "A2": "знаю основы", "B1": "говорю на простые темы",
    "B2": "уверенно читаю и пишу", "C1": "почти свободно", "C2": "как носитель",
}


def _cb(act_id: int, rev: int, op: str) -> str:
    return f"a:{act_id}:{rev}:{op}"


class Learning:
    def __init__(self, repo: Repository, clock: Clock, config: Config, rng: random.Random | None = None):
        self.repo = repo
        self.clock = clock
        self.cfg = config
        self.rng = rng or random.Random()

    # ====================================================================== users
    def ensure_user(self, user_id: int, username: str | None, first_name: str | None) -> dict[str, Any]:
        user = self.repo.ensure_user(user_id, username, first_name)
        if user["state"] == "new" and user["level"]:
            self.repo.update_user(user_id, state="idle")
        return user

    def _user(self, user_id: int) -> dict[str, Any]:
        user = self.repo.get_user(user_id)
        if user is None:  # e.g. stale callback after DB loss
            user = self.repo.ensure_user(user_id, None, None)
        return user

    # ====================================================================== home / start
    def start_screen(self, user_id: int) -> Screen:
        user = self._user(user_id)
        if not user["level"] and not self.repo.all_words(user_id):
            return Screen(texts.WELCOME, [[Btn("▶️ Начать", "m:today")]])
        return Screen("С возвращением! 👋 Продолжаем маленькими шагами.", self._home_rows(user_id))

    def home_screen(self, user_id: int, intro: str = "Что делаем дальше?") -> Screen:
        return Screen(intro, self._home_rows(user_id))

    def _home_rows(self, user_id: int) -> list[list[Btn]]:
        """Buttons depend on the saved state, so irrelevant ones never appear."""
        today = self.clock.today()
        user = self._user(user_id)
        daily = self.repo.get_daily(user_id, today)
        rows: list[list[Btn]] = []
        if daily is None or not user["level"]:
            rows.append([Btn("▶️ Начать", "m:today")])
        elif daily["status"] == "active":
            rows.append([Btn("▶️ Продолжить", "m:today")])
        else:
            rows.append([Btn("🧠 Дополнительные задания", "m:tasks")])
        due = self.repo.count_due(user_id, today)
        if due:
            rows.append([Btn(f"📚 Повторение ({due})", "m:review")])
        rows.append([Btn("📊 Мой прогресс", "m:progress"), Btn("⚙️ Настройки", "m:settings")])
        return rows

    def handle_menu(self, user_id: int, target: str) -> Response:
        """Navigation buttons (callback data `m:<target>`)."""
        if target == "today":
            resp = self.start_daily(user_id)
        elif target == "review":
            resp = self.start_review(user_id)
        elif target == "tasks":
            resp = self.start_practice(user_id)
        elif target == "progress":
            resp = Response(send=[self.progress_screen(user_id)])
        elif target == "settings":
            resp = Response(send=[self.settings_screen(user_id)])
        else:
            resp = Response(send=[self.home_screen(user_id)])
        resp.strip = True
        return resp

    # ====================================================================== daily lesson
    def start_daily(self, user_id: int) -> Response:
        user = self._user(user_id)
        if not user["level"]:
            return Response(send=[self.level_screen(onboarding=True)])
        today = self.clock.today()
        act = self.repo.get_daily(user_id, today)
        if act is None:
            self.repo.abandon_active(user_id, "daily", before_day=today)
            act = self._create_daily(user_id, user, today)
        if act["status"] == "done":
            note = "Сегодняшний урок уже пройден ✅ Новые слова будут завтра — так работает кайдзен: понемногу, но каждый день."
            return Response(send=[Screen(f"{note}\n\n{texts.DAILY_DONE}", [[Btn("🧠 Дополнительные задания", "m:tasks")]])])
        self.repo.update_user(user_id, state="learning")
        return Response(send=[self._render(act)])

    def _create_daily(self, user_id: int, user: dict, today) -> dict[str, Any]:
        settings = self.repo.get_settings(user_id)
        target = int(settings.get("daily_target") or self.cfg.default_daily_words)
        new_ids = self._pick_new_words(user_id, user, settings, target)
        due = [r for r in self.repo.due_words(user_id, today, self.cfg.review_per_session)
               if content.get_word(r["word_id"])]
        last = {r["word_id"]: r["last_ex_type"] for r in due}

        items: list[dict] = []
        used: list[str] = []
        for wid in new_ids:
            t = exercises.choose_type(self.rng, None, used)
            used.append(t)
            items.append({"kind": "new", "word": wid, "ex_type": t})
        for r in due:
            t = exercises.choose_type(self.rng, last.get(r["word_id"]), used)
            used = used[-(len(exercises.EX_TYPES) - 1):] + [t]
            items.append({"kind": "review", "word": r["word_id"], "ex_type": t})

        state = self._new_state(items)
        state["vocab_finished"] = len(new_ids) < target
        if not items:
            state["completed_text"] = texts.VOCAB_FINISHED
            act_id = self.repo.create_activity(user_id, "daily", today, state, status="done")
            self.repo.update_user(user_id, last_learning_date=today.isoformat(), state="idle")
        else:
            act_id = self.repo.create_activity(user_id, "daily", today, state)
        return self.repo.get_activity(act_id)  # type: ignore[return-value]

    def _pick_new_words(self, user_id: int, user: dict, settings: dict, n: int) -> list[str]:
        """Unlearned words of the user's track; own level first, then the nearest levels (higher first)."""
        track = settings.get("mode") or "general"
        learned = self.repo.learned_ids(user_id)
        order = content.LEVELS
        base = order.index(user["level"])
        candidates = [(i, w) for i, w in enumerate(content.WORDS)
                      if w.get("track", "general") == track and w["id"] not in learned]

        def key(item: tuple[int, dict]):
            idx, w = item
            diff = order.index(w["level"]) - base
            return (abs(diff), 0 if diff >= 0 else 1, idx)

        return [w["id"] for _, w in sorted(candidates, key=key)[:n]]

    # ====================================================================== manual review
    def start_review(self, user_id: int) -> Response:
        today = self.clock.today()
        due = [r for r in self.repo.due_words(user_id, today, self.cfg.review_max)
               if content.get_word(r["word_id"])]
        if not due:
            rows = [[Btn("📊 Мой прогресс", "m:progress")]]
            if not self.repo.all_words(user_id):
                return Response(send=[Screen(texts.NO_WORDS_YET, [[Btn("▶️ Начать", "m:today")]])])
            return Response(send=[Screen(texts.REVIEW_NONE, rows)])
        self.repo.abandon_active(user_id, "review")
        items = []
        used: list[str] = []
        for r in due:
            t = exercises.choose_type(self.rng, r["last_ex_type"], used)
            used = used[-(len(exercises.EX_TYPES) - 1):] + [t]
            items.append({"kind": "review", "word": r["word_id"], "ex_type": t})
        act_id = self.repo.create_activity(user_id, "review", today, self._new_state(items))
        return Response(send=[self._render(self.repo.get_activity(act_id))])  # type: ignore[arg-type]

    # ====================================================================== extra practice
    def start_practice(self, user_id: int) -> Response:
        today = self.clock.today()
        daily = self.repo.get_daily(user_id, today)
        if daily is None or daily["status"] != "done":
            label = "▶️ Продолжить" if daily else "▶️ Начать"
            return Response(send=[Screen(texts.TASKS_LOCKED, [[Btn(label, "m:today")]])])
        rows = [r for r in self.repo.all_words(user_id) if content.get_word(r["word_id"])]
        if not rows:
            return Response(send=[Screen(texts.NO_WORDS_YET, [[Btn("📊 Мой прогресс", "m:progress")]])])
        items = self._practice_items(rows, today)
        self.repo.abandon_active(user_id, "practice")
        act_id = self.repo.create_activity(user_id, "practice", today, self._new_state(items))
        return Response(send=[self._render(self.repo.get_activity(act_id))])  # type: ignore[arg-type]

    def _practice_items(self, rows: list[dict], today) -> list[dict]:
        """Mix of due / learned-today / older words. Different words first; a word is reused
        (with a different exercise type) only when the vocabulary is too small."""
        size = self.cfg.practice_size
        iso = today.isoformat()
        groups = [
            [r for r in rows if r["next_review"] <= iso],
            [r for r in rows if r["first_learned"] == iso and r["next_review"] > iso],
            [r for r in rows if r["first_learned"] != iso and r["next_review"] > iso],
        ]
        for g in groups:
            self.rng.shuffle(g)
        self.rng.shuffle(groups)
        picked: list[dict] = []
        while len(picked) < size and any(groups):
            for g in groups:                      # round-robin across the three groups
                if g and len(picked) < size:
                    picked.append(g.pop())

        items: list[dict] = []
        used_pairs: set[tuple[str, str]] = set()
        used_types: list[str] = []

        def add(row: dict) -> bool:
            wid = row["word_id"]
            free = [t for t in exercises.EX_TYPES if (wid, t) not in used_pairs]
            if not free:
                return False
            preferred = [t for t in free if t != row["last_ex_type"]] or free
            fresh = [t for t in preferred if t not in used_types] or preferred
            t = self.rng.choice(fresh)
            used_pairs.add((wid, t))
            used_types[:] = used_types[-(len(exercises.EX_TYPES) - 1):] + [t]
            items.append({"kind": "extra", "word": wid, "ex_type": t})
            return True

        for row in picked:
            add(row)
        while len(items) < size and picked:       # tiny vocabulary: reuse words, never the same exercise
            if not any(add(r) for r in self.rng.sample(picked, len(picked)) if len(items) < size):
                break
        self.rng.shuffle(items)
        return items

    # ====================================================================== activity engine
    @staticmethod
    def _new_state(items: list[dict]) -> dict[str, Any]:
        state: dict[str, Any] = {"items": items, "pos": 0, "ok": 0, "total": 0}
        Learning._reset_step(state)
        return state

    @staticmethod
    def _reset_step(state: dict) -> None:
        item = state["items"][state["pos"]] if state["pos"] < len(state["items"]) else None
        state.update(
            phase="card" if item and item["kind"] == "new" else "exercise",
            attempts=0, wrong=[], hint=None, feedback=None, ex=None, revealed=False,
        )

    def _prepare_exercise(self, state: dict) -> None:
        item = state["items"][state["pos"]]
        state["ex"] = exercises.build_exercise(content.get_word(item["word"]), item["ex_type"], self.rng)

    def _commit(self, act: dict, state: dict, status: str = "active") -> None:
        act["rev"] += 1
        act["state"] = state
        self.repo.save_activity(act["id"], state, act["rev"], status)

    def _header(self, act: dict) -> str:
        st = act["state"]
        item = st["items"][st["pos"]]
        kind = item["kind"]
        if kind == "retry":
            return "🔄 <b>Ещё раз</b> — закрепим слово"
        same = [i for i in st["items"] if i["kind"] == kind]
        idx = sum(1 for i in st["items"][: st["pos"] + 1] if i["kind"] == kind)
        label = {"new": "🌱 Новое слово", "review": "📚 Повторение", "extra": "🧠 Задание"}[kind]
        return f"{label} {idx}/{len(same)}"

    def _render(self, act: dict) -> Screen:
        """Screen for the saved position (used on resume and after every step)."""
        st = act["state"]
        item = st["items"][st["pos"]]
        word = content.get_word(item["word"])
        header = self._header(act)
        a = act["id"]
        rev = act["rev"]
        if st["phase"] == "card":
            return Screen(texts.card(word, header), [[Btn("✍️ К заданию", _cb(a, rev, "go"))]])
        if st["ex"] is None:                      # resumed lesson without a built exercise
            self._prepare_exercise(st)
            self.repo.save_activity(a, st, rev)
        ex = st["ex"]
        if st["phase"] == "answered":
            text = texts.exercise_screen(header, ex["prompt"], st["feedback"])
            return Screen(text, [[Btn("🔁 Повторить слово", _cb(a, rev, "rp")),
                                  Btn("▶️ Продолжить", _cb(a, rev, "nx"))]])
        rows = [[Btn(opt, _cb(a, rev, f"o{i}"))] for i, opt in enumerate(ex["options"]) if i not in st["wrong"]]
        if st["attempts"] >= 1:
            rows.append([Btn("🤷 Не знаю", _cb(a, rev, "dk"))])
        return Screen(texts.exercise_screen(header, ex["prompt"], st["hint"]), rows)

    def handle_action(self, user_id: int, act_id: int, rev: int, op: str) -> Response:
        """One button press inside a lesson. Stale/foreign buttons are rejected safely."""
        act = self.repo.get_activity(act_id)
        if act is None or act["user_id"] != user_id or act["status"] != "active" or act["rev"] != rev:
            return Response(toast=texts.STALE_BUTTON, toast_alert=True)
        st = act["state"]
        item = st["items"][st["pos"]]
        word = content.get_word(item["word"])
        if word is None:                           # word vanished from content: skip it
            return self._advance(user_id, act)

        phase = st["phase"]
        if op == "go" and phase == "card":
            st["phase"] = "exercise"
            self._prepare_exercise(st)
            self._commit(act, st)
            return Response(strip=True, send=[self._render(act)])

        if op == "rp" and phase == "answered":     # show the card again; no state change, not a new word
            return Response(send=[Screen(texts.card(word, "🔁 <b>Повторяем слово</b>"))])

        if op == "nx" and phase == "answered":
            return self._advance(user_id, act)

        if phase == "exercise" and op == "dk":
            if st["attempts"] == 0:
                return Response(toast="Сначала попробуй ответить 🙂")
            return self._reveal(user_id, act, item, word)

        if phase == "exercise" and op.startswith("o") and op[1:].isdigit():
            return self._answer(user_id, act, item, word, int(op[1:]))

        return Response(toast=texts.STALE_BUTTON, toast_alert=True)

    def _answer(self, user_id: int, act: dict, item: dict, word: dict, idx: int) -> Response:
        st = act["state"]
        ex = st["ex"]
        if not 0 <= idx < len(ex["options"]) or idx in st["wrong"]:
            return Response(toast="Этот вариант уже выбран 🙂")
        if idx == ex["answer"]:
            first = st["attempts"] == 0
            if first:
                self._record(user_id, act, item, True)
            st.update(phase="answered", feedback=texts.correct_feedback(word, first))
            self._commit(act, st)
            return Response(edit=self._render(act))
        st["attempts"] += 1
        st["wrong"].append(idx)
        if st["attempts"] == 1:
            self._record(user_id, act, item, False)           # the first try decides the schedule
        if st["attempts"] >= 2:
            return self._reveal(user_id, act, item, word)
        st["hint"] = exercises.wrong_answer_text(word, ex["type"], ex["options"][idx])
        self._commit(act, st)
        return Response(edit=self._render(act))

    def _reveal(self, user_id: int, act: dict, item: dict, word: dict) -> Response:
        st = act["state"]
        ex = st["ex"]
        if st["attempts"] == 0:                    # defensive; «Не знаю» is only offered after a try
            self._record(user_id, act, item, False)
        st.update(phase="answered", revealed=True,
                  feedback=texts.revealed_feedback(word, ex["options"][ex["answer"]]))
        self._commit(act, st)
        return Response(edit=self._render(act))

    def _record(self, user_id: int, act: dict, item: dict, correct: bool) -> None:
        """Persist the outcome of an exercise exactly once (first attempt decides)."""
        st = act["state"]
        today = self.clock.today()
        wid, kind, ex_type = item["word"], item["kind"], item["ex_type"]
        if kind != "retry":
            st["total"] += 1
            st["ok"] += 1 if correct else 0
        rec = self.repo.get_word(user_id, wid)
        if kind == "new" and rec is None:
            self.repo.insert_word(user_id, wid, **srs.new_word_record(today, correct, ex_type))
        elif rec is not None and kind == "retry":   # second chance: counters only, schedule untouched
            field = "correct_count" if correct else "incorrect_count"
            self.repo.update_word(user_id, wid, **{field: rec[field] + 1, "last_ex_type": ex_type})
        elif rec is not None:
            new = srs.apply_answer(rec, correct, today, ex_type)
            self.repo.update_word(user_id, wid, **{k: new[k] for k in WORD_FIELDS})
            asked = {i["word"] for i in st["items"] if i["kind"] == "retry"}
            if kind == "review" and not correct and wid not in asked:
                st["items"].append({"kind": "retry", "word": wid,
                                    "ex_type": exercises.choose_type(self.rng, ex_type, [])})

    def _advance(self, user_id: int, act: dict) -> Response:
        st = act["state"]
        while True:
            st["pos"] += 1
            if st["pos"] >= len(st["items"]):
                return self._finish(user_id, act)
            if content.get_word(st["items"][st["pos"]]["word"]):
                break
        self._reset_step(st)
        self._commit(act, st)
        return Response(strip=True, send=[self._render(act)])

    def _finish(self, user_id: int, act: dict) -> Response:
        st = act["state"]
        st["pos"] = len(st["items"])
        self._commit(act, st, status="done")
        today = self.clock.today()
        if act["kind"] == "daily":
            self.repo.update_user(user_id, last_learning_date=today.isoformat(), state="idle")
            text = texts.DAILY_DONE
            if st.get("vocab_finished"):
                text = f"{texts.VOCAB_FINISHED}\n\n{text}"
            screens = [Screen(text, [[Btn("🧠 Дополнительные задания", "m:tasks")]])]
            settings = self.repo.get_settings(user_id)
            if not settings.get("notify_enabled") and not settings.get("notify_prompted"):
                self.repo.update_settings(user_id, notify_prompted=1)   # offer only once
                screens.append(self.notify_offer_screen())
            return Response(strip=True, send=screens)
        if act["kind"] == "review":
            text = (f"✅ <b>Повторение завершено!</b>\nС первой попытки верно: {st['ok']} из {st['total']}.\n"
                    "Слова, где были ошибки, вернутся раньше — так память крепнет 🌱")
            return Response(strip=True, send=[Screen(text, [[Btn("📊 Мой прогресс", "m:progress")]])])
        text = f"🧠 <b>Тренировка завершена!</b>\nС первой попытки верно: {st['ok']} из {st['total']}. Отличная работа!"
        return Response(strip=True, send=[Screen(text, [[Btn("🧠 Ещё задания", "m:tasks")],
                                                        [Btn("📊 Мой прогресс", "m:progress")]])])

    # ====================================================================== progress
    def progress_screen(self, user_id: int) -> Screen:
        today = self.clock.today()
        stats = self.repo.stats(user_id, today, today - timedelta(days=6))
        streak = srs.streak(self.repo.completed_days(user_id), today)
        rows = []
        if stats["due"]:
            rows.append([Btn(f"📚 Повторение ({stats['due']})", "m:review")])
        rows.append([Btn("⬅️ Назад", "m:home")])
        return Screen(texts.progress(stats, streak), rows)

    # ====================================================================== reset
    def reset_prompt(self) -> Screen:
        return Screen(texts.RESET_PROMPT, [[Btn("🗑 Да, сбросить всё", "rst:yes")],
                                           [Btn("↩️ Отмена", "rst:no")]])

    def handle_reset(self, user_id: int, answer: str) -> Response:
        if answer == "yes":
            self.repo.reset_progress(user_id)
            return Response(edit=Screen(texts.RESET_DONE, [[Btn("▶️ Начать", "m:today")]]))
        return Response(edit=Screen(texts.RESET_CANCELLED, self._home_rows(user_id)))

    # ====================================================================== level & settings
    def level_screen(self, onboarding: bool) -> Screen:
        suffix = "o" if onboarding else "s"
        user_text = ("Выбери свой уровень английского — так я подберу слова. "
                     "Позже его можно изменить в настройках, прогресс не потеряется.")
        rows = [[Btn(f"{lv} — {LEVEL_HINTS[lv]}", f"lvl:{lv}:{suffix}")] for lv in content.levels_available()]
        return Screen(f"🎚 <b>Твой уровень</b>\n\n{user_text}", rows)

    def choose_level(self, user_id: int, level: str, onboarding: bool) -> Response:
        if level not in content.levels_available():
            return Response(toast="Такого уровня пока нет", toast_alert=True)
        self.repo.update_user(user_id, level=level)
        if onboarding:
            started = self.start_daily(user_id)
            return Response(edit=Screen(f"Отлично, уровень <b>{level}</b>! Начинаем 🌱"), send=started.send)
        return Response(edit=self.settings_screen(user_id), toast=f"Уровень: {level}")

    def settings_screen(self, user_id: int) -> Screen:
        user = self._user(user_id)
        s = self.repo.get_settings(user_id)
        target = s["daily_target"]
        notify = "включены" if s["notify_enabled"] else "выключены"
        text = ("⚙️ <b>Настройки</b>\n\n"
                f"🎚 Уровень: <b>{e(user['level'] or 'не выбран')}</b>\n"
                f"📅 Слов в день: <b>{target}</b> (изменение вступит в силу со следующего урока)\n"
                f"🔔 Напоминания: <b>{notify}</b>, время <b>{s['notify_time']}</b>")
        rows = [
            [Btn("🎚 Сменить уровень", "set:level:")],
            [Btn(("✅ " if target == n else "") + f"{n} слова в день", f"set:target:{n}") for n in (2, 3)],
            [Btn("🔕 Выключить напоминания" if s["notify_enabled"] else "🔔 Включить напоминания", "set:notif:toggle"),
             Btn("⏰ Время", "set:timemenu:")],
            [Btn("🎓 Режим IELTS (скоро)", "set:ielts:")],
            [Btn("⬅️ Назад", "m:home")],
        ]
        return Screen(text, rows)

    def notify_offer_screen(self) -> Screen:
        """Shown once, right after the first finished lesson."""
        rows = [[Btn(t, f"set:onb:{t}") for t in NOTIFY_TIMES[i:i + 3]] for i in range(0, len(NOTIFY_TIMES), 3)]
        rows.append([Btn("Не нужно", "set:onb:no")])
        return Screen("🔔 <b>Хочешь напоминания?</b>\n\nВыбери удобное время — я буду напоминать об уроке "
                      "раз в день, и только если он ещё не пройден. Это можно изменить в /settings.", rows)

    def handle_setting(self, user_id: int, key: str, value: str) -> Response:
        if key == "onb":
            if value == "no":
                self.repo.update_settings(user_id, notify_enabled=0)
                return Response(edit=Screen("Хорошо, без напоминаний 👍 Включить их можно в /settings."))
            if value in NOTIFY_TIMES:
                self.repo.update_settings(user_id, notify_time=value, notify_enabled=1)
                return Response(edit=Screen(f"Готово ✅ Буду напоминать каждый день в <b>{value}</b>. "
                                            "Изменить время можно в /settings."))
            return Response(toast=texts.STALE_BUTTON)
        if key == "level":
            return Response(edit=self.level_screen(onboarding=False))
        if key == "target" and value in ("2", "3"):
            self.repo.update_settings(user_id, daily_target=int(value))
            return Response(edit=self.settings_screen(user_id), toast="Сохранено ✅")
        if key == "notif":
            s = self.repo.get_settings(user_id)
            self.repo.update_settings(user_id, notify_enabled=0 if s["notify_enabled"] else 1)
            return Response(edit=self.settings_screen(user_id), toast="Сохранено ✅")
        if key == "timemenu":
            rows = [[Btn(t, f"set:time:{t}") for t in NOTIFY_TIMES[i:i + 3]] for i in range(0, len(NOTIFY_TIMES), 3)]
            rows.append([Btn("⬅️ Назад", "set:home:")])
            return Response(edit=Screen("⏰ Когда напоминать об уроке? (время — по часовому поясу бота)", rows))
        if key == "time" and len(value) == 5 and value in NOTIFY_TIMES:
            self.repo.update_settings(user_id, notify_time=value, notify_enabled=1)
            return Response(edit=self.settings_screen(user_id), toast=f"Напомню в {value} ✅")
        if key == "ielts":
            return Response(toast="Режим IELTS появится позже — твой прогресс при этом сохранится 🎓", toast_alert=True)
        if key == "home":
            return Response(edit=self.settings_screen(user_id))
        return Response(toast=texts.STALE_BUTTON)

    # ====================================================================== notifications
    def users_to_remind(self) -> list[int]:
        """Opted-in users whose reminder time has come (within a window), lesson not finished, not yet reminded today."""
        now = self.clock.now()
        today = now.date()
        result = []
        for cand in self.repo.notification_candidates(today.isoformat()):
            try:
                hh, mm = map(int, cand["notify_time"].split(":"))
            except ValueError:
                continue
            scheduled = datetime.combine(today, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=hh, minute=mm)
            if not scheduled <= now <= scheduled + timedelta(hours=self.cfg.notify_window_hours):
                continue
            daily = self.repo.get_daily(cand["user_id"], today)
            if daily and daily["status"] == "done":
                self.mark_reminded(cand["user_id"])    # nothing to remind about today
                continue
            result.append(cand["user_id"])
        return result

    def reminder_screen(self, user_id: int) -> Screen:
        daily = self.repo.get_daily(user_id, self.clock.today())
        label = "▶️ Продолжить" if daily else "▶️ Начать"
        return Screen("⏰ Время для маленького шага! Твой урок на сегодня ждёт — это всего около 5 минут 🌱",
                      [[Btn(label, "m:today")]])

    def mark_reminded(self, user_id: int) -> None:
        self.repo.update_settings(user_id, last_notified_date=self.clock.today().isoformat())

    def disable_notifications(self, user_id: int) -> None:
        self.repo.update_settings(user_id, notify_enabled=0)
